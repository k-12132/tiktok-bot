from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import subprocess
import os
import uuid

# التوكن واسم القناة من متغيرات البيئة
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = "@YourChannelUsername"  # غيّره إلى اسم قناتك

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أرسل رابط فيديو تيك توك وسأقوم بتحميله لك 🎥")

async def download_tiktok_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # التحقق من الاشتراك في القناة
    try:
        member = await context.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        if member.status not in ["member", "creator", "administrator"]:
            await update.message.reply_text("🚫 يجب عليك الاشتراك في القناة أولاً لاستخدام البوت:\n" + CHANNEL_USERNAME)
            return
    except Exception as e:
        await update.message.reply_text("🚫 يجب عليك الاشتراك في القناة أولاً لاستخدام البوت:\n" + CHANNEL_USERNAME)
        print("Error checking membership:", e)
        return

    url = update.message.text

    if "tiktok.com" not in url:
        await update.message.reply_text("الرجاء إرسال رابط صحيح من تيك توك 📎")
        return

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
        print(e)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_tiktok_video))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=os.environ.get("WEBHOOK_URL")
    )

if __name__ == "__main__":
    main()
