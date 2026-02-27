import json
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)

class MigrationState:
    """Manages persistence of the migration state to allow resumability."""
    
    def __init__(self, state_file: str | Path = "", messages_file: str | Path = ""):
        self.state_file: Path | None = Path(state_file) if state_file else None
        self.messages_file: Path | None = Path(messages_file) if messages_file else None
        
        # mappings: discord_id -> fluxer_id
        self.channel_map: Dict[str, str] = {}
        self.category_map: Dict[str, str] = {}
        self.role_map: Dict[str, str] = {}
        self.emoji_map: Dict[str, str] = {}
        self.sticker_map: Dict[str, str] = {}
        
        # audit log tracking
        self.audit_log_channel: str | None = None
        
        # message tracking
        self.message_map: Dict[str, str] = {}
        self.last_message_ids: Dict[str, str] = {}
        self.last_message_timestamps: Dict[str, str] = {}
        
        self.load()

    def load(self):
        migrated_state = False
        migrated_messages = False

        # 1. Load primary state file
        if self.state_file and self.state_file.exists():
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.channel_map = data.get("channels", {})
                self.category_map = data.get("categories", {})
                self.role_map = data.get("roles", {})
                self.emoji_map = data.get("emojis", {})
                self.sticker_map = data.get("stickers", {})
                self.audit_log_channel = data.get("audit_log_channel")

        # 2. Load separate messages file
        if self.messages_file and self.messages_file.exists():
            with open(self.messages_file, "r", encoding="utf-8") as f:
                msg_data = json.load(f)
                self.message_map = msg_data.get("messages", {})
                self.last_message_ids = msg_data.get("last_message_ids", {})
                self.last_message_timestamps = msg_data.get("last_message_timestamps", {})
        


    def save_state(self):
        """Saves only the core server configuration (channels, roles, emojis)."""
        if not self.state_file:
            return
        data = {
            "channels": self.channel_map,
            "categories": self.category_map,
            "roles": self.role_map,
            "emojis": self.emoji_map,
            "stickers": self.sticker_map,
            "audit_log_channel": self.audit_log_channel
        }
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def save_messages(self):
        """Saves only the message tracking data."""
        if not self.messages_file:
            return
        data = {
            "last_message_ids": self.last_message_ids,
            "last_message_timestamps": self.last_message_timestamps,
            "messages": self.message_map
        }
        with open(self.messages_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    # --- Type Specific Getters/Setters ---

    def set_channel_mapping(self, discord_id: str, fluxer_id: str):
        self.channel_map[str(discord_id)] = str(fluxer_id)
        self.save_state()

    def get_fluxer_channel_id(self, discord_id: str) -> str | None:
        return self.channel_map.get(str(discord_id))

    def remove_channel_mapping(self, discord_id: str):
        self.channel_map.pop(str(discord_id), None)
        self.save_state()

    def set_category_mapping(self, discord_id: str, fluxer_id: str):
        self.category_map[str(discord_id)] = str(fluxer_id)
        self.save_state()

    def get_fluxer_category_id(self, discord_id: str) -> str | None:
        return self.category_map.get(str(discord_id))

    def remove_category_mapping(self, discord_id: str):
        self.category_map.pop(str(discord_id), None)
        self.save_state()

    def set_role_mapping(self, discord_id: str, fluxer_id: str):
        self.role_map[str(discord_id)] = str(fluxer_id)
        self.save_state()

    def get_fluxer_role_id(self, discord_id: str) -> str | None:
        return self.role_map.get(str(discord_id))

    def remove_role_mapping(self, discord_id: str):
        self.role_map.pop(str(discord_id), None)
        self.save_state()

    def set_emoji_mapping(self, discord_id: str, fluxer_id: str):
        self.emoji_map[str(discord_id)] = str(fluxer_id)
        self.save_state()

    def get_fluxer_emoji_id(self, discord_id: str) -> str | None:
        return self.emoji_map.get(str(discord_id))

    def remove_emoji_mapping(self, discord_id: str):
        self.emoji_map.pop(str(discord_id), None)
        self.save_state()

    def set_sticker_mapping(self, discord_id: str, fluxer_id: str):
        self.sticker_map[str(discord_id)] = str(fluxer_id)
        self.save_state()

    def get_fluxer_sticker_id(self, discord_id: str) -> str | None:
        return self.sticker_map.get(str(discord_id))

    def remove_sticker_mapping(self, discord_id: str):
        self.sticker_map.pop(str(discord_id), None)
        self.save_state()

    # --- Generic Aliases for target platform migration ---
    
    get_target_channel_id = get_fluxer_channel_id
    set_channel_mapping = set_channel_mapping # already generic enough in name if we ignore the 'fluxer' in implementation
    
    def set_target_channel_mapping(self, discord_id: str, target_id: str):
        self.set_channel_mapping(discord_id, target_id)

    def get_target_category_id(self, discord_id: str) -> str | None:
        return self.get_fluxer_category_id(discord_id)

    def set_target_category_mapping(self, discord_id: str, target_id: str):
        self.set_category_mapping(discord_id, target_id)

    def get_target_role_id(self, discord_id: str) -> str | None:
        return self.get_fluxer_role_id(discord_id)

    def set_target_role_mapping(self, discord_id: str, target_id: str):
        self.set_role_mapping(discord_id, target_id)

    def get_target_emoji_id(self, discord_id: str) -> str | None:
        return self.get_fluxer_emoji_id(discord_id)

    def set_target_emoji_mapping(self, discord_id: str, target_id: str):
        self.set_emoji_mapping(discord_id, target_id)

    def get_target_sticker_id(self, discord_id: str) -> str | None:
        return self.get_fluxer_sticker_id(discord_id)

    def set_target_sticker_mapping(self, discord_id: str, target_id: str):
        self.set_sticker_mapping(discord_id, target_id)
        
    def get_target_message_id(self, discord_id: str) -> str | None:
        return self.get_fluxer_message_id(discord_id)

    def set_target_message_mapping(self, discord_id: str, target_id: str):
        self.set_message_mapping(discord_id, target_id)

    # --- Message Management ---

    def set_message_mapping(self, discord_id: str, fluxer_id: str):
        self.message_map[str(discord_id)] = str(fluxer_id)
        self.save_messages()

    def get_fluxer_message_id(self, discord_id: str) -> str | None:
        return self.message_map.get(str(discord_id))
        
    def update_last_message_timestamp(self, channel_id: str, timestamp: str):
        self.last_message_timestamps[str(channel_id)] = timestamp
        self.save_messages()

    def update_last_message_id(self, channel_id: str, message_id: str):
        self.last_message_ids[str(channel_id)] = message_id
        self.save_messages()
        
    def get_last_message_id(self, channel_id: str) -> str | None:
        return self.last_message_ids.get(str(channel_id))

    # --- Danger Zone Clearing ---

    def clear_channel_mappings(self):
        """Clears all channel and category mappings."""
        self.channel_map.clear()
        self.category_map.clear()
        self.save_state()

    def clear_role_mappings(self):
        """Clears all role mappings."""
        self.role_map.clear()
        self.save_state()

    def clear_asset_mappings(self):
        """Clears all emoji and sticker mappings."""
        self.emoji_map.clear()
        self.sticker_map.clear()
        self.save_state()

    def clear_message_history(self):
        """Clears all message mappings and timestamps."""
        self.message_map.clear()
        self.last_message_ids.clear()
        self.last_message_timestamps.clear()
        self.save_messages()

    def set_folder(self, server_id: str, clean_name: str):
        new_folder = Path(f"{clean_name}-{server_id}")
        
        # If we have an existing folder that is different, rename it
        if self.state_file and self.state_file.parent.exists() and self.state_file.parent != new_folder:
            # Check if it's actually in a server-specific folder (not roots)
            if self.state_file.parent.name.endswith(f"-{server_id}"):
                try:
                    self.state_file.parent.rename(new_folder)
                except Exception as e:
                    logger.debug(f"Could not rename {self.state_file.parent} to {new_folder}: {e}")

        new_folder.mkdir(exist_ok=True)
        
        self.state_file = new_folder / "state-migration.json"
        self.messages_file = new_folder / "message-tracker.json"
        
        self.save_state()
        self.save_messages()
