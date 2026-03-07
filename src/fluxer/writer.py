import asyncio
import io
import logging
from typing import Optional, List, Dict, Any
from fluxer import Bot, Webhook, Forbidden, File

logger = logging.getLogger(__name__)

class FluxerWriter:
    def __init__(self, token: str, community_id: str, api_url: str = "default"):
        self.token = token
        self.community_id = str(community_id)
        self.api_url = api_url
        self.bot: Optional[Bot] = None
        self._bot_task: Optional[asyncio.Task] = None
        self._ready_event = asyncio.Event()
        self._webhooks: Dict[str, Webhook] = {} # channel_id -> Webhook

    @staticmethod
    async def fetch_guilds(token: str, api_url: str = "default") -> list[tuple[str, str]]:
        """Fetches the list of Fluxer communities the bot is in. Returns list of (label, id)."""
        from fluxer import HTTPClient, Guild
        
        http_kwargs = {}
        if api_url and api_url != "default":
            http_kwargs["api_url"] = api_url
            
        async with HTTPClient(token, **http_kwargs) as http:
            try:
                guilds_data = await http.get_current_user_guilds()
                guilds_list = []
                for g_data in guilds_data:
                    g = Guild.from_data(g_data)
                    label = f"{g.id}-{g.name}"
                    guilds_list.append((label, str(g.id)))
                return guilds_list
            except Exception as e:
                logger.error(f"Failed to fetch Fluxer communities via HTTP: {e}")
                raise

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

        bot_kwargs = {}
        if self.api_url and self.api_url != "default":
            bot_kwargs["api_url"] = self.api_url
            
        self.bot = Bot(**bot_kwargs)
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
        """Validates the token, community ID, and permissions."""
        if not self.bot or not self._ready_event.is_set():
            await self.start()
        
        is_token_valid = False
        is_community_valid = False
        bot_name = None
        community_name = None
        permissions = {
            "manage_channels": False,
            "manage_messages": False,
            "manage_roles": False,
            "manage_emojis_stickers": False,
            "manage_webhooks": False
        }

        try:
            # Check token by fetching me
            me_id = None
            if self.bot and self.bot.user:
                is_token_valid = True
                bot_name = self.bot.user.username
                me_id = self.bot.user.id
            else:
                me = await self.client.get_current_user()
                if me:
                    is_token_valid = True
                    bot_name = me.get("username")
                    me_id = int(me["id"])
            
            # Check community
            guild_data = await self.client.get_guild(self.community_id)
            if guild_data:
                is_community_valid = True
                community_name = guild_data.get("name")

                if me_id:
                    try:
                        # Fetch member to get roles
                        member_data = await self.client.get_guild_member(self.community_id, me_id)
                        member_role_ids = [int(r) for r in member_data.get("roles", [])]
                        
                        # Fetch all roles to get their permissions
                        all_roles_data = await self.client.get_guild_roles(self.community_id)
                        
                        # Calculate total permissions
                        # In Discord/Fluxer, permissions are additive
                        total_perms = 0
                        fluxer_guild_id = 0
                        try:
                            fluxer_guild_id = int(self.community_id)
                        except (ValueError, TypeError):
                            pass

                        for r_data in all_roles_data:
                            r_id = int(r_data["id"])
                            # Add @everyone permissions (role ID same as guild ID)
                            if r_id == fluxer_guild_id or r_id in member_role_ids:
                                total_perms |= int(r_data.get("permissions", 0))
                        
                        # Debugging
                        # print(f"DEBUG: me_id={me_id}, roles={member_role_ids}, total_perms={total_perms}")
                        
                        # Bitmask Mapping (Discord standard)
                        # Administrator: 1 << 3
                        # Manage Channels: 1 << 4
                        # Manage Messages: 1 << 13
                        # Manage Roles: 1 << 28
                        # Manage Webhooks: 1 << 29
                        # Manage Emojis/Stickers: 1 << 30
                        is_admin = bool(total_perms & (1 << 3))
                        
                        permissions["manage_channels"] = is_admin or bool(total_perms & (1 << 4))
                        permissions["manage_messages"] = is_admin or bool(total_perms & (1 << 13))
                        permissions["manage_roles"] = is_admin or bool(total_perms & (1 << 28))
                        permissions["manage_webhooks"] = is_admin or bool(total_perms & (1 << 29))
                        permissions["manage_emojis_stickers"] = is_admin or bool(total_perms & (1 << 30))
                        
                        if is_admin:
                             logger.info(f"Fluxer bot {bot_name} has Administrator permission.")
                    except Exception as e:
                        logger.error(f"Failed to calculate Fluxer permissions: {e}")
        except Exception:
            pass
            
        return {
            "token": is_token_valid,
            "community": is_community_valid,
            "bot_name": bot_name,
            "community_name": community_name,
            "permissions": permissions
        }

    async def create_channel(self, name: str, topic: str = "", type: int = 0, parent_id: Optional[str] = None, nsfw: bool = False, slowmode_delay: int = 0, position: Optional[int] = None) -> str:
        """
        Creates a new channel in the target Fluxer community.
        Returns the new Fluxer channel ID.
        """
        assert self.client is not None
        
        logger.debug(f"Fluxer: Creating channel {name} (type {type}) with topic='{topic}', nsfw={nsfw}, slowmode={slowmode_delay}, position={position}")
        
        guild_channel = await self.client.create_guild_channel(
            guild_id=self.community_id,
            name=name,
            type=type,
            topic=topic or None,
            parent_id=parent_id,
            nsfw=nsfw,
            rate_limit_per_user=slowmode_delay,
            position=position
        )
        return str(guild_channel["id"])

    async def modify_channel(self, channel_id: str, parent_id: Optional[str] = None, name: Optional[str] = None, topic: Optional[str] = None, nsfw: Optional[bool] = None, slowmode_delay: Optional[int] = None, position: Optional[int] = None) -> bool:
        """
        Updates channel properties.
        """
        assert self.client is not None
        
        logger.debug(f"Fluxer: Modifying channel {channel_id}: name={name}, topic='{topic}', parent_id={parent_id}, nsfw={nsfw}, slowmode={slowmode_delay}, position={position}")
        
        try:
            await self.client.modify_channel(
                channel_id=channel_id,
                name=name,
                topic=topic,
                parent_id=parent_id,
                nsfw=nsfw,
                rate_limit_per_user=slowmode_delay,
                position=position
            )
        except Forbidden as e:
            if getattr(e, 'code', None) == "NSFW_CONTENT_AGE_RESTRICTED":
                logger.warning(f"Fluxer: Could not update certain properties (likely NSFW) on channel {channel_id}: {e.message}")
                return False
            raise
        return True

    async def move_channel(self, channel_id: str, parent_id: Optional[str]) -> bool:
        """
        Backward compatibility for moving a channel to a category.
        """
        return await self.modify_channel(channel_id, parent_id=parent_id)

    async def get_channels(self) -> List[Dict[str, Any]]:
        """Returns all channels in the community."""
        assert self.client is not None
        return await self.client.get_guild_channels(self.community_id)

    async def send_message(self, channel_id: str, author_name: str, content: str, timestamp: int, author_avatar_url: Optional[str] = None, files: Optional[List[Dict[str, Any]]] = None, reply_to_message_id: Optional[str] = None, is_forwarded: bool = False, embeds: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
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
        prefix = f"-# <t:{timestamp}:D>\n"
        if is_forwarded:
            prefix += "-# -->*forwarded*\n"
            
        display_content = content
        if is_forwarded and content:
            display_content = f">>> {content}"
            
        final_content = prefix + display_content if display_content else prefix

        # Convert files to fluxer.File objects
        fluxer_files = None
        if files:
            fluxer_files = [File(io.BytesIO(f["data"]), filename=f["filename"]) for f in files]

        # Normalize embeds (ensure they are dicts, handling fluxer.Embed objects or dicts)
        normalized_embeds = None
        if embeds:
            normalized_embeds = []
            for e in embeds:
                if hasattr(e, "to_dict"):
                    normalized_embeds.append(e.to_dict())
                else:
                    normalized_embeds.append(e)

        try:
            # Current limitation: fluxer.py execute_webhook doesn't support 'message_reference' yet.
            # So if we have a reply, we MUST use the bot's direct send method.
            if webhook and not reply_to_message_id:
                msg = await webhook.send(
                    content=final_content,
                    username=f"{author_name} (discord)",
                    avatar_url=author_avatar_url,
                    files=fluxer_files,
                    embeds=normalized_embeds,
                    wait=True
                )
                return str(msg.id) if msg else None
            else:
                # Use bot direct message (supports files and message_reference)
                # We add the author name to the prefix since bot name won't match
                bot_prefix = f"-# <t:{timestamp}:D>\n"
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
                    files=fluxer_files,
                    embeds=normalized_embeds,
                    message_reference=message_reference
                )
                return str(msg_data["id"]) if msg_data else None
        except Exception as e:
            err_msg = f"Failed to copy message: {e}"
            if hasattr(e, 'errors') and e.errors:
                err_msg += f" - Details: {e.errors}"
            print(err_msg)
            return None

    async def send_marker(self, channel_id: str, content: str, files: list[dict] | None = None, reply_to_message_id: Optional[str] = None) -> Optional[str]:
        """
        Sends a simple marker message (e.g., thread start/end) using the bot directly.
        """
        assert self.client is not None
        
        fluxer_files = None
        if files:
            fluxer_files = [File(io.BytesIO(f["data"]), filename=f["filename"]) for f in files]
        
        message_reference = None
        if reply_to_message_id:
            message_reference = {"message_id": str(reply_to_message_id), "channel_id": str(channel_id)}

        try:
            msg_data = await self.client.send_message(
                channel_id=channel_id,
                content=content,
                files=fluxer_files,
                message_reference=message_reference
            )
            return str(msg_data["id"]) if msg_data else None
        except Exception as e:
            print(f"Failed to send marker: {e}")
            return None

    async def create_role(self, name: str, color: int, hoist: bool, mentionable: bool, position: Optional[int] = None) -> str:
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
                mentionable=mentionable,
                position=position
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
            name = str(ch.get("name", "")).lower()
            if name in ["reaper-logs", "reaper_logs"]:
                logger.info(f"Danger Zone: Skipping deletion of audit channel {name}")
                total -= 1
                continue

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
            name = str(ch.get("name", "")).lower()
            if name in ["reaper-logs", "reaper_logs"]:
                logger.info(f"Danger Zone: Skipping permission reset for audit channel {name}")
                total -= 1
                continue

            try:
                # Fetch existing overwrites and delete each one
                overwrites = ch.get("permission_overwrites", [])
                for ow in overwrites:
                    try:
                        await self.client.request(
                            self.client._route(
                                "DELETE", 
                                "/channels/{channel_id}/permissions/{overwrite_id}",
                                channel_id=ch["id"],
                                overwrite_id=ow["id"]
                            )
                        )
                    except Exception as e:
                        logger.error(f"Failed to delete overwrite {ow['id']} for channel {ch['id']}: {e}")
                processed += 1
                if progress_callback:
                    await progress_callback(ch.get("name", "Unknown"), processed, total)
            except Exception as e:
                print(f"Failed to reset permissions for channel {ch.get('name')}: {e}")
        return processed

    async def set_channel_permission(self, channel_id: str, overwrite_id: str, allow: int, deny: int, is_role: bool = True):
        """Sets a permission overwrite for a channel or category."""
        assert self.client is not None
        try:
            target_channel_id = int(channel_id)
            target_overwrite_id = int(overwrite_id)
        except (ValueError, TypeError):
            logger.warning(f"Fluxer: Skipping permission set for non-numeric ID: channel={channel_id}, overwrite={overwrite_id}")
            return

        try:
            await self.client.edit_channel_permissions(
                channel_id=target_channel_id,
                overwrite_id=target_overwrite_id,
                allow=allow,
                deny=deny,
                type=0 if is_role else 1
            )
        except Exception as e:
            logger.error(f"Failed to set permission on channel {channel_id} for overwrite {overwrite_id}: {e}")


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
            try:
                await self.bot.close()
            except Exception as e:
                logger.debug(f"Error closing Fluxer bot: {e}")
                
        if self._bot_task:
            self._bot_task.cancel()
            try:
                await self._bot_task
            except asyncio.CancelledError:
                pass
        self._ready_event.clear()


