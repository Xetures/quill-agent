"""HTTP 层：把业务层暴露成 REST + SSE 接口。

它和 `app/`（旧的 Streamlit 界面）是**平级**的两个入口，两边都只依赖
`src/quill_agent/`：

    web/ (Vue)  ──HTTP/SSE──┐
                            ├──>  src/quill_agent/  (业务层)
    app/ (Streamlit) ───────┘

这正是当初坚持「业务层不许 import streamlit」换来的：换界面不用动业务代码，
甚至两个界面可以并存、对照着用。
"""
