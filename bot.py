import asyncio
import hashlib
import logging
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlencode, urlparse

from imageio_ffmpeg import get_ffmpeg_exe
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
)
logger = logging.getLogger(__name__)
# HTTPX includes request URLs in INFO logs. Telegram embeds the bot token in
# those URLs, so keep transport logging at WARNING to prevent secret leakage.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TIKTOK_URL = "https://www.tiktok.com/@kh01ed?_r=1&_t=ZS-99TrxV4Gtjc"
SNAPCHAT_URL = "https://snapchat.com/t/d9GtFjtN"
NOON_URL = "https://s.noon.com/iU1YAlSF0Mw"
NOON_DISCOUNT_CODE = "Hoob"
CHANNELS = ("@kh01ed",)

DOWNLOAD_TIMEOUT_SECONDS = int(os.getenv("DOWNLOAD_TIMEOUT_SECONDS", "120"))
VIDEO_PROCESS_TIMEOUT_SECONDS = int(os.getenv("VIDEO_PROCESS_TIMEOUT_SECONDS", "180"))
MAX_VIDEO_BYTES = int(os.getenv("MAX_VIDEO_BYTES", str(45 * 1024 * 1024)))
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2"))
DOWNLOAD_FRAGMENT_CONCURRENCY = int(os.getenv("DOWNLOAD_FRAGMENT_CONCURRENCY", "4"))
TARGET_VIDEO_WIDTH = int(os.getenv("TARGET_VIDEO_WIDTH", "720"))
TARGET_VIDEO_HEIGHT = int(os.getenv("TARGET_VIDEO_HEIGHT", "1280"))
BOT_DB_PATH = os.getenv("BOT_DB_PATH", "/tmp/tiktok-bot.sqlite3").strip()
ADMIN_USER_IDS = {
    int(value)
    for value in os.getenv("ADMIN_USER_IDS", "").split(",")
    if value.strip().isdigit()
}
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
SEEN_USERS: set[int] = set()
VIDEO_CACHE_VERSION = "portrait-v2"

TIKTOK_HOST_RE = re.compile(r"(^|\.)tiktok\.com$", re.IGNORECASE)
INSTAGRAM_HOST_RE = re.compile(r"(^|\.)instagram\.com$", re.IGNORECASE)
INSTAGRAM_MEDIA_PATH_RE = re.compile(
    r"^/(?:reel|reels|p|tv|share/(?:reel|p))/", re.IGNORECASE
)


class BotStore:
    """Small SQLite store for referrals, usage metrics, and Telegram file IDs."""

    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    invited_by INTEGER,
                    referrals INTEGER NOT NULL DEFAULT 0,
                    downloads INTEGER NOT NULL DEFAULT 0,
                    first_seen INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metrics (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS video_cache (
                    url_hash TEXT PRIMARY KEY,
                    telegram_file_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def register_user(self, user_id: int, invited_by: int | None = None) -> bool:
        now = int(time.time())
        with self.connection() as connection:
            valid_inviter = None
            if invited_by and invited_by != user_id:
                exists = connection.execute(
                    "SELECT 1 FROM users WHERE user_id = ?", (invited_by,)
                ).fetchone()
                if exists:
                    valid_inviter = invited_by
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO users
                    (user_id, invited_by, first_seen, last_seen)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, valid_inviter, now, now),
            )
            created = cursor.rowcount == 1
            if created and valid_inviter:
                connection.execute(
                    "UPDATE users SET referrals = referrals + 1 WHERE user_id = ?",
                    (valid_inviter,),
                )
            if not created:
                connection.execute(
                    "UPDATE users SET last_seen = ? WHERE user_id = ?",
                    (now, user_id),
                )
        return created

    def referral_count(self, user_id: int) -> int:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT referrals FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return int(row[0]) if row else 0

    def record_download(self, user_id: int, *, cached: bool = False) -> None:
        self.register_user(user_id)
        with self.connection() as connection:
            connection.execute(
                "UPDATE users SET downloads = downloads + 1 WHERE user_id = ?",
                (user_id,),
            )
            self._increment_metric(connection, "downloads_success")
            if cached:
                self._increment_metric(connection, "cache_hits")

    def record_failure(self) -> None:
        with self.connection() as connection:
            self._increment_metric(connection, "downloads_failed")

    @staticmethod
    def _increment_metric(connection: sqlite3.Connection, key: str) -> None:
        connection.execute(
            """
            INSERT INTO metrics (key, value) VALUES (?, 1)
            ON CONFLICT(key) DO UPDATE SET value = value + 1
            """,
            (key,),
        )

    @staticmethod
    def _url_hash(url: str) -> str:
        # Bump VIDEO_CACHE_VERSION whenever the generated media format changes.
        # This prevents Telegram from reusing an older file_id with bad geometry.
        value = f"{VIDEO_CACHE_VERSION}:{url.strip()}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def cached_file_id(self, url: str) -> str | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT telegram_file_id FROM video_cache WHERE url_hash = ?",
                (self._url_hash(url),),
            ).fetchone()
        return str(row[0]) if row else None

    def cache_file(self, url: str, file_id: str, platform: str) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO video_cache
                    (url_hash, telegram_file_id, platform, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (self._url_hash(url), file_id, platform, int(time.time())),
            )

    def remove_cached_file(self, url: str) -> None:
        with self.connection() as connection:
            connection.execute(
                "DELETE FROM video_cache WHERE url_hash = ?", (self._url_hash(url),)
            )

    def statistics(self) -> dict[str, int]:
        with self.connection() as connection:
            users = connection.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(downloads), 0),
                       COALESCE(SUM(referrals), 0)
                FROM users
                """
            ).fetchone()
            metrics = dict(connection.execute("SELECT key, value FROM metrics"))
            cached_videos = connection.execute(
                "SELECT COUNT(*) FROM video_cache"
            ).fetchone()[0]
        return {
            "users": int(users[0]),
            "downloads": int(users[1]),
            "referrals": int(users[2]),
            "failures": int(metrics.get("downloads_failed", 0)),
            "cache_hits": int(metrics.get("cache_hits", 0)),
            "cached_videos": int(cached_videos),
        }


STORE = BotStore(BOT_DB_PATH)


def get_supported_platform(value: str) -> str | None:
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    if TIKTOK_HOST_RE.search(parsed.hostname):
        return "TikTok"
    if INSTAGRAM_HOST_RE.search(parsed.hostname) and INSTAGRAM_MEDIA_PATH_RE.search(
        parsed.path
    ):
        return "Instagram"
    return None


def subscription_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"📢 اشترك في {channel}", url=f"https://t.me/{channel[1:]}")]
        for channel in CHANNELS
    ]
    rows.append(
        [InlineKeyboardButton("✅ تحققت من الاشتراك", callback_data="check_subscription")]
    )
    return InlineKeyboardMarkup(rows)


def noon_ad_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛒 تسوّق الآن من نون", url=NOON_URL)]]
    )


def invite_link(bot_username: str, user_id: int) -> str:
    return f"https://t.me/{bot_username}?start=ref_{user_id}"


def social_keyboard(bot_username: str, user_id: int) -> InlineKeyboardMarkup:
    personal_link = invite_link(bot_username, user_id)
    share_url = "https://t.me/share/url?" + urlencode(
        {
            "url": personal_link,
            "text": "حمّل مقاطع TikTok وInstagram بسهولة وبالصوت عبر هذا البوت 🎥",
        }
    )
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎵 تابعني على TikTok", url=TIKTOK_URL),
                InlineKeyboardButton("👻 أضفني على Snapchat", url=SNAPCHAT_URL),
            ],
            [
                InlineKeyboardButton(
                    "📤 شارك البوت مع أصدقائك",
                    url=share_url,
                )
            ],
            [InlineKeyboardButton("🎁 عدد دعواتي", callback_data="my_referrals")],
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user:
        inviter = None
        if context.args:
            match = re.fullmatch(r"ref_(\d+)", context.args[0])
            if match:
                inviter = int(match.group(1))
        STORE.register_user(user.id, inviter)
    await send_subscription_message(update)


async def invite_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    STORE.register_user(user.id)
    count = STORE.referral_count(user.id)
    await message.reply_text(
        "🎁 رابط دعوتك الخاص\n\n"
        f"👥 عدد الأشخاص الذين دعوتهم: {count}\n"
        "شارك الرابط من الزر التالي:",
        reply_markup=social_keyboard(context.bot.username, user.id),
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    if not ADMIN_USER_IDS or user.id not in ADMIN_USER_IDS:
        await message.reply_text("⛔ هذا الأمر متاح لمدير البوت فقط.")
        return
    stats = STORE.statistics()
    await message.reply_text(
        "📊 إحصائيات البوت منذ آخر تشغيل\n\n"
        f"👥 المستخدمون: {stats['users']}\n"
        f"✅ التحميلات الناجحة: {stats['downloads']}\n"
        f"❌ التحميلات الفاشلة: {stats['failures']}\n"
        f"🎁 الدعوات الناجحة: {stats['referrals']}\n"
        f"♻️ فيديوهات أُعيد إرسالها من تيليجرام: {stats['cache_hits']}\n"
        f"🗂️ الفيديوهات المحفوظة: {stats['cached_videos']}"
    )


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message and user:
        await message.reply_text(
            f"🆔 رقم حسابك في تيليجرام: `{user.id}`", parse_mode="Markdown"
        )


async def send_subscription_message(update: Update, extra_text: str = "") -> None:
    text = (
        "🎬 حمّل مقاطع TikTok وInstagram بسهولة وبالصوت!\n\n"
        "📢 للبدء، اشترك في القناة التالية ثم اضغط زر التحقق:"
    )
    if extra_text:
        text += f"\n\n⚠️ {extra_text}"

    if update.callback_query and update.callback_query.message:
        try:
            await update.callback_query.message.edit_text(
                text, reply_markup=subscription_keyboard()
            )
        except BadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
    elif update.effective_message:
        await update.effective_message.reply_text(
            text, reply_markup=subscription_keyboard()
        )


async def get_missing_channels(bot, user_id: int) -> tuple[list[str], bool]:
    missing: list[str] = []
    configuration_error = False
    allowed = {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.OWNER,
        ChatMemberStatus.ADMINISTRATOR,
    }
    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status not in allowed:
                missing.append(channel)
        except (BadRequest, Forbidden) as exc:
            configuration_error = True
            logger.warning("Cannot verify membership for %s: %s", channel, exc)
        except TelegramError:
            configuration_error = True
            logger.exception("Telegram error while checking %s", channel)
    return missing, configuration_error


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if query.data == "my_referrals":
        STORE.register_user(query.from_user.id)
        count = STORE.referral_count(query.from_user.id)
        if query.message:
            await query.message.reply_text(
                f"🎁 عدد دعواتك الناجحة: {count}\n\n"
                "شارك رابطك الخاص من الزر التالي:",
                reply_markup=social_keyboard(
                    context.bot.username, query.from_user.id
                ),
            )
        return
    if query.data != "check_subscription":
        return

    missing, configuration_error = await get_missing_channels(
        context.bot, query.from_user.id
    )
    if configuration_error:
        await send_subscription_message(
            update,
            "تعذر التحقق. تأكد أن البوت مشرف في جميع القنوات ثم حاول مجددًا.",
        )
        return
    if missing:
        await send_subscription_message(update, "اشترك في جميع القنوات ثم أعد التحقق.")
        return

    if query.message:
        await query.message.edit_text(
            "✅ تم التحقق، أرسل رابط فيديو من TikTok أو Instagram 🎥"
        )
        if query.from_user.id not in SEEN_USERS:
            SEEN_USERS.add(query.from_user.id)
            await query.message.reply_text(
                "🎉 تابعنا على تيك توك وسناب شات لمتابعة كل جديد 💡",
                reply_markup=social_keyboard(
                    context.bot.username, query.from_user.id
                ),
            )


async def download_video(url: str, directory: Path) -> Path:
    output_template = str(directory / "video.%(ext)s")
    command = (
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--no-progress",
        "--concurrent-fragments",
        str(DOWNLOAD_FRAGMENT_CONCURRENCY),
        "--retries",
        "2",
        "--fragment-retries",
        "2",
        "--socket-timeout",
        "20",
        "--max-filesize",
        str(MAX_VIDEO_BYTES),
        "--ffmpeg-location",
        get_ffmpeg_exe(),
        "--merge-output-format",
        "mp4",
        "--format",
        (
            # Prefer one ready-to-use MP4. This avoids downloading two streams
            # and merging them when TikTok/Instagram already offers a combined
            # file, which is considerably faster on small Render workers.
            "best[ext=mp4][vcodec!=none][acodec!=none]/"
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo+bestaudio/"
            "best[acodec!=none]"
        ),
        "--output",
        output_template,
        url,
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=DOWNLOAD_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError("download timed out")

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(f"yt-dlp failed: {detail}")

    files = [path for path in directory.iterdir() if path.is_file()]
    if len(files) != 1:
        raise RuntimeError("unexpected downloader output")
    video_path = files[0]
    if video_path.stat().st_size > MAX_VIDEO_BYTES:
        raise RuntimeError("downloaded file exceeds Telegram limit")
    return video_path


async def normalize_video_for_snapchat(video_path: Path, directory: Path) -> Path:
    """Create a standards-compliant 9:16 MP4 with unambiguous display geometry."""
    output_path = directory / "snapchat-ready.mp4"
    command = (
        get_ffmpeg_exe(),
        "-y",
        "-i",
        str(video_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        (
            f"scale={TARGET_VIDEO_WIDTH}:{TARGET_VIDEO_HEIGHT}:"
            "force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={TARGET_VIDEO_WIDTH}:{TARGET_VIDEO_HEIGHT}:"
            "(ow-iw)/2:(oh-ih)/2:black,setsar=1,setdar=9/16"
        ),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "27",
        "-pix_fmt",
        "yuv420p",
        "-metadata:s:v:0",
        "rotate=0",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(output_path),
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=VIDEO_PROCESS_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError("video normalization timed out")

    if process.returncode != 0 or not output_path.exists():
        detail = stderr.decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(f"ffmpeg normalization failed: {detail}")
    if output_path.stat().st_size > MAX_VIDEO_BYTES:
        raise RuntimeError("normalized video exceeds Telegram limit")
    return output_path


async def download_social_video(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.text:
        return

    STORE.register_user(user.id)

    missing, configuration_error = await get_missing_channels(context.bot, user.id)
    if configuration_error:
        await send_subscription_message(
            update,
            "تعذر التحقق. تأكد أن البوت مشرف في جميع القنوات ثم حاول مجددًا.",
        )
        return
    if missing:
        await send_subscription_message(update)
        return

    url = message.text.strip()
    platform = get_supported_platform(url)
    if not platform:
        await message.reply_text(
            "❌ أرسل رابط فيديو صحيحًا من TikTok أو Instagram فقط 📎"
        )
        return

    cached_file_id = STORE.cached_file_id(url)
    if cached_file_id:
        try:
            await message.reply_video(video=cached_file_id, supports_streaming=True)
            STORE.record_download(user.id, cached=True)
            await message.reply_text(
                "📢 إعلان\n\n"
                "🛍️ تسوّق من نون ووفر أكثر!\n"
                f"🎟️ كود الخصم: {NOON_DISCOUNT_CODE}\n\n"
                "اضغط على الزر للانتقال إلى نون 👇",
                reply_markup=noon_ad_keyboard(),
            )
            await message.reply_text(
                "🎉 تم التحميل. شارك البوت مع أصدقائك من الزر التالي!",
                reply_markup=social_keyboard(context.bot.username, user.id),
            )
            return
        except BadRequest:
            logger.info("Cached Telegram file is no longer valid; downloading again")
            STORE.remove_cached_file(url)

    progress_message = await message.reply_text("⏳ جاري تجهيز الفيديو...")
    work_dir = Path(tempfile.mkdtemp(prefix="social-video-", dir="/tmp"))
    try:
        async with DOWNLOAD_SEMAPHORE:
            video_path = await download_video(url, work_dir)
            video_path = await normalize_video_for_snapchat(video_path, work_dir)
        with video_path.open("rb") as video:
            sent_video = await message.reply_video(
                video=video,
                supports_streaming=True,
                width=TARGET_VIDEO_WIDTH,
                height=TARGET_VIDEO_HEIGHT,
                read_timeout=120,
                write_timeout=120,
            )
        if sent_video.video:
            STORE.cache_file(url, sent_video.video.file_id, platform)
        STORE.record_download(user.id)
        await message.reply_text(
            "📢 إعلان\n\n"
            "🛍️ تسوّق من نون ووفر أكثر!\n"
            f"🎟️ كود الخصم: {NOON_DISCOUNT_CODE}\n\n"
            "اضغط على الزر للانتقال إلى نون 👇",
            reply_markup=noon_ad_keyboard(),
        )
        await message.reply_text(
            "🎉 تم التحميل. شارك البوت مع أصدقائك وتابعنا للمزيد!",
            reply_markup=social_keyboard(context.bot.username, user.id),
        )
    except Exception:
        logger.exception("%s video download or upload failed", platform)
        STORE.record_failure()
        await message.reply_text(
            "❌ تعذر تحميل الفيديو. تأكد أنه عام وغير مقيد، ثم حاول رابطًا آخر."
        )
    finally:
        try:
            await progress_message.delete()
        except TelegramError:
            pass
        shutil.rmtree(work_dir, ignore_errors=True)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    logger.error(
        "Unhandled update error",
        exc_info=(type(error), error, error.__traceback__) if error else None,
    )


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("start", "بدء استخدام البوت"),
            BotCommand("invite", "دعوة الأصدقاء وعرض إحالاتك"),
            BotCommand("id", "عرض رقم حسابك"),
            BotCommand("stats", "إحصائيات المدير"),
        ]
    )


def build_application() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("invite", invite_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(
        CallbackQueryHandler(
            button_handler, pattern="^(check_subscription|my_referrals)$"
        )
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, download_social_video)
    )
    app.add_error_handler(error_handler)
    return app


def main() -> None:
    build_application().run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
