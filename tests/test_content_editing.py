"""提示词 / 技能的写入：名字安全校验、重名保护、技能元信息的拼装。

这一批用例的重点全在「不信任输入」上：名字直接来自输入框，而它就是文件名。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quill_agent.naming import safe_name
from quill_agent.prompts import PromptLibrary
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

    assert library.save("身份", "助手", "第一版", create_only=True) == "助手"
    assert library.read("身份", "助手") == "第一版"

    library.save("身份", "助手", "第二版")
    assert library.read("身份", "助手") == "第二版"


def test_prompt_create_refuses_to_overwrite(tmp_path: Path) -> None:
    """「AI助手」这种名字很容易撞上，悄悄盖掉一份写了很久的正文代价太大。"""
    library = PromptLibrary(tmp_path / "prompt")
    library.save("身份", "助手", "原文")

    with pytest.raises(ValueError, match="已经存在"):
        library.save("身份", "助手", "覆盖它", create_only=True)

    assert library.read("身份", "助手") == "原文"


def test_prompt_rejects_a_category_outside_the_six(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="类别"):
        PromptLibrary(tmp_path / "prompt").save("随便编一个", "x", "y")


def test_prompt_name_cannot_escape_the_prompt_dir(tmp_path: Path) -> None:
    """名字是用户输入的，直接拼路径就能写到 prompt/ 外面去 —— 必须拦住。"""
    root = tmp_path / "prompt"
    library = PromptLibrary(root)

    with pytest.raises(ValueError):
        library.save("身份", "../逃出去", "x")

    assert not (tmp_path / "逃出去.md").exists()


def test_prompt_name_is_trimmed_on_the_way_in(tmp_path: Path) -> None:
    library = PromptLibrary(tmp_path / "prompt")

    assert library.save("身份", "  助手  ", "内容") == "助手"
    assert library.list_names("身份") == ["助手"]


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
