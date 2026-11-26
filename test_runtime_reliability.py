import ast
import unittest
from pathlib import Path

class RuntimeTests(unittest.TestCase):
    def test_photo_guard_and_polling(self):
        source = Path("bot.py").read_text()
        ast.parse(source)
        self.assertIn('/photo/', source)
        self.assertIn('drop_pending_updates=False', source)
        self.assertIn('isinstance(error, NetworkError)', source)


from unittest.mock import AsyncMock, patch

class PortraitFastPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_portrait_resolutions(self):
        import bot
        for dimensions, expected in [('720x1280', True), ('1080x1920', True), ('540x960', True), ('1280x720', False)]:
            proc = AsyncMock()
            proc.communicate.return_value = (b'', f'Stream #0:0: Video: h264, yuv420p, {dimensions}\nStream #0:1: Audio: aac'.encode())
            with patch.object(bot.asyncio, 'create_subprocess_exec', return_value=proc):
                self.assertEqual(await bot.is_ready_portrait_video(Path('sample.mp4')), expected)
