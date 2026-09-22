"""配置层测试：验证默认值与环境变量覆盖行为。"""

import sys
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
    覆盖它 —— 数据根于是变成 `$HOME`，用户的配置会直接散在主目录里。
    这条用例守住那个 `validation_alias`。
    """
    monkeypatch.delenv(config.HOME_ENV, raising=False)
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    # 固定平台跑，断言才不会被 iOS / Linux 的路径规则差异弄成看平台的分支
    monkeypatch.setattr(sys, "platform", "linux")
    # 当前目录既不是源码树，也不像发布包 —— 走平台数据目录
    monkeypatch.chdir(tmp_path)

    settings = config.Settings(_env_file=None)

    assert settings.home == fake_home / ".local" / "share" / "quill"
    # 关键在于它落在平台目录**里面**，而不是直接就是主目录
    assert settings.home != fake_home


def test_running_inside_a_checkout_keeps_the_data_next_to_it(
    monkeypatch, tmp_path: Path
) -> None:
    """「就地运行」沿用旧行为：站在源码树里就把数据放旁边。

    这条是刻意保留的兼容 —— 早期版本所有路径都相对 cwd，开发者 / 从解压目录试用
    的人，数据就散在仓库里，直接改到平台目录会让那些配置看起来「消失」。

    判据是「像不像源码树 / 发布包」（`pyproject.toml` + `src/quill_agent`），
    不再是「有没有 data/ 」—— 后者在首次运行时还不存在，会让同一个仓库先是
    落到平台目录、第二次才就地。
    """
    monkeypatch.delenv(config.HOME_ENV, raising=False)
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "src" / "quill_agent").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    settings = config.Settings(_env_file=None)

    assert settings.home == tmp_path
    assert settings.models_path == tmp_path / "data" / "models.json"


def test_a_read_only_checkout_is_not_used_as_the_data_root(
    monkeypatch, tmp_path: Path
) -> None:
    """安装目录里的源码树不能当数据根 —— 桌面版就是这种形态。

    macOS 的签名包内、Windows 的 Program Files 下都**不可写**，而打包时源码是
    被带进去的（`pyproject.toml` + `src/` 都在）。少了可写性这一条，数据会往
    只读目录里写；Windows 上更隐蔽 —— 写请求被静默重定向到 VirtualStore，
    用户看不见，程序也不报错。
    """
    repo = tmp_path / "app"
    (repo / "src" / "quill_agent").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("", encoding="utf-8")
    repo.chmod(0o555)

    monkeypatch.delenv(config.HOME_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.chdir(repo)

    try:
        settings = config.Settings(_env_file=None)
        assert settings.home == tmp_path / "home" / ".local" / "share" / "quill"
    finally:
        # 收拾干净，否则 tmp_path 的清理会在只读目录上失败
        repo.chmod(0o755)


def test_work_dir_falls_back_when_cwd_is_not_usable(monkeypatch, tmp_path: Path) -> None:
    """cwd 是文件系统根时，不能拿它当安全边界。

    双击启动时 cwd 由启动器决定，macOS 的 Finder 给的就是 `/` —— 那样「工作目录」
    等于整个磁盘，模型的每一次读写都落在边界之外还能通过。
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir("/")

    settings = config.Settings(_env_file=None)

    assert settings.work_dir == tmp_path / "Quill"


def test_explicit_absolute_path_is_respected(monkeypatch, tmp_path: Path) -> None:
    """显式给了绝对路径就听它的 —— 锚定只管相对路径。"""
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path / "home"))
    elsewhere = tmp_path / "elsewhere" / "models.json"
    monkeypatch.setenv("MODELS_PATH", str(elsewhere))

    settings = config.Settings(_env_file=None)

    assert settings.models_path == elsewhere
