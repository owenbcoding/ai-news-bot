import os
import sys
import json
import asyncio
import time
from pathlib import Path
from typing import Dict, List, Set, Tuple

import aiohttp
import feedparser
import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

# Ensure output is not buffered
sys.stdout.reconfigure(line_buffering=True)

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
POLL_MINUTES = int(os.getenv("POLL_MINUTES", "30"))
# Optional: for instant slash-command registration (recommended).
# If unset/0, commands will sync globally (can take a while to appear).
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

# Start small; you can expand later.
FEEDS: List[Tuple[str, str]] = [
    ("OpenAI News", "https://openai.com/news/rss.xml"),
    ("OpenAI Engineering", "https://openai.com/news/engineering/rss.xml"),
    ("DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("arXiv cs.AI", "https://rss.arxiv.org/rss/cs.AI"),
    ("Google Research", "https://research.google/blog/rss/"),
    ("Microsoft Research", "https://www.microsoft.com/en-us/research/feed/"),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml"),
]

# Always store `seen.json` next to this script (avoid duplicates due to different working directories).
SEEN_PATH = Path(__file__).with_name("seen.json")
MAX_POSTS_PER_RUN = 5  # safety: avoid dumping 100 links at once
# Anti-spam controls
# - If BATCH_POST=1 (default), send one message containing up to MAX_POSTS_PER_RUN items.
# - If BATCH_POST=0, send items one-by-one with a short delay between sends.
BATCH_POST = os.getenv("BATCH_POST", "1") == "1"
POST_DELAY_SECONDS = float(os.getenv("POST_DELAY_SECONDS", "1.5"))
# Diversity control: limit how many items per source per poll.
MAX_PER_SOURCE_PER_RUN = int(os.getenv("MAX_PER_SOURCE_PER_RUN", "1"))

intents = discord.Intents.default()  # posting only; no message-content needed
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@tree.command(name="ping", description="Health check: replies with pong.")
async def ping(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("pong", ephemeral=True)

def print_accessible_text_channels(limit_per_guild: int = 25) -> None:
    try:
        guilds = list(client.guilds)
    except Exception:
        guilds = []

    if not guilds:
        print("No guilds visible yet (is the bot added to your server?).")
        return

    print("Accessible text channels (pick one and set CHANNEL_ID to its id):")
    for guild in guilds:
        print(f"- Server: {guild.name} (id={guild.id})")
        shown = 0
        for ch in getattr(guild, "text_channels", []):
            perms = ch.permissions_for(guild.me) if getattr(guild, "me", None) else None
            can_send = bool(perms and perms.view_channel and perms.send_messages)
            if not can_send:
                continue
            print(f"  - #{ch.name}: {ch.id}")
            shown += 1
            if shown >= limit_per_guild:
                print("  - ... (more channels hidden)")
                break

def load_seen() -> Set[str]:
    try:
        with open(SEEN_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()
    except Exception:
        return set()

def save_seen(seen: Set[str]) -> None:
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(seen)[-2000:], f, indent=2)  # keep last ~2000 IDs
        f.write("\n")

def entry_timestamp(entry: Dict) -> float:
    # Prefer published/updated parsed times; fall back to "now" so item doesn't get dropped.
    for key in ("published_parsed", "updated_parsed"):
        tm = entry.get(key)
        if tm:
            try:
                return float(time.mktime(tm))
            except Exception:
                pass
    return time.time()

async def fetch_feed(session: aiohttp.ClientSession, name: str, url: str) -> List[Dict]:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            # Some sites block programmatic access to RSS (403). Skip those feeds gracefully.
            if resp.status == 403:
                print(f"  ✗ Feed blocked (403) for {name}: {url}")
                return []
            resp.raise_for_status()
            data = await resp.read()

        parsed = feedparser.parse(data)
        items = []
        for entry in parsed.entries:
            # Some feeds (e.g. Hugging Face) may omit <link>; fall back to id/guid.
            link = entry.get("link") or entry.get("id")
            title = entry.get("title", "(no title)")
            # "id" is not always present; fall back to link.
            uid = entry.get("id") or link
            if not uid or not link:
                continue
            items.append(
                {
                    "source": name,
                    "uid": uid,
                    "title": title,
                    "link": link,
                    "ts": entry_timestamp(entry),
                }
            )
        return items
    except Exception as e:
        print(f"  ✗ Error fetching {name}: {e}")
        raise

def to_embed(item: Dict) -> discord.Embed:
    embed = discord.Embed(title=item["title"], url=item["link"])
    embed.set_footer(text=item["source"])
    return embed

def to_text(item: Dict) -> str:
    # Plain-text message, kept under Discord's 2000 char limit.
    source = item.get("source", "Unknown")
    title = item.get("title", "(no title)")
    link = item.get("link", "")
    msg = f"[{source}] {title}\n{link}".strip()
    if len(msg) <= 2000:
        return msg

    # Truncate title first, keep link if possible.
    reserved = len(f"[{source}] \n") + len(link)
    max_title = max(0, 2000 - reserved)
    if max_title <= 0:
        return link[:2000]
    if len(title) > max_title:
        title = title[: max(0, max_title - 1)].rstrip() + "…"
    return f"[{source}] {title}\n{link}".strip()[:2000]

def to_batch_text(items: List[Dict]) -> str:
    # Build a compact, plain-text batch message within Discord's 2000 char limit.
    lines: List[str] = []
    for item in items:
        source = item.get("source", "Unknown")
        title = (item.get("title") or "(no title)").replace("\n", " ").strip()
        link = item.get("link", "").strip()
        # One-line per item to keep it compact
        lines.append(f"- [{source}] {title} — {link}".strip())

    header = "AI news updates:\n"
    out = header
    for line in lines:
        # +1 for newline
        if len(out) + len(line) + 1 > 2000:
            break
        out += line + "\n"
    return out.rstrip()[:2000]

async def do_poll_and_post():
    """Core polling logic that can be called directly or as a task"""
    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Checking feeds for new posts...")
    
    channel = client.get_channel(CHANNEL_ID)
    if channel is None:
        # If the cache missed, fetch explicitly.
        try:
            channel = await client.fetch_channel(CHANNEL_ID)
            print(f"✓ Fetched channel: {channel.name if channel else 'Unknown'}")
        except discord.NotFound:
            print("ERROR: CHANNEL_ID was not found (Unknown Channel).")
            print("Fix: enable Developer Mode in Discord, right-click your target text channel, 'Copy Channel ID',")
            print("then set that value as CHANNEL_ID in your .env (NOT your bot/application ID).")
            print_accessible_text_channels()
            poll_and_post.stop()
            return
        except discord.Forbidden:
            print("ERROR: Bot cannot access that channel (Forbidden).")
            print("Fix: make sure the bot is in the server and has permission to View Channel + Send Messages (and Embed Links).")
            print_accessible_text_channels()
            poll_and_post.stop()
            return

    seen = load_seen()
    print(f"✓ Loaded {len(seen)} previously seen items")
    candidates: Dict[str, Dict] = {}

    # Use a browser-like UA; some RSS endpoints block unknown bots.
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) ai-news-bot/1.0",
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        results = await asyncio.gather(
            *(fetch_feed(session, name, url) for name, url in FEEDS),
            return_exceptions=True,
        )

    feed_count = 0
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            print(f"  ✗ Error fetching {FEEDS[i][0]}: {res}")
            continue
        feed_count += len(res)
        for item in res:
            uid = item.get("uid")
            if not uid or uid in seen:
                continue
            # De-dupe within a single poll; keep the newest timestamp for that uid.
            prev = candidates.get(uid)
            if prev is None or float(item.get("ts", 0)) > float(prev.get("ts", 0)):
                candidates[uid] = item
    
    print(f"  ✓ Fetched {feed_count} total items from {len([r for r in results if not isinstance(r, Exception)])} feeds")

    # Choose the newest items across ALL feeds (not just the last feed),
    # then post oldest-first for readability.
    all_items_newest_first = sorted(candidates.values(), key=lambda x: float(x.get("ts", 0)), reverse=True)

    # Enforce source diversity so one platform doesn't dominate.
    by_source: Dict[str, List[Dict]] = {}
    for it in all_items_newest_first:
        by_source.setdefault(it.get("source", "Unknown"), []).append(it)

    picked_newest_first: List[Dict] = []
    per_source_count: Dict[str, int] = {k: 0 for k in by_source.keys()}

    # Pass 1: round-robin pick across sources up to MAX_PER_SOURCE_PER_RUN each.
    made_progress = True
    while made_progress and len(picked_newest_first) < MAX_POSTS_PER_RUN:
        made_progress = False
        for source, items in by_source.items():
            if len(picked_newest_first) >= MAX_POSTS_PER_RUN:
                break
            if per_source_count[source] >= MAX_PER_SOURCE_PER_RUN:
                continue
            if not items:
                continue
            picked_newest_first.append(items.pop(0))
            per_source_count[source] += 1
            made_progress = True

    # Pass 2: if we still have room, fill with the newest remaining items regardless of source.
    if len(picked_newest_first) < MAX_POSTS_PER_RUN:
        remaining: List[Dict] = []
        for items in by_source.values():
            remaining.extend(items)
        remaining.sort(key=lambda x: float(x.get("ts", 0)), reverse=True)
        picked_newest_first.extend(remaining[: (MAX_POSTS_PER_RUN - len(picked_newest_first))])

    # Post oldest-first for readability.
    new_items = list(reversed(picked_newest_first))

    if new_items:
        print(f"  ✓ Found {len(new_items)} new items to post")
        # Pre-check posting permissions when possible.
        try:
            guild = getattr(channel, "guild", None)
            me = getattr(guild, "me", None) if guild else None
            if guild and me:
                perms = channel.permissions_for(me)
                if not (perms.view_channel and perms.send_messages):
                    print("ERROR: Missing permissions to post in this channel.")
                    print("Fix: grant the bot/role: View Channel + Send Messages (and Send Messages in Threads if applicable).")
                    poll_and_post.stop()
                    return
        except Exception:
            # If we can't compute perms (e.g., DM channel), we'll rely on send() error handling.
            pass

        if BATCH_POST:
            try:
                await channel.send(to_batch_text(new_items))
                for item in new_items:
                    seen.add(item["uid"])
                print(f"    → Posted batch of {len(new_items)} items")
            except discord.Forbidden:
                print("ERROR: Discord refused the send (Missing Permissions, code 50013).")
                print("Fix: in the target channel, grant the bot: View Channel + Send Messages.")
                print("Also check: if it's a thread, grant Send Messages in Threads.")
                poll_and_post.stop()
                return
        else:
            for item in new_items:
                try:
                    await channel.send(to_text(item))
                    print(f"    → Posted: {item['title'][:50]}...")
                    seen.add(item["uid"])
                    await asyncio.sleep(POST_DELAY_SECONDS)
                except discord.Forbidden:
                    print("ERROR: Discord refused the send (Missing Permissions, code 50013).")
                    print("Fix: in the target channel, grant the bot: View Channel + Send Messages.")
                    print("Also check: if it's a thread, grant Send Messages in Threads.")
                    poll_and_post.stop()
                    return
        save_seen(seen)
        print(f"  ✓ Saved {len(seen)} seen items")
    else:
        print("  ✓ No new items found")

@tasks.loop(minutes=POLL_MINUTES, count=None)
async def poll_and_post():
    await do_poll_and_post()

@client.event
async def on_ready():
    print("-" * 50)
    print(f"✓ Successfully logged in as {client.user} (id={client.user.id})")
    print(f"✓ Bot is ready and monitoring {len(FEEDS)} feeds")
    print(f"✓ Will check for new posts every {POLL_MINUTES} minutes")
    # Sync slash commands
    try:
        if GUILD_ID:
            await tree.sync(guild=discord.Object(id=GUILD_ID))
            print(f"✓ Slash commands synced to guild {GUILD_ID} (instant)")
        else:
            await tree.sync()
            print("✓ Slash commands synced globally (may take a while to appear)")
    except Exception as e:
        print(f"ERROR: Failed to sync slash commands: {e}")
    print("-" * 50)
    if CHANNEL_ID == getattr(client.user, "id", None):
        print("ERROR: CHANNEL_ID is set to the bot's user/application id, not a text channel id.")
        print("Fix: enable Developer Mode in Discord, right-click the channel, Copy Channel ID, and update CHANNEL_ID in .env.")
        print_accessible_text_channels()
        return
    if not poll_and_post.is_running():
        poll_and_post.start()
        print("✓ Polling task started")
        print("Running initial feed check...")
        async def _startup_poll():
            try:
                await do_poll_and_post()
            except Exception as e:
                print(f"ERROR: Initial poll failed: {e}")
        asyncio.create_task(_startup_poll())

def main():
    print("=" * 50)
    print("Starting AI News Bot...")
    print("=" * 50)
    
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN is missing from .env file")
        raise SystemExit("Missing DISCORD_TOKEN")
    
    if CHANNEL_ID == 0:
        print("ERROR: CHANNEL_ID is missing or invalid in .env file")
        raise SystemExit("Missing CHANNEL_ID")
    
    print(f"Channel ID: {CHANNEL_ID}")
    print(f"Poll interval: {POLL_MINUTES} minutes")
    print(f"Feeds to monitor: {len(FEEDS)}")
    print("Connecting to Discord...")
    print("-" * 50)
    
    try:
        client.run(DISCORD_TOKEN)
    except discord.LoginFailure:
        print("ERROR: Invalid Discord token. Please check your DISCORD_TOKEN in .env")
        raise
    except Exception as e:
        print(f"ERROR: Failed to start bot: {e}")
        raise

if __name__ == "__main__":
    main()
