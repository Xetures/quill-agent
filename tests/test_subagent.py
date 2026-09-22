"""子代理：自己跑一轮、把结论带回来，过程不占父级上下文。

用假的 `agent.run_agent_stream` 替换掉真正的模型调用 —— 这里要验的是「派出去的那一轮
被怎么装配」，不是模型怎么回答。
"""

from __future__ import annotations

import asyncio
import threading
import time

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
            "spawn_agents",
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


@pytest.fixture
def channel():
    """一条真实的交互通道 + 它的事件循环。

    通道本来就是为**跨线程投递**而设计的（`_pump` 就跑在另一条线程里），所以这里也让
    它跑在后台循环上 —— 拿个假的替代品反而验不到「worker 线程往队列里塞」那条路。
    """
    event_loop = asyncio.new_event_loop()
    thread = threading.Thread(target=event_loop.run_forever, daemon=True)
    thread.start()
    try:
        yield interaction.Interaction(event_loop, run_id="r"), event_loop
    finally:
        event_loop.call_soon_threadsafe(event_loop.stop)
        thread.join(timeout=2)
        event_loop.close()


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

    subagent.spawn_agents(["查一件事"])

    nested = fake.calls[0]["context"]
    assert "read_file" in nested.tools
    # 并行版同样要排掉。深度护栏拦得住调用，但留着它白占 schema，
    # 还可能让子代理白试一轮才发现调不通
    assert "spawn_agents" not in nested.tools
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

    subagent.spawn_agents(["查一件事"])

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

    subagent.spawn_agents(["查一件事"])

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

    subagent.spawn_agents(["查一件事"])

    prompt = fake.calls[0]["prompt"]
    assert "子代理" in prompt
    assert prompt.endswith("任务：查一件事")


# ---------------------------------------------------------------------------
# 结论与账
# ---------------------------------------------------------------------------


def test_result_carries_the_conclusion(monkeypatch: pytest.MonkeyPatch, in_run) -> None:
    """文本增量是按片段到的，要拼起来再交回去。"""
    monkeypatch.setattr(agent, "run_agent_stream", FakeRun(["它是", "这么回事。"]))

    result = subagent.spawn_agents(["查一件事"])

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

    assert "没有给出结论" in subagent.spawn_agents(["查一件事"])


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

    subagent.spawn_agents(["查一件事"])

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


def _capture_publishes(seen: list[dict]):
    """替掉 `_publish`，把播出去的**载荷**收进 `seen`。

    刻意丢掉第一个参数（子代理标签）：它是并行时才引入的，而这些用例关心的是
    「播了什么」，单个子代理的身份在这里没有意义。
    """

    def record(_label: str, payload: dict) -> None:
        seen.append(payload)

    return record


def test_only_actions_are_broadcast_and_the_text_is_not(
    monkeypatch: pytest.MonkeyPatch, in_run
) -> None:
    """**文本增量不往外播，动作要播。**

    子代理输出上千字的话那是几百个 SSE 事件，而外面真正想知道的其实是「它在做什么」。
    成品（结论）最后随工具结果一起到，不差这一点时间。
    """
    seen: list[dict] = []
    monkeypatch.setattr(subagent, "_publish", _capture_publishes(seen))
    monkeypatch.setattr(
        agent,
        "run_agent_stream",
        FakeRun(
            [
                "子代理说了一",
                "大段话",
                agent.ToolStart(name="read_file", arguments="{}"),
                agent.Notice("提示"),
            ]
        ),
    )

    subagent.spawn_agents(["查一件事"])

    assert [item["type"] for item in seen] == ["tool", "notice"]


def test_the_start_of_a_tool_is_what_gets_broadcast(
    monkeypatch: pytest.MonkeyPatch, in_run
) -> None:
    """播的是 ToolStart（执行**前**），不是 ToolStep（执行**后**）。

    两者字段一样，但该被看见的是工具**跑着**的那段时间 —— 耗时全在前面，播一句
    「跑完了」对「是不是卡住了」这个疑问没有帮助。父级界面上也是这个道理
    （见 `agent.ToolStart` 的说明）。
    """
    seen: list[dict] = []
    monkeypatch.setattr(subagent, "_publish", _capture_publishes(seen))
    monkeypatch.setattr(
        agent,
        "run_agent_stream",
        FakeRun([agent.ToolStart(name="run_command", arguments='{"command": "sleep 60"}')]),
    )

    subagent.spawn_agents(["查一件事"])

    assert seen == [
        {"type": "tool", "name": "run_command", "arguments": '{"command": "sleep 60"}'}
    ]


def test_a_quiet_stretch_is_broken_up_by_a_heartbeat(
    monkeypatch: pytest.MonkeyPatch, in_run
) -> None:
    """长时间只有正文 / 思维链时，替子代理播一句「还在工作」。

    真事（2026-09-21）：一次 `spawn_agents` 跑了整 360 秒，期间一个事件都没往外播，
    正好撞上前端那条 360 秒的静默保护 —— 整条流被当成死连接掐掉，这一轮白跑。
    那道保护的用意没错（防「对端睡了」，见 `web/src/api/chat.ts` 的 `IDLE_TIMEOUT_MS`），
    错的是这里给它的信息太少。

    阈值压到 0 来验「每个事件都补一句」；真实值是 30 秒（见 `_HEARTBEAT_SECONDS`）。
    """
    seen: list[dict] = []
    monkeypatch.setattr(subagent, "_publish", _capture_publishes(seen))
    monkeypatch.setattr(subagent, "_HEARTBEAT_SECONDS", 0.0)
    monkeypatch.setattr(
        agent, "run_agent_stream", FakeRun(["很长的", "一段正文", "分好几片"])
    )

    result = subagent.spawn_agents(["查一件事"])

    assert [item["type"] for item in seen] == ["notice", "notice", "notice"]
    assert all("正在工作" in item["text"] for item in seen)
    # 心跳归心跳，正文照旧收进结论里
    assert "很长的" in result and "分好几片" in result


def test_publish_is_a_no_op_without_a_channel() -> None:
    """没有通道时静默丢弃：子代理照样跑，只是外面看不到过程。"""
    subagent._publish("标签", {"type": "tool", "name": "read_file", "arguments": "{}"})


# ---------------------------------------------------------------------------
# 拒绝的几条路
# ---------------------------------------------------------------------------


def test_a_subagent_cannot_spawn_another(monkeypatch: pytest.MonkeyPatch, in_run) -> None:
    """递归派下去成本是乘的、排查是难的，收益却接近零。"""
    fake = FakeRun(["不该跑到这里"])
    monkeypatch.setattr(agent, "run_agent_stream", fake)

    token = subagent._depth.set(1)
    try:
        result = subagent.spawn_agents(["再派一个"])
    finally:
        subagent._depth.reset(token)

    assert "不能再派子代理" in result
    assert fake.calls == []


def test_an_empty_task_is_refused() -> None:
    assert "空的" in subagent.spawn_agents(["   "])


def test_outside_a_run_it_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """不在一次运行里时返回一句说明，**不能返回空** ——

    空的返回值会被调用方当成「子代理没说话」交给模型，比明说「派不了」更难排查。
    """
    monkeypatch.setattr(agent, "run_agent_stream", FakeRun([]))

    assert agent.current_environment() is None
    assert "不在一次 Agent 运行里" in subagent.spawn_agents(["查一件事"])


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
            subagent._publish("标签", {"type": "tool", "name": "read_file", "arguments": "{}"})
        finally:
            interaction.deactivate(token)

        assert _next(event_loop, channel) == (
            "subagent",
            # `agent` 是并行引入的：几个子代理同时播事件，看板要靠它分清谁在动
            {"agent": "标签", "type": "tool", "name": "read_file", "arguments": "{}"},
        )
    finally:
        event_loop.call_soon_threadsafe(event_loop.stop)
        thread.join(timeout=2)
        event_loop.close()


# ---------------------------------------------------------------------------
# 并行派多个
# ---------------------------------------------------------------------------


class SlowRun:
    """每次调用停一会儿，并吐一条**带任务名**的结论。

    停这一下是专门给「测并行」用的：串行地调三次同样会让三次都被调到，所以
    「调用了几次」说明不了任何事 —— 只有总耗时能。
    """

    def __init__(self, delay: float = 0.0, tokens: int = 0) -> None:
        self.delay = delay
        self.tokens = tokens
        self.prompts: list[str] = []
        # 工作线程会并发进来，列表的写入要自己保护（`append` 本身是原子的，
        # 但这里以后可能不止这一句）
        self._lock = threading.Lock()

    def __call__(self, **kwargs):
        prompt = kwargs["prompt"]
        with self._lock:
            self.prompts.append(prompt)
        if self.delay:
            time.sleep(self.delay)
        if self.tokens:
            kwargs["stats"].total_tokens += self.tokens
        yield f"结论-{prompt.rsplit('任务：', 1)[-1]}"


def test_tasks_really_run_in_parallel(monkeypatch: pytest.MonkeyPatch, in_run) -> None:
    """三件各 0.3 秒的活并行跑，总耗时接近 0.3 秒，而不是 0.9 秒。

    这是这个工具存在的**全部理由**。只验「三次都被调到了」是看不出并行的 ——
    串行地调三次同样做得到。
    """
    monkeypatch.setattr(agent, "run_agent_stream", SlowRun(delay=0.3))

    started = time.monotonic()
    result = subagent.spawn_agents(["甲", "乙", "丙"])
    elapsed = time.monotonic() - started

    assert elapsed < 0.6  # 串行要 0.9 秒左右
    for task in ("甲", "乙", "丙"):
        assert f"结论-{task}" in result


def test_every_worker_gets_the_run_context(monkeypatch: pytest.MonkeyPatch, in_run) -> None:
    """每个工作线程都要重新绑一遍运行上下文。

    这是并行最容易漏的地方：ContextVar **不跨线程继承**（见模块开头那张表）。
    漏了运行环境，子代理会直接回一句「不在一次运行里」—— 不是「效果差一点」。
    """
    seen: list[bool] = []

    def probing(**_: object):
        seen.append(agent.current_environment() is not None)
        yield "结论"

    monkeypatch.setattr(agent, "run_agent_stream", probing)

    subagent.spawn_agents(["甲", "乙", "丙"])

    assert seen == [True, True, True]


def test_worker_events_carry_the_subagent_label(
    monkeypatch: pytest.MonkeyPatch, in_run, channel
) -> None:
    """并行时看板上得能分清是谁在动 —— 事件要带子代理的标签。"""
    chan, loop = channel
    monkeypatch.setattr(
        agent,
        "run_agent_stream",
        FakeRun([agent.ToolStart(name="read_file", arguments="{}")]),
    )

    token = interaction.activate(chan)
    try:
        subagent.spawn_agents(["甲", "乙"])
        labels = {_next(loop, chan)[1]["agent"] for _ in range(2)}
    finally:
        interaction.deactivate(token)

    assert labels == {"甲", "乙"}


def test_children_receive_the_parents_shared_token_budget(
    monkeypatch: pytest.MonkeyPatch, in_run
) -> None:
    """所有并行子代理必须拿到同一份父级预算对象。"""
    seen: list[agent.TokenBudget] = []
    lock = threading.Lock()

    def fake(**kwargs):
        with lock:
            seen.append(kwargs["token_budget"])
        yield "结论"

    monkeypatch.setattr(agent, "run_agent_stream", fake)

    subagent.spawn_agents(["甲", "乙", "丙"])

    assert len(seen) == 3
    assert all(item is in_run.token_budget for item in seen)


def test_each_childs_usage_is_added_up(monkeypatch: pytest.MonkeyPatch, in_run) -> None:
    """每个子代理花的 token 都要记到父级账上 —— 并行时更不能漏。

    漏掉的那部分是**真花了钱**的，只是用量页上看不见。合并由父级统一做：
    各线程自己 merge 进父级是读-改-写，并发下会丢。
    """
    monkeypatch.setattr(agent, "run_agent_stream", SlowRun(tokens=100))

    subagent.spawn_agents(["甲", "乙", "丙"])

    assert in_run.stats.total_tokens == 300


def test_cancelling_parallel_work_does_not_wait_for_queued_tasks(
    monkeypatch: pytest.MonkeyPatch, in_run, channel
) -> None:
    """取消后立刻收场，线程池中排队的任务不能再启动。"""
    started = threading.Event()
    release = threading.Event()
    prompts: list[str] = []
    lock = threading.Lock()

    def blocking_run(**kwargs):
        with lock:
            prompts.append(kwargs["prompt"])
        started.set()
        release.wait(timeout=2)
        yield "结论"

    monkeypatch.setattr(agent, "run_agent_stream", blocking_run)
    monkeypatch.setattr(subagent, "MAX_PARALLEL_AGENTS", 1)
    chan, _ = channel
    token = interaction.activate(chan)
    try:
        canceller = threading.Thread(target=lambda: (started.wait(), chan.cancel()), daemon=True)
        canceller.start()
        started_at = time.monotonic()
        result = subagent.spawn_agents(["甲", "乙"])
        elapsed = time.monotonic() - started_at
        canceller.join(timeout=1)

        assert elapsed < 1
        assert len(prompts) == 1
        assert "未开始的任务已取消" in result
    finally:
        release.set()
        interaction.deactivate(token)


def test_running_child_stops_at_the_next_stream_item(
    monkeypatch: pytest.MonkeyPatch, in_run, channel
) -> None:
    """已经启动的子代理在模型流的下一个检查点看到取消，不再消费后续项。"""
    waiting = threading.Event()
    release = threading.Event()
    consumed: list[str] = []

    def streaming_run(**_):
        yield "第一段"
        waiting.set()
        release.wait(timeout=2)
        consumed.append("第二段")
        yield "第二段"
        consumed.append("第三段")
        yield "第三段"

    monkeypatch.setattr(agent, "run_agent_stream", streaming_run)
    chan, _ = channel
    token = interaction.activate(chan)
    try:
        def cancel_then_release() -> None:
            waiting.wait()
            chan.cancel()
            release.set()

        canceller = threading.Thread(target=cancel_then_release, daemon=True)
        canceller.start()
        result = subagent.spawn_agents(["甲"])
        canceller.join(timeout=1)

        assert consumed == ["第二段"]
        assert "已取消并行子代理" in result
    finally:
        release.set()
        interaction.deactivate(token)


def test_too_many_tasks_are_refused(monkeypatch: pytest.MonkeyPatch, in_run) -> None:
    """一次派太多直接拒绝，而不是照单全收：每个子代理都是一次完整的模型对话。"""
    run = SlowRun()
    monkeypatch.setattr(agent, "run_agent_stream", run)

    result = subagent.spawn_agents([f"任务{i}" for i in range(subagent.MAX_SUBAGENT_TASKS + 1)])

    assert "最多派" in result
    assert run.prompts == []  # 一个都不该跑起来


def test_extra_tasks_queue_instead_of_being_dropped(
    monkeypatch: pytest.MonkeyPatch, in_run
) -> None:
    """超过并行上限的在池子里排队 —— 排队而不是丢掉：模型可能只是没估准数量。"""
    run = SlowRun()
    monkeypatch.setattr(agent, "run_agent_stream", run)
    count = subagent.MAX_PARALLEL_AGENTS + 2

    result = subagent.spawn_agents([f"活{i}" for i in range(count)])

    assert len(run.prompts) == count
    for index in range(count):
        assert f"结论-活{index}" in result


def test_one_child_crashing_does_not_take_down_the_batch(
    monkeypatch: pytest.MonkeyPatch, in_run
) -> None:
    """一个子代理崩了不该拖垮整批：其余的把结论带回来，崩的那个如实说。"""

    def flaky(**kwargs):
        if "坏" in kwargs["prompt"]:
            raise RuntimeError("炸了")
        yield "好结论"

    monkeypatch.setattr(agent, "run_agent_stream", flaky)

    result = subagent.spawn_agents(["好的", "坏的"])

    assert "好结论" in result
    assert "出错了" in result


def test_the_depth_guard_survives_the_thread_boundary(
    monkeypatch: pytest.MonkeyPatch, in_run
) -> None:
    """「只允许一层」在并行里照样管用。

    深度同样存在 ContextVar 上，所以必须在**父线程**里取好再带进 worker ——
    worker 里读到的是默认值 0，看起来就像「父级从没派过子代理」，护栏也就失效了。
    """
    monkeypatch.setattr(agent, "run_agent_stream", FakeRun([]))

    token = subagent._depth.set(1)
    try:
        result = subagent.spawn_agents(["甲", "乙"])
    finally:
        subagent._depth.reset(token)

    assert "不能再派子代理" in result


def test_a_blank_task_list_is_refused(monkeypatch: pytest.MonkeyPatch, in_run) -> None:
    """空列表、以及只有空白的项，都要拒掉 —— 返回空会被当成「子代理没说话」。"""
    monkeypatch.setattr(agent, "run_agent_stream", FakeRun([]))

    assert "空的" in subagent.spawn_agents([])
    assert "空的" in subagent.spawn_agents(["   ", ""])
