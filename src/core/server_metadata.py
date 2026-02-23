import logging
from typing import Callable, Awaitable, List

from src.core.base import MigrationContext

logger = logging.getLogger(__name__)

async def sync_server_metadata(context: MigrationContext, progress_callback: Callable[[str, str], Awaitable[None]], components: List[str] = ["name", "icon", "banner"]):
    """Syncs the server name, logo and banner."""
    metadata = await context.discord_reader.get_server_metadata()
    
    # 1. Sync Name
    if "name" in components:
        try:
            name = metadata.get("name")
            await context.fluxer_writer.update_guild_metadata(name=name)
            await progress_callback("Server Name", "DONE")
        except Exception:
            await progress_callback("Server Name", "ERROR")

    # 2. Sync Icon
    if "icon" in components:
        try:
            icon_bytes = None
            if context.discord_reader.guild and context.discord_reader.guild.icon:
                icon_bytes = await context.discord_reader.download_asset(context.discord_reader.guild.icon)
            
            if icon_bytes:
                await context.fluxer_writer.update_guild_metadata(icon=icon_bytes)
                await progress_callback("Server Icon", "DONE")
            else:
                await progress_callback("Server Icon", "SKIP")
        except Exception:
            await progress_callback("Server Icon", "ERROR")
        
    # 3. Sync Banner
    if "banner" in components:
        try:
            banner_bytes = None
            if context.discord_reader.guild and context.discord_reader.guild.banner:
                banner_bytes = await context.discord_reader.download_asset(context.discord_reader.guild.banner)
            
            if banner_bytes:
                await context.fluxer_writer.update_guild_metadata(banner=banner_bytes)
                await progress_callback("Server Banner", "DONE")
            else:
                await progress_callback("Server Banner", "SKIP")
        except Exception:
            await progress_callback("Server Banner", "ERROR")
