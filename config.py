import os
from dotenv import load_dotenv

# Load variables from .env
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 0

# Check for required configurations
if not BOT_TOKEN:
    print("⚠️ WARNING: BOT_TOKEN is not set in your .env file!")
if not DATABASE_URL:
    print("⚠️ WARNING: DATABASE_URL is not set in your .env file!")
if ADMIN_ID == 0:
    print("⚠️ WARNING: ADMIN_ID is not configured properly. The admin panel may be open or inaccessible.")
