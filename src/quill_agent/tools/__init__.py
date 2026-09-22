"""工具层。

对外只需要用到 `registry`：

    registry.all()                    # 全部工具
    registry.search(keyword, category) # 按名称 / 分类筛选
    registry.schemas()                 # 转成 API 的 tools 参数
    registry.execute(name, args)       # 执行
"""

# 导入这些模块会触发工具的注册（装饰器在模块加载时执行）
from quill_agent.tools import (  # noqa: F401
    builtin,
    files,
    git,
    shell,
    subagent,
    symbols,
    todo,
    web,
)
from quill_agent.tools.base import ToolKind, ToolRegistry, ToolSpec, registry

__all__ = ["ToolKind", "ToolRegistry", "ToolSpec", "registry"]
