import logging
from pathlib import Path
from typing import Dict, Any

from src.core.configuration import AppConfig
from src.core.state import MigrationState
from src.core.discord_reader import DiscordReader
from src.fluxer.writer import FluxerWriter
from src.stoat.writer import StoatWriter

logger = logging.getLogger(__name__)

class MigrationContext:
    """Holds state and connections for reading from Discord and writing to the target platform."""
    
    def __init__(self, config: AppConfig, target_platform: str | None = None, source_mode: str = "live", base_dir: str = ""):
        self.config = config
        self.source_mode = source_mode
        # If caller didn't specify, fall back to config value
        self.target_platform = target_platform or config.target_platform or "fluxer"
        self.state = MigrationState()
        
        # Select the appropriate source reader
        if source_mode == "backup":
            from src.core.backup_reader import BackupReader
            backup_path = self._find_backup_path(config.discord_server_id, base_dir)
            self.discord_reader = BackupReader(backup_path)
            logger.info(f"Source mode: BACKUP — reading from {backup_path}")
        else:
            self.discord_reader = DiscordReader(
                token=config.discord_bot_token,
                server_id=config.discord_server_id
            )
            logger.info("Source mode: LIVE — using Discord API")
        
        # Build the writer for the active target platform only
        token = config.target_bot_token or ""
        community_id = config.target_server_id or ""
        api_url = config.target_api_url or "default"

        if self.target_platform == "stoat":
            self.writer = StoatWriter(token=token, community_id=community_id, api_url=api_url)
            self.stoat_writer = self.writer
            self.fluxer_writer = FluxerWriter(token="", community_id="", api_url="default")
        else:
            self.writer = FluxerWriter(token=token, community_id=community_id, api_url=api_url)
            self.fluxer_writer = self.writer
            self.stoat_writer = StoatWriter(token="", community_id="", api_url="default")
        
        self.is_running = False

    def _find_backup_path(self, server_id: str, base_dir_str: str) -> Path:
        """Searches workspace for a DISCORD_BACKUP-{server_id} directory. Creates it if missing."""
        base_dir = Path(base_dir_str) if base_dir_str else Path(".")
        
        # 1. Search inside the specific workspace directory first
        if base_dir.exists() and base_dir.is_dir():
            for d in base_dir.iterdir():
                if d.is_dir() and d.name.endswith(f"-{server_id}") and "DISCORD_BACKUP" in d.name:
                    logger.info(f"Found backup directory: {d}")
                    return d
                    
        # 2. Fallback to global search if it wasn't found in the workspace
        for d in Path(".").rglob(f"DISCORD_BACKUP-{server_id}"):
            if d.is_dir():
                logger.info(f"Found backup directory globally: {d}")
                return d
        
        # If not found anywhere, create it inside the workspace
        base_dir.mkdir(exist_ok=True)
        new_path = base_dir / f"DISCORD_BACKUP-{server_id}"
        logger.info(f"No existing backup directory found for {server_id}. Creating new one: {new_path}")
        new_path.mkdir(exist_ok=True)
        return new_path

    async def validate_all(self) -> Dict[str, Any]:
        """Returns connection validation status as a dictionary."""
        try:
            d_valid = await self.discord_reader.validate()
            t_valid = await self.writer.validate()
            return {
                "discord_token": d_valid.get("token", False),
                "discord_bot_name": d_valid.get("bot_name"),
                "discord_server": d_valid.get("server", False),
                "discord_server_name": d_valid.get("server_name"),
                "discord_intents": d_valid.get("intents", {}),
                "discord_permissions": d_valid.get("permissions", {}),
                "target_token": t_valid.get("token", False),
                "target_bot_name": t_valid.get("bot_name"),
                "target_community": t_valid.get("community", False),
                "target_community_name": t_valid.get("community_name"),
                "target_permissions": t_valid.get("permissions", {})
            }
        except Exception as e:
            logger.error(f"Validation failed with exception: {e}")
            return {
                "discord_token": False,
                "discord_server": False,
                "target_token": False,
                "target_community": False
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
