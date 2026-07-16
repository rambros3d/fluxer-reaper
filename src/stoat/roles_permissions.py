import asyncio
import logging
from typing import Callable, Awaitable

from src.core.base import MigrationContext

logger = logging.getLogger(__name__)

async def sync_roles_state(context: MigrationContext):
    """
    Scans Stoat for roles matching Discord names and updates state file mappings.
    """
    logger.info("Synchronizing role mappings with Stoat...")
    discord_roles = await context.source_reader.get_roles()
    server = await context.stoat_writer._get_server()
    stoat_roles = list(server.roles.values())
    
    # Build name -> id maps and ID sets for Stoat for fast lookup
    stoat_role_map = {r.name: str(r.id) for r in stoat_roles if r.name}
    stoat_role_ids = {str(r.id) for r in stoat_roles}
    
    updates = 0
    removals = 0
    
    # Verify and Sync Roles
    for role in discord_roles:
        discord_id = str(role.id)
        stoat_id = context.state.get_target_role_id(discord_id)
        
        if stoat_id:
            if stoat_id not in stoat_role_ids:
                context.state.remove_role_mapping(discord_id)
                removals += 1
        elif role.name in stoat_role_map:
            context.state.set_role_mapping(discord_id, stoat_role_map[role.name])
            updates += 1
            
    if updates > 0 or removals > 0:
        logger.info(f"Role sync: {updates} mapped, {removals} stale mappings removed")


async def sync_permissions(context: MigrationContext, progress_callback: Callable[[str, int, int], Awaitable[None]] | None = None) -> dict:
    """Syncs category and channel role overrides/permissions."""
    logger.info("Starting permissions synchronization for Stoat...")
    discord_categories = await context.source_reader.get_categories()
    discord_channels = await context.source_reader.get_channels()
    
    # Only sync for items that are already mapped
    mapped_categories = [c for c in discord_categories if context.state.get_target_category_id(str(c.id))]
    mapped_channels = [c for c in discord_channels if context.state.get_target_channel_id(str(c.id))]
    
    synced_info = {
        "categories_synced": [],
        "channels_synced": [],
        "structure": {} # category_name -> [channel_names]
    }
    
    total = len(mapped_categories) + len(mapped_channels)
    current_idx = 0
    
    if total == 0:
        logger.info("No mapped categories or channels found for Stoat permission sync.")
        return synced_info
    
    logger.info(f"Syncing permissions for {len(mapped_categories)} categories and {len(mapped_channels)} channels to Stoat.")

    cat_map = {cat.id: cat for cat in discord_categories}
    cat_names = {cat.id: cat.name for cat in discord_categories}

    # 1. Sync Categories (In Stoat, categories/folders don't have permissions, so we just track them for structure)
    for cat in mapped_categories:
        if not context.is_running: break
        
        synced_info["categories_synced"].append(cat.name)
        if cat.name not in synced_info["structure"]:
            synced_info["structure"][cat.name] = []
        
        current_idx += 1
        if progress_callback: await progress_callback(f"Cat: {cat.name}", current_idx, total)

    # 2. Sync Channel Permissions with Category Inheritance logic
    for channel in mapped_channels:
        if not context.is_running: break
        
        stoat_channel_id = context.state.get_target_channel_id(str(channel.id))
        if not stoat_channel_id:
            current_idx += 1
            continue

        # Flatten overwrites: start with category, then overlay channel overrides
        final_overwrites = {} # role_id -> PermissionOverwrite
        
        # A. Start with Category overwrites (Inheritance)
        if channel.category_id and channel.category_id in cat_map:
            cat = cat_map[channel.category_id]
            for target, ow in cat.overwrites.items():
                if type(target).__name__ == "Role":
                    # We store it by target.id to easily merge later
                    final_overwrites[target.id] = ow

        # B. Apply Channel overrides on top (Merge)
        for target, ow in channel.overwrites.items():
            if type(target).__name__ == "Role":
                if target.id in final_overwrites:
                    # Merge logic: channel overwrites specific settings, but 
                    # keeps inherited settings for any permission marked as 'None' in channel
                    cat_ow = final_overwrites[target.id]
                    merged = context.source_reader.create_permission_overwrite()
                    # Apply category settings
                    for name, value in cat_ow:
                        setattr(merged, name, value)
                    # Overwrite with channel settings
                    for name, value in ow:
                        if value is not None:
                            setattr(merged, name, value)
                    final_overwrites[target.id] = merged
                else:
                    # Brand new override for this channel
                    final_overwrites[target.id] = ow

        # C. Apply the merged overwrites to Stoat
        try:
            # Sort overrides to ensure @everyone (Community ID) is processed LAST.
            # This prevents the bot from accidentally locking itself out of the channel
            # (due to default denys) before it finishes setting other role permissions.
            sorted_overrides = sorted(
                final_overwrites.items(),
                key=lambda x: str(x[0]) == str(context.source_reader.server_id)
            )

            for d_role_id, merged_ov in sorted_overrides:
                # Map Discord role to Stoat role
                if str(d_role_id) == str(context.source_reader.server_id):
                    # @everyone
                    stoat_id = str(context.stoat_writer.community_id)
                    is_role = False # set_default_permissions path
                else:
                    stoat_id = context.state.get_target_role_id(str(d_role_id))
                    is_role = True
                    
                if not stoat_id:
                    continue
                    
                allow_val, deny_val = merged_ov.pair()
                await context.stoat_writer.set_channel_permission(
                    channel_id=stoat_channel_id,
                    overwrite_id=stoat_id,
                    allow=allow_val.value,
                    deny=deny_val.value,
                    is_role=is_role
                )
            
            synced_info["channels_synced"].append(channel.name)
            parent_name = cat_names.get(channel.category_id, "No Category")
            if parent_name not in synced_info["structure"]:
                synced_info["structure"][parent_name] = []
            synced_info["structure"][parent_name].append(channel.name)
            
        except Exception as e:
            logger.error(f"Failed syncing permissions for channel {channel.name}: {e}")
        
        current_idx += 1
        if progress_callback: await progress_callback(channel.name, current_idx, total)


    logger.info(f"Stoat permissions sync complete: {len(synced_info['categories_synced'])} categories, {len(synced_info['channels_synced'])} channels.")
    return synced_info


async def migrate_roles(context: MigrationContext, progress_callback: Callable[[str, int, int], Awaitable[None]] | None = None, force: bool = False) -> list[str]:
    """Copies roles and their baseline permissions. Returns a list of cloned role names."""
    
    # 1. Sync default permissions (@everyone)
    try:
        guild = context.source_reader.guild
        if guild:
            default_role = guild.default_role
            await context.stoat_writer.update_default_role_permissions(default_role.permissions.value)
            logger.info("Synced default server permissions (@everyone).")
    except Exception as e:
        logger.error(f"Failed to sync default permissions: {e}")

    # 2. Fetch and filter roles
    roles = await context.source_reader.get_roles()
    if not force:
        roles = [r for r in roles if not context.state.get_target_role_id(str(r.id))]

    total = len(roles)
    cloned_role_names = []
    
    logger.info(f"Migrating {total} roles to Stoat...")

    for idx, role in enumerate(roles):
        if not context.is_running:
            logger.warning("Stoat role migration interrupted.")
            break
            
        logger.debug(f"Creating role in Stoat: {role.name}")
        stoat_id = await context.stoat_writer.create_role(
            name=role.name,
            color=role.color.value,
            hoist=role.hoist,
            permissions=role.permissions.value
        )
        if stoat_id:
            context.state.set_role_mapping(str(role.id), stoat_id)
            cloned_role_names.append(role.name)
        
        if progress_callback: await progress_callback(role.name, idx + 1, total)
        
    return cloned_role_names
