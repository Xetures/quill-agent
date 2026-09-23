"""工具层的核心抽象：声明（ToolSpec）+ 注册表（ToolRegistry）。

设计要点：
    1. 工具的实现是普通 Python 函数 —— 不依赖 LLM SDK，也不依赖界面，
       可以脱离 Agent 单独测试；
    2. 注册表只负责「元数据 + 路由」，不知道任何具体工具怎么实现；
    3. 工具定义来自代码；给不给由模式的工具组决定（见 README-developer.md 3.6 / 3.8），
       这里不再有全局开关 —— 那会变成「改了却不生效」的死控件。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Container
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ToolKind(str, Enum):
    """工具的实现形态。

    `LOCAL` 是注册表里存着一个可调用的 Python 函数；`MCP` 是「调用要转发给某个 MCP
    服务器」—— 对注册表来说两者没区别（都是往 `_funcs` 里放一个函数），区别只在
    来源，界面拿它做提示和筛选。
    """

    LOCAL = "local"
    MCP = "mcp"

    @property
    def label(self) -> str:
        """界面展示用的名称。"""
        return {ToolKind.LOCAL: "本地", ToolKind.MCP: "MCP"}[self]


class ToolSpec(BaseModel):
    """一个工具的声明：给模型看的信息 + 执行所需的路由信息。

    Attributes:
        name: 唯一标识，对本地工具来说同时也是函数名。
        description: 何时使用该工具 —— 模型判断的唯一依据。
        category: 分类的**稳定 key**（`files` / `shell` / `code`…），只用于界面筛选，不发给
            模型。**这里不写中文**：它是界面文案，得跟着界面语言走（界面侧的翻译见
            `web/src/utils/tool-category.ts`）。MCP 工具用 `mcp:<服务器名>` 这种带前缀的形式。
        parameters: JSON Schema 格式的入参定义。
        kind: 实现形态；见 ToolKind。
    """

    name: str = Field(min_length=1, description="工具名")
    description: str = Field(min_length=1, description="功能说明")
    category: str = Field(default="other", description="分类 key（见类文档）")
    parameters: dict[str, Any] = Field(default_factory=dict, description="入参 JSON Schema")
    kind: ToolKind = Field(default=ToolKind.LOCAL, description="实现形态")

    def to_api_schema(self) -> dict[str, Any]:
        """转成模型 API 里 tools 参数需要的格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册表。

    元数据存在内存里（由代码注册）。给不给某个工具由模式的工具组决定，
    这里只负责「有哪些工具、怎么转成 API 格式、怎么执行」。
    """

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._funcs: dict[str, Callable[..., str]] = {}
        #: 外部来源（MCP 服务器 id）→ 它注册了哪些工具名。注销时整批摘掉用。
        self._sources: dict[str, list[str]] = {}

    def tool(
        self,
        *,
        description: str,
        category: str = "other",
        parameters: dict[str, Any] | None = None,
        required: list[str] | None = None,
    ) -> Callable[[Callable[..., str]], Callable[..., str]]:
        """装饰器：把一个本地函数注册成工具。

        工具名直接取函数名，省掉两处名字对不上的麻烦。
        """

        def decorator(func: Callable[..., str]) -> Callable[..., str]:
            schema: dict[str, Any] = dict(parameters) if parameters else {}
            schema.setdefault("type", "object")
            schema.setdefault("properties", {})
            if required:
                schema["required"] = required

            spec = ToolSpec(
                name=func.__name__,
                description=description,
                category=category,
                parameters=schema,
                kind=ToolKind.LOCAL,
            )
            self._specs[spec.name] = spec
            self._funcs[spec.name] = func
            return func

        return decorator

    def register_external(
        self,
        specs: list[tuple[ToolSpec, Callable[..., str]]],
        *,
        source: str,
    ) -> list[str]:
        """运行时注册一批外部工具（MCP），返回实际注册成功的名称。

        和 `tool` 装饰器的差别只有时机：那些在 import 时静态注册，这些要等连上服务器
        才知道有什么。

        Args:
            specs: (声明, 调用函数) 列表。
            source: 来源标识（服务器 id）。**按来源整批注销**，而不是逐个名字摘 ——
                服务器重连之后工具集可能变了，逐个摘会留下已不存在的幽灵工具。
        """
        self.unregister_source(source)

        names: list[str] = []
        for spec, func in specs:
            # 任何已有名称都不能覆盖：它可能属于内置工具，也可能属于另一个 MCP 来源。
            # 后者尤其危险，注销覆盖方时会把被覆盖方的路由一起删掉。
            if spec.name in self._specs:
                continue
            self._specs[spec.name] = spec
            self._funcs[spec.name] = func
            names.append(spec.name)

        self._sources[source] = names
        return names

    def unregister_source(self, source: str) -> None:
        """摘掉某个来源注册过的全部工具；没注册过就什么都不做。

        内置工具**不在任何来源之下**，所以这个方法动不到它们。
        """
        for name in self._sources.pop(source, []):
            self._specs.pop(name, None)
            self._funcs.pop(name, None)

    def sources(self) -> list[str]:
        """当前挂着的外部来源 id 列表。

        调用方拿它做「这次还有谁在」的比对：服务器被删掉或停用之后，管理器里已经没有
        它了，但注册表里还挂着它上次注册的那批工具 —— 不主动摘，那些工具会一直占着
        上下文，模型也还会去调（然后收到一句「不可用」）。
        """
        return list(self._sources)

    def all(self) -> list[ToolSpec]:
        """全部工具（按注册顺序）。"""
        return list(self._specs.values())

    def search(self, keyword: str = "", category: str = "") -> list[ToolSpec]:
        """按名称模糊 + 分类精确联合过滤；空条件表示不限制。"""
        items = self.all()

        keyword = keyword.strip()
        if keyword:
            lowered = keyword.lower()
            items = [item for item in items if lowered in item.name.lower()]

        if category:
            items = [item for item in items if item.category == category]

        return items

    def categories(self) -> list[str]:
        """现有工具涉及的全部分类，供筛选下拉使用。"""
        return sorted({spec.category for spec in self._specs.values()})

    def schemas(self, only: Container[str] | None = None) -> list[dict[str, Any]]:
        """转成 API 的 tools 参数。

        Args:
            only: 只要这几个名字的工具（模式的工具组给什么就发什么）。
                None 表示全部。

        不在名单里的工具不会被发给模型，模型也就不会请求调用它们。
        """
        items = self.all()
        if only is not None:
            items = [spec for spec in items if spec.name in only]

        return [spec.to_api_schema() for spec in items]

    def execute(self, name: str, arguments: str | dict[str, Any]) -> str:
        """执行工具并返回文本结果。

        按接口契约：任何异常都转成可读文本返回、不向上抛 ——
        模型看到错误说明后通常能自行修正。
        """
        func = self._funcs.get(name)
        if func is None:
            return f"未知工具：{name}"

        try:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError as exc:
            return f"参数不是合法的 JSON：{exc}"

        if not isinstance(args, dict):
            return "参数必须是 JSON 对象。"

        try:
            return func(**args)
        except TypeError as exc:
            return f"参数不正确：{exc}"
        except Exception as exc:  # 工具内部出错也转成文本，不要炸掉 Agent
            return f"执行失败：{exc}"


# 全局注册表：内置工具在 quill_agent.tools.builtin 里注册进来
registry = ToolRegistry()
