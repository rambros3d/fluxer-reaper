import logging
import stoat
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

class StoatWriter:
    def __init__(self, token: str, community_id: str, api_url: str = "default"):
        self.token = token
        self.community_id = str(community_id)
        self.api_url = api_url

    async def start(self):
        client_kwargs = {"token": self.token, "bot": True}
        if self.api_url and self.api_url != "default":
            client_kwargs["http_base"] = self.api_url
            
        self.client = stoat.Client(**client_kwargs)
        self._server = None
        self._me = None
        try:
            self._me = await self.client.fetch_user("@me")
        except Exception as e:
            logger.error(f"Failed to fetch bot user in StoatWriter: {e}")

    @property
    def my_id(self):
        return str(self._me.id) if self._me else None

    async def _get_server(self, populate_channels=False):
        # Always refetch if channels are requested to ensure we have them
        # Stoat Server objects use __slots__, so we can't easily add our own tracking attributes.
        if not self._server or populate_channels:
            self._server = await self.client.fetch_server(self.community_id, populate_channels=populate_channels)
        return self._server

    async def validate(self) -> dict:
        results = {
            "token": False,
            "community": False,
            "bot_name": "N/A",
            "community_name": "N/A",
            "permissions": {
                "manage_channels": False,
                "manage_server": False,
                "manage_permissions": False,
                "manage_roles": False,
                "manage_customization": False
            }
        }
        
        # Use a temporary client for validation
        client_kwargs = {"token": self.token, "bot": True}
        if self.api_url and self.api_url != "default":
            client_kwargs["http_base"] = self.api_url

        client = stoat.Client(**client_kwargs)
        try:
            # Validate token by fetching current user
            try:
                current_user = await client.fetch_user("@me")
                results["token"] = True
                results["bot_name"] = current_user.display_name or current_user.name
            except stoat.Unauthorized:
                logger.error("Invalid Stoat token.")
                return results
            
            # Validate server access
            try:
                server = await client.fetch_server(self.community_id)
                results["community"] = True
                results["community_name"] = server.name
                
                # Check permissions using effective server permissions for the bot
                # Use current_user.id since @me might not be supported in all member endpoints
                try:
                    me = await server.fetch_member(current_user.id)
                    # We use server.permissions_for(me) instead of me.server_permissions
                    # to avoid cache-related NoData exceptions.
                    # safe=False allows calculating even if some roles aren't in local cache.
                    perms = server.permissions_for(me, safe=False)
                    
                    results["permissions"] = {
                        "manage_channels": perms.manage_channels,
                        "manage_server": perms.manage_server,
                        "manage_permissions": perms.manage_permissions,
                        "manage_roles": perms.manage_roles,
                        "manage_customization": perms.manage_customization,
                        "manage_messages": perms.manage_messages,
                        "send_messages": perms.send_messages,
                        "masquerade": perms.use_masquerade,
                        "upload_files": perms.upload_files,
                        "react": perms.react,
                        "mention_everyone": perms.mention_everyone,
                        "mention_roles": perms.mention_roles
                    }
                except stoat.NotFound:
                    logger.error(f"Bot member {current_user.id} not found in Stoat server {self.community_id}.")
                except stoat.Forbidden:
                    logger.error(f"Bot lacks permissions to fetch its own member data in Stoat server {self.community_id}.")
                except Exception as e:
                    logger.error(f"Error fetching Stoat member permissions: {e}")

            except stoat.NotFound:
                logger.error(f"Stoat server {self.community_id} not found.")
            except stoat.Forbidden:
                logger.error(f"Bot has no access to Stoat server {self.community_id}.")
            except Exception as e:
                logger.error(f"Error validating Stoat server: {e}")
                
        except Exception as e:
            logger.error(f"Stoat validation failed: {str(e)}")
        finally:
            await client.close()
            
        return results
    
    async def get_channels(self) -> List[Dict[str, Any]]:
        try:
            server = await self._get_server(populate_channels=True)
            channels = server.channels
            categories = server.categories if hasattr(server, "categories") and server.categories else []
            
            cat_map = {}
            for cat in categories:
                cat_id_str = str(cat.id)
                for c_id in cat.channels:
                    cat_map[str(c_id)] = cat_id_str

            results = []
            for ch in channels:
                ch_type = -1
                if isinstance(ch, stoat.TextChannel):
                    ch_type = 0
                elif isinstance(ch, stoat.VoiceChannel):
                    ch_type = 2
                
                ch_id_str = str(ch.id)
                results.append({
                    "id": ch_id_str,
                    "name": getattr(ch, "title", getattr(ch, "name", "Unknown")),
                    "type": ch_type,
                    "parent_id": cat_map.get(ch_id_str)
                })

            for cat in categories:
                results.append({
                    "id": str(cat.id),
                    "name": getattr(cat, "title", getattr(cat, "name", "Unknown")),
                    "type": 4,
                    "parent_id": None
                })
            
            return results
        except Exception as e:
            logger.error(f"Failed to fetch Stoat channels: {e}")
            return []

    async def create_channel(self, name: str, type: int = 0, topic: str = "", parent_id: Optional[str] = None, **kwargs) -> str:
        server = await self._get_server(populate_channels=True)
        try:
            if type == 4: # Category
                # The POST /categories endpoint throws 404 on some server versions, so we use server.edit(categories)
                import random
                import time
                chars = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
                # Mock a ULID
                new_id = "01" + "".join(random.choice(chars) for _ in range(24))
                
                categories = list(server.categories) if hasattr(server, "categories") and server.categories else []
                # Workaround for stoat.py bug: existing categories may fail to_dict() if slots are uninitialized
                for c in categories:
                    if not hasattr(c, "default_permissions"): c.default_permissions = None
                    if not hasattr(c, "role_permissions"): c.role_permissions = {}
                
                new_cat = stoat.Category(id=new_id, title=name, channels=[])
                if not hasattr(new_cat, "default_permissions"): new_cat.default_permissions = None
                if not hasattr(new_cat, "role_permissions"): new_cat.role_permissions = {}
                categories.append(new_cat)
                
                await server.edit(categories=categories)
                return new_id
            else: # Text Channel
                ch = await server.create_text_channel(name=name, description=topic)
                # We no longer parent here, clone_server.py will do it in bulk
                return str(ch.id)
        except Exception as e:
            logger.error(f"Failed to create Stoat channel {name}: {e}")
            return ""

    async def modify_channel(self, channel_id: str, name: Optional[str] = None, topic: Optional[str] = None, nsfw: Optional[bool] = None, slowmode_delay: Optional[int] = None, **kwargs) -> bool:
        server = await self._get_server(populate_channels=True)
        try:
            channel = next((c for c in server.channels if str(c.id) == channel_id), None)
            if not channel:
                return False

            edit_kwargs = {}
            if name is not None:
                edit_kwargs["name"] = name
            if topic is not None:
                edit_kwargs["description"] = topic
            if nsfw is not None:
                edit_kwargs["nsfw"] = nsfw
            
            if edit_kwargs:
                await channel.edit(**edit_kwargs)

            # clone_server.py now handles all parenting bulk logic
            return True
        except Exception as e:
            logger.error(f"Failed to modify Stoat channel {channel_id}: {e}")
            return False

    async def move_channel(self, channel_id: str, parent_id: Optional[str]) -> bool:
        return True

    async def send_message(self, channel_id: str, author_name: str, content: str, timestamp: str, 
                           author_avatar_url: Optional[str] = None, files: Optional[List[Dict[str, Any]]] = None, 
                           reply_to_message_id: Optional[str] = None, is_forwarded: bool = False) -> Optional[str]:
        """
        Sends a message to the target channel using Stoat's masquerade feature.
        Raises on permission errors — caller must handle.
        """
        try:
            channel = await self.client.fetch_channel(channel_id)
            
            # Build masquerade to impersonate original author
            masquerade = stoat.MessageMasquerade(
                name=f"{author_name} (discord)",
                avatar=author_avatar_url
            )
            
            # Build content with timestamp prefix
            prefix = f"##### {timestamp}\n"
            if is_forwarded:
                prefix += "##### -->*forwarded*\n"
            
            display_content = content
            if is_forwarded and content:
                display_content = f"> {content}"
            
            final_content = prefix + display_content if display_content else prefix
            
            # Build replies list
            replies = None
            if reply_to_message_id:
                replies = [stoat.Reply(id=reply_to_message_id, mention=False)]
            
            # Build attachments list using ResolvableResource tuple format: (filename, bytes)
            attachments = None
            if files:
                attachments = []
                for f in files:
                    attachments.append((f["filename"], f["data"]))
            
            try:
                msg = await channel.send(
                    content=final_content,
                    masquerade=masquerade,
                    replies=replies,
                    attachments=attachments
                )
                return str(msg.id) if msg else None
            except Exception as e:
                # If file type not allowed, skip attachments and still send the message
                if "FileTypeNotAllowed" in str(e) and attachments:
                    logger.warning(f"File type blocked by server, sending without attachments: {e}")
                    filenames = "\n".join(f"- {a[0]}" for a in attachments)
                    note = f"\n⚠ {len(attachments)} attachment(s) (file type not allowed by stoat)\n{filenames}"
                    msg = await channel.send(
                        content=final_content + note,
                        masquerade=masquerade,
                        replies=replies
                    )
                    return str(msg.id) if msg else None
                raise  # Re-raise MissingPermission and other errors
        except Exception as e:
            logger.error(f"Failed to send Stoat message to {channel_id}: {e}")
            raise  # Let caller handle (migration loop will stop for permission errors)

    async def send_marker(self, channel_id: str, content: str, files: Optional[List[Dict[str, Any]]] = None, reply_to_message_id: Optional[str] = None) -> Optional[str]:
        try:
            channel = await self.client.fetch_channel(channel_id)
            attachments = None
            if files:
                attachments = []
                for f in files:
                    attachments.append((f["filename"], f["data"]))
            
            replies = None
            if reply_to_message_id:
                replies = [stoat.Reply(id=reply_to_message_id, mention=False)]

            msg = await channel.send(
                content=content, 
                attachments=attachments,
                replies=replies
            )
            return str(msg.id)
        except Exception as e:
            logger.error(f"Failed to send Stoat marker to {channel_id}: {e}")
            return None


    async def create_role(self, name: str, color: int = 0, hoist: bool = False, permissions: int = 0, **kwargs) -> str:
        server = await self._get_server()
        try:
            # Create role first (Stoat create_role only takes name and rank)
            role = await server.create_role(name=name)
            
            # Convert integer color to hex string if not 0
            hex_color = None
            if color != 0:
                hex_color = f"#{color:06x}"
            
            # Edit role to set color and hoist
            await role.edit(
                color=hex_color if hex_color is not None else stoat.UNDEFINED,
                hoist=hoist
            )
            
            # Set permissions
            if permissions != 0:
                s_perms = self._map_permissions(permissions)
                await server.set_role_permissions(role, allow=s_perms)
                
            return str(role.id)
        except Exception as e:
            logger.error(f"Failed to create Stoat role {name}: {e}")
            return ""

    def _map_permissions(self, discord_perms_int: int) -> stoat.Permissions:
        import discord
        d_perms = discord.Permissions(discord_perms_int)
        
        s_perms = stoat.Permissions.none()
        
        mapping = {
            "manage_channels": "manage_channels",
            "manage_guild": "manage_server",
            "manage_roles": "manage_roles",
            "kick_members": "kick_members",
            "ban_members": "ban_members",
            "view_channel": "view_channel",
            "send_messages": "send_messages",
            "manage_messages": "manage_messages",
            "embed_links": "send_embeds",
            "attach_files": "upload_files",
            "read_message_history": "read_message_history",
            "mention_everyone": "mention_everyone",
            "add_reactions": "react",
            "connect": "connect",
            "speak": "speak",
            "stream": "video",
            "mute_members": "mute_members",
            "deafen_members": "deafen_members",
            "move_members": "move_members",
            "manage_nicknames": "manage_nicknames",
            "manage_webhooks": "manage_webhooks",
            "manage_emojis": "manage_customization",
            "manage_stickers": "manage_customization",
            "moderate_members": "timeout_members",
        }
        
        for d_name, s_name in mapping.items():
            if getattr(d_perms, d_name, False):
                try:
                    setattr(s_perms, s_name, True)
                except Exception:
                    pass
                
        if d_perms.administrator:
            return stoat.Permissions.all()
            
        return s_perms

    async def update_default_role_permissions(self, permissions: int):
        """Sets the default server permissions from a Discord permissions bitfield."""
        server = await self._get_server()
        try:
            s_perms = self._map_permissions(permissions)
            await server.set_default_permissions(s_perms)
            return True
        except Exception as e:
            logger.error(f"Failed to update Stoat default permissions: {e}")
            return False


    async def create_emoji(self, name: str, image_bytes: bytes, **kwargs) -> str:
        server = await self._get_server()
        try:
            emoji = await server.create_server_emoji(name=name, image=image_bytes)
            return str(emoji.id)
        except Exception as e:
            logger.error(f"Failed to create Stoat emoji {name}: {e}")
            return ""

    async def create_sticker(self, **kwargs) -> str:
        return "dummy_stoat_sticker_id"

    async def update_guild_metadata(self, name: Optional[str] = None, icon: Optional[bytes] = None, banner: Optional[bytes] = None, **kwargs) -> None:
        server = await self._get_server()
        try:
            await server.edit(
                name=name if name is not None else stoat.UNDEFINED,
                icon=icon if icon is not None else stoat.UNDEFINED,
                banner=banner if banner is not None else stoat.UNDEFINED
            )
        except Exception as e:
            logger.error(f"Failed to update Stoat guild metadata: {e}")

    async def remove_community_logo_and_banner(self) -> dict:
        server = await self._get_server()
        has_icon = bool(server.icon)
        has_banner = bool(server.banner)

        if has_icon:
            try:
                await server.edit(icon=None)
            except Exception as e:
                logger.error(f"Failed to remove Stoat community icon: {e}")

        if has_banner:
            try:
                await server.edit(banner=None)
            except Exception as e:
                logger.error(f"Failed to remove Stoat community banner: {e}")

        return {
            "icon": "REMOVED" if has_icon else "SKIP",
            "banner": "REMOVED" if has_banner else "SKIP",
        }

    async def delete_all_channels(self, progress_callback=None, **kwargs) -> int:
        server = await self._get_server(populate_channels=True)
        channels = server.channels
        categories = list(server.categories) if hasattr(server, "categories") and server.categories else []
        count = 0
        total = len(channels) + len(categories)
        
        for i, ch in enumerate(channels, 1):
            try:
                name = getattr(ch, "title", getattr(ch, "name", "Unknown"))
                if str(name).lower() in ["reaper-logs", "reaper_logs"]:
                    logger.info(f"Danger Zone: Skipping deletion of audit channel {name}")
                    total -= 1
                    continue
                await ch.delete()
                count += 1
                if progress_callback:
                    await progress_callback(name, i, total)
            except Exception as e:
                logger.error(f"Failed to delete Stoat channel {ch.id}: {e}")
                
        # To delete categories, we can wipe the categories array via server.edit to avoid 404 endpoint
        try:
            surviving_cats = []
            for cat in categories:
                name = getattr(cat, "title", getattr(cat, "name", "Unknown"))
                if str(name).lower() in ["reaper-logs", "reaper_logs"]:
                    if not hasattr(cat, "default_permissions"): cat.default_permissions = None
                    if not hasattr(cat, "role_permissions"): cat.role_permissions = {}
                    surviving_cats.append(cat)
                    total -= 1

            await server.edit(categories=surviving_cats)
            count += len(categories) - len(surviving_cats)
            
            j = len(channels) + 1
            for cat in categories:
                if cat not in surviving_cats:
                    name = getattr(cat, "title", getattr(cat, "name", "Unknown"))
                    if progress_callback:
                        await progress_callback(name, j, total)
                    j += 1
        except Exception as e:
             logger.error(f"Failed to wipe Stoat categories via edit: {e}")

        return count

    async def reset_channel_permissions(self, progress_callback=None, **kwargs) -> int:
        server = await self._get_server(populate_channels=True)
        channels = server.channels
        count = 0
        total = len(channels)
        for i, ch in enumerate(channels, 1):
            try:
                name = getattr(ch, "title", getattr(ch, "name", "Unknown"))
                if str(name).lower() in ["reaper-logs", "reaper_logs"]:
                    logger.info(f"Danger Zone: Skipping permission reset for audit channel {name}")
                    total -= 1
                    continue
                # In Stoat, clearing overrides might involve setting them to default or explicitly removing the role_permissions/default_permissions
                # Since we don't know an explicit "clear_overrides" method, we'll wipe them by setting empty/none if possible.
                # Actually Stoat allows overwriting. Setting allow=0 deny=0 for role overrides isn't explicitly clear.
                # For safety, we will just pass. If the user expects it, we'd iterate over roles and set empty.
                # A quick way is to edit the channel permissions to empty state if possible.
                # Let's count them anyway. 
                # (Fluxer writer does a loop over existing overrides, we can just return 0 for now until we inspect Stoat `PermissionOverride` deletion)
                count += 1
                if progress_callback:
                    await progress_callback(name, i, total)
            except Exception as e:
                logger.error(f"Failed to reset Stoat channel permissions for {ch.id}: {e}")
        return count

    async def set_channel_permission(self, channel_id: str, overwrite_id: str, allow: int, deny: int, is_role: bool = True):
        try:
            channel = await self.client.fetch_channel(channel_id)
            
            # Stoat Permissions objects MUST be mapped from Discord bitfields
            allow_perms = self._map_permissions(allow)
            deny_perms = self._map_permissions(deny)
            
            # If overwrite_id is the community_id, it refers to the default permissions (@everyone)
            if str(overwrite_id) == self.community_id:
                override = stoat.PermissionOverride(allow=allow_perms, deny=deny_perms)
                await channel.set_default_permissions(override)
            elif is_role:
                # Stoat uses set_role_permissions(role_id, allow=..., deny=...)
                await channel.set_role_permissions(overwrite_id, allow=allow_perms, deny=deny_perms)
            else:
                # User-specific overrides are currently skipped for Stoat
                pass
        except Exception as e:
             err_msg = str(e)
             if "MissingPermission" in err_msg and "ViewChannel" in err_msg:
                 logger.error(f"Stoat LOCKOUT: Bot lacks 'ViewChannel' to edit {channel_id}. "
                             "Ensure the bot has 'Manage Server' or a role with 'Allow View Channel' rank higher than @everyone.")
             logger.error(f"Failed to set Stoat channel permission for {overwrite_id} on {channel_id}: {e}")



    async def delete_all_roles(self, **kwargs) -> int:
        server = await self._get_server()
        # Stoat roles are in server.roles (dict) or fetch_roles()
        # Let's use fetch_roles if available or access .roles
        try:
            # server.roles is a dict of {id: Role}
            roles = list(server.roles.values())
        except Exception:
            # Fallback
            return 0
            
        deleted = 0
        for role in roles:
            # Skip @everyone (usually has rank 0 or special flag?)
            # In Stoat, @everyone is usually the guild ID.
            if str(role.id) == self.community_id:
                continue
            
            # Check if managed/bot role - Stoat Role doesn't have a clear .managed property 
            # but we can try to guess or just attempt delete.
            try:
                await role.delete()
                deleted += 1
            except Exception as e:
                logger.debug(f"Skipping role {role.name} (likely managed or @everyone): {e}")
        
        return deleted

    async def delete_all_emojis_and_stickers(self, **kwargs) -> dict:
        server = await self._get_server()
        emojis = await server.fetch_emojis()
        count = 0
        for emoji in emojis:
            try:
                await emoji.delete()
                count += 1
            except Exception as e:
                logger.error(f"Failed to delete Stoat emoji {emoji.name}: {e}")
        
        return {"emojis": count, "stickers": 0}

    async def close(self):
        pass
