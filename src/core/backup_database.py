import sqlite3
import logging
import json
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Union

logger = logging.getLogger(__name__)

def parse_snowflake(value: Any) -> Optional[int]:
    """Safely parses a Discord ID (Snowflake) from any input, handling 'None' strings."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "none" or s == "NULL":
        return None
    try:
        return int(s)
    except ValueError:
        return None

class BackupDatabase:
    """Manages the SQLite database for local Discord backups."""
    
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL mode allows concurrent readers and batches disk flushes significantly
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA cache_size=-32000")  # 32 MB page cache
        self._init_db()

    def _init_db(self):
        """Initializes the database schema."""
        with self._lock:
            conn = self._conn
            try:
                # Guild Profile
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS guild_profile (
                        id TEXT PRIMARY KEY,
                        name TEXT,
                        description TEXT,
                        icon_file TEXT,
                        icon_url TEXT,
                        banner_file TEXT,
                        banner_url TEXT,
                        owner_id TEXT,
                        last_backup TEXT,
                        ignore_channels TEXT
                    )
                """)

                # Roles
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS roles (
                        id TEXT PRIMARY KEY,
                        name TEXT,
                        color INTEGER,
                        position INTEGER,
                        permissions TEXT,
                        hoist INTEGER,
                        mentionable INTEGER
                    )
                """)

                # Channels
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS channels (
                        id TEXT PRIMARY KEY,
                        name TEXT,
                        type INTEGER,
                        position INTEGER,
                        category_id TEXT,
                        topic TEXT,
                        nsfw INTEGER,
                        bitrate INTEGER,
                        slowmode_delay INTEGER
                    )
                """)

                # Channel Permissions
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS permissions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        channel_id TEXT,
                        target_id TEXT,
                        target_type TEXT,
                        allow INTEGER,
                        deny INTEGER
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_permissions_chan ON permissions(channel_id)")

                # Users (Author cache)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        username TEXT,
                        display_name TEXT,
                        avatar_file TEXT,
                        avatar_url TEXT,
                        roles TEXT
                    )
                """)

                # Messages
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id TEXT PRIMARY KEY,
                        channel_id TEXT,
                        author_id TEXT,
                        content TEXT,
                        timestamp TEXT,
                        type INTEGER,
                        message_reference TEXT,
                        is_pinned INTEGER,
                        extra_data TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp)")

                # Attachments
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS attachments (
                        id TEXT PRIMARY KEY,
                        message_id TEXT,
                        filename TEXT,
                        size INTEGER,
                        url TEXT,
                        content_type TEXT,
                        local_hash TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_attachments_msg ON attachments(message_id)")

                # Embeds
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS embeds (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        message_id TEXT,
                        title TEXT,
                        description TEXT,
                        url TEXT,
                        color INTEGER,
                        timestamp TEXT,
                        thumbnail_url TEXT,
                        image_url TEXT,
                        author_name TEXT,
                        author_url TEXT,
                        author_icon_url TEXT,
                        footer_text TEXT,
                        footer_icon_url TEXT,
                        fields TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_embeds_msg ON embeds(message_id)")

                # Reactions
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS reactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        message_id TEXT,
                        emoji_id TEXT,
                        emoji_name TEXT,
                        count INTEGER
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_reactions_msg ON reactions(message_id)")

                # Message Stickers
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS message_stickers (
                        message_id TEXT,
                        sticker_id TEXT,
                        name TEXT,
                        url TEXT,
                        format_type INTEGER,
                        local_hash TEXT,
                        PRIMARY KEY (message_id, sticker_id)
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_message_stickers_msg ON message_stickers(message_id)")

                # Threads
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS threads (
                        id TEXT PRIMARY KEY,
                        name TEXT,
                        type INTEGER,
                        parent_id TEXT,
                        message_count INTEGER,
                        member_count INTEGER,
                        archived INTEGER,
                        archive_timestamp TEXT,
                        auto_archive_duration INTEGER,
                        locked INTEGER,
                        applied_tags TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_threads_parent ON threads(parent_id)")

                # Forum Tags (Definitions for a forum channel)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS forum_tags (
                        id TEXT PRIMARY KEY,
                        forum_id TEXT,
                        name TEXT,
                        moderated INTEGER,
                        emoji_id TEXT,
                        emoji_name TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_forum_tags_forum ON forum_tags(forum_id)")

                # Media Pool (CAS)
                # Maps content hashes to local storage paths
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS media_pool (
                        hash TEXT PRIMARY KEY,
                        local_path TEXT,
                        size INTEGER,
                        mime_type TEXT,
                        first_seen_url TEXT
                    )
                """)
                
                # Server Assets (Emojis, Stickers, etc.)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS server_assets (
                        id TEXT PRIMARY KEY,
                        name TEXT,
                        type TEXT,
                        filename TEXT,
                        url TEXT,
                        mime_type TEXT
                    )
                """)

                conn.commit()
            finally:
                pass  # persistent connection — do not close

    def set_guild_profile(self, data: Dict[str, Any]):
        with self._lock:
            self._conn.execute("""
                INSERT OR REPLACE INTO guild_profile (id, name, description, icon_file, icon_url, banner_file, banner_url, owner_id, last_backup, ignore_channels)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(data.get("id")), data.get("name"), data.get("description"),
                data.get("icon_file"), data.get("icon_url"), 
                data.get("banner_file"), data.get("banner_url"), 
                str(data.get("owner_id")),
                data.get("last_backup"), json.dumps(data.get("ignore_channels", []))
            ))
            self._conn.commit()

    def get_guild_profile(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM guild_profile LIMIT 1").fetchone()
            if row:
                data = dict(row)
                if data.get("ignore_channels"):
                    data["ignore_channels"] = json.loads(data["ignore_channels"])
                else:
                    data["ignore_channels"] = []
                return data
            return None

    def save_roles(self, roles: List[Dict[str, Any]]):
        with self._lock:
            formatted = [
                {
                    "id": str(r["id"]),
                    "name": r["name"],
                    "color": r["color"],
                    "position": r["position"],
                    "permissions": str(r["permissions"]),
                    "hoist": 1 if r["hoist"] else 0,
                    "mentionable": 1 if r["mentionable"] else 0
                }
                for r in roles
            ]
            self._conn.executemany("""
                INSERT OR REPLACE INTO roles (id, name, color, position, permissions, hoist, mentionable)
                VALUES (:id, :name, :color, :position, :permissions, :hoist, :mentionable)
            """, formatted)
            self._conn.commit()

    def save_channels(self, channels: List[Dict[str, Any]]):
        with self._lock:
            self._conn.executemany("""
                INSERT OR REPLACE INTO channels (id, name, type, position, category_id, topic, nsfw, bitrate, slowmode_delay)
                VALUES (:id, :name, :type, :position, :category_id, :topic, :nsfw, :bitrate, :slowmode_delay)
            """, channels)
            self._conn.commit()

    def save_permissions(self, permissions: List[Dict[str, Any]]):
        """Saves a batch of channel permission overwrites."""
        with self._lock:
            self._conn.executemany("""
                INSERT INTO permissions (channel_id, target_id, target_type, allow, deny)
                VALUES (:channel_id, :target_id, :target_type, :allow, :deny)
            """, permissions)
            self._conn.commit()

    def save_users(self, users: List[Dict[str, Any]]):
        """Saves users to the author cache."""
        with self._lock:
            self._conn.executemany("""
                INSERT OR REPLACE INTO users (id, username, display_name, avatar_file, avatar_url, roles)
                VALUES (:id, :username, :display_name, :avatar_file, :avatar_url, :roles)
            """, users)
            self._conn.commit()

    def save_server_assets(self, assets: List[Dict[str, Any]]):
        """Saves a batch of server assets (emojis, stickers) to the database."""
        with self._lock:
            formatted = [
                {
                    "id": str(a["id"]),
                    "name": a.get("name"),
                    "type": a.get("type"),
                    "filename": a.get("filename"),
                    "url": a.get("url"),
                    "mime_type": a.get("mime_type")
                }
                for a in assets
            ]
            self._conn.executemany("""
                INSERT OR REPLACE INTO server_assets (id, name, type, filename, url, mime_type)
                VALUES (:id, :name, :type, :filename, :url, :mime_type)
            """, formatted)
            self._conn.commit()

    def save_threads(self, threads: List[Dict[str, Any]]):
        """Saves metadata for threads to the database."""
        with self._lock:
            self._conn.executemany("""
                INSERT OR REPLACE INTO threads (id, name, type, parent_id, message_count, member_count, archived, archive_timestamp, auto_archive_duration, locked, applied_tags)
                VALUES (:id, :name, :type, :parent_id, :message_count, :member_count, :archived, :archive_timestamp, :auto_archive_duration, :locked, :applied_tags)
            """, threads)
            self._conn.commit()

    def save_forum_tags(self, tags: List[Dict[str, Any]]):
        """Saves definitions for forum tags."""
        with self._lock:
            self._conn.executemany("""
                INSERT OR REPLACE INTO forum_tags (id, forum_id, name, moderated, emoji_id, emoji_name)
                VALUES (:id, :forum_id, :name, :moderated, :emoji_id, :emoji_name)
            """, tags)
            self._conn.commit()

    def save_messages_batch(self, messages: List[Dict[str, Any]]):
        """Batch inserts messages and their attachments."""
        with self._lock:
            conn = self._conn
            # Insert messages
            conn.executemany("""
                INSERT OR REPLACE INTO messages (id, channel_id, author_id, content, timestamp, type, message_reference, is_pinned, extra_data)
                VALUES (:id, :channel_id, :author_id, :content, :timestamp, :type, :message_reference, :is_pinned, :extra_data)
            """, messages)
            
            # Extract attachments, reactions, and stickers
            all_attachments = []
            all_reactions = []
            all_stickers = []
            
            for msg in messages:
                # Attachments
                if "attachments" in msg:
                    for att in msg["attachments"]:
                        att["message_id"] = msg["id"]
                        all_attachments.append(att)
                
                # Reactions
                if "reactions" in msg and msg["reactions"]:
                    for rea in msg["reactions"]:
                        all_reactions.append({
                            "message_id": msg["id"],
                            "emoji_id": str(rea["emoji_id"]) if rea.get("emoji_id") else None,
                            "emoji_name": rea.get("emoji_name"),
                            "count": rea.get("count", 0)
                        })
                
                # Stickers
                if "stickers" in msg and msg["stickers"]:
                    for st in msg["stickers"]:
                        all_stickers.append({
                            "message_id": msg["id"],
                            "sticker_id": str(st["id"]),
                            "name": st.get("name"),
                            "url": st.get("url"),
                            "format_type": st.get("format_type"),
                            "local_hash": st.get("local_hash")
                        })
                        
            if all_attachments:
                conn.executemany("""
                    INSERT OR REPLACE INTO attachments (id, message_id, filename, size, url, content_type, local_hash)
                    VALUES (:id, :message_id, :filename, :size, :url, :content_type, :local_hash)
                """, all_attachments)

            # Save Embeds (Normalized with JSON Fields)
            for msg in messages:
                if "embeds" in msg and msg["embeds"]:
                    for emb in msg["embeds"]:
                        conn.execute("""
                            INSERT INTO embeds (
                                message_id, title, description, url, color, timestamp,
                                thumbnail_url, image_url, author_name, author_url, 
                                author_icon_url, footer_text, footer_icon_url, fields
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            msg["id"], emb.get("title"), emb.get("description"), emb.get("url"),
                            emb.get("color"), emb.get("timestamp"),
                            emb.get("thumbnail", {}).get("url") if isinstance(emb.get("thumbnail"), dict) else None,
                            emb.get("image", {}).get("url") if isinstance(emb.get("image"), dict) else None,
                            emb.get("author", {}).get("name") if isinstance(emb.get("author"), dict) else None,
                            emb.get("author", {}).get("url") if isinstance(emb.get("author"), dict) else None,
                            emb.get("author", {}).get("icon_url") if isinstance(emb.get("author"), dict) else None,
                            emb.get("footer", {}).get("text") if isinstance(emb.get("footer"), dict) else None,
                            emb.get("footer", {}).get("icon_url") if isinstance(emb.get("footer"), dict) else None,
                            json.dumps(emb.get("fields", []))
                        ))

            if all_reactions:
                conn.executemany("""
                    INSERT INTO reactions (message_id, emoji_id, emoji_name, count)
                    VALUES (:message_id, :emoji_id, :emoji_name, :count)
                """, all_reactions)
            
            if all_stickers:
                conn.executemany("""
                    INSERT OR REPLACE INTO message_stickers (message_id, sticker_id, name, url, format_type, local_hash)
                    VALUES (:message_id, :sticker_id, :name, :url, :format_type, :local_hash)
                """, all_stickers)
            
            conn.commit()

    def get_last_message_id(self, channel_id: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT id FROM messages WHERE channel_id = ? ORDER BY id DESC LIMIT 1", (str(channel_id),)).fetchone()
            return row["id"] if row else None

    def get_media_by_hash(self, file_hash: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM media_pool WHERE hash = ?", (file_hash,)).fetchone()
            return dict(row) if row else None

    def get_media_by_url(self, url: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM media_pool WHERE first_seen_url = ?", (url,)).fetchone()
            return dict(row) if row else None

    def add_media_to_pool(self, file_hash: str, local_path: str, size: int, mime_type: str, url: str):
        # NOTE: No commit here — caller (save_messages_batch) commits at end of batch
        with self._lock:
            self._conn.execute("""
                INSERT OR REPLACE INTO media_pool (hash, local_path, size, mime_type, first_seen_url)
                VALUES (?, ?, ?, ?, ?)
            """, (file_hash, str(local_path), size, mime_type, url))
    def get_stats_by_channel(self) -> Dict[int, Dict[str, Any]]:
        """Returns aggregate stats for all channels with backups."""
        with self._lock:
            # 1. Message counts (aggregating threads into parent channel)
            msg_rows = self._conn.execute("""
                SELECT 
                    COALESCE(t.parent_id, m.channel_id) as agg_channel_id,
                    COUNT(m.id) as msg_count
                FROM messages m
                LEFT JOIN threads t ON m.channel_id = t.id
                GROUP BY agg_channel_id
            """).fetchall()
            
            # 2. Thread counts
            thread_rows = self._conn.execute("""
                SELECT parent_id, COUNT(*) as thread_count 
                FROM threads 
                GROUP BY parent_id
            """).fetchall()
            
            # 3. Attachment counts and sizes
            att_rows = self._conn.execute("""
                SELECT 
                    COALESCE(t.parent_id, m.channel_id) as agg_channel_id,
                    COUNT(a.id) as att_count,
                    SUM(a.size) as total_size
                FROM attachments a
                JOIN messages m ON a.message_id = m.id
                LEFT JOIN threads t ON m.channel_id = t.id
                GROUP BY agg_channel_id
            """).fetchall()
            
            stats = {}
            for r in msg_rows:
                cid = parse_snowflake(r["agg_channel_id"])
                if cid is None: continue
                stats[cid] = {
                    "message_count": r["msg_count"],
                    "thread_count": 0,
                    "attachment_count": 0,
                    "total_size": 0
                }

            for r in thread_rows:
                cid = parse_snowflake(r["parent_id"])
                if cid is None: continue
                if cid not in stats:
                    stats[cid] = {"message_count": 0, "thread_count": 0, "attachment_count": 0, "total_size": 0}
                stats[cid]["thread_count"] = r["thread_count"]
            
            for r in att_rows:
                cid = parse_snowflake(r["agg_channel_id"])
                if cid is None: continue
                if cid not in stats:
                    stats[cid] = {"message_count": 0, "thread_count": 0, "attachment_count": 0, "total_size": 0}
                stats[cid]["attachment_count"] = r["att_count"]
                stats[cid]["total_size"] = r["total_size"] or 0
                
            return stats

    def get_all_roles(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM roles ORDER BY position DESC").fetchall()
            return [dict(r) for r in rows]

    def get_all_channels(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM channels ORDER BY position ASC").fetchall()
            
            chan_list = [dict(r) for r in rows]
            if chan_list:
                ids = [c["id"] for c in chan_list]
                placeholders = ",".join(["?"] * len(ids))
                perm_rows = self._conn.execute(f"SELECT * FROM permissions WHERE channel_id IN ({placeholders})", ids).fetchall()
                
                perms_by_chan = {}
                for pr in perm_rows:
                    cid = pr["channel_id"]
                    if cid not in perms_by_chan: perms_by_chan[cid] = []
                    perms_by_chan[cid].append({
                        "id": pr["target_id"],
                        "type": pr["target_type"],
                        "allow": pr["allow"],
                        "deny": pr["deny"]
                    })
                
                for c in chan_list:
                    c["overwrites"] = perms_by_chan.get(c["id"], [])
                
                tag_rows = self._conn.execute(f"SELECT * FROM forum_tags WHERE forum_id IN ({placeholders})", ids).fetchall()
                tags_by_forum = {}
                for tr in tag_rows:
                    fid = tr["forum_id"]
                    if fid not in tags_by_forum: tags_by_forum[fid] = []
                    tags_by_forum[fid].append(dict(tr))
                
                for c in chan_list:
                    c["available_tags"] = tags_by_forum.get(c["id"], [])
            
            return chan_list

    def get_all_threads(self) -> List[Dict[str, Any]]:
        """Returns metadata for all threads in the backup."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM threads").fetchall()
            return [dict(r) for r in rows]

    def get_forum_tags(self, forum_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns forum tag definitions."""
        with self._lock:
            if forum_id:
                rows = self._conn.execute("SELECT * FROM forum_tags WHERE forum_id = ?", (str(forum_id),)).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM forum_tags").fetchall()
            return [dict(r) for r in rows]

    def get_threads_by_parent(self, parent_id: str) -> List[Dict[str, Any]]:
        """Returns all threads belonging to a parent channel."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM threads WHERE parent_id = ?", (str(parent_id),)).fetchall()
            return [dict(r) for r in rows]

    def get_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single thread's metadata."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM threads WHERE id = ?", (str(thread_id),)).fetchone()
            return dict(row) if row else None

    def get_all_users(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM users").fetchall()
            return [dict(r) for r in rows]

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE id = ?", (str(user_id),)).fetchone()
            if row:
                data = dict(row)
                if data.get("roles"):
                    data["roles"] = json.loads(data["roles"])
                return data
            return None

    def get_server_assets(self, asset_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns all server assets, optionally filtered by type."""
        with self._lock:
            if asset_type:
                rows = self._conn.execute("SELECT * FROM server_assets WHERE type = ?", (asset_type,)).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM server_assets").fetchall()
            return [dict(r) for r in rows]

    def get_all_media(self) -> Dict[str, Dict[str, Any]]:
        """Returns the entire media pool as a dictionary indexed by hash."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM media_pool").fetchall()
            return {r["hash"]: dict(r) for r in rows}

    def get_messages_paged(self, channel_id: str, limit: int = 100, offset: int = 0, after_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            query = "SELECT * FROM messages WHERE channel_id = ?"
            params = [str(channel_id)]
            
            if after_id:
                query += " AND id > ?"
                params.append(str(after_id))
            
            query += " ORDER BY id ASC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            rows = self._conn.execute(query, params).fetchall()
            msg_list = [dict(r) for r in rows]
            
            if msg_list:
                msg_ids = [m["id"] for m in msg_list]
                placeholders = ",".join(["?"] * len(msg_ids))
                
                att_rows = self._conn.execute(f"SELECT * FROM attachments WHERE message_id IN ({placeholders})", msg_ids).fetchall()
                atts_by_msg = {}
                for ar in att_rows:
                    mid = ar["message_id"]
                    if mid not in atts_by_msg: atts_by_msg[mid] = []
                    atts_by_msg[mid].append(dict(ar))
                
                emb_rows = self._conn.execute(f"SELECT * FROM embeds WHERE message_id IN ({placeholders})", msg_ids).fetchall()
                embs_by_msg = {}
                for er in emb_rows:
                    mid = er["message_id"]
                    if mid not in embs_by_msg: embs_by_msg[mid] = []
                    
                    e_dict = {
                        "title": er["title"],
                        "description": er["description"],
                        "url": er["url"],
                        "color": er["color"],
                        "timestamp": er["timestamp"],
                        "thumbnail": {"url": er["thumbnail_url"]} if er["thumbnail_url"] else None,
                        "image": {"url": er["image_url"]} if er["image_url"] else None,
                        "author": {
                            "name": er["author_name"],
                            "url": er["author_url"],
                            "icon_url": er["author_icon_url"]
                        } if er["author_name"] else None,
                        "footer": {
                            "text": er["footer_text"],
                            "icon_url": er["footer_icon_url"]
                        } if er["footer_text"] else None,
                        "fields": json.loads(er["fields"]) if er["fields"] else []
                    }
                    embs_by_msg[mid].append(e_dict)
                
                rea_rows = self._conn.execute(f"SELECT * FROM reactions WHERE message_id IN ({placeholders})", msg_ids).fetchall()
                reas_by_msg = {}
                for rr in rea_rows:
                    mid = rr["message_id"]
                    if mid not in reas_by_msg: reas_by_msg[mid] = []
                    reas_by_msg[mid].append(dict(rr))
                
                st_rows = self._conn.execute(f"SELECT * FROM message_stickers WHERE message_id IN ({placeholders})", msg_ids).fetchall()
                sts_by_msg = {}
                for sr in st_rows:
                    mid = sr["message_id"]
                    if mid not in sts_by_msg: sts_by_msg[mid] = []
                    sts_by_msg[mid].append(dict(sr))

                for m in msg_list:
                    m_id = m["id"]
                    m["attachments"] = atts_by_msg.get(m_id, [])
                    m["embeds"] = embs_by_msg.get(m_id, [])
                    m["reactions"] = reas_by_msg.get(m_id, [])
                    m["stickers"] = sts_by_msg.get(m_id, [])
            
            return msg_list

    def delete_channel_messages(self, channel_id: Union[str, int]):
        """Deletes all messages and related metadata for a specific channel and its threads."""
        cid = str(channel_id)
        with self._lock:
            # 1. Identify all channel IDs involved (parent + all threads)
            target_ids = [cid]
            thread_rows = self._conn.execute("SELECT id FROM threads WHERE parent_id = ?", (cid,)).fetchall()
            for tr in thread_rows:
                target_ids.append(tr["id"])
            
            placeholders_chans = ",".join(["?"] * len(target_ids))
            
            # 2. Get all message IDs for these channels
            msg_ids = [r["id"] for r in self._conn.execute(
                f"SELECT id FROM messages WHERE channel_id IN ({placeholders_chans})", target_ids
            ).fetchall()]
            
            if not msg_ids:
                return
            
            placeholders_msgs = ",".join(["?"] * len(msg_ids))
            
            # 3. Delete related metadata
            self._conn.execute(f"DELETE FROM attachments WHERE message_id IN ({placeholders_msgs})", msg_ids)
            self._conn.execute(f"DELETE FROM embeds WHERE message_id IN ({placeholders_msgs})", msg_ids)
            self._conn.execute(f"DELETE FROM reactions WHERE message_id IN ({placeholders_msgs})", msg_ids)
            self._conn.execute(f"DELETE FROM message_stickers WHERE message_id IN ({placeholders_msgs})", msg_ids)
            
            # 4. Delete messages
            self._conn.execute(f"DELETE FROM messages WHERE channel_id IN ({placeholders_chans})", target_ids)
            
            # 5. Delete thread metadata (optional but consistent)
            self._conn.execute(f"DELETE FROM threads WHERE parent_id = ?", (cid,))
            
            self._conn.commit()
            logger.info(f"Deleted messages, metadata, and threads for channel {cid}")

    def purge_unused_media(self, backup_root: Path) -> int:
        """Removes media files from disk and DB that are no longer referenced by any message."""
        purged_count = 0
        with self._lock:
            # 1. Find all hashes in use
            used_hashes = set()
            for r in self._conn.execute("SELECT DISTINCT local_hash FROM attachments WHERE local_hash IS NOT NULL").fetchall():
                used_hashes.add(r[0])
            for r in self._conn.execute("SELECT DISTINCT local_hash FROM message_stickers WHERE local_hash IS NOT NULL").fetchall():
                used_hashes.add(r[0])
            
            # 2. Get all hashes in pool
            all_media = self._conn.execute("SELECT hash, local_path FROM media_pool").fetchall()
            
            to_delete = []
            for m in all_media:
                m_hash = m["hash"]
                if m_hash not in used_hashes:
                    to_delete.append(dict(m))
            
            if not to_delete:
                return 0
            
            # 3. Delete from filesystem and DB
            for m in to_delete:
                try:
                    file_path = backup_root / m["local_path"]
                    if file_path.exists():
                        file_path.unlink()
                    
                    self._conn.execute("DELETE FROM media_pool WHERE hash = ?", (m["hash"],))
                    purged_count += 1
                except Exception as e:
                    logger.error(f"Failed to purge media {m['hash']}: {e}")
            
            self._conn.commit()
            logger.info(f"Purged {purged_count} unused media files")
        
        return purged_count

    def close(self):
        """Commits any pending writes and closes the connection."""
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except Exception:
                pass
