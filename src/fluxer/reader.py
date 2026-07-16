# src/fluxer/reader.py
import asyncio
import logging
from typing import AsyncGenerator, Dict, Any, Optional, List, Union

from fluxer import HTTPClient, Guild, Channel, Message, Role, Emoji, Forbidden

logger = logging.getLogger(__name__)

class FluxerReader:
    """
    Fluxer source reader – mimics DiscordReader interface.
    Uses HTTPClient (no WebSocket) for reading data.
    """
    def __init__(self, token: str, server_id: str, api_url: str = "default"):
        self.token = token
        self.server_id = str(server_id)
        self.api_url = api_url
        self._http: Optional[HTTPClient] = None
        self.guild_data: Optional[Dict] = None
        self.guild: Optional[Guild] = None

    async def _ensure_http(self):
        """Lazy initialise HTTP client and fetch guild."""
        if self._http is None:
            kwargs = {}
            if self.api_url and self.api_url != "default":
                kwargs["api_url"] = self.api_url
            self._http = HTTPClient(self.token, **kwargs)
            # Fetch guild data to cache
            try:
                self.guild_data = await self._http.get_guild(self.server_id)
                self.guild = Guild.from_data(self.guild_data)
                logger.info(f"FluxerReader: connected to community {self.guild.name} ({self.guild.id})")
            except Exception as e:
                await self.close()
                raise RuntimeError(f"Failed to fetch Fluxer community {self.server_id}: {e}") from e

    async def start(self):
        """Starts the reader (lazy load on first method call, but this ensures it's ready)."""
        await self._ensure_http()

    async def validate(self) -> Dict[str, Any]:
        """
        Validates token, server ID, and read permissions.
        Returns dict similar to DiscordReader.validate().
        """
        result = {
            "token": False,
            "server": False,
            "bot_name": None,
            "server_name": None,
            "error_reason": None,
            "intents": {"message_content": True},  # Fluxer has no intent system
            "permissions": {"view_channel": False, "read_message_history": False}
        }
        try:
            # 1. Validate token by fetching current user
            http = HTTPClient(self.token)
            try:
                me = await http.get_current_user()
                result["token"] = True
                result["bot_name"] = me.get("username")
            except Exception as e:
                result["error_reason"] = f"Token error: {e}"
                return result
            finally:
                await http.close()

            # 2. Fetch community
            http2 = HTTPClient(self.token)
            try:
                guild = await http2.get_guild(self.server_id)
                result["server"] = True
                result["server_name"] = guild.get("name")
                # Check if bot can view channels (fetch channels)
                channels = await http2.get_guild_channels(self.server_id)
                # At least one channel => view permission works
                if channels:
                    result["permissions"]["view_channel"] = True
                    # Check read history by trying to fetch messages from first text channel
                    for ch in channels:
                        if ch.get("type") == 0:  # text channel
                            try:
                                msgs = await http2.get_channel_messages(ch["id"], limit=1)
                                if msgs is not None:
                                    result["permissions"]["read_message_history"] = True
                                    break
                            except Exception:
                                pass
            except Forbidden:
                result["error_reason"] = "Missing Access to Community"
            except Exception as e:
                result["error_reason"] = f"Community error: {e}"
            finally:
                await http2.close()

        except Exception as e:
            result["error_reason"] = str(e)

        return result

    async def get_server_metadata(self) -> Dict[str, Any]:
        """Returns name, icon, banner URLs."""
        await self._ensure_http()
        return {
            "name": self.guild.name,
            "id": str(self.guild.id),
            "icon_url": self.guild.icon.url if self.guild.icon else None,
            "banner_url": self.guild.banner.url if self.guild.banner else None
        }

    async def download_asset(self, asset_url: str) -> bytes:
        """Downloads an asset (icon/banner) from a URL."""
        await self._ensure_http()
        if not asset_url:
            return b""
        # Use the http client's session to download
        async with self._http._session.get(asset_url) as resp:
            if resp.status == 200:
                return await resp.read()
        return b""

    async def get_categories(self):
        """Returns list of category channels (type=4)."""
        await self._ensure_http()
        channels = await self._http.get_guild_channels(self.server_id)
        cats = []
        for ch_data in channels:
            if ch_data.get("type") == 4:
                cats.append(Channel.from_data(ch_data))
        return cats

    async def get_roles(self):
        """Returns list of roles (excluding @everyone)."""
        await self._ensure_http()
        roles_data = await self._http.get_guild_roles(self.server_id)
        roles = []
        for r in roles_data:
            role = Role.from_data(r)
            if not role.is_default():
                roles.append(role)
        return roles

    async def get_emojis(self):
        """Returns list of custom emojis."""
        await self._ensure_http()
        emojis_data = await self._http.get_guild_emojis(self.server_id)
        return [Emoji.from_data(e) for e in emojis_data]

    async def get_stickers(self):
        """Returns list of custom stickers."""
        return []  # Fluxer may not support stickers; return empty list for now
        await self._ensure_http()
        stickers_data = await self._http.get_guild_stickers(self.server_id)
        return [Sticker.from_data(s) for s in stickers_data]

    async def get_members(self):
        """
        Returns all members. (May be expensive; implement if needed.)
        Fluxer API may not have a guild-wide members list; you might need to paginate.
        """
        await self._ensure_http()
        # If the API supports get_guild_members, use it; otherwise return empty.
        # For now, we'll try to fetch members (might need pagination).
        # This is a placeholder; you may need to implement pagination with ?limit=1000&after=...
        try:
            members = await self._http.get_guild_members(self.server_id, limit=1000)
            return members  # list of dicts; we could wrap in Member class if needed
        except AttributeError:
            logger.warning("get_guild_members not available in fluxer.py; returning []")
            return []

    async def get_channels(self, category_id: Optional[str] = None):
        """
        Returns all non-category channels.
        If category_id given, filter by parent_id.
        """
        await self._ensure_http()
        channels_data = await self._http.get_guild_channels(self.server_id)
        all_ch = []
        for ch_data in channels_data:
            if ch_data.get("type") == 4:
                continue  # skip categories
            ch = Channel.from_data(ch_data)
            if category_id is not None and ch.parent_id != int(category_id):
                continue
            all_ch.append(ch)
        return all_ch

    async def get_active_threads(self) -> List:
        """Fluxer may not have active threads; return empty list."""
        return []

    async def fetch_channels(self) -> List[Channel]:
        """Returns all channels (including categories)."""
        await self._ensure_http()
        channels_data = await self._http.get_guild_channels(self.server_id)
        return [Channel.from_data(ch) for ch in channels_data]

    async def get_channel(self, channel_id: str):
        """Fetch a single channel by ID."""
        await self._ensure_http()
        ch_data = await self._http.get_channel(channel_id)
        return Channel.from_data(ch_data)

    async def get_message(self, channel_id: str, message_id: str):
        """Fetch a single message."""
        await self._ensure_http()
        try:
            msg_data = await self._http.get_channel_message(channel_id, message_id)
            return Message.from_data(msg_data)
        except Exception:
            return None

    async def get_first_message(self, channel_id: str):
        """Returns the oldest message in the channel."""
        await self._ensure_http()
        try:
            msgs = await self._http.get_channel_messages(channel_id, limit=1, oldest_first=True)
            if msgs:
                return Message.from_data(msgs[0])
        except Exception:
            pass
        return None

    async def fetch_message_history(
        self,
        channel_id: str,
        limit: Optional[int] = None,
        after_id: Optional[str] = None,
        inclusive: bool = False
    ) -> AsyncGenerator[Message, None]:
        """
        Yields messages from oldest to newest.
        Fluxer's get_channel_messages may accept 'after' and 'before' parameters.
        We'll implement pagination by repeatedly fetching up to limit.
        """
        await self._ensure_http()
        fetched = 0
        last_id = None
        while True:
            # Calculate how many to fetch
            to_fetch = 100  # max per request; adjust if Fluxer supports larger
            if limit is not None:
                to_fetch = min(to_fetch, limit - fetched)
                if to_fetch <= 0:
                    break

            # Build params: after= after_id (exclusive) or inclusive? Fluxer might not support inclusive.
            # If inclusive and after_id, we need to fetch messages after that ID.
            # We'll handle inclusive by using after_id as is (it's exclusive) but the caller expects inclusive.
            # We can work around by fetching one extra and skipping if needed.
            params = {}
            if after_id:
                params["after"] = after_id
            # Add limit
            params["limit"] = to_fetch

            try:
                msgs_data = await self._http.get_channel_messages(channel_id, **params)
            except Exception as e:
                logger.error(f"Error fetching messages for channel {channel_id}: {e}")
                break

            if not msgs_data:
                break

            for msg_data in msgs_data:
                # If inclusive and after_id, we might have fetched the message with ID == after_id.
                # We'll skip if inclusive is False and msg.id == after_id (since after_id is exclusive normally)
                # But since we don't know what the server does, we'll just yield all and let the caller handle duplicates.
                msg = Message.from_data(msg_data)
                yield msg
                fetched += 1
                last_id = msg.id
                if limit and fetched >= limit:
                    return

            # If we got fewer than requested, we've reached the end.
            if len(msgs_data) < to_fetch:
                break

            # Set after to the last ID for next iteration (exclusive)
            after_id = last_id

    async def download_emoji(self, emoji: Emoji) -> bytes:
        """Download emoji image from its URL."""
        url = emoji.url
        if not url:
            return b""
        async with self._http._session.get(url) as resp:
            if resp.status == 200:
                return await resp.read()
        return b""

    async def download_sticker(self, sticker) -> bytes:
        return b""  # Fluxer may not support stickers; return empty bytes
    # async def download_sticker(self, sticker: Sticker) -> bytes:
    #     """Download sticker from its URL."""
    #     url = sticker.url
    #     if not url:
    #         return b""
    #     async with self._http._session.get(url) as resp:
    #         if resp.status == 200:
    #             return await resp.read()
    #     return b""

    async def download_attachment(self, attachment_url: str) -> bytes:
        """Download an attachment from a URL."""
        if not attachment_url:
            return b""
        async with self._http._session.get(attachment_url) as resp:
            if resp.status == 200:
                return await resp.read()
        return b""

    async def close(self):
        """Close HTTP client."""
        if self._http:
            await self._http.close()
            self._http = None
        self.guild = None
        self.guild_data = None