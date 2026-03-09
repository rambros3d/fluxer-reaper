import discord
import logging
from typing import AsyncGenerator, Dict, Any

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
        self.role_map: Dict[int, str] = {}
        self.channel_name_map: Dict[int, str] = {}
        # Session-level caches to avoid redundant fetch calls
        self._roles_cache: list[discord.Role] | None = None
        self._channels_cache: list[discord.abc.GuildChannel] | None = None
        self._categories_cache: list[discord.CategoryChannel] | None = None
        self._emojis_cache: list[discord.Emoji] | None = None
        self._stickers_cache: list[discord.GuildSticker] | None = None

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
        try:
            logger.info(f"Fetching guild {self.server_id}...")
            self.guild = await self.client.fetch_guild(self.server_id)
            logger.info(f"Successfully fetched guild: {self.guild.name}")
        except discord.Forbidden:
            logger.error(f"403 Forbidden: Missing Access to fetch guild {self.server_id}.")
            raise
        
        # Pre-fetch roles via API - Handle Forbidden gracefully
        try:
            roles = await self.guild.fetch_roles()
            self.role_map = {r.id: r.name for r in roles}
            self._roles_cache = [r for r in roles if not r.is_default()]
        except discord.Forbidden:
            logger.warning("403 Forbidden: Missing Access to fetch roles. Continuing without role mapping.")
            self.role_map = {}
        except Exception as e:
            logger.error(f"Failed to fetch roles: {e}")
            self.role_map = {}

        # Pre-fetch channels via API
        try:
            channels = await self.guild.fetch_channels()
            self.channel_name_map = {c.id: c.name for c in channels}
            self._channels_cache = [c for c in channels if not isinstance(c, discord.CategoryChannel)]
            self._categories_cache = [c for c in channels if isinstance(c, discord.CategoryChannel)]
            logger.debug(f"Pre-fetched {len(self.channel_name_map)} channels")
        except discord.Forbidden:
            logger.warning("403 Forbidden: Missing Access to fetch channels. Continuing without channel name mapping.")
            self.channel_name_map = {}
        except Exception as e:
            logger.error(f"Failed to fetch channels: {e}")
            self.channel_name_map = {}

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
        if self._categories_cache is not None:
            return self._categories_cache
        categories = await self.guild.fetch_channels()
        self._categories_cache = [c for c in categories if isinstance(c, discord.CategoryChannel)]
        return self._categories_cache

    async def get_roles(self):
        """Returns all roles in the server (excluding @everyone)."""
        if not self.guild:
            return []
        if self._roles_cache is not None:
            return self._roles_cache
        roles = await self.guild.fetch_roles()
        # Filter out default @everyone role which cannot typically be created
        self._roles_cache = [r for r in roles if not r.is_default()]
        return self._roles_cache

    async def get_emojis(self):
        """Returns all custom emojis in the server."""
        if not self.guild:
            return []
        if self._emojis_cache is not None:
            return self._emojis_cache
        self._emojis_cache = await self.guild.fetch_emojis()
        return self._emojis_cache

    async def get_stickers(self):
        """Returns all custom stickers in the server."""
        if not self.guild:
            return []
        if self._stickers_cache is not None:
            return self._stickers_cache
        self._stickers_cache = await self.guild.fetch_stickers()
        return self._stickers_cache

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
        
        if self._channels_cache is None:
            channels = await self.guild.fetch_channels()
            self._channels_cache = [c for c in channels if not isinstance(c, discord.CategoryChannel)]
        
        all_channels = self._channels_cache
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

    async def fetch_message_history(self, channel_id: int, limit: int = None, after_id: int = None) -> AsyncGenerator[discord.Message, None]:
        """Yields messages from a given channel, optionally handling pagination."""
        channel = await self.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel) or isinstance(channel, discord.Thread):
            after = discord.Object(id=after_id) if after_id else None
            logger.info(f"Fetching message history for {channel.name} ({channel.id}) oldest_first=True after={after_id}")
            # To avoid exploding RAM, we yield items one by one
            async for message in channel.history(limit=limit, oldest_first=True, after=after):
                yield message

    async def download_emoji(self, emoji: discord.Emoji) -> bytes:
        """Downloads a Discord emoji into memory."""
        return await emoji.read()

    async def download_sticker(self, sticker: discord.GuildSticker) -> bytes:
        """Downloads a Discord sticker into memory."""
        return await sticker.read()

    async def download_attachment(self, attachment: discord.Attachment) -> bytes:
        """Downloads a Discord attachment into memory."""
        return await attachment.read()

    async def close(self):
        client = self.client
        self.client = None # Atomic clear
        self.guild = None
        self._roles_cache = None
        self._channels_cache = None
        self._categories_cache = None
        self._emojis_cache = None
        self._stickers_cache = None
        if client:
            try:
                await client.close()
            except Exception as e:
                logger.debug(f"Error closing Discord client: {e}")
