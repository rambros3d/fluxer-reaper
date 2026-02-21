import asyncio
import logging
from typing import Callable, Awaitable, List, Dict, Any
from src.config import AppConfig
from src.core.state import MigrationState
from src.discord_bot.reader import DiscordReader
from src.fluxer_bot.writer import FluxerWriter

logger = logging.getLogger(__name__)

class MigrationEngine:
    """Orchestrates reading from Discord and writing to Fluxer."""
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.state = MigrationState()
        
        self.discord_reader = DiscordReader(
            token=config.discord_bot_token,
            server_id=config.discord_server_id
        )
        
        self.fluxer_writer = FluxerWriter(
            token=config.fluxer_bot_token,
            community_id=config.fluxer_community_id
        )
        
        self.is_running = False

    async def validate_all(self) -> Dict[str, Any]:
        """Returns True if both connections are valid."""
        try:
            d_valid = await self.discord_reader.validate()
            f_valid = await self.fluxer_writer.validate()
            return {
                "discord_token": d_valid.get("token", False),
                "discord_bot_name": d_valid.get("bot_name"),
                "discord_server": d_valid.get("server", False),
                "discord_server_name": d_valid.get("server_name"),
                "fluxer_token": f_valid.get("token", False),
                "fluxer_bot_name": f_valid.get("bot_name"),
                "fluxer_community": f_valid.get("community", False),
                "fluxer_community_name": f_valid.get("community_name")
            }
        except Exception as e:
            logger.error(f"Validation failed with exception: {e}")
            return {
                "discord_token": False,
                "discord_server": False,
                "fluxer_token": False,
                "fluxer_community": False
            }

    async def start_connections(self):
        await self.discord_reader.start()
        await self.fluxer_writer.start()

    async def start_fluxer_only(self):
        """Starts only the Fluxer writer (used for Danger Zone operations that don't need Discord)."""
        await self.fluxer_writer.start()

    async def close_connections(self):
        await self.discord_reader.close()
        await self.fluxer_writer.close()

    async def close_fluxer_only(self):
        """Closes only the Fluxer writer. Pair with start_fluxer_only()."""
        await self.fluxer_writer.close()

    async def sync_server_metadata(self, progress_callback: Callable[[str, str], Awaitable[None]], components: List[str] = ["name", "icon", "banner"]):
        """Syncs the server name, logo and banner."""
        metadata = await self.discord_reader.get_server_metadata()
        
        # 1. Sync Name
        if "name" in components:
            try:
                name = metadata.get("name")
                await self.fluxer_writer.update_guild_metadata(name=name)
                await progress_callback("Server Name", "DONE")
            except Exception:
                await progress_callback("Server Name", "ERROR")

        # 2. Sync Icon
        if "icon" in components:
            try:
                icon_bytes = None
                if self.discord_reader.guild and self.discord_reader.guild.icon:
                    icon_bytes = await self.discord_reader.download_asset(self.discord_reader.guild.icon)
                
                if icon_bytes:
                    await self.fluxer_writer.update_guild_metadata(icon=icon_bytes)
                    await progress_callback("Server Icon", "DONE")
                else:
                    await progress_callback("Server Icon", "SKIP")
            except Exception:
                await progress_callback("Server Icon", "ERROR")
            
        # 3. Sync Banner
        if "banner" in components:
            try:
                banner_bytes = None
                if self.discord_reader.guild and self.discord_reader.guild.banner:
                    banner_bytes = await self.discord_reader.download_asset(self.discord_reader.guild.banner)
                
                if banner_bytes:
                    await self.fluxer_writer.update_guild_metadata(banner=banner_bytes)
                    await progress_callback("Server Banner", "DONE")
                else:
                    await progress_callback("Server Banner", "SKIP")
            except Exception:
                await progress_callback("Server Banner", "ERROR")

    async def sync_channel_state(self):
        """
        Scans Fluxer for channels matching Discord names and updates state.json mappings.
        This prevents duplicate creation when the state.json is empty but channels exist in Fluxer.
        """
        categories = await self.discord_reader.get_categories()
        channels = await self.discord_reader.get_channels()
        fluxer_channels = await self.fluxer_writer.get_channels()
        
        # Build name -> id map and ID set for Fluxer for fast lookup
        fluxer_name_map = {c.get("name"): str(c.get("id")) for c in fluxer_channels if c.get("name")}
        fluxer_id_set = {str(c.get("id")) for c in fluxer_channels}
        
        updates = 0
        removals = 0
        
        # 1. Verify and Sync Categories
        for cat in categories:
            discord_id = str(cat.id)
            fluxer_id = self.state.get_fluxer_category_id(discord_id)
            
            if fluxer_id:
                if fluxer_id not in fluxer_id_set:
                    self.state.remove_category_mapping(discord_id)
                    removals += 1
            elif cat.name in fluxer_name_map:
                self.state.set_category_mapping(discord_id, fluxer_name_map[cat.name])
                updates += 1
                    
        # 2. Verify and Sync Channels
        for ch in channels:
            discord_id = str(ch.id)
            fluxer_id = self.state.get_fluxer_channel_id(discord_id)
            
            if fluxer_id:
                if fluxer_id not in fluxer_id_set:
                    self.state.remove_channel_mapping(discord_id)
                    removals += 1
            elif ch.name in fluxer_name_map:
                self.state.set_channel_mapping(discord_id, fluxer_name_map[ch.name])
                updates += 1
        
        if updates > 0 or removals > 0:
            logger.info(f"Channel sync: {updates} mapped, {removals} stale mappings removed")

    async def sync_assets_state(self):
        """
        Scans Fluxer for emojis and stickers matching Discord names and updates state.json mappings.
        """
        discord_emojis = await self.discord_reader.get_emojis()
        discord_stickers = await self.discord_reader.get_stickers()
        
        fluxer_emojis = await self.fluxer_writer.client.get_guild_emojis(self.config.fluxer_community_id)
        fluxer_stickers = await self.fluxer_writer.client.get_guild_stickers(self.config.fluxer_community_id)
        
        # Build name -> id maps and ID sets for Fluxer for fast lookup
        fluxer_emoji_map = {e.get("name"): str(e.get("id")) for e in fluxer_emojis if e.get("name")}
        fluxer_sticker_map = {s.get("name"): str(s.get("id")) for s in fluxer_stickers if s.get("name")}
        fluxer_emoji_ids = {str(e.get("id")) for e in fluxer_emojis}
        fluxer_sticker_ids = {str(s.get("id")) for s in fluxer_stickers}
        
        updates = 0
        removals = 0
        
        # 1. Verify and Sync Emojis
        for emoji in discord_emojis:
            discord_id = str(emoji.id)
            fluxer_id = self.state.get_fluxer_emoji_id(discord_id)
            
            if fluxer_id:
                if fluxer_id not in fluxer_emoji_ids:
                    self.state.remove_emoji_mapping(discord_id)
                    removals += 1
            elif emoji.name in fluxer_emoji_map:
                self.state.set_emoji_mapping(discord_id, fluxer_emoji_map[emoji.name])
                updates += 1
                    
        # 2. Verify and Sync Stickers
        for sticker in discord_stickers:
            discord_id = str(sticker.id)
            fluxer_id = self.state.get_fluxer_sticker_id(discord_id)
            
            if fluxer_id:
                if fluxer_id not in fluxer_sticker_ids:
                    self.state.remove_sticker_mapping(discord_id)
                    removals += 1
            elif sticker.name in fluxer_sticker_map:
                self.state.set_sticker_mapping(discord_id, fluxer_sticker_map[sticker.name])
                updates += 1
                    
        if updates > 0 or removals > 0:
            logger.info(f"Asset sync: {updates} mapped, {removals} stale mappings removed")

    async def migrate_channels(self, progress_callback: Callable[[str, str, int, int], Awaitable[None]] | None = None, force: bool = False):
        """Clones categories and text channels.
        
        Args:
            progress_callback: Optional callback receiving (item_name, status, current, total)
            force: If True, re-create channels even if they exist in state.
        """
        categories = await self.discord_reader.get_categories()
        channels = await self.discord_reader.get_channels()
        
        # Filter items if not forcing
        if not force:
            categories = [cat for cat in categories if not self.state.get_fluxer_category_id(str(cat.id))]
            channels = [ch for ch in channels if not self.state.get_fluxer_channel_id(str(ch.id))]

        total = len(categories) + len(channels)
        current_idx = 0
        
        if total == 0:
            return

        # Migrate Categories first
        for cat in categories:
            if not self.is_running: break
            
            state_key = str(cat.id)
            # 4 corresponds to Category type in Discord/Fluxer typically
            fluxer_id = await self.fluxer_writer.create_channel(cat.name, type=4)
            self.state.set_category_mapping(state_key, fluxer_id)
            
            current_idx += 1
            if progress_callback: await progress_callback(f"Cat: {cat.name}", "Copying", current_idx, total)
            await asyncio.sleep(self.config.migration.rate_limit_delay_seconds)

        # Migrate Text Channels
        for channel in channels:
            if not self.is_running: break
                
            state_key = str(channel.id)
            topic = channel.topic if channel.topic else ""
            parent_id = self.state.get_fluxer_category_id(str(channel.category_id)) if channel.category_id else None
            
            fluxer_id = await self.fluxer_writer.create_channel(
                name=channel.name, 
                topic=topic, 
                type=0, 
                parent_id=parent_id
            )
            self.state.set_channel_mapping(state_key, fluxer_id)
            
            current_idx += 1
            if progress_callback: await progress_callback(channel.name, "Copying", current_idx, total)
            await asyncio.sleep(self.config.migration.rate_limit_delay_seconds)

    async def sync_permissions(self, progress_callback: Callable[[str, int, int], Awaitable[None]] | None = None):
        """Syncs category and channel role overrides/permissions."""
        categories = await self.discord_reader.get_categories()
        channels = await self.discord_reader.get_channels()
        
        # Only sync for items that are already mapped
        categories = [c for c in categories if self.state.get_fluxer_category_id(str(c.id))]
        channels = [c for c in channels if self.state.get_fluxer_channel_id(str(c.id))]

        total = len(categories) + len(channels)
        current_idx = 0
        
        if total == 0:
            return

        # Sync Category Permissions (Role Overwrites)
        for cat in categories:
            if not self.is_running: break
            fluxer_id = self.state.get_fluxer_channel_id(str(cat.id))
            # In a real implementation, we would diff discord perms 
            # and apply them to fluxer_id using client methods.
            
            current_idx += 1
            if progress_callback: await progress_callback(f"Cat: {cat.name}", current_idx, total)

        # Sync Channel Permissions
        for channel in channels:
            if not self.is_running: break
            fluxer_id = self.state.get_fluxer_channel_id(str(channel.id))
            # apply perms
            
            current_idx += 1
            if progress_callback: await progress_callback(channel.name, current_idx, total)

    async def analyze_migration(self, source_channel_id: int, after_message_id: int | None = None, progress_callback: Callable[[int], Awaitable[None]] | None = None) -> Dict[str, int]:
        """
        Scans channel history to count messages, threads, and attachments.
        """
        stats = {"messages": 0, "threads": 0, "attachments": 0}
        
        async for msg in self.discord_reader.fetch_message_history(source_channel_id, after_id=after_message_id):
            if not self.is_running:
                break
            
            stats["messages"] += 1
            stats["attachments"] += len(msg.attachments)
            
            # Count thread messages and markers
            if hasattr(msg, 'thread') and msg.thread:
                stats["threads"] += 1
                # Recursively count thread content
                thread_stats = await self.analyze_migration(msg.thread.id)
                stats["messages"] += thread_stats["messages"]
                stats["attachments"] += thread_stats["attachments"]
                stats["threads"] += thread_stats["threads"] # Nested threads (rare in Discord but possible in forum channels)

            if progress_callback and stats["messages"] % 10 == 0:
                await progress_callback(stats["messages"])

        return stats

    async def migrate_messages(self, source_channel_id: int, target_channel_id: str, after_message_id: int | None = None, progress_callback: Callable[[int], Awaitable[None]] | None = None):
        """Migrate messages for a specific channel."""
        message_count = 0
        async for msg in self.discord_reader.fetch_message_history(source_channel_id, after_id=after_message_id):
            if not self.is_running:
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
                    att_data = await self.discord_reader.download_attachment(att)
                    files.append({"filename": att.filename, "data": att_data})
                except Exception as e:
                    logger.error(f"Failed to download attachment {att.filename}: {e}")
                
            try:
                # Check if this message is a reply
                reply_to_fluxer_id = None
                if msg.reference and msg.reference.message_id:
                    reply_to_fluxer_id = self.state.get_fluxer_message_id(str(msg.reference.message_id))
                
                fluxer_msg_id = await self.fluxer_writer.send_message(
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
                    self.state.set_message_mapping(str(msg.id), fluxer_msg_id)

                # Check for associated thread
                if hasattr(msg, 'thread') and msg.thread:
                    thread = msg.thread
                    logger.info(f"Detected thread '{thread.name}' on message {msg.id}")
                    
                    # Send Start Marker
                    await self.fluxer_writer.send_marker(
                        channel_id=target_channel_id,
                        content=f"> <<< THREAD: **{thread.name}** >>>"
                    )
                    
                    # Migrate thread messages
                    # We don't pass a progress callback here to avoid confusing the UI
                    # but we do want to track count if possible.
                    await self.migrate_messages(
                        source_channel_id=thread.id,
                        target_channel_id=target_channel_id
                    )
                    
                    # Send End Marker
                    await self.fluxer_writer.send_marker(
                        channel_id=target_channel_id,
                        content=f"> <<< END OF THREAD >>>"
                    )

                self.state.update_last_message_timestamp(str(source_channel_id), str(msg.created_at))
                message_count += 1
                if progress_callback:
                    await progress_callback(message_count)
            except Exception as e:
                logger.error(f"Failed to process message {msg.id}: {e}")
                import traceback
                logger.error(traceback.format_exc())
            
            # Delay for rate limit safety
            await asyncio.sleep(self.config.migration.rate_limit_delay_seconds)
            
        return message_count

    async def migrate_roles(self, progress_callback: Callable[[str, int, int], Awaitable[None]] | None = None, force: bool = False):
        """Copies roles and their baseline permissions."""
        roles = await self.discord_reader.get_roles()
        
        if not force:
            roles = [r for r in roles if not self.state.get_fluxer_role_id(str(r.id))]

        total = len(roles)
        
        if total == 0:
            return

        for idx, role in enumerate(roles):
            if not self.is_running: break
                
            fluxer_id = await self.fluxer_writer.create_role(
                name=role.name,
                color=role.color.value,
                hoist=role.hoist,
                mentionable=role.mentionable
            )
            if fluxer_id:
                self.state.set_role_mapping(str(role.id), fluxer_id)
            
            if progress_callback: await progress_callback(role.name, idx + 1, total)
            await asyncio.sleep(self.config.migration.rate_limit_delay_seconds)

    async def migrate_emojis(self, progress_callback: Callable[[str, str, int, int], Awaitable[None]] | None = None, types_to_include: List[str] = ["Emoji", "Sticker"], force: bool = False):
        """Copies custom emojis and stickers.
        
        Args:
            force: If True, skip state cache and re-copy even if already migrated.
        """
        objs = []
        if "Emoji" in types_to_include:
            emojis = await self.discord_reader.get_emojis()
            objs.extend([(e, "Emoji") for e in emojis])
        if "Sticker" in types_to_include:
            stickers = await self.discord_reader.get_stickers()
            objs.extend([(s, "Sticker") for s in stickers])
            
        if not force:
            objs = [(obj, obj_type) for obj, obj_type in objs if not (
                self.state.get_fluxer_emoji_id(str(obj.id)) if obj_type == "Emoji" else self.state.get_fluxer_sticker_id(str(obj.id))
            )]

        total = len(objs)
        
        if total == 0:
            return

        for idx, (obj, obj_type) in enumerate(objs):
            if not self.is_running: break
                
            try:
                if obj_type == "Emoji":
                    img_data = await self.discord_reader.download_emoji(obj)
                    fluxer_id = await self.fluxer_writer.create_emoji(
                        name=obj.name,
                        image_bytes=img_data
                    )
                    if fluxer_id:
                        self.state.set_emoji_mapping(str(obj.id), fluxer_id)
                else:
                    img_data = await self.discord_reader.download_sticker(obj)
                    fluxer_id = await self.fluxer_writer.create_sticker(
                        name=obj.name,
                        image_bytes=img_data
                    )
                    if fluxer_id:
                        self.state.set_sticker_mapping(str(obj.id), fluxer_id)
            except Exception as e:
                logger.error(f"Error downloading/uploading {obj_type.lower()} {obj.name}: {e}")
            
            if progress_callback: await progress_callback(obj.name, obj_type, idx + 1, total)
            await asyncio.sleep(self.config.migration.rate_limit_delay_seconds)

    async def run_full_migration(self):
        self.is_running = True
        try:
            await self.start_connections()
            await self.migrate_channels()
            
            # Example: just migrate one channel's messages to test
            channels = await self.discord_reader.get_channels()
            if channels:
                await self.migrate_messages(channels[0].id)
                
        finally:
            await self.close_connections()
            self.is_running = False

    def stop(self):
        self.is_running = False

    # ──────────────── DANGER ZONE ────────────────

    async def danger_remove_logo_and_banner(self) -> dict:
        """Removes the community logo and banner image. Returns per-field status."""
        return await self.fluxer_writer.remove_community_logo_and_banner()

    async def danger_delete_all_channels(self, progress_callback=None) -> int:
        """Deletes every channel and category in the Fluxer community."""
        count = await self.fluxer_writer.delete_all_channels(progress_callback=progress_callback)
        self.state.clear_channel_mappings()
        self.state.clear_message_history()
        return count

    async def danger_reset_channel_permissions(self, progress_callback=None) -> int:
        """Resets all permission overwrites on every channel and category."""
        return await self.fluxer_writer.reset_channel_permissions(progress_callback=progress_callback)

    async def danger_delete_all_roles(self, progress_callback=None) -> int:
        """Deletes all deletable roles (skips managed/bot roles and @everyone)."""
        count = await self.fluxer_writer.delete_all_roles(progress_callback=progress_callback)
        self.state.clear_role_mappings()
        return count

    async def danger_delete_all_emojis_and_stickers(self, progress_callback=None) -> dict:
        """Deletes all custom emojis and stickers. Returns {"emojis": int, "stickers": int}."""
        counts = await self.fluxer_writer.delete_all_emojis_and_stickers(progress_callback=progress_callback)
        self.state.clear_asset_mappings()
        return counts

