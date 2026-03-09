import asyncio
import logging
import stoat
from typing import Callable, Awaitable

from src.core.base import MigrationContext

logger = logging.getLogger(__name__)

async def sync_channel_state(context: MigrationContext):
    """
    Scans Stoat for channels matching Discord names and updates state file mappings.
    This prevents duplicate creation when the state file is empty but channels exist in Stoat.
    """
    categories = await context.discord_reader.get_categories()
    channels = await context.discord_reader.get_channels()
    target_channels = await context.writer.get_channels()
    
    # Build name -> id map and ID set for Stoat for fast lookup
    target_name_map = {c.get("name"): str(c.get("id")) for c in target_channels if c.get("name")}
    target_id_set = {str(c.get("id")) for c in target_channels}
    
    updates = 0
    removals = 0
    
    # 1. Verify and Sync Categories
    for cat in categories:
        discord_id = str(cat.id)
        target_id = context.state.get_target_category_id(discord_id)
        
        if target_id:
            if target_id not in target_id_set:
                context.state.remove_category_mapping(discord_id)
                removals += 1
        elif cat.name in target_name_map:
            context.state.set_target_category_mapping(discord_id, target_name_map[cat.name])
            updates += 1
                
    # 2. Verify and Sync Channels
    for ch in channels:
        discord_id = str(ch.id)
        target_id = context.state.get_target_channel_id(discord_id)
        
        if target_id:
            if target_id not in target_id_set:
                context.state.remove_channel_mapping(discord_id)
                removals += 1
        elif ch.name in target_name_map:
            context.state.set_target_channel_mapping(discord_id, target_name_map[ch.name])
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
    missing_categories = [cat for cat in categories if force or not context.state.get_target_category_id(str(cat.id))]
    missing_category_ids = {str(cat.id) for cat in missing_categories}
    
    # 2. Identify channels to create or move
    # Fetch current Target state to check parent_ids
    target_channels = await context.writer.get_channels()
    target_parent_map = {str(c["id"]): (str(c.get("parent_id")) if c.get("parent_id") else None) for c in target_channels}
    
    channels_to_create = []
    channels_to_move = []
    
    for ch in channels:
        discord_id = str(ch.id)
        target_id = context.state.get_target_channel_id(discord_id)
        
        if force or not target_id:
            # We'll resolve the parent_id in the loop after categories are created
            channels_to_create.append(ch)
        else:
            # Always add to move/sync list to ensure properties (topic, nsfw, slowmode) are synced
            # even if the parent category is already correct.
            channels_to_move.append((ch, target_id))

    total = len(missing_categories) + len(channels_to_create) + len(channels_to_move)
    current_idx = 0
    
    if total == 0:
        return cloned_info

    # 1. Migrate Categories
    missing_category_ids = {str(cat.id) for cat in missing_categories}
    for cat in missing_categories:
        if not context.is_running: break
        
        state_key = str(cat.id)
        target_id = await context.writer.create_channel(cat.name, type=4)
        if target_id:
            context.state.set_target_category_mapping(state_key, target_id)
            cloned_info["categories_created"].append(cat.name)
            if cat.name not in cloned_info["structure"]:
                cloned_info["structure"][cat.name] = []
        
        current_idx += 1
        if progress_callback: await progress_callback(f"Cat: {cat.name}", "Copying", current_idx, total)

    # 2. Create missing channels (unparented for now)
    for channel in channels_to_create:
        if not context.is_running: break
            
        state_key = str(channel.id)
        topic = getattr(channel, 'topic', "") or ""
        nsfw = getattr(channel, 'nsfw', False)
        slowmode = getattr(channel, 'slowmode_delay', 0)
        
        logger.debug(f"Creating channel {channel.name}: topic={topic}, nsfw={nsfw}, slowmode={slowmode}")
        
        target_id = await context.writer.create_channel(
            name=channel.name, 
            topic=topic, 
            type=0, 
            parent_id=None,
            nsfw=nsfw,
            slowmode_delay=slowmode
        )
        if target_id:
            context.state.set_target_channel_mapping(state_key, target_id)
            cloned_info["channels_created"].append(channel.name)
            
            parent_name = cat_name_map.get(str(channel.category_id), "No Category") if channel.category_id else "No Category"
            if parent_name not in cloned_info["structure"]:
                cloned_info["structure"][parent_name] = []
            cloned_info["structure"][parent_name].append(channel.name)
            
            # Sync properties immediately
            await context.writer.modify_channel(
                channel_id=target_id,
                parent_id=None,
                name=channel.name,
                topic=topic,
                nsfw=nsfw,
                slowmode_delay=slowmode
            )
        
        current_idx += 1
        if progress_callback: await progress_callback(channel.name, "Copying", current_idx, total)

    # 3. Move/Sync existing channels
    for channel, target_id in channels_to_move:
        if not context.is_running: break
        
        nsfw = getattr(channel, 'nsfw', False)
        slowmode = getattr(channel, 'slowmode_delay', 0)
        topic = getattr(channel, 'topic', "") or ""
        
        logger.debug(f"Syncing existing channel {channel.name} ({target_id}): topic={topic}, nsfw={nsfw}, slowmode={slowmode}")
        
        await context.writer.modify_channel(
            channel_id=target_id, 
            parent_id=None,
            name=channel.name,
            topic=topic,
            nsfw=nsfw,
            slowmode_delay=slowmode
        )
        
        cloned_info["channels_synced"].append(channel.name)
        
        current_idx += 1
        if progress_callback: await progress_callback(channel.name, "Syncing", current_idx, total)

    # 4. Final step: Parent the channels into categories via mass server.edit()
    logger.info("Parenting all channels into their respective categories...")
    # Force refetch to ensure we see all newly created categories from the loop above
    server = await context.writer._get_server(populate_channels=True, force=True)
    cats = list(server.categories) if hasattr(server, "categories") and server.categories else []
    
    # Workaround: Ensure default properties are set for all categories
    for c in cats:
        if not hasattr(c, "default_permissions"): c.default_permissions = None
        if not hasattr(c, "role_permissions"): c.role_permissions = {}
    
    # We will build a map of target_cat_id -> list of target_ch_ids
    cat_to_channels = {}
    for cat in categories:
        target_cat_id = context.state.get_target_category_id(str(cat.id))
        if not target_cat_id: continue

        target_channels_for_cat = []
        for ch in channels:
            if str(getattr(ch, 'category_id', '')) == str(cat.id):
                target_ch_id = context.state.get_target_channel_id(str(ch.id))
                if target_ch_id:
                    target_channels_for_cat.append(target_ch_id)
        
        cat_to_channels[target_cat_id] = target_channels_for_cat
    
    # Now correctly assign them in the cats array, and remove them from other cats
    all_assigned_channels = set()
    for cat_id, ch_list in cat_to_channels.items():
        all_assigned_channels.update(ch_list)
        
    for i, c in enumerate(cats):
        # Remove any channels that are being assigned to a specific category
        new_channels = [ch for ch in c.channels if ch not in all_assigned_channels]
        if c.id in cat_to_channels:
            # If this is one of our managed categories, set its channels to exactly what it should be
            new_channels = cat_to_channels[c.id]
        
        new_cat = stoat.Category(id=c.id, title=c.title, channels=new_channels)
        new_cat.default_permissions = c.default_permissions
        new_cat.role_permissions = c.role_permissions
        cats[i] = new_cat
            
    try:
        await server.edit(categories=cats)
        logger.info("Successfully parented all channels.")
    except Exception as ex:
        logger.error(f"Failed to mass parent channels: {ex}")

    return cloned_info
