# ai-news-bot

A Discord bot that posts AI news links (plain text) from multiple RSS feeds into a channel on a schedule.

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

- **DISCORD_TOKEN**: your bot token (Discord Developer Portal → your app → Bot)
- **CHANNEL_ID**: the *text channel ID* you want the bot to post in  
  (Discord Developer Mode ON → right-click channel → Copy Channel ID)
- **GUILD_ID** (recommended): your server ID for instant `/ping` availability  
  (right-click server icon → Copy Server ID)

## Run

```bash
source .venv/bin/activate
python bot.py
```

## Run 24/7 with Docker (recommended for Raspberry Pi)

This setup keeps the bot running continuously, restarts automatically after reboots/crashes, and persists `seen.json` so links are not reposted. See **[DEPLOY.md](DEPLOY.md)** for a full Raspberry Pi deployment guide.

### 1) Prerequisites on Pi

Install Docker + Docker Compose plugin, then add your user to `docker` group.

### 2) Configure env

```bash
cp .env.example .env
```

Set at minimum:

- `DISCORD_TOKEN`
- `CHANNEL_ID`
- `POLL_MINUTES`
- `MAX_POSTS_PER_RUN` (default `12`)
- `MAX_PER_SOURCE_PER_RUN` (default `3`)

### 3) Build and start

```bash
docker compose up -d --build
```

### 4) Useful operations

```bash
docker compose ps
docker compose logs -f
docker compose restart
docker compose pull && docker compose up -d --build
```

### 5) Verify auto-start after reboot

```bash
sudo reboot
# after reconnect
docker compose ps
```

Container should come back automatically because `restart: unless-stopped` is set in `docker-compose.yml`.

## Verify bot logic (before deploying)

Run the verification script to validate feed fetching, deduplication, and round-robin logic (no Discord token required):

```bash
source .venv/bin/activate   # or: pip install -r requirements.txt
python verify.py
```

Expected output: `All checks passed`. This confirms the core logic works before deploying.

## Verify bot behavior (when running)

When running, logs should show:

- `Logged in as ...`
- `Watching 6 feeds`
- `Polling every X minutes`

Then on each cycle:

- `[Info] No new items to post`, or
- several `[Posted] Source: Title...` lines

## Behavior / settings (current logic)

All settings are configured via `.env`:

- **POLL_MINUTES**: how often feeds are fetched (e.g. `30`)
- **MAX_POSTS_PER_RUN**: hard cap of articles posted per poll cycle (default `12`)
- **MAX_PER_SOURCE_PER_RUN**: cap per source inside each cycle (default `3`)
- **SEEN_PATH**: path to dedupe file (`seen.json`) used to avoid reposting

## Notes

- The bot stores seen items in `seen.json` to avoid reposting. This file is ignored by git.
- Some feeds may block RSS access (HTTP 403). The bot will skip those feeds.
- In Docker, dedupe state is persisted in a named volume mounted at `/app/state`.

## Article count sanity check after deployment

To confirm posting counts match config:

1. Set short poll interval temporarily, e.g. `POLL_MINUTES=5`.
2. Keep `MAX_POSTS_PER_RUN=12` and `MAX_PER_SOURCE_PER_RUN=3`.
3. Watch logs for one poll:
   - Count `[Posted]` lines in that cycle: must be `<= MAX_POSTS_PER_RUN`.
   - For each source name, count lines: must be `<= MAX_PER_SOURCE_PER_RUN`.
4. Set `POLL_MINUTES` back to production value (e.g. `180`) and restart:

```bash
docker compose up -d
```