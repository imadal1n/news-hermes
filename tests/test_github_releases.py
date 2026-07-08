from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import news_hermes.sources as sources_module
from news_hermes.config import GitHubReleasesSource, load_config
from news_hermes.models import NewsSource, RawNewsItem, SourceName, SourceType, parse_source
from news_hermes.sources import fetch_github_releases
from news_hermes.tools import ToolArgs, collect_items, source_from_args

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from news_hermes.clients import HttpClient
    from news_hermes.json_types import JsonObject, JsonValue

RELEASES_URL = "https://api.github.com/repos/owner/repo/releases"

RELEASE_JSON = json.dumps(
    [
        {
            "name": "Release 1.0.0",
            "tag_name": "v1.0.0",
            "html_url": "https://github.com/owner/repo/releases/tag/v1.0.0",
            "published_at": "2026-07-08T10:00:00Z",
            "draft": False,
            "prerelease": False,
        },
        {
            "name": "",
            "tag_name": "v1.1.0-rc1",
            "html_url": "https://github.com/owner/repo/releases/tag/v1.1.0-rc1",
            "published_at": "2026-07-09T12:00:00Z",
            "draft": False,
            "prerelease": True,
        },
        {
            "name": "Draft release",
            "tag_name": "v2.0.0-draft",
            "html_url": "https://github.com/owner/repo/releases/tag/v2.0.0-draft",
            "published_at": "2026-07-10T14:00:00Z",
            "draft": True,
            "prerelease": False,
        },
    ]
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


@dataclass(frozen=True, slots=True)
class EndpointHttpClient:
    expected_url: str
    text: str | None

    def get_text(self, url: str) -> str | None:
        if url == self.expected_url:
            return self.text
        return None

    def post_json(self, url: str, payload: JsonObject) -> JsonValue | None:
        _ = url
        _ = payload
        return None


def github_source(url: str = RELEASES_URL) -> NewsSource:
    return NewsSource(SourceName("repo"), SourceType.GITHUB_RELEASES, url, "")


def test_fetch_github_releases_maps_json_to_raw_items() -> None:
    # Given: a GitHub releases API response with valid releases.
    source = github_source()
    client: HttpClient = FakeHttpClient(RELEASE_JSON)

    # When: releases are fetched.
    items = fetch_github_releases(source, client)

    # Then: non-draft releases are mapped to raw news items with name fallback to tag_name.
    assert len(items) == 2
    assert items[0].title == "Release 1.0.0"
    assert items[0].url == "https://github.com/owner/repo/releases/tag/v1.0.0"
    assert items[0].published_at == "2026-07-08T10:00:00Z"
    assert items[0].source == "repo"
    assert items[0].source_type == SourceType.GITHUB_RELEASES
    assert items[1].title == "v1.1.0-rc1"
    assert items[1].source == "repo"


def test_fetch_github_releases_expands_owner_repo_url() -> None:
    # Given: a stored GitHub releases source uses owner/repo shorthand.
    source = github_source("owner/repo")
    client: HttpClient = EndpointHttpClient(RELEASES_URL, RELEASE_JSON)

    # When: releases are fetched.
    items = fetch_github_releases(source, client)

    # Then: the shorthand is expanded to the GitHub releases API URL.
    assert len(items) == 2


def test_fetch_github_releases_skips_drafts() -> None:
    # Given: a release marked as draft.
    source = github_source()
    client: HttpClient = FakeHttpClient(
        json.dumps(
            [
                {
                    "name": "Draft",
                    "tag_name": "draft",
                    "html_url": "https://example.test/draft",
                    "published_at": "2026-07-08T10:00:00Z",
                    "draft": True,
                    "prerelease": False,
                }
            ]
        )
    )

    # When: releases are fetched.
    items = fetch_github_releases(source, client)

    # Then: draft releases are skipped.
    assert items == ()


def test_fetch_github_releases_includes_prereleases() -> None:
    # Given: a prerelease that is not draft.
    source = github_source()
    client: HttpClient = FakeHttpClient(
        json.dumps(
            [
                {
                    "name": "",
                    "tag_name": "v1.0.0-beta",
                    "html_url": "https://example.test/beta",
                    "published_at": "2026-07-08T10:00:00Z",
                    "draft": False,
                    "prerelease": True,
                }
            ]
        )
    )

    # When: releases are fetched.
    items = fetch_github_releases(source, client)

    # Then: prereleases are included when title and url are valid.
    assert len(items) == 1
    assert items[0].title == "v1.0.0-beta"
    assert items[0].url == "https://example.test/beta"


def test_fetch_github_releases_skips_malformed_releases() -> None:
    # Given: releases with missing or non-string title/url and a non-object entry.
    source = github_source()
    client: HttpClient = FakeHttpClient(
        json.dumps(
            [
                {
                    "name": "Only name",
                    "tag_name": "v1",
                    "published_at": "2026-07-08T10:00:00Z",
                    "draft": False,
                    "prerelease": False,
                },
                "not an object",
                {
                    "name": "Bad URL",
                    "tag_name": "v2",
                    "html_url": 123,
                    "published_at": "2026-07-08T10:00:00Z",
                    "draft": False,
                    "prerelease": False,
                },
            ]
        )
    )

    # When: releases are fetched.
    items = fetch_github_releases(source, client)

    # Then: malformed releases are skipped.
    assert items == ()


def test_fetch_github_releases_stops_at_limit() -> None:
    # Given: more releases than the requested limit.
    source = github_source()
    releases = [
        {
            "name": f"Release {index}",
            "tag_name": f"v{index}",
            "html_url": f"https://example.test/{index}",
            "published_at": "2026-07-08T10:00:00Z",
            "draft": False,
            "prerelease": False,
        }
        for index in range(5)
    ]
    client: HttpClient = FakeHttpClient(json.dumps(releases))

    # When: releases are fetched with a limit.
    items = fetch_github_releases(source, client, limit=2)

    # Then: only the first limited releases are returned.
    assert [item.url for item in items] == [
        "https://example.test/0",
        "https://example.test/1",
    ]


def test_fetch_github_releases_returns_empty_when_none() -> None:
    # Given: a client that returns None.
    source = github_source()
    client: HttpClient = FakeHttpClient(None)

    # When: releases are fetched.
    items = fetch_github_releases(source, client)

    # Then: an empty tuple is returned.
    assert items == ()


def test_fetch_github_releases_returns_empty_for_non_array() -> None:
    # Given: a response that is not a JSON array.
    source = github_source()
    client: HttpClient = FakeHttpClient(json.dumps({"message": "Not Found"}))

    # When: releases are fetched.
    items = fetch_github_releases(source, client)

    # Then: an empty tuple is returned.
    assert items == ()


def test_parse_source_accepts_github_releases() -> None:
    # Given: a GitHub releases source object.
    value: JsonObject = {
        "name": "repo",
        "type": "github_releases",
        "url": RELEASES_URL,
        "query": "",
    }

    # When: it is parsed.
    result = parse_source(value)

    # Then: a valid NewsSource is returned.
    assert isinstance(result, NewsSource)
    assert result.type == SourceType.GITHUB_RELEASES
    assert result.url == RELEASES_URL
    assert result.query == ""


def test_source_from_args_accepts_github_releases() -> None:
    # Given: tool arguments for a GitHub releases source.
    args: ToolArgs = {"name": "repo", "type": "github_releases", "url": RELEASES_URL}

    # When: the arguments are parsed.
    result = source_from_args(args)

    # Then: a valid source is returned without requiring a query.
    assert isinstance(result, NewsSource)
    assert result.type == SourceType.GITHUB_RELEASES
    assert result.query == ""


def test_config_seeds_github_releases_from_repo(tmp_path: Path) -> None:
    # Given: a YAML config with a github_releases source specified as owner/repo.
    config_path = tmp_path / "news-hermes.yaml"
    _ = config_path.write_text(
        """
sources:
  github_releases:
    - name: repo
      repo: owner/repo
""",
        encoding="utf-8",
    )

    # When: config is loaded.
    config = load_config({"_news_config": str(config_path)})

    # Then: the source is seeded with the API URL derived from the repo.
    assert not isinstance(config, str)
    assert len(config.github_releases) == 1
    assert config.github_releases[0] == GitHubReleasesSource(name="repo", repo="owner/repo", url="")
    sources = config.seed_sources()
    assert len(sources) == 1
    assert sources[0].type == SourceType.GITHUB_RELEASES
    assert sources[0].url == RELEASES_URL
    assert sources[0].query == ""


def test_config_seeds_github_releases_from_explicit_url(tmp_path: Path) -> None:
    # Given: a YAML config with a github_releases source that already provides a URL.
    config_path = tmp_path / "news-hermes.yaml"
    _ = config_path.write_text(
        """
sources:
  github_releases:
    - name: repo
      url: https://api.github.com/repos/owner/repo/releases
""",
        encoding="utf-8",
    )

    # When: config is loaded.
    config = load_config({"_news_config": str(config_path)})

    # Then: the explicit URL is preserved.
    assert not isinstance(config, str)
    assert config.github_releases[0].url == RELEASES_URL
    sources = config.seed_sources()
    assert sources[0].url == RELEASES_URL


def test_collect_items_dispatches_github_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a GitHub releases source configured in the store.
    captured: list[tuple[str, int | None]] = []

    def fake_fetch(
        source: NewsSource, _client: HttpClient, *, limit: int | None = None
    ) -> tuple[RawNewsItem, ...]:
        captured.append((source.name, limit))
        return ()

    monkeypatch.setattr(sources_module, "fetch_github_releases", fake_fetch)
    source = github_source()

    class FakeClient:
        def get_text(self, url: str) -> str | None:
            _ = url
            return None

        def post_json(self, url: str, payload: JsonObject) -> JsonValue | None:
            _ = url
            _ = payload
            return None

    # When: items are collected.
    _ = collect_items((source,), "day", FakeClient(), max_items_per_source=5)

    # Then: the GitHub releases fetcher is invoked with the source limit.
    assert captured == [("repo", 5)]
