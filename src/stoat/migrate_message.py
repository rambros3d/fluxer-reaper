import asyncio
import logging
import re
from typing import Callable, Awaitable, Dict, Any

from src.core.base import MigrationContext

logger = logging.getLogger(__name__)

def clean_mentions(content: str, guild, user_mentions=None, role_mentions=None, role_map=None) -> str:
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

    content = re.sub(r'<@!?([0-9]+)>', replace_user, content)
    content = re.sub(r'<@&([0-9]+)>', replace_role, content)
    content = re.sub(r'<#([0-9]+)>', replace_channel, content)
    content = content.replace("@everyone", "`@everyone`").replace("@here", "`@here`")
    return content


async def analyze_migration(context: MigrationContext, source_channel_id: int, after_message_id: int | None = None, progress_callback: Callable[[Dict[str, Any]], Awaitable[None]] | None = None) -> Dict[str, int]:
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
    
    async for msg in context.discord_reader.fetch_message_history(source_channel_id, after_id=after_message_id):
        if not context.is_running:
            break
        
        # Count thread messages and markers even if parent is skipped
        if hasattr(msg, 'thread') and msg.thread:
            stats["threads"] += 1
            thread_stats = await analyze_migration(context, msg.thread.id)
            stats["messages"] += thread_stats["messages"]
            stats["attachments"] += thread_stats["attachments"]
            stats["threads"] += thread_stats["threads"]

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
    thread_id: str | None = None
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
        
    try:
        async for msg in context.discord_reader.fetch_message_history(source_channel_id, after_id=after_message_id):
            if not context.is_running:
                logger.warning("Migration interrupted by user (is_running=False)")
                break
                


            # Skip system messages like "pinned a message", etc.
            # We treat thread_starter_message (type 21) as our thread marker.
            content = "" # Initialize content
            if msg.type == context.discord_reader.MESSAGE_TYPE_THREAD_STARTER:
                content = f"> <<< THREAD: **{msg.channel.name}** >>>"
                # If it's a thread starter and we already processed the thread at the top,
                # we might be double-posting. But we want it as a marker.
            elif msg.type not in [context.discord_reader.MESSAGE_TYPE_DEFAULT, context.discord_reader.MESSAGE_TYPE_REPLY]:
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
                        thread_id=str(thread.id)
                    )
                    stats["messages"] += thread_stats["messages"]
                    stats["attachments"] += thread_stats["attachments"]
                    stats["threads"] += thread_stats["threads"]

                    # Send End Marker
                    await context.stoat_writer.send_marker(
                        channel_id=target_channel_id,
                        content=f"> <<< END OF THREAD >>>"
                    )
                continue
            else:
                # Use custom clean_mentions with msg mentions for accuracy
                content = clean_mentions(msg.content, msg.guild, msg.mentions, msg.role_mentions, context.discord_reader.role_map)
                
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
                                context.discord_reader.role_map
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
                
            try:
                # Check if this message is a reply
                reply_to_stoat_id = None
                if msg.reference and msg.reference.message_id:
                    reply_to_stoat_id = context.state.get_target_message_id(target_channel_id, str(msg.reference.message_id))
                    if reply_to_stoat_id:
                        logger.debug(f"Detected reply to Discord ID {msg.reference.message_id} -> Stoat ID {reply_to_stoat_id}")
                    else:
                        logger.debug(f"Reply target Discord ID {msg.reference.message_id} not found in current session map.")
                
                avatar_url = str(msg.author.display_avatar.url) if msg.author.display_avatar.url else None
                if avatar_url and not avatar_url.startswith("http"):
                    avatar_url = None

                stoat_msg_id = await context.stoat_writer.send_message(
                    channel_id=target_channel_id,
                    author_name=msg.author.display_name,
                    author_avatar_url=avatar_url,
                    content=content,
                    timestamp=msg.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    files=files if files else None,
                    reply_to_message_id=reply_to_stoat_id,
                    is_forwarded=is_forwarded
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
                
                # Periodic log
                if stats["messages"] % 50 == 0:
                    logger.info(f"Progress: Migrated {stats['messages']} messages in this channel.")

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
                        thread_id=str(thread.id)
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
            
            # Delay for rate limit safety
            await asyncio.sleep(context.config.migration.rate_limit_delay_seconds)
    except (KeyboardInterrupt, asyncio.CancelledError):
        context.is_running = False
        pass
        
    return stats
