"""沙箱：argv 拼得对、没后端时拒得干脆、有 bwrap 时真的关得住。

分三层：
    1. **纯 argv 断言** —— 不依赖本机装没装 bwrap，任何平台都能跑；
    2. **拒绝路径** —— 配了沙箱却没有后端时必须拒绝执行，而不是偷偷裸跑；
    3. **真实隔离** —— 只在装了可用 bubblewrap 的 Linux 上跑，其余平台 skip。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from quill_agent import config, sandbox
from quill_agent.tools import shell

# 本机能真跑沙箱吗？跑不了就让第三层用例整体跳过，而不是伪造一个通过
_HAS_BWRAP = sys.platform.startswith("linux") and shutil.which("bwrap") is not None
_needs_bwrap = pytest.mark.skipif(
    not _HAS_BWRAP, reason="需要 Linux + bubblewrap 才能验证真实的隔离效果"
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """配置和 bwrap 探测都带缓存，每个用例前后清一次，免得互相串。"""
    config.get_settings.cache_clear()
    sandbox._bwrap_probe.cache_clear()
    yield
    config.get_settings.cache_clear()
    sandbox._bwrap_probe.cache_clear()


@pytest.fixture
def fake_bwrap(monkeypatch: pytest.MonkeyPatch) -> None:
    """假装「这是一台装好了 bubblewrap 的 Linux」。

    把平台也一起改掉，argv 构造这部分用例才能在 macOS / Windows 上跑 —— 它恰恰是最
    容易写错的地方（少一个 --ro-bind 就是一个洞），不该只在 CI 的 Linux 上才被验到。
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sandbox, "_bwrap_probe", lambda: (True, ""))


@pytest.fixture
def fake_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """造一个假家目录，用来断言敏感目录确实被盖住了。"""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".ssh").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows 上的对应变量
    return home


def _policy(mode: str, work_dir: Path, *, network: bool = False) -> sandbox.SandboxPolicy:
    return sandbox.SandboxPolicy(
        mode=sandbox.SandboxMode(mode),
        work_dir=work_dir,
        allow_network=network,
    )


def _value_of(argv: list[str], flag: str) -> str:
    """取 `--flag` 后面紧跟的那个值（flag 不存在时用例直接失败）。"""
    return argv[argv.index(flag) + 1]


# ---------------------------------------------------------------------------
# argv 构造：off
# ---------------------------------------------------------------------------


def test_off_mode_does_not_wrap_anything(tmp_path: Path) -> None:
    """off 就是既有行为：命令原样交给平台 shell，不套任何前缀。"""
    argv = sandbox.build_argv("echo hi", _policy("off", tmp_path))

    assert argv[-1] == "echo hi"
    assert "bwrap" not in argv


# ---------------------------------------------------------------------------
# argv 构造：workspace-write
# ---------------------------------------------------------------------------


def test_whole_filesystem_is_read_only_first(tmp_path: Path, fake_bwrap: None) -> None:
    """白名单的起点：整个 / 先只读挂进来，再逐条开口子。"""
    argv = sandbox.build_argv("echo hi", _policy("workspace-write", tmp_path))

    assert argv[0].endswith("bwrap")
    assert argv[argv.index("--ro-bind") + 1] == "/"


def test_work_dir_is_rebound_writable_after_the_read_only_bind(
    tmp_path: Path, fake_bwrap: None
) -> None:
    """工作目录要「抬」成可写，而且**必须排在只读挂载之后**，否则会被盖回只读。"""
    argv = sandbox.build_argv("echo hi", _policy("workspace-write", tmp_path))

    assert _value_of(argv, "--bind") == str(tmp_path.resolve())
    assert argv.index("--bind") > argv.index("--ro-bind")


def test_read_only_mode_never_grants_a_writable_path(tmp_path: Path, fake_bwrap: None) -> None:
    argv = sandbox.build_argv("echo hi", _policy("read-only", tmp_path))

    assert "--bind" not in argv
    assert argv[argv.index("--ro-bind") + 1] == "/"


def test_network_is_cut_by_default(tmp_path: Path, fake_bwrap: None) -> None:
    argv = sandbox.build_argv("curl example.com", _policy("workspace-write", tmp_path))

    assert "--unshare-net" in argv


def test_network_can_be_allowed_explicitly(tmp_path: Path, fake_bwrap: None) -> None:
    """装依赖那一下确实需要网 —— 但必须是显式配的，不能是默认。"""
    argv = sandbox.build_argv(
        "pip install x", _policy("workspace-write", tmp_path, network=True)
    )

    assert "--unshare-net" not in argv


def test_home_secrets_are_masked(tmp_path: Path, fake_bwrap: None, fake_home: Path) -> None:
    """~/.ssh 这类目录在沙箱里根本不存在 —— 不是靠权限位，是让它看不见。"""
    argv = sandbox.build_argv("cat ~/.ssh/id_rsa", _policy("workspace-write", tmp_path))

    assert str(fake_home / ".ssh") in argv
    # 盖住它的那个空目录紧跟在 --tmpfs 后面
    assert _value_of(argv, "--tmpfs") in {str(fake_home / ".ssh"), "/tmp"}


def test_missing_secret_dirs_are_not_masked(
    tmp_path: Path, fake_bwrap: None, fake_home: Path
) -> None:
    """没装的（~/.aws）不必多此一举 —— 多一个挂载就多一分出错的可能。"""
    argv = sandbox.build_argv("echo hi", _policy("workspace-write", tmp_path))

    assert str(fake_home / ".aws") not in argv


def test_work_dir_inside_a_secret_dir_is_not_masked(
    tmp_path: Path, fake_bwrap: None, fake_home: Path
) -> None:
    """工作目录恰好落在 ~/.ssh 里时不能盖它 —— 否则用户要改的东西一起没了。"""
    work_dir = fake_home / ".ssh"
    argv = sandbox.build_argv("echo hi", _policy("workspace-write", work_dir))

    assert str(work_dir) not in argv[argv.index("--tmpfs") : argv.index("--bind")]


def test_command_is_handed_to_a_shell_after_the_separator(
    tmp_path: Path, fake_bwrap: None
) -> None:
    """命令必须落在 `--` 之后、交给 shell -c 执行；放错位置就等于把命令当成了选项。"""
    argv = sandbox.build_argv("echo hi && echo there", _policy("workspace-write", tmp_path))

    assert argv[-1] == "echo hi && echo there"
    assert argv.index("--") < argv.index("/bin/sh")
    assert argv[argv.index("/bin/sh") + 1] == "-c"


def test_pid_namespace_is_isolated(tmp_path: Path, fake_bwrap: None) -> None:
    """看不见也就杀不掉外面别的进程。"""
    argv = sandbox.build_argv("echo hi", _policy("workspace-write", tmp_path))

    assert "--unshare-pid" in argv
    assert "--die-with-parent" in argv


# ---------------------------------------------------------------------------
# 没有后端时：拒绝，而不是裸跑
# ---------------------------------------------------------------------------


def test_missing_backend_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sandbox, "_bwrap_probe", lambda: (False, "没有找到 bubblewrap（bwrap）"))

    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.build_argv("echo hi", _policy("workspace-write", tmp_path))


def test_run_command_refuses_instead_of_running_unsandboxed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """**底线用例。** 配了沙箱却没有后端时，命令绝不能被执行 —— 那是最坏的失败方式。"""
    monkeypatch.setattr(shell, "current_work_dir", lambda: tmp_path)
    monkeypatch.setenv("SANDBOX_MODE", "workspace-write")
    config.get_settings.cache_clear()
    monkeypatch.setattr(sandbox, "_bwrap_probe", lambda: (False, "没有找到 bubblewrap（bwrap）"))

    out = shell.run_command(f'"{sys.executable}" -c "print(\'ran\')"')

    assert "ran" not in out
    assert "没有执行" in out
    assert "SANDBOX_MODE" in out


def test_start_process_refuses_instead_of_running_unsandboxed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """后台进程活得比这一轮还长，漏掉它比漏掉一条普通命令更糟。"""
    monkeypatch.setattr(shell, "current_work_dir", lambda: tmp_path)
    monkeypatch.setenv("SANDBOX_MODE", "workspace-write")
    config.get_settings.cache_clear()
    monkeypatch.setattr(sandbox, "_bwrap_probe", lambda: (False, "没有找到 bubblewrap（bwrap）"))

    out = shell.start_process("sleep 30")

    assert "没有执行" in out
    assert shell.list_processes() == "当前没有后台进程。"


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def test_default_mode_is_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """默认必须是 off：Phase 1 只有 Linux 后端，默认开着会让别的平台当场不可用。"""
    monkeypatch.delenv("SANDBOX_MODE", raising=False)

    policy = sandbox.policy_for(tmp_path)

    assert policy.mode is sandbox.SandboxMode.OFF
    assert not policy.active


def test_mode_and_network_come_from_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "read-only")
    monkeypatch.setenv("SANDBOX_NETWORK", "true")
    config.get_settings.cache_clear()

    policy = sandbox.policy_for(tmp_path)

    assert policy.mode is sandbox.SandboxMode.READ_ONLY
    assert policy.allow_network is True


def test_typo_in_the_mode_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """写错的值必须当场报错。

    悄悄降级成 off 是危险的方向 —— 用户以为开着沙箱，实际在裸跑。
    """
    monkeypatch.setenv("SANDBOX_MODE", "workspace-writ")
    config.get_settings.cache_clear()

    with pytest.raises(ValueError):
        config.get_settings()


def test_startup_note_says_what_is_actually_in_effect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sandbox, "policy_for", lambda _: _policy("off", tmp_path))
    assert "未启用" in sandbox.startup_note()

    monkeypatch.setattr(sandbox, "policy_for", lambda _: _policy("workspace-write", tmp_path))
    monkeypatch.setattr(sandbox, "available", lambda: False)
    note = sandbox.startup_note()
    assert "没有可用后端" in note and "拒绝" in note


def test_describe_mentions_network_state(tmp_path: Path) -> None:
    assert "关闭" in _policy("off", tmp_path).describe()
    assert "断网" in _policy("workspace-write", tmp_path).describe()
    assert "联网放行" in _policy("workspace-write", tmp_path, network=True).describe()


# ---------------------------------------------------------------------------
# 真实隔离（需要 Linux + bubblewrap）
# ---------------------------------------------------------------------------


@_needs_bwrap
def test_writes_inside_the_work_dir_succeed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """沙箱不是「什么都不让干」—— 工作目录内照样正常写。"""
    monkeypatch.setattr(shell, "current_work_dir", lambda: tmp_path)
    monkeypatch.setenv("SANDBOX_MODE", "workspace-write")
    config.get_settings.cache_clear()

    out = shell.run_command("touch made.txt", timeout=20)

    assert "退出码：0" in out, out
    assert (tmp_path / "made.txt").is_file()


@_needs_bwrap
def test_writes_outside_the_work_dir_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """这就是沙箱要拦的东西：工作目录之外的写入在物理上失败。"""
    monkeypatch.setattr(shell, "current_work_dir", lambda: tmp_path)
    monkeypatch.setenv("SANDBOX_MODE", "workspace-write")
    config.get_settings.cache_clear()

    victim = tmp_path.parent / "outside.txt"
    out = shell.run_command(f"touch {victim}", timeout=20)

    assert "退出码：0" not in out
    assert not victim.exists()


@_needs_bwrap
def test_network_is_unreachable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(shell, "current_work_dir", lambda: tmp_path)
    monkeypatch.setenv("SANDBOX_MODE", "workspace-write")
    config.get_settings.cache_clear()

    script = (
        "import socket;"
        "s = socket.socket();"
        "s.settimeout(3);"
        "print('connected' if s.connect_ex(('1.1.1.1', 53)) == 0 else 'blocked')"
    )
    out = shell.run_command(f'"{sys.executable}" -c "{script}"', timeout=30)

    assert "blocked" in out


@_needs_bwrap
def test_probe_actually_works_here() -> None:
    """bwrap 在 PATH 里不等于它跑得起来（AppArmor 会挡 user namespace）。"""
    available, reason = sandbox._bwrap_probe()

    assert available, reason
    assert subprocess.run(["true"]).returncode == 0
