"""HTTP 层的请求体。

响应直接返回业务层的对象（`ModelConfig` / `PromptGroup` / `Mode` / `MemoryItem` ……），
FastAPI 会自动序列化，不必再抄一遍。这里只定义「只有 HTTP 才需要」的形状。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from quill_agent.models import Protocol


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


class ModelPayload(BaseModel):
    """新增 / 修改一条模型连接。"""

    name: str = Field(min_length=1)
    base_url: str = ""
    protocol: Protocol = Protocol.OPENAI
    api_key: str = ""
    models: list[str] = Field(default_factory=list)
    context_window: int = Field(default=128000, gt=0, description="上下文窗口大小（tokens）")


class TestConnectionPayload(BaseModel):
    """连通测试 / 拉取模型列表。"""

    base_url: str = ""
    api_key: str = ""
    protocol: Protocol = Protocol.OPENAI


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


class PromptGroupPayload(BaseModel):
    """新增 / 修改一个提示词组。

    偏好模型不在这里 —— 它随「模式」走（见 `ModePayload`）：提示词组只决定
    「用哪些提示词」，不该顺带决定用哪个模型。
    """

    name: str = Field(min_length=1, description="组名")
    description: str = Field(default="", description="功能简介")
    settings: dict[str, str] = Field(default_factory=dict, description="类别 -> 提示词名")


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


class WorkDirPayload(BaseModel):
    """切换文件工具的工作目录（同时是安全边界）。"""

    path: str
    # 传会话 id 是为了校验「只有空会话能换目录」——后端自己不知道当前在聊哪个
    # 会话（无状态），所以由界面带上
    conversation_id: str = ""
