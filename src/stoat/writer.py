import logging

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
            "permissions": {}
        }
    
    async def close(self):
        pass
