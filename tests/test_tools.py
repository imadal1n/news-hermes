from __future__ import annotations

from typing import TYPE_CHECKING

from news_hermes.clients import TriageResult
from news_hermes.json_types import JsonObject, JsonValue, parse_json_object
from news_hermes.models import RawNewsItem, SourceType
from news_hermes.storage import NewsStore
from news_hermes.tools import (
    news_bookmark,
    news_clear,
    news_dismiss,
    news_list,
    news_pull,
    news_source_add,
    news_source_list,
    news_source_remove,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def parse_result(result: str) -> JsonObject:
    value = parse_json_object(result)
    assert value is not None
    return value


def json_object(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict)
    return value


def json_list(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def json_string(value: JsonValue) -> str:
    assert isinstance(value, str)
    return value


def test_news_list_defaults_to_new_items(tmp_path: Path) -> None:
    # Given: one stored item.
    path = tmp_path / "news.json"
    store = NewsStore(path)
    _ = store.ingest(
        (RawNewsItem("Release", "https://e.test", "vendor", SourceType.RSS, None),), {}
    )

    # When: items are listed.
    payload = parse_result(news_list({}, _news_path=str(path)))

    # Then: new items are returned.
    assert payload["ok"] is True
    items = [json_object(item) for item in json_list(payload["items"])]
    assert items[0]["status"] == "new"


def test_news_bookmark_and_dismiss_return_updated_item(tmp_path: Path) -> None:
    # Given: one stored item.
    path = tmp_path / "news.json"
    store = NewsStore(path)
    created = store.ingest(
        (RawNewsItem("Release", "https://e.test", "vendor", SourceType.RSS, None),), {}
    )
    assert not isinstance(created, str)
    item_id = created.items[0].id

    # When: the item is bookmarked and dismissed through tools.
    bookmarked = json_object(
        parse_result(news_bookmark({"id": item_id}, _news_path=str(path)))["item"]
    )
    dismissed = json_object(
        parse_result(news_dismiss({"id": item_id}, _news_path=str(path)))["item"]
    )

    # Then: each tool returns the changed status.
    assert bookmarked["status"] == "bookmarked"
    assert dismissed["status"] == "dismissed"


def test_news_clear_all_requires_confirm(tmp_path: Path) -> None:
    # Given: a temporary news store.
    path = tmp_path / "news.json"

    # When: all items are cleared without confirmation.
    payload = parse_result(news_clear({"target": "all"}, _news_path=str(path)))

    # Then: the tool reports a confirmation error.
    assert payload["ok"] is False
    error = json_object(payload["error"])
    assert error["code"] == "clear_error"


def test_news_source_tools_manage_sources_in_store(tmp_path: Path) -> None:
    # Given: a temporary news store path.
    path = tmp_path / "news.json"

    # When: RSS and SearXNG sources are added, listed, and one is removed.
    _ = news_source_add(
        {"name": "vendor", "type": "rss", "url": "https://example.test/feed.xml"},
        _news_path=str(path),
    )
    _ = news_source_add(
        {"name": "search", "type": "searxng", "url": "http://localhost:8888", "query": "AI"},
        _news_path=str(path),
    )
    listed = parse_result(news_source_list({}, _news_path=str(path)))
    removed = parse_result(news_source_remove({"name": "vendor"}, _news_path=str(path)))

    # Then: source state is persisted in the news store.
    sources = [json_object(source) for source in json_list(listed["sources"])]
    remaining = [json_object(source) for source in json_list(removed["sources"])]
    assert [source["name"] for source in sources] == ["vendor", "search"]
    assert [source["name"] for source in remaining] == ["search"]


def test_news_pull_baselines_first_run_and_ingests_only_new_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a configured source with two existing feed items.
    path = tmp_path / "news.json"
    config_path = tmp_path / "news-hermes.yaml"
    watermark_dir = tmp_path / "watermarks"
    _ = config_path.write_text(
        f"""
store_path: {path}
sources:
  rss:
    - name: vendor
      url: https://example.test/feed.xml
watermark_dir: {watermark_dir}
""",
        encoding="utf-8",
    )
    old_items = (
        RawNewsItem("Old 1", "https://example.test/old-1", "vendor", SourceType.RSS, None),
        RawNewsItem("Old 2", "https://example.test/old-2", "vendor", SourceType.RSS, None),
    )
    new_item = RawNewsItem("New", "https://example.test/new", "vendor", SourceType.RSS, None)
    pulls = [old_items, (*old_items, new_item)]

    def fake_collect_items(*_args: object) -> tuple[RawNewsItem, ...]:
        return pulls.pop(0)

    def fake_triage_items(
        items: tuple[RawNewsItem, ...],
        *_args: object,
    ) -> TriageResult:
        return TriageResult({item.url: "Rezumat" for item in items})

    monkeypatch.setattr("news_hermes.tools.collect_items", fake_collect_items)
    monkeypatch.setattr("news_hermes.tools.triage_items", fake_triage_items)

    # When: the first pull establishes a baseline and the second pull sees one new URL.
    first = parse_result(news_pull({}, _news_path=str(path), _news_config=str(config_path)))
    second = parse_result(news_pull({}, _news_path=str(path), _news_config=str(config_path)))

    # Then: historical items are not ingested, but the next unseen item is stored.
    assert first["ok"] is True
    assert first["new_count"] == 0
    assert second["ok"] is True
    assert second["new_count"] == 1
    stored = NewsStore(path).load()
    assert not isinstance(stored, str)
    assert [item.url for item in stored.items] == [new_item.url]
    assert stored.items[0].summary == "Rezumat"


def test_news_pull_watermark_keeps_at_most_500_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a first pull with more than 500 feed items.
    path = tmp_path / "news.json"
    config_path = tmp_path / "news-hermes.yaml"
    watermark_dir = tmp_path / "watermarks"
    _ = config_path.write_text(
        f"""
store_path: {path}
sources:
  rss:
    - name: vendor
      url: https://example.test/feed.xml
watermark_dir: {watermark_dir}
""",
        encoding="utf-8",
    )
    raw_items = tuple(
        RawNewsItem(
            f"Item {index}",
            f"https://example.test/{index}",
            "vendor",
            SourceType.RSS,
            None,
        )
        for index in range(600)
    )

    def fake_collect_items(*_args: object) -> tuple[RawNewsItem, ...]:
        return raw_items

    monkeypatch.setattr("news_hermes.tools.collect_items", fake_collect_items)

    # When: the pull establishes the baseline.
    payload = parse_result(news_pull({}, _news_path=str(path), _news_config=str(config_path)))

    # Then: no historical items are ingested and the watermark is bounded.
    assert payload["ok"] is True
    assert payload["new_count"] == 0
    watermark = parse_json_object(
        (watermark_dir / "news-hermes-watermark.json").read_text(encoding="utf-8")
    )
    assert watermark is not None
    sources = json_object(watermark["sources"])
    seen_urls = json_list(sources["rss:vendor"])
    assert len(seen_urls) == 500


def test_news_pull_baselines_new_source_after_existing_watermark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one source has already established a watermark.
    path = tmp_path / "news.json"
    config_path = tmp_path / "news-hermes.yaml"
    watermark_dir = tmp_path / "watermarks"
    _ = config_path.write_text(
        f"""
store_path: {path}
sources:
  rss:
    - name: vendor-a
      url: https://example.test/a.xml
watermark_dir: {watermark_dir}
""",
        encoding="utf-8",
    )
    first_source = (
        RawNewsItem("Old A", "https://example.test/a/old", "vendor-a", SourceType.RSS, None),
    )
    second_source = (
        RawNewsItem("Old B", "https://example.test/b/old", "vendor-b", SourceType.RSS, None),
    )
    pulls = [first_source, second_source]

    def fake_collect_items(*_args: object) -> tuple[RawNewsItem, ...]:
        return pulls.pop(0)

    monkeypatch.setattr("news_hermes.tools.collect_items", fake_collect_items)
    first = parse_result(news_pull({}, _news_path=str(path), _news_config=str(config_path)))
    _ = config_path.write_text(
        f"""
store_path: {path}
sources:
  rss:
    - name: vendor-a
      url: https://example.test/a.xml
    - name: vendor-b
      url: https://example.test/b.xml
watermark_dir: {watermark_dir}
""",
        encoding="utf-8",
    )

    # When: the newly configured source is pulled for the first time.
    second = parse_result(news_pull({}, _news_path=str(path), _news_config=str(config_path)))

    # Then: the new source is baselined instead of backfilled into the store.
    assert first["new_count"] == 0
    assert second["new_count"] == 0
    stored = NewsStore(path).load()
    assert not isinstance(stored, str)
    assert stored.items == ()
