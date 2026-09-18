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
    app_debug: bool = Field(default=False, description="调试模式：开启后输出更详细的日志")

    # 后续要接 LLM 时可以替换 / 扩展的配置项
    agent_model: str = Field(default="gpt-4o-mini", description="默认模型名称")
    openai_api_key: str = Field(default="", description="OpenAI API Key")

    # 模型配置的存储文件位置
    models_path: Path = Field(default=Path("data/models.json"), description="模型配置 JSON 路径")

    # 提示词：正文文件放 prompt/ 目录（用户可直接维护），模式配置存 data/
    prompt_dir: Path = Field(default=Path("prompt"), description="提示词目录")
    modes_path: Path = Field(default=Path("data/modes.json"), description="提示词模式 JSON 路径")

    # 工具：定义在代码里，这里只存开关状态
    tools_path: Path = Field(default=Path("data/tools.json"), description="工具开关状态 JSON 路径")

    # 文件工具的工作目录 —— 同时是安全边界：
    # 模型给出的路径一律限制在这个目录内，越界直接拒绝。
    work_dir: Path = Field(
        default=Path("."),
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
