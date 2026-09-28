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


if __name__ == "__main__":
    unittest.main()
