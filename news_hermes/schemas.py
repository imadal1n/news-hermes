from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .json_types import JsonObject

STATUS_DESCRIPTION: Final[str] = "News status: new, bookmarked, dismissed, or all."

NEWS_LIST: Final[JsonObject] = {
    "name": "news_list",
    "description": "List stored news items, optionally filtered by status.",
    "parameters": {
        "type": "object",
        "properties": {"status": {"type": "string", "description": STATUS_DESCRIPTION}},
    },
}

NEWS_BOOKMARK: Final[JsonObject] = {
    "name": "news_bookmark",
    "description": "Mark a news item as bookmarked.",
    "parameters": {
        "type": "object",
        "properties": {"id": {"type": "string", "description": "News item id."}},
        "required": ["id"],
    },
}

NEWS_DISMISS: Final[JsonObject] = {
    "name": "news_dismiss",
    "description": "Mark a news item as dismissed.",
    "parameters": {
        "type": "object",
        "properties": {"id": {"type": "string", "description": "News item id."}},
        "required": ["id"],
    },
}

NEWS_PULL: Final[JsonObject] = {
    "name": "news_pull",
    "description": (
        "Fetch configured sources, triage new items, and ingest them into the JSON store."
    ),
    "parameters": {
        "type": "object",
        "properties": {"silent": {"type": "boolean", "description": "Return only counts."}},
    },
}

NEWS_CLEAR: Final[JsonObject] = {
    "name": "news_clear",
    "description": "Remove dismissed, expired, or all stored news items.",
    "parameters": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "dismissed, expired, or all."},
            "confirm": {"type": "boolean", "description": "Required when target is all."},
        },
        "required": ["target"],
    },
}

NEWS_SOURCE_ADD: Final[JsonObject] = {
    "name": "news_source_add",
    "description": "Add or replace a stored news source.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Source name."},
            "url": {
                "type": "string",
                "description": "RSS feed URL, SearXNG endpoint, or GitHub releases API URL.",
            },
            "type": {"type": "string", "description": "rss, searxng, or github_releases."},
            "query": {"type": "string", "description": "Required for searxng sources."},
        },
        "required": ["name", "url", "type"],
    },
}

NEWS_SOURCE_REMOVE: Final[JsonObject] = {
    "name": "news_source_remove",
    "description": "Remove a stored news source by name.",
    "parameters": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Source name."}},
        "required": ["name"],
    },
}

NEWS_SOURCE_LIST: Final[JsonObject] = {
    "name": "news_source_list",
    "description": "List stored news sources.",
    "parameters": {"type": "object", "properties": {}},
}

TOOL_SCHEMAS: Final[dict[str, JsonObject]] = {
    "news_list": NEWS_LIST,
    "news_bookmark": NEWS_BOOKMARK,
    "news_dismiss": NEWS_DISMISS,
    "news_pull": NEWS_PULL,
    "news_clear": NEWS_CLEAR,
    "news_source_add": NEWS_SOURCE_ADD,
    "news_source_remove": NEWS_SOURCE_REMOVE,
    "news_source_list": NEWS_SOURCE_LIST,
}
