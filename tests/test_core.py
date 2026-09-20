"""核心逻辑测试：不依赖 UI，也不依赖真实网络。"""

from quill_agent import core
from quill_agent.core import fetch_models
from quill_agent.models import Protocol


def test_fetch_models_rejects_unsupported_protocol() -> None:
    """目前只实现了 OpenAI 协议；别的协议要给出可读的失败说明。

    这一条不需要联网：协议在发请求之前就被拦下了。
    """
    result = fetch_models(protocol=Protocol.ANTHROPIC)

    assert result.ok is False
    assert "暂未实现" in result.detail


class _FakeConnectError(Exception):
    """名字里带 Connect —— 与 SDK 的连接类异常同类。

    刻意不引 httpx 来构造真异常：openai 3.x 的底层 HTTP 库已经从 httpx 换成了
    httpx2，测试跟着某个具体库走，只会在下次升级时白红一次。
    """


def test_connection_failure_says_which_url_was_tried() -> None:
    """连不上时要回显**实际请求的地址**。

    原先三种错法（地址写错 / 端口没人监听 / 服务只绑了 127.0.0.1）都只显示一句
    `Connection error.`，用户完全没有判断依据 —— 而这三件事的修法完全不同。
    """
    detail = core._connection_detail(
        _FakeConnectError("Connection error."), Protocol.OLLAMA, "http://192.168.2.165:11434/v1"
    )

    assert "http://192.168.2.165:11434/v1" in detail
    assert "原始错误" in detail


def test_connection_failure_for_ollama_mentions_the_listen_address() -> None:
    """Ollama 的「只监听 127.0.0.1」是跨机器连不上最常见的原因，直接写进提示。"""
    detail = core._connection_detail(
        _FakeConnectError("Connection error."), Protocol.OLLAMA, "http://192.168.2.165:11434/v1"
    )

    assert "OLLAMA_HOST" in detail
    assert "127.0.0.1" in detail


def test_other_errors_are_reported_verbatim() -> None:
    """不是连接问题的错误（比如鉴权失败）不要瞎猜原因。"""
    detail = core._connection_detail(ValueError("invalid api key"), Protocol.OPENAI, "http://x")

    assert "invalid api key" in detail
    assert "OLLAMA_HOST" not in detail
