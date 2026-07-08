from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import NewType, TypeAlias

from .json_types import JsonObject, JsonValue

NewsId = NewType("NewsId", str)
SourceName = NewType("SourceName", str)


class NewsStatus(StrEnum):
    NEW = "new"
    BOOKMARKED = "bookmarked"
    DISMISSED = "dismissed"


class SourceType(StrEnum):
    RSS = "rss"
    SEARXNG = "searxng"


NewsItemJson: TypeAlias = JsonObject
NewsSourceJson: TypeAlias = JsonObject
DocumentJson: TypeAlias = JsonObject
ParseItemResult: TypeAlias = "NewsItem | str"
ParseSourceResult: TypeAlias = "NewsSource | str"
ParseDocumentResult: TypeAlias = "NewsDocument | str"


@dataclass(frozen=True, slots=True)
class RawNewsItem:
    title: str
    url: str
    source: str
    source_type: SourceType
    published_at: str | None


@dataclass(frozen=True, slots=True)
class NewsSource:
    name: SourceName
    type: SourceType
    url: str
    query: str

    def to_json(self) -> NewsSourceJson:
        return {
            "name": self.name,
            "type": self.type.value,
            "url": self.url,
            "query": self.query,
        }


@dataclass(frozen=True, slots=True)
class NewsItem:
    id: NewsId
    title: str
    url: str
    summary: str
    source: str
    source_type: SourceType
    status: NewsStatus
    found_at: str
    published_at: str | None
    bookmarked_at: str | None
    dismissed_at: str | None

    def with_status(self, status: NewsStatus, at: str) -> NewsItem:
        match status:
            case NewsStatus.NEW:
                return replace(self, status=status, bookmarked_at=None, dismissed_at=None)
            case NewsStatus.BOOKMARKED:
                return replace(self, status=status, bookmarked_at=at, dismissed_at=None)
            case NewsStatus.DISMISSED:
                return replace(self, status=status, dismissed_at=at)

    def to_json(self) -> NewsItemJson:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "source": self.source,
            "source_type": self.source_type.value,
            "status": self.status.value,
            "found_at": self.found_at,
            "published_at": self.published_at,
            "bookmarked_at": self.bookmarked_at,
            "dismissed_at": self.dismissed_at,
        }


@dataclass(frozen=True, slots=True)
class NewsDocument:
    items: tuple[NewsItem, ...]
    sources: tuple[NewsSource, ...] = ()

    def to_json(self) -> DocumentJson:
        return {
            "items": [item.to_json() for item in self.items],
            "sources": [source.to_json() for source in self.sources],
        }


def parse_status(value: JsonValue) -> NewsStatus | None:
    if not isinstance(value, str):
        return None
    try:
        return NewsStatus(value)
    except ValueError:
        return None


def parse_source_type(value: JsonValue) -> SourceType | None:
    if not isinstance(value, str):
        return None
    try:
        return SourceType(value)
    except ValueError:
        return None


def parse_item(value: JsonValue) -> ParseItemResult:
    if not isinstance(value, dict):
        return "news item must be an object"
    status = parse_status(value.get("status"))
    source_type = parse_source_type(value.get("source_type"))
    required = _required_strings(value, ("id", "title", "url", "source", "found_at"))
    if isinstance(required, str):
        return required
    summary = value.get("summary")
    if not isinstance(summary, str):
        return "news item.summary must be a string"
    published_at = _optional_string(value.get("published_at"))
    bookmarked_at = _optional_string(value.get("bookmarked_at"))
    dismissed_at = _optional_string(value.get("dismissed_at"))
    if status is None:
        return "news item status must be new, bookmarked, or dismissed"
    if source_type is None:
        return "news item source_type must be rss or searxng"
    return NewsItem(
        id=NewsId(required["id"]),
        title=required["title"],
        url=required["url"],
        summary=summary,
        source=required["source"],
        source_type=source_type,
        status=status,
        found_at=required["found_at"],
        published_at=published_at,
        bookmarked_at=bookmarked_at,
        dismissed_at=dismissed_at,
    )


def parse_source(value: JsonValue) -> ParseSourceResult:
    if not isinstance(value, dict):
        return "news source must be an object"
    name = value.get("name")
    source_type = parse_source_type(value.get("type"))
    url = value.get("url")
    query = value.get("query", "")
    error: str | None = None
    if not isinstance(name, str) or not name:
        error = "news source.name must be a non-empty string"
    elif source_type is None:
        error = "news source.type must be rss or searxng"
    elif not isinstance(url, str):
        error = "news source.url must be a string"
    elif not isinstance(query, str):
        error = "news source.query must be a string"
    elif source_type == SourceType.RSS and not url:
        error = "rss source.url must be a non-empty string"
    elif source_type == SourceType.SEARXNG and not query:
        error = "searxng source.query must be a non-empty string"
    if error is not None:
        return error
    if (
        isinstance(name, str)
        and source_type is not None
        and isinstance(url, str)
        and isinstance(query, str)
    ):
        return NewsSource(name=SourceName(name), type=source_type, url=url, query=query)
    return "news source is invalid"


def parse_document(value: JsonValue) -> ParseDocumentResult:
    if not isinstance(value, dict):
        return "document must be an object"
    items = value.get("items")
    if not isinstance(items, list):
        return "document.items must be a list"
    parsed_items: list[NewsItem] = []
    for item_value in items:
        item = parse_item(item_value)
        if isinstance(item, str):
            return item
        parsed_items.append(item)
    sources = value.get("sources", [])
    if not isinstance(sources, list):
        return "document.sources must be a list"
    parsed_sources: list[NewsSource] = []
    for source_value in sources:
        source = parse_source(source_value)
        if isinstance(source, str):
            return source
        parsed_sources.append(source)
    return NewsDocument(tuple(parsed_items), tuple(parsed_sources))


def _required_strings(value: JsonObject, keys: tuple[str, ...]) -> dict[str, str] | str:
    result: dict[str, str] = {}
    for key in keys:
        raw = value.get(key)
        if not isinstance(raw, str) or not raw:
            return f"news item.{key} must be a non-empty string"
        result[key] = raw
    return result


def _optional_string(value: JsonValue) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return None
