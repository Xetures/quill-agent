"""配置层测试：验证默认值与环境变量覆盖行为。"""

from pathlib import Path

from quill_agent import config


def test_settings_defaults(monkeypatch) -> None:
    monkeypatch.delenv("APP_NAME", raising=False)

    settings = config.Settings(_env_file=None)

    assert settings.app_name == "Quill"
    # 工作目录默认取进程启动时的当前目录。这条断言顺带守住「默认值不是 Path('.')」
    # ——后者要到访问时才解析，中途 chdir 会改变安全边界
    assert settings.work_dir == Path.cwd()


def test_get_settings_is_cached() -> None:
    first = config.get_settings()
    second = config.get_settings()

    assert first is second


# ---------------------------------------------------------------------------
# 数据根：所有会写到磁盘的路径都挂在它下面
# ---------------------------------------------------------------------------


def test_quill_home_moves_the_whole_data_root(monkeypatch, tmp_path: Path) -> None:
    """一个 `QUILL_HOME` 把 data/、prompt/、skills/ 一起搬走 —— 打包后的默认形态。"""
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path))

    settings = config.Settings(_env_file=None)

    assert settings.home == tmp_path
    assert settings.models_path == tmp_path / "data" / "models.json"
    assert settings.conversations_dir == tmp_path / "data" / "conversations"
    assert settings.prompt_dir == tmp_path / "prompt"
    assert settings.skills_dir == tmp_path / "skills"


def test_home_env_var_is_not_mistaken_for_the_data_root(
    monkeypatch, tmp_path: Path
) -> None:
    """`HOME` 在 POSIX 上永远存在，不能让它把数据根顶掉。

    字段名如果就叫 `home` 而不加别名，pydantic-settings 会让环境变量 `HOME`
    覆盖它 —— 数据根于是变成 `$HOME`（`~/.quill` 那一层没了），用户的配置会
    直接散在主目录里。这条用例守住那个 `validation_alias`。
    """
    monkeypatch.delenv(config.HOME_ENV, raising=False)
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # 当前目录下没有 data/ prompt/，走「用 ~/.quill」那条规则
    monkeypatch.chdir(tmp_path)

    settings = config.Settings(_env_file=None)

    assert settings.home == fake_home / ".quill"


def test_running_inside_a_checkout_keeps_the_data_next_to_it(
    monkeypatch, tmp_path: Path
) -> None:
    """「就地运行」沿用旧行为：旁边有 `data/` 就把数据放旁边。

    这条是刻意保留的兼容 —— 早期版本所有路径都相对 cwd，用户的数据就散在仓库里，
    直接改到 `~/.quill` 会让那些配置看起来「消失」。
    """
    monkeypatch.delenv(config.HOME_ENV, raising=False)
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)

    settings = config.Settings(_env_file=None)

    assert settings.home == tmp_path
    assert settings.models_path == tmp_path / "data" / "models.json"


def test_explicit_absolute_path_is_respected(monkeypatch, tmp_path: Path) -> None:
    """显式给了绝对路径就听它的 —— 锚定只管相对路径。"""
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path / "home"))
    elsewhere = tmp_path / "elsewhere" / "models.json"
    monkeypatch.setenv("MODELS_PATH", str(elsewhere))

    settings = config.Settings(_env_file=None)

    assert settings.models_path == elsewhere
