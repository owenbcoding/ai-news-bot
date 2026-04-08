import asyncio
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
from html import unescape
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp
import discord
import feedparser
from discord import app_commands
from discord.ext import tasks

USER_AGENT = "Mozilla/5.0 (compatible; ai-news-bot/2.0; +https://example.com/bot)"
TRACKING_QUERY_PREFIXES = ("utm_", "mc_", "mkt_", "ga_", "igshid")
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "dclid",
    "gbraid",
    "wbraid",
}


@dataclass
class FeedItem:
    source: str
    title: str
    url: str
    canonical_url: str
    description: str
    published_at: Optional[str]


@dataclass
class PostedArticle:
    source: str
    title: str
    url: str
    canonical_url: str
    published_at: Optional[str]
    posted_at: str
    discord_message_id: str
    description: str = ""


@dataclass
class NewsBotConfig:
    bot_name: str
    discord_token: str
    channel_id: int
    guild_id: Optional[int]
    post_times_utc: Sequence[time]
    max_posts_per_run: int
    max_per_source_per_run: int
    feeds: Sequence[Tuple[str, str]]
    archive_path: str
    dedupe_index_path: str
    post_text_digest: bool = False
    latestlinks_default: int = 5


def clean_summary(raw_summary: str, max_length: int = 500) -> str:
    if not raw_summary:
        return ""

    summary = re.sub(r"<[^>]+>", "", raw_summary)
    summary = unescape(summary)
    summary = re.sub(r"\s+", " ", summary).strip()
    if len(summary) > max_length:
        return summary[: max_length - 3].rstrip() + "..."
    return summary


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    filtered_query: List[Tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        lowered = key.lower()
        if lowered in TRACKING_QUERY_KEYS:
            continue
        if lowered.startswith(TRACKING_QUERY_PREFIXES):
            continue
        filtered_query.append((key, value))

    filtered_query.sort()
    query = urlencode(filtered_query, doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def canonical_url_hash(canonical_url: str) -> str:
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def format_published_date(published_at: Optional[str]) -> str:
    if not published_at:
        return "Unknown date"

    candidate = published_at.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return published_at

    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def extract_published_at(entry: feedparser.FeedParserDict) -> Optional[str]:
    published_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if published_parsed:
        return datetime(*published_parsed[:6], tzinfo=timezone.utc).isoformat()

    published = entry.get("published") or entry.get("updated")
    if published:
        return str(published)

    return None


async def fetch_feed(
    session: aiohttp.ClientSession,
    source_name: str,
    feed_url: str,
) -> List[FeedItem]:
    async with session.get(feed_url, timeout=aiohttp.ClientTimeout(total=25)) as resp:
        resp.raise_for_status()
        data = await resp.read()

    parsed = feedparser.parse(data)
    items: List[FeedItem] = []
    for entry in parsed.entries:
        url = entry.get("link")
        if not url:
            continue

        title = str(entry.get("title", "(no title)")).strip() or "(no title)"
        canonical_url = normalize_url(str(url))
        description = clean_summary(entry.get("summary") or entry.get("description") or "")
        items.append(
            FeedItem(
                source=source_name,
                title=title,
                url=str(url),
                canonical_url=canonical_url,
                description=description,
                published_at=extract_published_at(entry),
            )
        )

    return items


def select_round_robin(
    items_by_source: Dict[str, Sequence[FeedItem]],
    max_total: int,
    max_per_source: int,
) -> List[FeedItem]:
    selected: List[FeedItem] = []
    source_indices = {source: 0 for source in items_by_source}
    source_totals = {source: 0 for source in items_by_source}

    while len(selected) < max_total:
        added_any = False
        for source_name, source_items in items_by_source.items():
            if len(selected) >= max_total:
                break
            if source_totals[source_name] >= max_per_source:
                continue

            idx = source_indices[source_name]
            while (
                idx < len(source_items)
                and source_totals[source_name] < max_per_source
                and len(selected) < max_total
            ):
                selected.append(source_items[idx])
                idx += 1
                source_totals[source_name] += 1
                added_any = True
            source_indices[source_name] = idx

        if not added_any:
            break

    return selected


class ArticleStore:
    def __init__(self, archive_path: str, dedupe_index_path: str):
        self.archive_path = archive_path
        self.dedupe_index_path = dedupe_index_path
        self.index = self._load_index()

    def _ensure_parent(self, path: str) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _load_index(self) -> Dict[str, Dict[str, str]]:
        try:
            with open(self.dedupe_index_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except FileNotFoundError:
            return {}
        except Exception:
            return {}
        return {}

    def has_canonical_url(self, canonical_url: str) -> bool:
        return canonical_url_hash(canonical_url) in self.index

    def record_post(self, article: PostedArticle) -> None:
        article_hash = canonical_url_hash(article.canonical_url)
        index_entry = {
            "canonical_url": article.canonical_url,
            "url": article.url,
            "title": article.title,
            "source": article.source,
            "posted_at": article.posted_at,
            "discord_message_id": article.discord_message_id,
        }
        self._append_archive(article)
        self.index[article_hash] = index_entry
        self._write_index()

    def _write_index(self) -> None:
        self._ensure_parent(self.dedupe_index_path)
        with open(self.dedupe_index_path, "w", encoding="utf-8") as handle:
            json.dump(self.index, handle, indent=2, sort_keys=True)

    def _append_archive(self, article: PostedArticle) -> None:
        self._ensure_parent(self.archive_path)
        with open(self.archive_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(article), ensure_ascii=True) + "\n")

    def latest(self, limit: int) -> List[PostedArticle]:
        if limit <= 0:
            return []

        rows: List[PostedArticle] = []
        try:
            with open(self.archive_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                        rows.append(PostedArticle(**payload))
                    except Exception:
                        continue
        except FileNotFoundError:
            return []

        return rows[-limit:]


def build_embed(item: FeedItem) -> discord.Embed:
    embed = discord.Embed(title=item.title, url=item.url)
    embed.description = item.description or f"Read more from {item.source}"
    embed.add_field(name="Read article", value=item.url, inline=False)
    embed.set_footer(text=f"{item.source} | {format_published_date(item.published_at)}")
    return embed


def build_text_digest(items: Sequence[FeedItem]) -> str:
    lines = ["Latest AI news links:"]
    for item in items:
        lines.append(f"- {item.source}: {item.title} | {item.url}")
    return "\n".join(lines)


def posted_article_from_message(item: FeedItem, discord_message_id: int) -> PostedArticle:
    return PostedArticle(
        source=item.source,
        title=item.title,
        url=item.url,
        canonical_url=item.canonical_url,
        published_at=item.published_at,
        posted_at=utc_now_iso(),
        discord_message_id=str(discord_message_id),
        description=item.description,
    )


class NewsDiscordClient(discord.Client):
    def __init__(self, config: NewsBotConfig):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.config = config
        self.store = ArticleStore(config.archive_path, config.dedupe_index_path)
        self.tree = app_commands.CommandTree(self)
        self._poll_started = False
        self.poll_and_post.change_interval(time=list(config.post_times_utc))

    async def setup_hook(self) -> None:
        @app_commands.command(
            name="latestlinks",
            description="Show the most recently saved article links.",
        )
        @app_commands.describe(limit="How many saved links to show (1-10).")
        async def latestlinks(
            interaction: discord.Interaction,
            limit: app_commands.Range[int, 1, 10] = self.config.latestlinks_default,
        ) -> None:
            articles = self.store.latest(limit)
            if not articles:
                await interaction.response.send_message(
                    "No saved links are in the archive yet.",
                    ephemeral=True,
                )
                return

            rows = []
            for article in reversed(articles):
                rows.append(f"- {article.source}: {article.title} | {article.url}")

            await interaction.response.send_message("\n".join(rows), ephemeral=True)

        self.tree.add_command(latestlinks)

        await self._sync_commands()

    async def _sync_commands(self) -> None:
        if self.config.guild_id:
            guild = discord.Object(id=self.config.guild_id)
            try:
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                print(f"✓ Synced slash commands to guild {self.config.guild_id}")
                return
            except discord.HTTPException as exc:
                print(
                    "[Warn] Guild slash-command sync failed for "
                    f"{self.config.guild_id}: {exc}"
                )
                print("[Warn] Falling back to global slash-command sync")

        try:
            await self.tree.sync()
            print("✓ Synced global slash commands")
        except discord.HTTPException as exc:
            print(f"[Warn] Global slash-command sync failed: {exc}")
            print("[Warn] Bot will continue running without slash commands for now")

    async def _resolve_channel(self) -> discord.abc.Messageable:
        channel = self.get_channel(self.config.channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.config.channel_id)
        return channel

    @tasks.loop(time=[time(hour=9, tzinfo=timezone.utc), time(hour=17, tzinfo=timezone.utc)])
    async def poll_and_post(self) -> None:
        channel = await self._resolve_channel()
        headers = {"User-Agent": USER_AGENT}

        async with aiohttp.ClientSession(headers=headers) as session:
            results = await asyncio.gather(
                *(fetch_feed(session, name, url) for name, url in self.config.feeds),
                return_exceptions=True,
            )

        items_by_source: Dict[str, List[FeedItem]] = {}
        for index, result in enumerate(results):
            source_name = self.config.feeds[index][0]
            if isinstance(result, Exception):
                print(f"[Error] Feed fetch failed for {source_name}: {result}")
                items_by_source[source_name] = []
                continue

            items_by_source[source_name] = [
                item
                for item in result
                if not self.store.has_canonical_url(item.canonical_url)
            ]

        selected = select_round_robin(
            items_by_source,
            max_total=self.config.max_posts_per_run,
            max_per_source=self.config.max_per_source_per_run,
        )
        if not selected:
            print("[Info] No new items to post")
            return

        posted_items: List[FeedItem] = []
        for item in selected:
            try:
                message = await channel.send(embed=build_embed(item))
                self.store.record_post(posted_article_from_message(item, message.id))
                posted_items.append(item)
                print(f"[Posted] {item.source}: {item.title[:50]}...")
            except Exception as exc:
                print(f"[Error] Failed to post {item.title[:50]}: {exc}")

        if posted_items and self.config.post_text_digest:
            try:
                await channel.send(build_text_digest(posted_items))
            except Exception as exc:
                print(f"[Warn] Failed to post text digest: {exc}")

    async def on_ready(self) -> None:
        print(f"✓ Logged in as {self.user}")
        print(f"✓ {self.config.bot_name} watching {len(self.config.feeds)} feeds")
        schedule = ", ".join(
            post_time.strftime("%H:%M UTC") for post_time in self.config.post_times_utc
        )
        print(f"✓ Scheduled posts at {schedule}")
        if not self._poll_started:
            self.poll_and_post.start()
            self._poll_started = True


def run_bot(config: NewsBotConfig) -> None:
    client = NewsDiscordClient(config)
    client.run(config.discord_token)
