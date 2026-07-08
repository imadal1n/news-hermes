from __future__ import annotations

from .clients import HttpClient, fetch_rss, search_searxng
from .config import SearxngConfig
from .json_types import JsonValue, parse_json
from .models import NewsSource, RawNewsItem, SourceType


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
        return ()
    parsed = parse_json(text)
    if not isinstance(parsed, list):
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
            return search_searxng(
                SearxngConfig(
                    endpoint=source.url,
                    time_range=time_range,
                    queries=(source.query,),
                ),
                client,
                limit=limit,
            )
        case SourceType.GITHUB_RELEASES:
            return fetch_github_releases(source, client, limit=limit)


def _string(value: JsonValue | None) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""
