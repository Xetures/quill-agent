"""记忆页面。

布局（按行）：
    1. 标题
    2. 说明 + 手动添加 + 清空
    3. 记忆列表：序号 / 内容 / 记录时间 / 是否生效 / 删除

记忆由模型调用 remember 工具写入，也可以在这里手动加。页面负责查看和管理 ——
过时的可以关掉（留着以后参考），记错的可以直接删。
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from quill_agent.config import get_settings
from quill_agent.memory import MAX_MEMORIES, MemoryStore

# 列表列宽：序号 / 内容 / 记录时间 / 是否生效 / 操作
COLUMN_WEIGHTS = [1, 8, 3, 2, 2]

# 手动添加的输入框键
NEW_MEMORY_KEY = "memory_new_text"

# 一次性的结果提示。回调里不能直接 st.error —— 随后的重跑会把提示冲掉，
# 所以先存下来，由主流程在重跑之后渲染。
FLASH_KEY = "memory_flash"


def build_store() -> MemoryStore:
    """每次运行都重新读文件，保证看到的是最新数据。"""
    return MemoryStore(get_settings().memory_path)


def format_time(text: str) -> str:
    """把 ISO 时间显示成紧凑格式；解析不了就原样显示。"""
    try:
        return datetime.fromisoformat(text).strftime("%m-%d %H:%M")
    except ValueError:
        return text or "—"


def render_flash() -> None:
    """显示上一步的结果（添加成功 / 添加失败的原因）。"""
    flash = st.session_state.pop(FLASH_KEY, None)
    if flash is None:
        return

    kind, message = flash
    if kind == "error":
        st.error(message)
    else:
        st.success(message)


def add_manual(store: MemoryStore) -> None:
    """手动添加一条记忆（按钮回调）。

    走的就是 MemoryStore.add，所以去重、长度、上限规则和 remember 工具完全一致。
    """
    text = st.session_state.get(NEW_MEMORY_KEY, "")
    if not text.strip():
        return

    try:
        store.add(text)
    except ValueError as exc:
        st.session_state[FLASH_KEY] = ("error", str(exc))
        return

    st.session_state[NEW_MEMORY_KEY] = ""
    st.session_state[FLASH_KEY] = ("success", "已添加")


def render_toolbar(store: MemoryStore) -> None:
    """第 2 行：说明 + 手动添加 + 清空。"""
    st.caption(
        "记忆是跨会话保留的长期事实，来自模型调用 remember 工具，也可以在这里手动添加。"
        f"启用中的记忆会以清单形式进入每一轮对话，上限 {MAX_MEMORIES} 条。"
    )

    input_col, add_col, clear_col = st.columns([7, 1, 2], vertical_alignment="bottom")
    with input_col:
        st.text_input(
            "手动添加",
            placeholder="例如：偏好简洁回答，不要啰嗦的总结",
            key=NEW_MEMORY_KEY,
            label_visibility="collapsed",
        )
    with add_col:
        st.button(
            "添加",
            type="primary",
            use_container_width=True,
            on_click=add_manual,
            args=(store,),
        )
    with clear_col:
        if st.button("清空全部", use_container_width=True):
            removed = store.clear()
            st.toast(f"已清空 {removed} 条记忆")
            st.rerun()


def render_table(store: MemoryStore) -> None:
    """第 3 行：五列记忆列表，中间是开关，最后是删除。"""
    header = st.columns(COLUMN_WEIGHTS)
    for col, text in zip(header, ["序号", "内容", "记录时间", "是否生效", "操作"], strict=True):
        col.caption(text)

    st.divider()

    items = store.list()
    if not items:
        st.info(
            "还没有记忆。两个来源：\n\n"
            "- 对话里让模型「记住这件事」，它会调用 `remember` 工具写入；\n"
            "- 用上面的输入框手动加一条。"
        )
        return

    for index, item in enumerate(items, start=1):
        number_col, text_col, time_col, switch_col, action_col = st.columns(COLUMN_WEIGHTS)
        number_col.write(index)
        text_col.write(item.text)
        time_col.write(format_time(item.created_at))

        with switch_col:
            toggled = st.toggle(
                "启用",
                value=item.enabled,
                key=f"memory_toggle_{item.id}",
                label_visibility="collapsed",
            )

        # 和持久化状态不一致，说明用户刚切换过 -> 落盘，下一次重跑读到新值
        if toggled != item.enabled:
            store.set_enabled(item.id, toggled)

        with action_col:
            if st.button("🗑️ 删除", key=f"memory_delete_{item.id}", use_container_width=True):
                store.remove(item.id)
                st.rerun()


def main() -> None:
    # 第 1 行：标题
    st.title("记忆")

    store = build_store()

    render_flash()
    render_toolbar(store)
    render_table(store)


main()
