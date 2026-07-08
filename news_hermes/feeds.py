from __future__ import annotations

from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Protocol

import feedparser

from .models import RawNewsItem, SourceType

if TYPE_CHECKING:
    from collections.abc import Iterable


class FeedSource(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def url(self) -> str: ...


class FeedEntry(Protocol):
    def get(self, key: str, default: str) -> object: ...


def parse_feed(
    source: FeedSource, text: str, *, limit: int | None = None
) -> tuple[RawNewsItem, ...]:
    feed = feedparser.parse(text)
    items: list[RawNewsItem] = []
    for entry in feed.entries:
        if limit is not None and len(items) >= limit:
            break
        title = _field(entry, "title")
        url = _field(entry, "link")
        if not title or not url:
            continue
        items.append(
            RawNewsItem(
                title=title,
                url=url,
                source=source.name,
                source_type=SourceType.RSS,
                published_at=_published_at(entry),
            )
        )
    return tuple(items)


def unseen_items(
    items: Iterable[RawNewsItem],
    seen_urls: frozenset[str],
) -> tuple[RawNewsItem, ...]:
    return tuple(item for item in items if item.url not in seen_urls)


def _field(entry: FeedEntry, key: str) -> str:
    value = entry.get(key, "")
    if isinstance(value, str):
        return value.strip()
    return ""


def _published_at(entry: FeedEntry) -> str | None:
    raw = _field(entry, "published") or _field(entry, "updated")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        return raw
