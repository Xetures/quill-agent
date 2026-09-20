"""协议层：可选协议、Key 校验与各自的默认地址。

重点是 Ollama —— 它是第一个「不是官方服务、却仍走 OpenAI SDK」的协议：
既要能在界面上选，也要在连接（`core.fetch_models`）和对话（`agent`）两条链路上
被认下来。判断收拢在 `Protocol.openai_compatible` / `default_base_url` 上，
所以这里测的是那两个属性，而不是某个函数里的分支。
"""

from __future__ import annotations

import pytest

from quill_agent import core
from quill_agent.models import Protocol, check_api_key, protocol_options


def test_ollama_reuses_the_openai_compatible_client() -> None:
    """Ollama 自带 OpenAI 兼容层（`/v1/chat/completions`），复用同一条调用链路。"""
    assert Protocol.OLLAMA.openai_compatible is True
    assert Protocol.OPENAI.openai_compatible is True


def test_anthropic_is_still_not_implemented() -> None:
    """Anthropic 是另一套请求体，还没接 —— 它不该被当成兼容协议放过去。"""
    assert Protocol.ANTHROPIC.openai_compatible is False


def test_only_ollama_has_a_default_address() -> None:
    """本地服务有「人人如此」的地址；远端服务没有，硬给一个只会让人白排查。"""
    assert Protocol.OLLAMA.default_base_url == "http://localhost:11434/v1"
    assert Protocol.OPENAI.default_base_url == ""
    assert Protocol.ANTHROPIC.default_base_url == ""


@pytest.mark.parametrize("key", ["", "local-token", "ollama"])
def test_ollama_accepts_a_missing_or_loose_key(key: str) -> None:
    """本地服务通常不校验 Key：留空是正常用法，随便填一个也不算错。"""
    assert check_api_key(Protocol.OLLAMA, key) is None


@pytest.mark.parametrize("key", ["有空格 的 key", "带换行\n的 key"])
def test_ollama_still_rejects_obviously_broken_keys(key: str) -> None:
    """宽松不等于不校验：带空格 / 换行的输入基本可以断定是复制粘贴出的错。"""
    assert check_api_key(Protocol.OLLAMA, key) is not None


def test_protocol_options_cover_every_protocol() -> None:
    """界面下拉直接用这份清单，加协议时不该漏掉任何一项。"""
    options = protocol_options()

    assert {item["value"] for item in options} == {item.value for item in Protocol}
    assert all(item["label"] and item["hint"] for item in options)


def test_fetch_models_falls_back_to_the_default_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只填协议、地址留空时，请求要落在默认地址上 —— 否则「选 Ollama 直接能连」不成立。"""
    captured: dict[str, object] = {}

    class _FakePage:
        data: list[object] = []

    class _FakeModels:
        def list(self) -> _FakePage:
            return _FakePage()

    class _FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.models = _FakeModels()

    monkeypatch.setattr(core, "OpenAI", _FakeClient)

    result = core.fetch_models(protocol=Protocol.OLLAMA)

    assert result.ok is True
    assert captured["base_url"] == "http://localhost:11434/v1"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", "http://localhost:11434/v1"),  # 留空 = 用默认地址
        ("192.168.2.165:11434", "http://192.168.2.165:11434/v1"),  # 少 http:// 和 /v1
        ("http://192.168.2.165:11434", "http://192.168.2.165:11434/v1"),
        ("http://192.168.2.165:11434/", "http://192.168.2.165:11434/v1"),  # 末尾斜杠
        ("http://192.168.2.165:11434/v1", "http://192.168.2.165:11434/v1"),
        # 自己写了路径（反代 / 网关）就别乱动它
        ("http://gw.example.com/ollama", "http://gw.example.com/ollama"),
    ],
)
def test_ollama_base_url_is_normalized(raw: str, expected: str) -> None:
    """地址写法可以随便一点 —— 这两处（http:// 与 /v1）用户很容易漏，
    而漏了之后的报错完全不会提它们。"""
    assert Protocol.OLLAMA.resolve_base_url(raw) == expected


def test_other_protocols_do_not_get_v1_appended() -> None:
    """远端协议的 base_url 本来就不带 /v1（SDK 自己会拼 /chat/completions）。"""
    assert Protocol.OPENAI.resolve_base_url("api.deepseek.com") == "http://api.deepseek.com"
    assert Protocol.OPENAI.resolve_base_url("https://api.deepseek.com") == (
        "https://api.deepseek.com"
    )
    assert Protocol.OPENAI.resolve_base_url("") == ""


def test_fetch_models_still_rejects_unimplemented_protocols() -> None:
    """换了协议属性之后，「暂未实现」这条兜底不能被顺手放开。"""
    result = core.fetch_models(protocol=Protocol.ANTHROPIC)

    assert result.ok is False
    assert "暂未实现" in result.detail
