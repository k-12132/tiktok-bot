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

# إعدادات السجل لتسجيل الأخطاء
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# متغيرات البيئة
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = "saudi_J0b"  # بدون @

# أمر /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أرسل رابط فيديو تيك توك وسأقوم بتحميله لك 🎥")

# الوظيفة الأساسية لتحميل فيديوهات TikTok
async def download_tiktok_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # التحقق من الاشتراك في القناة
    try:
        member = await context.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        if member.status not in ["member", "creator", "administrator"]:
            await update.message.reply_text("🚫 يجب عليك الاشتراك في القناة أولاً لاستخدام البوت:\nhttps://t.me/saudi_J0b")
            return
    except Exception as e:
        await update.message.reply_text("🚫 يجب عليك الاشتراك في القناة أولاً لاستخدام البوت:\nhttps://t.me/saudi_J0b")
        logging.error(f"Error checking membership: {e}")
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

# معالج الأخطاء العامة
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
