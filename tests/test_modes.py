"""模式测试：解析成四类资源，以及旧提示词组数据的迁移。

模式存的是「引用哪个组」，真正组装上下文要用的是组里的成员。这层解析是这次
改动的核心，也最容易出现「模式里选了却没生效」这类静默失效，所以单独测。

末尾几条例外 —— 它们测的是提示词组自身的引用清理（见 `PromptGroupStore`），
放在这里是「提示词组」这个主题下，单独开文件反而散。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from quill_agent import agent
from quill_agent.models import Mode
from quill_agent.store import PromptGroupStore, migrate_prompt_groups


def _write(path: Path, payload: list) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _settings(tmp_path: Path) -> SimpleNamespace:
    """只带组文件路径的假配置，避免测试依赖真实的 data 目录。"""
    return SimpleNamespace(
        prompt_groups_path=tmp_path / "prompt_groups.json",
        tool_groups_path=tmp_path / "tool_groups.json",
        skill_groups_path=tmp_path / "skill_groups.json",
    )


def _seed(tmp_path: Path) -> None:
    """造三组数据：id 写死，模式按 id 引用。"""
    _write(
        tmp_path / "prompt_groups.json",
        [
            {
                "id": "pg",
                "name": "标准提示词",
                "description": "",
                # 提示词按 id 引用（引用的是哪条不重要 —— resolve_mode 只负责把
                # 组的成员原样搬出来，正文由 build_system_prompt 去读）
                "prompts": ["aaaa1111", "bbbb2222"],
            }
        ],
    )
    _write(
        tmp_path / "tool_groups.json",
        [{"id": "tg", "name": "文件操作", "description": "", "tools": ["read_file", "list_dir"]}],
    )
    _write(
        tmp_path / "skill_groups.json",
        [{"id": "sg", "name": "写文档", "description": "", "skills": ["写周报"]}],
    )


def test_resolve_collects_group_members(monkeypatch, tmp_path: Path) -> None:
    """模式引用的三个组，成员都要被解析出来。"""
    _seed(tmp_path)
    monkeypatch.setattr(agent, "get_settings", lambda: _settings(tmp_path))

    context = agent.resolve_mode(
        Mode(
            id="m1",
            name="标准",
            description="",
            prompt_group_id="pg",
            tool_group_id="tg",
            skill_group_id="sg",
        )
    )

    assert context.prompts == ["aaaa1111", "bbbb2222"]
    assert context.skills == ["写周报"]
    assert context.memory_enabled is True
    # 组里的两个 + 自动补上的 read_skill（理由见 test_resolve_adds_skill_reader）
    assert context.tools == ["read_file", "list_dir", "read_skill"]


def test_resolve_adds_skill_reader(monkeypatch, tmp_path: Path) -> None:
    """给了技能就一定要给出 read_skill —— 否则模型会去调一个不存在的工具。"""
    _seed(tmp_path)
    monkeypatch.setattr(agent, "get_settings", lambda: _settings(tmp_path))

    context = agent.resolve_mode(
        Mode(id="m1", name="带技能", description="", skill_group_id="sg")
    )

    assert context.skills == ["写周报"]
    assert "read_skill" in context.tools


def test_resolve_without_mode_gives_nothing(monkeypatch, tmp_path: Path) -> None:
    """没有模式 = 什么都不给，连记忆也不给。"""
    _seed(tmp_path)
    monkeypatch.setattr(agent, "get_settings", lambda: _settings(tmp_path))

    context = agent.resolve_mode(None)

    assert context.prompts == []
    assert context.tools == []
    assert context.skills == []
    assert context.memory_enabled is False


def test_resolve_ignores_deleted_groups(monkeypatch, tmp_path: Path) -> None:
    """引用的组被删了就当这一类没选，而不是整轮跑不起来。"""
    monkeypatch.setattr(agent, "get_settings", lambda: _settings(tmp_path))

    context = agent.resolve_mode(
        Mode(id="m1", name="空引用", description="", prompt_group_id="早已删除")
    )

    assert context.prompts == []


def test_migrate_moves_prompt_groups(tmp_path: Path) -> None:
    """旧版 modes.json 里装的是提示词组 → 搬到 prompt_groups.json。"""
    legacy = tmp_path / "modes.json"
    target = tmp_path / "prompt_groups.json"
    _write(legacy, [{"id": "g1", "name": "旧组", "settings": {}}])

    assert migrate_prompt_groups(legacy, target) is True
    assert not legacy.exists()
    assert json.loads(target.read_text(encoding="utf-8"))[0]["name"] == "旧组"

    # 再调一次不动手：目标已存在
    assert migrate_prompt_groups(legacy, target) is False


def test_migrate_keeps_new_format_modes(tmp_path: Path) -> None:
    """modes.json 里已经是新模式的数据时，绝不能搬走。"""
    legacy = tmp_path / "modes.json"
    target = tmp_path / "prompt_groups.json"
    _write(legacy, [{"id": "m1", "name": "新模式", "prompt_group_id": "pg"}])

    assert migrate_prompt_groups(legacy, target) is False
    assert legacy.exists()
    assert not target.exists()


def _by_name(store: PromptGroupStore) -> dict[str, list[str]]:
    return {group.name: group.prompts for group in store.list()}


def test_drop_prompt_removes_it_from_every_group(tmp_path: Path) -> None:
    """删提示词必须级联到所有引用它的组。

    引用按 id 存，只删正文会在组里留下一个指向空处的 id：界面上连名字都显示不出来，
    保存时又被原样写回去 —— 用户删不掉它。这条就是那个 bug 的回归测试。
    """
    store = PromptGroupStore(tmp_path / "prompt_groups.json")
    store.add(name="A", prompts=["p1", "p2"])
    store.add(name="B", prompts=["p2"])
    store.add(name="C", prompts=["p3"])

    touched = store.drop_prompt("p2")

    assert sorted(touched) == ["A", "B"]
    # 没引用它的 C 不该被动
    assert _by_name(store) == {"A": ["p1"], "B": [], "C": ["p3"]}


def test_drop_prompt_with_no_reference_changes_nothing(tmp_path: Path) -> None:
    """没有任何组引用它时，一个组都不该被报成「已改动」。"""
    store = PromptGroupStore(tmp_path / "prompt_groups.json")
    store.add(name="A", prompts=["p1"])

    assert store.drop_prompt("nobody") == []
    assert _by_name(store) == {"A": ["p1"]}


def test_prune_missing_cleans_dangling_ids(tmp_path: Path) -> None:
    """更早的删除没做级联，留下的悬空 id 要能被清掉（启动自检里跑一次）。"""
    path = tmp_path / "prompt_groups.json"
    path.write_text(
        json.dumps(
            [
                {"id": "g1", "name": "A", "description": "", "prompts": ["p1", "gone", "p2"]},
                {"id": "g2", "name": "B", "description": "", "prompts": ["p1"]},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = PromptGroupStore(path)

    touched = store.prune_missing({"p1", "p2"})

    assert touched == ["A"]
    # B 里没有悬空 id，不该被报出来，也不该被改动
    assert _by_name(store) == {"A": ["p1", "p2"], "B": ["p1"]}


def test_prune_missing_is_idempotent(tmp_path: Path) -> None:
    """没有悬空 id 时不动盘 —— 它每次启动都会跑，写了就等于每次都改文件。"""
    path = tmp_path / "prompt_groups.json"
    store = PromptGroupStore(path)
    store.add(name="A", prompts=["p1"])

    assert store.prune_missing({"p1"}) == []
