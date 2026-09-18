"""配置层测试：验证默认值与环境变量覆盖行为。"""

from pathlib import Path

from quill_agent import config


def test_settings_defaults(monkeypatch) -> None:
    monkeypatch.delenv("APP_NAME", raising=False)

    settings = config.Settings(_env_file=None)

    assert settings.app_name == "quill"
    # 工作目录默认取进程启动时的当前目录。这条断言顺带守住「默认值不是 Path('.')」
    # ——后者要到访问时才解析，中途 chdir 会改变安全边界
    assert settings.work_dir == Path.cwd()


def test_get_settings_is_cached() -> None:
    first = config.get_settings()
    second = config.get_settings()

    assert first is second
