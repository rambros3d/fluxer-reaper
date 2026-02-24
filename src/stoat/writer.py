import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

class StoatWriter:
    def __init__(self, token: str, community_id: str):
        self.token = token
        self.community_id = str(community_id)

    async def start(self):
        logger.info("StoatWriter start (Not implemented)")

    async def validate(self) -> dict:
        return {
            "token": True,
            "community": True,
            "bot_name": "Stoat Dummy",
            "community_name": "Stoat Community Dummy",
            "permissions": {
                "manage_channels": True,
                "manage_messages": True,
                "manage_roles": True,
                "manage_emojis_stickers": True,
                "manage_webhooks": True
            }
        }
    
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

    async def create_emoji(self, **kwargs) -> str:
        return "dummy_stoat_emoji_id"

    async def create_sticker(self, **kwargs) -> str:
        return "dummy_stoat_sticker_id"

    async def update_guild_metadata(self, **kwargs) -> None:
        pass

    async def remove_community_logo_and_banner(self) -> dict:
        return {"icon": "SKIP", "banner": "SKIP"}

    async def delete_all_channels(self, **kwargs) -> int:
        return 0

    async def reset_channel_permissions(self, **kwargs) -> int:
        return 0

    async def delete_all_roles(self, **kwargs) -> int:
        return 0

    async def delete_all_emojis_and_stickers(self, **kwargs) -> dict:
        return {"emojis": 0, "stickers": 0}

    async def close(self):
        pass
