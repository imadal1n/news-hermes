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


def test_triage_failure_keeps_raw_items_by_returning_no_summaries() -> None:
    # Given: Ollama returns malformed content.
    item = RawNewsItem("Release", "https://e.test", "vendor", SourceType.RSS, None)
    config = TriageConfig("http://localhost:11434", "ornith:35b", 0.3, "ro", 5)
    client = FakeHttpClient("", {"message": {"content": "not-json"}})

    # When: triage runs.
    summaries = triage_items((item,), config, client)

    # Then: caller can still store raw items with empty summaries.
    assert summaries.summaries == {}
    assert summaries.error == "ollama response.message.content was not a JSON array"


def test_triage_parses_json_array_from_markdown_fenced_content() -> None:
    # Given: Ollama wraps the requested JSON array in a markdown code fence.
    item = RawNewsItem("Release", "https://e.test", "vendor", SourceType.RSS, None)
    config = TriageConfig("http://localhost:11434", "ornith:35b", 0.3, "ro", 5)
    response: JsonObject = {
        "message": {
            "content": '```json\n[{"url":"https://e.test","summary":"Rezumat"}]\n```',
        },
    }
    client = FakeHttpClient("", response)

    # When: triage runs.
    summaries = triage_items((item,), config, client)

    # Then: the summary is recovered instead of being silently dropped.
    assert summaries.summaries == {"https://e.test": "Rezumat"}
    assert summaries.error is None


def test_triage_uses_custom_system_prompt_with_language_placeholder() -> None:
    # Given: triage config includes a user-specific system prompt template.
    item = RawNewsItem("Release", "https://e.test", "vendor", SourceType.RSS, None)
    config = TriageConfig(
        "http://localhost:11434",
        "ornith:35b",
        0.3,
        "ro",
        5,
        "Keep Hermes and OpenClaw news. Summarize in {language}.",
    )
    response: JsonObject = {"message": {"content": "[]"}}
    client = FakeHttpClient(
        "",
        response,
        expected_system_prompt="Keep Hermes and OpenClaw news. Summarize in ro.",
    )

    # When: triage runs.
    summaries = triage_items((item,), config, client)

    # Then: the configured prompt is sent to Ollama.
    assert summaries.error is None
