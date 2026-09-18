"""内置工具。

这里是「不属于某个具体领域」的通用工具的落脚点。
新增工具只需写一个普通 Python 函数 + 加 @registry.tool 装饰器，
「工具」页面上的列表和分类筛选会自动出现它，不需要改任何界面代码。

和文件系统强相关的工具（读写、搜索、删除）放在 files.py。

当前为空 —— 原来的「获取当前时间」已移除：每轮请求的运行时上下文里
本来就带着当前时间（见 agent.build_user_message），再给一个工具是重复的。
工具列表越长，模型选错工具的概率越高，这类残留会拖累整体准确率。
"""

from __future__ import annotations

from quill_agent.tools.base import registry  # noqa: F401  后续加工具时直接用
