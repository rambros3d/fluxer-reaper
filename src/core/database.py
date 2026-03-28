from __future__ import annotations
import sqlite3
import logging
import json
import random
from pathlib import Path
from typing import Optional, Dict, Any, Union
import threading
import sys
from src.core.utils import parse_snowflake

logger = logging.getLogger(__name__)

class MigrationDatabase:
    """
    SQLite-based persistence for large-scale migration mappings and stats.
    Replaces the memory-bloated and O(N^2) JSON persistence for messages.
    """
    
    def __init__(self, db_path: Path, platform: str = None):
        self.db_path = db_path
        self.platform = platform.lower() if platform else None
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        """Initialize tables if they don't exist and handle migrations/platform-specific schemas."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 1. Determine active platform and column types
        # Create metadata table first as we need it for platform tracking
        cursor.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        
        # Load platform from DB if exists
        cursor.execute("SELECT value FROM metadata WHERE key = ?", ("target_platform",))
        row = cursor.fetchone()
        stored_platform = row[0] if row else None
        
        # If platform provided, update stored platform. If not provided, use stored.
        active_platform = self.platform or stored_platform or "stoat" # Default to stoat if unknown
        if self.platform and self.platform != stored_platform:
            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", ("target_platform", active_platform))
            conn.commit()
            
        # Define types
        # source_type is always INTEGER (Discord/Fluxer snowflakes)
        # target_type is INTEGER for Fluxer, TEXT for Stoat
        source_type = "INTEGER"
        target_type = "INTEGER" if active_platform == "fluxer" else "TEXT"
        
        # 2. Universal ID Migration (TEXT -> INTEGER vs Platform Switch)
        # Mapping of table names to columns that must match their respective types
        # key: table, value: (discord_cols, target_cols)
        id_migrations = {
            "message_mappings": (["source_msg_id"], ["channel_id", "target_msg_id"]),
            "thread_mappings": (["source_msg_id"], ["channel_id", "thread_id", "target_msg_id"]),
            "channel_tracking": ([], ["channel_id", "last_msg_id"]),
            "thread_tracking": ([], ["channel_id", "thread_id", "last_msg_id"]),
            "server_mappings": (["source_id"], ["target_id"]),
            "asset_mappings": (["source_id"], ["target_id"]),
            "user_alias": (["user_id"], [])
        }

        for table, (discord_cols, target_cols) in id_migrations.items():
            cursor.execute(f"SELECT count(*) FROM sqlite_master WHERE type='table' AND name='{table}'")
            res = cursor.fetchone()
            if not res or res[0] == 0:
                continue

            cursor.execute(f"PRAGMA table_info({table})")
            cols = cursor.fetchall()
            needs_migration = False
            for col in cols:
                c_name, c_type = col[1], col[2]
                if c_name in discord_cols and c_type != source_type:
                    needs_migration = True
                    break
                if c_name in target_cols and c_type != target_type:
                    needs_migration = True
                    break
            
            if needs_migration:
                logger.info(f"MigrationDatabase: Migrating {table} schema (Platform: {active_platform})")
                cursor.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
                
                if table == "message_mappings":
                    cursor.execute(f"CREATE TABLE message_mappings (channel_id {target_type}, source_msg_id {source_type}, target_msg_id {target_type}, timestamp TEXT, PRIMARY KEY (channel_id, source_msg_id))")
                elif table == "thread_mappings":
                    cursor.execute(f"CREATE TABLE thread_mappings (channel_id {target_type}, thread_id {target_type}, source_msg_id {source_type}, target_msg_id {target_type}, timestamp TEXT, PRIMARY KEY (channel_id, thread_id, source_msg_id))")
                elif table == "channel_tracking":
                    cursor.execute(f"CREATE TABLE channel_tracking (channel_id {target_type} PRIMARY KEY, last_msg_id {target_type}, last_msg_ts TEXT, msg_count INTEGER DEFAULT 0, file_count INTEGER DEFAULT 0)")
                elif table == "thread_tracking":
                    cursor.execute(f"CREATE TABLE thread_tracking (channel_id {target_type}, thread_id {target_type}, last_msg_id {target_type}, last_msg_ts TEXT, msg_count INTEGER DEFAULT 0, file_count INTEGER DEFAULT 0, completed INTEGER DEFAULT 0, PRIMARY KEY (channel_id, thread_id))")
                elif table == "server_mappings":
                    cursor.execute(f"CREATE TABLE server_mappings (category TEXT, source_id {source_type}, target_id {target_type}, PRIMARY KEY (category, source_id))")
                elif table == "asset_mappings":
                    cursor.execute(f"CREATE TABLE asset_mappings (category TEXT, source_id {source_type}, target_id {target_type}, PRIMARY KEY (category, source_id))")
                elif table == "user_alias":
                    cursor.execute(f"CREATE TABLE user_alias (user_id {source_type} PRIMARY KEY, alias TEXT UNIQUE)")
                
                old_cols = [c[1] for c in cursor.execute(f"PRAGMA table_info({table}_old)").fetchall()]
                new_cols = [c[1] for c in cursor.execute(f"PRAGMA table_info({table})").fetchall()]
                common_cols = [c for c in old_cols if c in new_cols]
                col_str = ", ".join(common_cols)
                
                cursor.execute(f"INSERT OR IGNORE INTO {table} ({col_str}) SELECT {col_str} FROM {table}_old")
                cursor.execute(f"DROP TABLE {table}_old")

        # Initial Creation / Ensure Schema Correctness
        # Table for message mappings: SourceID -> TargetID
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS message_mappings (
                channel_id {target_type},
                source_msg_id {source_type},
                target_msg_id {target_type},
                timestamp TEXT,
                PRIMARY KEY (channel_id, source_msg_id)
            )
        """)
        
        # Table for thread mappings
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS thread_mappings (
                channel_id {target_type},
                thread_id {target_type},
                source_msg_id {source_type},
                target_msg_id {target_type},
                timestamp TEXT,
                PRIMARY KEY (channel_id, thread_id, source_msg_id)
            )
        """)
        
        # Table for per-channel stats and tracking
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS channel_tracking (
                channel_id {target_type} PRIMARY KEY,
                last_msg_id {target_type},
                last_msg_ts TEXT,
                msg_count INTEGER DEFAULT 0,
                file_count INTEGER DEFAULT 0
            )
        """)
        
        # Table for per-thread stats
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS thread_tracking (
                channel_id {target_type},
                thread_id {target_type},
                last_msg_id {target_type},
                last_msg_ts TEXT,
                msg_count INTEGER DEFAULT 0,
                file_count INTEGER DEFAULT 0,
                completed INTEGER DEFAULT 0,
                PRIMARY KEY (channel_id, thread_id)
            )
        """)
        
        # Add completed column if it doesn't exist (backward compatibility)
        try:
            cursor.execute("ALTER TABLE thread_tracking ADD COLUMN completed INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass # Already exists

        # Table for server entity mappings (channels, roles, categories)
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS server_mappings (
                category TEXT,
                source_id {source_type},
                target_id {target_type},
                PRIMARY KEY (category, source_id)
            )
        """)

        # Table for asset mappings (emojis, stickers)
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS asset_mappings (
                category TEXT,
                source_id {source_type},
                target_id {target_type},
                PRIMARY KEY (category, source_id)
            )
        """)
        
        # Migrate old entity_mappings if it exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entity_mappings'")
        if cursor.fetchone():
            # Copy channels, categories, roles to server_mappings
            cursor.execute("""
                INSERT OR IGNORE INTO server_mappings (category, source_id, target_id)
                SELECT category, source_id, target_id FROM entity_mappings 
                WHERE category IN ('channel', 'category', 'role')
            """)
            
            # Copy emojis, stickers to asset_mappings
            cursor.execute("""
                INSERT OR IGNORE INTO asset_mappings (category, source_id, target_id)
                SELECT category, source_id, target_id FROM entity_mappings 
                WHERE category IN ('emoji', 'sticker')
            """)
            
            # Drop old table
            cursor.execute("DROP TABLE entity_mappings")

        # Table for auto-generated user aliases (user_id -> alias)
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS user_alias (
                user_id {source_type} PRIMARY KEY,
                alias TEXT UNIQUE
            )
        """)
        
        # Indexes for fast lookup by source message ID
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_message_mappings_source ON message_mappings (source_msg_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_thread_mappings_source ON thread_mappings (source_msg_id)")
        
        conn.commit()
        conn.close()

    def set_message_mapping(self, channel_id: str, source_id: str, target_id: str, timestamp: str = None):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO message_mappings (channel_id, source_msg_id, target_msg_id, timestamp) VALUES (?, ?, ?, ?)",
            (str(channel_id), parse_snowflake(source_id), str(target_id), timestamp)
        )
        conn.commit()

    def get_target_message_id(self, channel_id: str, source_id: str) -> Optional[Union[str, int]]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT target_msg_id FROM message_mappings WHERE channel_id = ? AND source_msg_id = ?",
            (str(channel_id), parse_snowflake(source_id))
        ).fetchone()
        if row:
            val = row["target_msg_id"]
            return str(val) if self.platform == "stoat" else val
        return None

    def get_all_message_mappings(self, channel_id: str) -> Dict[Union[str, int], Union[str, int]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT source_msg_id, target_msg_id FROM message_mappings WHERE channel_id = ?",
            (str(channel_id),)
        ).fetchall()
        if self.platform == "stoat":
            return {str(row["source_msg_id"]): str(row["target_msg_id"]) for row in rows}
        return {row["source_msg_id"]: row["target_msg_id"] for row in rows}

    # --- User Alias Methods ---

    def _generate_alias(self) -> str:
        """Generates a unique alias in the format {Adjective}{Name} from random_users.json."""
        # Robust path resolution for PyInstaller
        if hasattr(sys, '_MEIPASS'):
            # Running as a frozen bundle
            json_path = Path(sys._MEIPASS) / "src" / "random_users.json"
        else:
            # Running in normal python environment (from src/core/)
            json_path = Path(__file__).parent.parent / "random_users.json"
            
        if not json_path.exists():
             logger.error(f"MigrationDatabase: random_users.json not found at {json_path}")
             raise FileNotFoundError(f"Missing required resource: {json_path}")

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        names = data.get("names", [])
        adjectives = data.get("adjectives", [])
        
        conn = self._get_conn()
        
        # Try random combinations until unique
        for _ in range(10000):
            alias = f"{random.choice(adjectives)}{random.choice(names)}"
            # Check if this alias is already taken
            row = conn.execute("SELECT user_id FROM user_alias WHERE alias = ?", (alias,)).fetchone()
            if not row:
                return alias
        
        # Fallback: append a number just in case collision rate is too high
        import time
        return f"{random.choice(adjectives)}{random.choice(names)}{int(time.time()) % 10000}"

    def get_or_create_user_alias(self, user_id: str) -> str:
        """Gets the existing alias for a user or generates and saves a new one."""
        conn = self._get_conn()
        
        # Check for existing alias
        row = conn.execute("SELECT alias FROM user_alias WHERE user_id = ?", (parse_snowflake(user_id),)).fetchone()
        if row:
            return row["alias"]
        
        # Generate new, uniquely constrained alias
        # Using a simplistic retry loop in case of race-conditions, though lock-less SQLite handles this with errors
        try:
            new_alias = self._generate_alias()
            conn.execute(
                "INSERT INTO user_alias (user_id, alias) VALUES (?, ?)", 
                (parse_snowflake(user_id), new_alias)
            )
            conn.commit()
            return new_alias
        except sqlite3.IntegrityError:
            # Race condition: someone else inserted a conflicting alias or this user ID
            # Re-read or re-try
            row = conn.execute("SELECT alias FROM user_alias WHERE user_id = ?", (str(user_id),)).fetchone()
            if row: return row["alias"]
            # Otherwise uniquely retry
            new_alias = self._generate_alias()
            conn.execute(
                "INSERT OR REPLACE INTO user_alias (user_id, alias) VALUES (?, ?)", 
                (str(user_id), new_alias)
            )
            conn.commit()
            return new_alias

    # --- Server Mapping Methods ---

    def set_server_mapping(self, category: str, source_id: str, target_id: str):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO server_mappings (category, source_id, target_id) VALUES (?, ?, ?)",
            (category, parse_snowflake(source_id), str(target_id))
        )
        conn.commit()

    def get_server_mapping(self, category: str, source_id: str) -> Optional[Union[str, int]]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT target_id FROM server_mappings WHERE category = ? AND source_id = ?",
            (category, parse_snowflake(source_id))
        ).fetchone()
        if row:
            val = row["target_id"]
            return str(val) if self.platform == "stoat" else val
        return None

    def get_all_server_mappings(self, category: str) -> Dict[Union[str, int], Union[str, int]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT source_id, target_id FROM server_mappings WHERE category = ?",
            (category,)
        ).fetchall()
        if self.platform == "stoat":
            return {str(row["source_id"]): str(row["target_id"]) for row in rows}
        return {row["source_id"]: row["target_id"] for row in rows}

    def delete_server_mapping(self, category: str, source_id: str):
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM server_mappings WHERE category = ? AND source_id = ?",
            (category, parse_snowflake(source_id))
        )
        conn.commit()

    def clear_server_mappings(self, category: str = None):
        conn = self._get_conn()
        if category:
            conn.execute("DELETE FROM server_mappings WHERE category = ?", (category,))
        else:
            conn.execute("DELETE FROM server_mappings")
        conn.commit()

    # --- Asset Mapping Methods ---

    def set_asset_mapping(self, category: str, source_id: str, target_id: str):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO asset_mappings (category, source_id, target_id) VALUES (?, ?, ?)",
            (category, parse_snowflake(source_id), str(target_id))
        )
        conn.commit()

    def get_asset_mapping(self, category: str, source_id: str) -> Optional[Union[str, int]]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT target_id FROM asset_mappings WHERE category = ? AND source_id = ?",
            (category, parse_snowflake(source_id))
        ).fetchone()
        if row:
            val = row["target_id"]
            return str(val) if self.platform == "stoat" else val
        return None

    def get_all_asset_mappings(self, category: str) -> Dict[Union[str, int], Union[str, int]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT source_id, target_id FROM asset_mappings WHERE category = ?",
            (category,)
        ).fetchall()
        if self.platform == "stoat":
            return {str(row["source_id"]): str(row["target_id"]) for row in rows}
        return {row["source_id"]: row["target_id"] for row in rows}

    def delete_asset_mapping(self, category: str, source_id: str):
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM asset_mappings WHERE category = ? AND source_id = ?",
            (category, parse_snowflake(source_id))
        )
        conn.commit()

    def clear_asset_mappings(self, category: str = None):
        conn = self._get_conn()
        if category:
            conn.execute("DELETE FROM asset_mappings WHERE category = ?", (category,))
        else:
            conn.execute("DELETE FROM asset_mappings")
        conn.commit()

    # --- Metadata Methods ---

    def set_metadata(self, key: str, value: str):
        conn = self._get_conn()
        conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (key, str(value) if value is not None else None))
        conn.commit()

    def get_metadata(self, key: str) -> Optional[str]:
        conn = self._get_conn()
        row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def update_channel_tracking(self, channel_id: str, last_msg_id: str = None, last_msg_ts: str = None, msg_inc: int = 0, file_inc: int = 0):
        conn = self._get_conn()
        # Initialize if missing
        conn.execute("INSERT OR IGNORE INTO channel_tracking (channel_id) VALUES (?)", (str(channel_id),))
        
        if last_msg_id:
            conn.execute("UPDATE channel_tracking SET last_msg_id = ? WHERE channel_id = ?", (str(last_msg_id), str(channel_id)))
        if last_msg_ts:
            conn.execute("UPDATE channel_tracking SET last_msg_ts = ? WHERE channel_id = ?", (last_msg_ts, str(channel_id)))
        
        if msg_inc != 0 or file_inc != 0:
            conn.execute(
                "UPDATE channel_tracking SET msg_count = msg_count + ?, file_count = file_count + ? WHERE channel_id = ?",
                (msg_inc, file_inc, str(channel_id))
            )
        conn.commit()

    def get_channel_tracking(self, channel_id: str) -> Dict[str, Any]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM channel_tracking WHERE channel_id = ?", (str(channel_id),)).fetchone()
        if row:
            return dict(row)
        return {"last_msg_id": None, "last_msg_ts": None, "msg_count": 0, "file_count": 0}

    # Thread methods similar to channel methods
    def set_thread_message_mapping(self, channel_id: str, thread_id: str, source_id: str, target_id: str, timestamp: str = None):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO thread_mappings (channel_id, thread_id, source_msg_id, target_msg_id, timestamp) VALUES (?, ?, ?, ?, ?)",
            (str(channel_id), str(thread_id), parse_snowflake(source_id), str(target_id), timestamp)
        )
        conn.commit()

    def get_target_thread_message_id(self, channel_id: str, thread_id: str, source_id: str) -> Optional[Union[str, int]]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT target_msg_id FROM thread_mappings WHERE channel_id = ? AND thread_id = ? AND source_msg_id = ?",
            (str(channel_id), str(thread_id), parse_snowflake(source_id))
        ).fetchone()
        if row:
            val = row["target_msg_id"]
            return str(val) if self.platform == "stoat" else val
        return None

    def update_thread_tracking(self, channel_id: str, thread_id: str, last_msg_id: str = None, last_msg_ts: str = None, msg_inc: int = 0, file_inc: int = 0, completed: int = None):
        conn = self._get_conn()
        conn.execute("INSERT OR IGNORE INTO thread_tracking (channel_id, thread_id) VALUES (?, ?)", (str(channel_id), str(thread_id)))
        
        if last_msg_id:
            conn.execute("UPDATE thread_tracking SET last_msg_id = ? WHERE channel_id = ? AND thread_id = ?", (str(last_msg_id), str(channel_id), str(thread_id)))
        if last_msg_ts:
            conn.execute("UPDATE thread_tracking SET last_msg_ts = ? WHERE channel_id = ? AND thread_id = ?", (last_msg_ts, str(channel_id), str(thread_id)))
        if completed is not None:
            conn.execute("UPDATE thread_tracking SET completed = ? WHERE channel_id = ? AND thread_id = ?", (completed, str(channel_id), str(thread_id)))
        
        if msg_inc != 0 or file_inc != 0:
            conn.execute(
                "UPDATE thread_tracking SET msg_count = msg_count + ?, file_count = file_count + ? WHERE channel_id = ? AND thread_id = ?",
                (msg_inc, file_inc, str(channel_id), str(thread_id))
            )
        conn.commit()

    def get_thread_tracking(self, channel_id: str, thread_id: str) -> Dict[str, Any]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM thread_tracking WHERE channel_id = ? AND thread_id = ?", (str(channel_id), str(thread_id))).fetchone()
        if row:
            return dict(row)
        return {"last_msg_id": None, "last_msg_ts": None, "msg_count": 0, "file_count": 0}

    def clear_channel_data(self, channel_id: str):
        """Purge all mappings and tracking data for a specific channel and its threads."""
        conn = self._get_conn()
        conn.execute("DELETE FROM message_mappings WHERE channel_id = ?", (str(channel_id),))
        conn.execute("DELETE FROM thread_mappings WHERE channel_id = ?", (str(channel_id),))
        conn.execute("DELETE FROM channel_tracking WHERE channel_id = ?", (str(channel_id),))
        conn.execute("DELETE FROM thread_tracking WHERE channel_id = ?", (str(channel_id),))
        conn.commit()
        logger.info(f"Cleared all tracking and mapping data for channel: {channel_id}")

    def close(self):
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            del self._local.conn
