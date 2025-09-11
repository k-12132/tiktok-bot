import os 
import uuid
import subprocess
import logging
import json
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
TIKTOK_URL = "https://www.tiktok.com/@kh01ed?is_from_webapp=1&sender_device=pc"
SNAPCHAT_URL = "https://snapchat.com/t/Di0JRwPG"

# قائمة القنوات والقروبات (تم حذف القناة الأولى)
CHANNELS = [
    {"type": "channel", "id": "@saudiJ0b"},
    {"type": "channel", "id": "@kh01ed"},
    {"type": "group", "id": "@kh01ed2"}  # القروب العام
]

# ملف لتخزين المستخدمين الذين تم عرض الرسالة لهم
VERIFIED_FILE = "verified_users.json"

# تحميل المستخدمين المحققين عند بدء البوت
if os.path.exists(VERIFIED_FILE):
    with open(VERIFIED_FILE, "r") as f:
        verified_users = json.load(f)
else:
    verified_users = {}

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

    reply_markup = InlineKeyboardMarkup(keyboard)

    text = "🚫 يجب عليك الاشتراك في القنوات والدخول إلى القروبات التالية لاستخدام البوت:"
    if extra_text:
        text += f"\n\n⚠️ {extra_text}"

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
        user_id = str(query.from_user.id)  # نخزن كـ string للـ JSON
        not_joined = await not_subscribed_channels(context.bot, int(user_id))

        if not_joined:
            errors = [i for i in not_joined if "error" in i]
            if errors:
                await send_subscription_message(update, context, "تأكد أن البوت مضاف كأدمن في القنوات/القروبات حتى أقدر أتحقق من عضويتك.")
            else:
                await send_subscription_message(update, context)
        else:
            # ✅ تم الاشتراك
            await query.message.edit_text("✅ تم التحقق من اشتراكك، أرسل الآن رابط فيديو تيك توك 🎥")

            # عرض رسالة TikTok وSnapchat مرة واحدة لكل مستخدم
            if user_id not in verified_users:
                verified_users[user_id] = True
                # حفظ في ملف JSON
                with open(VERIFIED_FILE, "w") as f:
                    json.dump(verified_users, f)

                keyboard = [
                    [
                        InlineKeyboardButton("🎵 تابعني على TikTok", url=TIKTOK_URL),
                        InlineKeyboardButton("👻 أضفني على Snapchat", url=SNAPCHAT_URL)
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await query.message.reply_text(
                    "🎉 يسعدنا إضافتنا على حسابنا في تيك توك وسناب شات لمتابعة كل جديد 💡",
                    reply_markup=reply_markup
                )

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
