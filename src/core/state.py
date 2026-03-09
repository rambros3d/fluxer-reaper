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
        
        # message tracking per target channel
        # Format: { target_channel_id: {"message_map": {}, "last_message_id": "", "last_message_timestamp": ""} }
        self.channel_messages: Dict[str, Dict[str, Any]] = {}
        
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
            logger.info(f"Loading messages from {self.messages_file.name}")
            try:
                with open(self.messages_file, "r", encoding="utf-8") as f:
                    msg_data = json.load(f)
                    
                    # Check for new schema (nested under 'channels')
                    if "channels" in msg_data:
                        self.channel_messages = msg_data.get("channels", {})
                        logger.debug(f"Loaded {len(self.channel_messages)} tracked channels.")
                    else:
                        logger.warning("Legacy schema or empty tracker detected in messages file.")
                        # Legacy schema detection & conversion to a default 'unknown_channel' just in case,
                        # though new migrations shouldn't hit this based on previous removals.
                        legacy_map = msg_data.get("messages", {})
                        legacy_ids = msg_data.get("last_message_ids", {})
                        legacy_times = msg_data.get("last_message_timestamps", {})
                        
                        if legacy_map or legacy_ids or legacy_times:
                            self.channel_messages = {
                                "legacy_migrated_channel": {
                                    "message_map": legacy_map,
                                    "last_message_id": list(legacy_ids.values())[-1] if legacy_ids else "",
                                    "last_message_timestamp": list(legacy_times.values())[-1] if legacy_times else ""
                                }
                            }
            except Exception as e:
                logger.error(f"Failed to load messages file: {e}")
        


    def save_state(self):
        """Saves only the core server configuration (channels, roles, emojis)."""
        if not self.state_file:
            return
            
        logger.debug(f"Saving state to {self.state_file.name}")
        data = {
            "channels": self.channel_map,
            "categories": self.category_map,
            "roles": self.role_map,
            "emojis": self.emoji_map,
            "stickers": self.sticker_map,
            "audit_log_channel": self.audit_log_channel
        }
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save state file: {e}")

    def save_messages(self):
        """Saves only the message tracking data."""
        if not self.messages_file:
            return
            
        logger.debug(f"Saving messages to {self.messages_file.name}")
        data = {
            "channels": self.channel_messages
        }
        try:
            with open(self.messages_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save messages file: {e}")

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
        
    def get_target_message_id(self, target_channel_id: str, discord_id: str) -> str | None:
        return self.get_fluxer_message_id(target_channel_id, discord_id)

    def set_target_message_mapping(self, target_channel_id: str, discord_id: str, target_id: str):
        self.set_message_mapping(target_channel_id, discord_id, target_id)

    # --- Message Management ---

    def _ensure_channel_tracking(self, target_channel_id: str):
        if str(target_channel_id) not in self.channel_messages:
            self.channel_messages[str(target_channel_id)] = {
                "message_map": {},
                "last_message_id": "",
                "last_message_timestamp": "",
                "total_messages": 0,
                "total_files": 0,
                "threads": {}
            }
            
    def increment_stats(self, target_channel_id: str, messages: int = 1, files: int = 0):
        self._ensure_channel_tracking(target_channel_id)
        c = self.channel_messages[str(target_channel_id)]
        c["total_messages"] = c.get("total_messages", 0) + messages
        c["total_files"] = c.get("total_files", 0) + files
        self.save_messages()

    # --- Thread Tracking ---

    def _ensure_thread_tracking(self, target_channel_id: str, thread_id: str):
        self._ensure_channel_tracking(target_channel_id)
        threads = self.channel_messages[str(target_channel_id)].setdefault("threads", {})
        if str(thread_id) not in threads:
            threads[str(thread_id)] = {
                "thread_map": {},
                "last_message_id": "",
                "last_message_timestamp": "",
                "total_messages": 0,
                "total_files": 0
            }

    def increment_thread_stats(self, target_channel_id: str, thread_id: str, messages: int = 1, files: int = 0):
        self._ensure_thread_tracking(target_channel_id, thread_id)
        t = self.channel_messages[str(target_channel_id)]["threads"][str(thread_id)]
        t["total_messages"] = t.get("total_messages", 0) + messages
        t["total_files"] = t.get("total_files", 0) + files
        self.save_messages()

    def set_thread_message_mapping(self, target_channel_id: str, thread_id: str, discord_id: str, target_id: str):
        self._ensure_thread_tracking(target_channel_id, thread_id)
        self.channel_messages[str(target_channel_id)]["threads"][str(thread_id)]["thread_map"][str(discord_id)] = str(target_id)
        # Also add to main message_map for global message resolution (like replies)
        self.set_message_mapping(target_channel_id, discord_id, target_id)

    def update_thread_last_message_timestamp(self, target_channel_id: str, thread_id: str, timestamp: str):
        self._ensure_thread_tracking(target_channel_id, thread_id)
        self.channel_messages[str(target_channel_id)]["threads"][str(thread_id)]["last_message_timestamp"] = str(timestamp)
        self.save_messages()

    def update_thread_last_message_id(self, target_channel_id: str, thread_id: str, message_id: str):
        self._ensure_thread_tracking(target_channel_id, thread_id)
        self.channel_messages[str(target_channel_id)]["threads"][str(thread_id)]["last_message_id"] = str(message_id)
        self.save_messages()

    def get_thread_message_id(self, target_channel_id: str, thread_id: str, discord_id: str) -> str | None:
        if str(target_channel_id) in self.channel_messages:
            threads = self.channel_messages[str(target_channel_id)].get("threads", {})
            if str(thread_id) in threads:
                return threads[str(thread_id)]["thread_map"].get(str(discord_id))
        return None
            
    def set_message_mapping(self, target_channel_id: str, discord_id: str, fluxer_id: str):
        self._ensure_channel_tracking(target_channel_id)
        self.channel_messages[str(target_channel_id)]["message_map"][str(discord_id)] = str(fluxer_id)
        self.save_messages()

    def get_fluxer_message_id(self, target_channel_id: str, discord_id: str) -> str | None:
        if str(target_channel_id) in self.channel_messages:
            return self.channel_messages[str(target_channel_id)]["message_map"].get(str(discord_id))
        return None

    def find_message_mapping(self, discord_id: str) -> tuple[str, str] | tuple[None, None]:
        """
        Searches for a message mapping across all tracked channels.
        Returns (target_channel_id, target_message_id) or (None, None).
        """
        d_id = str(discord_id)
        for t_cid, data in self.channel_messages.items():
            # Check main message map
            if d_id in data.get("message_map", {}):
                return str(t_cid), str(data["message_map"][d_id])
            # Check threads
            for t_tid, t_data in data.get("threads", {}).items():
                if d_id in t_data.get("thread_map", {}):
                    # For thread links, the target_channel_id is technically the thread ID in some contexts,
                    # but usually for the URL it's the thread ID itself.
                    return str(t_tid), str(t_data["thread_map"][d_id])
        return None, None
        
    def update_last_message_timestamp(self, target_channel_id: str, timestamp: str):
        self._ensure_channel_tracking(target_channel_id)
        self.channel_messages[str(target_channel_id)]["last_message_timestamp"] = str(timestamp)
        self.save_messages()

    def update_last_message_id(self, target_channel_id: str, message_id: str):
        self._ensure_channel_tracking(target_channel_id)
        self.channel_messages[str(target_channel_id)]["last_message_id"] = str(message_id)
        self.save_messages()
        
    def get_last_message_id(self, target_channel_id: str) -> str | None:
        if str(target_channel_id) in self.channel_messages:
            return self.channel_messages[str(target_channel_id)].get("last_message_id")
        return None

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
        self.channel_messages.clear()
        self.save_messages()

    def set_folder(self, server_id: str, clean_name: str, base_dir: Path | str = ""):
        base = Path(base_dir) if base_dir else Path(".")
        new_folder = base / f"{clean_name}-{server_id}"
        logger.info(f"Setting active migration folder: {new_folder}")
        
        # 1. Search base_dir to see if an older folder for this server_id exists
        existing_folder: Path | None = None
        if base.exists() and base.is_dir():
            for d in base.iterdir():
                if d.is_dir() and d.name.endswith(f"-{server_id}"):
                    existing_folder = d
                    break
                    
        # 2. Rename it if it doesn't match the new desired name
        if existing_folder and existing_folder != new_folder:
            logger.info(f"Renaming existing folder {existing_folder.name} to {new_folder.name}")
            try:
                existing_folder.rename(new_folder)
            except Exception as e:
                logger.debug(f"Could not rename {existing_folder} to {new_folder}: {e}")

        new_folder.mkdir(parents=True, exist_ok=True)
        
        self.state_file = new_folder / "state-migration.json"
        self.messages_file = new_folder / "message-tracker.json"
        
        logger.debug("Re-loading data from new folder location.")
        self.load()
