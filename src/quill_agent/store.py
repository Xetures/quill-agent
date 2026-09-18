"""持久化层：把 ModelConfig 存到本地 JSON 文件。

接口刻意做得很薄（list / get / add / update / remove），
后续想换成 SQLite 或远端 API 时，只要替换本文件实现即可，
上层页面代码无需改动。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

from quill_agent.models import ModelConfig, PromptMode, Protocol


class ModelStore:
    """基于单个 JSON 文件的轻量存储。

    Args:
        path: JSON 文件路径，父目录会在首次写入时自动创建。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def list(self) -> list[ModelConfig]:
        """读取全部配置；文件不存在时返回空列表。"""
        if not self._path.exists():
            return []

        content = self._path.read_text(encoding="utf-8").strip()
        if not content:
            return []

        raw_items: Iterator[dict] = iter(json.loads(content))
        return [ModelConfig.model_validate(item) for item in raw_items]

    def get(self, model_id: str) -> ModelConfig | None:
        """按 id 查找，找不到返回 None。"""
        return next((item for item in self.list() if item.id == model_id), None)

    def add(
        self,
        *,
        name: str,
        models: list[str],
        base_url: str = "",
        api_key: str = "",
        protocol: Protocol = Protocol.OPENAI,
    ) -> ModelConfig:
        """新增一条配置，id 由本方法生成并返回。"""
        item = ModelConfig(
            id=uuid4().hex,
            name=name,
            models=list(models),
            base_url=base_url,
            protocol=protocol,
            api_key=api_key,
        )
        items = self.list()
        items.append(item)
        self._save_all(items)
        return item

    def update(self, item: ModelConfig) -> None:
        """按 id 覆盖更新；id 不存在时静默忽略。"""
        items = [existing if existing.id != item.id else item for existing in self.list()]
        self._save_all(items)

    def remove(self, model_id: str) -> None:
        """按 id 删除；id 不存在时静默忽略。"""
        self._save_all([item for item in self.list() if item.id != model_id])

    def _save_all(self, items: list[ModelConfig]) -> None:
        """整体覆写：学习阶段数据量很小，简单可靠优先。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [item.model_dump() for item in items]
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class ToolStateStore:
    """工具开关状态的持久化：{工具名: 是否启用}。

    只存状态，不存工具定义 —— 定义由代码负责（避免两份真相互相打架）。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> dict[str, bool]:
        """读取全部开关状态；文件不存在时返回空字典。"""
        if not self._path.exists():
            return {}

        content = self._path.read_text(encoding="utf-8").strip()
        if not content:
            return {}

        return {str(name): bool(enabled) for name, enabled in json.loads(content).items()}

    def set(self, name: str, enabled: bool) -> None:
        """写入单个工具的开关状态（其它工具的状态保持不变）。"""
        states = self.load()
        states[name] = enabled
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(states, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class PromptModeStore:
    """提示词模式的持久化。

    结构与 ModelStore 完全一致，只是存的数据类型不同。
    两者本可以抽象成一个泛型基类，这里为了直白先各自实现一份。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def list(self) -> list[PromptMode]:
        """读取全部模式；文件不存在时返回空列表。"""
        if not self._path.exists():
            return []

        content = self._path.read_text(encoding="utf-8").strip()
        if not content:
            return []

        return [PromptMode.model_validate(item) for item in json.loads(content)]

    def get(self, mode_id: str) -> PromptMode | None:
        """按 id 查找，找不到返回 None。"""
        return next((item for item in self.list() if item.id == mode_id), None)

    def add(
        self,
        *,
        name: str,
        settings: dict[str, str],
        preferred_model: str = "",
    ) -> PromptMode:
        """新增一个模式，id 由本方法生成并返回。"""
        item = PromptMode(
            id=uuid4().hex,
            name=name,
            settings=dict(settings),
            preferred_model=preferred_model,
        )
        items = self.list()
        items.append(item)
        self._save_all(items)
        return item

    def update(self, item: PromptMode) -> None:
        """按 id 覆盖更新；id 不存在时静默忽略。"""
        items = [existing if existing.id != item.id else item for existing in self.list()]
        self._save_all(items)

    def remove(self, mode_id: str) -> None:
        """按 id 删除；id 不存在时静默忽略。"""
        self._save_all([item for item in self.list() if item.id != mode_id])

    def _save_all(self, items: list[PromptMode]) -> None:
        """整体覆写。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [item.model_dump() for item in items]
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
