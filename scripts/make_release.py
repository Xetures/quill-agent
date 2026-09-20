"""打一个「源码测试包」（zip），给没有开发环境的人试用 / 做平台验证。

用法：

    uv run python scripts/make_release.py --windows

两个为什么：

1. **不用命令行的 `zip`。** macOS 自带的 Info-ZIP 不会设置 UTF-8 文件名标志位，
   而包里 `prompt/身份`、`skills/提交信息` 都是中文目录名 —— 那样打出来的包在
   Windows 资源管理器里解压会是乱码。Python 的 `zipfile` 遇到非 ASCII 名会自动
   带上那个标志位，Windows 认。

2. **必须排除 `data/`。** 那里面有用户的 API Key（`models.json` 是明文存的），
   打进包里等于把密钥发出去。

包是「源码 + uv」形态：使用者装一个 uv（它会顺带装 Python），双击 `start.bat`
就能跑。要做到「什么都不用装」，得把 Python 与依赖一起塞进包里
（python-build-standalone 那条路），那是下一步的事。
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "release"
VERSION = "0.1.0"

# 包内要带的目录（相对仓库根）。`web/dist` 是构建产物、已进版本库 ——
# 带上它，使用者就不需要 Node。
INCLUDE_DIRS = ("src", "server", "prompt", "skills", "web/dist")

INCLUDE_FILES = (
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "Makefile",
    "README.md",
    "LICENSE",
    ".env.example",
    "start.bat",
    "start.sh",
    "start.command",
)

# 一律不进包的东西：缓存、虚拟环境、前端依赖，以及最要紧的 data/
SKIP_PARTS = frozenset(
    {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules", ".git", "data"}
)

# 随包分发的一页说明（测试时「先做什么、重点看什么」）
NOTES = {
    "windows": ("scripts/windows-测试说明.txt", "请先读我-测试说明.txt"),
}


def collect() -> list[tuple[Path, Path]]:
    """收集 (磁盘路径, 包内相对路径)。"""
    items: list[tuple[Path, Path]] = []

    for name in INCLUDE_DIRS:
        for path in sorted((ROOT / name).rglob("*")):
            if path.is_file() and not any(part in SKIP_PARTS for part in path.parts):
                items.append((path, path.relative_to(ROOT)))

    for name in INCLUDE_FILES:
        path = ROOT / name
        if path.is_file():
            items.append((path, Path(name)))

    return items


def main() -> None:
    parser = argparse.ArgumentParser(description="打测试包")
    parser.add_argument(
        "--windows",
        action="store_true",
        help="按 Windows 测试包来打（附一页测试说明；文件名带 windows-test）",
    )
    args = parser.parse_args()

    suffix = "windows-test" if args.windows else "test"
    package = f"quill-v{VERSION}-{suffix}"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / f"{package}.zip"

    items = collect()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, relative in items:
            archive.write(path, str(Path(package) / relative))

        if args.windows:
            source, inside = NOTES["windows"]
            notes = ROOT / source
            if notes.is_file():
                archive.write(notes, str(Path(package) / inside))
            else:
                print(f"警告：没有找到 {source}，包内不带说明")

    print(target)
    print(f"{len(items)} 个文件，{target.stat().st_size / 1024 / 1024:.2f} MB")

    # 把「随包的提示词 / 技能」列出来：这两个目录是用户会在界面上随手加的，
    # 打完包不看一眼的话，很容易把只是自己试用的东西一起发出去
    for name in ("prompt", "skills"):
        files = sorted(
            path.relative_to(ROOT) for path in (ROOT / name).rglob("*") if path.is_file()
        )
        print(f"\n随包的 {name}/（{len(files)} 个）：")
        for item in files:
            print(f"  {item}")


if __name__ == "__main__":
    main()
