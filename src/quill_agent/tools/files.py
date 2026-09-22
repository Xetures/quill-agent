"""文件工具：让 Agent 能在工作目录内查看和修改文件。

安全是这个模块的第一原则。所有工具的能力边界完全由工作目录决定：
模型给出的任何路径都会先过 PathGuard 校验，越界（例如 ../../.ssh/id_rsa）
直接拒绝 —— 而不是指望模型「看到工作目录后自觉不去碰外面」。

十个工具的分工：

    list_dir        看目录里有什么（模型的「眼睛」，没有它只能猜文件名）
    read_file       读文件内容（带行号，超长自动截断并给出续读方式）
    write_file      写入 / 覆盖（自动建父目录，原子写入）
    edit_file       局部替换（比整文件重写省 token，也不容易误改别处）
    search_content  按正则搜文件内容（跳过依赖缓存与构建产物）
    search_files    按文件名模式找文件
    make_dir        新建目录（父目录不存在时一并创建）
    move_file       移动 / 重命名（文件或目录）
    copy_file       复制（文件或目录，原文件保留）
    delete_file     删除文件或空目录（写操作里最危险的一个）

两条贯穿始终的约束：
    1. 读取一律截断 —— 一个几千行的文件足以把上下文吃光；
    2. 编辑要求 old_string 唯一 —— 出现多次就拒绝，逼模型给出足够上下文，
       否则它很可能改错地方。

除工具之外，这个模块还兼着两件事（因为落点都是「工作目录」）：

    - 工作目录本身的读写（current_work_dir / set_work_dir / clear_work_dir）
    - 系统目录选择器与目录浏览（供界面挑目录用）

它们严格说属于「界面/系统」而非「工具」，放在这里是为了保证**界面看到的目录
和工具实际遵守的边界永远是同一个来源**。将来若要拆出去，别让这个来源分裂成两份。
"""

from __future__ import annotations

import base64
import fnmatch
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from quill_agent import changes
from quill_agent.config import get_settings
from quill_agent.preferences import PreferenceStore
from quill_agent.tools.base import registry

# 界面选中的工作目录存这个键。有它就用它，没有就回落配置里的 work_dir。
WORK_DIR_PREF_KEY = "work_dir"

# 上传附件的落脚目录（相对工作目录）。
# 必须放在工作目录**里面** —— 文件工具的安全边界就是工作目录，
# 放到外面去，模型反而读不到这些附件。
ATTACHMENTS_DIR = ".attachments"

# 能当图片发给模型的扩展名。只列两家接口都认的格式：
# bmp / tiff 这些 OpenAI 和 Anthropic 都不收，放进来只会得到一个 400。
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

# 单张图片的体积上限。base64 之后还要再涨三分之一，而它每一轮都要随请求发出去 ——
# 一张 4MB 的图能换掉几千 token 的额度，还未必看得更清楚
MAX_IMAGE_BYTES = 4_000_000

# 搜索时跳过的目录：依赖缓存、版本控制内部数据、构建产物、工具留下的临时文件。
# 不跳过的话，搜一个函数名会被 .venv 里的几万行命中淹掉。
#
# 判据是「内容不是人写的，但可能大量命中」—— 所以 .playwright-cli（浏览器自动化
# 工具存的页面快照）和 .pytest_cache 是一类，尽管它并不常见。
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


def work_dir_change_error(conversation_has_messages: bool) -> str | None:
    """换工作目录前的业务规则；返回错误文案，None 表示可以换。

    只允许**空会话**换：工作目录是文件工具的安全边界，聊到一半换掉的话，模型脑子里
    「我读过哪些文件」和实际边界就对不上了（历史里还留着旧目录的文件内容，而新目录下
    同名文件是另一个东西）。

    这条规则放在业务层而不是路由里 —— 它和界面无关，而**规则留在某一个界面里就会漏**：
    上一个界面（已删掉的 Streamlit 版）就没有这道校验，TUI 再写一遍同样会漏。

    Args:
        conversation_has_messages: 当前会话是否已经有消息。由调用方判断 ——
            业务层不该反向依赖会话存储。
    """
    if conversation_has_messages:
        return "会话已经开始，不能再改工作目录。"
    return None


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


@dataclass(frozen=True)
class SavedAttachment:
    """一个落盘后的附件。

    原先 `save_attachments()` 直接返回「给模型看的那一行文本」，调用方要拿路径
    只能去解析这行字符串 —— 而失败行长得完全不一样。图片要以内容块的形式随消息
    发出去之后，调用方需要的是**结构化**的结果，展示文案由 `describe()` 生成。

    Attributes:
        name: 原始文件名（已取末级名，不含路径）。
        path: 落盘后的绝对路径；保存失败时为 None。
        error: 失败原因；成功时是空串。
    """

    name: str
    path: Path | None = None
    error: str = ""

    @property
    def relative(self) -> str:
        """相对工作目录的路径 —— 这是给模型看的写法。"""
        return f"{ATTACHMENTS_DIR}/{self.name}"

    @property
    def is_image(self) -> bool:
        """是不是能直接发给模型的图片。"""
        return self.path is not None and self.path.suffix.lower() in IMAGE_SUFFIXES

    def describe(self) -> str:
        """一行说明，直接拼进 user 消息。"""
        return f"{self.name}（保存失败：{self.error}）" if self.path is None else self.relative


def save_attachments(files: list) -> list[SavedAttachment]:
    """把界面上传的附件保存进工作目录。

    为什么落盘、而不是把内容直接拼进消息：
        文件工具只能访问工作目录内。落盘之后，模型用现成的 `read_file` /
        `search_content` 就能处理文本类附件，不必为「附件」另造一套工具语义；
        **图片则另外走一条路**：它没法变成文本，只能以内容块的形式随消息发给
        模型的视觉能力（见 agent.image_content），落盘是为了让它留个痕迹、
        也让「本轮消息里那张图」有个可指向的路径。

    同名文件直接覆盖：附件是这一轮的输入，不需要保留历史版本。

    Args:
        files: 上传的文件对象列表（由 server 侧的适配壳提供，见 routes/chat.py）。

    Returns:
        每个附件一条记录。目录都建不出来时返回一条带 error 的记录 ——
        不要返回空列表：那会让调用方以为「这一轮本来就没有附件」。
    """
    if not files:
        return []

    target_dir = current_work_dir() / ATTACHMENTS_DIR
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return [SavedAttachment(name=ATTACHMENTS_DIR, error=f"附件目录创建失败：{exc}")]

    results: list[SavedAttachment] = []
    for item in files:
        # 只取末级文件名：这个名字来自浏览器，不能当作可信路径直接用
        name = Path(str(getattr(item, "name", "attachment"))).name or "attachment"

        try:
            # 上传对象可能是 BytesIO 子类（getbuffer() 能一次拿到全部字节），
            # 也可能只有 read()，两种都认
            raw = item.getbuffer() if hasattr(item, "getbuffer") else item.read()
            path = target_dir / name
            path.write_bytes(bytes(raw))
        except (OSError, AttributeError) as exc:
            results.append(SavedAttachment(name=name, error=str(exc)))
            continue

        results.append(SavedAttachment(name=name, path=path))

    return results


def image_data_url(path: Path) -> str | None:
    """把一张图片读成 data: URL；读不了、太大、或不是图片时返回 None。

    data: URL 而不是「先上传拿一个公网地址」：这是本地优先的应用，
    没有地方托管文件，也不该为了发一张图把用户的文件传出去。
    """
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        return None

    try:
        if path.stat().st_size > MAX_IMAGE_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None

    suffix = path.suffix.lower().lstrip(".")
    mime = "image/jpeg" if suffix in {"jpg", "jpeg"} else f"image/{suffix}"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


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


def _target(
    path: str,
    *,
    want: str = "any",
    must_exist: bool = True,
) -> tuple[Path | None, str]:
    """把模型给的路径解析到工作目录内，并校验存在性与类型。

    这是每个文件工具都要做的第一步。原先它散在七处，文案和校验组合各不相同 ——
    改一条规则要改七遍，还容易漏出行为不一致（比如 write_file 不判存在、
    edit_file 把「目录」也报成「不存在」）。

    Args:
        path: 模型给的路径，相对工作目录或绝对路径都行。
        want: 期望的类型 —— "file" / "dir" / "any"。
        must_exist: 为 False 时允许路径还不存在（write_file 要新建文件）。

    Returns:
        (路径, 错误文案)。成功时错误是空串，失败时路径是 None。
        文案只说明「哪里不对」；「该改用哪个工具」的建议由调用方补上 ——
        那属于各个工具自己的语境，塞进来会让这里变成文案大全。
    """
    try:
        target = guard().resolve(path)
    except ValueError as exc:
        # 越界（../../.ssh/id_rsa 之类）在这里被拦下，文案由 PathGuard 给
        return None, str(exc)

    if not target.exists():
        # 允许新建时不算错：路径本身合法，只是还没落盘
        return (target, "") if not must_exist else (None, f"路径不存在：{path}")

    if want == "file" and target.is_dir():
        return None, f"这是目录不是文件：{path}"
    if want == "dir" and not target.is_dir():
        return None, f"不是目录：{path}"

    return target, ""


def _destination(path: str) -> tuple[Path | None, str]:
    """解析一个**将要写入**的目标路径（新建 / 移动 / 复制的落点）。

    比 `_target` 多两条规矩，都是写操作特有的：

        1. **目标必须还不存在** —— 覆盖是这条链上最容易误伤的动作。两个工具都
           选择「不覆盖」：模型要替换一个已有文件，先 delete_file 是明确的，
           而「复制过去把原来的盖掉」往往不是它真正想要的；
        2. **父目录必须已经存在** —— 顺带把目录建出来听着方便，但一次手滑就能
           在错的地方铺出一整条路径。宁可让调用方先用 make_dir。

    Returns:
        (路径, 错误文案)。成功时错误是空串，失败时路径是 None。
    """
    target, error = _target(path, want="any", must_exist=False)
    if error:
        return None, error

    if target.exists():
        return None, f"目标已存在：{path}（本工具不覆盖已有内容；确实要替换就先 delete_file）"

    if not target.parent.is_dir():
        return None, f"目标所在的目录不存在：{target.parent}（先用 make_dir 建出目录）"

    return target, ""


def _points_to_same(src: Path, raw: str) -> bool:
    """目标写法是否和 `src` 指向同一处。

    解析不出来（越界、非法路径）时一律返回 False —— 那种情况真正的说明由
    `_destination` 给出，这里只负责把「源和目标本来就是同一个」提前挑出来。
    """
    try:
        return guard().resolve((raw or "").strip() or ".") == src
    except ValueError:
        return False


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
    target, error = _target(path, want="dir")
    if error:
        return f"{error}（读文件内容请用 read_file）"

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
    target, error = _target(path, want="file")
    if error:
        return f"{error}（看目录里有什么请用 list_dir）"

    try:
        size = target.stat().st_size
    except OSError as exc:
        return f"无法读取文件信息：{exc}"

    # 二进制文件先给一句有用的回执，别等到解码失败才说「不是 UTF-8 文本文件」——
    # 那句话等于什么都没说，模型读完还是不知道该干什么
    if target.suffix.lower() in IMAGE_SUFFIXES:
        return (
            f"这是一张图片（{target.suffix.lower()}，{size} 字节），不能用 read_file 读。"
            "图片在随附件发出去的时候就已经交给视觉能力了；"
            "如果你看不到画面，说明当前模型不支持图片输入，请让用户改用文字描述。"
        )

    if size > MAX_FILE_BYTES:
        return f"文件太大（{size} 字节，上限 {MAX_FILE_BYTES}），拒绝读取。"

    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return (
            f"这不是文本文件，读不出内容：{path}"
            f"（{target.suffix or '无扩展名'}，{size} 字节）。"
            "图片、PDF、压缩包、Office 文档这类二进制文件都不能用 read_file 读；"
            "如果它是这一轮的附件，请让用户改用文字描述。"
        )
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
    # 允许路径不存在 —— 这个工具的职责就是把它建出来
    target, error = _target(path, want="file", must_exist=False)
    if error:
        return error

    existed = target.exists()

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"无法创建父目录：{exc}"

    # 先记改动再动手：记录器读的是磁盘上那份旧内容，写完再读就没得记了。
    # kind 区分「新建」和「覆盖」—— 界面上那两件事看着完全不同
    changes.record_text(target, content, kind="write" if existed else "create")

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

    target, error = _target(path, want="file")
    if error:
        return f"{error}（edit_file 只能改已经存在的文件）"

    try:
        # 大小上限和 read_file 同一把尺子：edit_file 也得把整个文件读进内存才谈得上
        # 替换，几十 MB 的文件既慢又可能把内存撑爆
        if target.stat().st_size > MAX_FILE_BYTES:
            return f"文件太大（超过 {MAX_FILE_BYTES // 1024} KB），edit_file 不改它。"
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

    changes.record_text(target, updated, kind="edit")

    try:
        _atomic_write(target, updated)
    except OSError as exc:
        return f"写入失败：{exc}"

    return (
        f"已替换 1 处：{path}"
        f"（{len(text.splitlines())} 行 -> {len(updated.splitlines())} 行）"
    )


# ---------------------------------------------------------------------------
# 搜索 / 新建 / 移动 / 复制 / 删除
# ---------------------------------------------------------------------------
def _within(path: Path, boundary: Path) -> bool:
    """路径解析后是否仍在边界内。

    解析（`resolve`）会展开 `..` 并跟随符号链接 —— 和 `PathGuard.resolve` 一个口径。
    """
    try:
        return path.resolve().is_relative_to(boundary)
    except OSError:
        return False


def _walk_files(root: Path, boundary: Path, file_pattern: str = "") -> Iterator[Path]:
    """遍历目录下的文件，顺手剪掉噪声目录与越界的软链。

    用 os.walk 而不是 rglob：要在**进入目录之前**就砍掉 .venv 这类目录。
    先进去再过滤，等于白读几万个文件。

    Args:
        root: 遍历起点。
        boundary: 安全边界（工作目录）。

    为什么这里必须自己判一次边界：`os.walk` 默认不「进入」软链目录，但
    **指向文件的软链仍会出现在 `filenames` 里**。搜索类工具原先直接读这些路径，
    于是「工作目录里放一个指向 `~/.ssh/id_rsa` 的软链」就能让 `search_content`
    读出内容 —— 而同样的文件 `read_file` 会被 PathGuard 拦下。同一个边界两种
    行为，等于没有边界。
    """
    for current, subdirs, filenames in os.walk(root):
        # 原地修改 subdirs 才会让 os.walk 真的跳过这些目录
        subdirs[:] = [name for name in subdirs if name not in IGNORED_DIRS]

        for name in filenames:
            if file_pattern and not fnmatch.fnmatch(name, file_pattern):
                continue

            candidate = Path(current) / name
            # 只在「它是个软链」时才多花一次 resolve()：普通文件的父目录已经在
            # 边界内（遍历起点就是边界内的），没必要对每个文件都解析一遍
            if candidate.is_symlink() and not _within(candidate, boundary):
                continue

            yield candidate


def _read_text_safely(path: Path) -> str | None:
    """尽力把文件当文本读出来；二进制、超大、无权限一律返回 None。"""
    try:
        # 读解析后的路径：检查与读取之间隔着一次系统调用，直接读原路径的话，
        # 软链有机会在这中间被换掉（_walk_files 那道过滤就白做了）
        resolved = path.resolve()
        if resolved.stat().st_size > MAX_FILE_BYTES:
            return None
        return resolved.read_text(encoding="utf-8")
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


# 灾难性回溯的典型形状：一个分组里带重复量词，分组外又跟一个量词（`(a+)+`、`(\w*)*`）。
#
# 为什么要拦：Python 的 `re` **没有超时机制**，一个写歪的模式会让整个进程卡死 ——
# 而这个模式是模型随手生成的、没人审过。这里宁可拒绝一个本来能用的模式
# （模型收到提示后会换个更具体的写法，代价很小），也不要让服务挂在上面。
#
# 这是**启发式**，不是证明：它只覆盖最常见的形状。误伤面刻意压得很小 ——
# 只认「分组里的量词」这一种，像 `a*b*` 这种正常的相邻量词不拦
_NESTED_QUANTIFIER = re.compile(r"\([^()]*[+*}][^()]*\)\s*[+*{]")


def _unsafe_pattern(pattern: str) -> bool:
    """模式里有没有灾难性回溯的典型形状。"""
    return bool(_NESTED_QUANTIFIER.search(pattern))


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
    if _unsafe_pattern(pattern):
        return (
            "这个正则容易被灾难性回溯拖死（分组里带量词、外面又跟量词，比如 `(a+)+`）。"
            "换个写法：把它拆开，或者用一个更具体的前缀。"
        )

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"正则表达式不合法：{exc}"

    root, error = _target(path, want="dir")
    if error:
        return f"{error}（搜索起点需要是一个目录）"

    # 结果里要显示相对工作目录的路径，所以还得拿一次 guard。
    # 它只是把当前工作目录包一层，开销可以忽略
    base = guard().root

    hits: list[str] = []
    scanned = 0
    truncated = False

    for file in _walk_files(root, base, file_pattern):
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

            hits.append(f"{file.relative_to(base)}:{number}: {snippet}")
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
    root, error = _target(path, want="dir")
    if error:
        return f"{error}（查找起点需要是一个目录）"

    # 结果里要显示相对工作目录的路径，所以还得拿一次 guard
    base = guard().root

    matched: list[str] = []
    truncated = False

    for file in _walk_files(root, base):
        if not _match_pattern(file.relative_to(root), pattern):
            continue

        matched.append(str(file.relative_to(base)))
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
        "创建一个目录，父目录不存在时一并创建。已经存在时不算错，会告诉你它本来就在。"
        "要写一个新文件不需要先建目录 —— write_file 会自己建父目录。"
    ),
    category="文件",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径，相对工作目录"},
        },
        "required": ["path"],
    },
)
def make_dir(path: str) -> str:
    """创建目录（含多级父目录）。"""
    # 允许路径不存在 —— 这个工具的职责就是把它建出来
    target, error = _target(path, want="any", must_exist=False)
    if error:
        return error

    if target.exists():
        if target.is_dir():
            return f"目录已存在：{path}"
        return f"已存在同名文件，无法创建目录：{path}"

    try:
        target.mkdir(parents=True)
    except OSError as exc:
        return f"创建目录失败：{exc}"

    return f"已创建目录：{path}"


@registry.tool(
    description=(
        "移动或重命名一个文件 / 目录。"
        "目标路径必须还不存在 —— 本工具不覆盖已有内容。"
        "「重命名」就是移到同一目录下的新名字。"
    ),
    category="文件",
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "要移动的路径，相对工作目录"},
            "destination": {"type": "string", "description": "移动到的目标路径，相对工作目录"},
        },
        "required": ["source", "destination"],
    },
)
def move_file(source: str, destination: str) -> str:
    """移动 / 重命名文件或目录。"""
    src, error = _target(source, want="any")
    if error:
        return f"{error}（要移动的路径必须存在）"

    if src == guard().root:
        return "不能移动工作目录本身。"

    # 源和目标指向同一处时先说明 —— 否则下一步会以「目标已存在」为由拒绝，
    # 而那句报错会把人引偏
    if _points_to_same(src, destination):
        return f"源和目标相同，无需移动：{source}"

    dst, error = _destination(destination)
    if error:
        return error

    # 目录不能移进自己内部：shutil 会抛一句难懂的系统错误，这里提前说清
    if src.is_dir() and dst.is_relative_to(src):
        return "目标在源目录内部，无法移动。"

    kind = "目录" if src.is_dir() else "文件"

    # 移动 = 源没了 + 目标出现，两个都要记：还原时源要写回来、目标要删掉。
    # 目标的 after 传 None —— 它要表达的是「原本不存在」，还原逻辑据此把它删掉
    changes.record_text(src, None, kind="move")
    changes.record_text(dst, None, kind="move")

    try:
        shutil.move(str(src), str(dst))
    except OSError as exc:
        return f"移动失败：{exc}"

    return f"已移动{kind}：{source} -> {destination}"


@registry.tool(
    description=(
        "复制一个文件或目录，原文件保留。"
        "目标路径必须还不存在 —— 本工具不覆盖已有内容。"
        "复制目录时会连同里面的内容一起复制。"
    ),
    category="文件",
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "要复制的路径，相对工作目录"},
            "destination": {"type": "string", "description": "复制到的目标路径，相对工作目录"},
        },
        "required": ["source", "destination"],
    },
)
def copy_file(source: str, destination: str) -> str:
    """复制文件或目录。"""
    src, error = _target(source, want="any")
    if error:
        return f"{error}（要复制的路径必须存在）"

    if src == guard().root:
        return "不能复制整个工作目录。"

    # 同 move_file：源和目标相同时先说明，别让它以「目标已存在」收场
    if _points_to_same(src, destination):
        return f"源和目标相同，无需复制：{source}"

    dst, error = _destination(destination)
    if error:
        return error

    if src.is_dir():
        if dst.is_relative_to(src):
            return "目标在源目录内部，无法复制。"
        try:
            # symlinks=True：目录里的符号链接照原样复制过去，**不跟随**。
            # 跟随的话，一个指向工作目录外的链接会把外面那份内容抄进工作目录 ——
            # 文件工具承诺的是「只碰工作目录」，不能从这个口子漏进来
            shutil.copytree(src, dst, symlinks=True)
        except OSError as exc:
            return f"复制失败：{exc}"
        return f"已复制目录：{source} -> {destination}"

    # 复制只动目标：源还在原处，记它没有意义（还原时也轮不到它）
    changes.record_text(dst, None, kind="copy")

    try:
        shutil.copy2(src, dst)  # copy2 连时间戳一起带过去
    except OSError as exc:
        return f"复制失败：{exc}"

    return f"已复制文件：{source} -> {destination}"


@registry.tool(
    description=(
        "删除一个文件，或一个**空**目录。确认它确实不再需要时才使用 —— 这是不可撤销的操作。"
        "目录非空时会拒绝，需要先清掉里面的内容。"
    ),
    category="文件",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要删除的路径，相对工作目录"},
        },
        "required": ["path"],
    },
)
def delete_file(path: str) -> str:
    """删除文件或空目录。"""
    target, error = _target(path, want="any")
    if error:
        return error

    if target == guard().root:
        return "不能删除工作目录本身。"

    if target.is_dir():
        # 只删空目录。递归删除是另一回事 —— 一次误判就没了整棵目录树，
        # 这个工具不打算承担那种风险
        try:
            target.rmdir()
        except OSError as exc:
            return f"删除失败：{exc}（本工具只删除空目录，请先清空里面的内容）"
        return f"已删除空目录：{path}"

    # 删之前把内容记下来 —— 否则「还原」对这条改动无能为力，而删除正是最想要后悔药的操作
    changes.record_text(target, None, kind="delete")

    try:
        target.unlink()
    except OSError as exc:
        return f"删除失败：{exc}"

    return f"已删除文件：{path}"
