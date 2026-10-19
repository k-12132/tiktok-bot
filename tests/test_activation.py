import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
import activation
import bot

SECRET = 'test-only-secret-' * 3

class ReceiptTests(unittest.TestCase):
    def test_bound_to_user_and_secret_and_length(self):
        token = activation.issue(SECRET, 7729580704)
        self.assertLessEqual(len(token), 64)
        self.assertTrue(activation.verify(SECRET, token, 7729580704))
        self.assertFalse(activation.verify(SECRET, token, 42))
        self.assertFalse(activation.verify('other-secret-' * 4, token, 7729580704))
        self.assertFalse(activation.verify('', token, 7729580704))
        self.assertFalse(activation.verify(SECRET, token + 'x', 7729580704))

    def test_expired_and_future_receipts(self):
        with patch('activation.time.time', return_value=1800000000):
            token = activation.issue(SECRET, 42)
        for now in (1800000601, 1799999999):
            with patch('activation.time.time', return_value=now):
                self.assertFalse(activation.verify(SECRET, token, 42))

class GateTests(unittest.IsolatedAsyncioTestCase):
    async def test_unactivated_user_never_reaches_cached_download(self):
        store = Mock()
        store.snapchat_activated.return_value = False
        message = NS(text='https://www.instagram.com/reel/test/', reply_text=AsyncMock())
        update = NS(effective_message=message, effective_user=NS(id=42), callback_query=None)
        with patch.object(bot, 'STORE', store), patch.object(bot, 'get_missing_channels', AsyncMock(return_value=([], False))):
            await bot.download_social_video(update, NS(bot=Mock()))
        store.cached_file_id.assert_not_called()
        message.reply_text.assert_awaited_once()

    async def test_receipt_activates_only_its_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = bot.BotStore(str(Path(tmp) / 'db'))
            token = activation.issue(SECRET, 42)
            for user_id, expected in ((43, False), (42, True)):
                update = NS(effective_user=NS(id=user_id), effective_message=NS(reply_text=AsyncMock()), callback_query=None)
                with patch.object(bot, 'STORE', store), patch.object(bot, 'ACTIVATION_SECRET', SECRET):
                    await bot.start(update, NS(args=[token]))
                self.assertEqual(store.snapchat_activated(user_id), expected)
            self.assertTrue(bot.BotStore(str(Path(tmp) / 'db')).snapchat_activated(42))
