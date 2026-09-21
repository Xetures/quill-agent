"""Agent 循环的边界测试：工具轮次预算、开销上限与强制收尾。

用假的模型客户端替换掉 OpenAI SDK，专门盯住两条「闸门」最容易出错的地方：
轮次（`MAX_ITERATIONS`）和开销（`run_token_limit`）用尽后都不能再去执行工具 ——
那次执行的结果没有后续请求去消化，副作用白做，用户拿到的还只是一句「未完成」。
"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from quill_agent import agent, interaction
from quill_agent.models import ModelChoice, ModelConfig
from quill_agent.preferences import (
    MAX_ITERATIONS_KEY,
    MAX_RUN_TOKENS_KEY,
    PreferenceStore,
)


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
        # messages 是整轮复用的同一个 list，会随着工具结果不断 append。
        # 这里留一份当时的快照 —— 否则所有请求记录看到的都是最后一次的样子，
        # 「哪一轮带了什么」就验不出来了（收尾提示只在最后一轮加，正是要验的）。
        kwargs["messages"] = list(kwargs.get("messages") or [])
        self._requests.append(kwargs)
        return iter(self._responses.pop(0))


def _make_choice(base_url: str = "") -> ModelChoice:
    """一次模型选择。base_url 可指定 —— 用量探测是按它分别缓存的。"""
    config = ModelConfig(id="c1", name="测试连接", models=["m"], base_url=base_url)
    return ModelChoice(config=config, model="m")


def _run(
    monkeypatch, responses: list[list], stats=None, *, max_iterations=None, **kwargs
) -> tuple[list, list[dict]]:
    """跑一轮对话，返回（事件流, 每次请求的参数）。

    额外的关键字参数透传给 `run_agent_stream`（比如 `thinking=False`）。

    `max_iterations` 不传就按 `agent.MAX_ITERATIONS` 来 —— **绝不能让它去读真实的
    偏好文件**：那是用户本机的状态，测试结果会跟着「用户改没改设置」变，
    今天过明天挂。要验「配了才生效」就显式传值。
    """
    requests: list[dict] = []
    monkeypatch.setattr(agent, "OpenAI", lambda **_kw: _FakeClient(responses, requests))
    monkeypatch.setattr(
        agent, "run_max_iterations", lambda: max_iterations or agent.MAX_ITERATIONS
    )
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
            **kwargs,
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
        kwargs["messages"] = list(kwargs.get("messages") or [])
        self._requests.append(kwargs)
        if "stream_options" in kwargs:
            raise TypeError("unexpected keyword argument 'stream_options'")
        return iter(self._responses.pop(0))


class _RejectReasoningClient(_FakeClient):
    """模拟不认 `reasoning_effort` 的服务：带上这个参数就直接报错。

    关思考用的是这个字段 —— 它是 OpenAI 的标准字段，但不是所有兼容服务都实现了。
    不认的时候必须退回普通请求：**关不掉思考是小事，让整轮对话失败才是大事**。
    """

    def _create(self, **kwargs):
        if "reasoning_effort" in kwargs:
            self._requests.append(kwargs)
            raise TypeError("unexpected keyword argument 'reasoning_effort'")
        return super()._create(**kwargs)


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


def test_qwen_style_tool_call_block_is_caught(monkeypatch) -> None:
    """Qwen 系的标准调用块（`<tool_call>`）也要认出来。

    实测：Ollama 上跑 qwen 系模型时，调用可能以
    `<tool_call>{"name": …, "arguments": …}</tool_call>` 的形式漏进 content ——
    标签没被消掉，`tool_calls` 却是空的。
    """
    leak = '<tool_call>\n{"name": "read_file", "arguments": {"path": "README.md"}}\n</tool_call>'

    events, _ = _run(monkeypatch, [[_text_chunk(leak)]])

    assert _steps(events) == []
    assert any("写成了普通文本" in text for text in _notices(events))


def test_bare_json_tool_call_leak_is_caught(monkeypatch) -> None:
    """裸 JSON 的调用（一个标签都没有）也要报出来。

    实测：`qwen3-4b-function-calling-pro` 在真实工具组下稳定吐
    `[{"name": …, "arguments": …}]`。它不是任何固定标记，前缀匹配认不出来 ——
    不报的话用户只看到一串 JSON、工具一个都没跑，也不知道发生了什么。
    """
    leak = '[{"name": "list_dir", "arguments": {"path": "."}}]'

    events, _ = _run(monkeypatch, [[_text_chunk(leak)]])

    assert _steps(events) == []
    assert any("写成了普通文本" in text for text in _notices(events))


def test_plain_json_answer_is_not_called_a_leak(monkeypatch) -> None:
    """正常的 JSON 回答不该被扣上「工具调用漏了」的帽子（启发式要够保守）。"""
    events, _ = _run(monkeypatch, [[_text_chunk('{"answer": 42}')]])

    assert _notices(events) == []
    assert _texts(events) == '{"answer": 42}'


def test_looks_like_tool_call_heuristic() -> None:
    """启发式只在「确实像一次调用」时点头。"""
    assert agent._looks_like_tool_call('[{"name": "a", "arguments": {}}]')
    assert agent._looks_like_tool_call('{"name": "a", "parameters": {}}')
    # 有 name 但没有 arguments/parameters —— 不像调用
    assert not agent._looks_like_tool_call('{"name": "张三"}')
    # 完全正常的回答
    assert not agent._looks_like_tool_call('{"answer": 42}')
    assert not agent._looks_like_tool_call("名字叫 name，参数是 arguments")
    assert not agent._looks_like_tool_call("")


def test_thinking_off_sends_reasoning_effort(monkeypatch) -> None:
    """关掉思考时，请求要带上 `reasoning_effort="none"`。

    小模型常常一思考就把输出预算花光、正文一个字都给不出来 —— 关掉它才正常出答案。
    """
    monkeypatch.setattr(agent, "_reasoning_cache", {})

    events, requests = _run(monkeypatch, [[_text_chunk("好")]], thinking=False)

    assert requests[0]["reasoning_effort"] == "none"
    assert _texts(events) == "好"


def test_thinking_on_keeps_request_unchanged(monkeypatch) -> None:
    """默认（思考开着）**不带**这个参数 —— 与加这个开关之前的请求一字不差。"""
    monkeypatch.setattr(agent, "_reasoning_cache", {})

    _events, requests = _run(monkeypatch, [[_text_chunk("好")]])

    assert "reasoning_effort" not in requests[0]


def test_thinking_off_falls_back_when_service_rejects_it(monkeypatch) -> None:
    """服务不认 `reasoning_effort` 时退回普通请求：思考关不掉，但对话不能挂。"""
    requests: list[dict] = []
    monkeypatch.setattr(agent, "_stream_usage_cache", {})
    monkeypatch.setattr(agent, "_reasoning_cache", {})
    monkeypatch.setattr(
        agent,
        "OpenAI",
        lambda **_kw: _RejectReasoningClient([[_text_chunk("好")]], requests),
    )
    monkeypatch.setattr(agent.registry, "schemas", lambda *a, **k: [{"type": "function"}])

    events = list(
        agent.run_agent_stream(
            prompt="继续",
            files=[],
            mode=None,
            choice=_make_choice(),
            history=[],
            thinking=False,
        )
    )

    # 照样答出来了
    assert _texts(events) == "好"
    # 最后一次请求不带它；并且这个地址被记下来，之后不再白试
    assert "reasoning_effort" not in requests[-1]
    assert agent._reasoning_cache == {"": False}


def test_cached_tokens_are_read_from_usage() -> None:
    """缓存命中数要从用量里抠出来，两种字段名都要认。

    各家不统一：OpenAI / Ollama 放在 `prompt_tokens_details.cached_tokens`，
    DeepSeek 另有一个 `prompt_cache_hit_tokens`。界面上的缓存命中率就靠它。
    """
    stats = agent.RunStats()

    # OpenAI / Ollama 的形状
    stats.add_usage(
        SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=10,
            total_tokens=110,
            prompt_tokens_details=SimpleNamespace(cached_tokens=80),
        )
    )
    assert stats.context_tokens == 100
    assert stats.cached_tokens == 80

    # DeepSeek 的原生字段（没有 details 那一层）
    stats.add_usage(
        SimpleNamespace(
            prompt_tokens=200,
            completion_tokens=10,
            total_tokens=210,
            prompt_cache_hit_tokens=150,
        )
    )
    assert stats.context_tokens == 200
    assert stats.cached_tokens == 150


def test_cached_tokens_reset_when_service_does_not_report() -> None:
    """这次没报缓存字段就要归零，不能沿用上一次的值。

    它和 `context_tokens` 一样是「最后一次请求」的口径 —— 分母换了、分子还留着
    上次的数，算出来的命中率就是个假的。
    """
    stats = agent.RunStats()

    stats.add_usage(
        SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=1,
            total_tokens=101,
            prompt_tokens_details=SimpleNamespace(cached_tokens=90),
        )
    )
    assert stats.cached_tokens == 90

    stats.add_usage(
        SimpleNamespace(prompt_tokens=120, completion_tokens=1, total_tokens=121)
    )
    assert stats.context_tokens == 120
    assert stats.cached_tokens == 0


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


def test_final_round_tells_the_model_the_tools_are_gone(monkeypatch) -> None:
    """收尾轮要明确告诉模型「工具没了」，而不是只把 tools 字段撤掉。

    真事（2026-09-21 的 PPT 任务）：deepseek-flash 前 10 轮 18 个调用全部规范，
    第 11 轮该收尾时却把调用写进了正文。它从上下文里看得出「工具撤走了」吗？
    看不出来 —— 历史里全是 tool_calls，system 提示词也还列着工具。于是它继续
    按调用格式输出，而这一轮没有通道，就漏进了正文。

    撤字段是「不说」，补这句话才是「说」。模型得先知道，才谈得上收尾。
    """
    responses = [
        [_tool_chunk(f"call_{index}", "no_such_tool")] for index in range(agent.MAX_ITERATIONS)
    ]
    responses.append([_text_chunk("根据已有信息，答案是 42。")])

    events, requests = _run(monkeypatch, responses)

    assert "答案是 42" in _texts(events)
    # 收尾请求的最后一条消息就是这句说明
    final_messages = requests[-1]["messages"]
    assert final_messages[-1]["role"] == "user"
    assert "不再提供任何工具" in final_messages[-1]["content"]
    # 前面几轮不该带它：那时候工具还在，说了只会让模型莫名其妙
    for request in requests[:-1]:
        assert all(
            "不再提供任何工具" not in str(message.get("content"))
            for message in request["messages"]
        )


def test_leak_on_the_final_round_blames_the_cap_not_the_model(monkeypatch) -> None:
    """收尾轮仍漏出调用时，提示要指向「轮次上限」，不能甩锅给模型。

    同一次事故的另一种收场：模型没理会「不要再请求工具」，把调用块写进了正文。
    这时若提示说「换个函数调用更稳定的模型」，用户就会去换 —— 而换了照样漏，
    因为根子是我们没告诉它工具已经撤了（见上一个测试）。
    """
    responses = [
        [_tool_chunk(f"call_{index}", "no_such_tool")] for index in range(agent.MAX_ITERATIONS)
    ]
    responses.append([_text_chunk('我先看看。\n<tool_call>{"name": "read_file"}</tool_call>')])

    events, _ = _run(monkeypatch, responses)

    notices = _notices(events)
    assert any("上限" in text for text in notices)
    assert not any("换一个函数调用更稳定的模型" in text for text in notices)


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


def test_context_tokens_keeps_only_the_last_request(monkeypatch) -> None:
    """上下文大小取最后一次请求，**不**跟着累加。

    两次请求把同一份上下文各发了一遍：账单是 10 + 20，但窗口只占了 20。
    仪表盘要的是后者 —— 混用的话，读一次代码（十来轮工具）读数就会虚高十倍以上。
    """
    responses = [
        [_tool_chunk("call_1", "no_such_tool"), _usage_chunk(10, 5)],
        [_text_chunk("好了"), _usage_chunk(20, 8)],
    ]
    stats = agent.RunStats()

    _run(monkeypatch, responses, stats=stats)

    assert stats.prompt_tokens == 30
    assert stats.context_tokens == 20


def test_context_tokens_survives_a_usage_less_request(monkeypatch) -> None:
    """后面那次请求没带用量时，别把已有的上下文读数抹成 0。

    部分网关最后一分片不带 usage。抹掉的话仪表盘会从「上次的读数」直接掉到 0，
    看着像上下文被清空了。
    """
    responses = [
        [_tool_chunk("call_1", "no_such_tool"), _usage_chunk(10, 5)],
        [_text_chunk("好了")],
    ]
    stats = agent.RunStats()

    _run(monkeypatch, responses, stats=stats)

    assert stats.context_tokens == 10


def test_usage_is_broadcast_after_every_request(monkeypatch) -> None:
    """每拿到一次用量就播报一次，界面靠它把上下文读数实时刷上去。

    只在结束时随 stats 给一次的话，整个跑的过程中界面都停在上一轮的旧读数上，
    跑完才跳一下 —— 而这一轮里上下文其实已经长了好几轮。
    """
    responses = [
        [_tool_chunk("call_1", "no_such_tool"), _usage_chunk(10, 5)],
        [_text_chunk("好了"), _usage_chunk(20, 8)],
    ]

    events, _ = _run(monkeypatch, responses)

    broadcasts = [item.context_tokens for item in events if isinstance(item, agent.Usage)]
    assert broadcasts == [10, 20]


def test_no_broadcast_when_the_service_sends_no_usage(monkeypatch) -> None:
    """服务端不发用量时一个都不播报 —— 别把界面上的读数凭空抹成 0。"""
    events, _ = _run(monkeypatch, [[_text_chunk("好了")]])

    assert [item for item in events if isinstance(item, agent.Usage)] == []


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
    """没有通道时循环不该被「取消」影响 —— 单元测试走的就是这条路。"""
    events, requests = _run(monkeypatch, [[_text_chunk("答完了")]])

    assert len(requests) == 1
    assert _texts(events) == "答完了"
    assert not any("取消" in text for text in _notices(events))


# ---------------------------------------------------------------------------
# 单轮开销上限
#
# MAX_ITERATIONS 管的是「次数」，而两次请求的开销可以差两个数量级 ——
# 一轮长任务烧掉多少钱，只有 token 这一条线拦得住。
# ---------------------------------------------------------------------------


def test_budget_warning_tells_the_model_to_wrap_up(monkeypatch) -> None:
    """用到预算的八成时就提醒模型收尾，而且**只提醒一次**。

    硬停在预算上限上很亏：那一刻工具刚跑完、结果也回来了，却没有下一次请求去
    消化它。提前提醒，模型就能自己把剩下的活收拢到预算之内 —— Claude Code 的
    `TOKEN_BUDGET` 就是这个思路（「接近预算时提示模型继续或结束」）。

    反复提醒则有害无益：它常驻上下文，每轮都占地方，还会让模型一直念叨收尾。
    """
    monkeypatch.setattr(agent, "run_token_limit", lambda: 1000)

    events, requests = _run(
        monkeypatch,
        [
            # 第一轮就烧到 850（越过 1000 的八成），下一次请求前该收到提醒
            [_tool_chunk("call_1", "list_dir"), _usage_chunk(700, 150)],
            [_tool_chunk("call_2", "list_dir"), _usage_chunk(100, 50)],
            [_text_chunk("做完了")],
        ],
    )

    # 提醒出现在「第二轮请求」：预算是在第一轮流末尾才涨上去的
    assert any(
        "预算快用完" in str(message.get("content"))
        for message in requests[0]["messages"]
    ) is False
    assert any(
        "预算快用完" in str(message.get("content"))
        for message in requests[1]["messages"]
    )
    # 到第三轮它还在上下文里（这是对的），但只该有一条
    assert (
        sum("预算快用完" in str(message.get("content")) for message in requests[2]["messages"])
        == 1
    )
    # 界面也要知道发生了什么
    assert any("已提醒模型收尾" in text for text in _notices(events))


def test_no_budget_warning_while_there_is_room(monkeypatch) -> None:
    """预算还宽裕时不打扰模型 —— 那句提醒本身也占上下文。"""
    monkeypatch.setattr(agent, "run_token_limit", lambda: 10_000)

    events, requests = _run(
        monkeypatch,
        [
            [_tool_chunk("call_1", "list_dir"), _usage_chunk(400, 100)],
            [_text_chunk("做完了")],
        ],
    )

    assert all(
        "预算快用完" not in str(message.get("content"))
        for request in requests
        for message in request["messages"]
    )
    assert not any("已提醒模型收尾" in text for text in _notices(events))


def test_run_stops_when_the_token_limit_is_exceeded(monkeypatch) -> None:
    """超过开销上限就当场停：不执行工具，也不再请求模型。"""
    monkeypatch.setattr(agent, "run_token_limit", lambda: 100)

    events, requests = _run(
        monkeypatch,
        # 第一轮就要执行工具，同时报出 500 token 的用量（远超上限 100）
        [[_tool_chunk("call_1", "list_dir"), _usage_chunk(400, 100)]],
    )

    assert _steps(events) == []  # 工具一步都没执行
    assert len(requests) == 1  # 也没再请求模型
    assert any("超过上限" in text for text in _notices(events))


def test_run_continues_when_the_limit_is_not_reached(monkeypatch) -> None:
    """没超上限就照常走：执行工具、继续下一轮。"""
    monkeypatch.setattr(agent, "run_token_limit", lambda: 10_000)

    events, requests = _run(
        monkeypatch,
        [
            [_tool_chunk("call_1", "list_dir"), _usage_chunk(400, 100)],
            [_text_chunk("做完了")],
        ],
    )

    assert len(_steps(events)) == 1
    assert len(requests) == 2


def test_no_limit_configured_changes_nothing(monkeypatch) -> None:
    """上限为 0（没配）时不干预 —— 行为完全退回改动之前。"""
    monkeypatch.setattr(agent, "run_token_limit", lambda: 0)

    events, requests = _run(
        monkeypatch,
        [
            [_tool_chunk("call_1", "list_dir"), _usage_chunk(999_999, 999_999)],
            [_text_chunk("做完了")],
        ],
    )

    assert len(_steps(events)) == 1
    assert len(requests) == 2


def test_run_token_limit_reads_the_preference(monkeypatch, tmp_path: Path) -> None:
    """上限从偏好文件读；没配 / 填坏了 / 填负数都当「不限制」。

    坏值不能让对话直接跑不起来 —— 它是个安全阀，不是必配项。
    """
    path = tmp_path / "preferences.json"
    monkeypatch.setattr(agent, "get_settings", lambda: SimpleNamespace(preferences_path=path))

    assert agent.run_token_limit() == 0  # 文件还不存在

    store = PreferenceStore(path)
    store.set(MAX_RUN_TOKENS_KEY, "5000")
    assert agent.run_token_limit() == 5000

    store.set(MAX_RUN_TOKENS_KEY, "")
    assert agent.run_token_limit() == 0

    store.set(MAX_RUN_TOKENS_KEY, "abc")
    assert agent.run_token_limit() == 0

    store.set(MAX_RUN_TOKENS_KEY, "-10")
    assert agent.run_token_limit() == 0


def test_run_max_iterations_reads_the_preference(monkeypatch, tmp_path: Path) -> None:
    """轮次上限从偏好文件读；没配 / 填坏了 / 填非正数都退回默认值。

    退回的是**默认值**而不是 0（这点和开销上限正相反）：开销上限「不限制」
    只是不拦，是安全的；轮次上限「不限制」等于把防死循环那道兜底拆了。
    """
    path = tmp_path / "preferences.json"
    monkeypatch.setattr(agent, "get_settings", lambda: SimpleNamespace(preferences_path=path))

    assert agent.run_max_iterations() == agent.MAX_ITERATIONS  # 文件还不存在

    store = PreferenceStore(path)
    store.set(MAX_ITERATIONS_KEY, "50")
    assert agent.run_max_iterations() == 50

    for bad in ("", "abc", "0", "-5"):
        store.set(MAX_ITERATIONS_KEY, bad)
        assert agent.run_max_iterations() == agent.MAX_ITERATIONS


def test_max_iterations_can_be_raised_from_preferences(monkeypatch) -> None:
    """上限改了之后循环按新值停，提示语也要报那个值。

    提示语尤其不能写常量：那会说成「已达到 30 轮上限」，而实际只跑了 3 轮 ——
    用户照着这个数去查，只能查出一头雾水。
    """
    events, requests = _run(
        monkeypatch,
        [[_tool_chunk(f"call_{index}", "no_such_tool")] for index in range(4)],
        max_iterations=3,
    )

    assert len(_steps(events)) == 3
    assert len(requests) == 4  # 3 轮工具 + 1 次收尾
    assert any("已达到 3 轮" in text for text in _notices(events))
