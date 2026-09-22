"""首次运行的播种：只补缺失，绝不覆盖用户改过的东西。

这条底线值得单独测：提示词和技能是「用得越久越值钱」的资产。升级时把出厂版本
盖回去，比一开始就没有默认值还糟。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from quill_agent import bootstrap, config
from quill_agent.store import ToolGroupStore


def test_copy_missing_adds_new_files_only(tmp_path: Path) -> None:
    source = tmp_path / "factory"
    target = tmp_path / "data-root"

    (source / "身份").mkdir(parents=True)
    (source / "身份" / "助手.md").write_text("出厂版", encoding="utf-8")
    (source / "新增.md").write_text("出厂版", encoding="utf-8")

    (target / "身份").mkdir(parents=True)
    (target / "身份" / "助手.md").write_text("用户改过的", encoding="utf-8")

    copied = bootstrap.copy_missing(source, target)

    assert copied == 1
    assert (target / "身份" / "助手.md").read_text(encoding="utf-8") == "用户改过的"
    assert (target / "新增.md").read_text(encoding="utf-8") == "出厂版"


def test_copy_missing_keeps_the_directory_layout(tmp_path: Path) -> None:
    source = tmp_path / "factory"
    (source / "a" / "b").mkdir(parents=True)
    (source / "a" / "b" / "deep.md").write_text("x", encoding="utf-8")

    assert bootstrap.copy_missing(source, tmp_path / "target") == 1
    assert (tmp_path / "target" / "a" / "b" / "deep.md").is_file()


def test_seed_defaults_skips_the_skills_dir_without_a_template(
    monkeypatch, tmp_path: Path
) -> None:
    """找不到出厂目录就跳过，不该让启动失败（用户可能自己把 prompt/ 删了）。"""
    source = tmp_path / "factory" / "prompt"
    source.mkdir(parents=True)
    (source / "身份.md").write_text("内容", encoding="utf-8")

    monkeypatch.setattr(
        bootstrap,
        "template_dir",
        lambda name: source if name == "prompt" else None,
    )

    settings = SimpleNamespace(
        prompt_dir=tmp_path / "root" / "prompt",
        skills_dir=tmp_path / "root" / "skills",
    )

    assert bootstrap.seed_defaults(settings) == {"prompt": 1}
    assert not (tmp_path / "root" / "skills").exists()


def test_seed_defaults_does_not_copy_onto_itself(monkeypatch, tmp_path: Path) -> None:
    """就地运行时出厂目录就是数据目录，别自己抄自己。"""
    source = tmp_path / "prompt"
    source.mkdir()
    (source / "身份.md").write_text("内容", encoding="utf-8")

    monkeypatch.setattr(
        bootstrap, "template_dir", lambda name: source if name == "prompt" else None
    )

    settings = SimpleNamespace(prompt_dir=source, skills_dir=tmp_path / "skills")

    assert bootstrap.seed_defaults(settings) == {}
    assert (source / "身份.md").read_text(encoding="utf-8") == "内容"


def test_template_dir_finds_the_checkout_copy() -> None:
    """在仓库里运行时，出厂资源就是仓库根下的 prompt/。"""
    assert bootstrap.template_dir("prompt") is not None


def test_startup_note_mentions_where_the_data_lives(monkeypatch, tmp_path: Path) -> None:
    """「你的数据在哪」是排障第一个要问的，启动提示里得有。"""
    monkeypatch.setattr(bootstrap, "template_dir", lambda name: None)
    settings = SimpleNamespace(
        home=tmp_path,
        prompt_dir=tmp_path / "prompt",
        skills_dir=tmp_path / "skills",
        prompt_groups_path=tmp_path / "prompt_groups.json",
        # 播种出厂资源（两个默认模式及它们引用的各组）也要用到这几个路径
        tool_groups_path=tmp_path / "tool_groups.json",
        skill_groups_path=tmp_path / "skill_groups.json",
        modes_path=tmp_path / "modes.json",
    )

    assert str(tmp_path) in bootstrap.startup_note(settings)


def test_migrate_legacy_home_copies_the_old_data(
    monkeypatch, tmp_path: Path
) -> None:
    """老数据根（`~/.quill`）里的东西要搬到新的平台数据目录。

    不搬的话，老用户升级后看到的是「配置、会话、记忆全没了」—— 而它们还好端端
    躺在原地。这条迁移就是为了让升级不产生这种错觉。
    """
    old = tmp_path / "old-home"
    new = tmp_path / "new-home"
    (old / "data" / "conversations").mkdir(parents=True)
    (old / "data" / "models.json").write_text("{}", encoding="utf-8")
    (old / "prompt").mkdir()
    (old / "prompt" / "我的提示词.md").write_text("我改过的", encoding="utf-8")

    monkeypatch.setattr(config, "legacy_home", lambda: old)
    monkeypatch.setattr(config, "_platform_data_home", lambda: new)

    note = bootstrap.migrate_legacy_home(SimpleNamespace(home=new))

    assert note is not None and "复制" in note
    assert (new / "data" / "models.json").is_file()
    assert (new / "prompt" / "我的提示词.md").read_text(encoding="utf-8") == "我改过的"
    # 旧目录保留：用户万一回退到老版本，那边还得是完整的一份
    assert (old / "data" / "models.json").is_file()


def test_migrate_legacy_home_leaves_an_explicit_home_alone(
    monkeypatch, tmp_path: Path
) -> None:
    """用户显式指定了数据根（QUILL_HOME，含桌面壳 / 就地运行）就别去搬。

    那时「数据该在哪」是明确的；从别处搬一份进来，只会把两个位置搅在一起。
    """
    old = tmp_path / "old-home"
    (old / "data").mkdir(parents=True)
    (old / "data" / "models.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(config, "legacy_home", lambda: old)
    monkeypatch.setattr(config, "_platform_data_home", lambda: tmp_path / "new-home")

    explicit = tmp_path / "explicit-home"
    assert bootstrap.migrate_legacy_home(SimpleNamespace(home=explicit)) is None
    assert not (explicit / "data").exists()


def test_migrate_legacy_home_does_not_merge_into_a_home_in_use(
    monkeypatch, tmp_path: Path
) -> None:
    """新位置已经有 data/ 就不动 —— 两边的会话和记忆混成一锅，比不迁更糟。"""
    old = tmp_path / "old-home"
    new = tmp_path / "new-home"
    (old / "data").mkdir(parents=True)
    (old / "data" / "models.json").write_text("{}", encoding="utf-8")
    (new / "data").mkdir(parents=True)
    (new / "data" / "models.json").write_text('{"已有":"这边的"}', encoding="utf-8")

    monkeypatch.setattr(config, "legacy_home", lambda: old)
    monkeypatch.setattr(config, "_platform_data_home", lambda: new)

    assert bootstrap.migrate_legacy_home(SimpleNamespace(home=new)) is None
    assert "这边" in (new / "data" / "models.json").read_text(encoding="utf-8")


def test_migrate_legacy_home_is_a_noop_without_old_data(
    monkeypatch, tmp_path: Path
) -> None:
    """没有老数据就什么都不做 —— 它每次启动都会跑，不能凭空建目录、报假消息。"""
    monkeypatch.setattr(config, "legacy_home", lambda: tmp_path / "old-home")
    monkeypatch.setattr(config, "_platform_data_home", lambda: tmp_path / "new-home")

    new = tmp_path / "new-home"
    assert bootstrap.migrate_legacy_home(SimpleNamespace(home=new)) is None
    assert not new.exists()


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(tool_groups_path=tmp_path / "tool_groups.json")


def test_migrate_tool_names_renames_the_merged_subagent_tool(tmp_path: Path) -> None:
    """老数据里工具组的确认名单还写着 `spawn_agent`，要改成合并后的那个名字。

    工具组的 `tools` 是从注册表现取的、自动就跟上了，但 **`confirm` 是写死存下来的** ——
    不迁的话它永远匹配不上任何工具，表现是「派子代理再也不弹确认」，
    而设计意图是「另外花钱、要等，值得打断一次」。静默失效，还毫无提示。
    """
    settings = _settings(tmp_path)
    store = ToolGroupStore(settings.tool_groups_path)
    store.add(
        name="老组",
        description="",
        tools=["read_file"],
        confirm=["delete_file", "spawn_agent"],
    )

    touched = bootstrap.migrate_tool_names(settings)

    assert touched == ["老组"]
    assert store.list()[0].confirm == ["delete_file", "spawn_agents"]


def test_migrate_tool_names_leaves_other_groups_alone(tmp_path: Path) -> None:
    """没提到旧名字的组不该被动 —— 只报真的改过的那几个。"""
    settings = _settings(tmp_path)
    store = ToolGroupStore(settings.tool_groups_path)
    store.add(name="老组", description="", tools=["read_file"], confirm=["spawn_agent"])
    store.add(name="无关组", description="", tools=["read_file"], confirm=["delete_file"])

    touched = bootstrap.migrate_tool_names(settings)

    assert touched == ["老组"]
    untouched = next(item for item in store.list() if item.name == "无关组")
    assert untouched.confirm == ["delete_file"]


def test_migrate_tool_names_does_not_add_it_back(tmp_path: Path) -> None:
    """用户自己从名单里删过「派子代理要确认」的话，不能替他加回来 —— 只做重命名。"""
    settings = _settings(tmp_path)
    store = ToolGroupStore(settings.tool_groups_path)
    store.add(name="组", description="", tools=["read_file"], confirm=["delete_file"])

    assert bootstrap.migrate_tool_names(settings) == []
    assert store.list()[0].confirm == ["delete_file"]


def test_migrate_tool_names_is_idempotent(tmp_path: Path) -> None:
    """它每次启动都会跑，第二轮必须什么都不做（否则就是每次启动都写一遍盘）。"""
    settings = _settings(tmp_path)
    store = ToolGroupStore(settings.tool_groups_path)
    store.add(name="老组", description="", tools=["read_file"], confirm=["spawn_agent"])

    assert bootstrap.migrate_tool_names(settings) == ["老组"]
    assert bootstrap.migrate_tool_names(settings) == []
