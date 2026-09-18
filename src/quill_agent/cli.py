"""命令行入口。

存在的意义是演示「同一份底层逻辑可以被不同入口复用」：
- `uv run quill`                      -> 走这里
- `uv run streamlit run app/app.py`   -> 走 UI
- `from quill_agent.core import echo` -> 走 Python API
"""

from __future__ import annotations

import argparse

from quill_agent import __version__
from quill_agent.config import get_settings
from quill_agent.core import echo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quill", description="quill 命令行入口")
    parser.add_argument("-v", "--version", action="store_true", help="输出版本号")
    parser.add_argument("-m", "--message", help="传入一条消息并原样返回")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.version:
        print(__version__)
        return

    settings = get_settings()
    if args.message:
        print(echo(args.message))
        return

    print(f"{settings.app_name} v{__version__}")
    print("用法：quill [-v] [-m MESSAGE]")


if __name__ == "__main__":
    main()
