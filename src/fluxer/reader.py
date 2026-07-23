# src/fluxer/reader.py
"""
Fluxer source reader – reads guild metadata, roles, channels, emojis,
stickers, messages, and permission overwrites from a Fluxer community via
its HTTP API.

Currently supports:
  • Server name / icon / banner ("Sync Server Settings")
  • Roles & role permissions
  • Server structure (channels & categories)
  • Channel & category permission overwrites
  • Custom emoji & sticker fetching and download
  • Message history migration
"""

import asyncio
import logging
import re
from typing import AsyncGenerator, Dict, Any, Optional, List, Set

from fluxer import HTTPClient, Guild, Channel, Role, Message

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CDN / asset URL helpers
# ---------------------------------------------------------------------------

_DEFAULT_FLUXER_CDN = "https://fluxerusercontent.com"


def _cdn_base_from_api_url(api_url: Optional[str]) -> str:
    """Derive the CDN host from a custom API URL.

    * Official instance: ``https://api.fluxer.app/v1`` → ``https://fluxerusercontent.com``
    * Self-hosted: ``https://fluxgard.omster.dev/api/v1`` → ``https://fluxgard.omster.dev``
    * Falls back to the official CDN if nothing can be determined.
    """
    if not api_url or api_url == "default":
        return _DEFAULT_FLUXER_CDN

    url = api_url.rstrip("/")

    # Official Fluxer: use the known CDN domain
    if "api.fluxer.app" in url:
        return _DEFAULT_FLUXER_CDN

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
# FluxerMessageWrapper – bridges fluxer Message ↔ Discord-compatible attrs
# ---------------------------------------------------------------------------

class _MessageFlags:
    """Thin wrapper so ``msg.flags.forwarded`` and ``msg.flags.value`` work."""
    __slots__ = ("value",)

    def __init__(self, value: int = 0):
        self.value = value

    @property
    def forwarded(self) -> bool:
        # Discord bit 5 (32); Fluxer likely uses the same bit for forwarded
        return bool(self.value & (1 << 5))


class _MessageReference:
    """Thin wrapper so ``msg.reference.message_id`` works."""
    __slots__ = ("message_id", "channel_id", "guild_id")

    def __init__(self, data: Optional[Dict[str, Any]]):
        if data:
            self.message_id = data.get("message_id")
            self.channel_id = data.get("channel_id")
            self.guild_id = data.get("guild_id")
        else:
            self.message_id = None
            self.channel_id = None
            self.guild_id = None


class _MentionRole:
    """Thin wrapper so ``msg.role_mentions[i].id`` works."""
    __slots__ = ("id",)
    def __init__(self, role_id: str):
        self.id = role_id


class _MentionChannel:
    """Thin wrapper so ``msg.channel_mentions[i].id`` / ``.name`` / ``.type`` work."""
    __slots__ = ("id", "name", "type")
    def __init__(self, data: Dict[str, Any]):
        self.id = data.get("id")
        self.name = data.get("name")
        self.type = data.get("type")


class FluxerMessageWrapper:
    """Wraps a ``fluxer.Message`` to add Discord‑compatible attributes
    (``.type``, ``.reference``, ``.flags``, ``.stickers``, etc.) that
    the migration code expects.

    All other attribute accesses are delegated to the inner Message.
    """

    __slots__ = ("_msg", "_data", "type", "reference", "flags",
                 "stickers", "mention_everyone", "mention_roles",
                 "role_mentions", "channel_mentions",
                 "tts", "webhook_id", "message_snapshots", "position",
                 "jump_url")

    def __init__(self, msg: Message, raw_data: Dict[str, Any],
                 web_base: str = "https://fluxer.app",
                 guild_id: str = ""):
        self._msg = msg
        self._data = raw_data

        # Critical Discord-compatible attributes from the raw API response
        self.type: int = raw_data.get("type", 0)
        self.reference: Optional[_MessageReference] = _MessageReference(
            raw_data.get("message_reference")
        )
        self.flags: _MessageFlags = _MessageFlags(raw_data.get("flags", 0))
        self.stickers: List[Dict[str, Any]] = raw_data.get("stickers", [])
        self.mention_everyone: bool = raw_data.get("mention_everyone", False)
        self.mention_roles: List[str] = raw_data.get("mention_roles", [])
        self.tts: bool = raw_data.get("tts", False)
        self.webhook_id: Optional[str] = raw_data.get("webhook_id")
        self.message_snapshots: List[Dict[str, Any]] = raw_data.get("message_snapshots", [])
        self.position: Optional[int] = raw_data.get("position")

        # Discord-compatible role / channel mention wrappers
        self.role_mentions: List[_MentionRole] = [
            _MentionRole(rid) for rid in raw_data.get("mention_roles", [])
        ]
        self.channel_mentions: List[_MentionChannel] = [
            _MentionChannel(ch) for ch in raw_data.get("mention_channels", [])
        ]

        # Construct jump URL (web app link to this message)
        ch_id = raw_data.get("channel_id", "")
        msg_id = raw_data.get("id", "")
        self.jump_url: str = f"{web_base}/channels/{guild_id}/{ch_id}/{msg_id}"

    def __getattr__(self, name: str):
        # Delegate everything else to the inner fluxer Message
        return getattr(self._msg, name)

    def __repr__(self) -> str:
        return f"<FluxerMsg id={self._msg.id} type={self.type}>"


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

    # -- Message type constants (mirror Discord's for compatibility) ---------
    MESSAGE_TYPE_DEFAULT                    = 0
    MESSAGE_TYPE_REPLY                      = 19
    MESSAGE_TYPE_THREAD_STARTER             = 21
    MESSAGE_TYPE_FORWARD                    = 22   # Fluxer/Discord forward type
    MESSAGE_TYPE_CHAT_INPUT_COMMAND         = 20
    MESSAGE_TYPE_CONTEXT_MENU_COMMAND       = 23
    MESSAGE_TYPE_POLL_RESULT                = 46
    MESSAGE_TYPE_AUTO_MODERATION_ACTION     = 24
    MESSAGE_TYPE_GUILD_MEMBER_JOIN          = 7

    PLATFORM_NAME = "fluxer"

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

    @property
    def web_base_url(self) -> str:
        """Base URL for the Fluxer web app (used for jump URLs).

        Derived from the API base by stripping any ``/api/…`` path.
        """
        if "api.fluxer.app" in self._api_base:
            return "https://fluxer.app"
        # Self-hosted: scheme + host only (no /api path)
        m = re.match(r"(https?://[^/]+)", self._api_base)
        return m.group(1) if m else self._api_base

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
        (``icon`` / ``banner`` fields).  Full CDN URLs are composed here,
        matching the fluxer library's own ``Guild.icon_url`` convention
        (``a_`` prefix → ``.gif``, otherwise ``.png``).
        """
        await self._ensure_http()
        data = self.guild_data or {}
        g = self.guild

        def _url(kind: str, hash_val: Optional[str]) -> Optional[str]:
            if not hash_val:
                return None
            ext = "gif" if hash_val.startswith("a_") else "png"
            return f"{self._cdn_base}/{kind}/{self.community_id}/{hash_val}.{ext}"

        return {
            "name": g.name if g else "Unknown",
            "id": str(g.id) if g else self.community_id,
            "icon_url": _url("icons", data.get("icon")),
            "banner_url": _url("banners", data.get("banner")),
        }

    # ========================================================================
    #  Asset download helpers
    # ========================================================================

    async def download_asset(self, asset: str) -> bytes:
        """Download a guild asset (icon, banner) from a URL or raw hash.

        Accepts two forms:
        1. A full URL (e.g. ``https://fluxerusercontent.com/icons/...``)
        2. A raw asset hash string (e.g. ``"a_a5f0cb3d"``) – in this case
           the hash is matched against the cached guild data to determine
           whether it belongs to an icon or banner, and the correct CDN
           URL is composed automatically.

        Returns empty ``bytes`` on any failure.
        """
        if not asset or not self._http:
            return b""

        # Already a full URL – download directly
        if asset.startswith(("http://", "https://")):
            try:
                async with self._http._session.get(asset) as resp:
                    if resp.status == 200:
                        return await resp.read()
            except Exception:
                logger.debug("Failed to download asset: %s", asset, exc_info=True)
            return b""

        # Raw hash string – compose a CDN URL
        await self._ensure_http()
        data = self.guild_data or {}

        # Determine whether this hash is the icon or the banner
        if asset == data.get("icon"):
            kind = "icons"
        elif asset == data.get("banner"):
            kind = "banners"
        else:
            # Unknown hash – try icon first (most common case)
            kind = "icons"

        ext = "gif" if asset.startswith("a_") else "png"
        url = f"{self._cdn_base}/{kind}/{self.community_id}/{asset}.{ext}"

        try:
            async with self._http._session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
        except Exception:
            logger.debug("Failed to download asset: %s", url, exc_info=True)
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
    #  Messages
    # ========================================================================

    async def get_message(
        self, channel_id: str, message_id: str
    ) -> Optional[FluxerMessageWrapper]:
        """Fetch a single message via ``GET /channels/{channel_id}/messages/{message_id}``.

        Returns a :class:`FluxerMessageWrapper` with Discord-compatible
        attributes (``.type``, ``.reference``, ``.flags``, ``.stickers``,
        etc.), or ``None`` on failure.
        """
        await self._ensure_http()
        try:
            raw = await self._http.get_message(channel_id, message_id)
            msg = Message.from_data(raw)
            return FluxerMessageWrapper(msg, raw,
                                         web_base=self.web_base_url,
                                         guild_id=self.community_id)
        except Exception:
            logger.debug(
                "Failed to fetch message %s in channel %s",
                message_id, channel_id, exc_info=True,
            )
            return None

    async def get_first_message(
        self, channel_id: str
    ) -> Optional[FluxerMessageWrapper]:
        """Return the oldest message in *channel_id*."""
        await self._ensure_http()
        try:
            msgs = await self._http.get_messages(
                channel_id, limit=1
            )
            if msgs:
                raw = msgs[0]
                msg = Message.from_data(raw)
                return FluxerMessageWrapper(msg, raw,
                                             web_base=self.web_base_url,
                                             guild_id=self.community_id)
        except Exception:
            logger.debug(
                "Failed to fetch first message in channel %s",
                channel_id, exc_info=True,
            )
        return None

    async def fetch_message_history(
        self,
        channel_id: str,
        limit: Optional[int] = None,
        after_id: Optional[str] = None,
        inclusive: bool = False,
    ) -> AsyncGenerator[FluxerMessageWrapper, None]:
        """Yield messages from oldest to newest with automatic pagination.

        The Fluxer API returns messages newest-first and supports ``before``
        for backwards pagination.  We collect **all** batches by walking
        backwards from the newest message, then reverse the full list to
        yield oldest-first -- No alternative for newest-first API.

        Parameters
        ----------
        channel_id : str
            The channel to fetch messages from.
        limit : int, optional
            Maximum total messages to yield.  ``None`` means fetch all.
        after_id : str, optional
            Snowflake ID — only messages with ID > *after_id* are yielded
            (exclusive).  When *inclusive* is ``True``, the message with
            ``id == after_id`` is included as the first result.
        inclusive : bool
            If ``True`` and *after_id* is set, the message with that ID is
            the first one yielded.
        """
        await self._ensure_http()

        # ── Phase 1: collect all batches (newest → oldest) ──────────────
        all_raw: List[Dict[str, Any]] = []
        before_id: Optional[str] = None

        while True:
            to_fetch = 100
            if limit is not None:
                remaining = limit - len(all_raw)
                if remaining <= 0:
                    break
                to_fetch = min(to_fetch, remaining)

            params: Dict[str, Any] = {"limit": to_fetch}
            if before_id:
                params["before"] = before_id

            try:
                batch = await self._http.get_messages(channel_id, **params)
            except Exception as exc:
                logger.error(
                    "Error fetching messages for channel %s: %s",
                    channel_id, exc,
                )
                break

            if not batch:
                break

            all_raw.extend(batch)

            # The *last* message in a newest-first batch is the oldest.
            # Use its ID as ``before`` to fetch the next (older) page.
            before_id = batch[-1].get("id")

            if len(batch) < to_fetch:
                break

        # ── Phase 2: reverse → oldest-first, apply after_id filter ──────
        all_raw.reverse()

        fetched = 0
        for raw in all_raw:
            msg_id = raw.get("id")

            # Honour after_id filter
            if after_id is not None:
                if inclusive:
                    if str(msg_id) < str(after_id):
                        continue
                else:
                    if str(msg_id) <= str(after_id):
                        continue

            msg = Message.from_data(raw)
            wrapped = FluxerMessageWrapper(msg, raw,
                                           web_base=self.web_base_url,
                                           guild_id=self.community_id)
            yield wrapped
            fetched += 1

            if limit is not None and fetched >= limit:
                return

    async def download_attachment(self, attachment: Any) -> bytes:
        """Download a message attachment.

        Accepts a fluxer ``Attachment`` object (extracts ``.url``) or a raw
        URL string.  Returns empty ``bytes`` on any failure.
        """
        # Fluxer Attachment object → extract URL
        url = getattr(attachment, "url", None)
        if url:
            return await self.download_asset(url)
        # Raw URL string
        if isinstance(attachment, str):
            return await self.download_asset(attachment)
        return b""

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