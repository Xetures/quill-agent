"""跨进程的文件锁与原子写。

为什么需要这两样：`data/` 下的 JSON 全是「读全量 → 改内存 → 整文件覆写」，
而**真的会并发**：后端把同步路由跑在线程池里，同一瞬间可能有两个请求各改一个键；
用户也可能在服务开着的时候自己跑脚本，或者再起一个实例。于是有两个真实的坑：

    - **丢失更新**：两个进程各自读到同一份基线，后写的那个把先写的覆盖掉。
      最坏的情况是「列了 10 个工具组，改完名字剩 9 个」。
    - **读到中间态**：`write_text` 是先截断再写。另一个进程恰好在这时读，会拿到
      空文件 —— 而它对空文件的处理是「退回默认值」，接着就可能把默认值写回去，
      用户的配置整份消失。

锁解决前者，原子替换解决后者。两者都要：只用锁的话，读方（不打锁）仍可能读到
写了一半的文件；只用原子替换的话，并发写的丢更新照样发生。

跨平台：POSIX 用 `fcntl.flock`，Windows 用 `msvcrt.locking`。两边都不可用时
**降级为不加锁**而不是报错 —— 少一层保护，总好过应用在这台机器上直接不可用。
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from pathlib import Path

if os.name == "nt":  # pragma: no cover - 按平台二选一
    import msvcrt
else:
    import fcntl

# 等锁的上限（秒）。持锁的只是一次「读 + 写」，正常在毫秒级；等这么久还拿不到，
# 多半是某个进程卡死了 —— 此时继续等不如放行，界面卡住比丢一次更新更糟
LOCK_TIMEOUT = 5.0
_POLL_INTERVAL = 0.05

# `os.replace` 撞上「文件正被占用」时的重试窗口（秒）。Windows 特有：那边不允许
# 替换一个正被打开的文件，而另一个进程恰好在这几十毫秒里读同一个文件是会发生的
REPLACE_TIMEOUT = 1.0


def _replace_with_retry(tmp: Path, path: Path) -> None:
    """替换目标文件；被占用时短暂重试，仍失败就把异常抛出去。"""
    deadline = time.monotonic() + REPLACE_TIMEOUT

    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # 只在 Windows 上会走到这里（POSIX 允许替换被打开的文件）。
            # 试几次通常就过了；一直不行说明有别的东西攥着它，那种情况得让人知道
            if time.monotonic() >= deadline:
                raise
            time.sleep(_POLL_INTERVAL)


def atomic_write_text(path: Path, text: str) -> None:
    """先写同目录下的临时文件，再原子替换过去。

    `os.replace` 在 POSIX 与 Windows 上都是原子的（同一分区内），所以别的进程读到
    的要么是旧内容、要么是新内容，不会读到写了一半的东西。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")

    try:
        tmp.write_text(text, encoding="utf-8")
        _replace_with_retry(tmp, path)
    finally:
        # 正常路径下 replace 之后 tmp 已经不存在了；异常时清掉残留
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _prepare(handle) -> None:
    """Windows 的 `msvcrt.locking` 要求锁的区域真实存在，先把文件撑成一个字节。"""
    if os.name != "nt":  # pragma: no cover - 分支按平台
        return

    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:  # pragma: no cover - 仅 Windows
        handle.write(b"\0")
        handle.flush()


def _acquire(handle) -> bool:
    """尝试拿到排他锁；超时返回 False（调用方降级为不加锁）。"""
    deadline = time.monotonic() + LOCK_TIMEOUT

    while True:
        try:
            if os.name == "nt":  # pragma: no cover - 仅 Windows
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(_POLL_INTERVAL)


def _release(handle) -> None:
    """释放锁。失败就算了 —— `close()` 也会让内核放掉它。"""
    try:
        if os.name == "nt":  # pragma: no cover - 仅 Windows
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """对 `path` 加排他锁（锁文件是它旁边的 `<名字>.lock`）。

    **读操作要放在锁里面**：先在锁外读到基线、再进锁里写回，等于没锁 ——
    两个进程会各自读到同一份基线，后写的把先写的覆盖掉。

    拿不到锁时放行（见模块开头），所以调用方不需要处理"锁失败"这种情况 ——
    出错路径上抛出的异常由业务层自己的校验负责。
    """
    lock_path = path.with_name(f"{path.name}.lock")

    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
    except OSError:
        # 连锁文件都建不出来（只读目录之类）：退回不加锁，业务照跑
        yield
        return

    try:
        _prepare(handle)
        if _acquire(handle):
            try:
                yield
            finally:
                _release(handle)
        else:
            yield
    finally:
        handle.close()
