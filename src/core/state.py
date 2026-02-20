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
        self.user_map: Dict[str, str] = {}
        
        # tracking last message timestamp per channel to resume
        self.last_message_timestamps: Dict[str, str] = {}
        
        self.load()

    def load(self):
        if self.state_file.exists():
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.channel_map = data.get("channels", {})
                self.role_map = data.get("roles", {})
                self.user_map = data.get("users", {})
                self.last_message_timestamps = data.get("last_message_timestamps", {})

    def save(self):
        data = {
            "channels": self.channel_map,
            "roles": self.role_map,
            "users": self.user_map,
            "last_message_timestamps": self.last_message_timestamps
        }
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def set_channel_mapping(self, discord_id: str, fluxer_id: str):
        self.channel_map[str(discord_id)] = str(fluxer_id)
        self.save()

    def get_fluxer_channel_id(self, discord_id: str) -> str | None:
        return self.channel_map.get(str(discord_id))
        
    def update_last_message_timestamp(self, channel_id: str, timestamp: str):
        self.last_message_timestamps[str(channel_id)] = timestamp
        self.save()
