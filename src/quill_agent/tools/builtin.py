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
from quill_agent.naming import safe_name
from quill_agent.skills import SkillLibrary
from quill_agent.store import SkillGroupStore
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

    # 和新建 / 删除一样先过 `safe_name`：技能名会被拼进文件路径，
    # 不校验的话「../..」这种东西就能读到技能目录外面的文件。
    # 之前只有「读」这条路漏了校验 —— 而它恰恰是模型能自由传参的那个
    try:
        target = safe_name(name)
    except ValueError as exc:
        return f"读不了技能：{exc}"

    content = SkillLibrary(settings.skills_dir).read(target)

    if content is None:
        return f"没有找到技能「{name}」。可用技能以系统提示里的清单为准。"
    if not content:
        return f"技能「{name}」还没有写内容，请按常规做法处理。"

    return content


def _skill_group_hint(skill_name: str) -> str:
    """告诉模型这个新技能**现在**能不能用；不能用时说清该让用户去哪儿开。

    技能给不给由模式的技能组决定，这里不能替用户改配置（理由见 create_skill），
    所以必须把话说清楚：否则技能库里多出来一个谁也不认识的文件，而模型还以为
    自己已经「学到」了 —— 下一轮它照样读不到，看起来就像技能凭空消失。
    """
    # 延迟导入：`quill_agent.agent` 在模块级 import 了 `quill_agent.tools`（取 registry），
    # 而本模块又是在 tools/__init__ 里被导入的 —— 模块级写 import 会拿到一个只执行了
    # 一半的 agent 模块。subagent.spawn_agent 出于同样的原因也是延迟导入
    from quill_agent import agent

    environment = agent.current_environment()
    group_id = environment.context.skill_group_id if environment is not None else ""

    if not group_id:
        return (
            "但**当前模式没有配技能组，它现在不会生效**。"
            "请在回答的最后提醒用户：到「模式」页给这个模式选一个技能组，"
            "再到「技能组」页把这个技能勾上。"
        )

    group = SkillGroupStore(get_settings().skill_groups_path).get(group_id)
    if group is None:
        return (
            "但**当前模式引用的技能组已经不存在了，它现在不会生效**。"
            "请提醒用户重新给这个模式选一个技能组。"
        )

    if skill_name in group.skills:
        return f"它已经在当前模式的技能组「{group.name}」里，下一轮就会出现在「可用技能」清单里。"

    return (
        f"但它**现在不会生效** —— 它还没被加进任何技能组。"
        f"请在回答的最后用一句话提醒用户：到「技能组」页把「{skill_name}」加进「{group.name}」"
        f"（当前模式用的就是这个组），下次遇到同类任务就能自动用上。"
    )


@registry.tool(
    description=(
        "把一套值得复用的做法总结成技能存进技能库，以后遇到同类任务可以直接照着做。"
        "**什么时候用**：用户明确说「把刚才这套做法记下来」，"
        "或者你刚做完一件事、而这类事以后还会反复遇到（某个项目的构建步骤、"
        "某种固定格式的整理流程）。"
        "只写真正能复用的：一次性的任务细节、只对某一个文件成立的结论不要写成技能。"
        "name 要短、具体、望文生义。"
        "description 是以后判断「什么时候该用这个技能」的**唯一依据**，"
        "必须写成使用场景（例如「当需要把一批 Excel 汇总成一张表时使用」），"
        "而不是技能内容的摘要 —— 摘要没法让任何人知道何时该用它。"
        "body 写清步骤、命令和注意事项，可以写长：正文平时不进上下文，只在被读取时才占空间。"
        "同名技能已存在会失败，那是提醒你换个更具体的名字，不要试图覆盖已有的技能。"
    ),
    category="技能",
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "技能名，也是它在技能库里的目录名；短、具体、望文生义",
            },
            "description": {
                "type": "string",
                "description": "什么时候该用这个技能（一句话，写成使用场景而不是内容摘要）",
            },
            "body": {
                "type": "string",
                "description": "技能正文，Markdown：步骤、命令、注意事项",
            },
        },
        "required": ["name", "description", "body"],
    },
)
def create_skill(name: str, description: str, body: str) -> str:
    """新建一个技能，写进 skills/<名字>/SKILL.md。

    **不覆盖已有技能**（create_only）：重名时宁可失败。模型看不到技能的现有正文，
    让它「更新」等于让它照着记忆重写一遍 —— 那多半会把原来写好的东西弄丢。
    要改已有技能，那是用户在技能页里做的事。

    **也不替用户加进技能组**：技能给不给由模式的技能组决定（和工具一个道理，
    见 agent.resolve_mode）。工具偷偷改用户的组配置，等于绕开了「谁来决定这个模式
    有哪些能力」这件事 —— 模型可以建技能，但「下次这类任务用它吗」仍然得用户点头。
    所以这里只负责把「去哪儿勾」说清楚（见 _skill_group_hint）。
    """
    if not (name or "").strip():
        return "技能名是空的。请给一个短、具体、望文生义的名字。"

    if not (description or "").strip():
        return (
            "description 是空的 —— 它以后是判断「什么时候该用这个技能」的唯一依据，"
            "不能省。请写成使用场景，例如「当需要……时使用」，而不是技能内容的摘要。"
        )

    if not (body or "").strip():
        return "技能正文是空的。请把步骤、命令和注意事项写清楚。"

    library = SkillLibrary(get_settings().skills_dir)

    try:
        saved = library.save(name, description, body, create_only=True)
    except ValueError as exc:
        # 名字不合法（带路径分隔符等）和重名都走这里。
        # 异常文案本身就是给模型看的说明，它会据此决定是换个名字还是就此打住
        return str(exc)

    return f"已创建技能「{saved}」（{library.path_of(saved)}）。{_skill_group_hint(saved)}"


@registry.tool(
    description=(
        "删除一个技能，连同它的整个目录。"
        "**只有当用户明确要求删掉某个技能时才用** —— "
        "不要因为你自己觉得它过时、重复、或者和当前任务无关就删掉它，那是用户的东西。"
        "name 必须与「可用技能」清单里的名称完全一致，差一个字就会失败，这是为了防止误删。"
        "删除不可撤销：技能目录里的所有内容都会一起消失。"
        "如果它还在某个技能组里，那个组会多出一条读不到的名字，"
        "需要用户自己去技能组页把它移除。"
    ),
    category="技能",
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "要删除的技能名，必须是「可用技能」清单里的原名",
            }
        },
        "required": ["name"],
    },
)
def delete_skill(name: str) -> str:
    """删除一个技能。

    **动手前先问用户**：删除不可逆（整个目录连里面的东西一起没），而技能是用户
    自己维护的资产。做法照抄 `run_command` 对危险命令的处理 —— 没问到就按拒绝
    处理，绝不在没人点头的时候删。

    **不校验「技能是否启用」**：给不给由模式的技能组决定（见 read_skill），
    这里只管技能库里到底有没有这个东西。
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return "技能名是空的。请给出要删除的技能名。"

    try:
        target = safe_name(cleaned)
    except ValueError as exc:
        return str(exc)

    library = SkillLibrary(get_settings().skills_dir)

    # 名字先对上再弹确认：打错字时直接说「没这个技能」，而不是让用户对着一道
    # 莫名其妙的确认题点头（那种题点完才发现根本没这个技能，最容易被顺手点掉）
    if not library.exists(target):
        return f"没有找到技能「{target}」。要删的目标以「可用技能」清单里的名称为准。"

    approved = interaction.confirm(
        text=f"是否删除技能「{target}」？",
        detail=(
            f"技能目录：{library.skill_dir(target)}\n\n"
            "删除后无法恢复，目录里的所有内容都会一并移除。"
        ),
    )

    if approved is False:
        return (
            f"用户拒绝删除技能「{target}」。"
            "**不要换个名字或换个说法再试一次** —— 那是在规避用户刚刚做出的决定。"
        )

    if approved is None:
        # 没有通道（CLI / 单元测试）或等待超时。**按拒绝处理**：删除不可逆，
        # 没人点头就删，是这个机制最坏的失败方式
        return (
            f"没能问到用户（等待超时，或当前界面不支持确认），因此没有删除技能「{target}」。"
            "请告诉用户你想删除它，由他自己在技能页删除，或让他确认后再试一次。"
        )

    try:
        removed = library.delete(target)
    except ValueError as exc:
        return str(exc)

    return f"已删除技能「{removed}」（它的目录已从技能库移除）。"


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

    # 「没有通道」和「问了没人答」要分开说：前者是当前调用根本没法问（单元测试、
    # 纯脚本调用都没有这条通道），后者是人不在。模型据此决定要不要再问，用户也才知道去哪儿改
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
        # 没有通道就没法审批。**不能因此默许它动手** ——
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


# ---------------------------------------------------------------------------
# 检索更早的对话
# ---------------------------------------------------------------------------

# 每段原文的字符上限。太短看不出上下文，太长又把刚省下来的预算花回去了 ——
# 这个工具是「精准取回」，不是「把历史重新灌一遍」。
RECALL_SNIPPET_CHARS = 300

# 一次返回的总字符上限
RECALL_MAX_CHARS = 3000

# 一次最多返回几条
RECALL_MAX_LIMIT = 10


def _flatten(record: dict) -> str:
    """把一条记录拍平成可检索的文本：正文 + 工具调用的名字 / 参数 / 结果。

    工具结果也要算进去：模型要找的常常是「上次读到的那个报错」，
    而那东西只存在于工具的返回里，正文一个字都没提。
    """
    parts = [str(record.get("content") or "")]
    for step in record.get("steps") or []:
        if not isinstance(step, dict):
            continue
        parts.append(str(step.get("name") or ""))
        parts.append(str(step.get("arguments") or ""))
        parts.append(str(step.get("result") or ""))

    return "\n".join(part for part in parts if part)


def _snippet(text: str, keyword: str, limit: int = RECALL_SNIPPET_CHARS) -> str:
    """截取关键词**周围**的一段，而不是从开头截。

    命中点通常在正文中间（「认证中间件在 src/auth.py:42」）—— 从头截的话，
    真正要找的那一句往往刚好被切掉。
    """
    # 换行和连续空白压掉：片段是拿来看的，不需要保留原来的版式
    flat = " ".join(text.split())
    position = flat.lower().find(keyword.lower())
    if position < 0:
        return flat[:limit]

    half = max(0, (limit - len(keyword)) // 2)
    start = max(0, position - half)
    end = min(len(flat), start + limit)

    head = "…" if start > 0 else ""
    tail = "…" if end < len(flat) else ""
    return f"{head}{flat[start:end]}{tail}"


@registry.tool(
    description=(
        "在本次会话**更早的对话**里按关键词搜索原文，返回命中的片段。"
        "需要回忆之前讨论过的具体细节（文件路径、报错原文、参数值、某个决定）时用它；"
        "如果上下文里缺少更早的内容，也该先来这里查，而不是凭猜测作答。"
        "查工作目录里的文件用 search_content，不是这个。"
    ),
    category="对话",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "关键词，空格分隔多个（要全部命中）。例如「认证 中间件」",
            },
            "limit": {
                "type": "integer",
                "description": f"最多返回几条，默认 5，上限 {RECALL_MAX_LIMIT}",
            },
        },
        "required": ["query"],
    },
)
def recall_history(query: str, limit: int = 5) -> str:
    """按关键词检索本次会话的历史原文。

    检索的是**全量**历史，包括已经被上下文预算截掉的那部分 —— 这正是它存在的意义：
    截断只影响「发给模型多少」，不影响「能取回多少」，原文一直躺在会话文件里。
    """
    # 延迟导入：`quill_agent.agent` 在模块级就 import 了 `quill_agent.tools`（取 registry），
    # 而本模块是在 `tools/__init__` 里被导入的 —— 两边都写模块级 import，会拿到一个
    # 只执行了一半的 agent 模块。理由同 tools/subagent.py
    from quill_agent import agent

    environment = agent.current_environment()
    if environment is None:
        return "当前不在一次对话运行里（可能是被直接调用），没有可检索的历史。"

    keywords = [word for word in query.split() if word]
    if not keywords:
        return "query 是空的。请给出一个或几个关键词。"

    # 每条只拍平一次：关键词可能有好几个，逐个重算是白费
    flattened = [(index, record, _flatten(record).lower()) for index, record in enumerate(
        environment.history, start=1
    )]
    hits = [
        (index, record)
        for index, record, text in flattened
        if all(word.lower() in text for word in keywords)
    ]

    if not hits:
        return (
            f"没有找到同时包含「{'、'.join(keywords)}」的记录。"
            "换更短、更具体的关键词再试（只留一个词，或者用文件名的一个片段）—— "
            "关键词要**全部命中**才会返回。"
        )

    # 取最近的几处：要找的东西多半在近期
    hits.reverse()
    chosen = hits[: max(1, min(limit, RECALL_MAX_LIMIT))]

    lines = [
        f"在更早的对话里找到 {len(hits)} 处包含「{'、'.join(keywords)}」的记录，"
        f"以下是最近的 {len(chosen)} 处（编号是会话记录里的序号，越小越早）："
    ]
    for index, record in chosen:
        role = "用户" if record.get("role") == "user" else "助手"
        lines.append(f"\n【第 {index} 条 · {role}】\n{_snippet(_flatten(record), keywords[0])}")

    result = "\n".join(lines)

    # 总长兜底：一次召回塞回去的东西，不该比截断省下来的还多
    if len(result) > RECALL_MAX_CHARS:
        result = f"{result[:RECALL_MAX_CHARS]}…（结果较长已截断）"

    return result
