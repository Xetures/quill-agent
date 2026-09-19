"""技能库测试：目录扫描、元信息解析、清单生成。"""

from pathlib import Path
from types import SimpleNamespace

from quill_agent import agent
from quill_agent.skills import SkillLibrary, split_frontmatter


def make_skill(root: Path, name: str, text: str) -> None:
    """在指定目录下造一个技能。"""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# frontmatter 解析
# ---------------------------------------------------------------------------
def test_split_frontmatter_parses_meta_and_body() -> None:
    text = "---\ndescription: 什么时候用我\n---\n\n正文第一行\n正文第二行\n"

    meta, body = split_frontmatter(text)

    assert meta == {"description": "什么时候用我"}
    assert body == "正文第一行\n正文第二行"


def test_split_frontmatter_without_meta_returns_original() -> None:
    text = "# 标题\n\n正文"
    assert split_frontmatter(text) == ({}, text)


def test_split_frontmatter_with_unclosed_fence_returns_original() -> None:
    """有开头没结尾时，宁可整篇当正文，也不要把正文吃掉。"""
    text = "---\ndescription: 忘了收尾\n\n正文"
    assert split_frontmatter(text) == ({}, text)


def test_split_frontmatter_keeps_colon_in_value() -> None:
    """值里还有冒号时，只按第一个冒号切分。"""
    meta, _ = split_frontmatter("---\ndescription: 说明：先读文件\n---\n正文")

    assert meta["description"] == "说明：先读文件"


# ---------------------------------------------------------------------------
# 目录扫描
# ---------------------------------------------------------------------------
def test_list_names_only_counts_dirs_with_skill_file(tmp_path: Path) -> None:
    """只认带 SKILL.md 的目录；别的目录和散文件都不算技能。"""
    make_skill(tmp_path, "乙技能", "---\ndescription: b\n---\n正文")
    make_skill(tmp_path, "甲技能", "---\ndescription: a\n---\n正文")
    (tmp_path / "随手放的目录").mkdir()
    (tmp_path / "散文件.md").write_text("x", encoding="utf-8")

    assert SkillLibrary(tmp_path).list_names() == sorted(["甲技能", "乙技能"])


def test_list_names_on_missing_root(tmp_path: Path) -> None:
    """根目录还没建时返回空列表，而不是报错。"""
    assert SkillLibrary(tmp_path / "还没建").list_names() == []


def test_read_strips_frontmatter(tmp_path: Path) -> None:
    make_skill(tmp_path, "示例", "---\ndescription: 场景\n---\n\n正文内容\n")

    assert SkillLibrary(tmp_path).read("示例") == "正文内容"


def test_meta_reads_description(tmp_path: Path) -> None:
    make_skill(tmp_path, "示例", "---\ndescription: 场景\n---\n正文")

    meta = SkillLibrary(tmp_path).meta("示例")

    assert meta is not None
    assert meta.name == "示例"
    assert meta.description == "场景"


def test_meta_without_description_is_empty(tmp_path: Path) -> None:
    """没写描述不影响技能可用，只是少了「什么时候用它」的线索。"""
    make_skill(tmp_path, "示例", "# 只有正文\n")

    meta = SkillLibrary(tmp_path).meta("示例")

    assert meta is not None
    assert meta.description == ""


def test_missing_skill_returns_none(tmp_path: Path) -> None:
    library = SkillLibrary(tmp_path)

    assert library.exists("不存在") is False
    assert library.read("不存在") is None
    assert library.meta("不存在") is None


# ---------------------------------------------------------------------------
# 清单生成（agent 层）
# ---------------------------------------------------------------------------
def test_catalog_lists_selected_skills(monkeypatch, tmp_path: Path) -> None:
    """清单只在模式（技能组）点名的技能 —— 技能不再有全局开关。"""
    skills_dir = tmp_path / "skills"
    make_skill(skills_dir, "甲技能", "---\ndescription: 用来做甲\n---\n正文")
    make_skill(skills_dir, "乙技能", "---\ndescription: 用来做乙\n---\n正文")

    monkeypatch.setattr(agent, "get_settings", lambda: _settings(tmp_path, skills_dir))

    catalog = agent.build_skill_catalog(["甲技能"])

    assert "甲技能：用来做甲" in catalog
    assert "乙技能" not in catalog


def test_catalog_is_empty_without_selection(monkeypatch, tmp_path: Path) -> None:
    """技能组为空 → 一个技能都不给，哪怕 skills/ 里有一堆。"""
    skills_dir = tmp_path / "skills"
    make_skill(skills_dir, "甲技能", "---\ndescription: 用来做甲\n---\n正文")

    monkeypatch.setattr(agent, "get_settings", lambda: _settings(tmp_path, skills_dir))

    assert agent.build_skill_catalog([]) == ""


def test_catalog_is_empty_without_skills(monkeypatch, tmp_path: Path) -> None:
    """组里点名的技能在磁盘上已经没了 → 返回空串，调用方据此不加那条消息。"""
    monkeypatch.setattr(agent, "get_settings", lambda: _settings(tmp_path, tmp_path / "skills"))

    assert agent.build_skill_catalog(["早已删除的技能"]) == ""


def _settings(tmp_path: Path, skills_dir: Path) -> SimpleNamespace:
    """构造一个只带技能相关字段的假配置。

    只替换用到的那个字段，避免测试依赖真实的 .env / data 目录。
    """
    return SimpleNamespace(skills_dir=skills_dir)
