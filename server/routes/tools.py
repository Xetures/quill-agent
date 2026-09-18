"""工具与技能接口。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from quill_agent.config import get_settings
from quill_agent.store import skill_enabled
from quill_agent.tools import registry
from server import stores
from server.schemas import TogglePayload

router = APIRouter(tags=["tools"])


@router.get("/tools")
def list_tools() -> dict:
    """全部工具（开关状态已合并进来）与现有分类。

    `categories` 单独返回，省得前端自己从工具列表里去重 —— 筛选下拉要用它。
    """
    return {
        "tools": [spec.model_dump() for spec in registry.all()],
        "categories": registry.categories(),
    }


@router.patch("/tools/{name}")
def toggle_tool(name: str, payload: TogglePayload) -> dict:
    """开关一个工具。未启用的工具不会被发给模型，模型也就无从调用它。"""
    registry.set_enabled(name, payload.enabled)
    return {"name": name, "enabled": payload.enabled}


@router.get("/skills")
def list_skills() -> dict:
    """全部技能：元信息 + 开关状态。

    只给元信息（名字 + 适用场景），不返回正文 —— 正文可能上千字，
    列表页用不上，要看详情再单独取。
    """
    library = stores.skills()
    states = stores.skill_states().load()

    items = [
        {
            "name": meta.name,
            "description": meta.description,
            "enabled": skill_enabled(states, meta.name),
        }
        for meta in library.list_meta()
    ]
    return {"skills": items, "dir": str(get_settings().skills_dir)}


@router.get("/skills/{name}")
def read_skill(name: str) -> dict:
    """某个技能的正文（不含元信息块）。"""
    content = stores.skills().read(name)
    if content is None:
        raise HTTPException(status_code=404, detail=f"技能不存在：{name}")
    return {"name": name, "content": content}


@router.patch("/skills/{name}")
def toggle_skill(name: str, payload: TogglePayload) -> dict:
    """开关一个技能。停用的技能不会出现在给模型的清单里。"""
    if not stores.skills().exists(name):
        raise HTTPException(status_code=404, detail=f"技能不存在：{name}")

    stores.skill_states().set(name, payload.enabled)
    return {"name": name, "enabled": payload.enabled}
