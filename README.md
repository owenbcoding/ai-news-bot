# ai-news-bot

A Discord RSS bot for AI news that uses a shared posting pipeline, posts articles as embeds, dedupes by canonical URL hash, and keeps a permanent append-only archive of every posted link.

## Structure

- `news_core.py`: shared logic for feed fetching, URL normalization, dedupe, archive storage, embed formatting, and Discord posting
- `ai_news_bot.py`: AI-specific feed list and channel configuration
- `bot.py`: compatibility entrypoint so `python bot.py` still works
- `verify.py`: verification script for canonical URL dedupe, archive persistence, round-robin selection, and live feed fetches

## Stored Per Article

Each posted article is saved with:

- `source`
- `title`
- `url`
- `canonical_url`
- `published_at`
- `posted_at`
- `discord_message_id`

The bot keeps links in 3 places:

- Discord embed message
- append-only archive file at `state/ai_news_archive.jsonl`
- dedupe index at `state/ai_news_dedupe_index.json`

## Discord Output

Each article is posted as an embed with:

- clickable embed title
- cleaned summary in the description
- `Read article` field with the full URL
- footer containing source and published date

Optionally, the bot can also post a plain-text digest after the embeds by setting `POST_TEXT_DIGEST=1`.

## Setup

1. Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Create your `.env` file from the template:

```bash
cp .env.example .env
```

3. Fill in `.env`:

- `DISCORD_TOKEN`: your bot token
- `CHANNEL_ID`: default Discord text channel ID
- `AI_CHANNEL_ID`: optional dedicated AI news channel ID; falls back to `CHANNEL_ID`
- `GUILD_ID`: optional guild ID for faster slash command sync
- `POST_TIMES_UTC`: comma-separated UTC posting times, for example `09:00,17:00`
- `MAX_POSTS_PER_RUN`: max articles posted each cycle
- `MAX_PER_SOURCE_PER_RUN`: max articles per source each cycle
- `POST_TEXT_DIGEST=1`: optional plain-text digest message after embeds

By default, the bot posts twice a day at `09:00 UTC` and `17:00 UTC`.

## Run

Either command works:

```bash
source .venv/bin/activate
python bot.py
```

```bash
source .venv/bin/activate
python ai_news_bot.py
```

## Slash Command

The bot exposes:

- `/latestlinks`: prints the latest saved links from the archive

## Verify Bot Logic

Run the verification script before deploying:

```bash
source .venv/bin/activate
python verify.py
```

Expected output: `All checks passed`

## Run 24/7 with Docker

Docker persists archive and dedupe data in `/app/state`. See **[DEPLOY.md](DEPLOY.md)** for the Raspberry Pi deployment guide.

```bash
docker compose up -d --build
docker compose logs -f
docker compose ps
```

## Notes

- Feed URLs remain in the AI bot config and are unchanged from the original bot.
- Dedupe uses `sha256(canonical_url)` instead of feed GUIDs.
- The archive is append-only, so old posted links stay available for later lookup or reposting.
- Some feeds may intermittently fail or return HTTP errors; the bot logs and skips them for that cycle.