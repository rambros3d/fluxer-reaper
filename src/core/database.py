import sqlite3
import logging
from pathlib import Path
from typing import Optional, Dict, Any
import threading

logger = logging.getLogger(__name__)

class MigrationDatabase:
    """
    SQLite-based persistence for large-scale migration mappings and stats.
    Replaces the memory-bloated and O(N^2) JSON persistence for messages.
    """
    
    _local = threading.local()

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        """Initialize tables if they don't exist."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Table for message mappings: SourceID -> TargetID
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS message_mappings (
                channel_id TEXT,
                source_msg_id TEXT,
                target_msg_id TEXT,
                timestamp TEXT,
                PRIMARY KEY (channel_id, source_msg_id)
            )
        """)
        
        # Table for thread mappings
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS thread_mappings (
                channel_id TEXT,
                thread_id TEXT,
                source_msg_id TEXT,
                target_msg_id TEXT,
                timestamp TEXT,
                PRIMARY KEY (channel_id, thread_id, source_msg_id)
            )
        """)
        
        # Table for per-channel stats and tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS channel_tracking (
                channel_id TEXT PRIMARY KEY,
                last_msg_id TEXT,
                last_msg_ts TEXT,
                msg_count INTEGER DEFAULT 0,
                file_count INTEGER DEFAULT 0
            )
        """)
        
        # Table for per-thread stats
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS thread_tracking (
                channel_id TEXT,
                thread_id TEXT,
                last_msg_id TEXT,
                last_msg_ts TEXT,
                msg_count INTEGER DEFAULT 0,
                file_count INTEGER DEFAULT 0,
                PRIMARY KEY (channel_id, thread_id)
            )
        """)

        # Table for entity mappings (channels, roles, etc.)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS entity_mappings (
                category TEXT,
                source_id TEXT,
                target_id TEXT,
                PRIMARY KEY (category, source_id)
            )
        """)

        # Table for general metadata
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        conn.commit()
        conn.close()

    def set_message_mapping(self, channel_id: str, source_id: str, target_id: str, timestamp: str = None):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO message_mappings (channel_id, source_msg_id, target_msg_id, timestamp) VALUES (?, ?, ?, ?)",
            (channel_id, source_id, target_id, timestamp)
        )
        conn.commit()

    def get_target_message_id(self, channel_id: str, source_id: str) -> Optional[str]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT target_msg_id FROM message_mappings WHERE channel_id = ? AND source_msg_id = ?",
            (channel_id, source_id)
        ).fetchone()
        return row["target_msg_id"] if row else None

    # --- New Entity Mapping Methods ---

    def set_entity_mapping(self, category: str, source_id: str, target_id: str):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO entity_mappings (category, source_id, target_id) VALUES (?, ?, ?)",
            (category, str(source_id), str(target_id))
        )
        conn.commit()

    def get_entity_mapping(self, category: str, source_id: str) -> Optional[str]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT target_id FROM entity_mappings WHERE category = ? AND source_id = ?",
            (category, str(source_id))
        ).fetchone()
        return row["target_id"] if row else None

    def get_all_entity_mappings(self, category: str) -> Dict[str, str]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT source_id, target_id FROM entity_mappings WHERE category = ?",
            (category,)
        ).fetchall()
        return {row["source_id"]: row["target_id"] for row in rows}

    def delete_entity_mapping(self, category: str, source_id: str):
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM entity_mappings WHERE category = ? AND source_id = ?",
            (category, str(source_id))
        )
        conn.commit()

    def clear_entities(self, category: str = None):
        conn = self._get_conn()
        if category:
            conn.execute("DELETE FROM entity_mappings WHERE category = ?", (category,))
        else:
            conn.execute("DELETE FROM entity_mappings")
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
        conn.execute("INSERT OR IGNORE INTO channel_tracking (channel_id) VALUES (?)", (channel_id,))
        
        if last_msg_id:
            conn.execute("UPDATE channel_tracking SET last_msg_id = ? WHERE channel_id = ?", (last_msg_id, channel_id))
        if last_msg_ts:
            conn.execute("UPDATE channel_tracking SET last_msg_ts = ? WHERE channel_id = ?", (last_msg_ts, channel_id))
        
        if msg_inc != 0 or file_inc != 0:
            conn.execute(
                "UPDATE channel_tracking SET msg_count = msg_count + ?, file_count = file_count + ? WHERE channel_id = ?",
                (msg_inc, file_inc, channel_id)
            )
        conn.commit()

    def get_channel_tracking(self, channel_id: str) -> Dict[str, Any]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM channel_tracking WHERE channel_id = ?", (channel_id,)).fetchone()
        if row:
            return dict(row)
        return {"last_msg_id": None, "last_msg_ts": None, "msg_count": 0, "file_count": 0}

    # Thread methods similar to channel methods
    def set_thread_message_mapping(self, channel_id: str, thread_id: str, source_id: str, target_id: str, timestamp: str = None):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO thread_mappings (channel_id, thread_id, source_msg_id, target_msg_id, timestamp) VALUES (?, ?, ?, ?, ?)",
            (channel_id, thread_id, source_id, target_id, timestamp)
        )
        conn.commit()

    def get_target_thread_message_id(self, channel_id: str, thread_id: str, source_id: str) -> Optional[str]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT target_msg_id FROM thread_mappings WHERE channel_id = ? AND thread_id = ? AND source_msg_id = ?",
            (channel_id, thread_id, source_id)
        ).fetchone()
        return row["target_msg_id"] if row else None

    def update_thread_tracking(self, channel_id: str, thread_id: str, last_msg_id: str = None, last_msg_ts: str = None, msg_inc: int = 0, file_inc: int = 0):
        conn = self._get_conn()
        conn.execute("INSERT OR IGNORE INTO thread_tracking (channel_id, thread_id) VALUES (?, ?)", (channel_id, thread_id))
        
        if last_msg_id:
            conn.execute("UPDATE thread_tracking SET last_msg_id = ? WHERE channel_id = ? AND thread_id = ?", (last_msg_id, channel_id, thread_id))
        if last_msg_ts:
            conn.execute("UPDATE thread_tracking SET last_msg_ts = ? WHERE channel_id = ? AND thread_id = ?", (last_msg_ts, channel_id, thread_id))
        
        if msg_inc != 0 or file_inc != 0:
            conn.execute(
                "UPDATE thread_tracking SET msg_count = msg_count + ?, file_count = file_count + ? WHERE channel_id = ? AND thread_id = ?",
                (msg_inc, file_inc, channel_id, thread_id)
            )
        conn.commit()

    def get_thread_tracking(self, channel_id: str, thread_id: str) -> Dict[str, Any]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM thread_tracking WHERE channel_id = ? AND thread_id = ?", (channel_id, thread_id)).fetchone()
        if row:
            return dict(row)
        return {"last_msg_id": None, "last_msg_ts": None, "msg_count": 0, "file_count": 0}

    def close(self):
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            del self._local.conn
