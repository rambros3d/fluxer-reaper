import asyncio
import logging
from typing import Callable, Awaitable, List

from src.core.base import MigrationContext

logger = logging.getLogger(__name__)

async def sync_assets_state(context: MigrationContext):
    """
    Scans Stoat for emojis matching Discord names and updates state file mappings.
    """
    logger.info("Synchronizing asset mappings (emojis) with Stoat...")
    discord_emojis = await context.source_reader.get_emojis()
    # Stickers not supported on Stoat based on current library investigation
    
    # StoatWriter should have a way to fetch emojis
    # For now we use the client directly if it starts
    if not hasattr(context.writer, 'client'):
         await context.writer.start()
         
    server = await context.writer.client.fetch_server(context.writer.community_id)
    stoat_emojis = await server.fetch_emojis()
    
    # Build name -> id maps and ID sets for Stoat for fast lookup
    stoat_emoji_map = {e.name: str(e.id) for e in stoat_emojis if e.name}
    stoat_emoji_ids = {str(e.id) for e in stoat_emojis}
    
    updates = 0
    removals = 0
    
    # 1. Verify and Sync Emojis
    for emoji in discord_emojis:
        discord_id = str(emoji.id)
        # Using target mapping in state
        stoat_id = context.state.get_target_emoji_id(discord_id)
        
        if stoat_id:
            if stoat_id not in stoat_emoji_ids:
                context.state.remove_emoji_mapping(discord_id)
                removals += 1
        elif emoji.name in stoat_emoji_map:
            context.state.set_target_emoji_mapping(discord_id, stoat_emoji_map[emoji.name])
            updates += 1
                
    if updates > 0 or removals > 0:
        logger.info(f"Stoat Asset sync: {updates} mapped, {removals} stale mappings removed")


async def migrate_emojis(context: MigrationContext, progress_callback: Callable[[str, str, int, int], Awaitable[None]] | None = None, types_to_include: List[str] = ["Emoji", "Sticker"], force: bool = False) -> dict[str, dict[str, str]]:
    """Copies custom emojis.
    
    Args:
        force: If True, skip state cache and re-copy even if already migrated.
        
    Returns:
        A dictionary containing dicts of cloned asset names to their new IDs by type: {"Emoji": {"name": "id", ...}, "Sticker": {}}
    """
    objs = []
    if "Emoji" in types_to_include:
        emojis = await context.source_reader.get_emojis()
        objs.extend([(e, "Emoji") for e in emojis])
    
    # Stickers are skipped for Stoat
    
    if not force:
        objs = [(obj, obj_type) for obj, obj_type in objs if not (
            context.state.get_target_emoji_id(str(obj.id)) if obj_type == "Emoji" else None
        )]

    total = len(objs)
    cloned_assets: dict[str, dict[str, str]] = {"Emoji": {}, "Sticker": {}}
    
    if total == 0:
        return cloned_assets

    logger.info(f"Migrating {total} assets to Stoat (Types: {', '.join(types_to_include)})...")

    for idx, (obj, obj_type) in enumerate(objs):
        if not context.is_running:
            logger.warning("Stoat asset migration interrupted.")
            break
            
        try:
            if obj_type == "Emoji":
                logger.debug(f"Migrating emoji to Stoat: {obj.name}")
                img_data = await context.source_reader.download_emoji(obj)
                stoat_id = await context.writer.create_emoji(
                    name=obj.name,
                    image_bytes=img_data
                )
                if stoat_id:
                    context.state.set_target_emoji_mapping(str(obj.id), stoat_id)
                    cloned_assets["Emoji"][obj.name] = stoat_id
            else:
                # Sticker not supported
                pass
        except Exception as e:
            logger.error(f"Error downloading/uploading {obj_type.lower()} {obj.name}: {e}")
        
        if progress_callback: await progress_callback(obj.name, obj_type, idx + 1, total)
        
    return cloned_assets
