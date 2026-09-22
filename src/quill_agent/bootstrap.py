"""首次运行：把出厂资源播种到数据根。

为什么需要它：数据根默认在平台数据目录里（理由见 `config._platform_data_home`），
而提示词与技能是**用得越久越值钱**的东西 —— 用户会改它、会往里加。首次运行时把出厂
那份复制过去，用户就有东西可改；之后升级只补新增的，绝不动已经存在的。

「只补不覆盖」是硬要求：用户改过的提示词是他的资产，升级时被出厂版本盖回去，
比一开始就没有默认值还糟。

播种分两条路，都在本模块收口：

    1. **目录**（`prompt/`、`skills/`）—— 逐文件复制、已存在的一律跳过，
       见 `seed_defaults`；
    2. **JSON 里的出厂条目**（两个默认模式，以及它们引用的各组与内置提示词）——
       有固定 id，所以是按 id 补进列表、已存在的只把出厂标记修回来，
       见 `defaults.ensure`。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from quill_agent import config, defaults
from quill_agent.config import Settings
from quill_agent.prompts import migrate_layout
from quill_agent.store import ToolGroupStore, migrate_prompt_group_refs


def migrate_tool_names(settings: Settings) -> list[str]:
    """把工具组「需要确认」名单里的 `spawn_agent` 改成 `spawn_agents`。

    两个子代理工具合并成了一个（`spawn_agent` 实测从不被模型选中）。工具组的 `tools`
    是从注册表现取的、自动就跟上了，但 **`confirm` 是写死存下来的** —— 老数据里那个名字
    会一直留着，而它已经不对应任何工具。表现是**派子代理再也不弹确认**：设计意图
    （另外花钱、要等，值得打断一次）静默失效，而且没有任何提示。

    只做重命名：用户自己从名单里删过那一项的话，这里不会替他加回来。幂等。
    """
    store = ToolGroupStore(settings.tool_groups_path)
    touched: list[str] = []
    for group in store.list():
        if "spawn_agent" not in group.confirm:
            continue
        group.confirm = [
            "spawn_agents" if name == "spawn_agent" else name for name in group.confirm
        ]
        store.update(group)
        touched.append(group.name)

    return touched

# 需要播种的资源目录（名字同时对应 Settings 上的 `<name>_dir` 字段）
SEEDED_DIRS = ("prompt", "skills")

# 包内资源目录名。wheel 安装时出厂资源放这里（源码树里运行时则直接读仓库根）
RESOURCE_PACKAGE = "resources"


def template_dir(name: str) -> Path | None:
    """找出厂资源目录。

    两个来源，按顺序找：

        1. **包内** `quill_agent/resources/<name>` —— 装成 wheel 之后资源在这里；
        2. **源码树根**下的 `<name>` —— 在仓库里运行时就是它。

    都找不到就返回 None（比如用户自己把 `prompt/` 删了），这不该让启动失败。
    """
    packaged = Path(__file__).resolve().parent / RESOURCE_PACKAGE / name
    if packaged.is_dir():
        return packaged

    # __file__ 是 <root>/src/quill_agent/bootstrap.py，往上三层是仓库根
    source_root = Path(__file__).resolve().parents[2] / name
    if source_root.is_dir():
        return source_root

    return None


def copy_missing(source: Path, target: Path) -> int:
    """把 `source` 下**目标里还没有**的文件复制过去，返回复制了几个。

    目录结构保持原样；已存在的文件一律跳过（那个很可能是用户改过的）。
    """
    copied = 0

    for item in source.rglob("*"):
        if not item.is_file():
            continue

        destination = target / item.relative_to(source)
        if destination.exists():
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        copied += 1

    return copied


def seed_defaults(settings: Settings) -> dict[str, int]:
    """按需播种，返回 `{目录名: 补了几个文件}`（只含真的补过东西的项）。"""
    seeded: dict[str, int] = {}

    for name in SEEDED_DIRS:
        source = template_dir(name)
        target = getattr(settings, f"{name}_dir")

        if source is None:
            continue
        # 就地运行时数据根就是仓库根，出厂目录和目标目录是同一个 —— 别自己抄自己
        if source.resolve() == target.resolve():
            continue

        copied = copy_missing(source, target)
        if copied:
            seeded[name] = copied

    return seeded


def migrate_legacy_layout(settings: Settings) -> list[str]:
    """把旧结构搬到新结构，返回「这次做了什么」（没做就是空列表）。

    两件事**必须一起做**：

        1. 提示词文件：`prompt/<分类>/<名字>.md` -> `prompt/<id>.md`，
           分类与名字移进文件头的元信息块；
        2. 提示词组的引用：`{分类: 名字}` -> `[id, ...]`。

    第二步要靠第一步产出的「(分类, 名字) -> id」映射才能对上号 —— 分开做，
    中间就会停在「文件搬了、引用全悬空」的状态上。两步都幂等，重复启动无副作用。
    """
    mapping, moved = migrate_layout(settings.prompt_dir)

    notes: list[str] = []
    if moved:
        notes.append(f"{moved} 条提示词改成 id 文件名")

    # 引用迁移总是跑一遍：它自己判断有没有旧格式。就算这次没搬文件，上一次也可能
    # 搬到一半被打断过（文件搬了、引用没迁），那样它正好能补上
    if migrate_prompt_group_refs(settings.prompt_groups_path, mapping):
        notes.append("提示词组的引用已改为 id")

    return notes


def migrate_legacy_home(settings: Settings) -> str | None:
    """把老数据根（`~/.quill`）里的东西搬到新的平台数据目录，返回说明或 None。

    老版本的默认数据根是 `~/.quill`，现在按各平台惯例放在用户数据目录里
    （见 `config._platform_data_home`）。不搬的话，老用户升级后看到的是「配置、
    会话、记忆全不见了」—— 而它们其实还好好躺在原地。

    三条约束，缺一条都会出错：

    1. **只在当前用的是平台目录时才搬**。用户显式配了 `QUILL_HOME`（桌面壳也算）
       或者是就地运行，就说明数据该在哪是明确的 —— 这时去别处搬一份进来，
       反而把两个位置搅在一起；
    2. **新位置还没有数据时才搬**。已经有 `data/` 说明这边在用，再合并只会让
       两边的会话和记忆混成一锅；
    3. **只补不覆盖、也不删源**（复用 `copy_missing`，和出厂播种同一条原则）。
       旧目录留着：万一用户回退到老版本，那边还是完整的一份。
    """
    if settings.home != config._platform_data_home():
        return None

    source = config.legacy_home()
    if source.resolve() == settings.home.resolve():
        return None
    if not (source / "data").is_dir():
        return None
    if (settings.home / "data").exists():
        return None

    copied = copy_missing(source, settings.home)
    if not copied:
        return None

    return f"已把老数据从 {source} 复制到 {settings.home}（旧目录保留）"


def startup_note(settings: Settings) -> str:
    """启动时说明「数据放在哪、这次补了什么、迁了什么」。

    排障第一个要问的就是「你的数据在哪」，与其让人猜，不如启动时就打出来；
    数据搬迁也一样 —— 用户升级后看到「提示词不见了」时，日志里能给出答案。
    """
    note = f"数据根目录：{settings.home}"

    # 老数据先搬到新家，再播种出厂资源：反过来的话，播种写下的文件会让新位置
    # 看起来「已经有数据了」，那句判断就会把整次迁移拦掉
    moved = migrate_legacy_home(settings)
    if moved:
        note += f"（{moved}）"

    seeded = seed_defaults(settings)
    if seeded:
        detail = "、".join(f"{name} 补了 {count} 个文件" for name, count in seeded.items())
        note += f"（首次运行，已把默认资源复制过去：{detail}）"

    migrated = migrate_legacy_layout(settings)
    if migrated:
        note += f"（已迁移旧数据：{'；'.join(migrated)}）"

    renamed = migrate_tool_names(settings)
    if renamed:
        note += f"（已更新确认名单里的工具名：{'；'.join(renamed)}）"

    # 两个默认模式及它们引用的各组。必须在上面两步之后：内置提示词要落进已经
    # 迁移好的目录布局里，否则刚写下的文件会被下一轮的迁移再搬一次
    created = defaults.ensure(settings)
    if created:
        note += f"（已补上出厂资源：{'；'.join(created)}）"

    return note
