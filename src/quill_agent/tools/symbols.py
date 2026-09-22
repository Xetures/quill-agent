"""符号检索：定义在哪个文件、被谁引用。

**为什么是正则，不是 LSP / tree-sitter。** 那两条路都更准，但代价和这个项目的立身之本
冲突：

- **LSP** 要为每种语言起一个 server（pyright、gopls、rust-analyzer…），还要管它们的
  生命周期和崩溃。项目现在的依赖只有五个，靠这个才做到「clone 下来 `make api` 就能跑」；
- **tree-sitter** 要随包带每个语言的 grammar（C 扩展，每个一两 MB），打包体积和跨平台
  编译跟着一起变复杂。

所以这里给的是**启发式**：按扩展名选一组正则，把候选缩到几个文件、几个行号。它**不保证
准** —— 注释里的词、其他语言文件里的同名符号都会混进来。这一点写进了工具描述里，让模型
知道该用 `read_file` 复核，而不是把结果当结论。

将来真要做得准，把这两个函数的**实现**换掉即可：签名、工具名、描述都不用动，模型和提示词
一行都不用改。
"""

from __future__ import annotations

import re
from pathlib import Path

from quill_agent.tools.base import registry
from quill_agent.tools.files import current_work_dir

# 每个扩展名一条「定义」正则。都锚在行首（Python 那类允许缩进，所以是 `^\s*`），
# 不锚的话会把调用、注释、字符串里的同名片段一起捞进来 —— 那是最主要的误报来源。
_DEFINITIONS: dict[str, re.Pattern[str]] = {
    ".py": re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+(?P<name>\w+)"),
    ".js": re.compile(
        r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+(?P<name>\w+)"
        r"|^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name2>\w+)\s*=\s*(?:async\s+)?[(\w]"
    ),
    ".ts": re.compile(
        r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|interface|enum|type)\s+(?P<name>\w+)"
        r"|^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name2>\w+)\s*=\s*(?:async\s+)?[(\w]"
    ),
    ".go": re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(?P<name>\w+)"),
    ".rs": re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?(?:fn|struct|enum|trait)\s+(?P<name>\w+)"),
    ".java": re.compile(
        r"^\s*(?:public|private|protected|static|final|abstract|synchronized|\s)*"
        r"\w[\w<>\[\],\s]*\s+(?P<name>\w+)\s*\("
    ),
}
_DEFINITIONS[".tsx"] = _DEFINITIONS[".ts"]
_DEFINITIONS[".jsx"] = _DEFINITIONS[".js"]
_DEFINITIONS[".mjs"] = _DEFINITIONS[".js"]

# 扫描上限。这是给「把候选缩到几个文件」用的，不是给全仓库建索引 ——
# 真到几万个文件的规模，该换的是实现（见模块开头），不是把上限调大
MAX_FILES = 400
MAX_HITS = 60

# 这些目录要么不是源码，要么是生成物，扫了只会浪费时间并淹没真正的结果
_SKIP_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".pytest_cache"}
)


def _language_of(path: Path) -> re.Pattern[str] | None:
    return _DEFINITIONS.get(path.suffix.lower())


def _walk(root: Path) -> list[Path]:
    """工作目录下所有「能识别」的源码文件（跳过生成物目录和隐藏目录）。"""
    found: list[Path] = []
    boundary = current_work_dir().resolve()

    for path in root.rglob("*"):
        if len(found) >= MAX_FILES:
            break
        if not path.is_file() or _language_of(path) is None:
            continue
        try:
            relative = path.resolve().relative_to(boundary)
        except ValueError:
            continue  # 顺着符号链接跑到工作目录外了
        if any(part in _SKIP_DIRS or part.startswith(".") for part in relative.parts[:-1]):
            continue
        found.append(path)

    return sorted(found)


def _read_lines(path: Path) -> list[str]:
    """读成行。读不了（权限、二进制）就当空文件 —— 检索工具不该因为这个整体失败。"""
    try:
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []


def _define_name(line: str, pattern: re.Pattern[str]) -> str | None:
    """命中的定义行里，符号名是什么。"""
    match = pattern.match(line)
    if match is None:
        return None
    return match.group("name") or match.groupdict().get("name2")


def _resolve(path: str) -> tuple[Path | None, str]:
    """把模型给的路径解析到工作目录内。"""
    root = current_work_dir().resolve()
    candidate = Path(path).expanduser()
    target = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()

    if not target.is_relative_to(root):
        return None, f"路径在工作目录之外：{path}"
    if not target.exists():
        return None, f"路径不存在：{path}"
    return target, ""


@registry.tool(
    description=(
        "列出一个文件（或目录）里的符号骨架：类、函数、方法，带行号。"
        "**读一个长文件之前先用它** —— 800 行的文件整读要上万 token，骨架只要几十行，"
        "看清结构之后再决定读哪一段。"
        "只认 Python / JS / TS / Go / Rust / Java 这几类文件。"
    ),
    category="代码",
    parameters={
        "properties": {
            "path": {"type": "string", "description": "文件或目录，默认当前工作目录。"}
        }
    },
)
def outline(path: str = ".") -> str:
    target, error = _resolve(path)
    if target is None:
        return error

    if target.is_file():
        pattern = _language_of(target)
        if pattern is None:
            supported = ", ".join(sorted(_DEFINITIONS))
            return f"这个文件类型还不支持符号检索：{target.name}（支持 {supported}）"
        found = [
            (index, line.strip(), _define_name(line, pattern))
            for index, line in enumerate(_read_lines(target), start=1)
            if pattern.match(line)
        ]
        if not found:
            return f"{target.name} 里没有找到类或函数定义。"
        body = "\n".join(f"{index:>5}  {name}" for index, _, name in found if name)
        return f"{target.name}\n{body}"

    # 目录：按文件分组列
    files = _walk(target)
    if not files:
        return f"{path} 下没有可识别的源码文件。"

    blocks: list[str] = []
    root = current_work_dir().resolve()
    for file in files:
        pattern = _language_of(file)
        assert pattern is not None  # _walk 只收认识的类型
        names = [
            (index, _define_name(line, pattern))
            for index, line in enumerate(_read_lines(file), start=1)
            if pattern.match(line)
        ]
        names = [(index, name) for index, name in names if name]
        if not names:
            continue
        head = file.resolve().relative_to(root).as_posix()
        body = "\n".join(f"{index:>5}  {name}" for index, name in names[:MAX_HITS])
        blocks.append(f"{head}\n{body}")

    if not blocks:
        return f"{path} 下的文件里没有找到类或函数定义。"
    return "\n\n".join(blocks)


@registry.tool(
    description=(
        "跨文件搜索一个符号（类名 / 函数名），找它的定义和引用位置。"
        "想知道「这个函数在哪儿定义的、还有谁在用它」时用它 —— 比 search_content 准，"
        "因为它只认定义行的写法，不会把每一处出现都列出来。"
    ),
    category="代码",
    parameters={
        "properties": {
            "symbol": {"type": "string", "description": "要搜的符号名，区分大小写。"},
            "kind": {
                "type": "string",
                "description": "definition（只看定义）/ reference（只看引用）/ any，默认 any。",
            },
        }
    },
    required=["symbol"],
)
def find_symbol(symbol: str, kind: str = "any") -> str:
    name = symbol.strip()
    if not name:
        return "请给出要搜索的符号名。"

    want_definition = kind in ("any", "definition")
    want_reference = kind in ("any", "reference")
    # 词边界：搜 `read` 不该匹配到 `read_file`。`$` 在正则里有含义，这里按转义处理
    word = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")

    definitions: list[str] = []
    references: list[str] = []
    root = current_work_dir().resolve()

    for file in _walk(root):
        pattern = _language_of(file)
        assert pattern is not None
        relative = file.resolve().relative_to(root).as_posix()

        for index, line in enumerate(_read_lines(file), start=1):
            if not word.search(line):
                continue
            defined = pattern.match(line) is not None and _define_name(line, pattern) == name
            if defined and want_definition:
                definitions.append(f"{relative}:{index}  {line.strip()}")
            elif not defined and want_reference:
                references.append(f"{relative}:{index}  {line.strip()}")
            if len(definitions) + len(references) > MAX_HITS * 2:
                break

    if not definitions and not references:
        return f"没有找到符号「{name}」。注意这是**文本匹配**，不是编译器级分析。"

    blocks: list[str] = []
    if properties := definitions[:MAX_HITS]:
        blocks.append("定义：\n" + "\n".join(properties))
    if uses := references[:MAX_HITS]:
        blocks.append("引用：\n" + "\n".join(uses))

    # 把「这是候选」这句话放在结果里，而不是只写在工具描述里：模型看到的是返回值，
    # 描述只在一开始出现过一次
    blocks.append("（以上是文本匹配的候选，注释和同名变量也会混进来 —— 拿不准就看那几行）")
    return "\n\n".join(blocks)
