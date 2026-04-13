import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from threading import Thread

# --- RENDER KEEP-ALIVE SERVER ---
app = Flask('')
@app.route('/')
def home(): return "Bot is running and healthy!"

def run_web():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    Thread(target=run_web).start()

# --- CONFIGURATION (Environment Variables) ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
UPI_ID = os.getenv('UPI_ID')
CONTACT_USERNAME = os.getenv('CONTACT_USERNAME')

bot = telebot.TeleBot(BOT_TOKEN)
client = MongoClient(MONGO_URI)
db = client['sub_management']
channels_col = db['channels']
users_col = db['users']

# --- USER PAYMENT FLOW ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('select_'))
def user_pays(call):
    _, ch_id, mins = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]
    
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=upi://pay?pa={UPI_ID}%26am={price}%26cu=INR"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ I Have Paid", callback_data=f"paid_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{CONTACT_USERNAME}"))
    
    bot.send_photo(call.message.chat.id, qr_url, 
                   caption=f"Plan: {mins} Minutes\nPrice: ₹{price}\nUPI ID: `{UPI_ID}`\n\nPlease complete the payment and click 'I Have Paid'.", 
                   reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('paid_'))
def admin_notify(call):
    _, ch_id, mins = call.data.split('_')
    user = call.from_user
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]
    
    markup = InlineKeyboardMarkup()
    # Preset validity options in days
    markup.add(InlineKeyboardButton("✅ Approve 1 Day", callback_data=f"app_{user.id}_{ch_id}_1"))
    markup.add(InlineKeyboardButton("✅ Approve 7 Days", callback_data=f"app_{user.id}_{ch_id}_7"))
    markup.add(InlineKeyboardButton("✅ Approve 30 Days", callback_data=f"app_{user.id}_{ch_id}_30"))
    # Custom validity option
    markup.add(InlineKeyboardButton("✏️ Enter Custom Validity (Days)", callback_data=f"custom_{user.id}_{ch_id}"))
    markup.add(InlineKeyboardButton("❌ Reject", callback_data=f"rej_{user.id}"))
    
    bot.send_message(ADMIN_ID, f"🔔 *Payment Verification Required!*\n\nUser: {user.first_name}\nChannel: {ch_data['name']}\nPlan Paid: {mins} Minutes\nPrice: ₹{price}\n\nSelect validity:", 
                     reply_markup=markup, parse_mode="Markdown")
    
    u_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{CONTACT_USERNAME}"))
    bot.send_message(call.message.chat.id, "✅ Your payment request has been sent. Please wait for Admin approval.", reply_markup=u_markup)

# --- APPROVAL & EXPIRY (Days-based) ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('app_'))
def approve_now(call):
    _, u_id, ch_id, days = call.data.split('_')
    u_id, ch_id, days = int(u_id), int(ch_id), int(days)
    
    try:
        expiry_datetime = datetime.now() + timedelta(days=days)
        expiry_ts = int(expiry_datetime.timestamp())

        link = bot.create_chat_invite_link(ch_id, member_limit=1, expire_date=expiry_ts)
        
        users_col.update_one({"user_id": u_id, "channel_id": ch_id}, {"$set": {"expiry": expiry_datetime.timestamp()}}, upsert=True)
        
        bot.send_message(u_id, f"🥳 *Payment Approved!*\n\nSubscription: {days} Days\n\nJoin Link: {link.invite_link}\n\n⚠️ Note: This link and your access will expire in {days} days.", parse_mode="Markdown")
        bot.edit_message_text(f"✅ Approved user {u_id} for {days} days.", call.message.chat.id, call.message.message_id)
        
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Error: {e}")

# --- CUSTOM VALIDITY ENTRY (Days) ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('custom_'))
def custom_validity(call):
    _, u_id, ch_id = call.data.split('_')
    u_id, ch_id = int(u_id), int(ch_id)
    msg = bot.send_message(ADMIN_ID, "✏️ Enter validity in days (e.g., 1 for 1 day, 30 for 30 days):")
    bot.register_next_step_handler(msg, finalize_custom_validity, u_id, ch_id)

def finalize_custom_validity(message, u_id, ch_id):
    try:
        days = int(message.text.strip())
        expiry_datetime = datetime.now() + timedelta(days=days)
        expiry_ts = int(expiry_datetime.timestamp())

        link = bot.create_chat_invite_link(ch_id, member_limit=1, expire_date=expiry_ts)
        
        users_col.update_one({"user_id": u_id, "channel_id": ch_id}, {"$set": {"expiry": expiry_datetime.timestamp()}}, upsert=True)
        
        bot.send_message(u_id, f"🥳 *Payment Approved!*\n\nSubscription: {days} Days\n\nJoin Link: {link.invite_link}\n\n⚠️ Note: This link and your access will expire in {days} days.", parse_mode="Markdown")
        bot.send_message(ADMIN_ID, f"✅ Approved user {u_id} with custom validity of {days} days.")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Error: {e}")

# --- EXPIRY HANDLER ---
def kick_expired_users():
    now = datetime.now().timestamp()
    expired_users = users_col.find({"expiry": {"$lte": now}})
    bot_username = bot.get_me().username

    for user in expired_users:
        try:
            bot.ban_chat_member(user['channel_id'], user['user_id'])
            bot.unban_chat_member(user['channel_id'], user['user_id'])
            
            rejoin_url = f"https://t.me/{bot_username}?start={user['channel_id']}"
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔄 Re-join / Renew", url=rejoin_url))
            
            bot.send_message(user['user_id'], "⚠️ Your subscription has expired.\n\nTo join again or renew, please click the button below:", reply_markup=markup)
            users_col.delete_one({"_id": user['_id']})
        except: pass

# --- STARTUP ---
if __name__ == '__main__':
    keep_alive()
    scheduler = BackgroundScheduler()
    scheduler.add_job(kick_expired_users, 'interval', minutes=1)
    scheduler.start()
    bot.remove_webhook()
    print("Bot is running...")
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
