"""记忆测试：写入规则、开关过滤、清单生成。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from quill_agent import agent
from quill_agent.memory import MAX_MEMORIES, MAX_MEMORY_CHARS, MemoryStore


def make_store(tmp_path: Path, name: str = "memory.json") -> MemoryStore:
    return MemoryStore(tmp_path / name)


# ---------------------------------------------------------------------------
# 写入规则
# ---------------------------------------------------------------------------
def test_add_and_list(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    item = store.add("偏好简洁回答")

    assert item.text == "偏好简洁回答"
    assert item.enabled is True
    assert item.created_at
    assert [entry.text for entry in store.list()] == ["偏好简洁回答"]


def test_add_collapses_whitespace(tmp_path: Path) -> None:
    """记忆是一句话，换行和多余空格都压掉。"""
    store = make_store(tmp_path)

    item = store.add("  偏好简洁回答\n\n不要啰嗦  ")

    assert item.text == "偏好简洁回答 不要啰嗦"


def test_add_rejects_empty_text(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    with pytest.raises(ValueError, match="不能为空"):
        store.add("   \n  ")


def test_add_rejects_too_long_text(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    with pytest.raises(ValueError, match="太长"):
        store.add("字" * (MAX_MEMORY_CHARS + 1))


def test_add_rejects_duplicates(tmp_path: Path) -> None:
    """同一件事记两遍没有意义，还会把清单撑满。"""
    store = make_store(tmp_path)
    store.add("偏好简洁回答")

    with pytest.raises(ValueError, match="已经记过"):
        store.add("偏好简洁回答")


def test_add_rejects_when_full(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    for index in range(MAX_MEMORIES):
        store.add(f"第 {index} 条")

    with pytest.raises(ValueError, match="已满"):
        store.add("再来一条")


def test_newest_first(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.add("先记的")
    store.add("后记的")

    assert [entry.text for entry in store.list()] == ["后记的", "先记的"]


# ---------------------------------------------------------------------------
# 开关与删除
# ---------------------------------------------------------------------------
def test_only_enabled_items_are_injected(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    keep = store.add("保留这条")
    drop = store.add("关掉这条")

    store.set_enabled(drop.id, False)

    assert [entry.text for entry in store.enabled()] == ["保留这条"]
    # 关掉不等于删除，两条都还在
    assert len(store.list()) == 2
    assert keep.enabled is True


def test_forget_removes_matching_item(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.add("保留这条")
    store.add("忘掉这条")

    removed = store.forget("忘掉这条")

    assert removed.text == "忘掉这条"
    assert [entry.text for entry in store.list()] == ["保留这条"]


def test_forget_normalizes_whitespace(tmp_path: Path) -> None:
    """匹配前做和 add 相同的空白归一化，模型多带几个空格也能删对。"""
    store = make_store(tmp_path)
    store.add("偏好简洁回答")

    store.forget("  偏好简洁回答  ")

    assert store.list() == []


def test_forget_rejects_partial_text(tmp_path: Path) -> None:
    """只认完全一致的原文 —— 模糊匹配会连不该删的一起删掉。"""
    store = make_store(tmp_path)
    store.add("偏好简洁回答，不要啰嗦的总结")

    with pytest.raises(ValueError, match="没有找到"):
        store.forget("偏好简洁回答")

    assert len(store.list()) == 1


def test_forget_requires_text(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    with pytest.raises(ValueError, match="不能为空"):
        store.forget("   ")


def test_forget_leaves_other_items_alone(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.add("第一条")
    store.add("第二条")
    store.add("第三条")

    store.forget("第二条")

    assert [entry.text for entry in store.list()] == ["第三条", "第一条"]


def test_remove_and_clear(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = store.add("第一条")
    store.add("第二条")

    store.remove(first.id)
    assert [entry.text for entry in store.list()] == ["第二条"]

    assert store.clear() == 1
    assert store.list() == []


# ---------------------------------------------------------------------------
# 坏数据容错
# ---------------------------------------------------------------------------
def test_broken_file_degrades_gracefully(tmp_path: Path) -> None:
    """文件坏掉时退回空列表，不该让应用起不来。"""
    path = tmp_path / "memory.json"
    path.write_text("{ 这不是合法 JSON", encoding="utf-8")

    assert MemoryStore(path).list() == []


def test_bad_entries_are_skipped(tmp_path: Path) -> None:
    """单条坏数据跳过，其余照常读出。"""
    path = tmp_path / "memory.json"
    path.write_text(
        json.dumps(
            [
                {"id": "a", "text": "好的那条"},
                {"id": "b"},  # 缺 text
                "根本不是对象",
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert [entry.text for entry in MemoryStore(path).list()] == ["好的那条"]


# ---------------------------------------------------------------------------
# 清单生成（agent 层）
# ---------------------------------------------------------------------------
def test_memory_block_lists_enabled_only(monkeypatch, tmp_path: Path) -> None:
    memory_path = tmp_path / "memory.json"
    store = MemoryStore(memory_path)
    store.add("偏好简洁回答")
    dropped = store.add("已经过时的信息")
    store.set_enabled(dropped.id, False)

    monkeypatch.setattr(agent, "get_settings", lambda: SimpleNamespace(memory_path=memory_path))

    block = agent.build_memory_block()

    assert "偏好简洁回答" in block
    assert "已经过时的信息" not in block


def test_memory_block_is_empty_without_items(monkeypatch, tmp_path: Path) -> None:
    """没有记忆时返回空串，调用方据此不加那条 system 消息。"""
    monkeypatch.setattr(
        agent,
        "get_settings",
        lambda: SimpleNamespace(memory_path=tmp_path / "empty.json"),
    )

    assert agent.build_memory_block() == ""
