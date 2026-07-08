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
TRIAGE_BATCH_SIZE: Final = 25
OUTPUT_JSON_INSTRUCTION: Final = (
    'Output JSON only: [{"title":"...","url":"...","summary":"..."}]. '
    "If nothing is relevant, output []."
)
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


def fetch_rss(
    source: FeedSource,
    client: HttpClient,
    *,
    limit: int | None = None,
) -> tuple[RawNewsItem, ...]:
    text = client.get_text(source.url)
    if text is None:
        return ()
    return parse_feed(source, text, limit=limit)


def search_searxng(
    config: SearxngConfig,
    client: HttpClient,
    *,
    limit: int | None = None,
) -> tuple[RawNewsItem, ...]:
    items: list[RawNewsItem] = []
    for query in config.queries:
        if limit is not None and len(items) >= limit:
            break
        url = f"{config.endpoint.rstrip('/')}/search?{_query_params(query, config.time_range)}"
        value = client.get_text(url)
        if value is None:
            continue
        parsed = parse_json(value)
        if isinstance(parsed, dict):
            items.extend(_searxng_items(query, parsed, limit=_remaining_limit(limit, len(items))))
    return tuple(items)


def triage_items(
    items: tuple[RawNewsItem, ...],
    config: TriageConfig,
    client: HttpClient,
) -> TriageResult:
    if not items:
        return TriageResult({})
    summaries: dict[str, str] = {}
    for batch in _item_batches(items):
        result = _triage_batch(batch, config, client)
        if result.error is not None:
            return result
        summaries.update(result.summaries)
    return TriageResult(summaries)


def _triage_batch(
    items: tuple[RawNewsItem, ...],
    config: TriageConfig,
    client: HttpClient,
) -> TriageResult:
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


def _item_batches(items: tuple[RawNewsItem, ...]) -> tuple[tuple[RawNewsItem, ...], ...]:
    return tuple(
        items[index : index + TRIAGE_BATCH_SIZE]
        for index in range(0, len(items), TRIAGE_BATCH_SIZE)
    )


def _triage_error(message: str) -> TriageResult:
    LOGGER.warning("news-hermes triage error: %s", message)
    return TriageResult({}, message)


def _query_params(query: str, time_range: str) -> str:
    return urlencode({"q": query, "format": "json", "time_range": time_range})


def _remaining_limit(limit: int | None, count: int) -> int | None:
    if limit is None:
        return None
    return max(limit - count, 0)


def _searxng_items(
    source: str,
    payload: JsonObject,
    *,
    limit: int | None = None,
) -> tuple[RawNewsItem, ...]:
    results = payload.get("results")
    if not isinstance(results, list):
        return ()
    items: list[RawNewsItem] = []
    for result in results:
        if limit is not None and len(items) >= limit:
            break
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
    start = stripped.find(JSON_FENCE)
    if start == -1:
        return stripped
    body = stripped[start + len(JSON_FENCE) :].strip()
    end = body.find(JSON_FENCE)
    if end == -1:
        return stripped
    body = body[:end].strip()
    if not body:
        return stripped
    if body[0] not in "[{":
        parts = body.split(maxsplit=1)
        try:
            body = parts[1].strip()
        except IndexError:
            return stripped
    return body


def _string(value: JsonValue | None) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _system_prompt(config: TriageConfig) -> str:
    if config.system_prompt is not None:
        prompt = config.system_prompt.replace("{language}", config.language).strip()
        return f"{prompt} {OUTPUT_JSON_INSTRUCTION}"
    return (
        "You are a tech news triage filter. Keep only genuinely new and relevant items. "
        "Drop noise, ads, listicles, and generic aggregator pages. "
        f"For each kept item, write a concise summary in {config.language}. "
        f"{OUTPUT_JSON_INSTRUCTION}"
    )


def _connection(scheme: str, netloc: str, timeout_seconds: int) -> HTTPConnection:
    if scheme == "https":
        return HTTPSConnection(netloc, timeout=timeout_seconds)
    return HTTPConnection(netloc, timeout=timeout_seconds)
