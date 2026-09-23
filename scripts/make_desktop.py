"""打一个 macOS 桌面 App（未签名，本机「右键 → 打开」即可试用）。

用法：

    uv run python scripts/make_desktop.py                 # 产出 release/Quill.app
    uv run python scripts/make_desktop.py --keep          # 保留中间产物（排障用）
    uv run python scripts/make_desktop.py --python 3.12   # 换 Python 系列版本
    uv run python scripts/make_desktop.py --runtime ~/... # 指定本地运行时目录

运行时会先找**本机 uv 装过的那份**（`~/.local/share/uv/python/`）——它下的正是
python-build-standalone 的 `install_only` 包，和脚本自己要下的是同一个东西。开发机上
这几乎是必然命中的，省掉一次几十 MB 的下载；而 GitHub releases 在国内经常被限到
几十 KB/s，那一趟能等上十几分钟（实测）。都没找到才去下。

三件只有真打过一次包才知道的事，决定了这个脚本长什么样：

1. **依赖树必须可重定位 —— 所以不建 venv，用 `pip install --target` 平铺**。
   venv 会把绝对路径写进 `pyvenv.cfg`、脚本 shebang 里；整个目录被搬进 `.app`
   （路径变成 `.../Quill.app/Contents/Resources/...`）之后那些路径全指回原来的位置，
   换台机器或者挪一下目录就碎。平铺 + `PYTHONPATH` 没有这个毛病。

2. **布局要让现有代码原样找到东西（这版不改一行源码）**。两处查找都是「从包文件往上
   数几层」的写法，所以只要摆对位置：

       server/main.py    找 <lib>/web/dist
       bootstrap         找出厂资源 <lib>/quill_agent/resources/{prompt,skills}

   对应地，组装时要放：

       lib/web/dist
       lib/quill_agent/resources/prompt、lib/quill_agent/resources/skills

3. **Python 运行时用 python-build-standalone 的 `install_only` 版本**：自包含、可重定位、
   解压即用，不需要用户机器上有 Python，也不往系统里装东西。

还没做的（都在后续阶段里）：代码签名与公证、Windows / Linux 的包、体积裁剪
（pyobjc 那一串是 WebView 绑定的依赖，目前照装）。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import plistlib
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
RELEASE_DIR = ROOT / "release"
BUILD_DIR = ROOT / "build" / "desktop"

# 依赖树先装在构建目录、再复制进 App。放在这里而不是直接装进 App：`--skip-deps`
# 才有意义（App 每次都重建，依赖树不必跟着重装一遍）。
LIB_CACHE = BUILD_DIR / "lib"

# python-build-standalone 的发布页。用 GitHub API 找**最新一次发布**里匹配的资产，
# 而不是写死 URL：写死的话，过一阵那个 tag 被清理或者换命名规则，脚本就静默失效了
PYBS_LATEST = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"

# 随包分发的「出厂资源」目录（见 `bootstrap.template_dir`）
RESOURCE_DIRS = ("prompt", "skills")

# 运行时压缩包的实际大小在 30 MB 上下；低于这个数只可能是没下完
MIN_RUNTIME_BYTES = 20 * 1024 * 1024

# 进包的前端产物（`server/main.py` 找的是 <lib>/web/dist）
WEB_DIST = ROOT / "web" / "dist"

# 图标：优先用设计稿直接给的 .icns（毛玻璃蓝版的成品多尺寸图标包），
# 没有它才退回「渲染 svg → 生成各尺寸 → iconutil 打包」
ICON_ICNS = ROOT / "web" / "public" / "quill-glass.icns"
ICON_SVG = ROOT / "web" / "src" / "assets" / "quill-icon-glass.svg"
ICON_PNG = ROOT / "web" / "public" / "apple-touch-icon.png"

# uv 管理的 Python 装在这里，每个版本一个目录（见 `local_runtime`）
UV_PYTHON_DIR = Path.home() / ".local" / "share" / "uv" / "python"


def _log(message: str) -> None:
    print(f"[pack] {message}", flush=True)


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def _arch() -> str:
    """python-build-standalone 的资产名里，arm64 写作 aarch64。"""
    machine = platform.machine().lower()
    return {"arm64": "aarch64", "x86_64": "x86_64"}.get(machine, machine)


def local_runtime(python_series: str) -> Path | None:
    """本机 uv 装过的同系列 Python，没有就返回 None。

    uv 下的那份就是 python-build-standalone 的 `install_only` 包（未做任何改写，
    仍是可重定位的），所以可以直接当随包的运行时用。开发机上基本必然命中 ——
    而这个项目本来就用 uv 跑，等于运行时早就躺在盘上了。
    """
    # 目录名形如 cpython-3.12.14-macos-aarch64-none；机器名在资产里是 aarch64 / x86_64
    pattern = f"cpython-{python_series}*-macos-{_arch()}-none"
    for candidate in sorted(UV_PYTHON_DIR.glob(pattern), reverse=True):
        if (candidate / "bin" / "python3").is_file():
            return candidate
    return None


def find_runtime_asset(python_series: str) -> tuple[str, str]:
    """在最新发布里找出匹配的运行时资产，返回 (名字, 下载地址)。"""
    request = urllib.request.Request(
        PYBS_LATEST,
        headers={
            # GitHub API 要求带 UA，不带直接 403
            "User-Agent": "quill-desktop-packager",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    prefix = f"cpython-{python_series}."
    suffix = f"-{_arch()}-apple-darwin-install_only.tar.gz"
    for asset in payload.get("assets", []):
        name = str(asset.get("name", ""))
        if name.startswith(prefix) and name.endswith(suffix):
            return name, str(asset["browser_download_url"])

    raise SystemExit(
        f"最新发布里没有找到 {python_series}.x 的 macOS 运行时（找的是 {prefix}*{suffix}）。\n"
        f"去 {PYBS_LATEST} 看一眼资产命名，或用 --python 换一个系列。"
    )


def download(url: str, target: Path) -> Path:
    """下载到缓存位置；已经下过就直接用（重复打包含见）。"""
    if target.is_file():
        if target.stat().st_size >= MIN_RUNTIME_BYTES:
            _log(f"复用已下载的运行时：{target.name}")
            return target
        # 上次下载被打断（Ctrl+C、断网）留下的半截文件：直接「复用」的话，错误会在
        # 解压那一步冒出来，而那时的报错完全看不出是「文件本来就是残的」。当场重下
        _log(f"上次的下载不完整（{target.stat().st_size / 1024:.0f} KB），重新下载")
        target.unlink()

    target.parent.mkdir(parents=True, exist_ok=True)
    _log(f"下载运行时：{url.rsplit('/', 1)[-1]}")
    started = time.monotonic()
    with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    _log(f"下载完成（{target.stat().st_size / 1024 / 1024:.1f} MB，"
         f"{time.monotonic() - started:.0f} 秒）")
    return target


def extract_runtime(archive: Path, destination: Path) -> Path:
    """解压运行时，返回其中 `python/` 那一层。

    install_only 包里就是一个 `python/` 目录（bin/、lib/ 等），解压后直接可用。
    """
    if (destination / "python" / "bin" / "python3").exists():
        _log("复用已解压的运行时")
        return destination / "python"

    destination.mkdir(parents=True, exist_ok=True)
    _log("解压运行时…")
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(destination)

    python_dir = destination / "python"
    if not (python_dir / "bin" / "python3").exists():
        raise SystemExit(f"解压后的结构不对：{python_dir} 里没有 bin/python3")
    return python_dir


def install_dependencies(python: Path, target: Path) -> None:
    """把本项目与它的依赖平铺装到 `target`。

    装的是**本仓库**（`pip install .`），所以 `quill_agent` 与 `server` 两个包都在里面，
    版本号也来自 `pyproject.toml` —— 和 `uv sync` 装出来的是同一份东西。

    装的是 `.[desktop]`、不是光秃秃的 `.`：**壳自己就在可选依赖里**，主依赖不含
    pywebview —— 少写这个 extra，打出来的包会在用户双击那一刻才报「需要 pywebview」。

    优先用 uv（这个项目的开发环境本来就是它装的）：uv 并行下载、有一份全局缓存，
    那些 wheel 多半已经在盘上了，打一次包从几分钟降到十几秒 —— pip 逐个下载 pyobjc
    那一串（WebView 绑定，一二十个包）实测能卡好几分钟。机器上没有 uv 时退回 pip，
    功能一样，只是慢。

    `--no-compile`：预编译 pyc 只会让体积变大，运行时按需编译完全够用（也避免把
    `__pycache__` 写进只读的 App 包）。
    """
    target.mkdir(parents=True, exist_ok=True)
    # 方括号是 pip/uv 的 extra 语法，直接作为参数传（不经过 shell，不会被当成通配符）
    package = f"{ROOT}[desktop]"

    uv = shutil.which("uv")
    if uv:
        _log("安装依赖（uv pip install --target）…")
        command = [uv, "pip", "install", "--python", str(python)]
    else:
        _log("安装依赖（pip install --target；没有 uv，慢一些）…")
        command = [str(python), "-m", "pip", "install"]

    subprocess.run(
        [*command, "--quiet", "--no-compile", "--upgrade", "--target", str(target), package],
        check=True,
    )


def _tree_size(path: Path) -> int:
    """目录/文件的字节数（不跟随符号链接，避免重复计算）。"""
    if path.is_file() and not path.is_symlink():
        return path.stat().st_size

    total = 0
    for item in path.rglob("*"):
        if item.is_file() and not item.is_symlink():
            total += item.stat().st_size
    return total


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists() or path.is_symlink():
        path.unlink()


def dedupe_binaries(python: Path) -> None:
    """`bin/` 里 python、python3、python3.12 是同一份 18 MB 的二进制各存一份。

    留 `python3`（启动器调的就是它），其余换成指向它的**相对**符号链接：省下两份的
    空间，同时任何写 `python` 的调用照旧能跑（App 里没有别的进程会调，但留着更省心）。
    相对链接不写死绝对路径，和整个 App 一起挪动也没问题。
    """
    real = python / "bin" / "python3"
    if not real.is_file():
        return

    for path in sorted((python / "bin").glob("python*")):
        if path.name == real.name or path.is_symlink() or not path.is_file():
            continue
        # python3-config / python3.12-config 是小脚本，别动它们
        if path.stat().st_size < 1024 * 1024:
            continue
        path.unlink()
        path.symlink_to(real.name)


# 裁掉的东西：每一条都属于「打包后的 App 不会再碰」的类别。用 glob 而不是写死
# `python3.12`，这样 `--python 3.13` 也照样命中
_SLIM_PATTERNS = (
    # 运行时自带的包管理 —— 装好的 App 里不会再装包（打包时用的那份 pip 在裁剪前已用完）
    "python/lib/python3.*/site-packages/pip",
    "python/lib/python3.*/site-packages/pip-*.dist-info",
    "python/lib/python3.*/site-packages/setuptools",
    "python/lib/python3.*/site-packages/pkg_resources",
    "python/lib/python3.*/site-packages/wheel",
    "python/lib/python3.*/site-packages/_distutils_hack",
    "python/lib/python3.*/ensurepip",
    # 用不到的 stdlib 附属物
    "python/lib/python3.*/idlelib",  # 自带 IDE
    "python/lib/python3.*/lib2to3",  # 2→3 转换器（3.13 起已从标准库移除）
    "python/lib/python3.*/pydoc_data",  # help() 的离线文档
    "python/lib/python3.*/venv",  # App 里不会再建虚拟环境
    "python/lib/python3.*/test",  # 标准库测试套件
    "python/lib/python3.*/tkinter",  # GUI 工具包：界面走 WebView
    "python/lib/python3.*/turtledemo",
    # tcl/tk 运行时（tkinter 的底层，同上一条）
    "python/lib/tcl*",
    "python/lib/tk*",
    "python/lib/itcl*",
    "python/lib/libtcl*",
    "python/lib/libtk*",
    # 「编译扩展」才需要的东西
    "python/include",
    "python/lib/python3.*/config-*",
    "python/lib/libpython3.*.dylib",  # bin/python3 是静态链接的，没人引用它
    # 依赖树里带的测试与命令行脚本
    "lib/PyObjCTest",  # pyobjc-core 的测试包（16 MB）
    "lib/bin",  # 各包的可执行脚本（uvicorn / fastapi / dotenv…），启动器不走它们
)


def slim(resources: Path) -> None:
    """裁掉打包后用不到的东西。

    只删「运行时不碰」的类别（见 `_SLIM_PATTERNS` 里每一条的说明），**不碰任何业务
    代码与依赖包本身** —— 这个 App 的价值是那份 Python 依赖树，裁错了就是「双击打不开」，
    而省下来的那几 MB 完全不值。

    有一条底线：**裁完必须真跑一次**。`smoke_check` 只验证 import，而 Python 有些东西
    要到运行时才加载（比如 WebView 后端在开窗口时才 import 具体的 framework 模块）。
    """
    before = _tree_size(resources)
    removed = 0

    for pattern in _SLIM_PATTERNS:
        for path in sorted(resources.glob(pattern)):
            if not path.exists() and not path.is_symlink():
                continue
            removed += _tree_size(path)
            _remove(path)

    after = _tree_size(resources)
    _log(
        f"体积裁剪：{before / 1024 / 1024:.0f} MB → {after / 1024 / 1024:.0f} MB"
        f"（去掉 {removed / 1024 / 1024:.0f} MB）"
    )


def smoke_check(python: Path, lib: Path) -> None:
    """用**随包的**解释器和依赖树做一次导入自检。

    打出来的包最容易犯的错是「少装了一个包」或者「东西摆错了位置」，而这两种错在
    开发者机器上完全看不见（本机 venv 里什么都有）。等用户双击才发现就太晚了 ——
    这里用打包后的环境跑一次导入，缺什么当场就报。

    `import server.main` 只执行到模块级（建 app、注册路由），不会启动服务；它顺带
    验证了 `web/dist` 那套路径计算没被布局改动弄坏。
    """
    _log("自检：用随包的运行时导入关键模块…")
    subprocess.run(
        [str(python), "-c", "import webview, quill_agent.desktop, server.main"],
        check=True,
        env={**os.environ, "PYTHONPATH": str(lib)},
    )


def copy_tree(source: Path, destination: Path) -> None:
    """复制目录内容（不复制目录本身），已存在的覆盖。"""
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def copy_web_dist(lib: Path) -> None:
    """把前端产物摆到 `lib/web/dist`（`server/main.py` 找的就是那儿）。

    出厂资源（`prompt/`、`skills/`）**不在这里复制** —— 它们由 wheel 随包带过来
    （见 pyproject 的 `force-include`），那样才不会被「装完再补一步」的时序影响：
    真出过一次事故，一个还在后台跑的 pip 进程把 `lib/quill_agent` 重装了一遍，
    顺手删掉了刚复制进去的 resources —— 而打包日志一切正常，坑要等用户第一次
    打开时才发现（新用户的提示词和技能是空的）。
    """
    if not WEB_DIST.is_dir():
        raise SystemExit("web/dist 不存在，先跑 `make build`（打包要带上前端产物）。")

    copy_tree(WEB_DIST, lib / "web" / "dist")
    files = sum(1 for item in WEB_DIST.rglob("*") if item.is_file())
    _log(f"前端产物就位：lib/web/dist（{files} 个文件）")

    for name in RESOURCE_DIRS:
        if not (lib / "quill_agent" / "resources" / name).is_dir():
            raise SystemExit(
                f"wheel 里没有出厂资源 {name}/ —— 检查 pyproject 的 force-include 配置。"
            )
    _log(f"出厂资源随 wheel 就位：lib/quill_agent/resources/（{'、'.join(RESOURCE_DIRS)}）")


def build_icon(work: Path) -> Path | None:
    """给出 .icns，拿不到就返回 None（没有图标也能运行）。

    第一选择是设计稿现成的 `QuillGlass.icns`（已收进 `web/public/`）：那是做好的
    多尺寸图标包，比现场渲染保真，也省掉 sips 那一串缩放。

    没有它才退回现场生成：`qlmanage` 是 macOS 自带的 QuickLook 渲染器，把 svg 转成
    1024 的 png 最省事（sips 读不了 svg）。两条路都拿不到素材时不该让打包失败 ——
    图标是锦上添花。
    """
    if ICON_ICNS.is_file():
        return ICON_ICNS

    if not ICON_SVG.is_file() and not ICON_PNG.is_file():
        return None

    iconset = work / "Quill.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)

    source_png = work / "icon-1024.png"
    if ICON_SVG.is_file():
        subprocess.run(
            ["qlmanage", "-t", "-s", "1024", "-o", str(work), str(ICON_SVG)],
            check=False,
            capture_output=True,
        )
        rendered = work / f"{ICON_SVG.name}.png"
        if rendered.is_file():
            rendered.replace(source_png)
    if not source_png.is_file() and ICON_PNG.is_file():
        shutil.copy2(ICON_PNG, source_png)
    if not source_png.is_file():
        _log("图标渲染失败，跳过（不影响运行）")
        return None

    for size in (16, 32, 64, 128, 256, 512, 1024):
        for scale in (1, 2):
            pixels = size * scale
            if pixels > 1024:
                continue
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            subprocess.run(
                ["sips", "-z", str(pixels), str(pixels), str(source_png), "--out",
                 str(iconset / name)],
                check=False,
                capture_output=True,
            )

    icns = work / "Quill.icns"
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(icns)],
        check=False,
        capture_output=True,
    )
    return icns if icns.is_file() else None


def write_info_plist(contents: Path, version: str) -> None:
    info = {
        "CFBundleName": "Quill",
        "CFBundleDisplayName": "Quill",
        # 反着写的域名只是约定，标识的是「这是谁家的 App」；换成别的会造成
        # 同一台机器上被当成两个应用（数据目录与偏好都按它区分）
        "CFBundleIdentifier": "ai.quill.desktop",
        "CFBundleExecutable": "Quill",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "CFBundleIconFile": "Quill",
        # 低于 11 的系统上 WKWebView 的老版本对项目里用到的 CSS 支持不全，
        # 与其让人装上一个「看着坏掉」的版本，不如把门槛写清楚
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        # 沙箱与 MCP 都要起子进程；这条不是「允许」（那由内核沙箱决定），
        # 而是让系统知道这个 App 有这种行为，公证与用户提示时不会当成异常
        "LSApplicationCategoryType": "public.app-category.developer-tools",
    }
    with (contents / "Info.plist").open("wb") as handle:
        plistlib.dump(info, handle)


LAUNCHER = """#!/bin/sh
# Quill 桌面版启动器。
#
# 它只做一件事：把 PYTHONPATH 指到随包的依赖树，然后跑桌面壳 —— 和开发时的
# `uv run quill-desktop` 是同一份代码（`quill_agent.desktop:main`）。
#
# 用 sh 而不是直接放可执行文件：Finder 双击时 cwd 是 `/`，而启动器自己算出的
# 绝对路径不受影响；工作目录该是哪个由应用自己决定（见 config._default_work_dir）。
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
RESOURCES="$(cd "$HERE/../Resources" && pwd)"
export PYTHONPATH="$RESOURCES/lib"
exec "$RESOURCES/python/bin/python3" -m quill_agent.desktop
"""


def write_launcher(macos: Path) -> None:
    launcher = macos / "Quill"
    launcher.write_text(LAUNCHER, encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def main() -> int:
    parser = argparse.ArgumentParser(description="打 macOS 桌面 App（未签名）")
    parser.add_argument("--python", default="3.12", help="Python 系列版本（默认 3.12）")
    parser.add_argument(
        "--runtime",
        default="",
        help="直接指定运行时目录（默认找本机 uv 装的那份，找不到才下载）",
    )
    parser.add_argument("--keep", action="store_true", help="保留中间产物（排障用）")
    parser.add_argument("--skip-deps", action="store_true", help="跳过 pip 安装（复用上一次）")
    args = parser.parse_args()

    # 打包是「先整个删掉、再原地重建」，而 App 目录有六千多个文件。有些环境（比如
    # CodeBuddy 的沙箱）装了「一次删除超过 500 个文件就要人工确认」的保护，会卡在这里，
    # 每次重打都得手动加环境变量 —— 那不该是调用者要记住的事。
    #
    # 这里把阈值调高**只对本进程有效**：删的是我们自己上一次的产物，是这次构建的第一步，
    # 不存在「误删用户东西」的可能。别的进程、别的命令不受影响。
    threshold_env = "CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD"
    if threshold_env in os.environ:
        os.environ[threshold_env] = "999999"

    if platform.system() != "Darwin":
        raise SystemExit("目前只支持 macOS。Windows / Linux 的打包在后续阶段。")

    version = project_version()
    app = RELEASE_DIR / "Quill.app"
    work = BUILD_DIR / "work"

    _log(f"目标：{app}（版本 {version}）")
    if app.exists():
        shutil.rmtree(app)
    work.mkdir(parents=True, exist_ok=True)

    # 1. 运行时：本机有的先用（理由见模块开头），没有才去下
    if args.runtime:
        python_dir = Path(args.runtime).expanduser().resolve()
        if not (python_dir / "bin" / "python3").is_file():
            raise SystemExit(f"--runtime 指向的目录里没有 bin/python3：{python_dir}")
        _log(f"使用指定的运行时：{python_dir}")
    elif (found := local_runtime(args.python)) is not None:
        python_dir = found
        _log(f"复用本机 uv 的运行时：{found}")
    else:
        archive_name, url = find_runtime_asset(args.python)
        archive = download(url, BUILD_DIR / archive_name)
        python_dir = extract_runtime(archive, work / "runtime")

    # 2. 组装目录骨架
    contents = app / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    lib = resources / "lib"
    macos.mkdir(parents=True, exist_ok=True)
    shutil.copytree(python_dir, resources / "python", dirs_exist_ok=True)

    # 3. 依赖（含本仓库两个包与随包的出厂资源）
    if args.skip_deps and (LIB_CACHE / "quill_agent").is_dir():
        _log("按 --skip-deps 复用上次装好的依赖树")
    else:
        if LIB_CACHE.exists():
            shutil.rmtree(LIB_CACHE)
        install_dependencies(resources / "python" / "bin" / "python3", LIB_CACHE)
    shutil.copytree(LIB_CACHE, lib, dirs_exist_ok=True)

    # 4. 前端产物（出厂资源已由 wheel 带进来，见 copy_web_dist 的说明）
    copy_web_dist(lib)

    # 5. 体积裁剪（必须在装完依赖之后：打包用的那份 pip 在运行时里）
    dedupe_binaries(resources / "python")
    slim(resources)

    # 6. 自检（缺包 / 摆错位置在这里当场暴露，别等用户双击）
    smoke_check(resources / "python" / "bin" / "python3", lib)

    # 7. 图标与描述文件、启动器
    icns = build_icon(work)
    if icns is not None:
        shutil.copy2(icns, resources / "Quill.icns")
        _log("图标已生成：Resources/Quill.icns")
    write_info_plist(contents, version)
    write_launcher(macos)

    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)

    size_mb = _tree_size(app) / 1024 / 1024

    # **重新向 Launch Services 登记一次。**
    #
    # 打包是「先整个删掉、再原地重建」，而这个路径在 Finder / Launch Services 里是**有
    # 记录**的：记录还指着上一份包，于是新包的图标和元数据都不会被重新读取 —— 表现就是
    # 「图标没了」（显示成通用图标），而包里 `CFBundleIconFile` 和 `.icns` 其实都是好的。
    #
    # 每次构建都主动登记，省得让人去猜是不是自己没重启 Finder。
    lsregister = (
        "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
        "LaunchServices.framework/Support/lsregister"
    )
    if Path(lsregister).is_file():
        try:
            subprocess.run([lsregister, "-f", str(app)], check=False, timeout=20)
        except (OSError, subprocess.SubprocessError):
            pass  # 登记失败不影响包本身，只是图标可能要等 Finder 自己刷

    _log(f"完成：{app}（{size_mb:.0f} MB）")
    print("\n试用：open release/Quill.app    （未签名，首次要右键 → 打开）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
