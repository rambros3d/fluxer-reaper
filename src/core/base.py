import logging
from typing import Dict, Any

from src.core.configuration import AppConfig
from src.core.state import MigrationState
from src.core.discord_reader import DiscordReader
from src.core.fluxer_writer import FluxerWriter

logger = logging.getLogger(__name__)

class MigrationContext:
    """Holds state and connections for reading from Discord and writing to Fluxer."""
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.state = MigrationState()
        
        self.discord_reader = DiscordReader(
            token=config.discord_bot_token,
            server_id=config.discord_server_id
        )
        
        self.fluxer_writer = FluxerWriter(
            token=config.fluxer_bot_token,
            community_id=config.fluxer_community_id
        )
        
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
        await self.fluxer_writer.start()

    async def start_fluxer_only(self):
        """Starts only the Fluxer writer (used for Danger Zone operations that don't need Discord)."""
        await self.fluxer_writer.start()

    async def close_connections(self):
        try:
            await self.discord_reader.close()
        except Exception as e:
            logger.debug(f"Error closing Discord reader: {e}")
        try:
            await self.fluxer_writer.close()
        except Exception as e:
            logger.debug(f"Error closing Fluxer writer: {e}")

    async def close_fluxer_only(self):
        """Closes only the Fluxer writer. Pair with start_fluxer_only()."""
        try:
            await self.fluxer_writer.close()
        except Exception as e:
            logger.debug(f"Error closing Fluxer writer: {e}")


    def stop(self):
        self.is_running = False
