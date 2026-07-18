import asyncio
import io
import logging
from typing import Optional, List, Dict, Any
from fluxer import HTTPClient, Bot, Webhook, Forbidden, File

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
        self._channels_cache: List[Dict[str, Any]] | None = None

    @staticmethod
    async def fetch_guilds(token: str, api_url: str = "default") -> list[tuple[str, str]]:
        """Fetches the list of Fluxer communities the bot is in. Returns list of (label, id)."""
        import asyncio
        from fluxer import HTTPClient, Guild
        
        http_kwargs = {}
        if api_url and api_url != "default":
            http_kwargs["api_url"] = api_url
            
        async with HTTPClient(token, **http_kwargs) as http:
            try:
                guilds_data = await asyncio.wait_for(http.get_current_user_guilds(), timeout=15)
                guilds_list = []
                for g_data in guilds_data:
                    g = Guild.from_data(g_data)
                    label = f"{g.id}-{g.name}"
                    guilds_list.append((label, str(g.id)))
                return guilds_list
            except asyncio.TimeoutError:
                raise RuntimeError("Fetching guilds timed out")
            except Exception as e:
                print(f"Failed to fetch Fluxer communities via HTTP: {e}")
                logger.error(f"Failed to fetch Fluxer communities via HTTP: {e}")
                raise

    async def _get_or_create_webhook(self, channel_id: str) -> Optional[Webhook]:
        """Gets an existing webhook for the channel or creates one."""
        if channel_id in self._webhooks:
            return self._webhooks[channel_id]
        
        assert self.client is not None
        try:
            # 1. Try to find existing webhook named "ReapersWebhook"
            webhooks_data = await self.client.get_channel_webhooks(channel_id)
            for w_data in webhooks_data:
                if w_data.get("name") == "ReapersWebhook":
                    w = Webhook.from_data(w_data, self.client)
                    self._webhooks[channel_id] = w
                    return w
            
            # 2. Create new one if not found
            w_data = await self.client.create_webhook(channel_id, name="ReapersWebhook")
            w = Webhook.from_data(w_data, self.client)
            self._webhooks[channel_id] = w
            return w
        except Exception as e:
            print(f"Failed to manage webhook for channel {channel_id}: {e}")
            logger.error(f"Failed to manage webhook for channel {channel_id}: {e}")
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
        """
        Validate token and community using only HTTP.
        Returns a dict with token, community, permissions, etc.
        """
        import asyncio
        result = {
            "token": False,
            "community": False,
            "bot_name": None,
            "community_name": None,
            "error_reason": None,
            "permissions": {"administrator": False}
        }
        TIMEOUT = 15  # seconds

        try:
            # Build HTTP client with optional custom API URL
            http_kwargs = {}
            if self.api_url and self.api_url != "default":
                http_kwargs["api_url"] = self.api_url

            http = HTTPClient(self.token, **http_kwargs)
            try:
                # 1. Validate token – fetch current user
                me = await asyncio.wait_for(http.get_current_user(), timeout=TIMEOUT)
                result["token"] = True
                result["bot_name"] = me.get("username")
                me_id = int(me["id"])

                # 2. Fetch community metadata and roles concurrently
                guild_data, roles_data = await asyncio.gather(
                    asyncio.wait_for(http.get_guild(self.community_id), timeout=TIMEOUT),
                    asyncio.wait_for(http.get_guild_roles(self.community_id), timeout=TIMEOUT)
                )
                if guild_data:
                    result["community"] = True
                    result["community_name"] = guild_data.get("name")
                    owner_id = int(guild_data.get("owner_id", 0))

                    # 3. Check admin permission (bot is owner or has Administrator bit)
                    try:
                        member_data = await asyncio.wait_for(
                            http.get_guild_member(self.community_id, me_id),
                            timeout=TIMEOUT
                        )
                        member_role_ids = {int(r) for r in member_data.get("roles", [])}
                        computed_perms = 0
                        guild_id_int = int(self.community_id)
                        for r_data in roles_data:
                            r_id = int(r_data["id"])
                            if r_id == guild_id_int or r_id in member_role_ids:
                                computed_perms |= int(r_data.get("permissions", 0))
                        is_admin = (me_id == owner_id) or bool(computed_perms & (1 << 3))
                        result["permissions"]["administrator"] = is_admin
                    except Exception:
                        # Fallback: only owner check
                        result["permissions"]["administrator"] = (me_id == owner_id)
                else:
                    result["error_reason"] = "Community not found"

            except asyncio.TimeoutError:
                result["error_reason"] = "Validation timed out (API unreachable or slow)"
            except Exception as e:
                result["error_reason"] = f"Validation error: {e}"
            finally:
                await http.close()

        except Exception as e:
            result["error_reason"] = str(e)

        return result
    

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
        self._channels_cache = None
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
        if self._channels_cache is not None:
            return self._channels_cache
        assert self.client is not None
        self._channels_cache = await self.client.get_guild_channels(self.community_id)
        return self._channels_cache

    async def send_message(self, channel_id: str, author_name: str, content: str, timestamp: int, author_avatar_url: Optional[str] = None, files: Optional[List[Dict[str, Any]]] = None, reply_to_message_id: Optional[str] = None, is_forwarded: bool = False, embeds: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
        """
        Sends a message to the target channel.
        Uses a webhook to mimic the original author if possible.
        Returns the ID of the sent message if available.
        """
        assert self.client is not None
        logger.debug(f"Fluxer: send_message called for channel {channel_id}, author='{author_name}', content_len={len(content) if content else 0}, files={len(files) if files else 0}, is_forwarded={is_forwarded}")
        
        # Ensure we are ready before sending (wait a bit if needed)
        if not self._ready_event.is_set():
            logger.debug(f"Fluxer: Bot not ready, waiting...")
            try:
                await asyncio.wait_for(self._ready_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(f"Fluxer: Timeout waiting for bot readiness.")
                pass

        # Use webhook for avatar/username spoofing
        logger.debug(f"Fluxer: Resolving webhook for channel {channel_id}...")
        webhook = await self._get_or_create_webhook(channel_id)
        logger.debug(f"Fluxer: Webhook resolved: {webhook.id if webhook else 'None'}")
        
        # Prepare content with subtext timestamp
        # -# is Fluxer/Discord's subtext markdown: small, muted grey text
        prefix = f"-# <t:{timestamp}:D>\n"
        if is_forwarded:
            prefix += "-# ⮫*forwarded*\n"
            
        display_content = content
        if is_forwarded and content:
            display_content = f">>> {content}"
            
        final_content = prefix + display_content if display_content else prefix
        logger.debug(f"Fluxer: Prepared final_content (len {len(final_content)}): {final_content!r}")

        # Convert files to fluxer.File objects
        fluxer_files = None
        if files:
            fluxer_files = [File(io.BytesIO(f["data"]), filename=f["filename"]) for f in files]

        # Normalize embeds (ensure they are dicts, handling fluxer.Embed objects or dicts)
        normalized_embeds = None
        if embeds:
            normalized_embeds = []
            for e in embeds:
                d = e.to_dict() if hasattr(e, "to_dict") else e
                if not isinstance(d, dict):
                    continue
                
                # Heuristic: Skip redundant link previews to avoid "Invalid Embed" errors or duplication.
                # If an embed has a URL that is already in the message content, and no complex fields, skip it.
                if content and d.get("url") and str(d.get("url")) in content:
                    if not d.get("fields") and not d.get("description") and not d.get("title"):
                        logger.debug(f"Fluxer: Skipping redundant link preview embed for {d.get('url')}")
                        continue
                
                normalized_embeds.append(d)
        if not normalized_embeds: normalized_embeds = None

        try:
            # Current limitation: fluxer.py execute_webhook doesn't support 'message_reference' yet.
            # So if we have a reply, we MUST use the bot's direct send method.
            if webhook and not reply_to_message_id:
                logger.debug(f"Fluxer: Sending message via webhook {webhook.id} for user '{author_name}'")
                try:
                    msg = await asyncio.wait_for(
                        webhook.send(
                            content=final_content,
                            username=f"{author_name} (discord)",
                            avatar_url=author_avatar_url,
                            files=fluxer_files,
                            embeds=normalized_embeds,
                            wait=True
                        ),
                        timeout=45.0 # Increased timeout for potential large file uploads
                    )
                    logger.debug(f"Fluxer: Webhook send complete, msg_id={msg.id if msg else 'None'}")
                    return str(msg.id) if msg else None
                except asyncio.TimeoutError:
                    print(f"Fluxer: Webhook send timed out after 45s for channel {channel_id}")
                    logger.error(f"Fluxer: Webhook send timed out after 45s for channel {channel_id}")
                    return None
            else:
                # Use bot direct message (supports files and message_reference)
                # We add the author name to the prefix since bot name won't match
                bot_prefix = f"-# <t:{timestamp}:D>\n"
                if is_forwarded:
                    bot_prefix += "-# ⮫*forwarded*\n"
                bot_prefix += f"-# · {author_name}\n"
                
                final_bot_content = bot_prefix + display_content if display_content else bot_prefix
                
                message_reference = None
                if reply_to_message_id:
                    message_reference = {"message_id": str(reply_to_message_id), "channel_id": str(channel_id)}

                logger.debug(f"Fluxer: Sending message via bot for user '{author_name}'")
                try:
                    kwargs = {
                        "channel_id": channel_id,
                        "content": final_bot_content,
                        "embeds": normalized_embeds
                    }
                    if fluxer_files:
                        kwargs["files"] = fluxer_files
                    if message_reference:
                        kwargs["message_reference"] = message_reference

                    msg_data = await asyncio.wait_for(
                        self.client.send_message(**kwargs),
                        timeout=45.0
                    )
                    logger.debug(f"Fluxer: Bot send complete, msg_id={msg_data.get('id') if msg_data else 'None'}")
                    return str(msg_data["id"]) if msg_data else None
                except asyncio.TimeoutError:
                    print(f"Fluxer: Bot send timed out after 45s for channel {channel_id}")
                    logger.error(f"Fluxer: Bot send timed out after 45s for channel {channel_id}")
                    return None
        except Exception as e:
            err_msg = f"Failed to copy message to Fluxer: {e}"
            if hasattr(e, 'errors') and e.errors:
                err_msg += f" - Details: {e.errors}"
            logger.error(err_msg)
            return None

    async def send_marker(self, channel_id: str, content: str, files: list[dict] | None = None, reply_to_message_id: Optional[str] = None) -> Optional[str]:
        """
        Sends a simple marker message (e.g., thread start/end) using the bot directly.
        """
        assert self.client is not None
        
        fluxer_files = None
        if files:
            fluxer_files = [f if hasattr(f, "filename") else File(io.BytesIO(f["data"]), filename=f["filename"]) for f in files]
        
        message_reference = None
        if reply_to_message_id:
            message_reference = {"message_id": str(reply_to_message_id), "channel_id": str(channel_id)}

        try:
            kwargs = {
                "channel_id": channel_id,
                "content": content
            }
            if fluxer_files:
                kwargs["files"] = fluxer_files
            if message_reference:
                kwargs["message_reference"] = message_reference

            msg_data = await self.client.send_message(**kwargs)
            return str(msg_data["id"]) if msg_data else None
        except Exception as e:
            print(f"Failed to send marker: {e}")
            logger.error(f"Failed to send marker: {e}")
            return None

    async def create_role(self, name: str, color: int, hoist: bool, mentionable: bool, permissions: int, position: Optional[int] = None) -> str:
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
                permissions=permissions,
                position=position
            )
            return str(role["id"])
        except Exception as e:
            print(f"Failed to copy role {name}: {e}")
            logger.error(f"Failed to copy role {name}: {e}")
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
                content_type = "image/png"
            elif banner.startswith(b"\xff\xd8\xff"):
                content_type = "image/jpeg"
            elif banner.startswith(b"GIF89a") or banner.startswith(b"GIF87a"):
                content_type = "image/gif"
            else:
                content_type = "image/png"
            kwargs["banner"] = f"data:{content_type};base64,{image_data}"

        try:
            await self.client.modify_guild(
                guild_id=self.community_id,
                name=name,
                icon=icon,
                **kwargs
            )
        except Exception as e:
            print(f"Failed to update community metadata: {e}")
            logger.error(f"Failed to update community metadata: {e}")

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
                logger.error(f"Failed to remove community icon: {e}")

        # 3. Remove banner if set
        if has_banner:
            try:
                await self.client.modify_guild(
                    guild_id=self.community_id,
                    banner=None
                )
            except Exception as e:
                print(f"Failed to remove community banner: {e}")
                logger.error(f"Failed to remove community banner: {e}")

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
                logger.error(f"Failed to delete channel {ch.get('name')}: {e}")
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
                        print(f"Failed to delete overwrite {ow['id']} for channel {ch['id']}: {e}")
                        logger.error(f"Failed to delete overwrite {ow['id']} for channel {ch['id']}: {e}")
                processed += 1
                if progress_callback:
                    await progress_callback(ch.get("name", "Unknown"), processed, total)
            except Exception as e:
                print(f"Failed to reset permissions for channel {ch.get('name')}: {e}")
                logger.error(f"Failed to reset permissions for channel {ch.get('name')}: {e}")
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
            print(f"Failed to set permission on channel {channel_id} for overwrite {overwrite_id}: {e}")
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
                logger.error(f"Failed to delete role {role.get('name')}: {e}")
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
                    logger.error(f"Failed to delete emoji {emoji.get('name')}: {e}")
        except Exception as e:
            print(f"Failed to fetch emojis: {e}")
            logger.error(f"Failed to fetch emojis: {e}")

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
                    logger.error(f"Failed to delete sticker {sticker.get('name')}: {e}")
        except Exception as e:
            print(f"Failed to fetch stickers: {e}")
            logger.error(f"Failed to fetch stickers: {e}")

        return {"emojis": emoji_deleted, "stickers": sticker_deleted}


    async def close(self):
        """Cleanly close connection and stop bot task."""
        bot = self.bot
        self.bot = None # Atomic clear
        self._channels_cache = None
        self._webhooks.clear()

        if bot:
            try:
                await bot.close()
            except Exception as e:
                logger.debug(f"Error closing Fluxer bot: {e}")
                
        if self._bot_task:
            task = self._bot_task
            self._bot_task = None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._ready_event.clear()


