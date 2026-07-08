from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, TypeAlias

from .clients import HttpClient, UrlLibHttpClient, fetch_rss, search_searxng, triage_items
from .config import SearxngConfig, load_config
from .json_types import JsonObject, JsonScalar, JsonValue
from .models import NewsId, NewsItem, NewsSource, NewsStatus, RawNewsItem, SourceName, SourceType
from .storage import NewsStore, RetentionPolicy

ToolArgs: TypeAlias = dict[str, JsonScalar]
ToolResult: TypeAlias = dict[str, JsonValue]
Invalid: TypeAlias = Literal["invalid"]
PATH_KWARG: Final[str] = "_news_path"
CONFIG_KWARG: Final[str] = "_news_config"


@dataclass(frozen=True, slots=True)
class SourceArgs:
    name: str
    raw_type: str
    url: str
    query: str


def news_list(args: ToolArgs, **kwargs: JsonScalar) -> str:
    status = parse_list_status(args.get("status"))
    if status == "invalid":
        return error_json("invalid_status", "status must be new, bookmarked, dismissed, or all")
    result = store_from_kwargs(kwargs).list(status)
    if isinstance(result, str):
        return error_json("storage_error", result)
    return success_json({"items": [item.to_json() for item in result.items]})


def news_bookmark(args: ToolArgs, **kwargs: JsonScalar) -> str:
    item_id = required_string(args, "id")
    if item_id is None:
        return error_json("missing_id", "id is required")
    return item_result_json(store_from_kwargs(kwargs).bookmark(NewsId(item_id)))


def news_dismiss(args: ToolArgs, **kwargs: JsonScalar) -> str:
    item_id = required_string(args, "id")
    if item_id is None:
        return error_json("missing_id", "id is required")
    return item_result_json(store_from_kwargs(kwargs).dismiss(NewsId(item_id)))


def news_clear(args: ToolArgs, **kwargs: JsonScalar) -> str:
    target = required_string(args, "target")
    if target is None:
        return error_json("missing_target", "target is required")
    config = load_config(config_kwargs(kwargs))
    if isinstance(config, str):
        return error_json("config_error", config)
    confirm = args.get("confirm") is True
    result = store_from_kwargs(kwargs).clear(
        target,
        confirm=confirm,
        policy=RetentionPolicy(config.retention.dismissed_days, config.retention.new_days),
    )
    if isinstance(result, str):
        return error_json("clear_error", result)
    return success_json({"removed_count": 0, "items": [item.to_json() for item in result.items]})


def news_pull(args: ToolArgs, **kwargs: JsonScalar) -> str:
    config = load_config(config_kwargs(kwargs))
    if isinstance(config, str):
        return error_json("config_error", config)
    store = store_from_kwargs(kwargs)
    purge = store.purge_expired(
        RetentionPolicy(config.retention.dismissed_days, config.retention.new_days)
    )
    if isinstance(purge, str):
        return error_json("storage_error", purge)
    seeded = store.seed_sources(config.seed_sources())
    if isinstance(seeded, str):
        return error_json("storage_error", seeded)
    client = UrlLibHttpClient()
    raw_items = collect_items(seeded.sources, config.searxng.time_range, client)
    known_urls = {item.url for item in seeded.items}
    fresh_items = tuple(item for item in raw_items if item.url not in known_urls)
    summaries = triage_items(fresh_items, config.triage, client)
    ingested = store.ingest(fresh_items, summaries)
    if isinstance(ingested, str):
        return error_json("storage_error", ingested)
    new_items = tuple(
        item for item in ingested.items if item.url in {raw.url for raw in fresh_items}
    )
    if args.get("silent") is False:
        return success_json(
            {"new_count": len(new_items), "items": [item.to_json() for item in new_items]}
        )
    return success_json({"new_count": len(new_items)})


def news_source_add(args: ToolArgs, **kwargs: JsonScalar) -> str:
    source = source_from_args(args)
    if isinstance(source, str):
        return error_json("invalid_source", source)
    result = store_from_kwargs(kwargs).add_source(source)
    if isinstance(result, str):
        return error_json("storage_error", result)
    return success_json({"sources": [item.to_json() for item in result.sources]})


def news_source_remove(args: ToolArgs, **kwargs: JsonScalar) -> str:
    name = required_string(args, "name")
    if name is None:
        return error_json("missing_name", "name is required")
    result = store_from_kwargs(kwargs).remove_source(SourceName(name))
    if isinstance(result, str):
        return error_json("storage_error", result)
    return success_json({"sources": [item.to_json() for item in result.sources]})


def news_source_list(args: ToolArgs, **kwargs: JsonScalar) -> str:
    _ = args
    result = store_from_kwargs(kwargs).load()
    if isinstance(result, str):
        return error_json("storage_error", result)
    return success_json({"sources": [item.to_json() for item in result.sources]})


def collect_items(
    sources: tuple[NewsSource, ...],
    time_range: str,
    client: HttpClient,
) -> tuple[RawNewsItem, ...]:
    items: list[RawNewsItem] = []
    for source in sources:
        match source.type:
            case SourceType.RSS:
                items.extend(fetch_rss(source, client))
            case SourceType.SEARXNG:
                searxng = SearxngConfig(
                    endpoint=source.url,
                    time_range=time_range,
                    queries=(source.query,),
                )
                items.extend(search_searxng(searxng, client))
    return tuple(items)


def source_from_args(args: ToolArgs) -> NewsSource | str:
    parsed = source_args(args)
    if isinstance(parsed, str):
        return parsed
    try:
        source_type = SourceType(parsed.raw_type)
    except ValueError:
        return "type must be rss or searxng"
    if source_type == SourceType.SEARXNG and not parsed.query.strip():
        return "query is required for searxng sources"
    return NewsSource(
        name=SourceName(parsed.name),
        type=source_type,
        url=parsed.url,
        query=parsed.query.strip(),
    )


def store_from_kwargs(kwargs: ToolArgs) -> NewsStore:
    path = kwargs.get(PATH_KWARG)
    if isinstance(path, str) and path:
        return NewsStore(Path(path))
    return NewsStore()


def config_kwargs(kwargs: ToolArgs) -> JsonObject:
    value = kwargs.get(CONFIG_KWARG)
    if isinstance(value, str) and value:
        return {CONFIG_KWARG: value}
    return {}


def source_args(args: ToolArgs) -> SourceArgs | str:
    name = required_string(args, "name")
    raw_type = required_string(args, "type")
    url = required_string(args, "url")
    query = optional_string(args, "query")
    if name is None:
        return "name is required"
    if raw_type is None:
        return "type is required"
    if url is None:
        return "url is required"
    if query is None:
        return "query must be a string when provided"
    return SourceArgs(name=name, raw_type=raw_type, url=url, query=query)


def required_string(args: ToolArgs, key: str) -> str | None:
    value = args.get(key)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped


def optional_string(args: ToolArgs, key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    return value


def parse_list_status(value: JsonScalar) -> NewsStatus | None | Invalid:
    if value is None:
        return NewsStatus.NEW
    if value == "all":
        return None
    if not isinstance(value, str):
        return "invalid"
    try:
        return NewsStatus(value)
    except ValueError:
        return "invalid"


def item_result_json(result: NewsItem | str) -> str:
    if isinstance(result, str):
        code = "not_found" if result == "news item not found" else "storage_error"
        return error_json(code, result)
    return success_json({"item": result.to_json()})


def success_json(payload: ToolResult) -> str:
    return json.dumps({"ok": True, **payload}, separators=(",", ":"))


def error_json(code: str, message: str) -> str:
    return json.dumps(
        {"ok": False, "error": {"code": code, "message": message}},
        separators=(",", ":"),
    )
