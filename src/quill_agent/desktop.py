"""桌面壳：把本地服务装进一个原生窗口（pywebview）。

它就是「浏览器」那一层的替代品 —— 起同一个 uvicorn、打开同一个地址，只是容器换成
了系统 WebView（macOS 是 WKWebView、Windows 是 WebView2、Linux 是 WebKitGTK）。
前端与后端因此**一行都不用改**：那个地址在浏览器里能开，在窗口里也能开。这也意味着
两条路可以并存 —— 调试时照旧用浏览器打开同一个地址。

三个必须处理的东西：

    - **已有实例**：重复双击不该起第二个后端（见 `_find_existing`）；
    - **就绪时机**：窗口要等 `/api/health` 应答了再开，否则用户先看到一片白（见
      `RunningServer.wait_ready`）；
    - **退出收尾**：窗口关掉要带走 uvicorn（`RunningServer.stop`），MCP 用 stdio
      起的子进程在 lifespan 里收 —— 它们不该变成孤儿。

pywebview 是**可选依赖**（`uv sync --extra desktop`）：不做桌面版的人不该为它
多装一层，命令行与浏览器版照旧。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from quill_agent.config import get_settings
from quill_agent.server_runner import PORT_ATTEMPTS, RunningServer, start_server

# 桌面壳固定在本机、固定从默认端口开始找（顺延由 pick_port 负责）
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

# 自动化验收用的钩子：设了就在这么多秒后自动关窗，走完「关窗 → 停服务」整条链路。
# 无人值守时点不了关闭按钮，而这一步恰恰是最容易出错、也最该被验的（残留进程）。
EXIT_AFTER_ENV = "QUILL_DESKTOP_EXIT_AFTER"


def _import_webview() -> Any:
    """拿 pywebview。装不上时说清怎么装，而不是甩一个 ImportError 堆栈。

    双击启动的用户看不到这个堆栈（窗口一闪就没了），所以文案要能直接照着做。
    """
    try:
        import webview
    except ImportError as exc:  # pragma: no cover - 只在缺可选依赖时走到
        raise SystemExit(
            "桌面壳需要 pywebview（可选依赖），先装它：\n"
            "    uv sync --extra desktop\n"
            "不做桌面版的话，用浏览器版即可：\n"
            "    quill serve"
        ) from exc

    return webview


def log_path() -> Path:
    """桌面壳的日志文件位置（数据根下的 `desktop.log`）。"""
    return get_settings().home / "desktop.log"


def _redirect_output_to_log() -> None:
    """把标准输出与标准错误接到日志文件上。

    双击启动时**没有终端**：启动自检（数据根在哪、沙箱什么状态）和所有报错如果不
    落到文件里，就等于什么都没发生 —— 排障时无从下手。日志按追加写，不覆盖上一次。
    """
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # 进程级重定向：这个流要活到进程结束，不能关（也不需要关）
        stream = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        # 连日志都写不了（数据目录不可写）时不要因此起不来：继续跑，输出留在原处
        return


    sys.stdout = stream
    sys.stderr = stream


def _find_existing(host: str, port: int) -> str | None:
    """扫一遍默认端口段，返回已跑着的 Quill 的地址；没有就返回 None。

    为什么值得扫：重复双击（或者用户忘了自己开着）会起第二个后端，两个实例共享同一份
    数据文件 —— 存储层有锁，那只是不让文件写坏，**不是**让人看到两个界面各说各话。
    复用它更省事也更安全：窗口新开一个，服务不新建。

    判据是 `/api/health` 的应答里带 `status: ok` 与 `version`：只按「端口通不通」
    判断的话，会把恰好占用同一个端口的别的程序当成自己人，然后打开一个陌生页面。
    """
    for candidate in range(port, port + PORT_ATTEMPTS):
        url = f"http://{host}:{candidate}"
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=0.3) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            continue

        if isinstance(payload, dict) and payload.get("status") == "ok" and "version" in payload:
            return url

    return None


def _open_window(webview: Any, title: str, url: str) -> None:
    """开窗口并阻塞到它被关掉。"""
    window = webview.create_window(
        title,
        url,
        width=1280,
        height=860,
        min_size=(960, 640),
    )

    # 验收钩子（见 EXIT_AFTER_ENV 的说明）。正常使用时这个变量不存在
    auto_exit = os.environ.get(EXIT_AFTER_ENV, "").strip()
    if auto_exit:
        try:
            delay = float(auto_exit)
        except ValueError:
            print(f"[desktop] {EXIT_AFTER_ENV}={auto_exit!r} 不是秒数，忽略。", flush=True)
        else:
            print(f"[desktop] {delay} 秒后自动关窗（{EXIT_AFTER_ENV}）。", flush=True)
            threading.Timer(delay, window.destroy).start()

    # 这一行是给排障看的：用户报「窗口没出来」时，日志里能区分是服务没起来、
    # 还是窗口没创建（后者只可能是 WebView 那一层的问题）
    print(f"[desktop] 窗口已创建：{url}", flush=True)

    # debug=True 时开 WebView 自己的开发者工具：三种内核的差异要靠它看
    debug = os.environ.get("QUILL_DESKTOP_DEBUG", "").strip() not in {"", "0", "false"}
    webview.start(debug=debug)


def main() -> int:
    """桌面壳入口。返回进程退出码。"""
    _redirect_output_to_log()

    webview = _import_webview()
    settings = get_settings()
    print(f"[desktop] {settings.app_name} 桌面壳启动，数据根：{settings.home}", flush=True)

    running: RunningServer | None = None
    existing = _find_existing(DEFAULT_HOST, DEFAULT_PORT)
    if existing:
        print(f"[desktop] 已有实例在跑（{existing}），复用它。", flush=True)
        url = existing
    else:
        running = start_server(DEFAULT_HOST, DEFAULT_PORT)
        if not running.wait_ready():
            print("[desktop] 服务没能在超时内就绪，退出。", flush=True)
            running.stop()
            return 1
        print(f"[desktop] 服务就绪：{running.url}", flush=True)
        url = running.url

    try:
        _open_window(webview, settings.app_name, url)
    finally:
        # 窗口关掉就带走自己起的那个服务。复用别人的实例时不要停 —— 那会把
        # 另一个窗口的后端一起杀掉（`running` 为 None 正是这种情况）
        if running is not None:
            running.stop()
            print("[desktop] 服务已停止。", flush=True)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
