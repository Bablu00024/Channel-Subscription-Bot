import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
from flask import Flask, request

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
UPI_ID = os.getenv("UPI_ID")

bot = telebot.TeleBot(BOT_TOKEN)
client = MongoClient(MONGO_URI)
db = client['sub_management']
channels_col = db['channels']
users_col = db['users']

# --- APScheduler Job ---
def kick_expired_users():
    now = datetime.now().timestamp()
    expired_users = users_col.find({"expiry": {"$lt": now}})
    for user in expired_users:
        try:
            bot.kick_chat_member(user['channel_id'], user['user_id'])
            bot.send_message(user['user_id'], "⚠️ Your subscription has expired. Please renew.")
            users_col.delete_one({"_id": user["_id"]})
        except Exception as e:
            print(f"Error kicking user {user['user_id']}: {e}")

scheduler = BackgroundScheduler()
scheduler.add_job(kick_expired_users, 'interval', minutes=1)
scheduler.start()
atexit.register(lambda: scheduler.shutdown(wait=False))

# --- Flask app for webhook ---
app = Flask(__name__)

@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    update = telebot.types.Update.de_json(request.stream.read().decode("utf-8"))
    bot.process_new_updates([update])
    return "OK", 200

@app.route('/')
def index():
    return "Bot is running", 200

# --- START HANDLER ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    text = message.text.split()
    if len(text) > 1:
        try:
            ch_id = int(text[1])
            ch_data = channels_col.find_one({"channel_id": ch_id})
            if ch_data:
                markup = InlineKeyboardMarkup()
                for p_time, p_price in ch_data['plans'].items():
                    markup.add(InlineKeyboardButton(
                        f"💳 Plan ₹{p_price}",
                        callback_data=f"select_{ch_id}_{p_time}"
                    ))
                contact_username = ch_data.get("contact_username", "")
                if contact_username:
                    markup.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{contact_username}"))
                bot.send_message(message.chat.id,
                    f"Welcome!\n\nYou are joining: *{ch_data['name']}*.\n\nPlease select a subscription plan below:",
                    reply_markup=markup, parse_mode="Markdown")
                return
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Error: {e}")
    else:
        bot.send_message(message.chat.id, "Welcome! To join a channel, please use the link provided by the Admin.")

# --- CHANNEL MANAGEMENT ---
@bot.message_handler(commands=['add'])
def add_channel_start(message):
    msg = bot.send_message(message.chat.id, "Forward any message from your channel here (bot must be admin).")
    bot.register_next_step_handler(msg, get_plans)

def get_plans(message):
    if message.forward_from_chat:
        ch_id = message.forward_from_chat.id
        ch_name = message.forward_from_chat.title
        msg = bot.send_message(message.chat.id,
            f"Channel Detected: *{ch_name}*\n\nEnter prices only (comma separated):\nExample: `99, 199`",
            parse_mode="Markdown")
        bot.register_next_step_handler(msg, finalize_channel, ch_id, ch_name, message.from_user.id)
    else:
        bot.send_message(message.chat.id, "❌ Error: Message was not forwarded. Use /add again.")

def finalize_channel(message, ch_id, ch_name, admin_id):
    raw_prices = message.text.split(',')
    plans_dict = {str(idx): pr.strip() for idx, pr in enumerate(raw_prices, start=1)}
    msg = bot.send_message(admin_id, "Enter your contact username (without @):")
    bot.register_next_step_handler(msg, save_channel, ch_id, ch_name, plans_dict, admin_id)

def save_channel(message, ch_id, ch_name, plans_dict, admin_id):
    contact_username = message.text.strip().lstrip('@')
    channels_col.update_one(
        {"channel_id": ch_id},
        {"$set": {
            "name": ch_name,
            "plans": plans_dict,
            "admin_id": admin_id,
            "contact_username": contact_username
        }},
        upsert=True
    )
    bot_username = bot.get_me().username
    bot.send_message(admin_id, f"✅ Setup Successful!\n\nInvite Link:\n`https://t.me/{bot_username}?start={ch_id}`", parse_mode="Markdown")

# --- USER PAYMENT FLOW ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('select_'))
def user_pays(call):
    _, ch_id, plan_id = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][plan_id]
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=upi://pay?pa={UPI_ID}%26am={price}%26cu=INR"
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ I Have Paid", callback_data=f"paid_{ch_id}_{plan_id}"))
    contact_username = ch_data.get("contact_username", "")
    if contact_username:
        markup.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{contact_username}"))
    bot.send_photo(call.message.chat.id, qr_url,
                   caption=f"Plan Price: ₹{price}\nUPI ID: `{UPI_ID}`\n\nComplete payment and click 'I Have Paid'.",
                   reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('paid_'))
def admin_notify(call):
    _, ch_id, plan_id = call.data.split('_')
    user = call.from_user
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][plan_id]
    admin_id = ch_data['admin_id']
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Approve", callback_data=f"app_{user.id}_{ch_id}_{plan_id}"))
    markup.add(InlineKeyboardButton("❌ Reject", callback_data=f"rej_{user.id}_{ch_id}"))
    bot.send_message(admin_id, f"🔔 *Payment Verification Required!*\n\nUser: {user.first_name}\nChannel: {ch_data['name']}\nPrice: ₹{price}",
                     reply_markup=markup, parse_mode="Markdown")
    contact_username = ch_data.get("contact_username", "")
    u_markup = InlineKeyboardMarkup()
    if contact_username:
        u_markup.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{contact_username}"))
    bot.send_message(call.message.chat.id, "✅ Your payment request has been sent. Please wait for Admin approval.", reply_markup=u_markup)

# --- APPROVAL FLOW ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('app_'))
def approve_now(call):
    _, u_id, ch_id, _ = call.data.split('_')
    u_id, ch_id = int(u_id), int(ch_id)
    ch_data = channels_col.find_one({"channel_id": ch_id})
    admin_id = ch_data['admin_id']
    msg = bot.send_message(admin_id, f"Enter validity (in days) for user {u_id} in channel {ch_id}:")
    bot.register_next_step_handler(msg, finalize_approval, u_id, ch_id, admin_id)

def finalize_approval(message, u_id, ch_id, admin_id):
    try:
        validity_days = int(message.text.strip())
        expiry_datetime = datetime.now() + timedelta(days=validity_days)
        link_expiry = int((datetime.now() + timedelta(minutes=5)).timestamp())
        link = bot.create_chat_invite_link(ch_id, member_limit=1, expire_date=link_expiry)
        users_col.update_one({"user_id": u_id, "channel_id": ch_id}, {"$set": {"expiry": expiry_datetime.timestamp()}}, upsert=True)
        bot.send_message(u_id, f"🥳 *Payment Approved!*\n\nSubscription: {validity_days} Days\n\nJoin Link: {link.invite_link}\n\n⚠️ Note: This link will expire in 5 minutes. Please join immediately.\nYour access will last for {validity_days} days after joining.", parse_mode="Markdown")
        bot.send_message(admin_id, f"✅ Approved user {u_id} for {validity_days} days. Link expires in 5 minutes.")
    except Exception as e:
        bot.send_message(admin_id, f"❌ Error: {e}")

# --- REJECT FLOW ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('rej_
