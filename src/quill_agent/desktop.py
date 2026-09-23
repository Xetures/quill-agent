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
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from quill_agent.config import HOME_ENV, get_settings
from quill_agent.server_runner import PORT_ATTEMPTS, RunningServer, start_server

# 桌面壳固定在本机、固定从默认端口开始找（顺延由 pick_port 负责）
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

# 自动化验收用的钩子：设了就在这么多秒后自动关窗，走完「关窗 → 停服务」整条链路。
# 无人值守时点不了关闭按钮，而这一步恰恰是最容易出错、也最该被验的（残留进程）。
EXIT_AFTER_ENV = "QUILL_DESKTOP_EXIT_AFTER"

# 标题栏带的标准高度（pt）与红绿灯三个按钮占的宽度。
#
# 内容延伸进标题栏之后，这两个数字决定「界面要让出多少」和「拖动带从哪儿开始」。
# 它们是 macOS 的观感常量，不做成配置项 —— 系统自己画的红绿灯就在那个位置，
# 界面让多让少都得跟着它走。
TITLEBAR_HEIGHT = 28.0
TRAFFIC_LIGHT_WIDTH = 78.0

# 标题栏怎么处理：默认 `blend`（融进界面），设成 `system` 就退回系统的那条灰。
#
# 做成开关而不是写死，是因为这一层**完全没法在 CI 里验** —— 它是真机上的观感，
# 而失败模式又很恼人（窗口拖不动）。出了问题要能靠一个环境变量退回去，
# 而不是让人去改代码。
TITLEBAR_ENV = "QUILL_DESKTOP_TITLEBAR"


def _titlebar_wants_blend() -> bool:
    return os.environ.get(TITLEBAR_ENV, "blend").strip().lower() != "system"


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


def _wait_for_native(window: Any, *, seconds: float = 5.0) -> Any:
    """等 pywebview 把原生窗口句柄挂上来（macOS 上是 NSWindow）。

    `create_window` 返回时窗口**通常还没真正创建** —— 它是在事件循环的第一拍里建的，
    那时 `native` 才被填上。轮询而不是死等固定时长：慢机器上不至于错过，
    快机器上也不白等。
    """
    deadline = time.monotonic() + seconds
    while True:
        native = getattr(window, "native", None)
        if native is not None:
            return native
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.1)


def _blend_titlebar_into_ui(native: Any, attempt: int = 0) -> None:
    """把系统标题栏融进界面。**必须在主线程调用**（AppKit 的要求）。

    三件事凑成「内容延伸进标题栏、红绿灯浮在界面上」这个效果：

    1. `NSWindowStyleMaskFullSizeContentView` —— 内容从窗口最顶端开始画；
    2. 标题栏透明 + 标题文字隐藏；
    3. **补一条透明的拖动带** —— 内容延伸之后，原本负责拖动的区域被 WebView 占住了，
       鼠标事件到不了系统标题栏，窗口就拖不动。

    **第 1 条必须在窗口创建时带上，运行时再改是无效的** —— 这是整套里最坑的一点，
    而且它是静默的：`setStyleMask_` 之后读回来明明是 True，可 `contentLayoutRect` 始终是
    「窗口减标题栏」，内容区压根不含标题栏。也就是说 WebView 永远盖不到那一条（怎么设
    frame 都会被内容区拉回去，那半截就露出窗口背景的灰）。样式位在窗口显示之后改动
    **不会重建布局**，只能创建时带。所以窗口是用 `frameless=True` 建的，由 pywebview 在
    创建时设好它（见 `_open_window`）。

    代价是 pywebview 的 frameless 会把红绿灯一起隐藏（那是给「自绘按钮」准备的路线），
    这里再把它们显示回来 —— 用的还是系统那三个按钮。

    第 3 条最容易漏，漏掉的表现是「窗口拖不动」，比不融合还糟。pywebview 自带的拖动救不了：
    它只在 frameless 时启用，而 frameless 那条路正好把红绿灯藏了。
    """
    import AppKit
    from PyObjCTools import AppHelper  # 轮询要用 callLater（见下）

    # **先等 WebView 挂上来。** 这个回调跑在 `webview.start()` 里、WebView 被创建**之前**，
    # 那一刻 contentView 还是个空壳（实测 `subviews=0`）。早做的事全会白费 —— 窗口的可用
    # 区域改了，却没有东西去填它。
    #
    # 主线程上不能 sleep（会冻住界面），所以用 callLater 每 0.1 秒回来看一眼，最多 5 秒。
    if not native.contentView().subviews():
        if attempt >= 50:
            print("[desktop] 没等到 WebView，标题栏保持系统默认。", flush=True)
            return
        AppHelper.callLater(0.1, _blend_titlebar_into_ui, native, attempt + 1)
        return

    # 常量名随 pyobjc 版本变（旧版没有它），取不到就退回对应的位值 ——
    # pywebview 自己也是这么兜的
    full_size = getattr(AppKit, "NSWindowStyleMaskFullSizeContentView", 1 << 15)

    native.setStyleMask_(native.styleMask() | full_size)
    native.setTitlebarAppearsTransparent_(True)
    native.setTitleVisibility_(AppKit.NSWindowTitleHidden)
    # 红绿灯**不动**：它们正浮在内容上，这正是要的效果（pywebview 的 frameless 分支
    # 会把这三个按钮 hide 掉，那是另一条路 —— 自绘按钮）

    class _TitlebarBand(AppKit.NSView):
        """标题栏那一条：管拖窗口，也管红绿灯的「移上去才亮」。

        它盖在系统标题栏**之上**，所以能做两件事：

        - 默认画三个灰圆，把真红绿灯遮住（未激活那种观感）；
        - 鼠标移上来（`NSTrackingArea`）就撤掉灰圆，露出真红绿灯。

        `hitTest_` 把红绿灯那一段**放行给系统**：默认那三个圆是画上去的、不能真点，
        但鼠标一移过去就已经露出真灯了，所以点得到 —— 关窗、最小化都照常。
        其余部分归自己，按住就能拖窗口。
        """

        _hovered = False

        def mouseDownCanMoveWindow(self) -> bool:  # noqa: N802 - ObjC 协议方法名
            return True

        def acceptsFirstMouse_(self, event):  # noqa: N802
            # 窗口没激活时点这一下也立刻能拖 —— 系统标题栏就是这个行为
            return True

        def hitTest_(self, point):  # noqa: N802
            # `point` 是**父视图坐标系**里的点，`frame()` 也在同一个坐标系里，所以直接比。
            #
            # **这一句边界判断不能省。** 覆盖 `hitTest_` 就等于把 NSView 默认实现里
            # 「点是否落在自己 frame 内」的判断一并丢了 —— 少了它，整窗任何 x>=78 的点
            # 都会被认领，所有点击变成「拖窗口」，界面整个点不动（只剩 x<78 的红绿灯能用）。
            #
            # 这里也不能图省事写 `super().hitTest_(point)`：ObjC 子类里那样会触发
            # `ObjCSuperWarning: super() is not objc.super`，实测直接抛异常。
            box = self.frame()
            inside = (
                box.origin.x <= point.x <= box.origin.x + box.size.width
                and box.origin.y <= point.y <= box.origin.y + box.size.height
            )
            if not inside:
                return None
            # 标题栏那一条里，红绿灯所在的一段交给系统，其余归自己（用来拖窗口）
            return None if point.x < TRAFFIC_LIGHT_WIDTH else self

        def updateTrackingAreas(self):  # noqa: N802
            for area in self.trackingAreas():
                self.removeTrackingArea_(area)
            self.addTrackingArea_(
                AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                    self.bounds(),
                    AppKit.NSTrackingMouseEnteredAndExited
                    | AppKit.NSTrackingActiveAlways
                    | AppKit.NSTrackingInVisibleRect,
                    self,
                    None,
                )
            )

        def mouseEntered_(self, event):  # noqa: N802
            self._hovered = True
            self.setNeedsDisplay_(True)

        def mouseExited_(self, event):  # noqa: N802
            self._hovered = False
            self.setNeedsDisplay_(True)

        def drawRect_(self, rect):  # noqa: N802
            if self._hovered:
                return  # 悬停时不画，让真红绿灯露出来
            # tertiaryLabelColor 会跟着明暗模式走，浅色板上是深灰、深色板上是浅灰 ——
            # 正好是「未激活红绿灯」该有的样子
            AppKit.NSColor.tertiaryLabelColor().set()
            middle = self.bounds().size.height / 2
            # 位置照抄系统的：第一个圆心在 7+6.5、之后每隔 20，直径 13
            for index in range(3):
                centre = 7 + index * 20 + 6.5
                AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                    AppKit.NSMakeRect(centre - 6.5, middle - 6.5, 13, 13)
                ).fill()

    content = native.contentView()

    # **把红绿灯补回来。** pywebview 的 frameless 分支会把它们隐藏（那是给「自绘按钮」
    # 准备的路线），但这里要的正是系统那三个按钮 —— 只是显示回来，没有自绘。
    # 顺带确保 Titled 在：没有它，红绿灯根本没地方画。
    if not native.styleMask() & AppKit.NSWindowStyleMaskTitled:
        native.setStyleMask_(native.styleMask() | AppKit.NSWindowStyleMaskTitled)
    for kind in (
        AppKit.NSWindowCloseButton,
        AppKit.NSWindowMiniaturizeButton,
        AppKit.NSWindowZoomButton,
    ):
        button = native.standardWindowButton_(kind)
        if button is not None:
            button.setHidden_(False)
    print("[desktop] 红绿灯已恢复显示", flush=True)

    # 挂在**窗口框架视图**上，而不是 WebView 里 —— 它得盖在系统标题栏之上，
    # 那三个灰圆才遮得住真红绿灯（挂进 WebView 的话红绿灯在它上面，遮不住）。
    # 也因此**不能**再去改 WebView 的 frame：frameless 已经让内容铺满整个窗口了。
    theme_frame = content.superview() or content
    theme_bounds = theme_frame.bounds()
    band_y = 0 if theme_frame.isFlipped() else theme_bounds.size.height - TITLEBAR_HEIGHT
    band = _TitlebarBand.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, band_y, theme_bounds.size.width, TITLEBAR_HEIGHT)
    )
    # **必须是 layer-backed。** WebView 是 layer-backed 的，往它上面盖一个普通的 NSView，
    # 绘制与命中测试都不会按预期来 —— 上一版改动就栽在这一句上：拖动带加上了却收不到
    # 鼠标事件，于是窗口彻底拖不动（不报错，只是没反应）
    band.setWantsLayer_(True)
    # 窗口变宽时跟着变宽、始终贴着顶边（底部边距弹性 = 顶边固定）
    band.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin)
    theme_frame.addSubview_(band)

    # **读回来确认真的生效了。** AppKit 这类 setter 在最坏情况下是静默失败 —— 不报错、
    # 也不生效，比崩溃难查得多，所以不能只看「没抛异常」。这几项是判断成败的要点，
    # 尤其 `layer`：拖动带加上了却收不到鼠标事件，就栽在它不是 layer-backed 上
    frame = band.frame()
    print(
        f"[desktop] 标题栏已融入界面："
        f"fullSize={bool(native.styleMask() & full_size)} "
        f"transparent={bool(native.titlebarAppearsTransparent())} "
        f"band={frame.size.width:.0f}x{frame.size.height:.0f}@y{frame.origin.y:.0f} "
        f"layer={band.layer() is not None} "
        f"movable={bool(band.mouseDownCanMoveWindow())} "
        f"attached={bool(band.isDescendantOf_(theme_frame))}",
        flush=True,
    )


def _after_start(window: Any) -> None:
    """`webview.start` 的启动回调：窗口要等事件循环跑起来才真正存在。"""
    if sys.platform != "darwin":
        return
    if not _titlebar_wants_blend():
        print(f"[desktop] {TITLEBAR_ENV}=system，标题栏保持系统默认。", flush=True)
        return

    try:
        import AppKit  # noqa: F401 - 只是确认 pyobjc 在
        from PyObjCTools import AppHelper
    except ImportError:
        print("[desktop] 没有 pyobjc，标题栏保持系统默认。", flush=True)
        return

    native = _wait_for_native(window)
    if native is None:
        print("[desktop] 没拿到原生窗口句柄，标题栏保持系统默认。", flush=True)
        return

    # AppKit 的窗口操作**必须在主线程**：这个回调跑在 pywebview 自己的线程里，从那里
    # 直接调 NSWindow / NSView 的方法属于未定义行为 —— 实测是静默失败（不报错也不生效），
    # 比崩溃更难查
    AppHelper.callAfter(_blend_titlebar_into_ui, native)


def _mark_desktop(url: str) -> str:
    """给地址加上桌面壳的标记。

    - `desktop=1`：前端据此把界面从标题栏高度开始画（见 main.ts 与 App.vue）。
    - `t=`：一个时间戳，**每次启动都是新地址**。WKWebView 按地址走磁盘缓存，
      前端产物换了之后重启桌面壳仍然加载旧的那一份 —— 实测踩过：探针改动一直不生效，
      白花好几轮。桌面壳每次启动只加载一次，多个查询参数没有代价；另一个办法是启动时
      清 WebView 的缓存，那要动 WebKit 的 API，比这个脆弱得多。

    只加在自己打开的这个地址上 —— 复用已有实例时那个地址是别人的，改它没意义，
    也不该影响对方的窗口。
    """
    if not _titlebar_wants_blend():
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}desktop=1&t={int(time.time())}"


def _open_window(webview: Any, title: str, url: str) -> None:
    """开窗口并阻塞到它被关掉。"""
    # 桌面壳标记：前端据此把界面从标题栏高度开始画（见 `_mark_desktop`）
    url = _mark_desktop(url)
    window = webview.create_window(
        title,
        url,
        width=1280,
        # 高度别超过屏幕可用区：超了 macOS 会把窗口压小，而 WebView 的视口**不会**跟着
        # 更新，网页于是比窗口高出一截。留点余量给菜单栏与 Dock。
        height=800,
        min_size=(960, 640),
        # **关键：让 pywebview 在窗口创建时就设好 fullSizeContentView。**
        # 那个样式位在窗口显示之后再改是**不重建布局**的 —— 实测无论怎么改，
        # `contentLayoutRect` 始终是「窗口减标题栏」，内容区压根不含标题栏，
        # 于是 WebView 永远盖不到那一条（设多大都会被内容区拉回去）。
        # frameless 的代价是 pywebview 会顺手把红绿灯隐藏掉，在 `_blend_titlebar_into_ui`
        # 里补回来 —— 这样用的还是系统那三个按钮，不是自绘。
        #
        # **只在 macOS 上开**：别的平台没有那套 AppKit 逻辑，用了只会变成真的无边框窗口
        # （没有标题栏、也拖不动）。
        frameless=sys.platform == "darwin" and _titlebar_wants_blend(),
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
    # _after_start 跑在 pywebview 自己的线程里：窗口要等事件循环起来才真正存在，
    # 标题栏那套 AppKit 改动得等那一刻才能做（见那里）
    webview.start(_after_start, args=(window,), debug=debug)


def _pin_home() -> None:
    """把数据根定下来。**必须在第一次 `get_settings()` 之前调用。**

    壳必须自己指定数据根，不能指望 cwd —— 从访达双击启动时 cwd 是 `/`，而
    `config._default_home` 的三条优先级里，第 2 条「就地运行」要求 cwd 是个源码 / 发布
    目录，于是只剩下第 3 条的平台目录。后果是：数据明明在项目目录里，壳却去
    `~/Library/Application Support/Quill` 翻一个空目录 —— 打开 App 之后提示词库、
    API 设置、会话**全是空的**（数据没丢，只是读错了地方）。

    顺序：
    1. 环境里已经有 `QUILL_HOME` → 一个字都不动（用户显式指定，或测试实例自己设的）；
    2. 从解释器位置往上找得到 `pyproject.toml` → 就用那个目录。这覆盖「打包产物放在
       源码目录里跑」（`release/Quill.app`）这类用法 —— 也正是上面那个故障的场景；
    3. 找不到就交给 `config` 自己判断（平台数据目录），那才是安装到 /Applications 之后
       该有的落点。
    """
    if os.environ.get(HOME_ENV, "").strip():
        return

    for parent in list(Path(sys.executable).resolve().parents)[:8]:
        if (parent / "pyproject.toml").is_file():
            os.environ[HOME_ENV] = str(parent)
            return


def main() -> int:
    """桌面壳入口。返回进程退出码。"""
    # 必须在 `_redirect_output_to_log()` 之前 —— 那个函数也要用数据根（日志写在它下面）
    _pin_home()
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
