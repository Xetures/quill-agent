"""git 工具：让模型能看清自己改了什么、能提交。

**为什么不让模型直接 `run_command git ...`。** 三条，每条都有具体的坏结果：

1. `git status --short` 的那一列状态码（`M ` / ` M` / `??`）位置是有含义的，小模型经常
   读反 —— 把「工作区改了」看成「已暂存」，然后 commit 出一份空的、还报「成功」；
2. **commit 是不可逆的对外动作**，必须问用户一次。`run_command` 那条确认只认灾难性命令
   （`rm -rf /` 之类），`git commit` 不在里面，走它等于绕过了确认；
3. `git diff` 稍大一点就是几百行，原样回灌会把上下文吃掉一大块。这里截断。

**只做本地的事：不 push、不 fetch、不 pull、不碰远端。** 远端是用户的决定 —— 提交是自己
仓库里的事，推出去是给别人看的，这两件事不该由模型混在一起做。
"""

from __future__ import annotations

import subprocess

from quill_agent import interaction
from quill_agent.tools.base import registry
from quill_agent.tools.files import current_work_dir

# 输出截断。和工具结果回灌给模型的 2000 字符同一个口径 —— 那个是硬上限，
# 这里稍宽一些，留给 diff 这种「信息密度低但要看」的内容
MAX_OUTPUT_CHARS = 4000

# git 在本地跑，正常的命令都是毫秒级。给到 30 秒是为了容忍大仓库的首次 status
TIMEOUT = 30.0


def _git(*args: str) -> tuple[bool, str]:
    """在工作目录里跑一条 git。

    Returns:
        (成功?, 输出)。失败时输出是一句能行动的说明 —— 模型判断「为什么不行」只能靠它。
    """
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=str(current_work_dir()),
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except FileNotFoundError:
        return False, "这台机器上没有 git，或者它不在 PATH 里。"
    except subprocess.TimeoutExpired:
        return False, f"git {' '.join(args)} 超过 {TIMEOUT:.0f} 秒没有结束，已中断。"

    # stdout 和 stderr 都要：git 把「无改动可提交」这类**正常提示**也写在 stderr 上，
    # 只看 stdout 的话，那些情况会变成一句空话
    output = (done.stdout + done.stderr).strip()
    if done.returncode != 0:
        return False, output or f"git {' '.join(args)} 失败（退出码 {done.returncode}）。"
    return True, output


def _not_a_repo() -> str | None:
    """不在 git 仓库里时返回提示，否则返回 None。"""
    ok, output = _git("rev-parse", "--is-inside-work-tree")
    if not ok or output.strip() != "true":
        return f"当前工作目录不是一个 git 仓库：{current_work_dir()}"
    return None


@registry.tool(
    description=(
        "查看当前 git 仓库的状态：当前分支、已暂存和未暂存的改动、未跟踪的文件。"
        "想知道「我改了什么、还有什么没提交」时用它。"
        "想看具体改动内容用 git_diff；想看历史用 git_log。"
    ),
    category="git",
)
def git_status() -> str:
    error = _not_a_repo()
    if error:
        return error

    ok, output = _git("status", "--short", "--branch")
    if not ok:
        return output
    return output or "工作区是干净的，没有任何未提交的改动。"


@registry.tool(
    description=(
        "查看尚未提交的改动内容（unified diff 文本）。"
        "给 path 就只看那一个文件。提交之前先用它确认改的是不是你以为的东西。"
    ),
    category="git",
    parameters={
        "properties": {
            "path": {
                "type": "string",
                "description": "可选。只看这个文件/目录的改动，留空看全部。",
            }
        }
    },
)
def git_diff(path: str = "") -> str:
    error = _not_a_repo()
    if error:
        return error

    args = ["diff", "--no-color"]
    if path:
        args += ["--", path]

    ok, output = _git(*args)
    if not ok:
        return output
    if not output:
        return "没有未暂存的改动。若改动已经 add 过，它们不在 git diff 里 —— 用 git_status 看。"

    if len(output) > MAX_OUTPUT_CHARS:
        head = output[:MAX_OUTPUT_CHARS]
        return f"{head}\n…（diff 太长，只给了前 {MAX_OUTPUT_CHARS} 个字符；用 path 参数看单个文件）"
    return output


@registry.tool(
    description=(
        "查看最近的提交记录，一行一条（短哈希 + 日期 + 说明）。"
        "想知道「这块代码最近为什么改成这样」时用它。"
    ),
    category="git",
    parameters={
        "properties": {
            "limit": {"type": "integer", "description": "要几条，默认 10，最多 50。"}
        }
    },
)
def git_log(limit: int = 10) -> str:
    error = _not_a_repo()
    if error:
        return error

    count = max(1, min(int(limit), 50))
    ok, output = _git("log", f"-{count}", "--pretty=format:%h  %ad  %s", "--date=short")
    if not ok:
        return output
    return output or "还没有任何提交。"


@registry.tool(
    description=(
        "把当前工作区的改动提交到**本地**仓库。不会 push —— 推送要用户自己决定。"
        "调用前先看 git_status / git_diff 确认改了什么；message 写清「为什么改」，一行标题即可。"
        "提交不可逆，会先问用户一次。"
    ),
    category="git",
    parameters={
        "properties": {"message": {"type": "string", "description": "提交说明（一行标题）"}}
    },
    required=["message"],
)
def git_commit(message: str) -> str:
    # 参数校验放在环境校验前面：空说明是模型自己能改的，先告诉它这个，
    # 而不是让它绕一圈去查「这里是不是仓库」
    text = message.strip()
    if not text:
        return "提交说明不能为空。"

    error = _not_a_repo()
    if error:
        return error

    ok, status = _git("status", "--short")
    if not ok:
        return status
    if not status:
        return "工作区是干净的，没有要提交的东西。"

    # 提交写进仓库历史，而这个工具**不提供 reset / undo**。所以必须问一次，
    # 走的是和「危险命令」同一条确认通道
    approved = interaction.confirm(
        text="模型想把当前的改动提交到本地仓库，允许吗？",
        detail=f"提交说明：{text}\n\n将要提交的改动：\n{status}",
    )

    # None 和 False 要分开：前者是「问不到」（界面没有可回答的地方 / 超时），
    # 提示语不一样，用户才知道该去哪儿处理
    if approved is None:
        return "没能拿到用户确认（当前界面无法回答，或等待超时），提交没有执行。"
    if not approved:
        return "用户拒绝了这次提交，改动仍然留在工作区。"

    ok, output = _git("add", "-A")
    if not ok:
        return f"暂存失败：{output}"

    ok, output = _git("commit", "-m", text)
    if not ok:
        return f"提交失败：{output}"
    return output
