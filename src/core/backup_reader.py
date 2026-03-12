"""
BackupReader — discord.py-compatible local data provider.

Reads from local backup JSON files (produced by Reaper) instead of the
Discord API.  Implements the same public interface as DiscordReader so that
migration scripts and UI code can use either provider transparently.
"""

import json
import logging
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lightweight enum clones (mirror discord.py values for compatibility)
# ---------------------------------------------------------------------------

class ChannelType(IntEnum):
    text = 0
    voice = 2
    category = 4
    news = 5
    public_thread = 11
    forum = 15


class MessageType(IntEnum):
    default = 0
    reply = 19
    thread_starter_message = 21


# ---------------------------------------------------------------------------
# Backup colour / permissions helpers
# ---------------------------------------------------------------------------

class BackupColor:
    """Minimal stand-in for discord.Color."""

    __slots__ = ("value",)

    def __init__(self, value: int = 0):
        self.value = value

    def __str__(self) -> str:
        return f"#{self.value:06x}"

    def __repr__(self) -> str:
        return f"BackupColor(value={self.value})"

    def __eq__(self, other):
        if isinstance(other, (BackupColor, int)):
            return self.value == (other.value if isinstance(other, BackupColor) else other)
        return NotImplemented

    @classmethod
    def from_hex(cls, hex_str: str) -> "BackupColor":
        """Parse '#rrggbb' or '0x...' to BackupColor."""
        hex_str = hex_str.lstrip("#")
        try:
            return cls(int(hex_str, 16))
        except ValueError:
            return cls(0)


class BackupPermissions:
    """Minimal stand-in for discord.Permissions."""

    __slots__ = ("value",)

    def __init__(self, value: int = 0):
        self.value = value

    @property
    def view_channel(self) -> bool:
        return bool(self.value & 0x400)

    @property
    def read_message_history(self) -> bool:
        return bool(self.value & 0x10000)

    def __repr__(self) -> str:
        return f"BackupPermissions(value={self.value})"


class BackupPermissionOverwrite:
    """Minimal stand-in for discord.PermissionOverwrite.
    
    Supports:
      - .pair() -> (BackupPermissions(allow), BackupPermissions(deny))
      - iteration: yields (perm_name, True|False|None) tuples
      - setattr(ow, perm_name, value) for merging
    """

    # Standard Discord permission flag names and their bit positions
    VALID_NAMES = {
        "create_instant_invite": 0, "kick_members": 1, "ban_members": 2,
        "administrator": 3, "manage_channels": 4, "manage_guild": 5,
        "add_reactions": 6, "view_audit_log": 7, "priority_speaker": 8,
        "stream": 9, "view_channel": 10, "send_messages": 11,
        "send_tts_messages": 12, "manage_messages": 13, "embed_links": 14,
        "attach_files": 15, "read_message_history": 16, "mention_everyone": 17,
        "use_external_emojis": 18, "view_guild_insights": 19, "connect": 20,
        "speak": 21, "mute_members": 22, "deafen_members": 23,
        "move_members": 24, "use_vad": 25, "change_nickname": 26,
        "manage_nicknames": 27, "manage_roles": 28, "manage_webhooks": 29,
        "manage_emojis_and_stickers": 30, "use_application_commands": 31,
        "request_to_speak": 32, "manage_events": 33, "manage_threads": 34,
        "create_public_threads": 35, "create_private_threads": 36,
        "use_external_stickers": 37, "send_messages_in_threads": 38,
        "use_embedded_activities": 39, "moderate_members": 40,
    }

    def __init__(self, allow: int = 0, deny: int = 0):
        self._allow = allow
        self._deny = deny

    def pair(self):
        """Returns (Permissions(allow), Permissions(deny))."""
        return BackupPermissions(self._allow), BackupPermissions(self._deny)

    def __iter__(self):
        """Yields (perm_name, True|False|None) for each known permission."""
        for name, bit in self.VALID_NAMES.items():
            mask = 1 << bit
            if self._allow & mask:
                yield name, True
            elif self._deny & mask:
                yield name, False
            else:
                yield name, None

    def __setattr__(self, name, value):
        if name.startswith("_") or name not in self.VALID_NAMES:
            super().__setattr__(name, value)
            return
        bit = self.VALID_NAMES[name]
        mask = 1 << bit
        # Clear both first
        self._allow &= ~mask
        self._deny &= ~mask
        if value is True:
            self._allow |= mask
        elif value is False:
            self._deny |= mask
        # None = neutral, both cleared


class BackupOverwriteTarget:
    """Stand-in for the target (role) key in overwrites dict.
    
    Satisfies: type(target).__name__ == 'Role' and target.id
    """
    def __init__(self, role_id: int):
        self.id = role_id

    def __repr__(self):
        return f"Role(id={self.id})"

# Allow `type(target).__name__` to return "Role"
BackupOverwriteTarget.__name__ = "Role"
BackupOverwriteTarget.__qualname__ = "Role"


# ---------------------------------------------------------------------------
# Backup discord.py model objects
# ---------------------------------------------------------------------------

class BackupAsset:
    """Minimal stand-in for discord.Asset.  Points to a local file."""

    __slots__ = ("_path", "url")

    def __init__(self, local_path: Path | str | None):
        self._path = Path(local_path) if local_path else None
        self.url = str(local_path) if local_path else None

    async def read(self) -> bytes:
        if self._path and self._path.exists():
            return self._path.read_bytes()
        return b""

    def is_animated(self) -> bool:
        if self._path:
            return self._path.suffix.lower() in (".gif",)
        return False

    def __bool__(self) -> bool:
        return self._path is not None and self._path.exists()


class BackupRole:
    """Minimal stand-in for discord.Role."""

    __slots__ = ("id", "name", "color", "position", "permissions", "hoist", "managed", "mentionable")

    def __init__(self, data: dict):
        self.id = int(data["id"])
        self.name = data["name"]
        self.color = BackupColor.from_hex(data.get("color", "#000000"))
        self.position = data.get("position", 0)
        self.permissions = BackupPermissions(int(data.get("permissions", 0)))
        self.hoist = data.get("hoist", False)
        self.managed = data.get("managed", False)
        self.mentionable = data.get("mentionable", True)

    @property
    def mention(self) -> str:
        return f"<@&{self.id}>"

    @property
    def created_at(self) -> datetime:
        return datetime.now(timezone.utc)

    def is_default(self) -> bool:
        return self.name == "@everyone"

    def __repr__(self) -> str:
        return f"BackupRole(id={self.id}, name='{self.name}')"


def _parse_overwrites(raw_list: list) -> dict:
    """Parse overwrites JSON list into {BackupOverwriteTarget: BackupPermissionOverwrite} dict."""
    result = {}
    for entry in raw_list:
        target = BackupOverwriteTarget(int(entry["id"]))
        ow = BackupPermissionOverwrite(allow=int(entry.get("allow", 0)),
                                        deny=int(entry.get("deny", 0)))
        result[target] = ow
    return result


class BackupCategory:
    """Minimal stand-in for discord.CategoryChannel."""

    __slots__ = ("id", "name", "position", "type", "overwrites")

    def __init__(self, data: dict):
        try:
            self.id = int(data["id"])
        except (ValueError, TypeError):
            self.id = 0  # 'uncategorized' sentinel
        self.name = data["name"]
        self.position = data.get("position", 0)
        self.type = ChannelType.category
        self.overwrites = _parse_overwrites(data.get("overwrites", []))

    def __repr__(self) -> str:
        return f"BackupCategory(id={self.id}, name='{self.name}')"


class BackupChannel:
    """Minimal stand-in for discord.TextChannel / ForumChannel / VoiceChannel."""

    __slots__ = ("id", "name", "type", "position", "topic", "nsfw",
                 "category_id", "available_tags", "parent_id", "guild", "overwrites")

    _TYPE_MAP = {
        "text": ChannelType.text,
        "voice": ChannelType.voice,
        "news": ChannelType.news,
        "forum": ChannelType.forum,
        "thread": ChannelType.public_thread,
    }

    def __init__(self, data: dict, category_id: int | None = None, guild: "BackupGuild|None" = None):
        self.id = int(data["id"])
        self.name = data["name"]
        self.type = self._TYPE_MAP.get(data.get("type", "text"), ChannelType.text)
        self.position = data.get("position", 0)
        self.topic = data.get("topic")
        self.nsfw = data.get("nsfw", False)
        self.category_id = category_id
        self.parent_id = category_id
        self.available_tags = data.get("available_tags", [])
        self.guild = guild
        self.overwrites = _parse_overwrites(data.get("overwrites", []))

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"

    @property
    def created_at(self) -> datetime:
        return datetime.now(timezone.utc)

    @property
    def jump_url(self) -> str:
        return f"https://discord.com/channels/{self.guild.id if self.guild else 0}/{self.id}"

    def permissions_for(self, member):
        """Minimal mock for permissions_for. Returns full permissions for now."""
        return BackupPermissions(0x7FFFFFFF)

    def __repr__(self) -> str:
        return f"BackupChannel(id={self.id}, name='{self.name}', type={self.type})"


class BackupMember:
    """Minimal stand-in for discord.Member / discord.User."""

    __slots__ = ("id", "name", "display_name", "bot", "color",
                 "roles", "avatar", "guild_permissions", "discriminator",
                 "global_name", "created_at", "joined_at", "status", "activity", "system",
                 "_avatar_url")

    def __init__(self, data: dict, role_objects: list | None = None,
                 avatar_base: Path | None = None):
        self.id = int(data["userID"])
        self.name = data.get("username", "Unknown")
        self.display_name = data.get("userNickname") or self.name
        self.global_name = self.display_name
        self.bot = data.get("userIsBot", False)
        self.system = False
        self.discriminator = "0000"
        self.color = BackupColor.from_hex(data.get("userColor") or "#000000")
        self.roles = sorted(role_objects or [], key=lambda r: r.position, reverse=True)
        self.guild_permissions = BackupPermissions(0)
        
        self.created_at = datetime.now(timezone.utc)
        self.joined_at = datetime.now(timezone.utc)
        self.status = type("Status", (), {"value": "offline"})()
        self.activity = None

        # CDN URL from Discord (saved during backup)
        self._avatar_url = data.get("userAvatarUrl")

        # Local file asset (for reading bytes)
        avatar_rel = data.get("userAvatar")
        if avatar_rel and avatar_base:
            self.avatar = BackupAsset(avatar_base / avatar_rel)
        else:
            self.avatar = BackupAsset(None)

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"

    @property
    def top_role(self) -> "BackupRole|None":
        return self.roles[0] if self.roles else None

    @property
    def display_avatar(self) -> BackupAsset:
        """Returns an asset with the CDN URL if available, otherwise the local file asset."""
        if self._avatar_url:
            asset = BackupAsset(None)
            asset.url = self._avatar_url
            return asset
        return self.avatar

    def __repr__(self) -> str:
        return f"BackupMember(id={self.id}, name='{self.name}')"


class BackupAttachment:
    """Minimal stand-in for discord.Attachment."""

    __slots__ = ("id", "filename", "size", "url", "proxy_url", "_backup_root")

    def __init__(self, data: dict, backup_root: Path | None = None):
        self.id = int(data["id"])
        self.filename = data.get("fileName", "unknown")
        self.size = data.get("fileSizeBytes", 0)
        self.url = data.get("url", "")
        self.proxy_url = self.url
        self._backup_root = backup_root

    async def read(self) -> bytes:
        if self._backup_root:
            full = self._backup_root / self.url
            if full.exists():
                return full.read_bytes()
        return b""

    async def save(self, path) -> None:
        data = await self.read()
        Path(path).write_bytes(data)

    def __repr__(self) -> str:
        return f"BackupAttachment(id={self.id}, filename='{self.filename}')"


class BackupEmoji:
    """Minimal stand-in for discord.Emoji."""

    __slots__ = ("id", "name", "animated", "url", "_file_path")

    def __init__(self, data: dict, media_dir: Path | None = None):
        self.id = int(data["id"])
        self.name = data["name"]
        self.animated = data.get("animated", False)
        filename = data.get("filename", "")
        self._file_path = media_dir / filename if media_dir and filename else None
        self.url = str(self._file_path) if self._file_path else None

    async def read(self) -> bytes:
        if self._file_path and self._file_path.exists():
            return self._file_path.read_bytes()
        return b""

    def __repr__(self) -> str:
        return f"BackupEmoji(id={self.id}, name='{self.name}')"


class BackupSticker:
    """Minimal stand-in for discord.GuildSticker."""

    __slots__ = ("id", "name", "url", "format", "_backup_root")

    def __init__(self, data: dict, backup_root: Path | None = None):
        self.id = int(data["messageID"]) if "messageID" in data else int(data.get("id", 0))
        self.name = data.get("name", "Sticker")
        self.url = data.get("localPath", "")
        self._backup_root = backup_root
        self.format = data.get("format", "png")

    async def read(self) -> bytes:
        if self._backup_root and self.url:
            full = self._backup_root / self.url
            if full.exists():
                return full.read_bytes()
        return b""

    def __repr__(self) -> str:
        return f"BackupSticker(id={self.id}, name='{self.name}')"


class BackupPartialEmoji:
    """Minimal stand-in for discord.PartialEmoji."""

    __slots__ = ("name", "id", "animated")

    def __init__(self, name: str, id: int | None = None, animated: bool = False):
        self.name = name
        self.id = id
        self.animated = animated

    def __str__(self) -> str:
        if self.id:
            return f"<{'a' if self.animated else ''}:{self.name}:{self.id}>"
        return self.name


class BackupReaction:
    """Minimal stand-in for discord.Reaction."""

    __slots__ = ("emoji", "count")

    def __init__(self, data: dict):
        emoji_raw = data.get("emoji", "")
        self.count = data.get("count", 0)

        if ":" in emoji_raw and not emoji_raw.startswith("<"):
            parts = emoji_raw.split(":", 1)
            try:
                self.emoji = BackupPartialEmoji(name=parts[0], id=int(parts[1]))
            except (ValueError, IndexError):
                self.emoji = BackupPartialEmoji(name=emoji_raw)
        else:
            self.emoji = BackupPartialEmoji(name=emoji_raw)

    def is_custom_emoji(self) -> bool:
        return self.emoji.id is not None


class BackupMessageReference:
    """Minimal stand-in for discord.MessageReference."""

    __slots__ = ("message_id", "channel_id")

    def __init__(self, data: dict):
        self.message_id = int(data["messageId"])
        self.channel_id = int(data["channelId"])


class BackupThread:
    """Minimal stand-in for discord.Thread metadata attached to a starter message."""

    __slots__ = ("id", "name", "message_count", "archived",
                 "auto_archive_duration", "locked", "parent_id")

    def __init__(self, data: dict, parent_id: int | None = None):
        self.id = int(data["id"])
        self.name = data.get("name", "")
        self.message_count = data.get("message_count", 0)
        self.archived = data.get("archived", False)
        self.auto_archive_duration = data.get("auto_archive_duration", 1440)
        self.locked = data.get("locked", False)
        self.parent_id = parent_id


class BackupMessage:
    """Minimal stand-in for discord.Message."""

    _TYPE_MAP = {
        "Default": MessageType.default,
        "Reply": MessageType.reply,
        "ThreadStarter": MessageType.thread_starter_message,
        "Forward": MessageType.default,
    }

    __slots__ = ("id", "type", "created_at", "pinned", "content", "author",
                 "attachments", "embeds", "stickers", "reactions",
                 "reference", "thread", "channel_id", "flags", "guild", "channel",
                 "mentions", "role_mentions", "channel_mentions", "mention_everyone",
                 "tts", "nonce", "webhook_id", "application_id", "activity",
                 "application", "interaction", "components", "jump_url")

    def __init__(self, data: dict, *,
                 author: BackupMember | None = None,
                 guild: "BackupGuild | None" = None,
                 channel: BackupChannel | None = None,
                 backup_root: Path | None = None):
        self.id = int(data["messageID"])
        self.type = self._TYPE_MAP.get(data.get("type", "Default"), MessageType.default)
        self.pinned = data.get("isPinned", False)
        self.content = data.get("content", "")
        self.author = author
        self.guild = guild
        self.channel = channel
        self.channel_id = channel.id if channel else (int(data.get("channelID")) if data.get("channelID") else None)

        # Mentions (if not in backup, default to empty)
        self.mentions = []
        self.role_mentions = []
        self.channel_mentions = []
        self.mention_everyone = data.get("mentionEveryone", False)

        # Standard discord.Message properties
        self.tts = False
        self.nonce = None
        self.webhook_id = None
        self.application_id = None
        self.activity = None
        self.application = None
        self.interaction = None
        self.components = []
        self.jump_url = f"https://discord.com/channels/{guild.id if guild else 0}/{self.channel_id}/{self.id}"

        # Timestamp
        ts = data.get("timestamp")
        if ts:
            try:
                self.created_at = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                self.created_at = datetime.now(timezone.utc)
        else:
            self.created_at = datetime.now(timezone.utc)

        # Attachments
        self.attachments = [
            BackupAttachment(a, backup_root=backup_root)
            for a in data.get("attachments", [])
        ]

        # Embeds — store raw dicts (discord.py Embed.from_dict compatible)
        self.embeds = data.get("embeds", [])

        # Stickers
        self.stickers = [
            BackupSticker(s, backup_root=backup_root)
            for s in data.get("stickers", [])
        ]

        # Reactions
        self.reactions = [BackupReaction(r) for r in data.get("reactions", [])]

        # Reference (replies)
        ref = data.get("reference")
        self.reference = BackupMessageReference(ref) if ref else None

        # Thread info
        thread_data = data.get("thread")
        self.thread = BackupThread(thread_data, parent_id=self.channel_id) if thread_data else None

        # Flags placeholder
        self.flags = type("Flags", (), {
            "forwarded": data.get("type") == "Forward",
            "value": 0  # Bitmask value
        })()

    def __repr__(self) -> str:
        return f"BackupMessage(id={self.id}, author={self.author})"


class BackupGuild:
    """Minimal stand-in for discord.Guild."""

    __slots__ = ("id", "name", "icon", "banner", "_reader")

    def __init__(self, data: dict, backup_path: Path, reader: "BackupReader" = None):
        self.id = int(data["id"])
        self.name = data["name"]
        self._reader = reader

        icon_rel = data.get("icon")
        self.icon = BackupAsset(backup_path / icon_rel) if icon_rel else BackupAsset(None)

        banner_rel = data.get("banner")
        self.banner = BackupAsset(backup_path / banner_rel) if banner_rel else BackupAsset(None)

    @property
    def roles(self) -> List[BackupRole]:
        return self._reader._roles if self._reader else []

    @property
    def members(self) -> List[BackupMember]:
        return self._reader._members if self._reader else []

    @property
    def channels(self) -> List[BackupChannel]:
        return self._reader._channels if self._reader else []

    @property
    def categories(self) -> List[BackupCategory]:
        return self._reader._categories if self._reader else []

    @property
    def emojis(self) -> List[BackupEmoji]:
        return self._reader._emojis if self._reader else []

    @property
    def stickers(self) -> List[BackupSticker]:
        return self._reader._stickers if self._reader else []

    def get_member(self, user_id: int) -> "BackupMember | None":
        if self._reader:
            return self._reader._member_map.get(int(user_id))
        return None

    def get_role(self, role_id: int) -> "BackupRole | None":
        if self._reader:
            return next((r for r in self._reader._roles if r.id == int(role_id)), None)
        return None

    def get_channel(self, channel_id: int) -> "BackupChannel | None":
        if self._reader:
            return next((c for c in self._reader._channels if c.id == int(channel_id)), None)
        return None

    def __repr__(self) -> str:
        return f"BackupGuild(id={self.id}, name='{self.name}')"


# ---------------------------------------------------------------------------
# Custom Forbidden exception for backup context
# ---------------------------------------------------------------------------

class BackupForbidden(Exception):
    """Raised when a requested resource doesn't exist in the backup."""
    pass


# ---------------------------------------------------------------------------
# BackupReader — main provider class
# ---------------------------------------------------------------------------

class BackupReader:
    """Reads from local backup files instead of the Discord API.

    Implements the same public interface as DiscordReader so that
    migration scripts and UI code can use either provider transparently.
    """

    # -- Provider constants (same interface as DiscordReader) --
    MESSAGE_TYPE_DEFAULT = MessageType.default
    MESSAGE_TYPE_REPLY = MessageType.reply
    MESSAGE_TYPE_THREAD_STARTER = MessageType.thread_starter_message

    Forbidden = BackupForbidden

    CHANNEL_TYPE_TEXT = ChannelType.text
    CHANNEL_TYPE_NEWS = ChannelType.voice  # simplified
    CHANNEL_TYPE_FORUM = ChannelType.forum

    @staticmethod
    def find_item(iterable, **attrs):
        """Find first item in iterable matching all attrs."""
        for item in iterable:
            if all(getattr(item, k, None) == v for k, v in attrs.items()):
                return item
        return None

    @staticmethod
    def create_permission_overwrite():
        """Factory for a PermissionOverwrite mock."""
        return BackupPermissionOverwrite()

    def __init__(self, backup_path: str | Path):
        self.backup_path = Path(backup_path)
        self.guild: BackupGuild | None = None

        self._thread_info: Dict[int, Dict[str, Any]] = {}  # channel_id -> metadata (like name, parentID)

        # Lazy loading flags
        self._roles_loaded = False
        self._structure_loaded = False
        self._assets_loaded = False
        self._members_loaded = False
        
        # Internal storage
        self._categories: List[BackupCategory] = []
        self._channels: List[BackupChannel] = []
        self._roles: List[BackupRole] = []
        self._emojis: List[BackupEmoji] = []
        self._stickers: List[BackupSticker] = []
        self._members: List[BackupMember] = []
        self._member_map: Dict[int, BackupMember] = {}

    # ── startup ──────────────────────────────────────────────────────────

    async def start(self):
        """Initializes the backup path and loads the server profile."""
        bp = self.backup_path

        # 1. Server profile -> BackupGuild
        profile_file = bp / "server_profile" / "profile.json"
        if profile_file.exists():
            profile = json.loads(profile_file.read_text(encoding="utf-8"))
            self.guild = BackupGuild(profile, bp, reader=self)
            logger.info(f"[Backup] Initialized server: {self.guild.name} ({self.guild.id})")
        else:
            logger.warning(f"[Backup] server_profile/profile.json not found in {bp}")
            self.guild = None

    @property
    def roles(self) -> List[BackupRole]:
        if not self._roles_loaded:
            roles_file = self.backup_path / "server_profile" / "roles.json"
            if roles_file.exists():
                logger.info(f"[Backup] Lazy-loading roles...")
                roles_data = json.loads(roles_file.read_text(encoding="utf-8"))
                self._roles = [BackupRole(r) for r in roles_data]
            self._roles_loaded = True
        return self._roles

    @property
    def categories(self) -> List[BackupCategory]:
        self._ensure_structure_loaded()
        return self._categories

    @property
    def channels(self) -> List[BackupChannel]:
        self._ensure_structure_loaded()
        return self._channels

    def _ensure_structure_loaded(self):
        if self._structure_loaded:
            return
        struct_file = self.backup_path / "server_profile" / "structure.json"
        if struct_file.exists():
            logger.info(f"[Backup] Lazy-loading server structure...")
            structure = json.loads(struct_file.read_text(encoding="utf-8"))
            for cat_data in structure:
                cat = BackupCategory(cat_data)
                if cat.id != 0:
                    self._categories.append(cat)
                for ch_data in cat_data.get("channels", []):
                    ch_cat_id = cat.id if cat.id != 0 else None
                    channel = BackupChannel(ch_data, category_id=ch_cat_id, guild=self.guild)
                    self._channels.append(channel)
        self._structure_loaded = True

    @property
    def emojis(self) -> List[BackupEmoji]:
        self._ensure_assets_loaded()
        return self._emojis

    @property
    def stickers(self) -> List[BackupSticker]:
        self._ensure_assets_loaded()
        return self._stickers

    def _ensure_assets_loaded(self):
        if self._assets_loaded:
            return
        assets_file = self.backup_path / "server_profile" / "assets.json"
        media_dir = self.backup_path / "server_profile" / "assets"
        if assets_file.exists():
            logger.info(f"[Backup] Lazy-loading assets...")
            assets = json.loads(assets_file.read_text(encoding="utf-8"))
            self._emojis = [BackupEmoji(e, media_dir) for e in assets.get("emojis", [])]
            self._stickers = [BackupSticker(s, media_dir) for s in assets.get("stickers", [])]
        self._assets_loaded = True

    @property
    def members(self) -> List[BackupMember]:
        self._ensure_members_loaded()
        return self._members

    def _ensure_members_loaded(self):
        if self._members_loaded:
            return
        user_info_file = self.backup_path / "message_backup" / "users" / "user_info.json"
        if user_info_file.exists():
            logger.info(f"[Backup] Lazy-loading members...")
            try:
                users = json.loads(user_info_file.read_text(encoding="utf-8"))
                backup_root = self.backup_path / "message_backup"
                for u in users:
                    user_role_ids = {int(r["id"]) for r in u.get("userRoles", [])}
                    # Note: this triggers roles lazy load
                    role_objs = [r for r in self.roles if r.id in user_role_ids]
                    member = BackupMember(u, role_objects=role_objs, avatar_base=backup_root)
                    self._members.append(member)
                    self._member_map[member.id] = member
            except Exception as e:
                logger.warning(f"[Backup] Failed to lazy-load user_info.json: {e}")
        self._members_loaded = True

    # ── validation ───────────────────────────────────────────────────────

    async def validate(self) -> Dict[str, Any]:
        """Validates backup directory integrity."""
        results = {
            "token": False,
            "server": False,
            "bot_name": None,
            "server_name": None,
            "intents": {"message_content": True},
            "permissions": {"view_channel": True, "read_message_history": True},
        }

        bp = self.backup_path
        if not bp.exists() or not bp.is_dir():
            return results

        profile = bp / "server_profile" / "profile.json"
        structure = bp / "server_profile" / "structure.json"
        if profile.exists() and structure.exists():
            try:
                data = json.loads(profile.read_text(encoding="utf-8"))
                results["token"] = True
                results["server"] = True
                results["bot_name"] = "LOCAL BACKUP"
                results["server_name"] = data.get("name", "Unknown")
            except Exception:
                pass

        return results

    # ── server metadata ──────────────────────────────────────────────────

    async def get_server_metadata(self) -> Dict[str, Any]:
        if not self.guild:
            return {}
        return {
            "name": self.guild.name,
            "id": str(self.guild.id),
            "icon_url": self.guild.icon.url if self.guild.icon else None,
            "banner_url": self.guild.banner.url if self.guild.banner else None,
        }

    async def download_asset(self, asset: BackupAsset) -> bytes:
        return await asset.read()

    # ── categories & channels ────────────────────────────────────────────

    async def get_categories(self) -> List[BackupCategory]:
        return list(self._categories)

    async def get_channels(self, category_id: int | None = None) -> List[BackupChannel]:
        channels = [c for c in self._channels if c.type != ChannelType.category]
        if category_id is not None:
            channels = [c for c in channels if c.category_id == category_id]
        return channels

    async def get_backed_up_channel_ids(self) -> List[int]:
        """Returns a list of channel IDs that have corresponding backup directories."""
        backup_dir = self.backup_path / "message_backup"
        if not backup_dir.exists():
            return []
        
        ids = []
        for d in backup_dir.iterdir():
            if not d.is_dir():
                continue
            if d.name == "users":
                continue
            # A backed-up channel has a messages.json inside its directory
            if (d / "messages.json").exists():
                try:
                    ids.append(int(d.name))
                except ValueError:
                    pass
        return ids

    async def get_channel(self, channel_id: int) -> BackupChannel | None:
        for c in self._channels:
            if c.id == channel_id:
                return c
        return None

    # ── roles, emojis, stickers, members ─────────────────────────────────

    async def get_roles(self) -> List[BackupRole]:
        return [r for r in self._roles if not r.is_default()]

    async def get_emojis(self) -> List[BackupEmoji]:
        return list(self._emojis)

    async def get_stickers(self) -> List[BackupSticker]:
        return list(self._stickers)

    async def get_members(self) -> List[BackupMember]:
        return list(self._members)

    # ── messages ─────────────────────────────────────────────────────────

    def _resolve_author(self, user_id_str: str) -> BackupMember:
        """Returns BackupMember for a userID, creating a stub if missing."""
        uid = int(user_id_str)
        if uid in self._member_map:
            return self._member_map[uid]
        stub = BackupMember({
            "userID": user_id_str,
            "username": f"User#{user_id_str[-4:]}",
            "userIsBot": False,
        })
        self._member_map[uid] = stub
        return stub

    def _load_channel_messages_data(self, channel_id: int) -> list[dict]:
        """Loads the raw messages array from a channel JSON file."""
        bp = self.backup_path / "message_backup"
        
        # Primary: message_backup/{channel_id}/messages.json
        json_file = bp / str(channel_id) / "messages.json"
        if not json_file.exists():
            # Fallback: search for thread_messages.json inside any parent channel
            for candidate in bp.glob(f"*/{channel_id}/thread_messages.json"):
                if candidate.exists():
                    json_file = candidate
                    break
            else:
                return []

        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            
            # Cache thread info if this is a thread
            if "parentID" in data:
                self._thread_info[channel_id] = {
                    "name": data.get("channelName", "Unknown Thread"),
                    "parent_id": int(data["parentID"])
                }
            
            return data.get("messages", [])
        except Exception as e:
            logger.error(f"[Backup] Failed to load messages for channel {channel_id}: {e}")
            return []

    def _hydrate_message(self, msg_data: dict, channel_id: int) -> BackupMessage:
        author = self._resolve_author(msg_data.get("userID", "0"))
        backup_root = self.backup_path / "message_backup"
        
        # Resolve channel object
        channel = next((c for c in self._channels if c.id == channel_id), None)
        
        # If not found in channels, check if it's a known thread
        if not channel and channel_id in self._thread_info:
            info = self._thread_info[channel_id]
            # Create a stub BackupChannel for the thread
            channel = BackupChannel({
                "id": str(channel_id),
                "name": info["name"],
                "type": "thread"
            }, category_id=info["parent_id"], guild=self.guild)
        
        return BackupMessage(
            msg_data,
            author=author,
            guild=self.guild,
            channel=channel,
            backup_root=backup_root,
        )

    async def get_message(self, channel_id: int, message_id: int) -> BackupMessage | None:
        messages = self._load_channel_messages_data(channel_id)
        for m in messages:
            if int(m["messageID"]) == message_id:
                return self._hydrate_message(m, channel_id)
        return None

    async def get_first_message(self, channel_id: int) -> BackupMessage | None:
        messages = self._load_channel_messages_data(channel_id)
        if messages:
            return self._hydrate_message(messages[0], channel_id)
        return None

    async def fetch_message_history(
        self,
        channel_id: int,
        limit: int = None,
        after_id: int = None,
        inclusive: bool = False
    ) -> AsyncGenerator["BackupMessage", None]:
        """Yields BackupMessages from the backup, respecting after_id and limit."""
        messages = self._load_channel_messages_data(channel_id)
        count = 0

        for m in messages:
            msg_id = int(m["messageID"])
            if after_id:
                if inclusive and msg_id < after_id:
                    continue
                if not inclusive and msg_id <= after_id:
                    continue

            yield self._hydrate_message(m, channel_id)
            count += 1

            if limit and count >= limit:
                return

    # ── download helpers ─────────────────────────────────────────────────

    async def download_emoji(self, emoji: BackupEmoji) -> bytes:
        return await emoji.read()

    async def download_sticker(self, sticker: BackupSticker) -> bytes:
        return await sticker.read()

    async def download_attachment(self, attachment: BackupAttachment) -> bytes:
        return await attachment.read()

    # ── lifecycle ────────────────────────────────────────────────────────

    async def close(self):
        """No-op for backup reader (no connections to close)."""
        pass
