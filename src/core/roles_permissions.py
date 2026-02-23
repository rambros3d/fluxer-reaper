import asyncio
import logging
from typing import Callable, Awaitable

from src.core.base import MigrationContext

logger = logging.getLogger(__name__)

async def sync_roles_state(context: MigrationContext):
    """
    Scans Fluxer for roles matching Discord names and updates state.json mappings.
    """
    discord_roles = await context.discord_reader.get_roles()
    fluxer_roles = await context.fluxer_writer.client.get_guild_roles(context.config.fluxer_community_id)
    
    # Build name -> id maps and ID sets for Fluxer for fast lookup
    fluxer_role_map = {r.get("name"): str(r.get("id")) for r in fluxer_roles if r.get("name")}
    fluxer_role_ids = {str(r.get("id")) for r in fluxer_roles}
    
    updates = 0
    removals = 0
    
    # Verify and Sync Roles
    for role in discord_roles:
        discord_id = str(role.id)
        fluxer_id = context.state.get_fluxer_role_id(discord_id)
        
        if fluxer_id:
            if fluxer_id not in fluxer_role_ids:
                context.state.remove_role_mapping(discord_id)
                removals += 1
        elif role.name in fluxer_role_map:
            context.state.set_role_mapping(discord_id, fluxer_role_map[role.name])
            updates += 1
            
    if updates > 0 or removals > 0:
        logger.info(f"Role sync: {updates} mapped, {removals} stale mappings removed")


async def sync_permissions(context: MigrationContext, progress_callback: Callable[[str, int, int], Awaitable[None]] | None = None):
    """Syncs category and channel role overrides/permissions."""
    categories = await context.discord_reader.get_categories()
    channels = await context.discord_reader.get_channels()
    
    # Only sync for items that are already mapped
    categories = [c for c in categories if context.state.get_fluxer_category_id(str(c.id))]
    channels = [c for c in channels if context.state.get_fluxer_channel_id(str(c.id))]

    total = len(categories) + len(channels)
    current_idx = 0
    
    if total == 0:
        return

    async def _sync_overwrites(discord_item, fluxer_id):
        """Helper to sync role overwrites for a given channel or category."""
        for target, overwrite in discord_item.overwrites.items():
            if type(target).__name__ == "Role":
                discord_role_id = str(target.id)
                # Handle @everyone role special case
                if discord_role_id == context.config.discord_server_id:
                    fluxer_role_id = context.config.fluxer_community_id
                else:
                    fluxer_role_id = context.state.get_fluxer_role_id(discord_role_id)
                
                if not fluxer_role_id:
                    continue
                    
                allow_val, deny_val = overwrite.pair()
                await context.fluxer_writer.set_channel_permission(
                    channel_id=fluxer_id,
                    overwrite_id=fluxer_role_id,
                    allow=allow_val.value,
                    deny=deny_val.value,
                    is_role=True
                )

    # Sync Category Permissions (Role Overwrites)
    for cat in categories:
        if not context.is_running: break
        fluxer_id = context.state.get_fluxer_category_id(str(cat.id))
        if fluxer_id:
            try:
                await _sync_overwrites(cat, fluxer_id)
            except Exception as e:
                logger.error(f"Failed syncing permissions for category {cat.name}: {e}")
        
        current_idx += 1
        if progress_callback: await progress_callback(f"Cat: {cat.name}", current_idx, total)

    # Sync Channel Permissions
    for channel in channels:
        if not context.is_running: break
        fluxer_id = context.state.get_fluxer_channel_id(str(channel.id))
        if fluxer_id:
            try:
                await _sync_overwrites(channel, fluxer_id)
            except Exception as e:
                logger.error(f"Failed syncing permissions for channel {channel.name}: {e}")
        
        current_idx += 1
        if progress_callback: await progress_callback(channel.name, current_idx, total)


async def migrate_roles(context: MigrationContext, progress_callback: Callable[[str, int, int], Awaitable[None]] | None = None, force: bool = False):
    """Copies roles and their baseline permissions."""
    roles = await context.discord_reader.get_roles()
    
    if not force:
        roles = [r for r in roles if not context.state.get_fluxer_role_id(str(r.id))]

    total = len(roles)
    
    if total == 0:
        return

    for idx, role in enumerate(roles):
        if not context.is_running: break
            
        fluxer_id = await context.fluxer_writer.create_role(
            name=role.name,
            color=role.color.value,
            hoist=role.hoist,
            mentionable=role.mentionable
        )
        if fluxer_id:
            context.state.set_role_mapping(str(role.id), fluxer_id)
        
        if progress_callback: await progress_callback(role.name, idx + 1, total)
        await asyncio.sleep(context.config.migration.rate_limit_delay_seconds)
