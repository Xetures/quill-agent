"""运行期的交互通道：让工具在跑到一半时问用户，并阻塞等答案。

为什么需要这条「旁路」
----------------------
确认机制要求 agent 在**执行某个工具之前**停下来等用户点头。而 agent 循环是一个同步
生成器 —— 它被工具调用阻塞的那一刻，连 yield 都做不到，问题根本发不出去：

    生成器 ──yield──┐
                    ├──> queue ──> SSE ──> 浏览器
    ask() ──publish─┘   （生成器被阻塞时，这一路照样通）

所以问题不能走生成器，只能走一条属于本次运行的队列。生成器被卡住时，`ask()` 自己往
队列里塞一个事件；HTTP 层从队列里读、推给浏览器；用户在浏览器上回答后，另一个请求把
答案写回来，`ask()` 的阻塞解除，工具拿到答案继续跑。**生成器全程不中断。**

为什么用 ContextVar 而不是给工具加参数
--------------------------------------
工具的签名是 ``fn(**args) -> str``，参数是从模型的 JSON 里解出来的。往这个契约里塞一个
「运行上下文」意味着 `ToolRegistry.execute()` 和全部 11 个工具都要改签名，而绝大多数
工具根本用不上它。ContextVar 只在 runner 线程里设一次，谁要用谁自己取。

用 ContextVar 而不是 threading.local：每个线程天然有一份独立的上下文，`activate` /
`deactivate` 成对出现即可；将来若把 runner 换成 async task，这里一行不用改。
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

# 等不到回答时的上限（秒）。
#
# 必须有：用户关掉页面走人时，这一轮不能把 runner 线程永久占住 —— 那是「暂停等人」
# 最容易踩的坑。10 分钟是「人就在旁边、只是去接了个电话」和「人已经走了」之间的折中。
DEFAULT_TIMEOUT = 600.0

# 确认题的选项文案。定义成常量是为了让「判定哪一个是允许」和「展示给用户看」
# 用同一份定义，不至于一处改成「同意」另一处还在比「允许」。
APPROVE = "允许"
DENY = "拒绝"


@dataclass(frozen=True)
class Question:
    """抛给用户的一个问题。

    Attributes:
        id: 本次运行内唯一。答案按它回填 —— 用 id 而不是「第几个问题」，是因为
            答案可能乱序到达，而 id 能天然忽略掉重复提交。
        kind: 「confirm」（执行前确认）或「ask」（模型主动提问）。前端据此决定
            渲染成按钮组还是输入框。
        text: 问题本身，一句话。
        detail: 补充材料 —— 确认题里放要执行的命令或要做的事，让用户真的能判断。
        options: 可选项；为空表示自由输入。
        timeout: 等待上限（秒），供前端倒计时用。
    """

    id: str
    kind: str
    text: str
    detail: str = ""
    options: tuple[str, ...] = ()
    timeout: float = DEFAULT_TIMEOUT

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "detail": self.detail,
            "options": list(self.options),
            "timeout": self.timeout,
        }


class Interaction:
    """一次运行的交互通道。

    涉及三个线程，所以状态要用 `threading.Lock` 保护、跨线程唤醒要用
    `threading.Event`（asyncio 那套原语在这里用不上，`queue` 是个例外 ——
    它的写入由事件循环代劳）：

        - **runner 线程**：跑 agent 循环，工具在它里面调 `ask()` 并阻塞；
        - **事件循环**：从 `queue` 里取事件推给浏览器；
        - **请求线程**：处理 `POST /chat/answer`，把答案写回来。
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, run_id: str = "") -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.run_id = run_id
        self._loop = loop
        self._lock = threading.Lock()
        # 前端一次运行只展示一张问题卡。并行子代理可能同时走到确认点，因此发布前
        # 必须在这里排队；否则后一个 ask() 会覆盖前一个的 waiter / question id。
        self._question_lock = threading.Lock()
        self._waiter: threading.Event | None = None
        self._pending: str = ""  # 正在等答案的问题 id；空串表示没有
        self._answer: str | None = None
        self._cancelled = False
        self._seq = 0

    # ── 事件往外推 ────────────────────────────────────────────────────

    def publish(self, event: tuple[str, dict[str, Any]]) -> None:
        """把一个事件交给 HTTP 层。

        可从任意线程调用：`call_soon_threadsafe` 只负责把它排进事件循环，
        `put_nowait` 在循环线程里执行。队列无上界，所以不会阻塞 runner 线程 ——
        这一点很关键，阻塞了就等于死锁。
        """
        self._loop.call_soon_threadsafe(self.queue.put_nowait, event)

    def close(self) -> None:
        """关闭通道：让 HTTP 层的取值循环退出。"""
        self._loop.call_soon_threadsafe(self.queue.put_nowait, None)

    # ── 提问 ─────────────────────────────────────────────────────────

    def ask(
        self,
        *,
        kind: str,
        text: str,
        detail: str = "",
        options: Sequence[str] = (),
        timeout: float = DEFAULT_TIMEOUT,
    ) -> str | None:
        """发一个问题并**阻塞**等答案。

        并行调用会在发布前排队：前端每次运行只维护一张问题卡，后一个问题不能覆盖
        前一个。`timeout` 从调用本方法时开始计算，包含排队时间。

        Returns:
            用户给的答案；**问不到时返回 None**（超时 / 取消 / 没有前端接住）。
            调用方必须区分「None」和「用户回答了空字符串」：前者是没问到，
            后者是一个明确的（只是内容为空的）回答。
        """
        timeout = max(0.0, timeout)
        deadline = time.monotonic() + timeout

        # Lock.acquire() 不能同时等锁和取消，用短超时轮询给 cancel() 留检查点。
        while not self._question_lock.acquire(
            timeout=min(0.1, max(0.0, deadline - time.monotonic()))
        ):
            if self.is_cancelled() or time.monotonic() >= deadline:
                return None

        try:
            with self._lock:
                if self._cancelled:
                    return None

                self._seq += 1
                remaining = max(0.0, deadline - time.monotonic())
                question = Question(
                    id=f"q{self._seq}",
                    kind=kind,
                    text=text,
                    detail=detail,
                    options=tuple(options),
                    timeout=remaining,
                )
                waiter = threading.Event()
                self._waiter = waiter
                self._pending = question.id
                self._answer = None

            self.publish(("question", {**question.to_payload(), "run_id": self.run_id}))

            # 返回值不看 wait() 的结果，只看「有没有答案落进来」：
            # 超时和「答案恰好在超时那一下写进来」之间有个窄窗口，
            # answer() 已经认领了这次提问（清空了 _pending）并返回 True，
            # 那这一轮就必须用它的答案，否则两边对这次提问的结论会不一致。
            waiter.wait(remaining)

            with self._lock:
                answered = self._answer is not None
                value = self._answer
                self._answer = None
                self._waiter = None
                self._pending = ""

            return value if answered else None
        finally:
            self._question_lock.release()

    def answer(self, question_id: str, value: str) -> bool:
        """回填一个答案并唤醒等待中的提问。

        Returns:
            是否被接受。**问题 id 对不上的一律拒绝** —— 那个问题要么已经回答过，
            要么已经被超时放弃；一个迟到的答案不该落到下一轮提问上。
        """
        with self._lock:
            if self._waiter is None or self._pending != question_id:
                return False

            self._answer = value
            waiter = self._waiter
            self._pending = ""  # 认领掉了：之后的重复提交会被上面这条判掉

        waiter.set()
        return True

    def waiting(self) -> bool:
        """当前是否有问题在等答案（给测试和排查用）。"""
        with self._lock:
            return self._waiter is not None

    # ── 取消 ─────────────────────────────────────────────────────────

    def cancel(self) -> None:
        """取消这一轮。

        两件事：给 agent 循环留一个「别再往下跑了」的标记，以及**把正在等的提问唤醒** ——
        否则用户在确认卡片上点「停止」时，那一轮还卡在 `wait()` 里，什么都不会发生。

        被唤醒的 `ask()` 会返回 None（`_answer` 始终是空的），也就是走「问不到」那条路。
        调用方不需要为取消单独写一套返回值。
        """
        with self._lock:
            self._cancelled = True
            waiter = self._waiter
            self._waiter = None
            self._pending = ""

        if waiter is not None:
            waiter.set()

    def is_cancelled(self) -> bool:
        """用户是否要求取消这一轮。"""
        with self._lock:
            return self._cancelled


# 当前线程正在跑的那一轮。没有绑定时是 None。
_current: ContextVar[Interaction | None] = ContextVar("quill_interaction", default=None)


def activate(channel: Interaction) -> Token:
    """把一条通道绑定到当前线程，返回交给 `deactivate` 的 token。"""
    return _current.set(channel)


def deactivate(token: Token) -> None:
    """解绑。**必须放在 finally 里** —— 线程池会复用线程，留着会串到下一轮。"""
    _current.reset(token)


def current() -> Interaction | None:
    """当前这一轮的通道；没有（单元测试 / 非对话场景）时返回 None。"""
    return _current.get()


def cancelled() -> bool:
    """用户是否要求取消当前这一轮。

    没有通道时永远是 False —— 取消是「外面的人」发起的事，没有外面的人就没人能取消。
    """
    channel = _current.get()
    return channel is not None and channel.is_cancelled()


def ask(
    *,
    kind: str,
    text: str,
    detail: str = "",
    options: Sequence[str] = (),
    timeout: float = DEFAULT_TIMEOUT,
) -> str | None:
    """问用户一个问题并阻塞等答案；没有通道时直接返回 None。

    没有通道**不抛异常**：「没法问」是这类场景的常态（单元测试、纯脚本调用都没有这条通道），
    不该被当成错误。但调用方必须处理这个分支 —— 见 `confirm` 对 None 的说明。
    """
    channel = _current.get()
    if channel is None:
        return None
    return channel.ask(kind=kind, text=text, detail=detail, options=options, timeout=timeout)


def confirm(*, text: str, detail: str = "", timeout: float = DEFAULT_TIMEOUT) -> bool | None:
    """问一个是非题。

    Returns:
        True 允许、False 拒绝、**None 表示问不到**（没有通道 / 超时）。
        None 和 False 必须分开：前者要告诉调用方「当前界面根本没法确认」，
        提示语完全不同 —— 用户才知道该去哪儿改。
    """
    answer = ask(
        kind="confirm",
        text=text,
        detail=detail,
        options=(APPROVE, DENY),
        timeout=timeout,
    )

    if answer is None:
        return None
    return answer == APPROVE
