"""出厂资源：三个默认模式，以及它们引用的提示词组 / 工具组 / 技能组 / 提示词。

为什么要有它
------------
新装一份 quill，用户手上的数据是空的：没有模式、没有提示词组、没有工具组。而任务页
**必须选中一个模式**才能开始对话 —— 也就是说，不预置的话，「装好」和「能用」之间隔着
一整套配置的搭建：先建六条提示词、拼成组、再从 27 个工具里挑、最后组装成模式。

所以这里直接给三套立刻能用的搭配：

    全量 Agent     全套提示词 + 全部工具 + 全部技能 + 记忆
    轻量 Agent     精简提示词 + 六个核心文件工具、不带技能与记忆 —— 给 8K/12K
                   上下文的小模型用（见下面的「轻量版为什么这么配」）
    仅问答         三组全空、不开记忆 —— 一个纯粹的对话模型

轻量版为什么这么配
------------------
小窗口模型最缺的就是上下文余量，而挤占它的都是「每次请求都带」的东西：

    - 系统提示词   全量那六条约一千多 token；轻量版压到两百字上下
    - 工具定义     27 个工具的 schema 自己就要几千 token；只留六个文件相关的
    - 技能与记忆   技能目录每轮都在场、记忆清单随条目涨 —— 都关掉

留的六个工具（列目录 / 读 / 搜 / 写 / 改 / 问用户）已经覆盖「看看项目、改点东西、
不确定就问」这条最常用的路径；写不了的一律如实说，让用户换回全量模式。

出厂标记
--------
这些资源带 `builtin=True`（提示词写在文件头的 `builtin: true` 里），作用是**不让界面
删掉它们**：删掉「开箱即用」就没了，而用户并没有别的办法把那套配置拼回来。
标记不由界面写入（请求体里根本没有这个字段），而且每次启动都会按 id 补一遍。

关于「只补不覆盖」
------------------
播种是**幂等**的，且不覆盖已有内容：同 id 的条目已经存在就一个字节都不动 —— 用户可能
改过它的描述、甚至改过提示词正文，那是他的东西。这和 `bootstrap.seed_defaults` 对
`prompt/`、`skills/` 两个目录的做法是同一条原则。

唯一的例外是**把被抹掉的标记改回来**（见 `store.sync_builtin`）：少了它，一条出厂资源
就变成「用户可以删掉」的了，而那恰好是这个字段要防的事。
"""

from __future__ import annotations

from dataclasses import dataclass

from quill_agent.config import Settings
from quill_agent.models import Mode, PromptGroup, SkillGroup, ToolGroup
from quill_agent.prompts import PromptLibrary
from quill_agent.store import (
    ModeStore,
    PromptGroupStore,
    SkillGroupStore,
    ToolGroupStore,
    sync_builtin,
)

# 组的 id 用可读的字符串而不是 uuid：它们会出现在「模式 ID」这一栏里，
# 排障时一眼能认出是出厂的那几个，比一串 hex 有用得多。
BUILTIN_PROMPT_GROUP_ID = "builtin-prompt-group-all"
BUILTIN_TOOL_GROUP_ID = "builtin-tool-group-all"
BUILTIN_SKILL_GROUP_ID = "builtin-skill-group-all"
BUILTIN_MODE_AGENT_ID = "builtin-mode-agent"
BUILTIN_MODE_LITE_ID = "builtin-mode-agent-lite"
BUILTIN_MODE_QA_ID = "builtin-mode-qa"
BUILTIN_PROMPT_GROUP_LITE_ID = "builtin-prompt-group-lite"
BUILTIN_TOOL_GROUP_LITE_ID = "builtin-tool-group-lite"

# 提示词的 id 必须是 8 位小写十六进制（它是文件名，见 `prompts._ID_PATTERN`）。
# 挑一个能认出来的前缀，免得和用户自己建的混起来。
_IDENTITY = "a1000001"
_CAPABILITY = "a1000002"
_TOOL_POLICY = "a1000003"
_WORKFLOW = "a1000004"
_OUTPUT = "a1000005"
_CONSTRAINTS = "a1000006"

# 轻量版用 a1000011 起，和全量的隔开一段：以后全量要加第三、四条不会撞上
_LITE_IDENTITY = "a1000011"
_LITE_CAPABILITY = "a1000012"
_LITE_TOOL_POLICY = "a1000013"
_LITE_WORKFLOW = "a1000014"
_LITE_OUTPUT = "a1000015"
_LITE_CONSTRAINTS = "a1000016"


# 需要用户点头的工具。判断标准是**不可逆**：删掉的东西回不来；子代理要另外花钱、
# 而且要等 —— 这两类值得打断一次。
#
# `run_command` 刻意不在里面：它覆盖面太广（跑测试、装依赖都算），每次都问会把审批
# 变成噪音，用户很快就会条件反射地点「同意」—— 那样的审批等于没有。它该有的约束是
# **沙箱**（见 README-developer.md 3.10）：边界由内核保证，而不是靠一个个弹窗。
BUILTIN_TOOL_CONFIRM = ("delete_file", "delete_skill", "spawn_agents")

# 与 `skills/` 里出厂自带的技能对应。写死名字、而不是播种时现扫目录：扫出来的内容会随
# 「用户那一刻手上有什么」变，播种就不再是幂等的了。
BUILTIN_SKILLS = ("代码审查", "提交信息")


PROMPT_IDENTITY = """你是 quill 里的编码与调研助手 —— 一个跑在用户本机上的 Agent 工作台。

你不是只会聊天的助手：你可以读写工作目录里的文件、执行命令、联网查资料，也可以把互不依赖的
调研拆给子代理。你负责把问题推进到可验证的结果，而不是只给建议；用户能看到工具调用和进度，
所以你做过什么、没做什么，都必须能和最后的汇报对得上。

你尊重用户对工作区的所有权：已有改动先辨认、再在不覆盖的前提下工作；不把自己的猜测当成项目事实。
"""

PROMPT_CAPABILITY = """你能做这些：

- 读懂代码库：列目录、按文件名或内容搜索、阅读关键文件
- 实际完成改动：编辑文件、运行构建与测试，并根据结果修正
- 查证外部事实：联网搜索并阅读来源
- 记住适合跨会话复用的稳定结论
- 对复杂工作拆分任务、持续跟进进度，并并行处理互不依赖的调研

能力边界：

- 只能访问工作目录及工具明确提供的位置；不能把沙箱边界当作可绕过的障碍
- 不可逆或有明显外部副作用的操作须先取得用户同意
- 只能基于实际读取到的文件、工具结果和来源作结论；拿不到的信息就说明限制
"""

PROMPT_TOOL_POLICY = """调用工具时遵循这些原则：

1. **按需调用**：已知信息足够时直接回答；工具用于获取未知事实或完成实际工作。
2. **一轮多发**：互不依赖的读取、搜索和检查尽量并行，不要无谓串行等待。
3. **参数完整**：缺少必填信息时先问；不要为了调用工具自行编造路径、参数或账号。
4. **先读再改**：修改已有文件前先读取当前内容，并确认目标位置；不要凭旧上下文覆盖现场。
5. **尊重现有改动**：先看 git 状态和差异；不要用 reset、checkout 或清理命令抹掉用户的未提交工作。
6. **失败可恢复**：先判断错误原因，能安全修正时只重试一次；不能修正就说明原因和下一步。
7. **结果要核实**：工具返回空、异常或与预期矛盾时暂停并复查，不要把推测当结论。
8. **高风险先确认**：删除、覆盖、移动、提交或其他不可逆操作，先说明影响并取得用户同意。
"""

PROMPT_WORKFLOW = """按下面的闭环推进工作：

1. **理解**：明确目标、范围和限制；只有关键信息缺失且无法从项目中判断时才提问。
2. **探路**：陌生项目先看结构、状态和关键实现；修改前确认当前文件仍与判断一致。
3. **规划**：多文件或多阶段任务先提交计划并维护待办；简单任务不要为了形式列清单。
4. **执行**：按依赖顺序改动，每完成一个重要步骤就核对结果；互不依赖的工作可并行。
5. **验证**：优先运行与改动相关的测试、类型检查或构建；失败时定位原因，不用猜测掩盖。
6. **收尾**：说明结论、改动文件、验证结果、未完成项，以及是否留下进程或临时文件。

任务因中断、压缩或隔夜继续时，先重新检查 git 状态、后台进程、待办状态和相关文件，
不要把旧计划当成当前现场。
"""

PROMPT_OUTPUT = """- 用中文回答，结论先行；简短问题直接回答，复杂问题再展开。
- 不复述工具原文，提炼关键依据；引用代码、报错或数据时保留必要上下文。
- 涉及数据、外部事实或测量结果时标明来源、时间和口径；不把估计写成精确值。
- 使用 Markdown 组织较长内容，避免为了格式堆砌列表和总结。
- 报告改动时给出文件路径与关键位置；报告测试时说明命令和通过/失败结果。
"""

PROMPT_CONSTRAINTS = """
- 不编造事实、数据或引用；不确定就明确说不确定，并说明还缺什么证据。
- 不臆测工具结果的含义；结果异常、缺失或与预期矛盾时先复查。
- 删除、覆盖、移动、提交、支付或其他不可逆操作前，必须先向用户说明影响并确认。
- 尊重用户的未提交改动：不要使用 reset、checkout 或清理命令抹掉现场，也不要无故覆盖已有内容。
- 遇到沙箱限制、权限不足或工具不支持时如实说明；不要换写法绕过边界。
- 医疗、法律、投资等专业领域只给方向性信息，并提示咨询专业人士。
- 不输出与任务无关的个人观点。
"""


# ---------------------------------------------------------------------------
# 轻量版提示词：给 8K/12K 上下文的小模型（见模块 docstring 的「轻量版为什么这么配」）
#
# 每条都只有几行 —— 它们每个请求都要跟着 system prompt 进一次上下文，
# 字字都从窗口里扣。但六类的**骨架不变**：身份 / 能力 / 工具策略 / 工作流程 /
# 输出规范 / 约束缺一环，模型那边就少一维。
# ---------------------------------------------------------------------------

PROMPT_LITE_IDENTITY = """你是 quill 里的编码助手，跑在用户本机上：能读写工作目录里的文件、
按名字搜索、回答关于项目的问题。回答直接、简短 —— 上下文很小，省下来给任务本身。
"""

PROMPT_LITE_CAPABILITY = """- 能做：列目录、搜索、读文件、新建和修改文件
- 做不到：工作目录以外、删除与移动、联网、子代理 —— 这个模式没有这些工具，
  被要求时如实说明做不了，不要假装做了
"""

PROMPT_LITE_TOOL_POLICY = """1. 能直接回答就不要调工具。
2. 改文件前先读它，凭猜测写会改错。
3. 参数缺失就问，不要编。
4. 报错如实说明，不要编造结果。
"""

PROMPT_LITE_WORKFLOW = """1. 目标含糊就先问一句，不要假设。
2. 先用列目录 / 搜索找到要动的文件，再动手。
3. 改完用一两句话汇报：改了哪个文件、做了什么。
不要列长清单、不要铺开冗长计划 —— 上下文很小，留给任务本身。
"""

PROMPT_LITE_OUTPUT = """- 用中文，结论先行
- 默认几句话说完，确有必要才展开
- 给出文件路径，不复读工具返回的原文
"""

PROMPT_LITE_CONSTRAINTS = """- 不编造；不确定就说不确定。
- 做不了的操作如实说，并建议用户怎么办，不要反复换写法硬试。
- 沙箱拦截写入时如实说明是边界拦下的，不要试图绕过。
"""


@dataclass(frozen=True)
class BuiltinPrompt:
    """一条出厂提示词：id 固定（内置提示词组要引用它），正文随包发布。"""

    id: str
    name: str
    category: str
    content: str


BUILTIN_PROMPTS: tuple[BuiltinPrompt, ...] = (
    BuiltinPrompt(_IDENTITY, "工作台助手", "身份", PROMPT_IDENTITY),
    BuiltinPrompt(_CAPABILITY, "通用能力", "能力", PROMPT_CAPABILITY),
    BuiltinPrompt(_TOOL_POLICY, "工具使用规范", "工具策略", PROMPT_TOOL_POLICY),
    BuiltinPrompt(_WORKFLOW, "标准流程", "工作流程", PROMPT_WORKFLOW),
    BuiltinPrompt(_OUTPUT, "输出要求", "输出规范", PROMPT_OUTPUT),
    BuiltinPrompt(_CONSTRAINTS, "安全边界", "约束", PROMPT_CONSTRAINTS),
    BuiltinPrompt(_LITE_IDENTITY, "轻量身份", "身份", PROMPT_LITE_IDENTITY),
    BuiltinPrompt(_LITE_CAPABILITY, "轻量能力", "能力", PROMPT_LITE_CAPABILITY),
    BuiltinPrompt(_LITE_TOOL_POLICY, "轻量工具策略", "工具策略", PROMPT_LITE_TOOL_POLICY),
    BuiltinPrompt(_LITE_WORKFLOW, "轻量流程", "工作流程", PROMPT_LITE_WORKFLOW),
    BuiltinPrompt(_LITE_OUTPUT, "轻量输出", "输出规范", PROMPT_LITE_OUTPUT),
    BuiltinPrompt(_LITE_CONSTRAINTS, "轻量约束", "约束", PROMPT_LITE_CONSTRAINTS),
)

# 轻量工具组的手选清单。这里**必须手抄**（与全量组「从注册表现取」相反）：
# 轻量版的全部意义就是少带几个工具 —— 每个工具的 schema 都要从上下文里扣，
# 多留一个都是浪费。清单里只放「看看项目、改点东西、不确定就问」这条最常用
# 路径上的名字；测试会校验每个名字都在注册表里，写错了当场爆。
LITE_TOOLS: tuple[str, ...] = (
    "list_dir",
    "search_files",
    "read_file",
    "write_file",
    "edit_file",
    "ask_user",
)


def _all_tool_names() -> list[str]:
    """全部工具名，直接问注册表要。

    **不写死一份清单**：写死的话，以后每加一个工具，内置的那个「全量工具组」就会悄悄
    少一个 —— 而它叫「全量」，用户没有任何理由去核对它是不是真的全。
    延迟导入 `quill_agent.tools` 也是这个原因：它在 import 时就会注册全部工具，
    不必（也不该）让本模块在顶层就把工具层拉进来。
    """
    from quill_agent.tools import registry

    return [spec.name for spec in registry.all()]


def builtin_prompt_group() -> PromptGroup:
    """全量提示词组：出厂那六条提示词，六类各一条。"""
    return PromptGroup(
        id=BUILTIN_PROMPT_GROUP_ID,
        name="全量提示词组",
        description="六类提示词各一条：身份 / 能力 / 工具策略 / 工作流程 / 输出规范 / 约束",
        prompts=[
            _IDENTITY,
            _CAPABILITY,
            _TOOL_POLICY,
            _WORKFLOW,
            _OUTPUT,
            _CONSTRAINTS,
        ],
        builtin=True,
    )


def builtin_prompt_group_lite() -> PromptGroup:
    """轻量提示词组：同样六类各一条，正文压到几行（见模块 docstring）。"""
    return PromptGroup(
        id=BUILTIN_PROMPT_GROUP_LITE_ID,
        name="轻量提示词组",
        description="六类各一条、正文极简 —— 给上下文只有 8K/12K 的小模型",
        prompts=[
            _LITE_IDENTITY,
            _LITE_CAPABILITY,
            _LITE_TOOL_POLICY,
            _LITE_WORKFLOW,
            _LITE_OUTPUT,
            _LITE_CONSTRAINTS,
        ],
        builtin=True,
    )


def builtin_tool_group() -> ToolGroup:
    """全量工具组：注册表里的每一个工具。"""
    return ToolGroup(
        id=BUILTIN_TOOL_GROUP_ID,
        name="全量工具组",
        description="注册表里的全部工具，逐个按需调用",
        tools=_all_tool_names(),
        confirm=list(BUILTIN_TOOL_CONFIRM),
        builtin=True,
    )


def builtin_tool_group_lite() -> ToolGroup:
    """轻量工具组：六个手选的核心工具，schema 从小模型的上下文里省出来。

    没有 delete / move 那批不可逆工具，confirm 自然是空的 —— 无需审批的清单
    本身就是空。
    """
    return ToolGroup(
        id=BUILTIN_TOOL_GROUP_LITE_ID,
        name="轻量工具组",
        description="六个核心文件工具，给上下文只有 8K/12K 的小模型省窗口",
        tools=list(LITE_TOOLS),
        confirm=[],
        builtin=True,
    )


def builtin_skill_group() -> SkillGroup:
    """全量技能组：出厂自带的技能。"""
    return SkillGroup(
        id=BUILTIN_SKILL_GROUP_ID,
        name="全量技能组",
        description="出厂自带的技能；模型觉得用得上时才会读它的正文",
        skills=list(BUILTIN_SKILLS),
        builtin=True,
    )


def builtin_modes() -> list[Mode]:
    """三个默认模式。

    `preferred_model` 都留空：出厂时不知道用户配了哪个模型，留空表示「沿用任务页当前
    选中的那个」—— 硬指一个模型只会让默认模式一装上就是坏的。
    """
    return [
        Mode(
            id=BUILTIN_MODE_AGENT_ID,
            name="全量 Agent",
            description="什么都能干：全套提示词 + 全部工具与技能 + 记忆",
            prompt_group_id=BUILTIN_PROMPT_GROUP_ID,
            tool_group_id=BUILTIN_TOOL_GROUP_ID,
            skill_group_id=BUILTIN_SKILL_GROUP_ID,
            memory_enabled=True,
            builtin=True,
        ),
        Mode(
            id=BUILTIN_MODE_LITE_ID,
            name="轻量 Agent",
            description="给 8K/12K 小窗口模型：精简提示词 + 六个核心工具，不带技能与记忆",
            prompt_group_id=BUILTIN_PROMPT_GROUP_LITE_ID,
            tool_group_id=BUILTIN_TOOL_GROUP_LITE_ID,
            # 技能清单本身每轮都占上下文，小模型不带 —— 需要技能时换全量模式
            skill_group_id="",
            memory_enabled=False,
            builtin=True,
        ),
        Mode(
            id=BUILTIN_MODE_QA_ID,
            name="仅问答",
            description="不带提示词、工具与技能，也不记东西 —— 纯粹的对话",
            # 三个组 id 全空、记忆关掉，就是「什么都不给」——这是本项目里合法的配置，
            # 不是缺省值没填
            prompt_group_id="",
            tool_group_id="",
            skill_group_id="",
            memory_enabled=False,
            builtin=True,
        ),
    ]


def ensure(settings: Settings) -> list[str]:
    """把出厂资源补进数据根，返回「这次做了什么」（没做就是空列表）。

    顺序有讲究：**提示词文件必须先于提示词组**。提示词组按 id 引用提示词，反过来的话，
    中间那一刻组里的引用全是悬空的（界面上只能显示成 id）—— 虽然下一次启动就补上了，
    但没必要制造这个中间态。

    幂等，所以每次启动都跑一遍。
    """
    notes: list[str] = []

    written = _ensure_prompt_files(settings)
    if written:
        notes.append(f"{written} 条内置提示词")

    plans = (
        (
            "提示词组",
            PromptGroupStore(settings.prompt_groups_path),
            [builtin_prompt_group(), builtin_prompt_group_lite()],
        ),
        (
            "工具组",
            ToolGroupStore(settings.tool_groups_path),
            [builtin_tool_group(), builtin_tool_group_lite()],
        ),
        ("技能组", SkillGroupStore(settings.skill_groups_path), [builtin_skill_group()]),
        ("模式", ModeStore(settings.modes_path), builtin_modes()),
    )
    for label, store, items in plans:
        added, repaired = sync_builtin(store, items)
        if added:
            notes.append(f"{len(added)} 个内置{label}")
        if repaired:
            notes.append(f"{len(repaired)} 个{label}补回出厂标记")

    return notes


def _ensure_prompt_files(settings: Settings) -> int:
    """把缺的内置提示词文件写上，返回写了几个。"""
    library = PromptLibrary(settings.prompt_dir)
    library.ensure_dir()

    written = 0
    for spec in BUILTIN_PROMPTS:
        if library.write_builtin(
            spec.id, name=spec.name, category=spec.category, content=spec.content
        ):
            written += 1

    return written
