"""任务清单：把一件事拆成几步，并把进度摊开给用户看。

为什么值得单独一个工具
----------------------
一轮长任务里，模型心里其实有一份计划（先看什么、再改哪儿、最后验什么），但用户看不到。
于是「它到底在干什么、还剩多少」只能靠猜 —— 中间十几分钟的工具调用在界面上就是一串
看不懂的名字。这个工具把那份计划变成**看得见的东西**：模型每推进一步就重写一次清单，
界面据此显示进度。它不产生任何副作用，纯粹是「说出来给用户听」。

为什么每次提交**完整清单**，而不是「把第 3 项标成完成」
--------------------------------------------------------
两个理由：

1. 增量接口要求模型自己维护「第几项」这个坐标，而它每轮看到的清单是**它上一条消息里
   写的**。一旦数错序号或把两项并成一项，整份清单就开始漂，而界面会显示一份谁都没写过的
   计划；
2. 全量提交是**幂等**的：重复调用、漏掉一次、多个调用乱序到达，最终结果都只取决于最后
   一次提交，不会留下幽灵条目。

代价是每次重发一遍清单文本，而一份清单通常只有几百个字符 —— 换幂等，很划算。

清单存到哪
----------
条目存在 `TodoBoard` 里（由调用方创建、随这一轮走），和 `RunStats` 是同一个路子：
生成器没法「返回」值，只能让调用方拿一个可变对象进来，跑完再读。界面那边因此有两条路：

    运行中  —— publish 一个 `todo` 事件，实时刷新进度条；
    跑完了  —— 清单随这一轮的消息一起落盘，回看时还在（见 server/routes/chat.py）。

两条路不是重复：前者要在被工具阻塞的生成器之外送出去（见 interaction.py 开头那段），
后者要能活过这一轮。用途不同，所以各走各的。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from quill_agent import interaction
from quill_agent.tools.base import registry

# 清单最多几项。
#
# 上限的作用不是省 token，而是**拦住「把清单当笔记写」**：一份二十项的清单没人读得下去，
# 而模型会倾向于把每个小动作都列一条。十项出头正好是「一件事拆成看得清的几步」的量级。
MAX_TODOS = 12

# 单项内容（一句话）的长度上限。
MAX_CONTENT_CHARS = 120

# 三种状态。和界面的分组一一对应，也是模型能填的全部取值。
STATUS_PENDING = "pending"
STATUS_ACTIVE = "in_progress"
STATUS_DONE = "completed"
STATUSES = (STATUS_PENDING, STATUS_ACTIVE, STATUS_DONE)

# 状态 -> 展示用的那个括号。给模型看的那份结果也用它，两边长得一样，
# 模型更容易确认「我写的确实被记下来了」。
MARKS = {
    STATUS_DONE: "[x]",
    STATUS_ACTIVE: "[>]",
    STATUS_PENDING: "[ ]",
}


@dataclass(frozen=True)
class TodoItem:
    """清单里的一项。

    Attributes:
        content: 这一步做什么，一句话。
        status: 见 STATUSES。
    """

    content: str
    status: str

    def to_payload(self) -> dict[str, str]:
        return {"content": self.content, "status": self.status}


@dataclass
class TodoBoard:
    """一轮运行里的清单板。

    可变的、由调用方创建后交给 `run_agent_stream` 就地填充 —— 和 `RunStats` 同一个理由
    （生成器没法「返回」值）。

    只保留**最后一次**提交的清单：清单要回答的是「现在的计划长什么样」，不是「一路改过
    哪些版本」。所以它不是日志，每次提交整体替换。
    """

    items: tuple[TodoItem, ...] = ()

    def replace(self, items: Sequence[TodoItem]) -> None:
        self.items = tuple(items)

    def to_payload(self) -> list[dict[str, str]]:
        """转成能进 JSON 的形状（推给前端、落盘都用它）。"""
        return [item.to_payload() for item in self.items]


def render(items: Sequence[TodoItem]) -> str:
    """把清单排成一段文本。

    两个地方用它：给模型看的工具结果（确认它写的东西被记下了），以及出错时的提示。
    完成度放在标题里，是因为模型下一步要不要继续往前推，看的就是这个数。
    """
    done = sum(1 for item in items if item.status == STATUS_DONE)
    lines = [f"任务清单（{done}/{len(items)} 已完成）："]
    lines.extend(f"{MARKS[item.status]} {item.content}" for item in items)
    return "\n".join(lines)


def _parse(raw: Any) -> tuple[list[TodoItem], str]:
    """解析并校验模型给的清单。

    校验偏严：清单是**要给用户看的**，一份带未知状态或空条目的清单渲染出来是坏的，
    与其猜（把不认识的 status 当成 pending？），不如直接说清楚哪一项不对 —— 模型改一次
    就好了。

    Returns:
        (条目, 错误文案)。错误非空时条目为空。
    """
    if isinstance(raw, str):
        # 有些模型会把数组序列化成字符串再塞进来。给一次机会，别让整轮白跑
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return [], "todos 要的是数组，这里收到的是字符串。"

    if not isinstance(raw, list):
        return [], "todos 要的是数组。"

    if not raw:
        return [], "todos 是空的。至少要有一项 —— 确实没有要做的事就别调用本工具。"

    if len(raw) > MAX_TODOS:
        return [], f"清单最多 {MAX_TODOS} 项，现在给了 {len(raw)} 项。请合并成几步。"

    items: list[TodoItem] = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            return [], f'第 {index} 项不是对象，每项形如 {{"content": "...", "status": "..."}}。'

        content = str(entry.get("content") or "").strip()
        if not content:
            return [], f"第 {index} 项没有 content。"

        # 超长不报错，截断就好：内容长短是表述问题，不值得为它让整轮停下来
        if len(content) > MAX_CONTENT_CHARS:
            content = content[:MAX_CONTENT_CHARS] + "…"

        status = str(entry.get("status") or "").strip().lower()
        if status not in STATUSES:
            return [], (
                f"第 {index} 项的 status 是「{status}」，不认识。"
                f"只能是 {'、'.join(STATUSES)} 三者之一。"
            )

        items.append(TodoItem(content=content, status=status))

    return items, ""


def _hints(items: Sequence[TodoItem]) -> list[str]:
    """清单本身的软提醒，附在工具结果后面。

    **不拦**：同时标几项在进行中，是模型的表述选择，拦下来它只会为了凑格式去改内容。
    但它确实会让界面的进度读不出来，所以说一句。
    """
    active = [item for item in items if item.status == STATUS_ACTIVE]
    if len(active) > 1:
        return ["（提示：有多项同时标成 in_progress，进度会不好读。一次只留一项。）"]
    return []


def _board() -> TodoBoard | None:
    """当前这一轮的清单板；不在一次运行里（测试 / 直接调用）时返回 None。

    延迟导入 `quill_agent.agent`：它在模块级就 import 了 `quill_agent.tools`（取
    registry），而本模块是在 `tools/__init__` 里被导入的 —— 两边都写模块级 import，
    会拿到一个只执行了一半的 agent 模块。理由和 tools/subagent.py 里那段一样。
    """
    from quill_agent import agent

    environment = agent.current_environment()
    return environment.todos if environment is not None else None


def _publish(items: Sequence[TodoItem]) -> None:
    """把清单交给界面，实时刷新进度。

    没有通道（单元测试、纯脚本调用）时静默丢弃：清单照样记着、照样落盘，只是这一轮看不到
    实时进度。理由同 tools/subagent.py 的 `_publish`。
    """
    channel = interaction.current()
    if channel is not None:
        channel.publish(("todo", {"items": [item.to_payload() for item in items]}))


@registry.tool(
    description=(
        "把这次要做的事拆成几步，并随进度更新 —— 用户会在界面上看到这份清单和完成情况。"
        "**它只影响界面显示，本身不做任何操作。**"
        "任务要好几步、用户会想知道「现在到哪一步了」的时候就该用："
        "调研 + 改多处 + 验证这类活，先列一次清单再动手。"
        "一两下就完的事、或者只是闲聊，别用 —— 那时候清单是噪音。"
        "每次调用都要提交**完整清单**（不是「把第几项标成完成」），"
        "状态用 pending / in_progress / completed，**同一时刻只留一项 in_progress**。"
        "做完一项就立刻更新一次，别攒到最后一起写 —— 那样进度条就没有意义了。"
    ),
    category="规划",
    parameters={
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": f"完整清单，按执行顺序排列，最多 {MAX_TODOS} 项",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": f"这一步做什么，一句话（{MAX_CONTENT_CHARS} 字以内）",
                        },
                        "status": {
                            "type": "string",
                            "enum": list(STATUSES),
                            "description": "pending 未开始 / in_progress 进行中 / completed 已完成",
                        },
                    },
                    "required": ["content", "status"],
                },
            }
        },
        "required": ["todos"],
    },
)
def todo_write(todos: list) -> str:
    """记录 / 更新任务清单，返回排好的清单文本。"""
    items, error = _parse(todos)
    if error:
        return error

    # 顺序有讲究：先落到板上（这一步决定落盘的内容），再往外推（这一步决定界面看到的
    # 内容）。两者都用同一份 items，不会出现「界面显示了、记录里没有」
    board = _board()
    if board is not None:
        board.replace(items)

    _publish(items)

    lines = [render(items), *_hints(items)]
    return "\n".join(lines)
