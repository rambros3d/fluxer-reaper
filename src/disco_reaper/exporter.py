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

        # Add metadata fields
        from datetime import datetime
        metadata["last_backup"] = datetime.now().isoformat()
        
        output_file = self.export_path / "server_profile.json"
        
        # Preserve ignore_channels if the file already exists
        ignore_channels = []
        if output_file.exists():
            try:
                with open(output_file, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    ignore_channels = old_data.get("ignore_channels", [])
            except Exception as e:
                logger.warning(f"Could not read existing server_profile.json to preserve ignore_channels: {e}")
        
        metadata["ignore_channels"] = ignore_channels

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


    async def export_channels_structure(self):
        """Exports categories and channels hierarchy."""
        categories = await self.reader.get_categories()
        channels = await self.reader.get_channels()
        
        structure = []
        chan_count = 0
        cat_count = len(categories)
        
        for cat in categories:
            cat_channels = [c for c in channels if c.category_id == cat.id]
            formatted_channels = await asyncio.gather(*[self._format_channel(c) for c in cat_channels])
            chan_count += len(formatted_channels)
            structure.append({
                "type": "category",
                "id": str(cat.id),
                "name": cat.name,
                "position": cat.position,
                "channels": list(formatted_channels)
            })
            
        # Uncategorized
        uncategorized = [c for c in channels if not c.category_id]
        if uncategorized:
            formatted_uncat = await asyncio.gather(*[self._format_channel(c) for c in uncategorized])
            chan_count += len(formatted_uncat)
            structure.append({
                "type": "category",
                "id": "uncategorized",
                "name": "Uncategorized",
                "channels": list(formatted_uncat)
            })
            # No need to increment cat_count for 'Uncategorized' usually, 
            # but let's see if the user wants it. For now, cat_count is real Discord categories.
            
        output_file = self.export_path / "server_structure.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(structure, f, indent=4, ensure_ascii=False)
        return structure, cat_count, chan_count

    async def _format_channel(self, c):
        data = {
            "id": str(c.id),
            "name": c.name,
            "type": str(c.type),
            "position": c.position,
            "topic": getattr(c, "topic", None),
            "nsfw": getattr(c, "nsfw", False)
        }
        
        if isinstance(c, discord.ForumChannel):
            data["available_tags"] = [
                {"id": str(t.id), "name": t.name, "moderated": t.moderated, "emoji_id": str(t.emoji.id) if t.emoji and hasattr(t.emoji, "id") else None, "emoji_name": t.emoji.name if t.emoji else None}
                for t in c.available_tags
            ]
            
        return data

    async def export_channel_messages(self, channel_id: int, progress_callback=None, force=False):
        """Fetches and saves message history for a channel, handling incremental sync."""
        channel = await self.reader.get_channel(channel_id)
        if not channel:
            logger.error(f"Channel not found: {channel_id}")
            return 0
        
        channel_name = channel.name
        safe_name = channel_name.replace(" ", "-").lower()

        # Detection for thread grouping
        is_thread = isinstance(channel, discord.Thread)
        is_forum = isinstance(channel, discord.ForumChannel)
        backup_root = self.export_path / "message_backup"
        
        if is_thread:
            parent = await self.reader.get_channel(channel.parent_id)
            if isinstance(parent, discord.ForumChannel):
                # Forum thread: nested inside forum folder
                backup_dir = backup_root / str(channel.parent_id)
                avatar_rel_base = "../../user_avatars"
            else:
                # Regular thread
                backup_dir = backup_root / "threads"
                avatar_rel_base = "../user_avatars"
        elif is_forum:
            # Forum metadata root
            backup_dir = backup_root
            avatar_rel_base = "user_avatars"
        else:
            # Regular channel
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
        
        if force and asset_dir.exists():
            import shutil
            try:
                shutil.rmtree(asset_dir)
            except Exception as e:
                logger.warning(f"Failed to clear asset directory {asset_dir}: {e}")
        
        asset_dir.mkdir(exist_ok=True)
        
        messages = []
        last_id = None
        
        # Load existing messages for incremental sync (skip if force)
        if not force and json_file.exists():
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
                    await progress_callback(channel_name, new_count)
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

        msg_type = "Text"
        if is_thread:
            msg_type = "Thread"
        elif channel.type == discord.ChannelType.news:
            msg_type = "News"
        elif is_forum:
            msg_type = "Forum"

        output_data = {
            "channelName": channel_name,
            "channelID": str(channel_id),
            "channelType": msg_type,
            "messageCount": len(messages),
            "threadCount": thread_count,
            "lastMessageID": str(messages[-1]["messageID"]) if messages else None,
            "threadMessagesCount": thread_msg_count,
            "lastBackup": discord.utils.utcnow().isoformat(),
            "messages": messages
        }
        
        if is_thread:
            output_data["parentID"] = str(channel.parent_id)
        
        # Merge additional metadata for forums (like tags)
        if is_forum:
            fmt_data = await self._format_channel(channel)
            for k, v in fmt_data.items():
                if k not in output_data and k not in ["id", "name", "type", "position", "nsfw", "topic"]:
                    output_data[k] = v
        
        # Save channel messages
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)
            
        # Save/Update user_info.json
        with open(user_info_file, "w", encoding="utf-8") as f:
            json.dump(list(self.user_cache.values()), f, indent=4, ensure_ascii=False)

        # If it's a forum, also export its threads into the sub-directory
        if is_forum:
            await self.export_threads(channel_id, progress_callback=progress_callback, force=force)

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

        # Process Stickers (Download and Metadata)
        stickers = []
        for s in msg.stickers:
            sticker_filename = f"sticker_{s.id}"
            # Extension mapping based on format
            ext = "png"
            if str(s.format).endswith("apng"): ext = "apng"
            elif str(s.format).endswith("lottie"): ext = "json"
            elif str(s.format).endswith("gif"): ext = "gif"
            
            sticker_filename += f".{ext}"
            sticker_path = asset_dir / sticker_filename
            
            try:
                if not sticker_path.exists():
                    # Handle Lottie stickers manually since discord.py Refuses to save them
                    if str(s.format).endswith("lottie"):
                        # Use the name-mangled internal session from the client
                        session = self.reader.client.http._HTTPClient__session
                        async with session.get(s.url) as resp:
                            if resp.status == 200:
                                with open(sticker_path, "wb") as f:
                                    f.write(await resp.read())
                            else:
                                raise Exception(f"HTTP {resp.status}")
                    else:
                        await s.save(sticker_path)
                
                stickers.append({
                    "id": str(s.id),
                    "name": s.name,
                    "format": str(s.format).split(".")[-1],
                    "localPath": f"{asset_prefix}/{sticker_filename}"
                })
            except Exception as e:
                logger.error(f"Failed to download sticker {s.name} ({s.id}): {e}")
                # Fallback to minimal metadata if download fails
                stickers.append({
                    "id": str(s.id),
                    "name": s.name,
                    "format": str(s.format).split(".")[-1]
                })

        # Determine message type (Override if it's a thread starter or forward)
        msg_type = str(msg.type).split(".")[-1].capitalize()
        if msg.thread:
            msg_type = "ThreadStarter"
        
        # Check for forwarded flags (newer discord.py feature)
        try:
            if hasattr(msg.flags, "forwarded") and msg.flags.forwarded:
                msg_type = "Forward"
        except Exception:
            pass

        msg_content = msg.content
        if msg_type == "Forward" and not msg_content:
            try:
                if hasattr(msg, "message_snapshots") and msg.message_snapshots:
                    msg_content = msg.message_snapshots[0].content
            except Exception:
                pass

        data = {
            "messageID": str(msg.id),
            "type": msg_type,
            "timestamp": msg.created_at.isoformat(),
            "isPinned": msg.pinned,
            "content": msg_content,
            "userID": user_id,
            "attachments": attachments,
            "embeds": [e.to_dict() for e in msg.embeds],
            "stickers": stickers,
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

    async def export_threads(self, channel_id: int, progress_callback=None, force=False):
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

        is_forum = isinstance(channel, discord.ForumChannel)
        backup_root = self.export_path / "message_backup"
        forum_json_file = backup_root / f"{channel_id}.json"
        forum_asset_dir = backup_root / str(channel_id)
        avatar_dir = backup_root / "user_avatars"

        thread_count = 0
        if all_threads:
            logger.info(f"Found {len(all_threads)} threads in {channel.name}. Starting backup...")
        
        for thread in all_threads:
            # Whenever a forum thread backup starts, populate the forum root json with the starter message.
            if is_forum:
                logger.info(f"Adding starter message for thread: {thread.name} ({thread.id})")
                try:
                    msg_found = False
                    # In discord.py 2.x, we get the oldest message by using 'after' with a limit
                    async for msg in thread.history(limit=1, after=discord.Object(id=thread.id - 1)):
                        msg_found = True
                        logger.debug(f"Found starter message {msg.id} for {thread.name}")
                        
                        # Save assets in the thread's own directory inside the forum directory
                        thread_asset_dir = forum_asset_dir / str(thread.id)
                        thread_asset_dir.mkdir(parents=True, exist_ok=True)
                        
                        msg_data = await self._format_message(
                            msg, 
                            thread_asset_dir, 
                            f"{channel_id}/{thread.id}",  # Full relative path from message_backup/
                            avatar_dir, 
                            "../../user_avatars"  # Two levels up from {forum_id}/{thread_id}/
                        )
                        # Override type and add title for forum starter messages
                        msg_data["type"] = "Thread_starter_message"
                        msg_data["title"] = thread.name
                        
                        # Store applied tag IDs (as strings) — names are resolvable via the forum's available_tags
                        msg_data["tags"] = [str(tid) for tid in getattr(thread, "_applied_tags", [])]
                        
                        if forum_json_file.exists():
                            with open(forum_json_file, "r", encoding="utf-8") as f:
                                try:
                                    forum_data = json.load(f)
                                except Exception as e:
                                    logger.error(f"Failed to load forum JSON: {e}")
                                    forum_data = {}
                            
                            if "messages" not in forum_data:
                                forum_data["messages"] = []
                            
                            # Avoid duplicates
                            if not any(m["messageID"] == msg_data["messageID"] for m in forum_data["messages"]):
                                forum_data["messages"].append(msg_data)
                                forum_data["messageCount"] = len(forum_data["messages"])
                                # Keep chronological order
                                forum_data["messages"].sort(key=lambda x: x["timestamp"])
                                
                                with open(forum_json_file, "w", encoding="utf-8") as f:
                                    json.dump(forum_data, f, indent=4, ensure_ascii=False)
                                logger.info(f"Appended starter message for {thread.name} to {forum_json_file.name}")
                            else:
                                logger.debug(f"Starter message for {thread.name} already in JSON")
                        else:
                            logger.warning(f"Forum JSON file does not exist: {forum_json_file}")
                    
                    if not msg_found:
                        logger.warning(f"No starter message found for thread: {thread.name}")
                except Exception as e:
                    logger.error(f"Error adding starter message for {thread.name}: {e}")

            await self.export_channel_messages(thread.id, progress_callback=progress_callback, force=force)
            thread_count += 1
            
        return thread_count
