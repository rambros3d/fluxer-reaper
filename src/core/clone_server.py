import asyncio
import logging
from typing import Callable, Awaitable

from src.core.base import MigrationContext

logger = logging.getLogger(__name__)

async def sync_channel_state(context: MigrationContext):
    """
    Scans Fluxer for channels matching Discord names and updates state.json mappings.
    This prevents duplicate creation when the state.json is empty but channels exist in Fluxer.
    """
    categories = await context.discord_reader.get_categories()
    channels = await context.discord_reader.get_channels()
    fluxer_channels = await context.fluxer_writer.get_channels()
    
    # Build name -> id map and ID set for Fluxer for fast lookup
    fluxer_name_map = {c.get("name"): str(c.get("id")) for c in fluxer_channels if c.get("name")}
    fluxer_id_set = {str(c.get("id")) for c in fluxer_channels}
    
    updates = 0
    removals = 0
    
    # 1. Verify and Sync Categories
    for cat in categories:
        discord_id = str(cat.id)
        fluxer_id = context.state.get_fluxer_category_id(discord_id)
        
        if fluxer_id:
            if fluxer_id not in fluxer_id_set:
                context.state.remove_category_mapping(discord_id)
                removals += 1
        elif cat.name in fluxer_name_map:
            context.state.set_category_mapping(discord_id, fluxer_name_map[cat.name])
            updates += 1
                
    # 2. Verify and Sync Channels
    for ch in channels:
        discord_id = str(ch.id)
        fluxer_id = context.state.get_fluxer_channel_id(discord_id)
        
        if fluxer_id:
            if fluxer_id not in fluxer_id_set:
                context.state.remove_channel_mapping(discord_id)
                removals += 1
        elif ch.name in fluxer_name_map:
            context.state.set_channel_mapping(discord_id, fluxer_name_map[ch.name])
            updates += 1
    
    if updates > 0 or removals > 0:
        logger.info(f"Channel sync: {updates} mapped, {removals} stale mappings removed")


async def migrate_channels(context: MigrationContext, progress_callback: Callable[[str, str, int, int], Awaitable[None]] | None = None, force: bool = False) -> dict:
    """Clones categories and text channels.
    
    Args:
        progress_callback: Optional callback receiving (item_name, status, current, total)
        force: If True, re-create channels even if they exist in state.
    """
    categories = await context.discord_reader.get_categories()
    channels = await context.discord_reader.get_channels()
    
    cloned_info = {
        "categories_created": [],
        "channels_created": [],
        "channels_synced": [],
        "structure": {} # category_name -> [channel_names]
    }
    cat_name_map = {str(cat.id): cat.name for cat in categories}
    
    # 1. Identify categories to create
    missing_categories = [cat for cat in categories if force or not context.state.get_fluxer_category_id(str(cat.id))]
    missing_category_ids = {str(cat.id) for cat in missing_categories}
    
    # 2. Identify channels to create or move
    # Fetch current Fluxer state to check parent_ids
    fluxer_channels = await context.fluxer_writer.get_channels()
    fluxer_parent_map = {str(c["id"]): (str(c.get("parent_id")) if c.get("parent_id") else None) for c in fluxer_channels}
    
    channels_to_create = []
    channels_to_move = []
    
    for ch in channels:
        discord_id = str(ch.id)
        fluxer_id = context.state.get_fluxer_channel_id(discord_id)
        
        if force or not fluxer_id:
            # We'll resolve the parent_id in the loop after categories are created
            channels_to_create.append(ch)
        else:
            # Always add to move/sync list to ensure properties (topic, nsfw, slowmode) are synced
            # even if the parent category is already correct.
            channels_to_move.append((ch, fluxer_id))

    total = len(missing_categories) + len(channels_to_create) + len(channels_to_move)
    current_idx = 0
    
    if total == 0:
        return cloned_info

    # Migrate Categories first
    for cat in missing_categories:
        if not context.is_running: break
        
        state_key = str(cat.id)
        fluxer_id = await context.fluxer_writer.create_channel(cat.name, type=4)
        context.state.set_category_mapping(state_key, fluxer_id)
        cloned_info["categories_created"].append(cat.name)
        if cat.name not in cloned_info["structure"]:
            cloned_info["structure"][cat.name] = []
        
        current_idx += 1
        if progress_callback: await progress_callback(f"Cat: {cat.name}", "Copying", current_idx, total)
        await asyncio.sleep(context.config.migration.rate_limit_delay_seconds)

    # Create missing channels
    for channel in channels_to_create:
        if not context.is_running: break
            
        state_key = str(channel.id)
        topic = getattr(channel, 'topic', "") or ""
        nsfw = getattr(channel, 'nsfw', False)
        slowmode = getattr(channel, 'slowmode_delay', 0)
        
        logger.debug(f"Creating channel {channel.name}: topic={topic}, nsfw={nsfw}, slowmode={slowmode}")
        
        parent_id = context.state.get_fluxer_category_id(str(channel.category_id)) if channel.category_id else None
        
        fluxer_id = await context.fluxer_writer.create_channel(
            name=channel.name, 
            topic=topic, 
            type=0, 
            parent_id=parent_id,
            nsfw=nsfw,
            slowmode_delay=slowmode
        )
        context.state.set_channel_mapping(state_key, fluxer_id)
        cloned_info["channels_created"].append(channel.name)
        
        parent_name = cat_name_map.get(str(channel.category_id), "No Category") if channel.category_id else "No Category"
        if parent_name not in cloned_info["structure"]:
            cloned_info["structure"][parent_name] = []
        cloned_info["structure"][parent_name].append(channel.name)
        
        # Sync again immediately because some properties (like slowmode) are ignored on creation
        await context.fluxer_writer.modify_channel(
            channel_id=fluxer_id,
            parent_id=parent_id,
            name=channel.name,
            topic=topic,
            nsfw=nsfw,
            slowmode_delay=slowmode
        )
        
        current_idx += 1
        if progress_callback: await progress_callback(channel.name, "Copying", current_idx, total)
        await asyncio.sleep(context.config.migration.rate_limit_delay_seconds)

    # Move/Sync existing channels
    for channel, fluxer_id in channels_to_move:
        if not context.is_running: break
        
        parent_id = context.state.get_fluxer_category_id(str(channel.category_id)) if channel.category_id else None
        nsfw = getattr(channel, 'nsfw', False)
        slowmode = getattr(channel, 'slowmode_delay', 0)
        topic = getattr(channel, 'topic', "") or ""
        
        logger.debug(f"Syncing existing channel {channel.name} ({fluxer_id}): topic={topic}, nsfw={nsfw}, slowmode={slowmode}")
        
        await context.fluxer_writer.modify_channel(
            channel_id=fluxer_id, 
            parent_id=parent_id,
            name=channel.name,
            topic=topic,
            nsfw=nsfw,
            slowmode_delay=slowmode
        )
        
        cloned_info["channels_synced"].append(channel.name)
        
        current_idx += 1
        if progress_callback: await progress_callback(channel.name, "Syncing", current_idx, total)
        await asyncio.sleep(context.config.migration.rate_limit_delay_seconds)

    return cloned_info
