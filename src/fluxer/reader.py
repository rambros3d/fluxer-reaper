# src/fluxer/reader.py
"""
Fluxer source reader – reads guild metadata, roles, channels, emojis,
stickers, and permission overwrites from a Fluxer community via its HTTP API.

Currently supports:
  • Server name / icon / banner ("Sync Server Settings")
  • Roles & role permissions
  • Server structure (channels & categories)
  • Channel & category permission overwrites
  • Custom emoji & sticker fetching and download

Not yet implemented here:
  • Message history migration
"""

import asyncio
import logging
import re
from typing import Dict, Any, Optional, List, Set

from fluxer import HTTPClient, Guild, Channel, Role

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CDN / asset URL helpers
# ---------------------------------------------------------------------------

_DEFAULT_FLUXER_CDN = "https://cdn.fluxer.app"


def _cdn_base_from_api_url(api_url: Optional[str]) -> str:
    """Derive the CDN host from a custom API URL.

    * Official instance: ``https://api.fluxer.app/v1`` → ``https://cdn.fluxer.app``
    * Self-hosted: ``https://fluxgard.omster.dev/api/v1`` → ``https://fluxgard.omster.dev``
    * Falls back to the official CDN if nothing can be determined.
    """
    if not api_url or api_url == "default":
        return _DEFAULT_FLUXER_CDN

    url = api_url.rstrip("/")

    # Official Fluxer: swap "api" for "cdn" in the hostname
    if "api.fluxer" in url:
        return url.replace("api.fluxer", "cdn.fluxer")

    # Self-hosted: keep the scheme + host, drop any /api/… path segments
    # so assets are fetched from the server root (typical setup).
    m = re.match(r"(https?://[^/]+)", url)
    if m:
        return m.group(1)

    # Last resort
    return url


# ---------------------------------------------------------------------------
# Lightweight wrappers for API response objects
# ---------------------------------------------------------------------------

class EmojiWrapper:
    """Minimal emoji object matching the OpenAPI ``GuildEmojiResponse`` schema.

    Provides attribute access (``.id``, ``.name``, ``.animated``, ``.url``)
    so that downstream code written for Discord objects works unchanged.
    """

    __slots__ = ("id", "name", "animated", "nsfw", "user", "_cdn_base")

    def __init__(self, data: Dict[str, Any], cdn_base: str = _DEFAULT_FLUXER_CDN):
        self.id = data.get("id")
        self.name = data.get("name")
        self.animated = data.get("animated", False)
        self.nsfw = data.get("nsfw", False)
        self.user = data.get("user")          # raw UserPartialResponse dict, or None
        self._cdn_base = cdn_base

    @property
    def url(self) -> Optional[str]:
        """Reconstructed CDN URL for the emoji image."""
        if not self.id:
            return None
        ext = "gif" if self.animated else "png"
        return f"{self._cdn_base}/emojis/{self.id}.{ext}"

    def __repr__(self) -> str:
        return f"<Emoji id={self.id} name={self.name!r} animated={self.animated}>"


class StickerWrapper:
    """Minimal sticker object matching the OpenAPI ``GuildStickerResponse`` schema.

    Provides attribute access (``.id``, ``.name``, ``.animated``, ``.format``,
    ``.url``, ``.tags``) so downstream code works unchanged.
    """

    __slots__ = ("id", "name", "description", "tags", "animated", "nsfw",
                 "user", "_cdn_base")

    def __init__(self, data: Dict[str, Any], cdn_base: str = _DEFAULT_FLUXER_CDN):
        self.id = data.get("id")
        self.name = data.get("name")
        self.description = data.get("description", "")
        self.tags = data.get("tags", [])
        self.animated = data.get("animated", False)
        self.nsfw = data.get("nsfw", False)
        self.user = data.get("user")
        self._cdn_base = cdn_base

    @property
    def url(self) -> Optional[str]:
        """Reconstructed CDN URL for the sticker image."""
        if not self.id:
            return None
        ext = "gif" if self.animated else "png"
        return f"{self._cdn_base}/stickers/{self.id}.{ext}"

    @property
    def format(self) -> str:
        """File extension – ``"gif"`` for animated, ``"png"`` otherwise."""
        return "gif" if self.animated else "png"

    def __repr__(self) -> str:
        return f"<Sticker id={self.id} name={self.name!r} animated={self.animated}>"


# ---------------------------------------------------------------------------
# FluxerChannelWrapper
# ---------------------------------------------------------------------------


class FluxerChannelWrapper:
    """Wraps a ``fluxer.Channel`` to expose a Discord‑compatible ``category_id``.

    The Fluxer ``ChannelResponse`` schema uses ``parent_id`` for the category
    ID, while the rest of the codebase expects ``category_id``.  This wrapper
    bridges that gap.
    """

    def __init__(self, channel):
        self._channel = channel

    def __getattr__(self, name):
        return getattr(self._channel, name)

    @property
    def category_id(self):
        return self._channel.parent_id


# ---------------------------------------------------------------------------
# FluxerReader
# ---------------------------------------------------------------------------


class FluxerReader:
    """Fluxer source reader – mimics the :class:`DiscordReader` interface for
    the subset of operations needed by the *Clone Server Template* workflow.

    Uses the Fluxer HTTP API (no WebSocket / Gateway).  All metadata-fetching
    methods are aligned with the official `Fluxer OpenAPI specification
    <https://api.fluxer.app/openapi.json>`_.

    Parameters
    ----------
    token : str
        Bot / user token for authentication.
    server_id : str
        The Fluxer community (guild) ID to operate on.  (The parameter is
        named ``server_id`` for call-site compatibility with
        ``DiscordReader`` – internally we refer to it as ``community_id``.)
    api_url : str
        Override for the API base URL.  Use ``"default"`` (or omit) for the
        official ``https://api.fluxer.app`` instance.
    """

    # -- Channel type constants (mirror Discord's for compatibility) ---------
    CHANNEL_TYPE_TEXT     = 0
    CHANNEL_TYPE_VOICE    = 2
    CHANNEL_TYPE_CATEGORY = 4
    CHANNEL_TYPE_NEWS     = 5
    CHANNEL_TYPE_FORUM    = 15

    def __init__(self, token: str, server_id: str, api_url: str = "default"):
        self.token = token
        # Fluxer terminology is "community", but the constructor parameter is
        # named "server_id" for call-site compatibility with DiscordReader.
        self.community_id = str(server_id)
        self._raw_api_url = api_url

        # Resolve the effective API base (no trailing slash)
        if api_url and api_url != "default":
            self._api_base = api_url.rstrip("/")
        else:
            self._api_base = "https://api.fluxer.app"

        # CDN base is resolved lazily in _ensure_http() once we know the
        # actual API host (important for self-hosted instances).
        self._cdn_base: str = _DEFAULT_FLUXER_CDN

        self._http: Optional[HTTPClient] = None
        self.guild_data: Optional[Dict[str, Any]] = None
        self.guild: Optional[Guild] = None

    # -- Properties ----------------------------------------------------------

    @property
    def server_id(self) -> str:
        """Backward-compatible alias for ``community_id``."""
        return self.community_id

    @property
    def api_base_url(self) -> str:
        """Base URL for API requests."""
        return self._api_base

    @property
    def asset_base_url(self) -> str:
        """Base URL for constructing asset (CDN) URLs (icons, emojis, etc.)."""
        return self._cdn_base

    # -- Lifecycle -----------------------------------------------------------

    async def _ensure_http(self) -> None:
        """Lazily initialise the HTTP client and fetch guild metadata."""
        if self._http is None:
            kwargs: Dict[str, Any] = {}
            if self._raw_api_url and self._raw_api_url != "default":
                kwargs["api_url"] = self._raw_api_url
            self._http = HTTPClient(self.token, **kwargs)

            # Derive the CDN base from the *actual* API URL the client is
            # using.  This is essential for self-hosted instances where the
            # API host is not ``api.fluxer.app``.
            actual_api = getattr(self._http, "api_url", "")
            if actual_api:
                self._cdn_base = _cdn_base_from_api_url(actual_api)
                # Keep _api_base in sync as well
                self._api_base = actual_api.rstrip("/")

            try:
                self.guild_data = await self._http.get_guild(self.community_id)
                self.guild = Guild.from_data(self.guild_data)
                logger.info(
                    "FluxerReader: connected to community %s (%s)",
                    self.guild.name, self.guild.id,
                )
            except Exception as exc:
                await self.close()
                raise RuntimeError(
                    f"Failed to fetch Fluxer community {self.community_id}: {exc}"
                ) from exc

    async def start(self) -> None:
        """Pre-load the HTTP client and guild data."""
        await self._ensure_http()

    async def close(self) -> None:
        """Tear down the HTTP client and clear cached data."""
        if self._http is not None:
            await self._http.close()
            self._http = None
        self.guild = None
        self.guild_data = None

    # -- Validation ----------------------------------------------------------

    async def validate(self) -> Dict[str, Any]:
        """Validate token, community and permissions using only HTTP.

        Returns a dict compatible with :meth:`DiscordReader.validate`.

        Notes
        -----
        The Fluxer API does not expose intents the way Discord does, so
        ``message_content`` and ``members`` are always reported as ``True``.
        """
        result: Dict[str, Any] = {
            "token": False,
            "server": False,
            "bot_name": None,
            "server_name": None,
            "error_reason": None,
            "intents": {"message_content": True, "members": True},
            "permissions": {
                "view_channel": True,
                "read_messages": True,
                "read_message_history": True,
            },
        }
        TIMEOUT = 15  # seconds

        try:
            http_kwargs: Dict[str, Any] = {}
            if self._raw_api_url and self._raw_api_url != "default":
                http_kwargs["api_url"] = self._raw_api_url

            http = HTTPClient(self.token, **http_kwargs)
            try:
                # 1. Validate token via ``GET /users/@me``
                me = await asyncio.wait_for(
                    http.get_current_user(), timeout=TIMEOUT
                )
                result["token"] = True
                result["bot_name"] = me.get("username")
                me_id = int(me["id"])

                # 2. Fetch guild and roles concurrently
                guild_data, roles_data = await asyncio.gather(
                    asyncio.wait_for(
                        http.get_guild(self.community_id), timeout=TIMEOUT
                    ),
                    asyncio.wait_for(
                        http.get_guild_roles(self.community_id), timeout=TIMEOUT
                    ),
                )
                if guild_data:
                    result["server"] = True
                    result["server_name"] = guild_data.get("name")
                    owner_id = int(guild_data.get("owner_id", 0))

                    # 3. Resolve effective permissions (owner or Administrator bit)
                    try:
                        member_data = await asyncio.wait_for(
                            http.get_guild_member(self.community_id, me_id),
                            timeout=TIMEOUT,
                        )
                        member_role_ids = {
                            int(r) for r in member_data.get("roles", [])
                        }
                        computed_perms = 0
                        guild_id_int = int(self.community_id)
                        for r_data in roles_data:
                            r_id = int(r_data["id"])
                            if r_id == guild_id_int or r_id in member_role_ids:
                                # ``permissions`` is a string bitfield per the
                                # OpenAPI ``GuildRoleResponse`` schema
                                computed_perms |= int(
                                    r_data.get("permissions", 0)
                                )
                        is_admin = (me_id == owner_id) or bool(
                            computed_perms & (1 << 3)
                        )
                        result["permissions"]["administrator"] = is_admin
                    except Exception:
                        result["permissions"]["administrator"] = (
                            me_id == owner_id
                        )
                else:
                    result["error_reason"] = "Community not found"

            except asyncio.TimeoutError:
                result["error_reason"] = (
                    "Validation timed out (API unreachable or slow)"
                )
            except Exception as exc:
                result["error_reason"] = f"Validation error: {exc}"
            finally:
                await http.close()

        except Exception as exc:
            result["error_reason"] = str(exc)

        return result

    # ========================================================================
    #  Server / guild metadata
    # ========================================================================

    async def get_server_metadata(self) -> Dict[str, Any]:
        """Return guild metadata for the *Sync Server Settings* workflow.

        Returns a dict with keys matching what :meth:`DiscordReader.get_server_metadata`
        returns: ``name``, ``id``, ``icon_url``, ``banner_url``.

        Asset hashes come from the OpenAPI ``GuildResponse`` schema
        (``icon`` / ``banner`` fields).  Full CDN URLs are composed here.
        """
        await self._ensure_http()
        data = self.guild_data or {}
        g = self.guild

        def _url(kind: str, hash_val: Optional[str]) -> Optional[str]:
            if not hash_val:
                return None
            return f"{self._cdn_base}/{kind}/{self.community_id}/{hash_val}.png"

        return {
            "name": g.name if g else "Unknown",
            "id": str(g.id) if g else self.community_id,
            "icon_url": _url("icons", data.get("icon")),
            "banner_url": _url("banners", data.get("banner")),
        }

    # ========================================================================
    #  Asset download helpers
    # ========================================================================

    async def download_asset(self, asset_url: str) -> bytes:
        """Download an arbitrary asset (icon, banner, etc.) from a URL.

        Returns empty ``bytes`` on any failure.
        """
        if not asset_url or not self._http:
            return b""
        try:
            async with self._http._session.get(asset_url) as resp:
                if resp.status == 200:
                    return await resp.read()
        except Exception:
            logger.debug(
                "Failed to download asset: %s", asset_url, exc_info=True
            )
        return b""

    async def download_emoji(self, emoji) -> bytes:
        """Download an emoji image.

        Accepts an :class:`EmojiWrapper`, a ``fluxer.Emoji``, or any object
        with ``id`` / ``url`` / ``animated`` attributes.  Returns raw bytes.
        """
        await self._ensure_http()

        url = getattr(emoji, "url", None)
        if not url:
            ext = "gif" if getattr(emoji, "animated", False) else "png"
            eid = getattr(emoji, "id", None)
            if eid:
                url = f"{self._cdn_base}/emojis/{eid}.{ext}"
        return await self.download_asset(url or "")

    async def download_sticker(self, sticker) -> bytes:
        """Download a sticker image.

        Accepts a :class:`StickerWrapper` or any object with ``id`` / ``url``
        / ``animated`` / ``format`` attributes.  Returns raw bytes.
        """
        await self._ensure_http()

        url = getattr(sticker, "url", None)
        if not url:
            sid = getattr(sticker, "id", None)
            if sid:
                fmt = getattr(sticker, "format", "png")
                if hasattr(fmt, "name"):          # enum → string
                    fmt = fmt.name.lower()
                elif fmt in ("apng",):
                    fmt = "png"
                url = f"{self._cdn_base}/stickers/{sid}.{fmt}"
        return await self.download_asset(url or "")

    # ========================================================================
    #  Roles
    # ========================================================================

    async def get_roles(self) -> List[Role]:
        """Return guild roles (excluding the ``@everyone`` role).

        Aligned with the OpenAPI ``GuildRoleResponse`` schema:

        * ``id``, ``name``, ``color``, ``position`` – as expected.
        * ``permissions`` is a **string** bitfield (handled transparently by
          ``Role.from_data()``).
        * ``hoist``, ``mentionable``, ``unicode_emoji`` – available on the
          returned object.
        """
        await self._ensure_http()
        roles_data = await self._http.get_guild_roles(self.community_id)
        roles: List[Role] = []
        for r in roles_data:
            role = Role.from_data(r)
            if not role.is_default:
                roles.append(role)
        return roles

    # ========================================================================
    #  Channels & categories
    # ========================================================================

    async def get_categories(self) -> List[FluxerChannelWrapper]:
        """Return category channels (``type == 4``)."""
        await self._ensure_http()
        channels = await self._http.get_guild_channels(self.community_id)
        return [
            FluxerChannelWrapper(Channel.from_data(ch))
            for ch in channels
            if ch.get("type") == 4
        ]

    async def get_channels(
        self, category_id: Optional[str] = None
    ) -> List[FluxerChannelWrapper]:
        """Return non-category channels, optionally filtered by *category_id*."""
        await self._ensure_http()
        channels_data = await self._http.get_guild_channels(self.community_id)
        result: List[FluxerChannelWrapper] = []
        for ch_data in channels_data:
            if ch_data.get("type") == 4:
                continue
            wrapped = FluxerChannelWrapper(Channel.from_data(ch_data))
            if category_id is not None and wrapped.category_id != int(category_id):
                continue
            result.append(wrapped)
        return result

    async def fetch_channels(self) -> List[FluxerChannelWrapper]:
        """Return **all** channels (including categories), unsorted."""
        await self._ensure_http()
        channels_data = await self._http.get_guild_channels(self.community_id)
        return [
            FluxerChannelWrapper(Channel.from_data(ch))
            for ch in channels_data
        ]

    async def get_channel(self, channel_id: str) -> FluxerChannelWrapper:
        """Fetch a single channel by ID."""
        await self._ensure_http()
        ch_data = await self._http.get_channel(channel_id)
        return FluxerChannelWrapper(Channel.from_data(ch_data))

    async def get_active_threads(self) -> List:
        """Active threads are not currently exposed by the Fluxer REST API."""
        return []

    # ========================================================================
    #  Emojis
    # ========================================================================

    async def get_emojis(self) -> List[EmojiWrapper]:
        """Return custom guild emojis.

        Each emoji wraps the ``GuildEmojiResponse`` / ``GuildEmojiWithUserResponse``
        schema (``id``, ``name``, ``animated``, ``nsfw``, optional ``user``).

        Uses ``GET /guilds/{guild_id}/emojis``.
        """
        await self._ensure_http()
        emojis_data = await self._http.get_guild_emojis(self.community_id)
        # The library may return a list or a dict with an ``items`` key
        if isinstance(emojis_data, dict):
            items = emojis_data.get("items", [])
        elif isinstance(emojis_data, list):
            items = emojis_data
        else:
            items = []
        return [EmojiWrapper(e, cdn_base=self._cdn_base) for e in items]

    # ========================================================================
    #  Stickers
    # ========================================================================

    async def get_stickers(self) -> List[StickerWrapper]:
        """Return custom guild stickers.

        Each sticker wraps the ``GuildStickerResponse`` / ``GuildStickerWithUserResponse``
        schema (``id``, ``name``, ``description``, ``tags``, ``animated``,
        ``nsfw``, optional ``user``).

        Uses ``GET /guilds/{guild_id}/stickers``.
        """
        await self._ensure_http()
        try:
            stickers_data = await self._http.get_guild_stickers(self.community_id)
        except Exception as exc:
            logger.warning(
                "Could not fetch stickers from Fluxer: %s. Returning empty list.", exc
            )
            return []

        if isinstance(stickers_data, dict):
            items = stickers_data.get("items", [])
        elif isinstance(stickers_data, list):
            items = stickers_data
        else:
            items = []
        return [StickerWrapper(s, cdn_base=self._cdn_base) for s in items]

    # ========================================================================
    #  Stub / placeholder methods
    # ========================================================================

    async def get_all_channels(self) -> List[FluxerChannelWrapper]:
        """Alias for :meth:`fetch_channels` – used by the shuttle UI."""
        return await self.fetch_channels()

    async def get_backed_up_channel_ids(self) -> Set[str]:
        """Stub – returns an empty set.  Only meaningful in backup mode."""
        return set()

    @property
    def threads(self) -> List:
        """Stub – returns an empty list.  Thread support is not yet implemented."""
        return []

    # ========================================================================
    #  Permission overwrites
    # ========================================================================

    async def get_channel_overwrites(
        self, channel_id: str
    ) -> List[Dict[str, Any]]:
        """Return permission overwrites for a channel.

        Matches the ``permission_overwrites`` array from the OpenAPI
        `ChannelResponse`_ schema.  Each overwrite dict contains:

        * ``id``     – target role/user ID
        * ``type``   – 0 for role, 1 for member
        * ``allow``  – allowed permissions bitfield
        * ``deny``   – denied permissions bitfield
        """
        await self._ensure_http()
        ch_data = await self._http.get_channel(channel_id)
        return ch_data.get("permission_overwrites", [])