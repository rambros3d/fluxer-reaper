"""
BackupReader — discord.py-compatible local data provider.

Reads from local backup JSON files (produced by Reaper) instead of the
Discord API.  Implements the same public interface as DiscordReader so that
migration scripts and UI code can use either provider transparently.
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, List, Optional, Union
from src.core.backup_database import BackupDatabase, parse_snowflake

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lightweight enum clones (mirror discord.py values for compatibility)
# ---------------------------------------------------------------------------

class ChannelType(IntEnum):
    text = 0
    private = 1
    voice = 2
    group = 3
    category = 4
    news = 5
    news_thread = 10
    public_thread = 11
    private_thread = 12
    stage_voice = 13
    directory = 14
    forum = 15
    media = 16


class StickerFormatType(IntEnum):
    png = 1
    apng = 2
    lottie = 3
    gif = 4

class MessageType(IntEnum):
    default = 0
    recipient_add = 1
    recipient_remove = 2
    call = 3
    channel_name_change = 4
    channel_icon_change = 5
    channel_pinned_message = 6
    user_join = 7
    guild_boost = 8
    guild_boost_tier_1 = 9
    guild_boost_tier_2 = 10
    guild_boost_tier_3 = 11
    channel_follow_add = 12
    guild_discovery_disqualified = 14
    guild_discovery_requalified = 15
    guild_discovery_grace_period_initial_warning = 16
    guild_discovery_grace_period_final_warning = 17
    thread_created = 18
    reply = 19
    chat_input_command = 20
    thread_starter_message = 21
    guild_invite_reminder = 22
    context_menu_command = 23
    auto_moderation_action = 24
    role_subscription_purchase = 25
    interaction_premium_upsell = 26
    stage_start = 27
    stage_end = 28
    stage_speaker = 29
    stage_topic = 31
    guild_application_premium_subscription = 32
    guild_incident_alert_mode_enabled = 36
    guild_incident_alert_mode_disabled = 37
    guild_incident_report_raid = 38
    guild_incident_report_false_alarm = 39
    purchase_notification = 44
    poll_result = 46
    forward = 100


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
        if not isinstance(data, dict):
            self.id = 0
            self.name = "Unknown"
            return
        self.id = parse_snowflake(data["id"])
        self.name = data["name"]
        self.color = BackupColor(parse_snowflake(data.get("color", 0)) or 0)
        self.position = data.get("position", 0)
        self.permissions = BackupPermissions(parse_snowflake(data.get("permissions", 0)) or 0)
        self.hoist = bool(data.get("hoist", False))
        self.managed = False
        self.mentionable = bool(data.get("mentionable", True))

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


def _parse_overwrites(raw_list: list | Any) -> dict:
    """Parse overwrites JSON list into {BackupOverwriteTarget: BackupPermissionOverwrite} dict."""
    result = {}
    if not isinstance(raw_list, list):
        return result
    for entry in raw_list:
        if not isinstance(entry, dict):
            continue
        try:
            target = BackupOverwriteTarget(parse_snowflake(entry["id"]))
            ow = BackupPermissionOverwrite(allow=parse_snowflake(entry.get("allow", 0)) or 0,
                                            deny=parse_snowflake(entry.get("deny", 0)) or 0)
            result[target] = ow
        except (KeyError, ValueError, TypeError):
            continue
    return result


class BackupCategory:
    """Minimal stand-in for discord.CategoryChannel."""

    __slots__ = ("id", "name", "position", "type", "overwrites")

    def __init__(self, data: dict):
        try:
            self.id = parse_snowflake(data["id"])
        except (ValueError, TypeError):
            self.id = 0  # 'uncategorized' sentinel
        self.name = data["name"]
        self.position = data.get("position", 0)
        self.type = ChannelType.category
        
        # Overwrites are now passed as a list of dicts from get_all_channels
        raw_ow = data.get("overwrites", [])
        self.overwrites = _parse_overwrites(raw_ow)

    def __repr__(self) -> str:
        return f"BackupCategory(id={self.id}, name='{self.name}')"


class BackupChannel:
    """Minimal stand-in for discord.TextChannel / ForumChannel / VoiceChannel."""

    __slots__ = ("id", "name", "type", "position", "topic", "nsfw",
                 "category_id", "available_tags", "parent_id", "guild", "overwrites",
                 "bitrate", "slowmode_delay")

    _TYPE_MAP = {
        "text": ChannelType.text,
        "voice": ChannelType.voice,
        "news": ChannelType.news,
        "forum": ChannelType.forum,
        "thread": ChannelType.public_thread,
    }
    def __init__(self, data: dict, category_id: int | None = None, guild: "BackupGuild|None" = None):
        self.id = parse_snowflake(data["id"])
        self.name = data["name"]
        try:
            self.type = ChannelType(parse_snowflake(data.get("type", 0)) or 0)
        except ValueError:
            self.type = ChannelType.text
        self.position = data.get("position", 0)
        self.topic = data.get("topic")
        self.nsfw = bool(data.get("nsfw", False))
        self.bitrate = data.get("bitrate")
        self.slowmode_delay = data.get("slowmode_delay")
        cid = data.get("category_id")
        self.category_id = parse_snowflake(cid) if cid else category_id
        self.parent_id = self.category_id
        self.guild = guild
        
        # Overwrites are now passed as a list of dicts from get_all_channels
        raw_ow = data.get("overwrites", [])
        self.overwrites = _parse_overwrites(raw_ow)
        
        # Forum tags are now structural
        self.available_tags = []
        raw_tags = data.get("available_tags", [])
        for t_data in raw_tags:
            self.available_tags.append(BackupTag(t_data))

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
                 backup_path: Path | None = None):
        if not isinstance(data, dict):
            # Fallback for unexpected data format
            self.id = 0
            self.name = "Unknown"
            return
        self.id = parse_snowflake(data["id"])
        self.name = data.get("username", "Unknown")
        self.display_name = data.get("display_name") or self.name
        self.global_name = self.display_name
        self.bot = False 
        self.system = False
        self.discriminator = "0000"
        self.color = BackupColor(0)
        self.roles = sorted(role_objects or [], key=lambda r: r.position, reverse=True)
        self.guild_permissions = BackupPermissions(0)
        
        self.created_at = datetime.now(timezone.utc)
        self.joined_at = datetime.now(timezone.utc)
        self.status = type("Status", (), {"value": "offline"})()
        self.activity = None

        self._avatar_url = data.get("avatar_url")

        avatar_rel = data.get("avatar_file")
        if avatar_rel and backup_path:
            self.avatar = BackupAsset(backup_path / avatar_rel)
        else:
            self.avatar = BackupAsset(None)
        
        if self.avatar:
            self.avatar.url = self._avatar_url

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

    __slots__ = ("id", "filename", "size", "url", "proxy_url", "_backup_root", "local_hash", "content_type")

    def __init__(self, data: dict, backup_root: Path | None = None, media_pool: dict | None = None):
        if not isinstance(data, dict):
            self.id = 0
            self.filename = "unknown"
            return
        self.id = parse_snowflake(data["id"])
        self.filename = data.get("filename", "unknown")
        self.size = data.get("size", 0)
        self.url = data.get("url", "")
        self.proxy_url = self.url
        self._backup_root = backup_root
        self.local_hash = data.get("local_hash")
        self.content_type = data.get("content_type")
        
        # Resolve local path and ensure content_type via media pool if possible
        if media_pool and self.local_hash in media_pool:
            pool_entry = media_pool[self.local_hash]
            self.url = pool_entry["local_path"]
            if not self.content_type:
                self.content_type = pool_entry.get("content_type")
        elif self.local_hash:
            # Fallback conjecture if pool didn't have it (e.g. ad-hoc load)
            pass

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
        if not isinstance(data, dict):
            self.id = 0
            self.name = "Unknown"
            return
        self.id = parse_snowflake(data["id"])
        self.name = data["name"]
        self.animated = data.get("content_type") == "image/gif"
        filename = data.get("filename", "")
        self._file_path = media_dir / filename if media_dir and filename else None
        # Use the local path if available, else original URL
        self.url = str(self._file_path) if self._file_path else data.get("url", "")

    async def read(self) -> bytes:
        if self._file_path and self._file_path.exists():
            return self._file_path.read_bytes()
        return b""

    def __repr__(self) -> str:
        return f"BackupEmoji(id={self.id}, name='{self.name}')"


class BackupSticker:
    """Minimal stand-in for discord.GuildSticker."""

    __slots__ = ("id", "name", "url", "format", "_backup_root", "_file_path")

    def __init__(self, data: dict, backup_root: Path | None = None, media_pool: dict | None = None):
        if not isinstance(data, dict):
            self.id = 0
            self.name = "Sticker"
            return
        self.id = parse_snowflake(data.get("id") or data.get("sticker_id", 0)) or 0
        self.name = data.get("name", "Sticker")
        
        # Determine format
        fmt = data.get("format_type", 1) 
        try:
            self.format = StickerFormatType(fmt)
        except ValueError:
            self.format = StickerFormatType.png

        self._backup_root = backup_root
        
        # 1. Check if it's a CAS-based sticker (from message_stickers table)
        local_hash = data.get("local_hash")
        if local_hash and backup_root:
            ext = ".png"
            if self.format == StickerFormatType.lottie: ext = ".json"
            elif self.format == StickerFormatType.apng: ext = ".png"
            elif self.format == StickerFormatType.gif: ext = ".gif"
            
            self._file_path = backup_root / "attachments" / f"{local_hash}{ext}"
        # 2. Check if it's a server asset sticker (legacy or manual save)
        elif data.get("filename") and backup_root:
            self._file_path = backup_root / "server_assets" / data["filename"]
        else:
            self._file_path = None

        self.url = str(self._file_path) if self._file_path and self._file_path.exists() else data.get("url", "")

    async def read(self) -> bytes:
        if self._file_path and self._file_path.exists():
            return self._file_path.read_bytes()
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
                self.emoji = BackupPartialEmoji(name=parts[0], id=parse_snowflake(parts[1]))
            except (ValueError, IndexError):
                self.emoji = BackupPartialEmoji(name=emoji_raw)
        else:
            self.emoji = BackupPartialEmoji(name=emoji_raw)

    def is_custom_emoji(self) -> bool:
        return self.emoji.id is not None


class BackupTag:
    """Minimal stand-in for discord.ForumTag."""
    __slots__ = ("id", "name", "moderated", "emoji")
    def __init__(self, data: dict):
        self.id = parse_snowflake(data["id"])
        self.name = data["name"]
        self.moderated = bool(data.get("moderated", False))
        emoji_id = data.get("emoji_id")
        emoji_name = data.get("emoji_name")
        self.emoji = BackupPartialEmoji(name=emoji_name, id=parse_snowflake(emoji_id)) if emoji_name else None

    def __repr__(self) -> str:
        return f"BackupTag(id={self.id}, name='{self.name}')"




class BackupThread:
    """Minimal stand-in for discord.Thread metadata attached to a starter message."""

    __slots__ = ("id", "name", "message_count", "archived",
                 "auto_archive_duration", "locked", "parent_id", "applied_tags", "type")

    def __init__(self, data: dict, parent_id: int | None = None):
        if not isinstance(data, dict):
            self.id = 0
            self.name = ""
            return
        self.id = parse_snowflake(data["id"])
        self.name = data.get("name", "")
        try:
            if data.get("type") is not None:
                self.type = ChannelType(parse_snowflake(data.get("type", 11)) or 11)
            else:
                self.type = ChannelType.public_thread
        except ValueError:
            self.type = ChannelType.public_thread
        self.message_count = data.get("message_count", 0)
        self.archived = data.get("archived", False)
        self.auto_archive_duration = data.get("auto_archive_duration", 1440)
        self.locked = data.get("locked", False)
        pid = data.get("parent_id")
        self.parent_id = parse_snowflake(pid) if pid else parent_id
        
        # Parse applied tags (JSON IDs)
        self.applied_tags = []
        raw_tags = data.get("applied_tags")
        if raw_tags:
            try:
                tag_ids = json.loads(raw_tags) if isinstance(raw_tags, str) else raw_tags
                self.applied_tags = [parse_snowflake(tid) for tid in tag_ids if parse_snowflake(tid)]
            except Exception: pass


class BackupMessage:
    """Minimal stand-in for discord.Message."""

    _TYPE_MAP = {
        "Default": MessageType.default,
        "Reply": MessageType.reply,
        "ThreadStarter": MessageType.thread_starter_message,
        "Forward": 100,
    }

    __slots__ = ("id", "type", "created_at", "pinned", "content", "author",
                 "attachments", "embeds", "stickers", "reactions",
                 "reference", "thread", "channel_id", "flags", "guild", "channel",
                 "mentions", "role_mentions", "channel_mentions", "mention_everyone",
                 "tts", "nonce", "webhook_id", "application_id", "activity",
                 "application", "interaction", "components", "jump_url", "message_snapshots")
    def __init__(self, data: dict, *,
                 author: BackupMember | None = None,
                 guild: "BackupGuild | None" = None,
                 channel: Optional[Any] = None,
                 backup_root: Path | None = None,
                 media_pool: dict | None = None):
        self.id = parse_snowflake(data["id"])
        try:
            if data.get("type") is not None:
                self.type = MessageType(parse_snowflake(data.get("type", 0)) or 0)
            else:
                self.type = MessageType.default
        except ValueError:
            self.type = MessageType.default
        self.pinned = bool(data.get("is_pinned", False))
        self.content = data.get("content", "")
        self.author = author
        self.guild = guild
        self.channel = channel
        cid = data.get("channel_id")
        self.channel_id = parse_snowflake(cid) if cid else (channel.id if channel else None)
 
        # Mentions (resolved on the fly by clean_mentions via guild.get_member)
        self.mentions = []
        self.role_mentions = []
        self.channel_mentions = []
        self.mention_everyone = False
 
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
                self.created_at = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                self.created_at = datetime.now(timezone.utc)
        else:
            self.created_at = datetime.now(timezone.utc)
 
        # Attachments
        self.attachments = []
        raw_atts = data.get("attachments", [])
        if isinstance(raw_atts, str):
            try:
                raw_atts = json.loads(raw_atts)
            except Exception:
                raw_atts = []
        
        for a in raw_atts:
            if isinstance(a, dict):
                self.attachments.append(BackupAttachment(a, backup_root=backup_root, media_pool=media_pool))
 
        # Embeds
        self.embeds = []
        raw_embeds = data.get("embeds", [])
        if isinstance(raw_embeds, str):
            try:
                raw_embeds = json.loads(raw_embeds)
            except Exception:
                raw_embeds = []
        
        for e in raw_embeds:
            if isinstance(e, dict):
                self.embeds.append(BackupEmbed(e))
        
        # Stickers
        self.stickers = []
        raw_stickers = data.get("stickers", [])
        if isinstance(raw_stickers, str):
            try:
                raw_stickers = json.loads(raw_stickers)
            except Exception:
                raw_stickers = []
        for s in raw_stickers:
            if isinstance(s, dict):
                self.stickers.append(BackupSticker(s, backup_root=backup_root, media_pool=media_pool))

        # Reactions
        self.reactions = []
        raw_reactions = data.get("reactions", [])
        if isinstance(raw_reactions, str):
            try:
                self.reactions = json.loads(raw_reactions)
            except Exception:
                self.reactions = []
        elif isinstance(raw_reactions, list):
            self.reactions = raw_reactions
  
        # Reference (replies/forwards)
        self.reference = None
        if data.get("message_reference"):
            self.reference = BackupMessageReference(
                message_id=parse_snowflake(data["message_reference"]),
                channel_id=self.channel_id
            )

        self.thread = None
        self.flags = BackupMessageFlags(forwarded=self.type == MessageType.forward)
        self.message_snapshots = []

    def __repr__(self) -> str:
        return f"BackupMessage(id={self.id}, author={self.author})"

class BackupMessageReference:
    __slots__ = ("message_id", "channel_id")
    def __init__(self, message_id: int | None = None, channel_id: int | None = None):
        self.message_id = message_id
        self.channel_id = channel_id

class BackupMessageFlags:
    __slots__ = ("value", "forwarded")
    def __init__(self, value: int = 0, forwarded: bool = False):
        self.value = value
        self.forwarded = forwarded


class BackupEmbed:
    """Minimal stand-in for discord.Embed."""
    __slots__ = ("title", "description", "url", "color", "timestamp",
                 "thumbnail", "image", "author", "footer", "fields")

    def __init__(self, data: dict):
        self.title = data.get("title")
        self.description = data.get("description")
        self.url = data.get("url")
        self.color = data.get("color")
        self.timestamp = data.get("timestamp")
        
        self.thumbnail = BackupEmbedThumbnail(data["thumbnail"]["url"]) if data.get("thumbnail") and "url" in data["thumbnail"] else None
        self.image = BackupEmbedImage(data["image"]["url"]) if data.get("image") and "url" in data["image"] else None
        
        author = data.get("author")
        self.author = BackupEmbedAuthor(
            name=author.get("name"),
            url=author.get("url"),
            icon_url=author.get("icon_url")
        ) if author else None
        
        footer = data.get("footer")
        self.footer = BackupEmbedFooter(
            text=footer.get("text"),
            icon_url=footer.get("icon_url")
        ) if footer else None
        
        self.fields = [BackupEmbedField(f) for f in data.get("fields", [])]

    def to_dict(self) -> dict:
        result = {
            "type": "rich" # Default for bot-sent embeds
        }
        if self.title: result["title"] = self.title
        if self.description: result["description"] = self.description
        if self.url: result["url"] = self.url
        if self.color: result["color"] = self.color
        if self.timestamp: result["timestamp"] = self.timestamp
        if self.thumbnail: result["thumbnail"] = {"url": self.thumbnail.url}
        if self.image: result["image"] = {"url": self.image.url}
        if self.author:
            result["author"] = {
                "name": self.author.name,
                "url": self.author.url,
                "icon_url": self.author.icon_url
            }
        if self.footer:
            result["footer"] = {
                "text": self.footer.text,
                "icon_url": self.footer.icon_url
            }
        if self.fields:
            result["fields"] = [{"name": f.name, "value": f.value, "inline": f.inline} for f in self.fields]
        return result

class BackupEmbedThumbnail:
    __slots__ = ("url",)
    def __init__(self, url: str): self.url = url

class BackupEmbedImage:
    __slots__ = ("url",)
    def __init__(self, url: str): self.url = url

class BackupEmbedAuthor:
    __slots__ = ("name", "url", "icon_url")
    def __init__(self, name: str | None = None, url: str | None = None, icon_url: str | None = None):
        self.name = name
        self.url = url
        self.icon_url = icon_url

class BackupEmbedFooter:
    __slots__ = ("text", "icon_url")
    def __init__(self, text: str | None = None, icon_url: str | None = None):
        self.text = text
        self.icon_url = icon_url

class BackupEmbedField:
    """Minimal stand-in for embed fields."""
    __slots__ = ("name", "value", "inline")
    def __init__(self, data: dict):
        self.name = data.get("name")
        self.value = data.get("value")
        self.inline = bool(data.get("inline", False))


class BackupGuild:
    """Minimal stand-in for discord.Guild."""

    __slots__ = ("id", "name", "icon", "banner", "_reader")

    def __init__(self, data: dict, backup_path: Path, reader: "BackupReader" = None):
        self.id = parse_snowflake(data["id"])
        self.name = data["name"]
        self._reader = reader

        icon_file = data.get("icon_file")
        self.icon = BackupAsset(backup_path / icon_file) if icon_file else BackupAsset(None)
        if self.icon:
            self.icon.url = data.get("icon_url")

        banner_file = data.get("banner_file")
        self.banner = BackupAsset(backup_path / banner_file) if banner_file else BackupAsset(None)
        if self.banner:
            self.banner.url = data.get("banner_url")

    @property
    def active_threads(self) -> List[BackupThread]:
        """Returns all threads that are not archived."""
        if self._reader:
            return [t for t in self._reader._threads if not t.archived]
        return []

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
            uid = parse_snowflake(user_id)
            if uid in self._reader._member_map:
                return self._reader._member_map[uid]
            # Lazy load from DB
            return self._reader._resolve_author(uid)
        return None

    def get_role(self, role_id: int) -> "BackupRole | None":
        if self._reader:
            return next((r for r in self._reader._roles if r.id == parse_snowflake(role_id)), None)
        return None

    def get_channel(self, channel_id: int) -> "BackupChannel | None":
        if self._reader:
            return next((c for c in self._reader._channels if c.id == parse_snowflake(channel_id)), None)
        return None

    def get_thread(self, thread_id: int) -> "BackupThread | None":
        """Mock discord.Guild.get_thread."""
        if self._reader:
            return next((t for t in self._reader._threads if t.id == parse_snowflake(thread_id)), None)
        return None

    async def fetch_channels(self) -> List[Union["BackupChannel", "BackupCategory"]]:
        """Async stub for discord.Guild.fetch_channels."""
        if self._reader:
            self._reader._ensure_structure_loaded()
            return list(self._reader._channels) + list(self._reader._categories)
        return []

    async def fetch_active_threads(self) -> List["BackupThread"]:
        """Async stub for discord.Guild.active_threads helper (mirrors the property)."""
        return self.active_threads

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
    MESSAGE_TYPE_FORWARD = MessageType.forward   # Custom Reaper constant
    MESSAGE_TYPE_CHAT_INPUT_COMMAND = MessageType.chat_input_command
    MESSAGE_TYPE_CONTEXT_MENU_COMMAND = MessageType.context_menu_command
    MESSAGE_TYPE_POLL_RESULT = MessageType.poll_result
    MESSAGE_TYPE_AUTO_MODERATION_ACTION = MessageType.auto_moderation_action
    Forbidden = BackupForbidden

    CHANNEL_TYPE_TEXT = ChannelType.text
    CHANNEL_TYPE_VOICE = ChannelType.voice
    CHANNEL_TYPE_NEWS = ChannelType.news
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
        self.db: Optional[BackupDatabase] = None

        # Cache for performance
        self._media_pool: Dict[str, Dict[str, Any]] = {}

        # Lazy loading flags
        self._roles_loaded = False
        self._structure_loaded = False
        self._assets_loaded = False
        self._members_loaded = False
        
        # Internal storage
        self._categories: List[BackupCategory] = []
        self._channels: List[BackupChannel] = []
        self._threads: List[BackupThread] = []
        self._thread_map: Dict[int, BackupThread] = {} # Starter Message ID -> Thread
        self._roles: List[BackupRole] = []
        self._emojis: List[BackupEmoji] = []
        self._stickers: List[BackupSticker] = []
        self._members: List[BackupMember] = []
        self._member_map: Dict[int, BackupMember] = {}

    # ── startup ──────────────────────────────────────────────────────────

    async def start(self):
        """Initializes the backup path and the SQLite database."""
        bp = self.backup_path
        db_path = bp / "backup.db"
        
        if db_path.exists():
            self.db = BackupDatabase(db_path)
            profile = self.db.get_guild_profile()
            if profile:
                self.guild = BackupGuild(profile, bp, reader=self)
                logger.info(f"[Backup] Initialized database: {self.guild.name} ({self.guild.id})")
        else:
            logger.warning(f"[Backup] backup.db not found in {bp}")
            self.guild = None

    @property
    def roles(self) -> List[BackupRole]:
        if not self._roles_loaded and self.db:
            logger.info(f"[Backup] Loading roles from DB...")
            rows = self.db.get_all_roles()
            self._roles = [BackupRole(r) for r in rows]
            self._roles_loaded = True
        return self._roles

    @property
    def categories(self) -> List[BackupCategory]:
        self._ensure_structure_loaded()
        return self._categories

    @property
    def threads(self) -> List[BackupThread]:
        self._ensure_structure_loaded()
        return self._threads

    @property
    def channels(self) -> List[BackupChannel]:
        self._ensure_structure_loaded()
        return self._channels

    def _ensure_structure_loaded(self):
        if self._structure_loaded or not self.db:
            return
        
        logger.info(f"[Backup] Loading server structure from DB...")
        rows = self.db.get_all_channels()
        
        for data in rows:
            if data["type"] == 4: # Category
                self._categories.append(BackupCategory(data))
            else:
                self._channels.append(BackupChannel(data, guild=self.guild))
        
        # Load threads
        thread_rows = self.db.get_all_threads()
        for tdata in thread_rows:
            thread = BackupThread(tdata)
            self._threads.append(thread)
            if thread.id:
                self._thread_map[thread.id] = thread
            
            # Resolve tag IDs to BackupTag objects using parent forum's available_tags
            if thread.applied_tags and thread.parent_id:
                parent = next((c for c in self._channels if c.id == thread.parent_id), None)
                if parent and hasattr(parent, "available_tags"):
                    resolved = []
                    for tid in thread.applied_tags:
                        # tid is int because of BackupThread.__init__
                        tag = next((tag for tag in parent.available_tags if tag.id == tid), None)
                        if tag:
                            resolved.append(tag)
                    thread.applied_tags = resolved

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
        if self._assets_loaded or not self.db:
            return
        
        logger.info(f"[Backup] Loading assets from DB...")
        media_dir = self.backup_path / "server_assets"
        
        emoji_rows = self.db.get_server_assets("emoji")
        self._emojis = [BackupEmoji(e, media_dir) for e in emoji_rows if isinstance(e, dict)]
        
        sticker_rows = self.db.get_server_assets("sticker")
        self._stickers = [BackupSticker(s, self.backup_path) for s in sticker_rows if isinstance(s, dict)]
        
        self._assets_loaded = True

    @property
    def members(self) -> List[BackupMember]:
        self._ensure_members_loaded()
        return self._members

    def _ensure_members_loaded(self):
        if self._members_loaded or not self.db:
            return
        
        logger.info(f"[Backup] Loading members from DB...")
        rows = self.db.get_all_users()
        
        bp = self.backup_path
        for u in rows:
            if u.get("roles") and isinstance(u["roles"], str):
                try:
                    u["roles"] = json.loads(u["roles"])
                except Exception:
                    u["roles"] = []
            
            user_role_ids = set()
            for rid in (u.get("roles") or []):
                try:
                    rid_parsed = parse_snowflake(rid)
                    if rid_parsed:
                        user_role_ids.add(rid_parsed)
                except (ValueError, TypeError):
                    continue
            role_objs = [r for r in self.roles if r.id in user_role_ids]
            member = BackupMember(u, role_objects=role_objs, backup_path=bp)
            self._members.append(member)
            self._member_map[member.id] = member
        self._members_loaded = True

    # ── validation ───────────────────────────────────────────────────────

    async def validate(self) -> Dict[str, Any]:
        """Validates backup database integrity."""
        results = {
            "token": False,
            "server": False,
            "bot_name": None,
            "server_name": None,
            "intents": {
                "message_content": True,
                "members": True
            },
            "permissions": {
                "view_channel": True, 
                "read_messages": True,
                "read_message_history": True
            },
            "error": None
        }

        bp = self.backup_path
        db_path = bp / "backup.db"
        
        if not bp.exists():
            results["error"] = f"Backup folder not found: {bp}"
            return results
        
        if not db_path.exists():
            results["error"] = f"backup.db not found in {bp}"
            return results
            
        try:
            # Use sub-connection to validate
            from src.core.backup_database import BackupDatabase
            db = BackupDatabase(db_path)
            profile = db.get_guild_profile()
            if profile:
                results["token"] = True
                results["server"] = True
                results["bot_name"] = "LOCAL BACKUP"
                results["server_name"] = profile.get("name", "Unknown")
            else:
                results["error"] = "Backup profile (guild_profile table) is empty."
        except Exception as e:
            results["error"] = f"Database error: {str(e)}"

        return results

    # ── server metadata ──────────────────────────────────────────────────

    async def get_server_metadata(self) -> Dict[str, Any]:
        if not self.guild:
            return {}
        return {
            "name": self.guild.name,
            "id": str(self.guild.id),
            "icon_url": str(self.guild.icon.url) if self.guild.icon else None,
            "banner_url": str(self.guild.banner.url) if self.guild.banner else None,
        }

    async def download_asset(self, asset: BackupAsset) -> bytes:
        return await asset.read()

    # ── categories & channels ────────────────────────────────────────────

    async def get_categories(self) -> List[BackupCategory]:
        return list(self.categories)

    async def get_channels(self, category_id: int | None = None) -> List[BackupChannel]:
        channels = [c for c in self.channels if c.type != ChannelType.category]
        if category_id is not None:
            channels = [c for c in channels if c.category_id == category_id]
        return channels

    async def fetch_channels(self) -> List[Union[BackupChannel, BackupCategory]]:
        """Uniform interface for all channels (including categories)."""
        return list(self.channels) + list(self.categories)

    async def get_active_threads(self) -> List[BackupThread]:
        """Uniform interface for active threads."""
        return [t for t in self.threads if not t.archived]

    async def get_backed_up_channel_ids(self) -> List[int]:
        """Returns a list of channel IDs that have messages in the database."""
        if not self.db: return []
        import sqlite3
        conn = sqlite3.connect(self.db.db_path)
        rows = conn.execute("SELECT DISTINCT channel_id FROM messages").fetchall()
        conn.close()
        return [parse_snowflake(r[0]) for r in rows if parse_snowflake(r[0])]

    async def get_channel(self, channel_id: int) -> BackupChannel | BackupThread | None:
        for c in self.channels:
            if c.id == channel_id:
                return c
        for t in self.threads:
            if t.id == channel_id:
                return t
        return None

    # ── roles, emojis, stickers, members ─────────────────────────────────

    async def get_roles(self) -> List[BackupRole]:
        return [r for r in self.roles if not r.is_default()]

    async def get_emojis(self) -> List[BackupEmoji]:
        return list(self.emojis)

    async def get_stickers(self) -> List[BackupSticker]:
        return list(self.stickers)

    async def get_members(self) -> List[BackupMember]:
        return list(self.members)

    # ── messages ─────────────────────────────────────────────────────────

    def _ensure_media_pool_loaded(self):
        if not self._media_pool and self.db:
            self._media_pool = self.db.get_all_media()

    def _resolve_author(self, user_id: int) -> BackupMember:
        """Returns BackupMember for a userID, creating a stub if missing."""
        if user_id in self._member_map:
            return self._member_map[user_id]
        
        # Try to fetch from DB
        user_data = self.db.get_user(str(user_id)) if self.db else None
        if user_data:
            user_role_ids = {parse_snowflake(rid) for rid in (user_data.get("roles") or []) if parse_snowflake(rid)}
            role_objs = [r for r in self.roles if r.id in user_role_ids]
            member = BackupMember(user_data, role_objects=role_objs, backup_path=self.backup_path)
            self._members.append(member)
            self._member_map[user_id] = member
            return member

        # Stub
        stub = BackupMember({
            "id": str(user_id),
            "username": f"User#{str(user_id)[-4:]}",
        })
        self._member_map[user_id] = stub
        return stub

    def _hydrate_message(self, msg_data: dict) -> BackupMessage:
        user_id = parse_snowflake(msg_data.get("author_id", 0)) or 0
        author = self._resolve_author(user_id)
        
        self._ensure_media_pool_loaded()
        
        channel_id = parse_snowflake(msg_data["channel_id"])
        channel = next((c for c in self.channels if c.id == channel_id), None)
        
        bm = BackupMessage(
            msg_data,
            author=author,
            guild=self.guild,
            channel=channel,
            backup_root=self.backup_path,
            media_pool=self._media_pool
        )
        
        # Link thread if this is a starter message
        if bm.id in self._thread_map:
            bm.thread = self._thread_map[bm.id]
        
        return bm

    async def get_message(self, channel_id: int, message_id: int) -> BackupMessage | None:
        """Fetch a specific message from SQLite."""
        if not self.db: return None
        import sqlite3
        conn = sqlite3.connect(self.db.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (str(message_id),)).fetchone()
        if row:
            data = dict(row)
            # Fetch attachments
            atts = conn.execute("SELECT * FROM attachments WHERE message_id = ?", (str(message_id),)).fetchall()
            data["attachments"] = [dict(a) for a in atts]
            
            # Fetch stickers
            sts = conn.execute("SELECT * FROM message_stickers WHERE message_id = ?", (str(message_id),)).fetchall()
            data["stickers"] = [dict(s) for s in sts]
            
            conn.close()
            return self._hydrate_message(data)
        conn.close()
        return None

    async def get_first_message(self, channel_id: int) -> BackupMessage | None:
        """Fetch the first message in a channel from SQLite."""
        if not self.db: return None
        msgs = self.db.get_messages_paged(str(channel_id), limit=1)
        if msgs:
            return self._hydrate_message(msgs[0])
        return None

    async def fetch_message_history(
        self,
        channel_id: int,
        limit: int = None,
        after_id: int = None,
        inclusive: bool = False
    ) -> AsyncGenerator["BackupMessage", None]:
        """Yields BackupMessages from SQLite, respecting after_id and limit."""
        if not self.db: return
        
        offset = 0
        batch_size = 100
        count = 0
        
        while True:
            actual_limit = batch_size
            if limit:
                rem = limit - count
                if rem <= 0: break
                actual_limit = min(batch_size, rem)
            
            msgs = self.db.get_messages_paged(
                str(channel_id), 
                limit=actual_limit, 
                offset=offset, 
                after_id=str(after_id) if after_id else None
            )
            
            if not msgs:
                break
                
            for m in msgs:
                yield self._hydrate_message(m)
                count += 1
                
            offset += len(msgs)
            if len(msgs) < batch_size:
                break

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
