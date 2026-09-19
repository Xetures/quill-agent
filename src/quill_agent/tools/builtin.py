"""内置工具。

这里是「不属于某个具体领域」的通用工具的落脚点。
新增工具只需写一个普通 Python 函数 + 加 @registry.tool 装饰器，
「工具」页面上的列表和分类筛选会自动出现它，不需要改任何界面代码。

和文件系统强相关的工具（读写、搜索、删除）放在 files.py。
"""

from __future__ import annotations

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
