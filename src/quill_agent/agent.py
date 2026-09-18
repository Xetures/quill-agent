"""Agent 循环：组装上下文 → 请求模型 → 执行工具 → 直到得出答案。

这一层不依赖 streamlit、不依赖界面：

    输入：本轮输入 + 模式 + 模型选择 + 历史
    输出：一个事件流（文本增量 / 思维链增量 / 工具调用记录 / 系统提示），
          由界面决定怎么渲染

上下文的组装顺序（稳定 → 易变），目的是让 system prompt 尽量稳定：

    system:  身份 → 能力 → 工具策略 → 工作流程 → 输出规范 → 约束
    system:  记忆清单（有启用中的记忆时才发）
    system:  技能清单（skills/ 里有技能时才发）
    user:    运行时上下文（时间、目录、附件）+ 本轮问题

记忆和技能各是**一条独立的 system 消息**（见 _run_stream 里
build_memory_block / build_skill_catalog 那两段），不是拼进第一条里的。

运行时信息刻意放在 user 消息里而不是 system prompt 里 —— 它每轮都变，
放进 system 会让前缀缓存永远失效。

关于流式：一次流式响应里可能**同时**有文本增量和工具调用增量，所以要做双轨处理 ——
文本可以立刻往外吐，工具调用只能累积（分片到达，中途是残缺 JSON），
必须等流结束才能执行。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

from openai import OpenAI

from quill_agent.config import get_settings
from quill_agent.memory import MemoryStore
from quill_agent.models import ModelChoice, PromptMode, Protocol
from quill_agent.prompts import PROMPT_CATEGORIES, PromptLibrary
from quill_agent.skills import SkillLibrary
from quill_agent.store import SkillStateStore, skill_enabled
from quill_agent.tools import registry
from quill_agent.tools.files import current_work_dir, save_attachments

# 一轮对话里最多允许执行几「轮」工具调用，防止模型陷入死循环。
# 注意它计的是工具轮次而不是请求次数：预算用尽后还会再发一次
# **不带工具**的请求让模型收尾，所以最多请求 MAX_ITERATIONS + 1 次。
MAX_ITERATIONS = 5

# 单次请求模型的超时（秒）
REQUEST_TIMEOUT = 120.0

# 发给模型的历史最多保留多少条会话记录。
# 按「记录」而不是「消息」计数：一条记录若含工具调用，展开后是一组消息，
# 按记录截断才不会把 assistant.tool_calls 和对应的 tool 消息从中间切开。
MAX_HISTORY_RECORDS = 20

# 单条工具结果回放给模型时的长度上限。读大文件的工具结果动辄上万字符，
# 原样带入会让历史迅速膨胀；这里只截断回放的那一份，
# 界面上展示的完整结果仍保存在会话文件里。
MAX_TOOL_RESULT_CHARS = 2000

# 技能清单里每条描述的展示上限。清单一到就要常驻上下文，
# 放任它变长就等于把「按需加载」又改回了「全量注入」。
MAX_SKILL_DESCRIPTION_CHARS = 120


@dataclass(frozen=True)
class ToolStep:
    """一次工具调用的记录，供界面展示。

    Attributes:
        elapsed: 这次调用花了多久（秒）。工具慢不慢，只能从它看出来。
    """

    name: str
    arguments: str
    result: str
    elapsed: float = 0.0


@dataclass
class RunStats:
    """一轮对话的资源消耗。

    刻意做成可变的、由调用方创建后交给 run_agent_stream 就地填充：生成器没法
    「返回」值，而用量和耗时都要等流结束才知道，只能这样把结果带出来。

    Attributes:
        prompt_tokens / completion_tokens / total_tokens: 接口返回的用量。
            服务不支持流式用量时不会带这些值，三项都保持 0。
        elapsed: 整轮耗时（秒），包含工具执行的时间。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    elapsed: float = 0.0

    def add_usage(self, usage) -> None:
        """累加一次请求的用量 —— 一轮里可能请求模型好几次。"""
        if usage is None:
            return

        self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
        self.total_tokens += getattr(usage, "total_tokens", 0) or 0


@dataclass(frozen=True)
class Notice:
    """系统提示：配置缺失、调用失败、模型空回答这类情况。

    它**不是模型输出**，所以和文本增量分开产出：界面可以单独渲染（标黄），
    也不会被当成对话内容写进会话历史 —— 否则「请先选择模型」这种提示会变成
    模型「说过的话」，下一轮又被塞回上下文里。
    """

    text: str


@dataclass(frozen=True)
class ReasoningDelta:
    """思维链的一个增量片段（推理模型专有）。

    它和正文一样是流式到达的，所以也按增量产出，由界面累积渲染。
    但两者性质不同：思维链是模型的内部过程，只用于展示，**不进历史** ——
    回传它既浪费上下文，也可能影响后续推理。
    """

    text: str


@dataclass(frozen=True)
class AgentResult:
    """一轮 Agent 对话的结果。

    Attributes:
        text: 最终回答；模型一个字都没说时是空字符串。
        steps: 中间发生过的工具调用，按发生顺序排列。
        notices: 系统提示。界面单独渲染，不进入下一轮的上下文。
        reasoning: 这一轮的思维链全文（推理模型才有）；只用于回看。
        stats: 本轮的用量与耗时，供界面展示和排查。
    """

    text: str
    steps: list[ToolStep] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    reasoning: str = ""
    stats: RunStats = field(default_factory=RunStats)


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


def build_memory_block() -> str:
    """拼「关于用户的已知信息」清单。

    和技能清单一样单独占一条 system 消息：记忆随时会变，拼进身份提示词的话，
    改一条记忆就把前面那段的缓存前缀一起废掉。

    Returns:
        清单文本；没有启用中的记忆时返回空串，调用方据此决定要不要加这条消息。
    """
    items = MemoryStore(get_settings().memory_path).enabled()
    if not items:
        return ""

    return "\n".join(
        [
            "关于用户的已知信息（已经确认过，不必再问）：",
            *(f"- {item.text}" for item in items),
        ]
    )


def build_skill_catalog() -> str:
    """拼「可用技能」清单：只有名字和适用场景，正文让模型按需去取。

    这是整个技能机制的关键：常驻上下文的只有这一小段清单。技能正文动辄上千字，
    但只有模型判断用得上的那一个，才会通过 read_skill 工具被取回来 ——
    用不到的技能一个 token 都不花。

    Returns:
        清单文本；没有可用技能时返回空串，调用方据此决定要不要加这条消息。
    """
    settings = get_settings()
    states = SkillStateStore(settings.skills_state_path).load()

    lines: list[str] = []
    for meta in SkillLibrary(settings.skills_dir).list_meta():
        if not skill_enabled(states, meta.name):
            continue

        description = meta.description or "（作者未填写适用场景）"
        if len(description) > MAX_SKILL_DESCRIPTION_CHARS:
            description = description[:MAX_SKILL_DESCRIPTION_CHARS] + "…"
        lines.append(f"- {meta.name}：{description}")

    if not lines:
        return ""

    return "\n".join(
        [
            "可用技能（任务匹配其中某一项时，先用 read_skill 工具读取完整说明再动手）：",
            *lines,
        ]
    )


def build_user_message(prompt: str, attachments: list[str]) -> str:
    """把本轮问题与运行时上下文拼成一条 user 消息。

    Args:
        prompt: 本轮用户输入。
        attachments: `save_attachments()` 返回的附件说明行（路径或失败原因）。
    """
    lines = [f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]

    # 工作目录：文件工具的根，也是它们的安全边界（见 tools/files.py）。
    # 必须用 current_work_dir() 而不是配置值 —— 界面上可能改过，
    # 告诉模型的和工具实际遵守的必须是同一个。
    lines.append(f"工作目录：{current_work_dir()}")

    if attachments:
        # 必须点明「用 read_file 读」：附件已经落盘，模型手上就有现成的工具；
        # 不说明的话，它可能以为内容早就随这条消息一起给它了
        lines.append("本次附件（已存到工作目录，要内容就用 read_file 读）：")
        lines.extend(f"- {item}" for item in attachments)

    return "\n".join([*lines, "", prompt])


def _clip(text: str, limit: int) -> str:
    """超长文本截断，并说明原长度 —— 让模型知道信息是不完整的。"""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（内容过长已截断，原长 {len(text)} 字符）"


def build_history_messages(history: list[dict] | None) -> list[dict]:
    """把会话记录还原成 API 需要的消息序列。

    会话记录是界面口径：``{role, content, ts, steps?}``。
    API 口径不同 —— 一轮里如果调用过工具，实际是一组消息：

        assistant(content, tool_calls) -> tool(tool_call_id, content) -> ...

    这里把记录里的 steps 还原成这组消息，模型下一轮才能看到
    「上一轮我查了什么、结果是什么」。少了这一步，模型每轮都会失忆，
    反复调用同样的工具去重新获取已经拿到过的信息。

    截断发生在**展开之前**（只取最近 MAX_HISTORY_RECORDS 条记录）：
    先展开再截断，可能把 assistant.tool_calls 和它的 tool 消息切开，
    产生 API 无法接受的非法序列。

    Args:
        history: 会话记录列表；记录里的 ts 等展示字段会被忽略。

    Returns:
        可直接拼进请求体的消息列表。
    """
    messages: list[dict] = []

    records = list(history or [])
    # 这里不能写成 records[-MAX_HISTORY_RECORDS:] —— 上限为 0 时 -0 等于 0，
    # 切片会退化成「全部保留」，语义正好相反。
    if MAX_HISTORY_RECORDS > 0:
        records = records[-MAX_HISTORY_RECORDS:]

    for index, record in enumerate(records):
        role = record.get("role")
        if role not in {"user", "assistant"}:
            continue

        content = record.get("content") or ""
        steps = [item for item in (record.get("steps") or []) if isinstance(item, dict)]

        if role == "user":
            if content:
                messages.append({"role": "user", "content": content})
            continue

        if not content and not steps:
            continue  # 空回答：发过去只会让模型困惑

        if not steps:
            messages.append({"role": "assistant", "content": content})
            continue

        # 自拟 tool_call id：它只需在这组消息内部自洽。
        # 与本轮实时产生的工具调用不会冲突（那个用服务端下发的 id）。
        calls = [
            {
                "id": f"history_{index}_{position}",
                "type": "function",
                "function": {
                    "name": step.get("name", ""),
                    "arguments": step.get("arguments") or "{}",
                },
            }
            for position, step in enumerate(steps)
        ]

        messages.append(
            {"role": "assistant", "content": content or None, "tool_calls": calls}
        )
        messages.extend(
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": _clip(step.get("result") or "", MAX_TOOL_RESULT_CHARS),
            }
            for call, step in zip(calls, steps, strict=True)
        )

    return messages


# 每个接口地址认不认 stream_options（流式响应里附带用量统计）。
#
# 少数网关不认这个参数、带上就直接报错，所以要探测一次并沿用 —— 不能每轮都白跑
# 一次注定失败的请求。值 False 表示「确认不支持」，没有这个键表示还没探测过。
#
# key 用 base_url 而不是一个全局布尔：认不认是**按服务商**不同的。用全局值的话，
# 一个不支持的网关会把所有连接一起拖下水 —— 之后切回支持的服务也拿不到用量统计。
_stream_usage_cache: dict[str, bool] = {}


def _open_stream(client, *, base_url: str, model: str, messages: list, tools):
    """发起一次流式请求，尽量让它带上用量统计。

    include_usage 不是所有 OpenAI 兼容服务都认。探测失败之后就**对这个地址**
    永久退回普通请求：用量统计是锦上添花，不能因为它让对话本身不可用。

    并发说明：chat 端点跑在线程池里，可能有两个请求同时探测同一个地址 ——
    它们会各发一次请求、写同一个结果。无害（最坏情况是多探测一次），
    所以没有加锁。
    """
    if _stream_usage_cache.get(base_url) is not False:
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                stream=True,
                stream_options={"include_usage": True},
            )
        except Exception:
            # 这里吞掉异常是故意的：真正的错误（鉴权、网络、模型名）会在下面那次
            # 普通请求里再抛一次，由调用方统一处理，不会丢掉错误信息
            _stream_usage_cache[base_url] = False
        else:
            _stream_usage_cache[base_url] = True
            return stream

    return client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        stream=True,
    )


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
    stats: RunStats | None = None,
) -> Iterator[str | ReasoningDelta | ToolStep | Notice]:
    """以流式方式跑一轮 Agent 对话。

    Args:
        prompt: 本轮用户输入。
        files: 上传的附件对象；会先落盘到工作目录，再把路径告诉模型。
        mode: 提示词模式；None 表示不使用系统提示词。
        choice: 选中的模型（连接配置 + 模型名）。
        history: 历史会话记录，元素形如 {"role": ..., "content": ...}；
            助手记录可带 "steps"（工具调用），会被还原成 tool 消息，
            并只保留最近 MAX_HISTORY_RECORDS 条，见 build_history_messages。

    Yields:
        文本增量（str）/ 思维链增量（ReasoningDelta）/ 工具调用记录（ToolStep）/
        系统提示（Notice）。任何异常都转成 Notice 产出，不向上抛。

    最多请求模型 MAX_ITERATIONS + 1 次：前 MAX_ITERATIONS 次带工具，最后
    一次不带。预算用尽后强制模型基于已有信息收尾，避免出现「工具已经执行、
    副作用已经发生，结果却没机会被模型看到」的浪费。
    """
    tracker = stats if stats is not None else RunStats()
    started = time.monotonic()

    try:
        yield from _run_stream(
            prompt=prompt,
            files=files,
            mode=mode,
            choice=choice,
            history=history,
            tracker=tracker,
        )
    finally:
        # 答完了、出错了、撞上限了 —— 不管从哪条路出去，耗时都要补上
        tracker.elapsed = time.monotonic() - started


def _run_stream(
    *,
    prompt: str,
    files: list,
    mode: PromptMode | None,
    choice: ModelChoice | None,
    history: list[dict] | None,
    tracker: RunStats,
) -> Iterator[str | ReasoningDelta | ToolStep | Notice]:
    """run_agent_stream 的真实实现。

    单独拆出来只是为了能用 try/finally 统一收尾：生成器里有好几处 return，
    挨个补耗时很容易漏掉，包一层最稳。
    """
    if choice is None:
        yield Notice("请先选择模型；若还没有模型，请到「模型管理」页面添加。")
        return

    if choice.config.protocol is not Protocol.OPENAI:
        yield Notice(f"暂未实现「{choice.config.protocol.label}」的调用。")
        return

    # ── ① 组装上下文 ──────────────────────────────────────────────
    messages: list[dict] = []

    system_prompt = build_system_prompt(mode)
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    # 记忆与技能清单各自单独占一条 system 消息，都不拼进上面那段。
    # 原因和「运行时上下文不放 system prompt」是同一个：这两段都会变
    # （改一条记忆、停用一个技能），跟身份提示词拼在一起的话，动一下就整段重来，
    # 前面那段的缓存前缀跟着作废。
    # 顺序是先「用户是谁」，再「这类活怎么干」。
    memory = build_memory_block()
    if memory:
        messages.append({"role": "system", "content": memory})

    catalog = build_skill_catalog()
    if catalog:
        messages.append({"role": "system", "content": catalog})

    # 历史是「会话记录」口径（带 ts / steps 等展示字段），这里统一还原成 API 口径
    messages.extend(build_history_messages(history))

    # 附件先落盘再组装消息：模型要的是「工作目录里的路径」，
    # 而不是一个它根本访问不到的内存对象
    attachments = save_attachments(files)
    messages.append({"role": "user", "content": build_user_message(prompt, attachments)})

    # ── ② 工具菜单：只含已启用的工具 ───────────────────────────────
    tools = registry.schemas()

    client = OpenAI(
        base_url=choice.config.base_url or None,
        api_key=choice.config.api_key or "EMPTY",  # 本地服务通常不校验
        timeout=REQUEST_TIMEOUT,
        max_retries=1,
    )

    # ── ③ 循环 ────────────────────────────────────────────────────
    # 整轮下来模型是否真的输出过文本。用于兜底：一个字都没说时给个提示，
    # 否则界面上会是一个空气泡 —— 推理模型把输出预算全花在思考上时就会这样。
    emitted_text = False

    # 工具轮次预算：每执行一轮工具就减一。减到 0 之后请求里不再带 tools，
    # 模型只能基于已有信息收尾 —— 不会出现「工具执行了、副作用发生了，
    # 结果却没机会被模型看到」的浪费。
    tool_budget = MAX_ITERATIONS

    # 循环一定终止：每次迭代要么直接 return（拿到回答 / 预算已耗尽），
    # 要么把 tool_budget 减一。所以最多请求 MAX_ITERATIONS + 1 次。
    while True:
        # 预算用尽就不再提供工具；空列表也不能传，部分服务不接受空的 tools
        available_tools = tools if tool_budget > 0 else None

        try:
            stream = _open_stream(
                client,
                base_url=choice.config.base_url,
                model=choice.model,
                messages=messages,
                tools=available_tools or None,
            )
        except Exception as exc:  # 网络、鉴权、模型名错误都归到这里
            yield Notice(f"调用模型失败：{exc}")
            return

        text_parts: list[str] = []
        tool_calls: dict[int, dict] = {}

        # 双轨解析：文本立即外吐，工具调用只累积
        try:
            for chunk in stream:
                # 用量在流末尾单独一个 chunk 里，它通常是不带 choices 的
                tracker.add_usage(getattr(chunk, "usage", None))

                if not chunk.choices:  # 有些服务会额外发一个只带用量的 chunk
                    continue

                delta = chunk.choices[0].delta

                # 推理模型的思考过程走独立字段。DeepSeek 系叫 reasoning_content，
                # 少数中转站叫 reasoning —— 都用 getattr 兜底：这不是 OpenAI 的
                # 标准字段，SDK 未必保留，取不到就静默跳过，不影响正文。
                reasoning = getattr(delta, "reasoning_content", None) or getattr(
                    delta, "reasoning", None
                )
                if reasoning:
                    yield ReasoningDelta(reasoning)

                if delta.content:
                    text_parts.append(delta.content)
                    emitted_text = True
                    yield delta.content

                if delta.tool_calls:
                    _accumulate_tool_calls(tool_calls, delta.tool_calls)
        except Exception as exc:
            yield Notice(f"读取流式响应失败：{exc}")
            return

        # 没有工具调用 = 本轮就是最终回答，结束
        if not tool_calls:
            if not emitted_text:
                yield Notice(
                    "模型没有返回任何内容。可以重试，或换一个模型 —— "
                    "推理模型有时会把输出预算全用在思考上。"
                )
            return

        # 预算已耗尽却还收到工具调用：说明服务端没遵守「不给 tools」这条约定。
        # 这里不能再执行 —— 结果没有下一次请求去消化，副作用纯属白做。
        if tool_budget <= 0:
            yield Notice(f"已达到 {MAX_ITERATIONS} 轮工具调用上限，模型仍未给出最终回答。")
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
            call_started = time.monotonic()
            result = registry.execute(slot["name"], slot["arguments"])

            yield ToolStep(
                name=slot["name"],
                arguments=slot["arguments"],
                result=result,
                elapsed=time.monotonic() - call_started,
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": slot["id"],  # 必须与请求里的 id 对应
                    "content": result,
                }
            )

        tool_budget -= 1
