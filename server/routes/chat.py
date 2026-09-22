"""对话接口：一轮 Agent 对话的 SSE 流。

为什么用 SSE 而不是 WebSocket：推送的主方向是**单向**的（服务端把过程吐给前端）。
SSE 是普通 HTTP 响应，浏览器原生支持、用 curl 就能调试 —— 为这点需求上 WebSocket，
多出来的复杂度不划算。

> 但自从有了「执行前确认」，反方向也偶尔要有一次数据（用户点「允许」）。那一次走的是
> **一个普通的 POST**（`/chat/answer`），而不是把 SSE 换成双向长连接：一次性的、
> 用户主动触发的回传，用请求-响应表达最直白，也天然能重试。SSE 这一侧因此不用动。

数据流（见下方 `Run` / `_pump` / `_sse`）：

    runner 线程 ─ stream_round() ─┐
                                 ├─> queue ─> async SSE 生成器 ─> 浏览器
    interaction.ask() ───────────┘

为什么要多一层队列、而不是像以前那样让 StreamingResponse 直接迭代生成器：生成器被
确认题阻塞时，它连 yield 都做不到，问题就发不出去。拆开之后，生成器只管产出事件，
HTTP 层只管从队列里读，两边互不阻塞。**同一份机制也是将来做「模型主动提问」「取消
运行」的基础。**
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any, BinaryIO
from uuid import uuid4

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from quill_agent import changes, interaction
from quill_agent.agent import (
    Notice,
    ReasoningDelta,
    Round,
    RunStats,
    SummaryMade,
    ToolStart,
    ToolStep,
    Usage,
    run_agent_stream,
)
from quill_agent.config import get_settings
from quill_agent.history import ConversationStore
from quill_agent.interaction import Interaction
from quill_agent.models import Mode, ModelChoice
from quill_agent.tools.files import current_work_dir
from quill_agent.tools.todo import TodoBoard
from server import stores
from server.schemas import AnswerPayload, CancelPayload, ChatRequest

# 外围异常（落盘失败之类）写进日志，详情留在本机；界面只收到一句能行动的说明
logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


class Attachment:
    """上传附件的适配壳。

    业务层的 `save_attachments()` 按鸭子类型写（有 `name`、能 `read()` 就行），
    这里补一个同样形状的壳，业务层一行都不用改 —— 附件怎么落盘只有一份实现。

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


def _stamp(message: dict) -> dict:
    """给消息打上时间戳（就地改，顺带返回同一条，好串着写）。"""
    message["ts"] = datetime.now().isoformat(timespec="seconds")
    return message


def _remember(store: ConversationStore, conversation_id: str, message: dict) -> None:
    """写进会话文件，并补上时间戳。

    服务端不维护内存副本 —— 前端自己持有消息列表，刷新时重新拉。
    少一份需要同步的状态就少一类 bug。
    """
    store.append(conversation_id, _stamp(message))


# 助手消息边跑边落盘的节流间隔（秒）。
#
# 正文是**逐分片**来的，一个稍长的回答几百个分片 —— 每个都写一次是在拿磁盘换一个没人看
# 的中间态。但完全不写又回到原点：跑到一半进程被杀，盘上只剩用户那句提问。2 秒是
# 「重启后最多丢 2 秒的内容」和「不把磁盘写爆」之间的取舍。
ANSWER_FLUSH_SECONDS = 2.0


class _AnswerSaver:
    """这一轮的助手消息：边跑边往盘上写，但按时间节流。

    存在的理由是「跑到一半进程被杀」：在此之前助手消息只在整轮结束时写一次，所以那一刻
    盘上只有用户那句提问 —— 想过什么、动过哪些文件，重启后全都看不出来。

    **第一次写立即发生**（`_last` 从 0 起步）。否则一个几秒就跑完的回答要等到收尾才落盘，
    这件事就等于没做。短回答本来也丢不了什么，但长任务恰恰是从一开始就该看得见。
    """

    def __init__(self, store: ConversationStore, conversation_id: str, run_id: str) -> None:
        self._store = store
        self._conversation_id = conversation_id
        self._run_id = run_id
        self._last = 0.0

    def maybe(self, build: Callable[[], dict], *, force: bool = False) -> None:
        """到点了就写。

        `build` 到**真要写时**才调用：做一次快照要拼字符串、跑 `asdict`，不该为每个分片
        白做一遍 —— 那些调用里绝大多数会被上面的时间判断挡掉。
        """
        now = time.monotonic()
        if not force and now - self._last < ANSWER_FLUSH_SECONDS:
            return

        data = build()
        # 还没什么可写的（刚开跑，只有 round / start 这类过程事件）——跳过，**而且不动
        # `_last`**。
        #
        # 这一条是实测逼出来的：整轮事件往往是「几十毫秒内到齐，然后长时间静默」（等模型
        # 回话、等工具跑完）。若让那个空快照占掉「首次写」的机会，后面的正文和步骤就会
        # 全部落在 2 秒窗口内被挡掉 —— 而下一批事件要等模型响应，几百毫秒到几十秒之后
        # 才来。结果是盘上一直停在一个**空壳**上：明明已经想过、改过了，重启后看到的
        # 却是一片空白。空着不写，首次写的机会就留给第一个真正有内容的事件。
        if not force and not data.get("content") and not data.get("steps"):
            return

        self._last = now
        try:
            self._store.upsert_run(self._conversation_id, self._run_id, data)
        except OSError as exc:
            # 收尾那次失败是真问题 —— 这一轮的结果没留下来，得让用户知道（异常会冒到
            # `_pump` 的兜底里变成一条 Notice）。中间态则吞掉：它是附加的东西，一次 IO
            # 抖动不该把整轮对话打断 —— 后面还有几十次机会把它写进去
            if force:
                raise
            print(f"[chat] 这一轮的中间态没能落盘（不影响运行）：{exc}", flush=True)


def stream_round(
    request: ChatRequest, files: list, run_id: str
) -> Iterator[tuple[str, dict[str, Any]]]:
    """跑一轮对话，把 agent 的事件流翻译成「事件名 + 数据」。

    **产出的是二元组而不是拼好的 SSE 文本**：拼装是传输层的事，而这条流现在要经过
    一层队列（见 `Run`）。这样 `interaction` 也能往同一个队列里塞事件，
    两类事件在 `_sse` 里统一成同一种格式，不需要两份拼装逻辑。

    Args:
        request: 文本部分（会话 id、输入、模型、模式）。
        files: 本轮附件（`Attachment` 列表），交给业务层落盘。
        run_id: 这一轮的运行 id。助手消息**边跑边落盘**，写入时要靠它认领「盘上那条
            是不是我」（见 `_AnswerSaver`）。它是运行开始时才生成的，所以从外面传进来。
    """
    store = stores.conversations()
    conversation_id = request.conversation_id
    text = request.prompt.strip()

    if not text:
        yield "notice", {"text": "输入是空的。"}
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
    # 清单板走的是和账本同一条路：在这里创建、交给运行就地填、跑完再读
    board = TodoBoard()
    text_parts: list[str] = []
    steps: list[ToolStep] = []
    notices: list[str] = []
    reasoning_parts: list[str] = []
    saver = _AnswerSaver(store, conversation_id, run_id)

    # 这一轮发生的事，**按发生顺序**。
    #
    # `content` 和 `steps` 分开存是有理由的（前者给下一轮组装上下文用，后者给审阅用），
    # 代价是**丢掉了它们的相对顺序** —— 前端拿不到「哪句话在哪个工具前面」，于是只能把
    # 工具堆在一块、正文堆在另一块。穿插显示要的就是这份顺序，所以它必须在流式过程中
    # 记下来。两个老字段照旧保留（老消息和别的消费方都还在用）。
    parts: list[dict] = []
    # 正在累积的那一段（正文或思考）。它是**流式**的：要等下一段开始、或工具到达才封进 parts
    pending: list[str] = []
    pending_kind = ""

    def seal() -> None:
        """把正在累积的那一段封进 `parts`。

        空段不产生条目 —— 模型常常连着吐一大段纯思考而没有正文，不该留个空壳在那儿。
        """
        nonlocal pending_kind
        if pending and pending_kind:
            parts.append({"type": pending_kind, "content": "".join(pending)})
            pending.clear()
        pending_kind = ""

    def current_parts() -> list[dict]:
        """`parts` 加上「还在累积的那一段」—— 中间态渲染不能缺掉这一截。"""
        if pending and pending_kind:
            return [*parts, {"type": pending_kind, "content": "".join(pending)}]
        return list(parts)

    def snapshot(*, partial: bool) -> dict:
        """把「到此刻为止」的这一轮做成一条助手消息。

        字段只有一份固定定义 —— 换界面不该让历史记录变成两种格式。中间态和最终态是
        同一条消息的两种版本，唯一的差别是 `partial` 标记。
        """
        data = {
            "role": "assistant",
            # 认领用的身份：重启后盘上那条是「谁写的」全靠它（见 `_AnswerSaver`）
            "run_id": run_id,
            "content": "".join(text_parts),
            "steps": [asdict(step) for step in steps],
            # 顺序：`content` 与 `steps` 的**交错版本**，给界面穿插显示用（见上面的 parts）
            "parts": current_parts(),
            "notices": notices,
            "reasoning": "".join(reasoning_parts),
            "stats": asdict(stats),
            # 这一轮列过的任务清单（模型没列过就是空数组）。落盘是为了**回看时还在** ——
            # 只在运行中显示的话，一条长任务跑完，它当初打算做哪几步就再也看不到了
            "todos": board.to_payload(),
            # 用量统计按模型分组靠它。`stats` 里只有 token 数，认不出是哪个模型花的，
            # 事后也没法反推（会话里可以中途换模型），所以必须在落盘时就记下
            "model": request.model,
        }
        if partial:
            # 这一轮还没跑完，盘上这条是**中间态**。留着标记，重启后看到它才知道
            # 「这不是回答完了，是跑到一半断了」—— 少了这句话，半截内容看起来就是最终答案
            data["partial"] = True
        return _stamp(data)

    for item in run_agent_stream(
        prompt=text,
        files=files,
        mode=_resolve_mode(request),
        choice=_resolve_choice(request),
        history=history,
        stats=stats,
        board=board,
        thinking=request.thinking,
    ):
        # 这一轮迭代里是不是多了一个工具步骤 —— 是的话下面要强制写一次（见 `maybe` 调用）
        stepped = False

        if isinstance(item, Notice):
            notices.append(item.text)
            yield "notice", {"text": item.text}
        elif isinstance(item, ReasoningDelta):
            reasoning_parts.append(item.text)
            # 换段：上一段（正文，或更早的一次思考）到此为止
            if pending_kind != "reasoning":
                seal()
            pending_kind = "reasoning"
            pending.append(item.text)
            yield "reasoning", {"text": item.text}
        elif isinstance(item, Usage):
            # 运行中的用量播报：界面拿它在跑的过程中实时刷新上下文仪表盘。
            # **只推不存** —— 它是过程数据，这一轮的最终读数仍然随下面的 stats 落盘。
            # 必须在 else 之前接住：那个分支把剩下的一律当正文，一个 Usage 对象
            # 序列化出去会直接报错
            yield "usage", {
                "context_tokens": item.context_tokens,
                "cached_tokens": item.cached_tokens,
            }
        elif isinstance(item, Round):
            # 跑到第几圈了 —— 纯过程信号，不落盘。界面拿它显示进度，
            # 用户据此判断该继续等还是按停止
            yield "round", {"index": item.index, "total": item.total}
        elif isinstance(item, ToolStart):
            # 只是「现在开始跑这个工具」，不落盘也不进 steps —— 它是过程信号，
            # 成品是随后的 ToolStep
            yield "tool_start", {"name": item.name}
        elif isinstance(item, ToolStep):
            steps.append(item)
            stepped = True
            # 这次调用**之前**说过的话、想过的东西先封段 —— 顺序就落在这一句上
            seal()
            parts.append({"type": "tool", "step": asdict(item)})
            yield "tool", asdict(item)
        elif isinstance(item, SummaryMade):
            # 摘要要**落盘**：它得活过这一轮，下一轮组装上下文时才能直接用上，
            # 否则每次都从头压一遍 —— 每轮白花一次模型调用。
            # 界面上它是一条记录（刷新后仍看得到），所以和助手消息一样写进会话文件
            _remember(
                store,
                conversation_id,
                {"role": "summary", "content": item.content, "covers": item.covers},
            )
            yield "summary", {
                "content": item.content,
                "covers": item.covers,
                "saved": item.saved,
            }
        else:
            text_parts.append(item)
            if pending_kind != "text":
                seal()
            pending_kind = "text"
            pending.append(item)
            yield "text", {"text": item}

        # 每个事件之后都问一次「到点了吗」。事件是几毫秒一个的，真写盘由 `_AnswerSaver`
        # 按时间挡掉 —— 放在循环末尾而不是各个分支里，是为了**不漏事件类型**：
        # 将来加一种新事件，这里默认就覆盖到了。
        #
        # 工具步骤例外，它**强制**写：那是「做了什么」最硬的证据，而实际的时间线常常是
        # 「一串事件几十毫秒内到齐 → 长时间静默（等模型回话、等工具跑完）」。不强制的话
        # 它会被节流挡掉，而那之后就没有事件来推动下一次落盘了
        saver.maybe(lambda: snapshot(partial=True), force=stepped)

    # 收尾：写最终态。到这一步 `partial` 才消失 —— 它已经从「跑到一半」变成「跑完了」。
    # **这里不是「保存」**：中间态早就陆续写下去了，这一步是把最后一份内容盖上去
    answer = snapshot(partial=False)
    # 这一轮改了哪些文件，取一份摘要写进消息（界面据此显示「改了 N 个文件」和还原入口）。
    # 快照本身在每次改动时就落盘了（changes.record_text → _flush），这里只拿摘要 ——
    # 否则跑到一半断了，文件改了却还原不了。
    # 没有改动时是 None —— 前端据此不显示那一行，也不该有空荡荡的「还原」按钮
    recorder = changes.current()
    answer["changes"] = changes.save(recorder) if recorder else None
    # 强制写：`_last` 可能刚被上面循环里的最后一次刷新推近，但那一次的内容是**中间态**，
    # 不带最终统计。这里必须盖上去
    saver.maybe(lambda: answer, force=True)

    yield "done", answer


# ---------------------------------------------------------------------------
# 运行注册表：让「一轮正在跑的对话」成为一个可以被外部找到的对象
#
# 在此之前，一轮对话只活在 StreamingResponse 那个生成器里，外界没有任何句柄。
# 执行前确认需要外界能把答案送进那一轮 —— 所以要有这张表。
# ---------------------------------------------------------------------------


class Run:
    """一次正在跑的对话。

    目前只装两样东西：给外部用的 id，和一条交互通道。等做「取消运行」时，
    取消标志也挂这里。
    """

    def __init__(self, conversation_id: str, loop: asyncio.AbstractEventLoop) -> None:
        self.id = uuid4().hex[:8]
        self.conversation_id = conversation_id
        self.channel = Interaction(loop, run_id=self.id)


# 模块级的运行表。并发量是「同时有几轮对话在跑」，本地单用户场景下一直是个位数，
# 用一把全局锁足够 —— 不值得为它上更细的粒度
_runs: dict[str, Run] = {}
_runs_lock = threading.Lock()


def _open(run: Run) -> None:
    with _runs_lock:
        _runs[run.id] = run


def _close(run: Run) -> None:
    with _runs_lock:
        _runs.pop(run.id, None)


def _find(run_id: str) -> Run | None:
    with _runs_lock:
        return _runs.get(run_id)


def _pump(run: Run, events: Iterator[tuple[str, dict[str, Any]]]) -> None:
    """在独立线程里跑 agent 循环，把事件推进通道。**这个函数的线程是运行的主线程。**

    生成器被确认题阻塞时，`interaction.ask()` 会绕过它直接往队列里塞问题，
    所以这里的阻塞不会卡住前端。
    """
    # 改动记录器也在这里激活：它和交互通道一样，是「这一轮运行的上下文」——
    # 文件工具在别的线程/别的模块里执行，靠 ContextVar 找到当前这一轮
    recorder = changes.ChangeRecorder(
        work_dir=current_work_dir(),
        home=get_settings().home,
        conversation_id=run.conversation_id,
        run_id=run.id,
    )
    token = interaction.activate(run.channel)
    changes_token = changes.activate(recorder)
    try:
        # 先报一次「我是谁」。前端要靠 run_id 才能取消这一轮，而 run_id 是运行开始时
        # 才生成的。**从 pump 里发而不是在端点里发**，是为了保证它排在这条流的最前面 ——
        # 端点那边发的话，它和下面这些事件是两条线程写同一个队列，顺序没有保证
        run.channel.publish(("start", {"run_id": run.id}))

        for event in events:
            run.channel.publish(event)
    except Exception:  # noqa: BLE001 —— 兜住任何漏出来的异常
        # 生成器内部已经把「模型调用失败」之类转成了 Notice，这里兜的是外围异常
        # （落盘失败之类）。不兜的话线程会静默死掉，前端一直转圈等一个永远不来的 done。
        #
        # **异常原文不发给浏览器**：`str(exc)` 里常带着内部文件路径、配置片段，
        # 而这条 Notice 会原样渲染在对话里。详情写日志（那是本机），界面只给一句
        # 能行动的话
        logger.exception("这一轮运行中断")
        run.channel.publish(("notice", {"text": "运行中断：服务端出错了，详情见终端日志。"}))
    finally:
        run.channel.close()
        # 必须解绑：线程池会复用线程，留着会串到下一轮
        changes.deactivate(changes_token)
        interaction.deactivate(token)
        _close(run)


# SSE 心跳间隔（秒）。
#
# `queue.get()` 在没事件时会一直挂着 —— 那是长连接的常态（等模型吐第一个字、等工具
# 跑完、子代理在干活），所以不能把它当错误。但对**客户端**来说，「一直没有数据」和
# 「连接死了」是分不清的：前端为此设了一条静默保护（见 `web/src/api/chat.ts` 的
# `IDLE_TIMEOUT_MS`，360 秒），撞上就掐掉整条流 —— 服务端那一轮也跟着被取消。
#
# 心跳填的就是这段：发一行 SSE 注释，按协议会直接被忽略，但它让字节流不断。
# 60 秒相对 360 秒有 6 倍余量，也不至于让空闲连接持续产生无用流量。
SSE_HEARTBEAT_SECONDS = 60.0


async def _sse(run: Run) -> AsyncIterator[str]:
    """把通道里的事件翻成 SSE 文本。

    用 async 生成器而不是同步的：取值本来就是等待，`await queue.get()` 不占线程；
    写成同步生成器的话，starlette 得再开一个线程池线程来跑它。

    **取事件带超时**：一旦静默超过 `SSE_HEARTBEAT_SECONDS` 就补一行注释。
    这不是为了传数据，是为了让对面知道这条连接还活着 —— 理由见那个常量的说明。
    """
    finished = False

    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    run.channel.queue.get(), timeout=SSE_HEARTBEAT_SECONDS
                )
            except asyncio.TimeoutError:
                # `: ` 开头是 SSE 的注释行，客户端按协议应当忽略 —— 前端确实忽略了
                # （`parseBlock` 认不出 `event:` / `data:` 就跳过），但它照样算「收到了字节」
                yield ": keep-alive\n\n"
                continue

            if item is None:  # 通道关闭，这一轮结束
                finished = True
                return

            name, payload = item
            yield _event(name, payload)
    finally:
        # 客户端断开（关掉页面、断网、切走会话）时 starlette 会取消这个生成器，
        # 但真正干活的 `_pump` 跑在**另一条线程**上，完全不受影响 —— 不主动取消的话，
        # 它会一直跑下去；正卡在确认题上时更是要等到超时（10 分钟）才结束。
        #
        # 正常结束（收到通道关闭）不用取消：那一轮的收尾已经做完了
        if not finished:
            run.channel.cancel()


@router.post("/chat")
async def chat(
    conversation_id: Annotated[str, Form()],
    prompt: Annotated[str, Form()],
    model_config_id: Annotated[str, Form()] = "",
    model: Annotated[str, Form()] = "",
    mode_id: Annotated[str, Form()] = "",
    # FastAPI 会把表单里的 "true"/"false"/"1"/"0" 解析成 bool；缺省是开（与历史行为一致）
    thinking: Annotated[bool, Form()] = True,
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

    **`async def` 而不是同步 `def`**：以前是同步的，理由是「`run_agent_stream`
    内部是阻塞式 SDK 调用，放事件循环里会卡死服务」。现在那段阻塞式代码跑在
    `_pump` 自己的线程里，端点本身只剩「建个运行、起个线程」这种不阻塞的活，
    而且它必须拿到事件循环（`get_running_loop`）才能建队列 —— 所以改回异步是对的。
    附件是在 `_pump` 那个线程里读的，这里的 `UploadFile.file` 只是个句柄。
    """
    request = ChatRequest(
        conversation_id=conversation_id,
        prompt=prompt,
        model_config_id=model_config_id,
        model=model,
        mode_id=mode_id,
        thinking=thinking,
    )

    uploads = [
        Attachment(name=item.filename or "attachment", handle=item.file) for item in files
    ]

    run = Run(conversation_id=conversation_id, loop=asyncio.get_running_loop())
    _open(run)

    # daemon：进程退出时不该被卡在确认题上的线程拖住
    threading.Thread(
        target=_pump,
        # run.id 要传进去：助手消息边跑边落盘时，靠它认领盘上那条记录（见 `_AnswerSaver`）
        args=(run, stream_round(request, uploads, run.id)),
        name=f"quill-run-{run.id}",
        daemon=True,
    ).start()

    return StreamingResponse(
        _sse(run),
        media_type="text/event-stream",
        # 关掉中间层缓冲，否则前端会「憋一大段才收到」，流式就白做了
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/answer")
async def answer(payload: AnswerPayload) -> dict[str, bool]:
    """回答运行中抛出的一个问题（执行前确认 / 模型提问）。

    找不到运行、或问题 id 对不上时返回 `accepted: false`，**而不是 404**：
    这个请求晚到是完全正常的 —— 用户点「允许」的同时那一轮可能刚好超时结束。
    前端不该为一个正常的竞态弹错误提示。
    """
    run = _find(payload.run_id)
    if run is None:
        return {"accepted": False}

    return {"accepted": run.channel.answer(payload.question_id, payload.answer)}


@router.post("/chat/cancel")
async def cancel(payload: CancelPayload) -> dict[str, bool]:
    """停止正在跑的这一轮。

    和 `/chat/answer` 同一个口径：找不到运行时返回 `cancelled: false` 而不是 404 ——
    停止一个已经结束的轮次是个正常操作，不是错误。

    **只是「请求停止」，不是「已经停了」**：这个接口把标记立起来、唤醒正在等的提问，
    agent 循环在下一个检查点退出。所以调用方不要指望返回 true 就代表线程已经收尾，
    真正的结束信号仍然是从 SSE 里收到 `done`。
    """
    run = _find(payload.run_id)
    if run is None:
        return {"cancelled": False}

    run.channel.cancel()
    return {"cancelled": True}
