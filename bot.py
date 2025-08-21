import os
import uuid
import subprocess
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# إعداد السجل لتسجيل الأخطاء
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# متغيرات البيئة
BOT_TOKEN = os.getenv("BOT_TOKEN")

# أسماء القنوات المطلوبة
CHANNELS = ["@saudiJ0b", "@kh01ed"]

# أمر /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أرسل رابط فيديو تيك توك وسأقوم بتحميله لك 🎥")

# وظيفة للتحقق من اشتراك المستخدم في كل القنوات المطلوبة
async def is_user_subscribed(bot, user_id):
    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status not in ["member", "creator", "administrator"]:
                return False, channel
        except Exception as e:
            logging.error(f"Error checking membership in {channel}: {e}")
            return False, channel
    return True, None

# الوظيفة الأساسية لتحميل فيديوهات TikTok
async def download_tiktok_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # التحقق من الاشتراك في القنوات كلها
    subscribed, channel = await is_user_subscribed(context.bot, user_id)
    if not subscribed:
        await update.message.reply_text(
            f"🚫 يجب عليك الاشتراك في هذه القناة أولاً لاستخدام البوت:\nhttps://t.me/{channel.replace('@','')}"
        )
        return

    # تحقق من الرابط
    url = update.message.text
    if "tiktok.com" not in url:
        await update.message.reply_text("❌ الرجاء إرسال رابط صحيح من تيك توك 📎")
        return

    # التحميل
    filename = f"{uuid.uuid4()}.mp4"
    output_path = os.path.join("downloads", filename)

    try:
        os.makedirs("downloads", exist_ok=True)
        command = ["yt-dlp", "-o", output_path, url]
        subprocess.run(command, check=True)

        with open(output_path, "rb") as video:
            await update.message.reply_video(video)

        os.remove(output_path)

    except Exception as e:
        await update.message.reply_text("❌ حدث خطأ أثناء تحميل الفيديو. حاول مرة أخرى لاحقًا.")
        logging.error(f"Download error: {e}")

# معالج الأخطاء
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error(f"حدث خطأ غير متوقع: {context.error}")

# نقطة البداية
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_tiktok_video))
    app.add_error_handler(error_handler)

    app.run_polling()

if __name__ == "__main__":
    main()
