"""首次运行的播种：只补缺失，绝不覆盖用户改过的东西。

这条底线值得单独测：提示词和技能是「用得越久越值钱」的资产。升级时把出厂版本
盖回去，比一开始就没有默认值还糟。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from quill_agent import bootstrap


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
    )

    assert str(tmp_path) in bootstrap.startup_note(settings)
