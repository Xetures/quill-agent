"""Streamlit 应用入口（新版多页写法）。

启动方式：uv run streamlit run app/app.py

约定：
- 使用 st.navigation 后，本文件是唯一入口，页面列表在这里声明；
- st.set_page_config 只能在入口调用一次，各页面文件里不要再调用；
- 页面文件会作为脚本整体执行，它们自己的 main() 在导航切换时运行；
- 侧边栏的会话列表也在这里渲染 —— 它属于全局 UI，与当前在哪个页面无关。
"""

from __future__ import annotations

import streamlit as st

from quill_agent.config import get_settings
from quill_agent.history import ConversationStore
from quill_agent.preferences import PreferenceStore, draft_key

st.set_page_config(page_title="quill", page_icon="🪶", layout="wide")

pages = [
    st.Page("main.py", title="任务"),
    st.Page("models.py", title="模型"),
    st.Page("prompt.py", title="提示词"),
    st.Page("tools.py", title="工具"),
    st.Page("skills.py", title="技能"),
    st.Page("archived.py", title="归档"),
]


# 任务页的文件名（相对入口脚本）。侧边栏在所有页面都可见，
# 用户在这里点会话，意图是「去聊这个会话」，所以要跳回任务页。
TASK_PAGE = "main.py"


def build_store() -> ConversationStore:
    """每次都重新读目录，保证列表反映最新状态。"""
    store = ConversationStore(get_settings().conversations_dir)
    store.ensure_dirs()
    return store


def switch_to(store: ConversationStore, conv_id: str) -> None:
    """切换当前会话：记下 id 并加载它的消息。

    改动会在下一次脚本重跑时生效，所以调用方随后要 st.rerun()。
    """
    st.session_state.current_conversation_id = conv_id
    st.session_state.messages = store.load(conv_id)


def start_new(store: ConversationStore) -> None:
    """新建空会话并切过去。"""
    switch_to(store, store.create())


def clear_draft(conv_id: str) -> None:
    """会话被归档/删除后，顺手清掉它的草稿，别在偏好文件里留孤儿键。"""
    PreferenceStore(get_settings().preferences_path).remove(draft_key(conv_id))


def render_sidebar() -> None:
    """侧边栏：会话列表 —— 点击切换，📦 归档。"""
    store = build_store()
    current = st.session_state.get("current_conversation_id")

    with st.sidebar:
        st.subheader("会话")

        if st.button("➕ 新会话", use_container_width=True, type="primary"):
            start_new(store)
            # 新建会话就是为了聊天，直接跳过去；switch_page 自己会重跑，不必再 st.rerun()
            st.switch_page(TASK_PAGE)

        conversations = store.list_active()
        if not conversations:
            st.caption("还没有会话")

        for meta in conversations:
            title_col, archive_col = st.columns([5, 1], vertical_alignment="center")

            with title_col:
                if st.button(
                    meta.title,
                    key=f"conv_{meta.id}",
                    use_container_width=True,
                    # 当前会话用主色高亮
                    type="primary" if meta.id == current else "secondary",
                    help=f"最后更新：{meta.updated_text}",
                ):
                    switch_to(store, meta.id)
                    # 不论当前在哪个页面，点会话都回到任务页
                    st.switch_page(TASK_PAGE)

            with archive_col:
                if st.button("📦", key=f"archive_{meta.id}", help="归档（空会话直接删除）"):
                    archived = store.archive(meta.id)
                    clear_draft(meta.id)
                    # 空会话是被删掉的，归档列表里不会出现，这里说明一下，
                    # 否则用户会疑惑「归档后怎么哪都找不到」
                    st.toast("已归档" if archived else "空会话已删除")

                    # 归档的正好是当前会话：切到剩下的第一个，没有就新建一个
                    if meta.id == current:
                        remaining = store.list_active()
                        if remaining:
                            switch_to(store, remaining[0].id)
                        else:
                            start_new(store)

                    st.rerun()


pg = st.navigation(pages, position="sidebar")
pg.run()

render_sidebar()
