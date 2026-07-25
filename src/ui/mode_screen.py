"""
ModeScreen – the single unified screen used for all tool_mode values.
Shows the appropriate pane(s) based on the mode:
  - backup_only:      Backup pane only
  - direct_transfer:  Migrate pane only
  - backup_transfer:  Both panes with a toggle button to switch between them
"""

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Vertical, Horizontal, VerticalScroll
from textual.widgets import Header, Footer, Button, ContentSwitcher, Rule
from textual.screen import Screen

from src.core.configuration import load_config, FLUXER_SOURCE_DISABLE_BACKUP_MODES
from src.ui.shuttle_ops import OperationPane
from src.ui.widgets import RamDisplay, Footnote


class ModeScreen(Screen):
    """Unified mode screen — adapts content based on tool_mode."""

    CSS = """
    #main_outer {
        align: center top;
        height: 100%;
    }
    #main_container {
        width: 80%;
        height: 100%;
        layout: vertical;
        min-height: 20;
        border: solid green;
        padding: 1 2;
        margin: 2 0;
    }
    #switcher, #pane_backup, #pane_migrate {
        height: 1fr;
    }
    #mode_footer {
        height: auto;
        dock: bottom;
    }
    #bottom_actions {
        height: auto;
        margin: 1 1 0 1;
    }
    #bottom_actions Button, #btn_switch {
        width: 1fr;
        margin: 0 1;
    }
    #footer_rule { margin: 0; }

    ModalScreen {
        align: center middle;
    }
    #progress_dialog, #shuttle_config_dialog, #platform_select_dialog, #submenu_dialog, #confirm_dialog, #chanpick_dialog, #channel_dialog {
        width: 80%;
        height: 100%;
        layout: vertical;
        border: solid green;
        padding: 1 2;
        margin: 2 0;
        background: $surface;
    }
    #progress_status { text-style: bold; margin-bottom: 1; }
    #progress_bar { margin-bottom: 1; }
    #progress_loader { margin-bottom: 1; }
    #progress_log { height: 1fr; margin-bottom: 1; border: solid $primary; }
    #chanpick_scroll {
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
    #config_title, #platform_title, #submenu_title, #confirm_msg, #chanpick_title, #chan_title {
        text-style: bold; margin-bottom: 1;
    }
    #config_buttons, #confirm_buttons, #chanpick_buttons {
        height: auto; margin-top: 0;
    }
    #config_buttons Button, #confirm_buttons Button, #chanpick_buttons Button {
        width: 1fr; margin: 0 1;
    }
    #channel_list_scroll {
        height: 1fr;
        border: solid $primary;
        margin-bottom: 1;
        padding: 0 1;
    }
    #select_all_buttons { height: auto; margin-bottom: 1; }
    #select_all_buttons Button { width: auto; margin-right: 1; }

    RadioButton:focus { background: transparent; border: none; }
    RadioButton > .radio-button--label { padding: 0 1; }
    RadioButton:focus > .radio-button--label { background: transparent; text-style: none; }
    """

    BINDINGS = [
        ("q", "app.exit", "Quit"),
    ]

    def __init__(self, cfg_name: str, cfg_path: Path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cfg_name = cfg_name
        self.config_path = cfg_path
        self.config = load_config(cfg_path)
        self._showing_backup = True  # track which pane is visible in combined mode

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        mode = self.config.tool_mode or "backup_only"
        # Fluxer source only supports direct transfer until backup is implemented
        if FLUXER_SOURCE_DISABLE_BACKUP_MODES and getattr(self.config, "source_platform", "discord") == "fluxer":
            mode = "direct_transfer"

        with Container(id="main_outer"):
            with Container(id="main_container"):
                if mode == "backup_only":
                    yield OperationPane(self.cfg_name, self.config_path, view_mode="backup", id="pane_backup")
                elif mode == "direct_transfer":
                    yield OperationPane(self.cfg_name, self.config_path, view_mode="shuttle", id="pane_migrate")
                else:  # backup_transfer
                    with ContentSwitcher(initial="pane_backup", id="switcher"):
                        yield OperationPane(self.cfg_name, self.config_path, view_mode="backup", id="pane_backup")
                        yield OperationPane(self.cfg_name, self.config_path, view_mode="shuttle", id="pane_migrate")
                
                with Vertical(id="mode_footer"):
                    yield Rule(id="footer_rule")
                    with Horizontal(id="bottom_actions"):
                        if mode == "backup_transfer":
                            yield Button("Switch to Migrate ⇄", id="btn_switch", variant="primary", tooltip="Switch between\nBackup & Migrate operations")
                        yield Button("Configuration", id="btn_config", variant="success", tooltip="Change Bot tokens\nand Reaper Mode")
                        yield Button("Back to Config Selection", id="btn_back", variant="warning", tooltip="Return to the configuration selection screen")
                        yield Button("Exit", id="btn_exit", variant="error")

        yield Footer()
        yield Footnote()
        yield RamDisplay()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn_exit":
            self.app.exit()
        elif bid == "btn_back":
            self.app.pop_screen()
            from src.ui.main_app import ConfigSelectionScreen
            self.app.push_screen(ConfigSelectionScreen())
        elif bid == "btn_config":
            from src.ui.main_app import ConfigScreen
            def reload_screen(saved: bool = False):
                if saved:
                    new_cfg = load_config(self.config_path)
                    if new_cfg.tool_mode != self.config.tool_mode:
                        self.app.pop_screen()
                        from src.ui.mode_screen import ModeScreen
                        self.app.push_screen(ModeScreen(self.cfg_name, self.config_path))
                    else:
                        self.config = new_cfg
                        for pane in self.query(OperationPane):
                            pane.reload_config()
            self.app.push_screen(ConfigScreen(self.cfg_name, self.config_path), reload_screen)
        elif bid == "btn_switch":
            self._toggle_pane()

    def _toggle_pane(self) -> None:
        """Switch between Backup and Migrate panes in backup_transfer mode."""
        switcher = self.query_one("#switcher", ContentSwitcher)
        btn = self.query_one("#btn_switch", Button)
        if self._showing_backup:
            switcher.current = "pane_migrate"
            btn.label = "Switch to Backup ⇄"
            self._showing_backup = False
        else:
            switcher.current = "pane_backup"
            btn.label = "Switch to Migrate ⇄"
            self._showing_backup = True
