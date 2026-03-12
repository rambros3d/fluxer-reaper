import discord
import logging
from typing import AsyncGenerator, Dict, Any, Union

logger = logging.getLogger(__name__)

class DiscordReader:
    # -- Provider constants (used by migration scripts instead of importing discord) --
    MESSAGE_TYPE_DEFAULT = discord.MessageType.default
    MESSAGE_TYPE_REPLY = discord.MessageType.reply
    MESSAGE_TYPE_THREAD_STARTER = discord.MessageType.thread_starter_message
    
    # Exceptions
    Forbidden = discord.Forbidden
    
    # Channel Types
    CHANNEL_TYPE_TEXT = discord.ChannelType.text
    CHANNEL_TYPE_NEWS = discord.ChannelType.news
    CHANNEL_TYPE_FORUM = discord.ChannelType.forum

    @staticmethod
    def find_item(iterable, **attrs):
        """Find first item in iterable matching all attrs. Drop-in for discord.utils.get()."""
        for item in iterable:
            if all(getattr(item, k, None) == v for k, v in attrs.items()):
                return item
        return None

    @staticmethod
    def create_permission_overwrite():
        """Factory for discord.PermissionOverwrite, keeps the import centralized."""
        return discord.PermissionOverwrite()

    @staticmethod
    async def fetch_guilds(token: str) -> list[tuple[str, str]]:
        """Fetches the list of guilds the bot is a member of. Returns list of (name, id)."""
        intents = discord.Intents.default()
        intents.guilds = True
        client = discord.Client(intents=intents)
        guilds_list = []
        try:
            # We use a short-lived client just to fetch the guilds
            await client.login(token)
            async for guild in client.fetch_guilds(limit=None):
                label = f"{guild.id}-{guild.name}"
                guilds_list.append((label, str(guild.id)))
        except Exception as e:
            logger.error(f"Failed to fetch Discord guilds: {e}")
            raise
        finally:
            await client.close()
        return guilds_list

    def __init__(self, token: str, server_id: str):
        self.token = token
        try:
            self.server_id = int(server_id)
        except (ValueError, TypeError):
            # Fallback for placeholder strings like 'DISCORD_SERVER_ID'
            self.server_id = 0
        
        self.guild: discord.Guild | None = None
        self.client: discord.Client | None = None

    def _create_client(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.guilds = True
        return discord.Client(intents=intents)

    async def start(self):
        """Starts the Discord client to fetch metadata."""
        if not self.client or self.client.is_closed():
            self.client = self._create_client()
        
        # login() initializes the internal HTTP session needed for API calls
        await self.client.login(self.token)
        
        # Use fetch methods specifically to bypass dependency on gateway cache
        # fetch_guild initializes the guild object needed for subsequent API calls
        try:
            logger.info(f"Fetching guild {self.server_id}...")
            self.guild = await self.client.fetch_guild(self.server_id)
            logger.info(f"Successfully fetched guild: {self.guild.name}")
        except discord.Forbidden:
            logger.error(f"403 Forbidden: Missing Access to fetch guild {self.server_id}.")
            raise
        except Exception as e:
            logger.error(f"Failed to fetch guild {self.server_id}: {e}")
            raise

    async def validate(self) -> Dict[str, Any]:
        """Validates the token, server ID, intents, and permissions."""
        results = {
            "token": False, 
            "server": False, 
            "bot_name": None, 
            "server_name": None,
            "intents": {"message_content": False},
            "permissions": {"view_channel": False, "read_message_history": False}
        }
        temp_client = self._create_client()
        try:
            await temp_client.login(self.token)
            results["token"] = True
            if temp_client.user:
                results["bot_name"] = temp_client.user.display_name
                
            guild = await temp_client.fetch_guild(self.server_id)
            if guild is not None:
                results["server"] = True
                results["server_name"] = guild.name
                
                # Check intents
                results["intents"]["message_content"] = temp_client.intents.message_content
                
                # Check permissions
                # We need to fetch the member to check permissions
                try:
                    member = await guild.fetch_member(temp_client.user.id)
                    perms = member.guild_permissions
                    results["permissions"]["view_channel"] = perms.view_channel
                    results["permissions"]["read_message_history"] = perms.read_message_history
                except Exception:
                    # Fallback if member fetch fails, though it shouldn't for the bot itself
                    pass
        except Exception:
            pass
        finally:
            if not temp_client.is_closed():
                await temp_client.close()
        return results

    async def get_server_metadata(self) -> Dict[str, Any]:
        """Returns name, icon, and other metadata."""
        if not self.guild:
            return {}
        return {
            "name": self.guild.name,
            "id": str(self.guild.id),
            "icon_url": self.guild.icon.url if self.guild.icon else None,
            "banner_url": self.guild.banner.url if self.guild.banner else None
        }

    async def download_asset(self, asset: discord.Asset) -> bytes:
        """Downloads an asset (icon, banner) into memory."""
        return await asset.read()

    async def get_categories(self):
        if not self.guild:
            return []
        categories = await self.guild.fetch_channels()
        return [c for c in categories if isinstance(c, discord.CategoryChannel)]

    async def get_roles(self):
        """Returns all roles in the server (excluding @everyone)."""
        if not self.guild:
            return []
        roles = await self.guild.fetch_roles()
        return [r for r in roles if not r.is_default()]

    async def get_emojis(self):
        """Returns all custom emojis in the server."""
        if not self.guild:
            return []
        return await self.guild.fetch_emojis()

    async def get_stickers(self):
        """Returns all custom stickers in the server."""
        if not self.guild:
            return []
        return await self.guild.fetch_stickers()

    async def get_members(self):
        """Returns all members in the server."""
        if not self.guild:
            return []
        # Use a list to hold all members
        members = []
        async for member in self.guild.fetch_members(limit=None):
            members.append(member)
        return members

    async def get_channels(self, category_id: int | None = None):
        """Yields all non-category channels."""
        if not self.guild:
            return []
        
        channels = await self.guild.fetch_channels()
        all_channels = [c for c in channels if not isinstance(c, discord.CategoryChannel)]
        
        if category_id:
            all_channels = [c for c in all_channels if c.category_id == category_id]
        return all_channels

    async def get_channel(self, channel_id: int):
        """Returns a channel object."""
        return await self.client.fetch_channel(channel_id)

    async def get_message(self, channel_id: int, message_id: int):
        """Returns a specific message."""
        channel = await self.get_channel(channel_id)
        if hasattr(channel, "fetch_message"):
            return await channel.fetch_message(message_id)
        return None

    async def get_first_message(self, channel_id: int):
        """Returns the first (oldest) message in a channel."""
        channel = await self.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel) or isinstance(channel, discord.Thread):
            async for message in channel.history(limit=1, oldest_first=True):
                return message
        return None

    async def fetch_message_history(self, channel_id: int, limit: int = None, after_id: int = None, inclusive: bool = False) -> AsyncGenerator[discord.Message, None]:
        """Yields messages from a given channel, optionally handling pagination."""
        channel = await self.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel) or isinstance(channel, discord.Thread):
            # Discord's 'after' is exclusive. To make it inclusive, we use after_id - 1 if requested.
            after = None
            if after_id:
                after = discord.Object(id=after_id - 1) if inclusive else discord.Object(id=after_id)
            logger.info(f"Fetching message history for {channel.name} ({channel.id}) oldest_first=True after={after_id} inclusive={inclusive}")
            # To avoid exploding RAM, we yield items one by one
            async for message in channel.history(limit=limit, oldest_first=True, after=after):
                yield message

    async def download_emoji(self, emoji: discord.Emoji) -> bytes:
        """Downloads a Discord emoji into memory."""
        return await emoji.read()

    async def download_sticker(self, sticker: Union[discord.GuildSticker, discord.StickerItem]) -> bytes:
        """Downloads a Discord sticker into memory."""
        logger.debug(f"Attempting to download sticker: {getattr(sticker, 'name', 'unknown')} (type: {type(sticker)})")
        
        # 1. Try directly reading
        if hasattr(sticker, 'read'):
            try:
                return await sticker.read()
            except Exception as e:
                logger.debug(f"Direct read failed for sticker: {e}")
        
        # 2. Try converting to full sticker (only for StickerItem)
        if hasattr(sticker, 'to_sticker'):
            try:
                logger.debug(f"Attempting to_sticker() for {getattr(sticker, 'name', 'unknown')}")
                full_sticker = await sticker.to_sticker()
                if hasattr(full_sticker, 'read'):
                    return await full_sticker.read()
            except Exception as e:
                logger.debug(f"to_sticker fallback failed: {e}")

        # 3. Try downloading from URL as last resort
        url = getattr(sticker, 'url', None)
        if url:
            try:
                import aiohttp
                logger.debug(f"Attempting URL download for sticker from {url}")
                async with aiohttp.ClientSession() as session:
                    async with session.get(str(url)) as resp:
                        if resp.status == 200:
                            return await resp.read()
                        else:
                            logger.debug(f"URL download failed with status {resp.status}")
            except Exception as e:
                logger.debug(f"URL download failed for sticker: {e}")
        
        logger.warning(f"Failed to download sticker {getattr(sticker, 'name', 'unknown')} after all attempts")
        return b""

    async def download_attachment(self, attachment: discord.Attachment) -> bytes:
        """Downloads a Discord attachment into memory."""
        return await attachment.read()

    async def close(self):
        client = self.client
        self.client = None # Atomic clear
        self.guild = None
        if client:
            try:
                await client.close()
            except Exception as e:
                logger.debug(f"Error closing Discord client: {e}")
