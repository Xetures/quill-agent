"""界面文案的 i18n 迁移进度。

    uv run python scripts/i18n_progress.py    # 或 make i18n

文案迁移是分批做的（来龙去脉见 README-developer.md 的 6.38）：一页一提交，做完一页少一页。
但「还剩多少」肉眼看不出来 —— 挨个页面点一遍并不靠谱。这个脚本只做一件事：数界面里
还有多少中文片段，按文件从多到少列出来，好决定下一批挑哪一页。

数的是 `<template>` 与 `.ts` 的非注释行：**注释里的中文是给人看的，不用翻译**，
`<script>` / `<style>` 也基本是注释与样式，所以不数。

**刻意不做成 CI 门禁**：数量归零之前它一直在报数，卡在 CI 里只会变成噪音。真要卡，
卡「不许增加」才有意义，而那得先有一份基线。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "web" / "src"

CHINESE = re.compile(r"[\u4e00-\u9fff]+")
TEMPLATE = re.compile(r"<template>(.*)</template>", re.S)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)

# 列出多少个文件，其余合并成一行
TOP = 12


def count_vue(path: Path) -> int:
    match = TEMPLATE.search(path.read_text(encoding="utf-8"))
    if not match:
        return 0
    return len(CHINESE.findall(HTML_COMMENT.sub("", match.group(1))))


def count_ts(path: Path) -> int:
    lines = path.read_text(encoding="utf-8").splitlines()
    body = [line for line in lines if not line.strip().startswith(("//", "*", "/*"))]
    return len(CHINESE.findall("\n".join(body)))


def main() -> int:
    counts: list[tuple[int, str]] = []
    for path in sorted(SRC.rglob("*")):
        # 文案表本身不算「待迁移」—— 它就是迁移的去处（把中文放进去是完工，不是欠账）
        if "locales" in path.parts:
            continue
        if path.suffix == ".vue":
            found = count_vue(path)
        elif path.suffix == ".ts":
            found = count_ts(path)
        else:
            continue
        if found:
            counts.append((found, str(path.relative_to(ROOT))))

    counts.sort(reverse=True)
    total = sum(found for found, _ in counts)

    print(f"还有 {total} 处中文没接进 i18n（{len(counts)} 个文件）")
    print()
    for found, name in counts[:TOP]:
        print(f"  {found:4d}  {name}")
    if len(counts) > TOP:
        print(f"  …     另外 {len(counts) - TOP} 个文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
