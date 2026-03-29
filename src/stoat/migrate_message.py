import asyncio
import logging
import re
import json
import io
from typing import Callable, Awaitable, Dict, Any, List
from pathlib import Path

try:
    from lottie.objects import Animation
    from lottie.exporters.gif import export_gif
    HAS_LOTTIE = True
except ImportError:
    HAS_LOTTIE = False

from src.core.base import MigrationContext
from src.core.utils import resolve_discord_links

logger = logging.getLogger(__name__)

def clean_mentions(content: str, guild, user_mentions=None, role_mentions=None, channel_mentions=None, emoji_map=None, channel_map=None, state=None, target_server_id=None, channel_names=None, anonymize_users=False) -> str:
    if content is None:
        return ""
    if not content or not guild:
        return content
        
    def replace_user(match):
        uid = int(match.group(1))
        if anonymize_users and state:
            alias = state.get_user_alias(str(uid))
            return f"`@{alias}`" if alias else "`@Anonymized User`"
        
        # 1. Try provided guild
        member = guild.get_member(uid)
        if member:
            return f"`@{member.display_name}`"
        
        # 2. Try provided user_mentions
        if user_mentions:
            m = next((u for u in user_mentions if u.id == uid), None)
            if m:
                return f"`@{m.display_name}`"
        
        # 3. Try global cache via guild.client
        if hasattr(guild, 'client'):
            user = guild.client.get_user(uid)
            if user:
                return f"`@{user.name}`"
        return "`@Unknown User`"
        
    def replace_role(match):
        rid = int(match.group(1))
        # Stoat does not support migrating Discord Role mentions natively, always use the name as fallback

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
            
        return f"`@Unknown Role`"
        
    def replace_channel(match):
        cid = int(match.group(1))
        
        # 1. Check if channel is mapped in state
        if channel_map and str(cid) in channel_map:
            return f"<#{channel_map[str(cid)]}>"
            
        # 2. Try to resolve channel name from pre-fetched names
        name = None
        if channel_names and str(cid) in channel_names:
            name = channel_names[str(cid)]
            
        # 3. Try live lookup (fallback)
        if not name:
            try:
                channel = guild.get_channel(cid) or guild.get_thread(cid)
            except Exception:
                channel = None
            if not channel and channel_mentions:
                channel = next((c for c in channel_mentions if c.id == cid), None)
            if channel:
                name = channel.name

        if name:
            return f"`#{name}`"
            
        return f"<#{cid}>"

    def replace_emoji(match):
        animated = match.group(1) == "a"
        name = match.group(2)
        eid = match.group(3)
        
        if emoji_map and eid in emoji_map:
            target_eid = emoji_map[eid]
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
                
                # Fetch last migrated message ID for this thread
                target_channel_id = context.state.get_target_channel_id(str(source_channel_id))
                thread_after_id = None
                if target_channel_id:
                    thread_after_id = context.state.get_thread_last_message_id(target_channel_id, str(thread.id))
                
                thread_stats = await analyze_migration(context, thread.id, after_message_id=int(thread_after_id) if thread_after_id else None, processed_threads=processed_threads)
                stats["messages"] += thread_stats["messages"]
                stats["attachments"] += thread_stats["attachments"]
                stats["threads"] += thread_stats["threads"]

        # Consistent filtering with migrate_messages
        if msg.type not in [
            context.discord_reader.MESSAGE_TYPE_DEFAULT,
            context.discord_reader.MESSAGE_TYPE_REPLY,
            context.discord_reader.MESSAGE_TYPE_THREAD_STARTER,
            context.discord_reader.MESSAGE_TYPE_FORWARD,
            context.discord_reader.MESSAGE_TYPE_CHAT_INPUT_COMMAND,
            context.discord_reader.MESSAGE_TYPE_CONTEXT_MENU_COMMAND,
            context.discord_reader.MESSAGE_TYPE_POLL_RESULT,
            context.discord_reader.MESSAGE_TYPE_AUTO_MODERATION_ACTION
        ]:
            logger.debug(f"Skipping message {msg.id} in analyze: type={msg.type} (not an allowed type)")
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
                
                # Fetch last migrated message ID for this thread
                target_channel_id = context.state.get_target_channel_id(str(source_channel_id))
                thread_after_id = None
                if target_channel_id:
                    thread_after_id = context.state.get_thread_last_message_id(target_channel_id, str(t.id))

                thread_stats = await analyze_migration(context, t.id, after_message_id=int(thread_after_id) if thread_after_id else None, processed_threads=processed_threads)
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
        logger.info(f"Starting migration of {source_channel_id} (inclusive={inclusive})...")
    
    # Pre-fetch channel and thread names for better mention resolution
    if not hasattr(context, 'channel_names'):
        context.channel_names = {}
        try:
            logger.debug(f"Pre-fetching channel and thread names for guild {context.discord_reader.guild.id}...")
            all_channels = await context.discord_reader.fetch_channels()
            for c in all_channels:
                context.channel_names[str(c.id)] = c.name
            
            threads = await context.discord_reader.get_active_threads()
            for t in threads:
                context.channel_names[str(t.id)] = t.name
            
            logger.debug(f"Pre-fetched {len(context.channel_names)} names.")
        except Exception as e:
            logger.debug(f"Failed to pre-fetch channel names: {e}")

    # Process missed threads first if resuming
    if processed_threads is None:
        processed_threads = set()

    async def _process_missed_threads():
        """Helper to scan for threads not yet processed in the current scan."""
        if not context.is_running:
            return
        logger.info(f"Checking for missed or pending threads in channel {source_channel_id}...")
        all_threads = await get_channel_threads(context.discord_reader, source_channel_id)
        for t in all_threads:
            if not context.is_running:
                break
            if t.id not in processed_threads:
                processed_threads.add(t.id)
                
                # Skip if thread was already fully migrated in a previous run
                if context.state.is_thread_completed(target_channel_id, str(t.id)):
                    logger.debug(f"Skipping already completed thread '{t.name}' (ID: {t.id})")
                    continue

                logger.info(f"Checking missed thread '{t.name}' (ID: {t.id})")
                
                # Fetch last migrated message ID for this thread
                thread_after_id = context.state.get_thread_last_message_id(target_channel_id, str(t.id))
                if thread_after_id:
                    logger.info(f"Resuming missed/pending thread '{t.name}' from after message ID: {thread_after_id}")

                stats["threads"] += 1
                thread_stats = await migrate_messages(
                    context=context,
                    source_channel_id=t.id,
                    target_channel_id=target_channel_id,
                    after_message_id=int(thread_after_id) if thread_after_id else None,
                    thread_id=str(t.id),
                    parent_target_id=None,
                    thread_name=t.name,
                    processed_threads=processed_threads
                )
                stats["messages"] += thread_stats["messages"]
                stats["attachments"] += thread_stats["attachments"]
                stats["threads"] += thread_stats["threads"]
                
                if context.is_running:
                    await context.stoat_writer.send_marker(
                        channel_id=target_channel_id,
                        content=f"> <<< END OF THREAD >>>"
                    )

    try:
        # If resuming (after_message_id is set) and at top level, check for pending threads FIRST
        # to preserve chronological order (finish old unfinished business first)
        if not thread_id and after_message_id is not None:
            await _process_missed_threads()
        async for msg in context.discord_reader.fetch_message_history(source_channel_id, after_id=after_message_id, inclusive=inclusive):
            if not context.is_running:
                logger.warning("Migration interrupted by user (is_running=False)")
                break
                


            # Skip system messages like "pinned a message", etc.
            content = "" # Initialize content
            logger.debug(f"Analyzing message {msg.id}: type={msg.type}, content_len={len(msg.content) if msg.content else 0}, attachments={len(msg.attachments)}, embeds={len(msg.embeds)}")
            if msg.type not in [
                context.discord_reader.MESSAGE_TYPE_DEFAULT,
                context.discord_reader.MESSAGE_TYPE_REPLY,
                context.discord_reader.MESSAGE_TYPE_THREAD_STARTER,
                context.discord_reader.MESSAGE_TYPE_FORWARD,
                context.discord_reader.MESSAGE_TYPE_CHAT_INPUT_COMMAND,
                context.discord_reader.MESSAGE_TYPE_CONTEXT_MENU_COMMAND,
                context.discord_reader.MESSAGE_TYPE_POLL_RESULT,
                context.discord_reader.MESSAGE_TYPE_AUTO_MODERATION_ACTION
            ]:
                # If we are skipping the parent, we STILL need to check for a thread!
                if hasattr(msg, 'thread') and msg.thread:
                    thread = msg.thread
                    if thread.id not in processed_threads:
                        processed_threads.add(thread.id)
                        # Track thread entry
                        stats["threads"] += 1
                        
                        # Fetch last migrated message ID for this thread
                        thread_after_id = context.state.get_thread_last_message_id(target_channel_id, str(thread.id))
                        if thread_after_id:
                            logger.info(f"Resuming thread '{thread.name}' from after message ID: {thread_after_id}")

                        # Migrate thread messages recursively
                        thread_stats = await migrate_messages(
                            context=context,
                            source_channel_id=thread.id,
                            target_channel_id=target_channel_id,
                            after_message_id=int(thread_after_id) if thread_after_id else None,
                            thread_id=str(thread.id),
                            parent_target_id=None,
                            thread_name=thread.name,
                            processed_threads=processed_threads
                        )
                        stats["messages"] += thread_stats["messages"]
                        stats["attachments"] += thread_stats["attachments"]
                        stats["threads"] += thread_stats["threads"]
    
                        # Send End Marker
                        if context.is_running:
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
                    context.discord_reader.guild, 
                    msg.mentions, 
                    msg.role_mentions, 
                    msg.channel_mentions,
                    context.state.emoji_map,
                    context.state.channel_map,
                    state=context.state,
                    target_server_id=context.stoat_writer.community_id,
                    channel_names=context.channel_names if hasattr(context, 'channel_names') else None,
                    anonymize_users=context.config.anonymize_users
                )
                logger.debug(f"Message {msg.id} cleaned content length: {len(content) if content else 0}")
                
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
                        if context.discord_reader.guild:
                            content = clean_mentions(
                                content, 
                                context.discord_reader.guild, 
                                snapshot.mentions if hasattr(snapshot, 'mentions') else None,
                                snapshot.role_mentions if hasattr(snapshot, 'role_mentions') else None,
                                snapshot.channel_mentions if hasattr(snapshot, 'channel_mentions') else None,
                                context.state.emoji_map,
                                context.state.channel_map,
                                state=context.state,
                                target_server_id=context.stoat_writer.community_id,
                                channel_names=context.channel_names if hasattr(context, 'channel_names') else None,
                                anonymize_users=context.config.anonymize_users
                            )
                    attachments_to_process.extend(snapshot.attachments)
                    logger.debug(f"Found forwarded snapshot content: {content[:50]}... and {len(snapshot.attachments)} attachments")

            for att in attachments_to_process:
                try:
                    att_data = await context.discord_reader.download_attachment(att)
                    files.append({
                        "filename": att.filename, 
                        "data": att_data,
                        "content_type": getattr(att, "content_type", None)
                    })
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
                                        
                                        def _convert_lottie_to_gif(data):
                                            animation = Animation.load(data)
                                            output = io.BytesIO()
                                            export_gif(animation, output)
                                            return output.getvalue()
                                            
                                        sticker_data = await asyncio.to_thread(_convert_lottie_to_gif, lottie_data)
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
                                    logger.debug(f"Converting APNG sticker {s.name} (ID: {s.id}) to GIF for Stoat...")
                                    from PIL import Image
                                    
                                    def _convert_apng_to_gif(data):
                                        img = Image.open(io.BytesIO(data))
                                        gif_buf = io.BytesIO()
                                        if getattr(img, 'n_frames', 1) > 1:
                                            frames = []
                                            durations = []
                                            # Create a RGBA canvas for disposal handling
                                            for i in range(img.n_frames):
                                                img.seek(i)
                                                frame = img.convert('RGBA')
                                                # Use a fresh canvas for each frame to avoid ghosting/stacking
                                                # For stickers, we want each GIF frame to be independent
                                                current_frame = Image.new('RGBA', img.size, (0,0,0,0))
                                                current_frame.paste(frame, (0, 0))
                                                frames.append(current_frame)
                                                durations.append(img.info.get('duration', 100))
                                            frames[0].save(
                                                gif_buf, format='GIF', save_all=True,
                                                append_images=frames[1:], loop=0,
                                                duration=durations, disposal=2, transparency=0
                                            )
                                        else:
                                            img.save(gif_buf, format='GIF')
                                        return gif_buf.getvalue()

                                    sticker_data = await asyncio.to_thread(_convert_apng_to_gif, sticker_data)
                                    ext = 'gif'
                                    logger.debug(f"Successfully converted APNG sticker {s.name} to GIF")
                                except Exception as conv_err:
                                    logger.error(f"Failed to convert APNG sticker {s.name} to GIF: {conv_err}")
                                    # Keep original apng as fallback
                            
                            filename = f"sticker_{s.name}_{s.id}.{ext}"
                            files.append({
                                "filename": filename, 
                                "data": sticker_data,
                                "content_type": f"image/{ext}" if ext != "json" else "application/json"
                            })
                            stats["attachments"] += 1
                            logger.debug(f"Added sticker {s.name} as attachment (extension: {ext})")
                    except Exception as e:
                        logger.error(f"Failed to download sticker {getattr(s, 'name', 'unknown')}: {e}")
                
            try:
                # Check for existing mapping to avoid duplicates when resuming
                if context.state.get_target_message_id(target_channel_id, str(msg.id)):
                    continue

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
                
                # Always ensure alias is created/retrieved to populate user_alias table
                alias = context.state.get_user_alias(str(msg.author.id))
                
                anonymize_users = context.config.anonymize_users if hasattr(context, 'config') else False
                if anonymize_users:
                    author_name = alias or "Anonymized User"
                    author_avatar_url = None
                else:
                    author_name = msg.author.display_name
                    author_avatar_url = str(msg.author.display_avatar.url) if msg.author.display_avatar.url else None
                    if author_avatar_url and not author_avatar_url.startswith("http"):
                        author_avatar_url = None

                logger.debug(f"Stoat: Calling send_message for Discord ID {msg.id}")
                stoat_msg_id = await context.stoat_writer.send_message(
                    channel_id=target_channel_id,
                    author_name=author_name,
                    author_avatar_url=author_avatar_url,
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
                        
                        # Fetch last migrated message ID for this thread
                        thread_after_id = context.state.get_thread_last_message_id(target_channel_id, str(thread.id))
                        if thread_after_id:
                            logger.info(f"Resuming thread '{thread.name}' from after message ID: {thread_after_id}")

                        # Migrate thread messages recursively
                        thread_stats = await migrate_messages(
                            context=context,
                            source_channel_id=thread.id,
                            target_channel_id=target_channel_id,
                            after_message_id=int(thread_after_id) if thread_after_id else None,
                            thread_id=str(thread.id),
                            parent_target_id=stoat_msg_id,
                            thread_name=thread.name,
                            processed_threads=processed_threads
                        )
                        stats["messages"] += thread_stats["messages"]
                        stats["attachments"] += thread_stats["attachments"]
                        stats["threads"] += thread_stats["threads"]
                        
                        # Send End Marker
                        if context.is_running:
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
        
        # Mark thread as completed if we finished the loop without being interrupted
        if thread_id and context.is_running:
            context.state.update_thread_completed(target_channel_id, thread_id, completed=True)
            logger.info(f"Thread '{thread_name}' (ID: {thread_id}) marked as completed.")
            

    except (KeyboardInterrupt, asyncio.CancelledError):
        context.is_running = False
        pass
        
    return stats


async def analyze_global_migration(context: MigrationContext, after_message_id: int | None = None, inclusive: bool = False, progress_callback: Callable[[Dict[str, Any]], Awaitable[None]] | None = None) -> Dict[str, int]:
    """
    Scans the entire server history to count messages, threads, and attachments globally.
    """
    stats = {"messages": 0, "threads": 0, "attachments": 0}
    
    # Fetch global progress map to skip migrated messages efficiently
    progress_map = context.state.get_all_last_message_ids()

    async for msg in context.discord_reader.fetch_global_message_history(after_id=after_message_id):
        if not context.is_running:
            break
            
        # Determine target channel to check for existing mapping
        if not msg.channel:
            continue
            
        target_channel_id = context.state.get_target_channel_id(str(msg.channel.id))
        if not target_channel_id:
            continue

        # Efficient skip: if message ID is <= last migrated ID for this channel/thread
        # This is the primary resume mechanism: wait until we pass the last migrated ID for this channel
        last_id = progress_map.get(str(msg.channel.id))
        if last_id and msg.id <= int(last_id):
            continue

        if msg.type not in [
            context.discord_reader.MESSAGE_TYPE_DEFAULT,
            context.discord_reader.MESSAGE_TYPE_REPLY,
            context.discord_reader.MESSAGE_TYPE_THREAD_STARTER,
            context.discord_reader.MESSAGE_TYPE_FORWARD,
            context.discord_reader.MESSAGE_TYPE_CHAT_INPUT_COMMAND,
            context.discord_reader.MESSAGE_TYPE_CONTEXT_MENU_COMMAND,
            context.discord_reader.MESSAGE_TYPE_POLL_RESULT,
            context.discord_reader.MESSAGE_TYPE_AUTO_MODERATION_ACTION
        ]:
            continue
            
        stats["messages"] += 1
        stats["attachments"] += len(msg.attachments)
        if hasattr(msg, 'thread') and msg.thread:
            stats["threads"] += 1
            
        if progress_callback and stats["messages"] % 100 == 0:
            await progress_callback(stats)
            
    if progress_callback:
        await progress_callback(stats)
        
    return stats


async def migrate_global_messages(
    context: MigrationContext,
    after_message_id: int | None = None,
    inclusive: bool = False,
    progress_callback: Callable[[Dict[str, Any]], Awaitable[None]] | None = None
) -> Dict[str, Any]:
    """
    Migrates messages across all channels chronologically to Stoat.
    """
    stats = {
        "messages": 0,
        "threads": 0,
        "attachments": 0,
        "last_message_content": "",
        "last_message_author": "",
        "first_message_url": None,
        "last_message_url": None
    }
    
    processed_threads = set()
    logger.info("Starting Global Waterfall Migration for Stoat...")
    
    emoji_map = context.state.emoji_map
    db_media = context.discord_reader.db.get_all_media() if context.discord_reader.db else {}
    # Fetch global progress map to skip migrated messages efficiently
    progress_map = context.state.get_all_last_message_ids()
    
    try:
        async for msg in context.discord_reader.fetch_global_message_history(after_id=after_message_id):
            if not context.is_running:
                logger.warning("Global migration interrupted by user")
                break
                
            if msg.type not in [
                context.discord_reader.MESSAGE_TYPE_DEFAULT,
                context.discord_reader.MESSAGE_TYPE_REPLY,
                context.discord_reader.MESSAGE_TYPE_THREAD_STARTER,
                context.discord_reader.MESSAGE_TYPE_FORWARD,
                context.discord_reader.MESSAGE_TYPE_CHAT_INPUT_COMMAND,
                context.discord_reader.MESSAGE_TYPE_CONTEXT_MENU_COMMAND,
                context.discord_reader.MESSAGE_TYPE_POLL_RESULT,
                context.discord_reader.MESSAGE_TYPE_AUTO_MODERATION_ACTION
            ]:
                continue
                
            # Determine target channel
            if not msg.channel:
                continue
                
            target_channel_id = context.state.get_target_channel_id(str(msg.channel.id))
            if not target_channel_id:
                continue

            # Efficient skip: if message ID is <= last migrated ID for this channel/thread
            # This ensures we only resume a channel once we reach its last known progress point
            last_id = progress_map.get(str(target_channel_id))
            if last_id and msg.id <= int(last_id):
                continue
                
            parent_target_id = None
            if hasattr(msg, 'thread') and msg.thread and msg.id == msg.thread.id:
                processed_threads.add(msg.thread.id)
                stats["threads"] += 1
            elif msg.channel.type in [11, 12]:
                pass

            # Formatting
            files = []
            
            # Always ensure alias is created/retrieved to populate user_alias table
            alias = context.state.get_user_alias(str(msg.author.id))
            
            anonymize_users = context.config.anonymize_users if hasattr(context, 'config') else False
            
            if anonymize_users:
                author_name = alias or "Anonymized User"
                author_avatar_url = None
            else:
                author_name = msg.author.display_name
                author_avatar_url = msg.author.avatar.url if hasattr(msg.author, 'avatar') and msg.author.avatar else None

            for att in msg.attachments:
                media_info = db_media.get(att.local_hash) if db_media else None
                local_path = None
                if media_info:
                    local_path = Path(media_info["local_path"])

                if local_path and local_path.exists():
                    try:
                        with open(local_path, "rb") as f:
                            files.append({"filename": att.filename, "data": f.read()})
                    except Exception as fe:
                        logger.error(f"Failed to read file {local_path}: {fe}")

            content = msg.content or ""
            
            for sticker in msg.stickers:
                sticker_name = getattr(sticker, "name", "unknown")
                s_hash = getattr(sticker, "local_hash", None)
                sticker_file = None
                s_media = db_media.get(s_hash) if db_media and s_hash else None
                if s_media:
                    s_path = Path(s_media["local_path"])
                    if s_path.exists():
                        sticker_file = s_path
                
                content += f"\n[Sticker: {sticker_name}]"
                if sticker_file:
                    try:
                        with open(sticker_file, "rb") as f:
                            files.append({
                                "filename": f"sticker_{sticker_name}.png",
                                "data": f.read(),
                                "content_type": "image/png"
                            })
                    except Exception as e:
                        logger.error(f"Failed to read sticker file {sticker_file}: {e}")

            content = clean_mentions(
                content=content,
                guild=context.discord_reader.guild,
                user_mentions=msg.mentions,
                role_mentions=msg.role_mentions,
                channel_mentions=msg.channel_mentions,
                emoji_map=emoji_map,
                channel_map=context.state.channel_map,
                state=context.state,
                target_server_id=target_server_id,
                channel_names=context.channel_names if hasattr(context, 'channel_names') else None,
                anonymize_users=anonymize_users
            )

            if not content and not files:
                logger.debug(f"Message {msg.id} empty after processing, skipping.")
                continue
                
            timestamp_int = int(msg.created_at.timestamp())

            if msg.reference and msg.reference.message_id:
                # Resolve the author of the message being replied to
                source_ref_msg = await context.discord_reader.get_message(msg.channel_id, msg.reference.message_id)
                if source_ref_msg and source_ref_msg.author:
                    ref_author_id = str(source_ref_msg.author.id)
                    if anonymize_users:
                        ref_name = context.state.get_user_alias(ref_author_id) or "Anonymized User"
                    else:
                        ref_name = source_ref_msg.author.display_name
                    content = f"`@{ref_name}`\n{content}"
                else:
                    tgt_reply = context.state.get_target_message_id(target_channel_id, msg.reference.message_id)
                    if tgt_reply:
                        content = f"[Reply to {tgt_reply}]\n{content}"

            try:
                stoat_msg_id = await context.stoat_writer.send_message(
                    channel_id=target_channel_id,
                    author_name=author_name,
                    author_avatar_url=author_avatar_url,
                    content=content,
                    files=files,
                    timestamp=timestamp_int,
                    embeds=msg.embeds
                )
                
                if stoat_msg_id:
                    context.state.set_target_message_mapping(target_channel_id, msg.id, stoat_msg_id)
                    context.state.update_last_message_id(target_channel_id, msg.id)
                    context.state.update_last_message_timestamp(target_channel_id, str(msg.created_at))
                    context.state.increment_stats(target_channel_id, messages=1, files=len(files) if files else 0)
                    stats["attachments"] += len(files) if files else 0

                stats["messages"] += 1
                stats["last_message_content"] = content
                stats["last_message_author"] = author_name

                if not stats["first_message_url"]:
                    stats["first_message_url"] = msg.jump_url
                stats["last_message_url"] = msg.jump_url
                
                if progress_callback:
                    await progress_callback(stats)
                    
            except Exception as e:
                logger.error(f"Failed to process global message {msg.id}: {e}")
                
    except (KeyboardInterrupt, asyncio.CancelledError):
        context.is_running = False
        pass
        
    return stats
