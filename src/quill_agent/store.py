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

from quill_agent.models import (
    Mode,
    ModelConfig,
    PromptGroup,
    Protocol,
    SkillGroup,
    ToolGroup,
)

T = TypeVar("T")


def read_json(path: Path, default: T) -> T:
    """读一个 JSON 文件。文件不存在、内容为空、读取失败、JSON 损坏，一律返回 default。

    这段「防御式读取」原先在六个存储类里各抄了一遍，而且容错策略并不一致：
    `preferences.json` / `memory.json` 会静默退回默认值，`models.json` 则把
    异常抛到界面上（打不开页面）。

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
        context_windows: dict[str, int] | None = None,
    ) -> ModelConfig:
        """新增一条配置，id 由本方法生成并返回。"""
        item = ModelConfig(
            id=uuid4().hex,
            name=name,
            models=list(models),
            base_url=base_url,
            protocol=protocol,
            api_key=api_key,
            context_windows=dict(context_windows or {}),
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


class ToolGroupStore:
    """工具组的持久化。结构与 ModelStore 一致（一列对象、按 id 增删改）。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def list(self) -> list[ToolGroup]:
        """读取全部工具组；文件缺失或损坏时返回空列表（见 read_json）。"""
        raw = read_json(self._path, [])
        if not isinstance(raw, list):
            return []

        return [ToolGroup.model_validate(item) for item in raw]

    def get(self, group_id: str) -> ToolGroup | None:
        """按 id 查找，找不到返回 None。"""
        return next((item for item in self.list() if item.id == group_id), None)

    def find_by_name(self, name: str) -> ToolGroup | None:
        """按组名查找 —— 组名对用户是主要标识，用它判断重名。"""
        return next((item for item in self.list() if item.name == name), None)

    def add(
        self,
        *,
        name: str,
        description: str,
        tools: list[str],
        confirm: list[str] | None = None,
    ) -> ToolGroup:
        """新增一个工具组，id 由本方法生成并返回。

        Raises:
            ValueError: 组名已被占用。重名会让用户在模式编辑里分不清
                两个同名的组各自是什么，所以在这里拦下而不是放任。
        """
        if self.find_by_name(name) is not None:
            raise ValueError(f"已有叫「{name}」的工具组，请换一个名字。")

        item = ToolGroup(
            id=uuid4().hex,
            name=name,
            description=description,
            tools=list(tools),
            confirm=list(confirm or []),
        )
        items = self.list()
        items.append(item)
        self._save_all(items)
        return item

    def update(self, item: ToolGroup) -> None:
        """按 id 覆盖更新；id 不存在时静默忽略（与 ModelStore 同一口径）。

        组名冲突在这里拦：改成与**别的组**相同的名字同样会让人分不清。
        """
        existing = self.get(item.id)
        if existing is None:
            return

        clash = self.find_by_name(item.name)
        if clash is not None and clash.id != item.id:
            raise ValueError(f"已有叫「{item.name}」的工具组，请换一个名字。")

        items = [group if group.id != item.id else item for group in self.list()]
        self._save_all(items)

    def remove(self, group_id: str) -> None:
        """按 id 删除；id 不存在时静默忽略。"""
        self._save_all([group for group in self.list() if group.id != group_id])

    def _save_all(self, items: list[ToolGroup]) -> None:
        """整体覆写。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [item.model_dump() for item in items]
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class SkillGroupStore:
    """技能组的持久化。结构与 ToolGroupStore 完全一致（一列对象、按 id 增删改）。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def list(self) -> list[SkillGroup]:
        """读取全部技能组；文件缺失或损坏时返回空列表（见 read_json）。"""
        raw = read_json(self._path, [])
        if not isinstance(raw, list):
            return []

        return [SkillGroup.model_validate(item) for item in raw]

    def get(self, group_id: str) -> SkillGroup | None:
        """按 id 查找，找不到返回 None。"""
        return next((item for item in self.list() if item.id == group_id), None)

    def find_by_name(self, name: str) -> SkillGroup | None:
        """按组名查找 —— 组名对用户是主要标识，用它判断重名。"""
        return next((item for item in self.list() if item.name == name), None)

    def add(self, *, name: str, description: str, skills: list[str]) -> SkillGroup:
        """新增一个技能组，id 由本方法生成并返回。

        Raises:
            ValueError: 组名已被占用。
        """
        if self.find_by_name(name) is not None:
            raise ValueError(f"已有叫「{name}」的技能组，请换一个名字。")

        item = SkillGroup(id=uuid4().hex, name=name, description=description, skills=list(skills))
        items = self.list()
        items.append(item)
        self._save_all(items)
        return item

    def update(self, item: SkillGroup) -> None:
        """按 id 覆盖更新；id 不存在时静默忽略。组名冲突在这里拦。"""
        if self.get(item.id) is None:
            return

        clash = self.find_by_name(item.name)
        if clash is not None and clash.id != item.id:
            raise ValueError(f"已有叫「{item.name}」的技能组，请换一个名字。")

        items = [group if group.id != item.id else item for group in self.list()]
        self._save_all(items)

    def remove(self, group_id: str) -> None:
        """按 id 删除；id 不存在时静默忽略。"""
        self._save_all([group for group in self.list() if group.id != group_id])

    def _save_all(self, items: list[SkillGroup]) -> None:
        """整体覆写。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [item.model_dump() for item in items]
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class PromptGroupStore:
    """提示词组的持久化（原先是「模式」，模式现在指四类组的组合）。

    结构与 ModelStore 完全一致，只是存的数据类型不同。
    两者本可以抽象成一个泛型基类，这里为了直白先各自实现一份。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def list(self) -> list[PromptGroup]:
        """读取全部提示词组；文件缺失或损坏时返回空列表（见 read_json）。"""
        raw = read_json(self._path, [])
        if not isinstance(raw, list):
            return []

        return [PromptGroup.model_validate(item) for item in raw]

    def get(self, group_id: str) -> PromptGroup | None:
        """按 id 查找，找不到返回 None。"""
        return next((item for item in self.list() if item.id == group_id), None)

    def find_by_name(self, name: str) -> PromptGroup | None:
        """按组名查找 —— 组名对用户是主要标识，用它判断重名。"""
        return next((item for item in self.list() if item.name == name), None)

    def add(self, *, name: str, description: str = "", settings: dict[str, str]) -> PromptGroup:
        """新增一个提示词组，id 由本方法生成并返回。

        Raises:
            ValueError: 组名已被占用。
        """
        if self.find_by_name(name) is not None:
            raise ValueError(f"已有叫「{name}」的提示词组，请换一个名字。")

        item = PromptGroup(
            id=uuid4().hex, name=name, description=description, settings=dict(settings)
        )
        items = self.list()
        items.append(item)
        self._save_all(items)
        return item

    def update(self, item: PromptGroup) -> None:
        """按 id 覆盖更新；id 不存在时静默忽略。组名冲突在这里拦。"""
        if self.get(item.id) is None:
            return

        clash = self.find_by_name(item.name)
        if clash is not None and clash.id != item.id:
            raise ValueError(f"已有叫「{item.name}」的提示词组，请换一个名字。")

        self._save_all([group if group.id != item.id else item for group in self.list()])

    def remove(self, group_id: str) -> None:
        """按 id 删除；id 不存在时静默忽略。"""
        self._save_all([item for item in self.list() if item.id != group_id])

    def _save_all(self, items: list[PromptGroup]) -> None:
        """整体覆写。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [item.model_dump() for item in items]
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class ModeStore:
    """模式的持久化。

    结构和另外几个 store 一致，只是存的是 `Mode` —— 模式引用四类组，
    所以这里不做「成员是否存在」的校验（组可以为空，那是合法配置）。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def list(self) -> list[Mode]:
        """读取全部模式；文件缺失或损坏时返回空列表（见 read_json）。"""
        raw = read_json(self._path, [])
        if not isinstance(raw, list):
            return []

        return [Mode.model_validate(item) for item in raw]

    def get(self, mode_id: str) -> Mode | None:
        """按 id 查找，找不到返回 None。"""
        return next((item for item in self.list() if item.id == mode_id), None)

    def find_by_name(self, name: str) -> Mode | None:
        """按模式名查找 —— 用它拦重名。"""
        return next((item for item in self.list() if item.name == name), None)

    def add(self, mode: Mode) -> Mode:
        """新增一个模式（id 由调用方生成或已存在均可）。

        Raises:
            ValueError: 模式名已被占用。
        """
        if self.find_by_name(mode.name) is not None:
            raise ValueError(f"已有叫「{mode.name}」的模式，请换一个名字。")

        items = self.list()
        items.append(mode)
        self._save_all(items)
        return mode

    def update(self, mode: Mode) -> None:
        """按 id 覆盖更新；id 不存在时静默忽略。重名在这里拦。"""
        if self.get(mode.id) is None:
            return

        clash = self.find_by_name(mode.name)
        if clash is not None and clash.id != mode.id:
            raise ValueError(f"已有叫「{mode.name}」的模式，请换一个名字。")

        self._save_all([item if item.id != mode.id else mode for item in self.list()])

    def remove(self, mode_id: str) -> None:
        """按 id 删除；id 不存在时静默忽略。"""
        self._save_all([item for item in self.list() if item.id != mode_id])

    def _save_all(self, items: list[Mode]) -> None:
        """整体覆写。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [item.model_dump() for item in items]
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def migrate_prompt_groups(legacy: Path, target: Path) -> bool:
    """把旧版存在 `legacy` 里的提示词组挪到 `target`。

    历史：提示词组原先就叫「模式」，存在 `data/modes.json`。模式现在指四类组的
    组合，那个文件名归了新模式 —— 两边共用一个文件的话，新模式一写入就会把
    用户已经建好的提示词组**整份覆盖掉**。所以这里搬一次。

    只在这两个条件同时成立时动手：目标文件还不存在、且旧文件里装的**确实是
    提示词组**（有 `settings`、没有模式独有的字段）。后者是关键 ——
    否则升级后旧文件里已经是新模式的数据，再搬一次就把模式搬没了。

    Returns:
        是否真的搬了；重复调用无副作用。
    """
    if target.exists() or not legacy.exists():
        return False

    raw = read_json(legacy, [])
    if not isinstance(raw, list) or not raw:
        return False

    looks_like_prompt_group = all(
        isinstance(item, dict) and "settings" in item and "prompt_group_id" not in item
        for item in raw
    )
    if not looks_like_prompt_group:
        return False

    target.parent.mkdir(parents=True, exist_ok=True)
    legacy.rename(target)
    return True
