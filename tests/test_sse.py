"""SSE 通道的收尾：客户端断开时那一轮必须被取消。

为什么值得单独一条：`_sse` 是个 async 生成器，而真正干活的 `_pump` 跑在**另一条
线程**上。客户端关掉页面 / 断网时，starlette 只会取消这个生成器，runner 线程毫无
感知 —— 它会继续跑到底；正卡在确认题上时更是要等到 10 分钟超时才结束。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator

import pytest
from server.routes.chat import Run, _close, _open, _sse


@pytest.fixture
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    """在后台线程里跑一个真事件循环（通道本来就为跨线程投递而设计）。"""
    event_loop = asyncio.new_event_loop()
    thread = threading.Thread(target=event_loop.run_forever, daemon=True)
    thread.start()

    try:
        yield event_loop
    finally:
        event_loop.call_soon_threadsafe(event_loop.stop)
        thread.join(timeout=2)
        event_loop.close()


def test_client_disconnect_cancels_the_run(loop: asyncio.AbstractEventLoop) -> None:
    """断开连接 = 取消这一轮。"""
    run = Run(conversation_id="c1", loop=loop)
    _open(run)

    async def consume_then_disconnect() -> None:
        stream = _sse(run)
        run.channel.publish(("text", {"text": "hi"}))

        event = await stream.__anext__()
        assert "hi" in event

        # 模拟 starlette 在客户端断开时关闭这个生成器
        await stream.aclose()

    try:
        asyncio.run_coroutine_threadsafe(consume_then_disconnect(), loop).result(timeout=5)
        assert run.channel.is_cancelled() is True
    finally:
        _close(run)


def test_normal_finish_does_not_cancel(loop: asyncio.AbstractEventLoop) -> None:
    """正常跑完的那一轮不该被标记成「取消」—— 收尾已经做完了。"""
    run = Run(conversation_id="c2", loop=loop)
    _open(run)

    async def consume_to_the_end() -> list[str]:
        stream = _sse(run)
        run.channel.publish(("text", {"text": "done"}))
        run.channel.close()  # 通道关闭 = 这一轮结束

        return [item async for item in stream]

    try:
        events = asyncio.run_coroutine_threadsafe(consume_to_the_end(), loop).result(timeout=5)

        assert len(events) == 1
        assert run.channel.is_cancelled() is False
    finally:
        _close(run)
