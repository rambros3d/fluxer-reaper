import re
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll, Center
from textual.widgets import (
    Header, Footer, Button, Label, Input,
    Rule, RadioButton, RadioSet, Select, Markdown, Switch
)
from textual.screen import Screen, ModalScreen

from src.core.configuration import (
    get_available_configs, create_new_config, load_config, save_config,
    delete_config, clone_config, scan_config_data,
    FLUXER_SOURCE_DISABLE_BACKUP_MODES,
)
from src.ui.widgets import RamDisplay, Footnote, ClipboardInput
from src.core.utils import get_app_version


# ──────────────────────────────────────────────────────────────────────────────
# Modals
# ──────────────────────────────────────────────────────────────────────────────

class FirstInfoModal(ModalScreen[str]):
    """Modal to display first-time launch info."""
    
    DEFAULT_CSS = """
    FirstInfoModal { align: center middle; }
    #first_info_dialog {
        width: 80%; height: auto; max-height: 80%;
        border: thick $background 80%; background: $surface; padding: 1 2;
    }
    #btn_yeah { width: 100%; margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="first_info_dialog"):
            try:
                import sys
                import os
                
                # Handle PyInstaller path resolution
                if getattr(sys, 'frozen', False):
                    base_path = sys._MEIPASS
                else:
                    base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
                    
                file_path = os.path.join(base_path, "src", "first-info.md")
                
                with open(file_path, "r", encoding="utf-8") as f:
                    md_text = f.read()
            except Exception as e:
                md_text = f"# Welcome to the Reaper\n\nInfo text missing. ({e})"
            yield Markdown(md_text)
            yield Button("Get Started", variant="success", id="btn_yeah")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_yeah":
            self.dismiss("start_new")
            
class NewConfigModal(ModalScreen[str]):
    """Modal to enter a name for a new configuration."""

    DEFAULT_CSS = """
    NewConfigModal { align: center middle; }
    #new_config_dialog {
        width: 50; height: auto;
        border: thick $background 80%; background: $surface; padding: 1 2;
    }
    #new_config_title { text-style: bold; margin-bottom: 1; padding-bottom: 1; border-bottom: solid $primary; }
    #new_config_source { text-style: bold; margin-top: 1; padding-top: 1; border-bottom: solid $primary; }
    #new_config_buttons { height: auto; margin-top: 1; }
    #new_config_buttons Button { width: 1fr; margin: 0 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="new_config_dialog"):
            yield Label("Enter new configuration name:", id="new_config_title")
            yield Input(placeholder="e.g. MyServer", id="new_config_input", tooltip="Enter a unique name for this config")
            yield Label("Choose a SOURCE PLATFORM. Discord is \nthe default.", id="new_config_source")
            with RadioSet(id="new_config_platform"):
                yield RadioButton("Discord", id="src_discord", value=True)   # default
                yield RadioButton("Fluxer", id="src_fluxer")
            with Horizontal(id="new_config_buttons"):
                yield Button("Create", variant="success", id="btn_create", tooltip="Create config and launch setup")
                yield Button("Cancel", variant="primary", id="btn_cancel")

    def _get_sanitized_name(self) -> str:
        raw = self.query_one("#new_config_input", Input).value.strip()
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", raw).strip("-")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_create":
            name = self._get_sanitized_name()
            if name:
                # Get selected platform
                source_platform = "discord"  # default
                for rb in self.query("#new_config_platform RadioButton"):
                    if rb.value:
                        if rb.id == "src_discord":
                            source_platform = "discord"
                        elif rb.id == "src_fluxer":
                            source_platform = "fluxer"
                        break
                self.dismiss((name, source_platform))
        elif event.button.id == "btn_cancel":
            self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key == "enter":
            name = self._get_sanitized_name()
            if name:
                self.dismiss(name)
        elif event.key == "escape":
            self.dismiss(None)


# ──────────────────────────────────────────────────────────────────────────────
# Screen 1: pick (or create) a ReaperFiles-* config
# ──────────────────────────────────────────────────────────────────────────────

class DeleteConfigModal(ModalScreen[bool]):
    """Confirmation modal before deleting a configuration."""

    DEFAULT_CSS = """
    DeleteConfigModal { align: center middle; }
    #delete_dialog {
        width: 60; height: auto;
        border: thick $error 80%; background: $surface; padding: 1 2;
    }
    #delete_title { text-style: bold; color: $error; margin-bottom: 1; }
    #delete_warning {
        margin-bottom: 1; color: $text-warning;
    }
    #delete_buttons { height: auto; margin-top: 1; }
    #delete_buttons Button { width: 1fr; margin: 0 1; }
    """

    def __init__(self, cfg_name: str, has_data: bool = False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cfg_name = cfg_name
        self.has_data = has_data

    def compose(self) -> ComposeResult:
        btn_label = "Begin Delete" if self.has_data else "Delete Config"
        with Vertical(id="delete_dialog"):
            yield Label(f"Delete Configuration?", id="delete_title")
            yield Label(
                f"Are you sure you want to delete the configuration\n"
                f"[bold]{self.cfg_name}[/bold]?\n\n"
                f"This will permanently remove the folder\n"
                f"[bold]ReaperFiles-{self.cfg_name}[/bold]\n"
                f"and all its contents.\n\n"
                f"This action cannot be undone.",
                id="delete_warning"
            )
            with Horizontal(id="delete_buttons"):
                yield Button(btn_label, variant="error", id="btn_delete_confirm")
                yield Button("Cancel", variant="primary", id="btn_delete_cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_delete_confirm":
            self.dismiss(True)
        elif event.button.id == "btn_delete_cancel":
            self.dismiss(False)


class DeleteDataModal(ModalScreen[str]):
    """Second-step confirmation — config has db/backup files. What to do with them?"""

    DEFAULT_CSS = """
    DeleteDataModal { align: center middle; }
    #deletedata_dialog {
        width: 62; height: auto;
        border: thick $warning 80%; background: $surface; padding: 1 2;
    }
    #deletedata_title { text-style: bold; color: $warning; margin-bottom: 1; }
    #deletedata_files { margin-bottom: 1; color: $text-muted; }
    #deletedata_buttons1, #deletedata_buttons2, #deletedata_buttons3 { height: auto; margin-top: 1; }
    #deletedata_buttons1 Button, #deletedata_buttons2 Button, #deletedata_buttons3 Button { width: 1fr; margin: 0 1; }
    """

    def __init__(self, cfg_name: str, data_info: dict, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cfg_name = cfg_name
        self.data_info = data_info

    def compose(self) -> ComposeResult:
        lines = []
        if "db" in self.data_info:
            lines.append(f"• Database files: {', '.join(self.data_info['db'])}")
        if "backups" in self.data_info:
            lines.append(f"• Backup folders: {', '.join(self.data_info['backups'])}")

        with Vertical(id="deletedata_dialog"):
            yield Label("Extra Data Found!", id="deletedata_title")
            yield Label(
                f"The configuration [bold]{self.cfg_name}[/bold] also contains\n"
                f"the following data that will be [bold]permanently deleted[/bold]:\n\n"
                + "\n".join(lines) + "\n\n"
                f"You can open the folder to back up files yourself,\n"
                f"then choose Delete Everything when ready.",
                id="deletedata_files"
            )
            with Horizontal(id="deletedata_buttons1"):
                yield Button("Open Folder", variant="primary", id="btn_data_open",
                             tooltip="Open the config folder in your file manager to back up files")
            with Horizontal(id="deletedata_buttons2"):
                yield Button("Delete Everything", variant="error", id="btn_data_delete_all",
                             tooltip="Delete the config AND all db/backup files permanently")
            with Horizontal(id="deletedata_buttons3"):
                yield Button("Cancel", variant="primary", id="btn_data_cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_data_open":
            self.dismiss("open")
        elif event.button.id == "btn_data_delete_all":
            self.dismiss("delete_all")
        elif event.button.id == "btn_data_cancel":
            self.dismiss("cancel")


class OverwriteConfigModal(ModalScreen[bool]):
    """Confirmation modal before overwriting an existing configuration during clone."""

    DEFAULT_CSS = """
    OverwriteConfigModal { align: center middle; }
    #overwrite_dialog {
        width: 56; height: auto;
        border: thick $warning 80%; background: $surface; padding: 1 2;
    }
    #overwrite_title { text-style: bold; color: $warning; margin-bottom: 1; }
    #overwrite_warning { margin-bottom: 1; color: $text-warning; }
    #overwrite_buttons { height: auto; margin-top: 1; }
    #overwrite_buttons Button { width: 1fr; margin: 0 1; }
    """

    def __init__(self, new_name: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.new_name = new_name

    def compose(self) -> ComposeResult:
        with Vertical(id="overwrite_dialog"):
            yield Label("Configuration Already Exists!", id="overwrite_title")
            yield Label(
                f"A configuration named [bold]{self.new_name}[/bold] already exists.\n\n"
                f"Cloning over it will [bold]overwrite[/bold] its\n"
                f"[bold]ReaperFiles-{self.new_name}/reaper_config.yaml[/bold].\n\n"
                f"Existing database and backup files in the folder \nare kept.\n\n"
                f"Do you want to overwrite it?",
                id="overwrite_warning"
            )
            with Horizontal(id="overwrite_buttons"):
                yield Button("Overwrite", variant="warning", id="btn_overwrite_confirm")
                yield Button("Cancel", variant="primary", id="btn_overwrite_cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_overwrite_confirm":
            self.dismiss(True)
        elif event.button.id == "btn_overwrite_cancel":
            self.dismiss(False)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(False)


class CloneConfigModal(ModalScreen[tuple | None]):
    """Modal to clone a configuration with a new name and optionally a new source platform."""

    DEFAULT_CSS = """
    CloneConfigModal { align: center middle; }
    #clone_dialog {
        width: 50; height: auto;
        border: thick $primary 80%; background: $surface; padding: 1 2;
    }
    #clone_title { text-style: bold; margin-bottom: 1; padding-bottom: 1; border-bottom: solid $primary; }
    #clone_source_info { margin-bottom: 1; color: $text-muted; }
    #clone_source_select { text-style: bold; margin-top: 1; padding-top: 1; border-bottom: solid $primary; }
    #clone_buttons { height: auto; margin-top: 1; }
    #clone_buttons Button { width: 1fr; margin: 0 1; }
    """

    def __init__(self, source_name: str, source_platform: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_name = source_name
        self.source_platform = source_platform

    def compose(self) -> ComposeResult:
        with Vertical(id="clone_dialog"):
            yield Label(f"Clone Configuration", id="clone_title")
            yield Label(
                f"Cloning from: [bold]{self.source_name}[/bold]\n"
                f"Current source platform: [bold]{self.source_platform.capitalize()}[/bold]",
                id="clone_source_info"
            )
            yield Label("Enter new configuration name:", id="clone_name_label")
            yield Input(placeholder="e.g. MyClonedServer", id="clone_input", tooltip="Enter a unique name for the cloned config")
            yield Label("Change source platform? (keep same if unchanged)", id="clone_source_select")
            with RadioSet(id="clone_platform"):
                yield RadioButton(f"Keep same ({self.source_platform.capitalize()})", id="clone_plat_keep", value=True)
                yield RadioButton("Discord", id="clone_plat_discord")
                yield RadioButton("Fluxer", id="clone_plat_fluxer")
            with Horizontal(id="clone_buttons"):
                yield Button("Clone", variant="success", id="btn_clone_confirm", tooltip="Create the cloned configuration")
                yield Button("Cancel", variant="primary", id="btn_clone_cancel")

    def _get_sanitized_name(self) -> str:
        raw = self.query_one("#clone_input", Input).value.strip()
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", raw).strip("-")

    def _get_selected_platform(self) -> str | None:
        for rb in self.query("#clone_platform RadioButton"):
            if rb.value:
                if rb.id == "clone_plat_keep":
                    return None  # keep original
                elif rb.id == "clone_plat_discord":
                    return "discord"
                elif rb.id == "clone_plat_fluxer":
                    return "fluxer"
        return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_clone_confirm":
            name = self._get_sanitized_name()
            if name:
                platform = self._get_selected_platform()
                self.dismiss((name, platform))
        elif event.button.id == "btn_clone_cancel":
            self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


# ──────────────────────────────────────────────────────────────────────────────
# Config row widget — one per saved configuration
# ──────────────────────────────────────────────────────────────────────────────

class ConfigRow(Horizontal, can_focus=False):
    """A single row in the config list — name button + trash icon."""

    DEFAULT_CSS = """
    ConfigRow {
        height: auto; align: center middle; padding: 0 2; margin: 0;
    }
    ConfigRow .btn_open {
        width: 1fr; text-align: left;
        border: none; background: $surface;
    }
    ConfigRow .btn_open:hover {
        border: none; background: $boost;
    }
    ConfigRow .btn_clone {
        width: 7; min-width: 7; max-width: 7;
        border: none; background: $surface;
        color: $text-disabled;
    }
    ConfigRow .btn_clone:hover {
        color: $text; background: $primary 30%; border: none;
    }
    ConfigRow .btn_trash {
        width: 5; min-width: 5; max-width: 5;
        border: none; background: $surface;
        color: $text-disabled;
    }
    ConfigRow .btn_trash:hover {
        color: $text; background: $warning 30%; border: none;
    }
    """

    def __init__(self, cfg_name: str, display_name: str, standalone: bool = False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cfg_name = cfg_name
        self.display_name = display_name
        self.standalone = standalone

    def compose(self) -> ComposeResult:
        yield Button(self.display_name, id=f"open_cfg__{self.cfg_name}", classes="btn_open",
                     tooltip=f"Open configuration '{self.display_name}'")
        # The standalone "." config cannot be cloned/deleted via
        # "ReaperFiles-." paths, so those buttons are hidden for it.
        if not self.standalone:
            yield Button("📋", id=f"clone_cfg__{self.cfg_name}", classes="btn_clone",
                         tooltip=f"Clone configuration '{self.display_name}'")
            yield Button("🗑️", id=f"delete_cfg__{self.cfg_name}", classes="btn_trash",
                         tooltip=f"Delete configuration '{self.display_name}'")


# ──────────────────────────────────────────────────────────────────────────────
# Screen 1: pick (or create) a ReaperFiles-* config
# ──────────────────────────────────────────────────────────────────────────────

class ConfigSelectionScreen(Screen):
    """Screen to select or create a Reaper configuration."""

    DEFAULT_CSS = """
    ConfigSelectionScreen { align: center middle; }
    #config_sel_container {
        width: 65; height: auto;
        border: solid green; padding: 1 2;
    }
    #config_sel_title {
        text-style: bold; color: green; margin-bottom: 0;
        content-align: center middle; width: 100%;
    }
    #config_security_note {
        text-style: italic; color: $text-warning; margin-bottom: 1;
        content-align: center middle; width: 100%; height: auto;
    }
    #config_list_container {
        height: auto; max-height: 20;
        border: solid $primary; margin-bottom: 1; padding: 0;
    }
    #config_sel_actions { height: auto; margin-top: 0; }
    #config_sel_actions Button { width: 1fr; margin: 1 1; }
    #bottom_actions_row { height: auto; align: center middle; margin-top: 2; }
    #btn_update_app { display: none; margin-bottom: 1; width: 40; border: none; height: 1; }
    #btn_about { border: none; width: 40; height: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Center():
            with Container(id="config_sel_container"):
                yield Label(f"Reaper Configs", id="config_sel_title")
                yield Label(
                    "⚠  Bot tokens are stored in plaintext. \n    Delete configs when you're done.",
                    id="config_security_note"
                )
                with VerticalScroll(id="config_list_container"):
                    yield Label("No configurations found.", id="config_empty_label")
                with Horizontal(id="config_sel_actions"):
                    yield Button("New Config", id="btn_new_config", variant="success", tooltip="Create a new configuration folder")
                    yield Button("Exit", id="btn_exit", variant="error")
        with Center():
            with Vertical(id="bottom_actions_row"):
                yield Button("Update Available", id="btn_update_app", variant="warning", tooltip="A new version is available!")
                yield Button("Info", id="btn_about", tooltip="Show app information")
        yield Footer()
        yield Footnote()
        yield RamDisplay()

    def on_mount(self) -> None:
        self.run_worker(self.check_updates())
        configs = self.refresh_configs()
        if not configs:
            def on_first_info_dismiss(res):
                if res == "start_new":
                    self.action_new_config()
            self.app.push_screen(FirstInfoModal(), on_first_info_dismiss)

    async def check_updates(self):
        from src.core.updater import check_for_updates
        update_info = await check_for_updates()
        if update_info:
            self.update_info = update_info
            try:
                btn = self.query_one("#btn_update_app", Button)
                btn.display = True
                if update_info.get("prerelease", False):
                    btn.label = f"Beta Version Available ({update_info['version']})"
                    btn.variant = "warning"
                else:
                    btn.label = f"Update Available ({update_info['version']})"
                    btn.variant = "success"
            except Exception:
                pass

    def on_screen_resume(self) -> None:
        self.refresh_configs()

    def refresh_configs(self) -> list:
        configs = get_available_configs()
        scroll = self.query_one("#config_list_container", VerticalScroll)
        empty_label = self.query_one("#config_empty_label", Label)

        # Remove all existing ConfigRow widgets (keep the empty label)
        for child in list(scroll.children):
            if isinstance(child, ConfigRow):
                child.remove()

        is_standalone = ("." in configs)
        try:
            self.query_one("#btn_new_config", Button).display = not is_standalone
        except Exception:
            pass

        if not configs:
            empty_label.display = True
            return configs

        empty_label.display = False

        for c in configs:
            if c == ".":
                dir_name = Path(".").resolve().name
                if dir_name.startswith("ReaperFiles-"):
                    display_name = dir_name[len("ReaperFiles-"):]
                else:
                    display_name = dir_name
            else:
                display_name = c

            row = ConfigRow(cfg_name=c, display_name=display_name, standalone=(c == "."))
            scroll.mount(row)

        return configs

    def _open_config(self, cfg_name: str) -> None:
        """Navigate into the selected configuration."""
        if cfg_name == ".":
            cfg_path = Path("reaper_config.yaml")
            dir_name = Path(".").resolve().name
            if dir_name.startswith("ReaperFiles-"):
                display_name = dir_name[len("ReaperFiles-"):]
            else:
                display_name = dir_name
        else:
            cfg_path = Path(f"ReaperFiles-{cfg_name}") / "reaper_config.yaml"
            display_name = cfg_name

        from src.ui.mode_screen import ModeScreen
        self.app.push_screen(ModeScreen(display_name, cfg_path))

    def _delete_config(self, cfg_name: str) -> None:
        """Scan for extra data, then show appropriate confirmation modal(s)."""
        data_info = scan_config_data(cfg_name)

        def on_first_confirm(confirmed: bool):
            if not confirmed:
                return

            if not data_info:
                # No extra data — just delete
                delete_config(cfg_name)
                self.notify(f"Configuration '{cfg_name}' deleted.", severity="information")
                self.refresh_configs()
                return

            # Extra data exists — ask what to do with it
            def on_data_choice(choice: str):
                if choice == "open":
                    # Open the folder for the user to back up
                    import subprocess, sys
                    folder = Path(f"ReaperFiles-{cfg_name}").resolve()
                    try:
                        if sys.platform == "darwin":
                            subprocess.Popen(["open", str(folder)])
                        elif sys.platform == "win32":
                            subprocess.Popen(["explorer", str(folder)])
                        else:
                            subprocess.Popen(["xdg-open", str(folder)])
                        self.notify(f"Opened {folder.name} in file manager.", severity="information")
                    except Exception:
                        self.notify(f"Could not open folder: {folder}", severity="warning")
                    # Re-show the data modal so the user can decide after backing up
                    self._delete_config(cfg_name)
                elif choice == "delete_all":
                    # Re-evaluate: did the user move the files already?
                    still_there = scan_config_data(cfg_name)
                    if still_there:
                        self.notify(
                            f"Deleting config '{cfg_name}' and all data files.",
                            severity="information"
                        )
                    delete_config(cfg_name)
                    self.notify(f"Configuration '{cfg_name}' deleted.", severity="information")
                    self.refresh_configs()
                # "cancel" → do nothing

            self.app.push_screen(DeleteDataModal(cfg_name, data_info), on_data_choice)

        self.app.push_screen(DeleteConfigModal(cfg_name, has_data=bool(data_info)), on_first_confirm)

    def _clone_config(self, cfg_name: str) -> None:
        """Show clone modal, then clone the config with a new name and optional platform change."""
        # Load the source config to get its current platform
        cfg_path = Path(f"ReaperFiles-{cfg_name}") / "reaper_config.yaml"
        source_config = load_config(cfg_path)
        source_platform = source_config.source_platform or "discord"

        def do_clone(new_name: str, new_platform: str | None):
            cloned = clone_config(cfg_name, new_name, new_platform)
            if cloned:
                self.notify(f"Configuration cloned to '{new_name}'.", severity="information")
                self.refresh_configs()
            else:
                self.notify(f"Failed to clone configuration '{cfg_name}'.", severity="error")

        def on_clone(result):
            if result is None:
                return
            new_name, new_platform = result

            # Confirm before overwriting an existing configuration
            if Path(f"ReaperFiles-{new_name}").exists():
                def on_overwrite(confirmed: bool):
                    if confirmed:
                        do_clone(new_name, new_platform)
                    else:
                        self.notify(f"Clone to '{new_name}' cancelled.", severity="warning")
                self.app.push_screen(OverwriteConfigModal(new_name), on_overwrite)
                return

            do_clone(new_name, new_platform)

        self.app.push_screen(CloneConfigModal(cfg_name, source_platform), on_clone)

    def action_new_config(self) -> None:
        def cb(result):
            if result is None:
                return
            name, source_platform = result

            create_new_config(name, source_platform=source_platform)
            self.refresh_configs()
            # Immediately open the ConfigScreen for the new config
            cfg_path = Path(f"ReaperFiles-{name}") / "reaper_config.yaml"

            def on_config_saved(saved: bool = False):
                if saved:
                    self.refresh_configs()
                    # Navigate into the ModeScreen
                    from src.ui.mode_screen import ModeScreen
                    self.app.push_screen(ModeScreen(name, cfg_path))
                else:
                    # User exited without saving — clean up the newly created config
                    delete_config(name)
                    self.refresh_configs()

            self.app.push_screen(
                ConfigScreen(name, cfg_path, source_platform=source_platform, is_new_config=True),
                on_config_saved
                )

        self.app.push_screen(NewConfigModal(), cb)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""

        if bid == "btn_new_config":
            self.action_new_config()
        elif bid.startswith("open_cfg__"):
            cfg_name = bid[len("open_cfg__"):]
            self._open_config(cfg_name)
        elif bid.startswith("delete_cfg__"):
            cfg_name = bid[len("delete_cfg__"):]
            self._delete_config(cfg_name)
        elif bid.startswith("clone_cfg__"):
            cfg_name = bid[len("clone_cfg__"):]
            self._clone_config(cfg_name)
        elif bid == "btn_about":
            self.app.push_screen(FirstInfoModal())
        elif bid == "btn_update_app":
            if hasattr(self, 'update_info') and self.update_info:
                from src.ui.modals import UpdateModalScreen, UpdateProgressScreen
                def on_update_confirm(do_update):
                    if do_update:
                        self.app.push_screen(UpdateProgressScreen(asset_url=self.update_info['asset_url']))
                self.app.push_screen(
                    UpdateModalScreen(
                        version=self.update_info['version'],
                        notes=self.update_info['body'],
                        prerelease=self.update_info.get('prerelease', False)
                    ),
                    on_update_confirm
                )
        elif bid == "btn_exit":
            self.app.exit()


# ──────────────────────────────────────────────────────────────────────────────
# Screen 2: edit config + pick mode + start
# ──────────────────────────────────────────────────────────────────────────────

_MODE_MAP = {
    "radio_direct":  "direct_transfer",
    "radio_backup":  "backup_transfer",
    "radio_bkonly":  "backup_only",
}
_MODE_LABELS = {
    "direct_transfer":  "radio_direct",
    "backup_transfer":  "radio_backup",
    "backup_only":      "radio_bkonly",
}

_PLAT_MAP = {
    "radio_fluxer": "fluxer",
    "radio_stoat":  "stoat",
}
_PLAT_LABELS = {
    "fluxer": "radio_fluxer",
    "stoat":  "radio_stoat",
}


class ConfigScreen(Screen):
    """Configuration screen — Source config, tool mode, and target platform."""

    DEFAULT_CSS = """
    ConfigScreen { align: center middle; }
    #cfg_outer { width: 100%; height: 100%; align: center top; }
    #cfg_container {
        width: 80%; height: 100%; layout: vertical;
        border: solid green; padding: 1 2; margin: 2 0;
    }
    #cfg_scroll { width: 100%; height: 1fr; margin-bottom: 1; }
    #cfg_title {
        text-style: bold; color: green; margin-bottom: 1;
        content-align: center middle; width: 100%;
    }
    .section_title {
        text-style: bold; color: cyan; margin-top: 1; margin-bottom: 0;
    }
    .field_label { margin-top: 1; }
    #cfg_container Input { margin-bottom: 0; }
    #mode_radio, #plat_radio {
        height: auto; margin: 0 0 0 2;
    }
    #mode_radio RadioButton:disabled {
        opacity: 70%;
        color: gray;
    }
    .field_label2 { margin-top: 1; padding-left: 2;}
    #cfg_container Input { margin-bottom: 0; }
    #target_section { height: auto; }
    #cfg_actions { height: auto; margin-top: 0; margin-bottom: 0; dock: bottom; }
    #cfg_actions Button { width: 1fr; margin: 0 1; }
    #footer_rule { margin: 0; }
    .fetch_row { height: auto; align: left middle; margin-bottom: 1; }
    .fetch_row Input { width: 1fr; }
    .fetch_row Button { width: auto; margin-left: 1; }

    #info_row { height: auto; align: center middle; margin-bottom: 1; }
    #info_row Label { width: 50%; margin-left: 0; }
    #target_validate_row { height: auto; align: center middle; margin-bottom: 1; }
    #target_validate_row Button { width: 100%; margin-left: 0; }
    #inp_source_server { margin-bottom: 1; }
    .switch_row { height: auto; align: left middle; margin-top: 1; margin-bottom: 1; }
    #lbl_anonymize { margin-right: 2; margin-top: 0; }
    .api_warning {
        color: red;
        margin-top: 0;
        padding-left: 2;
        height: 1;
        text-style: bold;
    }
    """

    BINDINGS = [("escape", "go_back", "Back")]

    FETCH_CONFIGS = {
        "source_discord": {
            "btn_id": "#btn_fetch_source",
            "select_id": "#inp_source_server",
            "error_msg": "No Discord servers found.",
            "saved_id_attr": "source_server_id",  # attribute on self.config
        },
        "source_fluxer": {
            "btn_id": "#btn_fetch_source",
            "select_id": "#inp_source_server",
            "error_msg": "No Fluxer servers found.",
            "saved_id_attr": "source_server_id",
        },
        "target_fluxer": {
            "btn_id": "#btn_fetch_target",
            "select_id": "#inp_target_server",
            "error_msg": "No Fluxer servers found.",
            "saved_id_attr": "fluxer_server_id",
        },
        "target_stoat": {
            "btn_id": "#btn_fetch_target",
            "select_id": "#inp_target_server",
            "error_msg": "No Stoat servers found.",
            "saved_id_attr": "stoat_server_id",
        },
    }

    def __init__(self, cfg_name: str, cfg_path: Path, source_platform: str = None, is_new_config: bool = False, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.cfg_name = cfg_name
        self.cfg_path = cfg_path
        self.is_new_config = is_new_config
        self.config = load_config(cfg_path)
        
        # For brand-new configs (file was just created), use the explicitly chosen platform.
        # Otherwise, honour what is already saved in the config file.
        if is_new_config and source_platform:
            self.source_platform = source_platform
            self.config.source_platform = source_platform
        else:
            self.source_platform = self.config.source_platform or source_platform or "discord"

        if not self.config.source_api_url:
            self.api_url = ""
            self.config.source_api_url = ""
        else:
            self.config.source_api_url = self.config.source_api_url.strip() or ""

    def action_go_back(self) -> None:
        """Handle Escape key — dismiss without saving."""
        self.dismiss(False)

    def _validate_api_url(self, input_widget: Input, label_id: str, base_text: str, platform: str) -> None:
        """Update the label to show a warning if the URL doesn't end with /api."""
        url = input_widget.value.strip()
        label = self.query_one(f"#{label_id}", Label)
        if platform is not None and platform == "fluxer":
            if url and not url.startswith("http"):
                label.update(f"{base_text} ⚠️ must start with http:// or https://")
                label.add_class("api_warning")
            elif url and not url.endswith("/api"):
                label.update(f"{base_text} ⚠️ must end with /api")
                label.add_class("api_warning")
            else:
                label.update(base_text)
                label.remove_class("api_warning")



    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "inp_source_api_url":
            self._validate_api_url(event.input, "lbl_source_api", "Source API URL:", self.config.source_platform)
        elif event.input.id == "inp_target_api":
            self._validate_api_url(event.input, "lbl_target_api", "Target API URL:", self.config.target_platform)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="cfg_outer"):
            with Container(id="cfg_container"):
                yield Label(f"Configuration — {self.cfg_name}", id="cfg_title")

                with VerticalScroll(id="cfg_scroll"):
                    # -- Source Platform (Discord or Fluxer) --
                    yield Label(f"[{self.source_platform.capitalize()}](Source Platform)", classes="section_title")
                    if self.source_platform == "discord":
                        # ── Discord ──────────────────────────────────────────────
                        yield Label("Discord Bot Token:", classes="field_label")
                        with Horizontal(classes="fetch_row"):
                            yield ClipboardInput(
                                value=self.config.source_bot_token or "",
                                id="inp_source_token",
                                password=True,
                                placeholder="Paste Discord Bot Token here",
                            )
                            yield Button("Validate", id="btn_fetch_source", variant="primary", tooltip="Verify token and fetch available Discord servers")
                        yield Label("Server ID:", classes="field_label")
                        yield Select(
                            options=[],
                            id="inp_source_server",
                            prompt="Validate Bot Token"
                        )
                    elif self.source_platform == "fluxer":
                        # ── Fluxer ───────────────────────────────────────────────
                        with Horizontal(id="info_row", classes="fetch_row"):
                            yield Label("Fluxer Bot Token:", classes="field_label")
                            yield Label("API URL:", id="lbl_source_api", classes="field_label2")
                        with Horizontal(classes="fetch_row"):
                            yield ClipboardInput(
                                value=self.config.source_bot_token or "",
                                id="inp_source_token",
                                password=True,
                                placeholder="Paste Fluxer Bot Token here",
                            )

                            yield Input(
                                value=self.config.source_api_url or "",
                                id="inp_source_api_url",
                                placeholder="Leave empty for official Fluxer app/instance.",
                                tooltip="Enter the custom API url for self hosted instances"
                            )

                        
                        with Horizontal(id="target_validate_row"):
                            yield Button("Validate", id="btn_fetch_source", variant="primary", tooltip="Verify token and fetch available Fluxer communities")

                        yield Label("Source Server ID:", classes="field_label")
                        yield Select(
                            options=[],
                            id="inp_source_server",
                            prompt="Validate Bot Token"
                        )

                    # ── Reaper Mode ──────────────────────────────────────────
                    yield Label("Reaper Mode", classes="section_title")
                    cur_mode = self.config.tool_mode or "direct_transfer"
                    disable_backup = FLUXER_SOURCE_DISABLE_BACKUP_MODES and self.source_platform == "fluxer"
                    if disable_backup:
                        cur_mode = "direct_transfer"  # force for Fluxer sources
                    with RadioSet(id="mode_radio"):
                        yield RadioButton(
                            "Shuttle Transfer  (direct migration)",
                            id="radio_direct",
                            value=(cur_mode == "direct_transfer")
                        )
                        yield RadioButton(
                            "Backup & Migrate  (backup first, then migrate)",
                            id="radio_backup",
                            value=(cur_mode == "backup_transfer"),
                            disabled=disable_backup,
                        )
                        yield RadioButton(
                            "Backup Only       (local backup, no migration)",
                            id="radio_bkonly",
                            value=(cur_mode == "backup_only"),
                            disabled=disable_backup,
                        )

                    # ── Target Platform (hidden for backup_only) ─────────────
                    with Vertical(id="target_section"):
                        yield Label("Target Platform", classes="section_title")
                        cur_plat = self.config.target_platform or "fluxer"
                        with RadioSet(id="plat_radio"):
                            yield RadioButton(
                                "Fluxer",
                                id="radio_fluxer",
                                value=(cur_plat == "fluxer")
                            )
                            yield RadioButton(
                                "Stoat",
                                id="radio_stoat",
                                value=(cur_plat == "stoat")
                            )
                        with Horizontal(id="info_row", classes="fetch_row"):
                            yield Label("Bot Token:", classes="field_label")
                            yield Label("API URL:", id="lbl_target_api", classes="field_label2")

                        with Horizontal(classes="fetch_row"):
                            cur_plat = self.config.target_platform or "fluxer"
                            t_token = self.config.stoat_bot_token if cur_plat == "stoat" else self.config.fluxer_bot_token
                            yield ClipboardInput(
                                value=t_token or "",
                                id="inp_target_token",
                                password=True,
                                placeholder="Paste Target Bot Token",
                            )
                            
                            t_api = self.config.stoat_api_url if cur_plat == "stoat" else self.config.fluxer_api_url
                            yield Input(
                                value=t_api if (t_api and t_api != "default") else "",
                                id="inp_target_api",
                                placeholder="Leave this Empty for official instance",
                                tooltip="Enter the custom API url\nfor self hosted instances"
                            )

                        with Horizontal(id="target_validate_row"):
                            yield Button("Validate", id="btn_fetch_target", variant="primary", tooltip="Verify token and fetch available communities")

                        yield Label("Community / Server ID:", classes="field_label")
                        yield Select(
                            options=[],
                            id="inp_target_server",
                            prompt="Validate Bot Token"
                        )
                        
                        yield Horizontal(
                            Label("Anonymize Users:", id="lbl_anonymize"),
                            Switch(value=self.config.anonymize_users, id="inp_anonymize_users", tooltip="Anonymize user Names and Avatars during migration"),
                            id="anonymize_row",
                            classes="switch_row"
                        )

                yield Rule(id="footer_rule")
                with Horizontal(id="cfg_actions"):
                    yield Button("Save Configuration", variant="success", id="btn_save", tooltip="Save all changes to reaper_config.yaml")
                    yield Button("Back", id="btn_back")
        yield Footer()
        yield RamDisplay()

    def on_mount(self) -> None:
        self._toggle_target_section()
        
        # If we have a token, try to populate the select widget on mount
        if self.config.source_bot_token:
            self.run_worker(self._do_fetch(
                "source", 
                self.config.source_platform, 
                self.config.source_bot_token, 
                self.config.source_api_url or "default", 
                initial=True
            ))

        # Also auto-fetch target servers if mode is not backup_only
        if self._get_selected_mode() != "backup_only":
            if self.config.source_platform != "discord": 
                source_api = self.query_one("#inp_source_api_url", Input)
                self._validate_api_url(source_api, "lbl_source_api", "Source API URL:", self.config.source_platform)

            if self.config.target_platform != "none":
                target_api = self.query_one("#inp_target_api", Input)
                self._validate_api_url(target_api, "lbl_target_api", "Target API URL:", self.config.target_platform)

            platform = self.config.target_platform
            
            if platform == "fluxer":
                t_token = self.config.fluxer_bot_token
            elif platform == "stoat":
                t_token = self.config.stoat_bot_token
            else:
                t_token = None
            
            if t_token:
                if platform != "none":
                    t_api = self.config.stoat_api_url if platform == "stoat" else self.config.fluxer_api_url
                    self.run_worker(self._do_fetch(
                        role="target",
                        platform=platform,
                        token=t_token,
                        api_url=t_api,
                        initial=True
                    ))

    async def _fetch_and_populate(
        self, 
        fetch_coro, 
        config: dict, 
        saved_id: str | None = None,
        initial: bool = False
    ) -> None:
        """Generic helper to fetch data and update a Select widget."""
        try:
            results = await fetch_coro
            btn = self.query_one(config["btn_id"], Button)
            select = self.query_one(config["select_id"], Select)

            if not results:
                btn.variant = "warning"
                select.prompt = "No results found"
                if not initial:
                    self.notify(config["error_msg"], severity="warning")
                return

            btn.variant = "success"
            options = [(label, sid) for label, sid in results]
            select.prompt = "Select an item"
            select.set_options(options)

            if saved_id and any(sid == saved_id for _, sid in results):
                select.value = saved_id
                
        except Exception as e:
            try:
                self.query_one(config["btn_id"], Button).variant = "warning"
                self.query_one(config["select_id"], Select).prompt = "Invalid token"
            except Exception:
                pass
            if not initial:
                self.notify(f"Fetch failed: {e}", severity="error")
    
    async def _do_fetch(
        self,
        role: str,          # "source" or "target"
        platform: str,      # "discord", "fluxer", "stoat"
        token: str,
        api_url: str | None = None,
        initial: bool = False
    ) -> None:
        # Build the config key
        key = f"{role}_{platform}"
        config = self.FETCH_CONFIGS.get(key)
        if not config:
            logger.error(f"No fetch config for {key}")
            return

        # Get the saved ID from the config object
        btn_id = self.query_one(config["btn_id"], Button)
        saved_id = getattr(self.config, config["saved_id_attr"], None)

        # Choose the correct coroutine
        if role == "source" and platform == "discord":
            from src.core.discord_reader import DiscordReader
            coro = DiscordReader.fetch_guilds(token)
        elif platform == "fluxer":
            from src.fluxer.writer import FluxerWriter
            coro = FluxerWriter.fetch_guilds(token, api_url or "default")
        elif role == "target" and platform == "stoat":
            from src.stoat.writer import StoatWriter
            coro = StoatWriter.fetch_guilds(token, api_url or "default")
        else:
            logger.error(f"Unsupported fetch combination: {role}/{platform}")
            return

        await self._fetch_and_populate(coro, config, saved_id, initial)


        # Guard: if the user switched platforms while the fetch was in-flight,
        # discard the stale results so they don't contaminate the wrong platform.
        try:
            if self._get_selected_platform() != platform:
                select = self.query_one("#inp_target_server", Select)
                select.set_options([])
                select.value = Select.BLANK
                select.prompt = "Validate Bot Token"
                self.query_one("#btn_fetch_target", Button).variant = "primary"
        except Exception:
            pass

    async def _do_fetch_source_discord(self, token: str, initial: bool = False) -> None:
        from src.core.discord_reader import DiscordReader
        config = self.FETCH_CONFIGS["source_discord"]
        saved_id = getattr(self.config, config["saved_id_attr"])
        await self._fetch_and_populate(
            DiscordReader.fetch_guilds(token),
            config,
            saved_id,
            initial
        )

    async def _do_fetch_source_fluxer(self, token: str, api_url: str = None, initial: bool = False) -> None:
        from src.fluxer.writer import FluxerWriter
        config = self.FETCH_CONFIGS["source_fluxer"]
        saved_id = getattr(self.config, config["saved_id_attr"])
        await self._fetch_and_populate(
            FluxerWriter.fetch_guilds(token, api_url or "default"),
            config,
            saved_id,
            initial
        )

    async def _do_fetch_target_fluxer(self, token: str, api_url: str = None, initial: bool = False) -> None:
        from src.fluxer.writer import FluxerWriter
        config = self.FETCH_CONFIGS["target_fluxer"]
        saved_id = getattr(self.config, config["saved_id_attr"])
        await self._fetch_and_populate(
            FluxerWriter.fetch_guilds(token, api_url or "default"),
            config,
            saved_id,
            initial
        )

    async def _do_fetch_target_stoat(self, token: str, api_url: str = None, initial: bool = False) -> None:
        from src.stoat.writer import StoatWriter
        config = self.FETCH_CONFIGS["target_stoat"]
        saved_id = getattr(self.config, config["saved_id_attr"])
        await self._fetch_and_populate(
            StoatWriter.fetch_guilds(token, api_url or "default"),
            config,
            saved_id,
            initial
        )


    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_fetch_source":
            token = self.query_one("#inp_source_token", ClipboardInput).value.strip()
            if not token:
                self.notify("Please enter a valid Source Bot Token.", severity="error")
                return
            
            api_url = None
            if self.config.source_platform != "discord":
                api_url = self.query_one("#inp_source_api_url", Input).value.strip() or "default"
            self.run_worker(self._do_fetch("source", self.config.source_platform, token, api_url, initial=False))

        elif event.button.id == "btn_fetch_target":
            token = self.query_one("#inp_target_token", ClipboardInput).value.strip()
            if not token:
                self.notify("Please enter a valid Target Bot Token.", severity="error")
                return
            platform = self._get_selected_platform()
            api_url = self.query_one("#inp_target_api", Input).value.strip() or "default"
            self.run_worker(self._do_fetch("target", platform, token, api_url, initial=False))

        elif event.button.id == "btn_back":
            self.dismiss(False)

        elif event.button.id == "btn_save":
            self._collect_and_save()
            self.notify("Configuration saved.", severity="information")
            self.dismiss(True)

    def _get_selected_mode(self) -> str:
        if FLUXER_SOURCE_DISABLE_BACKUP_MODES and self.source_platform == "fluxer":
            return "direct_transfer"
        for rb in self.query("#mode_radio RadioButton"):
            if rb.value:
                return _MODE_MAP.get(rb.id, "backup_only")
        return "backup_only"

    def _get_selected_platform(self) -> str:
        for rb in self.query("#plat_radio RadioButton"):
            if rb.value:
                return _PLAT_MAP.get(rb.id, "fluxer")
        return "fluxer"

    def _toggle_target_section(self) -> None:
        section = self.query_one("#target_section")
        section.display = self._get_selected_mode() != "backup_only"

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id == "mode_radio":
            self._toggle_target_section()
        elif event.radio_set.id == "plat_radio":
            plat = self._get_selected_platform()
            try:
                inp_token = self.query_one("#inp_target_token", ClipboardInput)
                inp_api = self.query_one("#inp_target_api", Input)
                
                if plat == "fluxer":
                    inp_token.value = self.config.fluxer_bot_token or ""
                    api_val = self.config.fluxer_api_url
                    inp_api.value = api_val if (api_val and api_val != "default") else ""
                elif plat == "stoat":
                    inp_token.value = self.config.stoat_bot_token or ""
                    api_val = self.config.stoat_api_url
                    inp_api.value = api_val if (api_val and api_val != "default") else ""
                    
                select = self.query_one("#inp_target_server", Select)
                select.set_options([])
                select.value = Select.BLANK
                select.prompt = "Validate Bot Token"
                self.query_one("#btn_fetch_target", Button).variant = "primary"
                
                if inp_token.value:
                    self.run_worker(self._do_fetch("target", plat, inp_token.value, inp_api.value, initial=True))
            except Exception: pass

    # ── save / start ─────────────────────────────────────────────────────

    def _collect_and_save(self) -> None:
        # 1. Source Section
        self.config.source_bot_token = self.query_one("#inp_source_token", ClipboardInput).value.strip() or None
        if self.config.source_platform == "fluxer":
            self.config.source_api_url = self.query_one("#inp_source_api_url", Input).value.strip() or None
        
        d_select = self.query_one("#inp_source_server", Select)
        if d_select.value not in (Select.BLANK, Select.NULL):
            self.config.source_server_id = str(d_select.value)

        # 2. Mode
        if FLUXER_SOURCE_DISABLE_BACKUP_MODES and self.source_platform == "fluxer":
            self.config.tool_mode = "direct_transfer"
        else:
            self.config.tool_mode = self._get_selected_mode()

        # 3. Target Section
        if self.config.tool_mode != "backup_only":
            plat = self._get_selected_platform()
            self.config.target_platform = plat
            token_val = self.query_one("#inp_target_token", ClipboardInput).value.strip() or None
            
            t_select = self.query_one("#inp_target_server", Select)
            
            target_api = self.query_one("#inp_target_api", Input).value.strip()
            api_val = target_api or None
            
            if plat == "fluxer":
                self.config.fluxer_bot_token = token_val
                # Only update server_id if user actually selected something
                if t_select.value not in (Select.BLANK, Select.NULL):
                    self.config.fluxer_server_id = str(t_select.value)
                self.config.fluxer_api_url = api_val
            elif plat == "stoat":
                self.config.stoat_bot_token = token_val
                if t_select.value not in (Select.BLANK, Select.NULL):
                    self.config.stoat_server_id = str(t_select.value)
                self.config.stoat_api_url = api_val
            
            self.config.anonymize_users = self.query_one("#inp_anonymize_users", Switch).value
        else:
            self.config.target_platform = "none"

        save_config(self.config, self.cfg_path)

    def _launch_mode(self) -> None:
        pass # No longer needed



# ──────────────────────────────────────────────────────────────────────────────
# App
# ──────────────────────────────────────────────────────────────────────────────

class TerminalSizeWarningModal(ModalScreen[None]):
    """One-time popup warning the user that the terminal window is too small."""

    DEFAULT_CSS = """
    TerminalSizeWarningModal { align: center middle; }
    #termsize_dialog {
        width: 60; height: auto; min-height: 12;
        border: thick $warning 80%; background: $surface; padding: 1 2;
    }
    #termsize_title { text-style: bold; color: $warning; margin-bottom: 1; }
    #termsize_body { margin-bottom: 1; color: $text-warning; }
    #termsize_buttons { height: auto; margin-top: 1; }
    #termsize_buttons Button { width: 1fr; }
    """

    def __init__(self, cols: int, rows: int, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cols = cols
        self.rows = rows

    def compose(self) -> ComposeResult:
        with Vertical(id="termsize_dialog"):
            yield Label("Terminal Window Too Small", id="termsize_title")
            yield Label(
                f"Your terminal is [bold]{self.cols}x{self.rows}[/bold].\n\n"
                f"Enlarge it to at least [bold]100x45[/bold] — otherwise the \n"
                f"server-profile and log boxes may be hidden.\n\n"
                f"You can continue, but some information\n may not be visible.\n",
                id="termsize_body"
            )
            with Horizontal(id="termsize_buttons"):
                yield Button("Got it", variant="primary", id="btn_termsize_ok")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_termsize_ok":
            self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class ReaperApp(App):
    TITLE = get_app_version()
    SCREENS = {
        "config_selection": ConfigSelectionScreen,
    }

    # Recommended minimum terminal size (cols x rows).  Below this the
    # operation panes clip their log output box and the server-profile
    # information box on small terminals (e.g. a 30-row Konsole window).
    MIN_COLS = 100
    MIN_ROWS = 45

    DEFAULT_CSS = """
    RamDisplay {
        dock: bottom;
        width: 35;
        height: 1;
        margin-left: 2;
        color: green;
    }
    Footnote {
        dock: bottom;
        width: 100%;
        height: 1;
        text-align: right;
        padding-right: 1;
        background: $surface;
        color: $text;
    }
    """

    _terminal_size_warned = False

    def on_mount(self) -> None:
        self.push_screen("config_selection")
        self.theme = "dracula"
        self.call_after_refresh(self._warn_terminal_size_once)

    def _too_small(self) -> tuple[int, int] | None:
        """Return (cols, rows) if the terminal is below the recommended
        minimum, else None."""
        cols, rows = self.size
        if rows < self.MIN_ROWS or cols < self.MIN_COLS:
            return cols, rows
        return None

    def _warn_terminal_size_once(self) -> None:
        """One-time popup telling the user the terminal is too small."""
        if self._terminal_size_warned:
            return
        self._terminal_size_warned = True
        too_small = self._too_small()
        if too_small is not None:
            self.push_screen(TerminalSizeWarningModal(*too_small))

    def action_screenshot(self, filename: str | None = None, path: str | None = None) -> None:
        """Action to take a screenshot."""
        self.deliver_screenshot(filename, path)

    def deliver_screenshot(
        self,
        filename: str | None = None,
        path: str | None = None,
        time_format: str | None = None,
    ) -> str | None:
        """Deliver a screenshot by saving it locally and notifying the user."""
        # Use our local screenshots folder if no path provided
        save_path = path or os.path.abspath("screenshots")
        try:
            # Ensure directory exists
            os.makedirs(save_path, exist_ok=True)
            
            # Using save_screenshot to write directly to disk
            actual_path = self.save_screenshot(filename=filename, path=save_path, time_format=time_format)
            self.notify(f"Screenshot saved to: {os.path.basename(actual_path)}", title="Screenshot", severity="information")
            return actual_path
        except Exception as e:
            self.notify(f"Failed to save screenshot: {e}", title="Screenshot", severity="error")
            logger.error(f"Screenshot delivery failed: {e}", exc_info=True)
            return None


def run_disco_reaper_tui():
    app = ReaperApp()
    app.run()
