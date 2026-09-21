"""子代理：派一个独立的代理去完成一件具体的事，拿回它的结论。

它解决什么问题
--------------
主代理的上下文是有限的。让它自己去把「翻遍这个仓库找出所有调用点」做完，那一大堆
中间过程（读过的文件、搜过的结果）会永久占住上下文，而之后的对话还得在这个已经被
撑大的窗口里继续。子代理解决的就是这件事：**它有自己的一份上下文，用完就扔，
只把结论带回来。**

它和「一问一答」的通道是什么关系
--------------------------------
有关系，但不冲突。子代理**不占**那条通道 —— 它不向用户提问，而是借同一条通道把
自己的动静**单向**播出去（「我正在读 X」）。所以它不是「一问一答」，而是「自己跑完、
把结论当工具结果交回去」。

只做一层
--------
子代理不能再派子代理。递归派下去成本是乘的、排查是难的，收益却接近零 —— 真有嵌套的
需求，那是任务本身没拆清楚。
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Any

from quill_agent import interaction
from quill_agent.tools.base import registry

# 子代理用不了的工具。
#
# 全是「和用户打交道」的那几个：子代理的职责是干活并汇报，不是替父代理和用户对话。
# 让它去提问、去交计划，用户会看到两张卡片同时挂着，而他根本不知道子任务在干什么 ——
# 而父代理自己也在等，两边谁都说不清楚。
#
# todo_write 是同一类：它唯一的产出是**给用户看的**进度条，而子代理那份没地方显示 ——
# 父级只汇报它的结论。留着它，子代理会认真列一份清单，然后被默默丢掉，白花 token；
# 而它中途 publish 出去的那几条还可能和父级自己的计划打架，界面上的进度就乱了。
SUBAGENT_EXCLUDED = frozenset({"spawn_agent", "ask_user", "submit_plan", "todo_write"})

# 嵌套深度。只允许一层。
_depth: ContextVar[int] = ContextVar("quill_subagent_depth", default=0)

# 子代理「还在干活」的心跳间隔（秒）。
#
# 为什么需要它：子代理的事件是按**动作**播的（调了哪个工具），而两个动作之间可能隔着
# 很久 —— 模型在长思考、在长篇输出、工具在跑。那段时间外面一个字节都收不到，而前端
# 有一条 360 秒的静默保护（见 `web/src/api/chat.ts` 的 `IDLE_TIMEOUT_MS`）：它会把
# 「一直没数据」当成死连接，掐掉整条流。结果是**子代理干得越认真，越容易被自己人误杀**。
#
# 30 秒是折中：相对 360 秒有充足余量（中间可能隔着一次模型请求 + 一次工具），
# 又不至于把看板刷成流水账。
_HEARTBEAT_SECONDS = 30.0

# 交给子代理的交代，拼在任务前面成为它那一轮的 user 消息。
#
# 为什么不做成一条独立的 system 消息：子代理**继承父级的模式**，也就是继承父级那一整套
# system 提示词（身份、能力、工具策略…）。再插一条「你是子代理」的 system 消息，会和
# 父级的身份提示词打架。而这段交代本来就属于「任务上下文」—— 和项目里「运行时上下文
# 放 user 消息」是同一个口径，理由也一样：它每轮都不一样。
BRIEF = """\
你是一个被派来执行具体任务的子代理。

关于你的处境，三件事必须知道：

1. 你看到的是一份**全新的上下文** —— 发起方那边的对话你看不到。任务描述里没写的，
   就是真的没有。
2. 别去猜任务之外的东西想要什么：**遇到不确定的地方按最合理的假设继续**，并在结论里
   写明你假设了什么。不要反问、不要等确认 —— 你没有正在对话的用户。
3. 你只负责这一件事，做完就停。

结论必须**自包含**：对方只看得到你最后这段文字，看不到你读了哪些文件、跑了哪些命令。
所以直接给出答案本身，而不是「详见上面的分析」。
"""


def _publish(payload: dict[str, Any]) -> None:
    """把子代理的动静转告给外面。

    走的还是那条交互通道 —— 它本来就是「运行内部往外说话」的路子。没有通道
    （单元测试、纯脚本调用）时静默丢弃：子代理照样跑，只是外面看不到过程。
    """
    channel = interaction.current()
    if channel is not None:
        channel.publish(("subagent", payload))


@registry.tool(
    description=(
        "派一个子代理去独立完成一件具体的事，然后拿回它的结论。"
        "**它有自己的上下文** —— 读过的文件、搜过的结果都不会占你的上下文，"
        "所以它适合那种「过程很长、但你只关心结论」的活："
        "翻遍代码库找出某类调用点、读一批文件回答一个问题、摸清一个模块的结构。"
        "反过来，一两下就能查清的事别用它 —— 派一次的开销比你自己查大。"
        "任务描述要**自包含**：子代理看不到你和用户的对话，没交代的它不知道。"
        "它不能再派子代理（只允许一层）。"
    ),
    category="子代理",
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "交给子代理的任务。写清三件事：要回答什么问题、范围在哪"
                    "（哪些目录或文件）、结论要什么形式"
                ),
            }
        },
        "required": ["task"],
    },
)
def spawn_agent(task: str) -> str:
    """派一个子代理，阻塞等它跑完，返回它的结论。"""
    text = (task or "").strip()
    if not text:
        return "任务是空的。请说清要子代理做什么。"

    if _depth.get() > 0:
        return "子代理不能再派子代理（只允许一层）。请自己完成这件事。"

    # 延迟导入：`quill_agent.agent` 在模块级就 import 了 `quill_agent.tools`（取 registry），
    # 而本模块是在 `tools/__init__` 里被导入的 —— 两边都写模块级 import，会拿到一个
    # 只执行了一半的 agent 模块（`run_agent_stream` 那时还没定义）。放进函数体，
    # 等真正调用时两边都已加载完
    from quill_agent import agent

    environment = agent.current_environment()
    if environment is None:
        # 不在一次运行里（有人直接调这个函数）。理论到不了，但不能返回空 ——
        # 调用方会把它当字符串交给模型
        return "当前不在一次 Agent 运行里，派不了子代理。"

    parent = environment.context
    nested = agent.ModeContext(
        # 提示词、技能、记忆、需要确认的工具全都照搬：子代理是同一个身份、同一套规矩，
        # 只是换了一份干净的上下文。少了这几样，它会变得比父代理更「放得开」——
        # 比如父代理在只读模式下，子代理却拿到写工具
        prompts=parent.prompts,
        tools=[name for name in parent.tools if name not in SUBAGENT_EXCLUDED],
        skills=parent.skills,
        memory_enabled=parent.memory_enabled,
        confirm=parent.confirm,
        # 技能组照搬：子代理和父级用的是同一套技能，新建技能时要提醒勾的就是这个组
        skill_group_id=parent.skill_group_id,
    )

    # 子代理有自己的账本，跑完并回父级（见下面的 finally）
    nested_stats = agent.RunStats()
    token = _depth.set(_depth.get() + 1)
    text_parts: list[str] = []

    try:
        # 上次往外播报的时刻，心跳按它计时
        last_beat = time.monotonic()
        for item in agent.run_agent_stream(
            prompt=f"{BRIEF}\n任务：{text}",
            files=[],
            mode=None,  # 真正的上下文由 context 直接给，mode 在这里没有意义
            context=nested,
            choice=environment.choice,
            history=[],  # 空历史 = 干净的上下文，这正是子代理的意义所在
            stats=nested_stats,
        ):
            if isinstance(item, agent.ToolStart):
                # 播 ToolStart（执行**前**）而不是 ToolStep（执行**后**）：两者带的字段
                # 一样，但需要被看见的是**执行中**那段静默 —— 耗时全在前面，播一句
                # 「跑完了」对「是不是卡住了」这个疑问没有帮助。父级也是这个道理。
                _publish({"type": "tool", "name": item.name, "arguments": item.arguments})
                last_beat = time.monotonic()
            elif isinstance(item, agent.Notice):
                _publish({"type": "notice", "text": item.text})
                last_beat = time.monotonic()
            else:
                # 正文增量、思维链都不进看板：子代理输出上千字的话，那是几百个 SSE
                # 事件，而外面要看的其实是「它正在做什么」。成品（它的结论）最后随
                # 工具结果一起到，不差这一点时间。
                if isinstance(item, str):
                    text_parts.append(item)

                # 但它们证明「它还活着」—— 隔一会儿替它播一句。少了这句，一次长思考
                # 或长输出就会撞上前端那条 360 秒的静默保护（见 _HEARTBEAT_SECONDS），
                # 整条流被当成死连接掐掉。
                if time.monotonic() - last_beat >= _HEARTBEAT_SECONDS:
                    last_beat = time.monotonic()
                    _publish({"type": "notice", "text": "子代理正在工作…"})
    finally:
        _depth.reset(token)
        # 用量并回父级：不并的话这些 token 花了钱却不出现在用量页上。
        # 耗时不用并 —— 子代理的耗时已经含在父级「这一次工具调用」里了
        environment.stats.merge(nested_stats)

    answer = "".join(text_parts).strip()
    if not answer:
        return (
            "子代理没有给出结论（它可能一直在调工具，或者被工具轮预算切断了）。"
            "可以换个说法再派一次，或者你自己查。"
        )

    return f"子代理的结论（用了 {nested_stats.total_tokens} tokens）：\n\n{answer}"
