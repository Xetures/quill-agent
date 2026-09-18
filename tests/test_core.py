"""核心逻辑测试：不依赖 UI，也不依赖真实网络。"""

from quill_agent.core import fetch_models
from quill_agent.models import Protocol


def test_fetch_models_rejects_unsupported_protocol() -> None:
    """目前只实现了 OpenAI 协议；别的协议要给出可读的失败说明。

    这一条不需要联网：协议在发请求之前就被拦下了。
    """
    result = fetch_models(protocol=Protocol.ANTHROPIC)

    assert result.ok is False
    assert "暂未实现" in result.detail
