"""任务页面。

页面自上而下：

    1. 标题
    2. 输出区：展示模型返回的文本（固定高度、可滚动）
    3. 功能区：文件上传 / 模式选择 / 模型选择（图标形态）
    4. 输入框：提交按钮内嵌在输入框右侧，停靠在功能区下方

本文件只负责 UI 逻辑：收集参数 -> 调用 run_agent() -> 渲染返回结果。
Agent 循环、API 请求、响应解析全部留白在文件末尾的 run_agent() 里。
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import streamlit as st

from quill_agent.agent import AgentResult, ToolStep, run_agent_stream
from quill_agent.config import get_settings
from quill_agent.history import ConversationStore
from quill_agent.models import ModelChoice, ModelConfig, PromptMode, model_choice_key
from quill_agent.preferences import PreferenceStore, draft_key
from quill_agent.store import ModelStore, PromptModeStore
from quill_agent.tools.files import (
    clear_work_dir,
    current_work_dir,
    list_subdirs,
    pick_dir_with_system_dialog,
    set_work_dir,
    system_dir_picker_command,
)

# 输出区的兜底高度（像素）。实际高度由 CSS 按视口动态设定，
# 万一样式没生效，就退回这个固定值；取小一些可减少样式失效时的溢出。
OUTPUT_HEIGHT = 300

# 输出区实际占用的视口比例；输入框补足剩余的 60vh，两者相加刚好一屏，避免整页滚动。
OUTPUT_AREA_VH = "40vh"

# 选择项的 session_state 键名。注意存的是 id 而不是列表下标 ——
# 每次 rerun 都会重新读取配置，列表顺序可能变化，用下标会让选中项漂移。
MODEL_STATE_KEY = "toolbar_model_id"
MODE_STATE_KEY = "toolbar_mode_id"

# 上面两个键落盘时用的名字。session_state 随进程消失，
# 有了这个文件，重启后（甚至换了浏览器）选择才能接上。
PREF_MODEL_KEY = "model"
PREF_MODE_KEY = "mode"

# 草稿的键不再是固定值，而是由 draft_key(会话id) 现算，见 render_input_area

# 点了发送之后，内容先暂存在这里，由主流程统一处理（见 submit_draft）
PENDING_KEY = "pending_prompt"

# 目录浏览位置：弹层里的临时状态，只活在 session 里，不落盘
BROWSE_KEY = "workdir_browse_path"

# 弹层里最多列多少个目录（遇到 /System 这种超大目录时兜底）
BROWSE_MAX_ITEMS = 100

# 页面样式：Streamlit 没有暴露高度参数，只能靠 CSS 按视口分配空间。
#
# 实测结论（1.64 版）：
#   1. key="task_output" 会渲染成 .st-key-task_output，且它就是被设置高度的元素；
#   2. Streamlit 的高度**不是内联样式**，而是 emotion 生成的 class；
#   3. 它自己也带 !important，所以普通规则盖不住 —— 每条规则前面加 `body`
#      把优先级抬到 (0,2,0) 才能稳定覆盖。
PAGE_CSS = f"""
<style>
/* 1) 主容器的留白与块间距。
      padding-top 必须大于 Streamlit 顶栏高度（约 3.75rem），
      否则标题会钻到顶栏下面被遮住。 */
body [data-testid="stMainBlockContainer"] {{
    padding-top: 4rem;
    padding-bottom: 0.5rem;
    gap: 0.5rem;
}}

/* 2) 输出区：按视口高度动态伸缩。
      关键：Streamlit 把高度设在「外层 wrapper」上（stLayoutWrapper），
      .st-key-* 只是里面的内容层。所以要用 :has() 选中 wrapper 才能改高度，
      再把内容层设为 100% 填满它；否则内容层会溢出 wrapper 压住下方元素。
      选择器重复写两次是为了把优先级抬到 (0,2,3)，压过 Streamlit 的 emotion 规则。 */
body div:has(> .st-key-task_output):has(> .st-key-task_output) {{
    height: {OUTPUT_AREA_VH} !important;
    min-height: {OUTPUT_AREA_VH} !important;
    max-height: {OUTPUT_AREA_VH} !important;
}}
body .st-key-task_output {{
    height: 100% !important;
    max-height: 100% !important;
    overflow-y: auto;
}}

/* 3) 输入区：text_area 补足剩下的空间，发送按钮浮在框内右下角。
      310px 是唯一的调节旋钮（比 chat_input 版本大，因为多了发送按钮那一行）：
        - 页面出现滚动 / 标题被顶掉 -> 调大（如 340px）
        - 功能区下方出现空白       -> 调小（如 280px） */
body [data-testid="stTextArea"] textarea {{
    height: calc(60vh - 310px) !important;
    min-height: 4rem;
    max-height: none;
    resize: none !important;
    /* 白底 + 明显边框；底部留白，免得文字钻到发送按钮底下 */
    background-color: #ffffff !important;
    border: 1px solid #a8b1bf !important;
    border-radius: 0.75rem !important;
    box-shadow: 0 1px 2px rgba(16, 24, 40, 0.05) !important;
    padding: 0.75rem 1rem 3rem !important;
}}
/* 聚焦时换回主题色，保留 Streamlit 自带的交互反馈 */
body [data-testid="stTextArea"] textarea:focus {{
    border-color: #4f8bf9 !important;
}}

/* 输入区容器作为定位参考，让发送按钮能浮起来 */
body .st-key-prompt_area {{
    position: relative;
}}
/* 发送按钮：绝对定位到输入框右下角，视觉上落在框内 */
body .st-key-prompt_area .st-key-send_button {{
    position: absolute;
    right: 1.25rem;
    bottom: 0.6rem;
    width: auto;
    z-index: 5;
}}

/* 4) 底部容器：只清零上下内边距（把上 16px、下 56px 还给输入框），
      左右内边距必须保留 —— 否则输入框会横向铺满，比上方输出区宽。 */
body [data-testid="stBottom"] {{
    padding: 0 !important;
}}
body [data-testid="stBottomBlockContainer"] {{
    padding-top: 0 !important;
    padding-bottom: 0 !important;
}}
</style>
"""


def apply_page_style() -> None:
    """注入页面样式：让输出区与输入框按视口高度分配空间。"""
    st.markdown(PAGE_CSS, unsafe_allow_html=True)


def build_model_store() -> ModelStore:
    """读取模型配置，供「模型选择」使用。"""
    return ModelStore(get_settings().models_path)


def build_mode_store() -> PromptModeStore:
    """读取提示词模式，供「模式选择」使用（在「提示词」页维护）。"""
    return PromptModeStore(get_settings().modes_path)


def build_history_store() -> ConversationStore:
    """每次运行都重新读目录，保证列表反映最新状态。"""
    store = ConversationStore(get_settings().conversations_dir)
    store.ensure_dirs()
    return store


def build_preference_store() -> PreferenceStore:
    """读写跨进程保留的界面偏好（上次选中的模型 / 模式 / 各会话草稿）。"""
    return PreferenceStore(get_settings().preferences_path)


def current_conversation_id() -> str:
    """当前会话 id。init_state() 保证它在页面渲染前就已存在。"""
    return st.session_state.get("current_conversation_id", "")


def init_state() -> None:
    """初始化当前会话。

    首次进入（或页面刷新后）时：优先接续最近一个会话，没有就新建一个。
    之后的 rerun 直接用 session_state 里的消息，不重复读盘。
    """
    if "messages" in st.session_state:
        return

    store = build_history_store()
    conv_id = st.session_state.get("current_conversation_id")

    if not conv_id:
        existing = store.list_active()
        conv_id = existing[0].id if existing else store.create()
        st.session_state.current_conversation_id = conv_id

    st.session_state.messages = store.load(conv_id)


def render_output_area():
    """第 2 块：模型返回文本的展示区。

    返回容器内预留的一个空位，供流式输出实时写入 —— 这样流式文本就出现在
    输出区里，用户在视觉上看到的是「答案在这个框里长出来」。

    height 传入的是兜底像素值，实际高度由 PAGE_CSS 里基于 key 的样式覆盖成视口比例。
    """
    with st.container(height=OUTPUT_HEIGHT, border=True, key="task_output"):
        if not st.session_state.messages:
            st.caption("模型返回的文本会显示在这里。在下方输入内容并回车开始。")
        else:
            for message in st.session_state.messages:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])

                    # 工具调用过程折叠展示；它只用于回看，不会作为历史发给模型
                    for step in message.get("steps", []):
                        with st.expander(f"🔧 {step['name']}"):
                            st.code(step["arguments"] or "{}", language="json")
                            st.text(step["result"])

        return st.empty()


def render_model_picker(configs: list[ModelConfig]) -> ModelChoice | None:
    """模型选择控件。

    把「连接 + 模型」摊平成一层列表：一个连接下有 N 个模型就出现 N 个选项，
    这样切换模型不用先在连接之间跳来跳去。

    选中项用 "{连接id}::{模型名}" 作为稳定标识（存在 session_state 里），
    连接或模型被增删都不会让选择漂移。

    Returns:
        选中的连接配置 + 模型名；没有任何可选模型时返回 None。
    """
    choices = {
        model_choice_key(config.id, model_name): ModelChoice(
            config=config, model=model_name
        )
        for config in configs
        for model_name in config.models
    }
    if not choices:
        return None

    prefs = build_preference_store()

    # 选择来源的优先级：本进程内的选择 > 上次存盘的偏好 > 第一个选项。
    # 后两者都可能指向已被删除的模型，所以要校验还在不在选项里。
    # 必须在创建 widget 之前写回 session_state：
    #   - 不写，radio 会默认选中第一个选项，读到的偏好就白读了；
    #   - 写了个不在选项里的值，Streamlit 创建 widget 时会直接报错。
    remembered = st.session_state.get(MODEL_STATE_KEY) or prefs.get(PREF_MODEL_KEY)
    st.session_state[MODEL_STATE_KEY] = (
        remembered if remembered in choices else next(iter(choices))
    )

    with st.popover("🧠", help="选择模型"):
        st.radio(
            "模型",
            options=list(choices),
            format_func=lambda key: f"{choices[key].config.name} / {choices[key].model}",
            key=MODEL_STATE_KEY,
            label_visibility="collapsed",
        )

    # 落盘：下次启动时 session_state 是空的，靠这个文件把选择接回来
    prefs.set(PREF_MODEL_KEY, st.session_state[MODEL_STATE_KEY])
    return choices[st.session_state[MODEL_STATE_KEY]]


def apply_preferred_model(by_id: dict[str, PromptMode]) -> None:
    """模式切换后，把模型换成该模式配置的偏好模型；没配就保持原样。

    为什么用 on_change 回调而不是「渲染后比较上次的值」：
    回调在脚本重跑**之前**执行，此时改 MODEL_STATE_KEY，正好赶在模型选择
    控件渲染之前生效 —— 工具栏里模式控件排在模型控件前面，顺序上来得及。

    偏好模型若指向已被删除的模型也不用担心：模型选择控件渲染时会校验，
    发现值不在选项里就自动退回第一个。
    """
    mode = by_id.get(st.session_state.get(MODE_STATE_KEY, ""))
    if mode is None or not mode.preferred_model:
        return

    st.session_state[MODEL_STATE_KEY] = mode.preferred_model


def render_mode_picker(modes: list[PromptMode]) -> PromptMode | None:
    """模式选择控件。

    选项来自「提示词」页配置的模式（一个模式 = 若干提示词片段的组合）。
    同样按 id 记录选择，模式被删除时会自动回退到第一个。

    切换模式时若该模式配了「偏好模型」，模型会跟着一起换 —— 见
    apply_preferred_model。

    Returns:
        选中的模式；一个模式都没有时返回 None。
    """
    if not modes:
        return None

    by_id = {mode.id: mode for mode in modes}
    prefs = build_preference_store()

    # 优先级同模型选择：本进程内的选择 > 上次存盘的偏好 > 第一个模式。
    # 修正必须发生在创建 widget 之前，理由见 render_model_picker。
    remembered = st.session_state.get(MODE_STATE_KEY) or prefs.get(PREF_MODE_KEY)
    st.session_state[MODE_STATE_KEY] = (
        remembered if remembered in by_id else modes[0].id
    )

    with st.popover("🎛️", help="选择模式"):
        st.radio(
            "模式",
            options=list(by_id),
            format_func=lambda mode_id: by_id[mode_id].name,
            key=MODE_STATE_KEY,
            on_change=apply_preferred_model,
            args=(by_id,),
            label_visibility="collapsed",
        )

    prefs.set(PREF_MODE_KEY, st.session_state[MODE_STATE_KEY])
    return by_id[st.session_state[MODE_STATE_KEY]]


def render_browser(browse: Path) -> str:
    """弹层里的目录浏览器。

    Returns:
        用户动作：""（无）| "parent" | "dir:<路径>" | "select" | "reset"。
        动作要靠返回值带出去 —— 状态变更与 rerun 必须等到 popover 外再执行。
    """
    entries, error = list_subdirs(browse)
    if error:
        st.error(error)

    with st.container(height=240, border=True):
        if not entries:
            st.caption("（没有子目录）")
        for entry in entries[:BROWSE_MAX_ITEMS]:
            # key 用完整路径，保证唯一
            if st.button(f"📁 {entry.name}", key=f"nav_{entry}", use_container_width=True):
                return f"dir:{entry}"

    if len(entries) > BROWSE_MAX_ITEMS:
        st.caption(f"（只显示前 {BROWSE_MAX_ITEMS} 个，共 {len(entries)} 个）")

    nav_col, select_col = st.columns(2)
    if nav_col.button("⬆️ 上一级", use_container_width=True):
        return "parent"
    if select_col.button("✅ 选定此目录", type="primary", use_container_width=True):
        return "select"

    # 「恢复默认」由调用方统一提供，这里不要再放一个，否则会重复出现
    return ""


def render_workdir_picker() -> str:
    """工作目录选择。

    两条路按环境**二选一**渲染，不会同时出现 —— 功能重叠的入口摆在一起，
    用户反而不知道该点哪个：

        本地桌面 -> 「用系统对话框选择」，一步跳到任意位置
        其他环境 -> 后端目录浏览（远程部署时唯一可行的那条）

    为什么不做成「点 📁 就直接拉系统对话框」：popover 的展开是纯前端行为，
    点它的那一刻后端脚本根本没在执行，没法在那里做判断。所以判断只能放在
    渲染时 —— 决定渲染哪个入口，点击时才真正执行。

    顺带说明为什么还要留后端目录浏览：浏览器的目录选择 API 只给句柄不给
    绝对路径，而 Python 需要路径，两者不通。后端列目录这条路在任何环境都
    成立，正好补上系统选择器覆盖不到的场景。

    Returns:
        当前工作目录的展示名（只取末级目录名，完整路径在弹层里看）。
    """
    current = current_work_dir()

    # 浏览位置：默认从当前工作目录起步；
    # 记录的位置若已失效（目录被删/移走）就退回当前
    browse = Path(st.session_state.get(BROWSE_KEY) or current)
    if not browse.is_dir():
        browse = current

    action = ""
    with st.popover("📁", help="选择工作目录"):
        st.caption(f"当前：{current}")

        if system_dir_picker_command() is not None:
            # 本地桌面：直接给系统对话框，不再摆目录浏览
            if st.button("选择目录", key="system_picker", use_container_width=True):
                action = "system"
        else:
            # 远程等环境：系统对话框弹在服务器上看不见，只能用后端浏览
            st.caption(f"浏览：{browse}")
            action = render_browser(browse)

        if st.button("恢复默认", key="reset_workdir", use_container_width=True):
            action = "reset"

    # 状态变更统一放在 popover 外面处理，避免和内部渲染顺序纠缠
    if action == "system":
        path, error = pick_dir_with_system_dialog()
        if error:
            st.error(error)  # 不重跑，让提示留在页面上
        elif path:
            error = set_work_dir(path)
            if error:
                st.error(error)
            else:
                st.session_state.pop(BROWSE_KEY, None)
                st.toast(f"工作目录已切换到 {Path(path).name or path}")
                st.rerun()

    if action == "parent":
        st.session_state[BROWSE_KEY] = str(browse.parent)
        st.rerun()

    if action.startswith("dir:"):
        st.session_state[BROWSE_KEY] = action[4:]
        st.rerun()

    if action == "select":
        error = set_work_dir(str(browse))
        if error:
            st.error(error)  # 不重跑，让提示留在页面上
        else:
            st.session_state.pop(BROWSE_KEY, None)
            st.toast(f"工作目录已切换到 {browse.name or browse}")
            st.rerun()

    if action == "reset":
        clear_work_dir()
        st.session_state.pop(BROWSE_KEY, None)
        st.toast("已恢复默认工作目录")
        st.rerun()

    return current.name or str(current)


def render_toolbar(
    model_store: ModelStore,
    mode_store: PromptModeStore,
) -> tuple[list, PromptMode | None, ModelChoice | None]:
    """第 4 块：文件上传 / 模式选择 / 模型选择。

    三个控件都收成「图标 + 当前值」的形态：
    点击图标展开选择面板，鼠标悬停显示提示，选完后在图标后面显示所选项。

    Returns:
        (上传的文件列表, 选中的模式, 选中的模型)；未配置时对应项为 None。
    """
    files: list = []
    mode: PromptMode | None = None
    selected: ModelChoice | None = None

    # horizontal=True 让子元素按内容宽度横向排列，不会撑满整行
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        # 文件：➕ 图标，悬停提示「上传文件」
        with st.popover("➕", help="上传文件"):
            files = st.file_uploader(
                "上传文件",
                accept_multiple_files=True,
                label_visibility="collapsed",
                key="toolbar_files",
            )
        if files:
            st.caption("、".join(item.name for item in files))

        # 模式：🎛️ 图标，悬停提示「选择模式」，选项来自「提示词」页
        mode = render_mode_picker(mode_store.list())
        if mode is not None:
            st.caption(mode.name)
        else:
            st.caption("无可用模式，请先到「提示词」页面添加")

        # 模型：🧠 图标，悬停提示「选择模型」，选项来自「模型管理」页
        selected = render_model_picker(model_store.list())
        if selected is not None:
            st.caption(selected.model)
        else:
            st.caption("无可用模型，请先到「模型管理」页面添加")

        # 工作目录：📁 图标，悬停提示「选择工作目录」。
        # 它同时是文件工具的边界，改了之后模型上下文与工具行为会一起变。
        st.caption(render_workdir_picker())

    return files, mode, selected


def submit_draft() -> None:
    """发送按钮的回调：把草稿交给主流程，并清空输入框。

    为什么用 on_click 回调，而不是读 st.button() 的返回值：
    widget 一旦创建，就不能再修改它绑定的 session_state 值（Streamlit 会报错），
    而清空输入框恰恰要改草稿键。回调在脚本重跑之前执行，此时不受这个限制。
    """
    key = draft_key(current_conversation_id())
    text = st.session_state.get(key, "").strip()
    if not text:
        return  # 空白内容不触发提交

    st.session_state[PENDING_KEY] = text
    st.session_state[key] = ""


def render_input_area() -> None:
    """第 4 块：输入区。

    用 st.text_area 而不是 st.chat_input —— 后者的内容只在提交时才回传后端，
    切换页面（组件卸载）后草稿就没了。text_area 每次输入都会同步到 session_state，
    再配合下面两套机制，草稿能扛过多种情况：

        页面切换          -> persist_state="page"（session 内部保留）
        刷新 / 重开 / 重启 -> 落盘，session 重建后从磁盘恢复

    **草稿按会话隔离**：键名由 draft_key(会话id) 现算，所以在会话 A 里写一半
    切到 B，两边互不影响，切回 A 草稿还在。

    代价有两个：text_area 自带不了按钮（所以用 CSS 把按钮浮到右下角），
    以及它每敲一个字符都会重跑脚本。

    注意：草稿会以明文写进 data/preferences.json（该目录已在 .gitignore 里）。
    """
    prefs = build_preference_store()
    # 键随会话变化 —— 换了会话就是另一个 widget，内容自然分开
    key = draft_key(current_conversation_id())

    # 键不存在 = 首次进入这个会话，或 session 被重建（刷新页面）——从磁盘接上
    if key not in st.session_state:
        st.session_state[key] = prefs.get(key)

    with st.container(key="prompt_area"):
        st.text_area(
            "输入内容",
            key=key,
            persist_state="page",
            label_visibility="collapsed",
            placeholder="输入内容，然后点右下角按钮发送",
            height=180,
        )
        st.button(
            "发送",
            key="send_button",
            type="primary",
            on_click=submit_draft,
        )

    # 每轮都同步到磁盘；值没变时 PreferenceStore 内部会跳过写盘，不会白写。
    # 空草稿则删掉键，免得偏好文件里堆一串 "prompt_draft::xxx": "" 的垃圾。
    draft = st.session_state[key]
    if draft:
        prefs.set(key, draft)
    else:
        prefs.remove(key)


def handle_submit(
    *,
    prompt: str,
    files: list,
    mode: PromptMode | None,
    model: ModelChoice | None,
    stream_slot,
) -> None:
    """把用户输入与选中的参数交给 run_agent()，并把结果写回历史。"""
    text = prompt.strip()
    if not text:
        return  # 空输入（例如只敲了空格）直接忽略，不打扰模型

    store = build_history_store()
    conv_id = st.session_state.current_conversation_id

    # 用户消息：产生即落盘。
    # Streamlit 没有可靠的「关闭页面」钩子，不能攒到最后再存。
    remember(store, conv_id, {"role": "user", "content": text})

    result = run_agent(
        prompt=text,
        files=files,
        mode=mode,
        model=model,
        stream_slot=stream_slot,
    )

    # 助手消息：带工具调用记录（回看用；发请求时会被过滤掉）
    remember(
        store,
        conv_id,
        {
            "role": "assistant",
            "content": result.text,
            "steps": [asdict(step) for step in result.steps],
        },
    )


def remember(store: ConversationStore, conv_id: str, message: dict) -> None:
    """同时写进内存和磁盘，并补上时间戳。"""
    message["ts"] = datetime.now().isoformat(timespec="seconds")
    st.session_state.messages.append(message)
    store.append(conv_id, message)


def main() -> None:
    # 第 1 块：标题
    st.title("quill")
    apply_page_style()

    init_state()

    # 第 2 块：输出区（返回值是容器内的空位，供流式输出实时写入）
    stream_slot = render_output_area()

    # 第 3 块：功能区
    files, mode, model = render_toolbar(build_model_store(), build_mode_store())

    # 第 4 块：输入区（放最后，它会停靠在底部）
    render_input_area()

    # 发送按钮的回调已经把内容存进 PENDING_KEY，这里统一处理。
    # 用 pop 取走：取到即消费掉，重跑时不会重复提交。
    pending = st.session_state.pop(PENDING_KEY, "")
    if pending:
        handle_submit(
            prompt=pending,
            files=files,
            mode=mode,
            model=model,
            stream_slot=stream_slot,
        )
        # 输出区在上面已经渲染过了，重跑一次才能把新消息显示进去
        st.rerun()


# ---------------------------------------------------------------------------
# 页面侧封装：渲染流式输出 + 汇总结果
# ---------------------------------------------------------------------------
def run_agent(
    *,
    prompt: str,
    files: list,
    mode: PromptMode | None,
    model: ModelChoice | None,
    stream_slot,
) -> AgentResult:
    """流式执行一轮 Agent 对话。

    文本增量写进输出区预留的空位（实时渲染），工具调用写进状态面板。
    接口请求与上下文组装都在 src/quill_agent/agent.py，这里只负责渲染和汇总。
    """
    # 最后一条是本轮的 user 消息（handle_submit 刚写进去的），要排除掉：
    # 它会由 agent 层重新组装（带上运行时上下文），避免重复。
    history = [
        {"role": item["role"], "content": item["content"]}
        for item in st.session_state.messages[:-1]
    ]

    text_parts: list[str] = []
    steps: list[ToolStep] = []

    with st.status("思考中…", expanded=True) as status:
        for item in run_agent_stream(
            prompt=prompt,
            files=files,
            mode=mode,
            choice=model,
            history=history,
        ):
            if isinstance(item, str):
                # 文本增量：追加到输出区，实时渲染
                text_parts.append(item)
                stream_slot.markdown("".join(text_parts))
            else:
                # 工具调用完成：写进状态面板，让用户看到中间过程
                steps.append(item)
                status.write(f"🔧 调用 `{item.name}`")
                status.write(f"　→ {item.result[:300]}")

        status.update(label="已完成", state="complete", expanded=False)

    return AgentResult(text="".join(text_parts), steps=steps)


main()
