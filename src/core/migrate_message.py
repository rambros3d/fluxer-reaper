import asyncio
import logging
from typing import Callable, Awaitable, Dict

from src.core.base import MigrationContext

logger = logging.getLogger(__name__)

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
            # Recursively count thread content
            thread_stats = await analyze_migration(context, msg.thread.id)
            stats["messages"] += thread_stats["messages"]
            stats["attachments"] += thread_stats["attachments"]
            stats["threads"] += thread_stats["threads"] # Nested threads (rare in Discord but possible in forum channels)

        if progress_callback and stats["messages"] % 10 == 0:
            await progress_callback(stats["messages"])

    return stats


async def migrate_messages(context: MigrationContext, source_channel_id: int, target_channel_id: str, after_message_id: int | None = None, progress_callback: Callable[[int], Awaitable[None]] | None = None):
    """Migrate messages for a specific channel."""
    message_count = 0
    async for msg in context.discord_reader.fetch_message_history(source_channel_id, after_id=after_message_id):
        if not context.is_running:
            break
            
        # Process attachments
        files = []
        attachments_to_process = list(msg.attachments)
        
        # Check if this message is forwarded
        # Discord flags: forwarded (is bit 28 / 0x10000000)
        is_forwarded = False
        if hasattr(msg.flags, 'forwarded'):
            is_forwarded = msg.flags.forwarded
        
        # If forwarded, the content and attachments might be in message_snapshots (discord.py 2.5+)
        content = msg.content
        if is_forwarded:
            logger.debug(f"Detected forwarded message: ID={msg.id}, Flags={msg.flags.value}")
            if hasattr(msg, 'message_snapshots') and msg.message_snapshots:
                # For now we handle the first snapshot
                snapshot = msg.message_snapshots[0]
                if not content:
                    content = snapshot.content
                # Add snapshot attachments to the list to process
                attachments_to_process.extend(snapshot.attachments)
                logger.debug(f"Found forwarded snapshot content: {content[:50]}... and {len(snapshot.attachments)} attachments")

        for att in attachments_to_process:
            try:
                att_data = await context.discord_reader.download_attachment(att)
                files.append({"filename": att.filename, "data": att_data})
            except Exception as e:
                logger.error(f"Failed to download attachment {att.filename}: {e}")
            
        try:
            # Check if this message is a reply
            reply_to_fluxer_id = None
            if msg.reference and msg.reference.message_id:
                reply_to_fluxer_id = context.state.get_fluxer_message_id(str(msg.reference.message_id))
            
            fluxer_msg_id = await context.fluxer_writer.send_message(
                channel_id=target_channel_id,
                author_name=msg.author.display_name,
                author_avatar_url=str(msg.author.display_avatar.url),
                content=content,
                timestamp=msg.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                files=files if files else None,
                reply_to_message_id=reply_to_fluxer_id,
                is_forwarded=is_forwarded
            )
            
            if fluxer_msg_id:
                context.state.set_message_mapping(str(msg.id), fluxer_msg_id)

            # Check for associated thread
            if hasattr(msg, 'thread') and msg.thread:
                thread = msg.thread
                logger.info(f"Detected thread '{thread.name}' on message {msg.id}")
                
                # Send Start Marker
                await context.fluxer_writer.send_marker(
                    channel_id=target_channel_id,
                    content=f"> <<< THREAD: **{thread.name}** >>>"
                )
                
                # Migrate thread messages
                # We don't pass a progress callback here to avoid confusing the UI
                # but we do want to track count if possible.
                await migrate_messages(
                    context=context,
                    source_channel_id=thread.id,
                    target_channel_id=target_channel_id
                )
                
                # Send End Marker
                await context.fluxer_writer.send_marker(
                    channel_id=target_channel_id,
                    content=f"> <<< END OF THREAD >>>"
                )

            context.state.update_last_message_timestamp(str(source_channel_id), str(msg.created_at))
            message_count += 1
            if progress_callback:
                await progress_callback(message_count)
        except Exception as e:
            logger.error(f"Failed to process message {msg.id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        # Delay for rate limit safety
        await asyncio.sleep(context.config.migration.rate_limit_delay_seconds)
        
    return message_count
