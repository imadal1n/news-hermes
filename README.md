# news-hermes

`news-hermes` is a small Hermes directory plugin for tracking tech news from
configured RSS/Atom feeds, SearXNG queries, and GitHub releases with local
Ollama triage.

The plugin is intentionally runtime-agnostic. It does not assume a specific host,
workspace, identity, chat bridge, or deployment layout.

## Status

This is an alpha plugin for Hermes runtimes that support Python directory
plugins with registered tools. It is packaged as normal Python code and can also
be copied into Hermes' directory-plugin layout.

## Behavior

- Stores news items in JSON at `/opt/data/workspace/news.json` by default.
- Uses YAML config only as a seed for initial sources and defaults.
- Stores editable sources in `news.json`; add/remove sources through tools without
  editing config or restarting a runtime.
- Fetches RSS/Atom feeds through `feedparser`.
- Fetches SearXNG JSON search results when queries are configured (best-effort; unauthenticated, no paging).
- Fetches GitHub releases from `https://api.github.com/repos/{owner}/{repo}/releases` when sources are configured; skips draft releases and includes prereleases.
- On first pull, records a bounded feed watermark and ingests no historical items.
- Triages newly discovered items through a local Ollama model.
- Keeps raw items with an empty summary and reports `triage_error` if triage is unavailable or malformed.
- Deduplicates by URL against the store before ingest.
- Keeps at most 500 recently seen URLs in the watermark file.
- Uses an atomic temporary-file replace when writing the JSON document.
- Returns JSON strings from every Hermes tool handler, including errors.

Example storage file:

```json
{
  "items": [
    {
      "id": "news_8f4f1d2a9c31",
      "title": "Model runtime released",
      "url": "https://example.test/release",
      "summary": "Concise summary.",
      "source": "vendor-blog",
      "source_type": "rss",
      "status": "new",
      "found_at": "2026-07-08T13:00:00",
      "published_at": "2026-07-08T10:00:00+00:00",
      "bookmarked_at": null,
      "dismissed_at": null
    }
  ],
  "sources": [
    {
      "name": "vendor-blog",
      "type": "rss",
      "url": "https://example.test/feed.xml",
      "query": ""
    }
  ]
}
```

## Tools

### `news_list`

Arguments:

```json
{ "status": "new" }
```

`status` is optional and defaults to `new`. Accepted values are `new`,
`bookmarked`, `dismissed`, and `all`.

### `news_bookmark`

Arguments:

```json
{ "id": "news_..." }
```

Marks an item as bookmarked.

### `news_dismiss`

Arguments:

```json
{ "id": "news_..." }
```

Marks an item as dismissed.

### `news_pull`

Arguments:

```json
{ "silent": true }
```

Fetches configured RSS, SearXNG, and GitHub releases sources, triages new items,
ingests them, and runs retention purge first. The first pull writes the watermark
baseline and returns `new_count=0`; later pulls ingest only URLs absent from both
the store and watermark. `silent=true` returns `new_count`, plus `triage_error`
when raw items were kept because triage failed.

### `news_clear`

Arguments:

```json
{ "target": "dismissed" }
```

Accepted targets are `dismissed`, `expired`, and `all`. `all` requires
`confirm=true`.

### `news_source_add`

Arguments:

```json
{ "name": "vendor", "type": "rss", "url": "https://example.test/feed.xml" }
```

For SearXNG sources, pass `type="searxng"`, `url` as the endpoint, and `query` as
the search query. For GitHub releases, pass `type="github_releases"` and `url` as
the full releases API endpoint (`https://api.github.com/repos/{owner}/{repo}/releases`);
`query` is unused.

### `news_source_remove`

Arguments:

```json
{ "name": "vendor" }
```

Removes a stored source by name.

### `news_source_list`

Arguments:

```json
{}
```

Lists all stored sources.

## Configuration Seed

YAML is a seed surface. On first pull, configured sources are copied into
`news.json` if the store has no sources. After that, source management happens
through `news_source_add`, `news_source_remove`, and `news_source_list`.

```yaml
store_path: /opt/data/workspace/news.json

sources:
  rss:
    - name: example-blog
      url: https://example.test/feed.xml
  github_releases:
    - name: example-repo
      repo: owner/repo
  searxng:
    endpoint: http://127.0.0.1:8080
    time_range: day
    queries:
      - "AI model release"

triage:
  ollama_endpoint: http://127.0.0.1:11434
  model: ornith:35b
  temperature: 0.3
  language: en
  max_items_per_source: 5
  system_prompt: >
    You are a tech news triage filter. Keep only actionable technical news and
    write a concise summary in {language}.

retention:
  dismissed_days: 7
  new_days: 14

watermark_dir: /opt/data/watcher-state
```

## Source URL and query semantics

Each stored source keeps the same JSON shape:

```json
{ "name": "...", "type": "...", "url": "...", "query": "..." }
```

- `rss`: `url` is the feed URL; `query` is empty.
- `searxng`: `url` is the SearXNG endpoint; `query` is the search query.
- `github_releases`: `url` is the full GitHub releases API endpoint
  (`https://api.github.com/repos/{owner}/{repo}/releases`); `query` is empty.

## Minimal config and first pull

Create a YAML config at the default path or point `NEWS_HERMES_CONFIG` to it:

```yaml
store_path: /opt/data/workspace/news.json
sources:
  rss:
    - name: example-blog
      url: https://example.test/feed.xml
  github_releases:
    - name: example-repo
      repo: owner/repo
watermark_dir: /opt/data/watcher-state
```

Then call the `news_pull` tool through the host runtime. The first pull returns
`new_count=0`; subsequent pulls ingest only new URLs.

## Install

For package-based use:

```bash
uv add news-hermes
```

For direct source installs while testing:

```bash
uv pip install .
```

## Hermes Plugin Layout

Install the plugin directory so Hermes can discover it as:

```text
$HERMES_HOME/plugins/news-hermes/
  __init__.py
  clients.py
  config.py
  feeds.py
  json_types.py
  models.py
  plugin.py
  plugin.yaml
  py.typed
  schemas.py
  sources.py
  storage.py
  tools.py
```

Activation is controlled by Hermes configuration, for example by adding the
plugin name to `plugins.enabled` in the target Hermes profile.

## Validate

Run the local checks with `uv`:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
```

Build the Python package artifacts with:

```bash
uv build
```

The standalone repository also includes a GitHub Actions workflow that runs the
same checks on Python 3.11, 3.12, and 3.13.

## Non-Goals

- This package does not edit Hermes config.
- This package does not enable itself in any running profile.
- This package does not restart, recreate, or otherwise manage a Hermes runtime.
- This package does not schedule background jobs.
- This package does not send messages to any platform.
- This package does not require a database.

Deployment wrappers should keep activation and scheduling as separate operator
decisions.

## License

MIT. See `LICENSE`.
