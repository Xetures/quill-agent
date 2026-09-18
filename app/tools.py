"""工具页面。

布局（按行）：
    1. 标题
    2. 搜索区：名称输入框 / 分类下拉 / 清空按钮（输入或选择后自动过滤，联合生效）
    3. 工具列表：序号 / 工具名称 / 工具分类 / 功能简介 / 是否开启

工具定义来自代码（quill_agent.tools.registry），页面只负责筛选和开关。
筛选是纯内存过滤，不涉及任何存储查询 —— 工具总量本来就很小。
"""

from __future__ import annotations

import streamlit as st

from quill_agent.tools import ToolSpec, registry

# 列表列宽：序号 / 名称 / 分类 / 简介 / 开关
COLUMN_WEIGHTS = [1, 3, 2, 7, 2]

# 分类下拉里的「不限」选项
ALL_CATEGORIES = "全部分类"

NAME_KEY = "tool_filter_name"
CATEGORY_KEY = "tool_filter_category"


def clear_filters() -> None:
    """清空两个筛选条件。

    这个函数作为按钮回调执行 —— 回调发生在重跑之前，
    此时修改控件的会话状态是安全的。
    """
    st.session_state[NAME_KEY] = ""
    st.session_state[CATEGORY_KEY] = ALL_CATEGORIES


def render_search() -> tuple[str, str]:
    """第 2 行：名称 + 分类联合筛选。

    两个控件都即时生效：改动后脚本重跑，列表随之更新，所以不需要「搜索」按钮。
    """
    name_col, category_col, button_col = st.columns([4, 3, 1], vertical_alignment="bottom")

    with name_col:
        keyword = st.text_input(
            "工具名称",
            placeholder="输入名称模糊匹配，回车生效",
            key=NAME_KEY,
        )

    with category_col:
        category = st.selectbox(
            "分类",
            options=[ALL_CATEGORIES, *registry.categories()],
            key=CATEGORY_KEY,
        )

    with button_col:
        st.button("清空", use_container_width=True, on_click=clear_filters)

    return keyword, "" if category == ALL_CATEGORIES else category


def render_table(items: list[ToolSpec]) -> None:
    """第 3 行：五列工具列表，最后一列是开关。"""
    header = st.columns(COLUMN_WEIGHTS)
    titles = ["序号", "工具名称", "工具分类", "功能简介", "是否开启"]
    for col, text in zip(header, titles, strict=True):
        col.caption(text)

    st.divider()

    if not items:
        st.info("没有匹配的工具。")
        return

    for index, tool in enumerate(items, start=1):
        number_col, name_col, category_col, desc_col, switch_col = st.columns(COLUMN_WEIGHTS)
        number_col.write(index)
        name_col.write(tool.name)
        category_col.write(tool.category)
        desc_col.write(tool.description)

        with switch_col:
            enabled = st.toggle(
                "启用",
                value=tool.enabled,
                key=f"tool_toggle_{tool.name}",
                label_visibility="collapsed",
            )

        # 与持久化的状态不一致，说明用户刚切换过 -> 落盘。
        # 本轮已经渲染完，下一次重跑会读到新状态。
        if enabled != tool.enabled:
            registry.set_enabled(tool.name, enabled)


def main() -> None:
    # 第 1 行：标题
    st.title("工具")

    keyword, category = render_search()
    render_table(registry.search(keyword=keyword, category=category))


main()
