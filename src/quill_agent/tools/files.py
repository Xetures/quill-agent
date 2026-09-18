"""文件工具：让 Agent 能在工作目录内查看和修改文件。

安全是这个模块的第一原则。这四个工具的能力边界完全由工作目录决定：
模型给出的任何路径都会先过 PathGuard 校验，越界（例如 ../../.ssh/id_rsa）
直接拒绝 —— 而不是指望模型「看到工作目录后自觉不去碰外面」。

四个工具的分工：

    list_dir    看目录里有什么（模型的「眼睛」，没有它只能猜文件名）
    read_file   读文件内容（带行号，超长自动截断并给出续读方式）
    write_file  写入 / 覆盖（自动建父目录，原子写入）
    edit_file   局部替换（比整文件重写省 token，也不容易误改别处）

两条贯穿始终的约束：
    1. 读取一律截断 —— 一个几千行的文件足以把上下文吃光；
    2. 编辑要求 old_string 唯一 —— 出现多次就拒绝，逼模型给出足够上下文，
       否则它很可能改错地方。
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from quill_agent.config import get_settings
from quill_agent.preferences import PreferenceStore
from quill_agent.tools.base import registry

# 界面选中的工作目录存这个键。有它就用它，没有就回落配置里的 work_dir。
WORK_DIR_PREF_KEY = "work_dir"

# 搜索时跳过的目录：依赖缓存、版本控制内部数据、构建产物。
# 不跳过的话，搜一个函数名会被 .venv 里的几万行命中淹掉。
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".ruff_cache",
        ".pytest_cache",
        ".mypy_cache",
        ".playwright-cli",
        "dist",
        "build",
    }
)

# 搜索类工具的默认结果上限
DEFAULT_MAX_RESULTS = 50

# 命中行的展示长度上限：有些文件是压成一行的（如打包产物），不截断会撑爆上下文
MAX_LINE_CHARS = 200

# 单次读取的默认行数
DEFAULT_READ_LINES = 200

# 单次列目录最多展示多少项
MAX_LIST_ENTRIES = 200

# 超过这个大小直接拒绝读取：多半是二进制或巨型文件，读进来只会撑爆上下文
MAX_FILE_BYTES = 1_000_000

# 系统目录选择器的最长等待时间（秒）——用户不选也不能无限挂着
PICKER_TIMEOUT = 300


class PathGuard:
    """把模型给的路径限制在工作目录内。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def resolve(self, raw: str) -> Path:
        """解析并校验一个路径。

        相对路径以工作目录为基准；绝对路径也接受，但同样必须落在工作目录内。

        Raises:
            ValueError: 路径越出工作目录。
        """
        text = (raw or "").strip() or "."
        target = Path(text)
        if not target.is_absolute():
            target = self.root / target

        # resolve() 会展开 .. 并跟随符号链接，所以「软链接指向外面」也拦得住
        resolved = target.resolve()

        if not resolved.is_relative_to(self.root):
            raise ValueError(
                f"拒绝访问工作目录之外的位置：{raw}（工作目录：{self.root}）"
            )

        return resolved


def current_work_dir() -> Path:
    """当前生效的工作目录。

    优先用界面上选的那个；没选过（或选过的目录已经不在了）就用配置默认值。
    这里每次调用都校验一次目录是否存在 —— 万一它被删除或移走，
    工具会自动退回默认目录继续可用，而不是直接报错罢工。
    """
    chosen = PreferenceStore(get_settings().preferences_path).get(WORK_DIR_PREF_KEY)
    if chosen:
        candidate = Path(chosen)
        if candidate.is_dir():
            return candidate.resolve()

    return get_settings().work_dir.resolve()


def set_work_dir(raw: str) -> str | None:
    """设置工作目录。

    它是文件工具的安全边界，所以必须存在且确实是目录才接受。

    Returns:
        成功返回 None；失败返回可直接展示的错误文案。
    """
    text = raw.strip()
    if not text:
        return "路径不能为空。"

    target = Path(text).expanduser()
    if not target.exists():
        return f"路径不存在：{target}"
    if not target.is_dir():
        return f"这不是目录：{target}"

    PreferenceStore(get_settings().preferences_path).set(
        WORK_DIR_PREF_KEY, str(target.resolve())
    )
    return None


def clear_work_dir() -> None:
    """清除界面选择，回到配置里的默认工作目录。"""
    PreferenceStore(get_settings().preferences_path).remove(WORK_DIR_PREF_KEY)


def guard() -> PathGuard:
    """按当前生效的工作目录构造 PathGuard。"""
    return PathGuard(current_work_dir())


def list_subdirs(path: Path) -> tuple[list[Path], str | None]:
    """列出目录下的子目录，供界面做「目录浏览器」使用。

    和 list_dir 工具的区别：那个返回给模型看的格式化文本，
    这个返回结构化数据 —— 界面要自己决定怎么渲染。

    Returns:
        (子目录列表, 错误文案)。成功时错误为 None，失败时列表为空。
    """
    try:
        entries = sorted(
            (item for item in path.iterdir() if item.is_dir()),
            # 不以点开头的排前面：目录里往往有一堆 .venv / .pytest_cache 之类的
            # 工具缓存，按纯字母序会把真正的项目目录挤到下面，找起来费劲。
            key=lambda item: (item.name.startswith("."), item.name.lower()),
        )
    except OSError as exc:
        return [], f"无法读取该目录：{exc}"

    return entries, None


def _detect_system_picker() -> tuple[str, ...] | None:
    """探测「系统目录选择器」在当前环境是否可用。

    三个前提缺一不可，否则用户点了没反应（对话框弹在别处，或者根本不出现）：
        1. 平台本身有可用的选择器程序；
        2. 程序确实装了；
        3. 服务端和用户在同一个桌面会话里 —— SSH、容器里没有桌面，
           就算拉起来也只会弹在服务器上，所以直接排除。

    Returns:
        可直接交给 subprocess 的命令；不可用时返回 None。
    """
    # SSH 会话没有本地桌面
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return None

    if sys.platform == "darwin" and shutil.which("osascript"):
        return (
            "osascript",
            "-e",
            'POSIX path of (choose folder with prompt "选择工作目录")',
        )

    if sys.platform == "win32" and shutil.which("powershell"):
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
            "if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }"
        )
        return ("powershell", "-NoProfile", "-Command", script)

    # Linux：zenity 是 GNOME 系常见组件，另外必须有图形会话才算数
    if (
        sys.platform.startswith("linux")
        and shutil.which("zenity")
        and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    ):
        return ("zenity", "--file-selection", "--directory", "--title=选择工作目录")

    return None


# 进程启动时探测一次就够了 —— 运行环境不会中途改变，
# 没必要每轮 rerun 都去查一遍 PATH。界面据此决定渲染哪个入口。
SYSTEM_PICKER_COMMAND = _detect_system_picker()


def system_dir_picker_command() -> tuple[str, ...] | None:
    """当前环境可用的系统目录选择器命令；不可用时返回 None。

    结果在模块加载时就算好了，见 SYSTEM_PICKER_COMMAND。
    """
    return SYSTEM_PICKER_COMMAND


def pick_dir_with_system_dialog() -> tuple[str | None, str | None]:
    """拉起系统目录选择器。

    **这是阻塞调用**：对话框关掉之前，这次脚本运行就停在这里，页面一直转圈。
    对「用户正在选目录」这个场景可以接受，但别在别处随便用。

    Returns:
        (选中的路径, 错误文案)。用户取消时两者都是 None。
    """
    command = system_dir_picker_command()
    if command is None:
        return None, "当前环境没有可用的系统目录选择器。"

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=PICKER_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None, "等待选择超时，请重试，或改用下面的目录浏览。"
    except OSError as exc:
        return None, f"调用系统选择器失败：{exc}"

    path = result.stdout.strip()
    if not path:
        return None, None  # 用户按了取消，不算错误

    return path, None


def _atomic_write(target: Path, content: str) -> None:
    """先写临时文件再改名 —— 写到一半崩了也不会留下半截文件。"""
    tmp = target.with_name(f".{target.name}.tmp{os.getpid()}")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target)
    finally:
        # 正常路径下 replace 之后 tmp 已不存在；异常时清掉残留
        if tmp.exists():
            tmp.unlink(missing_ok=True)


@registry.tool(
    description=(
        "列出目录下的文件和子目录。"
        "当你不确定某个目录里有什么、或想确认某个文件是否存在时使用。"
        "只列一层，不会递归展开。"
    ),
    category="文件",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目录路径，相对工作目录；留空或用 . 表示工作目录本身",
            }
        },
    },
)
def list_dir(path: str = ".") -> str:
    """列出目录内容。"""
    try:
        target = guard().resolve(path)
    except ValueError as exc:
        return str(exc)

    if not target.exists():
        return f"路径不存在：{path}"
    if not target.is_dir():
        return f"不是目录：{path}（读文件内容请用 read_file）"

    try:
        # 目录排在文件前面，各自按名称排序
        entries = sorted(target.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
    except OSError as exc:
        return f"无法读取目录：{exc}"

    if not entries:
        return f"目录是空的：{path}"

    lines: list[str] = []
    for entry in entries[:MAX_LIST_ENTRIES]:
        try:
            if entry.is_dir():
                lines.append(f"[目录] {entry.name}/")
            else:
                lines.append(f"[文件] {entry.name}  ({entry.stat().st_size} 字节)")
        except OSError:
            lines.append(f"[未知] {entry.name}")

    if len(entries) > MAX_LIST_ENTRIES:
        lines.append(f"... 另有 {len(entries) - MAX_LIST_ENTRIES} 项未显示")

    return "\n".join([f"目录：{path}（共 {len(entries)} 项）", *lines])


@registry.tool(
    description=(
        "读取文本文件的内容，返回带行号的文本。"
        "文件较长时只返回其中一段，返回值里会说明总行数和如何继续读取后面的部分。"
    ),
    category="文件",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径，相对工作目录"},
            "offset": {
                "type": "integer",
                "description": "从第几行开始读（行号从 1 开始），默认 1",
            },
            "limit": {
                "type": "integer",
                "description": f"最多读取多少行，默认 {DEFAULT_READ_LINES}",
            },
        },
        "required": ["path"],
    },
)
def read_file(path: str, offset: int = 1, limit: int = DEFAULT_READ_LINES) -> str:
    """读取文件内容。"""
    try:
        target = guard().resolve(path)
    except ValueError as exc:
        return str(exc)

    if not target.exists():
        return f"文件不存在：{path}"
    if target.is_dir():
        return f"这是目录不是文件：{path}（看目录里有什么请用 list_dir）"

    try:
        size = target.stat().st_size
    except OSError as exc:
        return f"无法读取文件信息：{exc}"

    if size > MAX_FILE_BYTES:
        return f"文件太大（{size} 字节，上限 {MAX_FILE_BYTES}），拒绝读取。"

    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"不是 UTF-8 文本文件，无法按文本读取：{path}"
    except OSError as exc:
        return f"读取失败：{exc}"

    all_lines = text.splitlines()
    total = len(all_lines)

    start = max(offset, 1) - 1
    end = min(start + max(limit, 1), total)
    chunk = all_lines[start:end]

    numbered = [
        f"{number:>6}: {line}"
        for number, line in enumerate(chunk, start=start + 1)
    ]

    result = [f"文件：{path}（共 {total} 行，显示第 {start + 1}-{end} 行）", ""]
    result.extend(numbered)
    if end < total:
        result.append(f"... 还有 {total - end} 行未显示，可用 offset={end + 1} 继续读取。")

    return "\n".join(result)


@registry.tool(
    description=(
        "写入文件：覆盖原有内容；文件或父目录不存在时自动创建。"
        "如果只是修改已有文件的某一部分，优先使用 edit_file。"
    ),
    category="文件",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径，相对工作目录"},
            "content": {"type": "string", "description": "要写入的完整内容"},
        },
        "required": ["path", "content"],
    },
)
def write_file(path: str, content: str) -> str:
    """写入（覆盖）文件。"""
    try:
        target = guard().resolve(path)
    except ValueError as exc:
        return str(exc)

    if target.is_dir():
        return f"目标是个目录，不能当文件写入：{path}"

    existed = target.exists()

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"无法创建父目录：{exc}"

    try:
        _atomic_write(target, content)
    except OSError as exc:
        return f"写入失败：{exc}"

    action = "已覆盖" if existed else "已创建"
    lines = len(content.splitlines())
    size = len(content.encode("utf-8"))
    return f"{action}：{path}（{lines} 行，{size} 字节）"


@registry.tool(
    description=(
        "把文件里的一段文本替换成另一段，只改这一处。"
        "old_string 必须与文件中的内容完全一致（含缩进和空行），且在文件中只出现一次；"
        "若它出现多次，请多带几行上下文让它唯一。"
    ),
    category="文件",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径，相对工作目录"},
            "old_string": {
                "type": "string",
                "description": "要被替换的原文本，必须与文件内容完全一致",
            },
            "new_string": {
                "type": "string",
                "description": "替换后的新文本；传空字符串表示删除这一段",
            },
        },
        "required": ["path", "old_string", "new_string"],
    },
)
def edit_file(path: str, old_string: str, new_string: str) -> str:
    """局部替换文件内容。"""
    if not old_string:
        return "替换失败：old_string 不能为空。"

    try:
        target = guard().resolve(path)
    except ValueError as exc:
        return str(exc)

    if not target.is_file():
        return f"文件不存在：{path}"

    try:
        text = target.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        return f"读取失败：{exc}"

    count = text.count(old_string)
    if count == 0:
        return (
            "替换失败：文件里找不到与 old_string 完全一致的内容。"
            "请先用 read_file 确认当前内容，注意缩进和空行必须一模一样。"
        )
    if count > 1:
        return (
            f"替换失败：old_string 在文件中出现了 {count} 次，无法确定改哪一处。"
            "请在 old_string 中多包含几行上下文，使其唯一。"
        )

    updated = text.replace(old_string, new_string, 1)

    try:
        _atomic_write(target, updated)
    except OSError as exc:
        return f"写入失败：{exc}"

    return (
        f"已替换 1 处：{path}"
        f"（{len(text.splitlines())} 行 -> {len(updated.splitlines())} 行）"
    )


# ---------------------------------------------------------------------------
# 搜索与删除
# ---------------------------------------------------------------------------
def _walk_files(root: Path, file_pattern: str = "") -> Iterator[Path]:
    """遍历目录下的文件，顺手剪掉噪声目录。

    用 os.walk 而不是 rglob：要在**进入目录之前**就砍掉 .venv 这类目录。
    先进去再过滤，等于白读几万个文件。
    """
    for current, subdirs, filenames in os.walk(root):
        # 原地修改 subdirs 才会让 os.walk 真的跳过这些目录
        subdirs[:] = [name for name in subdirs if name not in IGNORED_DIRS]

        for name in filenames:
            if file_pattern and not fnmatch.fnmatch(name, file_pattern):
                continue
            yield Path(current) / name


def _read_text_safely(path: Path) -> str | None:
    """尽力把文件当文本读出来；二进制、超大、无权限一律返回 None。"""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _match_pattern(relative: Path, pattern: str) -> bool:
    """通配符匹配，同时支持 `*.py` 和 `**/*.py` 两种写法。

    fnmatch 里的 `*` 本身就能跨目录，所以 `*.py` 已经等价于「任意层级的 .py」；
    用户习惯写的 `**/*.py` 再把前缀剥掉比一次。
    """
    patterns = (pattern, pattern.removeprefix("**/"))
    return any(
        fnmatch.fnmatch(str(relative), item) or fnmatch.fnmatch(relative.name, item)
        for item in patterns
    )


@registry.tool(
    description=(
        "在文件内容里搜索文本（按正则匹配）。"
        "找「某个函数在哪定义」「哪个文件引用了这个配置」时用它，"
        "比逐个文件读一遍快得多。"
    ),
    category="文件",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "要搜索的正则表达式，例如 build_system_prompt",
            },
            "path": {
                "type": "string",
                "description": "搜索起点目录，相对工作目录；留空表示工作目录本身",
            },
            "file_pattern": {
                "type": "string",
                "description": "只搜文件名匹配该通配符的文件，例如 *.py；留空表示全部",
            },
            "max_results": {
                "type": "integer",
                "description": f"最多返回多少条命中，默认 {DEFAULT_MAX_RESULTS}",
            },
        },
        "required": ["pattern"],
    },
)
def search_content(
    pattern: str,
    path: str = ".",
    file_pattern: str = "",
    max_results: int = DEFAULT_MAX_RESULTS,
) -> str:
    """在文件内容里做正则搜索，返回「文件:行号: 内容」形式的命中列表。"""
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"正则表达式不合法：{exc}"

    try:
        instance = guard()
        root = instance.resolve(path)
    except ValueError as exc:
        return str(exc)

    if not root.exists():
        return f"路径不存在：{path}"
    if not root.is_dir():
        return f"不是目录：{path}（搜索起点需要是一个目录）"

    hits: list[str] = []
    scanned = 0
    truncated = False

    for file in _walk_files(root, file_pattern):
        text = _read_text_safely(file)
        if text is None:
            continue
        scanned += 1

        for number, line in enumerate(text.splitlines(), start=1):
            if not regex.search(line):
                continue

            snippet = line.strip()
            if len(snippet) > MAX_LINE_CHARS:
                snippet = snippet[:MAX_LINE_CHARS] + "…"

            hits.append(f"{file.relative_to(instance.root)}:{number}: {snippet}")
            if len(hits) >= max(1, max_results):
                truncated = True
                break

        if truncated:
            break

    if not hits:
        return f"没有找到匹配「{pattern}」的内容（已扫描 {scanned} 个文件）。"

    head = (
        f"在 {path} 下搜索「{pattern}」，命中 {len(hits)}"
        f"{'+' if truncated else ''} 处（已扫描 {scanned} 个文件）："
    )
    result = [head, *hits]
    if truncated:
        result.append(f"... 命中过多，只显示前 {len(hits)} 条，可用更精确的 pattern 缩小范围。")

    return "\n".join(result)


@registry.tool(
    description=(
        "按文件名 / 路径的通配符查找文件，例如 *.py、test_*.md、**/*.json。"
        "想知道「项目里都有哪些这类文件」时用它；"
        "只想看某个目录下有什么，用 list_dir 更合适。"
    ),
    category="文件",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "通配符模式，例如 *.py（会自动匹配任意层级）",
            },
            "path": {
                "type": "string",
                "description": "查找起点目录，相对工作目录；留空表示工作目录本身",
            },
            "max_results": {
                "type": "integer",
                "description": f"最多返回多少个，默认 {DEFAULT_MAX_RESULTS}",
            },
        },
        "required": ["pattern"],
    },
)
def search_files(
    pattern: str,
    path: str = ".",
    max_results: int = DEFAULT_MAX_RESULTS,
) -> str:
    """按通配符查找文件。"""
    try:
        instance = guard()
        root = instance.resolve(path)
    except ValueError as exc:
        return str(exc)

    if not root.exists():
        return f"路径不存在：{path}"
    if not root.is_dir():
        return f"不是目录：{path}（查找起点需要是一个目录）"

    matched: list[str] = []
    truncated = False

    for file in _walk_files(root):
        if not _match_pattern(file.relative_to(root), pattern):
            continue

        matched.append(str(file.relative_to(instance.root)))
        if len(matched) >= max(1, max_results):
            truncated = True
            break

    if not matched:
        return f"没有找到匹配「{pattern}」的文件（起点：{path}）。"

    head = f"在 {path} 下按「{pattern}」找到 {len(matched)}{'+' if truncated else ''} 个文件："
    result = [head, *matched]
    if truncated:
        result.append(f"... 结果过多，只显示前 {len(matched)} 个。")

    return "\n".join(result)


@registry.tool(
    description=(
        "删除一个文件。确认某个文件确实不再需要时才使用 —— 这是不可撤销的操作。"
        "不能用来删除目录。"
    ),
    category="文件",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要删除的文件路径，相对工作目录"},
        },
        "required": ["path"],
    },
)
def delete_file(path: str) -> str:
    """删除文件（不删目录）。"""
    try:
        target = guard().resolve(path)
    except ValueError as exc:
        return str(exc)

    if not target.exists():
        return f"文件不存在：{path}"
    if target.is_dir():
        return f"这是一个目录，本工具只删除文件：{path}"

    try:
        target.unlink()
    except OSError as exc:
        return f"删除失败：{exc}"

    return f"已删除：{path}"
