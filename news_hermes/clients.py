from __future__ import annotations

import json
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPSConnection
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlencode, urlsplit

from .feeds import parse_feed
from .json_types import JsonObject, JsonValue, parse_json
from .models import RawNewsItem, SourceType

if TYPE_CHECKING:
    from .config import SearxngConfig, TriageConfig

HTTP_TIMEOUT_SECONDS = 30


class HttpClient(Protocol):
    def get_text(self, url: str) -> str | None: ...

    def post_json(self, url: str, payload: JsonObject) -> JsonValue | None: ...


class FeedSource(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def url(self) -> str: ...


@dataclass(frozen=True, slots=True)
class UrlLibHttpClient:
    timeout_seconds: int = HTTP_TIMEOUT_SECONDS

    def get_text(self, url: str) -> str | None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            return None
        try:
            connection = _connection(parts.scheme, parts.netloc, self.timeout_seconds)
            path = parts.path or "/"
            if parts.query:
                path = f"{path}?{parts.query}"
            connection.request("GET", path, headers={"User-Agent": "news-hermes/0.1"})
            response = connection.getresponse()
            return response.read().decode("utf-8", errors="replace")
        except (TimeoutError, OSError, UnicodeDecodeError):
            return None

    def post_json(self, url: str, payload: JsonObject) -> JsonValue | None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            return None
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        try:
            connection = _connection(parts.scheme, parts.netloc, self.timeout_seconds)
            path = parts.path or "/"
            if parts.query:
                path = f"{path}?{parts.query}"
            connection.request(
                "POST",
                path,
                body=body,
                headers={"Content-Type": "application/json", "User-Agent": "news-hermes/0.1"},
            )
            response = connection.getresponse()
            return parse_json(response.read().decode("utf-8", errors="replace"))
        except (TimeoutError, OSError, UnicodeDecodeError):
            return None


def fetch_rss(source: FeedSource, client: HttpClient) -> tuple[RawNewsItem, ...]:
    text = client.get_text(source.url)
    if text is None:
        return ()
    return parse_feed(source, text)


def search_searxng(config: SearxngConfig, client: HttpClient) -> tuple[RawNewsItem, ...]:
    items: list[RawNewsItem] = []
    for query in config.queries:
        url = f"{config.endpoint.rstrip('/')}/search?{_query_params(query, config.time_range)}"
        value = client.get_text(url)
        if value is None:
            continue
        parsed = parse_json(value)
        if isinstance(parsed, dict):
            items.extend(_searxng_items(query, parsed))
    return tuple(items)


def triage_items(
    items: tuple[RawNewsItem, ...],
    config: TriageConfig,
    client: HttpClient,
) -> dict[str, str]:
    if not items:
        return {}
    prompt_items = [{"title": item.title, "url": item.url, "source": item.source} for item in items]
    payload: JsonObject = {
        "model": config.model,
        "stream": False,
        "options": {"temperature": config.temperature},
        "messages": [
            {"role": "system", "content": _system_prompt(config.language)},
            {"role": "user", "content": json.dumps(prompt_items, ensure_ascii=False)},
        ],
    }
    response = client.post_json(f"{config.ollama_endpoint.rstrip('/')}/api/chat", payload)
    if not isinstance(response, dict):
        return {}
    message = response.get("message")
    if not isinstance(message, dict):
        return {}
    content = message.get("content")
    if not isinstance(content, str):
        return {}
    parsed = parse_json(content)
    if not isinstance(parsed, list):
        return {}
    return _summaries(parsed)


def _query_params(query: str, time_range: str) -> str:
    return urlencode({"q": query, "format": "json", "time_range": time_range})


def _searxng_items(source: str, payload: JsonObject) -> tuple[RawNewsItem, ...]:
    results = payload.get("results")
    if not isinstance(results, list):
        return ()
    items: list[RawNewsItem] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        title = _string(result.get("title"))
        url = _string(result.get("url"))
        if title and url:
            items.append(
                RawNewsItem(
                    title=title,
                    url=url,
                    source=source,
                    source_type=SourceType.SEARXNG,
                    published_at=None,
                )
            )
    return tuple(items)


def _summaries(values: list[JsonValue]) -> dict[str, str]:
    summaries: dict[str, str] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        url = _string(value.get("url"))
        summary = _string(value.get("summary"))
        if url:
            summaries[url] = summary
    return summaries


def _string(value: JsonValue | None) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _system_prompt(language: str) -> str:
    return (
        "You are a tech news triage filter. Keep only genuinely new and relevant items. "
        "Drop noise, ads, listicles, and generic aggregator pages. "
        f"For each kept item, write a concise summary in {language}. "
        'Output JSON: [{"title":"...","url":"...","summary":"..."}]. '
        "If nothing is relevant, output []."
    )


def _connection(scheme: str, netloc: str, timeout_seconds: int) -> HTTPConnection:
    if scheme == "https":
        return HTTPSConnection(netloc, timeout=timeout_seconds)
    return HTTPConnection(netloc, timeout=timeout_seconds)
