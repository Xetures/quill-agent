"""MCP 服务器管理：配置的增删改查、连接状态、重连、试连。

**为什么「试连」要单独有一个端点。** 用户填的是一行命令加一串参数，光看配置判断不了它
能不能跑起来（命令不存在、包名写错、需要网络、需要 token）。让他在保存前就能点一下、
看到真实的工具列表，比保存之后对着一个空的工具列表猜好得多。

**试连用的是一次性连接**，用完立刻断开，不进管理器 —— 否则每点一次「测试」就多一个
常驻进程。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from quill_agent import mcp
from quill_agent.mcp import McpConnection
from quill_agent.models import McpServer
from quill_agent.tools.base import registry
from server import stores
from server.auth import REDACTED, redact_mapping
from server.schemas import McpServerPayload

router = APIRouter(tags=["mcp"])


def _to_model(payload: McpServerPayload, server_id: str) -> McpServer:
    return McpServer(
        id=server_id,
        name=payload.name,
        description=payload.description,
        transport=payload.transport,  # type: ignore[arg-type]  # 由 pydantic 校验
        command=payload.command,
        args=list(payload.args),
        env=dict(payload.env),
        url=payload.url,
        enabled=payload.enabled,
        timeout=payload.timeout,
    )


def _reload() -> dict:
    """按当前配置重连全部服务器，并重新注册工具。

    改了配置（增删改、启停）之后必须走一次 —— 连接和注册表里的工具都是**上一次**的状态。
    """
    mcp.manager.stop()
    mcp.manager.start(stores.mcp_servers().list())
    tools = mcp.register_tools(registry)
    return {"servers": mcp.manager.status(), "tools": tools}


@router.get("/mcp/servers")
def list_servers() -> dict:
    """列出全部配置，以及它们此刻的连接状态（连上没、有哪些工具、失败原因）。"""
    status = {item["id"]: item for item in mcp.manager.status()}
    servers = []
    for config in stores.mcp_servers().list():
        live = status.get(config.id)
        servers.append(
            {
                **config.model_dump(exclude={"env"}),
                "env": redact_mapping(config.env),
                "env_configured": bool(config.env),
                # 没连上的（停用、或管理器还没启动）也给一份空状态，前端不用判空
                "connected": bool(live and live["connected"]),
                "error": live["error"] if live else "",
                "tools": live["tools"] if live else [],
            }
        )
    return {"servers": servers}


@router.post("/mcp/servers")
def add_server(payload: McpServerPayload) -> dict:
    """新增一个服务器，并立刻按新配置重连。"""
    try:
        item = stores.mcp_servers().add(
            name=payload.name,
            description=payload.description,
            transport=payload.transport,
            command=payload.command,
            args=payload.args,
            env=payload.env,
            url=payload.url,
            timeout=payload.timeout,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    data = item.model_dump(exclude={"env"})
    data["env"] = redact_mapping(item.env)
    data["env_configured"] = bool(item.env)
    return {"server": data, **_reload()}


@router.put("/mcp/servers/{server_id}")
def update_server(server_id: str, payload: McpServerPayload) -> dict:
    """按 id 覆盖更新，并立刻重连。"""
    current = stores.mcp_servers().get(server_id)
    if current is None:
        raise HTTPException(status_code=404, detail=f"没有这个 MCP 服务器：{server_id}")

    effective_env = {
        key: current.env.get(key, value) if value == REDACTED else value
        for key, value in payload.env.items()
    }
    updated = _to_model(payload, server_id).model_copy(update={"env": effective_env})
    try:
        stores.mcp_servers().update(updated)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    data = updated.model_dump(exclude={"env"})
    data["env"] = redact_mapping(updated.env)
    data["env_configured"] = bool(updated.env)
    return {"server": data, **_reload()}


@router.delete("/mcp/servers/{server_id}")
def delete_server(server_id: str) -> dict:
    """删除一个服务器，并立刻重连（它的工具会随之从注册表里摘掉）。"""
    stores.mcp_servers().remove(server_id)
    return _reload()


@router.post("/mcp/servers/{server_id}/toggle")
def toggle_server(server_id: str) -> dict:
    """停用 / 启用。停用只是不连接，配置留着 —— 比删掉再重建省事。"""
    current = stores.mcp_servers().get(server_id)
    if current is None:
        raise HTTPException(status_code=404, detail=f"没有这个 MCP 服务器：{server_id}")

    stores.mcp_servers().update(current.model_copy(update={"enabled": not current.enabled}))
    return _reload()


@router.post("/mcp/reload")
def reload_servers() -> dict:
    """按当前配置重连全部。用户在界面外改了配置、或者想重试失败的那些时用。"""
    return _reload()


@router.post("/mcp/test")
def test_server(payload: McpServerPayload) -> dict:
    """试连一个**尚未保存**的配置，返回它暴露了哪些工具。

    这是「保存前先看看它到底行不行」的那一步。连接是一次性的：连上、取工具列表、立刻断开，
    不留在管理器里 —— 每点一次测试就多一个常驻进程是不可接受的。
    """
    # id 只是个占位：试连不落盘，也不会进管理器
    probe = McpConnection(_to_model(payload, "__probe__"))
    probe.start()
    try:
        if not probe.connected:
            return {"ok": False, "error": probe.error, "tools": []}
        return {
            "ok": True,
            "error": "",
            "tools": [
                {
                    "name": tool.local_name,
                    "remote": tool.remote_name,
                    "description": tool.description,
                }
                for tool in probe.tools
            ],
        }
    finally:
        probe.stop()
