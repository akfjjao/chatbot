import psycopg2
from psycopg2 import pool as pg_pool
from contextlib import contextmanager
import logging
from config import DATABASE_URL

logger = logging.getLogger(__name__)

db_pool = None

def init_db_pool():
    global db_pool
    if db_pool is None:
        if not DATABASE_URL:
            logger.error("❌ DATABASE_URL is not set. Cannot initialize pool.")
            raise ValueError("DATABASE_URL is not set.")
        try:
            db_pool = pg_pool.SimpleConnectionPool(1, 20, dsn=DATABASE_URL)
            logger.info("✅ PostgreSQL connection pool initialized successfully.")
        except Exception as e:
            logger.error(f"❌ Failed to initialize PostgreSQL pool: {e}")
            raise

@contextmanager
def get_connection():
    if db_pool is None:
        init_db_pool()
    conn = db_pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"⚠️ Database transaction rollback due to error: {e}")
        raise
    finally:
        db_pool.putconn(conn)

def init_db():
    """Initializes the database schema if tables do not exist."""
    with get_connection() as conn:
        with conn.cursor() as c:
            # 1. Userbots Table
            c.execute("""
                CREATE TABLE IF NOT EXISTS userbots (
                    phone VARCHAR(30) PRIMARY KEY,
                    api_id INTEGER NOT NULL,
                    api_hash VARCHAR(100) NOT NULL,
                    session_string TEXT NOT NULL,
                    first_name VARCHAR(100),
                    is_active BOOLEAN DEFAULT TRUE,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 2. Targets Table
            c.execute("""
                CREATE TABLE IF NOT EXISTS targets (
                    chat_id BIGINT PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 3. Mappings Table
            c.execute("""
                CREATE TABLE IF NOT EXISTS mappings (
                    id SERIAL PRIMARY KEY,
                    userbot_phone VARCHAR(30) REFERENCES userbots(phone) ON DELETE CASCADE,
                    source_chat_id BIGINT NOT NULL,
                    source_chat_title VARCHAR(255),
                    source_chat_type VARCHAR(50),
                    target_chat_id BIGINT REFERENCES targets(chat_id) ON DELETE CASCADE,
                    is_active BOOLEAN DEFAULT TRUE,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(userbot_phone, source_chat_id, target_chat_id)
                );
            """)

            # 4. Stats Table
            c.execute("""
                CREATE TABLE IF NOT EXISTS stats (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    messages_forwarded INTEGER DEFAULT 1
                );
            """)
            
            logger.info("✅ PostgreSQL tables verified/created successfully.")

# --- Userbot Database Queries ---

def add_userbot(phone, api_id, api_hash, session_string, first_name):
    try:
        with get_connection() as conn:
            with conn.cursor() as c:
                c.execute("""
                    INSERT INTO userbots (phone, api_id, api_hash, session_string, first_name, is_active)
                    VALUES (%s, %s, %s, %s, %s, TRUE)
                    ON CONFLICT (phone) DO UPDATE SET
                        api_id = EXCLUDED.api_id,
                        api_hash = EXCLUDED.api_hash,
                        session_string = EXCLUDED.session_string,
                        first_name = EXCLUDED.first_name,
                        is_active = TRUE;
                """, (phone, api_id, api_hash, session_string, first_name))
                return True
    except Exception as e:
        logger.error(f"Error adding userbot {phone}: {e}")
        return False

def remove_userbot(phone):
    try:
        with get_connection() as conn:
            with conn.cursor() as c:
                c.execute("DELETE FROM userbots WHERE phone = %s", (phone,))
                return c.rowcount > 0
    except Exception as e:
        logger.error(f"Error removing userbot {phone}: {e}")
        return False

def get_userbot(phone):
    try:
        with get_connection() as conn:
            with conn.cursor() as c:
                c.execute("SELECT phone, api_id, api_hash, session_string, first_name, is_active FROM userbots WHERE phone = %s", (phone,))
                return c.fetchone()
    except Exception as e:
        logger.error(f"Error getting userbot {phone}: {e}")
        return None

def get_all_userbots():
    try:
        with get_connection() as conn:
            with conn.cursor() as c:
                c.execute("SELECT phone, api_id, api_hash, session_string, first_name, is_active FROM userbots ORDER BY added_at DESC")
                return c.fetchall()
    except Exception as e:
        logger.error(f"Error getting all userbots: {e}")
        return []

def set_userbot_status(phone, is_active):
    try:
        with get_connection() as conn:
            with conn.cursor() as c:
                c.execute("UPDATE userbots SET is_active = %s WHERE phone = %s", (is_active, phone))
                return True
    except Exception as e:
        logger.error(f"Error setting userbot status for {phone}: {e}")
        return False

# --- Targets Database Queries ---

def add_target(chat_id, title):
    try:
        with get_connection() as conn:
            with conn.cursor() as c:
                c.execute("""
                    INSERT INTO targets (chat_id, title)
                    VALUES (%s, %s)
                    ON CONFLICT (chat_id) DO UPDATE SET title = EXCLUDED.title;
                """, (chat_id, title))
                return True
    except Exception as e:
        logger.error(f"Error adding target {chat_id}: {e}")
        return False

def remove_target(chat_id):
    try:
        with get_connection() as conn:
            with conn.cursor() as c:
                c.execute("DELETE FROM targets WHERE chat_id = %s", (chat_id,))
                return c.rowcount > 0
    except Exception as e:
        logger.error(f"Error removing target {chat_id}: {e}")
        return False

def get_all_targets():
    try:
        with get_connection() as conn:
            with conn.cursor() as c:
                c.execute("SELECT chat_id, title FROM targets ORDER BY added_at DESC")
                return c.fetchall()
    except Exception as e:
        logger.error(f"Error getting all targets: {e}")
        return []

# --- Mappings Database Queries ---

def add_mapping(userbot_phone, source_chat_id, source_chat_title, source_chat_type, target_chat_id):
    try:
        with get_connection() as conn:
            with conn.cursor() as c:
                c.execute("""
                    INSERT INTO mappings (userbot_phone, source_chat_id, source_chat_title, source_chat_type, target_chat_id, is_active)
                    VALUES (%s, %s, %s, %s, %s, TRUE)
                    ON CONFLICT (userbot_phone, source_chat_id, target_chat_id) DO UPDATE SET
                        source_chat_title = EXCLUDED.source_chat_title,
                        source_chat_type = EXCLUDED.source_chat_type,
                        is_active = TRUE;
                """, (userbot_phone, source_chat_id, source_chat_title, source_chat_type, target_chat_id))
                return True
    except Exception as e:
        logger.error(f"Error adding mapping: {e}")
        return False

def remove_mapping(mapping_id):
    try:
        with get_connection() as conn:
            with conn.cursor() as c:
                c.execute("DELETE FROM mappings WHERE id = %s", (mapping_id,))
                return c.rowcount > 0
    except Exception as e:
        logger.error(f"Error removing mapping {mapping_id}: {e}")
        return False

def get_mappings_for_userbot(phone):
    try:
        with get_connection() as conn:
            with conn.cursor() as c:
                c.execute("""
                    SELECT m.id, m.source_chat_id, m.source_chat_title, m.source_chat_type, m.target_chat_id, t.title, m.is_active
                    FROM mappings m
                    JOIN targets t ON m.target_chat_id = t.chat_id
                    WHERE m.userbot_phone = %s
                    ORDER BY m.added_at DESC
                """, (phone,))
                return c.fetchall()
    except Exception as e:
        logger.error(f"Error getting mappings for userbot {phone}: {e}")
        return []

def get_all_mappings():
    try:
        with get_connection() as conn:
            with conn.cursor() as c:
                c.execute("""
                    SELECT m.id, m.userbot_phone, u.first_name, m.source_chat_id, m.source_chat_title, m.target_chat_id, t.title, m.is_active
                    FROM mappings m
                    JOIN userbots u ON m.userbot_phone = u.phone
                    JOIN targets t ON m.target_chat_id = t.chat_id
                    ORDER BY m.added_at DESC
                """)
                return c.fetchall()
    except Exception as e:
        logger.error(f"Error getting all mappings: {e}")
        return []

def toggle_mapping(mapping_id, is_active):
    try:
        with get_connection() as conn:
            with conn.cursor() as c:
                c.execute("UPDATE mappings SET is_active = %s WHERE id = %s", (is_active, mapping_id))
                return True
    except Exception as e:
        logger.error(f"Error toggling mapping {mapping_id}: {e}")
        return False

def get_active_mappings_for_source(userbot_phone, source_chat_id):
    try:
        with get_connection() as conn:
            with conn.cursor() as c:
                c.execute("""
                    SELECT target_chat_id FROM mappings
                    WHERE userbot_phone = %s AND source_chat_id = %s AND is_active = TRUE
                """, (userbot_phone, source_chat_id))
                return [row[0] for row in c.fetchall()]
    except Exception as e:
        logger.error(f"Error getting active targets for {userbot_phone} - {source_chat_id}: {e}")
        return []

# --- Stats Queries ---

def increment_forwarded_stats(count=1):
    try:
        with get_connection() as conn:
            with conn.cursor() as c:
                c.execute("INSERT INTO stats (messages_forwarded) VALUES (%s)", (count,))
                return True
    except Exception as e:
        logger.error(f"Error incrementing stats: {e}")
        return False

def get_stats():
    try:
        with get_connection() as conn:
            with conn.cursor() as c:
                c.execute("SELECT COALESCE(SUM(messages_forwarded), 0) FROM stats")
                total = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM userbots")
                bots = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM mappings WHERE is_active = TRUE")
                mappings = c.fetchone()[0]
                return total, bots, mappings
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return 0, 0, 0
