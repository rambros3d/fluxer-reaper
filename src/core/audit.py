import logging
from src.core.base import MigrationContext

logger = logging.getLogger(__name__)

async def log_audit_event(context: MigrationContext, title: str, description: str, files: list[dict] | None = None) -> None:
    """
    Logs an event by sending a summary to the `#reaper-logs` audit channel.
    If the channel does not exist, it will dynamically create it.
    """
    # 1. Initialize or Validate channel
    channel_id = context.state.audit_log_channel
    
    if channel_id:
        try:
            channels = await context.writer.get_channels()
            channel_exists = any(str(ch.get("id")) == str(channel_id) for ch in channels)
            if not channel_exists:
                context.state.audit_log_channel = None
        except Exception as e:
            logger.warning(f"Failed to verify audit channel existence: {e}")

    if not context.state.audit_log_channel:
        try:
            channels = await context.writer.get_channels()
            channel_id = None
            
            for ch in channels:
                name = str(ch.get("name", "")).lower()
                if name in ["reaper-logs", "reaper_logs"]:
                    channel_id = str(ch.get("id"))
                    break
            
            if not channel_id:
                channel_id = await context.writer.create_channel(
                    name="reaper-logs",
                    topic="Discord Reaper - Migration audit logs.",
                    type=0
                )
            
            context.state.audit_log_channel = channel_id
            
            # For Fluxer, we still do the lockdown as it's proven to work
            if context.target_platform == "fluxer":
                await context.writer.set_channel_permission(
                    channel_id=channel_id,
                    overwrite_id=context.writer.community_id,
                    allow=0,
                    deny=1024,
                    is_role=True
                )
            
            context.state.save_state()
        except Exception as e:
            logger.error(f"Failed to setup audit log channel: {e}")
            return
            
    # 2. Format and send the message
    content = f"**[{title}]**\n{description}"
    try:
        await context.writer.send_marker(context.state.audit_log_channel, content, files=files)
    except Exception as e:
        logger.error(f"Failed to send audit log event: {e}")
