"""会话记录的按条删除。

会话文件是 jsonl（一行一条），删除要**重写整个文件** —— 因为「第几条」这个约定靠行号，
挖一个洞会让它失效，而前端的删除按钮正是按索引传的。所以这里钉住的是：删完之后，
剩下的还是一个**合法且紧凑**的 jsonl。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quill_agent.history import ConversationStore


@pytest.fixture
def store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(tmp_path)


@pytest.fixture
def conv(store: ConversationStore) -> str:
    conversation = store.create()
    for index in range(4):
        store.append(conversation, {"role": "user", "content": f"第{index}条"})
    return conversation


def test_delete_at_removes_that_one(store: ConversationStore, conv: str) -> None:
    assert store.delete_at(conv, 1) is True

    left = [item["content"] for item in store.load(conv)]

    assert left == ["第0条", "第2条", "第3条"]


def test_delete_at_keeps_file_valid_and_compact(
    store: ConversationStore, conv: str, tmp_path: Path
) -> None:
    """删完之后不能留下空行或半个条目 —— 重写整个文件就是为了这个。"""
    store.delete_at(conv, 0)

    path = tmp_path / "active" / f"{conv}.jsonl"
    lines = [line for line in path.read_text(encoding="utf-8").splitlines()]

    assert len(lines) == 3
    assert all(json.loads(line)["role"] == "user" for line in lines)


def test_delete_at_out_of_range_changes_nothing(store: ConversationStore, conv: str) -> None:
    assert store.delete_at(conv, 99) is False
    assert store.delete_at(conv, -1) is False

    assert len(store.load(conv)) == 4


def test_delete_at_missing_conversation_raises(store: ConversationStore) -> None:
    with pytest.raises(ValueError):
        store.delete_at("不存在的会话", 0)


def test_delete_at_first_and_last(store: ConversationStore, conv: str) -> None:
    """两头都要能删 —— 边界最容易写错（`>=` / `>` 写反就少删或多删一条）。"""
    assert store.delete_at(conv, 0) is True
    assert store.delete_at(conv, 2) is True  # 删掉 0 之后，原来的第 3 条到了第 2 位

    assert [item["content"] for item in store.load(conv)] == ["第1条", "第2条"]


def test_delete_until_empty_then_load_is_empty(store: ConversationStore, conv: str) -> None:
    """删到空之后 `load` 要给空列表 —— 归档那条路「空会话直接删」依赖这个判断。"""
    for _ in range(4):
        store.delete_at(conv, 0)

    assert store.load(conv) == []


def test_upsert_run_keeps_one_record_per_run(store: ConversationStore, conv: str) -> None:
    """同一轮写多少次，盘上就只有一条。

    助手消息是**边跑边写**的（`chat.stream_round` → `_AnswerSaver`），一轮里会写很多次。
    用 append 的话每写一次多一条，重启后看起来像模型回答了十几次。
    """
    store.upsert_run(conv, "run-a", {"role": "assistant", "run_id": "run-a", "content": "半截"})
    store.upsert_run(conv, "run-a", {"role": "assistant", "run_id": "run-a", "content": "完整了"})

    assistants = [item for item in store.load(conv) if item["role"] == "assistant"]
    assert len(assistants) == 1
    assert assistants[0]["content"] == "完整了"


def test_upsert_run_replaces_in_place(store: ConversationStore, conv: str) -> None:
    """替换**不改变位置**：这一轮的消息该留在它原来的先后顺序里。

    否则每刷一次盘，这条消息就跑到末尾 —— 而后面可能还有这一轮产生的摘要记录。
    """
    store.upsert_run(conv, "run-a", {"role": "assistant", "run_id": "run-a", "content": "半截"})
    store.append(conv, {"role": "summary", "content": "压过的前文"})
    store.upsert_run(conv, "run-a", {"role": "assistant", "run_id": "run-a", "content": "完整了"})

    assert [item.get("role") for item in store.load(conv)[-2:]] == ["assistant", "summary"]
    assert store.load(conv)[-2]["content"] == "完整了"


def test_upsert_run_appends_distinct_runs(store: ConversationStore, conv: str) -> None:
    """不同轮次各占一条 —— 别把上一轮的回答给覆盖掉。"""
    store.upsert_run(conv, "run-a", {"role": "assistant", "run_id": "run-a", "content": "第一轮"})
    store.upsert_run(conv, "run-b", {"role": "assistant", "run_id": "run-b", "content": "第二轮"})

    assistants = [item["content"] for item in store.load(conv) if item["role"] == "assistant"]
    assert assistants == ["第一轮", "第二轮"]


def test_upsert_run_creates_the_file_when_missing(tmp_path: Path) -> None:
    """会话文件不存在也要能写 —— 它不是「只在已有会话上用」的接口。"""
    store = ConversationStore(tmp_path)
    store.upsert_run("20260101-000000-abcd", "run-a", {"role": "assistant", "content": "x"})

    assert store.load("20260101-000000-abcd")[0]["content"] == "x"
