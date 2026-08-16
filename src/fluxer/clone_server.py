import asyncio
import logging
from typing import Callable, Awaitable

from src.core.base import MigrationContext

### Changes from Discord-specific to source-agnostic:

#Before	                            After
#channel.type.value	                _get_channel_type_int(channel) – handles both Enum and int.
#reader.CHANNEL_TYPE_VOICE.value	reader.CHANNEL_TYPE_VOICE (now an integer in both readers).
#Hardcoded type=4 for categories	Kept as is, since Fluxer uses the same numeric ID.
#Forum detection with .value	    Uses ch_type == reader.CHANNEL_TYPE_FORUM (integer).
#channel.category_id	            Provided by FluxerChannelWrapper; works for both.

logger = logging.getLogger(__name__)

def _reader_type_int(reader, attr_name: str) -> int:
    """Return the integer value of a channel type constant from the reader."""
    val = getattr(reader, attr_name)
    return val.value if hasattr(val, 'value') else val

# Helper to get the integer channel type regardless of source
def _get_channel_type_int(channel):
    if hasattr(channel.type, 'value'):
        return channel.type.value
    return channel.type  # assume it's already an int

async def sync_channel_state(context: MigrationContext):
    """
    Scans Fluxer for channels matching names and updates state file mappings.
    Prevents duplicate creation when state file is empty but channels exist.
    """
    categories = await context.source_reader.get_categories()
    channels = await context.source_reader.get_channels()
    fluxer_channels = await context.fluxer_writer.get_channels()
    
    fluxer_cats = {c.get("name"): str(c.get("id")) for c in fluxer_channels if c.get("type") == 4}
    fluxer_structure = {}
    for c in fluxer_channels:
        if c.get("type") == 4:
            continue
        p_id = str(c.get("parent_id")) if c.get("parent_id") else "root"
        if p_id not in fluxer_structure:
            fluxer_structure[p_id] = {}
        fluxer_structure[p_id][c.get("name")] = str(c.get("id"))
    
    fluxer_id_set = {str(c.get("id")) for c in fluxer_channels}
    updates = 0
    removals = 0
    
    for cat in categories:
        discord_id = str(cat.id)
        fluxer_id = context.state.get_fluxer_category_id(discord_id)
        if fluxer_id:
            if fluxer_id not in fluxer_id_set:
                context.state.remove_category_mapping(discord_id)
                removals += 1
        elif cat.name in fluxer_cats:
            context.state.set_category_mapping(discord_id, fluxer_cats[cat.name])
            updates += 1
                
    for ch in channels:
        discord_id = str(ch.id)
        fluxer_id = context.state.get_fluxer_channel_id(discord_id)
        if fluxer_id:
            if fluxer_id not in fluxer_id_set:
                context.state.remove_channel_mapping(discord_id)
                removals += 1
        else:
            p_discord_id = str(ch.category_id) if ch.category_id else "root"
            p_fluxer_id = context.state.get_fluxer_category_id(p_discord_id) if p_discord_id != "root" else "root"
            if p_fluxer_id in fluxer_structure and ch.name in fluxer_structure[p_fluxer_id]:
                context.state.set_channel_mapping(discord_id, fluxer_structure[p_fluxer_id][ch.name])
                updates += 1
    
    if updates > 0 or removals > 0:
        logger.info(f"Fluxer Channel sync: {updates} mapped, {removals} stale mappings removed")


async def migrate_channels(context: MigrationContext, progress_callback: Callable[[str, str, int, int], Awaitable[None]] | None = None, force: bool = False) -> dict:
    """Clones categories and text/voice channels. Skips forum channels."""
    categories = sorted(await context.source_reader.get_categories(), key=lambda c: getattr(c, 'position', 0))
    all_channels = sorted(await context.source_reader.get_channels(), key=lambda c: getattr(c, 'position', 0))

    reader = context.source_reader
    # Skip forums – both Discord and Fluxer use type 15 for forums
    channels = []
    skipped = []
    for ch in all_channels:
        forum_type = _reader_type_int(reader, 'CHANNEL_TYPE_FORUM')
        ch_type = _get_channel_type_int(ch)
        if ch_type == forum_type:
            skipped.append(ch.name)
        else:
            channels.append(ch)

    if skipped:
        logger.info(f"Skipping {len(skipped)} forum channel(s): {', '.join(skipped)}")
    
    cloned_info = {
        "categories_created": [],
        "channels_created": [],
        "channels_synced": [],
        "structure": {}
    }
    cat_name_map = {str(cat.id): cat.name for cat in categories}
    
    missing_categories = [cat for cat in categories if force or not context.state.get_fluxer_category_id(str(cat.id))]
    missing_category_ids = {str(cat.id) for cat in missing_categories}
    
    fluxer_channels = await context.fluxer_writer.get_channels()
    fluxer_parent_map = {str(c["id"]): (str(c.get("parent_id")) if c.get("parent_id") else None) for c in fluxer_channels}
    
    channels_to_create = []
    channels_to_move = []
    for ch in channels:
        source_id = str(ch.id)
        fluxer_id = context.state.get_fluxer_channel_id(source_id)
        if force or not fluxer_id:
            channels_to_create.append(ch)
        else:
            channels_to_move.append((ch, fluxer_id))

    total = len(missing_categories) + len(channels_to_create) + len(channels_to_move)
    current_idx = 0
    if total == 0:
        return cloned_info

    # Migrate categories
    for cat in missing_categories:
        if not context.is_running:
            break
        pos = getattr(cat, 'position', None)
        fluxer_id = await context.fluxer_writer.create_channel(cat.name, type=4, position=pos)
        context.state.set_category_mapping(str(cat.id), fluxer_id)
        cloned_info["categories_created"].append(cat.name)
        if cat.name not in cloned_info["structure"]:
            cloned_info["structure"][cat.name] = []
        current_idx += 1
        if progress_callback:
            await progress_callback(f"Cat: {cat.name}", "Copying", current_idx, total)

    # Create missing channels
    for channel in channels_to_create:
        if not context.is_running:
            break
        #ch_type = _get_channel_type_int(channel)
        # Map to Fluxer types: 0=text, 2=voice, others fallback to 0
        raw_type = _get_channel_type_int(channel)
        voice_type = _reader_type_int(reader, 'CHANNEL_TYPE_VOICE')
        text_type = _reader_type_int(reader, 'CHANNEL_TYPE_TEXT')
        news_type = _reader_type_int(reader, 'CHANNEL_TYPE_NEWS')

        logger.info(f"Channel: {channel.name}, raw_type={raw_type}, voice_type={voice_type}, text_type={text_type}, news_type={news_type}")
        
        if raw_type == voice_type:
            target_type = 2
            is_voice = True
        elif raw_type in (text_type, news_type):
            target_type = 0
            is_voice = False
        else:
            target_type = 0
            is_voice = False

        parent_id = context.state.get_fluxer_category_id(str(channel.category_id)) if channel.category_id else None
        topic = getattr(channel, 'topic', "") or ""
        nsfw = getattr(channel, 'nsfw', False)
        slowmode = getattr(channel, 'slowmode_delay', 0)
        pos = getattr(channel, 'position', None)

        fluxer_id = await context.fluxer_writer.create_channel(
            name=channel.name,
            topic=topic if not is_voice else "",
            type=target_type,
            parent_id=parent_id,
            nsfw=nsfw if not is_voice else False,
            slowmode_delay=slowmode if not is_voice else 0,
            position=pos
        )
        context.state.set_channel_mapping(str(channel.id), fluxer_id)
        cloned_info["channels_created"].append(channel.name)

        parent_name = cat_name_map.get(str(channel.category_id), "No Category") if channel.category_id else "No Category"
        if parent_name not in cloned_info["structure"]:
            cloned_info["structure"][parent_name] = []
        cloned_info["structure"][parent_name].append(channel.name)

        # Re‑apply properties (for safety)
        if not is_voice:
            await context.fluxer_writer.modify_channel(
                channel_id=fluxer_id,
                parent_id=parent_id,
                name=channel.name,
                topic=topic,
                nsfw=nsfw,
                slowmode_delay=slowmode,
                position=pos
            )
        current_idx += 1
        if progress_callback:
            await progress_callback(channel.name, "Copying", current_idx, total)

    # Move/Sync existing channels
    for channel, fluxer_id in channels_to_move:
        if not context.is_running:
            break
        parent_id = context.state.get_fluxer_category_id(str(channel.category_id)) if channel.category_id else None
        topic = getattr(channel, 'topic', "") or ""
        nsfw = getattr(channel, 'nsfw', False)
        slowmode = getattr(channel, 'slowmode_delay', 0)
        pos = getattr(channel, 'position', None)

        await context.fluxer_writer.modify_channel(
            channel_id=fluxer_id,
            parent_id=parent_id,
            name=channel.name,
            topic=topic,
            nsfw=nsfw,
            slowmode_delay=slowmode,
            position=pos
        )
        cloned_info["channels_synced"].append(channel.name)
        current_idx += 1
        if progress_callback:
            await progress_callback(channel.name, "Syncing", current_idx, total)

    return cloned_info