from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, TypeAlias
from uuid import uuid4

from .config import DEFAULT_STORE_PATH
from .json_types import parse_json
from .models import (
    NewsDocument,
    NewsId,
    NewsItem,
    NewsSource,
    NewsStatus,
    RawNewsItem,
    SourceName,
    parse_document,
)

if TYPE_CHECKING:
    from pathlib import Path

TEMP_SUFFIX: Final[str] = ".tmp"
ID_HEX_CHARS: Final[int] = 12
StoreResult: TypeAlias = NewsDocument | str
ItemResult: TypeAlias = NewsItem | str


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    dismissed_days: int
    new_days: int


@dataclass(frozen=True, slots=True)
class NewsStore:
    path: Path = DEFAULT_STORE_PATH

    def list(self, status: NewsStatus | None) -> StoreResult:
        document = self.load()
        if isinstance(document, str):
            return document
        if status is None:
            return document
        return NewsDocument(tuple(item for item in document.items if item.status == status))

    def bookmark(self, item_id: NewsId) -> ItemResult:
        return self._set_status(item_id, NewsStatus.BOOKMARKED)

    def dismiss(self, item_id: NewsId) -> ItemResult:
        return self._set_status(item_id, NewsStatus.DISMISSED)

    def add_source(self, source: NewsSource) -> StoreResult:
        document = self.load()
        if isinstance(document, str):
            return document
        sources = tuple(item for item in document.sources if item.name != source.name)
        updated = NewsDocument(document.items, (*sources, source))
        self.save(updated)
        return updated

    def remove_source(self, name: SourceName) -> StoreResult:
        document = self.load()
        if isinstance(document, str):
            return document
        updated = NewsDocument(
            document.items, tuple(source for source in document.sources if source.name != name)
        )
        self.save(updated)
        return updated

    def seed_sources(self, sources: tuple[NewsSource, ...]) -> StoreResult:
        document = self.load()
        if isinstance(document, str):
            return document
        if document.sources or not sources:
            return document
        updated = NewsDocument(document.items, sources)
        self.save(updated)
        return updated

    def ingest(self, raw_items: tuple[RawNewsItem, ...], summaries: dict[str, str]) -> StoreResult:
        document = self.load()
        if isinstance(document, str):
            return document
        known_urls = {item.url for item in document.items}
        now = timestamp_now()
        items = list(document.items)
        for raw in raw_items:
            if raw.url in known_urls:
                continue
            items.append(_news_item(raw, summaries.get(raw.url, ""), now))
            known_urls.add(raw.url)
        updated = NewsDocument(tuple(items), document.sources)
        self.save(updated)
        return updated

    def purge_expired(self, policy: RetentionPolicy, now: datetime | None = None) -> StoreResult:
        document = self.load()
        if isinstance(document, str):
            return document
        reference = datetime.now(UTC).replace(tzinfo=None) if now is None else now
        kept = tuple(item for item in document.items if not _expired(item, policy, reference))
        updated = NewsDocument(kept, document.sources)
        self.save(updated)
        return updated

    def clear(self, target: str, *, confirm: bool, policy: RetentionPolicy) -> StoreResult:
        document = self.load()
        if isinstance(document, str):
            return document
        if target == "all" and not confirm:
            return "confirm=true is required to clear all news items"
        if target == "all":
            updated = NewsDocument((), document.sources)
        elif target == "dismissed":
            updated = NewsDocument(
                tuple(item for item in document.items if item.status != NewsStatus.DISMISSED),
                document.sources,
            )
        elif target == "expired":
            reference = datetime.now(UTC).replace(tzinfo=None)
            updated = NewsDocument(
                tuple(item for item in document.items if not _expired(item, policy, reference)),
                document.sources,
            )
        else:
            return "target must be dismissed, expired, or all"
        self.save(updated)
        return updated

    def load(self) -> StoreResult:
        if not self.path.exists():
            return NewsDocument(())
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            return f"could not read news file: {exc}"
        value = parse_json(text)
        if value is None:
            return "news file is not valid JSON"
        return parse_document(value)

    def save(self, document: NewsDocument) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f"{self.path.name}{TEMP_SUFFIX}")
        _ = temp_path.write_text(
            json.dumps(document.to_json(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        _ = temp_path.replace(self.path)

    def _set_status(self, item_id: NewsId, status: NewsStatus) -> ItemResult:
        document = self.load()
        if isinstance(document, str):
            return document
        found = find_item(document.items, item_id)
        if found is None:
            return "news item not found"
        updated = found.with_status(status, timestamp_now())
        self.save(
            NewsDocument(
                tuple(updated if item.id == item_id else item for item in document.items),
                document.sources,
            )
        )
        return updated


def timestamp_now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


def new_news_id() -> NewsId:
    return NewsId(f"news_{uuid4().hex[:ID_HEX_CHARS]}")


def find_item(items: tuple[NewsItem, ...], item_id: NewsId) -> NewsItem | None:
    for item in items:
        if item.id == item_id:
            return item
    return None


def _news_item(raw: RawNewsItem, summary: str, found_at: str) -> NewsItem:
    return NewsItem(
        id=new_news_id(),
        title=raw.title,
        url=raw.url,
        summary=summary,
        source=raw.source,
        source_type=raw.source_type,
        status=NewsStatus.NEW,
        found_at=found_at,
        published_at=raw.published_at,
        bookmarked_at=None,
        dismissed_at=None,
    )


def _expired(item: NewsItem, policy: RetentionPolicy, now: datetime) -> bool:
    parsed = _parse_timestamp(
        item.dismissed_at if item.status == NewsStatus.DISMISSED else item.found_at
    )
    if parsed is None:
        return False
    if item.status == NewsStatus.DISMISSED:
        return parsed < now - timedelta(days=policy.dismissed_days)
    if item.status == NewsStatus.NEW:
        return parsed < now - timedelta(days=policy.new_days)
    return False


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError:
        return None
