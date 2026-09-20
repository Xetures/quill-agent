"""文件锁与原子写：并发写同一份 JSON 时不能丢数据。

这两个机制的动机很具体：`data/` 下的 JSON 都是「读全量 → 改内存 → 整文件覆写」，
而后端会把请求并发地跑在线程池里（可能再叠上用户自己的脚本、或第二个实例）。
没有锁会丢更新，没有原子替换会读到写了一半的空文件 —— 而空文件会被当成
「用户还没配过」，接着把默认值写回去，配置整份消失。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from quill_agent.locking import atomic_write_text, file_lock
from quill_agent.preferences import PreferenceStore


def test_atomic_write_leaves_a_complete_file(tmp_path: Path) -> None:
    """写完立刻能读到完整内容，且不留临时文件。"""
    path = tmp_path / "x.json"
    atomic_write_text(path, '{"a": 1}')

    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}
    assert [item.name for item in tmp_path.iterdir()] == ["x.json"]


def test_atomic_write_creates_missing_parents(tmp_path: Path) -> None:
    """父目录不存在时自己建 —— 调用方不该为落盘再关心一次目录。"""
    path = tmp_path / "deep" / "nested" / "x.json"
    atomic_write_text(path, "{}")

    assert path.is_file()


def test_atomic_write_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    atomic_write_text(path, "first")
    atomic_write_text(path, "second")

    assert path.read_text(encoding="utf-8") == "second"


def test_file_lock_is_exclusive(tmp_path: Path) -> None:
    """同一把锁上，临界区里同时最多只有一个线程。"""
    path = tmp_path / "x.json"
    inside = 0
    peak = 0
    guard = threading.Lock()

    def worker() -> None:
        nonlocal inside, peak
        for _ in range(5):
            with file_lock(path):
                with guard:
                    inside += 1
                    peak = max(peak, inside)
                time.sleep(0.01)
                with guard:
                    inside -= 1

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert peak == 1


def test_concurrent_writes_do_not_lose_keys(tmp_path: Path) -> None:
    """两边同时写偏好时，双方的键都要留下来。

    这就是加锁要解决的原始问题：各自读到同一份基线、后写的把先写的整份覆盖掉，
    于是「这边保存的模型选择」和「那边保存的草稿」互相抹掉。
    """
    path = tmp_path / "preferences.json"
    store = PreferenceStore(path)

    def writer(prefix: str) -> None:
        for index in range(20):
            store.set(f"{prefix}-{index}", "1")

    threads = [threading.Thread(target=writer, args=(prefix,)) for prefix in ("left", "right")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) == 40
