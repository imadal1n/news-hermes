from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from .json_types import JsonValue, parse_json_object

if TYPE_CHECKING:
    from pathlib import Path

    from .models import RawNewsItem

WATERMARK_FILE: Final[str] = "news-hermes-watermark.json"
WATERMARK_LIMIT: Final[int] = 500


@dataclass(frozen=True, slots=True)
class NewsWatermark:
    sources: dict[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class WatermarkStore:
    path: Path
    limit: int = WATERMARK_LIMIT

    @classmethod
    def from_dir(cls, directory: Path) -> WatermarkStore:
        return cls(directory / WATERMARK_FILE)

    def fresh_items(
        self,
        items: tuple[RawNewsItem, ...],
        known_urls: frozenset[str],
    ) -> tuple[RawNewsItem, ...] | str:
        loaded = self.load()
        if isinstance(loaded, str):
            return loaded
        if loaded is None:
            saved = self.save(NewsWatermark(self._sources_from_items(items, {})))
            if isinstance(saved, str):
                return saved
            return ()
        fresh = self._fresh_items(items, known_urls, loaded)
        saved = self.save(NewsWatermark(self._sources_from_items(items, loaded.sources)))
        if isinstance(saved, str):
            return saved
        return fresh

    def load(self) -> NewsWatermark | str | None:
        if not self.path.exists():
            return None
        try:
            value = parse_json_object(self.path.read_text(encoding="utf-8"))
        except OSError as exc:
            return f"could not read news watermark: {exc}"
        if value is None:
            return "news watermark is not valid JSON"
        return self._parse_sources(value.get("sources"))

    def _parse_sources(self, raw_sources: JsonValue | None) -> NewsWatermark | str:
        if not isinstance(raw_sources, dict):
            return "news watermark.sources must be an object"
        sources: dict[str, tuple[str, ...]] = {}
        for key, raw_urls in raw_sources.items():
            urls = self._parse_urls(raw_urls)
            if isinstance(urls, str):
                return urls
            sources[key] = urls
        return NewsWatermark(sources)

    def save(self, watermark: NewsWatermark) -> str | None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_name(f"{self.path.name}.tmp")
            _ = temp_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "sources": {
                            key: list(urls[: self.limit]) for key, urls in watermark.sources.items()
                        },
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            _ = temp_path.replace(self.path)
        except OSError as exc:
            return f"could not write news watermark: {exc}"
        return None

    def _bounded(self, urls: tuple[str, ...]) -> tuple[str, ...]:
        seen: set[str] = set()
        result: list[str] = []
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            result.append(url)
            if len(result) == self.limit:
                break
        return tuple(result)

    def _sources_from_items(
        self,
        items: tuple[RawNewsItem, ...],
        previous: dict[str, tuple[str, ...]],
    ) -> dict[str, tuple[str, ...]]:
        sources = dict(previous)
        grouped: dict[str, list[str]] = {}
        for item in items:
            grouped.setdefault(source_key(item), []).append(item.url)
        for key, urls in grouped.items():
            sources[key] = self._bounded((*tuple(urls), *sources.get(key, ())))
        return sources

    def _fresh_items(
        self,
        items: tuple[RawNewsItem, ...],
        known_urls: frozenset[str],
        watermark: NewsWatermark,
    ) -> tuple[RawNewsItem, ...]:
        fresh: list[RawNewsItem] = []
        for item in items:
            urls = watermark.sources.get(source_key(item))
            if urls is None:
                continue
            if item.url not in urls and item.url not in known_urls:
                fresh.append(item)
        return tuple(fresh)

    def _parse_urls(self, raw_urls: JsonValue | None) -> tuple[str, ...] | str:
        if not isinstance(raw_urls, list):
            return "news watermark source value must be a list"
        urls: list[str] = []
        for url in raw_urls:
            if not isinstance(url, str):
                return "news watermark source entries must be strings"
            urls.append(url)
        return tuple(urls[: self.limit])


def source_key(item: RawNewsItem) -> str:
    return f"{item.source_type.value}:{item.source}"
