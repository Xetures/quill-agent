"""联网搜索：搜索服务商的配置，以及「搜」和「读」两个动作。

分三层，每层只管一件事：

1. `SearchConfig` —— 用哪个后端、Key 是什么、端点要不要覆盖。存 `data/search.json`。
2. 服务商解析 —— Tavily / 博查各一个函数，把两家形状不同的响应掰成同一种
   `SearchHit`。多支持一家 = 多写一个解析函数 + 一个枚举项。
3. `search()` / `fetch_page()` —— 上面那层的统一入口，工具层直接用这两个。

为什么不引第三方库：这个项目至今只用标准库去发外部 HTTP 请求（`core.py` 里的
openai 是例外 —— 那是模型接口，它本来就有 SDK）。搜索接口就是两个 POST，为它拉
一个依赖不划算；正文提取用 `html.parser` 也够用 —— 目标是文章页，不是要跟浏览器
比 DOM 还原度，不引 readability 那一套。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field, ValidationError

from quill_agent.locking import atomic_write_text, file_lock
from quill_agent.store import read_json

# 搜索请求超时（秒）。搜索是「等结果才有下一步」的操作，用户就在那儿盯着，
# 给太长时间只会让卡顿更明显
SEARCH_TIMEOUT = 20.0

# 抓正文的超时单独给：正文页可能比搜索接口慢得多，而且模型在这期间本来就要等
FETCH_TIMEOUT = 25.0

# 抓取的字节上限。正常文章页几十到几百 KB；再大基本是文件、视频页或恶意灌水，
# 读进来只会白白吃内存
MAX_FETCH_BYTES = 2_000_000

# 单次搜索的返回条数：默认值，以及界面上允许用户填的上限
DEFAULT_MAX_RESULTS = 5
MAX_RESULTS_LIMIT = 20

# 一条摘要的长度上限。搜索接口给的摘要偶尔很长（博查开了 summary 尤其如此），
# 一条几百字、五条就是几千字，直接挤掉模型本来该用来干活的上下文
MAX_SNIPPET_CHARS = 400

# 抓回来的正文，**不做摘要时**直接交给模型的长度上限
MAX_FETCH_CHARS = 8000

# 抓取时用的 UA。有些站点会拒绝空 UA 或明显是脚本的 UA，
# 这里报清楚自己是谁，不做伪装
USER_AGENT = "Mozilla/5.0 (compatible; quill-agent/1.0; +https://github.com/)"

_HINT_TEMPLATE = "还没有配置「{label}」的 API Key。请到左侧「联网搜索」页填好再试。"


class SearchBackend(str, Enum):
    """支持的搜索后端。

    枚举的 `value` 是**持久化用的键**：`SearchConfig.keys` 按它分别存 Key，
    所以切后端时各自填过的 Key 不会互相覆盖（切回去还在）。
    """

    TAVILY = "tavily"
    BOCHA = "bocha"

    @property
    def label(self) -> str:
        """界面展示用的名字。"""
        return {
            SearchBackend.TAVILY: "Tavily",
            SearchBackend.BOCHA: "博查",
        }[self]

    @property
    def endpoint(self) -> str:
        """默认的接口地址；配置里填了 base_url 时会被覆盖。"""
        return {
            SearchBackend.TAVILY: "https://api.tavily.com/search",
            SearchBackend.BOCHA: "https://api.bochaai.com/v1/web-search",
        }[self]

    @property
    def hint(self) -> str:
        """Key 从哪儿来 —— 界面上的说明文字用它，省得用户去翻文档。"""
        return {
            SearchBackend.TAVILY: "在 tavily.com 注册后领取，免费额度够个人用，Key 以 tvly- 开头",
            SearchBackend.BOCHA: "在 open.bochaai.com 注册后领取，中文结果好，Key 以 sk- 开头",
        }[self]

    @classmethod
    def values(cls) -> list[str]:
        """按界面顺序列出全部取值。"""
        return [item.value for item in cls]


class SearchError(Exception):
    """调用搜索 / 抓取失败。

    消息是**给模型和用户看的**：不含堆栈、不含内部路径，调用方直接把它当结果文本
    返回即可。之所以做成异常而不是返回 `(ok, msg)`，是因为它要跨 `search()`、
    `fetch_page()` 和工具层三层传递，异常最省事。
    """


class SearchConfig(BaseModel):
    """联网搜索的配置。

    Attributes:
        backend: 当前选中的后端。
        keys: 后端 value -> API Key。**按后端分别存**：两台服务切来切去是常事，
            只留一个字段的话，切一次就把另一边的 Key 抹掉了。
        base_url: 覆盖默认端点。留空就用后端内置地址 —— 这一项是给「自建 /
            中转」留的口子（SearXNG 这类自建实例将来也接在这里）。
        max_results: 默认返回条数，模型没指定时用。
    """

    backend: SearchBackend = SearchBackend.TAVILY
    keys: dict[str, str] = Field(default_factory=dict, description="后端 -> API Key")
    base_url: str = Field(default="", description="覆盖默认端点；留空用内置地址")
    max_results: int = Field(default=DEFAULT_MAX_RESULTS, ge=1, le=MAX_RESULTS_LIMIT)

    def key_for(self, backend: SearchBackend | None = None) -> str:
        """取某个后端的 Key；没配过就是空串。"""
        target = backend or self.backend
        return (self.keys.get(target.value) or "").strip()

    def endpoint_for(self, backend: SearchBackend | None = None) -> str:
        """取某个后端的接口地址：填了 base_url 就用它，否则用内置的。"""
        return self.base_url.strip() or (backend or self.backend).endpoint


def has_key(config: SearchConfig, backend: SearchBackend | None = None) -> bool:
    """这个后端配好 Key 了没有。"""
    return bool(config.key_for(backend))


def missing_key_message(config: SearchConfig, backend: SearchBackend | None = None) -> str:
    """没配 Key 时给模型（以及用户）看的话。"""
    return _HINT_TEMPLATE.format(label=(backend or config.backend).label)


class SearchStore:
    """搜索配置的持久化。

    和另外几个 store 不一样的地方：它存的是**单个对象**而不是一列对象 ——
    「用哪个后端」全局只有一个答案，做成列表反而要多一套「选中的是哪个」。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> SearchConfig:
        """读取配置；文件缺失、损坏、内容不合法时一律退回默认值（见 `read_json`）。"""
        raw = read_json(self._path, {})
        if not isinstance(raw, dict):
            return SearchConfig()

        try:
            return SearchConfig.model_validate(raw)
        except ValidationError:
            # 用户手改过、字段写错了。配置类数据坏掉的代价不该是「功能不可用」，
            # 退回默认值比抛异常好 —— 界面打开还是好的，重新填一遍即可
            return SearchConfig()

    def save(self, config: SearchConfig) -> None:
        """整体覆写。

        和其它几个 store 一样是「持锁进入 + 原子替换」（理由见 `store.py` 的
        `_save_all` 那段）：不持锁的话两个进程会互相覆盖，不用原子替换的话别的进程
        可能读到写了一半的文件。
        """
        with file_lock(self._path):
            atomic_write_text(
                self._path,
                # mode="json"：让枚举落成它的取值（"tavily"）而不是枚举对象，
                # 存出来的文件直接可读可手改
                json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent=2),
            )


@dataclass(frozen=True)
class SearchHit:
    """一条搜索结果。

    Attributes:
        title: 标题。
        url: 原文地址 —— 模型要读正文时原样把它交给 `web_fetch`。
        snippet: 摘要。**不是全文**，接口给什么就是什么。
    """

    title: str
    url: str
    snippet: str


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _error_detail(exc: HTTPError) -> str:
    """从错误响应体里抠一句人话。

    两家的错误体形状不一样（Tavily 是 `{"detail": {"error": "..."}}`，博查是
    `{"code": 401, "msg": "..."}`），所以按候选键挨个试；实在抠不出来就退回
    HTTP 原因短语 —— 至少让用户知道是 401 还是 500。
    """
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:  # 读错误响应体也可能失败（连接已断），不影响主流程
        body = ""

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        data = None

    if isinstance(data, dict):
        for key in ("msg", "message", "detail", "error"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                inner = value.get("error") or value.get("message")
                if isinstance(inner, str) and inner.strip():
                    return inner.strip()

    return f"HTTP {exc.code} {exc.reason}".strip()


def _request_json(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = SEARCH_TIMEOUT,
    retries: int = 1,
) -> Any:
    """发一个 GET / POST，把响应体当 JSON 解出来。

    Args:
        payload: 有就是 POST + JSON 请求体，没有就是 GET。
        retries: 失败后**额外**重试的次数。只对「可能自己会好」的错误重试 ——
            网络抖动、超时、429、5xx。4xx（Key 不对、参数不对）重试多少次
            都是同一个结果，白等两轮只会让用户以为卡死了。

    Raises:
        SearchError: 网络不通、超时、非 JSON 响应、或 4xx。
    """
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    merged = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        **(headers or {}),
    }
    request = Request(url, data=body, headers=merged, method="POST" if body else "GET")

    last: SearchError | None = None
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - 地址来自配置，非模型输入
                raw = response.read()
        except HTTPError as exc:
            detail = _error_detail(exc)
            if exc.code != 429 and exc.code < 500:
                raise SearchError(detail) from exc
            last = SearchError(detail)
        except (URLError, TimeoutError, OSError) as exc:
            last = SearchError(f"网络请求失败：{exc}")
        else:
            try:
                return json.loads(raw.decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, ValueError) as exc:
                raise SearchError("接口返回的不是 JSON，可能地址填错了。") from exc

        if attempt < retries:
            time.sleep(0.8 * (attempt + 1))  # 退避：先等一下再试，别把对端压垮

    raise last if last is not None else SearchError("请求失败。")


# ---------------------------------------------------------------------------
# 各家后端
# ---------------------------------------------------------------------------


def _clip(text: str, limit: int = MAX_SNIPPET_CHARS) -> str:
    """把摘变压到 limit 以内，超了加省略号。"""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _hits_from(
    pages: list[Any], *, title_key: str, url_key: str, text_keys: tuple[str, ...]
) -> list[SearchHit]:
    """把一列原始条目掰成 `SearchHit`。

    两家返回的字段名不同（Tavily 是 title/url/content，博查是 name/url/snippet+summary），
    但结构是一样的，所以抽成一个「键名参数化」的函数。摘要按 `text_keys` 的顺序取
    第一个非空的 —— 比如博查优先用 `summary`，没有才退回 `snippet`。
    """
    hits: list[SearchHit] = []
    for page in pages:
        if not isinstance(page, dict):
            continue

        url = str(page.get(url_key) or "").strip()
        if not url:
            continue  # 没有地址的结果对模型没用 —— 它没法接着读正文

        snippet = ""
        for key in text_keys:
            snippet = str(page.get(key) or "").strip()
            if snippet:
                break

        hits.append(
            SearchHit(
                title=str(page.get(title_key) or "").strip() or url,
                url=url,
                snippet=_clip(snippet),
            )
        )

    return hits


def _search_tavily(config: SearchConfig, query: str, count: int) -> list[SearchHit]:
    """Tavily 的 `POST /search`。"""
    data = _request_json(
        config.endpoint_for(SearchBackend.TAVILY),
        payload={"query": query, "max_results": count, "search_depth": "basic"},
        headers={"Authorization": f"Bearer {config.key_for(SearchBackend.TAVILY)}"},
    )

    pages = data.get("results") if isinstance(data, dict) else None
    return _hits_from(
        pages if isinstance(pages, list) else [],
        title_key="title",
        url_key="url",
        text_keys=("content", "snippet"),
    )


def _search_bocha(config: SearchConfig, query: str, count: int) -> list[SearchHit]:
    """博查的 `POST /v1/web-search`。"""
    data = _request_json(
        config.endpoint_for(SearchBackend.BOCHA),
        payload={"query": query, "count": count, "summary": True},
        headers={"Authorization": f"Bearer {config.key_for(SearchBackend.BOCHA)}"},
    )

    # 返回结构是 {"code": 200, "data": {"webPages": {"value": [...]}}}，但
    # 官方文档示例里 `webPages` 是顶层的 —— 两个版本都见过，所以两种都认。
    # 键不存在时不要当失败：那可能只是「这一问真的没有结果」
    pages = None
    if isinstance(data, dict):
        if isinstance(data.get("data"), dict):
            pages = data["data"].get("webPages")
        if pages is None:
            pages = data.get("webPages")
    values = pages.get("value") if isinstance(pages, dict) else None

    return _hits_from(
        values if isinstance(values, list) else [],
        title_key="name",
        url_key="url",
        text_keys=("summary", "snippet"),
    )


_BACKENDS = {
    SearchBackend.TAVILY: _search_tavily,
    SearchBackend.BOCHA: _search_bocha,
}


def search(
    query: str,
    *,
    config: SearchConfig,
    count: int | None = None,
) -> list[SearchHit]:
    """按配置好的后端搜一次。

    Args:
        query: 搜索词。
        config: 搜索配置（后端、Key、端点）。
        count: 要几条；不传就用配置里的默认值。

    Raises:
        SearchError: 没配 Key，或调用失败。
    """
    text = (query or "").strip()
    if not text:
        raise SearchError("搜索词是空的。")

    if not has_key(config):
        raise SearchError(missing_key_message(config))

    limit = count if count and count > 0 else config.max_results
    limit = max(1, min(limit, MAX_RESULTS_LIMIT))

    return _BACKENDS[config.backend](config, text, limit)


# ---------------------------------------------------------------------------
# 抓正文
# ---------------------------------------------------------------------------

# 整块丢掉的标签。它们的内容是导航、广告、脚本，不是文章正文 ——
# 留着会让摘要里混进一堆「登录 注册 首页」之类的噪声
_SKIP_TAGS = frozenset(
    {
        "head",
        "script",
        "style",
        "noscript",
        "svg",
        "canvas",
        "iframe",
        "template",
        "form",
        "nav",
        "header",
        "footer",
        "aside",
    }
)

# 遇到就换行的标签。纯文本里没有排版，靠这些标签把「段落」还原出来，
# 否则整页会挤成一行，模型很难读
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "main",
        "br",
        "hr",
        "li",
        "ul",
        "ol",
        "tr",
        "td",
        "th",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "pre",
        "blockquote",
        "dd",
        "dt",
        "figcaption",
    }
)


class _HtmlTextExtractor(HTMLParser):
    """把 HTML 扒成纯文本，顺带记下 `<title>`。

    用 `html.parser` 而不是正则：正则剥标签会把 `<script>` 里的内容一起当正文
    （那段代码里往往有大量 `if (a < b)` 之类的东西），而且遇到 `<` `>` 出现
    在属性值里就会错位。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # 实体（&amp; 等）自动还原
        self._skip_depth = 0
        self._in_title = False
        self.title = ""
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            # title 在 <head> 里，而 head 整体是被跳过的 —— 所以这里单独记，
            # 由 handle_data 优先判断（见那里）
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        # title 必须先判断：它在被跳过的 head 里面
        if self._in_title:
            self.title += data
            return
        if self._skip_depth:
            return
        self._parts.append(data)

    def text(self) -> str:
        """收尾：折叠空白。

        HTML 源码里的换行和缩进是给人看排版的，不是内容 —— 直接留着的话，
        正文会被切成一堆两三个字符的碎行。所以只保留「块级标签产生的换行」，
        行内空白一律压成一个空格。
        """
        raw = "".join(self._parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _decode(body: bytes, content_type: str) -> str:
    """按「响应头 → 页面里的 meta → 常见中文编码」的顺序猜编码。

    中文站点用 GBK 的还不少，一律按 UTF-8 解会得到一屏乱码 —— 那句乱码最后
    会被当成摘要交给模型，等于白抓。
    """
    charset = ""
    match = re.search(r"charset=[\"']?([\w-]+)", content_type or "", re.IGNORECASE)
    if match:
        charset = match.group(1)

    if not charset:
        # 只看开头一段：meta charset 必须出现在 <head> 里，不可能在文件末尾
        head = body[:2048].decode("ascii", errors="ignore")
        match = re.search(r"charset=[\"']?([\w-]+)", head, re.IGNORECASE)
        if match:
            charset = match.group(1)

    for encoding in (charset, "utf-8", "gb18030"):
        if not encoding:
            continue
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue

    # 全试过还是不行，用替换字符兜底 —— 有乱码总好过整个抓取失败
    return body.decode("utf-8", errors="replace")


def html_to_text(html: str) -> tuple[str, str]:
    """把 HTML 扒成 `(正文, 标题)`。

    单独拆出来是为了能直接测：抓取要联网，但「扒得干不干净」才是这段代码里
    唯一容易出错的部分。
    """
    parser = _HtmlTextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # 畸形 HTML 让解析器炸了，退回未解析的原文
        return _clip_keep_lines(html), ""

    return parser.text(), parser.title.strip()


def fetch_page(url: str) -> tuple[str, str]:
    """抓一个网页，返回「纯文本正文」和「页面标题」。

    不做 SSRF 拦截：这个 Agent 本来就带着 `run_command`，能 `curl` 任何地址，
    在这儿堵是没有意义的边界。真正的边界在「给不给模型这个工具」（工具组）。

    Args:
        url: 要抓的地址。

    Returns:
        (正文纯文本, 标题)；标题抓不到时是空串。

    Raises:
        SearchError: 地址不合法、网络失败、或返回的不是能读的正文类型。
    """
    target = (url or "").strip()
    parsed = urlparse(target)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SearchError("只能抓 http / https 开头的网页地址。")

    request = Request(
        target,
        headers={
            "User-Agent": USER_AGENT,
            # 明示偏好中文：有些站点会据此返回中文版或不同的语言
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )

    try:
        with urlopen(request, timeout=FETCH_TIMEOUT) as response:  # noqa: S310 - 见上面的 SSRF 说明
            content_type = response.headers.get("Content-Type", "")
            body = response.read(MAX_FETCH_BYTES)
    except HTTPError as exc:
        raise SearchError(f"抓取失败：{_error_detail(exc)}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise SearchError(f"抓取失败：{exc}") from exc

    text = _decode(body, content_type)
    kind = (content_type or "").split(";")[0].strip().lower()

    # 没给 Content-Type 的服务端不少见，按 HTML 试一把（错了也就是多一层解析）
    if kind in ("", "text/html", "application/xhtml+xml"):
        return html_to_text(text)

    if kind.startswith("text/") or kind.endswith(("json", "xml")):
        return text.strip(), ""

    # PDF、图片、压缩包…… 抓回来模型也读不了。说清楚，比喂一堆二进制强
    raise SearchError(f"这个地址返回的是「{kind}」，不是网页正文（只认 HTML / 文本）。")


def _clip_keep_lines(text: str) -> str:
    """兜底用：只做长度截断，保留换行。"""
    return text if len(text) <= MAX_FETCH_CHARS else text[:MAX_FETCH_CHARS] + "…"
