"""用量统计：把会话记录里的 token 消耗按天、按任务汇总。

**只算 token，不算钱。** 各家怎么计价、有没有缓存折扣、什么时候调价，是供应商
自己的事；这里统计的是模型实际消耗的 token —— 唯一准确、且各家口径一致的东西。

**数据源就是会话文件本身**（assistant 消息里的 `stats`），不另立一份账本：
token 数早就随消息落盘了，再存一份只会多出一处会对不上的地方。代价要心里有数：
「清空归档」会把这些记录一起删掉，历史用量跟着变小。要留住历史就得另立账本，
那是以后的事。

**按模型分组依赖记录里的 `model` 字段**，而它是后加的 —— 在它之前产生的记录
读不到模型名，只进表格的合计、不进按模型的折线（frontend 会显示成「—」）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from quill_agent.history import ConversationStore

# 折线默认看最近七天
DEFAULT_DAYS = 7


@dataclass(frozen=True)
class ModelSeries:
    """一个模型的每日用量；`values` 与 `UsageReport.dates` 一一对应。"""

    model: str
    values: list[int]


@dataclass(frozen=True)
class TaskUsage:
    """一个任务的用量汇总（表格里的一行）。"""

    conversation_id: str
    title: str
    models: list[str]
    created_at: float
    tokens: int
    archived: bool


@dataclass(frozen=True)
class UsageReport:
    """用量页需要的东西：横轴、折线、表格。"""

    dates: list[str]
    series: list[ModelSeries]
    tasks: list[TaskUsage]


def _tokens_of(record: dict) -> int:
    """一条 assistant 记录消耗的 token 数。

    优先取 `total_tokens`；部分网关不返回用量，老记录里这个字段可能缺失或为 0，
    这时退回「输入 + 输出」相加。
    """
    stats = record.get("stats")
    if not isinstance(stats, dict):
        return 0

    total = stats.get("total_tokens")
    if isinstance(total, int) and total > 0:
        return total

    parts = [
        value
        for value in (stats.get("prompt_tokens"), stats.get("completion_tokens"))
        if isinstance(value, int) and value > 0
    ]
    return sum(parts)


def _day_of(record: dict) -> date | None:
    """记录落在哪一天（按本地时区的日期）。

    解析不出来就返回 None，调用方跳过 —— 旧记录的时间格式未必跟得上。
    """
    raw = record.get("ts")
    if not isinstance(raw, str):
        return None

    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def build_report(
    store: ConversationStore,
    days: int = DEFAULT_DAYS,
    today: date | None = None,
) -> UsageReport:
    """扫描全部会话（含归档），汇总成一份用量报告。

    Args:
        store: 会话存储。
        days: 折线的天数，含今天 —— `days=7` 就是今天往前数 7 天。
        today: 窗口的右端点，默认取今天。显式传进来是为了测试能定住时间。
    """
    end = today or date.today()
    window = [end - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    position = {day: index for index, day in enumerate(window)}

    # 模型名 -> 每日用量。用普通 dict 保序（Python 3.7+ 稳定），
    # 先出现的模型排在前面 —— 折线的颜色顺序才不会每次刷新都变
    daily: dict[str, list[int]] = {}
    tasks: list[TaskUsage] = []

    for meta, messages in store.iter_all():
        models: list[str] = []
        tokens = 0

        for record in messages:
            if record.get("role") != "assistant":
                continue

            amount = _tokens_of(record)
            tokens += amount

            raw_model = record.get("model")
            model = raw_model if isinstance(raw_model, str) else ""
            if model and model not in models:
                models.append(model)

            day = _day_of(record)
            if not model or not amount or day not in position:
                continue

            daily.setdefault(model, [0] * days)[position[day]] += amount

        tasks.append(
            TaskUsage(
                conversation_id=meta.id,
                title=meta.title,
                models=models,
                created_at=meta.created_at,
                tokens=tokens,
                archived=meta.archived,
            )
        )

    # 窗口内一次都没用过的模型不画：全是 0 的线只会占着图例干扰阅读。
    # 这一条同时把「没有模型信息的老记录」挡在了折线外面
    series = [
        ModelSeries(model=model, values=values) for model, values in daily.items() if any(values)
    ]

    # 表格按创建时间倒序：新任务在最上面，和会话列表的观感一致
    tasks.sort(key=lambda item: item.created_at, reverse=True)

    return UsageReport(
        dates=[day.isoformat() for day in window],
        series=series,
        tasks=tasks,
    )
