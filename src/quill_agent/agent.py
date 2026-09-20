"""Agent 循环：组装上下文 → 请求模型 → 执行工具 → 直到得出答案。

这一层不依赖界面：

    输入：本轮输入 + 模式 + 模型选择 + 历史
    输出：一个事件流（文本增量 / 思维链增量 / 工具调用记录 / 系统提示），
          由界面决定怎么渲染

上下文的组装顺序（稳定 → 易变），目的是让 system prompt 尽量稳定：

    system:  身份 → 能力 → 工具策略 → 工作流程 → 输出规范 → 约束
    system:  记忆清单（有启用中的记忆时才发）
    system:  技能清单（skills/ 里有技能时才发）
    user:    运行时上下文（时间、目录、附件）+ 本轮问题

记忆和技能各是**一条独立的 system 消息**（见 _run_stream 里
build_memory_block / build_skill_catalog 那两段），不是拼进第一条里的。

运行时信息刻意放在 user 消息里而不是 system prompt 里 —— 它每轮都变，
放进 system 会让前缀缓存永远失效。

关于流式：一次流式响应里可能**同时**有文本增量和工具调用增量，所以要做双轨处理 ——
文本可以立刻往外吐，工具调用只能累积（分片到达，中途是残缺 JSON），
必须等流结束才能执行。
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime

from openai import OpenAI

from quill_agent import interaction
from quill_agent.config import get_settings
from quill_agent.memory import MemoryStore
from quill_agent.models import Mode, ModelChoice
from quill_agent.preferences import MAX_RUN_TOKENS_KEY, PreferenceStore
from quill_agent.prompts import PromptLibrary
from quill_agent.skills import SkillLibrary
from quill_agent.store import (
    PromptGroupStore,
    SkillGroupStore,
    ToolGroupStore,
)
from quill_agent.tools import registry
from quill_agent.tools.files import (
    SavedAttachment,
    current_work_dir,
    image_data_url,
    save_attachments,
)
from quill_agent.tools.todo import TodoBoard

# 一轮对话里最多允许执行几「轮」工具调用，防止模型陷入死循环。
# 注意它计的是工具轮次而不是请求次数：预算用尽后还会再发一次
# **不带工具**的请求让模型收尾，所以最多请求 MAX_ITERATIONS + 1 次。
#
# 计「轮」而不是「次」：模型一轮里可以同时请求好几个工具调用，那一批只花一格预算。
#
# 从 5 提到 10，是被 plan 流程逼出来的：一轮「先调研 → 交计划 → 等批准 → 动手 →
# 验证」真实要 5～6 格（调研那几轮常一次读好几个文件，所以还好），留 5 格时
# 计划一批准就快到顶了，模型会被迫在半路上收尾。10 格既容得下，也仍然是个
# 有意义的防死循环上限 —— 它的目的是兜住跑飞，不是省那几次请求。
MAX_ITERATIONS = 10

# 技能清单要求模型「用 read_skill 读取正文」，所以只要模式里给了技能，
# 这个工具就必须在场 —— 否则模型会去调一个不存在的工具。
# 用户在工具组里漏选它也不算错：技能能用比「严格执行工具组」更重要。
SKILL_READER_TOOL = "read_skill"

# 单次请求模型的超时（秒）
REQUEST_TIMEOUT = 120.0

# 发给模型的历史最多保留多少条会话记录。
# 按「记录」而不是「消息」计数：一条记录若含工具调用，展开后是一组消息，
# 按记录截断才不会把 assistant.tool_calls 和对应的 tool 消息从中间切开。
#
# 这是**模型窗口未知时的兜底**。窗口配过的时候改用 token 预算（见 HISTORY_BUDGET_RATIO）：
# 条数和 token 完全不成比例 —— 20 条闲聊是 2k token，20 轮读代码的可以是 20 万。
# 只按条数截，结果就是「会不会撑爆窗口全看运气」。
MAX_HISTORY_RECORDS = 20

# 历史最多能占模型窗口的多少比例。剩下的留给 system 提示词、技能清单、记忆、
# 本轮输入和输出 —— 长模式下前面那几样加起来也可能上几万 token，取一半是保守值。
HISTORY_BUDGET_RATIO = 0.5

# 估算 token 时每个 token 按几个字符折算。**不引 tokenizer**：它对一个本地小应用
# 太重（额外几百 KB 依赖 + 首次加载开销），而这里只需要「别超窗口」这个量级的精度。
#
# 取 2 而不是常见的 3~4：中文约 1 字符 ≈ 0.7 token，英文/代码约 3~4 字符 1 token，
# 折中后偏保守。**宁可贵估**（少带一点历史）也不能低估 —— 低估会让请求直接超窗被拒。
CHARS_PER_TOKEN = 2

# 单条记录的最低估算值：再短的消息也有 role、分隔符这些消息框架开销
MIN_RECORD_TOKENS = 8

# 历史因为预算被截掉时，在它前面插的这一句 —— 让模型知道自己看到的不是全部。
#
# 没有这一句，模型会对着残缺的历史继续作答，甚至编出「我们之前说过……」：
# 它不是在编，是它真的看不到。同时必须指出取回的办法（recall_history），
# 否则「我看不到」对模型来说就是一条死路。
HISTORY_OMITTED_NOTE = (
    "（更早的对话因超出上下文预算已被省略，你看到的不是全部历史。"
    "需要回忆其中内容时用 recall_history 工具按关键词检索原文，不要凭猜测作答。）"
)

# ── 摘要：把超预算的早期历史压成一段 ────────────────────────────────────────

# 压缩后至少保留多少条**原文**（最近的）。摘要负责很久以前，原文负责正在进行 ——
# 正在做的事最怕失真：模型下一步要动的就是那个文件，而摘要里的路径未必还准。
SUMMARY_KEEP_RECENT = 8

# 至少有多少条可压才值得压。只超出一两条时压缩不划算：摘要本身也占上下文，
# 还要多花一次模型调用 —— 那种情况直接按预算丢更省事。
SUMMARY_MIN_RECORDS = 3

# 摘要正文的字符上限。它会长期留在上下文里（每次请求都带着），不能任它长；
# 提示词里也把这个字数告诉模型，两头一起约束。
SUMMARY_MAX_CHARS = 1200

# 拼给摘要模型的工具结果片段长度：要的是要点，不需要原始全文
SUMMARY_STEP_CHARS = 300

# 生成摘要的超时（秒）。它比一轮对话短得多：失败了就走降级路径（不压缩），
# 让用户为一个「压缩请求」等两分钟是不合理的。
SUMMARY_TIMEOUT = 60.0

# 单条工具结果回放给模型时的长度上限。读大文件的工具结果动辄上万字符，
# 原样带入会让历史迅速膨胀；这里只截断回放的那一份，
# 界面上展示的完整结果仍保存在会话文件里。
MAX_TOOL_RESULT_CHARS = 2000

# 技能清单里每条描述的展示上限。清单一到就要常驻上下文，
# 放任它变长就等于把「按需加载」又改回了「全量注入」。
MAX_SKILL_DESCRIPTION_CHARS = 120

# 模型偶尔不把工具调用放进 `tool_calls` 字段，而是当成正文吐出来（DeepSeek 系的
# 原生标记漏进 content，另一些模型漏成 XML 风格的标签）。这时 `delta.tool_calls`
# 是空的，循环会把这一轮误判成「模型给完了最终回答」—— 工具没执行、审批没弹出，
# 用户只看到一段乱码，还以为是自己卡住了。
#
# 每个标记只取该格式里最独特的那一段，避免把正常回答误判成泄漏。
TOOL_CALL_LEAK_MARKERS = (
    "<\uff5c\uff5cDSML\uff5c\uff5c",  # DeepSeek 原生标记（竖线是全角）
    "</invoke>",  # XML 风格的调用块
    "<function_calls>",  # 另一种常见的调用块写法
)

# 正文要压住多少个字符才往外吐：标记可能被切在相邻两个分片之间
# （「</inv」+「oke>」），不留这一小段尾巴就认不出来了。
# 代价只是每个分片的末尾晚几毫秒显示。
_HOLD_CHARS = max(len(marker) for marker in TOOL_CALL_LEAK_MARKERS)


def _leak_index(text: str) -> int:
    """正文里第一个工具调用泄漏标记的位置；没有就返回 -1。"""
    hits = [text.find(marker) for marker in TOOL_CALL_LEAK_MARKERS]
    positions = [hit for hit in hits if hit != -1]
    return min(positions) if positions else -1


@dataclass(frozen=True)
class ToolStart:
    """一个工具即将开始执行。

    存在的唯一理由是**填补那段静默**：`ToolStep` 是执行完之后才产的，而中间那几秒到
    几十秒（联网搜索、扫大目录、跑命令）事件流一个字都不吐 —— 界面只能干等，用户
    分不清是在跑还是卡住了。

    它不带结果，也不该被界面当成一条记录存下来：真正的记录是随后的 `ToolStep`。
    """

    name: str
    arguments: str


@dataclass(frozen=True)
class ToolStep:
    """一次工具调用的记录，供界面展示。

    Attributes:
        elapsed: 这次调用花了多久（秒）。工具慢不慢，只能从它看出来。
    """

    name: str
    arguments: str
    result: str
    elapsed: float = 0.0


@dataclass
class RunStats:
    """一轮对话的资源消耗。

    刻意做成可变的、由调用方创建后交给 run_agent_stream 就地填充：生成器没法
    「返回」值，而用量和耗时都要等流结束才知道，只能这样把结果带出来。

    Attributes:
        prompt_tokens / completion_tokens / total_tokens: 接口返回的用量，
            **一轮里请求了多次就累加多次**。服务不支持流式用量时不会带这些值，
            三项都保持 0。
        context_tokens: 最后一次请求的输入量，也就是这一轮结束时**上下文实际有多大**。
            它和 `prompt_tokens` 是两个口径，别混用：每轮都要把同一份上下文重发
            一遍，累加起来是账单，只有「最后一次」才是窗口占用。界面上的用量仪表盘
            读这个，用量页读那三个累加值。
        elapsed: 整轮耗时（秒），包含工具执行的时间。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    context_tokens: int = 0
    elapsed: float = 0.0

    def add_usage(self, usage) -> None:
        """记下一次请求的用量 —— 一轮里可能请求模型好几次。

        计费三项累加；`context_tokens` 取最后一次（覆盖）。
        """
        if usage is None:
            return

        prompt = getattr(usage, "prompt_tokens", 0) or 0
        self.prompt_tokens += prompt
        self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
        self.total_tokens += getattr(usage, "total_tokens", 0) or 0

        # 没拿到 prompt_tokens 就别覆盖：网关偶尔不发用量分片，抹成 0 会让仪表盘
        # 从「上次的读数」直接掉到 0，看着像上下文被清空了
        if prompt:
            self.context_tokens = prompt

    def merge(self, other: RunStats) -> None:
        """把另一轮的用量并进来（目前只有子代理会走这条）。

        **只并用量，不并耗时。** 子代理的耗时本来就包含在父级的 `elapsed` 里
        （父级那一次工具调用就是在等它跑完），再并一次就成了双倍。
        用量则必须并 —— 不并的话那些 token 花了钱却不出现在用量页上。

        `context_tokens` 也不并：子代理的上下文是它自己那一份，不是父级的。
        父级下一次请求会把它覆盖成正确的值 —— 那时子代理的结论已经作为工具结果
        进了父级的上下文，本来就该算进去。
        """
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens


@dataclass(frozen=True)
class Notice:
    """系统提示：配置缺失、调用失败、模型空回答这类情况。

    它**不是模型输出**，所以和文本增量分开产出：界面可以单独渲染（标黄），
    也不会被当成对话内容写进会话历史 —— 否则「请先选择模型」这种提示会变成
    模型「说过的话」，下一轮又被塞回上下文里。
    """

    text: str


@dataclass(frozen=True)
class SummaryMade:
    """刚把一段早期历史压成了摘要。

    它**要落盘**（不是过程数据）：摘要必须活过这一轮，否则下一轮重新组装上下文时
    又得从头压一遍 —— 每次都白花一次模型调用。所以调用方收到它应当把它追加进会话文件，
    和助手消息一样是一条记录。

    界面上它对应一张「已压缩 N 条」的卡片：不告诉用户的话，下次打开会话发现模型不记得
    前面的事，只会以为是把数据弄丢了。而**原文一条都没删** —— 截断只影响发给模型多少。
    """

    content: str
    """摘要正文。"""
    covers: int
    """覆盖到第几条记录（1-based，含）—— 它之后的记录仍以原文发送。"""
    saved: int
    """估算省下的 token 数，只用于界面显示。"""


@dataclass(frozen=True)
class ReasoningDelta:
    """思维链的一个增量片段（推理模型专有）。

    它和正文一样是流式到达的，所以也按增量产出，由界面累积渲染。
    但两者性质不同：思维链是模型的内部过程，只用于展示，**不进历史** ——
    回传它既浪费上下文，也可能影响后续推理。
    """

    text: str


@dataclass(frozen=True)
class Usage:
    """一次请求的用量播报，供界面在**跑的过程中**刷新读数。

    为什么需要它：一轮里模型会被请求多次（每执行完一轮工具就要再问一次），而上下文
    每轮都在长。用量本来只随 `RunStats` 在结束时一起给出去 —— 界面在整个跑的过程中
    只能显示上一轮的旧值，跑完才「啪」地跳一下。所以每拿到一次用量就播报一次。

    只带 `context_tokens`：它是**这次请求的输入量**，也就是「上下文现在有多大」，
    正是仪表盘要的那个数。累加的账单三项不给 —— 那要等这一轮结束才准，
    而实时界面上也不需要它。
    """

    context_tokens: int


@dataclass(frozen=True)
class AgentResult:
    """一轮 Agent 对话的结果。

    Attributes:
        text: 最终回答；模型一个字都没说时是空字符串。
        steps: 中间发生过的工具调用，按发生顺序排列。
        notices: 系统提示。界面单独渲染，不进入下一轮的上下文。
        reasoning: 这一轮的思维链全文（推理模型才有）；只用于回看。
        stats: 本轮的用量与耗时，供界面展示和排查。
        todos: 这一轮列过的任务清单（`todo_write` 的最后一次提交）；没列过就是空列表。
            存的是 payload 形状而不是 `TodoItem`，因为它下一步就是原样落盘、
            原样推给前端，中间不再有人读它的字段。
    """

    text: str
    steps: list[ToolStep] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    reasoning: str = ""
    stats: RunStats = field(default_factory=RunStats)
    todos: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class ModeContext:
    """模式解析后的结果：这一轮到底用哪些提示词 / 工具 / 技能 / 记忆。

    模式存的是「引用哪个组」，真正组装上下文时要用的是组里的**成员**，
    中间这层解析单独拎出来：解析只需要读一次存储，而组装要用到的地方有四处。
    """

    prompts: list[str] = field(default_factory=list)  # 提示词 id 列表
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    memory_enabled: bool = True
    # 其中每次调用都要用户点头的工具名。是 tools 的子集（保存时校验过）。
    confirm: frozenset[str] = frozenset()
    # 这一轮用的技能组的 id。**运行本身不用它做任何判断**，只为「回话」而带：
    # 技能可能是运行中新建的（create_skill），而技能给不给由组决定 ——
    # 新建的技能不在组里就不会生效，得让模型知道该请用户去哪儿勾
    # （见 tools/builtin._skill_group_hint）。组被删了就留空
    skill_group_id: str = ""


@dataclass(frozen=True)
class RunEnvironment:
    """一次运行的环境：解析后的模式、模型选择、账本、清单板。

    给「运行内部还需要再跑一轮」的场景用 —— 目前只有子代理。它要照搬父级的
    `context`（同一个身份、同一套工具，只是换一份干净的上下文），用同一个模型，
    还要把用量记到父级账上。

    `stats` 和 `todos` 都是为了绕开同一件事：生成器没法「返回」值，只能由调用方拿一个
    可变对象进来、跑完再读。区别在于账本每次都会被读（用量要落盘），而清单板**大多数
    时候是空的** —— 模型没列过清单就一直是空的，这不是异常。

    为什么用 ContextVar：工具函数的签名里只有参数本身（`registry.execute(name, args)`），
    拿不到「当前这一轮」的任何东西。和 `interaction` 是同一个理由、同一套做法。
    """

    context: ModeContext
    choice: ModelChoice | None
    stats: RunStats
    todos: TodoBoard
    # 这一轮的历史记录，**全量、未截断**：给 recall_history 工具用。
    #
    # 给全量而不是截断后的那份，是因为那个工具的价值恰恰在于「捞回被截掉的部分」——
    # 截断只影响发给模型多少，不影响能取回多少（见 build_history_messages）。
    history: list[dict] = field(default_factory=list)


_environment: ContextVar[RunEnvironment | None] = ContextVar("quill_environment", default=None)


def current_environment() -> RunEnvironment | None:
    """当前这一轮的环境；不在运行里（测试、直接调工具）时返回 None。"""
    return _environment.get()


def resolve_mode(mode: Mode | None) -> ModeContext:
    """把模式解析成这一轮实际要用的资源。

    模式里的每一类都是**可选的**：没选（空 id）就是这一类什么都不给 ——
    所以「纯问答模式」是四个都空，而不是一种特殊取值。

    三类组按 id 查不到（组被删了）时按空处理，不报错：用户看到的是
    「这个模式的技能没了」，而不是一轮跑不起来的对话。

    Args:
        mode: 选中的模式；None 表示没有模式（什么都不给）。
    """
    if mode is None:
        return ModeContext(prompts=[], tools=[], skills=[], memory_enabled=False)

    settings = get_settings()

    prompts: list[str] = []
    if mode.prompt_group_id:
        group = PromptGroupStore(settings.prompt_groups_path).get(mode.prompt_group_id)
        if group is not None:
            prompts = list(group.prompts)

    tools: list[str] = []
    confirm: frozenset[str] = frozenset()
    if mode.tool_group_id:
        group = ToolGroupStore(settings.tool_groups_path).get(mode.tool_group_id)
        if group is not None:
            tools = list(group.tools)
            # 只认在组里的那些：即使存储层被手工改出「要求确认一个不在场的工具」，
            # 这里也不过是多一条永不命中的判断，不该让它影响别的工具
            confirm = frozenset(name for name in group.confirm if name in set(tools))

    skills: list[str] = []
    if mode.skill_group_id:
        group = SkillGroupStore(settings.skill_groups_path).get(mode.skill_group_id)
        if group is not None:
            skills = list(group.skills)

    # 给了技能就一定给出读技能的工具，理由见 SKILL_READER_TOOL 的注释
    if skills and SKILL_READER_TOOL not in tools:
        tools = [*tools, SKILL_READER_TOOL]

    return ModeContext(
        prompts=prompts,
        tools=tools,
        skills=skills,
        memory_enabled=mode.memory_enabled,
        confirm=confirm,
        skill_group_id=mode.skill_group_id,
    )


def build_system_prompt(prompt_ids: list[str]) -> str:
    """把选中的提示词正文拼成 system prompt。

    顺序按 `PROMPT_CATEGORIES`（身份 → 能力 → 工具策略 → 工作流程 → 输出规范 →
    约束），同一类别内按名字排序 —— 与用户在界面上怎么排、什么时候勾的**无关**。
    顺序稳定，同一模式下拼出来的内容才完全一致，前缀缓存才有意义。

    一个类别下可以有多条提示词（引用不再限「一类一条」），它们会按名字顺序接着拼。

    Args:
        prompt_ids: 提示词 id 列表。空列表表示不带系统提示词（纯问答）。

    Returns:
        拼好的 system prompt；一条都没读到（比如全被删了）时返回空字符串。
    """
    if not prompt_ids:
        return ""

    library = PromptLibrary(get_settings().prompt_dir)
    wanted = set(prompt_ids)

    # list_items 已经按「分类顺序 + 名字」排好了，直接筛出来遍历即可
    parts: list[str] = []
    for item in library.list_items():
        if item.id not in wanted:
            continue
        content = library.read(item.id)
        if content and content.strip():
            parts.append(content.strip())

    return "\n\n".join(parts)


def build_memory_block() -> str:
    """拼「关于用户的已知信息」清单。

    和技能清单一样单独占一条 system 消息：记忆随时会变，拼进身份提示词的话，
    改一条记忆就把前面那段的缓存前缀一起废掉。

    Returns:
        清单文本；没有启用中的记忆时返回空串，调用方据此决定要不要加这条消息。
    """
    items = MemoryStore(get_settings().memory_path).enabled()
    if not items:
        return ""

    return "\n".join(
        [
            "关于用户的已知信息（已经确认过，不必再问）：",
            *(f"- {item.text}" for item in items),
        ]
    )


def build_skill_catalog(names: list[str]) -> str:
    """拼「可用技能」清单：只有名字和适用场景，正文让模型按需去取。

    这是整个技能机制的关键：常驻上下文的只有这一小段清单。技能正文动辄上千字，
    但只有模型判断用得上的那一个，才会通过 read_skill 工具被取回来 ——
    用不到的技能一个 token 都不花。

    Args:
        names: 模式（技能组）选中的技能名。技能不再有全局开关 ——
            **有没有技能由模式说了算**，和工具一样（见 `resolve_mode`）。

    Returns:
        清单文本；没有可用技能时返回空串，调用方据此决定要不要加这条消息。
    """
    if not names:
        return ""

    settings = get_settings()
    wanted = set(names)

    lines: list[str] = []
    for meta in SkillLibrary(settings.skills_dir).list_meta():
        if meta.name not in wanted:
            continue

        description = meta.description or "（作者未填写适用场景）"
        if len(description) > MAX_SKILL_DESCRIPTION_CHARS:
            description = description[:MAX_SKILL_DESCRIPTION_CHARS] + "…"
        lines.append(f"- {meta.name}：{description}")

    if not lines:
        return ""

    return "\n".join(
        [
            "可用技能（任务匹配其中某一项时，先用 read_skill 工具读取完整说明再动手）：",
            *lines,
        ]
    )


def build_user_message(prompt: str, attachments: list[SavedAttachment]) -> str:
    """把本轮问题与运行时上下文拼成一条 user 消息的**文本部分**。

    图片不在这里 —— 它没法变成文本，由 `image_content()` 以内容块的形式补进去。

    Args:
        prompt: 本轮用户输入。
        attachments: `save_attachments()` 的结果。
    """
    lines = [f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]

    # 工作目录：文件工具的根，也是它们的安全边界（见 tools/files.py）。
    # 必须用 current_work_dir() 而不是配置值 —— 界面上可能改过，
    # 告诉模型的和工具实际遵守的必须是同一个。
    lines.append(f"工作目录：{current_work_dir()}")

    if attachments:
        lines.append("本次附件（已存到工作目录）：")
        lines.extend(f"- {item.describe()}" for item in attachments)
        # 必须说清两条路：文本附件模型手上有现成的工具可以读，图片则已经给它了。
        # 不说明的话，它可能以为文本附件的内容也早就随消息给它了
        lines.append(
            "图片已经以画面形式提供，不要再用 read_file 去读它；文本类附件要用 read_file 读。"
        )

    return "\n".join([*lines, "", prompt])


def image_content(text: str, attachments: list[SavedAttachment]) -> str | list[dict]:
    """把 user 消息的正文与图片附件拼成请求体里的 `content`。

    **图片只能以这个方式交给模型。** 工具的结果契约是「返回一段文本」
    （见 tools/base.py 的 `execute`），所以没有任何一个工具能把图像递给视觉能力 ——
    这也是附件不做成 `read_attachment` 工具的原因。

    没有图片时**原样返回字符串**，请求体和以前一字不差：让所有调用方——以及模型侧的
    前缀缓存——不必为一个偶尔才用到的能力买单。

    只发**本轮**的图片，历史里的老图片不重发：一张截图一两千 token，而历史最多带
    `MAX_HISTORY_RECORDS` 条记录，全带着等于把每一轮的固定成本永久抬高。这和
    「附件是这一轮的输入」是同一个口径。
    """
    blocks: list[dict] = []
    for item in attachments:
        if item.path is None or not item.is_image:
            continue

        data_url = image_data_url(item.path)
        if data_url is None:
            continue  # 太大或读不了：退回「只有路径」的老行为，不因为一张图让整轮失败

        blocks.append({"type": "image_url", "image_url": {"url": data_url}})

    if not blocks:
        return text

    # 文字在前、图片在后：图片是「这段话在说什么」的补充，
    # 放在后面更贴近「先读要求、再看材料」的顺序
    return [{"type": "text", "text": text}, *blocks]


def _clip(text: str, limit: int) -> str:
    """超长文本截断，并说明原长度 —— 让模型知道信息是不完整的。"""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（内容过长已截断，原长 {len(text)} 字符）"


def _estimate_record_tokens(record: dict) -> int:
    """估算一条会话记录展开成 API 消息后占多少 token。

    正文、工具参数、工具结果都要算 —— 它们在请求里都是实打实的内容。工具结果按
    **回放时的上限**算（见 MAX_TOOL_RESULT_CHARS）：发出去的就是截断后的那份，
    按原长算会把它估得过大。
    """
    size = len(str(record.get("content") or ""))

    for step in record.get("steps") or []:
        if not isinstance(step, dict):
            continue
        size += len(str(step.get("name") or ""))
        size += len(str(step.get("arguments") or ""))
        size += min(len(str(step.get("result") or "")), MAX_TOOL_RESULT_CHARS)

    return max(MIN_RECORD_TOKENS, size // CHARS_PER_TOKEN)


def _trim_by_budget(records: list[dict], budget: int) -> list[dict]:
    """从最近一条往前留，直到预算用尽；返回保留下来的记录（原顺序）。

    以**记录**为单位取舍，理由同 `build_history_messages`：一轮里
    assistant(tool_calls) 和它的 tool 消息必须成对出现，切在中间会产生 API 拒绝的
    非法序列。

    最后一条无论多大都留着：它通常就是「上一轮发生了什么」，丢掉它模型直接失忆 ——
    那是截断最不该造成的结果。单条自己就超预算时（用户粘了一大段代码）也只能超。
    """
    kept: list[dict] = []
    used = 0

    for record in reversed(records):
        cost = _estimate_record_tokens(record)
        if kept and used + cost > budget:
            break

        kept.append(record)
        used += cost

    return list(reversed(kept))


def _estimate_text_tokens(text: str) -> int:
    """估算一段纯文本占多少 token（按字符折算，见 CHARS_PER_TOKEN）。"""
    return len(text) // CHARS_PER_TOKEN


def _estimate_records_tokens(records: list[dict]) -> int:
    """估算一组记录展开后占多少 token。"""
    return sum(_estimate_record_tokens(record) for record in records)


def _last_summary(records: list[dict]) -> tuple[int, str]:
    """找到最后一条摘要，返回（它覆盖到第几条, 摘要正文）。

    没有摘要时返回 `(0, "")`。「最后一条」是关键：滚动摘要下新摘要里已经并进了旧摘要的
    内容，所以只有最新那条算数，之前的都是它的前身。
    """
    covered = 0
    text = ""

    for index, record in enumerate(records, start=1):
        if record.get("role") == "summary":
            covered = index
            text = str(record.get("content") or "")

    return covered, text


def format_records_for_summary(records: list[dict]) -> str:
    """把记录拼成给摘要模型看的文本。

    工具结果要带上（但可以短）：需要被保留下来的「报错原文」「文件内容」往往只出现在
    工具的返回里，正文一个字都没提。
    """
    lines: list[str] = []

    for index, record in enumerate(records, start=1):
        role = {
            "user": "用户",
            "assistant": "助手",
            "summary": "上一版摘要",
        }.get(record.get("role"), "其它")

        content = str(record.get("content") or "").strip()
        if content:
            lines.append(f"[{index}] {role}：{content}")

        for step in record.get("steps") or []:
            if not isinstance(step, dict):
                continue
            result = _clip(str(step.get("result") or ""), SUMMARY_STEP_CHARS)
            lines.append(f"[{index}] 工具 {step.get('name') or '?'} 返回：{result}")

    return "\n".join(lines)


SUMMARY_PROMPT = """下面是一段对话记录。请把它压缩成一份要点摘要，供后续对话继续使用。

必须保留（有就写，没有就跳过）：
- 已经确定的决定和结论
- 涉及的文件路径、函数名、命令、参数值
- 未完成的计划和待办
- 用户的偏好、约束、明确要求
- 已知的错误现象和报错原文

可以丢弃：寒暄、重复的解释、试错过程、已被推翻的方案。

要求：中文，Markdown，{limit} 字以内。直接输出摘要正文，不要写「以下是摘要」之类的前言。

{previous}对话记录：
{records}"""


def summarize_history(
    *,
    client: OpenAI,
    model: str,
    records: list[dict],
    previous: str = "",
) -> str | None:
    """调模型把一段历史压成摘要；失败返回 None。

    **失败不抛异常**：压缩是锦上添花，它失败不该毁掉整轮对话 —— 调用方拿到 None
    就照旧按预算截断，信息少一点而已。「摘要没做好反而更糟」正是要避免的。
    """
    if not records:
        return None

    prompt = SUMMARY_PROMPT.format(
        limit=SUMMARY_MAX_CHARS,
        previous=f"上一版摘要（要把它也并进新摘要里）：\n{previous}\n\n" if previous else "",
        records=format_records_for_summary(records),
    )

    try:
        # 只传 create() 认的参数：`max_retries` / `base_url` 那些是**客户端构造时**的，
        # 塞到这里会直接 TypeError —— 而它会被下面的 except 吞掉，表现成「摘要总是失败」
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            timeout=SUMMARY_TIMEOUT,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001
        # 网络、超时、网关报错、返回结构不对 —— 全都不该让这一轮挂掉。
        # 这里刻意不区分：对调用方来说处理方式都是「这次不压」
        return None

    if not content:
        return None

    # 超长就截断：它会长期占着上下文，而模型不一定守字数
    return _clip(content, SUMMARY_MAX_CHARS)


def _split_for_summary(records: list[dict], budget: int) -> tuple[list[dict], list[dict]]:
    """把记录切成「该压缩的」和「保留原文的」两段。

    判据和截断用的是**同一把尺子**：`_trim_by_budget` 留不住的那些，正是要丢掉的 ——
    与其丢掉，不如先压成摘要。这样不会出现「明明超了却没压」或者「没超却白压一次」。

    `budget` 为 0（窗口没配）时**不压缩**：没有预算就说不清能省下多少，
    而摘要 + 本来就装得下的历史，可能反而更大。

    Returns:
        (要压缩的记录, 保留原文的记录)；第一个为空表示不用压。
    """
    if budget <= 0 or len(records) <= SUMMARY_KEEP_RECENT:
        return [], records

    head = records[:-SUMMARY_KEEP_RECENT]
    tail = records[-SUMMARY_KEEP_RECENT:]

    # 已经压过的不再压：只处理上次摘要之后的增量（滚动摘要）
    covered, _ = _last_summary(records)
    pending = head[covered:]

    if len(pending) < SUMMARY_MIN_RECORDS:
        return [], records

    # 预算本来就装得下 → 不值得多花这一次模型调用
    if _estimate_records_tokens(records) <= budget:
        return [], records

    return pending, tail


def _compact_if_needed(
    *,
    history: list[dict],
    budget: int,
    client: OpenAI,
    model: str,
) -> Iterator[Notice | SummaryMade]:
    """需要就把早期历史压成摘要，顺便 yield 过程事件；最后 return 组装用的记录列表。

    做成生成器而不是普通函数，是因为压缩要**当场调一次模型**（几秒到十几秒）。
    界面得能立刻看到「正在压缩」，而不是整轮对话卡在那里毫无反应。
    """
    to_compress, keep = _split_for_summary(history, budget)

    if not to_compress:
        return history

    covered, previous = _last_summary(history)
    yield Notice(f"上下文快满了，正在把更早的 {len(to_compress)} 条记录压缩成摘要……")

    summary = summarize_history(
        client=client, model=model, records=to_compress, previous=previous
    )

    if summary is None:
        # 降级：不压缩，照旧按预算截断。信息少一点，但这一轮照跑不误
        yield Notice("摘要没能生成（调模型失败或返回为空）。这一轮改按上下文预算裁剪历史。")
        return history

    covers = covered + len(to_compress)
    saved = max(0, _estimate_records_tokens(to_compress) - _estimate_text_tokens(summary))

    yield SummaryMade(content=summary, covers=covers, saved=saved)

    # 旧摘要不必带上了：它的内容已经并进新摘要（提示词里带了 previous）
    return [
        {"role": "summary", "content": summary, "covers": covers},
        *keep,
    ]


def build_history_messages(
    history: list[dict] | None, *, context_window: int = 0
) -> list[dict]:
    """把会话记录还原成 API 需要的消息序列。

    会话记录是界面口径：``{role, content, ts, steps?}``。
    API 口径不同 —— 一轮里如果调用过工具，实际是一组消息：

        assistant(content, tool_calls) -> tool(tool_call_id, content) -> ...

    这里把记录里的 steps 还原成这组消息，模型下一轮才能看到
    「上一轮我查了什么、结果是什么」。少了这一步，模型每轮都会失忆，
    反复调用同样的工具去重新获取已经拿到过的信息。

    截断发生在**展开之前**（按记录取舍）：先展开再截断，可能把
    assistant.tool_calls 和它的 tool 消息切开，产生 API 无法接受的非法序列。

    `history` 里可能有 `role == "summary"` 的记录（压缩产物，见 `_compact_if_needed`）：
    最新那条之前的所有记录都由它替代，**只有它之后的记录才按原文发送**。

    留多少条取决于 `context_window`：

        知道窗口 —— 按 token 预算留（见 `_trim_by_budget`）。「最近 20 条」这种
                    和窗口大小无关的固定值，正是「一轮读代码就爆窗」的成因；
        不知道   —— 退回按条数上限（`MAX_HISTORY_RECORDS`）。**不猜窗口**：
                    猜小了白丢历史，猜大了请求被拒。

    Args:
        history: 会话记录列表；记录里的 ts 等展示字段会被忽略。
        context_window: 当前模型的上下文窗口（token）。0 表示不知道。

    Returns:
        可直接拼进请求体的消息列表。
    """
    messages: list[dict] = []

    all_records = list(history or [])

    # 摘要覆盖它之前的全部记录：那些不再单独发送（滚动摘要下只有最新那条算数）。
    # 它作为一条 system 消息放在最前面 —— 模型读到的顺序就成了
    # 「很久以前（摘要）→ 近期（原文）→ 本轮提问」
    covered, summary_text = _last_summary(all_records)
    if summary_text:
        messages.append(
            {
                "role": "system",
                "content": (
                    "（更早的对话已压缩成摘要；原文仍在会话记录里，必要时可检索）\n"
                    f"{summary_text}"
                ),
            }
        )

    # 摘要之后、还需要按预算取舍的那些记录
    after_summary = all_records[covered:]
    records = after_summary

    if context_window > 0:
        records = _trim_by_budget(records, int(context_window * HISTORY_BUDGET_RATIO))
    # 这里不能写成 records[-MAX_HISTORY_RECORDS:] —— 上限为 0 时 -0 等于 0，
    # 切片会退化成「全部保留」，语义正好相反。
    elif MAX_HISTORY_RECORDS > 0:
        records = records[-MAX_HISTORY_RECORDS:]

    # 比对的是「摘要之后」那一段：被摘要覆盖掉的记录不算「被截掉」——
    # 它们的内容还在摘要里，两者不是一回事
    if len(records) < len(after_summary):
        messages.append({"role": "system", "content": HISTORY_OMITTED_NOTE})

    for index, record in enumerate(records):
        role = record.get("role")
        if role not in {"user", "assistant"}:
            continue

        content = record.get("content") or ""
        steps = [item for item in (record.get("steps") or []) if isinstance(item, dict)]

        if role == "user":
            if content:
                messages.append({"role": "user", "content": content})
            continue

        if not content and not steps:
            continue  # 空回答：发过去只会让模型困惑

        if not steps:
            messages.append({"role": "assistant", "content": content})
            continue

        # 自拟 tool_call id：它只需在这组消息内部自洽。
        # 与本轮实时产生的工具调用不会冲突（那个用服务端下发的 id）。
        calls = [
            {
                "id": f"history_{index}_{position}",
                "type": "function",
                "function": {
                    "name": step.get("name", ""),
                    "arguments": step.get("arguments") or "{}",
                },
            }
            for position, step in enumerate(steps)
        ]

        messages.append(
            {"role": "assistant", "content": content or None, "tool_calls": calls}
        )
        messages.extend(
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": _clip(step.get("result") or "", MAX_TOOL_RESULT_CHARS),
            }
            for call, step in zip(calls, steps, strict=True)
        )

    return messages


# 每个接口地址认不认 stream_options（流式响应里附带用量统计）。
#
# 少数网关不认这个参数、带上就直接报错，所以要探测一次并沿用 —— 不能每轮都白跑
# 一次注定失败的请求。值 False 表示「确认不支持」，没有这个键表示还没探测过。
#
# key 用 base_url 而不是一个全局布尔：认不认是**按服务商**不同的。用全局值的话，
# 一个不支持的网关会把所有连接一起拖下水 —— 之后切回支持的服务也拿不到用量统计。
_stream_usage_cache: dict[str, bool] = {}


def _open_stream(client, *, base_url: str, model: str, messages: list, tools):
    """发起一次流式请求，尽量让它带上用量统计。

    include_usage 不是所有 OpenAI 兼容服务都认。探测失败之后就**对这个地址**
    永久退回普通请求：用量统计是锦上添花，不能因为它让对话本身不可用。

    并发说明：chat 端点跑在线程池里，可能有两个请求同时探测同一个地址 ——
    它们会各发一次请求、写同一个结果。无害（最坏情况是多探测一次），
    所以没有加锁。
    """
    if _stream_usage_cache.get(base_url) is not False:
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                stream=True,
                stream_options={"include_usage": True},
            )
        except Exception:
            # 这里吞掉异常是故意的：真正的错误（鉴权、网络、模型名）会在下面那次
            # 普通请求里再抛一次，由调用方统一处理，不会丢掉错误信息
            _stream_usage_cache[base_url] = False
        else:
            _stream_usage_cache[base_url] = True
            return stream

    return client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        stream=True,
    )


def _accumulate_tool_calls(store: dict[int, dict], delta_calls) -> None:
    """把流式返回的工具调用分片累积起来。

    分片特点：同一个调用的 name / arguments 会分多次到达，必须按 index 累加；
    id 通常只在第一片里出现。中途任何时刻的 arguments 都可能是残缺 JSON，
    所以只能等整条流结束再解析执行。
    """
    for call in delta_calls:
        slot = store.setdefault(call.index, {"id": "", "name": "", "arguments": ""})

        if call.id:
            slot["id"] = call.id
        if call.function is None:
            continue
        if call.function.name:
            slot["name"] += call.function.name
        if call.function.arguments:
            slot["arguments"] += call.function.arguments


def _sorted_tool_calls(store: dict[int, dict]) -> list[dict]:
    """按 index 顺序取出累积好的工具调用。"""
    return [store[index] for index in sorted(store)]


# 确认题里「这次调用要做什么」的展示上限。用户要在几秒内看懂并做决定，
# 一屏之外的内容只会让人不看清就点「拒绝」
MAX_PREVIEW_CHARS = 600

# 预览时优先展示的字段，按「最能说明这次调用要干什么」排序。
# 整块参数 JSON 对用户是天书，而 command / path 一眼就懂
PREVIEW_KEYS = ("command", "path", "name", "pattern", "query", "url")


def _preview(name: str, arguments: str) -> str:
    """把一次工具调用压成用户能看懂的说明，给确认题当补充材料。"""
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError:
        args = None

    if not isinstance(args, dict):
        text = str(arguments)
    else:
        for key in PREVIEW_KEYS:
            value = args.get(key)
            if not isinstance(value, str) or not value.strip():
                continue

            rest = {k: v for k, v in args.items() if k != key}
            text = f"{key}：{value}"
            if rest:  # 其余参数照样给出来，别让用户以为就只有这一个
                text += "\n" + json.dumps(rest, ensure_ascii=False)
            break
        else:
            text = json.dumps(args, ensure_ascii=False)

    if len(text) > MAX_PREVIEW_CHARS:
        text = text[:MAX_PREVIEW_CHARS] + "…"

    return f"{name}\n{text}"


def _execute(slot: dict, confirm_names: frozenset[str]) -> str:
    """执行一次工具调用，必要时先让用户点头。

    **确认拦在 agent 这一层，而不是塞进工具里**：要不要确认是模式的配置
    （工具组的 `confirm`），工具自己不知道这件事。工具**自己**的判断走另一条路 ——
    例如 `run_command` 认出危险命令后主动调 `interaction.confirm()`，那是「这次调用
    本身有问题」，跟「这个工具要不要盯着用」是两码事，两者可以叠加。
    """
    name = slot["name"]
    if name not in confirm_names:
        return registry.execute(name, slot["arguments"])

    approved = interaction.confirm(
        text=f"是否允许调用「{name}」？",
        detail=_preview(name, slot["arguments"]),
    )

    if approved is True:
        return registry.execute(name, slot["arguments"])

    # 拒绝和「问不到」要分开说：前者是用户的决定，后者是这套配置在当前界面上
    # 根本没法用 —— 提示语不同，用户才知道下一步该干什么
    if approved is False:
        reason = "用户拒绝了这次调用"
    else:
        reason = "没能问到用户（等待超时，或当前界面不支持确认），已按拒绝处理"

    return f"{reason}。不要原样重试，请换一种做法，或告诉用户你需要什么。"


def run_agent_stream(
    *,
    prompt: str,
    files: list,
    mode: Mode | None,
    choice: ModelChoice | None,
    history: list[dict] | None = None,
    stats: RunStats | None = None,
    board: TodoBoard | None = None,
    context: ModeContext | None = None,
) -> Iterator[str | ReasoningDelta | ToolStart | ToolStep | Notice | Usage | SummaryMade]:
    """以流式方式跑一轮 Agent 对话。

    Args:
        prompt: 本轮用户输入。
        files: 上传的附件对象；会先落盘到工作目录，再把路径告诉模型。
        mode: 选中的模式（决定提示词 / 工具 / 技能 / 记忆）；None 表示什么都不给。
        choice: 选中的模型（连接配置 + 模型名）。
        history: 历史会话记录，元素形如 {"role": ..., "content": ...}；
            助手记录可带 "steps"（工具调用），会被还原成 tool 消息，
            并只保留最近 MAX_HISTORY_RECORDS 条，见 build_history_messages。
        stats: 由调用方提供的账本，跑完就地填好（生成器没法「返回」值）。
        board: 由调用方提供的清单板；模型每次调用 todo_write 就地更新它（同样是
            因为生成器没法「返回」值）。不给就自己建一个 —— 子代理走的正是这条路：
            它自己那份清单和父级没有关系，也不该混进父级的进度条里。
        context: **已经解析好的模式**；给了就不再解析 `mode`。子代理走这条路 ——
            它要照搬父级的提示词和技能，但工具集要去掉几样（见 tools/subagent.py）。
            没有这个参数的话，「给谁用哪些工具」就只能靠运行时的拒绝来兜，
            而一个「看得见却永远调不通」的工具比不给它更让人困惑。

    Yields:
        文本增量（str）/ 思维链增量（ReasoningDelta）/ 工具调用记录（ToolStep）/
        系统提示（Notice）/ 运行中的用量播报（Usage）。任何异常都转成 Notice 产出，
        不向上抛。

    最多请求模型 MAX_ITERATIONS + 1 次：前 MAX_ITERATIONS 次带工具，最后
    一次不带。预算用尽后强制模型基于已有信息收尾，避免出现「工具已经执行、
    副作用已经发生，结果却没机会被模型看到」的浪费。
    """
    tracker = stats if stats is not None else RunStats()
    todos = board if board is not None else TodoBoard()
    started = time.monotonic()

    resolved = context if context is not None else resolve_mode(mode)

    # 把这一轮的环境挂到当前线程上，给「运行内部要再跑一轮」的场景用（子代理）。
    # 子代理会自己再挂一层，退出时各自 reset —— 嵌套多少层都不会串
    token = _environment.set(
        RunEnvironment(
            context=resolved,
            choice=choice,
            stats=tracker,
            todos=todos,
            # 传全量：截断发生在组装请求的时候（见 build_history_messages），
            # 而 recall_history 要能捞回被截掉的那部分
            history=list(history or []),
        ),
    )

    try:
        yield from _run_stream(
            prompt=prompt,
            files=files,
            choice=choice,
            history=history,
            context=resolved,
            tracker=tracker,
        )
    finally:
        _environment.reset(token)
        # 答完了、出错了、撞上限了 —— 不管从哪条路出去，耗时都要补上
        tracker.elapsed = time.monotonic() - started


def run_token_limit() -> int:
    """本轮的开销上限（token）；0 表示不限制。

    这是**安全阀**，不是必配项：一轮死循环能烧多少钱，光靠 `MAX_ITERATIONS`
    的「次数」是管不住的 —— 每次请求的大小可以差两个数量级。

    读的是偏好文件（见 `preferences.MAX_RUN_TOKENS_KEY`），坏值一律当不限制：
    一个数字填错不该让对话直接跑不起来。
    """
    raw = PreferenceStore(get_settings().preferences_path).get(MAX_RUN_TOKENS_KEY)
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _run_stream(
    *,
    prompt: str,
    files: list,
    choice: ModelChoice | None,
    history: list[dict] | None,
    context: ModeContext,
    tracker: RunStats,
) -> Iterator[str | ReasoningDelta | ToolStart | ToolStep | Notice | Usage | SummaryMade]:
    """run_agent_stream 的真实实现。

    单独拆出来只是为了能用 try/finally 统一收尾：生成器里有好几处 return，
    挨个补耗时很容易漏掉，包一层最稳。
    """
    if choice is None:
        yield Notice("请先选择模型；若还没有模型，请到「模型管理」页面添加。")
        return

    if not choice.config.protocol.openai_compatible:
        yield Notice(f"暂未实现「{choice.config.protocol.label}」的调用。")
        return

    # 客户端先建好：下面的压缩要用它调一次模型生成摘要（见 _compact_if_needed）
    client = OpenAI(
        # 地址交给协议自己补全：留空时用默认地址（本机 Ollama 就是它），
        # 少写 http:// 或 /v1 也认 —— 见 Protocol.resolve_base_url
        base_url=choice.config.protocol.resolve_base_url(choice.config.base_url) or None,
        api_key=choice.config.api_key or "EMPTY",  # 本地服务通常不校验
        timeout=REQUEST_TIMEOUT,
        max_retries=1,
    )

    # ── ① 组装上下文 ──────────────────────────────────────────────
    # 模式已经由 run_agent_stream 解析好了（解析只做一次），这里只管按固定顺序拼装
    messages: list[dict] = []

    system_prompt = build_system_prompt(context.prompts)
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    # 记忆与技能清单各自单独占一条 system 消息，都不拼进上面那段。
    # 原因和「运行时上下文不放 system prompt」是同一个：这两段都会变
    # （改一条记忆、换一个技能组），跟身份提示词拼在一起的话，动一下就整段重来，
    # 前面那段的缓存前缀跟着作废。
    # 顺序是先「用户是谁」，再「这类活怎么干」。
    if context.memory_enabled:
        memory = build_memory_block()
        if memory:
            messages.append({"role": "system", "content": memory})

    catalog = build_skill_catalog(context.skills)
    if catalog:
        messages.append({"role": "system", "content": catalog})

    # 历史是「会话记录」口径（带 ts / steps 等展示字段），这里统一还原成 API 口径。
    #
    # 组装之前先看要不要把早期历史压成摘要：压完再组装，模型拿到的是
    # 「摘要 + 最近若干条原文」。压缩和截断用的是同一把尺子（超预算的那部分），
    # 所以不会出现「明明超了却没压」。窗口没配时预算为 0，两件事都不做。
    window = choice.config.context_windows.get(choice.model, 0)
    budget = int(window * HISTORY_BUDGET_RATIO)

    compacted = yield from _compact_if_needed(
        history=list(history or []),
        budget=budget,
        client=client,
        model=choice.model,
    )
    messages.extend(build_history_messages(compacted, context_window=window))

    # 附件先落盘再组装消息：模型要的是「工作目录里的路径」，
    # 而不是一个它根本访问不到的内存对象
    attachments = save_attachments(files)
    messages.append(
        {
            "role": "user",
            # 文本附件的路径走正文；图片走内容块（见 image_content）
            "content": image_content(build_user_message(prompt, attachments), attachments),
        }
    )

    # ── ② 工具菜单：只含模式里给的工具 ─────────────────────────────
    # 工具没有全局开关：给不给由模式的工具组决定（和技能一致）。
    # 空列表是合法结果 —— 纯问答模式就是一个工具都不给。
    tools = registry.schemas(only=context.tools)

    # ── ③ 循环 ────────────────────────────────────────────────────
    # 整轮下来模型是否真的输出过文本。用于兜底：一个字都没说时给个提示，
    # 否则界面上会是一个空气泡 —— 推理模型把输出预算全花在思考上时就会这样。
    emitted_text = False

    # 工具轮次预算：每执行一轮工具就减一。减到 0 之后请求里不再带 tools，
    # 模型只能基于已有信息收尾 —— 不会出现「工具执行了、副作用发生了，
    # 结果却没机会被模型看到」的浪费。
    tool_budget = MAX_ITERATIONS
    # 开销上限也在这里读一次：这一轮开始时的值说了算，
    # 中途改偏好文件不该影响正在跑的对话
    limit = run_token_limit()

    # 循环一定终止：每次迭代要么直接 return（拿到回答 / 预算已耗尽），
    # 要么把 tool_budget 减一。所以最多请求 MAX_ITERATIONS + 1 次。
    while True:
        # 用户按了停止。检查点放在循环顶部：这样「模型正在生成的这一次」也会被掐掉，
        # 而不是等它把这一轮跑完。没有通道（CLI / 单元测试）时永远是 False
        if interaction.cancelled():
            yield Notice("已取消这一轮。")
            return

        # 预算用尽就不再提供工具；空列表也不能传，部分服务不接受空的 tools
        available_tools = tools if tool_budget > 0 else None

        try:
            stream = _open_stream(
                client,
                base_url=choice.config.base_url,
                model=choice.model,
                messages=messages,
                tools=available_tools or None,
            )
        except Exception as exc:  # 网络、鉴权、模型名错误都归到这里
            yield Notice(f"调用模型失败：{exc}")
            return

        text_parts: list[str] = []
        tool_calls: dict[int, dict] = {}

        # 收到但还没外吐的正文尾巴（末尾可能正卡着一个泄漏标记的开头）
        pending = ""
        # 这一轮里模型是不是把工具调用写成了正文
        leaked = False

        # 双轨解析：文本立即外吐，工具调用只累积
        try:
            for chunk in stream:
                # 用量在流末尾单独一个 chunk 里，它通常是不带 choices 的
                usage = getattr(chunk, "usage", None)
                tracker.add_usage(usage)

                # 拿到就播报（见 Usage）：这一轮里上下文是在**长**的，攒到最后才给的话，
                # 界面整个过程都停在上一轮的读数上，跑完才跳一下
                if usage is not None and tracker.context_tokens:
                    yield Usage(context_tokens=tracker.context_tokens)

                if not chunk.choices:  # 有些服务会额外发一个只带用量的 chunk
                    continue

                delta = chunk.choices[0].delta

                # 推理模型的思考过程走独立字段。DeepSeek 系叫 reasoning_content，
                # 少数中转站叫 reasoning —— 都用 getattr 兜底：这不是 OpenAI 的
                # 标准字段，SDK 未必保留，取不到就静默跳过，不影响正文。
                reasoning = getattr(delta, "reasoning_content", None) or getattr(
                    delta, "reasoning", None
                )
                if reasoning:
                    yield ReasoningDelta(reasoning)

                if delta.content:
                    emitted_text = True

                    if not leaked:
                        pending += delta.content

                        cut = _leak_index(pending)
                        if cut != -1:
                            # 找到泄漏标记：它前面的人话照常留下，
                            # 后面的调用块一律丢掉 —— 留着只会污染正文和历史
                            leaked = True
                            flush, pending = pending[:cut], ""
                        elif len(pending) > _HOLD_CHARS:
                            # 标记可能跨分片，尾巴先压住不外吐
                            flush = pending[:-_HOLD_CHARS]
                            pending = pending[-_HOLD_CHARS:]
                        else:
                            flush = ""

                        if flush:
                            text_parts.append(flush)
                            yield flush

                if delta.tool_calls:
                    _accumulate_tool_calls(tool_calls, delta.tool_calls)
        except Exception as exc:
            yield Notice(f"读取流式响应失败：{exc}")
            return

        # 压着的那一小段尾巴：已经确认过不是泄漏标记的开头，补吐出去
        if pending:
            text_parts.append(pending)
            yield pending

        # 没有工具调用 = 本轮就是最终回答，结束
        if not tool_calls:
            if leaked:
                # 模型把工具调用写成了正文，这一轮其实一个工具都没执行。
                # 不能当成最终回答静默结束 —— 那在界面上看起来就是「卡住了」
                yield Notice(
                    "模型把工具调用写成了普通文本，这一轮没有执行任何工具。"
                    "可以重试，或换一个函数调用更稳定的模型。"
                )
            elif not emitted_text:
                yield Notice(
                    "模型没有返回任何内容。可以重试，或换一个模型 —— "
                    "推理模型有时会把输出预算全用在思考上。"
                )
            return

        # 预算已耗尽却还收到工具调用：说明服务端没遵守「不给 tools」这条约定。
        # 这里不能再执行 —— 结果没有下一次请求去消化，副作用纯属白做。
        if tool_budget <= 0:
            yield Notice(f"已达到 {MAX_ITERATIONS} 轮工具调用上限，模型仍未给出最终回答。")
            return

        # 开销上限：每次模型请求之后查一次（用量只在流末尾才有，没法中途拦）。
        #
        # 检查点放在**这一批工具执行之前**：超了就别再去动文件、跑命令了 ——
        # 那些副作用没人再消化。这里和 MAX_ITERATIONS 用尽时的做法**故意不同**：
        # 那边会再发一次「不带工具的收尾请求」（别浪费已经执行的工具结果），
        # 这边是直接停（别再花钱了）—— 目的相反，行为也就该相反。
        if limit and tracker.total_tokens > limit:
            yield Notice(
                f"本轮已用 {tracker.total_tokens} tokens，超过上限 {limit}，已停止。"
                "要放开的话去「偏好设置」里改「单轮开销上限」。"
            )
            return

        # 有工具调用：把这一轮的 assistant 消息回填（含模型已说出的文本和 tool_calls）
        messages.append(
            {
                "role": "assistant",
                "content": "".join(text_parts) or None,
                "tool_calls": [
                    {
                        "id": slot["id"],
                        "type": "function",
                        "function": {"name": slot["name"], "arguments": slot["arguments"]},
                    }
                    for slot in _sorted_tool_calls(tool_calls)
                ],
            }
        )

        # 逐个执行，并把结果作为 tool 消息回填
        for slot in _sorted_tool_calls(tool_calls):
            # 一批里可能有好几个调用（尤其是几个需要确认的），取消后剩下的就别做了 ——
            # 它们的副作用已经没人要了
            if interaction.cancelled():
                yield Notice("已取消这一轮，剩下的工具调用没有执行。")
                return

            # 先播「要跑什么」再跑：工具本身可能几秒到几十秒（联网搜索、扫目录），
            # 那段时间事件流是静默的。界面拿它显示「正在执行 xxx」，用户才知道
            # 现在在等的是工具、不是卡住了
            yield ToolStart(name=slot["name"], arguments=slot["arguments"])

            call_started = time.monotonic()
            result = _execute(slot, context.confirm)

            yield ToolStep(
                name=slot["name"],
                arguments=slot["arguments"],
                result=result,
                elapsed=time.monotonic() - call_started,
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": slot["id"],  # 必须与请求里的 id 对应
                    # 回放给模型的这一份要截断，和 `build_history_messages` 用同一个上限。
                    # 不截的话，本轮读一个大文件（动辄上万字符）当场就把上下文撑大几倍 ——
                    # 而完整结果已经随上面的 `ToolStep` 交给界面了，这里丢的只是模型那一份
                    "content": _clip(result, MAX_TOOL_RESULT_CHARS),
                }
            )

        tool_budget -= 1
