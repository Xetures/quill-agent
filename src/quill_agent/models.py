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
        """界面展示用的名称：用两家官方对自家接口的正式叫法。

        笼统的「OpenAI 协议」并不标准：OpenAI 的对话接口正式名叫
        **Chat Completions API**（`POST /v1/chat/completions`），
        Anthropic 的叫 **Messages API**（`POST /v1/messages`）。
        第三方中转站/本地服务凡是兼容这套请求体，业界统称「OpenAI 兼容」，
        但做枚举项时还是写全称更清楚。
        """
        return {
            Protocol.OPENAI: "OpenAI Chat Completions",
            Protocol.ANTHROPIC: "Anthropic Messages",
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
        context_windows: 模型名 -> 上下文窗口大小（tokens）。任务页的用量仪表盘靠它
            算占比。**它是模型的属性，不是连接的属性** —— 同一条连接下的多个模型窗口
            常常不一样（一条中转站同时挂着 64k 和 200k 的模型），一个连接级的值表达
            不了这件事。缺失某个模型就是「不知道」，仪表盘显示「—」而不是拿默认值硬凑。
    """

    id: str = Field(description="唯一标识")
    name: str = Field(min_length=1, description="名称")
    base_url: str = Field(default="", description="接口 URL")
    # 旧数据没有这个字段，默认按 OpenAI 协议处理，保证向下兼容
    protocol: Protocol = Field(default=Protocol.OPENAI, description="接口协议")
    api_key: str = Field(default="", description="接口鉴权 Key")
    models: list[str] = Field(default_factory=list, description="该连接下可用的模型名")
    context_windows: dict[str, int] = Field(
        default_factory=dict,
        description="模型名 -> 上下文窗口大小（tokens）；没有条目表示未知",
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_old_fields(cls, data: Any) -> Any:
        """兼容两种更早的写法。"""
        if not isinstance(data, dict):
            return data

        migrated = dict(data)

        # 早期格式：一条记录只能存一个模型，字段名叫 model
        if "model" in migrated and "models" not in migrated:
            single = migrated.pop("model")
            migrated["models"] = [single] if single else []

        # 旧格式：上下文窗口挂在连接上（一个连接只有一个值）。现在它是模型级属性，
        # 把那个值摊给当时配的所有模型 —— 这正是旧数据想表达的意思，不必让用户重填
        legacy = migrated.pop("context_window", None)
        if "context_windows" not in migrated and isinstance(legacy, int) and legacy > 0:
            names = migrated.get("models") or []
            migrated["context_windows"] = {name: legacy for name in names if name}

        return migrated

    @model_validator(mode="after")
    def _validate_windows(self) -> ModelConfig:
        """窗口大小必须是正数：0 或负数会让仪表盘的占比算成 0% 或负值。"""
        for model, tokens in self.context_windows.items():
            if tokens <= 0:
                raise ValueError(f"上下文窗口必须大于 0：{model}")

        return self

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


class ToolGroup(BaseModel):
    """一个工具组：给模型的一套工具搭配方案。

    背景：模式原先只管提示词 —— 选了「纯问答」，工具、技能、记忆照样全量
    拼进上下文。资源需要一层「组」作为搭配单位，模式最终是四类资源的组合；
    工具组是其中之一（提示词组见 `PromptGroup`，技能组见 `SkillGroup`，
    记忆不做组、只是一个开关）。

    Attributes:
        id: 唯一标识，用于编辑 / 删除时定位记录（对用户不可见）。
        name: 组名，例如「文件操作」「纯对话」。
        description: 功能简介，会出现在模式编辑界面，帮用户想起这组是干嘛的。
        tools: 组内工具名列表。**允许为空** —— 「纯对话」这种组就是要一个工具
            都不给，这是把工具从「全局开关」升级成「组」的核心动机。
    """

    id: str = Field(description="唯一标识")
    name: str = Field(min_length=1, description="组名")
    description: str = Field(default="", description="功能简介")
    tools: list[str] = Field(default_factory=list, description="组内工具名")


class SkillGroup(BaseModel):
    """一个技能组：给模型的一套技能搭配方案。

    与 ToolGroup 同一套思路（见 3.6）：模式最终是四类组的组合，这是技能这一环。

    Attributes:
        id: 唯一标识，用于编辑 / 删除时定位记录（对用户不可见）。
        name: 组名，例如「写作相关」。
        description: 功能简介，模式编辑界面用它帮用户想起这组是什么。
        skills: 组内技能名。允许为空 —— 有些模式一个技能都不该给。
    """

    id: str = Field(description="唯一标识")
    name: str = Field(min_length=1, description="组名")
    description: str = Field(default="", description="功能简介")
    skills: list[str] = Field(default_factory=list, description="组内技能名")


class PromptGroup(BaseModel):
    """一个提示词组：从六类提示词里各挑一个（可以不挑）拼成一套提示词。

    与 ToolGroup / SkillGroup 同一套思路（见 README 3.6）。原先这个东西就叫
    「模式」，现在模式指四类组的组合（见 `Mode`），它只是其中提示词那一类。

    Attributes:
        id: 唯一标识，用于删除时定位记录（对用户不可见）。
        name: 组名，例如「严谨分析」。
        description: 功能简介，模式编辑界面用它帮用户想起这组是什么。
        settings: {类别: 提示词名}，只包含用户实际选择的那几类；
            例如 {"身份": "AI助手", "约束": "合规红线"}。
            **允许为空** —— 那就退化成「不带任何系统提示词」的纯问答，
            不是每个任务都需要一整套提示词。
    """

    id: str = Field(description="唯一标识")
    name: str = Field(min_length=1, description="组名")
    description: str = Field(default="", description="功能简介")
    settings: dict[str, str] = Field(default_factory=dict, description="类别 -> 提示词名")


class Mode(BaseModel):
    """一个模式：Agent 的一套完整配置。

    模式 = 四类资源的搭配 + 记忆开关 + 偏好模型。它回答的是「这个 Agent 是什么
    样子」，而组回答的是「某一类资源给哪些」—— 前者引用后者，两层分开之后，
    同一个提示词组可以被多个模式复用。

    任务页**必须选中一个模式**（不能「不用模式」）：没有模式就没有系统提示词、
    工具、技能，那已经不是这个 Agent 了。

    Attributes:
        id: 唯一标识（界面上作为「模式 ID」展示，便于对照日志与配置）。
        name: 模式名。
        description: 模式简介。
        prompt_group_id / tool_group_id / skill_group_id: 引用的组 id。
            **空串表示这一类什么都不给**（纯问答就是三个都空）。
            存 id 而不是组名：组改名后模式不用跟着改。
        memory_enabled: 是否把记忆拼进上下文。关掉就是「这个模式不记得用户」。
        preferred_model: 偏好模型的稳定标识；空表示沿用任务页当前的模型。
    """

    id: str = Field(description="唯一标识")
    name: str = Field(min_length=1, description="模式名")
    description: str = Field(default="", description="模式简介")
    prompt_group_id: str = Field(default="", description="引用的提示词组 id")
    tool_group_id: str = Field(default="", description="引用的工具组 id")
    skill_group_id: str = Field(default="", description="引用的技能组 id")
    memory_enabled: bool = Field(default=True, description="是否启用记忆")
    preferred_model: str = Field(default="", description="偏好模型的稳定标识；空表示沿用当前")
