from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import subprocess
import os
import uuid

BOT_TOKEN = "7731592926:AAFM64D6cHceoKSTYE8xalfFGu3JwebyTVc"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أرسل رابط فيديو تيك توك وسأقوم بتحميله لك 🎥")

async def download_tiktok_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await update.message.reply_text("المقطع أكتمل دقايق و يوصل لك")
        print(e)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_tiktok_video))

    app.run_polling()

if __name__ == "__main__":
    main()
