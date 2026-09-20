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
Phase 1 只有 Linux 后端（bubblewrap）。开发机若是 macOS，就没有可用后端 ——
默认开着的话每条命令都会落到「无后端 → 拒绝执行」，开发和测试当场不可用。
等 Phase 2 补上 macOS 的 Seatbelt 后端，再把默认值翻成 workspace-write。
**无后端时不静默放行**：用户既然显式要了沙箱，偷偷裸跑是这里最坏的失败方式。

为什么是 bubblewrap 而不是自己写 seccomp
----------------------------------------
bubblewrap 是「非特权 + 走 user namespace」的现成实现，一条命令就能表达完整的隔离视图，
而且它已经在无数发行版里被验证过。自己拼 seccomp-bpf 规则，写错的代价是把沙箱写成筛子。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
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

    raise SandboxUnavailable(_no_backend_reason())


def _no_backend_reason() -> str:
    """没有后端时的说明，要能让用户自己修好。"""
    if sys.platform == "darwin":
        return (
            "本机是 macOS，而 Phase 1 只带了 Linux 的 bubblewrap 后端（macOS 的 Seatbelt "
            "后端计划在 Phase 2 提供）。"
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


def available() -> bool:
    """当前平台此刻有没有可用的沙箱后端。"""
    # 先看平台再看探测：非 Linux 上根本不会用 bwrap，没必要真去跑一次探测
    if not sys.platform.startswith("linux"):
        return False
    return _bwrap_probe()[0]


def startup_note() -> str:
    """进程启动时的一句自检说明，供界面 / 日志展示。

    它存在的理由是：一个「以为开了沙箱、其实没有」的配置，比明确关着更危险。
    """
    policy = policy_for(Path.cwd())

    if not policy.active:
        return "沙箱未启用（SANDBOX_MODE=off），执行类命令不受工作目录限制。"

    if available():
        return f"沙箱已启用：{policy.describe()}"

    return (
        f"沙箱配置为 {policy.mode.value}，但当前系统没有可用后端，"
        f"执行类命令将被**拒绝**。{_no_backend_reason()}"
    )
