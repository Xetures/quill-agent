"""联网搜索配置接口。

一共三个：读配置、存配置、试搜一次。试搜传的是**请求体里的配置**而不是已保存
的那份 —— 「先测通了再存」比「存完了发现 Key 是错的」省一轮往返。
"""

from __future__ import annotations

from fastapi import APIRouter

from quill_agent.search import (
    DEFAULT_MAX_RESULTS,
    MAX_RESULTS_LIMIT,
    SearchBackend,
    SearchConfig,
    SearchError,
    search,
)
from server import stores
from server.schemas import SearchConfigPayload, SearchTestPayload

router = APIRouter(tags=["search"])

# 试搜在用户没给搜索词时用的默认词。挑一个「一定有结果」的，
# 目的只是验证 Key 和网络通不通，不关心搜出来是什么
_DEFAULT_TEST_QUERY = "quill agent"


@router.get("/search")
def get_search_config() -> dict:
    """当前配置 + 支持的后端清单。

    后端清单随接口一起给出去，省得前端把「有哪些后端、各自端点是什么、
    Key 去哪领」再写一遍 —— 多一处硬编码，将来加一家就要改两个地方。
    """
    config = stores.search().load()
    return {
        "config": config.model_dump(mode="json"),
        "backends": [
            {
                "value": backend.value,
                "label": backend.label,
                "endpoint": backend.endpoint,
                "hint": backend.hint,
            }
            for backend in SearchBackend
        ],
        "limits": {
            "min": 1,
            "max": MAX_RESULTS_LIMIT,
            "default": DEFAULT_MAX_RESULTS,
        },
    }


@router.put("/search")
def save_search_config(payload: SearchConfigPayload) -> dict:
    """保存配置。整体覆写 —— 这一份配置只有一页在改。"""
    config = SearchConfig(**payload.model_dump())
    stores.search().save(config)
    return {"config": config.model_dump(mode="json")}


@router.post("/search/test")
def test_search(payload: SearchTestPayload) -> dict:
    """用当前填的配置真搜一次。

    返回 ok / message / hits：message 直接显示给用户（成功是条数，失败是原因）。
    搜到 0 条也算成功 —— 那说明 Key 和网络都是通的，只是这个词没结果。
    """
    config = SearchConfig(**payload.model_dump(exclude={"query"}))
    query = payload.query.strip() or _DEFAULT_TEST_QUERY

    try:
        hits = search(query, config=config, count=config.max_results)
    except SearchError as exc:
        # 配置类错误（Key 没填、401、地址不对）都在这儿 —— 它们是「填错了」，
        # 不是服务端故障，所以返回 200 + ok=false，而不是 4xx/5xx
        return {"ok": False, "message": str(exc), "hits": []}

    return {
        "ok": True,
        "message": f"搜索成功，返回 {len(hits)} 条结果。",
        "hits": [
            {"title": hit.title, "url": hit.url, "snippet": hit.snippet}
            for hit in hits
        ],
    }
