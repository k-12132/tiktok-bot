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

# روابط حساباتك في TikTok و Snapchat
TIKTOK_URL = "https://www.tiktok.com/@YourTikTokUser"
SNAPCHAT_URL = "https://www.snapchat.com/add/YourSnapUser"

# قائمة القنوات والقروبات
CHANNELS = [
    {"type": "channel", "id": "@saudiJ0b"},
    {"type": "channel", "id": "@kh01ed"},
    {"type": "group", "id": "@kh01ed2"}  # القروب العام
]

# أمر /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_subscription_message(update, context)

# دالة لإرسال رسالة الاشتراك
async def send_subscription_message(update: Update, context: ContextTypes.DEFAULT_TYPE, extra_text: str = ""):
    keyboard = []
    for item in CHANNELS:
        if item["type"] == "channel":
            keyboard.append([InlineKeyboardButton(f"📢 اشترك في {item['id']}", url=f"https://t.me/{item['id'].replace('@','')}")])
        elif item["type"] == "group":
            keyboard.append([InlineKeyboardButton("👥 انضم للقروب", url=f"https://t.me/{item['id'].replace('@','')}")])

    # زر التحقق
    keyboard.append([InlineKeyboardButton("✅ تحققت من الاشتراك", callback_data="check_subscription")])

    # أزرار TikTok و Snapchat
   InlineKeyboardButton("🎵 تابعني على TikTok", url="https://www.tiktok.com/@kh01ed?is_from_webapp=1&sender_device=pc")
InlineKeyboardButton("👻 أضفني على Snapchat", url="https://snapchat.com/t/Di0JRwPG")

    reply_markup = InlineKeyboardMarkup(keyboard)

    text = "🚫 يجب عليك الاشتراك في القنوات والدخول إلى القروبات التالية لاستخدام البوت:"
    if extra_text:
        text += f"\n\n⚠️ {extra_text}"

    # نص إضافي للترويج
    text += "\n\nولا تنسَ متابعتنا على تيك توك 🎵 و سناب 👻 لآخر التحديثات 💡"

    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup)

# التحقق من اشتراك المستخدم
async def not_subscribed_channels(bot, user_id):
    not_joined = []
    for item in CHANNELS:
        try:
            member = await bot.get_chat_member(item["id"], user_id)
            if member.status not in ["member", "creator", "administrator"]:
                not_joined.append(item)
        except Exception as e:
            logging.error(f"Error checking membership in {item['id']}: {e}")
            not_joined.append({"id": item["id"], "error": True, "type": item["type"]})
    return not_joined

# معالجة الأزرار
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "check_subscription":
        user_id = query.from_user.id
        not_joined = await not_subscribed_channels(context.bot, user_id)

        if not_joined:
            errors = [i for i in not_joined if "error" in i]
            if errors:
                await send_subscription_message(update, context, "تأكد أن البوت مضاف كأدمن في القنوات/القروبات حتى أقدر أتحقق من عضويتك.")
            else:
                await send_subscription_message(update, context)
        else:
            await query.message.edit_text("✅ تم التحقق من اشتراكك، أرسل الآن رابط فيديو تيك توك 🎥")

# الوظيفة الأساسية لتحميل الفيديو
async def download_tiktok_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    not_joined = await not_subscribed_channels(context.bot, user_id)

    if not_joined:
        errors = [i for i in not_joined if "error" in i]
        if errors:
            await send_subscription_message(update, context, "تأكد أن البوت مضاف كأدمن في القنوات/القروبات حتى أقدر أتحقق من عضويتك.")
        else:
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
