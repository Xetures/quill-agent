"""对话接口：一轮 Agent 对话的 SSE 流。

为什么用 SSE 而不是 WebSocket：这里的推送是**单向**的（服务端把过程吐给前端），
前端没有东西要实时回传。SSE 是普通 HTTP 响应，浏览器原生支持、断线重连免费、
用 curl 就能调试 —— 为这点需求上 WebSocket，多出来的复杂度不划算。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, BinaryIO

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from quill_agent.agent import Notice, ReasoningDelta, RunStats, ToolStep, run_agent_stream
from quill_agent.history import ConversationStore
from quill_agent.models import Mode, ModelChoice
from server import stores
from server.schemas import ChatRequest

router = APIRouter(tags=["chat"])


class Attachment:
    """上传附件的适配壳。

    业务层的 `save_attachments()` 按鸭子类型写（有 `name`、能 `read()` 就行），
    它原本适配的是 Streamlit 的 `UploadedFile`。这里补一个同样形状的壳，业务层
    一行都不用改 —— 附件怎么落盘只有一份实现，两个界面共用。

    用 `UploadFile.file`（底层的同步文件对象）而不是 `UploadFile.read()`：
    后者是 async，而本端点是同步 `def`（跑在线程池里），没有 await 可用。
    """

    def __init__(self, name: str, handle: BinaryIO) -> None:
        self.name = name
        self._handle = handle

    def read(self) -> bytes:
        return self._handle.read()


def _event(name: str, payload: dict) -> str:
    """拼一个 SSE 事件。

    data 走 JSON 是必须的：SSE 规定 data 里不能有裸换行，而思考过程、工具结果
    都带换行。JSON 会把它们转义掉，前端解析回来即可。
    """
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _resolve_choice(request: ChatRequest) -> ModelChoice | None:
    """把「连接 id + 模型名」还原成一次具体的模型选择。

    前端传 id 而不是下标：连接或模型被增删时下标会漂移，id 不会
    （和 `model_choice_key` 是同一个道理）。
    """
    if not request.model_config_id or not request.model:
        return None

    config = stores.models().get(request.model_config_id)
    if config is None:
        return None

    return ModelChoice(config=config, model=request.model)


def _resolve_mode(request: ChatRequest) -> Mode | None:
    """取出这一轮要用的提示词模式；没选就是 None（纯问答）。"""
    if not request.mode_id:
        return None
    return stores.modes().get(request.mode_id)


def _remember(store: ConversationStore, conversation_id: str, message: dict) -> None:
    """写进会话文件，并补上时间戳。

    这里不像 Streamlit 版那样同时维护一份内存副本 —— 前端自己持有消息列表，
    刷新时重新拉。少一份需要同步的状态就少一类 bug。
    """
    message["ts"] = datetime.now().isoformat(timespec="seconds")
    store.append(conversation_id, message)


def stream_round(request: ChatRequest, files: list) -> Iterator[str]:
    """跑一轮对话，把 agent 的事件流翻译成 SSE。

    Args:
        request: 文本部分（会话 id、输入、模型、模式）。
        files: 本轮附件（`Attachment` 列表），交给业务层落盘。
    """
    store = stores.conversations()
    conversation_id = request.conversation_id
    text = request.prompt.strip()

    if not text:
        yield _event("notice", {"text": "输入是空的。"})
        return

    # 用户消息：产生即落盘。服务端可能在回答到一半时被杀掉，
    # 攒到最后再存的话这条问题就丢了。
    #
    # 附件名也一并记下：给界面回看用（模型拿到的是落盘后的路径，由
    # `build_user_message` 写在消息正文里）。不记的话，界面上就完全看不出
    # 这条消息带过附件 —— 而模型明明看得见
    _remember(
        store,
        conversation_id,
        {"role": "user", "content": text, "files": [item.name for item in files]},
    )

    # 历史要排除刚写进去的那条 —— 它会由 agent 层重新组装（带上运行时上下文）
    history = store.load(conversation_id)[:-1]

    stats = RunStats()
    text_parts: list[str] = []
    steps: list[ToolStep] = []
    notices: list[str] = []
    reasoning_parts: list[str] = []

    for item in run_agent_stream(
        prompt=text,
        files=files,
        mode=_resolve_mode(request),
        choice=_resolve_choice(request),
        history=history,
        stats=stats,
    ):
        if isinstance(item, Notice):
            notices.append(item.text)
            yield _event("notice", {"text": item.text})
        elif isinstance(item, ReasoningDelta):
            reasoning_parts.append(item.text)
            yield _event("reasoning", {"text": item.text})
        elif isinstance(item, ToolStep):
            steps.append(item)
            yield _event("tool", asdict(item))
        else:
            text_parts.append(item)
            yield _event("text", {"text": item})

    # 助手消息落盘。字段和 Streamlit 版完全一致 —— 会话文件是两边共用的，
    # 换界面不该让历史记录变成两种格式
    answer = {
        "role": "assistant",
        "content": "".join(text_parts),
        "steps": [asdict(step) for step in steps],
        "notices": notices,
        "reasoning": "".join(reasoning_parts),
        "stats": asdict(stats),
        # 用量统计按模型分组靠它。`stats` 里只有 token 数，认不出是哪个模型花的，
        # 事后也没法反推（会话里可以中途换模型），所以必须在落盘时就记下
        "model": request.model,
    }
    _remember(store, conversation_id, answer)

    yield _event("done", answer)


@router.post("/chat")
def chat(
    conversation_id: Annotated[str, Form()],
    prompt: Annotated[str, Form()],
    model_config_id: Annotated[str, Form()] = "",
    model: Annotated[str, Form()] = "",
    mode_id: Annotated[str, Form()] = "",
    files: Annotated[Sequence[UploadFile], File()] = (),
) -> StreamingResponse:
    """发一条消息，用 SSE 推回整轮过程。

    请求体是 multipart 而不是 JSON：附件要和文本一起上传。拆成「先传文件拿路径、
    再带路径发消息」两个接口也能做，但那样「文件属于哪一轮」就得靠额外状态去维系；
    一轮请求带齐，语义最清楚。

    用 `Annotated` 形式而不是 `= Form(...)`：默认值是元组而不是列表，免得
    可变默认值被 lint 拦下（`files` 只读，元组完全够用）。

    这里的字段清单和 `schemas.ChatRequest` 有一份重复，是 multipart 的代价：
    请求体里既有文件又有普通字段时，FastAPI 只能用 `Form()` 逐个声明，没法交给
    pydantic 模型去解析。所以下面手工组装成 `ChatRequest`，好让 `stream_round`
    的入参保持类型明确。加字段时**两处都要改**。

    刻意用同步 `def` 而不是 `async def`：`run_agent_stream` 内部是阻塞式的 SDK
    调用，放在事件循环里会把整个服务卡死。FastAPI 会把同步端点丢进线程池执行，
    正好合适。
    """
    request = ChatRequest(
        conversation_id=conversation_id,
        prompt=prompt,
        model_config_id=model_config_id,
        model=model,
        mode_id=mode_id,
    )

    uploads = [
        Attachment(name=item.filename or "attachment", handle=item.file) for item in files
    ]

    return StreamingResponse(
        stream_round(request, uploads),
        media_type="text/event-stream",
        # 关掉中间层缓冲，否则前端会「憋一大段才收到」，流式就白做了
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
