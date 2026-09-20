"""执行工具：跑得动、拦得住、掐得掉、截得短。

这个工具是唯一绕过 PathGuard 的，所以用例一半在验证它的行为，
一半在验证那几条「防手滑」的拦截确实生效。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from quill_agent.tools import shell


@pytest.fixture(autouse=True)
def _work_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """把工作目录钉在临时目录上 —— 命令的 cwd 由它决定。"""
    monkeypatch.setattr(shell, "current_work_dir", lambda: tmp_path)
    return tmp_path


def _py(script: str) -> str:
    """把一段 Python 拼成命令行。用解释器本身而不是 echo / seq 这类外部命令，
    是为了让用例不依赖具体平台上有哪些 shell 工具。"""
    return f'"{sys.executable}" -c "{script}"'


# ---------------------------------------------------------------------------
# 正常执行
# ---------------------------------------------------------------------------


def test_returns_output_and_exit_code() -> None:
    out = shell.run_command(_py("print(1 + 1)"))

    assert "2" in out
    assert "退出码：0（成功）" in out


def test_failure_reports_the_exit_code() -> None:
    out = shell.run_command(_py("raise SystemExit(3)"))

    assert "退出码：3（失败）" in out


def test_command_runs_in_the_work_dir(tmp_path: Path) -> None:
    out = shell.run_command(_py("import os; print(os.getcwd())"))

    assert str(tmp_path) in out


def test_stderr_is_merged_into_the_output() -> None:
    """构建日志的先后顺序本身就是信息，所以 stderr 并进 stdout。"""
    out = shell.run_command(_py("import sys; sys.stderr.write('boom')"))

    assert "boom" in out


# ---------------------------------------------------------------------------
# 输出截断：必须边读边丢，不能读完再截
# ---------------------------------------------------------------------------


def test_terminate_uses_taskkill_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows 上清理进程走 taskkill，不碰 POSIX 专有的进程组接口。

    回归测试：原先这里写死 `os.killpg` + `signal.SIGKILL`，而 `os.killpg` /
    `os.getpgid` / `signal.SIGKILL` 在 Windows 上**都不存在** —— 那边只要有一条
    命令超时，就会抛 AttributeError，连「命令超时，已终止」这句提示都拿不到。
    """
    calls: list[list[str]] = []

    class _FakeProcess:
        pid = 4242

        @staticmethod
        def poll() -> None:
            return None  # 还在跑

        @staticmethod
        def wait(timeout: float = 0) -> int:
            return 0

        @staticmethod
        def kill() -> None:
            raise AssertionError("不该退回到 kill：taskkill 是可用的")

    monkeypatch.setattr(shell, "_POSIX", False)
    monkeypatch.setattr(shell.subprocess, "run", lambda argv, **kwargs: calls.append(argv))

    shell._terminate(_FakeProcess())

    assert calls and calls[0][:2] == ["taskkill", "/PID"]
    assert "/T" in calls[0]
    assert "/F" in calls[0]


def test_terminate_skips_a_process_that_already_exited(monkeypatch) -> None:
    """已经自己结束的进程不再发信号 —— 避开 pid 复用时误杀别的进程。"""
    touched: list[str] = []

    class _DeadProcess:
        pid = 4242

        @staticmethod
        def poll() -> int:
            return 0

        @staticmethod
        def kill() -> None:
            touched.append("kill")

    monkeypatch.setattr(
        shell, "_terminate_group", lambda process: touched.append("group")
    )

    shell._terminate(_DeadProcess())

    assert touched == []


def test_bounded_sink_caps_the_total_size() -> None:
    """收集器本身有界：喂进去多少都不该在内存里越攒越多。"""
    sink = shell._BoundedSink(limit=100)

    for _ in range(1000):
        sink.feed("x" * 50)

    text = sink.text()

    assert "输出过长" in text
    # 头部 50 + 尾部 50 再加一句提示，远小于喂进去的 50000
    assert len(text) < 200


def test_long_output_keeps_both_ends() -> None:
    """真的跑一条大输出命令：开头和结尾都要在。

    这正是 `communicate()` 做不到的 —— 它会把整段输出先留在内存里再截断，
    一条 `yes` 就能把线程堆顶爆（见 `_BoundedSink` 的说明）。
    """
    out = shell.run_command(_py("print('HEAD'); print('x' * 200000); print('TAIL')"))

    assert "HEAD" in out
    assert "TAIL" in out
    assert "输出过长" in out


def test_empty_command_is_rejected() -> None:
    assert shell.run_command("   ") == "命令不能为空。"


def test_zero_timeout_is_clamped_instead_of_failing() -> None:
    """上限 / 下限都要夹住，否则模型传 0 或一个天文数字都能把这一轮搞坏。"""
    assert "退出码：0" in shell.run_command(_py("print('ok')"), timeout=0)
    assert "退出码：0" in shell.run_command(_py("print('ok')"), timeout=99999)


# ---------------------------------------------------------------------------
# 超时与进程清理
# ---------------------------------------------------------------------------


def test_timeout_actually_terminates_the_command() -> None:
    started = time.monotonic()
    out = shell.run_command(_py("import time; time.sleep(30)"), timeout=1)
    elapsed = time.monotonic() - started

    assert "超时" in out
    # 关键：是把它掐了，而不是等它自己跑完
    assert elapsed < 15


def test_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    """只杀 shell 的话，它派生的子进程会变成孤儿继续跑，下一轮再来抢端口、抢文件。

    脚本写成文件再执行，而不是塞进 `-c "..."`：引号套引号在两种 shell 下行为不一样。
    """
    marker = tmp_path / "child.pid"
    script = tmp_path / "spawn.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    assert "超时" in shell.run_command(f'"{sys.executable}" {script.name}', timeout=2)
    assert marker.is_file(), "子进程没起来，这条用例就没有意义"

    child_pid = int(marker.read_text())
    for _ in range(20):  # 进程退出、被 init 收尸需要一点点时间
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)

    pytest.fail("派生的子进程还活着：超时只杀掉了 shell 本身，没有杀进程组")


# ---------------------------------------------------------------------------
# 防手滑（不是安全边界，但必须真的生效）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["vim", "nano 笔记.md", "less README.md", "top", "ssh 主机"])
def test_interactive_commands_are_rejected(command: str) -> None:
    assert "交互式" in shell.run_command(command)


def test_bare_interpreter_is_rejected_but_scripts_are_allowed() -> None:
    """`python` 不带参数是 REPL 要拦，带上脚本就是正常用法要放行。"""
    assert "交互式" in shell.run_command(sys.executable)
    assert "hello" in shell.run_command(_py("print('hello')"))


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -fr / ",
        "sudo apt install x",
        "chmod -R 777 /",
        "dd if=/dev/zero of=/dev/sda",
        "mkfs.ext4 /dev/sda1",
    ],
)
def test_dangerous_commands_are_flagged(command: str) -> None:
    assert shell._danger_reason(command) is not None


@pytest.mark.parametrize("command", ["rm -rf /", "sudo apt install x", "mkfs.ext4 /dev/sda1"])
def test_dangerous_command_is_refused_when_nobody_can_answer(command: str) -> None:
    """**判定危险 → 弹给用户 → 问不到就不执行。**

    这里没有通道（测试进程里没有运行中的对话），所以必须落回「拒绝」。
    底线是：一条被判为危险的命令，在没人点头的情况下执行，是这个机制最坏的失败方式。
    """
    out = shell.run_command(command)

    assert "没能问到用户" in out
    assert "原因：" in out


def test_dangerous_command_runs_once_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    """用户点了「允许」就照常跑 —— 判定危险的是我们，能不能承担风险的是用户。"""
    monkeypatch.setattr(shell, "_danger_reason", lambda _: "测试用：假装它危险")
    monkeypatch.setattr(shell.interaction, "confirm", lambda **_: True)

    out = shell.run_command(_py("print('ran')"))

    assert "ran" in out
    assert "退出码：0" in out


def test_rejected_command_is_not_run_and_retry_is_discouraged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """被拒绝之后，模型很擅长换个写法再试一遍 —— 那等于绕开用户刚做出的决定。"""
    monkeypatch.setattr(shell, "_danger_reason", lambda _: "测试用：假装它危险")
    monkeypatch.setattr(shell.interaction, "confirm", lambda **_: False)

    out = shell.run_command(_py("print('ran')"))

    assert "ran" not in out
    assert "用户拒绝" in out
    assert "不要换个写法" in out


def test_ordinary_rm_is_not_flagged() -> None:
    """别把正常的删除也当成危险 —— 拦得太宽，每条命令都弹窗，用户就闭着眼点了。"""
    assert shell._danger_reason("rm -rf build/") is None
    assert shell._danger_reason("git commit -m 'fix'") is None
    assert shell._danger_reason("pytest -q") is None


# ---------------------------------------------------------------------------
# 输出处理
# ---------------------------------------------------------------------------


def test_long_output_is_clipped_in_the_middle() -> None:
    """砍中间而不是砍尾巴：头几行是它在干什么，最后几十行是结论和报错。"""
    script = "print(chr(10).join(str(i) for i in range(500)))"
    out = shell.run_command(_py(script))

    assert "中间省略" in out
    assert "\n0\n" in out  # 头部还在
    assert "499" in out  # 尾部还在


def test_short_output_is_untouched() -> None:
    assert "中间省略" not in shell.run_command(_py("print('short')"))


def test_empty_output_says_so() -> None:
    """什么都不打印的命令返回一个空块，模型会以为工具没跑成功。"""
    assert "（命令没有输出）" in shell.run_command(_py("pass"))


def test_ansi_escapes_are_stripped() -> None:
    """有些工具不管有没有 TTY 都上色，那些转义序列进了上下文纯属噪声。"""
    assert shell._clean("\x1b[31mred\x1b[0m") == "red"
    assert shell._clean("a\r\nb\rc") == "a\nb\nc"
