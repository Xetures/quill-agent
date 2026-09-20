"""联网搜索测试。

所有网络调用一律打桩 —— 这些测试要能在离线、没配 Key 的机器上跑（CI 就是那样）。
真正要钉住的是四件容易出错的事：

1. 两家响应的字段名不一样，解析容易只照顾了一家；
2. HTML 扒正文时，`<script>` 里的代码最容易被当成正文带出来；
3. 配置坏了 / Key 没填时，要给**能照着做**的提示，而不是一句报错或一段堆栈；
4. 哪些错误该重试、哪些重试也没用。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from quill_agent import search as search_module
from quill_agent.search import (
    SearchBackend,
    SearchConfig,
    SearchError,
    SearchHit,
    SearchStore,
    html_to_text,
    search,
)

# ---------------------------------------------------------------------------
# HTML 扒正文
# ---------------------------------------------------------------------------


def test_html_to_text_drops_scripts_and_chrome() -> None:
    """脚本、样式、导航、页脚都不是正文。"""
    html = """
    <html>
      <head>
        <title>标题在这</title>
        <style>.a { color: red }</style>
        <script>if (a < b) { document.write("x") }</script>
      </head>
      <body>
        <nav>首页 登录 注册</nav>
        <article><p>第一段。</p><p>第二段。</p></article>
        <footer>版权所有</footer>
      </body>
    </html>
    """
    text, title = html_to_text(html)

    assert title == "标题在这"
    assert "第一段。" in text
    assert "第二段。" in text

    # 正则剥标签最容易在这里翻车：`if (a < b)` 里的 < 会让整段错位
    for noise in ("color: red", "document.write", "首页", "版权所有"):
        assert noise not in text


def test_html_to_text_keeps_paragraph_breaks() -> None:
    """块级标签要还原成换行 —— 否则整页挤成一行，模型很难读。"""
    text, _ = html_to_text("<div><p>甲</p><p>乙</p></div>")

    assert text == "甲\n\n乙"


def test_html_to_text_unescapes_entities() -> None:
    text, _ = html_to_text("<p>A &amp; B &lt;tag&gt;</p>")

    assert text == "A & B <tag>"


def test_html_to_text_on_plain_text() -> None:
    """不是 HTML 的输入不该炸，原样给回来。"""
    text, title = html_to_text("就是一句话")

    assert text == "就是一句话"
    assert title == ""


# ---------------------------------------------------------------------------
# 两家后端的响应解析
# ---------------------------------------------------------------------------


def test_tavily_results_are_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        search_module,
        "_request_json",
        lambda *args, **kwargs: {
            "results": [
                {"title": "标题", "url": "https://example.com/a", "content": "摘要"},
                # 没有地址的结果要丢掉：模型没法拿它去读正文
                {"title": "没有地址", "content": "丢掉"},
            ]
        },
    )

    hits = search("x", config=SearchConfig(keys={"tavily": "tvly-x"}))

    assert [hit.url for hit in hits] == ["https://example.com/a"]
    assert hits[0].title == "标题"
    assert hits[0].snippet == "摘要"


@pytest.mark.parametrize("wrapped", [True, False])
def test_bocha_results_are_parsed_in_both_shapes(
    monkeypatch: pytest.MonkeyPatch, wrapped: bool
) -> None:
    """`webPages` 既可能在顶层、也可能在 `data` 下面，两种都要认。"""
    pages = {
        "webPages": {
            "value": [
                {
                    "name": "标题",
                    "url": "https://example.com/b",
                    "snippet": "短摘要",
                    "summary": "长摘要",
                }
            ]
        }
    }
    payload = {"code": 200, "data": pages} if wrapped else {"code": 200, **pages}

    monkeypatch.setattr(search_module, "_request_json", lambda *args, **kwargs: payload)

    config = SearchConfig(backend=SearchBackend.BOCHA, keys={"bocha": "sk-x"})
    hits = search("x", config=config)

    assert [hit.url for hit in hits] == ["https://example.com/b"]
    # summary 比 snippet 详细，有它就不该退回短的
    assert hits[0].snippet == "长摘要"


def test_missing_title_falls_back_to_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        search_module,
        "_request_json",
        lambda *args, **kwargs: {"results": [{"url": "https://example.com/c", "content": "x"}]},
    )

    hits = search("x", config=SearchConfig(keys={"tavily": "k"}))

    assert hits[0].title == "https://example.com/c"


def test_long_snippet_is_clipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """一条摘要太长会挤掉模型本来该用来干活的上下文，所以要压。"""
    monkeypatch.setattr(
        search_module,
        "_request_json",
        lambda *args, **kwargs: {
            "results": [{"title": "T", "url": "https://e.com", "content": "字" * 5000}]
        },
    )

    hits = search("x", config=SearchConfig(keys={"tavily": "k"}))

    assert len(hits[0].snippet) <= search_module.MAX_SNIPPET_CHARS + 1


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def test_search_without_key_raises_actionable_message() -> None:
    with pytest.raises(SearchError) as excinfo:
        search("x", config=SearchConfig())

    # 提示里要指出「去哪儿填」，不然模型只会把它原样转述给用户，用户还是不知道怎么办
    assert "联网搜索" in str(excinfo.value)


def test_empty_query_raises() -> None:
    with pytest.raises(SearchError):
        search("   ", config=SearchConfig(keys={"tavily": "k"}))


def test_keys_are_kept_per_backend(tmp_path: Path) -> None:
    """切换后端不该抹掉另一边的 Key —— 这是 keys 做成字典的全部理由。"""
    store = SearchStore(tmp_path / "search.json")
    store.save(
        SearchConfig(backend=SearchBackend.BOCHA, keys={"tavily": "a", "bocha": "b"})
    )

    config = store.load()

    assert config.backend is SearchBackend.BOCHA
    assert config.key_for(SearchBackend.TAVILY) == "a"
    assert config.key_for(SearchBackend.BOCHA) == "b"


def test_corrupt_config_falls_back_to_defaults(tmp_path: Path) -> None:
    """用户手改配置写错一个字段，不该让整页打不开。"""
    path = tmp_path / "search.json"
    path.write_text('{"max_results": "很多"}', encoding="utf-8")

    assert SearchStore(path).load() == SearchConfig()


def test_base_url_overrides_default_endpoint() -> None:
    assert SearchConfig().endpoint_for() == SearchBackend.TAVILY.endpoint
    assert SearchConfig().endpoint_for(SearchBackend.BOCHA) == SearchBackend.BOCHA.endpoint

    proxied = SearchConfig(base_url="https://my-proxy/search")
    assert proxied.endpoint_for() == "https://my-proxy/search"
    # 覆盖是全局的：换后端也走同一个地址，这正是「自建 / 中转」要的行为
    assert proxied.endpoint_for(SearchBackend.BOCHA) == "https://my-proxy/search"


def test_max_results_is_clamped() -> None:
    """条数越界直接拒绝，而不是悄悄截断 —— 存进去的值要能反映用户填了什么。"""
    with pytest.raises(ValueError):
        SearchConfig(max_results=0)

    with pytest.raises(ValueError):
        SearchConfig(max_results=search_module.MAX_RESULTS_LIMIT + 1)


# ---------------------------------------------------------------------------
# 重试
# ---------------------------------------------------------------------------


class _FakeResponse:
    """`urlopen` 返回值的替身：只要能当上下文管理器、能 read 就够。"""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, *args: Any) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _http_error(code: int) -> HTTPError:
    return HTTPError(
        "https://example.com",
        code,
        "错误",
        None,  # type: ignore[arg-type]
        io.BytesIO('{"msg": "对端说的话"}'.encode()),
    )


def test_retries_once_on_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """5xx 可能是临时的，值得再试一次。"""
    calls: list[int] = []

    def fake_urlopen(request: Any, timeout: float = 0) -> _FakeResponse:
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(500)
        return _FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(search_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(search_module.time, "sleep", lambda _seconds: None)  # 别真等

    assert search_module._request_json("https://example.com", payload={"a": 1}) == {"ok": True}
    assert len(calls) == 2


def test_does_not_retry_on_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """401 是「Key 不对」，重试多少次都是同一个结果，白等只会显得卡死。"""
    calls: list[int] = []

    def fake_urlopen(request: Any, timeout: float = 0) -> _FakeResponse:
        calls.append(1)
        raise _http_error(401)

    monkeypatch.setattr(search_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(search_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(SearchError) as excinfo:
        search_module._request_json("https://example.com", payload={"a": 1})

    assert len(calls) == 1
    # 对端说了什么就转述什么，别自己编一句「请求失败」
    assert "对端说的话" in str(excinfo.value)


def test_non_json_response_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_module, "urlopen", lambda *a, **k: _FakeResponse(b"<html>404"))

    with pytest.raises(SearchError) as excinfo:
        search_module._request_json("https://example.com")

    assert "JSON" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 抓正文
# ---------------------------------------------------------------------------


def test_fetch_page_rejects_non_http_scheme() -> None:
    for url in ("file:///etc/passwd", "ftp://example.com", "javascript:alert(1)", "example.com"):
        with pytest.raises(SearchError):
            search_module.fetch_page(url)


# ---------------------------------------------------------------------------
# 工具层：给模型看的话
# ---------------------------------------------------------------------------


def test_web_search_without_key_points_at_the_settings_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """工具要把「怎么修」说出来，而不是抛异常 —— 模型会把它原样转述给用户。"""
    from quill_agent.tools import web

    monkeypatch.setattr(web, "_load_config", lambda: SearchConfig())

    assert "联网搜索" in web.web_search("北京今天的天气")


def test_web_search_formats_hits_and_hints_next_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from quill_agent.tools import web

    monkeypatch.setattr(web, "_load_config", lambda: SearchConfig(keys={"tavily": "k"}))
    monkeypatch.setattr(
        web,
        "search",
        lambda query, config, count: [SearchHit("标题", "https://example.com/x", "摘要")],
    )

    out = web.web_search("词")

    assert "https://example.com/x" in out
    assert "摘要" in out
    # 结果里要顺手告诉模型下一步用 web_fetch，否则它拿摘要就开始编
    assert "web_fetch" in out


def test_web_search_surfaces_provider_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from quill_agent.tools import web

    def boom(query: str, config: SearchConfig, count: int | None = None) -> list[SearchHit]:
        raise SearchError("Key 无效")

    monkeypatch.setattr(web, "_load_config", lambda: SearchConfig(keys={"tavily": "k"}))
    monkeypatch.setattr(web, "search", boom)

    assert "Key 无效" in web.web_search("词")


def test_web_fetch_requires_a_prompt() -> None:
    """prompt 必填是这套设计的关键：没它模型会习惯性抓整页，把窗口吃光。"""
    from quill_agent.tools import web

    out = web.web_fetch("https://example.com", "   ")

    assert "prompt" in out


def test_web_fetch_reports_bad_url_instead_of_raising() -> None:
    from quill_agent.tools import web

    assert "http" in web.web_fetch("file:///etc/passwd", "里面写了什么")
