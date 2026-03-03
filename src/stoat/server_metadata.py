import logging
from typing import Callable, Awaitable, List

from src.core.base import MigrationContext

logger = logging.getLogger(__name__)

async def sync_server_metadata(context: MigrationContext, progress_callback: Callable[[str, str], Awaitable[None]], components: List[str] = ["name", "icon", "banner"]) -> dict:
    """Syncs the server name, logo and banner. Returns a dict of cloned attributes."""
    metadata = await context.discord_reader.get_server_metadata()
    cloned_data = {}
    
    # 1. Sync Name
    if "name" in components:
        try:
            name = metadata.get("name")
            logger.info(f"Syncing server name to Stoat: {name}")
            await context.writer.update_guild_metadata(name=name)
            cloned_data["name"] = name
            await progress_callback("Server Name", "DONE")
        except Exception as e:
            logger.error(f"Failed to sync server name to Stoat: {e}")
            await progress_callback("Server Name", "ERROR")

    # 2. Sync Icon
    if "icon" in components:
        try:
            icon_bytes = None
            if context.discord_reader.guild and context.discord_reader.guild.icon:
                icon_bytes = await context.discord_reader.download_asset(context.discord_reader.guild.icon)
            
            if icon_bytes:
                logger.info(f"Uploading server icon to Stoat ({len(icon_bytes)} bytes)...")
                await context.writer.update_guild_metadata(icon=icon_bytes)
                cloned_data["icon"] = icon_bytes
                await progress_callback("Server Icon", "DONE")
            else:
                logger.debug("No server icon found to sync to Stoat.")
                await progress_callback("Server Icon", "SKIP")
        except Exception as e:
            logger.error(f"Failed to sync server icon to Stoat: {e}")
            await progress_callback("Server Icon", "ERROR")
        
    # 3. Sync Banner
    if "banner" in components:
        try:
            banner_bytes = None
            if context.discord_reader.guild and context.discord_reader.guild.banner:
                banner_bytes = await context.discord_reader.download_asset(context.discord_reader.guild.banner)
            
            if banner_bytes:
                logger.info(f"Uploading server banner to Stoat ({len(banner_bytes)} bytes)...")
                await context.writer.update_guild_metadata(banner=banner_bytes)
                cloned_data["banner"] = banner_bytes
                await progress_callback("Server Banner", "DONE")
            else:
                logger.debug("No server banner found to sync to Stoat.")
                await progress_callback("Server Banner", "SKIP")
        except Exception as e:
            logger.error(f"Failed to sync server banner to Stoat: {e}")
            await progress_callback("Server Banner", "ERROR")
            
    return cloned_data
