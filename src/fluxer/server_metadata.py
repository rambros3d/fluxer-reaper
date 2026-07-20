import logging
from typing import Callable, Awaitable, List

from src.core.base import MigrationContext

logger = logging.getLogger(__name__)

async def sync_server_metadata(context: MigrationContext, progress_callback: Callable[[str, str], Awaitable[None]], components: List[str] = ["name", "icon", "banner"]) -> dict:
    """Syncs server name, icon and banner in a source‑agnostic way."""
    metadata = await context.source_reader.get_server_metadata()
    cloned_data = {}

    # 1. Name
    if "name" in components:
        try:
            name = metadata.get("name")
            if name:
                logger.info(f"Syncing server name: {name}")
                await context.writer.update_guild_metadata(name=name)
                cloned_data["name"] = name
                await progress_callback("Server Name", "DONE")
            else:
                await progress_callback("Server Name", "SKIP")
        except Exception as e:
            logger.error(f"Failed to sync server name: {e}")
            await progress_callback("Server Name", "ERROR")

    # 2. Icon
    if "icon" in components:
        try:
            icon_url = metadata.get("icon_url")
            if icon_url:
                logger.info(f"Downloading server icon from {icon_url}...")
                icon_bytes = await context.source_reader.download_asset(icon_url)
                if icon_bytes:
                    await context.writer.update_guild_metadata(icon=icon_bytes)
                    cloned_data["icon"] = icon_bytes
                    await progress_callback("Server Icon", "DONE")
                else:
                    await progress_callback("Server Icon", "SKIP")
            else:
                logger.info("No server icon found to sync.")
                await progress_callback("Server Icon", "SKIP")
        except Exception as e:
            logger.error(f"Failed to sync server icon: {e}")
            await progress_callback("Server Icon", "ERROR")

    # 3. Banner
    if "banner" in components:
        try:
            banner_url = metadata.get("banner_url")
            if banner_url:
                logger.info(f"Downloading server banner from {banner_url}...")
                banner_bytes = await context.source_reader.download_asset(banner_url)
                if banner_bytes:
                    await context.writer.update_guild_metadata(banner=banner_bytes)
                    cloned_data["banner"] = banner_bytes
                    await progress_callback("Server Banner", "DONE")
                else:
                    await progress_callback("Server Banner", "SKIP")
            else:
                logger.info("No server banner found to sync.")
                await progress_callback("Server Banner", "SKIP")
        except Exception as e:
            logger.error(f"Failed to sync server banner: {e}")
            await progress_callback("Server Banner", "ERROR")

    return cloned_data