"""HTTP 层的请求体。

响应直接返回业务层的对象（`ModelConfig` / `PromptMode` / `MemoryItem` ……），
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
    mode_id: str = Field(default="", description="提示词模式 id；空表示不带系统提示词")


class ModelPayload(BaseModel):
    """新增 / 修改一条模型连接。"""

    name: str = Field(min_length=1)
    base_url: str = ""
    protocol: Protocol = Protocol.OPENAI
    api_key: str = ""
    models: list[str] = Field(default_factory=list)


class TestConnectionPayload(BaseModel):
    """连通测试 / 拉取模型列表。"""

    base_url: str = ""
    api_key: str = ""
    protocol: Protocol = Protocol.OPENAI


class TogglePayload(BaseModel):
    """开关类操作共用（工具、技能、记忆都只有「开 / 关」）。"""

    enabled: bool


class ModePayload(BaseModel):
    """新增 / 修改一个提示词模式。"""

    name: str = Field(min_length=1)
    settings: dict[str, str] = Field(default_factory=dict, description="类别 -> 提示词名")
    preferred_model: str = ""


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
