"""
BackupReader — discord.py-compatible local data provider.

Reads from local backup JSON files (produced by DiscordExporter) instead of the
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
# Mock colour / permissions helpers
# ---------------------------------------------------------------------------

class MockColor:
    """Minimal stand-in for discord.Color."""

    __slots__ = ("value",)

    def __init__(self, value: int = 0):
        self.value = value

    def __str__(self) -> str:
        return f"#{self.value:06x}"

    def __repr__(self) -> str:
        return f"MockColor(value={self.value})"

    def __eq__(self, other):
        if isinstance(other, (MockColor, int)):
            return self.value == (other.value if isinstance(other, MockColor) else other)
        return NotImplemented

    @classmethod
    def from_hex(cls, hex_str: str) -> "MockColor":
        """Parse '#rrggbb' or '0x...' to MockColor."""
        hex_str = hex_str.lstrip("#")
        try:
            return cls(int(hex_str, 16))
        except ValueError:
            return cls(0)


class MockPermissions:
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
        return f"MockPermissions(value={self.value})"


class MockPermissionOverwrite:
    """Minimal stand-in for discord.PermissionOverwrite."""
    pass


# ---------------------------------------------------------------------------
# Mock discord.py model objects
# ---------------------------------------------------------------------------

class MockAsset:
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


class MockRole:
    """Minimal stand-in for discord.Role."""

    __slots__ = ("id", "name", "color", "position", "permissions",
                 "hoist", "mentionable", "managed")

    def __init__(self, data: dict):
        self.id = int(data["id"])
        self.name = data["name"]
        self.color = MockColor.from_hex(data.get("color", "#000000"))
        self.position = data.get("position", 0)
        self.permissions = MockPermissions(int(data.get("permissions", 0)))
        self.hoist = data.get("hoist", False)
        self.mentionable = data.get("mentionable", False)
        self.managed = False

    def is_default(self) -> bool:
        return self.name == "@everyone"

    def __repr__(self) -> str:
        return f"MockRole(id={self.id}, name='{self.name}')"


class MockCategory:
    """Minimal stand-in for discord.CategoryChannel."""

    __slots__ = ("id", "name", "position", "type")

    def __init__(self, data: dict):
        try:
            self.id = int(data["id"])
        except (ValueError, TypeError):
            self.id = 0  # 'uncategorized' sentinel
        self.name = data["name"]
        self.position = data.get("position", 0)
        self.type = ChannelType.category

    def __repr__(self) -> str:
        return f"MockCategory(id={self.id}, name='{self.name}')"


class MockChannel:
    """Minimal stand-in for discord.TextChannel / ForumChannel / VoiceChannel."""

    __slots__ = ("id", "name", "type", "position", "topic", "nsfw",
                 "category_id", "available_tags", "parent_id")

    _TYPE_MAP = {
        "text": ChannelType.text,
        "voice": ChannelType.voice,
        "news": ChannelType.news,
        "forum": ChannelType.forum,
        "thread": ChannelType.public_thread,
    }

    def __init__(self, data: dict, category_id: int | None = None):
        self.id = int(data["id"])
        self.name = data["name"]
        self.type = self._TYPE_MAP.get(data.get("type", "text"), ChannelType.text)
        self.position = data.get("position", 0)
        self.topic = data.get("topic")
        self.nsfw = data.get("nsfw", False)
        self.category_id = category_id
        self.parent_id = category_id
        self.available_tags = data.get("available_tags", [])

    def __repr__(self) -> str:
        return f"MockChannel(id={self.id}, name='{self.name}', type={self.type})"


class MockMember:
    """Minimal stand-in for discord.Member / discord.User."""

    __slots__ = ("id", "name", "display_name", "bot", "color",
                 "roles", "avatar", "guild_permissions")

    def __init__(self, data: dict, role_objects: list | None = None,
                 avatar_base: Path | None = None):
        self.id = int(data["userID"])
        self.name = data.get("username", "Unknown")
        self.display_name = data.get("userNickname") or self.name
        self.bot = data.get("userIsBot", False)
        self.color = MockColor.from_hex(data.get("userColor") or "#000000")
        self.roles = role_objects or []
        self.guild_permissions = MockPermissions(0)

        avatar_rel = data.get("userAvatar")
        if avatar_rel and avatar_base:
            self.avatar = MockAsset(avatar_base / avatar_rel)
        else:
            self.avatar = MockAsset(None)

    def __repr__(self) -> str:
        return f"MockMember(id={self.id}, name='{self.name}')"


class MockAttachment:
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
        return f"MockAttachment(id={self.id}, filename='{self.filename}')"


class MockEmoji:
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
        return f"MockEmoji(id={self.id}, name='{self.name}')"


class MockSticker:
    """Minimal stand-in for discord.GuildSticker."""

    __slots__ = ("id", "name", "url", "format", "_file_path")

    def __init__(self, data: dict, media_dir: Path | None = None):
        self.id = int(data["id"])
        self.name = data["name"]
        filename = data.get("filename", "")
        self._file_path = media_dir / filename if media_dir and filename else None
        self.url = str(self._file_path) if self._file_path else None
        self.format = data.get("format", "png")

    async def read(self) -> bytes:
        if self._file_path and self._file_path.exists():
            return self._file_path.read_bytes()
        return b""

    def __repr__(self) -> str:
        return f"MockSticker(id={self.id}, name='{self.name}')"


class MockPartialEmoji:
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


class MockReaction:
    """Minimal stand-in for discord.Reaction."""

    __slots__ = ("emoji", "count")

    def __init__(self, data: dict):
        emoji_raw = data.get("emoji", "")
        self.count = data.get("count", 0)

        if ":" in emoji_raw and not emoji_raw.startswith("<"):
            parts = emoji_raw.split(":", 1)
            try:
                self.emoji = MockPartialEmoji(name=parts[0], id=int(parts[1]))
            except (ValueError, IndexError):
                self.emoji = MockPartialEmoji(name=emoji_raw)
        else:
            self.emoji = MockPartialEmoji(name=emoji_raw)

    def is_custom_emoji(self) -> bool:
        return self.emoji.id is not None


class MockMessageReference:
    """Minimal stand-in for discord.MessageReference."""

    __slots__ = ("message_id", "channel_id")

    def __init__(self, data: dict):
        self.message_id = int(data["messageId"])
        self.channel_id = int(data["channelId"])


class MockThread:
    """Minimal stand-in for discord.Thread metadata attached to a starter message."""

    __slots__ = ("id", "name", "message_count", "archived",
                 "auto_archive_duration", "locked", "parent_id")

    def __init__(self, data: dict, parent_id: int | None = None):
        self.id = int(data["id"])
        self.name = data.get("name", "")
        self.message_count = data.get("messageCount", 0)
        self.archived = data.get("archived", False)
        self.auto_archive_duration = data.get("archiveDuration", 1440)
        self.locked = data.get("locked", False)
        self.parent_id = parent_id


class MockMessage:
    """Minimal stand-in for discord.Message."""

    _TYPE_MAP = {
        "Default": MessageType.default,
        "Reply": MessageType.reply,
        "ThreadStarter": MessageType.thread_starter_message,
        "Thread_starter_message": MessageType.thread_starter_message,
        "Forward": MessageType.default,
    }

    __slots__ = ("id", "type", "created_at", "pinned", "content", "author",
                 "attachments", "embeds", "stickers", "reactions",
                 "reference", "thread", "channel_id", "flags")

    def __init__(self, data: dict, *,
                 author: MockMember | None = None,
                 channel_id: int | None = None,
                 backup_root: Path | None = None):
        self.id = int(data["messageID"])
        self.type = self._TYPE_MAP.get(data.get("type", "Default"), MessageType.default)
        self.pinned = data.get("isPinned", False)
        self.content = data.get("content", "")
        self.author = author
        self.channel_id = channel_id

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
            MockAttachment(a, backup_root=backup_root)
            for a in data.get("attachments", [])
        ]

        # Embeds — store raw dicts (discord.py Embed.from_dict compatible)
        self.embeds = data.get("embeds", [])

        # Stickers — store raw dicts
        self.stickers = data.get("stickers", [])

        # Reactions
        self.reactions = [MockReaction(r) for r in data.get("reactions", [])]

        # Reference (replies)
        ref = data.get("reference")
        self.reference = MockMessageReference(ref) if ref else None

        # Thread info
        thread_data = data.get("thread")
        self.thread = MockThread(thread_data, parent_id=channel_id) if thread_data else None

        # Flags placeholder
        self.flags = type("Flags", (), {"forwarded": data.get("type") == "Forward"})()

    def __repr__(self) -> str:
        return f"MockMessage(id={self.id}, author={self.author})"


class MockGuild:
    """Minimal stand-in for discord.Guild."""

    __slots__ = ("id", "name", "icon", "banner")

    def __init__(self, data: dict, backup_path: Path):
        self.id = int(data["id"])
        self.name = data["name"]

        icon_rel = data.get("icon")
        self.icon = MockAsset(backup_path / icon_rel) if icon_rel else MockAsset(None)

        banner_rel = data.get("banner")
        self.banner = MockAsset(backup_path / banner_rel) if banner_rel else MockAsset(None)


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
        return MockPermissionOverwrite()

    def __init__(self, backup_path: str | Path):
        self.backup_path = Path(backup_path)
        self.guild: MockGuild | None = None
        self.role_map: Dict[int, str] = {}

        # Internal caches populated by start()
        self._categories: List[MockCategory] = []
        self._channels: List[MockChannel] = []
        self._roles: List[MockRole] = []
        self._emojis: List[MockEmoji] = []
        self._stickers: List[MockSticker] = []
        self._members: List[MockMember] = []
        self._member_map: Dict[int, MockMember] = {}

    # ── startup ──────────────────────────────────────────────────────────

    async def start(self):
        """Loads all JSON files from the backup directory into memory."""
        bp = self.backup_path

        # 1. Server profile -> MockGuild
        profile_file = bp / "server_profile.json"
        if profile_file.exists():
            profile = json.loads(profile_file.read_text(encoding="utf-8"))
            self.guild = MockGuild(profile, bp)
            logger.info(f"[Backup] Loaded server profile: {self.guild.name} ({self.guild.id})")
        else:
            logger.warning(f"[Backup] server_profile.json not found in {bp}")
            self.guild = None

        # 2. Roles
        roles_file = bp / "server_roles.json"
        if roles_file.exists():
            roles_data = json.loads(roles_file.read_text(encoding="utf-8"))
            self._roles = [MockRole(r) for r in roles_data]
            self.role_map = {r.id: r.name for r in self._roles}
            logger.info(f"[Backup] Loaded {len(self._roles)} roles")

        # 3. Structure -> categories + channels
        struct_file = bp / "server_structure.json"
        if struct_file.exists():
            structure = json.loads(struct_file.read_text(encoding="utf-8"))
            for cat_data in structure:
                cat = MockCategory(cat_data)
                if cat.id != 0:  # skip 'uncategorized' as a real category
                    self._categories.append(cat)

                for ch_data in cat_data.get("channels", []):
                    ch_cat_id = cat.id if cat.id != 0 else None
                    channel = MockChannel(ch_data, category_id=ch_cat_id)
                    self._channels.append(channel)

            logger.info(f"[Backup] Loaded {len(self._categories)} categories, "
                        f"{len(self._channels)} channels")

        # 4. Assets (emojis + stickers)
        assets_file = bp / "server_assets.json"
        media_dir = bp / "server_media"
        if assets_file.exists():
            assets = json.loads(assets_file.read_text(encoding="utf-8"))
            self._emojis = [MockEmoji(e, media_dir) for e in assets.get("emojis", [])]
            self._stickers = [MockSticker(s, media_dir) for s in assets.get("stickers", [])]
            logger.info(f"[Backup] Loaded {len(self._emojis)} emojis, "
                        f"{len(self._stickers)} stickers")

        # 5. Users
        user_info_file = bp / "message_backup" / "user_info.json"
        if user_info_file.exists():
            try:
                users = json.loads(user_info_file.read_text(encoding="utf-8"))
                backup_root = bp / "message_backup"
                for u in users:
                    user_role_ids = {int(r["id"]) for r in u.get("userRoles", [])}
                    role_objs = [r for r in self._roles if r.id in user_role_ids]
                    member = MockMember(u, role_objects=role_objs, avatar_base=backup_root)
                    self._members.append(member)
                    self._member_map[member.id] = member
                logger.info(f"[Backup] Loaded {len(self._members)} users")
            except Exception as e:
                logger.warning(f"[Backup] Failed to load user_info.json: {e}")

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

        profile = bp / "server_profile.json"
        if profile.exists():
            try:
                data = json.loads(profile.read_text(encoding="utf-8"))
                results["token"] = True
                results["server"] = True
                results["bot_name"] = "BackupReader"
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

    async def download_asset(self, asset: MockAsset) -> bytes:
        return await asset.read()

    # ── categories & channels ────────────────────────────────────────────

    async def get_categories(self) -> List[MockCategory]:
        return list(self._categories)

    async def get_channels(self, category_id: int | None = None) -> List[MockChannel]:
        channels = [c for c in self._channels if c.type != ChannelType.category]
        if category_id is not None:
            channels = [c for c in channels if c.category_id == category_id]
        return channels

    async def get_channel(self, channel_id: int) -> MockChannel | None:
        for c in self._channels:
            if c.id == channel_id:
                return c
        return None

    # ── roles, emojis, stickers, members ─────────────────────────────────

    async def get_roles(self) -> List[MockRole]:
        return [r for r in self._roles if not r.is_default()]

    async def get_emojis(self) -> List[MockEmoji]:
        return list(self._emojis)

    async def get_stickers(self) -> List[MockSticker]:
        return list(self._stickers)

    async def get_members(self) -> List[MockMember]:
        return list(self._members)

    # ── messages ─────────────────────────────────────────────────────────

    def _resolve_author(self, user_id_str: str) -> MockMember:
        """Returns MockMember for a userID, creating a stub if missing."""
        uid = int(user_id_str)
        if uid in self._member_map:
            return self._member_map[uid]
        stub = MockMember({
            "userID": user_id_str,
            "username": f"User#{user_id_str[-4:]}",
            "userIsBot": False,
        })
        self._member_map[uid] = stub
        return stub

    def _load_channel_messages(self, channel_id: int) -> list[dict]:
        """Loads the messages array from a channel JSON file."""
        bp = self.backup_path / "message_backup"
        json_file = bp / f"{channel_id}.json"
        if not json_file.exists():
            for candidate in [
                bp / "threads" / f"{channel_id}.json",
                *bp.glob(f"*/{channel_id}.json"),
            ]:
                if candidate.exists():
                    json_file = candidate
                    break
            else:
                return []

        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            return data.get("messages", [])
        except Exception as e:
            logger.error(f"[Backup] Failed to load messages for channel {channel_id}: {e}")
            return []

    def _hydrate_message(self, msg_data: dict, channel_id: int) -> MockMessage:
        author = self._resolve_author(msg_data.get("userID", "0"))
        backup_root = self.backup_path / "message_backup"
        return MockMessage(
            msg_data,
            author=author,
            channel_id=channel_id,
            backup_root=backup_root,
        )

    async def get_message(self, channel_id: int, message_id: int) -> MockMessage | None:
        messages = self._load_channel_messages(channel_id)
        for m in messages:
            if int(m["messageID"]) == message_id:
                return self._hydrate_message(m, channel_id)
        return None

    async def get_first_message(self, channel_id: int) -> MockMessage | None:
        messages = self._load_channel_messages(channel_id)
        if messages:
            return self._hydrate_message(messages[0], channel_id)
        return None

    async def fetch_message_history(
        self,
        channel_id: int,
        limit: int = None,
        after_id: int = None,
    ) -> AsyncGenerator["MockMessage", None]:
        """Yields MockMessages from the backup, respecting after_id and limit."""
        messages = self._load_channel_messages(channel_id)
        count = 0

        for m in messages:
            msg_id = int(m["messageID"])
            if after_id and msg_id <= after_id:
                continue

            yield self._hydrate_message(m, channel_id)
            count += 1

            if limit and count >= limit:
                return

    # ── download helpers ─────────────────────────────────────────────────

    async def download_emoji(self, emoji: MockEmoji) -> bytes:
        return await emoji.read()

    async def download_sticker(self, sticker: MockSticker) -> bytes:
        return await sticker.read()

    async def download_attachment(self, attachment: MockAttachment) -> bytes:
        return await attachment.read()

    # ── lifecycle ────────────────────────────────────────────────────────

    async def close(self):
        """No-op for backup reader (no connections to close)."""
        pass
