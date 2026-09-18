"""归档页面。

页面自上而下：

    1. 工具行：名称搜索（模糊匹配）+ 全部删除（红色，需输入确认短语）
    2. 归档列表：序号 / 名称 / 归档时间 / 恢复 + 删除

恢复和单条删除属于常规操作，弹窗点一下确认即可；
「全部删除」不可逆、影响面大，要求一字不差地输入确认短语才放行。

弹窗的开关状态统一放在 session_state 里，由 main() 末尾渲染 —— 原因见那里的注释。
"""

from __future__ import annotations

import re

import streamlit as st

from quill_agent.config import get_settings
from quill_agent.history import ConversationStore

# 全部删除时要求输入的确认短语，必须完全一致才会执行
DELETE_PHRASE = "我知晓此删除操作的后果，并确定删除"

# 搜索关键词：点「搜索」才更新，避免边打字边过滤
SEARCH_KEY = "archived_keyword"

# 弹窗开关状态。恢复/删除存 {id, title}（连标题一起记住，弹窗里就不必回目录查），
# 全部删除只存一个布尔值；取不到或为假都表示「不显示」。
RESTORE_KEY = "archived_restore_target"
DELETE_KEY = "archived_delete_target"
DELETE_ALL_KEY = "archived_delete_all_open"

# 列表列宽：序号 / 名称 / 归档时间 / 操作
COLUMN_WEIGHTS = [1, 5, 4, 3]

# 「全部删除」用红色 —— Streamlit 的按钮只有主色/次要色，没有危险色，只能自己上样式
DANGER_CSS = """
<style>
.st-key-delete_all button {
    background-color: #dc2626 !important;
    border-color: #dc2626 !important;
    color: #ffffff !important;
}
.st-key-delete_all button:hover {
    background-color: #b91c1c !important;
    border-color: #b91c1c !important;
    color: #ffffff !important;
}
</style>
"""


def build_store() -> ConversationStore:
    """每次运行都重新读目录，保证列表反映最新状态。"""
    store = ConversationStore(get_settings().conversations_dir)
    store.ensure_dirs()
    return store


def close_dialog(key: str) -> None:
    """把弹窗状态清掉，下一次重跑时它就不会再渲染。"""
    st.session_state.pop(key, None)


@st.dialog("全部删除归档")
def delete_all_dialog(store: ConversationStore) -> None:
    """危险操作：输入确认短语且完全一致，才真正执行。"""
    st.write(f"如果要删除全部归档文件，请在下方输入「{DELETE_PHRASE}」")

    text = st.text_input("确认短语", label_visibility="collapsed")
    if not st.button("确认删除", type="primary", use_container_width=True):
        return

    # 正则整串匹配：多一个字、少一个字、多一个空格都不算一致
    if not re.fullmatch(re.escape(DELETE_PHRASE), text.strip()):
        # 只报错、不关弹窗，让用户看到提示后接着改
        st.error("输入的内容与提示不一致，未执行删除。")
        return

    removed = store.remove_all_archived()
    close_dialog(DELETE_ALL_KEY)
    st.session_state[SEARCH_KEY] = ""
    st.toast(f"已删除 {removed} 个归档")
    st.rerun()


@st.dialog("恢复会话")
def restore_dialog(store: ConversationStore, target: dict) -> None:
    """恢复前的二次确认。"""
    st.write(f"把「{target['title']}」恢复到会话列表？")

    cancel_col, confirm_col = st.columns(2)
    if cancel_col.button("取消", use_container_width=True):
        close_dialog(RESTORE_KEY)
        st.rerun()
    if confirm_col.button("确认恢复", type="primary", use_container_width=True):
        store.restore(target["id"])
        close_dialog(RESTORE_KEY)
        st.toast("已恢复")
        st.rerun()


@st.dialog("删除归档")
def delete_dialog(store: ConversationStore, target: dict) -> None:
    """删除前的二次确认。"""
    st.write(f"永久删除「{target['title']}」？该操作不可撤销。")

    cancel_col, confirm_col = st.columns(2)
    if cancel_col.button("取消", use_container_width=True):
        close_dialog(DELETE_KEY)
        st.rerun()
    if confirm_col.button("确认删除", type="primary", use_container_width=True):
        store.remove_archived(target["id"])
        close_dialog(DELETE_KEY)
        st.toast("已删除")
        st.rerun()


def render_search() -> str:
    """名称输入框 + 确认按钮。

    用 form 包起来：里面的控件在提交前不会触发脚本重跑，
    所以不存在「边打字边过滤」的问题，回车也能直接提交。

    Returns:
        当前生效的关键词；空串表示不过滤。
    """
    with st.form("archived_search", border=False):
        input_col, button_col = st.columns([4, 1], vertical_alignment="bottom")
        with input_col:
            keyword = st.text_input(
                "名称",
                value=st.session_state.get(SEARCH_KEY, ""),
                placeholder="输入名称，模糊匹配",
                label_visibility="collapsed",
            )
        with button_col:
            submitted = st.form_submit_button("搜索", use_container_width=True)

    if submitted:
        st.session_state[SEARCH_KEY] = keyword.strip()

    return st.session_state.get(SEARCH_KEY, "")


def render_toolbar(store: ConversationStore) -> str:
    """第 1 块：搜索 + 全部删除。"""
    st.markdown(DANGER_CSS, unsafe_allow_html=True)

    search_col, delete_col = st.columns([5, 1], vertical_alignment="bottom")
    with search_col:
        keyword = render_search()
    with delete_col:
        with st.container(key="delete_all"):
            if st.button("🗑️ 全部删除", use_container_width=True):
                st.session_state[DELETE_ALL_KEY] = True

    return keyword


def render_list(store: ConversationStore, keyword: str) -> None:
    """第 2 块：归档列表。"""
    metas = store.list_archived()

    if keyword:
        # 模糊匹配：忽略大小写的「包含」即可
        lowered = keyword.lower()
        metas = [meta for meta in metas if lowered in meta.title.lower()]

    if not metas:
        st.caption("没有匹配的归档。" if keyword else "归档是空的。")
        return

    header = st.columns(COLUMN_WEIGHTS, vertical_alignment="center")
    for col, text in zip(header, ["序号", "名称", "归档时间", "操作"], strict=True):
        col.caption(text)

    for index, meta in enumerate(metas, start=1):
        cols = st.columns(COLUMN_WEIGHTS, vertical_alignment="center")
        cols[0].write(str(index))
        cols[1].write(meta.title)
        cols[2].write(meta.archived_text)

        with cols[3]:
            restore_col, delete_col = st.columns(2, gap="small")
            # key 必须带 id，否则多行按钮会互相冲突
            if restore_col.button(
                "♻️ 恢复", key=f"restore_{meta.id}", use_container_width=True
            ):
                st.session_state[RESTORE_KEY] = {
                    "id": meta.id,
                    "title": meta.title,
                }
            if delete_col.button(
                "🗑️ 删除", key=f"delete_{meta.id}", use_container_width=True
            ):
                st.session_state[DELETE_KEY] = {"id": meta.id, "title": meta.title}


def main() -> None:
    st.title("归档")
    store = build_store()

    keyword = render_toolbar(store)
    render_list(store, keyword)

    # 弹窗放在最后，按状态渲染。
    # 不能写成「在按钮的 if 分支里直接调用弹窗」—— 弹窗内任何按钮一点就会重跑，
    # 重跑时那个 if 不再成立，弹窗会立刻消失，连错误提示都来不及看。
    if st.session_state.get(DELETE_ALL_KEY):
        delete_all_dialog(store)
    if target := st.session_state.get(RESTORE_KEY):
        restore_dialog(store, target)
    if target := st.session_state.get(DELETE_KEY):
        delete_dialog(store, target)


main()
