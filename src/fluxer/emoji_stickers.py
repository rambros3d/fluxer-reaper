import asyncio
import logging
from typing import Callable, Awaitable, List

from src.core.base import MigrationContext

logger = logging.getLogger(__name__)

async def sync_assets_state(context: MigrationContext):
    """
    Scans Fluxer for emojis and stickers matching Discord names and updates state file mappings.
    """
    logger.info("Synchronizing asset mappings (emojis/stickers) with Fluxer...")
    discord_emojis = await context.discord_reader.get_emojis()
    discord_stickers = await context.discord_reader.get_stickers()
    
    fluxer_emojis = await context.fluxer_writer.client.get_guild_emojis(context.config.fluxer_community_id)
    fluxer_stickers = await context.fluxer_writer.client.get_guild_stickers(context.config.fluxer_community_id)
    
    # Build name -> id maps and ID sets for Fluxer for fast lookup
    fluxer_emoji_map = {e.get("name"): str(e.get("id")) for e in fluxer_emojis if e.get("name")}
    fluxer_sticker_map = {s.get("name"): str(s.get("id")) for s in fluxer_stickers if s.get("name")}
    fluxer_emoji_ids = {str(e.get("id")) for e in fluxer_emojis}
    fluxer_sticker_ids = {str(s.get("id")) for s in fluxer_stickers}
    
    updates = 0
    removals = 0
    
    # 1. Verify and Sync Emojis
    for emoji in discord_emojis:
        discord_id = str(emoji.id)
        fluxer_id = context.state.get_fluxer_emoji_id(discord_id)
        
        if fluxer_id:
            if fluxer_id not in fluxer_emoji_ids:
                context.state.remove_emoji_mapping(discord_id)
                removals += 1
        elif emoji.name in fluxer_emoji_map:
            context.state.set_emoji_mapping(discord_id, fluxer_emoji_map[emoji.name])
            updates += 1
                
    # 2. Verify and Sync Stickers
    for sticker in discord_stickers:
        discord_id = str(sticker.id)
        fluxer_id = context.state.get_fluxer_sticker_id(discord_id)
        
        if fluxer_id:
            if fluxer_id not in fluxer_sticker_ids:
                context.state.remove_sticker_mapping(discord_id)
                removals += 1
        elif sticker.name in fluxer_sticker_map:
            context.state.set_sticker_mapping(discord_id, fluxer_sticker_map[sticker.name])
            updates += 1
                
    if updates > 0 or removals > 0:
        logger.info(f"Asset sync: {updates} mapped, {removals} stale mappings removed")


async def migrate_emojis(context: MigrationContext, progress_callback: Callable[[str, str, int, int], Awaitable[None]] | None = None, types_to_include: List[str] = ["Emoji", "Sticker"], force: bool = False) -> dict[str, dict[str, str]]:
    """Copies custom emojis and stickers.
    
    Args:
        force: If True, skip state cache and re-copy even if already migrated.
        
    Returns:
        A dictionary containing dicts of cloned asset names to their new IDs by type: {"Emoji": {"name": "id", ...}, "Sticker": {"name": "id", ...}}
    """
    objs = []
    if "Emoji" in types_to_include:
        emojis = await context.discord_reader.get_emojis()
        objs.extend([(e, "Emoji") for e in emojis])
    if "Sticker" in types_to_include:
        stickers = await context.discord_reader.get_stickers()
        objs.extend([(s, "Sticker") for s in stickers])
        
    if not force:
        objs = [(obj, obj_type) for obj, obj_type in objs if not (
            context.state.get_fluxer_emoji_id(str(obj.id)) if obj_type == "Emoji" else context.state.get_fluxer_sticker_id(str(obj.id))
        )]

    total = len(objs)
    cloned_assets: dict[str, dict[str, str]] = {"Emoji": {}, "Sticker": {}}
    
    logger.info(f"Migrating {total} assets to Fluxer (Types: {', '.join(types_to_include)})...")

    for idx, (obj, obj_type) in enumerate(objs):
        if not context.is_running:
            logger.warning("Asset migration interrupted.")
            break
            
        try:
            if obj_type == "Emoji":
                logger.debug(f"Migrating emoji: {obj.name}")
                img_data = await context.discord_reader.download_emoji(obj)
                fluxer_id = await context.fluxer_writer.create_emoji(
                    name=obj.name,
                    image_bytes=img_data
                )
                if fluxer_id:
                    context.state.set_emoji_mapping(str(obj.id), fluxer_id)
                    cloned_assets["Emoji"][obj.name] = fluxer_id
            else:
                logger.debug(f"Migrating sticker: {obj.name}")
                img_data = await context.discord_reader.download_sticker(obj)
                fluxer_id = await context.fluxer_writer.create_sticker(
                    name=obj.name,
                    image_bytes=img_data
                )
                if fluxer_id:
                    context.state.set_sticker_mapping(str(obj.id), fluxer_id)
                    cloned_assets["Sticker"][obj.name] = fluxer_id
        except Exception as e:
            logger.error(f"Error downloading/uploading {obj_type.lower()} {obj.name}: {e}")
        
        if progress_callback: await progress_callback(obj.name, obj_type, idx + 1, total)
        
    return cloned_assets
