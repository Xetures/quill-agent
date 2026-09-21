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
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import ValidationError

from quill_agent.locking import atomic_write_text, file_lock
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


def load_items(path: Path, model: type[T]) -> list[T]:
    """读一列对象，逐条校验；不合法的**那一条**跳过，其余照常返回。

    为什么不能整体校验：`read_json` 只兜得住「JSON 本身坏了」，兜不住「JSON 合法、
    但字段不合法」。用户手工编辑 `models.json` 时把某个 `api_key`（或窗口）写错，
    一条记录就能让 `list()` 抛异常 —— 模型页、任务页、用量页一起打不开。
    这和 `read_json` 那句「为了一个坏文件让整个应用起不来，代价太大」是同一个
    道理，只是粒度更细：坏到一条就只丢一条。

    `MemoryStore.list` 从一开始就是这么做的，这里把它抽出来给其余存储共用
    （原先五个 Store 各写一遍 list，行为却不一致 —— 只有记忆那一处是稳的）。
    """
    raw = read_json(path, [])
    if not isinstance(raw, list):
        return []

    items: list[T] = []
    for entry in raw:
        try:
            items.append(model.model_validate(entry))
        except ValidationError:
            continue  # 坏的那条跳过，其余照常读出

    return items


def reject_if_builtin(items: list[Any], item_id: str, label: str) -> None:
    """要删的这个是不是出厂资源？是就拒绝（各 store 的 `remove` 共用）。

    藏在存储层而不是只靠界面把删除按钮藏起来：界面是「不提供入口」，这里是「不给删」。
    少了这一层，一次误调接口（或者别的代码路径）就能把内置资源删掉，而用户下次启动
    才会发现模式里引用的组不见了。

    它**只管删除**：出厂资源是可以改的（改措辞、调描述都是正当需求），只是不能删 ——
    删掉之后「开箱即用」就永久少一块，而出厂的那个模式正引用着这些组和提示词。

    Raises:
        ValueError: 该 id 存在且 `builtin=True`。文案直接给用户看。
    """
    target = next((item for item in items if item.id == item_id), None)
    if target is not None and getattr(target, "builtin", False):
        raise ValueError(f"「{target.name}」是内置{label}，不能删除。")


def sync_builtin(store: Any, builtin_items: list[Any]) -> tuple[list[str], list[str]]:
    """把出厂条目补进一个 store，返回 `(新增的名字, 修回标记的名字)`。

    为什么不用 `store.add()`：它按**组名**查重，内置项的名字和用户自建项撞上时会
    直接抛错 —— 而这里要做的是「确保它存在」，不该因为一个重名就整体放弃。
    所以走一次整体的「读 → 改 → 写」（持锁），和 `_save_all` 的其它调用方一样。

    两件事：

        1. **缺失就补上** —— 新装一份应用时，这些条目一个都还没有；
        2. **标记被抹掉就改回来** —— 少了 `builtin`，一条出厂资源就变成「用户可以
           删掉」的了，而那正好是这个字段要防的事。别的字段一律不动：内容与描述
           都可能被用户改过，那是他的东西。

    顺序保持原样、新增的追加在末尾，这样界面上内置项的位置是稳定的。

    Returns:
        两个名字列表。都为空表示什么都没做（也就不落盘）。
    """
    with file_lock(store._path):  # noqa: SLF001 —— 同一模块内，这些 store 的实现就在上面
        items = store.list()
        positions = {item.id: index for index, item in enumerate(items)}
        added: list[str] = []
        repaired: list[str] = []

        for item in builtin_items:
            position = positions.get(item.id)
            if position is None:
                positions[item.id] = len(items)
                items.append(item)
                added.append(item.name)
            elif not items[position].builtin:
                items[position] = items[position].model_copy(update={"builtin": True})
                repaired.append(item.name)

        if added or repaired:
            store._save_all(items)  # noqa: SLF001

    return added, repaired


class ModelStore:
    """基于单个 JSON 文件的轻量存储。

    Args:
        path: JSON 文件路径，父目录会在首次写入时自动创建。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def list(self) -> list[ModelConfig]:
        """读取全部配置；文件缺失或损坏时返回空列表，单条不合法则跳过（见 load_items）。"""
        return load_items(self._path, ModelConfig)

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
        with file_lock(self._path):
            self._save_all([*self.list(), item])

        return item

    def update(self, item: ModelConfig) -> None:
        """按 id 覆盖更新；id 不存在时静默忽略。"""
        with file_lock(self._path):
            self._save_all(
                [existing if existing.id != item.id else item for existing in self.list()]
            )

    def remove(self, model_id: str) -> None:
        """按 id 删除；id 不存在时静默忽略。"""
        with file_lock(self._path):
            self._save_all([item for item in self.list() if item.id != model_id])

    def _save_all(self, items: list[ModelConfig]) -> None:
        """整体覆写：学习阶段数据量很小，简单可靠优先。

        必须由调用方持锁进入（见 `file_lock`），并且用原子替换落盘 ——
        否则另一个进程要么覆盖掉这次写入，要么读到一个写了一半的文件。
        """
        atomic_write_text(
            self._path,
            json.dumps([item.model_dump() for item in items], ensure_ascii=False, indent=2),
        )


class ToolGroupStore:
    """工具组的持久化。结构与 ModelStore 一致（一列对象、按 id 增删改）。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def list(self) -> list[ToolGroup]:
        """读取全部工具组；文件缺失或损坏时返回空列表，单条不合法则跳过（见 load_items）。"""
        return load_items(self._path, ToolGroup)

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
        item = ToolGroup(
            id=uuid4().hex,
            name=name,
            description=description,
            tools=list(tools),
            confirm=list(confirm or []),
        )

        # 重名校验必须在锁内、对着同一份快照判：在锁外先查再进锁写，
        # 两个进程会同时查到「不重名」，然后各写各的
        with file_lock(self._path):
            items = self.list()
            if any(group.name == name for group in items):
                raise ValueError(f"已有叫「{name}」的工具组，请换一个名字。")

            self._save_all([*items, item])

        return item

    def update(self, item: ToolGroup) -> None:
        """按 id 覆盖更新；id 不存在时静默忽略（与 ModelStore 同一口径）。

        组名冲突在这里拦：改成与**别的组**相同的名字同样会让人分不清。
        """
        with file_lock(self._path):
            items = self.list()
            if not any(group.id == item.id for group in items):
                return

            clash = any(group.name == item.name and group.id != item.id for group in items)
            if clash:
                raise ValueError(f"已有叫「{item.name}」的工具组，请换一个名字。")

            self._save_all([group if group.id != item.id else item for group in items])

    def remove(self, group_id: str) -> None:
        """按 id 删除；id 不存在时静默忽略。

        Raises:
            ValueError: 这是一个出厂内置的工具组（见 `reject_if_builtin`）。
        """
        with file_lock(self._path):
            items = self.list()
            reject_if_builtin(items, group_id, "工具组")
            self._save_all([group for group in items if group.id != group_id])

    def _save_all(self, items: list[ToolGroup]) -> None:
        """整体覆写（须由调用方持锁进入，见 `file_lock`）。"""
        atomic_write_text(
            self._path,
            json.dumps([item.model_dump() for item in items], ensure_ascii=False, indent=2),
        )


class SkillGroupStore:
    """技能组的持久化。结构与 ToolGroupStore 完全一致（一列对象、按 id 增删改）。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def list(self) -> list[SkillGroup]:
        """读取全部技能组；文件缺失或损坏时返回空列表，单条不合法则跳过（见 load_items）。"""
        return load_items(self._path, SkillGroup)

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
        item = SkillGroup(id=uuid4().hex, name=name, description=description, skills=list(skills))

        # 校验放在锁内、对着同一份快照判（理由同 ToolGroupStore.add）
        with file_lock(self._path):
            items = self.list()
            if any(group.name == name for group in items):
                raise ValueError(f"已有叫「{name}」的技能组，请换一个名字。")

            self._save_all([*items, item])

        return item

    def update(self, item: SkillGroup) -> None:
        """按 id 覆盖更新；id 不存在时静默忽略。组名冲突在这里拦。"""
        with file_lock(self._path):
            items = self.list()
            if not any(group.id == item.id for group in items):
                return

            clash = any(group.name == item.name and group.id != item.id for group in items)
            if clash:
                raise ValueError(f"已有叫「{item.name}」的技能组，请换一个名字。")

            self._save_all([group if group.id != item.id else item for group in items])

    def remove(self, group_id: str) -> None:
        """按 id 删除；id 不存在时静默忽略。

        Raises:
            ValueError: 这是一个出厂内置的技能组（见 `reject_if_builtin`）。
        """
        with file_lock(self._path):
            items = self.list()
            reject_if_builtin(items, group_id, "技能组")
            self._save_all([group for group in items if group.id != group_id])

    def _save_all(self, items: list[SkillGroup]) -> None:
        """整体覆写（须由调用方持锁进入，见 `file_lock`）。"""
        atomic_write_text(
            self._path,
            json.dumps([item.model_dump() for item in items], ensure_ascii=False, indent=2),
        )


class PromptGroupStore:
    """提示词组的持久化（原先是「模式」，模式现在指四类组的组合）。

    结构与 ModelStore 完全一致，只是存的数据类型不同。
    两者本可以抽象成一个泛型基类，这里为了直白先各自实现一份。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def list(self) -> list[PromptGroup]:
        """读取全部提示词组；文件缺失或损坏时返回空列表，单条不合法则跳过（见 load_items）。"""
        return load_items(self._path, PromptGroup)

    def get(self, group_id: str) -> PromptGroup | None:
        """按 id 查找，找不到返回 None。"""
        return next((item for item in self.list() if item.id == group_id), None)

    def find_by_name(self, name: str) -> PromptGroup | None:
        """按组名查找 —— 组名对用户是主要标识，用它判断重名。"""
        return next((item for item in self.list() if item.name == name), None)

    def add(self, *, name: str, description: str = "", prompts: list[str]) -> PromptGroup:
        """新增一个提示词组，id 由本方法生成并返回。

        Raises:
            ValueError: 组名已被占用。
        """
        item = PromptGroup(
            id=uuid4().hex, name=name, description=description, prompts=list(prompts)
        )

        # 校验放在锁内、对着同一份快照判（理由同 ToolGroupStore.add）
        with file_lock(self._path):
            items = self.list()
            if any(group.name == name for group in items):
                raise ValueError(f"已有叫「{name}」的提示词组，请换一个名字。")

            self._save_all([*items, item])

        return item

    def update(self, item: PromptGroup) -> None:
        """按 id 覆盖更新；id 不存在时静默忽略。组名冲突在这里拦。"""
        with file_lock(self._path):
            items = self.list()
            if not any(group.id == item.id for group in items):
                return

            clash = any(group.name == item.name and group.id != item.id for group in items)
            if clash:
                raise ValueError(f"已有叫「{item.name}」的提示词组，请换一个名字。")

            self._save_all([group if group.id != item.id else item for group in items])

    def remove(self, group_id: str) -> None:
        """按 id 删除；id 不存在时静默忽略。

        Raises:
            ValueError: 这是一个出厂内置的提示词组（见 `reject_if_builtin`）。
        """
        with file_lock(self._path):
            items = self.list()
            reject_if_builtin(items, group_id, "提示词组")
            self._save_all([item for item in items if item.id != group_id])

    def drop_prompt(self, prompt_id: str) -> list[str]:
        """把某条提示词从所有引用它的组里摘掉，返回被改动的组名。

        删提示词时必须走这一步：引用是按 **id** 存的，只删正文会在组里留下一个指向
        空处的 id —— 界面上它连名字都显示不出来，可每次保存又被原样写回去，想删都
        删不掉。这正是「已删除的提示词在提示词组里去不掉」的成因。

        Returns:
            被改动的组名（供界面告诉用户动了哪几个组）；没有任何组引用它就返回空列表。
        """
        with file_lock(self._path):
            items = self.list()
            touched = [group for group in items if prompt_id in group.prompts]
            if not touched:
                return []

            for group in touched:
                group.prompts = [pid for pid in group.prompts if pid != prompt_id]
            self._save_all(items)

        return [group.name for group in touched]

    def prune_missing(self, valid_ids: set[str]) -> list[str]:
        """清掉组里所有指向已不存在提示词的 id，返回被改动的组名。

        `drop_prompt` 管的是「这一次删除」，它管的是**已经躺在文件里的**悬空引用 ——
        更早的删除没做级联，那些 id 会一直留着。无变化时不写盘，重复调用无副作用。
        """
        with file_lock(self._path):
            items = self.list()
            touched: list[PromptGroup] = []
            for group in items:
                kept = [pid for pid in group.prompts if pid in valid_ids]
                if len(kept) != len(group.prompts):
                    group.prompts = kept
                    touched.append(group)

            if not touched:
                return []
            self._save_all(items)

        return [group.name for group in touched]

    def _save_all(self, items: list[PromptGroup]) -> None:
        """整体覆写（须由调用方持锁进入，见 `file_lock`）。"""
        atomic_write_text(
            self._path,
            json.dumps([item.model_dump() for item in items], ensure_ascii=False, indent=2),
        )


class ModeStore:
    """模式的持久化。

    结构和另外几个 store 一致，只是存的是 `Mode` —— 模式引用四类组，
    所以这里不做「成员是否存在」的校验（组可以为空，那是合法配置）。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def list(self) -> list[Mode]:
        """读取全部模式；文件缺失或损坏时返回空列表，单条不合法则跳过（见 load_items）。"""
        return load_items(self._path, Mode)

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
        # 校验放在锁内、对着同一份快照判（理由同 ToolGroupStore.add）
        with file_lock(self._path):
            items = self.list()
            if any(item.name == mode.name for item in items):
                raise ValueError(f"已有叫「{mode.name}」的模式，请换一个名字。")

            self._save_all([*items, mode])

        return mode

    def update(self, mode: Mode) -> None:
        """按 id 覆盖更新；id 不存在时静默忽略。重名在这里拦。"""
        with file_lock(self._path):
            items = self.list()
            if not any(item.id == mode.id for item in items):
                return

            clash = any(item.name == mode.name and item.id != mode.id for item in items)
            if clash:
                raise ValueError(f"已有叫「{mode.name}」的模式，请换一个名字。")

            self._save_all([item if item.id != mode.id else mode for item in items])

    def remove(self, mode_id: str) -> None:
        """按 id 删除；id 不存在时静默忽略。

        Raises:
            ValueError: 这是一个出厂内置的模式（见 `reject_if_builtin`）。
        """
        with file_lock(self._path):
            items = self.list()
            reject_if_builtin(items, mode_id, "模式")
            self._save_all([item for item in items if item.id != mode_id])

    def _save_all(self, items: list[Mode]) -> None:
        """整体覆写（须由调用方持锁进入，见 `file_lock`）。"""
        atomic_write_text(
            self._path,
            json.dumps([item.model_dump() for item in items], ensure_ascii=False, indent=2),
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
    try:
        # rename 本身是原子的（同一分区内），不用再套锁
        legacy.rename(target)
    except OSError:
        # 两套界面同时启动时会撞上：另一个进程刚好搬走了，这次就当没搬
        return False

    return True


def migrate_prompt_group_refs(path: Path, mapping: dict[tuple[str, str], str]) -> bool:
    """把提示词组的**旧引用**（`{分类: 名字}`）换成 id 列表。

    背景：提示词的标识以前是「分类 + 名字」，提示词组里存的就是这个组合。现在提示词
    有了自己的 id（名字可以随便改、分类也不再进路径），组里的引用得跟着换 ——
    否则升级之后，所有组里的提示词会一起「消失」。

    Args:
        path: prompt_groups.json 的路径。
        mapping: `{(分类, 名字): id}`，由 `prompts.migrate_layout` 产出 ——
            它正好知道每条旧提示词搬完变成了哪个 id。

    Returns:
        是否真的改了文件。没改动就不落盘（省一次写入，也让重复调用完全无副作用）。
    """
    raw = read_json(path, [])
    if not isinstance(raw, list):
        return False

    changed = False
    migrated: list[object] = []

    for entry in raw:
        # 只认「有 settings 且没有 prompts」的条目：那是旧格式，且还没迁过
        if isinstance(entry, dict) and "settings" in entry and "prompts" not in entry:
            settings = entry.pop("settings")
            ids: list[str] = []

            if isinstance(settings, dict):
                for category, name in settings.items():
                    # 映射里没有 = 那条提示词已经不在了（用户删过），跳过就好：
                    # 为一个已删除的引用造一个假 id 只会让组里多出一条永远「文件缺失」
                    prompt_id = mapping.get((str(category), str(name)))
                    if prompt_id:
                        ids.append(prompt_id)

            entry["prompts"] = ids
            changed = True

        migrated.append(entry)

    if changed:
        atomic_write_text(path, json.dumps(migrated, ensure_ascii=False, indent=2))

    return changed
