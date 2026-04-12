import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, request
import atexit

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
UPI_ID = os.getenv('UPI_ID')

bot = telebot.TeleBot(BOT_TOKEN)
client = MongoClient(MONGO_URI)
db = client['sub_management']
channels_col = db['channels']
users_col = db['users']

# --- FLASK SERVER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running and healthy!"

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.stream.read().decode("utf-8"))
    bot.process_new_updates([update])
    return "OK", 200

# --- HANDLERS ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    text = message.text.split()

    if len(text) > 1:
        try:
            ch_id = int(text[1])
            ch_data = channels_col.find_one({"channel_id": ch_id})
            if ch_data:
                markup = InlineKeyboardMarkup()
                for p_time, p_price in ch_data['plans'].items():
                    label = f"{p_time} Min" if int(p_time) < 60 else f"{int(p_time)//1440} Days"
                    markup.add(InlineKeyboardButton(
                        f"💳 {label} - ₹{p_price}",
                        callback_data=f"select_{ch_id}_{p_time}"
                    ))
                markup.add(InlineKeyboardButton(
                    "📞 Contact Admin",
                    url=f"https://t.me/{ch_data['contact_username']}"
                ))
                bot.send_message(
                    message.chat.id,
                    f"Welcome!\n\nYou are joining: *{ch_data['name']}*.\n\nPlease select a subscription plan below:",
                    reply_markup=markup,
                    parse_mode="Markdown"
                )
                return
        except Exception as e:
            bot.send_message(message.chat.id, f"Error: {e}")

    if user_id == ADMIN_ID:
        bot.send_message(message.chat.id,
            "✅ Admin Panel Active!\n\n/add - Add/Edit Channel & Prices\n/channels - Manage Existing Channels")
    else:
        bot.send_message(message.chat.id,
            "Welcome! To join a channel, please use the link provided by the Admin.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_'))
def user_pays(call):
    _, ch_id, mins = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=upi://pay?pa={UPI_ID}%26am={price}%26cu=INR"
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ I Have Paid", callback_data=f"paid_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{ch_data['contact_username']}"))
    bot.send_photo(
        call.message.chat.id,
        qr_url,
        caption=f"Plan: {mins} Minutes\nPrice: ₹{price}\nUPI ID: `{UPI_ID}`\n\nPlease complete the payment and click 'I Have Paid'.",
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('paid_'))
def admin_notify(call):
    _, ch_id, mins = call.data.split('_')
    user = call.from_user
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Approve", callback_data=f"app_{user.id}_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("❌ Reject", callback_data=f"rej_{user.id}"))
    bot.send_message(
        ADMIN_ID,
        f"🔔 *Payment Verification Required!*\n\nUser: {user.first_name}\nChannel: {ch_data['name']}\nPlan: {mins} Mins\nPrice: ₹{price}",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    u_markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{ch_data['contact_username']}")
    )
    bot.send_message(call.message.chat.id,
        "✅ Your payment request has been sent. Please wait for Admin approval.",
        reply_markup=u_markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('app_'))
def approve_now(call):
    _, u_id, ch_id, mins = call.data.split('_')
    u_id, ch_id, mins = int(u_id), int(ch_id), int(mins)
    try:
        expiry_datetime = datetime.now() + timedelta(minutes=mins)
        expiry_ts = int(expiry_datetime.timestamp())
        link = bot.create_chat_invite_link(ch_id, member_limit=1, expire_date=expiry_ts)
        users_col.update_one(
            {"user_id": u_id, "channel_id": ch_id},
            {"$set": {"expiry": expiry_datetime.timestamp()}},
            upsert=True
        )
        bot.send_message(
            u_id,
            f"🥳 *Payment Approved!*\n\n"
            f"Subscription: {mins} Minutes\n\n"
            f"Join Link: {link.invite_link}\n\n"
            f"⚠️ Note: This link and your access will expire in {mins} minutes.",
            parse_mode="Markdown"
        )
        bot.edit_message_text(
            f"✅ Approved user {u_id} for {mins} mins.",
            call.message.chat.id,
            call.message.message_id
        )
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Error: {e}")

def kick_expired_users():
    now = datetime.now().timestamp()
    expired_users = users_col.find({"expiry": {"$lte": now}})
    bot_username = bot.get_me().username
    for user in expired_users:
        try:
            bot.ban_chat_member(user['channel_id'], user['user_id'])
            bot.unban_chat_member(user['channel_id'], user['user_id'])
            rejoin_url = f"https://t.me/{bot_username}?start={user['channel_id']}"
            markup = InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔄 Re-join / Renew", url=rejoin_url)
            )
            bot.send_message(
                user['user_id'],
                "⚠️ Your subscription has expired.\n\nTo join again or renew, please click the button below:",
                reply_markup=markup
            )
            users_col.delete_one({"_id": user['_id']})
        except Exception as e:
            print(f"Error kicking user {user['user_id']}: {e}")

# --- STARTUP ---
if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(kick_expired_users, 'interval', minutes=1)
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))

    bot.remove_webhook()
    bot.set_webhook(url=f"https://your-render-app.onrender.com/{BOT_TOKEN}")

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
