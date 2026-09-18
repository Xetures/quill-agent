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
from quill_agent.store import ModelStore, PromptModeStore, SkillStateStore, ToolStateStore


def conversations() -> ConversationStore:
    """会话历史（含 active / archived 两个目录）。"""
    store = ConversationStore(get_settings().conversations_dir)
    store.ensure_dirs()
    return store


def models() -> ModelStore:
    """模型连接配置。"""
    return ModelStore(get_settings().models_path)


def modes() -> PromptModeStore:
    """提示词模式。"""
    return PromptModeStore(get_settings().modes_path)


def prompts() -> PromptLibrary:
    """提示词正文库（prompt/ 目录）。"""
    library = PromptLibrary(get_settings().prompt_dir)
    library.ensure_dirs()
    return library


def tool_states() -> ToolStateStore:
    """工具开关状态。"""
    return ToolStateStore(get_settings().tools_path)


def skills() -> SkillLibrary:
    """技能库（skills/ 目录）。"""
    library = SkillLibrary(get_settings().skills_dir)
    library.ensure_dir()
    return library


def skill_states() -> SkillStateStore:
    """技能开关状态。"""
    return SkillStateStore(get_settings().skills_state_path)


def memory() -> MemoryStore:
    """长期记忆。"""
    return MemoryStore(get_settings().memory_path)


def preferences() -> PreferenceStore:
    """界面偏好（上次选的模型 / 模式 / 工作目录等）。"""
    return PreferenceStore(get_settings().preferences_path)
