import discord
from typing import AsyncGenerator, Dict, Any

class DiscordReader:
    def __init__(self, token: str, server_id: str):
        self.token = token
        self.server_id = int(server_id)
        
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
            
        await self.client.login(self.token)
        # In a real app we'd wait until ready, here we simulate fetching the guild
        self.guild = await self.client.fetch_guild(self.server_id)

    async def validate(self) -> Dict[str, bool]:
        """Validates the token and server ID."""
        results = {"token": False, "server": False}
        temp_client = self._create_client()
        try:
            await temp_client.login(self.token)
            results["token"] = True
            guild = await temp_client.fetch_guild(self.server_id)
            if guild is not None:
                results["server"] = True
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
        # Filter out default @everyone role which cannot typically be created
        return [r for r in roles if not r.is_default()]

    async def get_emojis(self):
        """Returns all custom emojis in the server."""
        if not self.guild:
            return []
        return await self.guild.fetch_emojis()

    async def get_channels(self, category_id: int | None = None):
        """Yields all non-category channels."""
        if not self.guild:
            return []
        channels = await self.guild.fetch_channels()
        all_channels = [c for c in channels if not isinstance(c, discord.CategoryChannel)]
        if category_id:
            all_channels = [c for c in all_channels if c.category_id == category_id]
        return all_channels

    async def fetch_message_history(self, channel_id: int, limit: int = None) -> AsyncGenerator[discord.Message, None]:
        """Yields messages from a given channel, optionally handling pagination."""
        channel = await self.client.fetch_channel(channel_id)
        if isinstance(channel, discord.TextChannel) or isinstance(channel, discord.Thread):
            # To avoid exploding RAM, we yield items one by one
            async for message in channel.history(limit=limit, oldest_first=True):
                yield message

    async def download_emoji(self, emoji: discord.Emoji) -> bytes:
        """Downloads a Discord emoji into memory."""
        return await emoji.read()

    async def download_attachment(self, attachment: discord.Attachment) -> bytes:
        """Downloads a Discord attachment into memory."""
        return await attachment.read()

    async def close(self):
        await self.client.close()
