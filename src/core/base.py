import logging
from typing import Dict, Any

from src.core.configuration import AppConfig
from src.core.state import MigrationState
from src.core.discord_reader import DiscordReader
from src.fluxer.writer import FluxerWriter
from src.stoat.writer import StoatWriter

logger = logging.getLogger(__name__)

class MigrationContext:
    """Holds state and connections for reading from Discord and writing to Fluxer."""
    
    def __init__(self, config: AppConfig, target_platform: str = "fluxer"):
        self.config = config
        self.target_platform = target_platform
        
        # Use the target server/community ID for state file naming so each
        # target server gets its own independent migration history.
        if target_platform == "stoat":
            server_id = config.stoat_server_id
        else:
            server_id = config.fluxer_community_id
        
        # Fallback for unconfigured platforms
        if not server_id or server_id in [
            "000000000000000000", "DISCORD_SERVER_ID", "FLUXER_COMMUNITY_ID", "STOAT_SERVER_ID"
        ]:
            server_id = "unconfigured"
        
        # Try to find an existing folder for this server_id
        import os
        from pathlib import Path
        
        state_file: str | Path = ""
        messages_file: str | Path = ""
        
        if server_id and server_id not in ["000000000000000000", "DISCORD_SERVER_ID", "FLUXER_COMMUNITY_ID", "STOAT_SERVER_ID", "unconfigured", ""]:
            # If a folder doesn't exist yet, we stick with the generic server_id names,
            # but they won't be saved until set_folder is called.
            for d in Path(".").iterdir():
                if d.is_dir() and d.name.endswith(f"-{server_id}"):
                    state_file = d / "state-migration.json"
                    messages_file = d / "message-tracker.json"
                    break

        self.state = MigrationState(
            state_file=state_file,
            messages_file=messages_file
        )
        
        self.discord_reader = DiscordReader(
            token=config.discord_bot_token,
            server_id=config.discord_server_id
        )
        
        self.fluxer_writer = FluxerWriter(
            token=config.fluxer_bot_token or "",
            community_id=config.fluxer_community_id or "",
            api_url=config.fluxer_api_url or "default"
        )
        
        self.stoat_writer = StoatWriter(
            token=config.stoat_bot_token or "",
            community_id=config.stoat_server_id or "",
            api_url=config.stoat_api_url or "default"
        )

        self.writer = self.fluxer_writer if target_platform == "fluxer" else self.stoat_writer
        
        
        self.is_running = False

    async def validate_all(self) -> Dict[str, Any]:
        """Returns connection validation status as a dictionary."""
        try:
            d_valid = await self.discord_reader.validate()
            f_valid = await self.fluxer_writer.validate()
            return {
                "discord_token": d_valid.get("token", False),
                "discord_bot_name": d_valid.get("bot_name"),
                "discord_server": d_valid.get("server", False),
                "discord_server_name": d_valid.get("server_name"),
                "discord_intents": d_valid.get("intents", {}),
                "discord_permissions": d_valid.get("permissions", {}),
                "fluxer_token": f_valid.get("token", False),
                "fluxer_bot_name": f_valid.get("bot_name"),
                "fluxer_community": f_valid.get("community", False),
                "fluxer_community_name": f_valid.get("community_name"),
                "fluxer_permissions": f_valid.get("permissions", {})
            }
        except Exception as e:
            logger.error(f"Validation failed with exception: {e}")
            return {
                "discord_token": False,
                "discord_server": False,
                "fluxer_token": False,
                "fluxer_community": False
            }

    async def start_connections(self):
        await self.discord_reader.start()
        await self.writer.start()

    async def start_target_only(self):
        """Starts only the target platform writer (used for Danger Zone operations that don't need Discord)."""
        await self.writer.start()

    async def close_connections(self):
        try:
            await self.discord_reader.close()
        except Exception as e:
            logger.debug(f"Error closing Discord reader: {e}")
        try:
            await self.writer.close()
        except Exception as e:
            logger.debug(f"Error closing target writer: {e}")

    async def close_target_only(self):
        """Closes only the target platform writer. Pair with start_target_only()."""
        try:
            await self.writer.close()
        except Exception as e:
            logger.debug(f"Error closing target writer: {e}")


    def stop(self):
        self.is_running = False
