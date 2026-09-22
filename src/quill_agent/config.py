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
import sys
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
    "mcp_servers_path",
    "skill_groups_path",
    "memory_path",
    "search_path",
    "conversations_dir",
    "preferences_path",
    "prompt_dir",
    "skills_dir",
    "work_dir",
)


def _default_sandbox_mode() -> str:
    """沙箱的默认档位 —— 按平台给。

    为什么分平台：默认值对**所有人**生效，而「配了沙箱却没有后端」在本项目里是
    **拒绝执行**。全局默认 workspace-write 的话，没有后端的平台（目前是 Windows）
    会每条命令都失败 —— 而用户根本没配过沙箱，只会觉得程序坏了。

    这不是「Windows 懒得做」：那边确实没有能对标 bwrap / Seatbelt 的现成原语，
    要拼受限 Token + 合成 SID 才够用，是个独立工程。见 `sandbox.py`。
    """
    if sys.platform == "win32":
        return "off"
    return "workspace-write"


# 平台数据目录里的应用文件夹名。macOS / Windows 按那边的惯例大写开头；
# Linux 走 XDG，惯例是小写。
APP_DIR_NAME = "Quill"

# 「就地运行」的判据：源码树和解压出来的发布包**都同时有**这两样
# （发布包由 scripts/make_release.py 打，src/ 与 pyproject.toml 都在里面）
_CHECKOUT_MARKERS = ("pyproject.toml", "src/quill_agent")


def _is_checkout(path: Path) -> bool:
    """这个目录看起来是源码树 / 解压出来的发布包吗？"""
    return all((path / marker).exists() for marker in _CHECKOUT_MARKERS)


def _platform_data_home() -> Path:
    """按平台惯例给出用户数据目录 —— 双击启动与桌面版的落点。

    为什么不再用 `~/.quill`：主目录是用户的私人空间，谁都在那儿丢一个点目录，
    久了就是一地鸡毛；而下面这三个位置是各平台**备份 / 迁移 / 卸载**时都会去看的地方：

        macOS    ~/Library/Application Support/Quill
        Windows  %APPDATA%\\Quill
        Linux    $XDG_DATA_HOME/quill（默认 ~/.local/share/quill）

    老版本的数据在 `~/.quill`，启动时会自动迁过来（见 `bootstrap.migrate_legacy_home`）。
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME

    if sys.platform == "win32":
        # APPDATA 在这边基本总是有；真没有（服务账户、精简环境）就退回等效路径
        appdata = os.environ.get("APPDATA", "").strip()
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / APP_DIR_NAME

    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / APP_DIR_NAME.lower()


def legacy_home() -> Path:
    """老版本的数据根，只为迁移而留（见 `bootstrap.migrate_legacy_home`）。"""
    return Path.home() / ".quill"


def _default_home() -> Path:
    """默认的数据根目录。

    优先级（**第 2 条是刻意的向后兼容**）：

    1. `QUILL_HOME` 环境变量 —— 显式指定，永远听它的（桌面壳也走这条：
       壳知道自己的数据该放哪，不指望 cwd 猜）；
    2. **就地运行**：当前目录是个源码树 / 解压出来的发布包，**而且写得进去** ——
       沿用旧行为把数据放在旁边。早期版本所有路径都相对 cwd，开发者的数据本来就
       散在仓库里，直接改到平台目录会让那些配置看起来「消失」；
    3. 其余情况用**平台数据目录**（见 `_platform_data_home`）。

    第 2 条里「写得进去」这个限定是必需的：桌面 App 的安装目录里同样有源码
    （打包时被带进去），但它在 macOS 的签名包内、Windows 的 Program Files 下
    **都不可写** —— 不查可写性就会把数据往只读目录里写，而 Windows 上这一步还会
    被**静默重定向到 VirtualStore**，用户完全看不出来。

    判据也从「有 data/ 或 prompt/」收窄成了「确实是个源码 / 发布目录」：前者的
    麻烦是首次运行时那两个目录还不存在（于是第一次跑到平台目录、第二次才就地，
    同一个仓库两个位置），而它想表达的本来就是「这里是不是本项目的目录」。
    """
    explicit = os.environ.get(HOME_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser()

    here = Path.cwd()
    if _is_checkout(here) and os.access(here, os.W_OK):
        return here

    return _platform_data_home()


def _default_work_dir() -> Path:
    """文件工具的工作目录（同时是安全边界）的默认值。

    取进程启动时的当前目录，但**只在它可用时**：双击启动时 cwd 可能是文件系统根
    （macOS 的 Finder 就是给 `/`）或只读的安装目录 —— 前者的后果是「边界」变成整个
    磁盘，后者是一动笔就失败。那两种情况下退到主目录下的 `Quill/`，那是用户一眼
    找得到、也一定写得进去的地方。

    桌面壳会显式传 `WORK_DIR`（它知道该用哪个工作区），这条只是兜底。

    用 default_factory 而不是 Path(".")：它在实例化配置时求值一次，中途谁 chdir
    都不会让边界跟着飘走 —— 这是个安全边界，不该有那种隐式行为。
    """
    here = Path.cwd()
    if here.parent == here or not os.access(here, os.W_OK):
        return Path.home() / "Quill"
    return here


class Settings(BaseSettings):
    """应用配置对象。字段名与环境变量对应（不区分大小写）。

    环境变量前缀说明：这里不带前缀，因此 `app_name` 对应环境变量 `APP_NAME`。
    """

    model_config = SettingsConfigDict(
        # 两份 .env，都写成**绝对路径**：相对路径是按 cwd 解析的，而双击启动时
        # cwd 不可控（可能是安装目录、也可能是 `/`），那种「换个启动方式就换了
        # 配置文件」的行为排查起来极难。
        #
        # 列表靠后的优先级更高：数据根里那一份要盖过启动目录里的模板 ——
        # 换台机器 / 换个发布目录时，用户自己的配置不该被安装包里的样例顶掉
        env_file=(str(Path.cwd() / ".env"), str(_default_home() / ".env")),
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

    app_name: str = Field(default="Quill", description="应用名称，用于命令行提示与页面标题等")

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

    # MCP：外部工具来源。默认**空** —— 加一个服务器等于允许它在这台机器上跑进程，
    # 所以不预置任何默认值，全部由用户显式添加（见 README 的 MCP 一节）。
    mcp_servers_path: Path = Field(
        default=Path("data/mcp_servers.json"), description="MCP 服务器配置 JSON 路径"
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
    # 默认值见 `_default_work_dir`：取启动目录，但启动目录不可用时（文件系统根、
    # 只读的安装目录）退到主目录下的 `Quill/` —— 安全边界不能是「整个磁盘」。
    work_dir: Path = Field(
        default_factory=_default_work_dir,
        description="文件工具的工作目录（也是安全边界）",
    )

    # 沙箱：给执行类工具（run_command / start_process）加一层内核级边界。
    # 它和「危险命令先问用户」那套审批是两回事：沙箱管「够不够得着」，审批管「要不要问」。
    #
    #   off              不套沙箱 —— 执行类命令不受工作目录限制
    #   read-only        工作区只读、默认断网
    #   workspace-write  工作区可写、默认断网
    #
    # 默认值**按平台给**（见 `_default_sandbox_mode`）：
    #
    #   Linux / macOS    workspace-write —— 两个后端都真能拦住，这是最实用的一档
    #   Windows          off —— 那边没有可用后端，而「配了沙箱却没有后端」在本项目里
    #                    是**拒绝执行**（不静默裸跑）；全局默认开着会让开箱即用变成
    #                    开箱不可用，而用户根本没配过沙箱，只会觉得程序坏了
    #
    # 注意它现在只是**部署时的默认**，不是「最终生效值」：界面上改过的档位存在偏好里、
    # 优先级更高（见 `sandbox.effective_mode`）。用户在顶栏点两下就能换档，不必重启 ——
    # 这是有意的，沙箱档位天然按任务变，改一次重启一次的话用户就会干脆关掉它。
    #
    # Windows 那档不是「懒得做」：那边确实没有能对标 bwrap / Seatbelt 的现成原语，
    # 要拼受限 Token + 合成 SID 才够用，是个独立工程（见 `sandbox.py` 的说明）。
    sandbox_mode: Literal["off", "read-only", "workspace-write"] = Field(
        default_factory=_default_sandbox_mode,
        description="执行类工具的沙箱档位（默认值；界面设置优先）",
    )

    # 沙箱里是否放行网络。默认 False：数据外传是这类 Agent 最实际的风险，
    # 而绝大多数编码任务用不上网络（装依赖那一下可以临时开）。
    # 同 `sandbox_mode`：这也是默认值，界面上改过的存在偏好里。
    sandbox_network: bool = Field(
        default=False,
        description="沙箱内是否允许联网（默认值；界面设置优先）",
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
