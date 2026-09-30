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


## Video performance on Render

Each accepted media request emits one INFO line prefixed `video_performance`
followed by JSON. It includes `platform`, `route` (`pending`, `cache`, `direct`,
`normalized`), `result`, `failed_stage`, `bytes_sent`, `total_ms`, and `stage_ms`.
Stage times are milliseconds measured with a monotonic clock: `queue`,
`download`, `probe`, `normalize`, `upload`, and `cache_send`. Stages not run are
zero. `bytes_sent` is the local media size for an upload; it is null for cached
sends or failures before the size is known. `pending` means preparation failed
before the route could be selected. `result` describes delivery of the video;
follow-up messages are not part of that result. `total_ms` includes the handler's
work after URL validation, including follow-up messages and cleanup, but excludes
membership checks. Failed cached sends retain their timing when download fallback
succeeds. Summaries contain no URL, user ID, token, or exception text.

Run `python -m unittest discover -s tests -v`. Then on Render, send public TikTok
and Instagram videos covering compatible portrait files and files needing
normalization. Repeat the same URLs to exercise the cache. Check received audio
and 720x1280 (9:16) geometry. Compare median and P95 stage times separately by
platform and route, using enough requests to make percentiles meaningful. Keep
cache hits separate from new downloads and compare similar durations/file sizes.
The direct route should have zero normalization time. These logs measure latency;
they do not establish platform reliability or faster production delivery until
real requests have been observed.
