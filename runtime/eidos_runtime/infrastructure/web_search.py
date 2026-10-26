from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Literal
from urllib.parse import urlsplit

import anyio
import httpx2 as httpx
from pydantic import BaseModel, Field, ValidationError

from eidos_runtime.domain.web_search import (
    DoubaoSearchOptions,
    LangSearchOptions,
    RunWebSearchSnapshot,
    SearchProvider,
)
from eidos_runtime.models import EidosFrozenStrictModel

PROVIDER_HOSTS: dict[SearchProvider, str] = {
    "deepseek": "api.deepseek.com",
    "langsearch": "api.langsearch.com",
    "doubao": "open.feedcoopapi.com",
}
PROVIDER_URLS: dict[SearchProvider, str] = {
    "deepseek": "https://api.deepseek.com/v1/tools/search",
    "langsearch": "https://api.langsearch.com/v1/web-search",
    "doubao": "https://open.feedcoopapi.com/search_api/web_search",
}

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_EXCERPT_BYTES = 100 * 1024


class SearchHit(EidosFrozenStrictModel):
    title: str = Field(max_length=1000)
    url: str = Field(max_length=8192)
    excerpt: str = Field(max_length=10000)
    content_kind: Literal["summary", "snippet", "text", "none"]
    published_at: str | None = Field(default=None, max_length=128)
    truncated: bool = False


class SearchUsage(EidosFrozenStrictModel):
    input_tokens: int = Field(default=0, ge=0, le=9_007_199_254_740_991)
    output_tokens: int = Field(default=0, ge=0, le=9_007_199_254_740_991)


class SearchPage(EidosFrozenStrictModel):
    results: list[SearchHit] = Field(max_length=50)
    truncated: bool
    retrieved_at: str
    usage: SearchUsage | None = None


class SearchFailure(Exception):
    def __init__(self, code: str, message: str, *, sent: bool = False) -> None:
        self.code, self.message, self.sent = code, message, sent
        super().__init__(code)


# These DTOs describe third-party responses. Providers may add unrelated fields.
class _Hit(BaseModel):
    title: str = ""
    name: str = ""
    url: str
    snippet: str | None = None
    summary: str | None = None
    content: str | None = None
    text: str | None = None
    date: str | None = None
    datePublished: str | None = None


class _DeepSeekResponse(BaseModel):
    results: list[_Hit] = Field(max_length=50)


class _WebPages(BaseModel):
    value: list[_Hit] = Field(max_length=50)


class _LangData(BaseModel):
    webPages: _WebPages


class _LangResponse(BaseModel):
    code: int | str
    data: _LangData | None = None
    usage: SearchUsage | None = None


class _DoubaoHit(BaseModel):
    Title: str = ""
    Url: str | None = None
    Summary: str | None = None
    Snippet: str | None = None
    Content: str | None = None
    PublishTime: str | None = None


class _DoubaoError(BaseModel):
    Code: str | int
    CodeN: int | None = None


class _DoubaoMetadata(BaseModel):
    Error: _DoubaoError | None = None


class _DoubaoResult(BaseModel):
    ResultCount: int = Field(ge=0)
    WebResults: list[_DoubaoHit] | None = Field(default=None, max_length=50)


class _DoubaoResponse(BaseModel):
    ResponseMetadata: _DoubaoMetadata
    Result: _DoubaoResult | None = None


def request_payload(
    snapshot: RunWebSearchSnapshot, query: str, count: int
) -> dict[str, object]:
    options = snapshot.config.options(snapshot.provider)
    if snapshot.provider == "doubao":
        assert isinstance(options, DoubaoSearchOptions)
        filters: dict[str, object] = {"NeedUrl": True}
        if options.freshness != "noLimit":
            freshness = options.freshness
            filters["TimeRange"] = (
                freshness[0].upper() + freshness[1:]
                if freshness.startswith("one")
                else freshness
                if ".." in freshness
                else f"{freshness}..{freshness}"
            )
        if options.include_domains:
            filters["Sites"] = "|".join(options.include_domains)
        if options.exclude_domains:
            filters["BlockHosts"] = "|".join(options.exclude_domains)
        # NeedContent filters out pages without full text. It is not the
        # include_page_content switch, which only controls local projection.
        return {
            "Query": query,
            "SearchType": "web",
            "Count": count,
            "Filter": filters,
            "QueryControl": {"QueryRewrite": False},
            "ContentFormats": "text",
        }
    body: dict[str, object] = {"query": query, "count": count}
    if snapshot.provider == "deepseek":
        body["need_content"] = options.include_page_content
        if snapshot.config.deepseek.localization:
            body["localization"] = snapshot.config.deepseek.localization
    else:
        assert isinstance(options, LangSearchOptions)
        body["freshness"] = options.freshness
        if options.include_page_content:
            body["contents"] = {
                "text": {"maxCharacters": options.max_excerpt_characters}
            }
        if options.include_domains:
            body["includeDomains"] = options.include_domains
        if options.exclude_domains:
            body["excludeDomains"] = options.exclude_domains
    return body


def _status_error(status: int) -> SearchFailure:
    code, message = {
        401: ("web_search_auth_failed", "搜索服务拒绝了 API Key。请检查网页搜索设置。"),
        403: ("web_search_access_denied", "搜索服务拒绝访问。请检查账号权限或额度。"),
        429: (
            "web_search_rate_limited",
            "搜索服务当前限流或额度不足。请稍后重试或检查服务控制台。",
        ),
    }.get(
        status,
        ("web_search_unavailable", "搜索服务暂时不可用。请稍后重试。")
        if status >= 500
        else (
            "web_search_request_failed",
            "搜索服务未接受请求。请检查查询和服务设置。",
        ),
    )
    return SearchFailure(code, message, sent=True)


def _doubao_failure(error: _DoubaoError, status: int) -> SearchFailure:
    code = str(error.CodeN if error.CodeN is not None else error.Code)
    if code == "10401":
        return _status_error(401)
    if code in {"10402", "10403", "100013"}:
        return _status_error(403)
    if code == "700429":
        return _status_error(429)
    access_messages = {
        "10406": "豆包搜索免费额度已用尽。请检查是否已开通付费调用。",
        "10408": "豆包搜索尚未开通付费服务。请检查服务开通状态。",
        "10409": "豆包搜索当前套餐 Key 不支持该搜索类型。请检查 API Key 类型。",
        "10410": "豆包搜索没有可用订阅套餐。请检查套餐是否开通或已到期。",
        "10412": "豆包搜索订阅套餐剩余额度不足。请检查套餐额度或更换 API Key。",
    }
    if code in access_messages:
        return SearchFailure(
            "web_search_access_denied",
            access_messages[code],
            sent=True,
        )
    return _status_error(503 if code == "10500" else status if status != 200 else 400)


async def search(
    snapshot: RunWebSearchSnapshot,
    key: str,
    query: str,
    count: int,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SearchPage:
    options = snapshot.config.options(snapshot.provider)
    url = PROVIDER_URLS[snapshot.provider]
    try:
        with anyio.fail_after(options.timeout_seconds):
            async with httpx.AsyncClient(
                timeout=options.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                transport=transport,
            ) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers={"Authorization": "Bearer " + key},
                    json=request_payload(snapshot, query, count),
                ) as response:
                    status = response.status_code
                    if status != 200 and snapshot.provider != "doubao":
                        raise _status_error(response.status_code)
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise SearchFailure(
                                "web_search_response_too_large",
                                "搜索响应超过大小限制。请缩小查询范围。",
                                sent=True,
                            )
                        raw.extend(chunk)
        if snapshot.provider == "deepseek":
            hits = _DeepSeekResponse.model_validate_json(raw).results
            usage = None
        elif snapshot.provider == "doubao":
            try:
                doubao = _DoubaoResponse.model_validate_json(raw)
            except ValidationError:
                if status != 200:
                    raise _status_error(status) from None
                raise
            if doubao.ResponseMetadata.Error is not None:
                raise _doubao_failure(doubao.ResponseMetadata.Error, status)
            if status != 200:
                raise _status_error(status)
            if doubao.Result is None:
                raise ValueError("missing search data")
            if doubao.Result.WebResults is None and doubao.Result.ResultCount:
                raise ValueError("missing web results")
            hits = [
                _Hit(
                    title=hit.Title,
                    url=hit.Url or "",
                    summary=hit.Summary,
                    snippet=hit.Snippet,
                    content=hit.Content,
                    date=hit.PublishTime,
                )
                for hit in doubao.Result.WebResults or []
            ]
            usage = None
        else:
            parsed = _LangResponse.model_validate_json(raw)
            if str(parsed.code) != "200":
                status = int(parsed.code) if str(parsed.code).isdigit() else 400
                raise _status_error(status)
            if parsed.data is None:
                raise ValueError("missing search data")
            hits, usage = parsed.data.webPages.value, parsed.usage
        results: list[SearchHit] = []
        budget = MAX_EXCERPT_BYTES
        truncated = len(hits) > count
        result_budget = 240 * 1024
        for hit in hits[:count]:
            try:
                parts = urlsplit(hit.url)
                _ = parts.port
            except ValueError:
                continue
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or parts.username
                or parts.password
                or key in hit.url
                or len(hit.url) > 8192
                or any(ord(c) <= 32 or ord(c) == 127 for c in hit.url)
            ):
                continue
            candidates: list[tuple[str, str | None]] = []
            if options.include_page_content:
                candidates.extend((("text", hit.content), ("text", hit.text)))
            candidates.extend(
                (("summary", hit.summary), ("snippet", hit.snippet), ("text", hit.text))
            )
            kind, source = next(
                ((kind, text) for kind, text in candidates if text), ("none", "")
            )
            source = (source or "").replace(key, "[redacted]")
            excerpt = (
                source[: options.max_excerpt_characters]
                .encode()[:budget]
                .decode("utf-8", errors="ignore")
            )
            budget -= len(excerpt.encode())
            cut = excerpt != source
            truncated = truncated or cut
            result = SearchHit(
                title=(hit.title or hit.name or parts.hostname).replace(
                    key, "[redacted]"
                )[:1000],
                url=hit.url,
                excerpt=excerpt,
                content_kind=kind,
                published_at=((hit.date or hit.datePublished or "")[:128] or None)
                if key not in (hit.date or hit.datePublished or "")
                else None,
                truncated=cut,
            )
            size = len(json.dumps(result.to_wire_dict(), ensure_ascii=False).encode())
            if size > result_budget:
                truncated = True
                break
            result_budget -= size
            results.append(result)
        if hits and not results:
            raise ValueError("no valid search URLs")
        return SearchPage(
            results=results,
            truncated=truncated,
            usage=usage,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
        )
    except SearchFailure:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise SearchFailure(
            "web_search_timeout",
            "搜索请求超时。该请求可能已经计费。系统没有自动重试。",
            sent=True,
        ) from None
    except httpx.RequestError:
        raise SearchFailure(
            "web_search_network_error",
            "搜索请求未能完成。系统没有自动重试。",
            sent=True,
        ) from None
    except (ValidationError, ValueError):
        raise SearchFailure(
            "web_search_invalid_response", "搜索服务返回了无法识别的结果。", sent=True
        ) from None
