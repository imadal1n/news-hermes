from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from .clients import HttpClient, UrlLibHttpClient, fetch_rss, search_searxng, triage_items
from .config import SearxngConfig, load_config
from .json_types import JsonObject, JsonScalar, JsonValue
from .models import NewsId, NewsItem, NewsSource, NewsStatus, RawNewsItem, SourceName, SourceType
from .storage import NewsStore, RetentionPolicy
from .watermark import WatermarkStore

if TYPE_CHECKING:
    from .config import NewsConfig

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
    return _run_news_pull(config, args, kwargs)


def _run_news_pull(config: NewsConfig, args: ToolArgs, kwargs: ToolArgs) -> str:
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
    known_urls = {item.url for item in seeded.items}
    raw_items = limit_items_per_source(
        collect_items(
            seeded.sources,
            config.searxng.time_range,
            client,
            max_items_per_source=config.triage.max_items_per_source,
        ),
        config.triage.max_items_per_source,
    )
    fresh_items = WatermarkStore.from_dir(config.watermark_dir).fresh_items(
        raw_items,
        frozenset(known_urls),
    )
    if isinstance(fresh_items, str):
        return error_json("watermark_error", fresh_items)
    summaries = triage_items(fresh_items, config.triage, client)
    ingested = store.ingest(fresh_items, summaries.summaries)
    if isinstance(ingested, str):
        return error_json("storage_error", ingested)
    new_items = tuple(
        item for item in ingested.items if item.url in {raw.url for raw in fresh_items}
    )
    if args.get("silent") is False:
        payload = _pull_payload(len(new_items), summaries.error)
        items: JsonValue = [item.to_json() for item in new_items]
        payload["items"] = items
        return success_json(payload)
    return success_json(_pull_payload(len(new_items), summaries.error))


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
    *,
    max_items_per_source: int | None = None,
) -> tuple[RawNewsItem, ...]:
    items: list[RawNewsItem] = []
    for source in sources:
        match source.type:
            case SourceType.RSS:
                items.extend(fetch_rss(source, client, limit=max_items_per_source))
            case SourceType.SEARXNG:
                searxng = SearxngConfig(
                    endpoint=source.url,
                    time_range=time_range,
                    queries=(source.query,),
                )
                items.extend(search_searxng(searxng, client, limit=max_items_per_source))
    return tuple(items)


def limit_items_per_source(
    items: tuple[RawNewsItem, ...],
    max_items_per_source: int,
) -> tuple[RawNewsItem, ...]:
    counts: dict[tuple[SourceType, str], int] = {}
    limited: list[RawNewsItem] = []
    for item in items:
        key = (item.source_type, item.source)
        count = counts.get(key, 0)
        if count >= max_items_per_source:
            continue
        counts[key] = count + 1
        limited.append(item)
    return tuple(limited)


def _pull_payload(new_count: int, triage_error: str | None) -> ToolResult:
    payload: ToolResult = {"new_count": new_count}
    if triage_error is not None:
        payload["triage_error"] = triage_error
    return payload


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
