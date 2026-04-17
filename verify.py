#!/usr/bin/env python3
"""
Verification script for ai-news-bot logic.
Runs without a Discord token and validates canonical URL dedupe, archive storage,
round-robin selection, and live feed fetching.
"""
import asyncio
import os
import tempfile

import aiohttp

from ai_news_bot import FEEDS
from news_core import (
    ArticleStore,
    FeedItem,
    PostedArticle,
    USER_AGENT,
    canonical_url_hash,
    fetch_feed,
    normalize_url,
    parse_post_times_utc,
    select_round_robin,
)

MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "12"))
MAX_PER_SOURCE = int(os.getenv("MAX_PER_SOURCE_PER_RUN", "2"))


def test_url_normalization() -> None:
    raw = "https://Example.com/path/?utm_source=x&b=2&a=1#fragment"
    normalized = normalize_url(raw)
    assert normalized == "https://example.com/path?a=1&b=2", normalized
    assert canonical_url_hash(normalized), "Expected deterministic URL hash"
    print("[OK] Canonical URL normalization and hashing")


def test_archive_and_dedupe() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = os.path.join(tmpdir, "archive.jsonl")
        dedupe_index_path = os.path.join(tmpdir, "dedupe.json")
        store = ArticleStore(archive_path, dedupe_index_path)

        article = PostedArticle(
            source="Source A",
            title="Test title",
            url="https://example.com/post?utm_source=x",
            canonical_url="https://example.com/post",
            published_at="2026-01-01T00:00:00+00:00",
            posted_at="2026-01-02T00:00:00+00:00",
            discord_message_id="123",
            description="Summary",
        )
        store.record_post(article)

        assert store.has_canonical_url("https://example.com/post")
        latest = store.latest(5)
        assert len(latest) == 1
        assert latest[0].title == "Test title"
        assert latest[0].discord_message_id == "123"
        print("[OK] Archive append and dedupe index persistence")


def test_round_robin() -> None:
    items_by_source = {
        "A": [
            FeedItem("A", f"a{i}", f"https://a/{i}", f"https://a/{i}", "", None)
            for i in range(10)
        ],
        "B": [
            FeedItem("B", f"b{i}", f"https://b/{i}", f"https://b/{i}", "", None)
            for i in range(2)
        ],
        "C": [],
    }
    result = select_round_robin(
        items_by_source,
        max_total=MAX_POSTS_PER_RUN,
        max_per_source=MAX_PER_SOURCE,
    )
    counts = {}
    for item in result:
        counts[item.source] = counts.get(item.source, 0) + 1

    assert len(result) <= MAX_POSTS_PER_RUN
    assert all(count <= MAX_PER_SOURCE for count in counts.values())
    assert counts.get("A", 0) == min(3, MAX_PER_SOURCE)
    assert counts.get("B", 0) == 2
    assert counts.get("C", 0) == 0
    print("[OK] Round-robin distribution")


def test_post_schedule_parsing() -> None:
    result = parse_post_times_utc("17:00,09:00,17:00")
    assert [f"{entry.hour:02d}:{entry.minute:02d}" for entry in result] == [
        "09:00",
        "17:00",
    ]
    print("[OK] Post schedule parsing")


async def test_feed_fetch() -> None:
    headers = {"User-Agent": USER_AGENT}
    async with aiohttp.ClientSession(headers=headers) as session:
        results = await asyncio.gather(
            *(fetch_feed(session, name, url) for name, url in FEEDS),
            return_exceptions=True,
        )

    ok = 0
    for i, result in enumerate(results):
        source_name = FEEDS[i][0]
        if isinstance(result, Exception):
            print(f"[WARN] Feed {source_name}: {result}")
            continue

        ok += 1
        assert isinstance(result, list)
        if result:
            item = result[0]
            assert item.source == source_name
            assert item.url
            assert item.canonical_url
            assert item.title
        print(f"[OK] Feed {source_name}: {len(result)} items")

    assert ok >= 4, f"Expected at least 4 feeds to succeed, got {ok}"
    print(f"[OK] Feed fetch: {ok}/{len(FEEDS)} feeds returned data")


def main() -> None:
    print("=== ai-news-bot verification ===\n")
    test_url_normalization()
    test_archive_and_dedupe()
    test_round_robin()
    test_post_schedule_parsing()
    asyncio.run(test_feed_fetch())
    print("\n=== All checks passed ===")


if __name__ == "__main__":
    main()
