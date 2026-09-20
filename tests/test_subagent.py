"""子代理：自己跑一轮、把结论带回来，过程不占父级上下文。

用假的 `agent.run_agent_stream` 替换掉真正的模型调用 —— 这里要验的是「派出去的那一轮
被怎么装配」，不是模型怎么回答。
"""

from __future__ import annotations

import asyncio

import pytest

from quill_agent import agent, interaction
from quill_agent.tools import subagent
from quill_agent.tools.todo import TodoBoard


class FakeRun:
    """接住 run_agent_stream 的参数，并按预设吐事件。"""

    def __init__(self, events: list) -> None:
        self.events = events
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        yield from self.events


@pytest.fixture
def in_run():
    """把自己伪装成「正在跑一轮」。

    子代理要靠它拿父级的模式、模型和账本 —— 这三样都在 `RunEnvironment` 里。
    直接用私有的 ContextVar 是刻意的：本仓库的测试本来就贴着实现写
    （见 test_interaction 里对 `_pump` / `_runs` 的用法）。
    """
    context = agent.ModeContext(
        prompts={"身份": "你是 quill"},
        tools=[
            "read_file",
            "write_file",
            "spawn_agent",
            "ask_user",
            "submit_plan",
            "todo_write",
        ],
        skills=["查代码"],
        memory_enabled=True,
        confirm=frozenset({"write_file"}),
    )
    environment = agent.RunEnvironment(
        context=context, choice=None, stats=agent.RunStats(), todos=TodoBoard()
    )
    token = agent._environment.set(environment)
    try:
        yield environment
    finally:
        agent._environment.reset(token)


# ---------------------------------------------------------------------------
# 派出去的那一轮长什么样
# ---------------------------------------------------------------------------


def test_subagent_drops_the_tools_that_talk_to_the_user(
    monkeypatch: pytest.MonkeyPatch, in_run
) -> None:
    """「和用户打交道」的工具一律不给子代理。

    它该干活并汇报，而不是替父代理和用户对话：让它去提问、去交计划，用户会看到两张
    卡片同时挂着，而父代理自己也在等 —— 两边谁都说不清在等谁。

    `todo_write` 是同一类：它唯一的产出是给用户看的进度条，而子代理那份没地方显示
    （父级只汇报它的结论），留着它只会白花 token、还可能和父级自己的计划打架。
    """
    fake = FakeRun(["结论"])
    monkeypatch.setattr(agent, "run_agent_stream", fake)

    subagent.spawn_agent("查一件事")

    nested = fake.calls[0]["context"]
    assert "read_file" in nested.tools
    assert "spawn_agent" not in nested.tools
    assert "ask_user" not in nested.tools
    assert "submit_plan" not in nested.tools
    assert "todo_write" not in nested.tools


def test_subagent_inherits_everything_else(monkeypatch: pytest.MonkeyPatch, in_run) -> None:
    """提示词、技能、记忆、要确认的工具全都照搬。

    少了这几样，子代理会比父代理更「放得开」—— 比如父代理在只读模式下
    （确认名单里挂着写工具），子代理却拿到一套没有约束的工具。
    """
    fake = FakeRun(["结论"])
    monkeypatch.setattr(agent, "run_agent_stream", fake)

    subagent.spawn_agent("查一件事")

    nested = fake.calls[0]["context"]
    assert nested.prompts == in_run.context.prompts
    assert nested.skills == in_run.context.skills
    assert nested.memory_enabled is True
    assert nested.confirm == in_run.context.confirm


def test_subagent_starts_from_an_empty_history(
    monkeypatch: pytest.MonkeyPatch, in_run
) -> None:
    """**空历史 = 干净的上下文 —— 这就是子代理存在的全部意义。**

    带上父级的历史，那份被中间过程撑大的上下文就原样传给子代理了，
    等于既没省下上下文，还多花了一次模型调用。
    """
    fake = FakeRun(["结论"])
    monkeypatch.setattr(agent, "run_agent_stream", fake)

    subagent.spawn_agent("查一件事")

    call = fake.calls[0]
    assert call["history"] == []
    assert call["files"] == []
    assert call["choice"] is in_run.choice  # 同一个模型


def test_the_task_carries_the_brief_and_the_text(
    monkeypatch: pytest.MonkeyPatch, in_run
) -> None:
    """交接说明和任务拼在一条消息里。

    说明放 user 消息而**不是**独立的 system 消息：子代理继承父级那一整套 system
    提示词（身份、能力、工具策略），再插一条「你是子代理」会和父级的身份打架。
    而这段交代本来就属于任务上下文 —— 和「运行时上下文放 user 消息」同一个口径。
    """
    fake = FakeRun(["结论"])
    monkeypatch.setattr(agent, "run_agent_stream", fake)

    subagent.spawn_agent("查一件事")

    prompt = fake.calls[0]["prompt"]
    assert "子代理" in prompt
    assert prompt.endswith("任务：查一件事")


# ---------------------------------------------------------------------------
# 结论与账
# ---------------------------------------------------------------------------


def test_result_carries_the_conclusion(monkeypatch: pytest.MonkeyPatch, in_run) -> None:
    """文本增量是按片段到的，要拼起来再交回去。"""
    monkeypatch.setattr(agent, "run_agent_stream", FakeRun(["它是", "这么回事。"]))

    result = subagent.spawn_agent("查一件事")

    assert "它是这么回事。" in result


def test_a_silent_subagent_is_reported_as_such(
    monkeypatch: pytest.MonkeyPatch, in_run
) -> None:
    """只调工具不说话的，要明说「没拿到结论」——

    否则父代理会收到一段空文本，然后自己猜发生了什么。
    """
    monkeypatch.setattr(
        agent,
        "run_agent_stream",
        FakeRun([agent.ToolStep(name="read_file", arguments="{}", result="x")]),
    )

    assert "没有给出结论" in subagent.spawn_agent("查一件事")


def test_usage_is_merged_back_into_the_parent(
    monkeypatch: pytest.MonkeyPatch, in_run
) -> None:
    """子代理花的 token 也要记账 —— 不并的话，用量页会少算一整块。"""

    def fake(**kwargs):
        kwargs["stats"].prompt_tokens += 100
        kwargs["stats"].completion_tokens += 20
        kwargs["stats"].total_tokens += 120
        yield "结论"

    monkeypatch.setattr(agent, "run_agent_stream", fake)

    subagent.spawn_agent("查一件事")

    assert in_run.stats.total_tokens == 120


def test_merge_leaves_elapsed_alone() -> None:
    """耗时**不并**：子代理的耗时已经含在父级「这一次工具调用」里了，再并就是双倍。"""
    parent = agent.RunStats(elapsed=9.0)
    child = agent.RunStats(prompt_tokens=1, completion_tokens=2, total_tokens=3, elapsed=4.0)

    parent.merge(child)

    assert parent.total_tokens == 3
    assert parent.elapsed == 9.0


def test_merge_leaves_context_tokens_alone() -> None:
    """`context_tokens` 也不并：子代理的上下文是它自己那一份，不是父级的。

    并进来只会让父级的仪表盘显示成子代理的占用。父级下一次请求会覆盖成正确的值。
    """
    parent = agent.RunStats(context_tokens=8000)
    child = agent.RunStats(prompt_tokens=1, completion_tokens=2, total_tokens=3, context_tokens=500)

    parent.merge(child)

    assert parent.total_tokens == 3
    assert parent.context_tokens == 8000


# ---------------------------------------------------------------------------
# 只播「在做什么」
# ---------------------------------------------------------------------------


def test_only_tool_calls_and_notices_are_broadcast(
    monkeypatch: pytest.MonkeyPatch, in_run
) -> None:
    """**文本增量不往外播。**

    子代理输出上千字的话那是几百个 SSE 事件，而外面真正想知道的其实是「它在做什么」。
    成品（结论）最后随工具结果一起到，不差这一点时间。
    """
    seen: list[dict] = []
    monkeypatch.setattr(subagent, "_publish", seen.append)
    monkeypatch.setattr(
        agent,
        "run_agent_stream",
        FakeRun(
            [
                "子代理说了一",
                "大段话",
                agent.ToolStep(name="read_file", arguments="{}", result="x"),
                agent.Notice("提示"),
            ]
        ),
    )

    subagent.spawn_agent("查一件事")

    assert [item["type"] for item in seen] == ["tool", "notice"]


def test_publish_is_a_no_op_without_a_channel() -> None:
    """没有通道时静默丢弃：子代理照样跑，只是外面看不到过程。"""
    subagent._publish({"type": "tool", "name": "read_file", "arguments": "{}"})


# ---------------------------------------------------------------------------
# 拒绝的几条路
# ---------------------------------------------------------------------------


def test_a_subagent_cannot_spawn_another(monkeypatch: pytest.MonkeyPatch, in_run) -> None:
    """递归派下去成本是乘的、排查是难的，收益却接近零。"""
    fake = FakeRun(["不该跑到这里"])
    monkeypatch.setattr(agent, "run_agent_stream", fake)

    token = subagent._depth.set(1)
    try:
        result = subagent.spawn_agent("再派一个")
    finally:
        subagent._depth.reset(token)

    assert "不能再派子代理" in result
    assert fake.calls == []


def test_an_empty_task_is_refused() -> None:
    assert "空的" in subagent.spawn_agent("   ")


def test_outside_a_run_it_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """不在一次运行里时返回一句说明，**不能返回空** ——

    空的返回值会被调用方当成「子代理没说话」交给模型，比明说「派不了」更难排查。
    """
    monkeypatch.setattr(agent, "run_agent_stream", FakeRun([]))

    assert agent.current_environment() is None
    assert "不在一次 Agent 运行里" in subagent.spawn_agent("查一件事")


def test_the_depth_guard_is_per_thread_not_global() -> None:
    """深度记在 ContextVar 上，不只是为了好看：两个会话同时跑时不该互相看到对方的层数。"""
    seen: list[int] = []

    token = subagent._depth.set(1)
    try:
        thread = __import__("threading").Thread(
            target=lambda: seen.append(subagent._depth.get())
        )
        thread.start()
        thread.join(timeout=5)
    finally:
        subagent._depth.reset(token)

    assert seen == [0]  # 新线程是干净的：它没有「正在当子代理」这回事
    assert subagent._depth.get() == 0


# ---------------------------------------------------------------------------
# 通道
# ---------------------------------------------------------------------------


def _next(loop, channel, timeout=5.0):
    return asyncio.run_coroutine_threadsafe(channel.queue.get(), loop).result(timeout=timeout)


def test_progress_reaches_the_channel() -> None:
    event_loop = asyncio.new_event_loop()
    import threading

    thread = threading.Thread(target=event_loop.run_forever, daemon=True)
    thread.start()

    try:
        channel = interaction.Interaction(event_loop, run_id="r")
        token = interaction.activate(channel)
        try:
            subagent._publish({"type": "tool", "name": "read_file", "arguments": "{}"})
        finally:
            interaction.deactivate(token)

        assert _next(event_loop, channel) == (
            "subagent",
            {"type": "tool", "name": "read_file", "arguments": "{}"},
        )
    finally:
        event_loop.call_soon_threadsafe(event_loop.stop)
        thread.join(timeout=2)
        event_loop.close()
