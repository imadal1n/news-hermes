from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from news_hermes.models import (
    NewsDocument,
    NewsId,
    NewsSource,
    NewsStatus,
    RawNewsItem,
    SourceName,
    SourceType,
)
from news_hermes.storage import NewsStore, RetentionPolicy

if TYPE_CHECKING:
    from pathlib import Path


def test_missing_file_loads_empty_document(tmp_path: Path) -> None:
    # Given: a store path that does not exist.
    store = NewsStore(tmp_path / "news.json")

    # When: the document is loaded.
    result = store.load()

    # Then: the store starts empty.
    assert result == NewsDocument(())


def test_ingest_deduplicates_by_url(tmp_path: Path) -> None:
    # Given: two raw items with the same URL.
    store = NewsStore(tmp_path / "news.json")
    raw = RawNewsItem("Release", "https://example.test/a", "vendor", SourceType.RSS, None)

    # When: both are ingested.
    result = store.ingest((raw, raw), {raw.url: "Summary"})

    # Then: one item is stored with the triage summary.
    assert not isinstance(result, str)
    assert len(result.items) == 1
    assert result.items[0].summary == "Summary"
    assert result.items[0].source_type == SourceType.RSS


def test_bookmark_and_dismiss_update_status(tmp_path: Path) -> None:
    # Given: one stored item.
    store = NewsStore(tmp_path / "news.json")
    raw = RawNewsItem("Release", "https://example.test/a", "vendor", SourceType.RSS, None)
    created = store.ingest((raw,), {})
    assert not isinstance(created, str)
    item_id = created.items[0].id

    # When: the item is bookmarked then dismissed.
    bookmarked = store.bookmark(item_id)
    dismissed = store.dismiss(item_id)

    # Then: each operation returns the updated item.
    assert not isinstance(bookmarked, str)
    assert bookmarked.status == NewsStatus.BOOKMARKED
    assert not isinstance(dismissed, str)
    assert dismissed.status == NewsStatus.DISMISSED


def test_clear_all_requires_confirm(tmp_path: Path) -> None:
    # Given: a store with an item.
    store = NewsStore(tmp_path / "news.json")
    raw = RawNewsItem("Release", "https://example.test/a", "vendor", SourceType.RSS, None)
    _ = store.ingest((raw,), {})

    # When: clear all is called without confirmation.
    result = store.clear("all", confirm=False, policy=RetentionPolicy(7, 14))

    # Then: the clear is rejected.
    assert result == "confirm=true is required to clear all news items"


def test_sources_live_in_news_store(tmp_path: Path) -> None:
    # Given: an empty news store.
    store = NewsStore(tmp_path / "news.json")
    source = NewsSource(SourceName("vendor"), SourceType.RSS, "https://example.test/feed.xml", "")

    # When: a source is added and then removed by name.
    added = store.add_source(source)
    removed = store.remove_source(SourceName("vendor"))

    # Then: the source list is stored in news.json and mutates without config edits.
    assert not isinstance(added, str)
    assert added.sources == (source,)
    assert not isinstance(removed, str)
    assert removed.sources == ()


def test_retention_purges_old_dismissed_and_new_items(tmp_path: Path) -> None:
    # Given: old new and dismissed items in the store.
    store = NewsStore(tmp_path / "news.json")
    old = (datetime.now(UTC) - timedelta(days=20)).isoformat(timespec="seconds")
    dismissed_old = (datetime.now(UTC) - timedelta(days=8)).isoformat(timespec="seconds")
    _ = store.path.write_text(
        """
{"items":[
{"id":"news_new","title":"Old","url":"https://example.test/new","summary":"","source":"vendor","source_type":"rss","status":"new","found_at":"OLD","published_at":null,"bookmarked_at":null,"dismissed_at":null},
{"id":"news_dismissed","title":"Dismissed","url":"https://example.test/d","summary":"","source":"vendor","source_type":"rss","status":"dismissed","found_at":"OLD","published_at":null,"bookmarked_at":null,"dismissed_at":"DISMISSED"},
{"id":"news_bookmark","title":"Saved","url":"https://example.test/b","summary":"","source":"vendor","source_type":"rss","status":"bookmarked","found_at":"OLD","published_at":null,"bookmarked_at":"OLD","dismissed_at":null}
]}
""".replace("OLD", old).replace("DISMISSED", dismissed_old),
        encoding="utf-8",
    )

    # When: retention is applied.
    result = store.purge_expired(RetentionPolicy(dismissed_days=7, new_days=14))

    # Then: bookmarked items survive while expired new/dismissed items are removed.
    assert not isinstance(result, str)
    assert [item.id for item in result.items] == [NewsId("news_bookmark")]
