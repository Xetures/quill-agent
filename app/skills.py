"""技能页面。

布局（按行）：
    1. 标题
    2. 说明：技能是什么、正文放在哪
    3. 技能列表：序号 / 技能名称 / 适用场景 / 是否启用

技能正文由用户在 skills/ 目录里维护（一个技能一个子目录，入口是 SKILL.md），
页面只负责展示元信息和开关 —— 和「提示词」页的思路一致。
"""

from __future__ import annotations

import streamlit as st

from quill_agent.config import get_settings
from quill_agent.skills import SkillLibrary, SkillMeta
from quill_agent.store import SkillStateStore

# 列表列宽：序号 / 名称 / 适用场景 / 开关
COLUMN_WEIGHTS = [1, 3, 8, 2]

# 新建技能的示例目录名，用于空态提示
EXAMPLE_SKILL = "代码审查"


def build_library() -> SkillLibrary:
    """每次运行都重新扫目录，保证列表反映最新状态。"""
    library = SkillLibrary(get_settings().skills_dir)
    library.ensure_dir()
    return library


def build_state_store() -> SkillStateStore:
    """技能开关的持久化。"""
    return SkillStateStore(get_settings().skills_state_path)


def render_hint() -> None:
    """第 2 行：告诉用户技能正文该放在哪。"""
    st.caption(
        f"技能是一份「这类任务该怎么做」的说明。正文放在 "
        f"`{get_settings().skills_dir}/{EXAMPLE_SKILL}/SKILL.md` 这样的位置；"
        "模型平时只看到名称和适用场景，判断需要时才用 read_skill 把全文取回去。"
    )


def render_empty_hint() -> None:
    """一个技能都没有时，把新建方法直接摊开写出来。"""
    st.info(
        "还没有技能。建一个文件就有一个技能了：\n\n"
        f"```\n{get_settings().skills_dir}/{EXAMPLE_SKILL}/SKILL.md\n```\n\n"
        "文件开头可以写一段元信息，说明什么时候该加载它（这是模型判断的唯一依据）：\n\n"
        "```\n---\n"
        "description: 当用户要求审查代码、评估一段实现时使用\n"
        "---\n\n"
        "（正文：这类任务的完整做法，可以写得很长）\n"
        "```"
    )


def render_table(metas: list[SkillMeta], store: SkillStateStore) -> None:
    """第 3 行：四列技能列表，最后一列是开关。"""
    header = st.columns(COLUMN_WEIGHTS)
    for col, text in zip(header, ["序号", "技能名称", "适用场景", "是否启用"], strict=True):
        col.caption(text)

    st.divider()

    if not metas:
        render_empty_hint()
        return

    states = store.load()

    for index, meta in enumerate(metas, start=1):
        number_col, name_col, desc_col, switch_col = st.columns(COLUMN_WEIGHTS)
        number_col.write(index)
        name_col.write(meta.name)
        desc_col.write(meta.description or "（未填写，模型看不出什么时候该用它）")

        enabled = states.get(meta.name, True)
        with switch_col:
            toggled = st.toggle(
                "启用",
                value=enabled,
                key=f"skill_toggle_{meta.name}",
                label_visibility="collapsed",
            )

        # 和持久化状态不一致，说明用户刚切换过 -> 落盘。
        # 本轮已经渲染完，下一次重跑会读到新值。
        if toggled != enabled:
            store.set(meta.name, toggled)


def main() -> None:
    # 第 1 行：标题
    st.title("技能")

    library = build_library()

    render_hint()
    render_table(library.list_meta(), build_state_store())


main()
