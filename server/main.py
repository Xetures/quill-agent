"""FastAPI 应用：把业务层暴露成 REST + SSE 接口。

启动：uv run uvicorn server.main:app --reload --port 8000

分层很简单：界面与业务之间只隔一层 HTTP。

    web/ (Vue)  ──HTTP/SSE──>  server/ (FastAPI)  ──>  src/quill_agent/ (业务层)

业务层一行都没改 —— 它本来就不知道界面长什么样。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from quill_agent import __version__, bootstrap, mcp, sandbox
from quill_agent.config import get_settings
from quill_agent.tools.base import registry
from server import stores
from server.auth import TOKEN_COOKIE, TOKEN_QUERY, AuthConfig, supplied_token, unauthorized
from server.routes import chat, conversations, memory, models, search, tools, usage
from server.routes import mcp as mcp_routes

# `sandbox` 这个名字上面已经给了 `quill_agent.sandbox`（启动自检要用它，
# 见 `sandbox.startup_note()`）。并进上一行会把它盖掉 —— 而且只在启动自检那一刻
# 才炸成 AttributeError，测试跑不到那里。
from server.routes import sandbox as sandbox_routes

logger = logging.getLogger(__name__)


def _auth_config() -> AuthConfig:
    import os

    return AuthConfig(
        host=os.environ.get("QUILL_AUTH_HOST", "127.0.0.1"),
        token=os.environ.get("QUILL_ACCESS_TOKEN", ""),
    )


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        config = _auth_config()
        if request.url.path.startswith("/api/") or request.url.path == "/api":
            if not config.valid(supplied_token(request)):
                return unauthorized()
            return await call_next(request)

        response = await call_next(request)
        token = request.query_params.get(TOKEN_QUERY)
        if token and config.valid(token):
            response = RedirectResponse(
                url=str(request.url.remove_query_params(TOKEN_QUERY)), status_code=303
            )
            response.set_cookie(
                TOKEN_COOKIE,
                token,
                httponly=True,
                samesite="lax",
                secure=request.url.scheme == "https",
            )
        return response

# 前端构建产物（`make build` 生成）。存在就由后端一起托管 —— 发布形态下只需要
# 一个进程：clone 下来跑 `make api`，浏览器打开 8000 就能用，不必装 Node。
#
# 开发时（Vite dev server 在 5173）它可能不存在，那段托管代码整块跳过，
# 接口不受任何影响。这条路径也是「release 里该不该带 dist」的答案：
# 带上就能一键跑，不带也不影响开发。
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def _start_mcp_servers() -> None:
    """按配置连接 MCP 服务器，并把它们的工具挂进注册表。

    **连不上只是那一个不可用**，不影响应用启动 —— 一个第三方服务器的网络问题，
    不该让整个应用打不开。失败原因留在管理器里，界面上能看到。

    这一步是**同步**的：MCP 客户端要连上才知道有哪些工具，而「有哪些工具」会影响
    界面（工具组里能勾什么）。所以宁可启动多等一会儿，也不要在工具列表上空着。
    多个服务器之间是并发连接的，等待时间不是简单叠加。
    """
    servers = stores.mcp_servers().list()
    if not servers:
        return

    mcp.manager.start(servers)
    names = mcp.register_tools(registry)
    connected = sum(1 for item in mcp.manager.all() if item.connected)
    print(f"MCP：{connected}/{len(servers)} 个服务器已连接，注册了 {len(names)} 个工具", flush=True)

    for item in mcp.manager.all():
        if not item.connected:
            print(f"MCP：{item.server.name} 连接失败 —— {item.error}", flush=True)


def _prune_dangling_prompt_refs() -> None:
    """清掉提示词组里指向**已删除**提示词的 id。

    现在的删除会级联（见 `routes.models.delete_prompt`），但更早删掉的那批没做过 ——
    那些 id 会一直躺在组里：界面显示不出名字，保存时又被原样写回，用户删不掉。
    启动自检里跑一次，老数据就自己好了，不用人去动 JSON。

    无变化时不动盘；真动了就在日志里点名，那是「我明明删了，怎么组里还有」的答案。
    """
    valid = {item.id for item in stores.prompts().list_items()}
    touched = stores.prompt_groups().prune_missing(valid)
    if touched:
        # 用 print 而不是 logger：这个进程没配 logging（uvicorn 只管自己那套），
        # logger.info 是不显示的。启动自检本来就都走 print —— 见 cli.serve
        print(f"已清理提示词组中的悬空引用：{'、'.join(touched)}", flush=True)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """启动自检：说清「数据放在哪」与「沙箱实际是什么状态」。

    两件都值得在启动时喊一嗓子：

        - **数据根**：首次运行会在这里把默认提示词 / 技能播种过去。用户问
          「我的配置在哪」时，日志里就有答案，不用猜；
        - **沙箱**：「配了沙箱、但这台机器上没有后端」是个危险状态 —— 命令会被
          拒绝，而用户可能以为是命令本身有问题。
    """
    # 用 print 而不是 logger：这个进程里 root logger 是 WARNING 级别，`logger.info`
    # 会被直接挡掉 —— 而「数据根在哪、这次补了什么、迁了什么」是排障第一步要看的东西。
    # 启动自检必须**跑在这里**（直接 `uvicorn server.main:app` 时没有别的入口来做
    # 播种和迁移），但输出只有这一处，见 cli.serve 里对应删掉的那次
    settings = get_settings()
    print(bootstrap.startup_note(settings), flush=True)
    # 传生效的工作目录而不是让它自己读 cwd：启动目录可能被回退过（见
    # `config._default_work_dir`），那时按 cwd 报出来的沙箱边界是错的
    print(sandbox.startup_note(settings.work_dir), flush=True)
    _prune_dangling_prompt_refs()
    _start_mcp_servers()
    yield
    # 退出时断开：stdio 传输起的那些子进程要跟着一起收掉，否则会变成孤儿进程
    mcp.manager.stop()


app = FastAPI(
    title="quill",
    version=__version__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(AuthMiddleware)

# 开发时前端跑在 Vite 的 5173 端口，与后端不同源，必须显式放行；
# 生产环境下前端会被构建成静态文件由同一个服务托管，那时是同源，走不到这里
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(conversations.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(models.router, prefix="/api")
app.include_router(tools.router, prefix="/api")
app.include_router(memory.router, prefix="/api")
app.include_router(usage.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(sandbox_routes.router, prefix="/api")
app.include_router(mcp_routes.router, prefix="/api")


@app.get("/api/health")
def health() -> dict[str, str]:
    """探活接口：给启动脚本和前端确认后端是否就绪。"""
    return {"status": "ok", "version": __version__}


# ---------------------------------------------------------------------------
# 前端托管（发布形态）
#
# 必须注册在**所有 API 路由之后**：FastAPI 按注册顺序匹配，兜底路由放前面会把
# `/api/...` 一起吞掉。
# ---------------------------------------------------------------------------
if (WEB_DIST / "index.html").is_file():

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str) -> FileResponse:
        """托管前端：静态文件按原样给，其余交给前端路由（history 模式）。

        `/api/*` 不在这里兜底 —— 一个不存在的接口应该老老实实 404，
        而不是返回一份 HTML（那会让调用方拿到「200 + 一坨网页」）。
        """
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="接口不存在")

        candidate = (WEB_DIST / full_path).resolve()

        # 路径穿越防护：解析之后必须还在 dist 目录里
        if full_path and candidate.is_file() and candidate.is_relative_to(WEB_DIST):
            return FileResponse(candidate)

        # 其余（`/`、`/chat`、`/models` 这些前端路由）都返回入口页面，
        # 由前端自己决定渲染哪一页
        return FileResponse(WEB_DIST / "index.html")

else:
    logger.info(
        "没有找到前端产物（%s），本次只提供接口。"
        "需要完整界面时先执行 `make build`，开发时也可以用 `make web` 起 Vite。",
        WEB_DIST,
    )
