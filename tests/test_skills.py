"""技能库测试：目录扫描、元信息解析、清单生成、模型自建技能。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from quill_agent import agent
from quill_agent.skills import SkillLibrary, split_frontmatter
from quill_agent.tools import builtin


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


# ---------------------------------------------------------------------------
# create_skill：让模型把做法总结成技能
# ---------------------------------------------------------------------------
def _tool_settings(tmp_path: Path, skills_dir: Path) -> SimpleNamespace:
    """create_skill 同时要用技能目录和技能组，两个都给上。"""
    return SimpleNamespace(
        skills_dir=skills_dir,
        skill_groups_path=tmp_path / "skill_groups.json",
    )


def _write_groups(tmp_path: Path, groups: list[dict[str, object]]) -> None:
    """写一份技能组配置，供 _skill_group_hint 查名字用。"""
    (tmp_path / "skill_groups.json").write_text(
        json.dumps(groups, ensure_ascii=False), encoding="utf-8"
    )


def _in_mode_with(monkeypatch, group_id: str) -> None:
    """假装此刻正跑在一个引用了某个技能组的模式里。"""
    monkeypatch.setattr(
        agent,
        "current_environment",
        lambda: SimpleNamespace(context=agent.ModeContext(skill_group_id=group_id)),
    )


def test_create_skill_writes_a_readable_skill(monkeypatch, tmp_path: Path) -> None:
    """建出来的必须是一个正常技能：能进清单、正文和场景都对得上。

    它经由 SkillLibrary 落盘，所以这里同时是在验「元信息由代码拼」那条约定 ——
    模型只给场景和正文，结构不会拼坏（见 skills.compose_skill）。
    """
    skills_dir = tmp_path / "skills"
    monkeypatch.setattr(builtin, "get_settings", lambda: _tool_settings(tmp_path, skills_dir))

    result = builtin.create_skill(
        "批量转 Word",
        "当需要把一批 Markdown 转成 Word 时使用",
        "1. pandoc 逐个转换\n2. 检查页眉",
    )

    assert "已创建技能「批量转 Word」" in result

    library = SkillLibrary(skills_dir)
    assert library.list_names() == ["批量转 Word"]
    meta = library.meta("批量转 Word")
    assert meta is not None
    assert meta.description == "当需要把一批 Markdown 转成 Word 时使用"
    assert library.read("批量转 Word") == "1. pandoc 逐个转换\n2. 检查页眉"


def test_create_skill_never_overwrites(monkeypatch, tmp_path: Path) -> None:
    """重名不覆盖：模型看不到原正文，让它「更新」等于照着记忆重写一遍。"""
    skills_dir = tmp_path / "skills"
    make_skill(skills_dir, "已有技能", "---\ndescription: 原来的场景\n---\n原来的正文")
    monkeypatch.setattr(builtin, "get_settings", lambda: _tool_settings(tmp_path, skills_dir))

    result = builtin.create_skill("已有技能", "新场景", "新正文")

    assert "已经存在" in result
    assert SkillLibrary(skills_dir).read("已有技能") == "原来的正文"


def test_create_skill_needs_both_scenario_and_body(monkeypatch, tmp_path: Path) -> None:
    """description 和 body 缺一个都不写盘。

    缺 description 的技能进不了「什么时候该用它」的判断，缺 body 的技能读出来是空的 ——
    两种都是「看着建好了，其实没用」，宁可当场退回去让模型补齐。
    """
    skills_dir = tmp_path / "skills"
    monkeypatch.setattr(builtin, "get_settings", lambda: _tool_settings(tmp_path, skills_dir))

    assert "description" in builtin.create_skill("技能", "   ", "正文")
    assert "正文" in builtin.create_skill("技能", "场景", "   ")
    assert SkillLibrary(skills_dir).list_names() == []


def test_create_skill_survives_an_illegal_name(monkeypatch, tmp_path: Path) -> None:
    """名字非法要回一句话，不能抛异常把整轮打断（工具失败 ≠ 对话失败）。"""
    skills_dir = tmp_path / "skills"
    monkeypatch.setattr(builtin, "get_settings", lambda: _tool_settings(tmp_path, skills_dir))

    result = builtin.create_skill("../越界", "场景", "正文")

    assert result  # 文案由 safe_name 给，这里只要求「有话说、且没建出东西」
    assert SkillLibrary(skills_dir).list_names() == []


def test_create_skill_does_not_touch_the_skill_group(monkeypatch, tmp_path: Path) -> None:
    """新建的技能**不会**自动进技能组，工具只负责说清该去哪儿勾。

    技能给不给由模式的技能组决定，和工具一个道理 —— 工具偷偷改配置，
    等于绕开了「谁来决定这个模式有哪些能力」这件事。
    """
    skills_dir = tmp_path / "skills"
    _write_groups(tmp_path, [{"id": "sg", "name": "写文档", "skills": []}])
    monkeypatch.setattr(builtin, "get_settings", lambda: _tool_settings(tmp_path, skills_dir))
    _in_mode_with(monkeypatch, "sg")

    result = builtin.create_skill("生成周报", "当需要把一周的提交整理成周报时使用", "1. 读 git log")

    assert "写文档" in result
    groups = json.loads((tmp_path / "skill_groups.json").read_text(encoding="utf-8"))
    assert groups[0]["skills"] == []


def test_create_skill_says_it_is_already_enabled(monkeypatch, tmp_path: Path) -> None:
    """已经在组里就不该再让用户去勾一遍 —— 那会让人以为漏配了什么。"""
    skills_dir = tmp_path / "skills"
    _write_groups(tmp_path, [{"id": "sg", "name": "写文档", "skills": ["生成周报"]}])
    monkeypatch.setattr(builtin, "get_settings", lambda: _tool_settings(tmp_path, skills_dir))
    _in_mode_with(monkeypatch, "sg")

    result = builtin.create_skill("生成周报", "当需要把一周的提交整理成周报时使用", "正文")

    assert "已经在当前模式的技能组" in result


def test_create_skill_without_a_skill_group_says_so(monkeypatch, tmp_path: Path) -> None:
    """模式没配技能组时（纯问答）必须说清，否则模型会以为自己教会了自己。"""
    skills_dir = tmp_path / "skills"
    monkeypatch.setattr(builtin, "get_settings", lambda: _tool_settings(tmp_path, skills_dir))
    _in_mode_with(monkeypatch, "")

    result = builtin.create_skill("生成周报", "当需要整理周报时使用", "正文")

    assert "不会生效" in result


def test_create_skill_outside_a_run_is_still_reported(monkeypatch, tmp_path: Path) -> None:
    """不在运行里（没有环境）也不能崩 —— 这只是少了一条「该去哪儿勾」的线索。"""
    skills_dir = tmp_path / "skills"
    monkeypatch.setattr(builtin, "get_settings", lambda: _tool_settings(tmp_path, skills_dir))
    monkeypatch.setattr(agent, "current_environment", lambda: None)

    result = builtin.create_skill("生成周报", "当需要整理周报时使用", "正文")

    assert "已创建技能「生成周报」" in result


# ---------------------------------------------------------------------------
# SkillLibrary.delete：删除技能目录
# ---------------------------------------------------------------------------
def test_library_delete_removes_the_whole_directory(tmp_path: Path) -> None:
    """删的是整个目录：技能目录里作者放的其他文件也要一并清掉。"""
    make_skill(tmp_path, "待删", "---\ndescription: 场景\n---\n正文")
    (tmp_path / "待删" / "附件.txt").write_text("x", encoding="utf-8")
    library = SkillLibrary(tmp_path)

    assert library.delete("待删") == "待删"
    assert library.list_names() == []
    assert not (tmp_path / "待删").exists()


def test_library_delete_missing_skill_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SkillLibrary(tmp_path).delete("不存在")


def test_library_delete_ignores_a_dir_without_skill_file(tmp_path: Path) -> None:
    """只有同名目录、没有 SKILL.md 时不算技能，不能删 —— 那可能是用户的无关目录。"""
    (tmp_path / "不是技能").mkdir()

    with pytest.raises(ValueError):
        SkillLibrary(tmp_path).delete("不是技能")

    assert (tmp_path / "不是技能").exists()


# ---------------------------------------------------------------------------
# delete_skill：让模型删掉一个技能
# ---------------------------------------------------------------------------
def test_delete_skill_removes_it_after_approval(monkeypatch, tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    make_skill(skills_dir, "临时技能", "---\ndescription: 场景\n---\n正文")
    monkeypatch.setattr(builtin, "get_settings", lambda: _tool_settings(tmp_path, skills_dir))
    monkeypatch.setattr(builtin.interaction, "confirm", lambda **kwargs: True)

    result = builtin.delete_skill("临时技能")

    assert "已删除技能「临时技能」" in result
    assert SkillLibrary(skills_dir).list_names() == []
    assert not (skills_dir / "临时技能").exists()


def test_delete_skill_keeps_it_when_the_user_refuses(monkeypatch, tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    make_skill(skills_dir, "保留技能", "---\ndescription: 场景\n---\n正文")
    monkeypatch.setattr(builtin, "get_settings", lambda: _tool_settings(tmp_path, skills_dir))
    monkeypatch.setattr(builtin.interaction, "confirm", lambda **kwargs: False)

    result = builtin.delete_skill("保留技能")

    assert "拒绝" in result
    assert SkillLibrary(skills_dir).list_names() == ["保留技能"]


def test_delete_skill_without_a_channel_refuses(monkeypatch, tmp_path: Path) -> None:
    """没有通道（CLI / 单元测试）时按拒绝处理 —— 没人点头就不删。"""
    skills_dir = tmp_path / "skills"
    make_skill(skills_dir, "保留技能", "---\ndescription: 场景\n---\n正文")
    monkeypatch.setattr(builtin, "get_settings", lambda: _tool_settings(tmp_path, skills_dir))
    monkeypatch.setattr(builtin.interaction, "confirm", lambda **kwargs: None)

    result = builtin.delete_skill("保留技能")

    assert "没有删除" in result
    assert SkillLibrary(skills_dir).list_names() == ["保留技能"]


def test_delete_skill_does_not_ask_when_the_skill_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    """技能不存在时直接回话，不弹确认题 —— 免得用户对着没意义的题点头。"""
    skills_dir = tmp_path / "skills"
    monkeypatch.setattr(builtin, "get_settings", lambda: _tool_settings(tmp_path, skills_dir))

    def _fail(**kwargs):
        raise AssertionError("技能不存在时不该弹确认")

    monkeypatch.setattr(builtin.interaction, "confirm", _fail)

    result = builtin.delete_skill("没有的技能")

    assert "没有找到技能" in result


def test_delete_skill_survives_an_illegal_name(monkeypatch, tmp_path: Path) -> None:
    """名字非法要回一句话，不能抛异常把整轮打断（工具失败 ≠ 对话失败）。"""
    skills_dir = tmp_path / "skills"
    monkeypatch.setattr(builtin, "get_settings", lambda: _tool_settings(tmp_path, skills_dir))

    result = builtin.delete_skill("../越界")

    assert result
    assert SkillLibrary(skills_dir).list_names() == []


def test_read_skill_rejects_a_path_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """读技能也要过 `safe_name` —— 技能名会被拼进文件路径。

    新建 / 删除一直有这道校验，唯独「读」漏了 —— 而它恰恰是模型能自由传参的那个。
    """
    skills_dir = tmp_path / "skills"
    monkeypatch.setattr(builtin, "get_settings", lambda: _tool_settings(tmp_path, skills_dir))

    result = builtin.read_skill("../../etc/passwd")

    assert "读不了技能" in result
