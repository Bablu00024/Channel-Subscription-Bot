# --- APPROVAL & EXPIRY ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('app_'))
def approve_now(call):
    _, u_id, ch_id, mins = call.data.split('_')
    u_id, ch_id, mins = int(u_id), int(ch_id), int(mins)
    try:
        expiry_datetime = datetime.now() + timedelta(minutes=mins)
        expiry_ts = int(expiry_datetime.timestamp())

        # Create invite link that expires when subscription ends
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

@bot.callback_query_handler(func=lambda call: call.data.startswith('manage_'))
def manage_ch(call):
    ch_id = int(call.data.split('_')[1])
    ch_data = channels_col.find_one({"channel_id": ch_id})
    bot_username = bot.get_me().username
    link = f"https://t.me/{bot_username}?start={ch_id}"
    bot.edit_message_text(
        f"Settings for: *{ch_data['name']}*\n\nYour Link: `{link}`\n\n"
        "To edit prices, use /add and forward a message from this channel again.",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="Markdown"
    )

# Automate Kicking
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
if __name__ == '__main__':
    keep_alive()
    scheduler = BackgroundScheduler()
    scheduler.add_job(kick_expired_users, 'interval', minutes=1)
    scheduler.start()

    # Ensure scheduler shuts down cleanly
    atexit.register(lambda: scheduler.shutdown(wait=False))

    bot.remove_webhook()
    print("Bot is running...")
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
