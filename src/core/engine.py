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

    async def close_connections(self):
        await self.discord_reader.close()
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
            for att in msg.attachments:
                try:
                    att_data = await self.discord_reader.download_attachment(att)
                    files.append({"filename": att.filename, "data": att_data})
                except Exception as e:
                    logger.error(f"Failed to download attachment {att.filename}: {e}")
                
            try:
                await self.fluxer_writer.send_message(
                    channel_id=target_channel_id,
                    author_name=msg.author.name,
                    content=msg.content,
                    timestamp=msg.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    files=files if files else None
                )
                
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

    async def migrate_emojis(self, progress_callback: Callable[[str, str, int, int], Awaitable[None]] | None = None, types_to_include: List[str] = ["Emoji", "Sticker"]):
        """Copies custom emojis and stickers."""
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
            fluxer_id = self.state.get_fluxer_channel_id(state_key)
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
