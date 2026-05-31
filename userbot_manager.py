import asyncio
import logging
from pyrogram import Client
from pyrogram.handlers import MessageHandler
from pyrogram.errors import SessionExpired, AuthKeyDuplicated
import database as db

logger = logging.getLogger(__name__)

# Forward handler to process all incoming messages
async def on_message_handler(client: Client, message):
    try:
        phone = getattr(client, "phone", None)
        if not phone:
            return

        chat_id = message.chat.id
        
        # Query DB to see if we have any active targets mapped for this source
        target_ids = db.get_active_mappings_for_source(phone, chat_id)
        if not target_ids:
            return

        logger.info(f"⚡ [Userbot {phone}] Received message in mapped source chat {chat_id} ({message.chat.title or 'Private'}). Forwarding...")

        for target_id in target_ids:
            try:
                # Use Pyrogram's native forward message
                await message.forward(target_id)
                db.increment_forwarded_stats(1)
                logger.info(f"✅ Message forwarded successfully to {target_id}")
            except Exception as e:
                logger.error(f"❌ Failed to forward to target {target_id}: {e}")
                
    except Exception as e:
        logger.error(f"Error in userbot message listener: {e}")

class UserbotManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(UserbotManager, cls).__new__(cls)
            cls._instance.clients = {}  # Store running clients: phone -> Client
            cls._instance.lock = asyncio.Lock()
        return cls._instance

    async def start_userbot(self, phone, api_id, api_hash, session_string):
        """Starts a userbot instance using its session string."""
        async with self.lock:
            if phone in self.clients:
                logger.info(f"Userbot {phone} is already running.")
                return True

            logger.info(f"🚀 Starting userbot client for {phone}...")
            try:
                # Use in_memory=True or write a unique filename. In-memory is best because the session string has everything!
                client = Client(
                    name=f"userbot_{phone}",
                    api_id=api_id,
                    api_hash=api_hash,
                    session_string=session_string,
                    in_memory=True
                )
                client.phone = phone  # Bind phone number for callback lookup
                
                # Register the dynamic message listener
                client.add_handler(MessageHandler(on_message_handler))

                await client.start()
                self.clients[phone] = client
                db.set_userbot_status(phone, True)
                logger.info(f"🟢 Userbot {phone} started successfully!")
                return True
            except (SessionExpired, AuthKeyDuplicated) as se:
                logger.error(f"❌ Session string expired or duplicated for {phone}: {se}")
                db.set_userbot_status(phone, False)
                return False
            except Exception as e:
                logger.error(f"❌ Failed to start userbot {phone}: {e}")
                db.set_userbot_status(phone, False)
                return False

    async def stop_userbot(self, phone):
        """Stops a running userbot instance."""
        async with self.lock:
            client = self.clients.get(phone)
            if not client:
                logger.info(f"Userbot {phone} is not running.")
                db.set_userbot_status(phone, False)
                return True

            logger.info(f"🛑 Stopping userbot client for {phone}...")
            try:
                await client.stop()
            except Exception as e:
                logger.error(f"Error stopping userbot client {phone}: {e}")
            finally:
                if phone in self.clients:
                    del self.clients[phone]
                db.set_userbot_status(phone, False)
                logger.info(f"🔴 Userbot {phone} stopped successfully.")
            return True

    async def stop_all(self):
        """Stops all running userbots."""
        logger.info("🛑 Stopping all active userbots...")
        phones = list(self.clients.keys())
        for phone in phones:
            await self.stop_userbot(phone)
        logger.info("✅ All userbots stopped.")

    async def load_and_start_all_active(self):
        """Loads all active userbots from DB and boots them in the background."""
        userbots = db.get_all_userbots()
        active_count = 0
        
        for phone, api_id, api_hash, session_string, first_name, is_active in userbots:
            if is_active:
                # We start each in the background
                success = await self.start_userbot(phone, api_id, api_hash, session_string)
                if success:
                    active_count += 1
                    
        logger.info(f"🚀 Dynamic bootloader: Started {active_count} active userbot(s) on startup.")

    async def get_chats(self, phone, limit=100):
        """Fetches the dialogs (chats) for an active userbot."""
        client = self.clients.get(phone)
        if not client:
            logger.warning(f"Cannot fetch chats for {phone} because client is not running.")
            return []

        chats = []
        try:
            async for dialog in client.get_dialogs(limit=limit):
                chat = dialog.chat
                
                # Skip self-chats or standard Telegram service chat if needed, but let's list them all
                chat_title = chat.title or f"{chat.first_name or ''} {chat.last_name or ''}".strip()
                if not chat_title:
                    chat_title = "Unknown Chat"
                    
                chats.append({
                    "chat_id": chat.id,
                    "title": chat_title,
                    "type": str(chat.type).split('.')[-1].lower() # e.g. "channel", "supergroup", "private"
                })
        except Exception as e:
            logger.error(f"Error getting chats for userbot {phone}: {e}")
            
        return chats

    def is_running(self, phone):
        return phone in self.clients
