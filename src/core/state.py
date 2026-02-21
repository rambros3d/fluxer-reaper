import json
from pathlib import Path
from typing import Dict, Any

class MigrationState:
    """Manages persistence of the migration state to allow resumability."""
    
    def __init__(self, state_file: str | Path = "state.json"):
        self.state_file = Path(state_file)
        # mappings: discord_id -> fluxer_id
        self.channel_map: Dict[str, str] = {}
        self.role_map: Dict[str, str] = {}
        self.emoji_map: Dict[str, str] = {}
        self.sticker_map: Dict[str, str] = {}
        self.user_map: Dict[str, str] = {}
        self.message_map: Dict[str, str] = {}
        
        # tracking last message timestamp per channel to resume
        self.last_message_timestamps: Dict[str, str] = {}
        
        self.load()

    def load(self):
        if self.state_file.exists():
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.channel_map = data.get("channels", {})
                self.role_map = data.get("roles", {})
                self.emoji_map = data.get("emojis", {})
                self.sticker_map = data.get("stickers", {})
                self.user_map = data.get("users", {})
                self.message_map = data.get("messages", {})
                self.last_message_timestamps = data.get("last_message_timestamps", {})
                
            # Legacy Migration: Move role_, emoji_, sticker_ from channel_map to dedicated maps
            migrated = False
            legacy_keys = list(self.channel_map.keys())
            for k in legacy_keys:
                if k.startswith("role_"):
                    discord_id = k.replace("role_", "")
                    self.role_map[discord_id] = self.channel_map.pop(k)
                    migrated = True
                elif k.startswith("emoji_"):
                    discord_id = k.replace("emoji_", "")
                    self.emoji_map[discord_id] = self.channel_map.pop(k)
                    migrated = True
                elif k.startswith("sticker_"):
                    discord_id = k.replace("sticker_", "")
                    self.sticker_map[discord_id] = self.channel_map.pop(k)
                    migrated = True
            
            if migrated:
                self.save()

    def save(self):
        data = {
            "channels": self.channel_map,
            "roles": self.role_map,
            "emojis": self.emoji_map,
            "stickers": self.sticker_map,
            "users": self.user_map,
            "last_message_timestamps": self.last_message_timestamps,
            "messages": self.message_map
        }
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def set_channel_mapping(self, discord_id: str, fluxer_id: str):
        self.channel_map[str(discord_id)] = str(fluxer_id)
        self.save()

    def get_fluxer_channel_id(self, discord_id: str) -> str | None:
        return self.channel_map.get(str(discord_id))

    def set_message_mapping(self, discord_id: str, fluxer_id: str):
        self.message_map[str(discord_id)] = str(fluxer_id)
        self.save()

    def get_fluxer_message_id(self, discord_id: str) -> str | None:
        return self.message_map.get(str(discord_id))
        
    def update_last_message_timestamp(self, channel_id: str, timestamp: str):
        self.last_message_timestamps[str(channel_id)] = timestamp
        self.save()

    # --- Type Specific Getters/Setters ---

    def set_role_mapping(self, discord_id: str, fluxer_id: str):
        self.role_map[str(discord_id)] = str(fluxer_id)
        self.save()

    def get_fluxer_role_id(self, discord_id: str) -> str | None:
        return self.role_map.get(str(discord_id))

    def set_emoji_mapping(self, discord_id: str, fluxer_id: str):
        self.emoji_map[str(discord_id)] = str(fluxer_id)
        self.save()

    def get_fluxer_emoji_id(self, discord_id: str) -> str | None:
        return self.emoji_map.get(str(discord_id))

    def set_sticker_mapping(self, discord_id: str, fluxer_id: str):
        self.sticker_map[str(discord_id)] = str(fluxer_id)
        self.save()

    def get_fluxer_sticker_id(self, discord_id: str) -> str | None:
        return self.sticker_map.get(str(discord_id))

    # --- Danger Zone Clearing ---

    def clear_channel_mappings(self):
        """Clears all channel and category mappings."""
        self.channel_map.clear()
        self.save()

    def clear_role_mappings(self):
        """Clears all role mappings."""
        self.role_map.clear()
        self.save()

    def clear_asset_mappings(self):
        """Clears all emoji and sticker mappings."""
        self.emoji_map.clear()
        self.sticker_map.clear()
        self.save()

    def clear_message_history(self):
        """Clears all message mappings and timestamps."""
        self.message_map.clear()
        self.last_message_timestamps.clear()
        self.save()
