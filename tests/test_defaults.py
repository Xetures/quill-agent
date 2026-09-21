"""出厂资源：两个默认模式的播种，以及内置项不可删除。

这一层值得单独测：它管的是「装好就能用」，而出问题时**表现都是延迟才浮现的** ——
播种少一条，用户要等到用某个模式时才发现；标记丢了，用户以为自己删掉的是自建的东西；
把用户改过的内容盖回去，更是只在升级那一刻发生一次。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from quill_agent import bootstrap, config, defaults
from quill_agent.prompts import PROMPT_CATEGORIES, PromptLibrary
from quill_agent.store import (
    ModeStore,
    PromptGroupStore,
    SkillGroupStore,
    ToolGroupStore,
)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """数据根指向临时目录。

    播种会真的往数据根里写文件，而开发机上的那份就是仓库根 —— 不隔离的话，
    跑一次测试就往用户的配置里塞一份出厂资源。
    """
    monkeypatch.setenv("QUILL_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    yield config.get_settings()
    config.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 出厂内容
# ---------------------------------------------------------------------------


def test_first_start_creates_usable_default_modes(settings: Any) -> None:
    """新装一份就有三个能直接用的模式 —— 这就是「开箱即用」的全部意义。

    没有它们的话，任务页一个模式都选不了，而任务页**必须选中一个模式**才能对话。
    """
    defaults.ensure(settings)

    modes = {mode.id: mode for mode in ModeStore(settings.modes_path).list()}

    agent = modes[defaults.BUILTIN_MODE_AGENT_ID]
    assert agent.prompt_group_id == defaults.BUILTIN_PROMPT_GROUP_ID
    assert agent.tool_group_id == defaults.BUILTIN_TOOL_GROUP_ID
    assert agent.skill_group_id == defaults.BUILTIN_SKILL_GROUP_ID
    assert agent.memory_enabled is True
    assert agent.builtin is True
    # 出厂时不知道用户配了哪个模型，留空表示「沿用任务页当前选中的那个」
    assert agent.preferred_model == ""

    lite = modes[defaults.BUILTIN_MODE_LITE_ID]
    assert lite.prompt_group_id == defaults.BUILTIN_PROMPT_GROUP_LITE_ID
    assert lite.tool_group_id == defaults.BUILTIN_TOOL_GROUP_LITE_ID
    # 技能清单每轮都占上下文，小模型不带；记忆同理
    assert lite.skill_group_id == ""
    assert lite.memory_enabled is False
    assert lite.builtin is True

    qa = modes[defaults.BUILTIN_MODE_QA_ID]
    assert (qa.prompt_group_id, qa.tool_group_id, qa.skill_group_id) == ("", "", "")
    assert qa.memory_enabled is False
    assert qa.builtin is True


def test_the_lite_prompt_group_covers_all_six_categories(settings: Any) -> None:
    """轻量版也是六类各一条 —— 压缩的是字数，不能缺环。"""
    defaults.ensure(settings)

    groups = {group.id: group for group in PromptGroupStore(settings.prompt_groups_path).list()}
    group = groups[defaults.BUILTIN_PROMPT_GROUP_LITE_ID]
    assert group.builtin is True

    library = PromptLibrary(settings.prompt_dir)
    items = {item.id: item for item in library.list_items()}

    assert len(group.prompts) == len(PROMPT_CATEGORIES)
    assert {items[prompt_id].category for prompt_id in group.prompts} == set(PROMPT_CATEGORIES)
    # 和全量组用的不是同一批提示词：两套正文各是各的
    assert set(group.prompts).isdisjoint(groups[defaults.BUILTIN_PROMPT_GROUP_ID].prompts)


def test_the_lite_tool_group_only_names_real_tools_and_is_actually_light(settings: Any) -> None:
    """轻量工具组的每个名字都得是注册表里真有的，而且**必须真的比全量少**。

    名字是手抄的（理由见 defaults.LITE_TOOLS），抄错一个字就是静默少一个工具；
    而「手抄」这个行为本身也怕被顺手抄成全量 —— 那样轻量模式就名存实亡了。
    """
    from quill_agent.tools import registry

    defaults.ensure(settings)

    groups = {group.id: group for group in ToolGroupStore(settings.tool_groups_path).list()}
    lite = groups[defaults.BUILTIN_TOOL_GROUP_LITE_ID]
    all_tools = {spec.name for spec in registry.all()}

    assert set(lite.tools) <= all_tools
    assert len(lite.tools) < len(groups[defaults.BUILTIN_TOOL_GROUP_ID].tools)
    # 不可逆的那批不在里面 —— 没有它们，confirm 清单自然也是空的
    assert set(defaults.BUILTIN_TOOL_CONFIRM).isdisjoint(lite.tools)
    assert lite.confirm == []


def test_the_builtin_prompt_group_covers_all_six_categories(settings: Any) -> None:
    """六类各一条。提示词是按分类拼进 system prompt 的，缺一类就是缺一环。"""
    defaults.ensure(settings)

    groups = {group.id: group for group in PromptGroupStore(settings.prompt_groups_path).list()}
    group = groups[defaults.BUILTIN_PROMPT_GROUP_ID]
    assert group.builtin is True

    library = PromptLibrary(settings.prompt_dir)
    items = {item.id: item for item in library.list_items()}

    assert len(group.prompts) == len(PROMPT_CATEGORIES)
    assert {items[prompt_id].category for prompt_id in group.prompts} == set(PROMPT_CATEGORIES)


def test_the_builtin_tool_group_really_has_every_tool(settings: Any) -> None:
    """「全量」必须是真的全量。

    清单从注册表现取、而不是手抄一份：手抄的话，以后每加一个工具它就悄悄少一个，
    而用户没有任何理由去核对一个叫「全量」的组里到底有几个。
    """
    from quill_agent.tools import registry

    defaults.ensure(settings)

    groups = {group.id: group for group in ToolGroupStore(settings.tool_groups_path).list()}
    assert sorted(groups[defaults.BUILTIN_TOOL_GROUP_ID].tools) == sorted(
        spec.name for spec in registry.all()
    )


def test_the_builtin_skill_group_points_at_shipped_skills(settings: Any) -> None:
    """技能组里的名字要在 `skills/` 里真的存在。

    引用一个不存在的技能不会报错，只会让模型那边的技能清单静默少一项 ——
    用户还以为它在。
    """
    defaults.ensure(settings)

    groups = {group.id: group for group in SkillGroupStore(settings.skill_groups_path).list()}
    shipped = {path.name for path in Path("skills").iterdir() if path.is_dir()}

    assert set(groups[defaults.BUILTIN_SKILL_GROUP_ID].skills) <= shipped


# ---------------------------------------------------------------------------
# 播种的行为：幂等、不覆盖、修回标记
# ---------------------------------------------------------------------------


def test_seeding_is_idempotent(settings: Any) -> None:
    """第二次启动什么都不做 —— 否则每次启动都要往数据文件里写一遍。"""
    assert defaults.ensure(settings)

    assert defaults.ensure(settings) == []


def test_seeding_keeps_what_the_user_changed(settings: Any) -> None:
    """用户改过的内容不覆盖。

    他的改动是资产；升级时被出厂版本盖回去，比一开始就没有默认值还糟
    （和 `bootstrap.seed_defaults` 对提示词目录的做法是同一条原则）。
    """
    defaults.ensure(settings)

    store = ModeStore(settings.modes_path)
    mode = store.get(defaults.BUILTIN_MODE_AGENT_ID)
    store.update(mode.model_copy(update={"description": "我改过的说明"}))

    defaults.ensure(settings)

    again = ModeStore(settings.modes_path).get(defaults.BUILTIN_MODE_AGENT_ID)
    assert again.description == "我改过的说明"


def test_a_wiped_builtin_flag_comes_back(settings: Any) -> None:
    """标记被抹掉要改回来 —— 这是播种唯一的「例外」，而它正是这个字段存在的理由。

    少了标记，一条出厂资源就变成「用户可以删掉」的了。
    """
    defaults.ensure(settings)

    raw = json.loads(Path(settings.modes_path).read_text(encoding="utf-8"))
    for entry in raw:
        entry.pop("builtin", None)
    Path(settings.modes_path).write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    notes = defaults.ensure(settings)

    assert any("出厂标记" in note for note in notes)
    assert ModeStore(settings.modes_path).get(defaults.BUILTIN_MODE_AGENT_ID).builtin is True


def test_startup_note_actually_runs_the_seeding(settings: Any) -> None:
    """启动自检里得真的把它跑起来，否则这份「开箱即用」永远不会落到用户盘上。"""
    note = bootstrap.startup_note(settings)

    assert "出厂资源" in note
    assert ModeStore(settings.modes_path).get(defaults.BUILTIN_MODE_AGENT_ID) is not None
    assert PromptLibrary(settings.prompt_dir).get(defaults.BUILTIN_PROMPTS[0].id) is not None


# ---------------------------------------------------------------------------
# 删除保护
# ---------------------------------------------------------------------------


def test_builtin_resources_cannot_be_deleted(settings: Any) -> None:
    """内置的四个东西都删不掉，而且自建的照删不误。

    保护藏在**存储层**，而不是只把界面的按钮置灰：界面是「不提供入口」，这里是
    「不给删」。少了这一层，一次误调接口就能把内置资源删掉，而用户要等到下次启动、
    发现模式引用的组不见了才知道。
    """
    defaults.ensure(settings)

    with pytest.raises(ValueError, match="内置模式"):
        ModeStore(settings.modes_path).remove(defaults.BUILTIN_MODE_AGENT_ID)
    with pytest.raises(ValueError, match="内置提示词组"):
        PromptGroupStore(settings.prompt_groups_path).remove(defaults.BUILTIN_PROMPT_GROUP_ID)
    with pytest.raises(ValueError, match="内置技能组"):
        SkillGroupStore(settings.skill_groups_path).remove(defaults.BUILTIN_SKILL_GROUP_ID)

    tool_groups = ToolGroupStore(settings.tool_groups_path)
    with pytest.raises(ValueError, match="内置工具组"):
        tool_groups.remove(defaults.BUILTIN_TOOL_GROUP_ID)

    # 保护不能宽到把所有条目都拦住 —— 用户自己建的组得能删
    mine = tool_groups.add(name="我自己建的组", description="", tools=[])
    tool_groups.remove(mine.id)
    assert tool_groups.get(mine.id) is None


def test_builtin_prompts_cannot_be_deleted(settings: Any) -> None:
    defaults.ensure(settings)

    library = PromptLibrary(settings.prompt_dir)
    with pytest.raises(ValueError, match="内置提示词"):
        library.delete(defaults.BUILTIN_PROMPTS[0].id)


def test_editing_a_builtin_prompt_keeps_it_undeletable(settings: Any) -> None:
    """改一次内置提示词的正文，它还得是「不可删除」。

    出厂标记不在 `save` 的入参里，所以必须从现有文件读回来带上 —— 漏了这一步，
    用户编辑一次正文就等于自己把保护拆掉了，而且全程没有任何提示。
    """
    defaults.ensure(settings)

    library = PromptLibrary(settings.prompt_dir)
    spec = defaults.BUILTIN_PROMPTS[0]

    saved = library.save(spec.id, name="改过的名字", category="身份", content="改过的正文")

    assert saved.builtin is True
    assert library.get(spec.id).builtin is True
    with pytest.raises(ValueError, match="内置提示词"):
        library.delete(spec.id)


def test_editing_builtin_items_through_the_api_keeps_the_flag(settings: Any) -> None:
    """接口改内置资源时不能把标记弄丢。

    请求体里没有 `builtin` 字段（界面不该能改它），所以路由必须从已有条目把它带过来：
    直接 `Mode(**payload)` 会把它重置成 False，而这件事**要等用户哪天想删它**才暴露。
    """
    from server.routes import models as models_route
    from server.routes import tools as tools_route
    from server.schemas import ModePayload, ToolGroupPayload

    defaults.ensure(settings)

    mode = ModeStore(settings.modes_path).get(defaults.BUILTIN_MODE_AGENT_ID)
    models_route.update_mode(
        mode.id,
        ModePayload(
            name=mode.name,
            description="改过的说明",
            prompt_group_id=mode.prompt_group_id,
            tool_group_id=mode.tool_group_id,
            skill_group_id=mode.skill_group_id,
            memory_enabled=mode.memory_enabled,
        ),
    )
    assert ModeStore(settings.modes_path).get(mode.id).builtin is True

    group = ToolGroupStore(settings.tool_groups_path).get(defaults.BUILTIN_TOOL_GROUP_ID)
    tools_route.update_tool_group(
        group.id,
        ToolGroupPayload(
            name=group.name, description="改过的说明", tools=group.tools, confirm=group.confirm
        ),
    )
    assert ToolGroupStore(settings.tool_groups_path).get(group.id).builtin is True


def test_deleting_a_builtin_mode_through_the_api_is_a_400(settings: Any) -> None:
    """界面之外的调用也删不掉，而且回的是能看懂的 400，不是 500。"""
    from server.routes import models as models_route

    defaults.ensure(settings)

    with pytest.raises(HTTPException) as caught:
        models_route.delete_mode(defaults.BUILTIN_MODE_AGENT_ID)

    assert caught.value.status_code == 400
    assert "内置" in caught.value.detail
