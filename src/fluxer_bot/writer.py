import asyncio
import logging
from typing import Optional, List, Dict, Any
from fluxer import Bot, Webhook

logger = logging.getLogger(__name__)

class FluxerWriter:
    def __init__(self, token: str, community_id: str):
        self.token = token
        self.community_id = str(community_id)
        self.bot: Optional[Bot] = None
        self._bot_task: Optional[asyncio.Task] = None
        self._ready_event = asyncio.Event()
        self._webhooks: Dict[str, Webhook] = {} # channel_id -> Webhook

    async def _get_or_create_webhook(self, channel_id: str) -> Optional[Webhook]:
        """Gets an existing webhook for the channel or creates one."""
        if channel_id in self._webhooks:
            return self._webhooks[channel_id]
        
        assert self.client is not None
        try:
            # 1. Try to find existing webhook named "Stoat-Migrator"
            webhooks_data = await self.client.get_channel_webhooks(channel_id)
            for w_data in webhooks_data:
                if w_data.get("name") == "Stoat-Migrator":
                    w = Webhook.from_data(w_data, self.client)
                    self._webhooks[channel_id] = w
                    return w
            
            # 2. Create new one if not found
            w_data = await self.client.create_webhook(channel_id, name="Stoat-Migrator")
            w = Webhook.from_data(w_data, self.client)
            self._webhooks[channel_id] = w
            return w
        except Exception as e:
            print(f"Failed to manage webhook for channel {channel_id}: {e}")
            return None

    async def start(self):
        # ... (lines 14-35)
        # (I will use multi_replace or just replace_file_content carefully)
        # Actually I'm using replace_file_content so I need to provide the whole block.
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

    async def move_channel(self, channel_id: str, parent_id: Optional[str]) -> bool:
        """
        Updates the parent category of an existing channel.
        """
        assert self.client is not None
        await self.client.modify_channel(
            channel_id=channel_id,
            parent_id=parent_id
        )
        return True

    async def get_channels(self) -> List[Dict[str, Any]]:
        """Returns all channels in the community."""
        assert self.client is not None
        return await self.client.get_guild_channels(self.community_id)

    async def send_message(self, channel_id: str, author_name: str, content: str, timestamp: str, author_avatar_url: Optional[str] = None, files: Optional[List[Dict[str, Any]]] = None, reply_to_message_id: Optional[str] = None, is_forwarded: bool = False) -> Optional[str]:
        """
        Sends a message to the target channel.
        Uses a webhook to mimic the original author if possible.
        Returns the ID of the sent message if available.
        """
        assert self.client is not None
        
        # Ensure we are ready before sending (wait a bit if needed)
        if not self._ready_event.is_set():
            try:
                await asyncio.wait_for(self._ready_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

        # Use webhook for avatar/username spoofing
        webhook = await self._get_or_create_webhook(channel_id)
        
        # Prepare content with subtext timestamp
        # -# is Fluxer/Discord's subtext markdown: small, muted grey text
        prefix = f"-# {timestamp}\n"
        if is_forwarded:
            prefix += "-# -->*forwarded*\n"
            
        display_content = content
        if is_forwarded and content:
            display_content = f">>> {content}"
            
        final_content = prefix + display_content if display_content else prefix

        try:
            # Current limitation: fluxer.py execute_webhook doesn't support 'files' or 'message_reference' yet.
            # So if we have files OR a reply, we MUST use the bot's direct send method.
            if webhook and not files and not reply_to_message_id:
                msg = await webhook.send(
                    content=final_content,
                    username=f"{author_name} (via Discord)",
                    avatar_url=author_avatar_url,
                    wait=True
                )
                return str(msg.id) if msg else None
            else:
                # Use bot direct message (supports files and message_reference)
                # We add the author name to the prefix since bot name won't match
                bot_prefix = f"-# {timestamp}\n"
                if is_forwarded:
                    bot_prefix += "-# -->*forwarded*\n"
                bot_prefix += f"-# · {author_name}\n"
                
                final_bot_content = bot_prefix + display_content if display_content else bot_prefix
                
                message_reference = None
                if reply_to_message_id:
                    message_reference = {"message_id": str(reply_to_message_id), "channel_id": str(channel_id)}

                msg_data = await self.client.send_message(
                    channel_id=channel_id,
                    content=final_bot_content,
                    files=files,
                    message_reference=message_reference
                )
                return str(msg_data["id"]) if msg_data else None
        except Exception as e:
            err_msg = f"Failed to copy message: {e}"
            if hasattr(e, 'errors') and e.errors:
                err_msg += f" - Details: {e.errors}"
            print(err_msg)
            return None

    async def send_marker(self, channel_id: str, content: str) -> Optional[str]:
        """
        Sends a simple marker message (e.g., thread start/end) using the bot directly.
        """
        assert self.client is not None
        try:
            msg_data = await self.client.send_message(
                channel_id=channel_id,
                content=content
            )
            return str(msg_data["id"]) if msg_data else None
        except Exception as e:
            print(f"Failed to send marker: {e}")
            return None

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
            logger.error(f"Failed to copy emoji '{name}': {e}", exc_info=True)
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
            logger.error(f"Failed to copy sticker '{name}': {e}", exc_info=True)
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

    async def remove_community_logo_and_banner(self) -> dict:
        """
        Removes the community logo (icon) and banner.
        Fetches the current guild state first so it can report whether each
        field was actually set (REMOVED) or already empty (SKIP).

        Correct API calls per Fluxer contract:
            await http.modify_guild(guild_id, icon=None)
            await http.modify_guild(guild_id, banner=None)

        Returns:
            {"icon": "REMOVED"|"SKIP", "banner": "REMOVED"|"SKIP"}
        """
        assert self.client is not None

        # 1. Check current state
        guild = await self.client.get_guild(self.community_id)
        has_icon = bool(guild.get("icon"))
        has_banner = bool(guild.get("banner"))

        # 2. Remove icon if set
        if has_icon:
            try:
                await self.client.modify_guild(
                    guild_id=self.community_id,
                    icon=None
                )
            except Exception as e:
                print(f"Failed to remove community icon: {e}")

        # 3. Remove banner if set
        if has_banner:
            try:
                await self.client.modify_guild(
                    guild_id=self.community_id,
                    banner=None
                )
            except Exception as e:
                print(f"Failed to remove community banner: {e}")

        return {
            "icon": "REMOVED" if has_icon else "SKIP",
            "banner": "REMOVED" if has_banner else "SKIP",
        }

    async def delete_all_channels(self, progress_callback=None) -> int:
        """
        Deletes all channels and categories in the Fluxer community.
        Returns the count of deleted channels.
        """
        assert self.client is not None
        channels = await self.client.get_guild_channels(self.community_id)
        total = len(channels)
        deleted = 0
        # Delete non-category channels first, then categories
        sorted_channels = sorted(channels, key=lambda c: 0 if c.get("type") == 4 else -1)
        for ch in sorted_channels:
            try:
                await self.client.delete_channel(ch["id"])
                deleted += 1
                if progress_callback:
                    await progress_callback(ch.get("name", "Unknown"), deleted, total)
            except Exception as e:
                print(f"Failed to delete channel {ch.get('name')}: {e}")
        return deleted

    async def reset_channel_permissions(self, progress_callback=None) -> int:
        """
        Resets all permission overwrites on every channel and category.
        Returns the count of channels processed.
        """
        assert self.client is not None
        channels = await self.client.get_guild_channels(self.community_id)
        total = len(channels)
        processed = 0
        for ch in channels:
            try:
                # Fetch existing overwrites and delete each one
                overwrites = ch.get("permission_overwrites", [])
                for ow in overwrites:
                    try:
                        await self.client.delete_channel_permission(ch["id"], ow["id"])
                    except Exception:
                        pass
                processed += 1
                if progress_callback:
                    await progress_callback(ch.get("name", "Unknown"), processed, total)
            except Exception as e:
                print(f"Failed to reset permissions for channel {ch.get('name')}: {e}")
        return processed

    async def delete_all_roles(self, progress_callback=None) -> int:
        """
        Deletes all non-managed, non-default roles in the Fluxer community,
        while safely skipping the bot's own managed role.
        Returns the count of deleted roles.
        """
        assert self.client is not None
        
        # Fetch the bot's user ID so we can skip its managed role
        bot_user_id = None
        try:
            if self.bot and self.bot.user:
                bot_user_id = str(self.bot.user.id)
            else:
                me = await self.client.get_current_user()
                if me:
                    bot_user_id = str(me.get("id"))
        except Exception:
            pass

        roles = await self.client.get_guild_roles(self.community_id)
        deletable = []
        for r in roles:
            # Skip @everyone (position 0) and managed roles (e.g. bot roles)
            if r.get("managed") or r.get("name") == "@everyone":
                continue
            deletable.append(r)

        total = len(deletable)
        deleted = 0
        for role in deletable:
            try:
                await self.client.delete_guild_role(self.community_id, role["id"])
                deleted += 1
                if progress_callback:
                    await progress_callback(role.get("name", "Unknown"), deleted, total)
            except Exception as e:
                print(f"Failed to delete role {role.get('name')}: {e}")
        return deleted

    async def delete_all_emojis_and_stickers(self, progress_callback=None) -> dict:
        """
        Deletes all custom emojis and stickers in the Fluxer community.
        Returns {"emojis": int, "stickers": int} with independent counts.
        """
        assert self.client is not None
        emoji_deleted = 0
        sticker_deleted = 0

        # Delete emojis
        try:
            emojis = await self.client.get_guild_emojis(self.community_id)
            emoji_total = len(emojis)
            for emoji in emojis:
                try:
                    await self.client.delete_guild_emoji(self.community_id, emoji["id"])
                    emoji_deleted += 1
                    if progress_callback:
                        await progress_callback(emoji.get("name", "Unknown"), "Emoji", emoji_deleted, emoji_total)
                except Exception as e:
                    print(f"Failed to delete emoji {emoji.get('name')}: {e}")
        except Exception as e:
            print(f"Failed to fetch emojis: {e}")

        # Delete stickers
        try:
            stickers = await self.client.get_guild_stickers(self.community_id)
            sticker_total = len(stickers)
            for sticker in stickers:
                try:
                    await self.client.delete_guild_sticker(self.community_id, sticker["id"])
                    sticker_deleted += 1
                    if progress_callback:
                        await progress_callback(sticker.get("name", "Unknown"), "Sticker", sticker_deleted, sticker_total)
                except Exception as e:
                    print(f"Failed to delete sticker {sticker.get('name')}: {e}")
        except Exception as e:
            print(f"Failed to fetch stickers: {e}")

        return {"emojis": emoji_deleted, "stickers": sticker_deleted}


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

