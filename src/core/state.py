import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class MigrationState:
    """Manages persistence of the migration state to allow resumability.
    Uses SQLite for ALL mappings and metadata.
    """
    
    def __init__(self):
        # database instance for all persistence
        self.db: Optional['MigrationDatabase'] = None
        
    def _ensure_db(self):
        if not self.db:
            logger.warning("MigrationState: Accessing database before initialization")
            return False
        return True

    # --- Type Specific Getters/Setters (Database Backed) ---

    def set_channel_mapping(self, discord_id: str, target_id: str):
        if self._ensure_db():
            self.db.set_entity_mapping("channel", str(discord_id), str(target_id))

    def get_target_channel_id(self, discord_id: str) -> str | None:
        if self._ensure_db():
            return self.db.get_entity_mapping("channel", str(discord_id))
        return None
    
    get_fluxer_channel_id = get_target_channel_id
    set_target_channel_mapping = set_channel_mapping

    def set_category_mapping(self, discord_id: str, target_id: str):
        if self._ensure_db():
            self.db.set_entity_mapping("category", str(discord_id), str(target_id))

    def get_target_category_id(self, discord_id: str) -> str | None:
        if self._ensure_db():
            return self.db.get_entity_mapping("category", str(discord_id))
        return None
    
    get_fluxer_category_id = get_target_category_id
    set_target_category_mapping = set_category_mapping

    def set_role_mapping(self, discord_id: str, target_id: str):
        if self._ensure_db():
            self.db.set_entity_mapping("role", str(discord_id), str(target_id))

    def get_target_role_id(self, discord_id: str) -> str | None:
        if self._ensure_db():
            return self.db.get_entity_mapping("role", str(discord_id))
        return None
    
    get_fluxer_role_id = get_target_role_id
    set_target_role_mapping = set_role_mapping

    def set_emoji_mapping(self, discord_id: str, target_id: str):
        if self._ensure_db():
            self.db.set_entity_mapping("emoji", str(discord_id), str(target_id))

    def get_target_emoji_id(self, discord_id: str) -> str | None:
        if self._ensure_db():
            return self.db.get_entity_mapping("emoji", str(discord_id))
        return None
    
    get_fluxer_emoji_id = get_target_emoji_id
    set_target_emoji_mapping = set_emoji_mapping

    def set_sticker_mapping(self, discord_id: str, target_id: str):
        if self._ensure_db():
            self.db.set_entity_mapping("sticker", str(discord_id), str(target_id))

    def get_target_sticker_id(self, discord_id: str) -> str | None:
        if self._ensure_db():
            return self.db.get_entity_mapping("sticker", str(discord_id))
        return None
    
    get_fluxer_sticker_id = get_target_sticker_id
    set_target_sticker_mapping = set_sticker_mapping

    # --- Properties for backward compatibility ---
    @property
    def channel_map(self) -> Dict[str, str]:
        return self.db.get_all_entity_mappings("channel") if self.db else {}

    @property
    def category_map(self) -> Dict[str, str]:
        return self.db.get_all_entity_mappings("category") if self.db else {}

    @property
    def role_map(self) -> Dict[str, str]:
        return self.db.get_all_entity_mappings("role") if self.db else {}

    @property
    def emoji_map(self) -> Dict[str, str]:
        return self.db.get_all_entity_mappings("emoji") if self.db else {}

    @property
    def sticker_map(self) -> Dict[str, str]:
        return self.db.get_all_entity_mappings("sticker") if self.db else {}

    @property
    def audit_log_channel(self) -> str | None:
        return self.db.get_metadata("audit_log_channel") if self.db else None

    @audit_log_channel.setter
    def audit_log_channel(self, value: str | None):
        if self._ensure_db():
            self.db.set_metadata("audit_log_channel", value)

    # --- Message Management ---

    def set_target_message_mapping(self, target_channel_id: str, discord_id: str, target_id: str):
        if self._ensure_db():
            self.db.set_message_mapping(str(target_channel_id), str(discord_id), str(target_id))

    def get_target_message_id(self, target_channel_id: str, discord_id: str) -> str | None:
        if self._ensure_db():
            return self.db.get_target_message_id(str(target_channel_id), str(discord_id))
        return None

    def set_message_mapping(self, target_channel_id: str, discord_id: str, target_id: str):
        self.set_target_message_mapping(target_channel_id, discord_id, target_id)

    def get_fluxer_message_id(self, target_channel_id: str, discord_id: str) -> str | None:
        return self.get_target_message_id(target_channel_id, discord_id)

    def increment_stats(self, target_channel_id: str, messages: int = 1, files: int = 0):
        if self._ensure_db():
            self.db.update_channel_tracking(str(target_channel_id), msg_inc=messages, file_inc=files)

    def increment_thread_stats(self, target_channel_id: str, thread_id: str, messages: int = 1, files: int = 0):
        if self._ensure_db():
            self.db.update_thread_tracking(str(target_channel_id), str(thread_id), msg_inc=messages, file_inc=files)

    def set_thread_message_mapping(self, target_channel_id: str, thread_id: str, discord_id: str, target_id: str):
        if self._ensure_db():
            self.db.set_thread_message_mapping(str(target_channel_id), str(thread_id), str(discord_id), str(target_id))

    def update_thread_last_message_timestamp(self, target_channel_id: str, thread_id: str, timestamp: str):
        if self._ensure_db():
            self.db.update_thread_tracking(str(target_channel_id), str(thread_id), last_msg_ts=str(timestamp))

    def update_thread_last_message_id(self, target_channel_id: str, thread_id: str, message_id: str):
        if self._ensure_db():
            self.db.update_thread_tracking(str(target_channel_id), str(thread_id), last_msg_id=str(message_id))

    def get_thread_message_id(self, target_channel_id: str, thread_id: str, discord_id: str) -> str | None:
        if self._ensure_db():
            return self.db.get_target_thread_message_id(str(target_channel_id), str(thread_id), str(discord_id))
        return None

    def update_last_message_timestamp(self, target_channel_id: str, timestamp: str):
        if self._ensure_db():
            self.db.update_channel_tracking(str(target_channel_id), last_msg_ts=str(timestamp))

    def update_last_message_id(self, target_channel_id: str, message_id: str):
        if self._ensure_db():
            self.db.update_channel_tracking(str(target_channel_id), last_msg_id=str(message_id))
        
    def get_last_message_id(self, target_channel_id: str) -> str | None:
        if self._ensure_db():
            return self.db.get_channel_tracking(str(target_channel_id)).get("last_msg_id")
        return None

    def find_message_mapping(self, discord_id: str) -> tuple[str, str] | tuple[None, None]:
        if not self.db:
            return None, None
        conn = self.db._get_conn()
        row = conn.execute("SELECT channel_id, target_msg_id FROM message_mappings WHERE source_msg_id = ?", (str(discord_id),)).fetchone()
        if row:
            return str(row["channel_id"]), str(row["target_msg_id"])
        row = conn.execute("SELECT thread_id, target_msg_id FROM thread_mappings WHERE source_msg_id = ?", (str(discord_id),)).fetchone()
        if row:
            return str(row["thread_id"]), str(row["target_msg_id"])
        return None, None

    # --- Danger Zone Clearing ---

    def clear_channel_mappings(self):
        if self._ensure_db():
            self.db.clear_entities("channel")
            self.db.clear_entities("category")

    def clear_role_mappings(self):
        if self._ensure_db():
            self.db.clear_entities("role")

    def clear_asset_mappings(self):
        if self._ensure_db():
            self.db.clear_entities("emoji")
            self.db.clear_entities("sticker")

    def clear_message_history(self):
        if self.db:
            conn = self.db._get_conn()
            conn.execute("DELETE FROM message_mappings")
            conn.execute("DELETE FROM thread_mappings")
            conn.execute("DELETE FROM channel_tracking")
            conn.execute("DELETE FROM thread_tracking")
            conn.commit()

    def set_folder(self, server_id: str, clean_name: str, base_dir: Path | str = ""):
        """
        Initializes the SQLite database based on community name and ID.
        Filename: {name}-{id}.db (Flat structure)
        ID is priority: if a DB with the same ID exists but different name, rename it.
        """
        base = Path(base_dir) if base_dir else Path(".")
        desired_filename = f"{clean_name}-{server_id}.db"
        desired_path = base / desired_filename
        
        # Priority 1: Match by ID
        existing_db: Path | None = None
        # Look for any file ending with -{server_id}.db
        for f in base.glob(f"*-{server_id}.db"):
            if f.is_file():
                existing_db = f
                break
        
        db_path = desired_path
        if existing_db:
            if existing_db.name != desired_filename:
                logger.info(f"Server renamed: moving {existing_db.name} -> {desired_filename}")
                try:
                    existing_db.rename(desired_path)
                except Exception as e:
                    logger.error(f"Failed to rename database: {e}")
                    # If rename fails, we'll use the existing one if it exists at the old path,
                    # or the desired one if it exists there.
                    if not desired_path.exists():
                        db_path = existing_db
        
        logger.info(f"Setting active migration database: {db_path}")
        
        from src.core.database import MigrationDatabase
        if self.db:
            self.db.close()
        self.db = MigrationDatabase(db_path)
        logger.info(f"Initialized SQLite database at {db_path}")

    # No-op methods kept for compatibility with callers that might try to load/save JSON
    def load(self): pass
    def save_state(self): pass
