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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from quill_agent import __version__, bootstrap, sandbox
from quill_agent.config import get_settings
from server.routes import chat, conversations, memory, models, search, tools, usage

logger = logging.getLogger(__name__)

# 前端构建产物（`make build` 生成）。存在就由后端一起托管 —— 发布形态下只需要
# 一个进程：clone 下来跑 `make api`，浏览器打开 8000 就能用，不必装 Node。
#
# 开发时（Vite dev server 在 5173）它可能不存在，那段托管代码整块跳过，
# 接口不受任何影响。这条路径也是「release 里该不该带 dist」的答案：
# 带上就能一键跑，不带也不影响开发。
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """启动自检：说清「数据放在哪」与「沙箱实际是什么状态」。

    两件都值得在启动时喊一嗓子：

        - **数据根**：首次运行会在这里把默认提示词 / 技能播种过去。用户问
          「我的配置在哪」时，日志里就有答案，不用猜；
        - **沙箱**：「配了沙箱、但这台机器上没有后端」是个危险状态 —— 命令会被
          拒绝，而用户可能以为是命令本身有问题。
    """
    logger.info(bootstrap.startup_note(get_settings()))
    logger.info(sandbox.startup_note())
    yield


app = FastAPI(
    title="quill",
    version=__version__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

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
