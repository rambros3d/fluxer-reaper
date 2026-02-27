import os
import json
import logging
import asyncio
import discord
from pathlib import Path
from typing import Dict, Any, List, Optional, AsyncGenerator

logger = logging.getLogger(__name__)

class DiscordExporter:
    """Core logic for exporting Discord server data."""
    
    def __init__(self, reader):
        self.reader = reader
        self.server_name = ""
        self.server_id = ""

    async def setup(self):
        """Prepares the output directory and fetches server metadata."""
        metadata = await self.reader.get_server_metadata()
        self.server_name = metadata.get("name", "Unknown Server")
        self.server_id = metadata.get("id", "0")
        
        # Create safe folder name
        import re
        safe_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', self.server_name)
        self.export_path = Path(".") / f"EXPORT-{safe_name}-{self.server_id}"
        self.export_path.mkdir(parents=True, exist_ok=True)
        
        # Consolidate media into one folder
        self.media_path = self.export_path / "server_media"
        self.media_path.mkdir(exist_ok=True)
        
        logger.info(f"Export directory set to: {self.export_path}")
        return metadata

    async def export_metadata(self):
        """Saves server metadata to a JSON file."""
        metadata = await self.reader.get_server_metadata()
        output_file = self.export_path / "server_metadata.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=4, ensure_ascii=False)
        return metadata

    async def export_roles(self):
        """Exports all roles to roles.json."""
        roles = await self.reader.get_roles()
        role_data = []
        for r in roles:
            role_data.append({
                "id": str(r.id),
                "name": r.name,
                "color": str(r.color),
                "position": r.position,
                "permissions": r.permissions.value,
                "hoist": r.hoist,
                "mentionable": r.mentionable
            })
        
        output_file = self.export_path / "roles.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(role_data, f, indent=4, ensure_ascii=False)
        return role_data

    async def export_emojis_stickers(self):
        """Exports all emojis and stickers, plus server icon/banner, to server_media folder."""
        emojis = await self.reader.get_emojis()
        stickers = await self.reader.get_stickers()
        metadata = await self.reader.get_server_metadata()
        
        # Download Server Icon
        if metadata.get("icon_url"):
            try:
                # We need a way to download from URL directly or use reader
                # Since DiscordReader doesn't have a direct "download from URL" we might need one
                # but for guild icon/banner we can use guild object if available in reader.
                if self.reader.guild and self.reader.guild.icon:
                    data = await self.reader.download_asset(self.reader.guild.icon)
                    ext = "gif" if self.reader.guild.icon.is_animated() else "png"
                    with open(self.media_path / f"server_icon.{ext}", "wb") as f:
                        f.write(data)
            except Exception as e:
                logger.error(f"Failed to download server icon: {e}")

        # Download Server Banner
        if metadata.get("banner_url"):
            try:
                if self.reader.guild and self.reader.guild.banner:
                    data = await self.reader.download_asset(self.reader.guild.banner)
                    ext = "gif" if self.reader.guild.banner.is_animated() else "png"
                    with open(self.media_path / f"server_banner.{ext}", "wb") as f:
                        f.write(data)
            except Exception as e:
                logger.error(f"Failed to download server banner: {e}")

        emoji_data = []
        for e in emojis:
            ext = "gif" if e.animated else "png"
            filename = f"emoji_{e.name}_{e.id}.{ext}"
            emoji_path = self.media_path / filename
            try:
                data = await self.reader.download_emoji(e)
                with open(emoji_path, "wb") as f:
                    f.write(data)
                emoji_data.append({
                    "id": str(e.id),
                    "name": e.name,
                    "animated": e.animated,
                    "filename": filename
                })
            except Exception as ex:
                logger.error(f"Failed to download emoji {e.name}: {ex}")

        sticker_data = []
        for s in stickers:
            ext = "png"
            if s.url:
                if ".json" in str(s.url): ext = "json"
                elif ".gif" in str(s.url): ext = "gif"
                elif ".webp" in str(s.url): ext = "webp"
            
            filename = f"sticker_{s.name}_{s.id}.{ext}"
            sticker_path = self.media_path / filename
            try:
                data = await self.reader.download_sticker(s)
                with open(sticker_path, "wb") as f:
                    f.write(data)
                sticker_data.append({
                    "id": str(s.id),
                    "name": s.name,
                    "filename": filename
                })
            except Exception as ex:
                logger.error(f"Failed to download sticker {s.name}: {ex}")

        with open(self.export_path / "emojis.json", "w", encoding="utf-8") as f:
            json.dump(emoji_data, f, indent=4, ensure_ascii=False)
        with open(self.export_path / "stickers.json", "w", encoding="utf-8") as f:
            json.dump(sticker_data, f, indent=4, ensure_ascii=False)
            
        return len(emoji_data), len(sticker_data)

    async def export_channels_structure(self):
        """Exports categories and channels hierarchy."""
        categories = await self.reader.get_categories()
        channels = await self.reader.get_channels()
        
        structure = []
        for cat in categories:
            cat_channels = [c for c in channels if c.category_id == cat.id]
            structure.append({
                "type": "category",
                "id": str(cat.id),
                "name": cat.name,
                "position": cat.position,
                "channels": [self._format_channel(c) for c in cat_channels]
            })
            
        # Uncategorized
        uncategorized = [c for c in channels if not c.category_id]
        if uncategorized:
            structure.append({
                "type": "category",
                "id": "uncategorized",
                "name": "Uncategorized",
                "channels": [self._format_channel(c) for c in uncategorized]
            })
            
        output_file = self.export_path / "channels_structure.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(structure, f, indent=4, ensure_ascii=False)
        return structure

    def _format_channel(self, c):
        return {
            "id": str(c.id),
            "name": c.name,
            "type": str(c.type),
            "position": c.position,
            "topic": getattr(c, "topic", None),
            "nsfw": getattr(c, "nsfw", False)
        }

    async def export_channel_messages(self, channel_id: int, progress_callback=None):
        """Exports all messages from a channel, including attachments, pins, reactions."""
        channel = await self.reader.get_channel(channel_id)
        channel_name = channel.name
        
        # Create channel-specific folder
        chan_dir = self.export_path / "channels" / f"{channel_name}_{channel_id}"
        attach_dir = chan_dir / "attachments"
        chan_dir.mkdir(parents=True, exist_ok=True)
        attach_dir.mkdir(exist_ok=True)
        
        messages = []
        count = 0
        
        async for msg in self.reader.fetch_message_history(channel_id):
            msg_data = await self._format_message(msg, attach_dir)
            messages.append(msg_data)
            count += 1
            if progress_callback:
                await progress_callback(channel_name, count)
                
        # Export pins
        pins = []
        if hasattr(channel, "pins"):
            try:
                pins_objects = await channel.pins()
                pins = [str(p.id) for p in pins_objects]
            except Exception as e:
                logger.error(f"Failed to fetch pins for {channel_name}: {e}")

        output_data = {
            "channel_info": self._format_channel(channel),
            "messages": messages,
            "pins": pins
        }
        
        with open(chan_dir / "messages.json", "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)
            
        # Also check for threads
        if hasattr(channel, "threads"):
             # We fetch threads separately if needed, or they might be returned by get_channels if we are not careful
             pass

        return count

    async def _format_message(self, msg, attach_dir):
        attachments = []
        for a in msg.attachments:
            # Using ID to avoid collisions
            filename = f"{a.id}_{a.filename}"
            try:
                # Optimized download can be added later (e.g. queue)
                # data = await self.reader.download_attachment(a)
                # with open(attach_dir / filename, "wb") as f:
                #     f.write(data)
                attachments.append({
                    "id": str(a.id),
                    "filename": a.filename,
                    "url": a.url,
                    "local_path": f"attachments/{filename}"
                })
            except Exception as e:
                logger.error(f"Failed to download attachment {a.filename} from message {msg.id}: {e}")

        reactions = []
        for r in msg.reactions:
            emoji_str = str(r.emoji) if not r.is_custom_emoji() else f"{r.emoji.name}:{r.emoji.id}"
            reactions.append({
                "emoji": emoji_str,
                "count": r.count
            })

        return {
            "id": str(msg.id),
            "author": {
                "id": str(msg.author.id),
                "name": msg.author.name,
                "discriminator": msg.author.discriminator,
                "avatar": str(msg.author.avatar.url) if msg.author.avatar else None,
                "bot": msg.author.bot
            },
            "content": msg.content,
            "timestamp": msg.created_at.isoformat(),
            "edited_timestamp": msg.edited_at.isoformat() if msg.edited_at else None,
            "attachments": attachments,
            "embeds": [e.to_dict() for e in msg.embeds],
            "reactions": reactions,
            "type": str(msg.type),
            "is_pinned": msg.pinned
        }

    async def export_threads(self, channel_id: int):
        """Exports active and archived threads for a channel."""
        channel = await self.reader.get_channel(channel_id)
        if not hasattr(channel, "threads") and not hasattr(channel, "archived_threads"):
            return 0
        
        all_threads = []
        try:
            # Active threads
            if hasattr(channel, "threads"):
                all_threads.extend(channel.threads)
            
            # Archived threads (can be private or public)
            if hasattr(channel, "archived_threads"):
                async for thread in channel.archived_threads(limit=None):
                    all_threads.append(thread)
        except Exception as e:
            logger.error(f"Failed to fetch threads for {channel.name}: {e}")

        thread_count = 0
        for thread in all_threads:
            await self.export_channel_messages(thread.id)
            thread_count += 1
            
        return thread_count
