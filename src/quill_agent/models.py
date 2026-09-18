"""数据模型定义：描述「模型配置」长什么样。

这一层只定义结构 + 字段级规则，不关心数据存在哪儿、怎么展示。
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Protocol(str, Enum):
    """接口协议类型，决定 API Key 采用哪种格式校验。"""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"

    @property
    def label(self) -> str:
        """界面展示用的名称。"""
        return {
            Protocol.OPENAI: "OpenAI 协议",
            Protocol.ANTHROPIC: "Anthropic 协议",
        }[self]


# 各协议对应的 API Key 格式
API_KEY_PATTERNS: dict[Protocol, re.Pattern[str]] = {
    # 形如 sk-xxx / sk-proj-xxx；负向断言用于排除 Anthropic 的 sk-ant- 前缀
    Protocol.OPENAI: re.compile(r"^sk-(?!ant-)[A-Za-z0-9_\-]{20,}$"),
    # 形如 sk-ant-api03-xxx
    Protocol.ANTHROPIC: re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,}$"),
}

# 校验失败时给用户看的格式示例
API_KEY_HINTS: dict[Protocol, str] = {
    Protocol.OPENAI: "以 sk- 开头，例如 sk-proj-Ab12…（后续至少 20 位字母数字）",
    Protocol.ANTHROPIC: "以 sk-ant- 开头，例如 sk-ant-api03-Ab12…（后续至少 20 位字母数字）",
}


def check_api_key(protocol: Protocol, api_key: str) -> str | None:
    """按所选协议校验 API Key 格式。

    允许留空：本地部署的服务（如 Ollama 的兼容接口）通常不校验 Key。

    Returns:
        校验通过返回 None；失败返回可直接展示给用户的错误文案。
    """
    if not api_key:
        return None

    if not API_KEY_PATTERNS[protocol].match(api_key):
        return f"API Key 与「{protocol.label}」格式不符：{API_KEY_HINTS[protocol]}"

    return None


class ModelConfig(BaseModel):
    """一条模型配置：一份连接信息 + 该连接下可用的多个模型。

    这样同一套凭证只配置一次，改 Key 也只改一条记录。
    一个 API 通常能用多个模型（如 DeepSeek 的 chat / reasoner），
    所以模型名做成列表而不是单个字段。

    Attributes:
        id: 唯一标识，用于编辑 / 删除时定位记录（对用户不可见）。
        name: 展示名称，例如「DeepSeek 官方」。
        base_url: 接口地址，例如「https://api.deepseek.com」。
        protocol: 接口协议，决定 API Key 的格式要求。
        api_key: 接口鉴权 Key；注意会以明文写入 JSON 文件。
        models: 该连接下可用的模型名，例如 ["deepseek-chat", "deepseek-reasoner"]。
    """

    id: str = Field(description="唯一标识")
    name: str = Field(min_length=1, description="名称")
    base_url: str = Field(default="", description="接口 URL")
    # 旧数据没有这个字段，默认按 OpenAI 协议处理，保证向下兼容
    protocol: Protocol = Field(default=Protocol.OPENAI, description="接口协议")
    api_key: str = Field(default="", description="接口鉴权 Key")
    models: list[str] = Field(default_factory=list, description="该连接下可用的模型名")

    @model_validator(mode="before")
    @classmethod
    def _migrate_single_model(cls, data: Any) -> Any:
        """兼容早期格式：那时一条记录只能存一个模型，字段名叫 model。"""
        if isinstance(data, dict) and "model" in data and "models" not in data:
            migrated = dict(data)
            single = migrated.pop("model")
            migrated["models"] = [single] if single else []
            return migrated
        return data

    @model_validator(mode="after")
    def _validate_api_key(self) -> ModelConfig:
        """兜底校验：即使绕过界面直接构造对象，也不会写入格式错误的 Key。"""
        error = check_api_key(self.protocol, self.api_key)
        if error:
            raise ValueError(error)
        return self


class ModelChoice(BaseModel):
    """一次具体的模型选择：连接配置 + 模型名。

    任务页选择器的结果就是它。底层拿到后可以直接用
    `choice.config.base_url` / `choice.config.api_key` / `choice.model` 发请求。
    """

    config: ModelConfig
    model: str = Field(min_length=1, description="选中的模型名")


def model_choice_key(config_id: str, model: str) -> str:
    """模型选择的稳定标识：连接 id + 模型名。

    用它而不是列表下标 —— 连接或模型被增删时下标会漂移（删掉一个连接，
    原本的「第 3 个」可能变成「第 2 个」，选中项就悄悄换了模型），
    而这个标识永远指向同一个东西。

    任务页的模型选择、模式的偏好模型都用这个口径，两边可以直接互相赋值。
    """
    return f"{config_id}::{model}"


class PromptMode(BaseModel):
    """一个提示词模式：从六类提示词里各挑一个（可以不挑）拼成一套提示词。

    Attributes:
        id: 唯一标识，用于删除时定位记录（对用户不可见）。
        name: 模式名称，例如「严谨分析」。
        settings: {类别: 提示词名}，只包含用户实际选择的那几类；
            例如 {"身份": "AI助手", "约束": "合规红线"}。
    """

    id: str = Field(description="唯一标识")
    name: str = Field(min_length=1, description="模式名称")
    settings: dict[str, str] = Field(default_factory=dict, description="类别 -> 提示词名")
    # 旧数据没有这个字段，默认空串，保证向下兼容
    preferred_model: str = Field(
        default="",
        description="偏好模型的稳定标识；空表示不指定",
    )
