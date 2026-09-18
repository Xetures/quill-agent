"""接口连通性：测试模型服务是否可用、拉取它提供的模型列表。

放在业务层的原因：它要发 HTTP 请求，而界面层不该自己拼 SDK 调用。
（名字里的 core 是历史原因 —— 它并不是「核心逻辑」，业务代码分散在
 quill_agent 包的各个模块里。）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openai import OpenAI

from quill_agent.models import Protocol

# 请求超时时间（秒）：探活要快速给出结论，不适合长时间等待
TEST_TIMEOUT = 15.0


@dataclass(frozen=True)
class ConnectionResult:
    """接口调用结果。

    Attributes:
        ok: 是否成功。
        message: 展示给用户的简短文案（只有「连接成功」/「连接失败」两种）。
        models: 拉取到的模型名列表，只有 fetch_models 成功时才有值。
        detail: 原始错误信息，供排查用，界面不展示。
    """

    ok: bool
    message: str
    models: list[str] = field(default_factory=list)
    detail: str = ""


def fetch_models(
    *,
    base_url: str = "",
    api_key: str = "",
    protocol: Protocol = Protocol.OPENAI,
) -> ConnectionResult:
    """拉取接口提供的模型列表（GET /models）。

    目前只实现 OpenAI 协议。注意并不是所有服务都实现了这个端点：
    Azure OpenAI、部分网关、只支持 /chat/completions 的中转站会失败，
    那种情况下改用界面上的「手动添加」录入模型名即可。

    Args:
        base_url: 接口地址，留空则用 OpenAI 官方地址。
        api_key: 鉴权 Key，留空时填占位值（本地部署的服务通常不校验）。
        protocol: 协议类型，当前仅支持 OpenAI。

    Returns:
        ConnectionResult: ok 为真时，models 里是可用模型名列表。
    """
    if protocol is not Protocol.OPENAI:
        return ConnectionResult(False, "连接失败", detail=f"暂未实现「{protocol.label}」")

    client = OpenAI(
        base_url=base_url or None,
        api_key=api_key or "EMPTY",  # SDK 要求非空，本地服务通常不校验内容
        timeout=TEST_TIMEOUT,
        max_retries=0,  # 探活不要自动重试，快速失败更好排查
    )

    try:
        page = client.models.list()
    except Exception as exc:  # 网络不通、地址错误、鉴权失败都归到这里
        return ConnectionResult(False, "连接失败", detail=str(exc))

    return ConnectionResult(True, "连接成功", models=sorted(item.id for item in page.data))


def test_connection(
    *,
    base_url: str = "",
    api_key: str = "",
    protocol: Protocol = Protocol.OPENAI,
) -> ConnectionResult:
    """测试接口是否可用。

    内部复用 fetch_models 的那一次请求，界面只展示「连接成功 / 连接失败」，
    原始错误留在 detail 里备用。
    """
    result = fetch_models(base_url=base_url, api_key=api_key, protocol=protocol)
    return ConnectionResult(
        ok=result.ok,
        message="连接成功" if result.ok else "连接失败",
        models=result.models,
        detail=result.detail,
    )
