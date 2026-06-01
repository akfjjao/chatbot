import asyncio
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import Channel, Chat, User
import database as db

logger = logging.getLogger(__name__)

# Real-time Telethon event handler for new messages
@events.register(events.NewMessage)
async def on_new_message_handler(event):
    try:
        client = event.client
        phone = getattr(client, "phone", None)
        if not phone:
            return

        chat_id = event.chat_id
        
        # Query DB to check for active target mappings for this source chat
        target_ids = db.get_active_mappings_for_source(phone, chat_id)
        if not target_ids:
            return

        logger.info(f"⚡ [Telethon Userbot {phone}] Intercepted message in source {chat_id}. Forwarding...")

        for target_id in target_ids:
            try:
                # Use Telethon's forward_messages method
                await client.forward_messages(target_id, event.message)
                db.increment_forwarded_stats(1)
                logger.info(f"✅ Message forwarded to target {target_id}")
            except Exception as e:
                logger.error(f"❌ Failed to forward to target {target_id}: {e}")

    except Exception as e:
        logger.error(f"Error in Telethon dynamic listener: {e}")

class UserbotManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(UserbotManager, cls).__new__(cls)
            cls._instance.clients = {}  # Store running clients: phone -> TelegramClient
            cls._instance.lock = asyncio.Lock()
        return cls._instance

    async def start_userbot(self, phone, api_id, api_hash, session_string):
        """Starts a Telethon userbot instance using its string session."""
        async with self.lock:
            if phone in self.clients:
                logger.info(f"Userbot {phone} is already running.")
                return True

            logger.info(f"🚀 Starting Telethon userbot client for {phone}...")
            try:
                # Initialize Telethon TelegramClient with StringSession
                client = TelegramClient(
                    StringSession(session_string),
                    api_id=api_id,
                    api_hash=api_hash
                )
                client.phone = phone  # Bind phone property

                # Connect client
                await client.connect()
                
                # Verify session auth status
                if not await client.is_user_authorized():
                    logger.error(f"❌ Telethon session is unauthorized/expired for {phone}.")
                    db.set_userbot_status(phone, False)
                    return False

                # Register live event listener for incoming messages
                client.add_event_handler(on_new_message_handler)

                self.clients[phone] = client
                db.set_userbot_status(phone, True)
                logger.info(f"🟢 Telethon Userbot {phone} started and listening successfully!")
                return True
            except Exception as e:
                logger.error(f"❌ Failed to boot Telethon userbot {phone}: {e}")
                db.set_userbot_status(phone, False)
                return False

    async def stop_userbot(self, phone):
        """Stops a running Telethon userbot."""
        async with self.lock:
            client = self.clients.get(phone)
            if not client:
                logger.info(f"Userbot {phone} is not running.")
                db.set_userbot_status(phone, False)
                return True

            logger.info(f"🛑 Disconnecting Telethon client for {phone}...")
            try:
                await client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting Telethon client {phone}: {e}")
            finally:
                if phone in self.clients:
                    del self.clients[phone]
                db.set_userbot_status(phone, False)
                logger.info(f"🔴 Telethon Userbot {phone} disconnected.")
            return True

    async def stop_all(self):
        """Stops all running Telethon userbots."""
        logger.info("🛑 Disconnecting all Telethon userbots...")
        phones = list(self.clients.keys())
        for phone in phones:
            await self.stop_userbot(phone)
        logger.info("✅ All Telethon userbots disconnected.")

    async def load_and_start_all_active(self):
        """Auto-boots all previously active userbots on start."""
        userbots = db.get_all_userbots()
        active_count = 0
        
        for phone, api_id, api_hash, session_string, first_name, is_active in userbots:
            if is_active:
                success = await self.start_userbot(phone, api_id, api_hash, session_string)
                if success:
                    active_count += 1
                    
        logger.info(f"🚀 Telethon dynamic bootloader: Activated {active_count} userbot(s) on startup.")

    async def get_chats(self, phone, limit=150):
        """Fetches the dialogs (chats) for an active userbot and classifies them with custom emojis."""
        client = self.clients.get(phone)
        if not client:
            logger.warning(f"Cannot fetch chats for {phone} because client is offline.")
            return []

        chats = []
        try:
            # Iterate through client's active dialogs/conversations
            async for dialog in client.iter_dialogs(limit=limit):
                entity = dialog.entity
                chat_id = dialog.id
                chat_title = dialog.name or "Unknown Chat"

                # Dynamic classification structure
                if isinstance(entity, User):
                    if entity.bot:
                        chat_type = "bot"
                        emoji = "🤖"
                    elif entity.is_self:
                        chat_type = "saved"
                        emoji = "📁"
                        chat_title = "Saved Messages"
                    else:
                        chat_type = "private"
                        emoji = "👤"
                elif isinstance(entity, Channel):
                    if entity.megagroup:
                        # Differentiate Forum (topic groups) from standard supergroups
                        if getattr(entity, 'forum', False):
                            chat_type = "forum"
                            emoji = "💬"
                        else:
                            chat_type = "supergroup"
                            emoji = "👥"
                    else:
                        chat_type = "channel"
                        emoji = "📢"
                elif isinstance(entity, Chat):
                    chat_type = "group"
                    emoji = "👥"
                else:
                    chat_type = "unknown"
                    emoji = "❓"

                chats.append({
                    "chat_id": chat_id,
                    "title": chat_title,
                    "type": chat_type,
                    "emoji": emoji
                })
        except Exception as e:
            logger.error(f"Error fetching/classifying chats for {phone}: {e}")
            
        return chats

    def is_running(self, phone):
        return phone in self.clients
