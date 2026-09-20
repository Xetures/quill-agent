"""命令行入口。

存在的意义是演示「同一份底层逻辑可以被不同入口复用」：

    uv run quill                            -> 走这里
    from quill_agent.tools import registry  -> 走 Python API

两个用途：

    `quill serve` —— 启动后端（并托管前端产物）。这是「双击启动」那条路用的命令，
    也是打包发布时的统一入口；

    其余几个是**只读**的查看命令：不打开界面就能确认「工具装上了没、技能读到没、
    记忆里有什么」。排查配置问题时，比开浏览器快得多。
"""

from __future__ import annotations

import argparse
import socket
import threading
import webbrowser

from quill_agent import __version__
from quill_agent.bootstrap import startup_note
from quill_agent.config import get_settings
from quill_agent.memory import MemoryStore
from quill_agent.skills import SkillLibrary
from quill_agent.tools import registry

# 双击启动时若默认端口被占，往后顺延多少个端口
PORT_ATTEMPTS = 20


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quill", description="quill 命令行入口")
    parser.add_argument("-v", "--version", action="store_true", help="输出版本号")
    parser.add_argument("-t", "--tools", action="store_true", help="列出全部工具")
    parser.add_argument("-s", "--skills", action="store_true", help="列出全部技能")
    parser.add_argument("-m", "--memory", action="store_true", help="列出长期记忆")

    subparsers = parser.add_subparsers(dest="command")
    serve = subparsers.add_parser("serve", help="启动服务（浏览器打开即用）")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址；填 0.0.0.0 可供局域网访问")
    serve.add_argument("--port", type=int, default=8000, help="监听端口（被占用时自动顺延）")

    # 两个参数都收：`--no-browser` 是给命令行用户的（默认就会开浏览器，所以他们需要
    # 一个「别开」的开关），而启动脚本里写的是 `--open-browser`（读脚本的人一眼能看出
    # 它会开浏览器）。**只定义其中一个就会出事** —— 曾经启动脚本传 `--open-browser`、
    # 这里只认 `--no-browser`，双击启动直接报「unrecognized arguments」。
    browser = serve.add_mutually_exclusive_group()
    browser.add_argument(
        "--open-browser",
        dest="open_browser",
        action="store_true",
        default=True,
        help="启动后自动打开浏览器（默认）",
    )
    browser.add_argument(
        "--no-browser",
        dest="open_browser",
        action="store_false",
        help="不要自动打开浏览器",
    )

    return parser


def pick_port(host: str, port: int, attempts: int = PORT_ATTEMPTS) -> int:
    """找一个能用的端口：从 `port` 开始往上试。

    为什么不让 uvicorn 自己报错退出：双击启动的用户看不到命令行报错 ——
    窗口一闪就没了，他只会觉得「打不开」。顺延一个端口并把地址打出来，好得多。

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


def serve(host: str, port: int, *, open_browser: bool) -> None:
    """启动后端（`web/dist` 存在时会连前端一起托管）。"""
    # 延迟导入：只跑 `quill -t` 那种查看命令时不必把 Web 栈整个加载进来
    import uvicorn

    settings = get_settings()
    chosen = pick_port(host, port)

    # 用 print 而不是 logger：uvicorn 默认只配置自己的 logger，
    # 我们这边 logger.info 会被 root 的 WARNING 级别挡掉，用户就看不到数据根在哪了。
    # flush 是为了重定向到日志文件时也能立刻看到 —— 那时的 stdout 是块缓冲的
    print(startup_note(settings), flush=True)

    # 监听 0.0.0.0 时，浏览器该打开的是本机地址而不是「0.0.0.0」
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{display_host}:{chosen}"

    if chosen != port:
        print(f"端口 {port} 被占用，改用 {chosen}。", flush=True)

    print(f"{settings.app_name} v{__version__} 已启动：{url}", flush=True)
    print("按 Ctrl+C 结束。", flush=True)

    if open_browser:
        # 推迟一点再开：等 uvicorn 真的监听上，免得第一次打开是「无法连接」
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    uvicorn.run("server.main:app", host=host, port=chosen, log_level="info")


def show_tools() -> None:
    """列出全部工具。"""
    for spec in registry.all():
        print(f"{spec.name:<14} [{spec.category}] {spec.description}")


def show_skills() -> None:
    """列出全部技能。"""
    settings = get_settings()
    library = SkillLibrary(settings.skills_dir)

    names = library.list_names()
    if not names:
        print(f"（{settings.skills_dir}/ 下还没有技能）")
        return

    for name in names:
        meta = library.meta(name)
        description = (meta.description if meta else "") or "（未填写适用场景）"
        print(f"{name}：{description}")


def show_memory() -> None:
    """列出长期记忆。"""
    items = MemoryStore(get_settings().memory_path).list()
    if not items:
        print("（还没有记忆）")
        return

    for item in items:
        mark = "✓" if item.enabled else "✗"
        print(f"{mark} [{item.created_at}] {item.text}")


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "serve":
        serve(args.host, args.port, open_browser=args.open_browser)
        return

    if args.version:
        print(__version__)
        return

    if args.tools:
        show_tools()
        return

    if args.skills:
        show_skills()
        return

    if args.memory:
        show_memory()
        return

    settings = get_settings()
    print(f"{settings.app_name} v{__version__}")
    print("用法：quill serve [--host 0.0.0.0] [--port 8000] [--no-browser]")
    print("      quill [-v] [-t] [-s] [-m]")


if __name__ == "__main__":
    main()
