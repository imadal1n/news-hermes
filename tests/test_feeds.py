from __future__ import annotations

from news_hermes.config import RssSource
from news_hermes.feeds import parse_feed
from news_hermes.models import SourceType


def test_parse_feed_normalizes_atom_entries() -> None:
    # Given: an Atom feed with one entry.
    source = RssSource(name="vendor", url="https://example.test/feed.xml")
    text = """
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Runtime released</title>
    <link href="https://example.test/release" />
    <updated>2026-07-08T10:00:00Z</updated>
  </entry>
</feed>
"""

    # When: the feed is parsed.
    items = parse_feed(source, text)

    # Then: a raw RSS news item is returned.
    assert len(items) == 1
    assert items[0].title == "Runtime released"
    assert items[0].url == "https://example.test/release"
    assert items[0].source == "vendor"
    assert items[0].source_type == SourceType.RSS
    assert items[0].published_at is not None


def test_parse_feed_stops_after_limit() -> None:
    # Given: an Atom feed with more entries than the source limit.
    source = RssSource(name="vendor", url="https://example.test/feed.xml")
    entries = "\n".join(
        f"""
  <entry>
    <title>Runtime {index}</title>
    <link href=\"https://example.test/release-{index}\" />
    <updated>2026-07-08T10:00:00Z</updated>
  </entry>"""
        for index in range(8)
    )
    text = f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'

    # When: the feed is parsed with a limit.
    items = parse_feed(source, text, limit=5)

    # Then: only the first limited entries are materialized.
    assert [item.url for item in items] == [
        "https://example.test/release-0",
        "https://example.test/release-1",
        "https://example.test/release-2",
        "https://example.test/release-3",
        "https://example.test/release-4",
    ]
