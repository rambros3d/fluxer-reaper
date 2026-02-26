import asyncio
import discord
import logging
import re
from typing import Callable, Awaitable, Dict, Any

from src.core.base import MigrationContext

logger = logging.getLogger(__name__)

def clean_mentions(content: str, guild) -> str:
    if not content or not guild:
        return content
        
    def replace_user(match):
        uid = int(match.group(1))
        member = guild.get_member(uid)
        return f"@{member.display_name}" if member else match.group(0)
        
    def replace_role(match):
        rid = int(match.group(1))
        role = guild.get_role(rid)
        return f"@{role.name}" if role else match.group(0)
        
    def replace_channel(match):
        cid = int(match.group(1))
        channel = guild.get_channel(cid)
        return f"#{channel.name}" if channel else match.group(0)

    content = re.sub(r'<@!?([0-9]+)>', replace_user, content)
    content = re.sub(r'<@&([0-9]+)>', replace_role, content)
    content = re.sub(r'<#([0-9]+)>', replace_channel, content)
    return content


async def analyze_migration(context: MigrationContext, source_channel_id: int, after_message_id: int | None = None, progress_callback: Callable[[int], Awaitable[None]] | None = None) -> Dict[str, int]:
    """
    Scans channel history to count messages, threads, and attachments.
    """
    stats = {"messages": 0, "threads": 0, "attachments": 0}
    
    async for msg in context.discord_reader.fetch_message_history(source_channel_id, after_id=after_message_id):
        if not context.is_running:
            break
        
        stats["messages"] += 1
        stats["attachments"] += len(msg.attachments)
        
        # Count thread messages and markers
        if hasattr(msg, 'thread') and msg.thread:
            stats["threads"] += 1
            thread_stats = await analyze_migration(context, msg.thread.id)
            stats["messages"] += thread_stats["messages"]
            stats["attachments"] += thread_stats["attachments"]
            stats["threads"] += thread_stats["threads"]

        if progress_callback and stats["messages"] % 10 == 0:
            await progress_callback(stats["messages"])

    return stats


async def migrate_messages(context: MigrationContext, source_channel_id: int, target_channel_id: str, after_message_id: int | None = None, progress_callback: Callable[[int], Awaitable[None]] | None = None) -> Dict[str, Any]:
    """Migrate messages for a specific channel using Stoat masquerade for author impersonation."""
    stats = {
        "messages": 0,
        "attachments": 0,
        "threads": 0,
        "first_message_url": None,
        "last_message_url": None
    }
    try:
        async for msg in context.discord_reader.fetch_message_history(source_channel_id, after_id=after_message_id):
            if not context.is_running:
                break
                
            # Skip system messages like "pinned a message", etc.
            # We treat thread_starter_message (type 21) as our thread marker.
            content = "" # Initialize content
            if msg.type == discord.MessageType.thread_starter_message:
                content = f"> <<< THREAD: **{msg.channel.name}** >>>"
            elif msg.type not in [discord.MessageType.default, discord.MessageType.reply]:
                continue
            else:
                # Get clean content
                content = msg.clean_content
                
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
                            content = clean_mentions(content, msg.guild)
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
                    reply_to_stoat_id = context.state.get_target_message_id(str(msg.reference.message_id))
                
                stoat_msg_id = await context.stoat_writer.send_message(
                    channel_id=target_channel_id,
                    author_name=msg.author.display_name,
                    author_avatar_url=str(msg.author.display_avatar.url),
                    content=content,
                    timestamp=msg.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    files=files if files else None,
                    reply_to_message_id=reply_to_stoat_id,
                    is_forwarded=is_forwarded
                )
                
                if stoat_msg_id:
                    context.state.set_message_mapping(str(msg.id), stoat_msg_id)

                # Check for associated thread
                if hasattr(msg, 'thread') and msg.thread:
                    thread = msg.thread
                    logger.info(f"Detected thread '{thread.name}' on message {msg.id}")
                    
                    # Migrate thread messages recursively
                    thread_stats = await migrate_messages(
                        context=context,
                        source_channel_id=thread.id,
                        target_channel_id=target_channel_id
                    )
                    stats["messages"] += thread_stats["messages"]
                    stats["attachments"] += thread_stats["attachments"]
                    stats["threads"] += thread_stats["threads"]
                    
                    # Send End Marker
                    await context.stoat_writer.send_marker(
                        channel_id=target_channel_id,
                        content=f"> <<< END OF THREAD >>>"
                    )

                context.state.update_last_message_timestamp(str(source_channel_id), str(msg.created_at))
                context.state.update_last_message_id(str(source_channel_id), str(msg.id))
                stats["messages"] += 1
                
                # Update Link Tracking
                if not stats["first_message_url"]:
                    stats["first_message_url"] = msg.jump_url
                stats["last_message_url"] = msg.jump_url
                
                if progress_callback:
                    await progress_callback(stats["messages"])
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
