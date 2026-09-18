"""核心逻辑的单元测试：底层不依赖 UI，所以可以直接测。"""

from quill_agent.core import echo


def test_echo_returns_original_content() -> None:
    assert echo("hello") == "hello"


def test_echo_with_empty_string() -> None:
    assert echo("") == ""
