# Telegram TikTok Bot

Telegram bot that verifies channel membership and downloads public TikTok videos.

## Deploy on Render

1. Create a Blueprint from this repository. Render reads `render.yaml` from the repository root.
2. Set the secret environment variable `BOT_TOKEN` to the current token from BotFather.
3. Add the bot as an administrator in every channel listed in `CHANNELS` in `bot.py`.
4. Deploy the worker and confirm the logs contain no startup errors.

Never commit the bot token. If an old token was exposed, revoke it in BotFather before deployment.
