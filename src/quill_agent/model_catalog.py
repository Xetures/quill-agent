"""模型规格快照：模型名 -> 上下文窗口大小（tokens）。

**为什么需要这一层。** 绝大多数官方端点的 `GET /models` 只回 id / created / owned_by，
问不出窗口大小；而窗口大小是「用量仪表盘」的分母，填错还不如不填。别的 Agent 之所以
让人觉得「从来不用手填」，是因为它们内置了一份「模型名 → 规格」的表，运行时查表 ——
不是问端点。这里把同一件事做成本地快照：

    data/model_catalog.json      ← 从 models.dev 拉的一次快照，也可以自己手写

**刻意不内置一份写死的表。** 模型规格变得很快（同一家的模型半年前是 64k、现在是 128k），
一份过期的内置表会给出看起来对、其实是错的值 —— 那比空着更糟。宁可空着让用户自己填。

**查表只做归一化后的精确匹配**，不做「前缀 / 包含」这类模糊匹配：`gpt-4` 是 8k、
`gpt-4-1106-preview` 是 128k，两个名字互为前缀而窗口差了 16 倍。模糊匹配在这种地方
一定会命中错误的行，而代价是用户以为还有空间、实际早就超了。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from quill_agent.locking import atomic_write_text
from quill_agent.models import Protocol
from quill_agent.store import read_json

# 快照里可能表示「窗口」的字段名。`context` 是 models.dev 的 `limit.context` 用的名字
CONTEXT_KEYS = (
    "context_length",
    "max_model_len",
    "context_window",
    "max_context_length",
    "max_input_tokens",
    "context",
)

# 快照里可能表示「模型名」的字段名
NAME_KEYS = ("id", "name", "model")

# 下载快照的超时（秒）。models.dev 的 api.json 有几个 MB，给宽一点
REFRESH_TIMEOUT = 30.0

# models.dev 前面挡着 CDN，默认的 `Python-urllib/3.x` 会被 403 掉，得自己带一个
USER_AGENT = "quill-agent/0.1 (+https://models.dev)"


def normalize(model: str) -> str:
    """把模型名归一化，好让同一个模型的不同写法对上同一行。

    中转站和本地部署的写法很杂：`anthropic/claude-sonnet-4-5`、`claude-3.5-sonnet`、
    `deepseek-chat-0324`、`gpt-4o-2024-11-20`。统一处理成：小写、去掉 vendor 前缀、
    点 / 下划线转连字符、剥掉末尾的日期或版本尾巴。
    """
    text = model.strip().lower().replace("_", "-").replace(".", "-")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]

    # 末尾的日期尾巴：-2024-11-20 / -20241022 / -0324。
    # **只剥纯数字尾巴** —— `-preview`、`-mini`、`-v3` 要留着，它们是不同的模型
    text = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", text)
    text = re.sub(r"-\d{8}$", "", text)
    text = re.sub(r"-\d{4}$", "", text)

    return text


def _number(node: dict, keys: tuple[str, ...]) -> int | None:
    """从字典里按候选键名取一个正整数；取不到返回 None。"""
    for key in keys:
        value = node.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def _flatten(payload: Any) -> dict[str, int]:
    """把快照摊成 `{归一化模型名: 窗口}`。

    容忍几种形状：手写的扁平表 `{"gpt-4o": 128000}`；models.dev 那种
    `provider -> models -> {id: {limit: {context}}}`；以及 `[{id, context_length}]` 列表。
    摊不出来的一律跳过 —— 快照坏了应该降级成「查不到」，而不是让页面打不开。
    """
    # 手写的扁平表：整个对象就是「名字 -> 数字」
    if isinstance(payload, dict) and payload:
        values = list(payload.values())
        if all(isinstance(value, int) and not isinstance(value, bool) for value in values):
            return {
                normalize(str(key)): int(value)
                for key, value in payload.items()
                if int(value) > 0 and str(key).strip()
            }

    found: dict[str, int] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            name = next(
                (
                    node[key]
                    for key in NAME_KEYS
                    if isinstance(node.get(key), str) and node[key].strip()
                ),
                None,
            )

            window = _number(node, CONTEXT_KEYS)
            if window is None and isinstance(node.get("limit"), dict):
                window = _number(node["limit"], CONTEXT_KEYS)

            if name and window:
                key = normalize(name)
                # 归一化会把 `gpt-4o-2024-05-13` 和 `gpt-4o` 折成同一个键，先到先得。
                # 但名字本身就是规范形式的那条更可能代表「当前版本」，让它覆盖掉
                # 带日期尾巴的那条
                if key not in found or name == key:
                    found[key] = window

            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return found


# ---------------------------------------------------------------------------
# 服务商目录
#
# 和上面「模型名 -> 窗口」出自**同一份快照、同一次下载**：models.dev 的 api.json
# 里，服务商那一层本来就带着官方端点地址（`api`）、包名（`npm`）和模型清单。
# 上面那份是把它拍扁成「模型 -> 窗口」（查表用），这里是把服务商那一层留下来
# （填连接用）。两份都写盘，是为了各自的用途都保持简单 —— 拼成一份的话，
# 查窗口要遍历嵌套结构，而填连接又要从一个扁表里反推服务商。
# ---------------------------------------------------------------------------

# 从 `npm`（哪个 SDK 包）推协议：**只特判 anthropic，其余一律按 OpenAI 兼容算**。
# 不写一张「包名 -> 协议」的大表：兼容是事实标准，而那张表会随着对方改包名
# 一条条失效，失效的表现是「选了服务商却调不通」—— 比默认按兼容处理糟得多。
ANTHROPIC_NPM = "@ai-sdk/anthropic"


def _protocol_of(node: dict) -> str:
    """这份服务商该用哪套协议（见上面 `ANTHROPIC_NPM` 的说明）。"""
    if str(node.get("npm") or "").strip() == ANTHROPIC_NPM:
        return Protocol.ANTHROPIC.value
    return Protocol.OPENAI.value


def _providers(payload: Any) -> list[dict[str, str]]:
    """把快照摊成服务商清单 `[{id, name, base_url, protocol}]`，按名字排序。

    **`base_url` 允许是空串，而且第一方官方恰恰都是空的**：models.dev 只给
    「地址得自己填」的那些写 `api`（中转站、自建网关、各家兼容端点），
    OpenAI / Anthropic / Groq / Google 这些都不写 —— SDK 自己知道官方地址，
    没必要再抄一遍。所以**不能拿 `api` 当筛选条件**，那样正好把最常用的几家全漏掉。

    留空在这里不是缺陷：本项目的 `base_url` 留空就是这个意思
    （见 `Protocol.default_base_url`），照原样带过去即可。
    """
    if not isinstance(payload, dict):
        return []

    found: dict[str, dict[str, str]] = {}
    for key, node in payload.items():
        if not isinstance(node, dict):
            continue

        provider_id = str(node.get("id") or key).strip()
        if not provider_id:
            continue

        api = node.get("api")
        found[provider_id] = {
            "id": provider_id,
            "name": str(node.get("name") or provider_id).strip(),
            "base_url": api.strip() if isinstance(api, str) else "",
            "protocol": _protocol_of(node),
        }

    # 排序按小写归一：直接按原串排的话，ASCII 里大写字母在小写之前，
    # 「AMD」会排到「Abacus」前面 —— 用户扫这个列表时看着就是乱的
    return sorted(found.values(), key=lambda item: item["name"].lower())


def load_providers(path: str | Path) -> list[dict[str, str]]:
    """读服务商清单；文件不存在或坏掉就返回空表。

    空表是正常状态（还没同步过），界面据此提示去点一次「同步模型库」——
    不像快照那样还需要区分「坏了」，因为这一层本来就是「没有就用不了」。
    """
    payload = read_json(path, [])
    if not isinstance(payload, list):
        return []

    # 只要求 id：`base_url` 空着是常态（见 `_providers` 的说明）
    return [
        item for item in payload if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]


class ModelCatalog:
    """本地模型规格快照。

    Args:
        path: 快照文件路径。文件不存在时视为「没有快照」，查表一律返回 None。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._entries: dict[str, int] | None = None

    def entries(self) -> dict[str, int]:
        """全部条目；只解析一次，文件坏掉就退回空表。"""
        if self._entries is None:
            self._entries = _flatten(read_json(self.path, {}))
        return self._entries

    @property
    def available(self) -> bool:
        """快照能不能用（同步过、且至少解析出一条）。"""
        return bool(self.entries())

    def lookup(self, model: str) -> int | None:
        """查一个模型的窗口大小；查不到返回 None。"""
        if not model:
            return None
        return self.entries().get(normalize(model))


def refresh(
    path: str | Path,
    url: str,
    timeout: float = REFRESH_TIMEOUT,
    providers_path: str | Path | None = None,
) -> tuple[bool, str, int]:
    """从公共模型库拉一份快照写到本地。

    写成**摊平后的简单形式**（`{模型名: 窗口}`）而不是原样存下来：文件是给人看、
    给人改的，几百 KB 的嵌套结构没必要留在磁盘上。

    给了 `providers_path` 就**顺带**把服务商目录也写出来（见上面那段说明）：
    同一个下载里本来就有那一层，没有理由为了它再访问一次外网。
    服务商那半写失败不算失败 —— 两张表各有各的用处，快照照旧。

    Args:
        path: 模型规格快照的落盘路径。
        url: 下载地址。
        timeout: 下载超时（秒）。
        providers_path: 服务商目录的落盘路径；不给就只写快照。

    Returns:
        `(是否成功, 给用户看的文案, 收录的模型数)`。失败只返回文案不抛异常 ——
        拉不到快照只是「这一层没有」，不影响端点解析和用户手填。
    """
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"同步失败：{exc}", 0

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, f"同步失败：返回的不是合法 JSON（{exc}）", 0

    entries = _flatten(payload)
    if not entries:
        return False, "同步失败：返回里没有解析出模型规格", 0

    target = Path(path)
    # 原子替换，**不加锁**：这两份是「丢了随时能再拉一遍」的快照，就算两次同步撞在
    # 一起，写进去的也是同一份内容 —— 后到的覆盖先到的没有损失。真正要挡的是
    # 「写到一半」（比如下载完还没落完盘就退了），那是原子写管的事。
    atomic_write_text(
        target,
        json.dumps(dict(sorted(entries.items())), ensure_ascii=False, indent=2),
    )

    # 服务商目录。写不出来（缓存坏了、磁盘满）不让这次同步翻船 ——
    # 用户点的是「同步模型库」，结论是模型库同步好没有，服务商那一层下次会再写一遍
    if providers_path is not None:
        providers = _providers(payload)
        if providers:
            target = Path(providers_path)
            atomic_write_text(
                target,
                json.dumps(providers, ensure_ascii=False, indent=2),
            )

    return True, f"已收录 {len(entries)} 个模型", len(entries)
