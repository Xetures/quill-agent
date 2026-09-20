"""首次运行：把出厂资源播种到数据根。

为什么需要它：数据根默认是 `~/.quill`（理由见 `config` 模块开头），而提示词与技能是
**用得越久越值钱**的东西 —— 用户会改它、会往里加。首次运行时把出厂那份复制过去，
用户就有东西可改；之后升级只补新增的，绝不动已经存在的。

「只补不覆盖」是硬要求：用户改过的提示词是他的资产，升级时被出厂版本盖回去，
比一开始就没有默认值还糟。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from quill_agent.config import Settings
from quill_agent.prompts import migrate_layout
from quill_agent.store import migrate_prompt_group_refs

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


def startup_note(settings: Settings) -> str:
    """启动时说明「数据放在哪、这次补了什么、迁了什么」。

    排障第一个要问的就是「你的数据在哪」，与其让人猜，不如启动时就打出来；
    数据搬迁也一样 —— 用户升级后看到「提示词不见了」时，日志里能给出答案。
    """
    note = f"数据根目录：{settings.home}"

    seeded = seed_defaults(settings)
    if seeded:
        detail = "、".join(f"{name} 补了 {count} 个文件" for name, count in seeded.items())
        note += f"（首次运行，已把默认资源复制过去：{detail}）"

    migrated = migrate_legacy_layout(settings)
    if migrated:
        note += f"（已迁移旧数据：{'；'.join(migrated)}）"

    return note
