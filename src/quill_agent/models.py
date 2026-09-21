"""数据模型定义：描述「模型配置」长什么样。

这一层只定义结构 + 字段级规则，不关心数据存在哪儿、怎么展示。
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator


class Protocol(str, Enum):
    """接口协议类型，决定 API Key 采用哪种格式校验、以及用哪套客户端去调。"""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"

    @property
    def label(self) -> str:
        """界面展示用的名称：用各家对自家接口的正式叫法。

        笼统的「OpenAI 协议」并不标准：OpenAI 的对话接口正式名叫
        **Chat Completions API**（`POST /v1/chat/completions`），
        Anthropic 的叫 **Messages API**（`POST /v1/messages`）。
        第三方中转站/本地服务凡是兼容这套请求体，业界统称「OpenAI 兼容」，
        但做枚举项时还是写全称更清楚。

        Ollama 是个特例：它有自己的一套原生接口（`/api/chat`），但**同时**
        提供了 OpenAI 兼容层（`/v1/chat/completions`）。这里接的是后者 ——
        单独为它写一套协议适配不值得，兼容层能覆盖全部功能。
        """
        return {
            Protocol.OPENAI: "OpenAI Chat Completions",
            Protocol.ANTHROPIC: "Anthropic Messages",
            Protocol.OLLAMA: "Ollama（本地）",
        }[self]

    @property
    def openai_compatible(self) -> bool:
        """是否走 OpenAI SDK 的 Chat Completions 接口。

        这个判断原先散在 `core.fetch_models` 和 `agent._run_stream` 里各写一遍
        （都是 `is not Protocol.OPENAI` 就报「暂未实现」）。收拢到这里是因为
        加一个兼容协议要改的地方越少越好 —— 否则漏改一处，界面能选、调用却报
        「暂未实现」，这种不一致比不支持更让人困惑。
        """
        return self in (Protocol.OPENAI, Protocol.OLLAMA)

    def resolve_base_url(self, raw: str) -> str:
        """把用户填的地址补成可直接交给 HTTP 客户端的完整 URL（空串 = 用 SDK 默认）。

        为什么要这一层：界面上填地址时写「192.168.2.165:11434」很自然，但 HTTP
        客户端要带协议的完整 URL；而 Ollama 的兼容层又固定在 `/v1` 下 —— 两处都靠
        用户记住太苛刻，何况**连不上时的报错根本不会提这两件事**。

        补全规则：

            留空                     -> 协议默认地址（本机 Ollama 就是它）
            host:port                -> 补 `http://`
            http://host:port         -> 补 `/v1`（**仅 Ollama**，且路径为空时）
            http://host:port/v1      -> 原样
            http://host/custom/path  -> 原样（用户挂了反代/网关，不要乱动）

        为什么只给 Ollama 补 `/v1`：远端协议的 base_url 本来就不带它，SDK 自己会拼
        `/chat/completions`；只有本地兼容层把路径固定成了 `/v1`。
        """
        text = (raw or "").strip().rstrip("/")
        if not text:
            return self.default_base_url

        if "://" not in text:
            text = f"http://{text}"

        if self is Protocol.OLLAMA and urlparse(text).path in ("", "/"):
            text = f"{text}/v1"

        return text

    @property
    def default_base_url(self) -> str:
        """地址留空时用的默认值；空串表示「交给 SDK 用它自己的官方地址」。

        只有本地服务才有这个待遇：Ollama 默认就监听 `11434`，且 OpenAI 兼容层
        挂在 `/v1` 下。远端服务没有这种「人人如此」的地址，硬给一个默认值只会
        让人对着一个连不上的地址排查半天。
        """
        return {Protocol.OLLAMA: "http://localhost:11434/v1"}.get(self, "")


# 各协议对应的 API Key 格式
API_KEY_PATTERNS: dict[Protocol, re.Pattern[str]] = {
    # 形如 sk-xxx / sk-proj-xxx；负向断言用于排除 Anthropic 的 sk-ant- 前缀
    Protocol.OPENAI: re.compile(r"^sk-(?!ant-)[A-Za-z0-9_\-]{20,}$"),
    # 形如 sk-ant-api03-xxx
    Protocol.ANTHROPIC: re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,}$"),
    # Ollama 不校验 Key，所以这里没有可依据的格式。只拦「明显填错」的输入
    # （带空格、中文、换行之类），而不是真的按某种前缀去卡 —— 卡错了就会
    # 把一个本来能用的本地服务拦在门外。留空是正常用法，见 check_api_key。
    Protocol.OLLAMA: re.compile(r"^\S+$"),
}

# 校验失败时给用户看的格式示例
API_KEY_HINTS: dict[Protocol, str] = {
    Protocol.OPENAI: "以 sk- 开头，例如 sk-proj-Ab12…（后续至少 20 位字母数字）",
    Protocol.ANTHROPIC: "以 sk-ant- 开头，例如 sk-ant-api03-Ab12…（后续至少 20 位字母数字）",
    Protocol.OLLAMA: "本地服务一般不校验 Key，留空即可",
}


def protocol_options() -> list[dict[str, str]]:
    """协议清单，供界面渲染下拉框。

    由业务层下发而不是让前端自己硬编码一份：加协议时只改这里有多的枚举，
    界面自动出现那一项（和联网搜索的后端清单一个思路，见 `SearchBackend`）。
    """
    return [
        {
            "value": item.value,
            "label": item.label,
            "default_base_url": item.default_base_url,
            "hint": API_KEY_HINTS[item],
        }
        for item in Protocol
    ]


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
        confirm: 其中**每次调用都要用户点头**的工具名。是 `tools` 的子集。
            这个字段的动机很实际：`run_command` 这类工具要么「加进组 = 把一台机器
            交给它」，要么「不加 = 一点用没有」，中间没有档位。有了它就能表达
            「给，但每次动手前问我」—— 于是「是否授予能力」和「是否信任这次使用」
            变成两件事，后者由用户逐次决定。

            `tools` 里的工具名对着代码注册表校验，`confirm` 只需是 `tools` 的子集：
            一个不在场的工具要求确认是自相矛盾的配置，直接在保存时报错。
    """

    id: str = Field(description="唯一标识")
    name: str = Field(min_length=1, description="组名")
    description: str = Field(default="", description="功能简介")
    tools: list[str] = Field(default_factory=list, description="组内工具名")
    confirm: list[str] = Field(default_factory=list, description="其中需要用户确认的工具名")
    builtin: bool = Field(default=False, description="是否应用自带的出厂资源（不可删除）")


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
    builtin: bool = Field(default=False, description="是否应用自带的出厂资源（不可删除）")


class PromptGroup(BaseModel):
    """一个提示词组：从六类提示词里各挑一个（可以不挑）拼成一套提示词。

    与 ToolGroup / SkillGroup 同一套思路（见 README 3.6）。原先这个东西就叫
    「模式」，现在模式指四类组的组合（见 `Mode`），它只是其中提示词那一类。

    Attributes:
        id: 唯一标识，用于删除时定位记录（对用户不可见）。
        name: 组名，例如「严谨分析」。
        description: 功能简介，模式编辑界面用它帮用户想起这组是什么。
        prompts: 组内提示词的 **id 列表**。**允许为空** —— 那就退化成「不带任何
            系统提示词」的纯问答，不是每个任务都需要一整套提示词。

            为什么是 id 列表而不是 `{分类: 名字}`：后者等于「每个分类只能选一条」，
            于是同一类里想同时用两段内容时，只能把它们合并进一个文件。按 id 引用
            之后这个限制自然消失；而且**改名不再破坏引用**（名字是展示用的，id 才是标识）。

            顺序不在这里表达：拼接时按提示词自己的分类与名字排序
            （见 `agent.build_system_prompt`），这样同一套提示词在哪台机器、哪次运行
            拼出来的顺序都一致，前缀缓存才有意义。
    """

    id: str = Field(description="唯一标识")
    name: str = Field(min_length=1, description="组名")
    description: str = Field(default="", description="功能简介")
    prompts: list[str] = Field(default_factory=list, description="提示词 id 列表")
    builtin: bool = Field(default=False, description="是否应用自带的出厂资源（不可删除）")


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
    builtin: bool = Field(default=False, description="是否应用自带的出厂资源（不可删除）")
