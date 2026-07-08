from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

from .json_types import JsonObject, JsonValue, json_value
from .models import NewsSource, SourceName, SourceType

DEFAULT_CONFIG_PATH: Final[Path] = Path("/opt/data/workspace/news-hermes.yaml")
DEFAULT_STORE_PATH: Final[Path] = Path("/opt/data/workspace/news.json")
DEFAULT_WATERMARK_DIR: Final[Path] = Path("/opt/data/watcher-state")
CONFIG_ENV: Final[str] = "NEWS_HERMES_CONFIG"
CONFIG_KWARG: Final[str] = "_news_config"


@dataclass(frozen=True, slots=True)
class RssSource:
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class SearxngConfig:
    endpoint: str
    time_range: str
    queries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TriageConfig:
    ollama_endpoint: str
    model: str
    temperature: float
    language: str
    max_items_per_source: int
    system_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class RetentionConfig:
    dismissed_days: int
    new_days: int


@dataclass(frozen=True, slots=True)
class NewsConfig:
    store_path: Path
    rss_sources: tuple[RssSource, ...]
    searxng: SearxngConfig
    triage: TriageConfig
    retention: RetentionConfig
    watermark_dir: Path

    def seed_sources(self) -> tuple[NewsSource, ...]:
        rss = tuple(
            NewsSource(name=SourceName(source.name), type=SourceType.RSS, url=source.url, query="")
            for source in self.rss_sources
        )
        searxng = tuple(
            NewsSource(
                name=SourceName(f"searxng-{index + 1}"),
                type=SourceType.SEARXNG,
                url=self.searxng.endpoint,
                query=query,
            )
            for index, query in enumerate(self.searxng.queries)
        )
        return (*rss, *searxng)


def default_config() -> NewsConfig:
    return NewsConfig(
        store_path=DEFAULT_STORE_PATH,
        rss_sources=(),
        searxng=SearxngConfig(endpoint="http://127.0.0.1:8080", time_range="day", queries=()),
        triage=TriageConfig(
            ollama_endpoint="http://127.0.0.1:11434",
            model="ornith:35b",
            temperature=0.3,
            language="en",
            max_items_per_source=5,
            system_prompt=None,
        ),
        retention=RetentionConfig(dismissed_days=7, new_days=14),
        watermark_dir=DEFAULT_WATERMARK_DIR,
    )


def load_config(kwargs: dict[str, JsonValue]) -> NewsConfig | str:
    path_value = kwargs.get(CONFIG_KWARG)
    path = Path(path_value) if isinstance(path_value, str) and path_value else _env_config_path()
    try:
        exists = path.exists()
    except OSError:
        return default_config()
    if not exists:
        return default_config()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return f"could not read news config: {exc}"
    value = json_value(raw)
    if value is None:
        return "news config must contain JSON-compatible YAML"
    if not isinstance(value, dict):
        return "news config must be an object"
    return parse_config(value)


def parse_config(value: JsonObject) -> NewsConfig:
    base = default_config()
    sources = _object(value.get("sources"))
    triage = _object(value.get("triage"))
    retention = _object(value.get("retention"))
    return NewsConfig(
        store_path=Path(_string(value.get("store_path"), str(base.store_path))),
        rss_sources=_rss_sources(_array(sources.get("rss"))),
        searxng=_searxng(_object(sources.get("searxng")), base.searxng),
        triage=_triage(triage, base.triage),
        retention=_retention(retention, base.retention),
        watermark_dir=Path(_string(value.get("watermark_dir"), str(base.watermark_dir))),
    )


def _env_config_path() -> Path:
    configured = os.environ.get(CONFIG_ENV)
    if configured:
        return Path(configured)
    return DEFAULT_CONFIG_PATH


def _rss_sources(values: tuple[JsonValue, ...]) -> tuple[RssSource, ...]:
    sources: list[RssSource] = []
    for value in values:
        entry = _object(value)
        name = _string(entry.get("name"), "")
        url = _string(entry.get("url"), "")
        if name and url:
            sources.append(RssSource(name=name, url=url))
    return tuple(sources)


def _searxng(value: JsonObject, base: SearxngConfig) -> SearxngConfig:
    return SearxngConfig(
        endpoint=_string(value.get("endpoint"), base.endpoint),
        time_range=_string(value.get("time_range"), base.time_range),
        queries=tuple(
            _string(item, "") for item in _array(value.get("queries")) if _string(item, "")
        ),
    )


def _triage(value: JsonObject, base: TriageConfig) -> TriageConfig:
    return TriageConfig(
        ollama_endpoint=_string(value.get("ollama_endpoint"), base.ollama_endpoint),
        model=_string(value.get("model"), base.model),
        temperature=_float(value.get("temperature"), base.temperature),
        language=_string(value.get("language"), base.language),
        max_items_per_source=_int(value.get("max_items_per_source"), base.max_items_per_source),
        system_prompt=_optional_string(value.get("system_prompt"), base.system_prompt),
    )


def _retention(value: JsonObject, base: RetentionConfig) -> RetentionConfig:
    return RetentionConfig(
        dismissed_days=_int(value.get("dismissed_days"), base.dismissed_days),
        new_days=_int(value.get("new_days"), base.new_days),
    )


def _object(value: JsonValue | None) -> JsonObject:
    if isinstance(value, dict):
        return value
    return {}


def _array(value: JsonValue | None) -> tuple[JsonValue, ...]:
    if isinstance(value, list):
        return tuple(value)
    return ()


def _string(value: JsonValue | None, default: str) -> str:
    if isinstance(value, str):
        return value
    return default


def _optional_string(value: JsonValue | None, default: str | None) -> str | None:
    if isinstance(value, str):
        return value
    return default


def _int(value: JsonValue | None, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


def _float(value: JsonValue | None, default: float) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return default
