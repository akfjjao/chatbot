import asyncio
import logging
import database as db
from config import BOT_TOKEN
from userbot_manager import UserbotManager
import admin_bot


# Set up logging formatting
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def start_services():
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info("💎 Telegram Multi-Account Forwarder system starting...")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    # 1. Initialize PostgreSQL Database Pool and Schemas
    logger.info("📂 Initializing PostgreSQL database pool & verifying tables...")
    try:
        db.init_db()
        logger.info("✅ Database schemas validated successfully.")
    except Exception as e:
        logger.error(f"❌ Failed to initialize database: {e}")
        return

    # 2. Setup Loop Reference for Telebot Thread Communication
    logger.info("🔗 Binding active asyncio event loop to admin control panel...")
    admin_bot.loop = asyncio.get_running_loop()

    # 3. Instantiate and Start all active userbots stored in PostgreSQL
    logger.info("🚀 Dynamic Bootloader: Launching registered active userbots...")
    manager = UserbotManager()
    try:
        await manager.load_and_start_all_active()
    except Exception as e:
        logger.error(f"⚠️ Error while auto-loading active userbots: {e}")

    # 4. Start Admin Control Bot in a separate thread to prevent event loop blocking
    logger.info("⚡ Starting Telebot Admin Dashboard listener...")
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, admin_bot.bot.infinity_polling)
    logger.info("🟢 Telebot Admin Dashboard started successfully!")

    logger.info("🚀 System is now fully operational! Send /dashboard in the Admin Bot to begin.")
    
    # Keep the asyncio loop alive until user shuts it down (e.g. Ctrl+C)
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    
    # 5. Graceful shutdown
    logger.info("🛑 Shutting down system. Stopping all active userbots...")
    await manager.stop_all()
    logger.info("👋 System stopped. Goodbye!")

if __name__ == "__main__":
    if not BOT_TOKEN:
        logger.error("❌ Critical: BOT_TOKEN is not configured in the .env file! The Admin UI cannot start.")
    else:
        try:
            # Launch the central Pyrogram event loop
            asyncio.run(start_services())
        except (KeyboardInterrupt, SystemExit):
            logger.info("👋 System stopped via keyboard interrupt.")
