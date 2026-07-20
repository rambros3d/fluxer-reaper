import asyncio
import logging
from typing import Callable, Awaitable, List

from src.core.base import MigrationContext
from fluxer.errors import Unauthorized, Forbidden

### Changes from Discord-specific to source-agnostic:
#Before	                                            After
# context.fluxer_writer.client.                     await context.fluxer_writer.get_emojis() – uses writer's own community_id.
    # get_guild_emojis(context.config.fluxer_server_id)
#context.config.fluxer_server_id (hardcoded)	    Writer's community_id is used internally.
#Direct client access	                            Uses writer methods (better encapsulation).
#No helper method to fetch assets	                Added get_emojis() and get_stickers() to FluxerWriter.



logger = logging.getLogger(__name__)

async def sync_assets_state(context: MigrationContext):
    """
    Scans Fluxer for emojis and stickers matching Discord names and updates state file mappings.
    Gracefully handles 401 errors (treat as no assets).
    """
    logger.info("Synchronizing asset mappings (emojis/stickers) with Fluxer...")
    source_emojis = await context.source_reader.get_emojis()
    source_stickers = await context.source_reader.get_stickers()

    fluxer_emojis = []
    fluxer_stickers = []

    # Use the writer’s own methods (they already know the community_id)
    try:
        fluxer_emojis = await asyncio.wait_for(
            context.fluxer_writer.get_emojis(), timeout=10.0
        )
    except (Unauthorized, Forbidden, asyncio.TimeoutError, Exception) as e:
        logger.warning(f"Could not fetch emojis from Fluxer: {e}. Assuming none exist.")

    try:
        fluxer_stickers = await asyncio.wait_for(
            context.fluxer_writer.get_stickers(), timeout=10.0
        )
    except (Unauthorized, Forbidden, asyncio.TimeoutError, Exception) as e:
        logger.warning(f"Could not fetch stickers from Fluxer: {e}. Assuming none exist.")
    
    fluxer_emoji_map = {e.get("name"): e.get("id") for e in fluxer_emojis if e.get("name")}
    fluxer_sticker_map = {s.get("name"): s.get("id") for s in fluxer_stickers if s.get("name")}
    fluxer_emoji_ids = {e.get("id") for e in fluxer_emojis}
    fluxer_sticker_ids = {s.get("id") for s in fluxer_stickers}
    
    updates = 0
    removals = 0
    
    # Emojis
    for emoji in source_emojis:
        source_id = str(emoji.id)
        fluxer_id = context.state.get_fluxer_emoji_id(source_id)
        if fluxer_id:
            if fluxer_id not in fluxer_emoji_ids:
                context.state.remove_emoji_mapping(source_id)
                removals += 1
        elif emoji.name in fluxer_emoji_map:
            context.state.set_emoji_mapping(source_id, fluxer_emoji_map[emoji.name])
            updates += 1
                
    # Stickers
    for sticker in source_stickers:
        source_id = str(sticker.id)
        fluxer_id = context.state.get_fluxer_sticker_id(source_id)
        if fluxer_id:
            if fluxer_id not in fluxer_sticker_ids:
                context.state.remove_sticker_mapping(source_id)
                removals += 1
        elif sticker.name in fluxer_sticker_map:
            context.state.set_sticker_mapping(source_id, fluxer_sticker_map[sticker.name])
            updates += 1
                
    if updates > 0 or removals > 0:
        logger.info(f"Asset sync: {updates} mapped, {removals} stale mappings removed")


async def migrate_emojis(
        context: MigrationContext, 
        progress_callback: Callable[[str, str, int, int], Awaitable[None]] | None = None, 
        types_to_include: List[str] = ["Emoji", "Sticker"], force: bool = False
        ) -> dict[str, dict[str, str]]:
    """Copies custom emojis and stickers.
    
    Args:
        force: If True, skip state cache and re-copy even if already migrated.
        
    Returns:
        A dictionary containing dicts of cloned asset names to their new IDs by type: {"Emoji": {"name": "id", ...}, "Sticker": {"name": "id", ...}}
    """
    objs = []
    if "Emoji" in types_to_include:
        emojis = await context.source_reader.get_emojis()
        objs.extend([(e, "Emoji") for e in emojis])
    if "Sticker" in types_to_include:
        stickers = await context.source_reader.get_stickers()
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
                img_data = await context.source_reader.download_emoji(obj)
                fluxer_id = await context.fluxer_writer.create_emoji(
                    name=obj.name,
                    image_bytes=img_data
                )
                if fluxer_id:
                    context.state.set_emoji_mapping(str(obj.id), fluxer_id)
                    cloned_assets["Emoji"][obj.name] = fluxer_id
            else:
                logger.debug(f"Migrating sticker: {obj.name}")
                img_data = await context.source_reader.download_sticker(obj)
                fluxer_id = await context.fluxer_writer.create_sticker(
                    name=obj.name,
                    image_bytes=img_data
                )
                if fluxer_id:
                    context.state.set_sticker_mapping(str(obj.id), fluxer_id)
                    cloned_assets["Sticker"][obj.name] = fluxer_id
        except Exception as e:
            logger.error(f"Error downloading/uploading {obj_type.lower()} {obj.name}: {e}")
            # Optionally log full traceback for debugging
            # logger.exception(e)
        
        if progress_callback:
            await progress_callback(obj.name, obj_type, idx + 1, total)
        
    return cloned_assets