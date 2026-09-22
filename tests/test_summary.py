"""上下文压缩：把超出预算的早期历史压成摘要。

三条主线：
    1. 摘要怎么生成（提示词里要带什么、失败怎么办）
    2. 压哪一段（和截断用同一把尺子：留不住的那部分）
    3. 压完之后历史长什么样（摘要记录替代它之前的全部记录）
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

from quill_agent import agent
from quill_agent.agent import (
    SUMMARY_KEEP_RECENT,
    SUMMARY_MAX_CHARS,
    build_history_messages,
    summarize_history,
)


class FakeClient:
    """只实现「回一段固定文本」的最小客户端。

    顺带记下收到的提示词 —— 好几条用例要断言「提示里带了什么」（上一版摘要、记录正文），
    而那正是 `summarize_history` 对外的全部契约。
    """

    def __init__(
        self,
        reply: str | None = "【摘要】用户要求把认证改成 JWT。",
        error: Exception | None = None,
        usage: Any = None,
    ) -> None:
        self.reply = reply
        self.error = error
        self.usage = usage
        self.prompts: list[str] = []
        # 形状照着 openai SDK 的调用链摆：client.chat.completions.create(...)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.prompts.append(kwargs["messages"][0]["content"])
        if self.error is not None:
            raise self.error

        message = SimpleNamespace(content=self.reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=self.usage)


def _drain(gen: Iterator[Any]) -> tuple[list[Any], Any]:
    """跑完一个「yield 事件 + return 值」的生成器，返回（事件列表, 返回值）。"""
    events: list[Any] = []

    while True:
        try:
            events.append(next(gen))
        except StopIteration as stop:
            return events, stop.value


def _records(count: int, chars: int = 100) -> list[dict]:
    """造 count 条记录，每条正文 chars 个字（默认 100 字 ≈ 50 token）。"""
    return [{"role": "user", "content": "字" * chars} for _ in range(count)]


# ---------------------------------------------------------------------------
# 生成摘要
# ---------------------------------------------------------------------------


def test_summarize_returns_the_model_text() -> None:
    client = FakeClient("【摘要】要点一。")

    out = summarize_history(client=client, model="m", records=[{"role": "user", "content": "你好"}])

    assert out == "【摘要】要点一。"


def test_prompt_carries_the_previous_summary_and_the_records() -> None:
    """滚动摘要的关键：上一版摘要要一起发过去，新摘要才能把它并进去。"""
    client = FakeClient()

    summarize_history(
        client=client,
        model="m",
        records=[{"role": "user", "content": "把认证改成 JWT"}],
        previous="上一版：认证用的是 session",
    )

    prompt = client.prompts[0]
    assert "上一版：认证用的是 session" in prompt
    assert "把认证改成 JWT" in prompt


def test_prompt_carries_tool_results() -> None:
    """工具返回也要进摘要输入 —— 「报错原文」往往只在那里，正文一个字都没提。"""
    client = FakeClient()

    summarize_history(
        client=client,
        model="m",
        records=[
            {
                "role": "assistant",
                "content": "跑一下测试",
                "steps": [
                    {
                        "name": "run_command",
                        "arguments": "{}",
                        "result": "TypeError: 'NoneType' object is not subscriptable",
                    }
                ],
            }
        ],
    )

    assert "TypeError" in client.prompts[0]


def test_summarize_swallows_errors() -> None:
    """调模型失败不能抛出去：压缩是锦上添花，失败该降级成「这次不压」。"""
    client = FakeClient(error=RuntimeError("gateway exploded"))

    out = summarize_history(client=client, model="m", records=[{"role": "user", "content": "x"}])

    assert out is None


def test_summarize_treats_an_empty_reply_as_failure() -> None:
    out = summarize_history(
        client=FakeClient("   "), model="m", records=[{"role": "user", "content": "x"}]
    )

    assert out is None


def test_summarize_clips_an_overlong_reply() -> None:
    """摘要会长期占着上下文，模型不守字数时由我们兜住。"""
    out = summarize_history(
        client=FakeClient("字" * 9999), model="m", records=[{"role": "user", "content": "x"}]
    )

    assert out is not None
    assert len(out) <= SUMMARY_MAX_CHARS + 40  # 加上末尾那句「已截断」的说明


# ---------------------------------------------------------------------------
# 压哪一段
# ---------------------------------------------------------------------------


def test_no_compaction_when_the_window_is_unknown() -> None:
    """窗口没配（预算 0）时不压缩：说不清能省多少，压完可能反而更大。"""
    compress, keep = agent._split_for_summary(_records(50), budget=0)

    assert compress == []
    assert len(keep) == 50


def test_no_compaction_when_history_already_fits() -> None:
    """预算装得下就不压 —— 白花一次模型调用。"""
    compress, _ = agent._split_for_summary(_records(10), budget=100_000)

    assert compress == []


def test_compacts_exactly_what_does_not_fit() -> None:
    """压的是「留不住的那部分」：最近若干条留原文，其余压成摘要。"""
    records = _records(40)  # 每条 ≈ 50 token
    compress, keep = agent._split_for_summary(records, budget=500)  # 只装得下 10 条

    assert len(keep) == SUMMARY_KEEP_RECENT
    assert len(compress) == 40 - SUMMARY_KEEP_RECENT
    # 末尾那条留原文，紧接着它往前就是被压的
    assert keep[-1] is records[-1]
    assert compress[-1] is records[-SUMMARY_KEEP_RECENT - 1]


def test_too_few_records_is_not_worth_compacting() -> None:
    """只超出一两条时直接丢更省事：摘要本身也占上下文，还要花一次调用。"""
    records = _records(SUMMARY_KEEP_RECENT + 1)
    compress, _ = agent._split_for_summary(records, budget=1)

    assert compress == []


def test_already_covered_records_are_not_compacted_again() -> None:
    """滚动摘要：上次摘要覆盖过的部分不再压第二遍（否则每轮都在重压老内容）。"""
    records = [
        {"role": "summary", "content": "早期的摘要", "covers": 20},
        *_records(30),
    ]

    compress, _ = agent._split_for_summary(records, budget=1)

    assert all(item.get("role") != "summary" for item in compress)  # 摘要记录本身不重压
    assert len(compress) == 30 - SUMMARY_KEEP_RECENT


# ---------------------------------------------------------------------------
# 事件与结果
# ---------------------------------------------------------------------------


def test_compaction_returns_a_summary_record_plus_the_recent_ones() -> None:
    client = FakeClient("【摘要】X")
    history = _records(40)

    events, result = _drain(
        agent._compact_if_needed(history=history, budget=500, client=client, model="m")
    )

    assert isinstance(events[-1], agent.SummaryMade)
    assert isinstance(events[0], agent.Notice)  # 先说一句「正在压缩」，别让界面干等

    assert result[0]["role"] == "summary"
    assert result[0]["content"] == "【摘要】X"
    assert result[0]["covers"] == 40 - SUMMARY_KEEP_RECENT
    assert len(result) == 1 + SUMMARY_KEEP_RECENT


def test_compaction_usage_counts_toward_the_shared_budget() -> None:
    """历史压缩也是模型调用，必须进入账本和父子 Agent 共用的预算。"""
    usage = SimpleNamespace(prompt_tokens=80, completion_tokens=20, total_tokens=100)
    client = FakeClient("摘要", usage=usage)
    history = _records(40)
    stats = agent.RunStats()
    budget = agent.TokenBudget(limit=90)

    _drain(
        agent._compact_if_needed(
            history=history,
            budget=500,
            client=client,
            model="m",
            tracker=stats,
            token_budget=budget,
        )
    )

    assert stats.total_tokens == 100
    assert budget.used() == 100
    assert budget.exceeded()


def test_compaction_failure_returns_the_history_untouched() -> None:
    """摘要生成失败 → 原样返回，让下游按预算截断。绝不能因此跑不完这一轮。"""
    client = FakeClient(error=RuntimeError("boom"))
    history = _records(40)

    events, result = _drain(
        agent._compact_if_needed(history=history, budget=500, client=client, model="m")
    )

    assert result == history
    assert any(isinstance(item, agent.Notice) for item in events)
    assert not any(isinstance(item, agent.SummaryMade) for item in events)


def test_nothing_happens_when_compaction_is_not_needed() -> None:
    """不需要压时一条事件都不发 —— 界面不该看到莫名其妙的通知。"""
    events, result = _drain(
        agent._compact_if_needed(
            history=_records(5), budget=100_000, client=FakeClient(), model="m"
        )
    )

    assert events == []
    assert len(result) == 5


# ---------------------------------------------------------------------------
# 摘要参与组装
# ---------------------------------------------------------------------------


def test_last_summary_wins() -> None:
    """滚动摘要下只有最新那条算数（它的正文里已经带着旧摘要的内容）。"""
    records = [
        {"role": "summary", "content": "旧", "covers": 5},
        {"role": "user", "content": "中间的事"},
        {"role": "summary", "content": "新", "covers": 9},
    ]

    covered, text = agent._last_summary(records)

    assert covered == 3  # 1-based，含它自己
    assert text == "新"


def test_summary_replaces_everything_before_it() -> None:
    """摘要之前的所有记录都不再单独发送；摘要本身作为一条 system 消息。"""
    history = [
        {"role": "user", "content": "很久以前说过的话"},
        {"role": "assistant", "content": "嗯"},
        {"role": "summary", "content": "【摘要】之前讨论的是 X", "covers": 2},
        {"role": "user", "content": "后来的事"},
    ]

    messages = build_history_messages(history, context_window=100_000)

    assert [item["role"] for item in messages] == ["system", "user"]
    assert "之前讨论的是 X" in messages[0]["content"]
    assert messages[1]["content"] == "后来的事"
    assert all("很久以前说过的话" not in str(item.get("content")) for item in messages)


def test_summary_message_says_the_original_is_still_there() -> None:
    """那条 system 消息要讲清「原文还在」—— 否则模型可能不敢确信摘要。"""
    history = [
        {"role": "summary", "content": "【摘要】X", "covers": 3},
        {"role": "user", "content": "继续"},
    ]

    messages = build_history_messages(history, context_window=100_000)

    assert "原文仍在会话记录里" in messages[0]["content"]
