from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from .json_types import parse_json_object

if TYPE_CHECKING:
    from pathlib import Path

    from .models import RawNewsItem

WATERMARK_FILE: Final[str] = "news-hermes-watermark.json"
WATERMARK_LIMIT: Final[int] = 500


@dataclass(frozen=True, slots=True)
class NewsWatermark:
    seen_urls: tuple[str, ...]


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
        current_urls = tuple(item.url for item in items)
        if isinstance(loaded, str):
            return loaded
        if loaded is None:
            saved = self.save(NewsWatermark(self._bounded(current_urls)))
            if isinstance(saved, str):
                return saved
            return ()
        seen = frozenset(loaded.seen_urls)
        fresh = tuple(item for item in items if item.url not in seen and item.url not in known_urls)
        saved = self.save(NewsWatermark(self._bounded((*current_urls, *loaded.seen_urls))))
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
        raw_urls = value.get("seen_urls")
        if not isinstance(raw_urls, list):
            return "news watermark.seen_urls must be a list"
        urls: list[str] = []
        for url in raw_urls:
            if not isinstance(url, str):
                return "news watermark.seen_urls entries must be strings"
            urls.append(url)
        return NewsWatermark(tuple(urls[: self.limit]))

    def save(self, watermark: NewsWatermark) -> str | None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_name(f"{self.path.name}.tmp")
            _ = temp_path.write_text(
                json.dumps(
                    {"version": 1, "seen_urls": list(watermark.seen_urls[: self.limit])},
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
