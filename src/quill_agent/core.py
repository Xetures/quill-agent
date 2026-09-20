"""接口连通性：测试模型服务是否可用、拉取它提供的模型列表。

放在业务层的原因：它要发 HTTP 请求，而界面层不该自己拼 SDK 调用。
（名字里的 core 是历史原因 —— 它并不是「核心逻辑」，业务代码分散在
 quill_agent 包的各个模块里。）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openai import APIConnectionError, OpenAI

from quill_agent.models import Protocol

# 请求超时时间（秒）：探活要快速给出结论，不适合长时间等待
TEST_TIMEOUT = 15.0

# `/models` 返回里可能表示「上下文窗口」的字段名。
#
# 官方 OpenAI / Anthropic 的返回是干净的（只有 id / created / owned_by），**问不出来**；
# 这几家会带上，所以只能当加分项、不能指望：
#   OpenRouter  -> context_length
#   vLLM        -> max_model_len
#   Groq        -> context_window
#   LM Studio   -> max_context_length
#   LiteLLM 代理 -> max_input_tokens
# 字段名以各家文档为准；找不到就走本地快照（见 model_catalog.py），再找不到就留空。
CONTEXT_KEYS = (
    "context_length",
    "max_model_len",
    "context_window",
    "max_context_length",
    "max_input_tokens",
)


@dataclass(frozen=True)
class RemoteModel:
    """接口返回的一个模型。

    Attributes:
        name: 模型名（模型 id）。
        context_window: 从返回体里解析出来的上下文窗口；**解析不到就是 None**。
            不猜默认值：错的值比空值更糟 —— 用量仪表盘会给出一个看起来对、
            实际错的占比，用户以为还有空间。
    """

    name: str
    context_window: int | None = None


@dataclass(frozen=True)
class ConnectionResult:
    """接口调用结果。

    Attributes:
        ok: 是否成功。
        message: 展示给用户的简短文案（只有「连接成功」/「连接失败」两种）。
        models: 拉取到的模型列表，只有 fetch_models 成功时才有值。
        detail: 原始错误信息，供排查用，界面不展示。
    """

    ok: bool
    message: str
    models: list[RemoteModel] = field(default_factory=list)
    detail: str = ""


def _pick_window(item: Any) -> int | None:
    """尽最大努力从单个模型对象里找出上下文窗口。

    候选字段名挨个试，取不到就返回 None。鸭子类型读取（先 `model_dump()`，
    再 `getattr`）是因为 SDK 对未知字段的处理方式不一定，中转站塞的额外字段
    有时留在模型对象上、有时只在原始 JSON 里。
    """
    payload = item.model_dump() if hasattr(item, "model_dump") else {}

    for key in CONTEXT_KEYS:
        value = payload.get(key) if isinstance(payload, dict) else None
        if value is None:
            value = getattr(item, key, None)

        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value

    return None


def _is_connection_error(exc: Exception) -> bool:
    """是不是「连不上」这一类错误。

    不写死 `isinstance(exc, APIConnectionError)`：SDK 底层换过 HTTP 库
    （openai 3.x 已经从 httpx 换到了 httpx2），异常类型跟着变，绑死类型会在升级时
    悄悄失效。名称里带 `Connect` 的那些（`APIConnectionError` / `ConnectError` /
    `ConnectTimeout`…）说的是同一件事，按名字判断够用且不挑版本。
    """
    return isinstance(exc, APIConnectionError) or "Connect" in type(exc).__name__


def _connection_detail(exc: Exception, protocol: Protocol, url: str) -> str:
    """把 SDK 的报错翻成「下一步该查什么」。

    原先这里直接把 `str(exc)` 交出去，于是「地址写错」「端口没人监听」「服务只绑了
    127.0.0.1」三种情况**全都显示成同一句 `Connection error.`** —— 用户拿着它无法
    判断，只能来问；而这三件事的修法完全不同。

    所以这里把两样东西一起给出去：**实际请求的地址**（一眼能看出拼写与端口），
    以及一条排查顺序（连不上时从哪一层开始查）。地址是补全之后的，所以也能顺带
    确认「少写 /v1」这类问题有没有被自动补上。
    """
    lines = [
        f"请求地址：{url or '（SDK 默认地址）'}",
        f"原始错误：{exc}",
    ]

    if _is_connection_error(exc):
        lines.append(
            "连不上这个地址。按顺序查："
            "① 地址与端口是否写对；"
            "② 那个服务是否正在运行；"
            "③ 它是不是只监听了本机（127.0.0.1）—— 那样从别的机器访问必然连不上。"
        )
        if protocol is Protocol.OLLAMA:
            lines.append(
                "Ollama 默认只监听 127.0.0.1。要跨机器访问，在跑它的机器上设置 "
                "OLLAMA_HOST=0.0.0.0:11434 并重启 Ollama（macOS 用 launchctl setenv，"
                "Windows 用 setx，Linux 改 systemd 的 Environment）。"
            )

    return "\n".join(lines)


def fetch_models(
    *,
    base_url: str = "",
    api_key: str = "",
    protocol: Protocol = Protocol.OPENAI,
) -> ConnectionResult:
    """拉取接口提供的模型列表（GET /models）。

    只实现了 OpenAI 兼容这一侧（含 Ollama，它提供兼容层）。注意并不是所有服务都
    实现了这个端点：Azure OpenAI、部分网关、只支持 /chat/completions 的中转站会失败，
    那种情况下改用界面上的「手动添加」录入模型名即可。

    返回里顺带尝试解析每个模型的上下文窗口（能解析出来的服务很少，见 `CONTEXT_KEYS`）。

    Args:
        base_url: 接口地址。写法可以随便一点（`host:port` 也行），补全规则见
            `Protocol.resolve_base_url`。
        api_key: 鉴权 Key，留空时填占位值（本地部署的服务通常不校验）。
        protocol: 协议类型。

    Returns:
        ConnectionResult: ok 为真时，models 里是可用模型列表。
    """
    if not protocol.openai_compatible:
        return ConnectionResult(False, "连接失败", detail=f"暂未实现「{protocol.label}」")

    url = protocol.resolve_base_url(base_url)
    client = OpenAI(
        base_url=url or None,
        api_key=api_key or "EMPTY",  # SDK 要求非空，本地服务通常不校验内容
        timeout=TEST_TIMEOUT,
        max_retries=0,  # 探活不要自动重试，快速失败更好排查
    )

    try:
        page = client.models.list()
    except Exception as exc:  # 网络不通、地址错误、鉴权失败都归到这里
        return ConnectionResult(False, "连接失败", detail=_connection_detail(exc, protocol, url))

    items = [RemoteModel(name=item.id, context_window=_pick_window(item)) for item in page.data]
    items.sort(key=lambda entry: entry.name)

    return ConnectionResult(True, "连接成功", models=items)


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
