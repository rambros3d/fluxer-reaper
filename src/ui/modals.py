"""
Shared modals used by backup and shuttle operations.
"""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll, Container, Center
from textual.widgets import Button, Label, Input, ProgressBar, RichLog, Rule, RadioButton, LoadingIndicator, Header, Footer, RadioSet
from textual.screen import ModalScreen, Screen


import time
import logging
import asyncio

class UILogHandler(logging.Handler):
    """Custom logging handler to send logs to the Textual UI RichLog."""
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s', '%H:%M:%S'))

    def emit(self, record):
        try:
            msg = self.format(record)
            self.callback(msg)
        except Exception:
            pass

# ---------------------------------------------------------------------------
# ProgressScreen – unified full-screen progress / log / stats display
# ---------------------------------------------------------------------------

class ProgressScreen(Screen[None]):
    """Screen to display progress for any operation, with stats and logs."""
    
    DEFAULT_CSS = """
    ProgressScreen { align: center middle; }
    #prog_outer { width: 100%; height: 100%; align: center top; }
    #prog_dialog {
        width: 80%;
        height: 100%;
        layout: vertical;
        border: solid green;
        padding: 1 2;
        margin: 2 0;
        background: $surface;
    }
    #prog_header { height: 3; margin-bottom: 1; dock: top; align: left middle; }
    #prog_status { text-style: bold; width: 1fr; content-align: left middle; }
    #prog_loader { width: 10; height: 1; margin: 0 1; }
    #prog_timer { text-style: bold; width: 15; content-align: right middle; color: yellow; }
    
    #prog_stats { 
        height: auto; 
        layout: horizontal; 
        border: solid cyan; 
        padding: 1; 
        margin-bottom: 1; 
        display: none; 
    }
    .stat_label { width: 1fr; content-align: center middle; text-style: bold; }
    
    #prog_log { height: 1fr; margin-bottom: 1; border: solid $primary; }
    #live_log { height: 10; margin-bottom: 1; border: solid yellow; }
    
    #prog_bar_container { height: auto; width: 100%; align: center middle; }
    #prog_bar { margin-bottom: 1; width: 80%; }
    
    #info_container { height: auto; layout: vertical; border: solid $secondary; padding: 1; margin-bottom: 1; display: none; }
    .info_label { text-style: bold; content-align: center middle; width: 100%; color: $secondary-lighten-2; }
    
    #prog_actions { height: auto; margin-top: 1; dock: bottom; margin-bottom: 0; layout: vertical; }
    .action_row { height: auto; layout: horizontal; }
    .action_row Button { width: 1fr; margin: 0 1; }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="prog_outer"):
            with Container(id="prog_dialog"):
                with Horizontal(id="prog_header"):
                    yield Label("Operation Status...", id="prog_status")
                    yield LoadingIndicator(id="prog_loader")
                    yield Label("00:00", id="prog_timer")
                
                with Horizontal(id="prog_stats"):
                    yield Label("Messages: 0", id="stat_messages", classes="stat_label")
                    yield Label("Threads: 0", id="stat_threads", classes="stat_label")
                    yield Label("Files: 0", id="stat_files", classes="stat_label")


                with Vertical(id="info_container"):
                    yield Label("No previous migration data.", id="info_migration_status", classes="info_label")
                    yield Label("New items detected.", id="info_new_items", classes="info_label")
                    
                    # Progress bar moved inside info container
                    with Center(id="prog_bar_container"):
                        pb = ProgressBar(total=None, show_eta=False, id="prog_bar")
                        pb.display = False
                        yield pb

                yield RichLog(id="prog_log", highlight=True, markup=True)
                yield RichLog(id="live_log", highlight=True, markup=True)
                
                with Vertical(id="prog_actions"):
                    with Horizontal(classes="action_row", id="prog_actions_row1"):
                        yield Button("Start from First", id="btn_start_first", disabled=True, variant="primary")
                        yield Button("Continue Migration", id="btn_continue", disabled=True, variant="success")
                        yield Button("Start from ID", id="btn_start_id", disabled=True, variant="warning")
                    with Horizontal(classes="action_row", id="prog_actions_row2"):
                        yield Button("Back", id="btn_back", disabled=False)
                        yield Button("Main Menu", id="btn_main_menu", disabled=False)
                    with Horizontal(classes="action_row", id="prog_actions_cancel"):
                        yield Button("Cancel", id="btn_cancel", variant="error")
        yield Footer()

    def on_mount(self):
        self.confirm_future = None
        self.cancel_callback = None
        self.start_time = time.time()
        self.timer_event = self.set_interval(1.0, self.update_timer)
        
        # Hide all action rows by default (fetch phase = no buttons)
        try: self.query_one("#prog_actions_row1", Horizontal).display = False
        except Exception: pass
        try: self.query_one("#prog_actions_row2", Horizontal).display = False
        except Exception: pass
        try: self.query_one("#prog_actions_cancel", Horizontal).display = False
        except Exception: pass
        
        # Intercept Python logs and pipe to the #live_log
        self.log_handler = UILogHandler(self.write_live)
        # Attach to root logger
        logging.getLogger().addHandler(self.log_handler)
        # Also let's capture discord.py logs specifically if they aren't propagating
        logging.getLogger("discord").addHandler(self.log_handler)

    def on_unmount(self):
        # Detach log handler when UI is cleanly removed
        if hasattr(self, "log_handler"):
            logging.getLogger().removeHandler(self.log_handler)
            logging.getLogger("discord").removeHandler(self.log_handler)

    def update_timer(self):
        elapsed = int(time.time() - self.start_time)
        mins, secs = divmod(elapsed, 60)
        try:
            self.query_one("#prog_timer", Label).update(f"Elapsed: {mins:02d}:{secs:02d}")
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed):
        btn_id = event.button.id
        if self.confirm_future and not self.confirm_future.done():
            self.confirm_future.set_result(btn_id)
            return

        # If Cancel is pressed during operation, invoke callback and dismiss
        if btn_id == "btn_cancel":
            if self.cancel_callback:
                self.cancel_callback()
            if self.timer_event:
                self.timer_event.stop()
            self.dismiss("btn_cancel")
            return

        # If operation is done (report phase), just dismiss with the action
        if btn_id in ["btn_back", "btn_main_menu"]:
            if self.timer_event:
                self.timer_event.stop()
            self.dismiss(btn_id)

    def write(self, message: str):
        try:
            self.query_one("#prog_log", RichLog).write(message)
        except Exception:
            pass

    def write_live(self, message: str):
        try:
            self.query_one("#live_log", RichLog).write(message)
        except Exception:
            pass

    write_to_log = write

    def set_status(self, status: str):
        try:
            self.query_one("#prog_status", Label).update(status)
        except Exception:
            pass

    def set_progress(self, current: int, total: int):
        try:
            self.query_one("#prog_loader", LoadingIndicator).display = False
            bar = self.query_one("#prog_bar", ProgressBar)
            bar.display = True
            bar.update(total=total, progress=current)
        except Exception:
            pass

    def show_stats(self):
        try:
            self.query_one("#prog_stats", Horizontal).display = True
        except Exception:
            pass

    def update_stats(self, **kwargs):
        # kwargs can be messages, threads, files
        for key, val in kwargs.items():
            try:
                self.query_one(f"#stat_{key}", Label).update(f"{key.capitalize()}: {val}")
            except Exception:
                pass

    async def phase_wait_confirm(
        self, 
        show_continue: bool = False, 
        show_id: bool = True,
        btn_start_label: str = "Start from First",
        btn_continue_label: str = "Continue Migration",
        btn_id_label: str = "Start from ID"
    ):
        """Phase 2: Wait for user confirmation after analysis."""
        try: self.query_one("#prog_loader", LoadingIndicator).display = False
        except Exception: pass
        
        try: self.query_one("#prog_bar", ProgressBar).display = False
        except Exception: pass
        
        # Update button labels
        try: self.query_one("#btn_start_first", Button).label = btn_start_label
        except Exception: pass
        try: self.query_one("#btn_continue", Button).label = btn_continue_label
        except Exception: pass
        try: self.query_one("#btn_start_id", Button).label = btn_id_label
        except Exception: pass

        # Show confirmation buttons
        try: self.query_one("#prog_actions_row1", Horizontal).display = True
        except Exception: pass
        try: self.query_one("#prog_actions_row2", Horizontal).display = True
        except Exception: pass
        try: self.query_one("#prog_actions_cancel", Horizontal).display = False
        except Exception: pass
        
        try: self.query_one("#btn_start_first", Button).disabled = False
        except Exception: pass
        
        try: 
            btn_id = self.query_one("#btn_start_id", Button)
            btn_id.disabled = not show_id
            btn_id.display = show_id
        except Exception: pass
        
        try:
            if show_continue:
                self.query_one("#btn_continue", Button).disabled = False
                self.query_one("#btn_continue", Button).display = True
            else:
                self.query_one("#btn_continue", Button).display = False
        except Exception:
            pass
            
        loop = asyncio.get_running_loop()
        self.confirm_future = loop.create_future()
        
        # Wait until the user clicks one of the buttons
        choice = await self.confirm_future
        return choice

    def phase_progress(self):
        """Phase 3: The actual operation begins. Only Cancel visible."""
        try: self.query_one("#prog_actions_row1", Horizontal).display = False
        except Exception: pass
        
        try: self.query_one("#prog_actions_row2", Horizontal).display = False
        except Exception: pass
        
        # Show Cancel button
        try: self.query_one("#prog_actions_cancel", Horizontal).display = True
        except Exception: pass
        
        try: self.query_one("#prog_loader", LoadingIndicator).display = True
        except Exception: pass
        
        try: self.query_one("#info_migration_status", Label).display = False
        except Exception: pass
        
        try: self.query_one("#info_new_items", Label).display = False
        except Exception: pass

    def phase_report(self, operation_name: str, status: str = "complete"):
        """Phase 4: Operation is done. Show Back + Main Menu.
        
        status can be: 'complete', 'stopped', 'error'
        """
        try:
            if self.timer_event:
                self.timer_event.stop()
        except Exception:
            pass
        
        # Format status title with color
        status_map = {
            "complete": ("[bold green]", "Complete"),
            "stopped":  ("[bold yellow]", "Stopped"),
            "error":    ("[bold red]", "Error"),
        }
        color, label = status_map.get(status, ("[bold]", status.capitalize()))
        self.set_status(f"{color}{operation_name} {label}![/{color.split(']')[0].lstrip('[')}]")
        
        # Hide loader
        try: self.query_one("#prog_loader", LoadingIndicator).display = False
        except Exception: pass
        
        # Hide progress bar (no need to show 100% bar)
        try: self.query_one("#prog_bar", ProgressBar).display = False
        except Exception: pass
        
        # Hide Cancel, show Back + Main Menu
        try: self.query_one("#prog_actions_cancel", Horizontal).display = False
        except Exception: pass
        try: self.query_one("#prog_actions_row1", Horizontal).display = False
        except Exception: pass
        try: self.query_one("#prog_actions_row2", Horizontal).display = True
        except Exception: pass
        
        try:
            back_btn = self.query_one("#btn_back", Button)
            back_btn.disabled = False
        except Exception:
            pass

    def show_info(self, migration_status: str, items_status: str):
        try:
            info = self.query_one("#info_container", Vertical)
            info.display = True
            self.query_one("#info_migration_status", Label).update(migration_status)
            self.query_one("#info_new_items", Label).update(items_status)
        except Exception:
            pass

    def allow_close(self):
        """Legacy fallback: show Back + Main Menu buttons for early exit."""
        self.phase_report("Operation", "error")

# ---------------------------------------------------------------------------
# SubMenuModal – generic labelled-button list
# ---------------------------------------------------------------------------

class SubMenuModal(ModalScreen[str]):
    """A generic sub-menu modal that presents a list of labelled buttons."""
    DEFAULT_CSS = "SubMenuModal { align: center middle; }"

    def __init__(self, title: str, options: list[tuple[str, str, str]]):
        """options: list of (button_id, label, variant)"""
        super().__init__()
        self._title = title
        self._options = options

    def compose(self) -> ComposeResult:
        with Vertical(id="submenu_dialog"):
            yield Label(self._title, id="submenu_title")
            for btn_id, label, variant in self._options:
                yield Button(label, id=btn_id, variant=variant)
            yield Rule()
            yield Button("Cancel", id="btn_cancel_sub")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn_cancel_sub":
            self.dismiss(None)
        else:
            self.dismiss(event.button.id)


# ---------------------------------------------------------------------------
# OptionSelectModal – radio-button selection list
# ---------------------------------------------------------------------------

class OptionSelectModal(ModalScreen[list[str]]):
    """A modal that presents a list of options using RadioButtons (for multi-select) and a Proceed button."""
    DEFAULT_CSS = """
    OptionSelectModal { align: center middle; }
    #opt_dialog {
        width: 60;
        height: 80%;
        border: solid green;
        background: $surface;
        padding: 1 2;
    }
    #opt_title { text-style: bold; margin-bottom: 1; text-align: center; width: 100%; }
    #opt_scroll { margin-bottom: 1; height: 1fr; overflow-y: auto; }
    #opt_buttons { height: auto; }
    #opt_buttons Button { width: 1fr; margin: 0 1; }
    #opt_batch_buttons { height: auto; margin-bottom: 1; }
    #opt_batch_buttons Button { width: 1fr; margin: 0 1; }
    RadioButton { background: transparent; }
    RadioButton:focus { background: $accent 20%; }

    """

    def __init__(self, title: str, options: list[tuple[str, str]]):
        """options: list of (option_id, label)"""
        super().__init__()
        self._title = title
        self._options = options

    def compose(self) -> ComposeResult:
        with Vertical(id="opt_dialog"):
            yield Label(self._title, id="opt_title")
            with Horizontal(id="opt_batch_buttons"):
                yield Button("Select All", id="btn_opt_all", flat=True)
                yield Button("Deselect All", id="btn_opt_none", flat=True)
            
            with Vertical(id="opt_scroll"):
                for opt_id, label in self._options:
                    yield RadioButton(label, id=f"opt_{opt_id}")
            
            yield Rule()
            with Horizontal(id="opt_buttons"):
                yield Button("Proceed", variant="success", id="btn_opt_ok")
                yield Button("Back", id="btn_opt_back")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn_opt_back":
            self.dismiss(None)
        elif event.button.id == "btn_opt_ok":
            selected = []
            for rb in self.query(RadioButton):
                if rb.value:
                    selected.append(rb.id.split("_", 1)[1])
            if selected:
                self.dismiss(selected)
            else:
                self.app.notify("Please select at least one option.", severity="warning")
        elif event.button.id == "btn_opt_all":
            for rb in self.query(RadioButton):
                rb.value = True
        elif event.button.id == "btn_opt_none":
            for rb in self.query(RadioButton):
                rb.value = False


# ---------------------------------------------------------------------------
# ChannelPickerModal – single-channel selection (shuttle)
# ---------------------------------------------------------------------------

class ChannelPickerScreen(Screen[tuple]):
    """Screen listing Discord channels (left) and Target platforms channels (right) for dual selection."""

    DEFAULT_CSS = """
    ChannelPickerScreen { align: center middle; }
    #cp_outer { width: 100%; height: 100%; align: center top; }
    #chanpick_dialog {
        width: 80%;
        height: 100%;
        layout: vertical;
        border: solid green;
        padding: 1 2;
        margin: 2 0;
        background: $surface;
    }
    #chanpick_title { text-style: bold; margin-bottom: 1; content-align: center middle; width: 100%; }
    #chanpick_split {
        height: 1fr;
        layout: horizontal;
    }
    .split_pane {
        width: 1fr;
        height: 100%;
        border: solid $primary;
        margin: 0 1;
        padding: 0 1;
    }
    .pane_title { text-style: bold; margin-bottom: 1; color: cyan; margin-left: 1; }
    .category_header {
        margin-top: 1;
        background: $primary 10%;
        text-style: bold;
        padding-left: 1;
    }
    #chanpick_buttons { height: auto; margin-top: 1; dock: bottom; margin-bottom: 0; }
    #chanpick_buttons Button { width: 1fr; margin: 0 1; }
    """

    def __init__(self, src_channels: list, src_cat_map: dict, tgt_channels: list, tgt_cat_map: dict, tgt_name: str = "Fluxer"):
        super().__init__()
        self.src_channels = src_channels
        self.src_cat_map = src_cat_map
        self.tgt_channels = tgt_channels
        self.tgt_cat_map = tgt_cat_map
        self.tgt_name = tgt_name

    def _render_pane(self, channels, categories, pane_id, prefix):
        cat_grouped: dict[int | None, list] = {}
        for c in channels:
            cat_id = getattr(c, "category_id", None) if not isinstance(c, dict) else c.get("parent_id")
            cat_grouped.setdefault(cat_id, []).append(c)

        with VerticalScroll(classes="split_pane", id=pane_id):
            with RadioSet(id=f"{prefix}_radioset"):
                for cat_id in sorted(cat_grouped, key=lambda k: categories.get(k, "") if k else ""):
                    if cat_id is not None and cat_id in categories:
                        yield Label(f"[cyan]{categories[cat_id]}[/cyan]", classes="category_header")
                    for c in cat_grouped[cat_id]:
                        if isinstance(c, dict):
                            name = c.get("name", "Unnamed")
                            cid = c.get("id")
                        else:
                            name = c.name
                            cid = c.id
                        yield RadioButton(name, value=False, id=f"{prefix}_{cid}")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="cp_outer"):
            with Container(id="chanpick_dialog"):
                yield Label("Select Source and Target Channels", id="chanpick_title")
                with Horizontal(id="chanpick_split"):
                    with Vertical():
                        yield Label("Source: Discord", classes="pane_title")
                        yield from self._render_pane(self.src_channels, self.src_cat_map, "pane_src", "src")
                    
                    with Vertical():
                        yield Label(f"Target: {self.tgt_name}", classes="pane_title")
                        yield from self._render_pane(self.tgt_channels, self.tgt_cat_map, "pane_tgt", "tgt")

                yield Rule()
                with Horizontal(id="chanpick_buttons"):
                    yield Button("Select", variant="success", id="btn_pick_ok")
                    yield Button("Back", id="btn_pick_back")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn_pick_back":
            self.dismiss(None)
        elif event.button.id == "btn_pick_ok":
            src_val = None
            tgt_val = None
            
            src_rset = self.query_one("#src_radioset", RadioSet)
            if src_rset.pressed_button:
                 src_val = src_rset.pressed_button.id.split("_", 1)[1]
            
            tgt_rset = self.query_one("#tgt_radioset", RadioSet)
            if tgt_rset.pressed_button:
                 tgt_val = tgt_rset.pressed_button.id.split("_", 1)[1]
                 
            if src_val and tgt_val:
                 self.dismiss((int(src_val), tgt_val)) # Source is guaranteed Discord ID (int), Target could be UUID/string
            else:
                 self.notify("Please select one channel from both lists.", severity="warning")


# ---------------------------------------------------------------------------
# ChannelSelectModal – multi-channel selection with sync/force (backup)
# ---------------------------------------------------------------------------

class ChannelSelectScreen(Screen[dict]):
    """Screen for selecting channels using a simple checkbox list."""

    DEFAULT_CSS = """
    ChannelSelectScreen { align: center middle; }
    #cs_outer { width: 100%; height: 100%; align: center top; }
    #channel_dialog {
        width: 80%;
        height: 100%;
        layout: vertical;
        border: solid green;
        padding: 1 2;
        margin: 2 0;
        background: $surface;
    }
    #chan_title { text-style: bold; margin-bottom: 1; }
    #channel_list_scroll {
        height: 1fr;
        border: solid $primary;
        margin-bottom: 1;
        padding: 0 1;
    }
    .category_header {
        margin-top: 1;
        background: $primary 10%;
        text-style: bold;
        padding-left: 1;
    }
    .label_warning {
        padding-top: 1;
        padding-right: 1;
        color: yellow;
    }
    #select_all_buttons { height: auto; margin-bottom: 1; }
    #select_all_buttons Button { width: auto; margin-right: 1; }
    #confirm_buttons { height: auto; margin-top: 1; dock: bottom; margin-bottom: 0; }
    #confirm_buttons Button { width: 1fr; margin: 0 1; }
    """

    def __init__(self, channels: list, categories: dict, backed_up_ids: set, any_found: bool):
        super().__init__()
        # Group channels by category
        self.channels_by_category = {}
        for c in channels:
            cat_id = getattr(c, 'category_id', None)
            if cat_id not in self.channels_by_category:
                self.channels_by_category[cat_id] = []
            self.channels_by_category[cat_id].append(c)

        self.categories = categories  # id -> name
        self.backed_up_ids = backed_up_ids
        self.any_found = any_found

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="cs_outer"):
            with Container(id="channel_dialog"):
                yield Label("Select Channels to Backup", id="chan_title")

                with VerticalScroll(id="channel_list_scroll"):
                    cat_ids = sorted(
                        [k for k in self.channels_by_category.keys() if k is not None],
                        key=lambda k: self.categories.get(k, ""),
                    )

                    # No category channels
                    if None in self.channels_by_category:
                        for c in sorted(self.channels_by_category[None], key=lambda x: x.position if hasattr(x, 'position') else 0):
                            label = f"{c.name}"
                            color = "green" if c.id in self.backed_up_ids else "white"
                            yield RadioButton(f"[{color}]{label}[/]", value=False, id=f"chan_{c.id}")

                    for cat_id in cat_ids:
                        cat_name = self.categories.get(cat_id, "Unknown Category")
                        yield Label(f"[cyan]{cat_name}[/cyan]", classes="category_header")
                        for c in sorted(self.channels_by_category[cat_id], key=lambda x: x.position if hasattr(x, 'position') else 0):
                            label = f"{c.name}"
                            color = "green" if c.id in self.backed_up_ids else "white"
                            yield RadioButton(f"[{color}]{label}[/]", value=False, id=f"chan_{c.id}")

                with Horizontal(id="select_all_buttons"):
                    yield Button("Select All", id="btn_all")
                    yield Button("Deselect All", id="btn_none")

                yield Rule()
                with Horizontal(id="confirm_buttons"):
                    if self.any_found:
                        yield Label("Existing backups found:", classes="label_warning")
                        yield Button("Sync", variant="success", id="btn_sync")
                        yield Button("Force Overwrite", variant="error", id="btn_force")
                    else:
                        yield Button("Backup", variant="success", id="btn_backup")
                    yield Button("Back", id="btn_cancel_chan")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_all":
            for cb in self.query(RadioButton):
                cb.value = True
        elif event.button.id == "btn_none":
            for cb in self.query(RadioButton):
                cb.value = False
        elif event.button.id in ["btn_sync", "btn_force", "btn_backup"]:
            selected = []
            for cb in self.query(RadioButton):
                if cb.value and cb.id and cb.id.startswith("chan_"):
                    chan_id = int(cb.id.split("_")[1])
                    selected.append(chan_id)
            if not selected:
                return

            force = event.button.id == "btn_force"
            self.dismiss({"channels": selected, "force": force})
        elif event.button.id == "btn_cancel_chan":
            self.dismiss(None)
