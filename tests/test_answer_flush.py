"""一轮对话的落盘：助手消息**边跑边写**，而且盘上始终只有一条。

补的是「跑到一半进程被杀」这个场景。在此之前助手消息只在整轮结束时写一次，所以那一刻
盘上只剩用户那句提问 —— 想过什么、动过哪些工具，重启后全都看不出来。而窗口随任务变长
而变大，长任务恰恰最经不起这个。

两件事要同时成立，所以两件都测：

- **盘上一直有**：中途断了也得留下记录（否则这件事等于没做）
- **盘上只有一条**：写了很多次也不能变成很多条（否则比不写更糟）
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from server import stores
from server.routes import chat
from server.schemas import ChatRequest

from quill_agent.agent import Round, ToolStep
from quill_agent.history import ConversationStore


@pytest.fixture
def conv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[ConversationStore, str]]:
    """把会话存储指到临时目录。

    `stream_round` 写的是 `stores.conversations()` —— 不隔离的话，这些用例会直接写进
    开发机的真实会话目录，跑一次测试就多几条垃圾会话。
    """
    store = ConversationStore(tmp_path / "conversations")
    conversation_id = store.create()
    monkeypatch.setattr(stores, "conversations", lambda: store)
    yield store, conversation_id


def _request(conversation_id: str) -> ChatRequest:
    return ChatRequest(conversation_id=conversation_id, prompt="随便问问")


def _scripted(*events: object):
    """一个照本宣科的假 agent 事件流（不碰模型）。"""

    def fake(**_: object):
        yield from events

    return fake


def _assistants(store: ConversationStore, conversation_id: str) -> list[dict]:
    return [item for item in store.load(conversation_id) if item.get("role") == "assistant"]


def test_one_record_even_when_flushed_on_every_event(
    conv: tuple[ConversationStore, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """把节流关掉、逼它每个事件都真写，盘上也必须只有一条。

    这正是 `upsert_run` 存在的理由：内容是**同一轮**的多次更新，不是多条消息。
    """
    store, conversation_id = conv
    monkeypatch.setattr(chat, "run_agent_stream", _scripted("你好", "，世界"))
    monkeypatch.setattr(chat, "ANSWER_FLUSH_SECONDS", 0.0)

    list(chat.stream_round(_request(conversation_id), [], "run-1"))

    written = _assistants(store, conversation_id)
    assert len(written) == 1
    assert written[0]["content"] == "你好，世界"
    assert written[0]["run_id"] == "run-1"
    # 收尾写的是最终态，不再是中间态
    assert "partial" not in written[0]


def test_interrupted_round_keeps_a_partial_record(
    conv: tuple[ConversationStore, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """跑到一半断了，盘上要留下**标记为中间态**的那一条。

    标记不能省：重启后看到半截内容，没有它就会以为模型就回答到那里为止。
    """
    store, conversation_id = conv
    monkeypatch.setattr(chat, "ANSWER_FLUSH_SECONDS", 0.0)

    def exploding(**_: object):
        yield "已经想了一半"
        raise RuntimeError("模型挂了")

    monkeypatch.setattr(chat, "run_agent_stream", exploding)

    with pytest.raises(RuntimeError):
        list(chat.stream_round(_request(conversation_id), [], "run-1"))

    written = _assistants(store, conversation_id)
    assert len(written) == 1
    assert written[0]["content"] == "已经想了一半"
    assert written[0]["partial"] is True


def test_partial_record_carries_the_steps_so_far(
    conv: tuple[ConversationStore, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """中间态不只是正文，已经跑过的工具步骤也要在里面。

    否则「这一轮动过哪个文件」在重启后依然是空白 —— 而那正是要修的东西。
    """
    store, conversation_id = conv
    step = ToolStep(name="write_file", arguments="{}", result="已覆盖：a.txt", elapsed=0.01)
    monkeypatch.setattr(chat, "ANSWER_FLUSH_SECONDS", 0.0)

    def exploding_after_the_step(**_: object):
        yield step
        raise RuntimeError("模型挂了")

    monkeypatch.setattr(chat, "run_agent_stream", exploding_after_the_step)

    with pytest.raises(RuntimeError):
        list(chat.stream_round(_request(conversation_id), [], "run-1"))

    written = _assistants(store, conversation_id)
    assert written[0]["steps"][0]["name"] == "write_file"
    assert written[0]["partial"] is True


def test_throttle_skips_writes_inside_the_interval(
    conv: tuple[ConversationStore, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """间隔之内不重复写盘：正文是逐分片来的，每个分片写一次是在拿磁盘换没人看的中间态。

    三个事件会在同一瞬间到达，所以只有**第一次**（立即）和**收尾**（强制）会真写 ——
    中间那次被时间挡住。
    """
    store, conversation_id = conv
    written: list[dict] = []
    real_upsert = store.upsert_run

    def counting_upsert(conv_id: str, run_id: str, message: dict) -> None:
        written.append(message)
        real_upsert(conv_id, run_id, message)

    monkeypatch.setattr(store, "upsert_run", counting_upsert)
    monkeypatch.setattr(chat, "run_agent_stream", _scripted("一", "二", "三"))

    list(chat.stream_round(_request(conversation_id), [], "run-1"))

    assert len(written) == 2
    assert written[0].get("partial") is True  # 第一次是中间态
    assert "partial" not in written[-1]  # 最后一次是最终态


def test_an_empty_snapshot_does_not_use_up_the_first_write(
    conv: tuple[ConversationStore, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """过程事件不该占掉「首次写」的机会 —— 否则盘上会停在一个空壳上。

    这是实测抓出来的：整轮的事件往往在几十毫秒内到齐（`round` → 正文 → 工具 → `round`），
    而节流会把除第一条之外的全部挡下。第一条偏偏是 `round` —— 那时还没有正文，快照是空的。
    挡完之后模型就挂住了（等响应、等工具），**不会再有事件来推动下一次落盘**。

    结果：文件明明已经改过了、话也想过了，重启后看到的却是一条内容为空的消息。

    这里用一个极长的节流间隔把那个场景钉住：只有「首次写」这一次机会，它必须留给
    **第一个有内容的事件**。
    """
    store, conversation_id = conv
    monkeypatch.setattr(chat, "run_agent_stream", _scripted(Round(index=1, total=30), "正文来了"))
    monkeypatch.setattr(chat, "ANSWER_FLUSH_SECONDS", 9999.0)

    list(chat.stream_round(_request(conversation_id), [], "run-1"))

    written = _assistants(store, conversation_id)
    assert written[0]["content"] == "正文来了"


def test_a_tool_step_writes_even_inside_the_interval(
    conv: tuple[ConversationStore, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """工具步骤在节流窗口内也要落盘 —— 它是「做了什么」最硬的证据。

    同样用极长的节流：不强制写的话，这一步会被整轮唯一那次「首次写」挡在外面，
    盘上就只剩用户那句提问。
    """
    store, conversation_id = conv
    step = ToolStep(name="write_file", arguments="{}", result="已覆盖：a.txt", elapsed=0.01)
    monkeypatch.setattr(chat, "run_agent_stream", _scripted("动手前", step))
    monkeypatch.setattr(chat, "ANSWER_FLUSH_SECONDS", 9999.0)

    list(chat.stream_round(_request(conversation_id), [], "run-1"))

    written = _assistants(store, conversation_id)
    assert written[0]["steps"][0]["name"] == "write_file"


def test_a_failed_partial_write_does_not_break_the_round(
    conv: tuple[ConversationStore, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """中间态写盘失败不该打断这一轮 —— 它是附加的东西。

    否则一次 IO 抖动就能把整轮对话打掉，而后面本来还有几十次机会把它写进去。
    收尾那一次不一样：失败了这一轮的结果就真没留下来，得往上抛（变成一条 Notice）。
    """
    store, conversation_id = conv
    attempts: list[bool] = []

    def failing_partial(conv_id: str, run_id: str, message: dict) -> None:
        attempts.append(bool(message.get("partial")))
        if message.get("partial"):
            raise OSError("磁盘满了")
        # 最终态这里不抛 —— 这条用例只钉「中间态失败被吞」

    monkeypatch.setattr(store, "upsert_run", failing_partial)
    monkeypatch.setattr(chat, "run_agent_stream", _scripted("一", "二"))

    list(chat.stream_round(_request(conversation_id), [], "run-1"))  # 不该抛

    assert attempts == [True, False]
