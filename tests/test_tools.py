from __future__ import annotations

from typing import TYPE_CHECKING

from news_hermes.json_types import JsonObject, JsonValue, parse_json_object
from news_hermes.models import RawNewsItem, SourceType
from news_hermes.storage import NewsStore
from news_hermes.tools import (
    news_bookmark,
    news_clear,
    news_dismiss,
    news_list,
    news_source_add,
    news_source_list,
    news_source_remove,
)

if TYPE_CHECKING:
    from pathlib import Path


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
