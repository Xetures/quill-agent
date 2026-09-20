"""提示词库：管理 prompt/ 目录下的提示词文件。

每条提示词是一个 `.md` 文件。**文件名是稳定 id，名字与分类写在文件头的元信息块里**：

    prompt/
    ├── 8f3a2b1c.md
    │       ---
    │       name: 通用能力
    │       category: 能力
    │       ---
    │
    │       （正文）
    └── 5d9e0f12.md

为什么不沿用「目录当分类、文件名当名字」（那是更早的写法）：

    1. **中文路径的麻烦是实打实的**。最阴的一条是 Unicode 规范化：macOS 把文件名存成
       NFD（`能` 拆成基字符 + 组合符），Linux / Windows 用 NFC —— 同一个中文目录名在
       两个系统上是**不同字节**，git 会当成两个文件，克隆/同步时冒出「幽灵重命名」。
       此外还有 Windows 命令行的 GBK 编码、git 的八位转义显示、打包时的 UTF-8 标志位。
    2. **改名会破坏引用**。名字一旦就是文件名，重命名等于换了标识 —— 所有引用它的
       提示词组都会静默失效。用 id 之后，改名只动元信息，引用不动。

元信息块的解析规则与技能共用（见 `skills.split_frontmatter`）：只认单行 `键: 值`，
所以**名字里不能有换行**（写入前会压平）。用户手改文件时把块写坏了也不会丢内容 ——
解析失败时整段都当正文，界面上名字退化成 id。
"""

from __future__ import annotations

import contextlib
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from quill_agent.locking import atomic_write_text
from quill_agent.skills import FRONTMATTER_FENCE, split_frontmatter

# 提示词的分类。元组的顺序就是拼接进 system prompt 的顺序（身份在最前、约束在最后），
# 也是界面上的展示顺序 —— 顺序固定，同一模式下拼出来的内容才完全一致，前缀缓存才有意义。
PROMPT_CATEGORIES: tuple[str, ...] = (
    "身份",
    "能力",
    "工具策略",
    "工作流程",
    "输出规范",
    "约束",
)

MARKDOWN_SUFFIX = ".md"

# 展示名的长度上限。它不再是文件名，所以不受 `naming.safe_name` 那套限制，
# 但太长的名字在表格里会撑破版面
MAX_NAME_CHARS = 60

# 提示词 id：8 位十六进制。固定长度 + 只含 [0-9a-f]，天然是安全的文件名，
# 也彻底避开了编码、大小写（Windows 不区分大小写）、NFC/NFD 这些问题
_ID_PATTERN = re.compile(r"^[0-9a-f]{8}$")

_CATEGORY_ORDER = {name: index for index, name in enumerate(PROMPT_CATEGORIES)}


def new_prompt_id() -> str:
    """生成一个新的提示词 id。"""
    return secrets.token_hex(4)


def clean_name(name: str) -> str:
    """校验并规整展示名。

    比文件名宽松得多 —— 它只出现在界面上和拼接结果里，`/`、`:` 这些都可以用。
    唯一的硬要求是**不能有换行**：元信息块是逐行 `键: 值` 解析的，名字里带换行
    会把结构撑坏。这里直接把换行和多余空白压成单个空格。

    Raises:
        ValueError: 名字为空或超长（文案可直接展示）。
    """
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise ValueError("名称不能为空。")
    if len(cleaned) > MAX_NAME_CHARS:
        raise ValueError(f"名称不能超过 {MAX_NAME_CHARS} 个字符。")
    return cleaned


def check_category(category: str) -> str:
    """校验分类；不在六类里就报错（用于写入路径）。

    读取时**不**这么严：用户手改元信息把分类写坏，不该让那条提示词从列表里消失，
    只是排序时排到最后、界面上显示成未知分类而已。
    """
    if category not in PROMPT_CATEGORIES:
        raise ValueError(f"未知的提示词类别：{category}")
    return category


@dataclass(frozen=True)
class PromptItem:
    """一条提示词的元信息（不含正文）—— 列表、下拉、引用解析只需要这些。"""

    id: str
    name: str
    category: str


@dataclass(frozen=True)
class PromptDetail:
    """一条提示词的完整内容。"""

    id: str
    name: str
    category: str
    content: str


class PromptLibrary:
    """读写 prompt/ 目录下的提示词文件。

    Args:
        root: 提示词目录；不存在时会在首次写入时自动创建。
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def ensure_dir(self) -> None:
        """确保目录存在（首次运行时自动建好）。"""
        self._root.mkdir(parents=True, exist_ok=True)

    def path_of(self, prompt_id: str) -> Path:
        """id -> 文件路径。

        id 是路径的一环，所以必须先校验格式：不校验的话 `../../etc/passwd` 这样的
        「id」就能让读写跑到 prompt/ 之外去。名字（可能含任意字符）不在路径里，
        这是这套结构顺带的好处之一。

        Raises:
            ValueError: id 格式不合法。
        """
        if not _ID_PATTERN.match(prompt_id or ""):
            raise ValueError(f"提示词 id 不合法：{prompt_id}")

        return self._root / f"{prompt_id}{MARKDOWN_SUFFIX}"

    def list_items(self) -> list[PromptItem]:
        """全部提示词的元信息，按「分类顺序 + 名字」排序。

        不认识的文件（不是 `<8位hex>.md`）直接跳过：用户完全可能在这个目录里放
        草稿、附件、README，那些不该被当成提示词。
        """
        items: list[PromptItem] = []

        if not self._root.is_dir():
            return items

        for path in self._root.glob(f"*{MARKDOWN_SUFFIX}"):
            if not path.is_file() or not _ID_PATTERN.match(path.stem):
                continue

            meta, _ = split_frontmatter(path.read_text(encoding="utf-8"))
            items.append(
                PromptItem(
                    id=path.stem,
                    # 元信息缺了就退化成 id：用户手改文件把块写坏时，至少还能看见它
                    name=(meta.get("name") or "").strip() or path.stem,
                    category=(meta.get("category") or "").strip(),
                )
            )

        return sorted(items, key=_sort_key)

    def get(self, prompt_id: str) -> PromptItem | None:
        """按 id 取元信息；不存在返回 None。"""
        return next((item for item in self.list_items() if item.id == prompt_id), None)

    def read(self, prompt_id: str) -> str | None:
        """取正文；文件不存在返回 None。

        「正文是空的」和「文件不存在」是两回事：前者说明作者建了文件还没写内容。
        """
        path = self.path_of(prompt_id)
        if not path.is_file():
            return None

        _, body = split_frontmatter(path.read_text(encoding="utf-8"))
        return body

    def detail(self, prompt_id: str) -> PromptDetail | None:
        """取元信息 + 正文；文件不存在返回 None。"""
        item = self.get(prompt_id)
        if item is None:
            return None

        return PromptDetail(
            id=item.id,
            name=item.name,
            category=item.category,
            content=self.read(prompt_id) or "",
        )

    def create(self, *, name: str, category: str, content: str) -> PromptDetail:
        """新建一条提示词，id 由本方法生成。

        Raises:
            ValueError: 名字不合法、或分类不在六类里。
        """
        clean = clean_name(name)
        check_category(category)
        prompt_id = self._fresh_id()
        self._write(prompt_id, clean, category, content)

        return PromptDetail(id=prompt_id, name=clean, category=category, content=content.strip())

    def save(self, prompt_id: str, *, name: str, category: str, content: str) -> PromptDetail:
        """覆盖一条提示词；名字与分类都可以改（引用用的是 id，不怕改）。

        Raises:
            ValueError: id 不合法、文件不存在、名字不合法、或分类不在六类里。
        """
        if not self.path_of(prompt_id).is_file():
            raise ValueError(f"没有找到提示词 {prompt_id}。")

        clean = clean_name(name)
        check_category(category)
        self._write(prompt_id, clean, category, content)

        return PromptDetail(id=prompt_id, name=clean, category=category, content=content.strip())

    def delete(self, prompt_id: str) -> str:
        """删除一条提示词，返回 id。

        Raises:
            ValueError: id 不合法，或这条提示词本来就不存在。
                **不存在时报错而不是静默通过**：界面上删一个已经不存在的条目，
                多半是视图过期了，说一声比假装成功有用（与 `SkillLibrary.delete` 同口径）。
        """
        path = self.path_of(prompt_id)
        if not path.is_file():
            raise ValueError(f"没有找到提示词 {prompt_id}，没有删除任何东西。")

        path.unlink()
        return prompt_id

    # ── 内部 ──────────────────────────────────────────────────────────

    def _fresh_id(self) -> str:
        """生成一个当前没被占用的 id（8 位 hex 撞车概率极低，但撞了就再要一个）。"""
        for _ in range(100):
            candidate = new_prompt_id()
            if not self.path_of(candidate).exists():
                return candidate

        raise RuntimeError("连续 100 次都没生成出没被占用的提示词 id，这不该发生。")

    def _write(self, prompt_id: str, name: str, category: str, content: str) -> None:
        """写文件：元信息块由代码拼，正文原样放后面。"""
        front = (
            f"{FRONTMATTER_FENCE}\nname: {name}\ncategory: {category}\n{FRONTMATTER_FENCE}"
        )
        body = content.strip()
        text = f"{front}\n\n{body}\n" if body else f"{front}\n"
        atomic_write_text(self.path_of(prompt_id), text)


def _sort_key(item: PromptItem) -> tuple[int, str]:
    """排序键：先按分类顺序，同类别内按名字。

    分类写错（不在六类里）的排在最后 —— 不隐藏用户的东西，但也不让它插到中间
    打乱顺序。
    """
    return (_CATEGORY_ORDER.get(item.category, len(PROMPT_CATEGORIES)), item.name)


def migrate_layout(root: str | Path) -> tuple[dict[tuple[str, str], str], int]:
    """把旧的「目录当分类、文件名当名字」结构搬到新结构。

    旧的：

        prompt/能力/通用能力.md

    新的：

        prompt/<id>.md，文件头写 `name: 通用能力` 与 `category: 能力`

    Returns:
        `({(分类, 名字): id}, 真搬过来的条数)`。映射同时包含**本来就在新结构里**的
        条目 —— 提示词组的旧引用存的是「分类 + 名字」，要靠它才能对上 id
        （见 `store.migrate_prompt_group_refs`）。

    幂等：重复调用不会重复搬、也不会生成第二份副本。旧目录搬空之后会被删掉。
    """
    library = PromptLibrary(root)
    mapping: dict[tuple[str, str], str] = {}
    moved = 0

    # 先收下已经在新结构里的（重复运行、或用户手工建的）
    for item in library.list_items():
        mapping[(item.category, item.name)] = item.id

    if not library._root.is_dir():  # noqa: SLF001 —— 同模块内部，直接用
        return mapping, moved

    for category in PROMPT_CATEGORIES:
        directory = library._root / category
        if not directory.is_dir():
            continue

        for path in sorted(directory.glob(f"*{MARKDOWN_SUFFIX}")):
            if not path.is_file():
                continue

            key = (category, path.stem)
            if key not in mapping:
                prompt_id = library._fresh_id()
                library._write(prompt_id, path.stem, category, path.read_text(encoding="utf-8"))
                mapping[key] = prompt_id
                moved += 1

            path.unlink()

        # 内容都搬完就删掉空目录。用 rmdir（不是 rmtree）：目录里要是还有别的东西
        # （用户的草稿、附件），宁可留着让人看见，也不要替他决定删掉
        with contextlib.suppress(OSError):
            directory.rmdir()

    return mapping, moved
