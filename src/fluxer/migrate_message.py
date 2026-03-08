import asyncio
import logging
import re
from typing import Callable, Awaitable, Dict, Any

from src.core.base import MigrationContext

logger = logging.getLogger(__name__)

def clean_mentions(content: str, guild, user_mentions=None, role_mentions=None, role_map=None, emoji_map=None) -> str:
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
            
        # 4. Try provided role map
        if role_map and rid in role_map:
            return f"`@{role_map[rid]}`"
            
        return match.group(0)
        
    def replace_channel(match):
        cid = int(match.group(1))
        channel = guild.get_channel(cid)
        return f"#{channel.name}" if channel else match.group(0)

    def replace_emoji(match):
        animated = match.group(1) == "a"
        name = match.group(2)
        eid = match.group(3)
        
        if emoji_map and eid in emoji_map:
            target_eid = emoji_map[eid]
            prefix = "a" if animated else ""
            return f"<{prefix}:{name}:{target_eid}>"
        
        return f":{name}:"

    content = re.sub(r'<@!?([0-9]+)>', replace_user, content)
    content = re.sub(r'<@&([0-9]+)>', replace_role, content)
    content = re.sub(r'<#([0-9]+)>', replace_channel, content)
    content = re.sub(r'<(a?):([^:]+):([0-9]+)>', replace_emoji, content)
    content = content.replace("@everyone", "`@everyone`").replace("@here", "`@here`")
    return content


async def analyze_migration(context: MigrationContext, source_channel_id: int, after_message_id: int | None = None, progress_callback: Callable[[Dict[str, Any]], Awaitable[None]] | None = None) -> Dict[str, int]:
    """
    Scans channel history to count messages, threads, and attachments.
    """
    stats = {"messages": 0, "threads": 0, "attachments": 0}
    
    async for msg in context.discord_reader.fetch_message_history(source_channel_id, after_id=after_message_id):
        if not context.is_running:
            break
        
        # Count thread messages and markers even if parent is skipped
        if hasattr(msg, 'thread') and msg.thread:
            stats["threads"] += 1
            # Recursively count thread content
            thread_stats = await analyze_migration(context, msg.thread.id)
            stats["messages"] += thread_stats["messages"]
            stats["attachments"] += thread_stats["attachments"]
            stats["threads"] += thread_stats["threads"] # Nested threads (rare in Discord but possible in forum channels)

        # Consistent filtering with migrate_messages
        if msg.type not in [context.discord_reader.MESSAGE_TYPE_DEFAULT, context.discord_reader.MESSAGE_TYPE_REPLY, context.discord_reader.MESSAGE_TYPE_THREAD_STARTER]:
            continue

        stats["messages"] += 1
        stats["attachments"] += len(msg.attachments)

        if progress_callback and stats["messages"] % 10 == 0:
            await progress_callback(stats)

    return stats


async def migrate_messages(
    context: MigrationContext, 
    source_channel_id: int, 
    target_channel_id: str, 
    after_message_id: int | None = None, 
    progress_callback: Callable[[Dict[str, Any]], Awaitable[None]] | None = None,
    thread_id: str | None = None,
    parent_target_id: str | None = None,
    thread_name: str | None = None
) -> Dict[str, Any]:
    """Migrate messages for a specific channel and returns detailed statistics."""
    stats = {
        "messages": 0, 
        "threads": 0, 
        "attachments": 0,
        "first_message_url": "",
        "last_message_url": "",
        "last_message_content": "",
        "last_message_author": ""
    }
    
    logger.info(f"Starting message migration: Discord #{source_channel_id} -> Fluxer #{target_channel_id}")
    if after_message_id:
        logger.info(f"Resuming migration from after message ID: {after_message_id}")

    try:
        async for msg in context.discord_reader.fetch_message_history(source_channel_id, after_id=after_message_id):
            if not context.is_running:
                logger.warning("Migration interrupted by user (is_running=False)")
                break
                


            # Skip system messages like "pinned a message", etc.
            if msg.type not in [context.discord_reader.MESSAGE_TYPE_DEFAULT, context.discord_reader.MESSAGE_TYPE_REPLY, context.discord_reader.MESSAGE_TYPE_THREAD_STARTER]:
                # If we are skipping the parent, we STILL need to check for a thread!
                if hasattr(msg, 'thread') and msg.thread:
                    thread = msg.thread
                    logger.info(f"Detected thread '{thread.name}' on skipped message {msg.id}")
                    
                    # Track thread entry
                    stats["threads"] += 1
                    
                    # Migrate thread messages recursively
                    thread_stats = await migrate_messages(
                        context=context,
                        source_channel_id=thread.id,
                        target_channel_id=target_channel_id,
                        thread_id=str(thread.id),
                        parent_target_id=None,
                        thread_name=thread.name
                    )
                    stats["messages"] += thread_stats["messages"]
                    stats["attachments"] += thread_stats["attachments"]
                    stats["threads"] += thread_stats["threads"]

                    # Send End Marker
                    await context.fluxer_writer.send_marker(
                        channel_id=target_channel_id,
                        content=f"> <<< END OF THREAD >>>"
                    )
                continue
            else:
                # Use custom clean_mentions with msg mentions for accuracy
                # Use custom clean_mentions with msg mentions for accuracy
                content = clean_mentions(
                    msg.content, 
                    msg.guild, 
                    msg.mentions, 
                    msg.role_mentions, 
                    context.discord_reader.role_map,
                    context.state.emoji_map
                )
                
            # Process attachments
            files = []
            attachments_to_process = list(msg.attachments)
            
            # Check if this message is forwarded
            # Discord flags: forwarded (is bit 28 / 0x10000000)
            is_forwarded = False
            if hasattr(msg.flags, 'forwarded'):
                is_forwarded = msg.flags.forwarded
            
            # If forwarded, the content and attachments might be in message_snapshots (discord.py 2.5+)
            # Note: If content was set by thread_starter_message, we don't overwrite it.
            if is_forwarded:
                logger.debug(f"Detected forwarded message: ID={msg.id}, Flags={msg.flags.value}")
                if hasattr(msg, 'message_snapshots') and msg.message_snapshots:
                    # For now we handle the first snapshot
                    snapshot = msg.message_snapshots[0]
                    if not content: # Only update content if it wasn't already set (e.g., by thread_starter_message)
                        content = snapshot.content
                        if hasattr(msg, 'guild') and msg.guild:
                            content = clean_mentions(
                                content, 
                                msg.guild, 
                                snapshot.mentions if hasattr(snapshot, 'mentions') else None,
                                snapshot.role_mentions if hasattr(snapshot, 'role_mentions') else None,
                                context.discord_reader.role_map,
                                context.state.emoji_map
                            )
                    # Add snapshot attachments to the list to process
                    attachments_to_process.extend(snapshot.attachments)
                    logger.debug(f"Found forwarded snapshot content: {content[:50]}... and {len(snapshot.attachments)} attachments")

            for att in attachments_to_process:
                try:
                    att_data = await context.discord_reader.download_attachment(att)
                    files.append({"filename": att.filename, "data": att_data})
                    stats["attachments"] += 1
                except Exception as e:
                    logger.error(f"Failed to download attachment {att.filename}: {e}")
                
            try:
                # Check if this message is a reply
                reply_to_fluxer_id = None
                if msg.reference and msg.reference.message_id:
                    reply_to_fluxer_id = context.state.get_fluxer_message_id(target_channel_id, str(msg.reference.message_id))
                    if reply_to_fluxer_id:
                        logger.debug(f"Detected reply to Discord ID {msg.reference.message_id} -> Fluxer ID {reply_to_fluxer_id}")
                    else:
                        logger.debug(f"Reply target Discord ID {msg.reference.message_id} not found in current session map.")
                
                # If this is the FIRST thread message and we have a parent_target_id, force it as reply to the starter
                if not reply_to_fluxer_id and parent_target_id and stats["messages"] == 0:
                    reply_to_fluxer_id = parent_target_id
                
                # Prepend thread marker to the first message of the thread
                if thread_name and stats["messages"] == 0:
                    content = f"> <<< THREAD: **{thread_name}** >>>\n{content}"
                
                avatar_url = str(msg.author.display_avatar.url) if msg.author.display_avatar.url else None
                if avatar_url and not avatar_url.startswith("http"):
                    avatar_url = None

                fluxer_msg_id = await context.fluxer_writer.send_message(
                    channel_id=target_channel_id,
                    author_name=msg.author.display_name,
                    author_avatar_url=avatar_url,
                    content=content,
                    timestamp=int(msg.created_at.timestamp()),
                    files=files if files else None,
                    reply_to_message_id=reply_to_fluxer_id,
                    is_forwarded=is_forwarded,
                    embeds=msg.embeds
                )
                
                if fluxer_msg_id:
                    if thread_id:
                        context.state.set_thread_message_mapping(target_channel_id, thread_id, str(msg.id), fluxer_msg_id)
                    else:
                        context.state.set_message_mapping(target_channel_id, str(msg.id), fluxer_msg_id)

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

                # Periodic log
                if stats["messages"] % 50 == 0:
                    logger.info(f"Progress: Migrated {stats['messages']}/{total_to_process} messages in this channel.")

                # Check for associated thread (Normal case: parent message is migrated)
                if hasattr(msg, 'thread') and msg.thread:
                    thread = msg.thread
                    logger.info(f"Detected thread '{thread.name}' on message {msg.id}")
                    
                    # Track thread entry
                    stats["threads"] += 1
                    
                    # Migrate thread messages recursively
                    thread_stats = await migrate_messages(
                        context=context,
                        source_channel_id=thread.id,
                        target_channel_id=target_channel_id,
                        thread_id=str(thread.id),
                        parent_target_id=fluxer_msg_id,
                        thread_name=thread.name
                    )
                    stats["messages"] += thread_stats["messages"]
                    stats["attachments"] += thread_stats["attachments"]
                    stats["threads"] += thread_stats["threads"]
                    
                    # Send End Marker
                    await context.fluxer_writer.send_marker(
                        channel_id=target_channel_id,
                        content=f"> <<< END OF THREAD >>>"
                    )
                
                # Update Link Tracking (but prevent threaded messages from overwriting the parent channel pointers)
                # The 'after_message_id' param usually means it's the main function call and not a thread recursive call
                if not stats["first_message_url"]:
                    stats["first_message_url"] = msg.jump_url
                stats["last_message_url"] = msg.jump_url
                
                if progress_callback:
                    await progress_callback(stats)
            except Exception as e:
                logger.error(f"Failed to process message {msg.id}: {e}")
                import traceback
                logger.error(traceback.format_exc())
            
            # Delay for rate limit safety
    except (KeyboardInterrupt, asyncio.CancelledError):
        context.is_running = False
        pass
    
    return stats
