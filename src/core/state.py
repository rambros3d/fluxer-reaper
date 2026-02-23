import json
from pathlib import Path
from typing import Dict, Any

class MigrationState:
    """Manages persistence of the migration state to allow resumability."""
    
    def __init__(self, state_file: str | Path = "state.json", messages_file: str | Path = "messages.json"):
        self.state_file = Path(state_file)
        self.messages_file = Path(messages_file)
        
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

        # 1. Load primary state.json
        if self.state_file.exists():
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.channel_map = data.get("channels", {})
                self.category_map = data.get("categories", {})
                self.role_map = data.get("roles", {})
                self.emoji_map = data.get("emojis", {})
                self.sticker_map = data.get("stickers", {})
                self.audit_log_channel = data.get("audit_log_channel")
                
                # Check for legacy messages in state.json
                if "messages" in data or "last_message_timestamps" in data:
                    self.message_map = data.get("messages", {})
                    self.last_message_ids = data.get("last_message_ids", {})
                    self.last_message_timestamps = data.get("last_message_timestamps", {})
                    migrated_messages = True # We found legacy data, we should write it out to messages.json later

            # Legacy Migration: Move role_, emoji_, sticker_ from channel_map to dedicated maps
            legacy_keys = list(self.channel_map.keys())
            for k in legacy_keys:
                if k.startswith("role_"):
                    discord_id = k.replace("role_", "")
                    self.role_map[discord_id] = self.channel_map.pop(k)
                    migrated_state = True
                elif k.startswith("emoji_"):
                    discord_id = k.replace("emoji_", "")
                    self.emoji_map[discord_id] = self.channel_map.pop(k)
                    migrated_state = True
                elif k.startswith("sticker_"):
                    discord_id = k.replace("sticker_", "")
                    self.sticker_map[discord_id] = self.channel_map.pop(k)
                    migrated_state = True

        # 2. Load separate messages.json (overrides legacy state.json)
        if self.messages_file.exists():
            with open(self.messages_file, "r", encoding="utf-8") as f:
                msg_data = json.load(f)
                self.message_map = msg_data.get("messages", {})
                self.last_message_ids = msg_data.get("last_message_ids", {})
                self.last_message_timestamps = msg_data.get("last_message_timestamps", {})
                migrated_messages = False # No need to force a migrating save since it already exists
        
        # 3. Save if we migrated any legacy data to separate maps/files
        if migrated_state or migrated_messages:
            self.save_state()
        if migrated_messages:
            self.save_messages()

    def save_state(self):
        """Saves only the core server configuration (channels, roles, emojis)."""
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
