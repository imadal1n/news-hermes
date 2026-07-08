from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from .feeds import parse_feed
from .json_types import JsonObject, JsonValue, parse_json
from .models import NewsSource, RawNewsItem, SourceName, SourceType

if TYPE_CHECKING:
    from .clients import HttpClient
    from .config import SearxngConfig

LOGGER = logging.getLogger("news_hermes")


def fetch_rss(
    source: NewsSource,
    client: HttpClient,
    *,
    limit: int | None = None,
) -> tuple[RawNewsItem, ...]:
    text = client.get_text(source.url)
    if text is None:
        _log_no_items(source, "HTTP error")
        return ()
    items = parse_feed(source, text, limit=limit)
    if not items:
        _log_no_items(source, "0 feed entries")
    return items


def fetch_searxng(
    source: NewsSource,
    time_range: str,
    client: HttpClient,
    *,
    limit: int | None = None,
) -> tuple[RawNewsItem, ...]:
    url = f"{source.url.rstrip('/')}/search?{_query_params(source.query, time_range)}"
    text = client.get_text(url)
    if text is None:
        _log_no_items(source, "HTTP error")
        return ()
    parsed = parse_json(text)
    if not isinstance(parsed, dict):
        _log_no_items(source, "invalid JSON response")
        return ()
    items = _searxng_items(source.query, parsed, limit=limit)
    if not items:
        _log_no_items(source, "0 results")
    return items


def fetch_github_releases(
    source: NewsSource,
    client: HttpClient,
    *,
    limit: int | None = None,
) -> tuple[RawNewsItem, ...]:
    source_url = source.url.strip()
    if not source_url.startswith("http"):
        source_url = f"https://api.github.com/repos/{source_url}/releases"
    text = client.get_text(source_url)
    if text is None:
        _log_no_items(source, "HTTP error")
        return ()
    parsed = parse_json(text)
    if not isinstance(parsed, list):
        _log_no_items(source, "invalid JSON array")
        return ()
    items: list[RawNewsItem] = []
    for value in parsed:
        if limit is not None and len(items) >= limit:
            break
        if not isinstance(value, dict):
            continue
        if value.get("draft") is True:
            continue
        title = _string(value.get("name")) or _string(value.get("tag_name"))
        url = _string(value.get("html_url"))
        if not title or not url:
            continue
        items.append(
            RawNewsItem(
                title=title,
                url=url,
                source=source.name,
                source_type=SourceType.GITHUB_RELEASES,
                published_at=_string(value.get("published_at")),
            )
        )
    return tuple(items)


def collect_source(
    source: NewsSource,
    time_range: str,
    client: HttpClient,
    *,
    limit: int | None = None,
) -> tuple[RawNewsItem, ...]:
    match source.type:
        case SourceType.RSS:
            return fetch_rss(source, client, limit=limit)
        case SourceType.SEARXNG:
            return fetch_searxng(source, time_range, client, limit=limit)
        case SourceType.GITHUB_RELEASES:
            return fetch_github_releases(source, client, limit=limit)


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
        source = NewsSource(
            name=SourceName(query),
            type=SourceType.SEARXNG,
            url=config.endpoint,
            query=query,
        )
        items.extend(
            fetch_searxng(
                source,
                config.time_range,
                client,
                limit=_remaining_limit(limit, len(items)),
            )
        )
    return tuple(items)


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


def _log_no_items(source: NewsSource, reason: str) -> None:
    LOGGER.warning("news-hermes source %s returned no items: %s", source.name, reason)


def _string(value: JsonValue | None) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""
