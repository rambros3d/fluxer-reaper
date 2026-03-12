import json
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

    def __init__(self, profile_name: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.profile_name = profile_name
        self.base_dir = Path(f"ReaperFiles-{self.profile_name}")
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
            # Locate the correct backup directory inside base_dir
            if not self.base_dir.exists():
                raise FileNotFoundError(f"Config directory {self.base_dir} not found.")

            target_dir = None
            # The exporter creates directories like DISCORD_BACKUP-<id> or <name>-<id>
            # We look for the first one that contains a server_profile
            for child in self.base_dir.iterdir():
                if child.is_dir() and (child / "server_profile" / "profile.json").exists():
                    target_dir = child
                    # If it's a recent backup, prefer it. We'll simply grab the first valid one if multiple exist.
                    # Usually there's only one valid backup per profile.
                    break
            
            if not target_dir:
                raise FileNotFoundError(f"No valid backup found in {self.base_dir}")
                
            self.profile_path = target_dir / "server_profile"
            self.backup_path = target_dir / "message_backup"

            # 1. Profile / Server Info
            server_name = "Unknown Server"
            server_id = "0"
            last_backup_str = "Never"
            profile_file = self.profile_path / "profile.json"
            if profile_file.exists():
                with open(profile_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    server_name = data.get("name", server_name)
                    server_id = data.get("id", server_id)
                    ts = data.get("last_backup")
                    if ts:
                        try:
                            dt = datetime.fromisoformat(ts)
                            last_backup_str = dt.strftime("%d %b, %Y - %H:%M")
                        except Exception:
                            last_backup_str = ts
            
            self.query_one("#bs_name", Label).update(f"{server_name}")
            self.query_one("#bs_id", Label).update(f"{server_id}")
            self.query_one("#bs_last_backup", Label).update(f"{last_backup_str}")

            # 2. Assets / Entities
            member_count = 0
            emoji_count = 0
            sticker_count = 0
            
            # Fetch members from users/user_info.json
            if self.backup_path:
                user_info_file = self.backup_path / "users" / "user_info.json"
                if user_info_file.exists():
                    try:
                        with open(user_info_file, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            if isinstance(data, list):
                                member_count = len(data)
                            elif isinstance(data, dict):
                                member_count = len(data)
                    except Exception:
                        pass
                        
            assets_file = self.profile_path / "assets.json"
            if assets_file.exists():
                with open(assets_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    emoji_count = len(data.get("emojis", []))
                    sticker_count = len(data.get("stickers", []))
                    
            role_count = 0
            roles_file = self.profile_path / "roles.json"
            if roles_file.exists():
                with open(roles_file, "r", encoding="utf-8") as f:
                    role_count = len(json.load(f))

            self.query_one("#bs_val_members", Label).update(f"{member_count}")
            self.query_one("#bs_val_roles", Label).update(f"{role_count}")
            self.query_one("#bs_val_emojis", Label).update(f"{emoji_count}")
            self.query_one("#bs_val_stickers", Label).update(f"{sticker_count}")

            # 3. Structure & Per-Channel Stats
            structure_file = self.profile_path / "structure.json"
            total_channels = 0
            backed_up_channels = 0
            
            total_msgs = 0
            total_threads = 0
            total_files = 0
            total_size = 0
            
            cat_nodes = [] # Collect category data

            if structure_file.exists():
                with open(structure_file, "r", encoding="utf-8") as f:
                    structure = json.load(f)
                    
                for cat in structure:
                    cat_name = cat.get("name", "Unknown Category").upper()
                    c_msgs = 0
                    c_thds = 0
                    c_files = 0
                    c_size = 0
                    
                    chan_list = []
                    
                    for chan in cat.get("channels", []):
                        total_channels += 1
                        ch_id = chan.get("id")
                        ch_name = f"# {chan.get('name', 'unknown')}"
                        
                        m_count = 0
                        t_count = 0
                        f_count = 0
                        s_bytes = 0
                        is_backed_up = False
                        
                        msg_file = self.backup_path / str(ch_id) / "messages.json"
                        if msg_file.exists():
                            is_backed_up = True
                            backed_up_channels += 1
                            try:
                                with open(msg_file, "r", encoding="utf-8") as mf:
                                    mdata = json.load(mf)
                                    m_count = mdata.get("messageCount", 0)
                                    t_count = mdata.get("threadCount", 0)
                                    f_count = mdata.get("numberOfAttachments", 0)
                                    s_bytes = mdata.get("totalAttachmentSizeBytes", 0)
                            except Exception:
                                pass
                                
                        chan_list.append({
                            "name": ch_name,
                            "msgs": m_count if is_backed_up else "NA",
                            "threads": t_count if is_backed_up else "NA",
                            "files": f_count if is_backed_up else "NA",
                            "size": s_bytes,
                            "is_backed_up": is_backed_up
                        })
                        c_msgs += m_count
                        c_thds += t_count
                        c_files += f_count
                        c_size += s_bytes
                        
                    total_msgs += c_msgs
                    total_threads += c_thds
                    total_files += c_files
                    total_size += c_size
                    
                    cat_nodes.append({
                        "name": cat_name,
                        "channels": chan_list,
                        "msgs": c_msgs,
                        "threads": c_thds,
                        "files": c_files,
                        "size": c_size
                    })

            # 4. Global Stats
            self.query_one("#bs_val_msgs", Label).update(f"{total_msgs}")
            self.query_one("#bs_val_threads", Label).update(f"{total_threads}")
            self.query_one("#bs_val_files", Label).update(f"{total_files}")
            self.query_one("#bs_val_size", Label).update(f"{self._format_size(total_size)}")
            
            self.query_one("#bs_val_coverage", Label).update(f"{backed_up_channels} / {total_channels}")

            # 5. Build Tree
            for cat in cat_nodes:
                cat_lbl = self._format_tree_row(f"{cat['name']}", cat['msgs'], cat['threads'], cat['files'], self._format_size(cat['size']))
                cat_lbl.stylize("bold yellow")
                node = self.stats_tree.root.add(cat_lbl, expand=True)
                
                for ch in cat["channels"]:
                    size_str = self._format_size(ch['size']) if ch['is_backed_up'] else "NA"
                    ch_lbl = self._format_tree_row(f"  {ch['name']}", ch['msgs'], ch['threads'], ch['files'], size_str)
                    
                    if ch['is_backed_up']:
                        ch_lbl.stylize("bold white")
                    else:
                        ch_lbl.stylize("dim white") # Textual 'dim' looks like a dull grey
                    
                    node.add_leaf(ch_lbl)

        except Exception as e:
            self.query_one("#bs_name", Label).update(f"[red]Error loading data[/red]")
            self.query_one("#bs_id", Label).update(f"[red]{e}[/red]")
