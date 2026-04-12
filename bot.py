import os
import telebot
from pymongo import MongoClient
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')

bot = telebot.TeleBot(BOT_TOKEN)
client = MongoClient(MONGO_URI)
db = client['sub_management']
users_col = db['users']

# --- Job function ---
def kick_expired_users():
    now = datetime.now().timestamp()
    expired_users = users_col.find({"expiry": {"$lt": now}})
    for user in expired_users:
        try:
            bot.kick_chat_member(user['channel_id'], user['user_id'])
            bot.send_message(user['user_id'], "⚠️ Your subscription has expired. Please renew to regain access.")
            users_col.delete_one({"_id": user["_id"]})
        except Exception as e:
            print(f"Error kicking user {user['user_id']}: {e}")

# --- Scheduler setup ---
scheduler = BackgroundScheduler()
scheduler.add_job(kick_expired_users, 'interval', minutes=1)
scheduler.start()

# Ensure scheduler shuts down cleanly when interpreter exits
atexit.register(lambda: scheduler.shutdown(wait=False))

# --- Bot polling ---
bot.polling(none_stop=True)
