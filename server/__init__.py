"""HTTP 层：把业务层暴露成 REST + SSE 接口。

它和 `web/`（Vue 前端）是配套的两层，两者只通过 HTTP 说话，而业务全在
`src/quill_agent/`：

    web/ (Vue)  ──HTTP/SSE──>  server/ (FastAPI)  ──>  src/quill_agent/  (业务层)

所以 `server/` 本身也只是业务层的一个调用方：换一套前端、或者让 CLI 直接调业务层，
这一层都可以原样留着或整个换掉，业务代码一行不用动。
"""
