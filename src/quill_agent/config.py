"""集中式配置：所有环境变量统一在此声明，避免 os.environ 散落各处。

设计要点：
1. 单一来源：新增配置项时只改 Settings 这一个地方；
2. 类型校验：借助 pydantic 自动转换类型，读进来就是 str / bool / int；
3. 加载顺序：环境变量 > .env 文件 > 字段默认值。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置对象。字段名与环境变量对应（不区分大小写）。

    环境变量前缀说明：这里不带前缀，因此 `app_name` 对应环境变量 `APP_NAME`。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env 里多余的键不会导致报错
    )

    app_name: str = Field(default="quill", description="应用名称，用于页面标题等")

    # 模型配置的存储文件位置
    models_path: Path = Field(default=Path("data/models.json"), description="模型配置 JSON 路径")

    # 提示词：正文文件放 prompt/ 目录（用户可直接维护），配置存 data/
    prompt_dir: Path = Field(default=Path("prompt"), description="提示词目录")
    # 提示词组原先就叫「模式」，存在 modes.json。模式现在是四类组的组合，
    # 那个文件名归了新模式；提示词组挪到 prompt_groups.json（启动时自动搬一次，
    # 见 store.migrate_prompt_groups，用户已有的组不会丢）
    prompt_groups_path: Path = Field(
        default=Path("data/prompt_groups.json"), description="提示词组 JSON 路径"
    )
    modes_path: Path = Field(default=Path("data/modes.json"), description="模式 JSON 路径")

    # 工具：定义在代码里；给哪些工具由模式的工具组决定（见 tool_groups_path），
    # 这里不再有「全局开关」—— 那在工具组实现后就变成了改了却不生效的死控件。
    # 工具组：工具的搭配方案（模式的组成部分之一）
    tool_groups_path: Path = Field(
        default=Path("data/tool_groups.json"),
        description="工具组 JSON 路径",
    )

    # 技能：一个技能 = 一个目录（内含 SKILL.md），正文由用户维护；
    # 给哪些技能由模式的技能组决定（见 skill_groups_path），同样没有全局开关。
    skills_dir: Path = Field(default=Path("skills"), description="技能目录")
    # 技能组：技能的搭配方案（模式的组成部分之一）
    skill_groups_path: Path = Field(
        default=Path("data/skill_groups.json"),
        description="技能组 JSON 路径",
    )

    # 记忆：跨会话保留的长期事实，条目由模型调用 remember 写入
    memory_path: Path = Field(default=Path("data/memory.json"), description="记忆 JSON 路径")

    # 文件工具的工作目录 —— 同时是安全边界：
    # 模型给出的路径一律限制在这个目录内，越界直接拒绝。
    #
    # 默认值取**进程启动时**的当前目录：default_factory 在实例化配置时求值一次，
    # 不是每次访问都求值。用 Path(".") 的话，谁中途 chdir 一下边界就跟着飘走了 ——
    # 而这是个安全边界，不该有这种隐式行为。
    #
    # 生产环境建议在 .env 里显式配 WORK_DIR：默认值取决于「从哪个目录启动」，
    # 换个启动位置就等于换了个沙箱根目录。
    work_dir: Path = Field(
        default_factory=Path.cwd,
        description="文件工具的工作目录（也是安全边界）",
    )

    # 会话历史：一个会话一个 JSONL 文件，支持归档
    conversations_dir: Path = Field(
        default=Path("data/conversations"),
        description="会话存储目录（内含 active / archived）",
    )

    # 界面偏好：上次选中的模型 / 模式等，重启后接着用
    preferences_path: Path = Field(
        default=Path("data/preferences.json"),
        description="界面偏好 JSON 路径",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局唯一的 Settings 实例（带缓存，避免重复解析 .env）。

    测试时如需覆盖配置，可调用 `get_settings.cache_clear()`。
    """
    return Settings()
