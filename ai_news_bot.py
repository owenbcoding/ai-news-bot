import os
from typing import List, Tuple

from dotenv import load_dotenv

from news_core import NewsBotConfig, run_bot

load_dotenv()


def optional_int_env(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw or raw == "0":
        return None
    return int(raw)


DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
AI_CHANNEL_ID = int(os.getenv("AI_CHANNEL_ID") or os.getenv("CHANNEL_ID", "0"))
GUILD_ID = optional_int_env("GUILD_ID")
MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "12"))
MAX_PER_SOURCE_PER_RUN = int(os.getenv("MAX_PER_SOURCE_PER_RUN", "3"))
POST_TEXT_DIGEST = os.getenv("POST_TEXT_DIGEST", "0") == "1"
AI_ARCHIVE_PATH = os.getenv("AI_ARCHIVE_PATH", "state/ai_news_archive.jsonl")
AI_DEDUPE_INDEX_PATH = os.getenv(
    "AI_DEDUPE_INDEX_PATH", "state/ai_news_dedupe_index.json"
)

# Keep these feed URLs unchanged for AI news scraping.
FEEDS: List[Tuple[str, str]] = [
    ("OpenAI News", "https://openai.com/news/rss.xml"),
    ("OpenAI Engineering", "https://openai.com/news/engineering/rss.xml"),
    ("DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("arXiv cs.AI", "https://rss.arxiv.org/rss/cs.AI"),
    ("Google Research", "https://research.google/blog/rss/"),
    ("Microsoft Research", "https://www.microsoft.com/en-us/research/feed/"),
]


def main() -> None:
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN not set in .env")
        raise SystemExit(1)
    if AI_CHANNEL_ID == 0:
        print("ERROR: AI_CHANNEL_ID (or CHANNEL_ID) not set in .env")
        raise SystemExit(1)

    config = NewsBotConfig(
        bot_name="AI News Bot",
        discord_token=DISCORD_TOKEN,
        channel_id=AI_CHANNEL_ID,
        guild_id=GUILD_ID,
        max_posts_per_run=MAX_POSTS_PER_RUN,
        max_per_source_per_run=MAX_PER_SOURCE_PER_RUN,
        feeds=FEEDS,
        archive_path=AI_ARCHIVE_PATH,
        dedupe_index_path=AI_DEDUPE_INDEX_PATH,
        post_text_digest=POST_TEXT_DIGEST,
        latestlinks_default=5,
    )
    run_bot(config)


if __name__ == "__main__":
    main()
