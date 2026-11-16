import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import bot
from telegram import Update, User
from telegram.error import BadRequest, NetworkError


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_membership_retries_network_but_not_bad_request(self):
        client = NS(get_chat_member=AsyncMock(side_effect=[NetworkError('offline'), NS(status='member')]))
        with patch.object(bot.asyncio, 'sleep', AsyncMock()) as sleep:
            self.assertEqual(await bot.get_missing_channels(client, 42), ([], False))
            self.assertEqual(client.get_chat_member.await_count, 2)
            sleep.assert_awaited_once_with(0.5)
        client.get_chat_member = AsyncMock(side_effect=BadRequest('chat not found'))
        self.assertEqual(await bot.get_missing_channels(client, 42), ([], True))
        self.assertEqual(client.get_chat_member.await_count, 1)

    async def test_exhausted_membership_retry_stays_closed(self):
        client = NS(get_chat_member=AsyncMock(side_effect=NetworkError('offline')))
        with patch.object(bot.asyncio, 'sleep', AsyncMock()):
            self.assertEqual(await bot.get_missing_channels(client, 42), ([], True))
        self.assertEqual(client.get_chat_member.await_count, 3)

    async def test_busy_user_is_rejected_and_cancellation_releases_user(self):
        entered = asyncio.Event()
        async def processing(*args):
            entered.set()
            await asyncio.Event().wait()
        message = NS(text='https://www.tiktok.com/@demo/video/123', reply_text=AsyncMock())
        update = NS(effective_user=NS(id=42), effective_message=message)
        with patch.object(bot, 'process_social_video', processing), patch.object(bot, 'ACTIVE_DOWNLOAD_USERS', set()):
            task = asyncio.create_task(bot.download_social_video(update, NS()))
            await entered.wait()
            await bot.download_social_video(update, NS())
            message.reply_text.assert_awaited_once()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(bot.ACTIVE_DOWNLOAD_USERS, set())

    async def test_admission_limit_rejects_before_membership_or_download(self):
        update = NS(effective_user=NS(id=99), effective_message=NS(text='video', reply_text=AsyncMock()))
        with patch.object(bot, 'ACTIVE_DOWNLOAD_USERS', set(range(bot.MAX_PENDING_REQUESTS))), patch.object(bot, 'process_social_video', AsyncMock()) as work:
            await bot.download_social_video(update, NS())
            work.assert_not_awaited()
        update.effective_message.reply_text.assert_awaited_once()

    async def test_commands_run_while_media_handler_is_waiting(self):
        entered, release, command_ran = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def media(*args):
            entered.set()
            await release.wait()
        async def command(*args):
            command_ran.set()
        proxy_env = {key: '' for key in ('ALL_PROXY', 'all_proxy', 'HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy')}
        with patch.dict(os.environ, proxy_env), patch.object(bot, 'BOT_TOKEN', '123456:test'), patch.object(bot, 'download_social_video', media), patch.object(bot, 'id_command', command):
            app = bot.build_application()
        app._initialized = True
        app.bot._bot_user = User(123456, 'Test', True, username='test_bot')
        await app.start()
        def update(text, uid):
            data = {'update_id': uid, 'message': {'message_id': uid, 'date': 1,
                'chat': {'id': 42, 'type': 'private'}, 'from': {'id': 42, 'first_name': 'Test', 'is_bot': False}, 'text': text}}
            if text.startswith('/'):
                data['message']['entities'] = [{'type': 'bot_command', 'offset': 0, 'length': len(text)}]
            return Update.de_json(data, app.bot)
        try:
            await app.process_update(update('video', 1))
            await asyncio.wait_for(entered.wait(), 1)
            await app.process_update(update('/id', 2))
            self.assertTrue(command_ran.is_set())
        finally:
            release.set()
            await app.stop()
            await app.bot.shutdown()

    async def test_portrait_native_resolution_and_audio_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            for size, expected in [('540x960', True), ('1080x1920', True), ('640x960', False)]:
                path = Path(directory) / (size + '.mp4')
                subprocess.run([bot.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i',
                    f'color=c=black:s={size}:d=0.1', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.1',
                    '-vf', 'setsar=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-y', str(path)], check=True, capture_output=True)
                self.assertEqual(await bot.is_ready_portrait_video(path), expected)

    async def test_landscape_normalization_keeps_audio_and_nine_sixteen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'landscape.mp4'
            subprocess.run([bot.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i',
                'color=c=black:s=320x180:d=0.1', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.1',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-y', str(path)], check=True, capture_output=True)
            output = await bot.normalize_video_for_snapchat(path, Path(directory))
            self.assertTrue(await bot.is_ready_portrait_video(output))
            decoded = subprocess.run([bot.get_ffmpeg_exe(), '-v', 'error', '-i', str(output), '-map', '0:a:0', '-f', 'null', '-'], capture_output=True)
            self.assertEqual(decoded.returncode, 0, decoded.stderr)

    async def test_timeout_and_cancel_stop_child_process(self):
        for cancel in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                pid_path = Path(directory) / 'pid'
                script = 'import os,time,pathlib;pathlib.Path(' + repr(str(pid_path)) + ').write_text(str(os.getpid()));time.sleep(30)'
                task = asyncio.create_task(bot.process_output((sys.executable, '-c', script), 30 if cancel else 0.3))
                if cancel:
                    for _ in range(100):
                        if pid_path.exists():
                            break
                        await asyncio.sleep(0.01)
                    task.cancel()
                with self.assertRaises(asyncio.CancelledError if cancel else asyncio.TimeoutError):
                    await task
                self.assertTrue(pid_path.exists())
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(pid_path.read_text()), 0)

    async def test_accounting_failure_does_not_resend_delivered_video(self):
        store = Mock()
        store.cached_file_id.return_value = 'cached'
        store.save_owner_video.side_effect = RuntimeError('database unavailable')
        message = NS(text='https://www.tiktok.com/@demo/video/123', reply_text=AsyncMock(), reply_video=AsyncMock())
        update = NS(effective_user=NS(id=42), effective_message=message)
        with patch.object(bot, 'STORE', store), patch.object(bot, 'get_missing_channels', AsyncMock(return_value=([], False))), patch.object(bot, 'send_download_followups', AsyncMock()), patch.object(bot, 'download_video', AsyncMock()) as download:
            await bot.download_social_video(update, NS(bot=NS(username='test_bot')))
            download.assert_not_awaited()
            message.reply_video.assert_awaited_once()
            message.reply_text.assert_not_awaited()
            store.record_failure.assert_not_called()

    async def test_error_details_are_classified_without_private_url(self):
        with patch.object(bot, 'process_output', AsyncMock(return_value=(1, b'private https://example.org/?secret=hidden'))):
            with tempfile.TemporaryDirectory() as directory, self.assertRaises(bot.DownloadFailure) as error:
                await bot.download_video('https://www.tiktok.com/@demo/video/123', Path(directory))
        self.assertEqual(error.exception.reason, 'restricted')
        self.assertNotIn('secret', str(error.exception))


if __name__ == '__main__':
    unittest.main()
