import logging
import asyncio
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import BOT_TOKEN, ADMIN_ID
import database as db
from userbot_manager import UserbotManager
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded

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
#          MAIN DASHBOARD & MENUS
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

    # Fetch fresh stats
    total_forwarded, total_bots, total_mappings = db.get_stats()
    
    text = (
        "💎 *System Control Dashboard*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Welcome to the Premium Telegram Multi-Account Forwarder. "
        "Here you can manage your userbot clients, list and filter chats, and set up real-time message forwarding.\n\n"
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
#          DYNAMIC OTP LOGIN LOGIC
# ==========================================

async def initiate_pyrogram_client(chat_id, phone, api_id, api_hash):
    """Starts the dynamic connection and requests OTP code in an async context."""
    logger.info(f"Initiating login client for {phone}...")
    client = Client(
        name=f"temp_{chat_id}",
        api_id=api_id,
        api_hash=api_hash,
        in_memory=True
    )
    
    try:
        await client.connect()
        sent_code = await client.send_code(phone)
        
        # Save temp client and hash to state
        pending_logins[chat_id].update({
            "client": client,
            "phone_code_hash": sent_code.phone_code_hash,
            "state": "AWAITING_OTP"
        })
        
        bot.send_message(
            chat_id,
            "📩 *Verification Code Sent*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Telegram has sent a verification code to `{phone}`.\n\n"
            "Please *reply directly to this message* with the OTP code you received:",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error during code generation: {e}")
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

async def verify_otp(chat_id, otp_code):
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
        # Sign in
        user = await client.sign_in(phone, phone_code_hash, otp_code)
        
        # Success! Export session string
        session_string = await client.export_session_string()
        first_name = user.first_name or "Userbot"
        
        # Store in PostgreSQL
        db.add_userbot(phone, api_id, api_hash, session_string, first_name)
        
        # Launch persistent userbot
        manager = UserbotManager()
        await manager.start_userbot(phone, api_id, api_hash, session_string)
        
        bot.send_message(
            chat_id,
            "🟢 *Authentication Successful!*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Account: `{phone}` (`{first_name}`)\n\n"
            "The userbot has been successfully registered and started in the background. "
            "Its configuration is now stored securely in PostgreSQL.",
            parse_mode="Markdown"
        )
        
        await client.disconnect()
        pending_logins.pop(chat_id, None)
    except SessionPasswordNeeded:
        # Account has 2FA enabled
        info["state"] = "AWAITING_2FA"
        bot.send_message(
            chat_id,
            "🔒 *Two-Step Verification Required*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Your account has a 2FA password enabled. "
            "Please reply with your 2FA password to complete the setup:",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error during OTP verification: {e}")
        bot.send_message(
            chat_id,
            f"❌ *Verification Error*:\n`{e}`\n\nPlease check your code or try again.",
            parse_mode="Markdown"
        )

async def verify_2fa(chat_id, password):
    """Verifies 2FA password and signs in user."""
    info = pending_logins.get(chat_id)
    if not info:
        return

    client = info["client"]
    phone = info["phone"]
    api_id = info["api_id"]
    api_hash = info["api_hash"]

    try:
        user = await client.check_password(password)
        session_string = await client.export_session_string()
        first_name = user.first_name or "Userbot"
        
        # Store in PostgreSQL
        db.add_userbot(phone, api_id, api_hash, session_string, first_name)
        
        # Launch persistent userbot
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

    # Always acknowledge callback
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
            "Welcome to the Premium Telegram Multi-Account Forwarder. "
            "Here you can manage your userbot clients, list and filter chats, and set up real-time message forwarding.\n\n"
            "📈 *Live Statistics*:\n"
            f" • Connected Userbots: `{total_bots}`\n"
            f" • Active Forwarding Rules: `{total_mappings}`\n"
            f" • Messages Forwarded: `{total_forwarded}`\n\n"
            "Select an action below to begin configuration:"
        )
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=get_main_keyboard(), parse_mode="Markdown")
        except:
            # If nothing changed, we ignore error
            pass

    # --- ACCOUNT MANAGEMENT ---
    elif data == "manage_accounts":
        userbots = db.get_all_userbots()
        text = (
            "📱 *Manage Accounts*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Here is the list of connected Telegram accounts. Click on any account to stop/start it, view active rules, or delete it:"
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
                # Run starting userbot inside loop safely
                future = asyncio.run_coroutine_threadsafe(
                    manager.start_userbot(phone, api_id, api_hash, session),
                    loop
                )
                try:
                    success = future.result(timeout=15)
                    if success:
                        bot.send_message(chat_id, f"🟢 Userbot `{phone}` started successfully!")
                    else:
                        bot.send_message(chat_id, f"❌ Failed to start userbot `{phone}`. Verification/Session issue.")
                except Exception as e:
                    bot.send_message(chat_id, f"❌ Timeout starting userbot `{phone}`: {e}")
            else:
                future = asyncio.run_coroutine_threadsafe(manager.stop_userbot(phone), loop)
                future.result()
                bot.send_message(chat_id, f"🔴 Userbot `{phone}` stopped successfully.")
        
        # Return to details menu
        callback_dispatcher(telebot.types.CallbackQuery(call.id, call.from_user, call.message, call.inline_message_id, f"acc_detail:{phone}"))

    elif data.startswith("acc_delete_confirm:"):
        phone = data.split(":")[1]
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("🔥 Yes, Delete", callback_data=f"acc_delete_exec:{phone}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"acc_detail:{phone}")
        )
        bot.edit_message_text(
            f"⚠️ *Are you absolutely sure?*\n\nThis will stop userbot `{phone}` and permanently erase all its configurations, session string, and rules from the PostgreSQL database.",
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
        
        # Return to accounts list
        callback_dispatcher(telebot.types.CallbackQuery(call.id, call.from_user, call.message, call.inline_message_id, "manage_accounts"))

    # --- CHAT LISTING & PAGINATION ---
    elif data.startswith("list_chats:"):
        phone, page_str = data.split(":")[1:3]
        page = int(page_str)
        is_running = manager.is_running(phone)
        
        if not is_running:
            bot.answer_callback_query(call.id, "❌ Please start the userbot first!", show_alert=True)
            return

        bot.send_message(chat_id, "🔍 *Fetching chats...* (This may take a moment)", parse_mode="Markdown")
        
        # Cache dialogs to prevent spamming Telegram servers on pagination click
        if phone not in chat_cache:
            future = asyncio.run_coroutine_threadsafe(manager.get_chats(phone), loop)
            chats = future.result()
            chat_cache[phone] = chats
        else:
            chats = chat_cache[phone]

        if not chats:
            bot.send_message(chat_id, "🤷 No active groups, channels, or chats found for this account.")
            return

        # Pagination parameters
        page_size = 7
        total_pages = (len(chats) + page_size - 1) // page_size
        start_idx = page * page_size
        end_idx = start_idx + page_size
        chats_slice = chats[start_idx:end_idx]

        markup = InlineKeyboardMarkup()
        
        for chat in chats_slice:
            chat_id_val = chat["chat_id"]
            chat_title = chat["title"]
            chat_type = chat["type"]

            # Set descriptive emoji
            emoji = "📢" if chat_type == "channel" else "👥" if chat_type in ["group", "supergroup"] else "👤"
            btn_text = f"{emoji} {chat_title}"
            
            # Since callback_data can only hold 64 bytes, we store selection state in memory
            # and use a compact callback query pointing to that state
            state_key = f"{phone}:{chat_id_val}"
            admin_states[chat_id] = {
                "phone": phone,
                "source_id": chat_id_val,
                "source_title": chat_title,
                "source_type": chat_type
            }
            # Callback contains a compact format: map_src:<chat_id_val>
            markup.row(InlineKeyboardButton(btn_text, callback_data=f"map_src:{chat_id_val}"))

        # Add pagination buttons
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"list_chats:{phone}:{page-1}"))
        nav_row.append(InlineKeyboardButton(f"Page {page+1}/{total_pages}", callback_data="noop"))
        if end_idx < len(chats):
            nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"list_chats:{phone}:{page+1}"))
        markup.row(*nav_row)

        markup.row(InlineKeyboardButton("🔄 Refresh Chats List", callback_data=f"clear_cache_list:{phone}"))
        markup.row(InlineKeyboardButton("🔙 Back to Details", callback_data=f"acc_detail:{phone}"))

        bot.send_message(
            chat_id,
            f"📢 *Chat List: {phone}*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Here are the chats your userbot account is currently in.\n\n"
            "👉 *Click on any chat* to forward its real-time messages to a designated Target Group:",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    elif data.startswith("clear_cache_list:"):
        phone = data.split(":")[1]
        chat_cache.pop(phone, None)
        callback_dispatcher(telebot.types.CallbackQuery(call.id, call.from_user, call.message, call.inline_message_id, f"list_chats:{phone}:0"))

    # --- FORWARDING RULE MAPPING CREATION ---
    elif data.startswith("map_src:"):
        source_id = int(data.split(":")[1])
        state = admin_states.get(chat_id)
        
        if not state or state["source_id"] != source_id:
            bot.send_message(chat_id, "❌ Interaction session expired. Please list chats and try again.")
            return

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
        state = admin_states.pop(chat_id, None)
        
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
                "Any *new incoming message* in the source chat will now be instantly forwarded to the target group in real-time!",
                parse_mode="Markdown"
            )
        else:
            bot.send_message(chat_id, "❌ Failed to create forwarding rule (might be a duplicate).")

        # Return to Details
        callback_dispatcher(telebot.types.CallbackQuery(call.id, call.from_user, call.message, call.inline_message_id, f"acc_detail:{phone}"))

    # --- TARGETS MANAGEMENT ---
    elif data == "manage_targets":
        targets = db.get_all_targets()
        text = (
            "🎯 *Target Groups*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "These are the groups where userbots will forward intercepted messages. Click a group to delete it from targets:"
        )
        markup = InlineKeyboardMarkup()
        for target_id, title in targets:
            markup.row(InlineKeyboardButton(f"🗑 {title} ({target_id})", callback_data=f"del_tgt:{target_id}"))
            
        markup.row(InlineKeyboardButton("➕ Add Target Group", callback_data="add_target"))
        markup.row(InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu"))
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode="Markdown")

    elif data == "add_target":
        admin_states[chat_id] = {"action": "AWAITING_TARGET_INPUT"}
        bot.send_message(
            chat_id,
            "🎯 *Add Target Group*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Please send the Target Group **Chat ID** and **Title** separated by a space.\n\n"
            "Example: `-100123456789 My Forward Dump`",
            parse_mode="Markdown"
        )

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
            # Different indexes depending on if filtering by phone
            if phone_filter:
                m_id, src_id, src_title, src_type, tgt_id, tgt_title, is_active = mapping
                mapping_desc = f"• `{src_title}` ➡️ `{tgt_title}`"
            else:
                m_id, phone, name, src_id, src_title, tgt_id, tgt_title, is_active = mapping
                mapping_desc = f"👤 `{name}`\n• `{src_title}` ➡️ `{tgt_title}`"
                
            status_emoji = "🟢 Active" if is_active else "🔴 Paused"
            
            # Display Rule Information
            bot.send_message(chat_id, f"{mapping_desc}\nStatus: {status_emoji}", parse_mode="Markdown")
            
            # Action buttons
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
            # Sanitize phone number
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
                initiate_pyrogram_client(
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
            
            asyncio.run_coroutine_threadsafe(verify_otp(chat_id, otp), loop)
            return

        elif state == "AWAITING_2FA":
            password = text
            state_info["state"] = "VERIFYING_2FA"
            bot.send_message(chat_id, "⏳ Verifying Two-Step password. Please wait...")
            
            asyncio.run_coroutine_threadsafe(verify_2fa(chat_id, password), loop)
            return

    # Check pending administrative action states
    if chat_id in admin_states:
        state_info = admin_states.pop(chat_id)
        action = state_info.get("action")

        if action == "AWAITING_TARGET_INPUT":
            try:
                parts = text.split(" ", 1)
                target_id = int(parts[0])
                title = parts[1]
                
                success = db.add_target(target_id, title)
                if success:
                    bot.send_message(chat_id, f"✅ Target group `{title}` (`{target_id}`) added successfully!")
                else:
                    bot.send_message(chat_id, "❌ Failed to save target group.")
            except Exception as e:
                bot.reply_to(message, f"❌ Invalid format. Please check the sample format:\nError: {e}")
            return
