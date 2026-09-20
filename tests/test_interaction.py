"""交互通道：问题发得出去、答案收得回来、问不到时不乱来。

这个模块存在的唯一理由是「生成器被工具阻塞时，问题还能出去」—— 所以最要紧的一条
用例就是 `test_pump_...`：它真的把一个生成器卡在确认题上，然后验证问题照样到达、
答案照样回填。少了这条，「执行前确认」就是死锁。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator

import pytest
from server.routes.chat import Run, _close, _open, _pump, _runs

from quill_agent import agent, interaction
from quill_agent.tools import builtin

# ---------------------------------------------------------------------------
# 夹具：一条绑在真事件循环上的通道
# ---------------------------------------------------------------------------


@pytest.fixture
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    """在后台线程里跑一个真事件循环。

    不用假循环：通道本来就是为「跨线程投递」设计的（`publish` 走
    `call_soon_threadsafe`），用一个不跑的循环去测，测的就不是它了。
    """
    event_loop = asyncio.new_event_loop()
    thread = threading.Thread(target=event_loop.run_forever, daemon=True)
    thread.start()

    try:
        yield event_loop
    finally:
        event_loop.call_soon_threadsafe(event_loop.stop)
        thread.join(timeout=2)
        event_loop.close()


def _next(loop: asyncio.AbstractEventLoop, channel: interaction.Interaction, timeout: float = 5.0):
    """取通道里的下一个事件（测试里同步地取）。"""
    return asyncio.run_coroutine_threadsafe(channel.queue.get(), loop).result(timeout=timeout)


def _ask_in_thread(channel: interaction.Interaction, sink: list, **kwargs) -> threading.Thread:
    """在另一个线程里发起提问 —— 通道本来就是给这个场景用的。"""
    thread = threading.Thread(target=lambda: sink.append(channel.ask(**kwargs)), daemon=True)
    thread.start()
    return thread


def _in_thread(channel: interaction.Interaction, fn) -> threading.Thread:
    """在**另一个线程**里执行 fn，并在那个线程里绑定通道。

    必须在新线程里 `activate`：`threading.Thread` 拿到的是全新的上下文，
    在主线程绑的通道它看不见 —— 而 `interaction.current()` 正是靠上下文取通道的。
    生产环境里 `_pump` 做的就是这件事（见 server/routes/chat.py）。
    """

    def worker() -> None:
        token = interaction.activate(channel)
        try:
            fn()
        finally:
            interaction.deactivate(token)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# 一问一答
# ---------------------------------------------------------------------------


def test_question_carries_what_the_frontend_needs(loop: asyncio.AbstractEventLoop) -> None:
    channel = interaction.Interaction(loop, run_id="r1")
    sink: list = []
    thread = _ask_in_thread(
        channel,
        sink,
        kind="confirm",
        text="允许吗？",
        detail="$ rm -rf /",
        options=(interaction.APPROVE, interaction.DENY),
    )

    name, payload = _next(loop, channel)

    assert name == "question"
    assert payload["run_id"] == "r1"
    assert payload["kind"] == "confirm"
    assert payload["detail"] == "$ rm -rf /"
    assert payload["options"] == [interaction.APPROVE, interaction.DENY]

    assert channel.answer(payload["id"], interaction.APPROVE) is True
    thread.join(timeout=5)
    assert sink == [interaction.APPROVE]


def test_answer_with_a_stale_question_id_is_refused(loop: asyncio.AbstractEventLoop) -> None:
    """迟到的答案不能落到新问题上 —— 这正是答案要带 id 的原因。"""
    channel = interaction.Interaction(loop)
    sink: list = []
    thread = _ask_in_thread(channel, sink, kind="ask", text="叫什么？")
    _next(loop, channel)

    assert channel.answer("q999", "答错了地方") is False
    assert channel.answer("q1", "张三") is True

    thread.join(timeout=5)
    assert sink == ["张三"]


def test_a_second_answer_to_the_same_question_is_refused(loop: asyncio.AbstractEventLoop) -> None:
    """一个问题只认第一个答案 —— 否则「允许」之后紧跟一个「拒绝」会把结论改掉。"""
    channel = interaction.Interaction(loop)
    sink: list = []
    thread = _ask_in_thread(channel, sink, kind="ask", text="?")
    _, payload = _next(loop, channel)

    assert channel.answer(payload["id"], "第一次") is True
    assert channel.answer(payload["id"], "第二次") is False

    thread.join(timeout=5)
    assert sink == ["第一次"]


def test_empty_answer_is_not_the_same_as_no_answer(loop: asyncio.AbstractEventLoop) -> None:
    """空字符串是一个明确的回答；None 是「没问到」。两者必须分得开。"""
    channel = interaction.Interaction(loop)
    sink: list = []
    thread = _ask_in_thread(channel, sink, kind="ask", text="?")
    _, payload = _next(loop, channel)

    channel.answer(payload["id"], "")

    thread.join(timeout=5)
    assert sink == [""]


def test_timeout_returns_none_and_then_refuses_late_answers(
    loop: asyncio.AbstractEventLoop,
) -> None:
    """超时是这套机制里最容易出事的一条路：既不能永久卡住，也不能事后被塞个答案。"""
    channel = interaction.Interaction(loop)

    assert channel.ask(kind="ask", text="?", timeout=0.05) is None
    assert channel.waiting() is False
    assert channel.answer("q1", "来晚了") is False


# ---------------------------------------------------------------------------
# 取消
# ---------------------------------------------------------------------------


def test_cancel_wakes_a_blocked_ask(loop: asyncio.AbstractEventLoop) -> None:
    """**这条是「停止」按钮能不能用的关键。**

    用户在确认卡片上按停止时，那一轮正卡在 `wait()` 里 —— 只立一个标记是没用的，
    必须把它叫醒。叫不醒的话，停止按钮对一个正在等确认的轮次完全失效。
    """
    channel = interaction.Interaction(loop)
    sink: list = []
    thread = _ask_in_thread(channel, sink, kind="confirm", text="允许吗？")
    _next(loop, channel)

    channel.cancel()

    thread.join(timeout=5)
    assert sink == [None]  # 走「问不到」那条路，不必为取消另造一套返回值
    assert channel.is_cancelled() is True
    assert channel.waiting() is False


def test_cancel_without_a_pending_question_is_harmless(
    loop: asyncio.AbstractEventLoop,
) -> None:
    channel = interaction.Interaction(loop)

    channel.cancel()

    assert channel.is_cancelled() is True


def test_module_level_cancelled_is_false_without_a_channel() -> None:
    """没有通道就没人能取消 —— 取消是「外面的人」发起的事。"""
    assert interaction.cancelled() is False


# ---------------------------------------------------------------------------
# 模块级入口：没有通道时怎么办
# ---------------------------------------------------------------------------


def test_module_level_entry_points_without_a_channel() -> None:
    """没有通道时返回 None 而不是抛异常 —— 纯脚本调用 / 单元测试就是这个场景。

    抛异常会让整个 agent 循环挂掉；返回 None 只是「问不到」，调用方各自决定怎么办。
    """
    assert interaction.current() is None
    assert interaction.ask(kind="ask", text="?") is None
    assert interaction.confirm(text="?") is None


def test_activate_binds_the_channel_to_the_current_thread(
    loop: asyncio.AbstractEventLoop,
) -> None:
    channel = interaction.Interaction(loop)

    token = interaction.activate(channel)
    try:
        assert interaction.current() is channel
    finally:
        interaction.deactivate(token)

    assert interaction.current() is None


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (interaction.APPROVE, True),
        (interaction.DENY, False),
        ("随便打的", False),  # 不是「允许」就一律当拒绝，别猜
        (None, None),  # 问不到
    ],
)
def test_confirm_only_reads_approve_as_yes(
    monkeypatch: pytest.MonkeyPatch, answer: str | None, expected: bool | None
) -> None:
    monkeypatch.setattr(interaction, "ask", lambda **_: answer)

    assert interaction.confirm(text="允许吗？") is expected


# ---------------------------------------------------------------------------
# 整条链路：生成器被卡住时，问题还能不能出去
# ---------------------------------------------------------------------------


def test_pump_keeps_events_flowing_while_the_generator_is_blocked(
    loop: asyncio.AbstractEventLoop,
) -> None:
    """**这个机制存在的理由。**

    生成器在确认题上阻塞时，它连 yield 都做不到；问题必须走旁路（队列）出去，
    答案再从另一个线程写回来。这条用例把那三件事串起来验一遍。
    """
    run = Run(conversation_id="c1", loop=loop)
    _open(run)

    def events() -> Iterator[tuple[str, dict]]:
        yield "text", {"text": "先说话"}
        # 到这里生成器就卡住了 —— 而问题必须已经出去了
        approval = interaction.confirm(text="允许吗？", detail="$ rm -rf /")
        yield "text", {"text": f"用户说：{approval}"}

    thread = threading.Thread(target=_pump, args=(run, events()), daemon=True)
    thread.start()

    # 第一条永远是 start —— 前端要靠它拿到 run_id 才能取消这一轮
    assert _next(loop, run.channel) == ("start", {"run_id": run.id})
    assert _next(loop, run.channel) == ("text", {"text": "先说话"})

    name, payload = _next(loop, run.channel)
    assert name == "question"
    assert payload["detail"] == "$ rm -rf /"

    # 生成器还在等，但问题已经到前端了 —— 这就是要证明的
    assert run.channel.waiting() is True
    assert run.id in _runs

    assert run.channel.answer(payload["id"], interaction.APPROVE) is True
    # confirm() 把它翻成了布尔值 —— 生成器拿到的是 True，不是「允许」这个词
    assert _next(loop, run.channel) == ("text", {"text": "用户说：True"})

    # 流走完 → 通道关闭（None 是结束标记）→ 运行从表里摘掉
    assert _next(loop, run.channel) is None
    thread.join(timeout=5)
    assert run.id not in _runs


def test_pump_reports_a_crash_instead_of_dying_silently(
    loop: asyncio.AbstractEventLoop,
) -> None:
    """外围异常也要变成一条事件。

    不兜的话线程会静默死掉，前端一直转圈等一个永远不来的 `done` —— 那种失败
    最难排查，因为日志里什么都没有。

    但**异常原文不发进事件**：`str(exc)` 里常带着内部路径、配置片段，而这条
    Notice 会原样渲染在对话里。详情写日志，界面只给一句通用的说明。
    """
    run = Run(conversation_id="c2", loop=loop)
    _open(run)

    def events() -> Iterator[tuple[str, dict]]:
        yield "text", {"text": "开始"}
        raise RuntimeError("落盘炸了")

    threading.Thread(target=_pump, args=(run, events()), daemon=True).start()

    assert _next(loop, run.channel)[0] == "start"
    assert _next(loop, run.channel) == ("text", {"text": "开始"})

    name, payload = _next(loop, run.channel)
    assert name == "notice"
    assert "运行中断" in payload["text"]  # 用户看得懂、能行动
    assert "落盘炸了" not in payload["text"]  # 内部细节不外泄

    assert _next(loop, run.channel) is None
    _close(run)


# ---------------------------------------------------------------------------
# agent 层的确认钩子
# ---------------------------------------------------------------------------


def test_a_tool_in_the_confirm_set_runs_only_after_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[str] = []
    monkeypatch.setattr(agent.registry, "execute", lambda name, args: ran.append(name) or "执行了")

    monkeypatch.setattr(agent.interaction, "confirm", lambda **_: False)
    refused = agent._execute({"name": "run_command", "arguments": "{}"}, frozenset({"run_command"}))
    assert ran == []
    assert "用户拒绝" in refused

    monkeypatch.setattr(agent.interaction, "confirm", lambda **_: True)
    call = {"name": "run_command", "arguments": "{}"}
    assert agent._execute(call, frozenset({"run_command"})) == "执行了"
    assert ran == ["run_command"]


def test_a_tool_outside_the_confirm_set_never_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent.registry, "execute", lambda name, args: "执行了")
    monkeypatch.setattr(agent.interaction, "confirm", lambda **_: pytest.fail("不该问用户"))

    result = agent._execute({"name": "read_file", "arguments": "{}"}, frozenset({"run_command"}))

    assert result == "执行了"


def test_no_channel_is_treated_as_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """问不到 ≠ 允许。默认必须是拒绝 —— 反向的默认会静默地放行每一次危险调用。"""
    ran: list[str] = []
    monkeypatch.setattr(agent.registry, "execute", lambda name, args: ran.append(name) or "ok")
    monkeypatch.setattr(agent.interaction, "confirm", lambda **_: None)

    result = agent._execute({"name": "run_command", "arguments": "{}"}, frozenset({"run_command"}))

    assert ran == []
    assert "没能问到用户" in result


def test_refused_and_unanswerable_say_different_things() -> None:
    """「用户拒绝」和「问不到」必须给出不同的提示。

    前者是用户的决定，模型该放弃这条路；后者是这套配置在当前界面上根本用不了，
    该做的是告诉用户。合成一句话的话，模型（和用户）都分不清发生了什么。
    """
    from quill_agent.tools import shell

    unanswerable = shell.run_command("rm -rf /")  # 测试进程里没有通道

    assert "没能问到用户" in unanswerable
    assert "用户拒绝" not in unanswerable
    assert "对根目录执行 rm" in unanswerable  # 原因要带上，否则用户不知道该改什么


def test_preview_shows_the_meaningful_field_not_raw_json() -> None:
    """确认题是给人看的：先给最能说明问题的那个字段，其余当补充。"""
    text = agent._preview("run_command", '{"command": "rm -rf build", "timeout": 60}')

    assert "run_command" in text
    assert "rm -rf build" in text
    assert "timeout" in text  # 其余参数照样给出，别让用户以为就这一个


def test_preview_survives_broken_arguments() -> None:
    """参数是模型拼出来的，可能是半截 JSON —— 这里炸掉就等于确认机制本身不可用。"""
    assert "read_file" in agent._preview("read_file", "{不是 JSON")


def test_preview_is_clipped() -> None:
    long_value = "x" * (agent.MAX_PREVIEW_CHARS * 2)

    assert len(agent._preview("read_file", f'{{"path": "{long_value}"}}')) < (
        agent.MAX_PREVIEW_CHARS + 50
    )


# ---------------------------------------------------------------------------
# 传输层的两处约定
# ---------------------------------------------------------------------------


def test_sse_serializes_both_kinds_of_events() -> None:
    """生成器产出的事件和 `ask()` 旁路塞进来的问题，到前端是**同一种格式**。

    这正是把「拼 SSE」从生成器里挪到队列下游的理由 —— 两类来源共用一份拼装逻辑。
    """
    from server.routes.chat import _sse

    async def main() -> list[str]:
        run = Run(conversation_id="c3", loop=asyncio.get_running_loop())
        # 直接往队列里塞，绕开线程：这里要验的是格式，不是投递
        run.channel.queue.put_nowait(("text", {"text": "你好"}))
        run.channel.queue.put_nowait(("question", {"id": "q1", "text": "允许吗？"}))
        run.channel.queue.put_nowait(None)  # 通道关闭

        return [chunk async for chunk in _sse(run)]

    chunks = asyncio.run(main())

    assert chunks == [
        'event: text\ndata: {"text": "你好"}\n\n',  # ensure_ascii=False：中文不转义
        'event: question\ndata: {"id": "q1", "text": "允许吗？"}\n\n',
    ]


def test_answer_endpoint_accepts_a_late_answer_gracefully() -> None:
    """运行已经结束时的回答返回 accepted=false，**不是 404**。

    用户点「允许」的同时那一轮刚好超时结束，是完全正常的竞态；
    让它变成一个 HTTP 错误，前端就得为一个正常情况弹提示。
    """
    from server.routes.chat import answer
    from server.schemas import AnswerPayload

    result = asyncio.run(
        answer(AnswerPayload(run_id="早就结束了", question_id="q1", answer=interaction.APPROVE))
    )

    assert result == {"accepted": False}


def test_cancel_endpoint_accepts_an_unknown_run() -> None:
    """停止一个已经结束的轮次是正常操作，同样不该变成 404。"""
    from server.routes.chat import cancel
    from server.schemas import CancelPayload

    assert asyncio.run(cancel(CancelPayload(run_id="早就结束了"))) == {"cancelled": False}


# ---------------------------------------------------------------------------
# ask_user 工具
# ---------------------------------------------------------------------------


@pytest.fixture
def channel(loop: asyncio.AbstractEventLoop) -> Iterator[interaction.Interaction]:
    """在当前线程上绑一条通道，用完解绑。"""
    item = interaction.Interaction(loop, run_id="r1")
    token = interaction.activate(item)
    try:
        yield item
    finally:
        interaction.deactivate(token)


def test_ask_user_without_a_channel_says_what_to_do_instead() -> None:
    """没有通道（CLI / 单元测试）时不能只报「失败」——

    那会让模型原地卡住或者反复重试。得告诉它「自己拿主意，并说明假设」。
    """
    text = builtin.ask_user("用哪个环境？")

    assert "问不到用户" in text
    assert "假设" in text


def test_ask_user_returns_the_answer_marked_as_the_users_words(
    loop: asyncio.AbstractEventLoop, channel: interaction.Interaction
) -> None:
    """答案要带上「用户回答：」前缀：这句会原样进 tool 消息，

    不加前缀的话，用户说的话和工具自己的输出长得一模一样，模型分不出来。
    """

    sink: list = []
    thread = _in_thread(
        channel,
        lambda: sink.append(builtin.ask_user("用哪个环境？", ["测试", "生产"])),
    )

    name, payload = _next(loop, channel)
    assert name == "question"
    assert payload["kind"] == "ask"
    assert payload["options"] == ["测试", "生产"]

    assert channel.answer(payload["id"], "生产") is True
    thread.join(timeout=5)

    assert sink == ["用户回答：生产"]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (None, "没有回答"),
        ("   ", "空的"),
    ],
)
def test_ask_user_always_leaves_the_model_a_way_forward(
    monkeypatch: pytest.MonkeyPatch,
    channel: interaction.Interaction,
    answer: str | None,
    expected: str,
) -> None:
    """没问到、或者用户交了个空白 —— 两种都要给一句「接下来怎么办」，

    否则模型只会把问题原样再问一遍，而那也是白问。
    """
    monkeypatch.setattr(channel, "ask", lambda **_: answer)

    text = builtin.ask_user("用哪个环境？")

    assert expected in text
    assert "假设" in text


# ---------------------------------------------------------------------------
# submit_plan
# ---------------------------------------------------------------------------


def test_submit_plan_without_a_channel_forbids_acting() -> None:
    """没有审批通道时**不能默许它动手**。

    默许的话，这个工具就把自己要防的那件事放了进去：「先问再动」变成了「不问就动」。
    退一步是让它把计划当成本轮回答交出去 —— 用户回一句「做吧」，下一轮再执行。
    """

    text = builtin.submit_plan("## 计划\n\n1. 改 a.py")

    assert "交不了计划" in text
    assert "还没有动手" in text


def test_submit_plan_refuses_an_empty_plan() -> None:

    assert "空的" in builtin.submit_plan("   ")


def test_submit_plan_card_carries_the_body_and_the_two_options(
    loop: asyncio.AbstractEventLoop, channel: interaction.Interaction
) -> None:
    """卡片要拿到正文（用户就是靠它做判断的）和两个选项。

    **标题那一行要从正文里去掉**：不去的话卡片头上一个标题、正文里再来一个，
    同一句话接连出现两次 —— 看着像个标题没对齐的文档。
    """
    plan = "### 重写登录流程\n\n1. 抽出 TokenStore\n2. 补测试"
    sink: list = []
    thread = _in_thread(channel, lambda: sink.append(builtin.submit_plan(plan)))

    name, payload = _next(loop, channel)
    assert name == "question"
    assert payload["kind"] == "plan"
    assert payload["text"] == "重写登录流程"
    assert payload["detail"] == "1. 抽出 TokenStore\n2. 补测试"
    assert payload["options"] == [builtin.PLAN_APPROVE, builtin.PLAN_REJECT]

    assert channel.answer(payload["id"], builtin.PLAN_APPROVE) is True
    thread.join(timeout=5)
    assert "可以开始执行" in sink[0]


def test_submit_plan_prefers_the_summary_as_the_title(
    loop: asyncio.AbstractEventLoop, channel: interaction.Interaction
) -> None:
    """给了 summary 就整段正文原样保留 —— 标题是从别处来的，不必去正文里抠。"""
    plan = "# 重写登录流程\n\n细节…"
    sink: list = []
    thread = _in_thread(
        channel,
        lambda: sink.append(builtin.submit_plan(plan, summary="一句话概括")),
    )

    _, payload = _next(loop, channel)
    assert payload["text"] == "一句话概括"
    assert payload["detail"] == plan

    channel.answer(payload["id"], builtin.PLAN_APPROVE)
    thread.join(timeout=5)


def test_submit_plan_keeps_the_body_when_the_first_line_is_not_a_heading(
    loop: asyncio.AbstractEventLoop, channel: interaction.Interaction
) -> None:
    """正文第一句不是标题时**别把它吃掉** —— 那句话往往是有用的。

    这时用一句通用标题，正文原样保留。
    """
    plan = "先把现有的登录链路看一遍\n\n再决定怎么改"
    sink: list = []
    thread = _in_thread(channel, lambda: sink.append(builtin.submit_plan(plan)))

    _, payload = _next(loop, channel)
    assert payload["text"] == builtin.PLAN_FALLBACK_TITLE
    assert payload["detail"] == plan

    channel.answer(payload["id"], builtin.PLAN_APPROVE)
    thread.join(timeout=5)


def test_submit_plan_survives_a_degenerate_heading(
    loop: asyncio.AbstractEventLoop, channel: interaction.Interaction
) -> None:
    """整行只有 `#`、或计划就只有一行标题 —— 都不能把标题或正文削成空串。

    空标题会在卡片上留一个无字框；空正文则让用户没东西可看。
    """
    sink: list = []
    thread = _in_thread(channel, lambda: sink.append(builtin.submit_plan("##\n\n正文")))

    _, payload = _next(loop, channel)
    assert payload["text"] == builtin.PLAN_FALLBACK_TITLE
    assert payload["detail"] == "##\n\n正文"

    channel.answer(payload["id"], builtin.PLAN_APPROVE)
    thread.join(timeout=5)

    sink.clear()
    only_title = _in_thread(channel, lambda: sink.append(builtin.submit_plan("# 只有标题")))
    _, payload = _next(loop, channel)

    assert payload["text"] == "只有标题"
    assert payload["detail"] == "# 只有标题"  # 去掉就空了，那还不如留着

    channel.answer(payload["id"], builtin.PLAN_APPROVE)
    only_title.join(timeout=5)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (builtin.PLAN_APPROVE, "可以开始执行"),
        (f"{builtin.PLAN_APPROVE}\n顺便把测试补上", "顺便把测试补上"),
        (f"{builtin.PLAN_REJECT}\n步骤 2 拆开", "步骤 2 拆开"),
        (builtin.PLAN_REJECT, "先问清楚"),
        (None, "不要开始执行"),
    ],
)
def test_submit_plan_reads_the_verdict_and_the_note(
    monkeypatch: pytest.MonkeyPatch,
    channel: interaction.Interaction,
    answer: str | None,
    expected: str,
) -> None:
    """**按钮文本在第一行、补充说明在第二行** —— 这是前后端之间唯一的约定。

    顺带把五种结局都钉住：批准、批准+补充、驳回+原因、驳回无原因、没答复。
    最后一种必须是「别动手」，而不是「那我先做着」。
    """

    monkeypatch.setattr(channel, "ask", lambda **_: answer)

    assert expected in builtin.submit_plan("计划正文")


def test_a_note_does_not_turn_an_approval_into_a_rejection(
    monkeypatch: pytest.MonkeyPatch, channel: interaction.Interaction
) -> None:
    """带补充说明的「批准」仍然是批准。

    判定只看第一行 —— 用户随手在说明里写个「驳回」不该把结论翻过来。
    """

    monkeypatch.setattr(channel, "ask", lambda **_: f"{builtin.PLAN_APPROVE}\n驳回吧")

    assert builtin.submit_plan("计划正文").startswith("用户批准了计划")
