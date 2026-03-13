import json
import logging
from pathlib import Path
from datetime import datetime
import asyncio

from textual.app import ComposeResult
from textual.screen import Screen
from textual.containers import Container, Vertical, VerticalScroll, Horizontal
from textual.widgets import Button, Label, Rule, Tree
from textual import work
from rich.text import Text
from rich.style import Style

from src.ui.widgets import RamDisplay
from src.core.backup_reader import BackupReader, ChannelType

logger = logging.getLogger(__name__)

class BackupStatsScreen(Screen[None]):
    """Full-screen view for displaying detailed backup statistics."""

    DEFAULT_CSS = """
    BackupStatsScreen {
        background: $surface;
    }
    
    #bs_scroll_container {
        padding: 1 2;
    }

    #bs_header {
        height: auto;
        layout: horizontal;
        padding-bottom: 1;
        margin-bottom: 1;
        border-bottom: solid $accent;
    }
    
    #bs_header_left {
        width: 1fr;
        height: auto;
        layout: horizontal;
    }
    
    #bs_server_info {
        layout: vertical;
        height: auto;
        margin-left: 1;
    }
    
    .header_title {
        text-style: bold;
    }
    .header_subtitle {
        color: $text-muted;
    }
    
    #bs_header_right {
        width: auto;
        height: auto;
        layout: vertical;
        content-align: right middle;
    }
    
    .top_card_row {
        height: auto;
        layout: horizontal;
        margin-bottom: 1;
    }
    
    .stat_card {
        width: 1fr;
        height: 6;
        layout: vertical;
        border: solid $accent;
        padding: 1;
        margin-right: 1;
        background: $boost;
    }
    .stat_card:focus {
        border: solid $secondary;
    }
    
    .card_title {
        color: $text-muted;
        text-style: bold;
    }
    .card_value {
        text-style: bold;
    }
    
    .entity_row {
        height: auto;
        layout: horizontal;
        margin-bottom: 1;
    }
    
    .wide_card {
        width: 1fr;
        height: auto;
        layout: vertical;
        border: solid $accent;
        padding: 1;
        margin-right: 1;
        background: $boost;
    }
    
    .inner_card_row {
        height: auto;
        layout: horizontal;
        margin-top: 1;
    }
    
    .inner_stat {
        width: 1fr;
        height: 4;
        layout: vertical;
        border: solid $surface;
        background: $surface;
        padding: 0 1;
        content-align: center middle;
        margin-right: 1;
    }
    .inner_stat_value {
        text-style: bold;
        content-align: center middle;
        width: 100%;
    }
    .inner_stat_label {
        color: $text-muted;
        content-align: center middle;
        width: 100%;
    }
    
    #detailed_stats_container {
        height: auto;
        margin-top: 2;
    }
    
    #stats_tree {
        height: auto;
        border: solid $accent;
        background: $boost;
    }
    
    #bs_actions {
        height: auto;
        margin-top: 1;
        layout: horizontal;
    }
    #bs_actions Button {
        width: 100%;
    }
    """

    def __init__(self, profile_name: str, target_dir: Path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.profile_name = profile_name
        self.target_dir = target_dir
        self.profile_path = None
        self.backup_path = None

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="bs_scroll_container"):
            # Header
            with Horizontal(id="bs_header"):
                with Horizontal(id="bs_header_left"):
                    yield Label("🚀", id="bs_server_icon") # Fallback icon
                    with Vertical(id="bs_server_info"):
                        yield Label("Loading...", id="bs_name", classes="header_title")
                        yield Label("...", id="bs_id", classes="header_subtitle")
                with Vertical(id="bs_header_right"):
                    yield Label("LAST BACKUP", classes="header_subtitle", id="bs_last_backup_lbl")
                    yield Label("-", id="bs_last_backup", classes="header_title")

            # 4 Top Cards
            with Horizontal(classes="top_card_row"):
                with Vertical(classes="stat_card"):
                    yield Label("# MESSAGES", classes="card_title")
                    yield Label("-", id="bs_val_msgs", classes="card_value")
                with Vertical(classes="stat_card"):
                    yield Label("💭 THREADS", classes="card_title")
                    yield Label("-", id="bs_val_threads", classes="card_value")
                with Vertical(classes="stat_card"):
                    yield Label("📎 ATTACHMENTS", classes="card_title")
                    yield Label("-", id="bs_val_files", classes="card_value")
                with Vertical(classes="stat_card"):
                    yield Label("💾 ATTACHMENTS SIZE", classes="card_title")
                    yield Label("-", id="bs_val_size", classes="card_value")

            # 2 Main Secondary Cards (Combined)
            with Horizontal(classes="entity_row"):
                with Vertical(classes="wide_card"):
                    yield Label("👥 SERVER ENTITIES & COVERAGE", classes="card_title")
                    with Horizontal(classes="inner_card_row"):
                        with Vertical(classes="inner_stat"):
                            yield Label("-", id="bs_val_members", classes="inner_stat_value")
                            yield Label("TOTAL MEMBERS", classes="inner_stat_label")
                        with Vertical(classes="inner_stat"):
                            yield Label("-", id="bs_val_roles", classes="inner_stat_value")
                            yield Label("ROLES DEFINED", classes="inner_stat_label")
                        with Vertical(classes="inner_stat"):
                            yield Label("-", id="bs_val_emojis", classes="inner_stat_value")
                            yield Label("CUSTOM EMOJIS", classes="inner_stat_label")
                        with Vertical(classes="inner_stat"):
                            yield Label("-", id="bs_val_stickers", classes="inner_stat_value")
                            yield Label("CUSTOM STICKERS", classes="inner_stat_label")
                        with Vertical(classes="inner_stat"):
                            yield Label("-", id="bs_val_coverage", classes="inner_stat_value")
                            yield Label("CHANNEL BACKUP", classes="inner_stat_label")

            # Detailed Tree
            with Vertical(id="detailed_stats_container"):
                yield Label("DETAILED CHANNEL & CATEGORY STATISTICS", classes="card_title", id="tree_title")
                yield Tree("Categories", id="stats_tree")

            with Horizontal(id="bs_actions"):
                yield Button("Back", id="btn_back", variant="default")
        yield RamDisplay()

    def on_mount(self) -> None:
        self.stats_tree = self.query_one("#stats_tree", Tree)
        self.stats_tree.root.expand()
        self.stats_tree.show_root = False
        
        # Add a header row to the tree root, purely for visual columns
        header_text = self._format_tree_row("NAME", "MESSAGES", "THREADS", "FILES", "SIZE")
        header_text.stylize("bold")
        self.stats_tree.root.set_label(header_text)
        self.stats_tree.show_root = True

        self.load_data()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_back":
            self.dismiss()

    def _format_size(self, size_bytes: int) -> str:
        if size_bytes == 0:
            return "0 B"
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        if size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.2f} MB"
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    def _format_tree_row(self, name: str, msgs, threads, files, size) -> Text:
        """Pads and aligns columns for the tree view to simulate a table."""
        col_name = str(name)[:30].ljust(35)
        col_msg = str(msgs).rjust(12)
        col_thd = str(threads).rjust(12)
        col_file = str(files).rjust(12)
        col_size = str(size).rjust(15)
        return Text(f"{col_name}{col_msg}{col_thd}{col_file}{col_size}")

    @work(exclusive=True)
    async def load_data(self) -> None:
        try:
            # Initialize BackupReader
            reader = BackupReader(self.target_dir)
            await reader.start()
            
            if not reader.guild:
                raise FileNotFoundError(f"No valid backup found in {self.target_dir}")
            
            # 1. Profile / Server Info
            guild = reader.guild
            server_name = guild.name
            server_id = str(guild.id)
            
            # Get last backup info from guild profile
            profile = reader.db.get_guild_profile()
            last_backup_str = "Never"
            ts = profile.get("last_backup")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                    last_backup_str = dt.strftime("%d %b, %Y - %H:%M")
                except Exception:
                    last_backup_str = ts
            
            self.query_one("#bs_name", Label).update(f"{server_name}")
            self.query_one("#bs_id", Label).update(f"{server_id}")
            self.query_one("#bs_last_backup", Label).update(f"{last_backup_str}")

            # 2. Assets / Entities (using properties)
            member_count = len(reader.members)
            emoji_count = len(reader.emojis)
            sticker_count = len(reader.stickers)
            role_count = len(reader.roles)

            self.query_one("#bs_val_members", Label).update(f"{member_count}")
            self.query_one("#bs_val_roles", Label).update(f"{role_count}")
            self.query_one("#bs_val_emojis", Label).update(f"{emoji_count}")
            self.query_one("#bs_val_stickers", Label).update(f"{sticker_count}")

            # 3. Aggregate Stats from DB
            channel_stats = reader.db.get_stats_by_channel()
            
            total_msgs = sum(s["message_count"] for s in channel_stats.values())
            total_threads = sum(s["thread_count"] for s in channel_stats.values())
            total_files = sum(s["attachment_count"] for s in channel_stats.values())
            total_size = sum(s["total_size"] for s in channel_stats.values())
            
            backed_up_channel_ids = set(channel_stats.keys())
            
            # 4. Structure & Per-Channel Stats
            categories = reader.categories
            all_channels = reader.channels
            
            total_channels = len([c for c in all_channels if c.type != ChannelType.category])
            backed_up_channels = len([cid for cid in backed_up_channel_ids if any(c.id == cid for c in all_channels)])
            
            # Helper to map channels to categories
            cat_map = {cat.id: {"cat": cat, "chans": []} for cat in categories}
            cat_map[None] = {"cat": None, "chans": []} # Uncategorized
            
            for chan in all_channels:
                if chan.type == ChannelType.category: continue
                cid = chan.category_id
                if cid not in cat_map: cid = None
                cat_map[cid]["chans"].append(chan)

            # Global update
            self.query_one("#bs_val_msgs", Label).update(f"{total_msgs}")
            self.query_one("#bs_val_threads", Label).update(f"{total_threads}")
            self.query_one("#bs_val_files", Label).update(f"{total_files}")
            self.query_one("#bs_val_size", Label).update(f"{self._format_size(total_size)}")
            self.query_one("#bs_val_coverage", Label).update(f"{backed_up_channels} / {total_channels}")

            # 5. Build Tree
            for cat_id, info in cat_map.items():
                cat = info["cat"]
                chans = info["chans"]
                if not chans: continue
                
                cat_name = cat.name.upper() if cat else "UNCATEGORIZED"
                
                # Aggregate for category
                c_msgs = 0
                c_thds = 0
                c_files = 0
                c_size = 0
                
                chan_nodes_data = []
                for ch in chans:
                    stats = channel_stats.get(ch.id, {"message_count": 0, "thread_count": 0, "attachment_count": 0, "total_size": 0})
                    is_bu = ch.id in backed_up_channel_ids
                    
                    chan_nodes_data.append({
                        "name": f"# {ch.name}",
                        "msgs": stats["message_count"] if is_bu else "NA",
                        "threads": stats["thread_count"] if is_bu else "NA",
                        "files": stats["attachment_count"] if is_bu else "NA",
                        "size": stats["total_size"] if is_bu else 0,
                        "is_backed_up": is_bu
                    })
                    
                    c_msgs += stats["message_count"]
                    c_thds += stats["thread_count"]
                    c_files += stats["attachment_count"]
                    c_size += stats["total_size"]
                
                cat_lbl = self._format_tree_row(cat_name, c_msgs, c_thds, c_files, self._format_size(c_size))
                cat_lbl.stylize("bold yellow")
                node = self.stats_tree.root.add(cat_lbl, expand=True)
                
                for ch_data in chan_nodes_data:
                    size_str = self._format_size(ch_data['size']) if ch_data['is_backed_up'] else "NA"
                    ch_lbl = self._format_tree_row(f"  {ch_data['name']}", ch_data['msgs'], ch_data['threads'], ch_data['files'], size_str)
                    
                    if ch_data['is_backed_up']:
                        ch_lbl.stylize("bold white")
                    else:
                        ch_lbl.stylize("dim white")
                    
                    node.add_leaf(ch_lbl)

        except Exception as e:
            import traceback
            logger.exception("Failed to load backup stats")
            trace_str = traceback.format_exc()
            logger.error(f"Full Traceback: {trace_str}")
            self.query_one("#bs_name", Label).update(f"[red]Error loading data[/red]")
            self.query_one("#bs_id", Label).update(f"[red]{e}[/red]")
        finally:
            if 'reader' in locals():
                await reader.close()
