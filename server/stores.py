"""各存储对象的构造入口。

每次调用都新建一个，不做缓存：这些 store 本身不带状态（全是「读文件 → 处理 →
写文件」），构造开销可以忽略，换来的是**永远读到最新数据** —— 不会像缓存那样，
改完配置要重启才生效。
"""

from __future__ import annotations

from quill_agent.config import get_settings
from quill_agent.history import ConversationStore
from quill_agent.memory import MemoryStore
from quill_agent.preferences import PreferenceStore
from quill_agent.prompts import PromptLibrary
from quill_agent.skills import SkillLibrary
from quill_agent.store import (
    ModelStore,
    ModeStore,
    PromptGroupStore,
    SkillGroupStore,
    ToolGroupStore,
    migrate_prompt_groups,
)


def conversations() -> ConversationStore:
    """会话历史（含 active / archived 两个目录）。"""
    store = ConversationStore(get_settings().conversations_dir)
    store.ensure_dirs()
    return store


def models() -> ModelStore:
    """模型连接配置。"""
    return ModelStore(get_settings().models_path)


def prompt_groups() -> PromptGroupStore:
    """提示词组。

    原先叫「模式」、存在 modes.json；模式现在是四类组的组合，那个名字归了新模式。
    旧数据在这里搬一次（见 `migrate_prompt_groups`），用户建好的组不会丢。
    """
    settings = get_settings()
    migrate_prompt_groups(settings.modes_path, settings.prompt_groups_path)
    return PromptGroupStore(settings.prompt_groups_path)


def modes() -> ModeStore:
    """模式：四类组的组合 + 记忆开关 + 偏好模型。"""
    return ModeStore(get_settings().modes_path)


def prompts() -> PromptLibrary:
    """提示词正文库（prompt/ 目录）。"""
    library = PromptLibrary(get_settings().prompt_dir)
    library.ensure_dirs()
    return library


def tool_groups() -> ToolGroupStore:
    """工具组（工具的搭配方案，模式的组成部分之一）。"""
    return ToolGroupStore(get_settings().tool_groups_path)


def skills() -> SkillLibrary:
    """技能库（skills/ 目录）。"""
    library = SkillLibrary(get_settings().skills_dir)
    library.ensure_dir()
    return library


def skill_groups() -> SkillGroupStore:
    """技能组（技能的搭配方案，模式的组成部分之一）。"""
    return SkillGroupStore(get_settings().skill_groups_path)


def memory() -> MemoryStore:
    """长期记忆。"""
    return MemoryStore(get_settings().memory_path)


def preferences() -> PreferenceStore:
    """界面偏好（上次选的模型 / 模式 / 工作目录等）。"""
    return PreferenceStore(get_settings().preferences_path)
