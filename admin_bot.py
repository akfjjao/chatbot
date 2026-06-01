import logging
import asyncio
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import BOT_TOKEN, ADMIN_ID
import database as db
from userbot_manager import UserbotManager
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

logger = logging.getLogger(__name__)

# Initialize Telebot
bot = telebot.TeleBot(BOT_TOKEN)

# Reference to the main asyncio event loop (set in main.py)
loop = None

# In-memory states and caches
pending_logins = {}  # chat_id -> auth state dictionary
admin_states = {}    # chat_id -> action state dictionary
chat_cache = {}      # phone -> list of dict chats fetched from userbot

def is_admin(user_id):
    if ADMIN_ID == 0:
        return True  # If admin ID is not set, allow anyone for debugging/local setup
    return user_id == ADMIN_ID

# ==========================================
#          MAIN DASHBOARD & KEYBOARDS
# ==========================================

def get_main_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📱 Manage Accounts", callback_data="manage_accounts"),
        InlineKeyboardButton("🎯 Targets", callback_data="manage_targets")
    )
    markup.row(
        InlineKeyboardButton("🔗 Forwarding Rules", callback_data="manage_rules"),
        InlineKeyboardButton("📊 Refresh Stats", callback_data="refresh_stats")
    )
    return markup

@bot.message_handler(commands=['start', 'dashboard'])
def start_cmd(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "❌ *Access Denied.* You are not authorized to access this control panel.", parse_mode="Markdown")
        return

    # Fetch stats
    total_forwarded, total_bots, total_mappings = db.get_stats()
    
    text = (
        "💎 *System Control Dashboard*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Welcome to the Premium Telegram Multi-Account Forwarder (Telethon Edition).\n\n"
        "📈 *Live Statistics*:\n"
        f" • Connected Userbots: `{total_bots}`\n"
        f" • Active Forwarding Rules: `{total_mappings}`\n"
        f" • Messages Forwarded: `{total_forwarded}`\n\n"
        "Select an action below to begin configuration:"
    )
    
    bot.send_message(
        message.chat.id, 
        text, 
        reply_markup=get_main_keyboard(), 
        parse_mode="Markdown"
    )

# ==========================================
#          TELETHON DYNAMIC LOGIN STEPS
# ==========================================

async def initiate_telethon_client(chat_id, phone, api_id, api_hash):
    """Starts the dynamic Telethon connection and requests OTP code."""
    logger.info(f"Initiating Telethon login for {phone}...")
    client = TelegramClient(StringSession(), api_id, api_hash)
    
    try:
        await client.connect()
        # Telethon send_code_request
        result = await client.send_code_request(phone)
        
        # Save temp client and code hash to state
        pending_logins[chat_id].update({
            "client": client,
            "phone_code_hash": result.phone_code_hash,
            "state": "AWAITING_OTP"
        })
        
        bot.send_message(
            chat_id,
            "📩 *Verification Code Sent*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Telegram has sent a verification code to `{phone}`.\n\n"
            "Please *reply directly to this message* with the OTP code:",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error during Telethon code generation: {e}")
        try:
            await client.disconnect()
        except:
            pass
        pending_logins.pop(chat_id, None)
        bot.send_message(
            chat_id,
            f"❌ *Failed to send verification code*:\n`{e}`\n\nPlease check your credentials and try again using /dashboard.",
            parse_mode="Markdown"
        )

async def verify_telethon_otp(chat_id, otp_code):
    """Verifies OTP code and signs in user."""
    info = pending_logins.get(chat_id)
    if not info:
        return

    client = info["client"]
    phone = info["phone"]
    phone_code_hash = info["phone_code_hash"]
    api_id = info["api_id"]
    api_hash = info["api_hash"]

    try:
        # Sign in with code
        user = await client.sign_in(phone, code=otp_code, phone_code_hash=phone_code_hash)
        
        # Export string session
        session_string = client.session.save()
        first_name = user.first_name or "Userbot"
        
        # Save to PostgreSQL
        db.add_userbot(phone, api_id, api_hash, session_string, first_name)
        
        # Start in dynamic manager
        manager = UserbotManager()
        await manager.start_userbot(phone, api_id, api_hash, session_string)
        
        bot.send_message(
            chat_id,
            "🟢 *Authentication Successful!*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Account: `{phone}` (`{first_name}`)\n\n"
            "The userbot has been successfully connected and activated in the background.",
            parse_mode="Markdown"
        )
        
        # We do not disconnect here because the manager has spun its own running instance.
        # However, we disconnect our temporary login client to clean up resources.
        await client.disconnect()
        pending_logins.pop(chat_id, None)
    except SessionPasswordNeededError:
        info["state"] = "AWAITING_2FA"
        bot.send_message(
            chat_id,
            "🔒 *Two-Step Verification Required*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Your account has a 2FA password enabled. Please reply with your 2FA password:",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error during OTP verification: {e}")
        bot.send_message(
            chat_id,
            f"❌ *Verification Error*:\n`{e}`\n\nPlease check your code or try again.",
            parse_mode="Markdown"
        )

async def verify_telethon_2fa(chat_id, password):
    """Verifies 2FA password and signs in user."""
    info = pending_logins.get(chat_id)
    if not info:
        return

    client = info["client"]
    phone = info["phone"]
    api_id = info["api_id"]
    api_hash = info["api_hash"]

    try:
        # Check password
        user = await client.sign_in(password=password)
        
        # Save string session
        session_string = client.session.save()
        first_name = user.first_name or "Userbot"
        
        db.add_userbot(phone, api_id, api_hash, session_string, first_name)
        
        manager = UserbotManager()
        await manager.start_userbot(phone, api_id, api_hash, session_string)
        
        bot.send_message(
            chat_id,
            "🟢 *Authentication Successful!*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Account: `{phone}` (`{first_name}`) (2FA Bypass)\n\n"
            "The userbot has been successfully connected and activated in the background.",
            parse_mode="Markdown"
        )
        
        await client.disconnect()
        pending_logins.pop(chat_id, None)
    except Exception as e:
        logger.error(f"Error during 2FA check: {e}")
        bot.send_message(
            chat_id,
            f"❌ *2FA Authentication Failed*:\n`{e}`\n\nPlease enter the correct password:",
            parse_mode="Markdown"
        )

# ==========================================
#          CALLBACK HANDLERS
# ==========================================

@bot.callback_query_handler(func=lambda call: True)
def callback_dispatcher(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Access Denied.", show_alert=True)
        return

    data = call.data
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    manager = UserbotManager()

    try:
        bot.answer_callback_query(call.id)
    except:
        pass

    # --- MAIN MENU / REFRESH ---
    if data == "refresh_stats" or data == "main_menu":
        total_forwarded, total_bots, total_mappings = db.get_stats()
        text = (
            "💎 *System Control Dashboard*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Welcome to the Premium Telegram Multi-Account Forwarder (Telethon Edition).\n\n"
            "📈 *Live Statistics*:\n"
            f" • Connected Userbots: `{total_bots}`\n"
            f" • Active Forwarding Rules: `{total_mappings}`\n"
            f" • Messages Forwarded: `{total_forwarded}`\n\n"
            "Select an action below to begin configuration:"
        )
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=get_main_keyboard(), parse_mode="Markdown")
        except:
            pass

    # --- ACCOUNT MANAGEMENT ---
    elif data == "manage_accounts":
        userbots = db.get_all_userbots()
        text = (
            "📱 *Manage Accounts*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Here is the list of connected Telegram accounts. Click on any account to configure it:"
        )
        markup = InlineKeyboardMarkup()
        for phone, _, _, _, name, is_active in userbots:
            status_icon = "🟢" if manager.is_running(phone) else "🔴"
            markup.row(InlineKeyboardButton(f"{status_icon} {name} ({phone})", callback_data=f"acc_detail:{phone}"))
            
        markup.row(InlineKeyboardButton("➕ Add Account", callback_data="add_account"))
        markup.row(InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu"))
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode="Markdown")

    elif data == "add_account":
        pending_logins[chat_id] = {"state": "AWAITING_PHONE"}
        bot.send_message(
            chat_id,
            "📱 *Add New Userbot Account (Step 1/3)*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Please send the phone number of the account you wish to connect (including country code, e.g. `+1234567890`):",
            parse_mode="Markdown"
        )

    elif data.startswith("acc_detail:"):
        phone = data.split(":")[1]
        userbot = db.get_userbot(phone)
        if not userbot:
            bot.send_message(chat_id, "❌ Userbot account not found.")
            return

        phone, api_id, api_hash, _, name, is_active = userbot
        is_running = manager.is_running(phone)
        status_text = "🟢 *Active & Listening*" if is_running else "🔴 *Stopped*"
        
        text = (
            f"📱 *Account Detail: {name}*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Name: `{name}`\n"
            f"📞 Phone: `{phone}`\n"
            f"🔑 API ID: `{api_id}`\n"
            f"📊 Status: {status_text}\n\n"
            "Use the controls below to configure this account:"
        )
        
        markup = InlineKeyboardMarkup()
        
        # Toggle running state button
        if is_running:
            markup.row(InlineKeyboardButton("🛑 Stop Userbot", callback_data=f"acc_toggle:{phone}:stop"))
        else:
            markup.row(InlineKeyboardButton("⚡ Start Userbot", callback_data=f"acc_toggle:{phone}:start"))

        markup.row(
            InlineKeyboardButton("📢 List Chats", callback_data=f"list_chats:{phone}:0"),
            InlineKeyboardButton("🔗 Mappings", callback_data=f"acc_mappings:{phone}")
        )
        markup.row(InlineKeyboardButton("🗑 Delete Account", callback_data=f"acc_delete_confirm:{phone}"))
        markup.row(InlineKeyboardButton("🔙 Back", callback_data="manage_accounts"))
        
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("acc_toggle:"):
        phone, action = data.split(":")[1:3]
        userbot = db.get_userbot(phone)
        if userbot:
            phone, api_id, api_hash, session, _, _ = userbot
            if action == "start":
                bot.send_message(chat_id, f"⚙️ Attempting to boot userbot `{phone}`...")
                future = asyncio.run_coroutine_threadsafe(
                    manager.start_userbot(phone, api_id, api_hash, session),
                    loop
                )
                try:
                    success = future.result(timeout=20)
                    if success:
                        bot.send_message(chat_id, f"🟢 Telethon Userbot `{phone}` started successfully!")
                    else:
                        bot.send_message(chat_id, f"❌ Failed to start Telethon Userbot `{phone}`. Verification/Session issue.")
                except Exception as e:
                    bot.send_message(chat_id, f"❌ Timeout starting Telethon Userbot `{phone}`: {e}")
            else:
                future = asyncio.run_coroutine_threadsafe(manager.stop_userbot(phone), loop)
                future.result()
                bot.send_message(chat_id, f"🔴 Telethon Userbot `{phone}` stopped successfully.")
        
        callback_dispatcher(telebot.types.CallbackQuery(call.id, call.from_user, call.message, call.inline_message_id, f"acc_detail:{phone}"))

    elif data.startswith("acc_delete_confirm:"):
        phone = data.split(":")[1]
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("🔥 Yes, Delete", callback_data=f"acc_delete_exec:{phone}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"acc_detail:{phone}")
        )
        bot.edit_message_text(
            f"⚠️ *Are you absolutely sure?*\n\nThis will stop Telethon userbot `{phone}` and permanently erase its configurations and mappings from the PostgreSQL database.",
            chat_id,
            message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

    elif data.startswith("acc_delete_exec:"):
        phone = data.split(":")[1]
        future = asyncio.run_coroutine_threadsafe(manager.stop_userbot(phone), loop)
        future.result()
        db.remove_userbot(phone)
        bot.send_message(chat_id, f"🗑 Account `{phone}` deleted and stopped.")
        callback_dispatcher(telebot.types.CallbackQuery(call.id, call.from_user, call.message, call.inline_message_id, "manage_accounts"))

    # --- CHAT LISTING & PAGINATION (SOURCE MAPPINGS) ---
    elif data.startswith("list_chats:"):
        phone, page_str = data.split(":")[1:3]
        page = int(page_str)
        is_running = manager.is_running(phone)
        
        if not is_running:
            bot.answer_callback_query(call.id, "❌ Please start the userbot first!", show_alert=True)
            return

        bot.send_message(chat_id, "🔍 *Fetching chats & entities...* (This may take a moment)", parse_mode="Markdown")
        
        # Cache dialogs
        if phone not in chat_cache:
            future = asyncio.run_coroutine_threadsafe(manager.get_chats(phone), loop)
            chats = future.result()
            chat_cache[phone] = chats
        else:
            chats = chat_cache[phone]

        if not chats:
            bot.send_message(chat_id, "🤷 No active groups, channels, or chats found for this account.")
            return

        # Pagination
        page_size = 7
        total_pages = (len(chats) + page_size - 1) // page_size
        start_idx = page * page_size
        end_idx = start_idx + page_size
        chats_slice = chats[start_idx:end_idx]

        markup = InlineKeyboardMarkup()
        
        # Ensure admin_states has a sub-dictionary for options to avoid looping overwrite
        if chat_id not in admin_states or not isinstance(admin_states[chat_id], dict):
            admin_states[chat_id] = {}
        if "options" not in admin_states[chat_id]:
            admin_states[chat_id]["options"] = {}

        for chat in chats_slice:
            chat_id_val = chat["chat_id"]
            chat_title = chat["title"]
            emoji = chat["emoji"]
            chat_type = chat["type"]

            btn_text = f"{emoji} {chat_title}"
            
            # Save mapping state keyed by source ID under the admin's chat session
            admin_states[chat_id]["options"][chat_id_val] = {
                "phone": phone,
                "source_id": chat_id_val,
                "source_title": chat_title,
                "source_type": chat_type
            }
            markup.row(InlineKeyboardButton(btn_text, callback_data=f"map_src:{chat_id_val}"))

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"list_chats:{phone}:{page-1}"))
        nav_row.append(InlineKeyboardButton(f"Page {page+1}/{total_pages}", callback_data="noop"))
        if end_idx < len(chats):
            nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"list_chats:{phone}:{page+1}"))
        markup.row(*nav_row)

        markup.row(InlineKeyboardButton("🔄 Refresh Chats List", callback_data=f"clear_cache_list:{phone}:src"))
        markup.row(InlineKeyboardButton("🔙 Back to Details", callback_data=f"acc_detail:{phone}"))

        bot.send_message(
            chat_id,
            f"📢 *Source Chats: {phone}*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Here are the chats your userbot account is currently in, classified by type.\n\n"
            "👉 *Click on any chat* to configure forwarding from it:",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    elif data.startswith("clear_cache_list:"):
        phone, action = data.split(":")[1:3]
        chat_cache.pop(phone, None)
        if action == "src":
            callback_dispatcher(telebot.types.CallbackQuery(call.id, call.from_user, call.message, call.inline_message_id, f"list_chats:{phone}:0"))
        else:
            callback_dispatcher(telebot.types.CallbackQuery(call.id, call.from_user, call.message, call.inline_message_id, f"add_target_list:{phone}:0"))

    # --- FORWARDING RULE MAPPING CREATION ---
    elif data.startswith("map_src:"):
        source_id = int(data.split(":")[1])
        admin_data = admin_states.get(chat_id)
        options = admin_data.get("options", {}) if isinstance(admin_data, dict) else {}
        state = options.get(source_id)
        
        if not state:
            bot.send_message(chat_id, "❌ Interaction session expired. Please list chats and try again.")
            return

        # Save selected source chat in admin_states for the next callback step (map_tgt)
        admin_states[chat_id]["selected_source"] = state

        targets = db.get_all_targets()
        if not targets:
            bot.send_message(
                chat_id, 
                "❌ *No Target Groups configured.*\n\n"
                "Please configure at least one Target Group first in the **Targets Menu** before mapping forwarding rules.", 
                parse_mode="Markdown"
            )
            return

        markup = InlineKeyboardMarkup()
        for target_id, title in targets:
            markup.row(InlineKeyboardButton(f"🎯 {title}", callback_data=f"map_tgt:{target_id}"))
            
        markup.row(InlineKeyboardButton("🔙 Cancel", callback_data=f"acc_detail:{state['phone']}"))

        bot.send_message(
            chat_id,
            "🔗 *Set Forwarding Target*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 Source Userbot: `{state['phone']}`\n"
            f"📢 Source Chat: `{state['source_title']}` (`{source_id}`)\n\n"
            "Please select the **Target Group** to forward real-time messages to:",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    elif data.startswith("map_tgt:"):
        target_id = int(data.split(":")[1])
        admin_data = admin_states.pop(chat_id, None)
        state = admin_data.get("selected_source") if isinstance(admin_data, dict) else None
        
        if not state:
            bot.send_message(chat_id, "❌ Session expired.")
            return

        phone = state["phone"]
        source_id = state["source_id"]
        source_title = state["source_title"]
        source_type = state["source_type"]

        success = db.add_mapping(phone, source_id, source_title, source_type, target_id)
        if success:
            bot.send_message(
                chat_id,
                "✅ *Forwarding Mapping Activated!*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"Phone: `{phone}`\n"
                f"Source: `{source_title}`\n"
                f"Target ID: `{target_id}`\n\n"
                "Any *new incoming message* in the source chat will now be instantly forwarded to the target in real-time!",
                parse_mode="Markdown"
            )
        else:
            bot.send_message(chat_id, "❌ Failed to create forwarding rule (might be a duplicate).")

        callback_dispatcher(telebot.types.CallbackQuery(call.id, call.from_user, call.message, call.inline_message_id, f"acc_detail:{phone}"))

    # --- TARGETS MANAGEMENT (DYNAMIC LISTING) ---
    elif data == "manage_targets":
        targets = db.get_all_targets()
        text = (
            "🎯 *Target Groups & Channels*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "These are the groups where userbots will forward intercepted messages. Click a group to delete it from targets:"
        )
        markup = InlineKeyboardMarkup()
        for target_id, title in targets:
            markup.row(InlineKeyboardButton(f"🗑 {title} ({target_id})", callback_data=f"del_tgt:{target_id}"))
            
        markup.row(InlineKeyboardButton("➕ Add Target Group (Dynamic)", callback_data="add_target"))
        markup.row(InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu"))
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode="Markdown")

    elif data == "add_target":
        userbots = db.get_all_userbots()
        if not userbots:
            bot.send_message(chat_id, "❌ Please add at least one Userbot Account first to fetch targets dynamically!")
            return

        markup = InlineKeyboardMarkup()
        for phone, _, _, _, name, _ in userbots:
            status_icon = "🟢" if manager.is_running(phone) else "🔴"
            markup.row(InlineKeyboardButton(f"{status_icon} Select {name} ({phone})", callback_data=f"add_target_list:{phone}:0"))
            
        markup.row(InlineKeyboardButton("🔙 Cancel", callback_data="manage_targets"))
        bot.edit_message_text(
            "🎯 *Add Target Group: Select Account*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Please select which active userbot account's chat list you want to browse to choose a target group:",
            chat_id,
            message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

    elif data.startswith("add_target_list:"):
        phone, page_str = data.split(":")[1:3]
        page = int(page_str)
        is_running = manager.is_running(phone)
        
        if not is_running:
            bot.answer_callback_query(call.id, "❌ Please start this userbot first to fetch chats!", show_alert=True)
            return

        bot.send_message(chat_id, "🔍 *Fetching chats...* (This may take a moment)", parse_mode="Markdown")
        
        # Cache dialogs
        if phone not in chat_cache:
            future = asyncio.run_coroutine_threadsafe(manager.get_chats(phone), loop)
            chats = future.result()
            chat_cache[phone] = chats
        else:
            chats = chat_cache[phone]

        if not chats:
            bot.send_message(chat_id, "🤷 No active groups or channels found for this account.")
            return

        # Pagination
        page_size = 7
        total_pages = (len(chats) + page_size - 1) // page_size
        start_idx = page * page_size
        end_idx = start_idx + page_size
        chats_slice = chats[start_idx:end_idx]

        markup = InlineKeyboardMarkup()
        
        # Ensure admin_states has a targets_options sub-dictionary to avoid loop overwrites
        if chat_id not in admin_states or not isinstance(admin_states[chat_id], dict):
            admin_states[chat_id] = {}
        if "targets_options" not in admin_states[chat_id]:
            admin_states[chat_id]["targets_options"] = {}

        for chat in chats_slice:
            chat_id_val = chat["chat_id"]
            chat_title = chat["title"]
            emoji = chat["emoji"]

            btn_text = f"{emoji} {chat_title}"
            
            # Save selected target details keyed by target ID
            admin_states[chat_id]["targets_options"][chat_id_val] = {
                "phone": phone,
                "target_chat_id": chat_id_val,
                "target_title": chat_title
            }
            # Callback points to target selection save
            markup.row(InlineKeyboardButton(btn_text, callback_data=f"save_tgt_exec:{chat_id_val}"))

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"add_target_list:{phone}:{page-1}"))
        nav_row.append(InlineKeyboardButton(f"Page {page+1}/{total_pages}", callback_data="noop"))
        if end_idx < len(chats):
            nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"add_target_list:{phone}:{page+1}"))
        markup.row(*nav_row)

        markup.row(InlineKeyboardButton("🔄 Refresh Chats List", callback_data=f"clear_cache_list:{phone}:tgt"))
        markup.row(InlineKeyboardButton("🔙 Cancel", callback_data="manage_targets"))

        bot.send_message(
            chat_id,
            f"🎯 *Select Target Group: {phone}*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Browse your userbot's chats below.\n\n"
            "👉 *Click on any group, topic group, channel, or folder* to register it as a target group instantly:",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    elif data.startswith("save_tgt_exec:"):
        target_id = int(data.split(":")[1])
        admin_data = admin_states.pop(chat_id, None)
        targets_options = admin_data.get("targets_options", {}) if isinstance(admin_data, dict) else {}
        state = targets_options.get(target_id)
        
        if not state:
            bot.send_message(chat_id, "❌ Target registration session expired.")
            return

        title = state["target_title"]
        success = db.add_target(target_id, title)
        if success:
            bot.send_message(
                chat_id,
                "✅ *Target Group Registered Dynamically!*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"Title: `{title}`\n"
                f"Chat ID: `{target_id}`\n\n"
                "This chat is now active in the system. You can map forwarding rules to it directly from any userbot account!",
                parse_mode="Markdown"
            )
        else:
            bot.send_message(chat_id, "❌ Failed to register target group.")

        callback_dispatcher(telebot.types.CallbackQuery(call.id, call.from_user, call.message, call.inline_message_id, "manage_targets"))

    elif data.startswith("del_tgt:"):
        target_id = int(data.split(":")[1])
        db.remove_target(target_id)
        bot.send_message(chat_id, "🗑 Target group deleted successfully.")
        callback_dispatcher(telebot.types.CallbackQuery(call.id, call.from_user, call.message, call.inline_message_id, "manage_targets"))

    # --- FORWARDING RULES (ALL MAPPINGS) ---
    elif data == "manage_rules" or data.startswith("acc_mappings:"):
        phone_filter = data.split(":")[1] if ":" in data else None
        
        if phone_filter:
            mappings = db.get_mappings_for_userbot(phone_filter)
            title_text = f"🔗 *Forwarding Rules: {phone_filter}*"
        else:
            mappings = db.get_all_mappings()
            title_text = "🔗 *All System Forwarding Rules*"

        text = (
            f"{title_text}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Here are the established mappings. Toggle or delete them directly:"
        )
        
        markup = InlineKeyboardMarkup()
        for mapping in mappings:
            if phone_filter:
                m_id, src_id, src_title, src_type, tgt_id, tgt_title, is_active = mapping
                mapping_desc = f"• `{src_title}` ➡️ `{tgt_title}`"
            else:
                m_id, phone, name, src_id, src_title, tgt_id, tgt_title, is_active = mapping
                mapping_desc = f"👤 `{name}`\n• `{src_title}` ➡️ `{tgt_title}`"
                
            status_emoji = "🟢 Active" if is_active else "🔴 Paused"
            bot.send_message(chat_id, f"{mapping_desc}\nStatus: {status_emoji}", parse_mode="Markdown")
            
            toggle_text = "⏸ Pause Rule" if is_active else "▶️ Resume Rule"
            markup.row(
                InlineKeyboardButton(toggle_text, callback_data=f"toggle_rule:{m_id}:{0 if is_active else 1}:{phone_filter or 'all'}"),
                InlineKeyboardButton("🗑 Delete", callback_data=f"del_rule:{m_id}:{phone_filter or 'all'}")
            )

        markup.row(InlineKeyboardButton("🔙 Back to Main Dashboard", callback_data="main_menu"))
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("toggle_rule:"):
        m_id, val, ref = data.split(":")[1:4]
        db.toggle_mapping(int(m_id), bool(int(val)))
        bot.send_message(chat_id, "✅ Rule toggled successfully.")
        
        ref_call = f"acc_mappings:{ref}" if ref != "all" else "manage_rules"
        callback_dispatcher(telebot.types.CallbackQuery(call.id, call.from_user, call.message, call.inline_message_id, ref_call))

    elif data.startswith("del_rule:"):
        m_id, ref = data.split(":")[1:3]
        db.remove_mapping(int(m_id))
        bot.send_message(chat_id, "🗑 Forwarding rule deleted.")
        
        ref_call = f"acc_mappings:{ref}" if ref != "all" else "manage_rules"
        callback_dispatcher(telebot.types.CallbackQuery(call.id, call.from_user, call.message, call.inline_message_id, ref_call))

# ==========================================
#          TEXT MESSAGES INTERCEPTOR
# ==========================================

@bot.message_handler(func=lambda message: is_admin(message.from_user.id))
def handle_text_messages(message):
    chat_id = message.chat.id
    text = message.text.strip()

    # Check pending dynamic login steps
    if chat_id in pending_logins:
        state_info = pending_logins[chat_id]
        state = state_info["state"]

        if state == "AWAITING_PHONE":
            phone = text.replace(" ", "")
            state_info["phone"] = phone
            state_info["state"] = "AWAITING_API_ID"
            bot.send_message(
                chat_id,
                "📱 *Add New Userbot Account (Step 2/3)*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"Phone Number saved: `{phone}`\n\n"
                "Now, please send your Telegram **API ID** (integer):",
                parse_mode="Markdown"
            )
            return

        elif state == "AWAITING_API_ID":
            try:
                api_id = int(text)
                state_info["api_id"] = api_id
                state_info["state"] = "AWAITING_API_HASH"
                bot.send_message(
                    chat_id,
                    "📱 *Add New Userbot Account (Step 3/3)*\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"API ID saved: `{api_id}`\n\n"
                    "Finally, please send your Telegram **API HASH** (string):",
                    parse_mode="Markdown"
                )
            except ValueError:
                bot.reply_to(message, "❌ API ID must be an integer. Please enter a valid number:")
            return

        elif state == "AWAITING_API_HASH":
            api_hash = text
            state_info["api_hash"] = api_hash
            state_info["state"] = "CONNECTING"
            bot.send_message(chat_id, "⏳ Connecting to Telegram servers & generating code. Please wait...")
            
            # Execute connection step in asyncio loop safely
            asyncio.run_coroutine_threadsafe(
                initiate_telethon_client(
                    chat_id, 
                    state_info["phone"], 
                    state_info["api_id"], 
                    state_info["api_hash"]
                ),
                loop
            )
            return

        elif state == "AWAITING_OTP":
            otp = text.replace(" ", "")
            state_info["state"] = "VERIFYING_OTP"
            bot.send_message(chat_id, "⏳ Verifying verification code. Please wait...")
            
            asyncio.run_coroutine_threadsafe(verify_telethon_otp(chat_id, otp), loop)
            return

        elif state == "AWAITING_2FA":
            password = text
            state_info["state"] = "VERIFYING_2FA"
            bot.send_message(chat_id, "⏳ Verifying Two-Step password. Please wait...")
            
            asyncio.run_coroutine_threadsafe(verify_telethon_2fa(chat_id, password), loop)
            return
