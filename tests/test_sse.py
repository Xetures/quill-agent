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
from server.routes import chat
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


def test_a_quiet_stream_is_kept_alive(
    loop: asyncio.AbstractEventLoop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """静默超过心跳间隔时补一行 SSE 注释，别让对面以为连接死了。

    真事（2026-09-21）：一次 `spawn_agent` 跑了整 360 秒，期间事件流一个字节都不走，
    撞上前端那条 360 秒的静默保护（`web/src/api/chat.ts` 的 `IDLE_TIMEOUT_MS`）——
    整条流被当成死连接掐掉，`reader.cancel()` 一路传到服务端，**那一轮白跑**。

    **「没事件」不等于「死了」**：一次长思考、一个慢工具、子代理在干活，都会让流安静
    好几分钟 —— 越是认真的任务越容易撞上。心跳填的就是这段。

    发的是注释行（`: ` 开头），协议规定客户端应当忽略：前端 `parseBlock` 认不出
    `event:` / `data:` 就跳过，但那一行字节照样算「收到了数据」。
    """
    monkeypatch.setattr(chat, "SSE_HEARTBEAT_SECONDS", 0.05)
    run = Run(conversation_id="c3", loop=loop)
    _open(run)

    async def read_a_couple_of_beats() -> list[str]:
        stream = _sse(run)
        try:
            return [await stream.__anext__() for _ in range(2)]
        finally:
            await stream.aclose()

    try:
        chunks = asyncio.run_coroutine_threadsafe(read_a_couple_of_beats(), loop).result(timeout=5)

        assert all(chunk.startswith(":") for chunk in chunks)
        assert all("keep-alive" in chunk for chunk in chunks)
    finally:
        _close(run)
