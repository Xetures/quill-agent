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
import os
import threading
import webbrowser

from quill_agent import __version__
from quill_agent.config import get_settings
from quill_agent.memory import MemoryStore
from quill_agent.skills import SkillLibrary
from quill_agent.tools import registry


def __getattr__(name: str):
    """按需转发「起服务」那一层的东西（`pick_port`、`start_server`…）。

    存在的理由是那两条要求凑在一起了：`pick_port` 的行为属于命令行（测试与
    排障都从 `cli` 这个门进来找它），而 `server_runner` 会连带把 uvicorn 拉进来 ——
    可只跑 `quill -t` 那种查看命令的人不该为此付一次 Web 栈的导入开销（见模块
    开头「其余几个是只读的查看命令」那条）。转发只在真正访问时才触发导入。
    """
    if name in {"PORT_ATTEMPTS", "RunningServer", "pick_port", "start_server"}:
        from quill_agent import server_runner

        return getattr(server_runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    serve.add_argument(
        "--token", default="", help="非本机监听时的 API 访问令牌；也可用 QUILL_ACCESS_TOKEN"
    )

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


def serve(host: str, port: int, *, open_browser: bool, token: str = "") -> None:
    """启动后端（`web/dist` 存在时会连前端一起托管）。

    这里只做「命令行特有」的那部分：打印、开浏览器、阻塞等结束。挑端口、校验令牌、
    跑 uvicorn 都在 `server_runner` 里 —— 桌面壳用的是同一份（见那里的模块说明）。

    启动自检（播种出厂资源、迁移旧数据、说清数据根在哪）由 `server.main` 的 lifespan
    负责 —— 那里是**唯一**的入口：直接 `uvicorn server.main:app` 时没有 CLI，
    而这里再调一次就会把同一份说明打两遍、副作用也白做第二遍（虽然都是幂等的）。
    """
    # 延迟导入：只跑 `quill -t` 那种查看命令时不必把 Web 栈整个加载进来
    from quill_agent.server_runner import start_server

    settings = get_settings()
    running = start_server(host, port, token=token)

    print(f"{settings.app_name} v{__version__} 已启动：{running.url}", flush=True)
    access_token = os.environ.get("QUILL_ACCESS_TOKEN", "")
    if access_token:
        print("API 访问认证已启用。", flush=True)
    print("按 Ctrl+C 结束。", flush=True)

    if open_browser:
        # 推迟一点再开：等 uvicorn 真的监听上，免得第一次打开是「无法连接」
        browser_url = (
            f"{running.url}/?access_token={access_token}" if access_token else running.url
        )
        threading.Timer(1.5, lambda: webbrowser.open(browser_url)).start()

    running.wait()


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
        serve(args.host, args.port, open_browser=args.open_browser, token=args.token)
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
