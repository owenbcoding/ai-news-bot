# Deploy ai-news-bot on Raspberry Pi 24/7

This guide walks through running the bot in Docker on a Raspberry Pi with automatic restart and persistent state.

## Prerequisites

- Raspberry Pi 4 or 5 (or Pi 3) with Raspberry Pi OS or similar
- Docker and Docker Compose installed
- Discord bot token and channel ID

## 1. Install Docker on the Pi

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Log out and back in (or `newgrp docker`) so the `docker` group takes effect. Then install the Compose plugin:

```bash
sudo apt-get update && sudo apt-get install -y docker-compose-plugin
```

Verify:

```bash
docker --version
docker compose version
```

## 2. Clone and Configure

```bash
git clone <your-repo-url> ai-news-bot
cd ai-news-bot
```

Create your `.env` from the example:

```bash
cp .env.example .env
nano .env   # or your preferred editor
```

Set at minimum:

| Variable | Description |
|----------|-------------|
| `DISCORD_TOKEN` | Bot token from [Discord Developer Portal](https://discord.com/developers/applications) |
| `CHANNEL_ID` | Text channel ID (Developer Mode → right-click channel → Copy ID) |
| `POLL_MINUTES` | Poll interval (default `180` = 3 hours) |
| `MAX_POSTS_PER_RUN` | Max articles per cycle (default `12`) |
| `MAX_PER_SOURCE_PER_RUN` | Max per feed per cycle (default `3`) |

`SEEN_PATH` is overridden in Docker to `/app/state/seen.json` so deduplication persists across restarts.

## 3. Build and Run

```bash
docker compose up -d --build
```

First build on a Pi can take a few minutes. Subsequent builds are faster thanks to layer caching.

## 4. Verify It's Running

```bash
docker compose ps
docker compose logs -f
```

You should see:

- `Logged in as ...`
- `Watching 6 feeds`
- `Polling every X minutes`

On each poll cycle: either `[Info] No new items to post` or `[Posted] Source: Title...`.

## 5. Auto-Start After Reboot

The `restart: unless-stopped` policy in `docker-compose.yml` keeps the container running and restarts it after crashes. Docker itself starts on boot on Raspberry Pi OS.

To confirm after a reboot:

```bash
sudo reboot
# after reconnect
docker compose -f /path/to/ai-news-bot/docker-compose.yml ps
```

Or run the bot from a directory that’s easy to remember:

```bash
cd ~/ai-news-bot
docker compose ps
```

## 6. Useful Commands

| Command | Description |
|---------|-------------|
| `docker compose logs -f` | Follow logs |
| `docker compose restart` | Restart the container |
| `docker compose down` | Stop and remove |
| `docker compose up -d` | Start in background |

## Architecture Notes

- **Base image**: `python:3.12-slim` is multi-arch. On Pi 4/5 (arm64) or Pi 3 (armv7), Docker pulls the correct image.
- **State**: `seen.json` is stored in a Docker volume `ai_news_bot_state` at `/app/state/`.
- **Logic**: All RSS polling, round-robin distribution, and Discord posting runs inside the container; no code changes needed for Pi.
