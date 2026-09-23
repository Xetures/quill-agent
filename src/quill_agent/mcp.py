"""MCP 客户端：连接外部服务器，把它们的工具变成可调用的本地函数。

**为什么要一个后台事件循环线程。** MCP 的 Python SDK 是 async 的，而工具调用那条链
（`registry.execute` → `agent._execute`）是**同步**的，跑在 agent 的线程里。stdio 传输
还要求整个会话活在同一个事件循环里 —— 所以每个服务器配一个长期存活的后台线程跑
asyncio，同步侧用 `run_coroutine_threadsafe` 提交任务并等结果。

**为什么不做自动重连。** 静默自愈会让工具「时有时无」，而间歇性故障是最难排查的一类
问题。断开就标记为不可用、把原因留着，让用户手动重连 —— 这样「为什么用不了」始终有
一个明确的答案。

**为什么服务器是"整体引入"的。** 一个服务器暴露哪些工具由它自己决定，而且它升级之后
会变。让用户逐个勾选的话，服务端多一个新工具时那个名单不会自动跟上，新工具静默不可用
而没人知道为什么。所以工具组里引用的是 `mcp:<server_id>`，展开成它**当前**的全部工具
（见 `agent.resolve_mode`）。

**调用是阻塞的。** 一次 MCP 调用会占住 agent 线程直到返回或超时（和 `run_command` 一样）。
用户按「停止」不会中断它 —— 超时是唯一的出路，所以 `timeout` 不能设得太大。
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from quill_agent import sandbox
from quill_agent.models import McpServer
from quill_agent.tools.base import ToolKind, ToolRegistry, ToolSpec
from quill_agent.tools.files import current_work_dir

logger = logging.getLogger(__name__)

# 连接阶段的超时。和调用超时分开口径：连不上要快速失败（免得应用启动卡住），
# 而调用要给足时间（有些工具本身就慢）。
CONNECT_TIMEOUT = 20.0

#: 工具组里引用「整个 MCP 服务器」的前缀，形如 `mcp:<server_id>`。
#:
#: 放在这里而不是 `agent` 里：路由层要拿它校验工具组，而 `agent` 是个重模块 ——
#: 让 HTTP 层为了一个常量去 import 整个 Agent 循环没有道理。
MCP_REF_PREFIX = "mcp:"

# 一次列出来的工具数量上限。真遇到一个暴露上百个工具的服务器，全量塞进上下文会把
# 小窗口模型直接撑爆 —— 这里截断并如实说明，比默默发出去好。
MAX_TOOLS = 64

# 工具名总长度上限。模型 API 对 function name 有长度限制（通常 64），
# 前缀 `mcp__<服务器>__` 会占掉一截，所以在这里先按最长的可能截。
MAX_NAME_LENGTH = 64


@dataclass
class McpTool:
    """一个 MCP 工具，已经准备好变成一个本地工具。"""

    #: 服务器上的原名。调用时要用回它 —— 发给模型的是清洗后的名字（见 `local_name`）。
    remote_name: str
    #: 给模型看的名字：`mcp__<服务器>__<工具>`，字符已清洗到 API 允许的范围。
    local_name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


def _slug(text: str) -> str:
    """把任意文本清洗成模型 API 允许的工具名字符。

    MCP 对工具名很宽松（点、中文、空格都可能出现），而 function name 只允许
    `[A-Za-z0-9_-]`。不清洗的话，一个带点的工具名会让**整个请求**失败。
    """
    allowed = "_-"
    cleaned = "".join(
        char if (char.isascii() and (char.isalnum() or char in allowed)) else "_" for char in text
    )
    return cleaned.strip("_") or "tool"


def _render(result: Any) -> str:
    """把 MCP 的调用结果转成一段文本。

    工具契约要求返回 `str`，而 MCP 返回的是内容块列表（可能混着图片、资源）。
    这里只取文本，其余类型如实说明「被丢掉了」—— 假装它们不存在，
    模型会以为工具没返回东西。
    """
    parts: list[str] = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
        else:
            kind = getattr(item, "type", "未知")
            parts.append(f"[{kind} 类型的内容，这里只转述文本]")

    body = "\n".join(parts) or "（没有返回内容）"
    # is_error 也要转达：调用「成功」但工具内部报错，是两回事
    return f"（该工具报告了错误）\n{body}" if getattr(result, "is_error", False) else body


class McpConnection:
    """一个 MCP 服务器的连接。自带一个后台事件循环线程。"""

    def __init__(self, server: McpServer) -> None:
        self.server = server
        self.tools: list[McpTool] = []
        #: 连不上或被断开时的原因；空串表示正常。
        self.error: str = ""

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: ClientSession | None = None
        self._stop: asyncio.Event | None = None
        #: 初始化完成（成功或失败）时置位，`start` 靠它等结果。
        self._ready = threading.Event()

    # -- 同步侧 ---------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._session is not None and not self.error

    def start(self) -> None:
        """连上并列出工具；失败只记录原因，不抛。

        **失败不抛是刻意的**：一个 MCP 服务器连不上，不该让整个应用起不来。
        """
        if self._thread is not None:
            return

        self._thread = threading.Thread(
            target=self._thread_main, name=f"mcp-{self.server.name}", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=CONNECT_TIMEOUT + 5)

        if not self._ready.is_set():
            self.error = f"连接超时（{CONNECT_TIMEOUT:.0f} 秒）"

    def call(self, tool: str, arguments: dict[str, Any]) -> str:
        """同步调用一个工具。任何失败都转成文本（工具契约如此）。"""
        if self._loop is None or self._session is None:
            return f"MCP 服务器「{self.server.name}」当前不可用：{self.error or '尚未连接'}"

        future = asyncio.run_coroutine_threadsafe(self._call(tool, arguments), self._loop)
        try:
            return future.result(timeout=self.server.timeout)
        except FutureTimeoutError:
            # 取消这一次等待。服务端可能还在跑那个工具 —— 这一点无法从这里收回，
            # 所以 timeout 不该设得太大（见模块开头）
            future.cancel()
            return f"调用 {tool} 超过 {self.server.timeout:.0f} 秒没有返回，已放弃等待。"
        except Exception as exc:  # noqa: BLE001 —— 任何异常都要变成文本
            return f"MCP 调用失败：{exc}"

    def stop(self) -> None:
        """断开并等后台线程收尾（它会连带清理 stdio 子进程）。"""
        if self._loop is not None and self._stop is not None:
            try:
                self._loop.call_soon_threadsafe(self._stop.set)
            except RuntimeError:
                pass  # loop 已经关了

        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

        self._session = None
        self._loop = None
        self._stop = None
        self.tools = []

    # -- 异步侧（都在那个后台线程里）--------------------------------------------

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._serve())
        except Exception as exc:  # noqa: BLE001
            self.error = f"{exc.__class__.__name__}: {exc}"
            logger.exception("MCP 服务器 %s 的后台线程异常退出", self.server.name)
        finally:
            try:
                loop.close()
            finally:
                self._ready.set()  # 兜底：无论怎么退出都要放行等待方

    async def _serve(self) -> None:
        self._stop = asyncio.Event()

        try:
            async with self._open() as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.tools = await self._list_tools(session)
                    self._session = session
                    self.error = ""
                    self._ready.set()
                    # 一直挂在这里，直到 `stop()` 把事件置位 —— 会话必须保持存活
                    await self._stop.wait()
        except Exception as exc:  # noqa: BLE001
            self.error = f"{exc.__class__.__name__}: {exc}"
            logger.warning("MCP 服务器 %s 连接失败：%s", self.server.name, exc)
        finally:
            self._ready.set()

    def _open(self):
        """按传输方式打开连接。"""
        if self.server.transport == "http":
            return streamable_http_client(self.server.url)

        work_dir = current_work_dir()
        policy = sandbox.policy_for(work_dir)
        argv = sandbox.build_process_argv(
            [self.server.command, *self.server.args], policy
        )
        # MCP 是可执行的外部扩展，默认只给启动所需的环境，避免把 API keys、
        # 云凭证和其它宿主秘密无意传给第三方进程。服务器配置中的 env 是显式授权。
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "TEMP", "TMP"}
            or key == "LANG"
            or key.startswith("LC_")
        }
        env.update(self.server.env)
        params = StdioServerParameters(
            command=argv[0],
            args=argv[1:],
            cwd=work_dir,
            env=env,
        )
        return stdio_client(params)

    async def _list_tools(self, session: ClientSession) -> list[McpTool]:
        listed = await session.list_tools()
        tools: list[McpTool] = []

        for item in listed.tools[:MAX_TOOLS]:
            local = f"mcp__{_slug(self.server.name)}__{_slug(item.name)}"
            tools.append(
                McpTool(
                    remote_name=item.name,
                    local_name=local[:MAX_NAME_LENGTH],
                    # 描述为空时给一句兜底的：模型没有描述就无从判断该不该用，
                    # 而空描述在 API 里也是不合法的
                    # 描述为空时给一句兜底的：模型没有描述就无从判断该不该用，
                    # 而空描述在 API 里也不合法
                    description=(
                        item.description
                        or f"由 MCP 服务器「{self.server.name}」提供的 {item.name}。"
                    ),
                    parameters=item.input_schema or {"type": "object", "properties": {}},
                )
            )

        return tools

    async def _call(self, tool: str, arguments: dict[str, Any]) -> str:
        assert self._session is not None  # 由 call() 保证
        result = await self._session.call_tool(tool, arguments)
        return _render(result)


class McpManager:
    """所有 MCP 服务器的连接总控。

    生命周期跟着应用走：启动时连、退出时断。**不自动重连**（见模块开头）。
    """

    def __init__(self) -> None:
        self._connections: dict[str, McpConnection] = {}

    def start(self, servers: list[McpServer]) -> None:
        """并发连接所有启用的服务器。一个失败不影响其他，也不影响应用启动。"""
        active = [server for server in servers if server.enabled]
        if not active:
            return

        connections = {server.id: McpConnection(server) for server in active}
        with ThreadPoolExecutor(max_workers=len(active)) as pool:
            # 并发：每个 `start` 都要等自己的连接（最长 CONNECT_TIMEOUT）——
            # 串行的话，N 个连不上的服务器就是 N 倍等待
            list(pool.map(lambda item: item.start(), connections.values()))

        self._connections = connections

    def stop(self) -> None:
        """断开全部连接。应用退出时调用。"""
        for connection in self._connections.values():
            connection.stop()
        self._connections.clear()

    def get(self, server_id: str) -> McpConnection | None:
        return self._connections.get(server_id)

    def all(self) -> list[McpConnection]:
        return list(self._connections.values())

    def status(self) -> list[dict[str, Any]]:
        """给界面看的状态。"""
        out = []
        for connection in self._connections.values():
            out.append(
                {
                    "id": connection.server.id,
                    "name": connection.server.name,
                    "connected": connection.connected,
                    "error": connection.error,
                    "tools": [tool.local_name for tool in connection.tools],
                }
            )
        return out


def _make_caller(connection: McpConnection, remote_name: str) -> Callable[..., str]:
    """给注册表用的调用函数。

    注册表的契约是「关键字参数进去、文本出来」，而 MCP 要的是 (原名, dict) ——
    包一层做这个转换，顺便把「用原名调用」这件事锁在这里：发给模型的是清洗后的名字，
    回到服务器必须是原名。
    """

    def call(**kwargs: Any) -> str:
        return connection.call(remote_name, kwargs)

    return call


def register_tools(target: ToolRegistry) -> list[str]:
    """把已连上的 MCP 工具注册进注册表，返回注册了哪些工具名。

    **每次重连之后都要再调一遍**：服务器上的工具集可能变了，所以这里是「摘掉旧的、
    挂上新的」，而不是只做新增。

    名字冲突时**跳过并记日志**，不覆盖：前缀已经能避免绝大多数撞车，真撞上说明用户把
    服务器命名成了某个内置工具的名字 —— 那时候悄悄覆盖掉一个内置工具，比少几个 MCP
    工具危险得多。
    """
    registered: list[str] = []
    builtin = {spec.name for spec in target.all() if spec.kind is ToolKind.LOCAL}

    live = {connection.server.id for connection in manager.all() if connection.connected}

    # **先摘掉已经不在管理器里的来源**（服务器被删掉、被停用，或者管理器已停止）。
    # 少了这一步，那些工具会永远留在注册表里：占着上下文，模型也还会去调，
    # 然后每次都收到「不可用」—— 而用户明明已经把那个服务器删了。
    for source in target.sources():
        if source not in live:
            target.unregister_source(source)

    for connection in manager.all():
        if not connection.connected:
            # 连不上的，把它之前注册过的摘掉 —— 否则界面上的工具列表会停在上一次的状态
            target.unregister_source(connection.server.id)
            continue

        specs: list[tuple[ToolSpec, Callable[..., str]]] = []
        for tool in connection.tools:
            if tool.local_name in builtin:
                logger.warning("MCP 工具名与内置工具冲突，已跳过：%s", tool.local_name)
                continue

            specs.append(
                (
                    ToolSpec(
                        name=tool.local_name,
                        description=tool.description,
                        # 分类带服务器名：界面上按分类筛选时能一眼看出这批工具来自谁。
                        # 写成 `mcp:<服务器名>` 而不是中文文案 —— 分类是界面文案，
                        # 得跟着语言走（见 tools/base.py 里 ToolSpec.category 的说明）
                        category=f"mcp:{connection.server.name}",
                        parameters=tool.parameters,
                        kind=ToolKind.MCP,
                    ),
                    _make_caller(connection, tool.remote_name),
                )
            )

        accepted = target.register_external(specs, source=connection.server.id)
        rejected = {spec.name for spec, _ in specs} - set(accepted)
        for name in sorted(rejected):
            logger.warning("MCP 工具名与其他来源冲突，已跳过：%s", name)
        registered.extend(accepted)

    return registered


#: 全局管理器。应用启动时填，退出时清空。
manager = McpManager()
