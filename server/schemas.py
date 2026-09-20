"""HTTP 层的请求体。

响应直接返回业务层的对象（`ModelConfig` / `PromptGroup` / `Mode` / `MemoryItem` ……），
FastAPI 会自动序列化，不必再抄一遍。这里只定义「只有 HTTP 才需要」的形状。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from quill_agent.models import Protocol
from quill_agent.search import DEFAULT_MAX_RESULTS, MAX_RESULTS_LIMIT, SearchBackend


class ChatRequest(BaseModel):
    """发一条消息，用 SSE 把整轮过程推回去。"""

    conversation_id: str = Field(min_length=1, description="会话 id")
    # 不设 min_length：空输入由 server/routes/chat.py 的 stream_round 处理成一条
    # 友好提示；卡在 schema 这一层只会变成一个 500
    prompt: str = Field(default="", description="本轮输入")
    model_config_id: str = Field(default="", description="模型连接 id")
    model: str = Field(default="", description="模型名")
    # 空表示不用模式（界面上不允许，但接口不强求 —— 由调用方保证必须选一个）
    mode_id: str = Field(default="", description="模式 id；空表示不带模式")


class AnswerPayload(BaseModel):
    """回答运行中抛出的一个问题（确认题或模型提问）。

    三个 id 缺一不可：`run_id` 找到是哪一轮在等，`question_id` 确认等的是哪一个问题。
    后者不能省 —— 用户点「允许」的同时那一轮可能刚好超时结束，又开了新一轮提问，
    只按 run 匹配的话，这个迟到的答案会落到**新问题**上。
    """

    run_id: str = Field(min_length=1, description="运行的 id")
    question_id: str = Field(min_length=1, description="问题的 id")
    answer: str = Field(default="", description="用户的回答")


class CancelPayload(BaseModel):
    """停止一次运行。

    只需要运行 id —— 一个运行整体停掉，不存在「停哪一个工具」这种粒度。
    """

    run_id: str = Field(min_length=1, description="运行的 id")


class ModelPayload(BaseModel):
    """新增 / 修改一条模型连接。

    `context_windows` 是**模型级**的：同一条连接下的多个模型窗口常常不一样，
    一个连接级的值表达不了。没给某个模型就是不填（仪表盘显示「—」）。
    """

    name: str = Field(min_length=1)
    base_url: str = ""
    protocol: Protocol = Protocol.OPENAI
    api_key: str = ""
    models: list[str] = Field(default_factory=list)
    context_windows: dict[str, int] = Field(
        default_factory=dict,
        description="模型名 -> 上下文窗口大小（tokens）",
    )


class TestConnectionPayload(BaseModel):
    """连通测试 / 拉取模型列表。"""

    base_url: str = ""
    api_key: str = ""
    protocol: Protocol = Protocol.OPENAI


class CatalogLookupPayload(BaseModel):
    """按模型名批量查本地规格快照。"""

    # 上限防呆：界面一次最多也就查几十个名字
    names: list[str] = Field(default_factory=list, max_length=200)


class PromptPayload(BaseModel):
    """新建 / 修改一条提示词。

    名字与分类都只是**元信息**（文件用后端生成的 id 命名，见 `quill_agent.prompts`），
    所以这里不做文件名规则校验 —— 业务层的 `clean_name` 只要求非空、能压成单行、
    长度合理。也正因为名字不再是标识，**改名与换分类都是安全操作**，不会让引用失效。
    """

    name: str = Field(min_length=1, description="展示名")
    category: str = Field(min_length=1, description="六类提示词之一")
    content: str = Field(default="", description="正文（.md 原文）")


class SkillPayload(BaseModel):
    """新建一个技能。

    使用场景与正文是**拆开的两个字段**：前端编的就是这个形状，拼回 SKILL.md
    由业务层的 `skills.compose_skill` 负责（元信息块不能让用户直接编，见那个函数）。
    """

    name: str = Field(min_length=1, description="技能名，也就是目录名")
    description: str = Field(default="", description="使用场景：什么时候该加载它")
    content: str = Field(default="", description="正文（不含元信息块）")


class SkillContentPayload(BaseModel):
    """覆盖一个已有技能的使用场景与正文。

    名字不可改：技能目录名就是技能的标识（提示词已经换成 id 标识，技能还没有）。
    """

    description: str = Field(default="", description="使用场景")
    content: str = Field(default="", description="正文（不含元信息块）")


class TogglePayload(BaseModel):
    """记忆的「开 / 关」（工具、技能的全局开关已移除，改由各自的组决定）。"""

    enabled: bool


class SkillGroupPayload(BaseModel):
    """新增 / 修改一个技能组。

    技能名是否真实存在由路由层对着 skills/ 目录校验（schema 层不知道目录里有什么）。
    """

    name: str = Field(min_length=1, description="组名")
    description: str = Field(default="", description="功能简介")
    skills: list[str] = Field(default_factory=list, description="组内技能名")


class ToolGroupPayload(BaseModel):
    """新增 / 修改一个工具组。

    工具名是否真实存在不在这里校验：schema 层不知道注册表里有什么，
    由路由层对着注册表查（错误才能变成 400 而不是 500）。
    """

    name: str = Field(min_length=1, description="组名")
    description: str = Field(default="", description="功能简介")
    tools: list[str] = Field(default_factory=list, description="组内工具名")
    confirm: list[str] = Field(default_factory=list, description="其中需要用户确认的工具名")


class PromptGroupPayload(BaseModel):
    """新增 / 修改一个提示词组。

    偏好模型不在这里 —— 它随「模式」走（见 `ModePayload`）：提示词组只决定
    「用哪些提示词」，不该顺带决定用哪个模型。
    """

    name: str = Field(min_length=1, description="组名")
    description: str = Field(default="", description="功能简介")
    prompts: list[str] = Field(
        default_factory=list,
        description="提示词 id 列表；同一个分类可以选多条",
    )


class ModePayload(BaseModel):
    """新增 / 修改一个模式。

    四类组都按 **id** 引用（不是组名）：组改名后模式不会跟着失效。
    空串表示「这一类什么都不给」—— 三个都空就是纯问答模式。
    """

    name: str = Field(min_length=1, description="模式名")
    description: str = Field(default="", description="模式简介")
    prompt_group_id: str = Field(default="", description="提示词组 id")
    tool_group_id: str = Field(default="", description="工具组 id")
    skill_group_id: str = Field(default="", description="技能组 id")
    memory_enabled: bool = Field(default=True, description="是否启用记忆")
    preferred_model: str = Field(default="", description="偏好模型标识；空表示沿用当前")


class MemoryPayload(BaseModel):
    """新增一条记忆。"""

    text: str = Field(min_length=1)


class PreferencePayload(BaseModel):
    """批量写入界面偏好。"""

    values: dict[str, str]


class SearchConfigPayload(BaseModel):
    """保存联网搜索的配置。

    `keys` 是**后端 -> Key** 的字典而不是单个字段：Tavily 和博查之间切来切去是
    常事，只留一个字段的话，切一次就把另一边填过的 Key 抹掉了。
    """

    backend: SearchBackend = SearchBackend.TAVILY
    keys: dict[str, str] = Field(default_factory=dict, description="后端 -> API Key")
    base_url: str = Field(default="", description="覆盖默认端点；留空用内置地址")
    max_results: int = Field(
        default=DEFAULT_MAX_RESULTS, ge=1, le=MAX_RESULTS_LIMIT, description="默认返回条数"
    )


class SearchTestPayload(SearchConfigPayload):
    """用**界面上当前填的**配置试搜一次，不必先保存。

    先测后存能省掉一轮「存了个错的、又得改回来」，所以测试走的是请求体里的配置，
    而不是已保存的那份。
    """

    query: str = Field(default="", description="测试用的搜索词；留空用一句默认词")


class WorkDirPayload(BaseModel):
    """切换文件工具的工作目录（同时是安全边界）。"""

    path: str
    # 传会话 id 是为了校验「只有空会话能换目录」——后端自己不知道当前在聊哪个
    # 会话（无状态），所以由界面带上
    conversation_id: str = ""
