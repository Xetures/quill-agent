"""配置层测试：验证默认值与环境变量覆盖行为。"""

from quill_agent import config


def test_settings_defaults(monkeypatch) -> None:
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.delenv("APP_DEBUG", raising=False)

    settings = config.Settings(_env_file=None)

    assert settings.app_name == "quill"
    assert settings.app_debug is False


def test_get_settings_is_cached() -> None:
    first = config.get_settings()
    second = config.get_settings()

    assert first is second
