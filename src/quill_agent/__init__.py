"""quill_agent 包：项目底层能力，与 UI 完全解耦。

分层约定（自上而下单向依赖）：
    UI 层   app/        -> 只做页面渲染和交互
    业务层  quill_agent    -> 纯 Python 逻辑，可被 UI / CLI / 脚本任意复用

即：这里的代码永远不要 import 界面层的东西（HTTP 框架、前端、终端 UI），
保证底层可以脱离界面独立测试。
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("quill-agent")
except PackageNotFoundError:  # 未安装（例如直接以源码方式运行）时的兜底
    __version__ = "0.1.0"

__all__ = ["__version__"]
