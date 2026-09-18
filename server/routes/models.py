"""模型连接、提示词模式、提示词库接口。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from quill_agent.core import test_connection
from quill_agent.models import ModelConfig, PromptMode, check_api_key, model_choice_key
from quill_agent.prompts import PROMPT_CATEGORIES
from server import stores
from server.schemas import ModelPayload, ModePayload, TestConnectionPayload

router = APIRouter(tags=["models"])


@router.get("/models")
def list_models() -> dict:
    """全部连接，外加前端下拉直接能用的「摊平选项」。

    摊平是刻意的：一个连接下可能有 N 个模型，让前端自己拼「连接 id + 模型名」
    很容易和 `model_choice_key` 的口径对不上，后端算好再给过去最省事。
    """
    configs = stores.models().list()
    options = [
        {
            "key": model_choice_key(config.id, name),
            "config_id": config.id,
            "config_name": config.name,
            "model": name,
            "label": f"{config.name} / {name}",
        }
        for config in configs
        for name in config.models
    ]
    return {"configs": configs, "options": options}


def _validate_key(payload: ModelPayload) -> None:
    """Key 的格式规则只有一份，在业务层；这里只把结果转成 HTTP 错误。"""
    error = check_api_key(payload.protocol, payload.api_key)
    if error:
        raise HTTPException(status_code=400, detail=error)


@router.post("/models")
def add_model(payload: ModelPayload) -> dict:
    _validate_key(payload)

    try:
        item = stores.models().add(
            name=payload.name,
            models=payload.models,
            base_url=payload.base_url,
            api_key=payload.api_key,
            protocol=payload.protocol,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return item.model_dump()


@router.put("/models/{config_id}")
def update_model(config_id: str, payload: ModelPayload) -> dict:
    _validate_key(payload)

    try:
        item = ModelConfig(
            id=config_id,
            name=payload.name,
            base_url=payload.base_url,
            protocol=payload.protocol,
            api_key=payload.api_key,
            models=payload.models,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    stores.models().update(item)
    return item.model_dump()


@router.delete("/models/{config_id}")
def delete_model(config_id: str) -> dict[str, bool]:
    stores.models().remove(config_id)
    return {"removed": True}


@router.post("/models/test")
def test_model_connection(payload: TestConnectionPayload) -> dict:
    """连通测试，顺带拉取可用模型名。

    走 `GET /models`，不消耗 token。并非所有服务都实现了这个端点，
    失败时前端应该允许手动录入模型名。
    """
    result = test_connection(
        base_url=payload.base_url,
        api_key=payload.api_key,
        protocol=payload.protocol,
    )
    return {
        "ok": result.ok,
        "message": result.message,
        "models": result.models,
        "detail": result.detail,
    }


# ---------------------------------------------------------------------------
# 提示词模式
# ---------------------------------------------------------------------------
@router.get("/modes")
def list_modes() -> dict:
    return {"modes": stores.modes().list()}


@router.post("/modes")
def add_mode(payload: ModePayload) -> dict:
    item = stores.modes().add(
        name=payload.name,
        settings=payload.settings,
        preferred_model=payload.preferred_model,
    )
    return item.model_dump()


@router.put("/modes/{mode_id}")
def update_mode(mode_id: str, payload: ModePayload) -> dict:
    item = PromptMode(
        id=mode_id,
        name=payload.name,
        settings=payload.settings,
        preferred_model=payload.preferred_model,
    )
    stores.modes().update(item)
    return item.model_dump()


@router.delete("/modes/{mode_id}")
def delete_mode(mode_id: str) -> dict[str, bool]:
    stores.modes().remove(mode_id)
    return {"removed": True}


# ---------------------------------------------------------------------------
# 提示词库
# ---------------------------------------------------------------------------
@router.get("/prompts")
def list_prompts() -> dict:
    """每个类别下有哪些可选提示词。

    只给名字、不给正文：界面填下拉框只需要名字，而正文可能很长。
    """
    library = stores.prompts()
    return {
        "categories": list(PROMPT_CATEGORIES),
        "names": {category: library.list_names(category) for category in PROMPT_CATEGORIES},
    }


@router.get("/prompts/{category}/{name}")
def read_prompt(category: str, name: str) -> dict:
    """某条提示词的正文。"""
    content = stores.prompts().read(category, name)
    if content is None:
        raise HTTPException(status_code=404, detail=f"提示词不存在：{category}/{name}")
    return {"category": category, "name": name, "content": content}
