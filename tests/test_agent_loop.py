"""Agent 循环的边界测试：工具轮次预算与强制收尾。

用假的模型客户端替换掉 OpenAI SDK，专门盯住 MAX_ITERATIONS 最容易出错的地方：
预算耗尽后不能再去执行工具 —— 那次执行的结果没有后续请求去消化，副作用白做，
用户拿到的还只是一句「未完成」。
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace

from quill_agent import agent, interaction
from quill_agent.models import ModelChoice, ModelConfig


def _text_chunk(text: str) -> SimpleNamespace:
    """一个只带文本增量的流式分片。"""
    delta = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _tool_chunk(call_id: str, name: str) -> SimpleNamespace:
    """一个只带工具调用的流式分片（id 与函数名一次给全）。"""
    call = SimpleNamespace(
        index=0,
        id=call_id,
        function=SimpleNamespace(name=name, arguments="{}"),
    )
    delta = SimpleNamespace(content=None, tool_calls=[call])
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _reasoning_chunk(text: str) -> SimpleNamespace:
    """一个只带思维链增量的分片（推理模型的非标准字段）。"""
    delta = SimpleNamespace(content=None, tool_calls=None, reasoning_content=text)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


class _FakeClient:
    """按预设顺序吐出一批批分片，并记录每次请求的参数。"""

    def __init__(self, responses: list[list], requests: list[dict]) -> None:
        self._responses = responses
        self._requests = requests
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self._requests.append(kwargs)
        return iter(self._responses.pop(0))


def _make_choice(base_url: str = "") -> ModelChoice:
    """一次模型选择。base_url 可指定 —— 用量探测是按它分别缓存的。"""
    config = ModelConfig(id="c1", name="测试连接", models=["m"], base_url=base_url)
    return ModelChoice(config=config, model="m")


def _run(monkeypatch, responses: list[list], stats=None) -> tuple[list, list[dict]]:
    """跑一轮对话，返回（事件流, 每次请求的参数）。"""
    requests: list[dict] = []
    monkeypatch.setattr(agent, "OpenAI", lambda **kwargs: _FakeClient(responses, requests))
    # 工具菜单固定为非空，免得测试结果受本机工具注册表的影响
    # （mode=None 时本来一个工具都不给，这里要的就是「有工具」这个前提）
    monkeypatch.setattr(
        agent.registry, "schemas", lambda *args, **kwargs: [{"type": "function"}]
    )

    events = list(
        agent.run_agent_stream(
            prompt="继续",
            files=[],
            mode=None,
            choice=_make_choice(),
            history=[],
            stats=stats,
        )
    )
    return events, requests


def _texts(events: list) -> str:
    """事件流里的文本增量拼起来。"""
    return "".join(item for item in events if isinstance(item, str))


def _steps(events: list) -> list:
    """事件流里的工具调用记录。"""
    return [item for item in events if isinstance(item, agent.ToolStep)]


def _notices(events: list) -> list[str]:
    """事件流里的系统提示（不是模型输出）。"""
    return [item.text for item in events if isinstance(item, agent.Notice)]


def _usage_chunk(prompt_tokens: int, completion_tokens: int) -> SimpleNamespace:
    """只带用量的收尾分片 —— 它没有 choices，很容易被当成无用分片丢掉。"""
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return SimpleNamespace(choices=[], usage=usage)


class _StrictClient(_FakeClient):
    """模拟不认 stream_options 的网关：带上这个参数就直接报错。"""

    def _create(self, **kwargs):
        self._requests.append(kwargs)
        if "stream_options" in kwargs:
            raise TypeError("unexpected keyword argument 'stream_options'")
        return iter(self._responses.pop(0))


def test_plain_answer_finishes_in_one_request(monkeypatch) -> None:
    """模型直接给答案时，只请求一次。"""
    events, requests = _run(monkeypatch, [[_text_chunk("你好")]])

    assert _texts(events) == "你好"
    assert len(requests) == 1
    assert requests[0]["tools"] is not None


def test_tool_rounds_are_capped_and_never_wasted(monkeypatch) -> None:
    """模型一直要工具时：只执行 MAX_ITERATIONS 轮，然后强制收尾。"""
    responses = [
        [_tool_chunk(f"call_{index}", "no_such_tool")] for index in range(agent.MAX_ITERATIONS + 2)
    ]

    events, requests = _run(monkeypatch, responses)

    # 工具恰好执行 MAX_ITERATIONS 轮。收尾请求不带 tools，模型即便仍返回工具调用
    # 也不会被执行 —— 那种执行的结果没有任何人去消化，副作用纯属白做
    assert len(_steps(events)) == agent.MAX_ITERATIONS
    # 请求次数 = 工具轮次 + 1，多出来的那次就是「强制收尾」
    assert len(requests) == agent.MAX_ITERATIONS + 1
    assert requests[-2]["tools"] is not None
    assert requests[-1]["tools"] is None
    # 收尾失败给的是系统提示，不是模型说过的话
    assert any("上限" in text for text in _notices(events))


def test_empty_reply_yields_a_notice(monkeypatch) -> None:
    """模型一个字都没说时给个提示，而不是让界面空着。"""
    # 只带用量的分片：既没有文本增量，也没有工具调用
    events, requests = _run(monkeypatch, [[SimpleNamespace(choices=[])]])

    assert _texts(events) == ""
    assert _steps(events) == []
    assert len(requests) == 1
    assert any("没有返回任何内容" in text for text in _notices(events))


def test_leaked_tool_call_is_reported_not_answered(monkeypatch) -> None:
    """模型把工具调用写成了正文时，不能当成最终回答静默结束。

    真事：deepseek 系的模型偶尔把原生标记漏进 content，`tool_calls` 因此是空的。
    以前这一轮会被判成「模型答完了」，界面上只剩一段乱码 —— 看起来就像卡住了。
    """
    leak = '我再看两处配置。\n<\uff5c\uff5cDSML\uff5c\uff5c invoke name="read_file">\n</invoke>\n'

    events, requests = _run(monkeypatch, [[_text_chunk(leak)]])

    # 标记之前的人话留下，标记之后的内容一个字都不进正文（否则会污染历史）
    assert _texts(events) == "我再看两处配置。\n"
    assert _steps(events) == []
    assert len(requests) == 1
    assert any("写成了普通文本" in text for text in _notices(events))


def test_leak_marker_split_across_chunks_is_caught(monkeypatch) -> None:
    """标记被切在相邻两个分片之间时也要认得出来。"""
    events, _ = _run(monkeypatch, [[_text_chunk("好的</inv"), _text_chunk("oke>")]])

    assert _texts(events) == "好的"
    assert any("写成了普通文本" in text for text in _notices(events))


def test_normal_answer_is_not_mistaken_for_a_leak(monkeypatch) -> None:
    """普通回答照常逐字吐出，压住的那一小段尾巴最后要补上。"""
    events, _ = _run(monkeypatch, [[_text_chunk("答案是 "), _text_chunk("42。")]])

    assert _texts(events) == "答案是 42。"
    assert _notices(events) == []


def test_reasoning_content_is_yielded_separately(monkeypatch) -> None:
    """推理模型的思考过程单独产出，不混进正文。"""
    events, _ = _run(monkeypatch, [[_reasoning_chunk("先想想…"), _text_chunk("答案是 42。")]])

    reasoning = "".join(item.text for item in events if isinstance(item, agent.ReasoningDelta))
    assert reasoning == "先想想…"
    assert _texts(events) == "答案是 42。"
    # 有思考不算「空回答」：正文也给出了
    assert _notices(events) == []


def test_reasoning_only_reply_still_warns(monkeypatch) -> None:
    """只思考、不回答仍然算空回答，要给提示。"""
    events, _ = _run(monkeypatch, [[_reasoning_chunk("想啊想")]])

    assert _texts(events) == ""
    assert any("没有返回任何内容" in text for text in _notices(events))


def test_last_request_forces_a_final_answer(monkeypatch) -> None:
    """预算耗尽后的那次请求不再提供工具，模型只能凭已有信息作答。"""
    responses = [
        [_tool_chunk(f"call_{index}", "no_such_tool")] for index in range(agent.MAX_ITERATIONS)
    ]
    responses.append([_text_chunk("根据已有信息，答案是 42。")])

    events, requests = _run(monkeypatch, responses)

    assert "答案是 42" in _texts(events)
    assert len(requests) == agent.MAX_ITERATIONS + 1
    assert requests[-1]["tools"] is None


def test_empty_tool_menu_is_sent_as_none(monkeypatch) -> None:
    """一个启用的工具都没有时，不能给 API 传空列表。"""
    requests: list[dict] = []
    monkeypatch.setattr(
        agent, "OpenAI", lambda **kwargs: _FakeClient([[_text_chunk("好")]], requests)
    )
    monkeypatch.setattr(agent.registry, "schemas", lambda *args, **kwargs: [])

    list(
        agent.run_agent_stream(
            prompt="你好", files=[], mode=None, choice=_make_choice(), history=[]
        )
    )

    assert requests[0]["tools"] is None


# ---------------------------------------------------------------------------
# 用量与耗时
# ---------------------------------------------------------------------------
def test_stats_accumulate_usage_across_requests(monkeypatch) -> None:
    """一轮里可能请求模型多次，用量要累加。"""
    responses = [
        [_tool_chunk("call_1", "no_such_tool"), _usage_chunk(10, 5)],
        [_text_chunk("好了"), _usage_chunk(20, 8)],
    ]
    stats = agent.RunStats()

    _run(monkeypatch, responses, stats=stats)

    assert stats.prompt_tokens == 30
    assert stats.completion_tokens == 13
    assert stats.total_tokens == 43


def test_stats_elapsed_is_always_filled(monkeypatch) -> None:
    """不管从哪条路结束，耗时都要补上。"""
    stats = agent.RunStats()

    _run(monkeypatch, [[_text_chunk("好")]], stats=stats)

    assert stats.elapsed > 0


def test_tool_step_records_elapsed(monkeypatch) -> None:
    """工具耗时是排查「哪一步慢」的唯一依据。"""
    responses = [[_tool_chunk("call_1", "no_such_tool")], [_text_chunk("好")]]

    events, _ = _run(monkeypatch, responses)

    steps = _steps(events)
    assert len(steps) == 1
    assert isinstance(steps[0].elapsed, float)


def test_open_stream_falls_back_when_options_unsupported(monkeypatch) -> None:
    """不认 stream_options 的网关：退回普通请求，而不是让整轮失败。"""
    requests: list[dict] = []
    responses = [[_text_chunk("好")]]
    monkeypatch.setattr(agent, "_stream_usage_cache", {})
    monkeypatch.setattr(agent, "OpenAI", lambda **kwargs: _StrictClient(responses, requests))
    monkeypatch.setattr(agent.registry, "schemas", lambda *args, **kwargs: [{"type": "function"}])

    events = list(
        agent.run_agent_stream(
            prompt="继续", files=[], mode=None, choice=_make_choice(), history=[]
        )
    )

    # 第一次带 stream_options 被拒，第二次去掉参数就成功了
    assert "stream_options" in requests[0]
    assert "stream_options" not in requests[1]
    assert _texts(events) == "好"


def test_stream_usage_probe_is_cached(monkeypatch) -> None:
    """探测结果要记住：不能每一轮都白跑一次注定失败的请求。"""
    requests: list[dict] = []
    responses = [[_text_chunk("一")], [_text_chunk("二")]]
    monkeypatch.setattr(agent, "_stream_usage_cache", {})
    monkeypatch.setattr(agent, "OpenAI", lambda **kwargs: _StrictClient(responses, requests))
    monkeypatch.setattr(agent.registry, "schemas", lambda *args, **kwargs: [{"type": "function"}])

    def run_once() -> None:
        list(
            agent.run_agent_stream(
                prompt="继续", files=[], mode=None, choice=_make_choice(), history=[]
            )
        )

    run_once()
    # 探测结果按 base_url 记录；测试用的连接没配 base_url，所以键是空串
    assert agent._stream_usage_cache == {"": False}

    requests.clear()
    run_once()

    assert len(requests) == 1
    assert "stream_options" not in requests[0]


def test_usage_probe_is_per_provider(monkeypatch) -> None:
    """一个网关不认 stream_options，不该连累别的网关。

    探测结果按 base_url 分开记 —— 存成单个全局布尔的话，某个网关失败一次，
    之后切回支持的服务也永久拿不到用量统计了。
    """
    requests: list[dict] = []
    responses = [[_text_chunk("一")], [_text_chunk("二")]]
    monkeypatch.setattr(agent, "_stream_usage_cache", {})
    monkeypatch.setattr(agent, "OpenAI", lambda **kwargs: _StrictClient(responses, requests))
    monkeypatch.setattr(agent.registry, "schemas", lambda *args, **kwargs: [{"type": "function"}])

    def run_with(base_url: str) -> None:
        list(
            agent.run_agent_stream(
                prompt="继续",
                files=[],
                mode=None,
                choice=_make_choice(base_url=base_url),
                history=[],
            )
        )

    # 网关 A：不认这个参数，被 _StrictClient 拒掉
    run_with("https://a.example")
    assert agent._stream_usage_cache == {"https://a.example": False}

    # 网关 B 是全新地址，缓存里没有它 —— 第一次请求就该带上 stream_options
    requests.clear()
    run_with("https://b.example")

    assert "stream_options" in requests[0]


# ---------------------------------------------------------------------------
# 取消
# ---------------------------------------------------------------------------


def _tool_chunk_at(index: int, call_id: str, name: str) -> SimpleNamespace:
    """带 index 的工具调用分片 —— 一批里有多个调用时，index 是它们唯一的区分。"""
    call = SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments="{}"),
    )
    delta = SimpleNamespace(content=None, tool_calls=[call])
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


@contextmanager
def _cancellable() -> Iterator[object]:
    """绑一条真通道到当前线程，退出时解绑。"""
    # cancel() 用不到事件循环，构造函数要一个只是为了让 publish 有地方去
    loop = asyncio.new_event_loop()
    channel = interaction.Interaction(loop, run_id="r1")
    token = interaction.activate(channel)
    try:
        yield channel
    finally:
        interaction.deactivate(token)
        loop.close()


def test_cancel_stops_the_loop_before_the_next_request(monkeypatch) -> None:
    """取消之后不再发下一次请求 —— 那正是「停止」的意义。

    这里让工具在执行期间按下停止：循环必须在这一轮收尾后退出，
    而不是揣着取消标记再问模型一遍。
    """
    with _cancellable():

        def fake_execute(name, arguments):
            interaction.current().cancel()
            return "结果"

        monkeypatch.setattr(agent.registry, "execute", fake_execute)
        events, requests = _run(monkeypatch, [[_tool_chunk("c1", "read_file")]])

    assert len(requests) == 1  # 第二次请求没有发生
    assert any("已取消这一轮" in text for text in _notices(events))


def test_cancel_between_tools_skips_the_rest_of_the_batch(monkeypatch) -> None:
    """一批里有好几个工具调用时，取消后剩下那些就别做了 —— 副作用已经没人要了。"""
    with _cancellable():
        ran: list[str] = []

        def fake_execute(name, arguments):
            ran.append(name)
            interaction.current().cancel()
            return "结果"

        monkeypatch.setattr(agent.registry, "execute", fake_execute)
        events, _ = _run(
            monkeypatch,
            [[_tool_chunk_at(0, "c1", "read_file"), _tool_chunk_at(1, "c2", "write_file")]],
        )

    assert ran == ["read_file"]  # 第二个没有执行
    assert len(_steps(events)) == 1
    assert any("剩下的工具调用没有执行" in text for text in _notices(events))


def test_no_channel_means_cancel_can_never_fire(monkeypatch) -> None:
    """没有通道时循环不该被「取消」影响 —— Streamlit 版和单元测试走的就是这条路。"""
    events, requests = _run(monkeypatch, [[_text_chunk("答完了")]])

    assert len(requests) == 1
    assert _texts(events) == "答完了"
    assert not any("取消" in text for text in _notices(events))
