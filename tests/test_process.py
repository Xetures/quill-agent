"""后台进程工具：起得来、读得到增量、停得干净、拦得住。

和 `run_command` 共用同一套「防手滑」判断，所以这里也顺带验证那道闸对后台进程生效。
命令一律用当前解释器拼，不依赖平台上有哪些外部命令。
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import pytest

from quill_agent.tools import shell


def _py(script: str) -> str:
    return f'"{sys.executable}" -c "{script}"'


def _pid(out: str) -> str:
    """从「已启动后台进程 pN…」里取出进程号。"""
    match = re.search(r"p\d+", out)
    assert match is not None, out
    return match.group(0)


# 一个打印一行就长期挂着的进程：用来模拟 dev server
_LONG_RUNNING = _py("import time; print('ready', flush=True); time.sleep(30)")


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """钉住工作目录，并在每个用例后把后台进程清空 —— 免得留下的进程互相干扰。"""
    monkeypatch.setattr(shell, "current_work_dir", lambda: tmp_path)
    yield
    with shell._processes_lock:
        items = list(shell._processes.values())
        shell._processes.clear()
    for item in items:
        if item.alive():
            item.stop()


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------


def test_start_returns_process_id() -> None:
    out = shell.start_process(_LONG_RUNNING)

    assert "已启动后台进程 p" in out
    assert "process_output" in out


def test_started_process_appears_in_list() -> None:
    shell.start_process(_LONG_RUNNING)

    listed = shell.list_processes()
    assert "当前后台进程" in listed
    assert "运行中" in listed


def test_empty_command_is_rejected() -> None:
    assert shell.start_process("   ") == "命令不能为空。"


def test_immediately_exiting_command_is_not_kept() -> None:
    """命令拼错时会瞬间退出，必须当场告诉模型，而不是留一个死掉的 id。"""
    out = shell.start_process(_py("print('boom')"))

    assert "立刻退出" in out
    assert "boom" in out
    assert shell.list_processes() == "当前没有后台进程。"


# ---------------------------------------------------------------------------
# 读取输出
# ---------------------------------------------------------------------------


def test_output_is_readable() -> None:
    pid = _pid(shell.start_process(_LONG_RUNNING))

    out = shell.process_output(pid)
    assert "ready" in out
    assert "运行中" in out


def test_output_is_incremental() -> None:
    """第二次取只给新产生的部分，不重复已经给过的。"""
    pid = _pid(shell.start_process(_LONG_RUNNING))

    assert "ready" in shell.process_output(pid)
    assert "没有新输出" in shell.process_output(pid)


def test_output_reports_exit_code_once_it_stops() -> None:
    """进程自己退出后，取输出时要能看出它退了、退了多少。"""
    pid = _pid(shell.start_process(_py("import time; time.sleep(1.0)")))

    time.sleep(1.5)
    assert "已退出（退出码 0）" in shell.process_output(pid)


def test_unknown_process_id_is_reported() -> None:
    assert "没有这个后台进程" in shell.process_output("p999")
    assert "没有这个后台进程" in shell.stop_process("p999")


# ---------------------------------------------------------------------------
# 停止
# ---------------------------------------------------------------------------


def test_stop_removes_it_from_the_table() -> None:
    pid = _pid(shell.start_process(_LONG_RUNNING))

    out = shell.stop_process(pid)
    assert "已停止" in out
    assert shell.list_processes() == "当前没有后台进程。"


def test_stop_kills_its_child_processes(tmp_path: Path) -> None:
    """只杀 shell 本身的话，它派生的子进程会变成孤儿，继续占着端口。"""
    marker = tmp_path / "child.pid"
    script = tmp_path / "spawn.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    pid = _pid(shell.start_process(f'"{sys.executable}" {script.name}'))

    # 等子进程把 pid 写出来，否则这条用例没有意义
    for _ in range(50):
        if marker.is_file():
            break
        time.sleep(0.1)
    assert marker.is_file(), "子进程没起来"

    shell.stop_process(pid)

    child_pid = int(marker.read_text())
    for _ in range(20):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)

    pytest.fail("派生的子进程还活着：停止只杀掉了 shell，没有杀进程组")


# ---------------------------------------------------------------------------
# 上限
# ---------------------------------------------------------------------------


def test_process_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shell, "MAX_PROCESSES", 1)
    shell.start_process(_LONG_RUNNING)

    out = shell.start_process(_LONG_RUNNING)
    assert "已达上限" in out


# ---------------------------------------------------------------------------
# 防手滑（和 run_command 共用同一道闸）
# ---------------------------------------------------------------------------


def test_interactive_command_is_rejected() -> None:
    assert "交互式" in shell.start_process("vim")


def test_dangerous_command_is_refused_when_nobody_can_answer() -> None:
    out = shell.start_process("sudo apt install x")

    assert "没能问到用户" in out
