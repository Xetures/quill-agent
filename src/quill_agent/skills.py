"""技能库：管理 skills/ 目录下的技能包。

一个「技能」就是一个子目录，里面至少有一个 SKILL.md：

    skills/
    ├── 代码审查/
    │   └── SKILL.md
    └── 提交信息/
        └── SKILL.md

SKILL.md = 文件头的元信息块 + 正文：

    ---
    description: 一句话说清「什么时候该加载我」
    ---

    （正文：这类任务的完整做法，可以写得很长）

**技能和提示词的关键区别在加载时机。** 提示词的正文每轮都被全量拼进 system
prompt；技能平时只贡献清单里的「名字 + 一句话描述」，模型判断任务匹配时才用
read_skill 工具把正文取回来。所以技能可以又长又专，而不会拖累无关的对话。

几个和 prompt/ 一致的取舍：
    - 正文是文件：用户用编辑器维护，能进版本控制；
    - 目录名就是技能名：和 prompt/ 拿文件名当标识是一个道理；
    - 元信息只有 description 一项，所以自己解析、不引 YAML 依赖。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from quill_agent.naming import safe_name

# 技能目录里约定的入口文件名
SKILL_FILENAME = "SKILL.md"

# 元信息块的定界符
FRONTMATTER_FENCE = "---"


@dataclass(frozen=True)
class SkillMeta:
    """技能的元信息 —— 拼「可用技能」清单只需要这些。

    Attributes:
        name: 技能名，也就是目录名。
        description: 什么时候该加载它；留空表示作者没写。
    """

    name: str
    description: str


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """把技能文件拆成 (元信息, 正文)。

    元信息块必须从文件第一行开始，用 `---` 包起来：

        ---
        description: 什么时候用我
        ---

    只支持单行 `键: 值`：技能的元信息目前只有 description 一项，
    为它引一个 YAML 解析库不划算。

    没有元信息块（或者有开头没结尾）时，返回 ({}, 原文) —— 宁可少解析，
    也不要把正文误当成元信息吃掉。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_FENCE:
        return {}, text

    for index in range(1, len(lines)):
        if lines[index].strip() != FRONTMATTER_FENCE:
            continue

        meta: dict[str, str] = {}
        for line in lines[1:index]:
            key, separator, value = line.partition(":")
            if separator:
                meta[key.strip()] = value.strip()

        return meta, "\n".join(lines[index + 1 :]).strip()

    return {}, text


def compose_skill(description: str, body: str) -> str:
    """把「使用场景 + 正文」拼回一个 SKILL.md 的完整内容。

    **元信息由代码拼，不让用户直接编。** `split_frontmatter` 只认单行 `键: 值`，
    用户在编辑器里多写一行、缩进一下，解析就会**静默失败** —— description 丢掉，
    而它正是模型判断「什么时候该用我」的唯一依据。界面上把 description 做成独立的
    输入框，正文只编 body，这个函数负责合起来，用户就没有机会把结构弄坏。

    `description` 为空时不写元信息块（保持「没有块」这种合法形态，见 split_frontmatter）。
    """
    text = body.strip()
    if not description.strip():
        return f"{text}\n" if text else ""

    front = f"{FRONTMATTER_FENCE}\ndescription: {description.strip()}\n{FRONTMATTER_FENCE}"
    return f"{front}\n\n{text}\n"


class SkillLibrary:
    """读写 skills/ 目录下的技能包。

    Args:
        root: 技能根目录；它下面每个子目录是一个技能。
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def ensure_dir(self) -> None:
        """确保根目录存在（首次运行时自动建好）。"""
        self._root.mkdir(parents=True, exist_ok=True)

    def skill_dir(self, name: str) -> Path:
        """某个技能的目录路径。"""
        return self._root / name

    def path_of(self, name: str) -> Path:
        """某个技能的入口文件路径。"""
        return self.skill_dir(name) / SKILL_FILENAME

    def exists(self, name: str) -> bool:
        """判断技能是否可用。"""
        return self.path_of(name).is_file()

    def list_names(self) -> list[str]:
        """全部技能名（即目录名），按名称排序。

        只认「目录里有 SKILL.md」的，这样在 skills/ 下放别的东西
        （草稿、附件目录）不会被误当成技能。
        """
        if not self._root.is_dir():
            return []

        names = [
            item.name
            for item in self._root.iterdir()
            if item.is_dir() and (item / SKILL_FILENAME).is_file()
        ]
        return sorted(names)

    def read(self, name: str) -> str | None:
        """读取技能正文（不含元信息块）；技能不存在时返回 None。

        正文为空串和技能不存在是两回事：前者说明作者建了目录还没写内容，
        调用方需要分别处理。
        """
        path = self.path_of(name)
        if not path.is_file():
            return None

        _, body = split_frontmatter(path.read_text(encoding="utf-8"))
        return body.strip()

    def save(
        self,
        name: str,
        description: str,
        body: str,
        *,
        create_only: bool = False,
    ) -> str:
        """写入一个技能，返回最终使用的名字（已去掉首尾空白）。

        调用方给的是**拆开的两部分**（使用场景 + 正文），拼装由 `compose_skill`
        负责 —— 见那个函数的说明。

        Args:
            create_only: 新建时置真。同名技能已存在就报错，不覆盖。

        Raises:
            ValueError: 名字不合法，或重名（文案可直接展示）。
        """
        clean = safe_name(name)
        path = self.skill_dir(clean) / SKILL_FILENAME

        if create_only and path.exists():
            raise ValueError(f"技能「{clean}」已经存在了，换个名字，或直接编辑它。")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(compose_skill(description, body), encoding="utf-8")
        return clean

    def meta(self, name: str) -> SkillMeta | None:
        """读取技能元信息；技能不存在时返回 None。

        没写 description 时是空串 —— 技能照样能用，只是模型少了一条
        「什么时候该用它」的线索。
        """
        path = self.path_of(name)
        if not path.is_file():
            return None

        data, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        return SkillMeta(name=name, description=data.get("description", "").strip())

    def list_meta(self) -> list[SkillMeta]:
        """全部技能的元信息（顺便过滤掉读不出来的目录）。"""
        metas = (self.meta(name) for name in self.list_names())
        return [item for item in metas if item is not None]
