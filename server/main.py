"""FastAPI 应用：把业务层暴露成 REST + SSE 接口。

启动：uv run uvicorn server.main:app --reload --port 8000

分层和 Streamlit 版一模一样，只是把「界面」换成了 HTTP：

    web/ (Vue)  ──HTTP/SSE──>  server/ (FastAPI)  ──>  src/quill_agent/ (业务层)

业务层一行都没改 —— 它本来就不知道界面长什么样。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from quill_agent import __version__
from server.routes import chat, conversations, memory, models, tools

app = FastAPI(
    title="quill",
    version=__version__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
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


@app.get("/api/health")
def health() -> dict[str, str]:
    """探活接口：给启动脚本和前端确认后端是否就绪。"""
    return {"status": "ok", "version": __version__}
