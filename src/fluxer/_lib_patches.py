"""Runtime fixes for bugs in the fluxer.py library.

The library's guild *sticker* HTTP methods construct their routes with a bare
``Route(...)`` instead of ``self._route(...)``. ``Route`` defaults its
``base_url`` to the official host (``https://api.fluxer.app/v1``), so on a
self-hosted instance these requests are sent to the wrong server and rejected
with ``401 UNAUTHORIZED`` — even though the bot token is valid for the
self-hosted host. The emoji methods are unaffected because they correctly use
``self._route`` (which passes ``base_url=self.api_url``).

We only patch the two methods disco-reaper actually calls
(``get_guild_stickers`` and ``create_guild_sticker``); the other sticker
methods are buggy too but unused, and their REST paths can't be verified here.

This lives in disco-reaper rather than the vendored library so the fix survives
``pip install -r requirements.txt`` (which reinstalls fluxer.py from git).

Remove this module once https://github.com/akarealemil/fluxer.py ships the fix.
"""

import base64
import logging
from typing import Any

logger = logging.getLogger(__name__)

_applied = False


def apply_fluxer_patches() -> None:
    """Idempotently patch the broken sticker methods on fluxer's HTTPClient."""
    global _applied
    if _applied:
        return

    try:
        from fluxer.http import HTTPClient
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"Could not import fluxer.http to apply sticker patch: {e}")
        return

    async def get_guild_stickers(self, guild_id: int | str) -> list[dict[str, Any]]:
        # Use _route so the request targets the configured (self-hosted) api_url.
        return await self.request(
            self._route("GET", "/guilds/{guild_id}/stickers", guild_id=guild_id)
        )

    async def create_guild_sticker(
        self,
        guild_id: int | str,
        *,
        name: str,
        image: bytes,
        roles: "list[int | str] | None" = None,
        reason: "str | None" = None,
    ) -> dict[str, Any]:
        image_data = base64.b64encode(image).decode("ascii")
        if image.startswith(b"\x89PNG"):
            mime_type = "image/png"
        elif image.startswith(b"\xff\xd8\xff"):
            mime_type = "image/jpeg"
        elif image.startswith(b"GIF89a") or image.startswith(b"GIF87a"):
            mime_type = "image/gif"
        else:
            mime_type = "image/png"

        payload: dict[str, Any] = {
            "name": name,
            "image": f"data:{mime_type};base64,{image_data}",
        }
        if roles is not None:
            payload["roles"] = [str(role_id) for role_id in roles]

        # Use _route so the request targets the configured (self-hosted) api_url.
        return await self.request(
            self._route("POST", "/guilds/{guild_id}/stickers", guild_id=guild_id),
            json=payload,
            reason=reason,
        )

    HTTPClient.get_guild_stickers = get_guild_stickers
    HTTPClient.create_guild_sticker = create_guild_sticker

    _applied = True
    logger.debug("Applied fluxer.py sticker route patches (self-hosted base_url fix).")
