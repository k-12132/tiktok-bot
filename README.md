# Telegram TikTok & Instagram Bot

Telegram bot that verifies channel membership and downloads public TikTok and
Instagram videos. It includes personal referral links, admin statistics, and a
Telegram `file_id` cache that avoids uploading the same URL more than once.

## Deploy on Render

1. Create a Blueprint from this repository. Render reads `render.yaml` from the repository root.
2. Set the secret environment variable `BOT_TOKEN` to the current token from BotFather.
3. Set `ADMIN_USER_IDS` to the Telegram numeric user ID of each bot administrator.
   Multiple IDs can be separated with commas.
4. Add the bot as an administrator in every channel listed in `CHANNELS` in `bot.py`.
5. Deploy the worker and confirm the logs contain no startup errors.

## Growth commands

- `/invite` shows the user's personal referral link and successful referral count.
- `/stats` shows private bot statistics to IDs configured in `ADMIN_USER_IDS`.
- `/id` shows the current user's numeric Telegram ID for administrator setup.

By default, SQLite data is stored at `/tmp/tiktok-bot.sqlite3`. Render's filesystem
is ephemeral, so statistics and cached file IDs reset after a restart or deploy.
Set `BOT_DB_PATH` to a persistent-disk path if durable history is required later.

Never commit the bot token. If an old token was exposed, revoke it in BotFather before deployment.
