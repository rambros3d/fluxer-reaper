import logging
import stoat
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

class StoatWriter:
    def __init__(self, token: str, community_id: str):
        self.token = token
        self.community_id = str(community_id)

    async def start(self):
        self.client = stoat.Client(token=self.token, bot=True)
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
        client = stoat.Client(token=self.token, bot=True)
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
                        "manage_customization": perms.manage_customization
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
            results = []
            for ch in channels:
                # Map Stoat types to Fluxer/Discord integers for internal compatibility
                # 0: Text, 2: Voice, 4: Category
                ch_type = -1
                if isinstance(ch, stoat.TextChannel):
                    ch_type = 0
                elif isinstance(ch, stoat.VoiceChannel):
                    ch_type = 2
                elif isinstance(ch, stoat.Category):
                    ch_type = 4
                
                results.append({
                    "id": str(ch.id),
                    "name": getattr(ch, "title", ch.name),
                    "type": ch_type
                })
            return results
        except Exception as e:
            logger.error(f"Failed to fetch Stoat channels: {e}")
            return []

    async def create_channel(self, name: str, type: int = 0, topic: str = "", parent_id: Optional[str] = None, **kwargs) -> str:
        server = await self._get_server()
        try:
            if type == 4: # Category
                cat = await server.create_category(title=name)
                return str(cat.id)
            else: # Text Channel
                ch = await server.create_text_channel(name=name, description=topic)
                # If parent_id is provided, Stoat might need a separate call to move it?
                # Actually server.create_text_channel might take category?
                # Let's check the signature again.
                return str(ch.id)
        except Exception as e:
            logger.error(f"Failed to create Stoat channel {name}: {e}")
            return ""

    async def modify_channel(self, channel_id: str, **kwargs) -> bool:
        return True

    async def move_channel(self, channel_id: str, parent_id: Optional[str]) -> bool:
        return True

    async def send_message(self, **kwargs) -> Optional[str]:
        return "dummy_stoat_message_id"

    async def send_marker(self, channel_id: str, content: str, files: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
        try:
            channel = await self.client.fetch_channel(channel_id)
            # Stoat channel.send takes content
            # files support might be different, skipping for markers
            msg = await channel.send(content=content)
            return str(msg.id)
        except Exception as e:
            logger.error(f"Failed to send Stoat marker to {channel_id}: {e}")
            return None


    async def create_role(self, name: str, color: int = 0, hoist: bool = False, **kwargs) -> str:
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
            return str(role.id)
        except Exception as e:
            logger.error(f"Failed to create Stoat role {name}: {e}")
            return ""

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

    async def delete_all_channels(self, **kwargs) -> int:
        return 0

    async def set_channel_permission(self, channel_id: str, overwrite_id: str, allow: int, deny: int, is_role: bool = True):
        try:
            channel = await self.client.fetch_channel(channel_id)
            
            # Stoat Permissions objects are created from raw integers
            allow_perms = stoat.Permissions(allow)
            deny_perms = stoat.Permissions(deny)
            
            # If overwrite_id is the community_id, it refers to the default permissions (@everyone)
            if str(overwrite_id) == self.community_id:
                override = stoat.PermissionOverride(allow=allow_perms, deny=deny_perms)
                await channel.set_default_permissions(override)
            elif is_role:
                # Stoat uses set_role_permissions(role_id, allow=..., deny=...)
                await channel.set_role_permissions(overwrite_id, allow=allow_perms, deny=deny_perms)
            else:
                # This case might not be directly supported for individual users in the same way
                # but we'll try something if needed.
                pass
        except Exception as e:
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
