import os
import json
import logging
import asyncio
import hashlib
import discord
from pathlib import Path
from typing import Dict, Any, List, Optional, AsyncGenerator
from src.core.backup_database import BackupDatabase

logger = logging.getLogger(__name__)

class DiscordExporter:
    """Core logic for exporting Discord server data."""
    
    def __init__(self, reader, base_dir: Path | str = ""):
        self.reader = reader
        self.server_name = ""
        self.server_id = ""
        self.user_cache = {}
        self.base_dir = Path(base_dir) if base_dir else Path(".")
        self.is_running = True
        self.db: Optional[BackupDatabase] = None

    async def setup(self):
        """Prepares the output directory and fetches server metadata."""
        metadata = await self.reader.get_server_metadata()
        self.server_name = metadata.get("name", "Unknown Server")
        self.server_id = metadata.get("id", "0")

        # Root export path: DISCORD_BACKUP-{id}
        self.export_path = self.base_dir / f"DISCORD_BACKUP-{self.server_id}"
        self.export_path.mkdir(parents=True, exist_ok=True)
        
        # New directory structure
        self.assets_path = self.export_path / "server_assets"
        self.assets_path.mkdir(exist_ok=True)
        
        self.users_path = self.export_path / "users"
        self.users_path.mkdir(exist_ok=True)

        self.attachments_path = self.export_path / "attachments"
        self.attachments_path.mkdir(exist_ok=True)

        # Initialize SQLite database
        db_path = self.export_path / "backup.db"
        self.db = BackupDatabase(db_path)
        
        logger.info(f"Export directory set to: {self.export_path}")
        logger.info(f"Initialized backup database at {db_path}")
        return metadata

    def _calculate_sha256(self, file_path: Path) -> str:
        """Calculates SHA-256 hash of a file."""
        hash_sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()

    async def export_metadata(self):
        """Saves server metadata to the SQLite database."""
        metadata = await self.reader.get_server_metadata()
        
        # Relative paths to local assets for the UI/Reader
        if self.reader.guild:
            if self.reader.guild.icon:
                ext = "gif" if self.reader.guild.icon.is_animated() else "png"
                metadata["icon_file"] = f"server_assets/server_icon.{ext}"
                metadata["icon_url"] = str(self.reader.guild.icon.url)
            else:
                metadata["icon_file"] = None
                metadata["icon_url"] = None
                
            if self.reader.guild.banner:
                ext = "gif" if self.reader.guild.banner.is_animated() else "png"
                metadata["banner_file"] = f"server_assets/server_banner.{ext}"
                metadata["banner_url"] = str(self.reader.guild.banner.url)
            else:
                metadata["banner_file"] = None
                metadata["banner_url"] = None

        from datetime import datetime
        metadata["last_backup"] = datetime.now().isoformat()
        
        # Fetch existing ignore_channels from DB if available
        if self.db:
            existing_profile = self.db.get_guild_profile()
            if existing_profile and "ignore_channels" in existing_profile:
                metadata["ignore_channels"] = existing_profile["ignore_channels"]
            else:
                metadata["ignore_channels"] = [] # Initialize if not present
            
            self.db.set_guild_profile(metadata)
            
        return metadata

    async def export_roles(self):
        """Exports all roles to the SQLite database."""
        roles = await self.reader.get_roles()
        role_data = []
        for r in roles:
            role_data.append({
                "id": str(r.id),
                "name": r.name,
                "color": r.color.value,
                "position": r.position,
                "permissions": str(r.permissions.value),
                "hoist": 1 if r.hoist else 0,
                "mentionable": 1 if r.mentionable else 0
            })
        
        if self.db:
            self.db.save_roles(role_data)
        return role_data

    async def download_server_assets(self):
        """Downloads server icon and banner to server_assets folder."""
        metadata = await self.reader.get_server_metadata()
        # Download Server Icon
        if metadata.get("icon_url"):
            try:
                if self.reader.guild and self.reader.guild.icon:
                    logger.info(f"Downloading server icon: {self.reader.guild.icon.url}")
                    data = await self.reader.download_asset(self.reader.guild.icon)
                    ext = "gif" if self.reader.guild.icon.is_animated() else "png"
                    icon_path = self.assets_path / f"server_icon.{ext}"
                    with open(icon_path, "wb") as f:
                        f.write(data)
                    logger.info(f"Saved server icon to {icon_path}")
            except Exception as e:
                logger.error(f"Failed to download server icon: {e}")

        # Download Server Banner
        if metadata.get("banner_url"):
            try:
                if self.reader.guild and self.reader.guild.banner:
                    logger.info(f"Downloading server banner: {self.reader.guild.banner.url}")
                    data = await self.reader.download_asset(self.reader.guild.banner)
                    ext = "gif" if self.reader.guild.banner.is_animated() else "png"
                    banner_path = self.assets_path / f"server_banner.{ext}"
                    with open(banner_path, "wb") as f:
                        f.write(data)
                    logger.info(f"Saved server banner to {banner_path}")
            except Exception as e:
                logger.error(f"Failed to download server banner: {e}")

    async def export_assets(self):
        """Exports emojis and stickers to server_assets/ folder."""
        await self.download_server_assets()
        
        emojis = await self.reader.get_emojis()
        stickers = await self.reader.get_stickers()
        
        emoji_data = []
        logger.info(f"Exporting {len(emojis)} emojis...")
        for e in emojis:
            ext = "gif" if e.animated else "png"
            filename = f"emoji_{e.id}.{ext}"
            emoji_path = self.assets_path / filename
            try:
                if not emoji_path.exists():
                    data = await self.reader.download_emoji(e)
                    with open(emoji_path, "wb") as f:
                        f.write(data)
                emoji_data.append({
                    "id": str(e.id),
                    "name": e.name,
                    "type": "emoji",
                    "filename": filename,
                    "url": str(e.url),
                    "mime_type": "image/gif" if e.animated else "image/png"
                })
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
            
            filename = f"sticker_{s.id}.{ext}"
            sticker_path = self.assets_path / filename
            try:
                if not sticker_path.exists():
                    data = await self.reader.download_sticker(s)
                    with open(sticker_path, "wb") as f:
                        f.write(data)
                mime_type = "image/png"
                if ext == "json": mime_type = "application/json"
                elif ext == "gif": mime_type = "image/gif"
                elif ext == "webp": mime_type = "image/webp"

                sticker_data.append({
                    "id": str(s.id),
                    "name": s.name,
                    "type": "sticker",
                    "filename": filename,
                    "url": str(s.url),
                    "mime_type": mime_type
                })
            except Exception as ex:
                logger.error(f"Failed to download sticker {s.name}: {ex}")

        # Save to database
        if self.db:
            all_assets = emoji_data + sticker_data
            if all_assets:
                self.db.save_server_assets(all_assets)

        return len(emoji_data), len(sticker_data)


    async def export_channels_structure(self):
        """Exports categories and channels hierarchy to SQLite."""
        categories = await self.reader.get_categories()
        channels = await self.reader.get_channels()
        
        db_channels = []
        db_permissions = []
        db_forum_tags = []
        chan_count = 0
        cat_count = len(categories)
        
        for cat in categories:
            cat_channels = [c for c in channels if c.category_id == cat.id]
            formatted_channels, cat_chan_perms, cat_forum_tags = await self._process_channel_batch(cat_channels)
            chan_count += len(formatted_channels)
            db_permissions.extend(cat_chan_perms)
            db_forum_tags.extend(cat_forum_tags)
            
            # Category permissions
            for target, ow in cat.overwrites.items():
                if isinstance(target, (discord.Role, discord.Member)):
                    allow, deny = ow.pair()
                    db_permissions.append({
                        "channel_id": str(cat.id),
                        "target_id": str(target.id),
                        "target_type": "role" if isinstance(target, discord.Role) else "member",
                        "allow": allow.value,
                        "deny": deny.value
                    })

            db_channels.append({
                "id": str(cat.id),
                "name": cat.name,
                "type": int(cat.type.value) if hasattr(cat.type, "value") else 4,
                "position": cat.position,
                "category_id": None,
                "topic": None,
                "nsfw": 0
            })

            # Add child channels to list
            for fc in formatted_channels:
                fc["category_id"] = str(cat.id)
                db_channels.append(fc)
            
        # Uncategorized
        uncategorized = [c for c in channels if not c.category_id]
        if uncategorized:
            formatted_uncat, uncat_perms, uncat_forum_tags = await self._process_channel_batch(uncategorized)
            chan_count += len(formatted_uncat)
            db_permissions.extend(uncat_perms)
            db_forum_tags.extend(uncat_forum_tags)
            for fc in formatted_uncat:
                fc["category_id"] = None
                db_channels.append(fc)
            
        if self.db:
            self.db.save_channels(db_channels)
            if db_permissions:
                self.db.save_permissions(db_permissions)
            if db_forum_tags:
                self.db.save_forum_tags(db_forum_tags)
            
        return db_channels, cat_count, chan_count

    async def _process_channel_batch(self, channels):
        """Processes a batch of channels, extracting metadata, permissions, and forum tags."""
        results = await asyncio.gather(*[self._format_channel(c) for c in channels])
        formatted = []
        permissions = []
        forum_tags = []
        for f_data, f_perms, f_tags in results:
            formatted.append(f_data)
            permissions.extend(f_perms)
            if f_tags:
                forum_tags.extend(f_tags)
        return formatted, permissions, forum_tags

    async def _format_channel(self, c):
        """Prepares channel data, its permissions, and forum tags for DB storage."""
        ch_permissions = []
        for target, ow in c.overwrites.items():
            if isinstance(target, (discord.Role, discord.Member)):
                allow, deny = ow.pair()
                ch_permissions.append({
                    "channel_id": str(c.id),
                    "target_id": str(target.id),
                    "target_type": "role" if isinstance(target, discord.Role) else "member",
                    "allow": allow.value,
                    "deny": deny.value
                })

        ch_forum_tags = []
        if isinstance(c, discord.ForumChannel):
            for t in c.available_tags:
                ch_forum_tags.append({
                    "id": str(t.id),
                    "forum_id": str(c.id),
                    "name": t.name,
                    "moderated": 1 if t.moderated else 0,
                    "emoji_id": str(t.emoji.id) if t.emoji and hasattr(t.emoji, "id") else None,
                    "emoji_name": t.emoji.name if t.emoji else (str(t.emoji) if t.emoji else None)
                })

        data = {
            "id": str(c.id),
            "name": c.name,
            "type": int(c.type.value) if hasattr(c.type, "value") else 0,
            "position": c.position,
            "topic": getattr(c, "topic", None),
            "nsfw": 1 if getattr(c, "nsfw", False) else 0
        }
        
        return data, ch_permissions, ch_forum_tags

    async def export_channel_messages(self, channel_id: int, progress_callback=None, force=False, accumulated_count=0, accumulated_threads=0, accumulated_files=0, after_id: int | None = None):
        """Fetches and saves message history for a channel to SQLite, handling incremental sync."""
        channel = await self.reader.get_channel(channel_id)
        if not channel:
            logger.error(f"Channel not found: {channel_id}")
            return accumulated_count, accumulated_threads, accumulated_files
        
        channel_name = channel.name
        is_thread = isinstance(channel, discord.Thread)
        is_forum = isinstance(channel, discord.ForumChannel)

        # 1. Determine incremental sync point
        last_id = after_id
        if not force and last_id is None and self.db:
            stored_last_id = self.db.get_last_message_id(channel_id)
            if stored_last_id:
                last_id = int(stored_last_id)
                logger.info(f"Incremental sync for {channel_name}: starting after {last_id}")

        new_count = 0
        BATCH_SIZE = 100
        USER_SAVE_INTERVAL = 500  # Save user cache every N new messages
        
        # Batch accumulator for DB inserts
        batch_messages = []
        batch_users = []

        try:
            batch_raw = []
            async for msg in self.reader.fetch_message_history(channel_id, after_id=last_id):
                if not self.is_running: break
                batch_raw.append(msg)
                
                if len(batch_raw) >= BATCH_SIZE:
                    results = await asyncio.gather(*(self._format_message(m) for m in batch_raw))
                    for m_data, u_data in results:
                        batch_messages.append(m_data)
                        if u_data: batch_users.append(u_data)
                    
                    new_count += len(batch_messages)
                    accumulated_count += len(batch_messages)
                    
                    for m in batch_messages:
                        if "attachments" in m:
                            accumulated_files += len(m["attachments"])

                    # Persist to DB
                    if self.db:
                        if batch_users: self.db.save_users(batch_users)
                        self.db.save_messages_batch(batch_messages)
                    
                    if progress_callback:
                        last_msg = batch_raw[-1]
                        author_name = getattr(last_msg.author, "display_name", "Unknown")
                        preview = (last_msg.content or "")[:150]
                        await progress_callback(channel_name, accumulated_count, author_name=author_name, message_preview=preview, thread_count=accumulated_threads, file_count=accumulated_files)
                    
                    batch_messages.clear()
                    batch_users.clear()
                    batch_raw.clear()

            # Final partial batch
            if batch_raw and self.is_running:
                results = await asyncio.gather(*(self._format_message(m) for m in batch_raw))
                for m_data, u_data in results:
                    batch_messages.append(m_data)
                    if u_data: batch_users.append(u_data)
                
                new_count += len(batch_messages)
                accumulated_count += len(batch_messages)
                
                for m in batch_messages:
                    if "attachments" in m:
                        accumulated_files += len(m["attachments"])
                
                
                if self.db:
                    if batch_users: self.db.save_users(batch_users)
                    self.db.save_messages_batch(batch_messages)
                
                if progress_callback:
                    last_msg = batch_raw[-1]
                    author_name = getattr(last_msg.author, "display_name", "Unknown")
                    await progress_callback(channel_name, accumulated_count, author_name=author_name, thread_count=accumulated_threads, file_count=accumulated_files)
                
                batch_messages.clear()
                batch_users.clear()
                batch_raw.clear()

        except discord.Forbidden:
            logger.error(f"403 Forbidden: Missing Access to read messages in {channel_name} ({channel_id})")
        except Exception as e:
            logger.error(f"Error fetching messages for {channel_name}: {e}")

        if not is_thread:
            accumulated_count, accumulated_threads, accumulated_files = await self.export_threads(channel_id, progress_callback=progress_callback, force=force, accumulated_count=accumulated_count, accumulated_threads=accumulated_threads, accumulated_files=accumulated_files)

        return accumulated_count, accumulated_threads, accumulated_files

    async def _format_message(self, msg):
        """Formats a single message and its author for DB storage."""
        # 1. Author handling
        author = msg.author
        user_id = str(author.id)
        user_data = None
        
        if user_id not in self.user_cache:
            # New user discovered
            avatar_file = None
            if author.avatar:
                try:
                    av_name = f"{user_id}.png"
                    av_target = self.users_path / av_name
                    if not av_target.exists():
                        await author.avatar.save(av_target)
                    avatar_file = f"users/{av_name}"
                except Exception as e:
                    logger.error(f"Failed to save avatar for {author.name}: {e}")

            roles = []
            if hasattr(author, "roles"):
                roles = [str(r.id) for r in author.roles if not r.is_default()]

            user_data = {
                "id": user_id,
                "username": author.name,
                "display_name": getattr(author, "display_name", author.name),
                "avatar_file": avatar_file,
                "avatar_url": str(author.display_avatar.url) if author.avatar else None,
                "roles": json.dumps(roles)
            }
            self.user_cache[user_id] = user_data

        # 2. Attachments handling (Content-Addressable Storage)
        attachments = []
        if msg.attachments:
            for att in msg.attachments:
                att_data = await self._process_media(
                    media_id=att.id,
                    url=att.url,
                    filename=att.filename,
                    size=att.size,
                    content_type=att.content_type,
                    save_method=att.save
                )
                if att_data:
                    attachments.append(att_data)

        # 2.5 Stickers handling
        stickers = []
        if msg.stickers:
            for st in msg.stickers:
                # Determine extension based on format
                ext = ".png"
                if hasattr(st, "format"):
                    try:
                        from discord import StickerFormatType
                        if st.format == StickerFormatType.lottie:
                            ext = ".json"
                        elif st.format == StickerFormatType.apng:
                            ext = ".png"
                        elif st.format == StickerFormatType.gif:
                            ext = ".gif"
                    except ImportError:
                        pass
                
                st_data = await self._process_media(
                    media_id=st.id,
                    url=st.url,
                    filename=f"{st.name}{ext}",
                    content_type=f"image/{ext[1:]}" if ext != ".json" else "application/json",
                    save_method=st.save
                )
                if st_data:
                    st_data["format_type"] = int(st.format.value) if hasattr(st, "format") and hasattr(st.format, "value") else 1
                    stickers.append(st_data)

        # 3. Embeds
        embeds = []
        if msg.embeds:
            embeds = [emb.to_dict() for emb in msg.embeds]

        # 4. Reactions
        reactions = []
        if msg.reactions:
            for react in msg.reactions:
                emoji = react.emoji
                reactions.append({
                    "emoji_id": emoji.id if hasattr(emoji, "id") else None,
                    "emoji_name": emoji.name if hasattr(emoji, "name") else str(emoji),
                    "count": react.count
                })

        # 5. Message data
        # Check for reference (reply/origin of forward)
        message_reference = None
        if msg.reference and msg.reference.message_id:
            message_reference = str(msg.reference.message_id)

        # 5.5 Forwarded snapshots
        content = msg.content or ""
        msg_type = int(msg.type.value) if hasattr(msg.type, "value") else 0
        
        # Detect if this message is forwarded (discord.py 2.5+)
        is_forwarded = getattr(msg.flags, 'forwarded', False)
        if is_forwarded and hasattr(msg, 'message_snapshots') and msg.message_snapshots:
            msg_type = 100 # Custom Forward type
            snapshot = msg.message_snapshots[0]
            if snapshot.content:
                content = snapshot.content
            
            # Process snapshot attachments into main attachments list
            for s_att in snapshot.attachments:
                att_res = await self._process_media(
                    media_id=s_att.id,
                    url=s_att.url,
                    filename=s_att.filename,
                    size=s_att.size,
                    content_type=s_att.content_type,
                    save_method=s_att.save
                )
                if att_res: attachments.append(att_res)
            
            # Process snapshot embeds (simplified)
            for s_emb in snapshot.embeds:
                # We reuse the main embeds list
                embeds.append(s_emb.to_dict())

        m_data = {
            "id": str(msg.id),
            "channel_id": str(msg.channel.id),
            "author_id": user_id,
            "content": content,
            "timestamp": msg.created_at.isoformat(),
            "type": msg_type,
            "message_reference": message_reference,
            "is_pinned": 1 if msg.pinned else 0,
            "attachments": attachments,
            "stickers": stickers,
            "embeds": embeds,
            "reactions": reactions,
            "extra_data": None
        }

        return m_data, user_data

    async def _process_media(self, media_id, url, filename, size=None, content_type=None, save_method=None):
        """Downloads and deduplicates any media (attachment or sticker) using SHA-256 (CAS)."""
        # 1. First check by URL in DB
        if self.db:
            existing = self.db.get_media_by_url(str(url))
            if existing:
                return {
                    "id": str(media_id),
                    "filename": filename,
                    "size": existing["size"],
                    "url": str(url),
                    "content_type": existing["content_type"],
                    "local_hash": existing["hash"]
                }

        # 2. Temporary download to calculate hash
        import tempfile
        import shutil
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = Path(tmp.name)
            try:
                if save_method:
                    await save_method(tmp_path)
                else:
                    return None
                
                file_hash = self._calculate_sha256(tmp_path)
                actual_size = tmp_path.stat().st_size
                
                # Check if hash already exists in pool
                if self.db:
                    in_pool = self.db.get_media_by_hash(file_hash)
                    if in_pool:
                        tmp_path.unlink()
                        return {
                            "id": str(media_id),
                            "filename": filename,
                            "size": actual_size,
                            "url": str(url),
                            "content_type": content_type or in_pool["content_type"],
                            "local_hash": file_hash
                        }
                
                # New content: move to pool
                ext = Path(filename).suffix
                target_filename = f"{file_hash}{ext}"
                target_path = self.attachments_path / target_filename
                
                shutil.move(str(tmp_path), str(target_path))
                
                if self.db:
                    self.db.add_media_to_pool(file_hash, f"attachments/{target_filename}", actual_size, content_type, str(url))
                
                return {
                    "id": str(media_id),
                    "filename": filename,
                    "size": actual_size,
                    "url": str(url),
                    "content_type": content_type,
                    "local_hash": file_hash
                }
            except Exception as e:
                logger.error(f"Failed to process media {filename}: {e}")
                if tmp_path.exists(): tmp_path.unlink()
                return None

    async def export_threads(self, channel_id: int, progress_callback=None, force=False, accumulated_count=0, accumulated_threads=0, accumulated_files=0, after_id: int | None = None):
        """Exports active and archived threads for a channel to SQLite."""
        channel = await self.reader.get_channel(channel_id)
        if not hasattr(channel, "threads") and not hasattr(channel, "archived_threads"):
            return accumulated_count, accumulated_threads, accumulated_files
        
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
        logger.debug(f"Exporting threads for channel '{channel.name}' ({channel.id}) [Type: {type(channel)}] [Is Forum: {is_forum}]")

        if all_threads and self.db:
            thread_meta = []
            for t in all_threads:
                applied_tags = []
                
                # If parent is missing, try to link it from our fetched channel object
                # This ensures discord.py can resolve the applied_tags correctly
                try:
                    if t.parent is None:
                        # Internal hack: link parent if missing to help resolve tags
                        t._parent = channel
                except Exception:
                    pass

                if hasattr(t, "applied_tags"):
                    # Attempt 1: Standard attribute
                    applied_tags = [str(tag.id) for tag in t.applied_tags]
                    
                    # Attempt 2: Internal list of IDs if available (sometimes populated when property is empty)
                    if not applied_tags and hasattr(t, "_applied_tags"):
                        raw_ids = getattr(t, "_applied_tags", [])
                        if raw_ids:
                            logger.info(f"Thread '{t.name}' ({t.id}) found raw tags in _applied_tags: {raw_ids}")
                            applied_tags = [str(tid) for tid in raw_ids]

                    # Attempt 3: If still empty and it's a forum thread, try to fetch it specifically
                    if not applied_tags and is_forum:
                        try:
                            # We can try to fetch the thread specifically to get tags
                            # (Discord sometimes doesn't include tags in bulk guild.active_threads)
                            fetched_t = await self.reader.client.fetch_channel(t.id)
                            # Check both property and internal list on fetched object
                            if hasattr(fetched_t, "applied_tags"):
                                applied_tags = [str(tag.id) for tag in fetched_t.applied_tags]
                            if not applied_tags and hasattr(fetched_t, "_applied_tags"):
                                raw_ids = getattr(fetched_t, "_applied_tags", [])
                                applied_tags = [str(tid) for tid in raw_ids]
                        except Exception as e:
                            logger.debug(f"Failed to fetch thread {t.id} for tags: {e}")
                            pass
                
                if not applied_tags and is_forum:
                    logger.warning(f"Thread '{t.name}' ({t.id}) is in forum '{channel.name}' but NO tags found (tried all methods)")

                thread_meta.append({
                    "id": str(t.id),
                    "name": t.name,
                    "type": int(t.type.value) if hasattr(t.type, "value") else 11, # Default to public_thread
                    "parent_id": str(t.parent_id) if t.parent_id else str(channel.id),
                    "message_count": getattr(t, "message_count", 0),
                    "member_count": getattr(t, "member_count", 0),
                    "archived": 1 if t.archived else 0,
                    "archive_timestamp": t.archive_timestamp.isoformat() if t.archive_timestamp else None,
                    "auto_archive_duration": t.auto_archive_duration,
                    "locked": 1 if getattr(t, "locked", False) else 0,
                    "applied_tags": json.dumps(applied_tags)
                })
            self.db.save_threads(thread_meta)

        for thread in all_threads:
            if not self.is_running: break
            await asyncio.sleep(0)
            
            accumulated_threads += 1
            if progress_callback:
                await progress_callback(channel.name, accumulated_count, thread_count=accumulated_threads, file_count=accumulated_files)
            
            # Backup thread messages
            accumulated_count, accumulated_threads, accumulated_files = await self.export_channel_messages(thread.id, progress_callback=progress_callback, force=force, accumulated_count=accumulated_count, accumulated_threads=accumulated_threads, accumulated_files=accumulated_files, after_id=after_id)

            # For forums, ensure the starter message exists in the DB
            if is_forum:
                # starter_message is handled by export_channel_messages since it's just a message in that thread
                # However we may want to mark it or store forum-specific tags
                try:
                    # Just yield for concurrency
                    await asyncio.sleep(0)
                except Exception as e:
                    logger.error(f"Error processing forum thread {thread.name}: {e}")
                    
        return accumulated_count, accumulated_threads, accumulated_files
