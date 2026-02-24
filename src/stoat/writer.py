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
        # We don't fetch the server here to avoid slowing down startup if not needed
        # but we might want a helper to get it
        self._server = None

    async def _get_server(self):
        if not self._server:
            self._server = await self.client.fetch_server(self.community_id)
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
        return []

    async def create_channel(self, name: str, **kwargs) -> str:
        return "dummy_stoat_channel_id"

    async def modify_channel(self, channel_id: str, **kwargs) -> bool:
        return True

    async def move_channel(self, channel_id: str, parent_id: Optional[str]) -> bool:
        return True

    async def send_message(self, **kwargs) -> Optional[str]:
        return "dummy_stoat_message_id"

    async def send_marker(self, **kwargs) -> Optional[str]:
        return "dummy_stoat_marker_id"

    async def create_role(self, **kwargs) -> str:
        return "dummy_stoat_role_id"

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
        return {"icon": "SKIP", "banner": "SKIP"}

    async def delete_all_channels(self, **kwargs) -> int:
        return 0

    async def reset_channel_permissions(self, **kwargs) -> int:
        return 0

    async def delete_all_roles(self, **kwargs) -> int:
        return 0

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
