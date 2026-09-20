"""`recall_history` 工具：在更早的对话里按关键词取回原文。

为什么需要它：历史会因为上下文预算被截断（见 `agent.build_history_messages`），
截断之后模型只知道自己「看不到了」。这个工具让它能把细节捞回来 —— 全量原文一直在
会话文件里，截断只影响「发给模型多少」，不影响「能取回多少」。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from quill_agent import agent
from quill_agent.tools import builtin


@contextmanager
def running_with(history: list[dict]) -> Iterator[None]:
    """把当前线程挂上一个带指定历史的运行环境。

    工具函数拿不到「当前这一轮」，只能从 ContextVar 里取 —— 所以测试也得照
    `run_agent_stream` 的做法把它挂上（同 tests/test_subagent.py）。
    """
    environment = agent.RunEnvironment(
        context=agent.ModeContext(),
        choice=None,
        stats=agent.RunStats(),
        todos=agent.TodoBoard(),
        history=history,
    )
    token = agent._environment.set(environment)
    try:
        yield
    finally:
        agent._environment.reset(token)


def test_without_a_run_returns_a_clear_message() -> None:
    """不在运行里（被直接调用）时给一句明确的话，而不是空字符串。"""
    out = builtin.recall_history("任意")

    assert "没有可检索的历史" in out


def test_finds_the_record_containing_the_keyword() -> None:
    """命中记录要带上定位信息和片段，没命中的不该混进来。"""
    history = [
        {"role": "user", "content": "先看看认证怎么做的"},
        {"role": "assistant", "content": "认证中间件在 src/auth.py:42"},
        {"role": "user", "content": "换个话题"},
    ]

    with running_with(history):
        out = builtin.recall_history("认证")

    assert "src/auth.py:42" in out
    assert "第 2 条" in out  # 编号让模型知道那是多早的事
    assert "助手" in out
    assert "换个话题" not in out


def test_multiple_keywords_must_all_match() -> None:
    """多个关键词是「全部命中」而不是「命中任一」—— 否则召回会全是噪声。"""
    history = [
        {"role": "user", "content": "认证走的是 JWT"},
        {"role": "user", "content": "缓存用的是 Redis"},
    ]

    with running_with(history):
        both = builtin.recall_history("认证 Redis")
        single = builtin.recall_history("认证")

    assert "没有找到" in both
    assert "JWT" in single


def test_searches_inside_tool_results() -> None:
    """工具结果也要能搜到 —— 要找的东西常常只存在于工具的返回里。"""
    history = [
        {
            "role": "assistant",
            "content": "我读一下",
            "steps": [
                {"name": "read_file", "arguments": '{"path": "a.py"}', "result": "TOPSECRET=1"}
            ],
        }
    ]

    with running_with(history):
        out = builtin.recall_history("TOPSECRET")

    assert "TOPSECRET=1" in out


def test_no_hit_tells_the_model_how_to_retry() -> None:
    """查不到时要给出下一步（换词重试），否则模型只会重复同样的查询。"""
    with running_with([{"role": "user", "content": "你好"}]):
        out = builtin.recall_history("不存在的东西")

    assert "没有找到" in out
    assert "关键词" in out


def test_empty_query_is_rejected() -> None:
    with running_with([{"role": "user", "content": "你好"}]):
        out = builtin.recall_history("   ")

    assert "query 是空的" in out


def test_limit_keeps_the_most_recent_hits() -> None:
    """命中很多时取最近的几条 —— 要找的东西多半在近期。"""
    history = [{"role": "user", "content": f"第{index}次提到关键词"} for index in range(20)]

    with running_with(history):
        out = builtin.recall_history("关键词", limit=3)

    assert "找到 20 处" in out  # 命中总数要说清楚，模型才知道还有更多
    assert "第 20 条" in out
    assert "第 18 条" in out
    assert "第 17 条" not in out


def test_limit_is_capped() -> None:
    """limit 给得再大也不会一次拉回太多 —— 那等于把截断省下的预算又花回去。"""
    history = [{"role": "user", "content": f"第{index}条 关键词"} for index in range(30)]

    with running_with(history):
        out = builtin.recall_history("关键词", limit=999)

    assert f"以下是最近的 {builtin.RECALL_MAX_LIMIT} 处" in out


def test_snippet_shows_the_context_around_the_keyword() -> None:
    """片段要围着关键词截，而不是从开头截。

    否则「关键结论在正文中间」这种情况，取回来的全是开头的铺垫，
    真正要找的那一句刚好被切掉。
    """
    history = [{"role": "user", "content": f"{'铺垫' * 400}关键结论在这里"}]

    with running_with(history):
        out = builtin.recall_history("关键结论")

    assert "关键结论在这里" in out
    assert out.count("铺垫") < 200  # 前面那 400 个铺垫只跟过来一小段


def test_total_length_is_capped() -> None:
    """一次召回的总长度有上限，不然它会把刚省下来的预算原样花回去。"""
    history = [{"role": "user", "content": "关键词" + "字" * 2000} for _ in range(10)]

    with running_with(history):
        out = builtin.recall_history("关键词", limit=10)

    # 上限 + 末尾那句「已截断」的说明
    assert len(out) <= builtin.RECALL_MAX_CHARS + 30
