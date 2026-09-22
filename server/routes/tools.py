"""工具与技能接口。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from quill_agent import mcp
from quill_agent.config import get_settings
from quill_agent.models import SkillGroup, ToolGroup
from quill_agent.tools import registry
from server import stores
from server.schemas import (
    SkillContentPayload,
    SkillGroupPayload,
    SkillPayload,
    ToolGroupPayload,
)

router = APIRouter(tags=["tools"])


@router.get("/tools")
def list_tools() -> dict:
    """全部工具与现有分类。

    `categories` 单独返回，省得前端自己从工具列表里去重 —— 筛选下拉要用它。
    这里不再有「开关」：给不给某个工具由模式的工具组决定。
    """
    return {
        "tools": [spec.model_dump() for spec in registry.all()],
        "categories": registry.categories(),
    }


# ---------------------------------------------------------------------------
# 工具组
#
# 它是「给模型的工具搭配方案」，是模式的组成部分之一。Agent 循环每轮都用
# 模式解析出的工具名单去取工具（`registry.schemas(only=...)`），
# 所以组一旦被某个模式引用，就直接决定模型能看到哪些工具。
# ---------------------------------------------------------------------------


def _check_tool_names(names: list[str]) -> None:
    """组里的工具名必须是注册表里真实存在的。

    拼错的工具名意味着「这个组引用了一个不存在的东西」—— 模型端的工具
    菜单按组生成时会静默少一个工具，用户还以为它在。宁可保存时就报错。

    **例外：`mcp:<server_id>` 这种引用**。它不是一个工具名，而是「引入整个 MCP
    服务器」—— 展开发生在 `agent.resolve_mode` 里（见 `_expand_mcp_refs`），
    到不了这张注册表。这里放行，但要确认那个服务器**确实存在**，否则就是同一个
    「引用了一个不存在的东西」的问题，只不过换了个对象。
    """
    known = {spec.name for spec in registry.all()}
    servers = {server.id for server in stores.mcp_servers().list()}

    unknown: list[str] = []
    for name in names:
        if name.startswith(mcp.MCP_REF_PREFIX):
            server_id = name[len(mcp.MCP_REF_PREFIX) :]
            if server_id not in servers:
                unknown.append(f"{name}（没有这个 MCP 服务器）")
            continue
        if name not in known:
            unknown.append(name)

    if unknown:
        raise HTTPException(status_code=400, detail=f"未知的工具名：{'、'.join(unknown)}")


def _check_confirm(names: list[str], tools: list[str]) -> None:
    """要求确认的工具必须是组内已有工具的子集。

    「不在场却要求确认」是自相矛盾的配置：那个工具压根不会发给模型，
    这条确认规则永远不会被触发 —— 与其静默失效，不如保存时就报错。
    """
    # `mcp:<id>` 不能出现在这里：它展开成一批**名字不同**的工具，这条规则永远命中不了 ——
    # 与其静默失效，不如要求逐个指定（那批工具在工具列表里是可见、可勾的）
    refs = [name for name in names if name.startswith(mcp.MCP_REF_PREFIX)]
    if refs:
        raise HTTPException(
            status_code=400,
            detail="「需要确认」只能逐个指定工具，不能引用整个 MCP 服务器 —— 请从工具列表里挑。",
        )

    stray = [name for name in names if name not in set(tools)]
    if stray:
        raise HTTPException(
            status_code=400,
            detail=f"这些工具不在组里，无法要求确认：{'、'.join(stray)}",
        )


@router.get("/tool-groups")
def list_tool_groups() -> dict:
    """全部工具组。"""
    return {"groups": [item.model_dump() for item in stores.tool_groups().list()]}


@router.post("/tool-groups")
def create_tool_group(payload: ToolGroupPayload) -> dict:
    """新建一个工具组。"""
    _check_tool_names(payload.tools)
    _check_confirm(payload.confirm, payload.tools)

    try:
        item = stores.tool_groups().add(
            name=payload.name,
            description=payload.description,
            tools=payload.tools,
            confirm=payload.confirm,
        )
    except ValueError as exc:
        # 组名重复。文案是给用户看的，转成 400 而不是让校验异常漏成 500
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return item.model_dump()


@router.put("/tool-groups/{group_id}")
def update_tool_group(group_id: str, payload: ToolGroupPayload) -> dict:
    """修改一个工具组；id 不存在时 404。"""
    store = stores.tool_groups()
    existing = store.get(group_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="工具组不存在。")

    _check_tool_names(payload.tools)
    _check_confirm(payload.confirm, payload.tools)

    try:
        item = ToolGroup(
            id=group_id,
            name=payload.name,
            description=payload.description,
            tools=payload.tools,
            confirm=payload.confirm,
            # 出厂标记沿用已有的那个：请求体里没有这个字段，直接构造会把它重置成
            # False —— 一个内置工具组改一次描述，就悄悄变成可删除的了
            builtin=existing.builtin,
        )
        store.update(item)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return item.model_dump()


@router.delete("/tool-groups/{group_id}")
def delete_tool_group(group_id: str) -> dict[str, bool]:
    """删除一个工具组；id 不存在时静默成功（删除是幂等的）。"""
    try:
        stores.tool_groups().remove(group_id)
    except ValueError as exc:
        # 出厂内置的工具组（见 defaults.py）
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"removed": True}


# ---------------------------------------------------------------------------
# 技能组
#
# 与工具组完全同构：搭配方案 + 唯一组名 + 名字必须真实存在。
# 差别只有校验依据 —— 工具查注册表，技能查 skills/ 目录。
# ---------------------------------------------------------------------------


def _check_skill_names(names: list[str]) -> None:
    """组里的技能名必须是 skills/ 目录下真实存在的。

    技能是用户靠「建目录 + 放 SKILL.md」维护的，目录被删掉后组里就会留下
    失效引用 —— 模型端的技能清单会静默少一项，用户还以为它在。
    """
    library = stores.skills()
    unknown = [name for name in names if not library.exists(name)]
    if unknown:
        raise HTTPException(status_code=400, detail=f"未知的技能名：{'、'.join(unknown)}")


@router.get("/skill-groups")
def list_skill_groups() -> dict:
    """全部技能组。"""
    return {"groups": [item.model_dump() for item in stores.skill_groups().list()]}


@router.post("/skill-groups")
def create_skill_group(payload: SkillGroupPayload) -> dict:
    """新建一个技能组。"""
    _check_skill_names(payload.skills)

    try:
        item = stores.skill_groups().add(
            name=payload.name,
            description=payload.description,
            skills=payload.skills,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return item.model_dump()


@router.put("/skill-groups/{group_id}")
def update_skill_group(group_id: str, payload: SkillGroupPayload) -> dict:
    """修改一个技能组；id 不存在时 404。"""
    store = stores.skill_groups()
    existing = store.get(group_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="技能组不存在。")

    _check_skill_names(payload.skills)

    try:
        item = SkillGroup(
            id=group_id,
            name=payload.name,
            description=payload.description,
            skills=payload.skills,
            # 理由同 `update_tool_group`：请求体里没有出厂标记，得从已有条目带过来
            builtin=existing.builtin,
        )
        store.update(item)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return item.model_dump()


@router.delete("/skill-groups/{group_id}")
def delete_skill_group(group_id: str) -> dict[str, bool]:
    """删除一个技能组；id 不存在时静默成功。"""
    try:
        stores.skill_groups().remove(group_id)
    except ValueError as exc:
        # 出厂内置的技能组（见 defaults.py）
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"removed": True}


@router.get("/skills")
def list_skills() -> dict:
    """全部技能：只给元信息（名字 + 适用场景），不返回正文。

    正文可能上千字，列表页用不上，要看详情再单独取。
    这里不再有「开关」：给不给某个技能由模式的技能组决定。
    """
    library = stores.skills()
    items = [
        {"name": meta.name, "description": meta.description}
        for meta in library.list_meta()
    ]
    return {"skills": items, "dir": str(get_settings().skills_dir)}


@router.get("/skills/{name}")
def read_skill(name: str) -> dict:
    """某个技能的使用场景与正文（正文不含元信息块）。

    两样一起给：编辑器打开的就是「使用场景 + 正文」两个框，分开取还得多一次请求，
    也容易和列表里的那份对不上。
    """
    library = stores.skills()
    content = library.read(name)
    if content is None:
        raise HTTPException(status_code=404, detail=f"技能不存在：{name}")

    meta = library.meta(name)
    return {"name": name, "description": meta.description if meta else "", "content": content}


@router.post("/skills")
def create_skill(payload: SkillPayload) -> dict:
    """新建一个技能（一个目录 + 一个 SKILL.md）。

    同名技能已存在时报 400 而不是覆盖：技能名是技能组里的引用标识。
    """
    try:
        name = stores.skills().save(
            payload.name,
            payload.description,
            payload.content,
            create_only=True,
        )
    except ValueError as exc:
        # 名字不合法 / 重名，文案都是给用户看的
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"name": name}


@router.put("/skills/{name}")
def update_skill(name: str, payload: SkillContentPayload) -> dict:
    """覆盖一个技能的使用场景与正文。

    只写 SKILL.md，名字是目录名、不可改 —— 它是技能组里的引用标识。
    """
    library = stores.skills()
    if not library.exists(name):
        raise HTTPException(status_code=404, detail=f"技能不存在：{name}")

    try:
        library.save(name, payload.description, payload.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"name": name}
