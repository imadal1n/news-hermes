from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from news_hermes.clients import search_searxng, triage_items
from news_hermes.config import SearxngConfig, TriageConfig
from news_hermes.models import RawNewsItem, SourceType

if TYPE_CHECKING:
    from news_hermes.json_types import JsonObject, JsonValue


@dataclass(frozen=True, slots=True)
class FakeHttpClient:
    text: str
    response: JsonValue | None = None
    expected_system_prompt: str | None = None

    def get_text(self, url: str) -> str | None:
        assert "format=json" in url
        return self.text

    def post_json(self, url: str, payload: JsonObject) -> JsonValue | None:
        assert url.endswith("/api/chat")
        assert payload["model"] == "ornith:35b"
        if self.expected_system_prompt is not None:
            messages = payload["messages"]
            assert isinstance(messages, list)
            system_message = messages[0]
            assert isinstance(system_message, dict)
            assert system_message["content"] == self.expected_system_prompt
        return self.response


@dataclass(frozen=True, slots=True)
class RecordingHttpClient:
    responses: tuple[JsonValue | None, ...]
    payloads: list[JsonObject]

    def get_text(self, url: str) -> str | None:
        assert "format=json" in url
        return ""

    def post_json(self, url: str, payload: JsonObject) -> JsonValue | None:
        assert url.endswith("/api/chat")
        self.payloads.append(payload)
        return self.responses[len(self.payloads) - 1]


def test_search_searxng_normalizes_results() -> None:
    # Given: a fake SearXNG JSON result.
    client = FakeHttpClient(
        json.dumps({"results": [{"title": "Release", "url": "https://e.test"}]})
    )
    config = SearxngConfig("http://localhost:8888", "day", ("model release",))

    # When: search runs.
    items = search_searxng(config, client)

    # Then: results become raw news items.
    assert len(items) == 1
    assert items[0].source_type == SourceType.SEARXNG
    assert items[0].source == "model release"


def test_search_searxng_stops_after_limit() -> None:
    # Given: SearXNG returns more results than the source limit.
    results = [
        {"title": f"Release {index}", "url": f"https://e.test/{index}"} for index in range(8)
    ]
    client = FakeHttpClient(json.dumps({"results": results}))
    config = SearxngConfig("http://localhost:8888", "day", ("model release",))

    # When: search runs with a limit.
    items = search_searxng(config, client, limit=5)

    # Then: only the first limited results are materialized.
    assert [item.url for item in items] == [
        "https://e.test/0",
        "https://e.test/1",
        "https://e.test/2",
        "https://e.test/3",
        "https://e.test/4",
    ]


def test_triage_failure_keeps_raw_items_by_returning_no_summaries() -> None:
    # Given: Ollama returns malformed content.
    item = RawNewsItem("Release", "https://e.test", "vendor", SourceType.RSS, None)
    config = TriageConfig("http://localhost:11434", "ornith:35b", 0.3, "en", 5)
    client = FakeHttpClient("", {"message": {"content": "not-json"}})

    # When: triage runs.
    summaries = triage_items((item,), config, client)

    # Then: caller can still store raw items with empty summaries.
    assert summaries.summaries == {}
    assert summaries.error == "ollama response.message.content was not a JSON array"


def test_triage_parses_json_array_from_markdown_fenced_content() -> None:
    # Given: Ollama wraps the requested JSON array in a markdown code fence.
    item = RawNewsItem("Release", "https://e.test", "vendor", SourceType.RSS, None)
    config = TriageConfig("http://localhost:11434", "ornith:35b", 0.3, "en", 5)
    response: JsonObject = {
        "message": {
            "content": '```json\n[{"url":"https://e.test","summary":"Summary"}]\n```',
        },
    }
    client = FakeHttpClient("", response)

    # When: triage runs.
    summaries = triage_items((item,), config, client)

    # Then: the summary is recovered instead of being silently dropped.
    assert summaries.summaries == {"https://e.test": "Summary"}
    assert summaries.error is None


def test_triage_parses_json_array_from_inline_markdown_fence() -> None:
    # Given: Ollama returns fenced JSON on one line.
    item = RawNewsItem("Release", "https://e.test", "vendor", SourceType.RSS, None)
    config = TriageConfig("http://localhost:11434", "ornith:35b", 0.3, "en", 5)
    response: JsonObject = {
        "message": {"content": '```json [{"url":"https://e.test","summary":"Summary"}] ```'},
    }
    client = FakeHttpClient("", response)

    # When: triage runs.
    summaries = triage_items((item,), config, client)

    # Then: the inline fenced JSON is parsed.
    assert summaries.summaries == {"https://e.test": "Summary"}
    assert summaries.error is None


def test_triage_parses_json_array_from_fence_surrounded_by_text() -> None:
    # Given: Ollama adds prose around the fenced JSON payload.
    item = RawNewsItem("Release", "https://e.test", "vendor", SourceType.RSS, None)
    config = TriageConfig("http://localhost:11434", "ornith:35b", 0.3, "en", 5)
    response: JsonObject = {
        "message": {
            "content": 'Here is the JSON:\n```json\n[{"url":"https://e.test","summary":"Summary"}]\n```\nDone.',
        },
    }
    client = FakeHttpClient("", response)

    # When: triage runs.
    summaries = triage_items((item,), config, client)

    # Then: the fenced JSON is extracted from the surrounding prose.
    assert summaries.summaries == {"https://e.test": "Summary"}
    assert summaries.error is None


def test_triage_uses_custom_system_prompt_with_language_placeholder() -> None:
    # Given: triage config includes a user-specific system prompt template.
    item = RawNewsItem("Release", "https://e.test", "vendor", SourceType.RSS, None)
    config = TriageConfig(
        "http://localhost:11434",
        "ornith:35b",
        0.3,
        "en",
        5,
        "Keep Hermes and OpenClaw news. Summarize in {language}.",
    )
    response: JsonObject = {"message": {"content": "[]"}}
    expected_prompt = (
        "Keep Hermes and OpenClaw news. Summarize in en. "
        'Output JSON only: [{"title":"...","url":"...","summary":"..."}]. '
        "If nothing is relevant, output []."
    )
    client = FakeHttpClient("", response, expected_system_prompt=expected_prompt)

    # When: triage runs.
    summaries = triage_items((item,), config, client)

    # Then: the configured prompt is sent to Ollama.
    assert summaries.error is None


def test_triage_batches_items_and_merges_summaries() -> None:
    # Given: more raw items than one safe Ollama triage batch.
    items = tuple(
        RawNewsItem(f"Release {index}", f"https://e.test/{index}", "vendor", SourceType.RSS, None)
        for index in range(55)
    )
    config = TriageConfig("http://localhost:11434", "ornith:35b", 0.3, "en", 5)
    responses = tuple(
        _triage_response(range(start, min(start + 25, len(items)))) for start in (0, 25, 50)
    )
    payloads: list[JsonObject] = []
    client = RecordingHttpClient(responses, payloads)

    # When: triage runs.
    summaries = triage_items(items, config, client)

    # Then: requests are chunked and all batch summaries are merged.
    assert [len(_user_items(payload)) for payload in payloads] == [25, 25, 5]
    assert summaries.error is None
    assert summaries.summaries["https://e.test/0"] == "Summary 0"
    assert summaries.summaries["https://e.test/54"] == "Summary 54"
    assert len(summaries.summaries) == 55


def test_triage_batch_failure_returns_no_partial_summaries() -> None:
    # Given: the second Ollama batch returns malformed content.
    items = tuple(
        RawNewsItem(f"Release {index}", f"https://e.test/{index}", "vendor", SourceType.RSS, None)
        for index in range(30)
    )
    config = TriageConfig("http://localhost:11434", "ornith:35b", 0.3, "en", 5)
    payloads: list[JsonObject] = []
    client = RecordingHttpClient(
        (_triage_response(range(25)), {"message": {"content": "nope"}}),
        payloads,
    )

    # When: triage runs.
    summaries = triage_items(items, config, client)

    # Then: the caller gets the existing fallback signal without partial results.
    assert [len(_user_items(payload)) for payload in payloads] == [25, 5]
    assert summaries.summaries == {}
    assert summaries.error == "ollama response.message.content was not a JSON array"


def _triage_response(indexes: range) -> JsonObject:
    return {
        "message": {
            "content": json.dumps(
                [
                    {"url": f"https://e.test/{index}", "summary": f"Summary {index}"}
                    for index in indexes
                ],
            ),
        },
    }


def _user_items(payload: JsonObject) -> list[JsonValue]:
    messages = payload["messages"]
    assert isinstance(messages, list)
    user_message = messages[1]
    assert isinstance(user_message, dict)
    content = user_message["content"]
    assert isinstance(content, str)
    parsed = json.loads(content)
    assert isinstance(parsed, list)
    return parsed
