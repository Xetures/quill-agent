"""任务清单工具：解析校验、那块板、以及推给界面的那条路。

重点不是「清单文本排得对不对」，而是三件事各自到位 —— 一份坏清单会被直接渲染到用户
眼前（所以必须拦住）、清单要落在那块由调用方交给运行的板上（落盘靠它）、还要实时推一条
事件出去（进度条靠它）。
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from quill_agent import agent, interaction
from quill_agent.models import ModelChoice, ModelConfig
from quill_agent.tools import registry
from quill_agent.tools.todo import TodoBoard


def _write(todos) -> str:
    """走一遍完整的调用路径：参数校验 -> 执行 -> 结果文案。

    不直接调 `todo_write()`，是因为模型递进来的其实是**解析后的 JSON**，
    `registry.execute` 那一层才是真实入口（比如参数缺失时的报错就从那里出来）。
    """
    return registry.execute("todo_write", {"todos": todos})


@pytest.fixture
def board() -> Iterator[TodoBoard]:
    """挂一块清单板，把自己伪装成「正在跑一轮」。

    工具要靠环境里的那块板才落得下东西。直接用私有的 ContextVar 是刻意的 ——
    本仓库的测试本来就贴着实现写（同 test_subagent.py）。
    """
    board = TodoBoard()
    environment = agent.RunEnvironment(
        context=agent.ModeContext(tools=["todo_write"]),
        choice=None,
        stats=agent.RunStats(),
        todos=board,
    )
    token = agent._environment.set(environment)
    try:
        yield board
    finally:
        agent._environment.reset(token)


# ---------------------------------------------------------------------------
# 解析与校验
# ---------------------------------------------------------------------------


def test_a_full_list_is_rendered_back(board: TodoBoard) -> None:
    """结果里要把清单排回来：模型据此确认自己写的确实被记下了。"""
    result = _write(
        [
            {"content": "看现有实现", "status": "completed"},
            {"content": "改代码", "status": "in_progress"},
            {"content": "跑测试", "status": "pending"},
        ]
    )

    assert "1/3 已完成" in result
    assert "[x] 看现有实现" in result
    assert "[>] 改代码" in result
    assert "[ ] 跑测试" in result


def test_the_board_gets_the_items(board: TodoBoard) -> None:
    _write([{"content": "改代码", "status": "in_progress"}])

    assert board.to_payload() == [{"content": "改代码", "status": "in_progress"}]


def test_status_is_normalised(board: TodoBoard) -> None:
    """大小写和空格都收一下 —— 模型写 `Completed ` 是常事，这不值得让整轮停下来。"""
    _write([{"content": "改代码", "status": " Completed "}])

    assert board.items[0].status == "completed"


def test_a_json_string_is_parsed(board: TodoBoard) -> None:
    """有些模型会把数组序列化成字符串再塞进来。给一次机会，别让整轮白跑。"""
    payload = json.dumps([{"content": "改代码", "status": "pending"}], ensure_ascii=False)

    assert "[ ] 改代码" in _write(payload)


def test_a_long_item_is_truncated(board: TodoBoard) -> None:
    """内容超长只截断、不报错：长短是表述问题，不值得为它让整轮停下。"""
    _write([{"content": "很长" * 200, "status": "pending"}])

    assert board.items[0].content.endswith("…")
    assert len(board.items[0].content) <= 121


def test_an_empty_list_is_refused(board: TodoBoard) -> None:
    """空清单要挡住：它多半意味着模型没想清楚就调了 —— 而界面会显示一张空卡片。"""
    assert "空的" in _write([])
    assert board.items == ()


def test_a_non_list_is_refused(board: TodoBoard) -> None:
    assert "数组" in _write({"content": "改代码"})


def test_an_unknown_status_is_refused(board: TodoBoard) -> None:
    """**不猜**：把不认识的 status 当成 pending，界面会显示一份模型没写过的计划。"""
    result = _write([{"content": "改代码", "status": "跑完了"}])

    assert "跑完了" in result
    assert "pending" in result  # 报错里要给出合法取值
    assert board.items == ()


def test_an_item_without_content_is_refused(board: TodoBoard) -> None:
    assert "content" in _write([{"content": "   ", "status": "pending"}])


def test_a_non_object_item_is_refused(board: TodoBoard) -> None:
    assert "第 2 项" in _write([{"content": "改代码", "status": "pending"}, "顺手跑测试"])


def test_too_many_items_is_refused(board: TodoBoard) -> None:
    """上限拦的是「把清单当笔记写」：二十项的清单没人读得下去。"""
    result = _write([{"content": f"第 {i} 步", "status": "pending"} for i in range(20)])

    assert "最多" in result
    assert board.items == ()


def test_a_bad_submission_leaves_the_board_alone(board: TodoBoard) -> None:
    """校验没过时**不动**那块板。

    否则一次写歪的调用会把界面上好好的进度条清空 —— 用户看到进度全没了，而实际上
    什么都没变。
    """
    _write([{"content": "第一步", "status": "in_progress"}])
    _write([{"content": "第二步", "status": "乱写的状态"}])

    assert [item.content for item in board.items] == ["第一步"]


def test_several_in_progress_gets_a_nudge(board: TodoBoard) -> None:
    """提醒但**不拦**：状态怎么标是模型的表述选择，拦下来它只会为了凑格式去改内容。"""
    result = _write(
        [
            {"content": "改 A", "status": "in_progress"},
            {"content": "改 B", "status": "in_progress"},
        ]
    )

    assert "只留一项" in result
    assert len(board.items) == 2  # 照样记下了


def test_only_the_last_submission_counts(board: TodoBoard) -> None:
    """清单是「现在的计划」，不是日志 —— 所以整体替换，不留旧条目。"""
    _write(
        [
            {"content": "旧的第一步", "status": "pending"},
            {"content": "旧的第二步", "status": "pending"},
        ]
    )
    _write([{"content": "只做这一件", "status": "in_progress"}])

    assert [item.content for item in board.items] == ["只做这一件"]


def test_without_a_run_the_tool_still_works() -> None:
    """不在一次运行里（没板可写）时照样返回清单文本。

    落盘和显示各走各的，缺了板不该让工具本身失败 —— 直连工具跑一遍、或者别的调用方
    忘了传板，都不该看到一个报错。
    """
    assert agent.current_environment() is None
    assert "1/1 已完成" in _write([{"content": "改代码", "status": "completed"}])


# ---------------------------------------------------------------------------
# 推给界面的那条路
# ---------------------------------------------------------------------------


def _next(event_loop, channel, timeout=5.0):
    return asyncio.run_coroutine_threadsafe(channel.queue.get(), event_loop).result(timeout=timeout)


def test_the_update_reaches_the_channel() -> None:
    """事件名是 `todo`，载荷是**完整清单**（前端直接覆盖，不做增量合并）。"""
    event_loop = asyncio.new_event_loop()
    thread = threading.Thread(target=event_loop.run_forever, daemon=True)
    thread.start()

    try:
        channel = interaction.Interaction(event_loop, run_id="r")
        token = interaction.activate(channel)
        try:
            _write([{"content": "改代码", "status": "in_progress"}])
        finally:
            interaction.deactivate(token)

        assert _next(event_loop, channel) == (
            "todo",
            {"items": [{"content": "改代码", "status": "in_progress"}]},
        )
    finally:
        event_loop.call_soon_threadsafe(event_loop.stop)
        thread.join(timeout=2)
        event_loop.close()


def test_publish_is_a_no_op_without_a_channel() -> None:
    """没有通道时静默丢弃：清单照样记着，只是这一轮看不到实时进度。"""
    assert _write([{"content": "改代码", "status": "pending"}])


# ---------------------------------------------------------------------------
# 接进运行链路
#
# 上面几组都是直接调工具。这一段要验的是**那根线**：`run_agent_stream` 收到板之后
# 有没有真的把它挂到环境上 —— 没挂上的话，工具会安安静静地写进一块谁也不读的板里，
# 而所有单元测试都还是绿的。
# ---------------------------------------------------------------------------


def _tool_call(arguments: str) -> SimpleNamespace:
    call = SimpleNamespace(
        index=0, id="c1", function=SimpleNamespace(name="todo_write", arguments=arguments)
    )
    delta = SimpleNamespace(content=None, tool_calls=[call])
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _text(text: str) -> SimpleNamespace:
    delta = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


class _FakeClient:
    def __init__(self, responses: list[list]) -> None:
        self._responses = responses
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        return iter(self._responses.pop(0))


def test_the_board_is_filled_in_a_real_run(monkeypatch: pytest.MonkeyPatch) -> None:
    choice = ModelChoice(
        config=ModelConfig(id="c1", name="测试连接", models=["m"]),
        model="m",
    )
    payload = json.dumps(
        {"todos": [{"content": "查资料", "status": "in_progress"}]}, ensure_ascii=False
    )
    monkeypatch.setattr(
        agent, "OpenAI", lambda **kwargs: _FakeClient([[_tool_call(payload)], [_text("好")]])
    )
    # 工具菜单固定，免得结果受本机工具注册表影响（同 test_agent_loop.py）
    monkeypatch.setattr(agent.registry, "schemas", lambda *a, **k: [{"type": "function"}])

    board = TodoBoard()
    events = list(
        agent.run_agent_stream(
            prompt="做件事",
            files=[],
            mode=None,
            choice=choice,
            history=[],
            board=board,
        )
    )

    assert [item.content for item in board.items] == ["查资料"]
    assert [item.name for item in events if isinstance(item, agent.ToolStep)] == ["todo_write"]


def test_a_run_without_a_board_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """不给板就自己建一块 —— 子代理走的正是这条路（它那份清单不落到父级去）。"""
    choice = ModelChoice(
        config=ModelConfig(id="c1", name="测试连接", models=["m"]),
        model="m",
    )
    payload = json.dumps({"todos": [{"content": "查资料", "status": "pending"}]})
    monkeypatch.setattr(
        agent, "OpenAI", lambda **kwargs: _FakeClient([[_tool_call(payload)], [_text("好")]])
    )
    monkeypatch.setattr(agent.registry, "schemas", lambda *a, **k: [{"type": "function"}])

    events = list(
        agent.run_agent_stream(
            prompt="做件事", files=[], mode=None, choice=choice, history=[]
        )
    )

    assert [item.name for item in events if isinstance(item, agent.ToolStep)] == ["todo_write"]
