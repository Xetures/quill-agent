"""Agent 循环：组装上下文 → 请求模型 → 执行工具 → 直到得出答案。

这一层不依赖 streamlit、不依赖界面：

    输入：本轮输入 + 模式 + 模型选择 + 历史
    输出：一个事件流（文本增量 / 工具调用记录），由界面决定怎么渲染

上下文的组装顺序（稳定 → 易变），目的是让 system prompt 尽量稳定：

    system:  身份 → 能力 → 工具策略 → 工作流程 → 输出规范 → 约束
             （技能、记忆留待后续加在这一段之后）
    user:    运行时上下文（时间、目录、附件）+ 本轮问题

运行时信息刻意放在 user 消息里而不是 system prompt 里 —— 它每轮都变，
放进 system 会让前缀缓存永远失效。

关于流式：一次流式响应里可能**同时**有文本增量和工具调用增量，所以要做双轨处理 ——
文本可以立刻往外吐，工具调用只能累积（分片到达，中途是残缺 JSON），
必须等流结束才能执行。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

from openai import OpenAI

from quill_agent.config import get_settings
from quill_agent.models import ModelChoice, PromptMode, Protocol
from quill_agent.prompts import PROMPT_CATEGORIES, PromptLibrary
from quill_agent.tools import registry
from quill_agent.tools.files import current_work_dir

# 一轮对话里最多允许几次「请求模型」，防止工具调用陷入死循环
MAX_ITERATIONS = 5

# 单次请求模型的超时（秒）
REQUEST_TIMEOUT = 120.0


@dataclass(frozen=True)
class ToolStep:
    """一次工具调用的记录，供界面展示。"""

    name: str
    arguments: str
    result: str


@dataclass(frozen=True)
class AgentResult:
    """一轮 Agent 对话的结果。

    Attributes:
        text: 最终回答；出错时是可直接展示的错误说明。
        steps: 中间发生过的工具调用，按发生顺序排列。
    """

    text: str
    steps: list[ToolStep] = field(default_factory=list)


def build_system_prompt(mode: PromptMode | None) -> str:
    """把模式里选中的提示词片段拼成 system prompt。

    顺序固定按 PROMPT_CATEGORIES（身份 → 能力 → 工具策略 → 工作流程 →
    输出规范 → 约束），与用户在弹窗里的勾选顺序无关 ——
    顺序稳定，同一模式下拼出来的内容才完全一致，前缀缓存才有意义。

    Args:
        mode: 选中的提示词模式；None 表示不带任何系统提示词（纯问答）。

    Returns:
        拼好的 system prompt；一个片段都没有时返回空字符串。
    """
    if mode is None:
        return ""

    library = PromptLibrary(get_settings().prompt_dir)

    parts: list[str] = []
    for category in PROMPT_CATEGORIES:
        name = mode.settings.get(category)
        if not name:
            continue
        content = library.read(category, name)
        if content:
            parts.append(content.strip())

    return "\n\n".join(parts)


def build_user_message(prompt: str, files: list) -> str:
    """把本轮问题与运行时上下文拼成一条 user 消息。

    附件目前只带上文件名 —— 内容解析留待后续实现。
    """
    lines = [f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]

    # 工作目录：文件工具的根，也是它们的安全边界（见 tools/files.py）。
    # 必须用 current_work_dir() 而不是配置值 —— 界面上可能改过，
    # 告诉模型的和工具实际遵守的必须是同一个。
    lines.append(f"工作目录：{current_work_dir()}")

    if files:
        names = "、".join(getattr(item, "name", str(item)) for item in files)
        lines.append(f"本次附件：{names}")

    return "\n".join([*lines, "", prompt])


def _accumulate_tool_calls(store: dict[int, dict], delta_calls) -> None:
    """把流式返回的工具调用分片累积起来。

    分片特点：同一个调用的 name / arguments 会分多次到达，必须按 index 累加；
    id 通常只在第一片里出现。中途任何时刻的 arguments 都可能是残缺 JSON，
    所以只能等整条流结束再解析执行。
    """
    for call in delta_calls:
        slot = store.setdefault(call.index, {"id": "", "name": "", "arguments": ""})

        if call.id:
            slot["id"] = call.id
        if call.function is None:
            continue
        if call.function.name:
            slot["name"] += call.function.name
        if call.function.arguments:
            slot["arguments"] += call.function.arguments


def _sorted_tool_calls(store: dict[int, dict]) -> list[dict]:
    """按 index 顺序取出累积好的工具调用。"""
    return [store[index] for index in sorted(store)]


def run_agent_stream(
    *,
    prompt: str,
    files: list,
    mode: PromptMode | None,
    choice: ModelChoice | None,
    history: list[dict] | None = None,
) -> Iterator[str | ToolStep]:
    """以流式方式跑一轮 Agent 对话。

    产出两类东西，界面按类型分别处理：

        str       —— 模型输出的文本增量，直接追加渲染即可
        ToolStep  —— 一次工具调用已完成（参数与结果都有了）

    Args:
        prompt: 本轮用户输入。
        files: 上传的文件列表（当前只取文件名）。
        mode: 提示词模式；None 表示不使用系统提示词。
        choice: 选中的模型（连接配置 + 模型名）。
        history: 历史对话，元素形如 {"role": ..., "content": ...}。

    Yields:
        文本增量或工具调用记录。任何异常都转成一段说明文本产出，不向上抛。
    """
    if choice is None:
        yield "请先选择模型；若还没有模型，请到「模型管理」页面添加。"
        return

    if choice.config.protocol is not Protocol.OPENAI:
        yield f"暂未实现「{choice.config.protocol.label}」的调用。"
        return

    # ── ① 组装上下文 ──────────────────────────────────────────────
    messages: list[dict] = []

    system_prompt = build_system_prompt(mode)
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    # 历史里可能带界面用的额外字段，这里只取 API 需要的两个
    for item in history or []:
        messages.append({"role": item["role"], "content": item["content"]})

    messages.append({"role": "user", "content": build_user_message(prompt, files)})

    # ── ② 工具菜单：只含已启用的工具 ───────────────────────────────
    tools = registry.schemas()

    client = OpenAI(
        base_url=choice.config.base_url or None,
        api_key=choice.config.api_key or "EMPTY",  # 本地服务通常不校验
        timeout=REQUEST_TIMEOUT,
        max_retries=1,
    )

    # ── ③ 循环 ────────────────────────────────────────────────────
    for _ in range(MAX_ITERATIONS):
        try:
            stream = client.chat.completions.create(
                model=choice.model,
                messages=messages,
                tools=tools or None,
                stream=True,
            )
        except Exception as exc:  # 网络、鉴权、模型名错误都归到这里
            yield f"调用模型失败：{exc}"
            return

        text_parts: list[str] = []
        tool_calls: dict[int, dict] = {}

        # 双轨解析：文本立即外吐，工具调用只累积
        try:
            for chunk in stream:
                if not chunk.choices:  # 有些服务会额外发一个只带用量的 chunk
                    continue

                delta = chunk.choices[0].delta

                if delta.content:
                    text_parts.append(delta.content)
                    yield delta.content

                if delta.tool_calls:
                    _accumulate_tool_calls(tool_calls, delta.tool_calls)
        except Exception as exc:
            yield f"读取流式响应失败：{exc}"
            return

        # 没有工具调用 = 本轮就是最终回答，结束
        if not tool_calls:
            return

        # 有工具调用：把这一轮的 assistant 消息回填（含模型已说出的文本和 tool_calls）
        messages.append(
            {
                "role": "assistant",
                "content": "".join(text_parts) or None,
                "tool_calls": [
                    {
                        "id": slot["id"],
                        "type": "function",
                        "function": {"name": slot["name"], "arguments": slot["arguments"]},
                    }
                    for slot in _sorted_tool_calls(tool_calls)
                ],
            }
        )

        # 逐个执行，并把结果作为 tool 消息回填
        for slot in _sorted_tool_calls(tool_calls):
            result = registry.execute(slot["name"], slot["arguments"])

            yield ToolStep(name=slot["name"], arguments=slot["arguments"], result=result)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": slot["id"],  # 必须与请求里的 id 对应
                    "content": result,
                }
            )

    yield f"已达到 {MAX_ITERATIONS} 轮工具调用上限，任务未完成。"
