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

    async def migrate_channels(self, progress_callback: Callable[[str, int, int], Awaitable[None]] | None = None):
        """Clones categories and text channels."""
        categories = await self.discord_reader.get_categories()
        channels = await self.discord_reader.get_channels()
        
        total = len(categories) + len(channels)
        current_idx = 0
        
        # Migrate Categories first
        for cat in categories:
            if not self.is_running: break
            fluxer_id = self.state.get_fluxer_channel_id(str(cat.id))
            if not fluxer_id:
                # 4 corresponds to Category type in Discord/Fluxer typically
                fluxer_id = await self.fluxer_writer.create_channel(cat.name, type=4)
                self.state.set_channel_mapping(str(cat.id), fluxer_id)
            
            current_idx += 1
            if progress_callback: await progress_callback(f"Cat: {cat.name}", current_idx, total)
            await asyncio.sleep(self.config.migration.rate_limit_delay_seconds)

        # Migrate Text Channels
        for channel in channels:
            if not self.is_running: break
                
            fluxer_id = self.state.get_fluxer_channel_id(str(channel.id))
            if not fluxer_id:
                topic = channel.topic if channel.topic else ""
                parent_id = self.state.get_fluxer_channel_id(str(channel.category_id)) if channel.category_id else None
                
                fluxer_id = await self.fluxer_writer.create_channel(
                    name=channel.name, 
                    topic=topic, 
                    type=0, 
                    parent_id=parent_id
                )
                self.state.set_channel_mapping(str(channel.id), fluxer_id)
            
            current_idx += 1
            if progress_callback: await progress_callback(channel.name, current_idx, total)
            await asyncio.sleep(self.config.migration.rate_limit_delay_seconds)

    async def sync_permissions(self, progress_callback: Callable[[str, int, int], Awaitable[None]] | None = None):
        """Syncs category and channel role overrides/permissions."""
        categories = await self.discord_reader.get_categories()
        channels = await self.discord_reader.get_channels()
        
        total = len(categories) + len(channels)
        current_idx = 0
        
        # Sync Category Permissions (Role Overwrites)
        for cat in categories:
            if not self.is_running: break
            fluxer_id = self.state.get_fluxer_channel_id(str(cat.id))
            if fluxer_id:
                # In a real implementation, we would diff discord perms 
                # and apply them to fluxer_id using client methods.
                pass
            
            current_idx += 1
            if progress_callback: await progress_callback(f"Cat: {cat.name}", current_idx, total)
            await asyncio.sleep(self.config.migration.rate_limit_delay_seconds)

        # Sync Channel Permissions
        for channel in channels:
            if not self.is_running: break
            fluxer_id = self.state.get_fluxer_channel_id(str(channel.id))
            if fluxer_id:
                pass
            
            current_idx += 1
            if progress_callback: await progress_callback(channel.name, current_idx, total)
            await asyncio.sleep(self.config.migration.rate_limit_delay_seconds)

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

                self.state.update_last_message_timestamp(str(source_channel_id), str(msg.created_at))
                message_count += 1
                if progress_callback:
                    await progress_callback(message_count)
            except Exception as e:
                logger.error(f"Failed to send message to Fluxer: {e}")
            
            # Delay for rate limit safety
            await asyncio.sleep(self.config.migration.rate_limit_delay_seconds)
            
        return message_count

    async def migrate_roles(self, progress_callback: Callable[[str, int, int], Awaitable[None]] | None = None):
        """Copies roles and their baseline permissions."""
        roles = await self.discord_reader.get_roles()
        total = len(roles)
        
        for idx, role in enumerate(roles):
            if not self.is_running: break
                
            fluxer_id = self.state.get_fluxer_channel_id(f"role_{role.id}") # reusing mapping method
            if not fluxer_id:
                fluxer_id = await self.fluxer_writer.create_role(
                    name=role.name,
                    color=role.color.value,
                    hoist=role.hoist,
                    mentionable=role.mentionable
                )
                if fluxer_id:
                    self.state.set_channel_mapping(f"role_{role.id}", fluxer_id)
            
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
            
        total = len(objs)
        
        for idx, (obj, obj_type) in enumerate(objs):
            if not self.is_running: break
                
            state_key = f"{obj_type.lower()}_{obj.id}"
            fluxer_id = None if force else self.state.get_fluxer_channel_id(state_key)
            if not fluxer_id:
                try:
                    if obj_type == "Emoji":
                        img_data = await self.discord_reader.download_emoji(obj)
                        fluxer_id = await self.fluxer_writer.create_emoji(
                            name=obj.name,
                            image_bytes=img_data
                        )
                    else:
                        img_data = await self.discord_reader.download_sticker(obj)
                        fluxer_id = await self.fluxer_writer.create_sticker(
                            name=obj.name,
                            image_bytes=img_data
                        )
                    
                    if fluxer_id:
                        self.state.set_channel_mapping(state_key, fluxer_id)
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
        return await self.fluxer_writer.delete_all_channels(progress_callback=progress_callback)

    async def danger_reset_channel_permissions(self, progress_callback=None) -> int:
        """Resets all permission overwrites on every channel and category."""
        return await self.fluxer_writer.reset_channel_permissions(progress_callback=progress_callback)

    async def danger_delete_all_roles(self, progress_callback=None) -> int:
        """Deletes all deletable roles (skips managed/bot roles and @everyone)."""
        return await self.fluxer_writer.delete_all_roles(progress_callback=progress_callback)

    async def danger_delete_all_emojis_and_stickers(self, progress_callback=None) -> dict:
        """Deletes all custom emojis and stickers. Returns {"emojis": int, "stickers": int}."""
        return await self.fluxer_writer.delete_all_emojis_and_stickers(progress_callback=progress_callback)

