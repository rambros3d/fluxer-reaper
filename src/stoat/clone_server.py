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
    categories = await context.source_reader.get_categories()
    channels = await context.source_reader.get_channels()
    target_channels = await context.writer.get_channels()
    
    # Build maps for Stoat lookup
    # {name: id} for categories (type 4)
    target_cats = {c.get("name"): str(c.get("id")) for c in target_channels if c.get("type") == 4}
    # {parent_id: {name: id}} for channels
    target_structure = {}
    for c in target_channels:
        if c.get("type") == 4: continue
        p_id = str(c.get("parent_id")) if c.get("parent_id") else "root"
        if p_id not in target_structure: target_structure[p_id] = {}
        target_structure[p_id][c.get("name")] = str(c.get("id"))
        
    target_id_set = {str(c.get("id")) for c in target_channels}
    updates = 0
    removals = 0
    
    # 1. Sync Categories
    for cat in categories:
        discord_id = str(cat.id)
        target_id = context.state.get_target_category_id(discord_id)
        if target_id:
            if target_id not in target_id_set:
                context.state.remove_category_mapping(discord_id)
                removals += 1
        elif cat.name in target_cats:
            context.state.set_target_category_mapping(discord_id, target_cats[cat.name])
            updates += 1
                
    # 2. Sync Channels (parent-aware)
    for ch in channels:
        discord_id = str(ch.id)
        target_id = context.state.get_target_channel_id(discord_id)
        if target_id:
            if target_id not in target_id_set:
                context.state.remove_channel_mapping(discord_id)
                removals += 1
        else:
            # Try to match by name within the mapped parent category
            p_discord_id = str(ch.category_id) if ch.category_id else "root"
            p_target_id = context.state.get_target_category_id(p_discord_id) if p_discord_id != "root" else "root"
            
            if p_target_id in target_structure and ch.name in target_structure[p_target_id]:
                context.state.set_target_channel_mapping(discord_id, target_structure[p_target_id][ch.name])
                updates += 1
    
    if updates > 0 or removals > 0:
        logger.info(f"Stoat Channel sync: {updates} mapped, {removals} stale mappings removed")


async def migrate_channels(context: MigrationContext, progress_callback: Callable[[str, str, int, int], Awaitable[None]] | None = None, force: bool = False) -> dict:
    """Clones categories and text channels.
    
    Args:
        progress_callback: Optional callback receiving (item_name, status, current, total)
        force: If True, re-create channels even if they exist in state.
    """
    categories = await context.source_reader.get_categories()
    all_channels = await context.source_reader.get_channels()
    
    # Sort categories and channels by position to preserve order
    categories = sorted(categories, key=lambda c: getattr(c, 'position', 0))
    all_channels = sorted(all_channels, key=lambda c: getattr(c, 'position', 0))

    # Only migrate text-like and voice channels. Forum channels are not yet supported in Stoat.
    reader = context.source_reader
    channels = [
        ch for ch in all_channels
        if ch.type != reader.CHANNEL_TYPE_FORUM
    ]
    skipped = [ch.name for ch in all_channels if ch not in channels]
    if skipped:
        logger.info(f"Skipping {len(skipped)} forum channel(s): {', '.join(skipped)}")
    
    cloned_info = {
        "categories_created": [],
        "channels_created": [],
        "channels_synced": [],
        "structure": {} # category_name -> [channel_names]
    }
    cat_name_map = {str(cat.id): cat.name for cat in categories}
    
    # 1. Identify categories to create
    missing_categories = [cat for cat in categories if force or not context.state.get_target_category_id(str(cat.id))]
    
    # 2. Identify channels to create or move
    channels_to_create = []
    channels_to_move = []
    
    for ch in channels:
        discord_id = str(ch.id)
        target_id = context.state.get_target_channel_id(discord_id)
        
        if force or not target_id:
            channels_to_create.append(ch)
        else:
            channels_to_move.append((ch, target_id))

    total = len(missing_categories) + len(channels_to_create) + len(channels_to_move)
    current_idx = 0
    
    if total == 0:
        return cloned_info

    # 1. Create missing categories first
    for cat in missing_categories:
        if not context.is_running: break
        
        state_key = str(cat.id)
        target_id = await context.writer.create_channel(cat.name, type=4)
        if target_id:
            context.state.set_target_category_id(state_key, target_id)
            cloned_info["categories_created"].append(cat.name)
            if cat.name not in cloned_info["structure"]:
                cloned_info["structure"][cat.name] = []
        
        current_idx += 1
        if progress_callback: await progress_callback(cat.name, "Copying", current_idx, total)

    # 2. Create missing channels (now with parent_id available)
    for channel in channels_to_create:
        if not context.is_running: break
            
        state_key = str(channel.id)
        topic = getattr(channel, 'topic', "") or ""
        nsfw = getattr(channel, 'nsfw', False)
        slowmode = getattr(channel, 'slowmode_delay', 0)
        
        logger.debug(f"Creating channel {channel.name}: topic={topic}, nsfw={nsfw}, slowmode={slowmode}")
        
        # Map Discord-specific types to target-supported types
        raw_type = channel.type.value if hasattr(channel.type, 'value') else 0
        if raw_type == context.source_reader.CHANNEL_TYPE_VOICE.value:
            ch_type = 2
            is_voice = True
        elif raw_type in [context.source_reader.CHANNEL_TYPE_TEXT.value, context.source_reader.CHANNEL_TYPE_NEWS.value]:
            ch_type = 0
            is_voice = False
        else:
            ch_type = 0
            is_voice = False
        
        # Resolve parent category
        parent_id = context.state.get_target_category_id(str(channel.category_id)) if channel.category_id else None
        
        target_id = await context.writer.create_channel(
            name=channel.name, 
            topic=topic if not is_voice else "", 
            type=ch_type, 
            parent_id=parent_id,
            nsfw=nsfw if not is_voice else False,
            slowmode_delay=slowmode if not is_voice else 0
        )
        if target_id:
            context.state.set_target_channel_id(state_key, target_id)
            cloned_info["channels_created"].append(channel.name)
            
            parent_name = cat_name_map.get(str(channel.category_id), "No Category") if channel.category_id else "No Category"
            if parent_name not in cloned_info["structure"]:
                cloned_info["structure"][parent_name] = []
            cloned_info["structure"][parent_name].append(channel.name)
            
            # Sync properties immediately (only for non-voice channels)
            if not is_voice:
                await context.writer.modify_channel(
                    channel_id=target_id,
                    name=channel.name,
                    topic=topic,
                    nsfw=nsfw,
                    slowmode_delay=slowmode,
                    parent_id=parent_id
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
        
        parent_id = context.state.get_target_category_id(str(channel.category_id)) if channel.category_id else None
        
        await context.writer.modify_channel(
            channel_id=target_id, 
            name=channel.name,
            topic=topic,
            nsfw=nsfw,
            slowmode_delay=slowmode,
            parent_id=parent_id
        )
        
        cloned_info["cloned_info" if "cloned_info" in locals() else "channels_synced"].append(channel.name)
        
        current_idx += 1
        if progress_callback: await progress_callback(channel.name, "Syncing", current_idx, total)
        if progress_callback: await progress_callback(f"Cat: {cat.name}", "Copying", current_idx, total)

    # 4. Final step: Parent the channels into categories via mass server.edit()
    logger.info("Parenting all channels into their respective categories...")
    # Force refresh server to get latest categories created during migration
    server = await context.writer._get_server(populate_channels=True, force=True)
    
    # Stoat categories are managed via server.edit(categories=...)
    # We must preserve ALL categories that exist in Stoat, but update the ones we manage.
    target_categories = []
    
    # Build map of Discord Cat ID -> [Target Channel IDs]
    # Channels should already be sorted by position in the 'channels' list
    discord_cat_to_channels = {}
    for ch in channels:
        cat_id = str(getattr(ch, 'category_id', '')) if ch.category_id else "unparented"
        if cat_id not in discord_cat_to_channels:
            discord_cat_to_channels[cat_id] = []
        
        target_id = context.state.get_target_channel_id(str(ch.id))
        if target_id:
            discord_cat_to_channels[cat_id].append(target_id)

    # Resolve Stoat categories
    # We iterate over the categories from the server to ensure we don't drop any
    for stoat_cat in server.categories:
        # Check if this Stoat category maps to any Discord category
        discord_cat_id = next((d_id for d_id, t_id in context.state.category_map.items() if t_id == str(stoat_cat.id)), None)
        
        if discord_cat_id:
            # Managed category - update its channels
            new_channels = discord_cat_to_channels.get(discord_cat_id, [])
            new_cat = stoat.Category(
                id=str(stoat_cat.id),
                title=stoat_cat.title,
                channels=new_channels
            )
        else:
            # Unmanaged category - preserve as is but ensure all channels are strings
            new_cat = stoat.Category(
                id=str(stoat_cat.id),
                title=stoat_cat.title,
                channels=[str(c_id) for c_id in stoat_cat.channels]
            )
        
        # Workaround for stoat.py missing attributes
        if not hasattr(new_cat, "default_permissions"): new_cat.default_permissions = getattr(stoat_cat, "default_permissions", None)
        if not hasattr(new_cat, "role_permissions"): new_cat.role_permissions = getattr(stoat_cat, "role_permissions", {})
            
        target_categories.append(new_cat)

    # Sort target_categories based on the original Discord category positions
    def get_cat_position(s_cat):
        d_id = next((d_id for d_id, t_id in context.state.category_map.items() if t_id == str(s_cat.id)), None)
        if d_id:
            d_cat = next((c for c in categories if str(c.id) == d_id), None)
            if d_cat:
                return getattr(d_cat, 'position', 999)
        return 999

    target_categories.sort(key=get_cat_position)

    try:
        await server.edit(categories=target_categories)
        logger.info("Successfully parented all channels.")
    except Exception as ex:
        logger.error(f"Failed to mass parent channels: {ex}")

    return cloned_info
