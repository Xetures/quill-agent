"""模型管理页面。

自上而下：标题 -> 添加模型按钮 -> 四列列表（名称 / 模型 / URL / 操作）。

一条记录 = 一份连接信息（名称 / URL / Key / 协议）+ 该连接下可用的多个模型。
一个 API 通常能用多个模型，所以模型是列表，改 Key 也只需要改一条记录。

编辑弹窗的布局（按行）：
    1. 名称 + 协议
    2. URL
    3. API Key
    4. 获取模型列表 / 手动添加
    5. 模型列表（模型名 + 删除按钮）
    6. 确定 / 连通测试

「获取模型列表」会在同一个弹窗内切到第二视图（选择窗口）：勾选确认后，
选中的模型回填到第 5 行的模型列表。Streamlit 不允许弹窗里再开弹窗，
所以用视图切换代替嵌套。
"""

from __future__ import annotations

from uuid import uuid4

import streamlit as st

from quill_agent.config import get_settings
from quill_agent.core import fetch_models, test_connection
from quill_agent.models import ModelConfig, Protocol, check_api_key
from quill_agent.store import ModelStore

# 各列宽度：操作列要放下「编辑 + 删除」两个带文字的按钮，需要给足空间
COLUMN_WEIGHTS = [3, 4, 4, 3]

# 编辑弹窗的会话状态键。每次打开都会换一个「令牌」，
# 令牌变了，弹窗内所有控件的 key 也就变了，从而整体重新初始化，不残留上次内容。
EDITOR_TOKEN_KEY = "editor_token"
EDITOR_ITEM_KEY = "editor_item_id"
EDITOR_OPEN_KEY = "editor_open"


def build_store() -> ModelStore:
    """每次运行都重新读取文件，保证页面上看到的是最新数据。"""
    return ModelStore(get_settings().models_path)


def open_editor(store: ModelStore, item: ModelConfig | None = None) -> None:
    """打开编辑弹窗；item 为 None 表示新增。

    这里只记录「要编辑哪条」，真正的渲染交给主流程统一处理 ——
    因为 Streamlit 的弹窗只在函数被调用的那一轮显示，脚本一重跑就没了。
    放到主流程里按状态渲染，弹窗才能在重跑后继续存在。
    """
    st.session_state[EDITOR_TOKEN_KEY] = uuid4().hex
    st.session_state[EDITOR_ITEM_KEY] = item.id if item else None
    st.session_state[EDITOR_OPEN_KEY] = True


def close_editor() -> None:
    """关闭编辑弹窗。"""
    st.session_state[EDITOR_OPEN_KEY] = False


@st.dialog("模型配置")
def model_editor_dialog(store: ModelStore) -> None:
    """弹窗主体：在「表单视图」与「模型选择视图」之间切换。"""
    token = st.session_state[EDITOR_TOKEN_KEY]
    item_id = st.session_state.get(EDITOR_ITEM_KEY)
    item = store.get(item_id) if item_id else None
    prefix = f"editor_{token}"

    # 本弹窗用到的会话状态键
    models_key = f"{prefix}_models"  # 当前已选的模型名列表
    view_key = f"{prefix}_view"  # 当前视图：form / picking
    options_key = f"{prefix}_options"  # 从接口拉取到的可选模型
    manual_key = f"{prefix}_manual"  # 手动输入区是否展开

    # 首次渲染时用原记录初始化模型列表（令牌变了就重新初始化）
    if models_key not in st.session_state:
        st.session_state[models_key] = list(item.models) if item else []

    if st.session_state.get(view_key) == "picking":
        render_picker_view(prefix, models_key, view_key, options_key)
        return

    render_editor_form(store, item, prefix, models_key, view_key, options_key, manual_key)


def render_editor_form(
    store: ModelStore,
    item: ModelConfig | None,
    prefix: str,
    models_key: str,
    view_key: str,
    options_key: str,
    manual_key: str,
) -> None:
    """表单视图：名称 / URL / API Key / 两个按钮 / 模型列表 / 确定与连通测试。"""
    # 第 1 行：名称 + 协议（协议决定 API Key 的格式要求）
    name_col, protocol_col = st.columns([3, 2])
    with name_col:
        name = st.text_input(
            "名称",
            value=item.name if item else "",
            placeholder="例如：DeepSeek 官方",
            key=f"{prefix}_name",
        )

    protocols = list(Protocol)
    with protocol_col:
        protocol = st.selectbox(
            "协议",
            options=protocols,
            index=protocols.index(item.protocol) if item else 0,
            format_func=lambda option: option.label,
            key=f"{prefix}_protocol",
        )

    # 第 2 行：URL
    base_url = st.text_input(
        "URL",
        value=item.base_url if item else "",
        placeholder="例如：https://api.deepseek.com",
        key=f"{prefix}_base_url",
    )

    # 第 3 行：API Key
    api_key = st.text_input(
        "API Key",
        value=item.api_key if item else "",
        type="password",
        placeholder="留空表示无需鉴权",
        key=f"{prefix}_api_key",
    )

    # 第 4 行：获取模型列表 / 手动添加
    fetch_col, manual_col = st.columns(2)
    with fetch_col:
        if st.button("获取模型列表", use_container_width=True, key=f"{prefix}_fetch"):
            load_options(base_url, api_key, protocol, options_key, view_key)
    with manual_col:
        if st.button("手动添加", use_container_width=True, key=f"{prefix}_manual_btn"):
            st.session_state[manual_key] = True

    # 点过「手动添加」后才展开输入区
    if st.session_state.get(manual_key):
        render_manual_input(prefix, models_key)

    # 第 5 行：模型列表
    render_model_list(prefix, models_key)

    # 第 6 行：确定 / 连通测试
    save_col, test_col = st.columns(2)
    with save_col:
        if st.button("确定", type="primary", use_container_width=True, key=f"{prefix}_save"):
            save(store, item, name, base_url, protocol, api_key, st.session_state[models_key])
    with test_col:
        if st.button("连通测试", use_container_width=True, key=f"{prefix}_test"):
            result = test_connection(
                protocol=protocol,
                base_url=base_url.strip(),
                api_key=api_key.strip(),
            )
            if result.ok:
                st.success(result.message)
            else:
                st.error(result.message)

    # 放弃编辑（「确定」保存后会自动关闭）
    if st.button("取消", use_container_width=True, key=f"{prefix}_cancel"):
        close_editor()
        st.rerun()


def render_picker_view(prefix: str, models_key: str, view_key: str, options_key: str) -> None:
    """第二视图：列出接口返回的模型，勾选后回填到模型列表。"""
    available: list[str] = st.session_state.get(options_key, [])
    current: list[str] = st.session_state[models_key]

    st.caption(f"接口返回 {len(available)} 个模型，勾选后点「确认」。")

    # 选项 = 接口返回的 + 已手动添加的（去重且保持顺序）
    options = list(dict.fromkeys([*available, *current]))

    # 每次进入都换一个 key，避免残留上一次的勾选状态
    seq_key = f"{prefix}_picker_seq"
    if seq_key not in st.session_state:
        st.session_state[seq_key] = uuid4().hex[:8]

    chosen = st.multiselect(
        "选择模型",
        options=options,
        default=current,
        key=f"{prefix}_picker_{st.session_state[seq_key]}",
        label_visibility="collapsed",
    )

    confirm_col, back_col = st.columns(2)
    with confirm_col:
        if st.button("确认", type="primary", use_container_width=True, key=f"{prefix}_confirm"):
            st.session_state[models_key] = list(chosen)
            st.session_state[view_key] = "form"
            st.session_state.pop(seq_key, None)  # 下次进入重新生成 key
            st.rerun()
    with back_col:
        if st.button("返回", use_container_width=True, key=f"{prefix}_back"):
            st.session_state[view_key] = "form"
            st.session_state.pop(seq_key, None)
            st.rerun()


def load_options(
    base_url: str,
    api_key: str,
    protocol: Protocol,
    options_key: str,
    view_key: str,
) -> None:
    """拉取可选模型；成功后切到选择视图。"""
    with st.spinner("正在获取模型列表…"):
        result = fetch_models(
            protocol=protocol,
            base_url=base_url.strip(),
            api_key=api_key.strip(),
        )

    if not result.ok:
        st.error(result.message)
        return

    if not result.models:
        st.warning("接口没有返回任何模型，可以改用「手动添加」。")
        return

    st.session_state[options_key] = result.models
    st.session_state[view_key] = "picking"
    st.rerun()


def render_manual_input(prefix: str, models_key: str) -> None:
    """手填模型名的输入区。

    这里刻意不用 st.form：表单提交在 dialog 内会把弹窗关掉。
    改为普通按钮 + 「序号换 key」的方式，加入成功后换一个序号，
    输入框就会以新 key 重建，从而自动清空。
    """
    seq_key = f"{prefix}_manual_seq"
    if seq_key not in st.session_state:
        st.session_state[seq_key] = uuid4().hex[:6]
    input_key = f"{prefix}_manual_value_{st.session_state[seq_key]}"

    input_col, add_col = st.columns([5, 1])
    with input_col:
        st.text_input(
            "模型名",
            placeholder="输入模型名，例如 deepseek-chat",
            label_visibility="collapsed",
            key=input_key,
        )
    with add_col:
        clicked = st.button("加入", use_container_width=True, key=f"{prefix}_manual_add")

    if not clicked:
        return

    model_name = st.session_state.get(input_key, "").strip()
    if not model_name:
        return

    current: list[str] = st.session_state[models_key]
    if model_name in current:
        st.warning("该模型已在列表中")
        return

    current.append(model_name)
    st.session_state[seq_key] = uuid4().hex[:6]  # 换 key -> 输入框自动清空
    st.rerun()


def render_model_list(prefix: str, models_key: str) -> None:
    """第 5 行：已选模型列表，每行是「模型名 + 删除按钮」。"""
    st.caption("模型列表")

    current: list[str] = st.session_state[models_key]
    if not current:
        st.caption("还没有模型，可用上方「获取模型列表」或「手动添加」。")
        return

    for index, model_name in enumerate(list(current)):
        name_col, delete_col = st.columns([6, 1])
        name_col.write(model_name)
        if delete_col.button("🗑️", key=f"{prefix}_del_{index}", help="移除"):
            current.remove(model_name)
            st.rerun()


def save(
    store: ModelStore,
    item: ModelConfig | None,
    name: str,
    base_url: str,
    protocol: Protocol,
    api_key: str,
    models: list[str],
) -> None:
    """第 6 行「确定」：校验通过后写入存储。"""
    if not name.strip():
        st.error("名称不能为空")
        return

    if not models:
        st.error("请至少添加一个模型")
        return

    key = api_key.strip()
    error = check_api_key(protocol, key)
    if error:
        st.error(error)
        return

    values = {
        "name": name.strip(),
        "base_url": base_url.strip(),
        "protocol": protocol,
        "api_key": key,
        "models": list(models),
    }

    if item is None:
        store.add(**values)
    else:
        store.update(item.model_copy(update=values))

    close_editor()
    st.rerun()


@st.dialog("删除确认")
def delete_dialog(store: ModelStore, item: ModelConfig) -> None:
    """删除前二次确认，避免误删。"""
    st.write(f"确定要删除「{item.name}」吗？该操作不可恢复。")

    if st.button("确认删除", type="primary"):
        store.remove(item.id)
        st.rerun()


def render_header(store: ModelStore) -> None:
    """标题下方的操作区：添加按钮靠右排布。"""
    _, button_col = st.columns([6, 2])
    with button_col:
        if st.button("➕ 添加模型", type="primary", use_container_width=True):
            open_editor(store)


def render_table(store: ModelStore) -> None:
    """四列列表：表头 + 数据行。"""
    header = st.columns(COLUMN_WEIGHTS)
    for col, text in zip(header, ["名称", "模型", "URL", "操作"], strict=True):
        col.caption(text)

    st.divider()

    items = store.list()
    if not items:
        st.info("暂无模型配置，点击右上角「添加模型」新建。")
        return

    for item in items:
        name_col, model_col, url_col, action_col = st.columns(COLUMN_WEIGHTS)
        name_col.write(item.name)
        model_col.write("、".join(item.models) if item.models else "—")
        url_col.write(item.base_url or "—")

        with action_col:
            edit_col, delete_col = st.columns(2, gap="small")
            # key 必须带 id，否则多行按钮会互相冲突
            if edit_col.button("✏️ 编辑", key=f"edit_{item.id}", use_container_width=True):
                open_editor(store, item)
            if delete_col.button("🗑️ 删除", key=f"delete_{item.id}", use_container_width=True):
                delete_dialog(store, item)


def main() -> None:
    st.title("模型管理")
    store = build_store()

    render_header(store)
    render_table(store)

    # 弹窗按状态渲染，而不是「点按钮时顺手调用」——
    # 后者一旦脚本重跑（弹窗里几乎每个按钮都会触发重跑）弹窗就消失了。
    if st.session_state.get(EDITOR_OPEN_KEY):
        model_editor_dialog(store)


main()
