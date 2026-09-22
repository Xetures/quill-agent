"""一轮回答里各片段的**顺序**（`parts`）。

`content` 和 `steps` 分开存是有理由的（前者给下一轮组装上下文，后者给审阅），代价是
**丢掉了它们的相对顺序** —— 界面拿不到「哪句话在哪个工具前面」，于是只能把工具堆在一块、
正文堆在另一块。`parts` 补的就是这份顺序：没有它，穿插显示在数据层面就不可能。

老字段照旧保留，所以这里头两条也在钉「它们没被这次改动弄坏」。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from server import stores
from server.routes import chat
from server.schemas import ChatRequest

from quill_agent.agent import ReasoningDelta, ToolStep
from quill_agent.history import ConversationStore


@pytest.fixture
def conv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[ConversationStore, str]]:
    store = ConversationStore(tmp_path / "conversations")
    conversation_id = store.create()
    monkeypatch.setattr(stores, "conversations", lambda: store)
    yield store, conversation_id


def _request(conversation_id: str) -> ChatRequest:
    return ChatRequest(conversation_id=conversation_id, prompt="随便问问")


def _scripted(*events: object):
    def fake(**_: object):
        yield from events

    return fake


def _step(name: str = "read_file") -> ToolStep:
    return ToolStep(name=name, arguments="{}", result="好了", elapsed=0.01)


def _answer(store: ConversationStore, conversation_id: str) -> dict:
    """落盘的那条助手消息（收尾写的是最终态）。"""
    return next(item for item in store.load(conversation_id) if item.get("role") == "assistant")


def test_text_and_tools_keep_their_order(conv, monkeypatch: pytest.MonkeyPatch) -> None:
    """正文和工具调用按**发生顺序**记下来 —— 界面穿插显示全靠它。"""
    store, conversation_id = conv
    monkeypatch.setattr(
        chat, "run_agent_stream", _scripted("先看看这里", _step(), "然后是这里")
    )

    list(chat.stream_round(_request(conversation_id), [], "run-1"))

    parts = _answer(store, conversation_id)["parts"]
    assert [part["type"] for part in parts] == ["text", "tool", "text"]
    assert parts[0]["content"] == "先看看这里"
    assert parts[2]["content"] == "然后是这里"


def test_reasoning_sits_where_it_happened(conv, monkeypatch: pytest.MonkeyPatch) -> None:
    """思考过程混在序列里，按它真实发生的位置 —— 它本来就夹在两次工具调用之间。"""
    store, conversation_id = conv
    monkeypatch.setattr(
        chat,
        "run_agent_stream",
        _scripted(
            ReasoningDelta("先想想"),
            _step("search_content"),
            ReasoningDelta("再想想"),
            "结论",
        ),
    )

    list(chat.stream_round(_request(conversation_id), [], "run-1"))

    parts = _answer(store, conversation_id)["parts"]
    assert [part["type"] for part in parts] == ["reasoning", "tool", "reasoning", "text"]
    assert parts[0]["content"] == "先想想"
    assert parts[2]["content"] == "再想想"


def test_consecutive_chunks_become_one_part(conv, monkeypatch: pytest.MonkeyPatch) -> None:
    """连续的正文分片合成**一段**，不是每个分片一段 —— 否则界面会被切得粉碎。

    正文是逐分片到达的，一次回答能来几百个。
    """
    store, conversation_id = conv
    monkeypatch.setattr(chat, "run_agent_stream", _scripted("一", "二", "三"))

    list(chat.stream_round(_request(conversation_id), [], "run-1"))

    assert _answer(store, conversation_id)["parts"] == [{"type": "text", "content": "一二三"}]


def test_an_empty_draft_does_not_leave_an_empty_part(
    conv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连着两次工具调用、中间没说话，不该在中间插一个空段。"""
    store, conversation_id = conv
    monkeypatch.setattr(chat, "run_agent_stream", _scripted(_step("list_dir"), _step("read_file")))

    list(chat.stream_round(_request(conversation_id), [], "run-1"))

    parts = _answer(store, conversation_id)["parts"]
    assert [part["type"] for part in parts] == ["tool", "tool"]


def test_an_interrupted_round_keeps_the_draft(conv, monkeypatch: pytest.MonkeyPatch) -> None:
    """跑到一半断了，盘上那条也要带上「还在累积的那一段」—— 少一截界面就缺内容。"""
    store, conversation_id = conv
    monkeypatch.setattr(chat, "ANSWER_FLUSH_SECONDS", 0.0)

    def exploding(**_: object):
        yield "已经说了一半"
        raise RuntimeError("断了")

    monkeypatch.setattr(chat, "run_agent_stream", exploding)
    with pytest.raises(RuntimeError):
        list(chat.stream_round(_request(conversation_id), [], "run-1"))

    saved = _answer(store, conversation_id)
    assert saved["parts"][-1] == {"type": "text", "content": "已经说了一半"}


def test_the_flat_fields_are_still_there(conv, monkeypatch: pytest.MonkeyPatch) -> None:
    """`content` / `steps` 照旧 —— 下一轮上下文、审阅、导出都还在用它们。"""
    store, conversation_id = conv
    monkeypatch.setattr(chat, "run_agent_stream", _scripted("甲", _step("edit_file"), "乙"))

    list(chat.stream_round(_request(conversation_id), [], "run-1"))

    answer = _answer(store, conversation_id)
    assert answer["content"] == "甲乙"
    assert [step["name"] for step in answer["steps"]] == ["edit_file"]
