import json
import logging
from pathlib import Path
from typing import Dict, Optional, Any, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.database import MigrationDatabase

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

    # --- Type Specific Getters/Setters    # --- Channel Mapping ---
    def set_channel_mapping(self, discord_id: int | str, target_id: str):
        """Maps an original text/voice/forum channel ID to a minted server channel ID."""
        if self.db:
            self.db.set_server_mapping("channel", str(discord_id), str(target_id))
            
    def get_target_channel_id(self, discord_id: int | str) -> str | None:
        if self.db:
            return self.db.get_server_mapping("channel", str(discord_id))
        return None
        
    def remove_channel_mapping(self, discord_id: int | str):
        if self.db:
            self.db.delete_server_mapping("channel", str(discord_id))

    
    get_fluxer_channel_id = get_target_channel_id
    set_target_channel_mapping = set_channel_mapping

    # --- Category Mapping ---
    def set_category_mapping(self, discord_id: int | str, target_id: str):
        """Maps an original discord category ID to a Stoat category Group ID."""
        if self.db:
            self.db.set_server_mapping("category", str(discord_id), str(target_id))
            
    def get_category_mapping(self, discord_id: int | str) -> str | None:
        """Returns the Stoat Group ID for a previously migrated category."""
        if self.db:
            return self.db.get_server_mapping("category", str(discord_id))
        return None
        
    def remove_category_mapping(self, discord_id: int | str):
        if self.db:
            self.db.delete_server_mapping("category", str(discord_id))
    
    get_fluxer_category_id = get_category_mapping
    get_target_category_id = get_category_mapping
    set_target_category_mapping = set_category_mapping

    # --- Role Mapping ---
    def set_role_mapping(self, discord_id: int | str, target_id: str):
        """Maps an original discord Role ID to a Stoat Role ID."""
        if self.db:
            self.db.set_server_mapping("role", str(discord_id), str(target_id))

    def get_role_mapping(self, discord_id: int | str) -> str | None:
        """Returns the target Role ID for a previously migrated Role."""
        if self.db:
            return self.db.get_server_mapping("role", str(discord_id))
        return None
        
    def remove_role_mapping(self, discord_id: int | str):
        if self.db:
            self.db.delete_server_mapping("role", str(discord_id))
    
    get_fluxer_role_id = get_role_mapping
    get_target_role_id = get_role_mapping
    set_target_role_mapping = set_role_mapping

    # --- Emoji Mapping ---
    def set_emoji_mapping(self, discord_id: int | str, target_id: str):
        """Maps an original discord Custom Emoji ID to a minted Emoji ID/URL."""
        if self.db:
            self.db.set_asset_mapping("emoji", str(discord_id), str(target_id))
            
    def get_emoji_mapping(self, discord_id: int | str) -> str | None:
        if self.db:
            return self.db.get_asset_mapping("emoji", str(discord_id))
        return None

    def remove_emoji_mapping(self, discord_id: int | str):
        if self.db:
            self.db.delete_asset_mapping("emoji", str(discord_id))
    
    get_fluxer_emoji_id = get_emoji_mapping
    get_target_emoji_id = get_emoji_mapping
    set_target_emoji_mapping = set_emoji_mapping

    # --- Sticker Mapping ---
    def set_sticker_mapping(self, discord_id: int | str, target_id: str):
        """Maps an original discord Custom Sticker ID to a target URL or ID."""
        if self.db:
            self.db.set_asset_mapping("sticker", str(discord_id), str(target_id))
            
    def get_sticker_mapping(self, discord_id: int | str) -> str | None:
        if self.db:
            return self.db.get_asset_mapping("sticker", str(discord_id))
        return None
        
    def remove_sticker_mapping(self, discord_id: int | str):
        if self.db:
            self.db.delete_asset_mapping("sticker", str(discord_id))
    
    get_fluxer_sticker_id = get_sticker_mapping
    get_target_sticker_id = get_sticker_mapping
    set_target_sticker_mapping = set_sticker_mapping

    # --- Properties for backward compatibility ---
    @property
    def channel_map(self) -> Dict[Union[str, int], Union[str, int]]:
        return self.db.get_all_server_mappings("channel") if self.db else {}
        
    @property
    def category_map(self) -> Dict[Union[str, int], Union[str, int]]:
        return self.db.get_all_server_mappings("category") if self.db else {}
        
    @property
    def role_map(self) -> Dict[Union[str, int], Union[str, int]]:
        return self.db.get_all_server_mappings("role") if self.db else {}

    @property
    def emoji_map(self) -> Dict[Union[str, int], Union[str, int]]:
        return self.db.get_all_asset_mappings("emoji") if self.db else {}
        
    @property
    def sticker_map(self) -> Dict[Union[str, int], Union[str, int]]:
        return self.db.get_all_asset_mappings("sticker") if self.db else {}

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

    def get_target_message_id(self, target_channel_id: str, discord_id: str) -> str | int | None:
        if self._ensure_db():
            return self.db.get_target_message_id(str(target_channel_id), str(discord_id))
        return None

    def set_message_mapping(self, target_channel_id: str, discord_id: str, target_id: str):
        self.set_target_message_mapping(target_channel_id, discord_id, target_id)

    def get_fluxer_message_id(self, target_channel_id: str, discord_id: str) -> str | int | None:
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

    def update_thread_completed(self, target_channel_id: str, thread_id: str, completed: bool = True):
        if self._ensure_db():
            self.db.update_thread_tracking(str(target_channel_id), str(thread_id), completed=1 if completed else 0)

    def is_thread_completed(self, target_channel_id: str, thread_id: str) -> bool:
        if self._ensure_db():
            tracking = self.db.get_thread_tracking(str(target_channel_id), str(thread_id))
            return bool(tracking.get("completed", 0))
        return False

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

    def get_global_min_last_message_id(self, mapped_channel_ids: List[str]) -> str | None:
        """Returns the absolute minimum last_msg_id among the given list of mapped target channel IDs."""
        if self._ensure_db():
            return self.db.get_global_min_last_message_id(mapped_channel_ids)
        return None

    def get_thread_last_message_id(self, target_channel_id: str, thread_id: str) -> str | None:
        if self._ensure_db():
            return self.db.get_thread_tracking(str(target_channel_id), str(thread_id)).get("last_msg_id")
        return None

    def find_message_mapping(self, discord_id: str) -> tuple[str, str] | tuple[None, None]:
        if not self.db:
            return None, None
        conn = self.db._get_conn()
        row = conn.execute("SELECT channel_id, target_msg_id FROM message_mappings WHERE source_msg_id = ?", (str(discord_id),)).fetchone()
        if row:
            return str(row["channel_id"]), str(row["target_msg_id"])
        row = conn.execute("SELECT channel_id, target_msg_id FROM thread_mappings WHERE source_msg_id = ?", (str(discord_id),)).fetchone()
        if row:
            return str(row["channel_id"]), str(row["target_msg_id"])
        return None, None

    # --- Danger Zone Clearing ---

    def clear_channel_mappings(self):
        if self.db:
            self.db.clear_server_mappings("channel")
            self.db.clear_server_mappings("category")

    def clear_role_mappings(self):
        if self.db:
            self.db.clear_server_mappings("role")

    def clear_asset_mappings(self):
        if self.db:
            self.db.clear_asset_mappings("emoji")
            self.db.clear_asset_mappings("sticker")

    def clear_message_history(self):
        if self.db:
            conn = self.db._get_conn()
            conn.execute("DELETE FROM message_mappings")
            conn.execute("DELETE FROM thread_mappings")
            conn.execute("DELETE FROM channel_tracking")
            conn.execute("DELETE FROM thread_tracking")
            conn.commit()

    def clear_channel_data(self, target_channel_id: str):
        if self._ensure_db():
            self.db.clear_channel_data(str(target_channel_id))

    def set_folder(self, server_id: str, clean_name: str, platform: str = "stoat", base_dir: Path | str = ""):
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
        self.db = MigrationDatabase(db_path, platform=platform)
        logger.info(f"Initialized SQLite database at {db_path}")

    def get_user_alias(self, user_id: str) -> str | None:
        """Gets or generates a unique alias for a given user ID via the Migration Database."""
        if self.db:
            return self.db.get_or_create_user_alias(user_id)
        return None

    # No-op methods kept for compatibility with callers that might try to load/save JSON
    def load(self): pass
    def save_state(self): pass
