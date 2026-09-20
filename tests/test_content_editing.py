"""提示词 / 技能的写入：名字安全校验、重名保护、技能元信息的拼装。

这一批用例的重点全在「不信任输入」上：名字直接来自输入框，而它就是文件名。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quill_agent.naming import safe_name
from quill_agent.prompts import PromptLibrary, migrate_layout
from quill_agent.skills import SkillLibrary, compose_skill, split_frontmatter

# ---------------------------------------------------------------------------
# 名字：能不能当文件名用
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "../etc/passwd",
        "a/b",
        "a\\b",
        ".hidden",
        "a?b",
        "a*b",
        "带\n换行",
        "trailing.",
        "x" * 61,
        "CON",
        "com1",
        # 带扩展名同样是保留名：Windows 判定设备名时不看扩展名，
        # 而提示词落盘用的正是「名字 + .md」
        "CON.txt",
        "nul.md",
    ],
)
def test_safe_name_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        safe_name(bad)


def test_safe_name_trims_and_keeps_chinese() -> None:
    assert safe_name("  Agent助手  ") == "Agent助手"


# ---------------------------------------------------------------------------
# 提示词
# ---------------------------------------------------------------------------


def test_prompt_create_then_update(tmp_path: Path) -> None:
    library = PromptLibrary(tmp_path / "prompt")

    created = library.create(name="助手", category="身份", content="第一版")

    assert library.read(created.id) == "第一版"
    assert (created.name, created.category) == ("助手", "身份")

    library.save(created.id, name="助手", category="身份", content="第二版")

    assert library.read(created.id) == "第二版"


def test_the_file_name_is_an_id_not_the_name(tmp_path: Path) -> None:
    """文件名是 id，中文名不进路径。

    这是这次改造的核心动机：macOS 把中文名存成 NFD、Linux/Windows 用 NFC，
    同一个名字在两边是**不同的字节序列**，git 会当成两个文件。
    """
    root = tmp_path / "prompt"
    created = PromptLibrary(root).create(name="通用能力", category="能力", content="正文")

    files = [path.stem for path in root.glob("*.md")]

    assert files == [created.id]
    assert created.id.isascii()


def test_renaming_and_recategorising_keep_the_reference(tmp_path: Path) -> None:
    """改名、换分类都只动元信息，id 不变 —— 引用它的提示词组不会失效。"""
    library = PromptLibrary(tmp_path / "prompt")
    created = library.create(name="旧名字", category="能力", content="正文")

    library.save(created.id, name="新名字", category="工作流程", content="正文")

    item = library.get(created.id)
    assert item is not None
    assert (item.name, item.category) == ("新名字", "工作流程")


def test_duplicate_names_are_allowed(tmp_path: Path) -> None:
    """名字不再是标识，重名合法（表格里并排显示，用户自己看得见）。"""
    library = PromptLibrary(tmp_path / "prompt")

    first = library.create(name="助手", category="身份", content="甲")
    second = library.create(name="助手", category="身份", content="乙")

    assert first.id != second.id
    # 比集合不比顺序：两条同名同类，排序键完全一样，谁在前由 id 决定（随机的）
    assert {item.id for item in library.list_items()} == {first.id, second.id}


def test_prompt_rejects_a_category_outside_the_six(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="类别"):
        PromptLibrary(tmp_path / "prompt").create(name="x", category="随便编一个", content="y")


def test_prompt_id_cannot_escape_the_prompt_dir(tmp_path: Path) -> None:
    """id 是路径的一环，必须校验格式。名字不在路径里，随便写都无所谓。"""
    library = PromptLibrary(tmp_path / "prompt")

    with pytest.raises(ValueError):
        library.path_of("../../逃出去")

    assert not (tmp_path / "逃出去.md").exists()


def test_a_name_with_a_newline_is_flattened(tmp_path: Path) -> None:
    """名字里不能有换行：元信息块是逐行 `键: 值` 解析的，换行会把结构撑坏。"""
    library = PromptLibrary(tmp_path / "prompt")

    created = library.create(name="  第一行\n第二行  ", category="能力", content="正文")

    assert created.name == "第一行 第二行"


def test_prompt_delete(tmp_path: Path) -> None:
    library = PromptLibrary(tmp_path / "prompt")
    created = library.create(name="助手", category="身份", content="正文")

    assert library.delete(created.id) == created.id
    assert library.read(created.id) is None

    with pytest.raises(ValueError, match="没有找到"):
        library.delete(created.id)


def test_files_that_are_not_ours_are_ignored(tmp_path: Path) -> None:
    """用户完全可能在这个目录里放草稿、README —— 那些不该被当成提示词。"""
    root = tmp_path / "prompt"
    root.mkdir()
    (root / "README.md").write_text("说明", encoding="utf-8")
    (root / "草稿.md").write_text("草稿", encoding="utf-8")

    assert PromptLibrary(root).list_items() == []


def test_items_are_sorted_by_category_then_name(tmp_path: Path) -> None:
    """拼接顺序要稳定：先按分类顺序（身份 → … → 约束），同类内按名字。"""
    library = PromptLibrary(tmp_path / "prompt")
    library.create(name="丙", category="约束", content="x")
    library.create(name="甲", category="身份", content="x")
    library.create(name="乙", category="身份", content="x")

    assert [(item.category, item.name) for item in library.list_items()] == [
        ("身份", "乙"),
        ("身份", "甲"),
        ("约束", "丙"),
    ]


# ---------------------------------------------------------------------------
# 迁移：旧的「目录当分类」结构 → 新的 id 文件
# ---------------------------------------------------------------------------


def test_migrate_moves_legacy_directories(tmp_path: Path) -> None:
    root = tmp_path / "prompt"
    (root / "能力").mkdir(parents=True)
    (root / "能力" / "通用能力.md").write_text("旧正文", encoding="utf-8")

    mapping, moved = migrate_layout(root)

    assert moved == 1
    assert PromptLibrary(root).read(mapping[("能力", "通用能力")]) == "旧正文"
    assert not (root / "能力").exists()


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    """重复启动不能重复搬 —— 否则每次开应用都会多出一份副本。"""
    root = tmp_path / "prompt"
    (root / "身份").mkdir(parents=True)
    (root / "身份" / "助手.md").write_text("正文", encoding="utf-8")

    first, moved_first = migrate_layout(root)
    second, moved_second = migrate_layout(root)

    assert (moved_first, moved_second) == (1, 0)
    assert first == second


# ---------------------------------------------------------------------------
# 技能：拆开的两部分怎么拼回 SKILL.md
# ---------------------------------------------------------------------------


def test_compose_and_split_round_trip() -> None:
    text = compose_skill("什么时候用我", "正文第一行\n\n正文第二段")
    meta, body = split_frontmatter(text)

    assert meta["description"] == "什么时候用我"
    assert body == "正文第一行\n\n正文第二段"


def test_compose_without_description_writes_no_frontmatter() -> None:
    """没写使用场景时不要留一个空元信息块 —— 「没有块」是合法形态。"""
    text = compose_skill("", "只有正文")

    assert not text.startswith("---")
    assert split_frontmatter(text)[0] == {}


def test_skill_save_keeps_description_out_of_the_body(tmp_path: Path) -> None:
    library = SkillLibrary(tmp_path / "skills")
    library.save("代码审查", "审查代码时使用", "先取证，再下结论", create_only=True)

    # 正文不含元信息块，使用场景单独能读到
    assert library.read("代码审查") == "先取证，再下结论"
    meta = library.meta("代码审查")
    assert meta is not None
    assert meta.description == "审查代码时使用"


def test_skill_update_rewrites_both_parts(tmp_path: Path) -> None:
    library = SkillLibrary(tmp_path / "skills")
    library.save("提交信息", "写提交信息时使用", "旧正文")

    library.save("提交信息", "写 PR 描述时也用", "新正文")

    assert library.read("提交信息") == "新正文"
    meta = library.meta("提交信息")
    assert meta is not None
    assert meta.description == "写 PR 描述时也用"


def test_skill_create_refuses_to_overwrite(tmp_path: Path) -> None:
    library = SkillLibrary(tmp_path / "skills")
    library.save("代码审查", "旧", "旧正文")

    with pytest.raises(ValueError, match="已经存在"):
        library.save("代码审查", "覆盖", "新正文", create_only=True)

    assert library.read("代码审查") == "旧正文"


def test_skill_name_cannot_escape_the_skills_dir(tmp_path: Path) -> None:
    library = SkillLibrary(tmp_path / "skills")

    with pytest.raises(ValueError):
        library.save("../跑出去", "d", "b")

    assert not (tmp_path / "跑出去").exists()


def test_new_skill_shows_up_in_the_list(tmp_path: Path) -> None:
    library = SkillLibrary(tmp_path / "skills")
    library.save("代码审查", "审查代码时使用", "正文")

    assert library.list_names() == ["代码审查"]
