from __future__ import annotations

from typing import TYPE_CHECKING

from news_hermes.config import DEFAULT_STORE_PATH, load_config

if TYPE_CHECKING:
    from pathlib import Path


def test_missing_config_uses_open_source_localhost_defaults() -> None:
    # Given: no config file at the default path.

    # When: config is loaded without overrides.
    config = load_config({})

    # Then: defaults are runtime-agnostic localhost values.
    assert not isinstance(config, str)
    assert config.store_path == DEFAULT_STORE_PATH
    assert config.searxng.endpoint == "http://127.0.0.1:8080"
    assert config.triage.ollama_endpoint == "http://127.0.0.1:11434"
    assert config.triage.model == "ornith:35b"
    assert config.triage.system_prompt is None


def test_config_kwarg_loads_yaml(tmp_path: Path) -> None:
    # Given: a YAML config file with source and retention overrides.
    config_path = tmp_path / "news-hermes.yaml"
    store_path = tmp_path / "news.json"
    _ = config_path.write_text(
        f"""
store_path: {store_path}
sources:
  rss:
    - name: vendor
      url: https://example.test/feed.xml
  searxng:
    endpoint: http://localhost:9999
    time_range: week
    queries:
      - model release
retention:
  dismissed_days: 3
  new_days: 9
triage:
  system_prompt: Keep items about Hermes and summarize in {{language}}.
""".strip(),
        encoding="utf-8",
    )

    # When: config is loaded through the hidden test/host kwarg.
    config = load_config({"_news_config": str(config_path)})

    # Then: configured values override defaults.
    assert not isinstance(config, str)
    assert config.store_path == store_path
    assert config.rss_sources[0].name == "vendor"
    assert config.seed_sources()[0].name == "vendor"
    assert config.searxng.endpoint == "http://localhost:9999"
    assert config.searxng.queries == ("model release",)
    assert config.triage.system_prompt == "Keep items about Hermes and summarize in {language}."
    assert config.retention.dismissed_days == 3
    assert config.retention.new_days == 9
