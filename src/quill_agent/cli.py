"""命令行入口。

存在的意义是演示「同一份底层逻辑可以被不同入口复用」：

    uv run quill                          -> 走这里
    uv run streamlit run app/app.py       -> 走 UI
    from quill_agent.tools import registry -> 走 Python API

这里只提供几个**只读**的查看命令：不打开界面就能确认「工具装上了没、技能读到没、
记忆里有什么」。排查配置问题时，比开浏览器快得多。
"""

from __future__ import annotations

import argparse

from quill_agent import __version__
from quill_agent.config import get_settings
from quill_agent.memory import MemoryStore
from quill_agent.skills import SkillLibrary
from quill_agent.tools import registry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quill", description="quill 命令行入口")
    parser.add_argument("-v", "--version", action="store_true", help="输出版本号")
    parser.add_argument("-t", "--tools", action="store_true", help="列出全部工具")
    parser.add_argument("-s", "--skills", action="store_true", help="列出全部技能")
    parser.add_argument("-m", "--memory", action="store_true", help="列出长期记忆")
    return parser


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
    print("用法：quill [-v] [-t] [-s] [-m]")


if __name__ == "__main__":
    main()
