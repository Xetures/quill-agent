"""集中式配置：所有环境变量统一在此声明，避免 os.environ 散落各处。

设计要点：
1. 单一来源：新增配置项时只改 Settings 这一个地方；
2. 类型校验：借助 pydantic 自动转换类型，读进来就是 str / bool / int；
3. 加载顺序：环境变量 > .env 文件 > 字段默认值。

关于路径：所有「会写到磁盘上」的路径都写成相对路径（`data/models.json` 这样），
再由 `_anchor_to_home` 统一挂到**数据根**下面 —— 相对于 cwd 的话，双击启动时
cwd 不可控（可能是安装目录、也可能是用户主目录），数据会散落各处；装在
`C:\\Program Files` 下时更惨，写文件会被 Windows 静默重定向到 VirtualStore。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 数据根目录的环境变量名。单独拎出来是因为下面的字段别名要用它，
# 而它**不能**就叫 HOME（见 `_default_home`）。
HOME_ENV = "QUILL_HOME"

# 这些字段是「会落到磁盘上的路径」：相对值一律锚定到数据根（见 `_anchor_to_home`）
_PATH_FIELDS = (
    "models_path",
    "model_catalog_path",
    "prompt_groups_path",
    "modes_path",
    "tool_groups_path",
    "skill_groups_path",
    "memory_path",
    "search_path",
    "conversations_dir",
    "preferences_path",
    "prompt_dir",
    "skills_dir",
    "work_dir",
)


def _default_home() -> Path:
    """默认的数据根目录。

    优先级（**第 2 条是刻意的向后兼容**）：

    1. `QUILL_HOME` 环境变量 —— 显式指定，永远听它的；
    2. 当前目录下有 `data/` 或 `prompt/` —— 说明是「就地运行」（在仓库里开发，
       或者从解压目录直接跑），沿用旧行为把数据放在旁边。早期版本所有路径都
       相对 cwd，用户的数据本来就散在仓库里，直接改到 `~/.quill` 会让那些
       配置看起来「消失」；
    3. 其余情况用 `~/.quill` —— 打包 / 双击启动走的就是这条：cwd 不可控，
       而用户的主目录一定可写。
    """
    explicit = os.environ.get(HOME_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser()

    here = Path.cwd()
    if (here / "data").is_dir() or (here / "prompt").is_dir():
        return here

    return Path.home() / ".quill"


class Settings(BaseSettings):
    """应用配置对象。字段名与环境变量对应（不区分大小写）。

    环境变量前缀说明：这里不带前缀，因此 `app_name` 对应环境变量 `APP_NAME`。
    """

    model_config = SettingsConfigDict(
        # 数据根里的 .env 排在后面 = 优先级更高：换台机器/换个发布目录时，
        # 用户那一份配置不该被安装目录里的模板盖回去
        env_file=(".env", str(_default_home() / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",  # .env 里多余的键不会导致报错
    )

    home: Path = Field(
        default_factory=_default_home,
        # 必须显式指定别名。字段名 `home` 默认会去匹配环境变量 `HOME`，
        # 而那个变量在 POSIX 上**永远存在**（指向用户主目录）—— 不写这一行的话，
        # 数据根会被悄悄改成 `$HOME`，用户的配置散在主目录里。
        #
        # 注意别名里**不能**再写上字段名 `home`（`AliasChoices("QUILL_HOME", "home")`
        # 看着更周全，实际会把 `HOME` 又请回来：环境变量匹配是大小写不敏感的）。
        # 所以第二个别名用下划线写法 `quill_home`，既避开冲突，又让
        # `Settings(quill_home=...)` 这种显式构造可用。
        validation_alias=AliasChoices(HOME_ENV, HOME_ENV.lower()),
        description="数据根目录（data/、prompt/、skills/ 都挂在它下面）",
    )

    app_name: str = Field(default="quill", description="应用名称，用于页面标题等")

    # 模型配置的存储文件位置
    models_path: Path = Field(default=Path("data/models.json"), description="模型配置 JSON 路径")

    # 模型规格快照：模型名 -> 上下文窗口。绝大多数官方端点的 /models 不返回窗口大小，
    # 只能靠一份本地快照查表（见 quill_agent/model_catalog.py）。它不在仓库里，
    # 由用户在「API 设置」页点「同步模型库」拉一次，也可以自己手写。
    model_catalog_path: Path = Field(
        default=Path("data/model_catalog.json"),
        description="模型规格快照 JSON 路径",
    )
    model_catalog_url: str = Field(
        default="https://models.dev/api.json",
        description="模型规格快照的下载地址，默认取自 models.dev",
    )

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

    # 联网搜索：后端（Tavily / 博查）、各自的 Key、可选的自定义端点。
    # 只有一个对象，不像模型配置那样是一列 —— 「用哪个后端」全局只有一个答案
    search_path: Path = Field(
        default=Path("data/search.json"), description="联网搜索配置 JSON 路径"
    )

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

    # 沙箱：给执行类工具（run_command / start_process）加一层内核级边界。
    # 它和「危险命令先问用户」那套审批是两回事：沙箱管「够不够得着」，审批管「要不要问」。
    #
    #   off              不套沙箱（默认）—— 执行类命令不受工作目录限制
    #   read-only        工作区只读、默认断网
    #   workspace-write  工作区可写、默认断网
    #
    # 默认 off 的原因：Phase 1 只实现了 Linux 的 bubblewrap 后端，别的平台没有可用后端。
    # 而「配了沙箱却没有后端」在本项目里的处理是**拒绝执行**（不静默裸跑）——
    # 默认开着会让 macOS / Windows 上的开发和测试当场不可用。等补上 macOS 后端再翻默认值。
    sandbox_mode: Literal["off", "read-only", "workspace-write"] = Field(
        default="off",
        description="执行类工具的沙箱档位：off / read-only / workspace-write",
    )

    # 沙箱里是否放行网络。默认 False：数据外传是这类 Agent 最实际的风险，
    # 而绝大多数编码任务用不上网络（装依赖那一下可以临时开）。
    sandbox_network: bool = Field(
        default=False,
        description="沙箱内是否允许联网（默认断网）",
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

    @model_validator(mode="after")
    def _anchor_to_home(self) -> Settings:
        """把相对路径挂到数据根下面。

        「相对路径相对于谁」这件事只在这里决定：**相对于数据根**，而不是 cwd
        （理由见模块开头）。显式给绝对路径的场景（环境变量、测试里的 tmp_path）
        原样保留 —— 那是调用方明确表达了「就要用这里」。
        """
        home = self.home.expanduser()
        if not home.is_absolute():
            # QUILL_HOME 给了相对路径：按启动目录解释，免得 anchor 出一个
            # 「relative/data/x.json」这种仍然依赖 cwd 的半吊子结果
            home = Path.cwd() / home
        if home != self.home:
            self.home = home

        for name in _PATH_FIELDS:
            value = getattr(self, name)
            if not value.is_absolute():
                setattr(self, name, home / value)

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局唯一的 Settings 实例（带缓存，避免重复解析 .env）。

    测试时如需覆盖配置，可调用 `get_settings.cache_clear()`。
    """
    return Settings()
