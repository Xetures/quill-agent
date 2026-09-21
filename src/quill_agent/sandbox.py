"""沙箱：把执行类工具关进一个**由内核执行**的边界里。

为什么需要它
------------
`run_command` / `start_process` 是这个项目里唯一绕过 PathGuard 的入口 —— 工作目录只是
命令的 cwd，不是它「只能到哪里」。文件工具靠参数校验就能守住边界，命令守不住：
`cat ~/.ssh/id_rsa`、`curl -d @secret http://…`、`rm -rf ~/Documents` 全都是合法命令。

shell.py 里那两道闸（危险命令先问用户、交互式程序直接拒）是**防手滑，不是安全边界**
—— 换个写法就能绕过去。真正的边界只能由内核给：本模块用 bubblewrap 把命令关进一个
「只有工作目录可写、默认没有网络」的命名空间里。够不着就是够不着，和模型怎么写命令无关。

沙箱不替代审批
--------------
两者是正交的，别把它们混成一件事：

    - **沙箱**回答「能做什么」—— 越界写在物理上就失败；
    - **审批**回答「要不要问」—— 够得着，但先让用户点个头（见 shell._guard）。

分档
----
    off              不套沙箱 —— 默认值，保持既有行为
    read-only        工作区只读、默认断网（读代码、做分析这类任务）
    workspace-write  工作区可写、默认断网（改代码、跑测试这类任务）

为什么默认是 off
----------------
默认值仍是 off：开着的话，**没有可用后端的平台**上每条命令都会落到「无后端 → 拒绝执行」，
开发和测试当场不可用。**无后端时不静默放行** —— 用户既然显式要了沙箱，偷偷裸跑是这里
最坏的失败方式。

两个后端（按平台自动选，不用配）
-------------------------------
    Linux    bubblewrap —— 非特权 user namespace，一条命令表达完整的隔离视图
    macOS    Seatbelt（sandbox-exec）—— 系统自带的策略引擎

两者语义上有几处**有意的不对齐**（做不到完全一致，见 `_seatbelt_profile` 里的说明）：
bubblewrap 能给沙箱一个干净空的 `/tmp`，Seatbelt 只能「允许 / 拒绝某个路径」，
所以那边的临时目录是**放行**而不是隔离。写清楚这一点，比假装两边一样有用。

为什么用现成的沙箱，而不是自己写 seccomp
----------------------------------------
bubblewrap 与 Seatbelt 都是**已经被验证过**的实现，一条命令就能表达完整的隔离视图。
自己拼 seccomp-bpf 规则，写错的代价是把沙箱写成筛子。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path

from quill_agent.config import get_settings


class SandboxMode(str, Enum):
    """隔离档位。取值同时是 `SANDBOX_MODE` 环境变量的合法值。"""

    OFF = "off"
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"

    @property
    def label(self) -> str:
        """界面 / 回执里展示用的名称。"""
        return {
            SandboxMode.OFF: "不隔离",
            SandboxMode.READ_ONLY: "只读",
            SandboxMode.WORKSPACE_WRITE: "工作区可写",
        }[self]


class SandboxUnavailable(RuntimeError):
    """当前系统没有可用的沙箱后端。

    调用方**不能**当作「那就直接跑吧」—— 要回一句明确的拒绝文案，
    告诉用户怎么装后端、或者怎么显式地关掉沙箱。
    """


@dataclass(frozen=True)
class SandboxPolicy:
    """一次执行要遵守的边界。

    Attributes:
        mode: 隔离档位。
        work_dir: 唯一可写的目录（read-only 档下连它也只读）。
        allow_network: 是否放行网络。**默认 False** —— 数据外传是这类 Agent 最实际的
            风险，而绝大多数编码任务（跑测试、构建、装依赖在装的时候才需要）用不上网络。
    """

    mode: SandboxMode
    work_dir: Path
    allow_network: bool = False

    @property
    def active(self) -> bool:
        """这档策略是否真的需要套一层沙箱。"""
        return self.mode is not SandboxMode.OFF

    def describe(self) -> str:
        """一行说明，拼进工具回执 —— 让模型和用户都看得见这条命令是在什么边界里跑的。"""
        if not self.active:
            return "沙箱：关闭（命令不受工作目录限制）"
        network = "联网放行" if self.allow_network else "断网"
        return f"沙箱：{self.mode.label}（{network}，仅有 {self.work_dir} 可写）"


# 家目录里那些「读到就等于失守」的东西。沙箱里用空目录盖住它们 ——
# 不是靠权限位，是让它们**根本不存在于这个进程的视野里**。
#
# 故意不整个盖住 $HOME：pip / uv / npm 的缓存都住在家里，盖掉会让每次安装
# 从零开始（慢到不可用），而它们不是秘密。
SENSITIVE_HOME_DIRS = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".config/gcloud",
    ".kube",
    ".docker",
    ".netrc",
    ".npmrc",
    ".pypirc",
)


def policy_for(work_dir: str | Path) -> SandboxPolicy:
    """按配置和给定工作目录造一份策略。

    配置项的合法值由 `Settings` 上的 Literal 保证：`SANDBOX_MODE` 写错会在**启动时**
    直接报错，而不是在这里被悄悄降级成 off。「以为开了沙箱、其实没开」比启动失败危险
    得多 —— 尤其是这里降级的默认方向恰好是「不隔离」。
    """
    settings = get_settings()
    return SandboxPolicy(
        mode=SandboxMode(settings.sandbox_mode),
        work_dir=Path(work_dir).resolve(),
        allow_network=bool(settings.sandbox_network),
    )


def _shell_for_platform() -> list[str]:
    """不带沙箱时的执行壳。"""
    if sys.platform == "win32":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c"]
    return ["/bin/sh", "-c"]


def build_argv(command: str, policy: SandboxPolicy) -> list[str]:
    """把一条 shell 命令包成可以直接交给 `subprocess` 的 argv。

    返回的列表一律配 `shell=False` 使用 —— 命令字符串已经明明白白地交给
    `/bin/sh -c` 了，不必再让 subprocess 去猜一次 shell。

    Raises:
        SandboxUnavailable: 策略要求沙箱，但当前系统没有可用后端。
    """
    if not policy.active:
        return [*_shell_for_platform(), command]

    if sys.platform.startswith("linux"):
        return _bwrap_argv(command, policy)
    if sys.platform == "darwin":
        return _seatbelt_argv(command, policy)

    raise SandboxUnavailable(_no_backend_reason())


def _no_backend_reason() -> str:
    """没有后端时的说明，要能让用户自己修好。"""
    if sys.platform == "darwin":
        return (
            "本机是 macOS，但 sandbox-exec 用不了（系统被裁剪过，或该工具已被移除）。"
            "可以先用 SANDBOX_MODE=off 显式关掉隔离。"
        )
    if sys.platform == "win32":
        return (
            "本机是 Windows。原生 Windows 没有等价的沙箱原语，"
            "建议在 WSL2 里运行本程序（那条路复用 Linux 的 bubblewrap 后端）。"
        )
    return "当前系统没有已知的沙箱后端。"


def _bwrap_argv(command: str, policy: SandboxPolicy) -> list[str]:
    """用 bubblewrap 把命令关进命名空间里。

    Raises:
        SandboxUnavailable: bwrap 没装，或装了但在这个内核上跑不起来。
    """
    available, reason = _bwrap_probe()
    if not available:
        raise SandboxUnavailable(f"{reason}（{_no_backend_reason()}）")

    work_dir = str(policy.work_dir)
    argv = [
        "bwrap",
        # 父进程（也就是本应用）没了就跟着退出，不留孤儿
        "--die-with-parent",
        # 整个文件系统先**只读**挂进来 —— 白名单思维：先全关，再逐条开口子
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        # /tmp 给一个干净的空目录：别让沙箱里的进程和外面共用临时文件
        "--tmpfs", "/tmp",
    ]

    # 盖住家目录里的密钥 / 凭据（存在才盖，不存在就别多此一举）
    for name in SENSITIVE_HOME_DIRS:
        secret = Path.home() / name
        if not secret.exists():
            continue
        # 工作目录恰好落在里面时跳过 —— 否则会把用户要改的东西一起盖掉
        if policy.work_dir == secret or policy.work_dir.is_relative_to(secret):
            continue
        argv += ["--tmpfs", str(secret)]

    # 再把工作目录「抬」成可写。顺序很关键：它必须排在上面那些只读 / 空挂了之后
    #
    # 工作目录是 / 时不抬 —— 那等于把整个文件系统重新挂成可写，沙箱直接失效。
    # WORK_DIR=/ 本身就是个错配置，这里让它退化成「只读」而不是「全开」：
    # 出错时倒向安全的方向。
    if policy.mode is SandboxMode.WORKSPACE_WRITE and policy.work_dir != Path("/"):
        argv += ["--bind", work_dir, work_dir]

    if not policy.allow_network:
        argv += ["--unshare-net"]

    # 独立的 PID 命名空间：命令看不见（也就杀不掉）外面别的进程
    argv += ["--unshare-pid"]

    argv += ["--chdir", work_dir, "--", *_shell_for_platform(), command]
    return argv


@lru_cache(maxsize=1)
def _bwrap_probe() -> tuple[bool, str]:
    """真跑一次 bwrap 看它能不能建出命名空间。

    为什么不用 `shutil.which` 就够了：装了不等于能用。Ubuntu 24.04 起
    `kernel.apparmor_restrict_unprivileged_userns=1` 会挡住非特权 user namespace，
    这时 bwrap 在 PATH 里躺着、但每次都报权限错误 —— 那种失败必须在这里就翻译成人话，
    而不是原样丢给模型。

    Returns:
        (是否可用, 不可用的原因)。结果缓存 —— 运行环境不会中途改变。
    """
    executable = shutil.which("bwrap")
    if executable is None:
        return False, "没有找到 bubblewrap（bwrap），请先安装它（如 apt install bubblewrap）"

    try:
        result = subprocess.run(
            [executable, "--ro-bind", "/", "/", "--unshare-net", "--", "/bin/true"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"bubblewrap 执行失败：{exc}"

    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        hint = detail[0] if detail else f"退出码 {result.returncode}"
        return False, f"bubblewrap 无法创建沙箱：{hint}"

    return True, ""


def _seatbelt_argv(command: str, policy: SandboxPolicy) -> list[str]:
    """用 macOS 的 Seatbelt（sandbox-exec）把命令关进策略里。

    Raises:
        SandboxUnavailable: sandbox-exec 不在，或这套 profile 语法在当前系统上不认。
    """
    ok, reason = _seatbelt_probe()
    if not ok:
        raise SandboxUnavailable(f"{reason}（{_no_backend_reason()}）")

    return [
        "sandbox-exec",
        # profile 直接写在命令行里、不落盘：它是**按策略实时算出来的**，落盘就得管
        # 生命周期（什么时候清、并发时谁覆盖谁），而它只活这一条命令
        "-p",
        _seatbelt_profile(policy),
        "--",
        *_shell_for_platform(),
        command,
    ]


def _seatbelt_profile(policy: SandboxPolicy) -> str:
    """把策略翻译成 Seatbelt 的 profile（Scheme 语法）。

    **规则语义是「后写的覆盖先写的」** —— 所以顺序整个是刻意的：
    先铺一个「能跑起来」的底子，再用更具体的规则往里收（读全盘 → 再关掉密钥目录）。

    和 bubblewrap 那版有几处**有意的不一致**，都是为了「能用」：

    1. **临时目录是放行，不是隔离。** bwrap 能 `--tmpfs /tmp` 给一个干净的空目录；
       Seatbelt 只能「允许 / 拒绝某个路径」，做不到「换一个」。不给写的话 python 会
       直接报「没有可用的临时目录」，构建和跑测试全线失败 —— 而临时文件本来也不是秘密。
    2. **`/dev/null` 这类要单独开口子。** `2>/dev/null` 到处都是，而它属于「写设备文件」，
       不在 `file-write*` 的通配范围里。
    3. **`mach-lookup` 必须放行。** 那是 macOS 的进程间服务，不给的话大半个系统调用
       都起不来。这是平台差异，不是放松。

    `work_dir` 是 `/` 时不放开写 —— 那等于全盘可写，沙箱直接失效。错配置退化成只读。
    """
    lines = [
        "(version 1)",
        # 白名单思维：先全关，再逐条开口子（和 bwrap 的 `--ro-bind / /` 一个路子）
        "(deny default)",
        # —— 跑起一条命令的最低需求 ——
        "(allow process-exec*)",
        "(allow process-fork)",
        "(allow sysctl-read)",  # 不少程序启动时会读系统信息
        "(allow mach-lookup)",  # macOS 的进程间服务，不给的话大半个系统调用起不来
        "(allow ipc-posix-shm)",  # 共享内存，python / node 之类要用
        "(allow signal (target self))",
        # 读盘整个放开 —— 隔离的是「写」和「网」；读代码本来就是它的正经用途
        "(allow file-read*)",
    ]

    # 设备文件：`2>/dev/null` 这类重定向太常见，不给的话大量命令直接失败
    lines.append(
        '(allow file-write-data (literal "/dev/null") (literal "/dev/stdout") '
        '(literal "/dev/stderr") (literal "/dev/zero"))'
    )

    # 临时目录（理由见 docstring 第 1 条）
    writable = ["/private/tmp", "/var/tmp", str(Path(tempfile.gettempdir()).resolve())]
    for target in dict.fromkeys(writable):  # 去重，且保持顺序
        lines.append(f'(allow file-write* (subpath "{target}"))')

    # 家目录里的密钥 / 凭据：排在 `file-read*` **之后**，靠「后写覆盖先写」把它们收回去。
    # 不是靠权限位，是让它们在这个进程的视野里读不到。
    for name in SENSITIVE_HOME_DIRS:
        secret = Path.home() / name
        if not secret.exists():
            continue
        # 工作目录恰好落在里面时跳过 —— 否则会把用户要改的东西一起封掉
        if policy.work_dir == secret or policy.work_dir.is_relative_to(secret):
            continue
        lines.append(f'(deny file-read* (subpath "{secret}"))')

    if policy.mode is SandboxMode.WORKSPACE_WRITE and policy.work_dir != Path("/"):
        lines.append(f'(allow file-write* (subpath "{policy.work_dir}"))')

    if policy.allow_network:
        lines.append("(allow network*)")

    return "\n".join(lines)


# 探针用的 profile：能起一个进程就算通过。
# 刻意**不用** `(allow default)` —— 那样连语法写错都能跑通，探针就白探了。
_SEATBELT_PROBE_PROFILE = "(version 1)(deny default)(allow process-exec*)(allow file-read*)"


@lru_cache(maxsize=1)
def _seatbelt_probe() -> tuple[bool, str]:
    """真跑一次 sandbox-exec 看它能不能落地。

    和 bwrap 那边同一个道理：**在系统里躺着不等于能用**。`sandbox-exec` 是 Apple
    长期标记为 deprecated 的工具，profile 认的语法也随系统版本变过；探一次、把失败
    翻译成人话，比让每条命令各自失败一次强。

    Returns:
        (是否可用, 不可用的原因)。结果缓存 —— 运行环境不会中途改变。
    """
    executable = shutil.which("sandbox-exec")
    if executable is None:
        return False, "没有找到 sandbox-exec（macOS 自带；缺失说明系统被裁剪过）"

    try:
        result = subprocess.run(
            [executable, "-p", _SEATBELT_PROBE_PROFILE, "--", "/usr/bin/true"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"sandbox-exec 执行失败：{exc}"

    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        hint = detail[0] if detail else f"退出码 {result.returncode}"
        return False, f"sandbox-exec 无法落地：{hint}"

    return True, ""


def available() -> bool:
    """当前平台此刻有没有可用的沙箱后端。"""
    # 先看平台再看探测：别的平台上根本不会用这个后端，没必要真去跑一次探测
    if sys.platform.startswith("linux"):
        return _bwrap_probe()[0]
    if sys.platform == "darwin":
        return _seatbelt_probe()[0]
    return False


def startup_note() -> str:
    """进程启动时的一句自检说明，供界面 / 日志展示。

    它存在的理由是：一个「以为开了沙箱、其实没有」的配置，比明确关着更危险。
    """
    policy = policy_for(Path.cwd())

    if not policy.active:
        return "沙箱未启用（SANDBOX_MODE=off），执行类命令不受工作目录限制。"

    if available():
        # 不复用 `describe()`：它自带「沙箱：」前缀（那是给工具回执用的），
        # 套在这里会读成「沙箱已启用：沙箱：…」
        network = "联网放行" if policy.allow_network else "断网"
        # 顺带报一下用的是哪个后端：两个后端的隔离语义有几处不一样（见 `_seatbelt_profile`），
        # 出问题时第一件事就是确认跑的是哪一个
        backend = "bubblewrap" if sys.platform.startswith("linux") else "Seatbelt"
        return (
            f"沙箱已启用：{policy.mode.label}（{network}，仅有 {policy.work_dir} 可写，"
            f"后端 {backend}）"
        )

    return (
        f"沙箱配置为 {policy.mode.value}，但当前系统没有可用后端，"
        f"执行类命令将被**拒绝**。{_no_backend_reason()}"
    )
