"""内置工具。

这里是「不属于某个具体领域」的通用工具的落脚点。
新增工具只需写一个普通 Python 函数 + 加 @registry.tool 装饰器，
「工具」页面上的列表和分类筛选会自动出现它，不需要改任何界面代码。

和文件系统强相关的工具（读写、搜索、删除）放在 files.py。
"""

from __future__ import annotations

from quill_agent import interaction
from quill_agent.config import get_settings
from quill_agent.memory import MemoryStore
from quill_agent.skills import SkillLibrary
from quill_agent.tools.base import registry


@registry.tool(
    description=(
        "读取某个技能的完整说明，也就是「这类任务具体该怎么做」的步骤。"
        "当任务匹配系统提示里「可用技能」清单中某一项的适用场景时，"
        "先用它把说明取回来，再按说明动手。"
    ),
    category="技能",
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "技能名，取自「可用技能」清单里的名称",
            }
        },
        "required": ["name"],
    },
)
def read_skill(name: str) -> str:
    """读取技能正文。

    这里**不再**校验「技能是否启用」：技能没有全局开关了，给不给由模式的技能组
    决定（见 agent.resolve_mode）。而且这个工具本来就是随技能组一起下发的 ——
    模型手上的清单里只有组里那几个，它想读也读不到别的。留一道全局开关在这儿，
    只会变成「组里选了却读不出来」这种查不出原因的静默失败。
    """
    settings = get_settings()

    content = SkillLibrary(settings.skills_dir).read(name)

    if content is None:
        return f"没有找到技能「{name}」。可用技能以系统提示里的清单为准。"
    if not content:
        return f"技能「{name}」还没有写内容，请按常规做法处理。"

    return content


@registry.tool(
    description=(
        "把关于用户的长期信息记下来，让以后的会话也能知道。"
        "只记长期有效的：稳定的偏好、习惯、项目约定。"
        "临时的任务状态（刚读了哪个文件、当前在做什么）不要记 —— 那些本来就在会话历史里，"
        "记下来反而污染长期记忆。同一件事只说一次，重复调用会被拒绝。"
    ),
    category="记忆",
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要记住的一句话，例如「偏好简洁回答，不要啰嗦的总结」",
            }
        },
        "required": ["text"],
    },
)
def remember(text: str) -> str:
    """写入一条长期记忆。"""
    try:
        item = MemoryStore(get_settings().memory_path).add(text)
    except ValueError as exc:
        # 重复、超长、超上限都走这里。异常文案本身就是给模型看的说明，
        # 它会据此决定是换个说法、还是提醒用户去清理
        return str(exc)

    return f"已记住：{item.text}"


@registry.tool(
    description=(
        "忘掉一条已经记下的长期信息。"
        "只有当用户明确要求「别记这条了」「这条不对」时才用 —— "
        "不要因为你自己觉得某条过时、或者和当前任务无关就删掉它，那是用户的信息。"
        "text 必须与记忆原文完全一致（照抄「关于用户的已知信息」清单里的那一行），"
        "差一个字就会失败，这是为了防止误删。"
    ),
    category="记忆",
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要忘掉的那条记忆的原文，必须一字不差",
            }
        },
        "required": ["text"],
    },
)
def forget(text: str) -> str:
    """忘掉一条长期记忆。"""
    try:
        item = MemoryStore(get_settings().memory_path).forget(text)
    except ValueError as exc:
        return str(exc)

    return f"已忘掉：{item.text}"


@registry.tool(
    description=(
        "向用户提一个问题，然后**停下来等他回答**。"
        "只在「猜错的代价很大、而且从上下文推断不出来」时才用 —— "
        "能从代码、文件、历史或常识判断出来的都不要问。"
        "值得问的场合：需求有几种合理理解且结果差别很大；要动用户的东西但不确定是哪一个；"
        "需要只有用户才知道的信息（环境地址、账号、业务规则、个人偏好）。"
        "不该问的：只是想让用户替你拿主意、能自己查的、或者问「我可以开始了吗」。"
        "一次只问一个问题，别把几件事塞进同一个 question。"
        "用户可能不回答（走开或超时），所以问之前先想好「没有回答时怎么办」，"
        "并在后续回答里说明你的假设。"
    ),
    category="交互",
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "要问的问题；有选项时也把背景交代清楚，用户才知道在选什么",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "候选答案。给了的话界面会渲染成按钮，用户点一下就答完 —— "
                    "答案确实是有限的几个时请务必给上，比让用户打字快得多、也不会答偏。"
                    "需要用户自由发挥的就别给。"
                ),
            },
        },
        "required": ["question"],
    },
)
def ask_user(question: str, options: list[str] | None = None) -> str:
    """问用户一个问题，阻塞等答案。

    一轮里最多能问几次由 `MAX_ITERATIONS` 兜着（每次提问都占一轮工具预算），
    所以不必另外设一个「提问次数」的闸 —— 那只会多一个要同步的状态。
    """
    channel = interaction.current()

    # 「没有通道」和「问了没人答」要分开说：前者是当前界面根本没法问（Streamlit 版
    # 就没有这条通道），后者是人不在。模型据此决定要不要再问，用户也才知道去哪儿改
    if channel is None:
        return (
            "当前界面没有可用的提问通道，问不到用户。"
            "请自己选一个最合理的方案继续，并在回答里说明你假设了什么。"
        )

    answer = channel.ask(kind="ask", text=question, options=options or ())

    if answer is None:
        return (
            "用户没有回答（等待超时，或被取消）。"
            "不要再问一遍，请按最合理的假设继续，并在回答里说明你的假设。"
        )

    if not answer.strip():
        return "用户回答了，但内容是空的。请按最合理的假设继续，或换一个更具体的问题。"

    # 带上「用户回答：」这个前缀：这一句会原样进 tool 消息，
    # 不加前缀的话，用户的话和工具自己的输出会长得一模一样
    return f"用户回答：{answer}"


# 计划审批的两个选项。定义成常量是因为判定（哪个是批准）和展示（按钮文案）
# 必须用同一份，改一处忘一处会让「批准」变成一次驳回
PLAN_APPROVE = "批准"
PLAN_REJECT = "驳回"

# 「按钮 + 补充说明」回传时的分隔符。**用换行而不是冒号**：
# 用户写的说明里完全可能有冒号，而第一行一定只是那个按钮文本，切一刀不会错
ANSWER_LINE = "\n"


PLAN_FALLBACK_TITLE = "请审阅这份计划"


def _card_title(plan: str, summary: str) -> tuple[str, str]:
    """决定卡片标题，并返回（标题, 卡片正文）。

    标题有两个来源：模型给的 `summary`，或者正文第一个 Markdown 标题。

    **只有真拿它当了标题，才把那一行从正文里去掉。** 不去的话，卡片头上一个标题、
    正文里再来一个，同一句话接连出现两次 —— 第一版就是这样，看着像个标题没对齐的文档。

    第一行不是标题时（有的模型上来就写正文）**不猜**：用一句通用标题，正文原样保留。
    猜的话会把人家正文的第一句吃掉，而那句话往往是有用的。
    """
    if summary.strip():
        return summary.strip(), plan

    lines = plan.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        # 不是标题就停 —— 别把正文第一句当标题
        if not stripped.startswith("#"):
            break

        title = stripped.lstrip("#").strip()
        if not title:  # 整行只有 #，那是排版符号本身，不配当标题
            break

        body = "\n".join([*lines[:index], *lines[index + 1 :]]).strip()
        # 计划只有一行标题时正文会空掉，那时保留原文 —— 至少还有东西可看
        return title, body or plan

    return PLAN_FALLBACK_TITLE, plan


@registry.tool(
    description=(
        "把一份实施方案交给用户审批，然后**停下来等他答复**。"
        "在动手改任何东西之前用它 —— 这就是「先说清楚要做什么、得到许可再动手」那一步。"
        "计划里要说清：打算动哪些文件、各改什么、为什么这么改、以及你拿不准的地方。"
        "用户批准后就开始执行；被驳回时他会给出要改什么，改完再提交一次。"
        "一次只提交一份计划 —— 要让用户在几个方案里挑，那是提问，用 ask_user。"
    ),
    category="交互",
    parameters={
        "type": "object",
        "properties": {
            "plan": {
                "type": "string",
                "description": "计划正文，用 Markdown 写：改什么、怎么改、为什么",
            },
            "summary": {
                "type": "string",
                "description": "一句话概括，给卡片当标题；不填就取计划正文的第一行",
            },
        },
        "required": ["plan"],
    },
)
def submit_plan(plan: str, summary: str = "") -> str:
    """交一份计划给用户审批，阻塞等答复。

    **这里没有「计划存在哪儿」这件事**，而那不是省略，是这个问题本来就不存在：
    审批走的是同一条阻塞通道，运行自始至终没有中断过，所以既不需要把计划落成一份
    单独的状态，也不需要「批准之后怎么恢复」——批准就是这次工具调用返回了一个字符串，
    循环接着往下跑。计划正文本身随 tool 调用记进会话文件，回看时它就在那儿。

    代价和「执行前确认」是同一份：服务重启会丢掉正在等审批的这一轮。
    """
    text = (plan or "").strip()
    if not text:
        return "计划是空的。请把要做什么写清楚再提交。"

    channel = interaction.current()

    if channel is None:
        # 没有通道就没法审批（Streamlit 版就是这样）。**不能因此默许它动手** ——
        # 那等于把「先问再动」变成「不问就动」，正是这个工具要防的事。
        # 退一步：把计划当成本轮回答交出去，用户回一句「做吧」下一轮再执行
        return (
            "当前界面没有可用的审批通道，交不了计划。"
            "请把这份计划作为本轮的回答直接输出，并说明你还没有动手、在等用户确认。"
        )

    title, body = _card_title(text, summary)

    answer = channel.ask(
        kind="plan",
        text=title,
        detail=body,
        options=(PLAN_APPROVE, PLAN_REJECT),
    )

    if answer is None:
        # 超时或被取消。**按「没批准」处理**：没拿到许可就动手，是这个机制最坏的失败方式
        return (
            "用户没有答复（等待超时，或被取消）。"
            "**不要开始执行** —— 没有得到许可前不要动任何东西。"
            "请说明你在等他对这份计划的答复。"
        )

    verdict, _, feedback = answer.partition(ANSWER_LINE)
    feedback = feedback.strip()

    if verdict.strip() == PLAN_APPROVE:
        if feedback:
            return f"用户批准了计划，并补充：{feedback}\n可以按计划开始执行。"
        return "用户批准了计划，可以开始执行了。"

    if feedback:
        return f"用户驳回了计划，他的意见是：\n{feedback}\n请按这个意见改完再提交一次。"

    return "用户驳回了计划，但没有说明原因。请先问清楚他希望改什么，再重新提交。"
