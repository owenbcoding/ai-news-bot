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

## Auto-start on boot (Raspberry Pi)

### Option A: systemd (recommended for Pi)

A native systemd service is more reliable than PM2 on a headless Raspberry Pi:

```bash
sudo cp /home/kali/discordbots/ai-news-bot/ai-news-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ai-news-bot
sudo systemctl start ai-news-bot
```

Useful commands:

- `sudo systemctl status ai-news-bot` – see if running
- `journalctl -u ai-news-bot -f` – view logs
- `sudo systemctl restart ai-news-bot` – restart

### Option B: PM2

1. Start the bot and save:

```bash
cd /home/kali/discordbots/ai-news-bot
pm2 start ecosystem.config.cjs
pm2 save
```

2. **Critical:** Run the exact command that `pm2 startup` prints. For example:

```bash
pm2 startup
# Then run the output, e.g.:
sudo env PATH=$PATH:/usr/bin /usr/local/lib/node_modules/pm2/bin/pm2 startup systemd -u kali --hp /home/kali
```

- `pm2 status` / `pm2 logs ai-news-bot` – check status and logs

If PM2 still doesn't start after reboot, use Option A (systemd) instead.

## Test the bot

Once the bot is running, use the slash command:

- `/ping` → responds with `pong`

## Behavior / settings

All settings are configured via `.env`:

- **POLL_MINUTES**: how often to fetch feeds (e.g. `30` for every 30 minutes)
- **MAX_POSTS_PER_RUN** (in code): max links per poll (defaults to 5)
- **BATCH_POST**: `1` sends one message per poll with multiple links (recommended)
- **POST_DELAY_SECONDS**: delay between messages if `BATCH_POST=0`
- **MAX_PER_SOURCE_PER_RUN**: limit items per source per poll (defaults to 1 for diversity)

## Notes

- The bot stores seen items in `seen.json` to avoid reposting. This file is ignored by git.
- Some feeds may block RSS access (HTTP 403). The bot will skip those feeds.
