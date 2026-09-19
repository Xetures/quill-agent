"""工具层的核心抽象：声明（ToolSpec）+ 注册表（ToolRegistry）。

设计要点：
    1. 工具的实现是普通 Python 函数 —— 不依赖 LLM SDK，也不依赖界面，
       可以脱离 Agent 单独测试；
    2. 注册表只负责「元数据 + 路由」，不知道任何具体工具怎么实现；
    3. 工具定义来自代码；给不给由模式的工具组决定（见 README 3.6 / 3.8），
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

    目前只有「本地函数」一种：注册表里存的就是可调用的 Python 函数。
    将来接 MCP 或远程工具时在这里加成员，注册表和页面都不用改结构 ——
    但没有实际实现之前不预置占位成员：一个永远取不到的枚举值，只会让读的人
    以为「远程工具这条路已经通了」。
    """

    LOCAL = "local"

    @property
    def label(self) -> str:
        """界面展示用的名称。"""
        return {ToolKind.LOCAL: "本地"}[self]


class ToolSpec(BaseModel):
    """一个工具的声明：给模型看的信息 + 执行所需的路由信息。

    Attributes:
        name: 唯一标识，对本地工具来说同时也是函数名。
        description: 何时使用该工具 —— 模型判断的唯一依据。
        category: 分类，只用于界面筛选，不会发给模型。
        parameters: JSON Schema 格式的入参定义。
        kind: 实现形态；见 ToolKind。
    """

    name: str = Field(min_length=1, description="工具名")
    description: str = Field(min_length=1, description="功能说明")
    category: str = Field(default="未分类", description="分类")
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

    def tool(
        self,
        *,
        description: str,
        category: str = "未分类",
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
