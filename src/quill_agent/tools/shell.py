"""执行工具：在工作目录里跑一条命令，或起一个长驻的后台进程。

**`run_command` 和 `start_process` 都绕过 PathGuard。** 文件工具的能力边界是
工作目录，而一条命令
（`cat ~/.ssh/id_rsa`、`curl ...`、`pip install`）不受这个边界约束 —— cwd 只是它
「从哪里开始」，不是它「只能到哪里」。这件事没法靠参数校验抹平，只能靠三件事：

    1. **它是自选的**：工具定义不会自动进入任何模式的工具组，要用必须显式加进去，
       等于一次知情同意。这是唯一真正的边界；
    2. **对模型说清楚**：description 里点明它不受工作目录限制、点明不可逆操作要先问
       用户（约束提示词里已有这条，工具这层再说一次更稳）；
    3. **动手前先问一次**：见 `_danger_reason` / `_reject_reason`。判定危险的是我们，
       能不能承担风险的是用户 —— 所以「看着危险」的命令不硬拒，而是弹给用户点头。
       它挡不住有心绕过的写法，所以不是安全边界，只是防手滑。**

    > 想让**每一条**命令都要点头（而不只是看着危险的那些），在工具组的 `confirm`
    > 里勾上 `run_command` —— 那道确认在 agent 层，和这里的判断相互独立。

上面三条全是**软约束**，真正的边界由沙箱给（见 `quill_agent.sandbox`）：配置
`SANDBOX_MODE=workspace-write` 之后，命令跑在一个「只有工作目录可写、默认断网」的
命名空间里 —— 越界写在物理上就失败，和模型怎么写命令无关。沙箱配了但本机没有可用
后端时**拒绝执行**，绝不静默裸跑。两个机制正交：沙箱管「够不够得着」，审批管「要不要问」。

另外三条是执行类工具的通用要求，缺一个都会出问题：

    - **必须有超时**：一条 `sleep 1000` 会把 chat 端点的 worker 线程占住不放；
    - **必须杀进程组**：只杀 shell 本身，它派生的子进程会变成孤儿继续跑，
      下一轮又和这些残留抢端口、抢文件；
    - **输出必须截断**：`npm install` 的日志动辄几万行，原样回填会撑爆上下文，
      也会把会话文件顶到几 MB。
"""

from __future__ import annotations

import itertools
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

from quill_agent import interaction, sandbox
from quill_agent.tools.base import registry
from quill_agent.tools.files import current_work_dir

# 默认与最长的执行时间（秒）。默认值偏短是刻意的：绝大多数命令几秒内就有结果，
# 真正需要几分钟的是少数，让它显式传 timeout 比让所有命令都默认挂两分钟好
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 300

# 平台分支：Windows 上没有进程组信号（`os.killpg` / `os.getpgid` / `SIGKILL` 都不存在），
# 清理进程树只能换一套做法 —— 见 `_terminate`
_POSIX = os.name != "nt"

# 模型看不见底下用的是哪个 shell，默认它多半会写 POSIX 那套（`ls -la`），
# 而 Windows 上的执行壳是 cmd.exe —— 那条命令只会得到「不是内部或外部命令」。
# 在工具说明里点一句，省掉一轮试错
if _POSIX:
    _SHELL_NOTE = "命令交给系统 shell 执行，可以用管道和 && 串联。"
else:  # pragma: no cover - 按平台
    _SHELL_NOTE = (
        "命令由 Windows 的 cmd.exe 执行：请用 dir / type / where 这类 Windows 命令，"
        "不要用 ls / cat / which；管道和 && 可以用。"
    )

# 回给模型和界面的输出上限
MAX_OUTPUT_CHARS = 20_000
HEAD_LINES = 100
TAIL_LINES = 100

# ANSI 颜色 / 光标控制序列。有些工具不管有没有 TTY 都上色，
# 那些转义序列进了上下文纯属噪声，还会白占 token
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# 纯交互式程序：带上参数也一样在等输入，一律拒绝。
# 它们在等一个永远不会到来的回车，只会把这一轮挂到超时。
ALWAYS_INTERACTIVE = frozenset(
    {
        "vim", "vi", "nvim", "nano", "pico", "emacs", "less", "more",
        "top", "htop", "watch", "ssh", "telnet", "ftp", "sftp", "man",
    }
)

# 只有「不带参数」时才是交互式的。带了参数就是正常用法，必须放行：
# `python` 是 REPL（拦），`python -c "..."` / `python x.py` 是执行脚本（放行）
BARE_ONLY_INTERACTIVE = frozenset(
    {"python", "python3", "ipython", "node", "psql", "mysql", "sqlite3", "redis-cli"}
)

# 看着危险的写法。命中不当成错误，而是**弹给用户点头**。
#
# **这是防手滑，不是安全边界** —— 换个写法就能绕过去。它拦的是「模型一时糊涂写出来的
# 那几条」，拦下来之后怎么办交给用户：判定危险的是我们，能不能承担风险的是用户。
# 模式按 POSIX shell 写；Windows 上拦不住多少，但也不会误伤
CATASTROPHIC_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brm\s+(-[A-Za-z]+\s+)*/(?:\s|$)", "对根目录执行 rm"),
    (r":\(\)\s*\{.*\}\s*;\s*:", "fork 炸弹"),
    (r"\bsudo\b", "sudo —— 它会等密码输入，只会挂到超时"),
    (r"\bmkfs\b", "格式化文件系统"),
    (r"\bdd\s+if=", "直接用 dd 写设备"),
    (r">\s*/dev/[sh]d[a-z]", "直接写块设备"),
    (r"\bchmod\s+-R\s+777\s+/(?:\s|$)", "把根目录权限改成 777"),
)


def _reject_reason(command: str) -> str | None:
    """该**直接拒绝**的理由；没问题返回 None。

    只剩交互式程序这一类。它们和危险命令不一样：**问用户也没用** ——
    用户说「允许」，它照样在那儿等一个永远不会到来的回车，最后挂到超时。
    所以这类不设确认，直接挡掉并告诉模型换个写法。
    """
    stripped = command.strip()
    head = os.path.basename(stripped.split(maxsplit=1)[0])

    if head in ALWAYS_INTERACTIVE:
        return (
            f"「{stripped}」是交互式程序，它会在等输入时挂住整轮对话。"
            "要读文件用 read_file，要看文件开头用 read_file 的 offset / limit。"
        )

    if head in BARE_ONLY_INTERACTIVE and " " not in stripped:
        return (
            f"「{stripped}」不带参数时是交互式解释器，会一直等输入。"
            "请带上要执行的脚本或 -c 参数。"
        )

    return None


def _danger_reason(command: str) -> str | None:
    """该**先问用户**的理由；看着正常时返回 None。"""
    stripped = command.strip()

    for pattern, reason in CATASTROPHIC_PATTERNS:
        if re.search(pattern, stripped):
            return reason

    return None


def _guard(command: str) -> str | None:
    """执行前的两道闸：**拒绝交互式**、**危险命令先问用户**。

    Returns:
        应当直接回给模型的文本（被拦下）；`None` 表示通过、可以执行。

    `run_command` 和 `start_process` 共用这一份判断 —— 后台进程同样是不受
    工作目录约束的入口，放它单独走一套判断，就等于给用户留了一个没上闸的后门。
    """
    refused = _reject_reason(command)
    if refused:
        return f"无法执行：{refused}"

    danger = _danger_reason(command)
    if danger is None:
        return None

    approved = interaction.confirm(
        text="模型想执行一条可能有害的命令，允许吗？",
        detail=f"$ {command}\n\n判定为风险的原因：{danger}",
    )

    if approved is False:
        return (
            f"用户拒绝了这条命令（原因：{danger}）。"
            "**不要换个写法再试一次** —— 那是在规避用户刚刚做出的决定。"
            "如果确实需要，请让用户自己在终端里执行。"
        )

    if approved is None:
        # 没有通道（CLI / 单元测试）或等待超时。**按拒绝处理**：
        # 一条被判为危险的命令，在没人点头的情况下执行，是这个机制最坏的失败方式
        return (
            f"没能问到用户（等待超时，或当前界面不支持确认），因此没有执行"
            f"（原因：{danger}）。请告诉用户你需要执行它，或改用更安全的做法。"
        )

    return None


def _launch(command: str, work_dir: Path) -> tuple[list[str] | None, str | None]:
    """按沙箱策略拼出要执行的 argv。

    Returns:
        (argv, 错误文案)。成功时错误是 None；沙箱配了却没有可用后端时 argv 是 None，
        错误文案可直接回给模型。

        这里刻意**没有**「那就算了、直接裸跑」的分支：用户既然显式要了边界，
        偷偷不套是这个机制最坏的失败方式 —— 比没做沙箱更糟，因为它给了虚假的安全感。
    """
    policy = sandbox.policy_for(work_dir)

    try:
        return sandbox.build_argv(command, policy), None
    except sandbox.SandboxUnavailable as exc:
        return None, (
            f"无法执行：沙箱配置为 {policy.mode.value}，但当前系统没有可用后端 —— {exc}\n"
            "这条命令**没有执行**。请安装 bubblewrap（如 apt install bubblewrap）、"
            "换到支持的系统，或把 SANDBOX_MODE 显式改成 off 后再试。"
        )


def _clean(output: str) -> str:
    """去掉颜色转义序列，统一换行符。"""
    return ANSI_ESCAPE.sub("", output).replace("\r\n", "\n").replace("\r", "\n")


def _clip(output: str) -> str:
    """过长的输出保留头尾。

    为什么砍中间而不是砍尾巴：命令的头几行是它自己在说什么，最后几十行是结论和报错，
    中间往往是最没有信息量的大段进度日志。
    """
    text = output.strip()
    if not text:
        return "（命令没有输出）"

    lines = text.splitlines()
    if len(lines) > HEAD_LINES + TAIL_LINES:
        dropped = len(lines) - HEAD_LINES - TAIL_LINES
        lines = [
            *lines[:HEAD_LINES],
            f"... 中间省略 {dropped} 行 ...",
            *lines[-TAIL_LINES:],
        ]
        text = "\n".join(lines)

    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "…（输出过长已截断）"

    return text


class _BoundedSink:
    """有界输出收集器：只留开头与结尾两段，中间边读边丢。

    为什么不是「先全读进内存、再调 `_clip` 截断」：截断发生在读完之后，等于没防 ——
    一条 `yes`、`cat /dev/zero` 或者死循环打印的命令，在默认 30 秒超时内就能往内存里
    塞几百 MB，把 runner 线程的堆顶爆。所以必须**边读边丢**。

    保留头尾的理由和 `_clip` 一样：开头是命令在说什么，结尾是结论和报错，中间是
    最没有信息量的大段进度日志。
    """

    def __init__(self, limit: int = MAX_OUTPUT_CHARS) -> None:
        self._head_budget = limit // 2
        self._tail_budget = limit - self._head_budget
        self._head: list[str] = []
        self._head_len = 0
        self._tail: list[str] = []
        self._tail_len = 0
        self._dropped = False
        # 读线程写入、runner 线程读取，必须加锁
        self._lock = threading.Lock()

    def feed(self, chunk: str) -> None:
        """收下一块输出。"""
        with self._lock:
            if self._head_len < self._head_budget:
                take = min(self._head_budget - self._head_len, len(chunk))
                self._head.append(chunk[:take])
                self._head_len += take
                chunk = chunk[take:]

            if not chunk:
                return

            self._dropped = True
            self._tail.append(chunk)
            self._tail_len += len(chunk)

            # 从最老的整块开始丢，避免每来一块都重排一次尾巴
            while self._tail and self._tail_len - len(self._tail[0]) >= self._tail_budget:
                self._tail_len -= len(self._tail.pop(0))

    def text(self) -> str:
        """拼出当前收集到的内容；中途被丢掉的段落会留一句说明。"""
        with self._lock:
            head = "".join(self._head)
            tail = "".join(self._tail)
            dropped = self._dropped

        if len(tail) > self._tail_budget:
            tail = tail[len(tail) - self._tail_budget :]

        if not dropped:
            return head + tail
        if not tail:
            return head + "\n... 输出过长，中间内容已丢弃 ..."
        return f"{head}\n... 输出过长，中间内容已丢弃 ...\n{tail}"


def _pump_output(stream, sink: _BoundedSink) -> None:
    """把管道读空并喂给收集器（读线程的活）。

    必须有人在读：子进程写满管道缓冲区就会卡住，表现成「命令莫名不动了」。
    读到出错也要忍住（管道被关闭 / 进程被杀都会走到这里），已经收到的部分仍然有用。
    """
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            sink.feed(chunk)
    except (OSError, ValueError):
        return


def _terminate(process: subprocess.Popen) -> None:
    """终止进程，并连带把它的子进程一起清掉。

    只对 shell 本身发信号是不够的：`npm run build` 会派生一堆子进程，它们会变成孤儿
    继续跑，然后下一轮又和这些残留抢端口、抢文件。两个平台各有一种做法：

        - **POSIX**：`start_new_session=True` 让子进程自成进程组，按组发 SIGTERM / SIGKILL；
        - **Windows**：没有进程组信号 —— `os.killpg` / `os.getpgid` 根本不存在，
          `signal.SIGKILL` 也没有。那边借 `taskkill /T` 遍历整棵进程树。

    之前这里直接写死 POSIX 那套，于是「Windows 上一条命令超时」会变成 AttributeError，
    连「命令超时，已终止」这句提示都拿不到。
    """
    if process.poll() is not None:
        # 它已经自己结束了。先判一次是为了避开 PID 复用：此刻再按 pid 杀，
        # 命中的可能是系统里另一个刚拿到这个 pid 的进程
        return

    if _POSIX:
        _terminate_group(process)
    else:  # pragma: no cover - 仅在 Windows 上走到
        _terminate_tree(process)


def _terminate_group(process: subprocess.Popen) -> None:
    """POSIX：按进程组 SIGTERM，给 5 秒体面退出，再 SIGKILL。"""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except OSError:
        # 进程组已经不在了（命令自己结束了），或拿不到 —— 退回到杀它自己
        process.kill()

    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass  # 有自己的想法，上 SIGKILL

    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except OSError:
        process.kill()

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        # 已经 SIGKILL 过了还是等不到（内核层面卡住）。不能在这里无限等下去 ——
        # 那会把这一轮的 worker 线程永久占住，正是加超时要避免的事
        pass


def _terminate_tree(process: subprocess.Popen) -> None:  # pragma: no cover - 仅 Windows
    """Windows：`taskkill /T /F` 连子进程一起清。

    `/T` 杀整棵进程树，`/F` 强制。拿不到 taskkill（极少数精简环境）就退回
    `process.kill()` —— 聊胜于无，至少不留着这条命令本身。
    """
    try:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        process.kill()

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


@registry.tool(
    description=(
        "在工作目录里执行一条 shell 命令。跑测试、装依赖、查 git 状态、构建都用它。"
        + _SHELL_NOTE
        + "**注意它不受工作目录限制**：工作目录只是它的起点（cwd），命令本身可以访问"
        "这个目录之外的任何位置。所以涉及删除、覆盖、发布这类不可恢复的操作前，"
        "先向用户确认再执行。"
        "若启用了沙箱，越界写入和联网会直接失败 —— 那是设计如此，不要试图绕过它。"
        "少数看着危险的命令（改根目录、提权、写设备）会先弹给用户确认，"
        "被拒绝时不要换个写法重试 —— 那是在规避用户的决定。"
        "输出过长会被截断，不要用它读大文件（那用 read_file）。"
    ),
    category="shell",
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的命令，例如 pytest -q 或 git status --short",
            },
            "timeout": {
                "type": "integer",
                "description": f"超时秒数，默认 {DEFAULT_TIMEOUT}，上限 {MAX_TIMEOUT}",
            },
        },
        "required": ["command"],
    },
)
def run_command(command: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """在工作目录里执行一条命令。"""
    text = (command or "").strip()
    if not text:
        return "命令不能为空。"

    blocked = _guard(text)
    if blocked is not None:
        return blocked

    try:
        limit = int(timeout)
    except (TypeError, ValueError):
        limit = DEFAULT_TIMEOUT
    limit = max(1, min(limit, MAX_TIMEOUT))

    work_dir = current_work_dir()

    argv, blocked = _launch(text, work_dir)
    if blocked is not None:
        return blocked

    try:
        process = subprocess.Popen(
            argv,
            # 命令已经被明确交给 /bin/sh -c（见 sandbox.build_argv），
            # 不必再让 subprocess 自己猜一次 shell —— 猜错的那次会把沙箱前缀当成命令名
            shell=False,
            cwd=work_dir,
            # stderr 并进 stdout：构建日志的先后顺序本身就是信息，
            # 分开收就得自己去猜它们之间的先后
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",  # 输出里混进非 UTF-8 字节时不要炸
            # 自成进程组，方便按组清干净（Windows 上这个参数会被忽略，且没有等价物，
            # 那边的清理走 taskkill /T，见 _terminate）
            start_new_session=_POSIX,
        )
    except OSError as exc:
        return f"无法执行命令：{exc}"

    try:
        # 收集与等待解耦：读线程负责把管道读空（丢中间、留头尾），
        # 主线程只管超时。`communicate()` 会把输出**全部**留在内存里，
        # 截断再发生在读完之后 —— 那就防不住 `yes` 这类命令（见 _BoundedSink）
        sink = _BoundedSink()
        reader = threading.Thread(
            target=_pump_output,
            args=(process.stdout, sink),
            daemon=True,
        )
        reader.start()

        try:
            process.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            _terminate(process)
            # 等一小会儿让读线程把残留输出收干净，但进程组里若还有别的进程
            # 攥着这个管道，它不会结束 —— 所以只是等一会儿，不是必须等到
            reader.join(timeout=5)

            # 超时前吐出来的东西往往正说明它卡在哪，别丢
            partial = sink.text()
            head = f"命令超时（{limit} 秒），已终止：{text}"
            return "\n".join([head, "", _clip(_clean(partial))]) if partial else head

        reader.join(timeout=5)
        output = sink.text()
    finally:
        # 父进程这一侧的读端要主动收掉，否则它跟着 process 对象一起等 GC
        if process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass

    code = process.returncode
    status = "成功" if code == 0 else "失败"

    return "\n".join(
        [
            f"$ {text}",
            f"目录：{work_dir}    退出码：{code}（{status}）",
            sandbox.policy_for(work_dir).describe(),
            "",
            _clip(_clean(output or "")),
        ]
    )


# ---------------------------------------------------------------------------
# 后台进程：起一个长驻进程（开发服务器、watch、监听），随后按需取输出、停掉它
#
# 为什么需要它：`run_command` 是「跑完才返回」的同步调用，且封顶 300 秒。起一个
# dev server 只会两种结局 —— 要么把这一轮的 runner 线程占满两分钟后被掐掉，要么
# 超时被杀。可「改完代码重启前端」这类活，恰恰必须让进程活过这一轮。
#
# 生命周期刻意不绑在一轮对话上：dev server 活到下一轮才有意义，所以它只能被
# `stop_process` 显式停掉。代价是可能泄漏，于是有 MAX_PROCESSES 这道上限兜底。
# ---------------------------------------------------------------------------

# 同时存活的后台进程上限。本地单用户场景，个位数足够
MAX_PROCESSES = 8

# 每个进程保留的输出行数。超出就丢最老的 —— dev server 的日志动辄几千行，
# 全留着纯属占内存，而模型要看的永远是「最近那段」
BUFFER_LINES = 500

# 启动后留在原地观察一小会儿，看它是不是立刻就退出了。命令拼错（`npx vite` 少个
# 字母）时进程会瞬间退出，若不当场告诉模型，它会拿一个已经死掉的 id 反复取输出
START_PROBE_SECONDS = 0.5


class _BackgroundProcess:
    """一个正在跑（或刚跑完）的后台进程。

    输出由一条专属线程持续从管道里读走。**必须有这条线程**：管道缓冲区写满以后，
    子进程会阻塞在 write 上假死，dev server 就不再响应了。
    """

    def __init__(self, pid: str, command: str, cwd: str, process: subprocess.Popen) -> None:
        self.id = pid
        self.command = command
        self.cwd = cwd
        self.process = process
        self.started = time.monotonic()
        self.returncode: int | None = None
        self._lines: list[str] = []
        self._cursor = 0  # process_output 已经读到哪一行
        self._lock = threading.Lock()
        self._reader = threading.Thread(
            target=self._drain, name=f"quill-proc-{pid}", daemon=True
        )

    def start(self) -> None:
        self._reader.start()

    def _drain(self) -> None:
        """把子进程的输出搬进缓冲，直到它结束（或被 stop 掉）。"""
        stream = self.process.stdout
        try:
            for line in stream:  # type: ignore[union-attr]
                with self._lock:
                    self._lines.append(line)
                    if len(self._lines) > BUFFER_LINES:
                        dropped = len(self._lines) - BUFFER_LINES
                        self._lines = self._lines[dropped:]
                        # 缓冲缩短了，读游标要跟着回退，否则会跳过还没被读走的行
                        self._cursor = max(0, self._cursor - dropped)
        finally:
            self.process.wait()
            self.returncode = self.process.returncode

    def read_new(self) -> str:
        """取走自上次调用以来的新输出（增量），并推进游标。"""
        with self._lock:
            fresh = "".join(self._lines[self._cursor:])
            self._cursor = len(self._lines)
        return fresh

    def alive(self) -> bool:
        return self.process.poll() is None

    def stop(self) -> None:
        _terminate(self.process)
        self.returncode = self.process.returncode

    def status(self) -> str:
        if self.alive():
            return f"运行中（已 {time.monotonic() - self.started:.0f} 秒）"
        return f"已退出（退出码 {self.returncode}）"


# 模块级的进程表。和 server 那边的运行注册表同一个理由：工具拿不到「会话」，
# 只能靠一张模块级的表把句柄存下来，供下一次工具调用取。
_processes: dict[str, _BackgroundProcess] = {}
_processes_lock = threading.Lock()
_process_seq = itertools.count(1)


def _reap_dead_locked() -> None:
    """回收已经退出的进程，腾出名额。**调用方必须持有 `_processes_lock`。**

    只在名额不够时才调用 —— 平时留着已退出的进程，好让模型还能读到它的最终输出。
    """
    for key in [key for key, item in _processes.items() if not item.alive()]:
        _processes.pop(key, None)


@registry.tool(
    description=(
        "起一个**长驻**进程（开发服务器、watch、监听端口），立刻返回一个进程号。"
        "什么时候用它：命令不会自己结束 —— `npm run dev`、`uvicorn ... --reload`、"
        "`vite`。什么时候别用：命令跑完就完事（测试、构建、装依赖、git），"
        "那用 run_command 直接拿输出。"
        "起完用 process_output 取它的输出，改完代码后用 stop_process 停掉再重起。"
        "**它不受工作目录限制**（除非启用了沙箱），且进程会活到被显式停掉为止，"
        "所以危险操作同样要先向用户确认。"
    ),
    category="shell",
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要后台运行的长驻命令，例如 npm run dev",
            },
        },
        "required": ["command"],
    },
)
def start_process(command: str) -> str:
    """启动一个后台长驻进程，返回它的进程号。"""
    text = (command or "").strip()
    if not text:
        return "命令不能为空。"

    blocked = _guard(text)
    if blocked is not None:
        return blocked

    # 和 run_command 一样先过沙箱：后台进程同样是不受工作目录约束的入口，
    # 它活得比这一轮还长，所以漏掉它比漏掉一条普通命令更糟
    work_dir = current_work_dir()
    argv, blocked = _launch(text, work_dir)
    if blocked is not None:
        return blocked

    with _processes_lock:
        _reap_dead_locked()
        if len(_processes) >= MAX_PROCESSES:
            running = ", ".join(item.id for item in _processes.values() if item.alive())
            return (
                f"后台进程已达上限（{MAX_PROCESSES} 个，在跑的：{running}）。"
                "先用 stop_process 停掉不再需要的，再起新的。"
            )

        try:
            process = subprocess.Popen(
                argv,
                shell=False,  # 同 run_command：shell 已经由 sandbox.build_argv 明确指定
                cwd=work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                start_new_session=_POSIX,  # 自成进程组，stop 时按组一起清
            )
        except OSError as exc:
            return f"无法启动：{exc}"

        pid = f"p{next(_process_seq)}"
        item = _BackgroundProcess(pid, text, work_dir, process)
        _processes[pid] = item
        item.start()

    # 短暂观察（在锁外）：命令拼错时进程会瞬间退出，当场告诉模型比让它拿着一个
    # 死掉的 id 反复取输出要好
    time.sleep(START_PROBE_SECONDS)
    if not item.alive():
        initial = _clip(_clean(item.read_new()))
        with _processes_lock:
            _processes.pop(pid, None)
        return (
            f"命令立刻退出了（{item.status()}），没有被当成后台进程。"
            f"若是拼写错误请修正后重试；若它本来就不该常驻，改用 run_command。\n"
            f"$ {text}\n\n{initial}"
        )

    return (
        f"已启动后台进程 {pid}（在 {work_dir} 里）：\n$ {text}\n"
        f"用 process_output 传 {pid} 取它的最新输出，stop_process 传 {pid} 停掉它。"
    )


@registry.tool(
    description=(
        "取一个后台进程**自上次取过之后**的新输出（增量，不会重复给已给过的）。"
        "进程若已退出，会在这里一并说明退出码 —— 这也是确认「它是不是崩了」的方式。"
        "取不到新输出时返回空提示，不是错误。"
    ),
    category="shell",
    parameters={
        "type": "object",
        "properties": {
            "process_id": {
                "type": "string",
                "description": "start_process 返回的进程号，例如 p1",
            },
        },
        "required": ["process_id"],
    },
)
def process_output(process_id: str) -> str:
    """读取后台进程的增量输出。"""
    pid = (process_id or "").strip()
    with _processes_lock:
        item = _processes.get(pid)

    if item is None:
        return f"没有这个后台进程：{pid}。用 list_processes 看看现在有哪些。"

    fresh = _clean(item.read_new())
    header = f"{pid}：{item.status()}\n$ {item.command}"
    return f"{header}\n\n{_clip(fresh) if fresh.strip() else '（没有新输出）'}"


@registry.tool(
    description=(
        "停掉一个后台进程，并把它从进程表里移除。"
        "改了后端代码要重启、或确认它崩了要再起一个时用它。"
        "**它连带把派生的子进程一起清掉**，不会留下占着端口的孤儿。"
    ),
    category="shell",
    parameters={
        "type": "object",
        "properties": {
            "process_id": {
                "type": "string",
                "description": "要停止的进程号，例如 p1",
            },
        },
        "required": ["process_id"],
    },
)
def stop_process(process_id: str) -> str:
    """停止一个后台进程。"""
    pid = (process_id or "").strip()
    with _processes_lock:
        item = _processes.get(pid)

    if item is None:
        return f"没有这个后台进程：{pid}。用 list_processes 看看现在有哪些。"

    if item.alive():
        item.stop()

    tail = _clean(item.read_new())
    with _processes_lock:
        _processes.pop(pid, None)

    body = f"\n\n最后一段输出：\n{_clip(tail)}" if tail.strip() else ""
    return f"已停止 {pid}（{item.status()}）：$ {item.command}{body}"


@registry.tool(
    description=(
        "列出当前所有后台进程（进程号、状态、命令）。"
        "忘了进程号、或不确定还剩哪些在跑时用它 —— 别去猜。"
    ),
    category="shell",
)
def list_processes() -> str:
    """列出当前的后台进程。"""
    with _processes_lock:
        items = list(_processes.values())

    if not items:
        return "当前没有后台进程。"

    lines = [f"{item.id}：{item.status()}    $ {item.command}" for item in items]
    return "\n".join(["当前后台进程：", *lines])
