import asyncio
from typing import Optional, List, Dict, Any
from fluxer import Bot

class FluxerWriter:
    def __init__(self, token: str, community_id: str):
        self.token = token
        self.community_id = str(community_id)
        self.bot: Optional[Bot] = None
        self._bot_task: Optional[asyncio.Task] = None
        self._ready_event = asyncio.Event()

    async def start(self):
        """Authenticate with Fluxer and start the background bot session."""
        if self.bot and self._bot_task and not self._bot_task.done():
            return

        self.bot = Bot()
        self._ready_event.clear()

        # Define a simple on_ready listener to signal when we're connected
        @self.bot.event
        async def on_ready():
            self._ready_event.set()

        # Start the bot in the background
        self._bot_task = asyncio.create_task(self.bot.start(self.token))
        
        # Wait for the bot to be ready (timeout of 10s to be safe)
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            # If it's not ready, it might still work for some REST calls, 
            # but send_message might fail later.
            pass

    @property
    def client(self):
        """Helper to access the underlying HTTP client."""
        return self.bot._http if self.bot else None

    async def validate(self) -> dict:
        """Validates the token and community ID."""
        if not self.bot or not self._ready_event.is_set():
            await self.start()
        
        is_token_valid = False
        is_community_valid = False
        bot_name = None
        community_name = None
        try:
            # Check token by fetching me
            if self.bot and self.bot.user:
                is_token_valid = True
                bot_name = self.bot.user.username
            else:
                # Fallback if on_ready didn't fire yet but we want to check token
                me = await self.client.get_current_user()
                if me:
                    is_token_valid = True
                    bot_name = me.get("username")
            
            # Check community
            guild = await self.client.get_guild(self.community_id)
            if guild:
                is_community_valid = True
                community_name = guild.get("name")
        except Exception:
            pass
            
        return {
            "token": is_token_valid,
            "community": is_community_valid,
            "bot_name": bot_name,
            "community_name": community_name
        }

    async def create_channel(self, name: str, topic: str = "", type: int = 0, parent_id: Optional[str] = None) -> str:
        """
        Creates a new channel in the target Fluxer community.
        Returns the new Fluxer channel ID.
        """
        assert self.client is not None
        
        guild_channel = await self.client.create_guild_channel(
            guild_id=self.community_id,
            name=name,
            type=type,
            topic=topic or None,
            parent_id=parent_id
        )
        return str(guild_channel["id"])

    async def get_channels(self) -> List[Dict[str, Any]]:
        """Returns all channels in the community."""
        assert self.client is not None
        return await self.client.get_guild_channels(self.community_id)

    async def send_message(self, channel_id: str, author_name: str, content: str, timestamp: str, files: Optional[List[Dict[str, Any]]] = None) -> None:
        """
        Sends a message to the target channel.
        """
        assert self.client is not None
        
        # Ensure we are ready before sending (wait a bit if needed)
        if not self._ready_event.is_set():
            try:
                await asyncio.wait_for(self._ready_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

        prefix = f"**[{timestamp}] {author_name}**:\n"
        final_content = prefix + content if content else prefix
        
        try:
            await self.client.send_message(
                channel_id=channel_id,
                content=final_content,
                files=files
            )
        except Exception as e:
            # Handle empty messages if an attachment is the only content
            err_msg = f"Failed to copy message: {e}"
            if hasattr(e, 'errors') and e.errors:
                err_msg += f" - Details: {e.errors}"
            print(err_msg)

    async def create_role(self, name: str, color: int, hoist: bool, mentionable: bool) -> str:
        """
        Creates a new role in the Fluxer community.
        Returns the new Fluxer role ID.
        """
        assert self.client is not None
        
        try:
            role = await self.client.create_guild_role(
                guild_id=self.community_id,
                name=name,
                color=color,
                hoist=hoist,
                mentionable=mentionable
            )
            return str(role["id"])
        except Exception as e:
            print(f"Failed to copy role {name}: {e}")
            return ""

    async def create_emoji(self, name: str, image_bytes: bytes) -> str:
        """
        Creates a custom emoji in the Fluxer community.
        """
        assert self.client is not None
        
        try:
            emoji = await self.client.create_guild_emoji(
                guild_id=self.community_id,
                name=name,
                image=image_bytes
            )
            return str(emoji["id"])
        except Exception as e:
            print(f"Failed to copy emoji {name}: {e}")
            return ""

    async def create_sticker(self, name: str, image_bytes: bytes) -> str:
        """
        Creates a custom sticker in the Fluxer community.
        """
        assert self.client is not None
        
        try:
            sticker = await self.client.create_guild_sticker(
                guild_id=self.community_id,
                name=name,
                image=image_bytes
            )
            return str(sticker["id"])
        except Exception as e:
            print(f"Failed to copy sticker {name}: {e}")
            return ""

    async def update_guild_metadata(self, name: Optional[str] = None, icon: Optional[bytes] = None, banner: Optional[bytes] = None) -> None:
        """
        Updates the Fluxer community name, icon, and banner.
        """
        assert self.client is not None
        
        kwargs = {}
        if banner:
            import base64
            image_data = base64.b64encode(banner).decode("ascii")
            if banner.startswith(b"\x89PNG"):
                mime_type = "image/png"
            elif banner.startswith(b"\xff\xd8\xff"):
                mime_type = "image/jpeg"
            elif banner.startswith(b"GIF89a") or banner.startswith(b"GIF87a"):
                mime_type = "image/gif"
            else:
                mime_type = "image/png"
            kwargs["banner"] = f"data:{mime_type};base64,{image_data}"

        try:
            await self.client.modify_guild(
                guild_id=self.community_id,
                name=name,
                icon=icon,
                **kwargs
            )
        except Exception as e:
            print(f"Failed to update community metadata: {e}")

    async def close(self):
        """Cleanly close connection and stop bot task."""
        if self.bot:
            await self.bot.close()
        if self._bot_task:
            self._bot_task.cancel()
            try:
                await self._bot_task
            except asyncio.CancelledError:
                pass
        self._ready_event.clear()

