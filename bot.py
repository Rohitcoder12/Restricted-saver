# --- Restricted Content Saver Bot with Premium Features (Complete) ---

import os
import logging
from threading import Thread
from typing import Dict, Any
from functools import wraps

from dotenv import load_dotenv
load_dotenv() # Loads variables from .env for VPS deployment

# Web Server
from flask import Flask

# Telegram Bot
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# Pyrogram
from pyrogram.client import Client
from pyrogram.errors import (
    SessionPasswordNeeded, PhoneCodeInvalid, PasswordHashInvalid,
    FloodWait, PhoneNumberInvalid, PhoneCodeExpired
)
from pyrogram.types import Message

# MongoDB
import pymongo

# --- Configuration ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Credentials from Environment Variables
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", 0))
MONGO_URI = os.getenv("MONGO_URI")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0)) # Your numeric Telegram User ID
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME") # Your Telegram @username

# File Size Limit for Free Users (in MB)
FILE_SIZE_LIMIT_MB = 100
FILE_SIZE_LIMIT_BYTES = FILE_SIZE_LIMIT_MB * 1024 * 1024

# --- Database Setup ---
db_client = None
sessions_collection = None
premium_users_collection = None
try:
    if MONGO_URI:
        db_client = pymongo.MongoClient(MONGO_URI)
        db = db_client.get_database("telegram_bot_db")
        sessions_collection = db.get_collection("user_sessions")
        premium_users_collection = db.get_collection("premium_users")
        logger.info("Successfully connected to MongoDB.")
except Exception as e:
    logger.error(f"FATAL: Could not connect to MongoDB: {e}")

# --- Helper Functions for Database & Sessions ---
def load_sessions() -> Dict[int, str]:
    if not sessions_collection: return {}
    sessions = {}
    try:
        for doc in sessions_collection.find({}):
            sessions[doc["user_id"]] = doc["session_string"]
        logger.info(f"Loaded {len(sessions)} sessions from the database.")
        return sessions
    except Exception as e:
        logger.error(f"Error loading sessions from DB: {e}")
        return {}

def save_session(user_id: int, session_string: str):
    if sessions_collection is not None:
        sessions_collection.update_one({"user_id": user_id}, {"$set": {"session_string": session_string}}, upsert=True)
        logger.info(f"Session saved for user {user_id}")

def delete_session(user_id: int):
    if sessions_collection is not None:
        sessions_collection.delete_one({"user_id": user_id})
        logger.info(f"Session deleted for user {user_id}")

user_sessions = load_sessions()

# --- Helper Functions for Premium System ---
def is_user_premium(user_id: int) -> bool:
    if not premium_users_collection: return False
    return premium_users_collection.find_one({"user_id": user_id}) is not None

def add_premium_user(user_id: int):
    if premium_users_collection is not None:
        premium_users_collection.update_one({"user_id": user_id}, {"$set": {"user_id": user_id}}, upsert=True)

def remove_premium_user(user_id: int):
    if premium_users_collection is not None:
        premium_users_collection.delete_one({"user_id": user_id})

def get_all_premium_users() -> list:
    if not premium_users_collection: return []
    return [doc['user_id'] for doc in premium_users_collection.find({})]

# --- Decorator for Admin-Only Commands ---
def admin_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("❌ This command is for the admin only.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- Admin Commands ---
@admin_only
async def add_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /addpremium <user_id>"); return
    try:
        user_id_to_add = int(context.args[0])
        add_premium_user(user_id_to_add)
        await update.message.reply_text(f"✅ User `{user_id_to_add}` is now premium.", parse_mode='Markdown')
    except (ValueError, IndexError): await update.message.reply_text("❌ Invalid User ID.")

@admin_only
async def remove_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /removepremium <user_id>"); return
    try:
        user_id_to_remove = int(context.args[0])
        remove_premium_user(user_id_to_remove)
        await update.message.reply_text(f"✅ User `{user_id_to_remove}` is no longer premium.", parse_mode='Markdown')
    except (ValueError, IndexError): await update.message.reply_text("❌ Invalid User ID.")

@admin_only
async def list_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    premium_ids = get_all_premium_users()
    if not premium_ids: await update.message.reply_text("No premium users found."); return
    message = "👑 **Premium Users:**\n\n" + "\n".join(f"- `{user_id}`" for user_id in premium_ids)
    await update.message.reply_text(message, parse_mode='Markdown')

# --- Login Conversation Handler ---
GET_PHONE, GET_OTP, GET_2FA = range(3)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        await update.message.reply_text("✅ You are already logged in and ready to go!")
        return ConversationHandler.END
    await update.message.reply_text("🔐 To begin, please send your phone number in international format (e.g., +1234567890).")
    return GET_PHONE

async def get_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone_number = update.message.text.strip()
    user_id = update.effective_user.id
    try:
        client = Client(f"session_{user_id}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        await client.connect()
        sent_code = await client.send_code(phone_number)
        context.user_data.update({'client': client, 'phone_code_hash': sent_code.phone_code_hash, 'phone_number': phone_number})
        await update.message.reply_text("✅ OTP sent! Please send me the code you received.")
        return GET_OTP
    except Exception as e:
        logger.error(f"Phone number error for {user_id}: {e}")
        await update.message.reply_text(f"❌ Error: {e}\nPlease try again with /start.")
        return ConversationHandler.END

async def get_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp = update.message.text.strip()
    client = context.user_data['client']
    try:
        await client.sign_in(context.user_data['phone_number'], context.user_data['phone_code_hash'], otp)
        session_string = await client.export_session_string()
        user_sessions[update.effective_user.id] = session_string
        save_session(update.effective_user.id, session_string)
        await update.message.reply_text("✅ Login Successful! You can now send me links to download.")
        await client.disconnect()
        return ConversationHandler.END
    except SessionPasswordNeeded:
        await update.message.reply_text("🔐 Your account has 2FA enabled. Please send your password.")
        return GET_2FA
    except Exception as e:
        logger.error(f"OTP error for {update.effective_user.id}: {e}")
        await update.message.reply_text(f"❌ Login Failed: {e}\nPlease start over with /start.")
        await context.user_data['client'].disconnect()
        return ConversationHandler.END

async def get_2fa_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    client = context.user_data['client']
    try:
        await client.check_password(password)
        session_string = await client.export_session_string()
        user_sessions[update.effective_user.id] = session_string
        save_session(update.effective_user.id, session_string)
        await update.message.reply_text("✅ 2FA Verified! Login successful.")
        await client.disconnect()
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"2FA error for {update.effective_user.id}: {e}")
        await update.message.reply_text(f"❌ 2FA Failed: {e}\nPlease start over with /start.")
        await client.disconnect()
        return ConversationHandler.END

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'client' in context.user_data and context.user_data['client'].is_connected:
        await context.user_data['client'].disconnect()
    context.user_data.clear()
    await update.message.reply_text("Login process canceled.")
    return ConversationHandler.END

# --- Standard User Commands ---
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    status = "✅ Logged In" if user_id in user_sessions else "❌ Not Logged In"
    premium_status = "👑 Premium" if is_user_premium(user_id) else "🆓 Free Tier"
    await update.message.reply_text(f"**Your Status**\n\nLogin: {status}\nSubscription: {premium_status}", parse_mode='Markdown')

async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
        delete_session(user_id)
        await update.message.reply_text("✅ You have been successfully logged out.")
    else:
        await update.message.reply_text("❌ You are not logged in.")

async def refresh_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
        delete_session(user_id)
        await update.message.reply_text("🔄 Session refreshed. Please /start again to log in with your updated chat list.")
    else:
        await update.message.reply_text("🤔 You are not logged in. Use /start to begin.")

# --- Media Handling and Download Logic ---
async def _send_downloaded_media(update: Update, context: ContextTypes.DEFAULT_TYPE, file_path: str, message: Message):
    caption = message.caption or ""
    with open(file_path, 'rb') as file:
        if message.photo: await context.bot.send_photo(update.effective_chat.id, file, caption=caption)
        elif message.video: await context.bot.send_video(update.effective_chat.id, file, caption=caption)
        elif message.audio: await context.bot.send_audio(update.effective_chat.id, file, caption=caption)
        elif message.voice: await context.bot.send_voice(update.effective_chat.id, file, caption=caption)
        elif message.animation: await context.bot.send_animation(update.effective_chat.id, file, caption=caption)
        else: await context.bot.send_document(update.effective_chat.id, file, caption=caption)

def get_media_file_size(message: Message) -> int:
    for media_type in ["video", "document", "audio", "voice", "photo", "animation"]:
        media = getattr(message, media_type, None)
        if media and hasattr(media, 'file_size') and media.file_size:
            return media.file_size
    return 0

async def handle_message_with_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions: await update.message.reply_text("🔐 You need to login first. Use /start."); return
    
    message_text = update.message.text.strip()
    progress_msg = await update.message.reply_text("⏳ Analyzing link...")

    try:
        if 't.me' not in message_text: await progress_msg.edit_text("❌ This is not a valid Telegram link."); return

        parts = message_text.split('/')
        msg_id = int(parts[-1].split('?')[0])
        chat_id_str = parts[-2]
        chat_id = int(f"-100{chat_id_str}") if '/c/' in message_text else chat_id_str
        
        await progress_msg.edit_text("⏳ Connecting to your user session...")
        
        session_string = user_sessions[user_id]
        user_client = Client(f"temp_{user_id}", session_string=session_string, api_id=API_ID, api_hash=API_HASH)
        
        async with user_client:
            await progress_msg.edit_text("⏳ Accessing the message...")
            message = await user_client.get_messages(chat_id, msg_id)
            if not message: raise Exception("Message not found or inaccessible.")

            if message.media:
                file_size = get_media_file_size(message)
                if file_size > FILE_SIZE_LIMIT_BYTES and not is_user_premium(user_id):
                    limit_msg = (f"⚠️ **File Size Limit Exceeded**\n\nFile is **{file_size / 1024 / 1024:.2f} MB**. "
                                 f"Your limit is **{FILE_SIZE_LIMIT_MB} MB**.\n\n"
                                 f"👑 For large files, contact @{ADMIN_USERNAME} for premium.")
                    await progress_msg.edit_text(limit_msg); return

            if message.media:
                await progress_msg.edit_text("Downloading protected content...")
                file_path = await user_client.download_media(message)
                if file_path:
                    await progress_msg.edit_text("✅ Uploading to you...")
                    await _send_downloaded_media(update, context, file_path, message)
                    if os.path.exists(file_path): os.remove(file_path)
                else: raise Exception("Failed to download media.")
            elif message.text:
                await context.bot.send_message(update.effective_chat.id, f"📝 **Content from source:**\n\n{message.text}", parse_mode='Markdown')
            else: await progress_msg.edit_text("❌ No downloadable content found."); return
            
            await progress_msg.delete()
            log_message = f"📥 **#Download**\n👤 User: {update.effective_user.full_name} (`{user_id}`)\n🔗 Link: `{message_text}`"
            await context.bot.send_message(LOG_CHANNEL_ID, log_message, parse_mode='Markdown')

    except Exception as e:
        error_text = str(e)
        logger.error(f"Error for user {user_id}: {error_text}")
        if "Peer id invalid" in error_text:
            error_msg = "❌ **Failed to Access Chat.** Your session is out of date. Please use /refresh and log in again."
        else:
            error_msg = f"❌ **An error occurred:** `{error_text}`"
        await progress_msg.edit_text(error_msg, parse_mode='Markdown')

# --- Web Server for Health Checks ---
app = Flask(__name__)
@app.route('/')
def home(): return {"status": "alive", "bot": "Restricted Content Saver Bot"}

def run_flask():
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)

# --- Main Bot Application ---
def main():
    required_vars = ["API_ID", "API_HASH", "BOT_TOKEN", "LOG_CHANNEL_ID", "MONGO_URI", "ADMIN_ID", "ADMIN_USERNAME"]
    missing_vars = [v for v in required_vars if not os.getenv(v)]
    if missing_vars:
        logger.critical(f"CRITICAL: Missing environment variables: {', '.join(missing_vars)}"); return
    if not db_client:
        logger.critical("CRITICAL: Bot cannot start without a database connection."); return

    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start_command)],
        states={
            GET_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone_number)],
            GET_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_otp)],
            GET_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_2fa_password)],
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
        conversation_timeout=300
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('status', status_command))
    application.add_handler(CommandHandler('logout', logout_command))
    application.add_handler(CommandHandler('refresh', refresh_command))
    application.add_handler(CommandHandler('addpremium', add_premium_command))
    application.add_handler(CommandHandler('removepremium', remove_premium_command))
    application.add_handler(CommandHandler('listpremium', list_premium_command))
    application.add_handler(MessageHandler(filters.TEXT & filters.Entity("url") & ~filters.COMMAND, handle_message_with_link))

    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    logger.info("Bot started successfully with all features!")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()