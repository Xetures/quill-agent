"""提示词页面。

布局（按行）：
    1. 标题
    2. 添加模式按钮 → 弹窗：模式名称 + 六个下拉选择 + 确定
    3. 模式列表：序号 / 模式名称 / 模式设置 / 删除

一个「模式」= 从六类提示词里各挑一个（可以不挑）拼成的一套提示词。
提示词正文本身不由界面维护，用户在项目根目录的 prompt/ 文件夹里
直接新建 / 修改 / 删除 .md 文件即可，界面只负责引用它们。
"""

from __future__ import annotations

from uuid import uuid4

import streamlit as st

from quill_agent.config import get_settings
from quill_agent.models import PromptMode, model_choice_key
from quill_agent.prompts import PROMPT_CATEGORIES, PromptLibrary
from quill_agent.store import ModelStore, PromptModeStore

# 下拉里的「不选」占位项
EMPTY_CHOICE = "（不选）"

# 「偏好模型」的「不指定」项：空串表示切到这个模式时不改动当前模型
NO_MODEL = ""

# 各列宽度：序号 / 模式名称 / 模式设置 / 操作
COLUMN_WEIGHTS = [1, 3, 6, 3]

# 弹窗的会话状态键；每次打开换一个令牌，控件随之整体重新初始化
EDITOR_TOKEN_KEY = "mode_editor_token"
EDITOR_ITEM_KEY = "mode_editor_item_id"
EDITOR_OPEN_KEY = "mode_editor_open"


def build_library() -> PromptLibrary:
    """准备提示词目录（六类子目录不存在时自动创建）。"""
    library = PromptLibrary(get_settings().prompt_dir)
    library.ensure_dirs()
    return library


def build_store() -> PromptModeStore:
    """每次运行都重新读取，保证看到的是最新数据。"""
    return PromptModeStore(get_settings().modes_path)


def build_model_store() -> ModelStore:
    """读取模型配置，供「偏好模型」下拉使用。"""
    return ModelStore(get_settings().models_path)


def model_options() -> dict[str, str]:
    """「偏好模型」下拉的选项：稳定标识 -> 展示名。

    标识口径与任务页的模型选择完全一致（见 model_choice_key），
    所以从模式里取出来可以直接赋给那边的选择器，不用做任何转换。
    """
    options = {NO_MODEL: "（不指定）"}
    for config in build_model_store().list():
        for name in config.models:
            options[model_choice_key(config.id, name)] = f"{config.name} / {name}"

    return options


def format_settings(mode: PromptMode, library: PromptLibrary) -> str:
    """把模式设置格式化成一行展示文本；引用的文件已不存在时标注出来。"""
    if not mode.settings:
        return "—"

    parts = []
    for category, name in mode.settings.items():
        missing = "" if library.exists(category, name) else "（文件缺失）"
        parts.append(f"{category}：{name}{missing}")

    return "；".join(parts)


def open_editor(store: PromptModeStore, mode: PromptMode | None = None) -> None:
    """打开弹窗；mode 为 None 表示新增，否则编辑该模式。

    只记录「要编辑哪条」，真正的渲染交给主流程 —— 否则脚本一重跑弹窗就没了。
    """
    st.session_state[EDITOR_TOKEN_KEY] = uuid4().hex
    st.session_state[EDITOR_ITEM_KEY] = mode.id if mode else None
    st.session_state[EDITOR_OPEN_KEY] = True


@st.dialog("模式配置")
def mode_editor_dialog(library: PromptLibrary, store: PromptModeStore) -> None:
    """新增 / 编辑模式：填名称，再从六类里各挑一个提示词（都可以不选）。"""
    token = st.session_state[EDITOR_TOKEN_KEY]
    item_id = st.session_state.get(EDITOR_ITEM_KEY)
    current = store.get(item_id) if item_id else None
    prefix = f"mode_{token}"

    name = st.text_input(
        "模式名称",
        value=current.name if current else "",
        placeholder="例如：严谨分析",
        key=f"{prefix}_name",
    )

    # 偏好模型：可选。默认「不指定」—— 那就沿用任务页当前的模型。
    options = model_options()
    option_keys = list(options)
    default_model = current.preferred_model if current else NO_MODEL
    if default_model not in options:
        # 原来指定的模型已经被删掉了，退回「不指定」，别让下拉报错
        default_model = NO_MODEL

    preferred_model = st.selectbox(
        "偏好模型",
        options=option_keys,
        index=option_keys.index(default_model),
        format_func=lambda key: options[key],
        key=f"{prefix}_preferred",
        help="在任务页选到这个模式时会自动切换成它；不指定则保持当前模型不变。",
    )

    st.divider()

    # 六类各一个下拉框；选择结果按「类别 -> 文件名」收集
    selections: dict[str, str] = {}
    for index, category in enumerate(PROMPT_CATEGORIES):
        options = [EMPTY_CHOICE, *library.list_names(category)]
        # 编辑时回填该类别原本选中的提示词；文件若已被删除就退回「不选」
        default = current.settings.get(category, EMPTY_CHOICE) if current else EMPTY_CHOICE
        if default not in options:
            default = EMPTY_CHOICE

        chosen = st.selectbox(
            category,
            options=options,
            index=options.index(default),
            key=f"{prefix}_category_{index}",
        )
        if chosen != EMPTY_CHOICE:
            selections[category] = chosen

    save_col, cancel_col = st.columns(2)
    with save_col:
        if st.button("确定", type="primary", use_container_width=True, key=f"{prefix}_save"):
            if not name.strip():
                st.error("模式名称不能为空")
            else:
                # 六类都不选也是合法的：那就退化成「不带任何系统提示词」的纯问答模式，
                # 适合简单提问 —— 不是每个任务都需要一整套提示词。
                if current is None:
                    store.add(
                        name=name.strip(),
                        settings=selections,
                        preferred_model=preferred_model,
                    )
                else:
                    store.update(
                        current.model_copy(
                            update={
                                "name": name.strip(),
                                "settings": selections,
                                "preferred_model": preferred_model,
                            }
                        )
                    )
                close_editor()
                st.rerun()
    with cancel_col:
        if st.button("取消", use_container_width=True, key=f"{prefix}_cancel"):
            close_editor()
            st.rerun()


def close_editor() -> None:
    """关闭弹窗。"""
    st.session_state[EDITOR_OPEN_KEY] = False


def render_header(store: PromptModeStore) -> None:
    """第 2 行：添加模式按钮，靠右排布。"""
    _, button_col = st.columns([6, 2])
    with button_col:
        if st.button("➕ 添加模式", type="primary", use_container_width=True):
            open_editor(store)


def render_table(library: PromptLibrary, store: PromptModeStore) -> None:
    """第 3 行：四列模式列表。"""
    header = st.columns(COLUMN_WEIGHTS)
    for col, text in zip(header, ["序号", "模式名称", "模式设置", "操作"], strict=True):
        col.caption(text)

    st.divider()

    modes = store.list()
    if not modes:
        st.info("暂无模式，点击右上角「添加模式」新建。")
        return

    for index, mode in enumerate(modes, start=1):
        number_col, name_col, settings_col, action_col = st.columns(COLUMN_WEIGHTS)
        number_col.write(index)
        name_col.write(mode.name)
        settings_col.write(format_settings(mode, library))

        with action_col:
            edit_col, delete_col = st.columns(2, gap="small")
            # key 必须带 id，否则多行按钮会互相冲突
            if edit_col.button("✏️ 编辑", key=f"edit_mode_{mode.id}", use_container_width=True):
                open_editor(store, mode)
            if delete_col.button("🗑️ 删除", key=f"delete_mode_{mode.id}", use_container_width=True):
                store.remove(mode.id)
                st.rerun()


def main() -> None:
    # 第 1 行：标题
    st.title("提示词")

    library = build_library()
    store = build_store()

    render_header(store)
    render_table(library, store)

    # 弹窗按状态渲染：脚本重跑后它依然存在
    if st.session_state.get(EDITOR_OPEN_KEY):
        mode_editor_dialog(library, store)


main()
