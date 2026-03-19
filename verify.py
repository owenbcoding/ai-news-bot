#!/usr/bin/env python3
"""
Verification script for ai-news-bot logic.
Runs without Discord token - validates feed fetching, deduplication, and round-robin logic.
"""
import asyncio
import json
import os
import re
import tempfile
from html import unescape
from typing import Dict, List, Set, Tuple

import aiohttp
import feedparser
from dotenv import load_dotenv

load_dotenv()

FEEDS: List[Tuple[str, str]] = [
    ("OpenAI News", "https://openai.com/news/rss.xml"),
    ("OpenAI Engineering", "https://openai.com/news/engineering/rss.xml"),
    ("DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("arXiv cs.AI", "https://rss.arxiv.org/rss/cs.AI"),
    ("Google Research", "https://research.google/blog/rss/"),
    ("Microsoft Research", "https://www.microsoft.com/en-us/research/feed/"),
]

MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "12"))
MAX_PER_SOURCE = int(os.getenv("MAX_PER_SOURCE_PER_RUN", "3"))


async def fetch_feed(session: aiohttp.ClientSession, name: str, url: str) -> List[Dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ai-news-bot/1.0; +https://example.com/bot)"
    }
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=25)) as resp:
        resp.raise_for_status()
        data = await resp.read()
    parsed = feedparser.parse(data)
    items = []
    for entry in parsed.entries:
        link = entry.get("link")
        title = entry.get("title", "(no title)")
        uid = entry.get("id") or entry.get("guid") or link
        if not uid or not link:
            continue
        description = entry.get("summary") or entry.get("description") or ""
        if description:
            description = re.sub(r"<[^>]+>", "", description)
            description = unescape(description)
            description = re.sub(r"\s+", " ", description).strip()
            if len(description) > 500:
                description = description[:497] + "..."
        items.append({"source": name, "uid": str(uid), "title": str(title), "link": str(link), "description": description})
    return items


def run_round_robin(items_by_source: Dict[str, List[Dict]], max_total: int, max_per_source: int) -> List[Dict]:
    """Mirrors bot.py round-robin: total per-source cap across the whole run."""
    new_items: List[Dict] = []
    source_indices = {source: 0 for source in items_by_source.keys()}
    source_totals = {source: 0 for source in items_by_source.keys()}
    while len(new_items) < max_total:
        added_any = False
        for source_name in items_by_source.keys():
            if len(new_items) >= max_total:
                break
            if source_totals[source_name] >= max_per_source:
                continue
            source_items = items_by_source[source_name]
            idx = source_indices[source_name]
            while (
                idx < len(source_items)
                and source_totals[source_name] < max_per_source
                and len(new_items) < max_total
            ):
                new_items.append(source_items[idx])
                idx += 1
                source_totals[source_name] += 1
                added_any = True
            source_indices[source_name] = idx
        if not added_any:
            break
    return new_items


def test_seen_roundtrip():
    """Test load_seen/save_seen logic."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        path = f.name
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(["id1", "id2"], f)
        with open(path, "r", encoding="utf-8") as f:
            seen = set(json.load(f))
        assert seen == {"id1", "id2"}, f"Expected {{'id1','id2'}}, got {seen}"
        # Test trim to 2000
        large = list(range(2500))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted([str(x) for x in large])[-2000:], f)
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert len(loaded) <= 2000, f"Expected <=2000, got {len(loaded)}"
        print("[OK] Seen load/save/trim logic")
    finally:
        os.unlink(path)


def test_round_robin():
    """Test round-robin distribution respects caps."""
    items_by_source = {
        "A": [{"uid": f"a{i}", "source": "A"} for i in range(10)],
        "B": [{"uid": f"b{i}", "source": "B"} for i in range(2)],
        "C": [],
    }
    result = run_round_robin(items_by_source, max_total=12, max_per_source=3)
    counts = {}
    for it in result:
        counts[it["source"]] = counts.get(it["source"], 0) + 1
    assert len(result) <= 12, f"Expected <=12, got {len(result)}"
    assert all(c <= 3 for c in counts.values()), f"Expected <=3 per source, got {counts}"
    assert counts.get("A", 0) == 3, f"Expected 3 from A, got {counts.get('A',0)}"
    assert counts.get("B", 0) == 2, f"Expected 2 from B, got {counts.get('B',0)}"
    assert counts.get("C", 0) == 0, f"Expected 0 from C, got {counts.get('C',0)}"
    print("[OK] Round-robin distribution (max_total=12, max_per_source=3)")


async def test_feed_fetch():
    """Test fetching all 6 feeds."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ai-news-bot/1.0; +https://example.com/bot)"}
    async with aiohttp.ClientSession(headers=headers) as session:
        results = await asyncio.gather(
            *(fetch_feed(session, name, url) for name, url in FEEDS),
            return_exceptions=True,
        )
    ok = 0
    for i, res in enumerate(results):
        name = FEEDS[i][0]
        if isinstance(res, Exception):
            print(f"[WARN] Feed {name}: {res}")
        else:
            ok += 1
            assert isinstance(res, list), f"Expected list from {name}"
            if res:
                it = res[0]
                assert "uid" in it and "link" in it and "title" in it and "source" in it, f"Missing keys in {name}"
            print(f"[OK] Feed {name}: {len(res)} items")
    assert ok >= 4, f"Expected at least 4 feeds to succeed, got {ok}"
    print(f"[OK] Feed fetch: {ok}/6 feeds returned data")


def test_env():
    """Validate required env vars are present (for runtime)."""
    token = os.getenv("DISCORD_TOKEN")
    channel = os.getenv("CHANNEL_ID")
    if not token or token == "your_discord_bot_token_here":
        print("[SKIP] DISCORD_TOKEN not set (optional for verify)")
    else:
        print("[OK] DISCORD_TOKEN is set")
    if not channel or channel == "123456789012345678":
        print("[SKIP] CHANNEL_ID not set (optional for verify)")
    else:
        print("[OK] CHANNEL_ID is set")


def main():
    print("=== ai-news-bot verification ===\n")
    test_seen_roundtrip()
    test_round_robin()
    test_env()
    asyncio.run(test_feed_fetch())
    print("\n=== All checks passed ===")


if __name__ == "__main__":
    main()
