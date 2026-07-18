import asyncio
import logging
from pathlib import Path
from typing import Dict, Any

from src.core.configuration import AppConfig
from src.core.state import MigrationState
from src.core.discord_reader import DiscordReader
from src.fluxer.reader import FluxerReader        # new import
from src.fluxer.writer import FluxerWriter
from src.stoat.writer import StoatWriter

logger = logging.getLogger(__name__)

class MigrationContext:
    """Holds state and connections for reading from a source platform and writing to a target platform."""
    
    def __init__(self, config: AppConfig, target_platform: str | None = None, source_mode: str = "live", base_dir: str = ""):
        self.config = config
        self.source_mode = source_mode
        self.base_dir = base_dir
        self.target_platform = target_platform or config.target_platform or "fluxer"
        self.state = MigrationState()
        
        # Apply config-based log level
        if hasattr(self.config, "log_level") and self.config.log_level:
            level = getattr(logging, self.config.log_level.upper(), logging.INFO)
            logging.getLogger().setLevel(level)
            logger.info(f"Log level updated to {self.config.log_level.upper()}")
        
        # ── Select the appropriate source reader ─────────────────────────────
        if source_mode == "backup":
            from src.core.backup_reader import BackupReader
            backup_path = self._find_backup_path(config.source_server_id, base_dir)
            self.source_reader = BackupReader(backup_path)
            logger.info(f"Source mode: BACKUP — reading from {backup_path}")
        else:
            # Determine source platform from config (default to discord if not set)
            source_platform = getattr(config, "source_platform", None)
            if not source_platform:
                logger.warning("POTENTIAL ERROR: source_platform not set in config; defaulting to 'discord'")
                source_platform = "discord"

            if source_platform == "discord":
                self.source_reader = DiscordReader(
                    token=config.source_bot_token,
                    server_id=config.source_server_id
                )
                logger.info("Source mode: LIVE — using Discord API")
            else:  # fluxer
                api_url = getattr(config, "source_api_url", None) or "default"
                self.source_reader = FluxerReader(
                    token=config.source_bot_token,
                    server_id=config.source_server_id,
                    api_url=api_url
                )
                logger.info("Source mode: LIVE — using Fluxer API")
        
        # ── Build the target writer ──────────────────────────────────────────
        if self.target_platform == "stoat":
            token = config.stoat_bot_token
            community_id = config.stoat_server_id
            api_url = config.stoat_api_url or "default"
            self.writer = StoatWriter(token=token, community_id=community_id, api_url=api_url)
            # Keep aliases for backward compatibility with shuttle_ops
            self.stoat_writer = self.writer
            self.fluxer_writer = FluxerWriter(token="", community_id="", api_url="default")
        else:  # fluxer
            token = config.fluxer_bot_token or ""
            community_id = config.fluxer_server_id or ""
            api_url = config.fluxer_api_url or "default"
            self.writer = FluxerWriter(token=token, community_id=community_id, api_url=api_url)
            self.fluxer_writer = self.writer
            self.stoat_writer = StoatWriter(token="", community_id="", api_url="default")
        
        self.is_running = False

    def _find_backup_path(self, server_id: str | int | None, base_dir_str: str) -> Path:
        """Searches workspace for a SOURCE_BACKUP-{server_id} directory. Returns the path (does not create)."""
        if not server_id:
            return Path(base_dir_str or ".") / "SOURCE_BACKUP-UNKNOWN"

        sid_str = str(server_id).strip()
        base_dir = Path(base_dir_str) if base_dir_str else Path(".")
        
        # Search inside the workspace
        if base_dir.exists() and base_dir.is_dir():
            for d in base_dir.iterdir():
                if d.is_dir():
                    dname = d.name.upper()
                    if "SOURCE_BACKUP" in dname and dname.endswith(f"-{sid_str}"):
                        logger.info(f"Found backup directory in workspace: {d}")
                        return d
        
        # Fallback to expected location
        new_path = base_dir / f"SOURCE_BACKUP-{sid_str}"
        logger.info(f"Using lazy backup path (not yet existing): {new_path}")
        return new_path

    # async def validate_all(self) -> Dict[str, Any]:
    #     """
    #     Returns connection validation status.
    #     Keys: source_* and target_*.
    #     """
    #     try:
    #         # Validate source
    #         s_valid = await self.source_reader.validate()
    #         # Validate target
    #         t_valid = await self.writer.validate()
            
    #         results = {
    #             "source_token": s_valid.get("token", False),
    #             "source_bot_name": s_valid.get("bot_name"),
    #             "source_server": s_valid.get("server", False),
    #             "source_server_name": s_valid.get("server_name"),
    #             "discord_intents": s_valid.get("intents", {}),       # only meaningful for Discord
    #             "source_permissions": s_valid.get("permissions", {}),
    #             "target_token": t_valid.get("token", False),
    #             "target_bot_name": t_valid.get("bot_name"),
    #             "target_community": t_valid.get("community", False),
    #             "target_community_name": t_valid.get("community_name"),
    #             "target_permissions": t_valid.get("permissions", {})
    #         }
            
    #         # Initialise the state database if target is valid
    #         if results["target_community"]:
    #             tid = self.config.fluxer_server_id if self.target_platform == "fluxer" else self.config.stoat_server_id
    #             # Prefer source server name for the DB file
    #             db_name = results.get("source_server_name")
    #             if not db_name or db_name in ("Not Found", "Unknown"):
    #                 db_name = results.get("target_community_name") or "Unknown"
    #             self.ensure_state_initialized(str(tid or ""), db_name)
                
    #         return results
    #     except Exception as e:
    #         logger.error(f"Validation failed with exception: {e}")
    #         return {
    #             "source_token": False,
    #             "source_server": False,
    #             "target_token": False,
    #             "target_community": False
    #         }

    async def validate_all(self) -> Dict[str, Any]:
        """Returns connection validation status for both source and target."""
        try:
            # Run both validations concurrently
            s_task = asyncio.create_task(self.source_reader.validate())
            t_task = asyncio.create_task(self.writer.validate())
            s_valid, t_valid = await asyncio.gather(s_task, t_task, return_exceptions=True)

            # Unpack exceptions (if any)
            if isinstance(s_valid, Exception):
                s_valid = {
                    "token": False,
                    "server": False,
                    "bot_name": None,
                    "server_name": None,
                    "error_reason": f"Exception: {s_valid}",
                    "intents": {},
                    "permissions": {}
                }
            if isinstance(t_valid, Exception):
                t_valid = {
                    "token": False,
                    "community": False,
                    "bot_name": None,
                    "community_name": None,
                    "error_reason": f"Exception: {t_valid}",
                    "permissions": {}
                }

            results = {
                "source_token": s_valid.get("token", False),
                "source_bot_name": s_valid.get("bot_name"),
                "source_server": s_valid.get("server", False),
                "source_server_name": s_valid.get("server_name"),
                "source_intents": s_valid.get("intents", {}),
                "source_permissions": s_valid.get("permissions", {}),
                "target_token": t_valid.get("token", False),
                "target_bot_name": t_valid.get("bot_name"),
                "target_community": t_valid.get("community", False),
                "target_community_name": t_valid.get("community_name"),
                "target_permissions": t_valid.get("permissions", {})
            }

            # Initialise state if target is valid
            if results["target_community"]:
                tid = self.config.fluxer_server_id if self.target_platform == "fluxer" else self.config.stoat_server_id
                db_name = results.get("source_server_name")
                if not db_name or db_name in ("Not Found", "Unknown"):
                    db_name = results.get("target_community_name") or "Unknown"
                self.ensure_state_initialized(str(tid or ""), db_name)

            return results

        except Exception as e:
            logger.error(f"Validation failed with exception: {e}")
            return {
                "source_token": False,
                "source_server": False,
                "target_token": False,
                "target_community": False
            }

    def ensure_state_initialized(self, community_id: str, community_name: str):
        """Initialises the MigrationState database with the correct folder naming."""
        if not community_id or not community_name:
            return
            
        import re
        import json
        
        # Override with actual source server name if available (live or backup)
        try:
            # Try to get metadata from the source reader
            if hasattr(self.source_reader, "get_server_metadata"):
                meta = self.source_reader.get_server_metadata()
                if meta and meta.get("name"):
                    community_name = meta["name"]
            # Fallback for backup reader
            elif hasattr(self.source_reader, "backup_dir") and self.source_reader.backup_dir:
                meta_file = self.source_reader.backup_dir / "metadata.json"
                if meta_file.exists():
                    data = json.loads(meta_file.read_text())
                    community_name = data.get("name", community_name)
        except Exception:
            pass

        clean_name = re.sub(r'[^\w\s-]', '', community_name).strip()
        clean_name = re.sub(r'[-\s]+', '_', clean_name)
        
        base_dir = getattr(self, "base_dir", "")
        self.state.set_folder(community_id, clean_name, self.target_platform, base_dir=base_dir)

    async def start_connections(self):
        await self.source_reader.start()
        await self.writer.start()

    async def start_target_only(self):
        """Starts only the target platform writer (used for Danger Zone operations)."""
        await self.writer.start()

    async def close_connections(self):
        try:
            await self.source_reader.close()
        except Exception as e:
            logger.debug(f"Error closing source reader: {e}")
        try:
            await self.writer.close()
        except Exception as e:
            logger.debug(f"Error closing target writer: {e}")

    async def close_target_only(self):
        """Closes only the target platform writer."""
        try:
            await self.writer.close()
        except Exception as e:
            logger.debug(f"Error closing target writer: {e}")

    def stop(self):
        self.is_running = False