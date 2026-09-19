"""模型连接、模式、提示词组、提示词库接口。"""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, HTTPException

from quill_agent.config import get_settings
from quill_agent.core import test_connection
from quill_agent.model_catalog import refresh
from quill_agent.models import (
    Mode,
    ModelConfig,
    check_api_key,
    model_choice_key,
)
from quill_agent.prompts import PROMPT_CATEGORIES
from server import stores
from server.schemas import (
    CatalogLookupPayload,
    ModelPayload,
    ModePayload,
    PromptContentPayload,
    PromptGroupPayload,
    PromptPayload,
    TestConnectionPayload,
)

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
            # 任务页的上下文用量仪表盘要拿它当分母，所以随选项一起给出去。
            # 0 表示这个模型没填窗口 —— 界面据此显示「—」，别拿默认值硬凑
            "context_window": config.context_windows.get(name, 0),
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
            context_windows=payload.context_windows,
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
            context_windows=payload.context_windows,
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
    """连通测试，顺带拉取可用模型名与各自的上下文窗口。

    走 `GET /models`，不消耗 token。并非所有服务都实现了这个端点，
    失败时前端应该允许手动录入模型名。

    每个模型的窗口按两步取：**先看接口返回**（少数服务会带上），**再查本地快照**
    （见 `quill_agent.model_catalog`），两边都没有就留空 —— 不猜默认值。
    合并放在服务端做，前端只负责把结果填进输入框。
    """
    result = test_connection(
        base_url=payload.base_url,
        api_key=payload.api_key,
        protocol=payload.protocol,
    )
    catalog = stores.model_catalog()

    models = []
    for item in result.models:
        window = item.context_window
        source = "endpoint" if window else ""

        if not window:
            window = catalog.lookup(item.name)
            source = "catalog" if window else ""

        models.append({"name": item.name, "context_window": window or 0, "source": source})

    return {
        "ok": result.ok,
        "message": result.message,
        "models": models,
        "detail": result.detail,
        # 快照还没同步过时，界面提示用户去点「同步模型库」
        "catalog_ready": catalog.available,
    }


# ---------------------------------------------------------------------------
# 模型规格快照（模型名 -> 上下文窗口）
# ---------------------------------------------------------------------------
@router.get("/model-catalog")
def get_model_catalog() -> dict:
    """本地快照的状态。界面用它决定要不要提示「还没同步」。"""
    catalog = stores.model_catalog()
    return {"available": catalog.available, "count": len(catalog.entries())}


@router.post("/model-catalog/lookup")
def lookup_model_catalog(payload: CatalogLookupPayload) -> dict:
    """按模型名批量查快照，只回查得到的那些。

    单独给一个接口，是因为这条路径**不依赖端点**：用户从下拉里手选一个模型、
    或打开一条已有连接时，端点可能都没被拉过，本地快照仍然能给出窗口。

    在服务端查而不是把整份快照发给前端：快照有几千条、几百 KB，
    而一次要查的通常只有几个名字。
    """
    catalog = stores.model_catalog()
    windows = {name: window for name in payload.names if (window := catalog.lookup(name))}
    return {"windows": windows}


@router.post("/model-catalog/refresh")
def refresh_model_catalog() -> dict:
    """从公共模型库拉一份最新的规格快照。

    **这是唯一一个会主动访问外部网络的接口**（默认 models.dev），而且只由用户点按钮
    触发 —— 不在后台偷偷跑。拉不到不影响任何功能：那只是「快照这一层没有」，
    端点解析和手动填写都还在。
    """
    settings = get_settings()
    ok, message, count = refresh(settings.model_catalog_path, settings.model_catalog_url)
    return {"ok": ok, "message": message, "count": count}


# ---------------------------------------------------------------------------
# 模式：四类组的组合 + 记忆开关 + 偏好模型
# ---------------------------------------------------------------------------
@router.get("/modes")
def list_modes() -> dict:
    """全部模式。

    顺带把三类组的 id -> 名字一起给出去：模式表里要显示「用了哪个组」，
    让前端自己再去拉三份组列表既慢又容易对不上。
    """
    return {
        "modes": stores.modes().list(),
        "groups": {
            "prompt": {item.id: item.name for item in stores.prompt_groups().list()},
            "tool": {item.id: item.name for item in stores.tool_groups().list()},
            "skill": {item.id: item.name for item in stores.skill_groups().list()},
        },
    }


@router.post("/modes")
def add_mode(payload: ModePayload) -> dict:
    item = Mode(id=uuid4().hex, **payload.model_dump())
    stores.modes().add(item)
    return item.model_dump()


@router.put("/modes/{mode_id}")
def update_mode(mode_id: str, payload: ModePayload) -> dict:
    item = Mode(id=mode_id, **payload.model_dump())
    stores.modes().update(item)
    return item.model_dump()


@router.delete("/modes/{mode_id}")
def delete_mode(mode_id: str) -> dict[str, bool]:
    stores.modes().remove(mode_id)
    return {"removed": True}


# ---------------------------------------------------------------------------
# 提示词组（原先的「模式」；模式现在是四类组的组合，这里只管提示词那一类）
# ---------------------------------------------------------------------------
@router.get("/prompt-groups")
def list_prompt_groups() -> dict:
    return {"groups": stores.prompt_groups().list()}


@router.post("/prompt-groups")
def add_prompt_group(payload: PromptGroupPayload) -> dict:
    item = stores.prompt_groups().add(
        name=payload.name,
        description=payload.description,
        settings=payload.settings,
    )
    return item.model_dump()


@router.put("/prompt-groups/{group_id}")
def update_prompt_group(group_id: str, payload: PromptGroupPayload) -> dict:
    item = stores.prompt_groups().get(group_id)
    if item is None:
        raise HTTPException(status_code=404, detail="提示词组不存在")

    item = item.model_copy(update={**payload.model_dump(), "id": group_id})
    stores.prompt_groups().update(item)
    return item.model_dump()


@router.delete("/prompt-groups/{group_id}")
def delete_prompt_group(group_id: str) -> dict[str, bool]:
    stores.prompt_groups().remove(group_id)
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


@router.post("/prompts")
def create_prompt(payload: PromptPayload) -> dict:
    """新建一条提示词。

    同名文件已存在时报 400 而不是覆盖：提示词名是提示词组里的引用标识，而
    「AI助手」这种名字很容易撞上，悄悄盖掉一份写了很久的正文代价太大。
    """
    try:
        name = stores.prompts().save(
            payload.category,
            payload.name,
            payload.content,
            create_only=True,
        )
    except ValueError as exc:
        # 名字不合法 / 类别不存在 / 重名，文案都是给用户看的，原样转 400
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"category": payload.category, "name": name}


@router.put("/prompts/{category}/{name}")
def update_prompt(category: str, name: str, payload: PromptContentPayload) -> dict:
    """覆盖一条提示词的正文。

    路径上的名字就是目标文件，不改名 —— 名字是提示词组里的引用标识，
    改掉它会让引用静默失效（见 `PromptContentPayload`）。
    """
    try:
        stores.prompts().save(category, name, payload.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"category": category, "name": name}
