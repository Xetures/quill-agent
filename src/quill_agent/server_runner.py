"""HTTP 服务的启动与停止 —— 命令行与桌面壳共用这一份。

为什么单独一个模块：`quill serve`（前台阻塞）和桌面壳（后台线程跑、窗口关掉再停）
要做的事一模一样 —— 挑端口、校验认证参数、设好环境变量、跑 uvicorn。各写一份必然
漂移，而漂移出来的差异只在**打包之后**才暴露（比如端口顺延少了一个入口，双击启动
那天就变成「打不开」，还没有任何报错）。

两条设计约定：

    - 它**不**导入 fastapi 或任何业务层：交给 uvicorn 的始终是 `server.main:app`
      这个字符串，和 `quill serve` 走的是同一个入口（启动自检、播种、迁移都在
      `server.main` 的 lifespan 里，一处都不重复）；
    - `start_server` **不阻塞**：调用方自己决定等它（CLI 用 `wait`）还是配窗口
      （桌面壳）。阻塞式的那份写法在壳里拿不到端口，只能去猜。
"""

from __future__ import annotations

import os
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import uvicorn

# 默认端口被占时，往后顺延多少个端口
PORT_ATTEMPTS = 20

# 本机监听的几种写法：这些地址上不需要访问令牌（见 server/auth.py）
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def pick_port(host: str, port: int, attempts: int = PORT_ATTEMPTS) -> int:
    """找一个能用的端口：从 `port` 开始往上试。

    为什么不让 uvicorn 自己报错退出：双击启动的用户看不到命令行报错 —— 窗口一闪
    就没了，他只会觉得「打不开」。顺延一个端口并把地址打出来，好得多。

    Raises:
        SystemExit: 连续 attempts 个端口都被占用。
    """
    for candidate in range(port, port + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            # 不加这一句的话，刚被别的进程关掉的端口会因为 TIME_WAIT 被判成「占用」
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, candidate))
            except OSError:
                continue
            return candidate

    raise SystemExit(f"{port} 起连续 {attempts} 个端口都被占用了，用 --port 换一个再试。")


@dataclass
class RunningServer:
    """一个跑起来的服务。`url` 是**可以直接打开**的地址。"""

    url: str
    host: str
    port: int
    _server: uvicorn.Server
    _thread: threading.Thread

    def wait(self) -> None:
        """阻塞到服务结束（命令行入口用这个）。"""
        self._thread.join()

    def stop(self, timeout: float = 10.0) -> None:
        """优雅停止。

        走 uvicorn 自己的退出流程（`should_exit`），而不是杀线程：lifespan 的收尾
        在那里跑 —— MCP 用 stdio 起的那些子进程就靠它收掉（见 `server/main.py`），
        硬杀会留下孤儿进程。
        """
        self._server.should_exit = True
        self._thread.join(timeout=timeout)

    def healthy(self, timeout: float = 1.0) -> bool:
        """服务此刻能不能应答。

        用 `/api/health` 而不是「端口能不能连上」：端口在 uvicorn 完成监听之后就
        通了，而那时 lifespan 的启动自检（播种出厂资源、迁移老数据、连 MCP）可能
        还没跑完 —— 那种状态下打开的页面是空白的。health 是路由层面的应答，
        它通了才说明应用真的起来了。
        """
        try:
            with urllib.request.urlopen(f"{self.url}/api/health", timeout=timeout) as response:
                return response.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def wait_ready(self, timeout: float = 30.0) -> bool:
        """等它真的能应答，返回是否等到。

        轮询而不是 sleep 一个固定秒数：首次启动要做播种、迁移、连 MCP，耗时不确定
        （数据库冷的机器上可能是几百毫秒，也可能是几秒）。猜一个值不是早了
        （用户看到白页）就是晚了（启动被人为拖慢）。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.healthy():
                return True
            if not self._thread.is_alive():
                # 服务自己退了（端口被抢、启动时报错），再等下去也没有意义
                return False
            time.sleep(0.15)
        return False


def start_server(host: str = "127.0.0.1", port: int = 8000, *, token: str = "") -> RunningServer:
    """起服务并**立刻返回**。调用方决定等它（`wait`）还是先配窗口（桌面壳）。

    认证参数在这里校验并写进环境变量：中间件读的正是这两个变量（见
    `server/auth.py` 与 `server/main.py` 的 `_auth_config`）。放在这里，两个入口
    就都拦得住「非本机监听却不给令牌」这件事。
    """
    access_token = token or os.environ.get("QUILL_ACCESS_TOKEN", "")
    if host not in LOOPBACK_HOSTS and not access_token:
        raise SystemExit("非本机监听必须配置访问令牌：使用 --token 或 QUILL_ACCESS_TOKEN。")

    chosen = pick_port(host, port)
    if chosen != port:
        print(f"端口 {port} 被占用，改用 {chosen}。", flush=True)

    os.environ["QUILL_AUTH_HOST"] = host
    if access_token:
        os.environ["QUILL_ACCESS_TOKEN"] = access_token

    # 监听 0.0.0.0 时，浏览器该打开的地址是「本机」那个，而不是字面的 0.0.0.0
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host

    config = uvicorn.Config("server.main:app", host=host, port=chosen, log_level="info")
    server = uvicorn.Server(config)
    # 信号处理只有主线程能装。桌面壳里 uvicorn 跑在后台线程，装着会在启动时抛
    # `ValueError: signal only works in main thread`；而那边停止本来就走
    # `should_exit`（见 stop），不需要信号。让它成为空操作，两种跑法就统一了。
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]

    thread = threading.Thread(target=server.run, name="quill-server", daemon=True)
    thread.start()

    return RunningServer(
        url=f"http://{display_host}:{chosen}",
        host=host,
        port=chosen,
        _server=server,
        _thread=thread,
    )
