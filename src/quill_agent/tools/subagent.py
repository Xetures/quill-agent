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

并行派多个（`spawn_agents`）
--------------------------
几件**互不依赖**的活一起派出去跑，总耗时约等于最慢的那一个 —— 而不是它们相加。这是
子代理最自然的用法：一次派三个去摸三个模块，本来就是三件独立的事。

代价全在**线程边界**上：子代理跑在线程池的工作线程里，而项目里三样东西是走 ContextVar
传的（运行环境、交互通道、改动记录器），ContextVar **不跨线程继承**。少绑一样就是一个
具体的故障，而不是「效果差一点」：

| 没绑的东西 | 会发生什么 |
| --- | --- |
| 运行环境 | 子代理一开口就是「不在一次运行里，派不了子代理」 |
| 交互通道 | 看板上一片空白，用户全程看不到它在干什么 |
| 改动记录器 | 它改过的文件不进「还原」快照 —— 事后退不回去，而且没有任何提示 |

所以 worker 里那三行 `activate` 是**功能的一部分**，不是样板代码。同理，账本不能由各
线程自己 `merge` 进父级（那是读-改-写，并发下会丢），得由父级收齐了统一并。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from quill_agent import changes, interaction
from quill_agent.tools.base import registry

if TYPE_CHECKING:  # 只为类型标注：agent 必须延迟 import（见 _run_child 里的说明）
    from quill_agent.agent import RunEnvironment, RunStats
    from quill_agent.changes import ChangeRecorder
    from quill_agent.interaction import Interaction

# 子代理用不了的工具。
#
# 全是「和用户打交道」的那几个：子代理的职责是干活并汇报，不是替父代理和用户对话。
# 让它去提问、去交计划，用户会看到两张卡片同时挂着，而他根本不知道子任务在干什么 ——
# 而父代理自己也在等，两边谁都说不清楚。
#
# todo_write 是同一类：它唯一的产出是**给用户看的**进度条，而子代理那份没地方显示 ——
# 父级只汇报它的结论。留着它，子代理会认真列一份清单，然后被默默丢掉，白花 token；
# 而它中途 publish 出去的那几条还可能和父级自己的计划打架，界面上的进度就乱了。
# spawn_agents 也在里面：深度护栏（只允许一层）本身拦得住它，但**别留着** ——
# 子代理每个请求都要带上这份 schema，而它永远调不通；更糟的是它可能真去试一次，
# 白费一轮才拿到「不能再派子代理」。
SUBAGENT_EXCLUDED = frozenset({"spawn_agents", "ask_user", "submit_plan", "todo_write"})

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

# 同时跑的子代理数上限。
#
# 它同时就是「并发打给模型的请求数」—— 每个子代理都在独立地调模型。而本地部署
# （Ollama 那类）通常只有几个并行槽（`OLLAMA_NUM_PARALLEL` 默认就是 4），派出去超过
# 这个数的子代理不会更快，只会在服务端排成一队、还多占着线程。4 既和本地默认值对齐，
# 也够覆盖「同时摸几个模块」这类真实用法。
MAX_PARALLEL_AGENTS = 4

# 一次最多派几个。超过就拒绝，而不是照单全收 ——
# 每一个子代理都是一次完整的模型对话，模型估不准时可能一口气报几十个。
MAX_SUBAGENT_TASKS = 8

# 看板上子代理标签的长度（取任务开头几个字）。
LABEL_MAX_CHARS = 14

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


def _publish(label: str, payload: dict[str, Any]) -> None:
    """把子代理的动静转告给外面。

    走的还是那条交互通道 —— 它本来就是「运行内部往外说话」的路子。没有通道
    （单元测试、纯脚本调用）时静默丢弃：子代理照样跑，只是外面看不到过程。

    `label` 跟着一起出去，标明**是哪个子代理**在动：并行时几个子代理的事件交错着来，
    没有它，看板显示的是一团混在一起的动静。
    """
    channel = interaction.current()
    if channel is not None:
        channel.publish(("subagent", {"agent": label, **payload}))


@dataclass(frozen=True)
class _Carried:
    """要带进工作线程的那几样运行上下文（每一样的后果见模块开头那张表）。"""

    environment: RunEnvironment
    channel: Interaction | None
    recorder: ChangeRecorder | None
    # 父级已经在第几层。**必须在父线程里取好**：`_depth` 同样是 ContextVar，worker 里
    # 读到的是默认值 0 —— 看起来就像「父级从没派过子代理」，那层「只允许一层」的护栏
    # 也就跟着失效了
    depth: int


def _capture() -> _Carried | None:
    """抓一份当前线程的运行上下文；不在一次运行里时返回 None。"""
    from quill_agent import agent

    environment = agent.current_environment()
    if environment is None:
        return None
    return _Carried(
        environment=environment,
        channel=interaction.current(),
        recorder=changes.current(),
        depth=_depth.get(),
    )


@contextmanager
def _bound(carried: _Carried) -> Iterator[None]:
    """在当前线程里绑好那几样 ContextVar，退出时按绑定的**逆序**解开。

    三样都得绑，理由见模块开头 —— 漏一样不是「效果差一点」，而是一个具体的故障：
    子代理根本跑不起来、看板全白、或者它改过的文件悄悄不进「还原」快照。
    """
    from quill_agent import agent

    undo: list[tuple[Any, Token]] = [
        (agent.deactivate_environment, agent.activate_environment(carried.environment))
    ]
    if carried.channel is not None:
        undo.append((interaction.deactivate, interaction.activate(carried.channel)))
    if carried.recorder is not None:
        undo.append((changes.deactivate, changes.activate(carried.recorder)))

    try:
        yield
    finally:
        for restore, token in reversed(undo):
            restore(token)


def _label(task: str) -> str:
    """给子代理起个短标签（任务开头的几个字）。

    并行时几个子代理的事件交错着来，看板上得能分清谁在干什么 —— 而任务本身就是它最
    自然的身份。截断是因为它只是个**标签**：看板一行放不下整段任务描述。
    """
    clean = " ".join(task.split())
    if len(clean) <= LABEL_MAX_CHARS:
        return clean
    return clean[:LABEL_MAX_CHARS] + "…"


def _run_child(task: str, carried: _Carried, label: str) -> tuple[str, RunStats]:
    """跑一个子代理，返回（结论原文, 它的账本）。

    单个和并行共用这一个实现 —— 差别只在**谁调它**（父线程 / 线程池），子代理本身怎么
    跑是一样的。前提是当前线程已经绑好环境：父线程本来就绑着，worker 靠 `_bound`。

    **结论不在这里加工**：空结论要不要转成一句提示、带不带 token 数，是调用方的事 ——
    单个和并行的排版不一样。账本同理，由调用方合并：并行时各线程自己 merge 进父级是
    读-改-写，并发下会丢。
    """
    # 延迟导入：`quill_agent.agent` 在模块级就 import 了 `quill_agent.tools`（取 registry），
    # 而本模块是在 `tools/__init__` 里被导入的 —— 两边都写模块级 import，会拿到一个
    # 只执行了一半的 agent 模块（`run_agent_stream` 那时还没定义）。放进函数体，
    # 等真正调用时两边都已加载完
    from quill_agent import agent

    if interaction.cancelled():
        return "", agent.RunStats()

    environment = carried.environment
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

    # 子代理有自己的账本，跑完由调用方并回父级
    nested_stats = agent.RunStats()
    token = _depth.set(carried.depth + 1)
    text_parts: list[str] = []

    try:
        # 上次往外播报的时刻，心跳按它计时（每个子代理各算各的）
        last_beat = time.monotonic()
        for item in agent.run_agent_stream(
            prompt=f"{BRIEF}\n任务：{task}",
            files=[],
            mode=None,  # 真正的上下文由 context 直接给，mode 在这里没有意义
            context=nested,
            choice=environment.choice,
            history=[],  # 空历史 = 干净的上下文，这正是子代理的意义所在
            stats=nested_stats,
            token_budget=environment.token_budget,
        ):
            # 模型请求或工具调用本身未必可中断；一旦当前调用返回并产出下一项，
            # 就停止子代理，避免继续发下一次模型请求或执行下一项工具调用。
            if interaction.cancelled():
                _publish(label, {"type": "notice", "text": "子代理已取消。"})
                return "", nested_stats

            if isinstance(item, agent.ToolStart):
                # 播 ToolStart（执行**前**）而不是 ToolStep（执行**后**）：两者带的字段
                # 一样，但需要被看见的是**执行中**那段静默 —— 耗时全在前面，播一句
                # 「跑完了」对「是不是卡住了」这个疑问没有帮助。父级也是这个道理。
                _publish(label, {"type": "tool", "name": item.name, "arguments": item.arguments})
                last_beat = time.monotonic()
            elif isinstance(item, agent.Notice):
                _publish(label, {"type": "notice", "text": item.text})
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
                    _publish(label, {"type": "notice", "text": "子代理正在工作…"})
    finally:
        _depth.reset(token)

    return "".join(text_parts).strip(), nested_stats


def _in_thread(task: str, carried: _Carried, label: str) -> tuple[str, RunStats]:
    """线程池里的一个子代理：先绑上下文，再跑。"""
    with _bound(carried):
        return _run_child(task, carried, label)


_NO_CONCLUSION = (
    "子代理没有给出结论（它可能一直在调工具，或者被工具轮预算切断了）。"
    "可以换个说法再派一次，或者你自己查。"
)


@registry.tool(
    description=(
        "派子代理去独立完成具体的事，可以一次派几个**同时**跑，等它们都跑完，"
        "把每份结论带回来。"
        "**子代理有自己的上下文** —— 读过的文件、搜过的结果都不会占你的上下文，"
        "所以它适合那种「过程很长、但你只关心结论」的活："
        "翻遍代码库找出某类调用点、读一批文件回答一个问题、摸清一个模块的结构。"
        "反过来，一两下就能查清的事别用它 —— 派一次的开销比你自己查大。"
        "几件互不依赖的活一起派，总耗时约等于最慢的那一个、而不是它们相加："
        "同时摸清三个模块、同时查几个不相关的报错。**只派一个就传一个元素。**"
        "**有依赖就别用**：如果第二件事要先看第一件事的结论才知道怎么做，"
        "那本来就该分两轮，硬并行只会让它们各自对着不完整的信息猜。"
        "每个任务都要**自包含**（子代理看不到你和用户的对话）："
        "写清要回答什么、范围在哪、结论要什么形式。它不能再派子代理（只允许一层）。"
        f"最多 {MAX_PARALLEL_AGENTS} 个真正同时跑（多出来的排队等），"
        f"一次最多派 {MAX_SUBAGENT_TASKS} 个。"
    ),
    category="subagent",
    parameters={
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "要派出去的任务，每个都必须自包含（子代理看不到你的对话）。"
                    "写清要回答什么、范围在哪、结论要什么形式。只有一件事就传一个元素"
                ),
            }
        },
        "required": ["tasks"],
    },
)
def spawn_agents(tasks: list[str]) -> str:
    """并行派多个子代理，等它们全部跑完，把每份结论带回来。"""
    # 逐项清洗：模型偶尔会塞进空串或只有空白的项
    cleaned = [item.strip() for item in (tasks or []) if isinstance(item, str) and item.strip()]
    if not cleaned:
        return "任务是空的。请说清要让子代理做什么。"

    if len(cleaned) > MAX_SUBAGENT_TASKS:
        return (
            f"一次最多派 {MAX_SUBAGENT_TASKS} 个子代理（收到 {len(cleaned)} 个）。"
            "把相关的几件合并成一个任务，或者分批派。"
        )

    carried = _capture()
    if carried is None:
        return "当前不在一次 Agent 运行里，派不了子代理。"

    if carried.depth > 0:
        return "子代理不能再派子代理（只允许一层）。请自己完成这件事。"

    labels = [_label(item) for item in cleaned]
    started = time.monotonic()
    outcomes: list[tuple[str, RunStats | None]] = [("", None)] * len(cleaned)

    # 超出上限的那些在池子里**排队**，而不是当场拒掉：模型可能只是没估准数量，
    # 多等一会儿就都跑完了；拒掉的话它会换个法子再试一遍，反而更贵
    pool = ThreadPoolExecutor(
        max_workers=min(len(cleaned), MAX_PARALLEL_AGENTS),
        thread_name_prefix="quill-subagent",
    )
    next_index = 0
    pending = {}

    def submit_next() -> bool:
        nonlocal next_index
        if next_index >= len(cleaned) or interaction.cancelled():
            return False
        future = pool.submit(_in_thread, cleaned[next_index], carried, labels[next_index])
        pending[future] = next_index
        next_index += 1
        return True

    for _ in range(min(len(cleaned), MAX_PARALLEL_AGENTS)):
        if not submit_next():
            break

    cancelled = False
    try:
        while pending:
            # `as_completed()` 没有取消检查点；短超时轮询让 SSE 断开后立刻停止等待。
            done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            if interaction.cancelled():
                cancelled = True
                for future in pending:
                    future.cancel()
                break

            for future in done:
                index = pending.pop(future)
                try:
                    outcomes[index] = future.result()
                except Exception as exc:  # noqa: BLE001 —— 一个子代理崩了不该拖垮整批
                    outcomes[index] = (f"（这个子代理出错了：{exc}）", None)
                submit_next()
    finally:
        # 取消时不能等待 worker：这会重新引入“已经取消却仍等所有子代理结束”的阻塞。
        # 已运行任务会在 `_run_child()` 检查点退出；未开始的任务从线程池队列移除。
        pool.shutdown(wait=not cancelled, cancel_futures=cancelled)

    if cancelled:
        return "已取消并行子代理；未开始的任务已取消，运行中的任务会在当前调用结束后停止。"

    # 账本由父级**统一**合并：各线程自己 merge 进父级是读-改-写，并发下会丢。
    # 放在这里还有一个好处 —— merge 的次数和结果顺序无关，重跑一遍也是同一个账
    total = 0
    for _, child_stats in outcomes:
        if child_stats is not None:
            carried.environment.stats.merge(child_stats)
            total += child_stats.total_tokens

    elapsed = time.monotonic() - started
    blocks = [f"{len(cleaned)} 个子代理的结论（并行跑了 {elapsed:.0f} 秒，共 {total} tokens）："]
    for index, (label, (answer, _)) in enumerate(zip(labels, outcomes, strict=True), start=1):
        blocks.append(f"\n### {index}. {label}\n{answer or _NO_CONCLUSION}")

    return "\n".join(blocks)
