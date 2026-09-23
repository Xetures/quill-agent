"""模型「还没开口」时按停止，必须真能停下来。

回归的是这个场景：取消检查原先放在 `for chunk in stream` 的循环体里，只有**收到分片**时
才会被执行 —— 而模型吐出第一个字之前可能静默很久（推理模型尤其）。那段时间循环阻塞在
`next(stream)` 上，检查一次都轮不到：用户按了停止，界面毫无反应。

实测过一次：界面上连点十几次「停止」，服务端日志里每次都是 200（请求到了、标记也立了），
可那条流始终不收尾。所以这里用一个「一个分片都不吐的流」把那一刻钉住。
"""

import asyncio
import threading
from types import SimpleNamespace

from quill_agent import agent, interaction
from quill_agent.models import ModelChoice, ModelConfig


class _SilentStream:
    """**一个分片都不吐**的流：模拟模型开口之前那段静默。

    `__next__` 一直等到 `release` 被放开才结束 —— 在那之前，读流的那条线程就卡在这儿，
    和真实情况一样。
    """

    def __init__(self) -> None:
        self.release = threading.Event()

    def __iter__(self):
        return self

    def __next__(self):
        # **一直等**，直到测试放行才结束 —— 这就是「模型还没吐第一个字」。
        # 不能写成「等一小会儿就 StopIteration」：那样流会自己结束，测试根本碰不到
        # 目标场景（第一次这么写，测试如实报了「还没取消就结束了」）
        while not self.release.wait(0.05):
            pass
        raise StopIteration

    def close(self) -> None:
        self.release.set()


def _make_choice() -> ModelChoice:
    return ModelChoice(config=ModelConfig(id="c1", name="测试连接", models=["m"]), model="m")


def test_cancel_is_seen_while_the_stream_is_silent(monkeypatch) -> None:
    stream = _SilentStream()

    class _Client:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **_: stream))

    monkeypatch.setattr(agent, "OpenAI", lambda **_: _Client())
    monkeypatch.setattr(agent, "run_max_iterations", lambda: agent.MAX_ITERATIONS)
    # 开销上限也读偏好文件，一并按住 —— 否则测试结果会跟着本机设置变
    if hasattr(agent, "run_token_limit"):
        monkeypatch.setattr(agent, "run_token_limit", lambda: 10**9)
    monkeypatch.setattr(agent.registry, "schemas", lambda *args, **kwargs: [{"type": "function"}])

    loop = asyncio.new_event_loop()
    channel = interaction.Interaction(loop, run_id="r1")
    events: list = []
    finished = threading.Event()

    def work() -> None:
        interaction.activate(channel)
        try:
            events.extend(
                agent.run_agent_stream(
                    prompt="继续", files=[], mode=None, choice=_make_choice(), history=[]
                )
            )
        finally:
            finished.set()

    threading.Thread(target=work, daemon=True).start()

    # 先确认它确实卡在「等模型开口」上：还没取消时不该自己收场，
    # 否则这个测试根本碰不到要验的那段静默
    assert not finished.wait(0.6), "还没取消就结束了，这个测试没碰到目标场景"

    channel.cancel()

    # 取消之后必须在一个轮询间隔左右收场（这里给足余量）
    assert finished.wait(3.0), "按了停止仍然卡着 —— 取消没能穿透「等模型开口」这段静默"
    assert any("已取消" in f"{getattr(item, 'text', item)}" for item in events)

    stream.release.set()
