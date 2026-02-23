import logging
from src.core.base import MigrationContext

logger = logging.getLogger(__name__)

async def log_audit_event(context: MigrationContext, title: str, description: str) -> None:
    """
    Logs an event by sending a summary to the `#fluxer-reaper` audit channel.
    If the channel does not exist, it will dynamically create it and hide it from @everyone.
    """
    # 1. Initialize channel if not tracked
    if not context.state.audit_log_channel:
        logger.info("Audit log channel not found in state. Checking Fluxer community...")
        try:
            # Check if it already exists in the community but isn't in state
            channels = await context.fluxer_writer.get_channels()
            channel_id = None
            
            for ch in channels:
                name = str(ch.get("name", "")).lower()
                if name in ["fluxer-reaper", "fluxer_reaper"]:
                    channel_id = str(ch.get("id"))
                    logger.info(f"Found existing audit channel: {channel_id}")
                    break
            
            if not channel_id:
                logger.info("Audit log channel not found. Creating #fluxer-reaper.")
                # Create channel
                channel_id = await context.fluxer_writer.create_channel(
                    name="fluxer-reaper",
                    topic="Fluxer Reaper - Migration audit logs.",
                    type=0
                )
            
            # Immediately lock down 'View Channel' (1024) for @everyone (community_id)
            await context.fluxer_writer.set_channel_permission(
                channel_id=channel_id,
                overwrite_id=context.config.fluxer_community_id, # @everyone matches community ID
                allow=0,
                deny=1024,
                is_role=True
            )
            
            # Save permanently
            context.state.audit_log_channel = channel_id
            context.state.save_state()
            
        except Exception as e:
            logger.error(f"Failed to setup audit log channel: {e}")
            return
            
    # 2. Format and send the message natively through FluxerBot (avoiding impersonation webhook for admin logs)
    content = f"**[{title}]**\n{description}"
    try:
        await context.fluxer_writer.send_marker(context.state.audit_log_channel, content)
    except Exception as e:
        logger.error(f"Failed to send audit log event: {e}")
