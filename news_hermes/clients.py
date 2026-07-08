from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPSConnection
from typing import TYPE_CHECKING, Final, Protocol
from urllib.parse import urlencode, urlsplit

from .feeds import parse_feed
from .json_types import JsonObject, JsonValue, parse_json
from .models import RawNewsItem, SourceType

if TYPE_CHECKING:
    from .config import SearxngConfig, TriageConfig

HTTP_TIMEOUT_SECONDS = 30
JSON_FENCE: Final = "```"
FENCED_JSON_MIN_LINES: Final = 3
LOGGER = logging.getLogger("news_hermes")


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


@dataclass(frozen=True, slots=True)
class TriageResult:
    summaries: dict[str, str]
    error: str | None = None


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
) -> TriageResult:
    if not items:
        return TriageResult({})
    prompt_items = [{"title": item.title, "url": item.url, "source": item.source} for item in items]
    payload: JsonObject = {
        "model": config.model,
        "stream": False,
        "options": {"temperature": config.temperature},
        "messages": [
            {"role": "system", "content": _system_prompt(config)},
            {"role": "user", "content": json.dumps(prompt_items, ensure_ascii=False)},
        ],
    }
    response = client.post_json(f"{config.ollama_endpoint.rstrip('/')}/api/chat", payload)
    if not isinstance(response, dict):
        return _triage_error("ollama response was not a JSON object")
    message = response.get("message")
    if not isinstance(message, dict):
        return _triage_error("ollama response.message was not an object")
    content = message.get("content")
    if not isinstance(content, str):
        return _triage_error("ollama response.message.content was not a string")
    parsed = parse_json(_json_content(content))
    if not isinstance(parsed, list):
        return _triage_error("ollama response.message.content was not a JSON array")
    return TriageResult(_summaries(parsed))


def _triage_error(message: str) -> TriageResult:
    LOGGER.warning("news-hermes triage error: %s", message)
    return TriageResult({}, message)


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


def _json_content(content: str) -> str:
    stripped = content.strip()
    if not stripped.startswith(JSON_FENCE):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < FENCED_JSON_MIN_LINES or not lines[-1].strip().startswith(JSON_FENCE):
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _string(value: JsonValue | None) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _system_prompt(config: TriageConfig) -> str:
    if config.system_prompt is not None:
        return config.system_prompt.replace("{language}", config.language)
    return (
        "You are a tech news triage filter. Keep only genuinely new and relevant items. "
        "Drop noise, ads, listicles, and generic aggregator pages. "
        f"For each kept item, write a concise summary in {config.language}. "
        'Output JSON: [{"title":"...","url":"...","summary":"..."}]. '
        "If nothing is relevant, output []."
    )


def _connection(scheme: str, netloc: str, timeout_seconds: int) -> HTTPConnection:
    if scheme == "https":
        return HTTPSConnection(netloc, timeout=timeout_seconds)
    return HTTPConnection(netloc, timeout=timeout_seconds)
