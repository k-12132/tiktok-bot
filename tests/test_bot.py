import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from contextlib import ExitStack

import bot
import tempfile
import subprocess
import unittest
from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe

from bot import BotStore, get_supported_platform, invite_link, is_ready_portrait_video


class BotStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = BotStore(str(Path(self.temp_dir.name) / "bot.sqlite3"))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_referral_is_counted_only_once(self):
        self.store.register_user(100)
        self.assertTrue(self.store.register_user(200, 100))
        self.assertFalse(self.store.register_user(200, 100))
        self.assertEqual(self.store.referral_count(100), 1)

    def test_self_referral_is_ignored(self):
        self.store.register_user(100, 100)
        self.assertEqual(self.store.referral_count(100), 0)

    def test_cache_and_statistics(self):
        url = "https://www.tiktok.com/@demo/video/123"
        self.store.register_user(100)
        self.store.cache_file(url, "telegram-file-id", "TikTok")
        self.assertEqual(self.store.cached_file_id(url), "telegram-file-id")
        self.store.record_download(100, cached=True)
        stats = self.store.statistics()
        self.assertEqual(stats["users"], 1)
        self.assertEqual(stats["downloads"], 1)
        self.assertEqual(stats["cache_hits"], 1)
        self.assertEqual(stats["cached_videos"], 1)


class PlatformTests(unittest.TestCase):
    def test_supported_platforms(self):
        self.assertEqual(
            get_supported_platform("https://www.tiktok.com/@demo/video/123"),
            "TikTok",
        )
        self.assertEqual(
            get_supported_platform("https://www.instagram.com/reel/abc/"),
            "Instagram",
        )
        self.assertIsNone(get_supported_platform("https://example.com/video"))

    def test_personal_invite_link(self):
        self.assertEqual(
            invite_link("demo_bot", 12345),
            "https://t.me/demo_bot?start=ref_12345",
        )


class PortraitFastPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_compatible_portrait_skips_encoding_and_landscape_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            for size, expected in (("720x1280", True), ("1280x720", False)):
                path = Path(directory) / f"{size}.mp4"
                subprocess.run(
                    [get_ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i",
                     f"color=c=black:s={size}:d=0.1", "-c:v", "libx264",
                     "-pix_fmt", "yuv420p", "-y", str(path)],
                    check=True, capture_output=True,
                )
                self.assertEqual(await is_ready_portrait_video(path), expected)


class VideoPerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def run_request(self, *, ready=True, cached=False, failure=None,
                          invalid_cache=False, cancelled=False):
        self.now = 0.0
        progress = SimpleNamespace(delete=AsyncMock())
        store = Mock()
        store.cached_file_id.return_value = "cached-id" if cached else None
        message = SimpleNamespace(
            text="https://www.instagram.com/reel/private-query/?secret=hidden",
            reply_text=AsyncMock(return_value=progress),
        )
        update = SimpleNamespace(effective_message=message,
                                 effective_user=SimpleNamespace(id=123456))
        context = SimpleNamespace(bot=SimpleNamespace(username="demo_bot"))
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            media = Path(directory) / "video.mp4"
            media.write_bytes(b"media")
            async def download(*args):
                self.now += 2
                if failure == "download":
                    raise RuntimeError("download error")
                return media
            async def probe(*args):
                self.now += 0.25
                return ready
            async def normalize(*args):
                self.now += 3
                return media
            async def send(*args, **kwargs):
                if kwargs["video"] == "cached-id":
                    self.now += 0.5
                    if invalid_cache:
                        raise bot.BadRequest("invalid file")
                else:
                    self.now += 4
                if cancelled:
                    raise asyncio.CancelledError()
                if failure == "upload":
                    raise RuntimeError("upload error")
                return SimpleNamespace(video=SimpleNamespace(file_id="sent-id"))
            message.reply_video = AsyncMock(side_effect=send)
            for name, value in {
                "STORE": store,
                "DOWNLOAD_SEMAPHORE": asyncio.Semaphore(1),
                "get_missing_channels": AsyncMock(return_value=([], False)),
                "download_video": AsyncMock(side_effect=download),
                "is_ready_portrait_video": AsyncMock(side_effect=probe),
                "normalize_video_for_snapchat": AsyncMock(side_effect=normalize),
            }.items():
                stack.enter_context(patch.object(bot, name, value))
            stack.enter_context(patch.object(bot.time, "perf_counter", side_effect=lambda: self.now))
            log = stack.enter_context(patch.object(bot.logger, "info"))
            stack.enter_context(patch.object(bot.logger, "exception"))
            if cancelled:
                with self.assertRaises(asyncio.CancelledError):
                    await bot.download_social_video(update, context)
            else:
                await bot.download_social_video(update, context)
            records = [call.args[1] for call in log.call_args_list
                       if call.args[0] == "video_performance %s"]
            self.assertEqual(len(records), 1)
            self.assertNotIn("private-query", records[0])
            self.assertNotIn("123456", records[0])
            self.assertNotIn("secret", records[0])
            self.assertEqual(bot.DOWNLOAD_SEMAPHORE._value, 1)
            self.normalizer_calls = bot.normalize_video_for_snapchat.await_count
            self.download_calls = bot.download_video.await_count
            self.store = store
            return json.loads(records[0])

    async def test_direct_stage_timings(self):
        result = await self.run_request()
        self.assertEqual(result["route"], "direct")
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["platform"], "Instagram")
        self.assertEqual(result["bytes_sent"], 5)
        self.assertEqual(result["stage_ms"], dict(queue=0, download=2000,
                         probe=250, normalize=0, upload=4000, cache_send=0))
        self.assertEqual(result["total_ms"], 6250)
        self.assertEqual(self.normalizer_calls, 0)

    async def test_normalized_route(self):
        result = await self.run_request(ready=False)
        self.assertEqual(result["route"], "normalized")
        self.assertEqual(result["stage_ms"]["normalize"], 3000)
        self.assertEqual(result["total_ms"], 9250)
        self.assertEqual(self.normalizer_calls, 1)

    async def test_cache_does_not_download_or_upload(self):
        result = await self.run_request(cached=True)
        self.assertEqual(result["route"], "cache")
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["stage_ms"]["cache_send"], 500)
        self.assertEqual(result["stage_ms"]["upload"], 0)
        self.assertIsNone(result["bytes_sent"])
        self.assertEqual(self.download_calls, 0)

    async def test_upload_failure_is_measured(self):
        result = await self.run_request(failure="upload")
        self.assertEqual(result["result"], "failed")
        self.assertEqual(result["failed_stage"], "upload")
        self.assertEqual(result["stage_ms"]["upload"], 4000)
        self.store.record_failure.assert_called_once()

    async def test_download_failure_releases_slot(self):
        result = await self.run_request(failure="download")
        self.assertEqual(result["failed_stage"], "download")
        self.assertEqual(result["stage_ms"]["download"], 2000)
        self.assertEqual(result["stage_ms"]["upload"], 0)

    async def test_invalid_cache_falls_back_with_one_summary(self):
        result = await self.run_request(cached=True, invalid_cache=True)
        self.assertEqual(result["route"], "direct")
        self.assertEqual(result["result"], "success")
        self.assertIsNone(result["failed_stage"])
        self.assertEqual(result["stage_ms"]["cache_send"], 500)
        self.store.remove_cached_file.assert_called_once()

    async def test_cancellation_still_logs_failure(self):
        result = await self.run_request(cancelled=True)
        self.assertEqual(result["result"], "failed")
        self.assertEqual(result["failed_stage"], "upload")


if __name__ == "__main__":
    unittest.main()

