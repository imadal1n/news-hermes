from __future__ import annotations

from typing import TYPE_CHECKING

from news_hermes.clients import TriageResult
from news_hermes.json_types import JsonObject, JsonValue, parse_json_object
from news_hermes.models import NewsSource, RawNewsItem, SourceName, SourceType
from news_hermes.storage import NewsStore
from news_hermes.tools import collect_items, news_pull, news_source_add

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def parse_result(result: str) -> JsonObject:
    value = parse_json_object(result)
    assert value is not None
    return value


def test_news_pull_applies_source_limit_before_triage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one configured source returns more fresh items than the source limit.
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
triage:
  max_items_per_source: 5
watermark_dir: {watermark_dir}
""",
        encoding="utf-8",
    )
    first_pull = tuple(
        RawNewsItem(
            f"Old {index}", f"https://example.test/old-{index}", "vendor", SourceType.RSS, None
        )
        for index in range(5)
    )
    second_pull = tuple(
        RawNewsItem(
            f"New {index}", f"https://example.test/new-{index}", "vendor", SourceType.RSS, None
        )
        for index in range(12)
    )
    pulls = [first_pull, second_pull]
    triaged_counts: list[int] = []

    def fake_collect_items(*_args: object, **_kwargs: object) -> tuple[RawNewsItem, ...]:
        return pulls.pop(0)

    def fake_triage_items(
        items: tuple[RawNewsItem, ...],
        *_args: object,
    ) -> TriageResult:
        triaged_counts.append(len(items))
        return TriageResult({item.url: "Summary" for item in items})

    monkeypatch.setattr("news_hermes.tools.collect_items", fake_collect_items)
    monkeypatch.setattr("news_hermes.tools.triage_items", fake_triage_items)

    # When: the second pull sees twelve fresh URLs from one source.
    _ = news_pull({}, _news_path=str(path), _news_config=str(config_path))
    second = parse_result(news_pull({}, _news_path=str(path), _news_config=str(config_path)))

    # Then: only the per-source limit reaches triage and ingest.
    assert second["new_count"] == 5
    assert triaged_counts[-1] == 5
    stored = NewsStore(path).load()
    assert not isinstance(stored, str)
    assert len(stored.items) == 5


def test_collect_items_passes_source_limit_to_fetchers(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: RSS and SearXNG sources are collected together.
    sources = (
        NewsSource(SourceName("vendor"), SourceType.RSS, "https://example.test/feed.xml", ""),
        NewsSource(SourceName("search"), SourceType.SEARXNG, "http://localhost:8888", "AI"),
    )
    calls: list[tuple[str, int | None]] = []

    def fake_fetch_rss(*_args: object, limit: int | None = None) -> tuple[RawNewsItem, ...]:
        calls.append(("rss", limit))
        return ()

    def fake_search_searxng(*_args: object, limit: int | None = None) -> tuple[RawNewsItem, ...]:
        calls.append(("searxng", limit))
        return ()

    class FakeClient:
        def get_text(self, url: str) -> str | None:
            _ = url
            return None

        def post_json(self, url: str, payload: JsonObject) -> JsonValue | None:
            _ = url
            _ = payload
            return None

    monkeypatch.setattr("news_hermes.sources.fetch_rss", fake_fetch_rss)
    monkeypatch.setattr("news_hermes.sources.search_searxng", fake_search_searxng)

    # When: items are collected with a source limit.
    _ = collect_items(sources, "day", FakeClient(), max_items_per_source=5)

    # Then: each fetcher receives the same per-source cap.
    assert calls == [("rss", 5), ("searxng", 5)]


def test_news_pull_collects_stored_searxng_sources_when_config_queries_are_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a SearXNG source was added to the persistent store and config has no queries.
    path = tmp_path / "news.json"
    config_path = tmp_path / "news-hermes.yaml"
    watermark_dir = tmp_path / "watermarks"
    _ = config_path.write_text(
        f"""
store_path: {path}
sources:
  searxng:
    queries: []
triage:
  max_items_per_source: 5
watermark_dir: {watermark_dir}
""",
        encoding="utf-8",
    )
    _ = news_source_add(
        {"name": "search", "type": "searxng", "url": "http://localhost:8888", "query": "AI"},
        _news_path=str(path),
    )
    collected_sources: list[tuple[NewsSource, ...]] = []
    collected_limits: list[int | None] = []

    def fake_collect_items(
        sources: tuple[NewsSource, ...],
        *_args: object,
        max_items_per_source: int | None = None,
    ) -> tuple[RawNewsItem, ...]:
        collected_sources.append(sources)
        collected_limits.append(max_items_per_source)
        return ()

    monkeypatch.setattr("news_hermes.tools.collect_items", fake_collect_items)

    # When: news is pulled from an existing store.
    payload = parse_result(news_pull({}, _news_path=str(path), _news_config=str(config_path)))

    # Then: the stored SearXNG source, not the empty config query list, drives collection.
    assert payload["ok"] is True
    assert collected_limits == [5]
    assert [(source.name, source.type, source.query) for source in collected_sources[0]] == [
        ("search", SourceType.SEARXNG, "AI")
    ]
