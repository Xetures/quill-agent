"""改动记录器：diff 与「还原」。

两件事的测试放在一个文件里，是因为它们共用**一条数据**。「界面显示的改动」和「能还原的
改动」必须一致，分开测就测不出这种一致 —— 而那正是这套设计存在的理由。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from quill_agent import changes


@pytest.fixture
def env(tmp_path: Path) -> Iterator[tuple[Path, Path, changes.ChangeRecorder]]:
    """一个独立的 home / 工作目录，并激活记录器（退出时解绑，别串到别的测试）。"""
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()

    recorder = changes.ChangeRecorder(
        work_dir=work, home=home, conversation_id="c1", run_id="run1"
    )
    token = changes.activate(recorder)
    yield home, work, recorder
    changes.deactivate(token)


def test_record_is_silent_without_recorder(tmp_path: Path) -> None:
    """没激活时记录静默跳过 —— 这层是附加的，工具不能依赖它（单元测试会直接调工具）。"""
    target = tmp_path / "a.txt"
    target.write_text("x\n", encoding="utf-8")

    changes.record_text(target, "y\n", kind="write")  # 不抛异常

    assert changes.current() is None
    assert changes.mark() == 0
    assert changes.taken(0) == []


def test_counts_changed_lines(env: tuple[Path, Path, changes.ChangeRecorder]) -> None:
    """行数用 opcodes 算：改一行时前后长度相同，比长度会算出 0。"""
    _, work, recorder = env
    target = work / "a.txt"
    target.write_text("1\n2\n3\n", encoding="utf-8")

    changes.record_text(target, "1\nX\n3\n4\n", kind="write")

    change = recorder.changes[0]
    assert (change.added, change.removed) == (2, 1)
    assert change.path == "a.txt"


def test_new_file_has_no_diff(env: tuple[Path, Path, changes.ChangeRecorder]) -> None:
    """新建文件不出 diff：全绿的一片没有信息量，内容看文件本身就行。"""
    _, work, recorder = env
    target = work / "new.txt"

    changes.record_text(target, "line\n" * 5, kind="create")

    change = recorder.changes[0]
    assert change.before is None
    assert change.unified() == ""
    assert (change.added, change.removed) == (5, 0)


def test_delete_keeps_diff(env: tuple[Path, Path, changes.ChangeRecorder]) -> None:
    """删除保留 diff —— 那份内容消失了，值得看一眼。"""
    _, work, recorder = env
    target = work / "gone.txt"
    target.write_text("也要\n没了\n", encoding="utf-8")

    changes.record_text(target, None, kind="delete")

    change = recorder.changes[0]
    assert "-也要" in change.unified()
    assert (change.added, change.removed) == (0, 2)


def test_diff_is_truncated(env: tuple[Path, Path, changes.ChangeRecorder]) -> None:
    """diff 是给人扫一眼的，不是让人在浏览器里读完整个改动。"""
    _, work, recorder = env
    target = work / "big.txt"
    target.write_text("\n".join(f"old{i}" for i in range(500)), encoding="utf-8")

    changes.record_text(target, "\n".join(f"new{i}" for i in range(500)), kind="write")

    text = recorder.changes[0].unified()
    assert len(text.splitlines()) <= changes.MAX_DIFF_LINES + 1
    assert "还有" in text.splitlines()[-1]


def test_save_returns_none_without_changes(env: tuple[Path, Path, changes.ChangeRecorder]) -> None:
    _, _, recorder = env
    assert changes.save(recorder) is None


def test_restore_roundtrip(env: tuple[Path, Path, changes.ChangeRecorder]) -> None:
    """改了 → 存 → 还原 → 逐字回到改动前。"""
    home, work, recorder = env
    target = work / "a.txt"
    original = "第一行\n第二行\n第三行\n"
    target.write_text(original, encoding="utf-8")

    changes.record_text(target, "第一行\n改过了\n", kind="write")
    target.write_text("第一行\n改过了\n", encoding="utf-8")
    changes.save(recorder)

    restored, skipped = changes.restore(home, "c1", "run1", work)

    assert restored == ["a.txt"]
    assert skipped == []
    assert target.read_text(encoding="utf-8") == original


def test_restore_removes_created_and_recovers_deleted(
    env: tuple[Path, Path, changes.ChangeRecorder],
) -> None:
    """新建的删掉、删掉的写回来 —— 两个方向都要对。"""
    home, work, recorder = env
    created = work / "created.txt"
    deleted = work / "deleted.txt"
    deleted.write_text("别丢了我\n", encoding="utf-8")

    changes.record_text(created, "新的\n", kind="create")
    created.write_text("新的\n", encoding="utf-8")
    changes.record_text(deleted, None, kind="delete")
    deleted.unlink()
    changes.save(recorder)

    restored, _ = changes.restore(home, "c1", "run1", work)

    assert sorted(restored) == ["created.txt", "deleted.txt"]
    assert not created.exists()
    assert deleted.read_text(encoding="utf-8") == "别丢了我\n"


def test_restore_uses_earliest_before(env: tuple[Path, Path, changes.ChangeRecorder]) -> None:
    """同一个文件在一轮里被改两次，还原要回到**最早**那份，不是中间那次的结果。"""
    home, work, recorder = env
    target = work / "a.txt"
    target.write_text("v1\n", encoding="utf-8")

    changes.record_text(target, "v2\n", kind="write")
    target.write_text("v2\n", encoding="utf-8")
    changes.record_text(target, "v3\n", kind="write")
    target.write_text("v3\n", encoding="utf-8")
    changes.save(recorder)

    changes.restore(home, "c1", "run1", work)

    assert target.read_text(encoding="utf-8") == "v1\n"


def test_restore_refuses_path_outside_work_dir(
    env: tuple[Path, Path, changes.ChangeRecorder],
) -> None:
    """manifest 是磁盘上的文件 —— 它不能成为一条路径穿越的跳板。"""
    home, work, _ = env
    outside = work.parent / "evil.txt"
    outside.write_text("不该被动\n", encoding="utf-8")

    folder = home / "checkpoints" / "c1" / "run1"
    (folder / "files").mkdir(parents=True)
    manifest = [{"path": "../evil.txt", "kind": "write", "before_hash": "x", "binary": False}]
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    restored, skipped = changes.restore(home, "c1", "run1", work)

    assert restored == []
    assert skipped == ["../evil.txt"]
    assert outside.read_text(encoding="utf-8") == "不该被动\n"


def test_binary_is_recorded_but_not_restored(
    env: tuple[Path, Path, changes.ChangeRecorder], monkeypatch: pytest.MonkeyPatch
) -> None:
    """二进制/过大只记「改过」：存了也还原不回去，但界面该知道它动过。"""
    home, work, recorder = env
    target = work / "big.bin"
    target.write_bytes(b"x" * 64)

    monkeypatch.setattr(changes, "MAX_SNAPSHOT_BYTES", 16)
    changes.record_text(target, "whatever", kind="write")
    changes.save(recorder)

    change = recorder.changes[0]
    assert change.binary
    assert change.added == 0 and change.unified() == ""

    restored, skipped = changes.restore(home, "c1", "run1", work)
    assert restored == []
    assert skipped == ["big.bin"]


def test_forget_conversation_removes_checkpoints(
    env: tuple[Path, Path, changes.ChangeRecorder],
) -> None:
    """会话被永久删除时，它的快照也该一起走 —— 否则会一直占着磁盘。"""
    home, work, recorder = env
    target = work / "a.txt"
    target.write_text("x\n", encoding="utf-8")
    changes.record_text(target, "y\n", kind="write")
    changes.save(recorder)

    folder = home / "checkpoints" / "c1"
    assert folder.exists()

    changes.forget_conversation(home, "c1")

    assert not folder.exists()
