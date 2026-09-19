"""执行工具：在工作目录里跑一条命令。

**这是唯一绕过 PathGuard 的工具。** 文件工具的能力边界是工作目录，而一条命令
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

另外三条是执行类工具的通用要求，缺一个都会出问题：

    - **必须有超时**：一条 `sleep 1000` 会把 chat 端点的 worker 线程占住不放；
    - **必须杀进程组**：只杀 shell 本身，它派生的子进程会变成孤儿继续跑，
      下一轮又和这些残留抢端口、抢文件；
    - **输出必须截断**：`npm install` 的日志动辄几万行，原样回填会撑爆上下文，
      也会把会话文件顶到几 MB。
"""

from __future__ import annotations

import os
import re
import signal
import subprocess

from quill_agent import interaction
from quill_agent.tools.base import registry
from quill_agent.tools.files import current_work_dir

# 默认与最长的执行时间（秒）。默认值偏短是刻意的：绝大多数命令几秒内就有结果，
# 真正需要几分钟的是少数，让它显式传 timeout 比让所有命令都默认挂两分钟好
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 300

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


def _terminate(process: subprocess.Popen) -> None:
    """终止进程，并连带把它的子进程一起清掉。

    只对 shell 本身发信号是不够的：`npm run build` 会派生一堆子进程，它们会变成孤儿
    继续跑，然后下一轮又和这些残留抢端口、抢文件。`start_new_session=True` 让子进程
    自成进程组，这里按组杀。
    """
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


@registry.tool(
    description=(
        "在工作目录里执行一条 shell 命令。跑测试、装依赖、查 git 状态、构建都用它。"
        "命令交给系统 shell 执行，可以用管道和 && 串联。"
        "**注意它不受工作目录限制**：工作目录只是它的起点（cwd），命令本身可以访问"
        "这个目录之外的任何位置。所以涉及删除、覆盖、发布这类不可恢复的操作前，"
        "先向用户确认再执行。"
        "少数看着危险的命令（改根目录、提权、写设备）会先弹给用户确认，"
        "被拒绝时不要换个写法重试 —— 那是在规避用户的决定。"
        "输出过长会被截断，不要用它读大文件（那用 read_file）。"
    ),
    category="执行",
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

    refused = _reject_reason(text)
    if refused:
        return f"无法执行：{refused}"

    danger = _danger_reason(text)
    if danger is not None:
        approved = interaction.confirm(
            text="模型想执行一条可能有害的命令，允许吗？",
            detail=f"$ {text}\n\n判定为风险的原因：{danger}",
        )

        if approved is False:
            return (
                f"用户拒绝了这条命令（原因：{danger}）。"
                "**不要换个写法再试一次** —— 那是在规避用户刚刚做出的决定。"
                "如果确实需要，请让用户自己在终端里执行。"
            )

        if approved is None:
            # 没有通道（Streamlit / 测试）或等待超时。**按拒绝处理**：
            # 一条被判为危险的命令，在没人点头的情况下执行，是这个机制最坏的失败方式
            return (
                f"没能问到用户（等待超时，或当前界面不支持确认），因此没有执行"
                f"（原因：{danger}）。请告诉用户你需要执行它，或改用更安全的做法。"
            )

    try:
        limit = int(timeout)
    except (TypeError, ValueError):
        limit = DEFAULT_TIMEOUT
    limit = max(1, min(limit, MAX_TIMEOUT))

    work_dir = current_work_dir()

    try:
        process = subprocess.Popen(  # shell=True 是刻意的，见模块开头
            text,
            shell=True,
            cwd=work_dir,
            # stderr 并进 stdout：构建日志的先后顺序本身就是信息，
            # 分开收就得自己去猜它们之间的先后
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",  # 输出里混进非 UTF-8 字节时不要炸
            start_new_session=True,
        )
    except OSError as exc:
        return f"无法执行命令：{exc}"

    try:
        output, _ = process.communicate(timeout=limit)
    except subprocess.TimeoutExpired as expired:
        _terminate(process)
        # 超时前吐出来的东西往往正说明它卡在哪，别丢
        partial = expired.output if isinstance(expired.output, str) else ""
        head = f"命令超时（{limit} 秒），已终止：{text}"
        return "\n".join([head, "", _clip(_clean(partial))]) if partial else head

    code = process.returncode
    status = "成功" if code == 0 else "失败"

    return "\n".join(
        [
            f"$ {text}",
            f"目录：{work_dir}    退出码：{code}（{status}）",
            "",
            _clip(_clean(output or "")),
        ]
    )
