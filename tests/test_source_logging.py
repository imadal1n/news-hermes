from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from news_hermes.models import NewsSource, SourceName, SourceType
from news_hermes.sources import collect_source, fetch_github_releases, fetch_rss

if TYPE_CHECKING:
    import pytest

    from news_hermes.json_types import JsonObject, JsonValue

RSS_SOURCE = NewsSource(SourceName("feed"), SourceType.RSS, "https://example.test/feed.xml", "")
GITHUB_SOURCE = NewsSource(SourceName("repo"), SourceType.GITHUB_RELEASES, "owner/repo", "")
SEARXNG_SOURCE = NewsSource(
    SourceName("search"),
    SourceType.SEARXNG,
    "https://search.example.test",
    "model release",
)


@dataclass(frozen=True, slots=True)
class FakeHttpClient:
    text: str | None

    def get_text(self, url: str) -> str | None:
        _ = url
        return self.text

    def post_json(self, url: str, payload: JsonObject) -> JsonValue | None:
        _ = url
        _ = payload
        return None


def test_fetch_rss_logs_http_error_when_response_is_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: an RSS source whose HTTP request fails.
    caplog.set_level(logging.WARNING, logger="news_hermes")

    # When: the source is fetched.
    items = fetch_rss(RSS_SOURCE, FakeHttpClient(None))

    # Then: the empty result is logged with the source name and reason.
    assert items == ()
    assert _warning_messages(caplog) == ["news-hermes source feed returned no items: HTTP error"]


def test_fetch_rss_logs_empty_feed_entries(caplog: pytest.LogCaptureFixture) -> None:
    # Given: an RSS source returns a valid feed with no entries.
    caplog.set_level(logging.WARNING, logger="news_hermes")

    # When: the source is fetched.
    items = fetch_rss(RSS_SOURCE, FakeHttpClient("<rss><channel /></rss>"))

    # Then: the empty feed is logged.
    assert items == ()
    assert _warning_messages(caplog) == [
        "news-hermes source feed returned no items: 0 feed entries"
    ]


def test_fetch_github_releases_logs_http_error_when_response_is_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: a GitHub releases source whose HTTP request fails.
    caplog.set_level(logging.WARNING, logger="news_hermes")

    # When: the source is fetched.
    items = fetch_github_releases(GITHUB_SOURCE, FakeHttpClient(None))

    # Then: the empty result is logged with the source name and reason.
    assert items == ()
    assert _warning_messages(caplog) == ["news-hermes source repo returned no items: HTTP error"]


def test_fetch_github_releases_logs_invalid_json_array(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: GitHub responds with JSON that is not a releases array.
    caplog.set_level(logging.WARNING, logger="news_hermes")

    # When: the source is fetched.
    items = fetch_github_releases(GITHUB_SOURCE, FakeHttpClient(json.dumps({"message": "bad"})))

    # Then: the invalid shape is logged.
    assert items == ()
    assert _warning_messages(caplog) == [
        "news-hermes source repo returned no items: invalid JSON array"
    ]


def test_collect_source_logs_searxng_http_error(caplog: pytest.LogCaptureFixture) -> None:
    # Given: a SearXNG source whose HTTP request fails.
    caplog.set_level(logging.WARNING, logger="news_hermes")

    # When: the source is collected.
    items = collect_source(SEARXNG_SOURCE, "day", FakeHttpClient(None))

    # Then: the empty result is logged with the source name and reason.
    assert items == ()
    assert _warning_messages(caplog) == ["news-hermes source search returned no items: HTTP error"]


def test_collect_source_logs_searxng_zero_results(caplog: pytest.LogCaptureFixture) -> None:
    # Given: SearXNG responds successfully with no results.
    caplog.set_level(logging.WARNING, logger="news_hermes")

    # When: the source is collected.
    items = collect_source(SEARXNG_SOURCE, "day", FakeHttpClient(json.dumps({"results": []})))

    # Then: the empty search result is logged.
    assert items == ()
    assert _warning_messages(caplog) == ["news-hermes source search returned no items: 0 results"]


def _warning_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.message for record in caplog.records]
