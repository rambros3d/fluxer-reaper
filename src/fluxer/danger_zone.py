import logging
from typing import Callable, Awaitable

from src.core.base import MigrationContext

logger = logging.getLogger(__name__)

async def danger_remove_logo_and_banner(context: MigrationContext) -> dict:
    """Removes the target community's logo and banner image. Returns per-field status."""
    return await context.writer.remove_community_logo_and_banner()

async def danger_delete_all_channels(context: MigrationContext, progress_callback=None) -> int:
    """Deletes every channel and category in the target community."""
    count = await context.writer.delete_all_channels(progress_callback=progress_callback)
    context.state.clear_channel_mappings()
    context.state.clear_message_history()
    return count

async def danger_reset_channel_permissions(context: MigrationContext, progress_callback=None) -> int:
    """Resets all permission overwrites on every channel and category in the target community."""
    return await context.writer.reset_channel_permissions(progress_callback=progress_callback)

async def danger_delete_all_roles(context: MigrationContext, progress_callback=None) -> int:
    """Deletes all deletable roles in the target community."""
    count = await context.writer.delete_all_roles(progress_callback=progress_callback)
    context.state.clear_role_mappings()
    return count

async def danger_delete_all_emojis_and_stickers(context: MigrationContext, progress_callback=None) -> dict:
    """Deletes all custom emojis and stickers from the target community. Returns {"emojis": int, "stickers": int}."""
    counts = await context.writer.delete_all_emojis_and_stickers(progress_callback=progress_callback)
    context.state.clear_asset_mappings()
    return counts
