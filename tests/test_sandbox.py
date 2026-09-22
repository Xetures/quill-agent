"""沙箱：argv 拼得对、没后端时拒得干脆、有后端时真的关得住。

分三层：
    1. **纯 argv 断言** —— 不依赖本机装没装后端，任何平台都能跑；
       两个后端的 profile / 参数各测一遍（拼错一个字母就是一个洞）；
    2. **拒绝路径** —— 配了沙箱却没有后端时必须拒绝执行，而不是偷偷裸跑；
    3. **真实隔离** —— 只在真有可用后端的机器上跑，其余平台 skip。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from server import stores
from server.schemas import SandboxPayload

from quill_agent import config, sandbox
from quill_agent.preferences import SANDBOX_MODE_KEY, SANDBOX_NETWORK_KEY, PreferenceStore
from quill_agent.tools import shell

# 本机能真跑沙箱吗？跑不了就让第三层用例整体跳过，而不是伪造一个通过。
# 两个后端都算 —— Linux 的 bubblewrap、macOS 的 Seatbelt。
_HAS_BWRAP = sys.platform.startswith("linux") and shutil.which("bwrap") is not None
_HAS_SEATBELT = sys.platform == "darwin" and shutil.which("sandbox-exec") is not None
_HAS_BACKEND = _HAS_BWRAP or _HAS_SEATBELT
_needs_bwrap = pytest.mark.skipif(
    not _HAS_BWRAP, reason="需要 Linux + bubblewrap 才能验证真实的隔离效果"
)
_needs_sandbox = pytest.mark.skipif(
    not _HAS_BACKEND, reason="需要可用的沙箱后端（Linux bubblewrap / macOS Seatbelt）"
)
_needs_seatbelt = pytest.mark.skipif(
    not _HAS_SEATBELT, reason="需要 macOS 才能验证 Seatbelt 的探针"
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """配置和两个探测都带缓存，每个用例前后清一次，免得互相串。"""
    config.get_settings.cache_clear()
    sandbox._bwrap_probe.cache_clear()
    sandbox._seatbelt_probe.cache_clear()
    yield
    config.get_settings.cache_clear()
    sandbox._bwrap_probe.cache_clear()
    sandbox._seatbelt_probe.cache_clear()


@pytest.fixture
def fake_bwrap(monkeypatch: pytest.MonkeyPatch) -> None:
    """假装「这是一台装好了 bubblewrap 的 Linux」。

    把平台也一起改掉，argv 构造这部分用例才能在 macOS / Windows 上跑 —— 它恰恰是最
    容易写错的地方（少一个 --ro-bind 就是一个洞），不该只在 CI 的 Linux 上才被验到。
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sandbox, "_bwrap_probe", lambda: (True, ""))


@pytest.fixture
def fake_seatbelt(monkeypatch: pytest.MonkeyPatch) -> None:
    """假装「这是一台 macOS」，且 sandbox-exec 可用。

    理由同 `fake_bwrap`：profile 里少一条 deny 就是一个洞，而它同样只在 macOS 上才
    真跑得起来 —— 不能等到那时才第一次被人看。
    """
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sandbox, "_seatbelt_probe", lambda: (True, ""))


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


def test_off_mode_preserves_direct_process_argv(tmp_path: Path) -> None:
    argv = sandbox.build_process_argv(["python", "-m", "fake_mcp"], _policy("off", tmp_path))

    assert argv == ["python", "-m", "fake_mcp"]


# ---------------------------------------------------------------------------
# argv 构造：workspace-write
# ---------------------------------------------------------------------------


def test_direct_process_is_wrapped_without_shell(tmp_path: Path, fake_bwrap: None) -> None:
    argv = sandbox.build_process_argv(
        ["node", "server.js", "--stdio"], _policy("read-only", tmp_path)
    )

    assert argv[-3:] == ["node", "server.js", "--stdio"]
    assert "/bin/sh" not in argv


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


def test_seatbelt_denies_by_default(tmp_path: Path, fake_seatbelt: None) -> None:
    """从「全关」起步，再逐条开口子 —— 和 bwrap 的 `--ro-bind / /` 一个思路。

    反过来的写法（先 `allow default` 再 deny 几条）一旦漏掉哪条就是个洞，
    而漏掉的是哪条没人看得出来。
    """
    argv = sandbox.build_argv("echo hi", _policy("read-only", tmp_path))
    profile = _value_of(argv, "-p")

    assert argv[0] == "sandbox-exec"
    assert "(deny default)" in profile
    assert "(allow file-read*)" in profile
    # 只读档绝不能出现「工作目录可写」那条
    assert f'(allow file-write* (subpath "{tmp_path}"))' not in profile


def test_seatbelt_grants_only_the_work_dir(tmp_path: Path, fake_seatbelt: None) -> None:
    """工作区可写档：只多出「`work_dir` 可写」这一条。"""
    argv = sandbox.build_argv("echo hi", _policy("workspace-write", tmp_path))

    assert f'(allow file-write* (subpath "{tmp_path}"))' in _value_of(argv, "-p")


def test_seatbelt_masks_home_secrets(
    tmp_path: Path, fake_seatbelt: None, fake_home: Path
) -> None:
    """密钥目录排在 `file-read*` 之后 —— 靠「后写覆盖先写」把它们收回去。

    顺序是这段的关键：Seatbelt 是**后写的规则生效**，把 deny 写在前面等于没写。
    """
    profile = _value_of(sandbox.build_argv("echo hi", _policy("read-only", tmp_path)), "-p")

    assert f'(deny file-read* (subpath "{fake_home / ".ssh"}"))' in profile
    assert profile.index("(allow file-read*)") < profile.index(str(fake_home / ".ssh"))


def test_seatbelt_work_dir_inside_a_secret_dir_is_not_masked(
    tmp_path: Path, fake_seatbelt: None, fake_home: Path
) -> None:
    """工作目录恰好落在密钥目录里时不要盖它 —— 那会把用户要改的东西一起封掉。"""
    inside = fake_home / ".ssh"
    profile = _value_of(sandbox.build_argv("echo hi", _policy("read-only", inside)), "-p")

    assert f'(deny file-read* (subpath "{inside}"))' not in profile


def test_seatbelt_cuts_network_by_default(tmp_path: Path, fake_seatbelt: None) -> None:
    assert "(allow network*)" not in _value_of(
        sandbox.build_argv("echo hi", _policy("read-only", tmp_path)), "-p"
    )


def test_seatbelt_allows_network_on_request(tmp_path: Path, fake_seatbelt: None) -> None:
    profile = _value_of(
        sandbox.build_argv("echo hi", _policy("read-only", tmp_path, network=True)), "-p"
    )

    assert "(allow network*)" in profile


def test_seatbelt_keeps_the_devices_writable(tmp_path: Path, fake_seatbelt: None) -> None:
    """`2>/dev/null` 到处都是，不给的话大量命令直接失败。

    它属于「写设备文件」，不在 `file-write*` 的通配范围里 —— 必须单独开口子。
    """
    profile = _value_of(sandbox.build_argv("echo hi", _policy("read-only", tmp_path)), "-p")

    assert "file-write-data" in profile
    assert '"/dev/null"' in profile


def test_seatbelt_root_work_dir_does_not_unlock_everything(fake_seatbelt: None) -> None:
    """`WORK_DIR=/` 是错配置：退化成只读，而不是「全盘可写」。

    和 bwrap 那条同一个判断 —— 出错时倒向安全的方向。
    """
    profile = _value_of(
        sandbox.build_argv("echo hi", _policy("workspace-write", Path("/"))), "-p"
    )

    assert '(allow file-write* (subpath "/"))' not in profile


def test_seatbelt_command_goes_through_a_shell(tmp_path: Path, fake_seatbelt: None) -> None:
    """`--` 之后才是执行壳和命令本身。"""
    argv = sandbox.build_argv("echo hi", _policy("read-only", tmp_path))

    assert argv[-4:] == ["--", "/bin/sh", "-c", "echo hi"]


def test_pid_namespace_is_isolated(tmp_path: Path, fake_bwrap: None) -> None:
    """看不见也就杀不掉外面别的进程。"""
    argv = sandbox.build_argv("echo hi", _policy("workspace-write", tmp_path))

    assert "--unshare-pid" in argv
    assert "--die-with-parent" in argv


# ---------------------------------------------------------------------------
# 没有后端时：拒绝，而不是裸跑
# ---------------------------------------------------------------------------


@pytest.fixture
def no_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """假装这台机器**一个沙箱后端都没有**。

    两个探测都要改：只改 bwrap 的话，在 macOS 上会落到 Seatbelt 分支 —— 而那条路在
    这台机器上是真能用的，用例就验不到「没后端时拒绝」这件事了。
    """
    monkeypatch.setattr(sandbox, "_bwrap_probe", lambda: (False, "没有找到 bubblewrap（bwrap）"))
    monkeypatch.setattr(
        sandbox, "_seatbelt_probe", lambda: (False, "没有找到 sandbox-exec（macOS 自带）")
    )


def test_missing_backend_raises(no_backend: None, tmp_path: Path) -> None:
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.build_argv("echo hi", _policy("workspace-write", tmp_path))


def test_run_command_refuses_instead_of_running_unsandboxed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_backend: None
) -> None:
    """**底线用例。** 配了沙箱却没有后端时，命令绝不能被执行 —— 那是最坏的失败方式。"""
    monkeypatch.setattr(shell, "current_work_dir", lambda: tmp_path)
    monkeypatch.setenv("SANDBOX_MODE", "workspace-write")
    config.get_settings.cache_clear()

    out = shell.run_command(f'"{sys.executable}" -c "print(\'ran\')"')

    assert "ran" not in out
    assert "没有执行" in out
    assert "SANDBOX_MODE" in out


def test_start_process_refuses_instead_of_running_unsandboxed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_backend: None
) -> None:
    """后台进程活得比这一轮还长，漏掉它比漏掉一条普通命令更糟。"""
    monkeypatch.setattr(shell, "current_work_dir", lambda: tmp_path)
    monkeypatch.setenv("SANDBOX_MODE", "workspace-write")
    config.get_settings.cache_clear()

    out = shell.start_process("sleep 30")

    assert "没有执行" in out
    assert shell.list_processes() == "当前没有后台进程。"


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def test_default_mode_depends_on_the_platform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """默认档位**按平台给**：有后端的平台默认工作区可写，没有的默认关。

    为什么不能一律默认开：默认值对**所有人**生效，而「配了沙箱却没有后端」在本项目里
    是**拒绝执行**。全局默认 workspace-write 的话，没有后端的平台（目前是 Windows）
    会每条命令都失败 —— 而用户根本没配过沙箱，只会觉得程序坏了。
    """
    monkeypatch.delenv("SANDBOX_MODE", raising=False)

    # 有后端的平台：默认就是最实用的那一档
    for platform in ("darwin", "linux"):
        monkeypatch.setattr(sys, "platform", platform)
        config.get_settings.cache_clear()
        assert sandbox.policy_for(tmp_path).mode is sandbox.SandboxMode.WORKSPACE_WRITE

    # 没有后端的平台：默认关，否则开箱即用变成开箱不可用
    monkeypatch.setattr(sys, "platform", "win32")
    config.get_settings.cache_clear()
    assert sandbox.policy_for(tmp_path).mode is sandbox.SandboxMode.OFF


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


# ---------------------------------------------------------------------------
# 界面可改：偏好优先于 .env，且改完立即生效
#
# 这一节存在的理由：档位要是只能靠改 .env + 重启，用户遇到命令被拦时的理性选择
# 就是干脆一关了之 —— 那才是最坏的结果。
# ---------------------------------------------------------------------------


def test_preference_overrides_the_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sandbox_preferences: PreferenceStore
) -> None:
    """界面设过的档位盖过 .env —— 后者是「部署时的默认」，前者是「用户此刻要什么」。"""
    monkeypatch.setenv("SANDBOX_MODE", "read-only")
    config.get_settings.cache_clear()
    sandbox_preferences.set(SANDBOX_MODE_KEY, "workspace-write")

    assert sandbox.policy_for(tmp_path).mode is sandbox.SandboxMode.WORKSPACE_WRITE


def test_broken_preference_falls_back_to_the_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sandbox_preferences: PreferenceStore
) -> None:
    """偏好里填坏了就退回 .env 的值 —— 不是报错，也**不是退到 off**。

    退到 off 就成了「以为开着沙箱、其实在裸跑」，正是这个模块最怕的失败方式。
    退到 .env 则是安全的：那边的值写错会在**启动时**被 `Literal` 拦下，
    所以能走到这一步的那个值一定合法。
    """
    monkeypatch.setenv("SANDBOX_MODE", "read-only")
    config.get_settings.cache_clear()
    sandbox_preferences.set(SANDBOX_MODE_KEY, "workspace-writ")

    assert sandbox.policy_for(tmp_path).mode is sandbox.SandboxMode.READ_ONLY


def test_network_switch_is_separate_from_the_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sandbox_preferences: PreferenceStore
) -> None:
    """联网是独立的一个旋钮：装依赖那一下要开，装完就该收回去。"""
    monkeypatch.setenv("SANDBOX_NETWORK", "false")
    config.get_settings.cache_clear()

    sandbox_preferences.set(SANDBOX_NETWORK_KEY, "true")
    assert sandbox.policy_for(tmp_path).allow_network is True

    sandbox_preferences.set(SANDBOX_NETWORK_KEY, "false")
    assert sandbox.policy_for(tmp_path).allow_network is False


def test_changing_the_preference_takes_effect_without_a_restart(
    tmp_path: Path, sandbox_preferences: PreferenceStore
) -> None:
    """改完**下一条命令**就生效，不必重启进程。"""
    sandbox_preferences.set(SANDBOX_MODE_KEY, "read-only")
    assert sandbox.policy_for(tmp_path).mode is sandbox.SandboxMode.READ_ONLY

    sandbox_preferences.set(SANDBOX_MODE_KEY, "workspace-write")
    assert sandbox.policy_for(tmp_path).mode is sandbox.SandboxMode.WORKSPACE_WRITE


def test_status_lists_every_mode_with_a_plain_language_hint(
    sandbox_preferences: PreferenceStore,
) -> None:
    """档位清单与每档的说明都由后端给。

    界面不另抄一份：抄漏一档的话用户会永久少一个选项，而且不会有任何报错 ——
    这种错只有用户才碰得到。
    """
    state = sandbox.status()

    assert [item["value"] for item in state["modes"]] == ["off", "read-only", "workspace-write"]
    assert all(item["label"] and item["hint"] for item in state["modes"])
    assert state["mode"] in {"off", "read-only", "workspace-write"}
    assert isinstance(state["available"], bool)


def test_status_admits_when_the_value_came_from_the_ui(
    sandbox_preferences: PreferenceStore,
) -> None:
    """界面改过要看得出来 —— 否则「我改了 .env 怎么没反应」只能靠猜。"""
    assert sandbox.status()["customized"] is False

    sandbox_preferences.set(SANDBOX_MODE_KEY, "read-only")
    assert sandbox.status()["customized"] is True


def test_the_api_rejects_an_unknown_mode() -> None:
    """接口层就拒掉拼错的档位，而不是「先存下、再静默回落」。

    回落的方向恰好是「不隔离」—— 那等于让用户以为开着沙箱，实际在裸跑。
    """
    with pytest.raises(ValidationError):
        SandboxPayload(mode="workspace-writ", network=False)


def test_saving_via_the_api_lands_in_preferences(
    monkeypatch: pytest.MonkeyPatch, sandbox_preferences: PreferenceStore
) -> None:
    """接口把两个值写进偏好，并回一份改完的状态（界面因此不必再补一次 GET）。"""
    from server.routes import sandbox as sandbox_route

    monkeypatch.setattr(stores, "preferences", lambda: sandbox_preferences)

    payload = SandboxPayload(mode=sandbox.SandboxMode.READ_ONLY, network=True)
    state = sandbox_route.set_sandbox(payload)

    assert sandbox_preferences.get(SANDBOX_MODE_KEY) == "read-only"
    assert sandbox_preferences.get(SANDBOX_NETWORK_KEY) == "true"
    assert state["mode"] == "read-only"
    assert state["network"] is True


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
# 真实隔离（需要本机有可用后端：Linux bubblewrap / macOS Seatbelt）
#
# 这三个用例走的是**完整链路**（shell.run_command → sandbox.build_argv），
# 所以两个后端共用同一份断言 —— 换后端不该换掉「沙箱该拦什么」这件事。
# ---------------------------------------------------------------------------


@_needs_sandbox
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


@_needs_sandbox
def test_writes_outside_the_work_dir_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """这就是沙箱要拦的东西：工作目录之外的写入在物理上失败。"""
    monkeypatch.setattr(shell, "current_work_dir", lambda: tmp_path)
    monkeypatch.setenv("SANDBOX_MODE", "workspace-write")
    config.get_settings.cache_clear()

    # 目标要落在**真正的外面**：不能是工作目录，也不能是临时区 ——
    # 后者在两个后端下都可写（bwrap 给一个干净的 /tmp；macOS 上 Seatbelt 只能
    # 「放行 TMPDIR」，做不到「换一个」，见 sandbox.py 的说明），拿它当「外面」验不出东西。
    # 而 pytest 的 tmp_path 恰好就住在 TMPDIR 里。
    victim = Path("/_sbx_outside_probe.txt")
    out = shell.run_command(f"touch {victim}", timeout=20)

    assert "退出码：0" not in out
    assert not victim.exists()


@_needs_sandbox
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


@_needs_seatbelt
def test_seatbelt_probe_actually_works_here() -> None:
    """`sandbox-exec` 躺在系统里，不等于这套 profile 语法在这个系统版本上还认。

    它被 Apple 长期标为 deprecated，认的语法也变过 —— 探针正是为此存在的。
    """
    available, reason = sandbox._seatbelt_probe()

    assert available, reason
    assert sandbox.available() is True
