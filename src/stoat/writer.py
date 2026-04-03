import asyncio
import logging
import stoat
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

async def _discover_stoat_config(api_url: str) -> Dict[str, Optional[str]]:
    """
    Fetches the Stoat/Revolt instance configuration to discover the WS and CDN URLs.
    Returns a dict with 'ws' and 'cdn' keys.
    """
    results = {"ws": None, "cdn": None}
    
    if not api_url or api_url == "default" or "stoat.chat" in api_url:
        return results

    import aiohttp
    try:
        # Standard Revolt discovery endpoint is the root API URL
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url.rstrip("/") + "/") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results["ws"] = data.get("ws")
                    # Features might be in 'features.autumn.url'
                    features = data.get("features", {})
                    autumn = features.get("autumn", {})
                    results["cdn"] = autumn.get("url")
                    
                    if results["ws"] or results["cdn"]:
                        logger.debug(f"Stoat Discovery: Found WS={results['ws']}, CDN={results['cdn']}")
                        return results
    except Exception as e:
        logger.debug(f"Stoat Discovery failed (fetching): {e}")

    # Fallback to inference if fetch failed
    from urllib.parse import urlparse
    try:
        parsed = urlparse(api_url)
        if parsed.netloc:
            # Traditional defaults for self-hosted
            results["ws"] = f"wss://{parsed.netloc}/ws"
            results["cdn"] = f"https://{parsed.netloc}/autumn"
    except Exception:
        pass
        
    return results

class StoatWriter:
    def __init__(self, token: str, community_id: str, api_url: str = "default", ws_url: str = None):
        self.token = token
        self.community_id = str(community_id)
        self.api_url = api_url
        self.ws_url = ws_url
        self.client: Optional[stoat.Client] = None
        self._server = None
        self._me = None
        self._validation_cache = None

    @staticmethod
    async def fetch_guilds(token: str, api_url: str = "default", ws_url: str = None, cdn_url: str = None) -> list[tuple[str, str]]:
        """Fetches the list of Stoat servers the bot is in. Returns list of (label, id)."""
        token = token.strip()
        api_url = (api_url or "default").strip()
        ws_url = (ws_url or "").strip()
        cdn_url = (cdn_url or "").strip()
        
        # Auto-discover URLs if not provided for custom domains
        if api_url != "default" and (not ws_url or not cdn_url):
            discovery = await _discover_stoat_config(api_url)
            if not ws_url: ws_url = discovery["ws"] or ""
            if not cdn_url: cdn_url = discovery["cdn"] or ""

        # Diagnostics to both stdout and logger
        log_msg = f"Stoat: Fetching guilds using API URL: {api_url}"
        if ws_url: log_msg += f" [WS: {ws_url}]"
        if cdn_url: log_msg += f" [CDN: {cdn_url}]"
        print(log_msg)
        logger.debug(log_msg)
        
        client_kwargs = {
            "token": token, 
            "bot": True,
            "http_base": api_url if api_url != "default" else None,
            "websocket_base": ws_url or None,
            "cdn_base": cdn_url or None
        }
        client = stoat.Client(**client_kwargs)
        logger.debug(f"Stoat: Initialized client with native http_base={client_kwargs['http_base']} and websocket_base={client_kwargs['websocket_base']}")
        
        ready_event = asyncio.Event()
        servers_list = []

        @client.on(stoat.ReadyEvent)
        async def on_ready(event: stoat.ReadyEvent):
            nonlocal servers_list
            servers_list = event.servers
            ready_event.set()

        client_task = asyncio.create_task(client.start())
        guilds_list = []
        try:
            # Wait for bot to be ready OR for the task to fail
            done, pending = await asyncio.wait(
                [asyncio.create_task(ready_event.wait()), client_task],
                return_when=asyncio.FIRST_COMPLETED,
                timeout=10.0
            )

            if ready_event.is_set():
                for s in servers_list:
                    label = f"{s.id}-{s.name}"
                    guilds_list.append((label, str(s.id)))
            else:
                # If we got here, either it timed out or client_task finished early
                if client_task in done:
                    # Check for exception in the client task
                    exc = client_task.exception()
                    if exc:
                        raise exc
                    else:
                        raise Exception("Client task finished early without ready event")
                else:
                    raise asyncio.TimeoutError("Timed out waiting for Stoat to be ready")
        except Exception as e:
            print(f"Failed to fetch Stoat servers: {e}")
            logger.error(f"Failed to fetch Stoat servers: {e}")
            raise
        finally:
            # Shutdown the specific client instance used for fetching
            try:
                await client.close()
            except Exception:
                pass
                
            client_task.cancel()
            try:
                # Wait for the task to actually finish terminating
                await asyncio.wait_for(client_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            
        return guilds_list

    async def start(self):
        if self.client:
            # Check if client is actually usable (not half-closed)
            try:
                if self.client and not self.client.is_closed:
                    return
            except Exception:
                pass
            self.client = None

        api_url = (self.api_url or "default").strip()
        ws_url = (self.ws_url or "").strip()
        cdn_url = getattr(self, "cdn_url", "").strip()
        
        # Auto-discover if not provided for custom domains
        if api_url != "default" and (not ws_url or not cdn_url):
            discovery = await _discover_stoat_config(api_url)
            if not ws_url: ws_url = discovery["ws"] or ""
            if not cdn_url: cdn_url = discovery["cdn"] or ""

        token = self.token.strip()
        
        # Diagnostics to both stdout and logger
        log_msg = f"Stoat: Starting client using API URL: {api_url}"
        if ws_url: log_msg += f" [WS: {ws_url}]"
        if cdn_url: log_msg += f" [CDN: {cdn_url}]"
        print(log_msg)
        logger.debug(log_msg)

        client_kwargs = {
            "token": token, 
            "bot": True,
            "http_base": api_url if api_url != "default" else None,
            "websocket_base": ws_url or None,
            "cdn_base": cdn_url or None
        }
        self.client = stoat.Client(**client_kwargs)
        
        # Keep track of the start task so we can clean it up later
        self._start_task = asyncio.create_task(self.client.start())
        
        try:
            self._me = await self.client.fetch_user("@me")
        except Exception as e:
            print(f"Failed to fetch bot user in StoatWriter: {e}")
            logger.error(f"Failed to fetch bot user in StoatWriter: {e}")
            # Ensure we clean up if start fails
            await self.close()
            self.client = None

    @property
    def my_id(self):
        return str(self._me.id) if self._me else None

    async def _get_server(self, populate_channels=False, force=False):
        # Always refetch if channels are requested AND we don't already have them
        # OR if force=True is passed.
        if force:
            self._server = await self.client.fetch_server(self.community_id, populate_channels=populate_channels)
        elif populate_channels:
            if not self._server or not hasattr(self._server, "channels") or not self._server.channels:
                self._server = await self.client.fetch_server(self.community_id, populate_channels=True)
        elif not self._server:
            self._server = await self.client.fetch_server(self.community_id, populate_channels=False)
        return self._server

    async def validate(self) -> dict:
        results = {
            "token": False,
            "community": False,
            "bot_name": "N/A",
            "community_name": "N/A",
            "error_reason": None,
            "permissions": {
                "manage_channels": False,
                "manage_server": False,
                "manage_permissions": False,
                "manage_roles": False,
                "manage_customization": False,
                "manage_messages": False,
                "manage_webhooks": False,
                "view_channel": False,
                "send_messages": False,
                "send_embeds": False,
                "upload_files": False,
                "masquerade": False
            }
        }
        
        # Ensure client is started
        if not self.client:
            await self.start()
        
        client = self.client
        assert client is not None

        try:
            # Validate token by fetching current user
            try:
                # Reuse self._me if already fetched during start()
                if not self._me:
                    self._me = await client.fetch_user("@me")
                
                current_user = self._me
                results["token"] = True
                results["bot_name"] = current_user.display_name or current_user.name
            except stoat.Unauthorized:
                results["error_reason"] = "Invalid Stoat Token"
                return results
            except Exception as e:
                results["error_reason"] = f"Token Error: {str(e)}"
                return results
            
            # Validate server access
            try:
                server = await client.fetch_server(self.community_id)
                results["community"] = True
                results["community_name"] = server.name
                
                # Check permissions using effective server permissions for the bot
                try:
                    me = await server.fetch_member(current_user.id)
                    perms = server.permissions_for(me, safe=False)
                    
                    results["permissions"] = {
                        "manage_channels": getattr(perms, "manage_channels", False),
                        "manage_server": getattr(perms, "manage_server", False),
                        "manage_permissions": getattr(perms, "manage_permissions", False),
                        "manage_roles": getattr(perms, "manage_roles", False),
                        "manage_customization": getattr(perms, "manage_customization", False),
                        "manage_messages": getattr(perms, "manage_messages", False),
                        "manage_webhooks": getattr(perms, "manage_webhooks", False),
                        "view_channel": getattr(perms, "view_channel", False),
                        "send_messages": getattr(perms, "send_messages", False),
                        "send_embeds": getattr(perms, "send_embeds", False),
                        "upload_files": getattr(perms, "upload_files", False),
                        "masquerade": getattr(perms, "use_masquerade", False)
                    }
                except stoat.NotFound:
                    results["error_reason"] = "Bot not member of server"
                except stoat.Forbidden:
                    results["error_reason"] = "Missing member data access"
                except Exception as e:
                    results["error_reason"] = f"Permission error: {str(e)}"
            except stoat.NotFound:
                results["error_reason"] = "Server not found"
            except stoat.Forbidden:
                results["error_reason"] = "Missing access to server"
            except Exception as e:
                results["error_reason"] = f"Server error: {str(e)}"
                
        except Exception as e:
            results["error_reason"] = f"Stoat validation failed: {str(e)}"
            
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
            print(f"Failed to fetch Stoat channels: {e}")
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
                self._server = None # Clear cache after structural change
                return new_id
            elif type == 2: # Voice Channel
                ch = await server.create_voice_channel(name=name)
                self._server = None # Clear cache
                return str(ch.id)
            else: # Text Channel
                ch = await server.create_text_channel(name=name, description=topic)
                self._server = None # Clear cache
                return str(ch.id)
        except Exception as e:
            print(f"Failed to create Stoat channel {name}: {e}")
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
                self._server = None # Clear cache

            # clone_server.py now handles all parenting bulk logic
            return True
        except Exception as e:
            print(f"Failed to modify Stoat channel {channel_id}: {e}")
            logger.error(f"Failed to modify Stoat channel {channel_id}: {e}")
            return False

    async def move_channel(self, channel_id: str, parent_id: Optional[str]) -> bool:
        return True

    async def send_message(self, channel_id: str, author_name: str, content: str, timestamp: int, 
                           author_avatar_url: Optional[str] = None, files: Optional[List[Dict[str, Any]]] = None, 
                           reply_to_message_id: Optional[str] = None, is_forwarded: bool = False,
                           embeds: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
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
            prefix = f"###### <t:{timestamp}:D>\n"
            if is_forwarded:
                prefix += "##### ⮫*forwarded*\n"
            
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
                    if f.get("content_type"):
                        attachments.append((f["filename"], f["data"], f["content_type"]))
                    else:
                        attachments.append((f["filename"], f["data"]))
            
            try:
                # Stoat requires SendableEmbed objects, not raw dicts
                stoat_embeds = []
                if embeds:
                    for raw_embed in embeds:
                        # Normalize embed to dict (handles both live discord.Embed and backup dicts)
                        if hasattr(raw_embed, "to_dict"):
                            e = raw_embed.to_dict()
                        else:
                            e = raw_embed

                        # Convert integer color to hex string if present
                        color = None
                        if e.get("color"):
                            color = f"#{e['color']:06x}"
                        
                        # Map Discord fields to Stoat SendableEmbed fields
                        # icon_url: author or footer icon
                        icon_url = None
                        if e.get("author"):
                            icon_url = e["author"].get("icon_url")
                        if not icon_url and e.get("footer"):
                            icon_url = e["footer"].get("icon_url")
                            
                        # media: image or thumbnail. 
                        # Stoat's SendableEmbed.media expects a file ID or ResolvableResource (Upload).
                        # It does NOT properly handle external URLs in the 'media' field (causes 404).
                        media = None
                        image_url = e.get("image", {}).get("url")
                        thumbnail_url = e.get("thumbnail", {}).get("url")

                        # If we have an image/thumbnail URL and no icon_url, use it as icon_url
                        # (Stoat icons can be URLs and show up as small images)
                        if not icon_url:
                            icon_url = thumbnail_url or image_url

                        # Only use media if it's NOT a URL (likely a file ID from a previous Stoat message)
                        if image_url and not image_url.startswith("http"):
                            media = image_url
                        elif thumbnail_url and not thumbnail_url.startswith("http"):
                            media = thumbnail_url
                            
                        stoat_embeds.append(stoat.SendableEmbed(
                            title=e.get("title"),
                            description=e.get("description"),
                            icon_url=icon_url,
                            url=e.get("url"),
                            media=media,
                            color=color
                        ))

                # Retry logic for 'NotFound' (common race condition on self-hosted instances)
                max_tries = 2
                for attempt in range(max_tries):
                    try:
                        msg = await channel.send(
                            content=final_content,
                            masquerade=masquerade,
                            replies=replies,
                            attachments=attachments,
                            embeds=stoat_embeds
                        )
                        return str(msg.id) if msg else None
                    except Exception as send_err:
                        # 'NotFound' often means the attachment is still being processed by the database
                        if "NotFound" in str(send_err) and attempt < max_tries - 1:
                            logger.warning(f"Stoat: Received NotFound during send (likely race condition). Retrying in 1.5s... (Attempt {attempt+1}/{max_tries})")
                            await asyncio.sleep(1.5)
                            continue
                        raise send_err

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
            print(f"Failed to send Stoat message to {channel_id}: {e}")
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
            print(f"Failed to send Stoat marker to {channel_id}: {e}")
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
                requested_perms = self._map_permissions(permissions)
                
                # Fetch bot's own permissions to mask the requested set.
                # Stoat/Revolt prevents granting permissions that the bot itself lacks.
                try:
                    me = await server.fetch_member(self.my_id)
                    bot_perms = server.permissions_for(me)
                    
                    # Manual masking of boolean attributes
                    final_perms = stoat.Permissions.none()
                    for attr in dir(requested_perms):
                        if not attr.startswith("_") and isinstance(getattr(requested_perms, attr), bool):
                            if getattr(requested_perms, attr) and getattr(bot_perms, attr, False):
                                try:
                                    setattr(final_perms, attr, True)
                                except Exception:
                                    pass
                    
                    await server.set_role_permissions(role, allow=final_perms)
                except Exception as perm_err:
                    logger.warning(f"Stoat: Could not mask/verify role permissions for {name} (falling back to minimal): {perm_err}")
                    # Attempt to set at least one basic permission if possible, or just skip
                    try:
                        minimal = stoat.Permissions.none()
                        minimal.view_channel = True
                        minimal.send_messages = True
                        await server.set_role_permissions(role, allow=minimal)
                    except Exception:
                        pass
                
            return str(role.id)
        except Exception as e:
            print(f"Failed to create Stoat role {name}: {e}")
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
            print(f"Failed to update Stoat default permissions: {e}")
            logger.error(f"Failed to update Stoat default permissions: {e}")
            return False


    async def create_emoji(self, name: str, image_bytes: bytes, **kwargs) -> str:
        server = await self._get_server()
        try:
            emoji = await server.create_server_emoji(name=name, image=image_bytes)
            return str(emoji.id)
        except Exception as e:
            print(f"Failed to create Stoat emoji {name}: {e}")
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
            print(f"Failed to update Stoat guild metadata: {e}")
            logger.error(f"Failed to update Stoat guild metadata: {e}")

    async def remove_community_logo_and_banner(self) -> dict:
        server = await self._get_server()
        has_icon = bool(server.icon)
        has_banner = bool(server.banner)

        if has_icon:
            try:
                await server.edit(icon=None)
            except Exception as e:
                print(f"Failed to remove Stoat community icon: {e}")
                logger.error(f"Failed to remove Stoat community icon: {e}")

        if has_banner:
            try:
                await server.edit(banner=None)
            except Exception as e:
                print(f"Failed to remove Stoat community banner: {e}")
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
                print(f"Failed to delete Stoat channel {ch.id}: {e}")
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
             print(f"Failed to wipe Stoat categories via edit: {e}")
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
                
                # Fetch fresh channel to get current role_permissions
                fresh_ch = await self.client.fetch_channel(ch.id)
                # Clear default permissions
                if hasattr(fresh_ch, "default_permissions") and fresh_ch.default_permissions is not None:
                    await fresh_ch.set_default_permissions(None)
                
                # Clear all role overrides
                if hasattr(fresh_ch, "role_permissions"):
                    for role_id in list(fresh_ch.role_permissions.keys()):
                        await fresh_ch.set_role_permissions(str(role_id), allow=stoat.Permissions.none(), deny=stoat.Permissions.none())

                count += 1
                if progress_callback:
                    await progress_callback(name, i, total)
            except Exception as e:
                print(f"Failed to reset Stoat channel permissions for {ch.id}: {e}")
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
             print(f"Failed to set Stoat channel permission for {overwrite_id} on {channel_id}: {e}")
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
                print(f"Failed to delete Stoat emoji {emoji.name}: {e}")
                logger.error(f"Failed to delete Stoat emoji {emoji.name}: {e}")
        
        return {"emojis": count, "stickers": 0}

    async def close(self):
        client = self.client
        task = getattr(self, "_start_task", None)
        
        self.client = None # Atomic clear to prevent new usage
        self._start_task = None
        self._me = None
        self._server = None
        
        if client:
            try:
                await client.close()
            except Exception as e:
                logger.debug(f"Error closing Stoat client session: {e}")
                
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
                
        self._validation_cache = None
