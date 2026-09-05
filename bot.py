import asyncio
import logging
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from imageio_ffmpeg import get_ffmpeg_exe
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
SEEN_USERS: set[int] = set()

TIKTOK_HOST_RE = re.compile(r"(^|\.)tiktok\.com$", re.IGNORECASE)
INSTAGRAM_HOST_RE = re.compile(r"(^|\.)instagram\.com$", re.IGNORECASE)
INSTAGRAM_MEDIA_PATH_RE = re.compile(
    r"^/(?:reel|reels|p|tv|share/(?:reel|p))/", re.IGNORECASE
)


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


def social_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎵 تابعني على TikTok", url=TIKTOK_URL),
                InlineKeyboardButton("👻 أضفني على Snapchat", url=SNAPCHAT_URL),
            ],
            [
                InlineKeyboardButton(
                    "🤝 شارك البوت مع أصدقائك",
                    url=f"https://t.me/{bot_username}",
                )
            ],
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_subscription_message(update)


async def send_subscription_message(update: Update, extra_text: str = "") -> None:
    text = "🚫 يجب الاشتراك في القنوات التالية لاستخدام البوت:"
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
                reply_markup=social_keyboard(context.bot.username),
            )


async def download_video(url: str, directory: Path) -> Path:
    output_template = str(directory / "video.%(ext)s")
    command = (
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--no-progress",
        "--max-filesize",
        str(MAX_VIDEO_BYTES),
        "--ffmpeg-location",
        get_ffmpeg_exe(),
        "--merge-output-format",
        "mp4",
        "--format",
        (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo+bestaudio/best[ext=mp4][acodec!=none]/"
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
    """Create a standards-compliant 9:16 MP4 that Snapchat handles consistently."""
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
        "scale=720:1280:force_original_aspect_ratio=decrease,"
        "pad=720:1280:(ow-iw)/2:(oh-ih)/2:black,setsar=1",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "27",
        "-pix_fmt",
        "yuv420p",
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

    progress_message = await message.reply_text("⏳ جاري تجهيز الفيديو...")
    work_dir = Path(tempfile.mkdtemp(prefix="social-video-", dir="/tmp"))
    try:
        async with DOWNLOAD_SEMAPHORE:
            video_path = await download_video(url, work_dir)
            video_path = await normalize_video_for_snapchat(video_path, work_dir)
        with video_path.open("rb") as video:
            await message.reply_video(
                video=video,
                supports_streaming=True,
                read_timeout=120,
                write_timeout=120,
            )
        await message.reply_text(
            "📢 إعلان\n\n"
            "🛍️ تسوّق من نون ووفر أكثر!\n"
            f"🎟️ كود الخصم: {NOON_DISCOUNT_CODE}\n\n"
            "اضغط على الزر للانتقال إلى نون 👇",
            reply_markup=noon_ad_keyboard(),
        )
        await message.reply_text(
            "🎉 تم التحميل. تابعنا على تيك توك وسناب لمزيد من المحتوى!",
            reply_markup=social_keyboard(context.bot.username),
        )
    except Exception:
        logger.exception("%s video download or upload failed", platform)
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


def build_application() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^check_subscription$"))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, download_social_video)
    )
    app.add_error_handler(error_handler)
    return app


def main() -> None:
    build_application().run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
