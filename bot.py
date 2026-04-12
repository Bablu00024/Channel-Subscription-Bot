import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, request
from threading import Thread
import atexit

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
UPI_ID = os.getenv('UPI_ID')

# --- BOT + DB INIT ---
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

# --- ADMIN LOGIC (simplified example) ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    if message.from_user.id == ADMIN_ID:
        bot.send_message(message.chat.id, "✅ Admin Panel Active!\n\n/add - Add/Edit Channel & Prices\n/channels - Manage Existing Channels")
    else:
        bot.send_message(message.chat.id, "Welcome! To join a channel, please use the link provided by the Admin.")

# --- APPROVAL & EXPIRY ---
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
    # Scheduler for expiry checks
    scheduler = BackgroundScheduler()
    scheduler.add_job(kick_expired_users, 'interval', minutes=1)
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))

    # Set webhook
    bot.remove_webhook()
    bot.set_webhook(url=f"https://your-render-app.onrender.com/{BOT_TOKEN}")

    # Run Flask server
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
