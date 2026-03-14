import asyncio
import logging
import re
import json
import io
from typing import Callable, Awaitable, Dict, Any

try:
    from lottie.objects import Animation
    from lottie.exporters.gif import export_gif
    HAS_LOTTIE = True
except ImportError:
    HAS_LOTTIE = False

from src.core.base import MigrationContext
from src.core.utils import resolve_discord_links

logger = logging.getLogger(__name__)

def clean_mentions(content: str, guild, user_mentions=None, role_mentions=None, emoji_map=None, channel_map=None, state=None, target_server_id=None) -> str:
    if not content or not guild:
        return content
        
    def replace_user(match):
        uid = int(match.group(1))
        # 1. Try provided guild
        member = guild.get_member(uid)
        if member:
            return f"`@{member.display_name}`"
        # 2. Try message's user_mentions
        if user_mentions:
            for u in user_mentions:
                if u.id == uid:
                    return f"`@{getattr(u, 'display_name', u.name)}`"
        # 3. Try global cache via guild.client
        if hasattr(guild, 'client'):
            user = guild.client.get_user(uid)
            if user:
                return f"`@{user.name}`"
        return match.group(0)
        
    def replace_role(match):
        rid = int(match.group(1))
        # 1. Try provided guild cache/list
        role = guild.get_role(rid) or next((r for r in guild.roles if r.id == rid), None)
        # 2. Try message's role_mentions
        if not role and role_mentions:
            role = next((r for r in role_mentions if r.id == rid), None)
        
        # 3. Try all guilds the client is aware of (fallback for cache issues)
        if not role and hasattr(guild, 'client'):
            for g in guild.client.guilds:
                role = g.get_role(rid)
                if role: break
        
        if role and role.name:
            return f"`@{role.name}`"
            
        return match.group(0)
        
    def replace_channel(match):
        cid = int(match.group(1))
        
        # 1. Check if channel is mapped in state
        if channel_map and str(cid) in channel_map:
            return f"<#{channel_map[str(cid)]}>"
            
        # 2. Fallback to name in backticks
        channel = guild.get_channel(cid)
        return f"`#{channel.name}`" if channel else f"<#{cid}>"

    def replace_emoji(match):
        animated = match.group(1) == "a"
        name = match.group(2)
        eid = match.group(3)
        
        if emoji_map and eid in emoji_map:
            target_eid = emoji_map[eid]
            prefix = "a" if animated else ""
            #return f"<{prefix}:{name}:{target_eid}>" name not require for stoat
            return f":{target_eid}:"
        return f":{name}:"

    content = re.sub(r'<@!?([0-9]+)>', replace_user, content)
    content = re.sub(r'<@&([0-9]+)>', replace_role, content)
    content = re.sub(r'<#([0-9]+)>', replace_channel, content)
    content = re.sub(r'<(a?):([^:]+):([0-9]+)>', replace_emoji, content)
    content = content.replace("@everyone", "`@everyone`").replace("@here", "`@here`")

    # Resolve Discord Links
    if state and target_server_id:
        content = resolve_discord_links(content, state, "stoat", target_server_id)

    return content


    return content


async def get_channel_threads(reader: Any, channel_id: int) -> List[Any]:
    """Helper to fetch all threads (active and archived) for a channel from Live or Backup."""
    threads = []
    
    # 1. From Backup (BackupReader has 'db' attribute)
    if hasattr(reader, 'db') and hasattr(reader, 'threads'):
        for t in reader.threads:
            if t.parent_id == channel_id:
                threads.append(t)
        return threads

    # 2. From live Discord
    if hasattr(reader, 'guild') and reader.guild:
        try:
            # Guild-wide active threads
            if hasattr(reader.guild, 'active_threads'):
                for t in reader.guild.active_threads:
                    if t.parent_id == channel_id:
                        threads.append(t)
            
            # Archived threads for this specific channel
            channel = await reader.get_channel(channel_id)
            if hasattr(channel, 'archived_threads'):
                # discord.py method
                async for t in channel.archived_threads(limit=None):
                    threads.append(t)
        except Exception as e:
            logger.debug(f"Could not fetch live threads for {channel_id}: {e}")
            
    return threads


async def analyze_migration(context: MigrationContext, source_channel_id: int, after_message_id: int | None = None, inclusive: bool = False, progress_callback: Callable[[Dict[str, Any]], Awaitable[None]] | None = None, processed_threads: set | None = None) -> Dict[str, int]:
    """
    Scans channel history to count messages, threads, and attachments.
    """
    stats = {
        "messages": 0, 
        "threads": 0, 
        "attachments": 0,
        "first_message_url": "",
        "last_message_url": ""
    }
    
    if processed_threads is None:
        processed_threads = set()

    async for msg in context.discord_reader.fetch_message_history(source_channel_id, after_id=after_message_id, inclusive=inclusive):
        if not context.is_running:
            break
        
        # Count thread messages and markers even if parent is skipped
        if hasattr(msg, 'thread') and msg.thread:
            thread = msg.thread
            if thread.id not in processed_threads:
                processed_threads.add(thread.id)
                stats["threads"] += 1
                thread_stats = await analyze_migration(context, thread.id, processed_threads=processed_threads)
                stats["messages"] += thread_stats["messages"]
                stats["attachments"] += thread_stats["attachments"]
                stats["threads"] += thread_stats["threads"]

        # Consistent filtering with migrate_messages
        if msg.type not in [
            context.discord_reader.MESSAGE_TYPE_DEFAULT,
            context.discord_reader.MESSAGE_TYPE_REPLY,
            context.discord_reader.MESSAGE_TYPE_THREAD_STARTER,
            context.discord_reader.MESSAGE_TYPE_FORWARD
        ]:
            continue

        stats["messages"] += 1
        stats["attachments"] += len(msg.attachments)

        if progress_callback and stats["messages"] % 10 == 0:
            await progress_callback(stats)

    # After scanning messages, explicitly check for any missed threads (e.g. archived or skipped in scan)
    # Only do this at the top level
    if after_message_id is not None or inclusive:
        all_threads = await get_channel_threads(context.discord_reader, source_channel_id)
        for t in all_threads:
            if t.id not in processed_threads:
                processed_threads.add(t.id)
                stats["threads"] += 1
                thread_stats = await analyze_migration(context, t.id, processed_threads=processed_threads)
                stats["messages"] += thread_stats["messages"]
                stats["attachments"] += thread_stats["attachments"]
                stats["threads"] += thread_stats["threads"]

    return stats


async def migrate_messages(
    context: MigrationContext, 
    source_channel_id: int, 
    target_channel_id: str, 
    after_message_id: int | None = None, 
    inclusive: bool = False,
    progress_callback: Callable[[Dict[str, Any]], Awaitable[None]] | None = None,
    thread_id: str | None = None,
    parent_target_id: str | None = None,
    thread_name: str | None = None,
    processed_threads: set | None = None
) -> Dict[str, Any]:
    """Migrate messages for a specific channel using Stoat masquerade for author impersonation."""
    stats = {
        "messages": 0, 
        "threads": 0, 
        "attachments": 0,
        "first_message_url": "",
        "last_message_url": "",
        "last_message_content": "",
        "last_message_author": ""
    }
    
    logger.info(f"Starting message migration: Discord #{source_channel_id} -> Stoat #{target_channel_id}")
    if after_message_id:
        logger.info(f"Resuming migration from after message ID: {after_message_id}")
        
    if processed_threads is None:
        processed_threads = set()
        
    try:
        async for msg in context.discord_reader.fetch_message_history(source_channel_id, after_id=after_message_id, inclusive=inclusive):
            if not context.is_running:
                logger.warning("Migration interrupted by user (is_running=False)")
                break
                


            # Skip system messages like "pinned a message", etc.
            content = "" # Initialize content
            if msg.type not in [
                context.discord_reader.MESSAGE_TYPE_DEFAULT,
                context.discord_reader.MESSAGE_TYPE_REPLY,
                context.discord_reader.MESSAGE_TYPE_THREAD_STARTER,
                context.discord_reader.MESSAGE_TYPE_FORWARD
            ]:
                # If we are skipping the parent, we STILL need to check for a thread!
                if hasattr(msg, 'thread') and msg.thread:
                    thread = msg.thread
                    if thread.id not in processed_threads:
                        processed_threads.add(thread.id)
                        # Track thread entry
                        stats["threads"] += 1
                        
                        # Migrate thread messages recursively
                        thread_stats = await migrate_messages(
                            context=context,
                            source_channel_id=thread.id,
                            target_channel_id=target_channel_id,
                            thread_id=str(thread.id),
                            parent_target_id=None,
                            thread_name=thread.name,
                            processed_threads=processed_threads
                        )
                        stats["messages"] += thread_stats["messages"]
                        stats["attachments"] += thread_stats["attachments"]
                        stats["threads"] += thread_stats["threads"]
    
                        # Send End Marker
                        await context.stoat_writer.send_marker(
                            channel_id=target_channel_id,
                            content=f"> <<< END OF THREAD >>>"
                        )
                    
                if progress_callback:
                    await progress_callback(stats)
                continue
            else:
                # Use custom clean_mentions with msg mentions for accuracy
                # Use custom clean_mentions with msg mentions for accuracy
                content = clean_mentions(
                    msg.content, 
                    msg.guild, 
                    msg.mentions, 
                    msg.role_mentions, 
                    context.state.emoji_map,
                    context.state.channel_map,
                    state=context.state,
                    target_server_id=context.stoat_writer.community_id
                )
                
            # Process attachments
            files = []
            attachments_to_process = list(msg.attachments)
            
            # Check if this message is forwarded
            is_forwarded = False
            if hasattr(msg.flags, 'forwarded'):
                is_forwarded = msg.flags.forwarded
            
            if is_forwarded:
                logger.debug(f"Detected forwarded message: ID={msg.id}, Flags={msg.flags.value}")
                if hasattr(msg, 'message_snapshots') and msg.message_snapshots:
                    snapshot = msg.message_snapshots[0]
                    if not content:
                        content = snapshot.content
                        if hasattr(msg, 'guild') and msg.guild:
                            content = clean_mentions(
                                content, 
                                msg.guild, 
                                snapshot.mentions if hasattr(snapshot, 'mentions') else None,
                                snapshot.role_mentions if hasattr(snapshot, 'role_mentions') else None,
                                context.state.emoji_map,
                                context.state.channel_map,
                                state=context.state,
                                target_server_id=context.stoat_writer.community_id
                            )
                    attachments_to_process.extend(snapshot.attachments)
                    logger.debug(f"Found forwarded snapshot content: {content[:50]}... and {len(snapshot.attachments)} attachments")

            for att in attachments_to_process:
                try:
                    att_data = await context.discord_reader.download_attachment(att)
                    files.append({"filename": att.filename, "data": att_data})
                    stats["attachments"] += 1
                except Exception as e:
                    logger.error(f"Failed to download attachment {att.filename}: {e}")
            
            # Process stickers as attachments
            if hasattr(msg, 'stickers') and msg.stickers:
                for s in msg.stickers:
                    try:
                        sticker_data = await context.discord_reader.download_sticker(s)
                        if sticker_data:
                            # Use format to determine extension
                            format_val = getattr(s, 'format', 'png')
                            logger.debug(f"Sticker {getattr(s, 'name', 'unknown')} format_val type: {type(format_val)}, value: {format_val}")
                            
                            if hasattr(format_val, 'name'): # discord.py StickerFormat enum
                                ext = format_val.name.lower()
                            elif isinstance(format_val, int):
                                format_map = {1: 'png', 2: 'apng', 3: 'lottie', 4: 'gif'}
                                ext = format_map.get(format_val, 'png')
                            else:
                                ext = str(format_val).lower()
                            
                            logger.debug(f"Determined sticker extension: {ext}")
                            
                            # Stoat: Convert animated stickers to GIF
                            # Lottie (json) → GIF (via lottie lib)
                            if ext == 'lottie':
                                if HAS_LOTTIE:
                                    try:
                                        logger.debug(f"Converting Lottie sticker {s.name} to GIF...")
                                        lottie_data = json.loads(sticker_data)
                                        animation = Animation.load(lottie_data)
                                        output = io.BytesIO()
                                        export_gif(animation, output)
                                        sticker_data = output.getvalue()
                                        ext = 'gif'
                                        logger.debug(f"Successfully converted Lottie sticker {s.name} to GIF")
                                    except Exception as conv_err:
                                        logger.error(f"Failed to convert Lottie sticker {s.name} to GIF: {conv_err}")
                                        ext = 'json'
                                else:
                                    logger.warning(f"Lottie library not available, sending sticker {s.name} as raw JSON")
                                    ext = 'json'
                            
                            # APNG → GIF (via Pillow, with proper frame disposal)
                            elif ext == 'apng':
                                try:
                                    logger.debug(f"Converting APNG sticker {s.name} to GIF...")
                                    from PIL import Image
                                    img = Image.open(io.BytesIO(sticker_data))
                                    gif_buf = io.BytesIO()
                                    if getattr(img, 'n_frames', 1) > 1:
                                        # Extract each frame onto a clean background to avoid overlap
                                        frames = []
                                        durations = []
                                        for frame_idx in range(img.n_frames):
                                            img.seek(frame_idx)
                                            # Create fresh background and composite the frame
                                            canvas = Image.new('RGBA', img.size, (0, 0, 0, 0))
                                            frame = img.convert('RGBA')
                                            canvas.paste(frame, (0, 0), frame)
                                            # Convert to palette mode for GIF
                                            frames.append(canvas.convert('RGBA'))
                                            durations.append(img.info.get('duration', 100))
                                        # Save with disposal=2 (clear frame before next)
                                        frames[0].save(
                                            gif_buf, format='GIF', save_all=True,
                                            append_images=frames[1:], loop=0,
                                            duration=durations, disposal=2, transparency=0
                                        )
                                    else:
                                        img.save(gif_buf, format='GIF')
                                    sticker_data = gif_buf.getvalue()
                                    ext = 'gif'
                                    logger.debug(f"Successfully converted APNG sticker {s.name} to GIF")
                                except Exception as conv_err:
                                    logger.error(f"Failed to convert APNG sticker {s.name} to GIF: {conv_err}")
                                    # Keep original apng as fallback
                            
                            filename = f"sticker_{s.name}_{s.id}.{ext}"
                            files.append({"filename": filename, "data": sticker_data})
                            stats["attachments"] += 1
                            logger.debug(f"Added sticker {s.name} as attachment (extension: {ext})")
                    except Exception as e:
                        logger.error(f"Failed to download sticker {getattr(s, 'name', 'unknown')}: {e}")
                
            try:
                # Check if this message is a reply
                reply_to_stoat_id = None
                if msg.reference and msg.reference.message_id:
                    reply_to_stoat_id = context.state.get_target_message_id(target_channel_id, str(msg.reference.message_id))
                    if reply_to_stoat_id:
                        logger.debug(f"Detected reply to Discord ID {msg.reference.message_id} -> Stoat ID {reply_to_stoat_id}")
                    else:
                        logger.debug(f"Reply target Discord ID {msg.reference.message_id} not found in current session map.")

                # If this is the FIRST thread message and we have a parent_target_id, force it as reply to the starter
                if not reply_to_stoat_id and parent_target_id and stats["messages"] == 0:
                    reply_to_stoat_id = parent_target_id
                
                # Prepend thread marker to the first message of the thread
                if thread_name and stats["messages"] == 0:
                    content = f"> <<< THREAD: **{thread_name}** >>>\n{content}"
                
                avatar_url = str(msg.author.display_avatar.url) if msg.author.display_avatar.url else None
                if avatar_url and not avatar_url.startswith("http"):
                    avatar_url = None

                stoat_msg_id = await context.stoat_writer.send_message(
                    channel_id=target_channel_id,
                    author_name=msg.author.display_name,
                    author_avatar_url=avatar_url,
                    content=content,
                    timestamp=int(msg.created_at.timestamp()),
                    files=files if files else None,
                    reply_to_message_id=reply_to_stoat_id,
                    is_forwarded=is_forwarded,
                    embeds=msg.embeds
                )
                
                if stoat_msg_id:
                    if thread_id:
                        context.state.set_thread_message_mapping(target_channel_id, thread_id, str(msg.id), stoat_msg_id)
                    else:
                        context.state.set_message_mapping(target_channel_id, str(msg.id), stoat_msg_id)

                if thread_id:
                    context.state.update_thread_last_message_timestamp(target_channel_id, thread_id, str(msg.created_at))
                    context.state.update_thread_last_message_id(target_channel_id, thread_id, str(msg.id))
                    context.state.increment_thread_stats(target_channel_id, thread_id, messages=1, files=len(files) if files else 0)
                else:
                    context.state.update_last_message_timestamp(target_channel_id, str(msg.created_at))
                    context.state.update_last_message_id(target_channel_id, str(msg.id))
                    context.state.increment_stats(target_channel_id, messages=1, files=len(files) if files else 0)
                
                stats["messages"] += 1
                stats["last_message_content"] = content
                stats["last_message_author"] = msg.author.display_name
                
                # Check for associated thread (Normal case: parent message is migrated)
                if hasattr(msg, 'thread') and msg.thread:
                    thread = msg.thread
                    if thread.id not in processed_threads:
                        processed_threads.add(thread.id)
                        # Track thread entry
                        stats["threads"] += 1
    
                        # Migrate thread messages recursively
                        thread_stats = await migrate_messages(
                            context=context,
                            source_channel_id=thread.id,
                            target_channel_id=target_channel_id,
                            thread_id=str(thread.id),
                            parent_target_id=stoat_msg_id,
                            thread_name=thread.name,
                            processed_threads=processed_threads
                        )
                        stats["messages"] += thread_stats["messages"]
                        stats["attachments"] += thread_stats["attachments"]
                        stats["threads"] += thread_stats["threads"]
                        
                        # Send End Marker
                        await context.stoat_writer.send_marker(
                            channel_id=target_channel_id,
                            content=f"> <<< END OF THREAD >>>"
                        )
                
                # Update Link Tracking
                if not stats["first_message_url"]:
                    stats["first_message_url"] = msg.jump_url
                stats["last_message_url"] = msg.jump_url
                
                if progress_callback:
                    await progress_callback(stats)
            except Exception as e:
                # If it's a permission error, stop the entire migration
                if "MissingPermission" in str(e):
                    raise
                logger.error(f"Failed to process message {msg.id}: {e}")
                import traceback
                logger.error(traceback.format_exc())
        
        # After scanning messages, explicitly check for any missed threads (e.g. archived or skipped in scan)
        # Only do this at the top level
        if not thread_id and (after_message_id is not None or inclusive or stats["messages"] > 0):
            all_threads = await get_channel_threads(context.discord_reader, source_channel_id)
            for t in all_threads:
                if t.id not in processed_threads:
                    processed_threads.add(t.id)
                    logger.info(f"Migrating missed thread '{t.name}' (ID: {t.id})")
                    
                    stats["threads"] += 1
                    thread_stats = await migrate_messages(
                        context=context,
                        source_channel_id=t.id,
                        target_channel_id=target_channel_id,
                        thread_id=str(t.id),
                        parent_target_id=None,
                        thread_name=t.name,
                        processed_threads=processed_threads
                    )
                    stats["messages"] += thread_stats["messages"]
                    stats["attachments"] += thread_stats["attachments"]
                    stats["threads"] += thread_stats["threads"]
                    
                    await context.stoat_writer.send_marker(
                        channel_id=target_channel_id,
                        content=f"> <<< END OF THREAD >>>"
                    )

    except (KeyboardInterrupt, asyncio.CancelledError):
        context.is_running = False
        pass
        
    return stats
