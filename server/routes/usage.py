"""用量统计接口。"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter

from quill_agent.usage import DEFAULT_DAYS, build_report
from server import stores

router = APIRouter(tags=["usage"])

# 折线最多看三个月。再往前拉没有意义（一天一个点，屏幕上也画不下），
# 而且每次请求都要把全部会话读一遍，得有个上限
MAX_DAYS = 90


@router.get("/usage")
def get_usage(days: int = DEFAULT_DAYS) -> dict:
    """token 用量：折线（按天、按模型）+ 表格（按任务）。

    `days` 只影响折线的窗口；表格始终覆盖全部会话（含归档）——
    用量页要能翻到更早的任务，而不是只能看最近七天。
    """
    window = max(1, min(days, MAX_DAYS))
    return asdict(build_report(stores.conversations(), days=window))
