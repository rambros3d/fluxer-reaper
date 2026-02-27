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
        self.user_cache = {}

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
        logger.info(f"Targeting server: {self.server_name} ({self.server_id})")
        return metadata

    async def export_metadata(self):
        """Saves server metadata to a JSON file."""
        metadata = await self.reader.get_server_metadata()
        
        # Add relative paths to local assets
        if self.reader.guild:
            if self.reader.guild.icon:
                ext = "gif" if self.reader.guild.icon.is_animated() else "png"
                metadata["icon"] = f"server_media/server_icon.{ext}"
            else:
                metadata["icon"] = None
                
            if self.reader.guild.banner:
                ext = "gif" if self.reader.guild.banner.is_animated() else "png"
                metadata["banner"] = f"server_media/server_banner.{ext}"
            else:
                metadata["banner"] = None

        output_file = self.export_path / "server_profile.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=4, ensure_ascii=False)
        return metadata

    async def export_roles(self):
        """Exports all roles to server_roles.json."""
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
        
        output_file = self.export_path / "server_roles.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(role_data, f, indent=4, ensure_ascii=False)
        return role_data

    async def download_server_assets(self):
        """Downloads server icon and banner to media folder."""
        metadata = await self.reader.get_server_metadata()
        # Download Server Icon
        if metadata.get("icon_url"):
            try:
                if self.reader.guild and self.reader.guild.icon:
                    logger.info(f"Downloading server icon: {self.reader.guild.icon.url}")
                    data = await self.reader.download_asset(self.reader.guild.icon)
                    ext = "gif" if self.reader.guild.icon.is_animated() else "png"
                    icon_path = self.media_path / f"server_icon.{ext}"
                    with open(icon_path, "wb") as f:
                        f.write(data)
                    logger.info(f"Saved server icon to {icon_path}")
                else:
                    logger.warning("Icon URL found in metadata but guild icon asset is missing.")
            except discord.Forbidden:
                logger.error("403 Forbidden: Missing Access to download server icon.")
            except Exception as e:
                logger.error(f"Failed to download server icon: {e}")
        else:
            logger.info("No server icon found to download.")

        # Download Server Banner
        if metadata.get("banner_url"):
            try:
                if self.reader.guild and self.reader.guild.banner:
                    logger.info(f"Downloading server banner: {self.reader.guild.banner.url}")
                    data = await self.reader.download_asset(self.reader.guild.banner)
                    ext = "gif" if self.reader.guild.banner.is_animated() else "png"
                    banner_path = self.media_path / f"server_banner.{ext}"
                    with open(banner_path, "wb") as f:
                        f.write(data)
                    logger.info(f"Saved server banner to {banner_path}")
            except discord.Forbidden:
                logger.error("403 Forbidden: Missing Access to download server banner.")
            except Exception as e:
                logger.error(f"Failed to download server banner: {e}")
        else:
            logger.info("No server banner found to download.")

    async def export_assets(self):
        """Exports emojis, stickers, and server media to server_assets.json and server_media/."""
        await self.download_server_assets()
        
        emojis = await self.reader.get_emojis()
        stickers = await self.reader.get_stickers()
        
        emoji_data = []
        logger.info(f"Exporting {len(emojis)} emojis...")
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
            except discord.Forbidden:
                logger.error(f"403 Forbidden: Missing Access to download emoji {e.name}")
            except Exception as ex:
                logger.error(f"Failed to download emoji {e.name}: {ex}")

        sticker_data = []
        logger.info(f"Exporting {len(stickers)} stickers...")
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
            except discord.Forbidden:
                logger.error(f"403 Forbidden: Missing Access to download sticker {s.name}")
            except Exception as ex:
                logger.error(f"Failed to download sticker {s.name}: {ex}")

        # Try to load existing customization to merge (if it exists)
        custom_file = self.export_path / "server_assets.json"
        customization = {"emojis": emoji_data, "stickers": sticker_data, "members": []}
        if custom_file.exists():
            try:
                with open(custom_file, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    customization["members"] = old_data.get("members", [])
            except Exception: pass

        with open(custom_file, "w", encoding="utf-8") as f:
            json.dump(customization, f, indent=4, ensure_ascii=False)
            
        return len(emoji_data), len(sticker_data)

    async def export_members(self):
        """Exports server members to server_assets.json."""
        member_data = []
        logger.info("Attempting to export members (requires Server Members Intent)...")
        try:
            members = await self.reader.get_members()
            for m in members:
                member_data.append({
                    "id": str(m.id),
                    "name": m.name,
                    "display_name": m.display_name,
                    "discriminator": m.discriminator,
                    "avatar_url": str(m.avatar.url) if m.avatar else None,
                    "bot": m.bot,
                    "roles": [str(r.id) for r in m.roles if not r.is_default()]
                })
            logger.info(f"Successfully exported {len(member_data)} members.")
        except discord.Forbidden:
            logger.warning("403 Forbidden: Missing Access to fetch members. Skipping members export.")
            return 0
        except Exception as e:
            logger.error(f"Failed to fetch members: {e}")
            return 0

        # Merge with existing assets
        custom_file = self.export_path / "server_assets.json"
        customization = {"emojis": [], "stickers": [], "members": member_data}
        if custom_file.exists():
            try:
                with open(custom_file, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    customization["emojis"] = old_data.get("emojis", [])
                    customization["stickers"] = old_data.get("stickers", [])
            except Exception: pass

        with open(custom_file, "w", encoding="utf-8") as f:
            json.dump(customization, f, indent=4, ensure_ascii=False)
            
        return len(member_data)

    async def export_channels_structure(self):
        """Exports categories and channels hierarchy."""
        categories = await self.reader.get_categories()
        channels = await self.reader.get_channels()
        
        structure = []
        chan_count = 0
        cat_count = len(categories)
        
        for cat in categories:
            cat_channels = [c for c in channels if c.category_id == cat.id]
            formatted_channels = [self._format_channel(c) for c in cat_channels]
            chan_count += len(formatted_channels)
            structure.append({
                "type": "category",
                "id": str(cat.id),
                "name": cat.name,
                "position": cat.position,
                "channels": formatted_channels
            })
            
        # Uncategorized
        uncategorized = [c for c in channels if not c.category_id]
        if uncategorized:
            formatted_uncat = [self._format_channel(c) for c in uncategorized]
            chan_count += len(formatted_uncat)
            structure.append({
                "type": "category",
                "id": "uncategorized",
                "name": "Uncategorized",
                "channels": formatted_uncat
            })
            # No need to increment cat_count for 'Uncategorized' usually, 
            # but let's see if the user wants it. For now, cat_count is real Discord categories.
            
        output_file = self.export_path / "server_structure.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(structure, f, indent=4, ensure_ascii=False)
        return structure, cat_count, chan_count

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
        if not channel: return 0
        
        channel_name = channel.name
        safe_name = channel_name.replace(" ", "-").lower()

        # Detection for thread grouping
        is_thread = isinstance(channel, discord.Thread)
        backup_root = self.export_path / "message_backup"
        
        if is_thread:
            backup_dir = backup_root / "threads"
            avatar_rel_base = "../user_avatars"
        else:
            backup_dir = backup_root
            avatar_rel_base = "user_avatars"

        backup_dir.mkdir(parents=True, exist_ok=True)
        
        # Shared avatars directory (always at root of message_backup)
        avatar_dir = backup_root / "user_avatars"
        avatar_dir.mkdir(exist_ok=True)
        
        # Load existing user_info.json
        user_info_file = backup_root / "user_info.json"
        if not self.user_cache and user_info_file.exists():
            try:
                with open(user_info_file, "r", encoding="utf-8") as f:
                    u_list = json.load(f)
                    self.user_cache = {u["id"]: u for u in u_list}
            except Exception:
                self.user_cache = {}

        base_filename = str(channel_id)
        json_file = backup_dir / f"{base_filename}.json"
        asset_dir = backup_dir / base_filename
        asset_dir.mkdir(exist_ok=True)
        
        messages = []
        last_id = None
        
        # Load existing messages for incremental sync
        if json_file.exists():
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    messages = old_data.get("messages", [])
                    if "lastMessageID" in old_data:
                        last_id = int(old_data["lastMessageID"])
                    elif messages:
                        last_id = int(messages[-1]["messageID"])
            except Exception as e:
                logger.warning(f"Could not load existing backup for sync in {channel_name}: {e}")
                messages = []

        count = len(messages)
        new_count = 0
        thread_count = 0
        thread_msg_count = 0
        
        # 1. Fetch new messages - Handle Forbidden gracefully
        try:
            async for msg in self.reader.fetch_message_history(channel_id, after_id=last_id):
                msg_data = await self._format_message(msg, asset_dir, base_filename, avatar_dir, avatar_rel_base)
                messages.append(msg_data)
                new_count += 1
                if progress_callback:
                    await progress_callback(channel_name, count + new_count)
        except discord.Forbidden:
            logger.error(f"403 Forbidden: Missing Access to read messages in {channel_name} ({channel_id})")
            if not messages: return 0
        except Exception as e:
            logger.error(f"Error fetching messages for {channel_name}: {e}")
            if not messages: return 0

        # 2. Handle Threads and collect counts accurately
        all_threads = []
        try:
            # Active threads: Use active_threads() coroutine for 2.6.4
            if self.reader.guild:
                threads = await self.reader.guild.active_threads()
                all_threads.extend([t for t in threads if t.parent_id == channel_id])
            
            # Archived threads: Use the consolidated archived_threads() iterator
            try:
                if hasattr(channel, "archived_threads"):
                    async for thread in channel.archived_threads(limit=None):
                        all_threads.append(thread)
            except discord.Forbidden:
                logger.warning(f"403 Forbidden: Cannot fetch archived threads in {channel_name}")
            except Exception as e:
                logger.warning(f"Error fetching archived threads: {e}")
        except Exception as e:
            logger.warning(f"Failed to fetch threads for count in {channel_name}: {e}")

        thread_count = len(all_threads)
        for t in all_threads:
            thread_msg_count += (t.message_count or 0)

        output_data = {
            "channelName": channel_name,
            "channelID": str(channel_id),
            "messageCount": len(messages),
            "threadCount": thread_count,
            "lastMessageID": str(messages[-1]["messageID"]) if messages else None,
            "threadMessagesCount": thread_msg_count,
            "lastBackup": discord.utils.utcnow().isoformat(),
            "messages": messages
        }

        if is_thread:
            output_data["parentID"] = str(channel.parent_id)
        
        # Save channel messages
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)
            
        # Save/Update user_info.json
        with open(user_info_file, "w", encoding="utf-8") as f:
            json.dump(list(self.user_cache.values()), f, indent=4, ensure_ascii=False)

        return count

    async def _format_message(self, msg, asset_dir, asset_prefix, avatar_dir, avatar_rel_base):
        """Formats a single message to match the reference format."""
        attachments = []
        for a in msg.attachments:
            # mimic reference asset naming (suffixing hash/id)
            safe_name = a.filename
            short_id = str(a.id)[-5:]
            stored_name = f"{Path(safe_name).stem}-{short_id}{Path(safe_name).suffix}"
            
            try:
                # Check if exists, else download (basic cache)
                target = asset_dir / stored_name
                if not target.exists():
                    data = await a.read()
                    with open(target, "wb") as f:
                        f.write(data)
                
                attachments.append({
                    "id": str(a.id),
                    "url": f"{asset_prefix}/{stored_name}",
                    "fileName": a.filename,
                    "fileSizeBytes": a.size
                })
            except Exception as e:
                logger.error(f"Failed to download attachment {a.filename}: {e}")

        # Author info extraction and deduplication
        author = msg.author
        user_id = str(author.id)
        
        if user_id not in self.user_cache:
            avatar_url = None
            if author.avatar:
                try:
                    av_name = f"{user_id}.png"
                    av_target = avatar_dir / av_name
                    if not av_target.exists():
                        await author.avatar.save(av_target)
                    avatar_url = f"{avatar_rel_base}/{av_name}"
                except Exception as e:
                    logger.error(f"Failed to save avatar for {author.name}: {e}")

            roles = []
            if hasattr(author, "roles"):
                for r in author.roles:
                    if r.is_default(): continue
                    roles.append({
                        "id": str(r.id),
                        "name": r.name,
                        "color": str(r.color),
                        "position": r.position
                    })

            self.user_cache[user_id] = {
                "userID": user_id,
                "username": author.name,
                "userNickname": getattr(author, "display_name", author.name),
                "userColor": str(author.color) if hasattr(author, "color") else None,
                "userIsBot": author.bot,
                "userRoles": roles,
                "userAvatar": f"user_avatars/{user_id}.png" if author.avatar else None
            }

        reactions = []
        for r in msg.reactions:
            emoji_str = str(r.emoji) if not r.is_custom_emoji() else f"{r.emoji.name}:{r.emoji.id}"
            reactions.append({
                "emoji": emoji_str,
                "count": r.count
            })

        # Determine message type (Override if it's a thread starter)
        msg_type = str(msg.type).split(".")[-1].capitalize()
        if msg.thread:
            msg_type = "ThreadStarter"

        data = {
            "messageID": str(msg.id),
            "type": msg_type,
            "timestamp": msg.created_at.isoformat(),
            "isPinned": msg.pinned,
            "content": msg.content,
            "userID": user_id,
            "attachments": attachments,
            "embeds": [e.to_dict() for e in msg.embeds], # simplified
            "stickers": [],
            "reactions": reactions
        }

        # Thread info for creation/starter messages
        if msg.thread:
            data["thread"] = {
                "id": str(msg.thread.id),
                "name": msg.thread.name,
                "messageCount": getattr(msg.thread, "message_count", 0),
                "archived": msg.thread.archived,
                "archiveDuration": msg.thread.auto_archive_duration,
                "locked": msg.thread.locked
            }

        # Add reply reference if exists
        if msg.reference and msg.reference.message_id:
            data["reference"] = {
                "messageId": str(msg.reference.message_id),
                "channelId": str(msg.reference.channel_id)
            }

        return data

    async def export_threads(self, channel_id: int):
        """Exports active and archived threads for a channel."""
        channel = await self.reader.get_channel(channel_id)
        if not hasattr(channel, "threads") and not hasattr(channel, "public_archived_threads"):
            return 0
        
        all_threads = []
        try:
            # Active threads
            if self.reader.guild:
                threads = await self.reader.guild.active_threads()
                all_threads.extend([t for t in threads if t.parent_id == channel_id])
            
            # Archived threads
            try:
                if hasattr(channel, "archived_threads"):
                    async for thread in channel.archived_threads(limit=None):
                        all_threads.append(thread)
            except discord.Forbidden:
                logger.warning(f"403 Forbidden: Cannot fetch archived threads in {channel.name}")
            except Exception as e:
                logger.warning(f"Error fetching archived threads: {e}")
        except Exception as e:
            logger.error(f"Failed to fetch threads for {channel.name}: {e}")

        thread_count = 0
        if all_threads:
            logger.info(f"Found {len(all_threads)} threads in {channel.name}. Starting backup...")
        
        for thread in all_threads:
            await self.export_channel_messages(thread.id)
            thread_count += 1
            
        return thread_count
