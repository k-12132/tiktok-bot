import os
import uuid
import subprocess
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# إعداد السجل لتسجيل الأخطاء
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

# أسماء القنوات المطلوبة
CHANNELS = [
    {"type": "channel", "id": "@saudiJ0b"},
    {"type": "channel", "id": "@kh01ed"},
    {"type": "group", "id": "@kh01ed2"}  # ← هذا القروب الجديد
]

# أمر /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_subscription_message(update, context)

# دالة لإرسال رسالة الاشتراك
async def send_subscription_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(f"📢 اشترك في {ch}", url=f"https://t.me/{ch.replace('@','')}")]
        for ch in CHANNELS
    ]
    keyboard.append([InlineKeyboardButton("✅ تحققت من الاشتراك", callback_data="check_subscription")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(
            "🚫 يجب عليك الاشتراك في القنوات التالية لاستخدام البوت:",
            reply_markup=reply_markup
        )
    elif update.callback_query:
        await update.callback_query.message.edit_text(
            "🚫 يجب عليك الاشتراك في القنوات التالية لاستخدام البوت:",
            reply_markup=reply_markup
        )

# التحقق من اشتراك المستخدم
async def not_subscribed_channels(bot, user_id):
    not_joined = []
    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status not in ["member", "creator", "administrator"]:
                not_joined.append(channel)
        except Exception as e:
            logging.error(f"Error checking membership in {channel}: {e}")
            not_joined.append(channel)
    return not_joined

# معالجة الأزرار
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "check_subscription":
        user_id = query.from_user.id
        not_joined = await not_subscribed_channels(context.bot, user_id)
        if not_joined:
            await send_subscription_message(update, context)
        else:
            await query.message.edit_text("✅ تم التحقق من اشتراكك، أرسل الآن رابط فيديو تيك توك 🎥")

# الوظيفة الأساسية لتحميل الفيديو
async def download_tiktok_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    not_joined = await not_subscribed_channels(context.bot, user_id)

    if not_joined:
        await send_subscription_message(update, context)
        return

    url = update.message.text
    if "tiktok.com" not in url:
        await update.message.reply_text("❌ الرجاء إرسال رابط صحيح من تيك توك 📎")
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
        logging.error(f"Download error: {e}")

# معالج الأخطاء
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error(f"حدث خطأ غير متوقع: {context.error}")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_tiktok_video))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)

    app.run_polling()

if __name__ == "__main__":
    main()
