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
    protocol_options,
)
from quill_agent.prompts import PROMPT_CATEGORIES
from server import stores
from server.schemas import (
    CatalogLookupPayload,
    ModelPayload,
    ModePayload,
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


@router.get("/protocols")
def list_protocols() -> dict:
    """可选的接口协议清单（含各自的默认地址与 Key 提示）。

    由后端下发而不是前端硬编码一份：加协议只需改业务层的 `Protocol` 枚举，
    界面自动多出一项。前端拿到 `default_base_url` 后可以在用户选协议时
    顺手把地址填好 —— Ollama 的 `http://localhost:11434/v1` 没人愿意背。
    """
    return {"protocols": protocol_options()}


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
        prompts=payload.prompts,
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
#
# 一条提示词 = 一个 `.md` 文件，**文件名是稳定 id，名字与分类在文件头的元信息里**
# （见 `quill_agent.prompts`）。所以这里只认 id：URL 里没有中文、没有分类，
# 分类只是元信息里的一个字段，名字改起来不影响任何引用。
# ---------------------------------------------------------------------------
@router.get("/prompts")
def list_prompts() -> dict:
    """分类清单 + 全部提示词的元信息。

    只给元信息、不给正文：列表与下拉只需要这些，而正文可能很长。
    """
    library = stores.prompts()
    return {
        "categories": list(PROMPT_CATEGORIES),
        "items": [
            {"id": item.id, "name": item.name, "category": item.category}
            for item in library.list_items()
        ],
    }


@router.post("/prompts")
def create_prompt(payload: PromptPayload) -> dict:
    """新建一条提示词；id 由后端生成并返回。

    不再有「同名就报错」这回事：名字不再是标识，两条提示词重名完全合法
    （表格里并排显示，用户自己看得见）。真正的标识是返回的 id。
    """
    try:
        item = stores.prompts().create(
            name=payload.name, category=payload.category, content=payload.content
        )
    except ValueError as exc:
        # 名字为空 / 超长、分类不在六类里 —— 文案都是给用户看的，原样转 400
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"id": item.id, "name": item.name, "category": item.category}


@router.get("/prompts/{prompt_id}")
def read_prompt(prompt_id: str) -> dict:
    """某条提示词的元信息与正文。"""
    try:
        item = stores.prompts().detail(prompt_id)
    except ValueError as exc:
        # id 格式不合法（它是路径的一环，业务层会把关）
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if item is None:
        raise HTTPException(status_code=404, detail=f"提示词不存在：{prompt_id}")

    return {"id": item.id, "name": item.name, "category": item.category, "content": item.content}


@router.put("/prompts/{prompt_id}")
def update_prompt(prompt_id: str, payload: PromptPayload) -> dict:
    """覆盖一条提示词：**名字与分类都可以改**。

    引用用的是 id，所以改名不会再让提示词组失效 —— 这是换成 id 标识顺带解决的
    （从前名字就是文件名，改个名等于换了个标识，所有引用一起静默失效）。
    """
    try:
        item = stores.prompts().save(
            prompt_id, name=payload.name, category=payload.category, content=payload.content
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"id": item.id, "name": item.name, "category": item.category}


@router.delete("/prompts/{prompt_id}")
def delete_prompt(prompt_id: str) -> dict:
    """删除一条提示词，并把它从所有引用它的提示词组里摘掉。

    **级联是必须的，不是顺手做的**：引用按 id 存，只删正文会在组里留下一个指向空处的
    id —— 界面上它连名字都显示不出来，却每次保存都被原样写回去，用户根本删不掉它
    （见 `PromptGroupStore.drop_prompt`）。

    返回被改动的组名：让界面能说清「顺手动了哪几个组」，而不是让用户自己去猜。
    """
    try:
        removed = stores.prompts().delete(prompt_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    used_by = stores.prompt_groups().drop_prompt(removed)

    return {"removed": True, "used_by": used_by}
