"""持久化层：把配置类数据存到本地 JSON 文件。

接口刻意做得很薄（list / get / add / update / remove），
后续想换成 SQLite 或远端 API 时，只要替换本文件实现即可，
上层页面代码无需改动。

这里还放着整个持久化层共用的 `read_json()` —— 每个 store 都要做一遍
「文件可能不存在、可能是空的、可能被用户手滑改坏」的防御式读取。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from quill_agent.models import ModelConfig, PromptMode, Protocol

T = TypeVar("T")


def read_json(path: Path, default: T) -> T:
    """读一个 JSON 文件。文件不存在、内容为空、读取失败、JSON 损坏，一律返回 default。

    这段「防御式读取」原先在六个存储类里各抄了一遍，而且容错策略并不一致：
    `preferences.json` / `memory.json` 会静默退回默认值，`models.json` /
    `tools.json` 则把异常抛到界面上（打不开页面）。

    统一成前者：用户会直接编辑这些文件（手滑写坏一个括号是常事），
    为了一个坏文件让整个应用起不来，代价太大。

    JSON 损坏时会把坏文件改名成 `xxx.corrupt` 再退回默认值 —— 不能只是忽略，
    否则下一次保存就把用户原来的数据彻底盖掉了。

    Args:
        path: JSON 文件路径。
        default: 退回时返回的值；会**深拷贝**一份再返回，避免调用方拿到
            同一个可变对象互相污染。
    """
    content = ""
    try:
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
    except OSError:
        content = ""  # 读不了就当空文件处理，和「不存在」一个待遇

    if content:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            try:
                path.replace(path.with_name(f"{path.name}.corrupt"))
            except OSError:
                pass  # 改名也失败就算了，至少别让读取这一步把应用带崩

    return copy.deepcopy(default)


class ModelStore:
    """基于单个 JSON 文件的轻量存储。

    Args:
        path: JSON 文件路径，父目录会在首次写入时自动创建。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def list(self) -> list[ModelConfig]:
        """读取全部配置；文件缺失或损坏时返回空列表（见 read_json）。"""
        raw = read_json(self._path, [])
        if not isinstance(raw, list):
            return []

        return [ModelConfig.model_validate(item) for item in raw]

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
        """读取全部开关状态；文件缺失或损坏时返回空字典（见 read_json）。"""
        raw = read_json(self._path, {})
        if not isinstance(raw, dict):
            return {}

        return {str(name): bool(enabled) for name, enabled in raw.items()}

    def set(self, name: str, enabled: bool) -> None:
        """写入单个工具的开关状态（其它工具的状态保持不变）。"""
        states = self.load()
        states[name] = enabled
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(states, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class SkillStateStore:
    """技能开关状态的持久化：{技能名: 是否启用}。

    结构与 ToolStateStore 完全一致（都是「名字 -> 布尔」）。这里没有再往上抽
    一层基类：两个用法的差异（工具 / 技能）比共性更值得在代码里直白地摆着，
    等真的出现第三个同类需求时再抽象也不迟。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> dict[str, bool]:
        """读取全部开关状态；文件缺失或损坏时返回空字典（见 read_json）。

        调用方约定：字典里没有的键 = 该技能还没被改过。取值请用
        `skill_enabled()`，别自己写默认值。
        """
        raw = read_json(self._path, {})
        if not isinstance(raw, dict):
            return {}

        return {str(name): bool(enabled) for name, enabled in raw.items()}

    def set(self, name: str, enabled: bool) -> None:
        """写入单个技能的开关状态（其它技能保持不变）。"""
        states = self.load()
        states[name] = enabled
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(states, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def skill_enabled(states: dict[str, bool], name: str) -> bool:
    """某个技能是否启用（`states` 是 `SkillStateStore.load()` 的结果）。

    字典里没有这个名字 = 用户从没动过它的开关，按默认值处理。默认是**启用**：
    技能是用户主动放进 skills/ 的，不特意关掉就该生效。

    这条规则集中在这里，是因为它原先在四个调用点各写了一遍 `True` ——
    想改成「默认停用」时必然漏掉某一处，然后出现「模型说没启用、
    界面说已启用」这种自相矛盾。
    """
    return states.get(name, True)


class PromptModeStore:
    """提示词模式的持久化。

    结构与 ModelStore 完全一致，只是存的数据类型不同。
    两者本可以抽象成一个泛型基类，这里为了直白先各自实现一份。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def list(self) -> list[PromptMode]:
        """读取全部模式；文件缺失或损坏时返回空列表（见 read_json）。"""
        raw = read_json(self._path, [])
        if not isinstance(raw, list):
            return []

        return [PromptMode.model_validate(item) for item in raw]

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
