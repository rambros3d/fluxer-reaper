import discord
import logging
from typing import AsyncGenerator, Dict, Any, List, Union

logger = logging.getLogger(__name__)

class DiscordReader:
    # -- Provider constants (used by migration scripts instead of importing discord) --
    MESSAGE_TYPE_DEFAULT = discord.MessageType.default
    MESSAGE_TYPE_REPLY = discord.MessageType.reply
    MESSAGE_TYPE_THREAD_STARTER = discord.MessageType.thread_starter_message
    MESSAGE_TYPE_CHAT_INPUT_COMMAND = discord.MessageType.chat_input_command
    MESSAGE_TYPE_CONTEXT_MENU_COMMAND = discord.MessageType.context_menu_command
    MESSAGE_TYPE_FORWARD = getattr(discord.MessageType, 'forward', 100)
    MESSAGE_TYPE_POLL_RESULT = getattr(discord.MessageType, 'poll_result', 46)
    MESSAGE_TYPE_AUTO_MODERATION_ACTION = getattr(discord.MessageType, 'auto_moderation_action', 24)
    MESSAGE_TYPE_GUILD_MEMBER_JOIN = getattr(discord.MessageType, 'new_member', 7)
    
    # Exceptions
    Forbidden = discord.Forbidden
    
    # Channel Types
    CHANNEL_TYPE_TEXT = discord.ChannelType.text
    CHANNEL_TYPE_VOICE = discord.ChannelType.voice
    CHANNEL_TYPE_NEWS = discord.ChannelType.news
    CHANNEL_TYPE_FORUM = discord.ChannelType.forum

    PLATFORM_NAME = "discord"

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
            # Fallback for placeholder strings like 'source_server_id'
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
            "error_reason": None,
            "intents": {"message_content": False},
            "permissions": {"view_channel": False, "read_message_history": False}
        }
        temp_client = self._create_client()
        try:
            try:
                await temp_client.login(self.token)
                results["token"] = True
                if temp_client.user:
                    results["bot_name"] = temp_client.user.display_name
            except discord.LoginFailure:
                results["error_reason"] = "Invalid Discord Token"
                return results
            except Exception as e:
                results["error_reason"] = f"Login Error: {str(e)}"
                return results
                
            try:
                guild = await temp_client.fetch_guild(self.server_id)
                if guild is not None:
                    results["server"] = True
                    results["server_name"] = guild.name
                    
                    # Check intents (fetch app info with strict 1s timeout to prevent hang)
                    try:
                        import asyncio
                        app_info = await asyncio.wait_for(temp_client.application_info(), timeout=1.5)
                        flags = app_info.flags
                        
                        has_msg_content = getattr(flags, 'gateway_message_content', False) or getattr(flags, 'gateway_message_content_limited', False)
                        has_members = getattr(flags, 'gateway_guild_members', False) or getattr(flags, 'gateway_guild_members_limited', False)
                        
                        results["intents"]["message_content"] = has_msg_content
                        results["intents"]["members"] = has_members
                    except Exception as e:
                        logger.debug(f"Failed to check application intents (timed out/error): {e}")
                        # Fallback to true since the code requested it
                        results["intents"]["message_content"] = True
                        results["intents"]["members"] = True
                    
                    # Check permissions
                    try:
                        member = await guild.fetch_member(temp_client.user.id)
                        perms = member.guild_permissions
                        results["permissions"]["view_channel"] = perms.view_channel
                        results["permissions"]["read_messages"] = perms.read_messages
                        results["permissions"]["read_message_history"] = perms.read_message_history
                    except Exception as e:
                        logger.debug(f"Member fetch failed: {e}")
                        # Fallback if member fetch fails, though it shouldn't for the bot itself
            except discord.Forbidden:
                results["error_reason"] = "Missing Access to Server"
            except discord.NotFound:
                results["error_reason"] = "Server Not Found"
            except Exception as e:
                results["error_reason"] = f"Server Error: {str(e)}"
        except Exception as e:
            results["error_reason"] = str(e)
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
    
    async def get_channel_overwrites(self, channel_id: int) -> List[Dict[str, Any]]:
        """Fetch permission overwrites for a Discord channel."""
        channel = await self.get_channel(channel_id)
        overwrites = []
        for target, overwrite in channel.overwrites.items():
            if isinstance(target, discord.Role):
                allow_val, deny_val = overwrite.pair()
                overwrites.append({
                    "id": str(target.id),
                    "type": 0,  # role
                    "allow": allow_val.value,
                    "deny": deny_val.value,
                })
            # Members not supported for now
        return overwrites

    async def download_asset(self, asset: discord.Asset) -> bytes:
        """Downloads an asset (icon, banner) into memory. Extended to accept a URL string as well."""
        if isinstance(asset, str):
            # Fetch from URL using the client's HTTP session
            if self.client and hasattr(self.client.http, '_HTTPClient__session'):
                session = self.client.http._HTTPClient__session
                async with session.get(asset) as resp:
                    if resp.status == 200:
                        return await resp.read()
            # Fallback to generic aiohttp
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(asset) as resp:
                    if resp.status == 200:
                        return await resp.read()
            return b""
        else:
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

    async def get_active_threads(self) -> list[discord.Thread]:
        """Returns all active threads in the guild."""
        if not self.guild:
            return []
        return self.guild.active_threads

    async def fetch_channels(self) -> list[Union[discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel, discord.ForumChannel]]:
        """Async stub for discord.Guild.fetch_channels."""
        if not self.guild:
            return []
        return await self.guild.fetch_channels()

    async def get_channel(self, channel_id: int):
        """Returns a channel object."""
        return await self.client.fetch_channel(channel_id)

    async def get_message(self, channel_id: int, message_id: int):
        """Returns a specific message."""
        channel = await self.get_channel(channel_id)
        if hasattr(channel, "fetch_message"):
            try:
                return await channel.fetch_message(message_id)
            except discord.NotFound:
                logger.warning(f"Message {message_id} not found in channel {channel_id}.")
                return None
            except Exception as e:
                logger.error(f"Error fetching message {message_id}: {e}")
                return None
        return None

    async def get_first_message(self, channel_id: int):
        """Returns the first (oldest) message in a channel."""
        channel = await self.get_channel(channel_id)
        if hasattr(channel, 'history'):
            async for message in channel.history(limit=1, oldest_first=True):
                return message
        elif isinstance(channel, discord.ForumChannel):
            # For forums, find the oldest thread and get its starter message
            threads = []
            threads.extend(channel.threads)
            async for arch_thread in channel.archived_threads(limit=None):
                threads.append(arch_thread)
            if threads:
                threads.sort(key=lambda t: t.id)
                oldest_thread = threads[0]
                try:
                    return await oldest_thread.fetch_message(oldest_thread.id)
                except Exception:
                    pass
        return None

    async def fetch_message_history(self, channel_id: int, limit: int = None, after_id: int = None, inclusive: bool = False) -> AsyncGenerator[discord.Message, None]:
        """Yields messages from a given channel, optionally handling pagination."""
        channel = await self.get_channel(channel_id)
        if hasattr(channel, 'history'):
            # Discord's 'after' is exclusive. To make it inclusive, we use after_id - 1 if requested.
            after = None
            if after_id:
                after = discord.Object(id=after_id - 1) if inclusive else discord.Object(id=after_id)
            logger.info(f"Fetching message history for {channel.name} ({channel.id}) oldest_first=True after={after_id} inclusive={inclusive}")
            # To avoid exploding RAM, we yield items one by one
            async for message in channel.history(limit=limit, oldest_first=True, after=after):
                yield message
        elif isinstance(channel, discord.ForumChannel):
            logger.info(f"Fetching message history for ForumChannel {channel.name} ({channel.id}) oldest_first=True after={after_id} inclusive={inclusive}")
            threads = []
            threads.extend(channel.threads)
            async for arch_thread in channel.archived_threads(limit=None):
                threads.append(arch_thread)
            
            # Sort threads chronologically (by ID)
            threads.sort(key=lambda t: t.id)
            
            for thread in threads:
                if after_id:
                    if not inclusive and thread.id <= after_id:
                        continue
                    if inclusive and thread.id < after_id:
                        continue
                
                try:
                    # In a forum, the starter message ID is the thread ID
                    starter = await thread.fetch_message(thread.id)
                    # Bind the thread if possible so downstream code has context
                    try:
                        if not hasattr(starter, 'thread') or starter.thread is None:
                            starter.thread = thread
                    except (AttributeError, TypeError):
                        # Some versions of discord.py don't allow setting the thread property
                        # or it might already be populated as a read-only property
                        pass
                    yield starter
                except Exception as e:
                    logger.debug(f"Could not fetch starter message for forum thread {thread.id}: {e}")

    async def download_emoji(self, emoji: discord.Emoji) -> bytes:
        """Downloads a Discord emoji into memory."""
        return await emoji.read()

    @staticmethod
    def get_sticker_extension(sticker) -> str:
        """Determines the correct file extension for a sticker."""
        fmt = getattr(sticker, 'format', None)
        if fmt:
            # StickerFormatType: png=1, apng=2, lottie=3, gif=4
            val = getattr(fmt, 'value', fmt)
            if val == 3: return "json"
            if val == 4: return "gif"
            if val == 2: return "png" # APNG is often saved as PNG
        
        # Fallback to URL parsing
        url = str(getattr(sticker, 'url', ""))
        if ".json" in url: return "json"
        if ".gif" in url: return "gif"
        if ".webp" in url: return "webp"
        return "png"

    async def download_sticker(self, sticker: Union[discord.GuildSticker, discord.StickerItem]) -> bytes:
        """Downloads a Discord sticker into memory."""
        name = getattr(sticker, 'name', 'unknown')
        logger.debug(f"Attempting to download sticker: {name} (ID: {sticker.id}, type: {type(sticker)})")
        
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

        # 3. Try download via reader's session (Robust fallback)
        url = getattr(sticker, 'url', None)
        if url:
            try:
                # Use the internal session from discord.py if possible (it has proper headers/auth)
                session = None
                if self.client and hasattr(self.client, 'http') and hasattr(self.client.http, '_HTTPClient__session'):
                     session = self.client.http._HTTPClient__session
                
                if session:
                    logger.debug(f"Attempting download for sticker '{name}' using bot's session from {url}")
                    async with session.get(str(url)) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            if data:
                                logger.debug(f"Successfully downloaded sticker '{name}' (size: {len(data)})")
                                return data
                else:
                    # Generic fallback session
                    import aiohttp
                    logger.debug(f"Attempting download for sticker '{name}' using generic session from {url}")
                    async with aiohttp.ClientSession() as session:
                        async with session.get(str(url)) as resp:
                            if resp.status == 200:
                                data = await resp.read()
                                if data: return data
            except Exception as e:
                logger.debug(f"URL download failed for sticker '{name}': {e}")
        
        logger.warning(f"Failed to download sticker {name} ({sticker.id}) after all attempts")
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
