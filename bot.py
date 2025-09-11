import os 
import uuid
import subprocess
import logging
import json
import requests
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
SNAPCHAT_URL = "https://snapchat.com/t/9g7sbzuB"

# قائمة القنوات والقروبات (تم حذف القناة الأولى)
CHANNELS = [
    {"type": "channel", "id": "@kh01ed"},
    {"type": "group", "id": "@kh01ed2"}  # القروب العام
]

# ملفات لتخزين المستخدمين الذين تم عرض الرسالة لهم
VERIFIED_FILE = "verified_users.json"

if os.path.exists(VERIFIED_FILE):
    with open(VERIFIED_FILE, "r") as f:
        verified_users = json.load(f)
else:
    verified_users = {}

# أمر /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_subscription_message(update, context)

# دالة إرسال رسالة الاشتراك
async def send_subscription_message(update: Update, context: ContextTypes.DEFAULT_TYPE, extra_text: str = ""):
    keyboard = []
    for item in CHANNELS:
        if item["type"] == "channel":
            keyboard.append([InlineKeyboardButton(f"📢 اشترك في {item['id']}", url=f"https://t.me/{item['id'].replace('@','')}")])
        elif item["type"] == "group":
            keyboard.append([InlineKeyboardButton("👥 انضم للقروب", url=f"https://t.me/{item['id'].replace('@','')}")])

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
        user_id = str(query.from_user.id)
        not_joined = await not_subscribed_channels(context.bot, int(user_id))

        if not_joined:
            errors = [i for i in not_joined if "error" in i]
            if errors:
                await send_subscription_message(update, context, "تأكد أن البوت مضاف كأدمن في القنوات/القروبات حتى أقدر أتحقق من عضويتك.")
            else:
                await send_subscription_message(update, context)
        else:
            await query.message.edit_text("✅ تم التحقق من اشتراكك، أرسل رابط فيديو تيك توك أو صورة 📎")

            if user_id not in verified_users:
                verified_users[user_id] = True
                with open(VERIFIED_FILE, "w") as f:
                    json.dump(verified_users, f)

                keyboard = [
                    [
                        InlineKeyboardButton("🎵 تابعني على TikTok", url=TIKTOK_URL),
                        InlineKeyboardButton("👻 أضفني على Snapchat", url=SNAPCHAT_URL)
                    ],
                    [
                        InlineKeyboardButton("🤝 شارك البوت مع أصدقائك", switch_inline_query="جرب هذا البوت لتحميل فيديوهات تيك توك وصور 🎵")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.message.reply_text(
                    "🎉 يسعدنا إضافتنا على حسابنا في تيك توك وسناب شات لمتابعة كل جديد 💡",
                    reply_markup=reply_markup
                )

# دالة تحميل الفيديو
async def download_tiktok_video(update: Update, url: str):
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

# دالة تحميل الصور
async def download_image(update: Update, url: str):
    try:
        filename = f"{uuid.uuid4()}.jpg"
        output_path = os.path.join("downloads", filename)
        os.makedirs("downloads", exist_ok=True)

        response = requests.get(url)
        response.raise_for_status()

        with open(output_path, "wb") as f:
            f.write(response.content)

        with open(output_path, "rb") as photo:
            await update.message.reply_photo(photo)

        os.remove(output_path)
    except Exception as e:
        await update.message.reply_text("❌ حدث خطأ أثناء تحميل الصورة. حاول مرة أخرى لاحقًا.")
        logging.error(f"Image download error: {e}")

# دالة التعامل مع الرسائل
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    not_joined = await not_subscribed_channels(context.bot, user_id)
    if not_joined:
        errors = [i for i in not_joined if "error" in i]
        if errors:
            await send_subscription_message(update, context, "تأكد أن البوت مضاف كأدمن في القنوات/القروبات.")
        else:
            await send_subscription_message(update, context)
        return

    url = update.message.text

    if "tiktok.com" in url:
        await download_tiktok_video(update, url)
    elif any(url.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"]):
        await download_image(update, url)
    else:
        await update.message.reply_text("❌ هذا الرابط غير مدعوم. يرجى إرسال رابط تيك توك أو رابط صورة مباشر.")

    # رسالة تذكير بعد التحميل
    keyboard = [
        [
            InlineKeyboardButton("🎵 تابعني على TikTok", url=TIKTOK_URL),
            InlineKeyboardButton("👻 أضفني على Snapchat", url=SNAPCHAT_URL)
        ],
        [
            InlineKeyboardButton("🤝 شارك البوت مع أصدقائك", switch_inline_query="جرب هذا البوت لتحميل فيديوهات تيك توك وصور 🎵")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🎉 إذا أعجبك المحتوى، تابعنا على تيك توك وسناب لمزيد من المحتوى!",
        reply_markup=reply_markup
    )

# معالج الأخطاء
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error(f"حدث خطأ غير متوقع: {context.error}")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)

    app.run_polling()

if __name__ == "__main__":
    main()
