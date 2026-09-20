"""用户输入的名字能不能直接当文件名 / 目录名用。

提示词和技能都是「名字就是文件名」的目录约定，而这两个名字直接来自界面上的输入框。
不拦的话，`../..` 之类的输入会写到 `prompt/` 和 `skills/` 之外去 —— 和文件工具的
`PathGuard` 是同一个问题的两个面：那边是模型给的路径，这边是用户给的名字。

规则只有一条：**名字只能是一个名字，不能是一段路径**。
"""

from __future__ import annotations

import re

# 各平台文件名的非法字符取并集：这个项目声称跨平台，不能只在 macOS 上跑得通。
# \x00-\x1f 顺带挡掉换行和制表符 —— 名字里有换行会让路径看起来像两行
INVALID_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# Windows 的保留设备名，同名的文件在那边根本建不出来
RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{index}" for index in range(1, 10)]
    + [f"LPT{index}" for index in range(1, 10)]
)

MAX_NAME_CHARS = 60


def safe_name(name: str) -> str:
    """校验并返回一个可直接用作文件名 / 目录名的名字。

    Args:
        name: 用户输入的名字。

    Returns:
        去掉首尾空白后的名字。

    Raises:
        ValueError: 文案可直接展示给用户，由路由层转成 400。
    """
    cleaned = name.strip()

    if not cleaned:
        raise ValueError("名称不能为空。")
    if len(cleaned) > MAX_NAME_CHARS:
        raise ValueError(f"名称不能超过 {MAX_NAME_CHARS} 个字符。")
    # 以点开头会造出隐藏文件，也会让 `.` / `..` 这种路径片段混进来
    if cleaned.startswith("."):
        raise ValueError("名称不能以点开头。")
    # 结尾是点在 Windows 上会被静默去掉，文件建出来跟预期的名字不一样
    if cleaned.endswith("."):
        raise ValueError("名称不能以点结尾。")
    if INVALID_CHARS.search(cleaned):
        raise ValueError('名称不能包含 / \\ : * ? " < > | 这些字符。')
    # 带扩展名也要拦：Windows 判定设备名时不看扩展名，`CON.txt` 一样建不出来。
    # 而提示词的文件名正是「用户输入的名字 + .md」，所以这一步不能只看整体
    stem = cleaned.split(".")[0]
    if cleaned.upper() in RESERVED_NAMES or stem.upper() in RESERVED_NAMES:
        raise ValueError(f"「{cleaned}」是系统保留名，换一个吧。")

    return cleaned
