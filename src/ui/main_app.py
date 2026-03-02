import re
import os
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Header, Footer, Button, Label, Input, ListItem,
    ListView, Rule, RadioButton, RadioSet,
)
from textual.screen import Screen, ModalScreen

from src.core.configuration import (
    get_available_configs, create_new_config, load_config, save_config,
)


# ──────────────────────────────────────────────────────────────────────────────
# Modal: create a new Reaper-* config folder
# ──────────────────────────────────────────────────────────────────────────────

class NewConfigModal(ModalScreen[str]):
    """Modal to enter a name for a new configuration."""

    DEFAULT_CSS = """
    NewConfigModal { align: center middle; }
    #new_config_dialog {
        width: 50; height: auto;
        border: thick $background 80%; background: $surface; padding: 1 2;
    }
    #new_config_title { text-style: bold; margin-bottom: 1; }
    #new_config_buttons { height: auto; margin-top: 1; }
    #new_config_buttons Button { width: 1fr; margin: 0 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="new_config_dialog"):
            yield Label("Enter new configuration name:", id="new_config_title")
            yield Input(placeholder="e.g. MyServer", id="new_config_input")
            with Horizontal(id="new_config_buttons"):
                yield Button("Create", variant="success", id="btn_create")
                yield Button("Cancel", variant="primary", id="btn_cancel")

    def _get_sanitized_name(self) -> str:
        raw = self.query_one("#new_config_input", Input).value.strip()
        return re.sub(r"[^a-zA-Z0-9_-]+", "-", raw).strip("-")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_create":
            name = self._get_sanitized_name()
            if name:
                self.dismiss(name)
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
# Screen 1: pick (or create) a Reaper-* config
# ──────────────────────────────────────────────────────────────────────────────

class ConfigSelectionScreen(Screen):
    """Screen to select or create a Reaper configuration."""

    DEFAULT_CSS = """
    ConfigSelectionScreen { align: center middle; }
    #config_sel_container {
        width: 60; height: auto;
        border: solid green; padding: 1 2;
    }
    #config_sel_title {
        text-style: bold; color: green; margin-bottom: 1;
        content-align: center middle; width: 100%;
    }
    #config_list_container {
        height: 10; max-height: 20;
        border: solid $primary; margin-bottom: 1;
    }
    #config_sel_actions { height: auto; margin-top: 1; }
    #config_sel_actions Button { width: 1fr; margin: 0 1; }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="config_sel_container"):
            yield Label("Disco Reaper — Select Configuration", id="config_sel_title")
            with VerticalScroll(id="config_list_container"):
                yield ListView(id="config_list")
            with Horizontal(id="config_sel_actions"):
                yield Button("Create New Config", id="btn_new_config", variant="success")
                yield Button("Exit", id="btn_exit", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_configs()

    def on_screen_resume(self) -> None:
        self.refresh_configs()

    def refresh_configs(self) -> None:
        configs = get_available_configs()
        lv = self.query_one("#config_list", ListView)
        lv.clear()
        for c in configs:
            lv.append(ListItem(Label(f"Reaper-{c}"), name=c))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        cfg_name = event.item.name
        cfg_path = Path(f"Reaper-{cfg_name}") / "config.yaml"
        self.app.push_screen(ConfigScreen(cfg_name, cfg_path))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_new_config":
            def cb(name: str | None):
                if name:
                    create_new_config(name)
                    self.refresh_configs()
            self.app.push_screen(NewConfigModal(), cb)
        elif event.button.id == "btn_exit":
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
    """Configuration screen — Discord config, tool mode, and target platform."""

    DEFAULT_CSS = """
    ConfigScreen { align: center middle; }
    #cfg_scroll { width: 100%; height: 100%; align: center middle; }
    #cfg_container {
        width: 70; height: auto;
        border: solid green; padding: 1 2; margin: 1 0;
    }
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
    #target_section { height: auto; }
    #cfg_actions { height: auto; margin-top: 1; }
    #cfg_actions Button { width: 1fr; margin: 0 1; }
    """

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self, cfg_name: str, cfg_path: Path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cfg_name = cfg_name
        self.cfg_path = cfg_path
        self.config = load_config(cfg_path)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="cfg_scroll"):
            with Container(id="cfg_container"):
                yield Label(f"Configuration — {self.cfg_name}", id="cfg_title")

                # ── Discord ──────────────────────────────────────────────
                yield Label("Discord", classes="section_title")
                yield Rule()
                yield Label("Bot Token:", classes="field_label")
                yield Input(
                    value=self.config.discord_bot_token or "",
                    id="inp_discord_token",
                    password=True,
                )
                yield Label("Server ID:", classes="field_label")
                yield Input(
                    value=self.config.discord_server_id or "",
                    id="inp_discord_server",
                )

                # ── Reaper Mode ──────────────────────────────────────────
                yield Label("Reaper Mode", classes="section_title")
                yield Rule()
                cur_mode = self.config.tool_mode or "backup_only"
                with RadioSet(id="mode_radio"):
                    yield RadioButton(
                        "Shuttle Transfer  (direct migration)",
                        id="radio_direct",
                        value=(cur_mode == "direct_transfer"),
                    )
                    yield RadioButton(
                        "Backup & Migrate  (backup first, then migrate)",
                        id="radio_backup",
                        value=(cur_mode == "backup_transfer"),
                    )
                    yield RadioButton(
                        "Backup Only       (local backup, no migration)",
                        id="radio_bkonly",
                        value=(cur_mode == "backup_only"),
                    )

                # ── Target Platform (hidden for backup_only) ─────────────
                with Vertical(id="target_section"):
                    yield Label("Target Platform", classes="section_title")
                    yield Rule()
                    cur_plat = self.config.target_platform or "none"
                    with RadioSet(id="plat_radio"):
                        yield RadioButton(
                            "Fluxer",
                            id="radio_fluxer",
                            value=(cur_plat == "fluxer"),
                        )
                        yield RadioButton(
                            "Stoat",
                            id="radio_stoat",
                            value=(cur_plat == "stoat"),
                        )
                    yield Label("Bot Token:", classes="field_label")
                    yield Input(
                        value=self.config.target_bot_token or "",
                        id="inp_target_token",
                        password=True,
                    )
                    yield Label("Community / Server ID:", classes="field_label")
                    yield Input(
                        value=self.config.target_server_id or "",
                        id="inp_target_server",
                    )

                yield Rule()
                with Horizontal(id="cfg_actions"):
                    yield Button("Save & Start", variant="success", id="btn_start")
                    yield Button("Save", variant="primary", id="btn_save")
                    yield Button("Back", id="btn_back")
        yield Footer()

    def on_mount(self) -> None:
        self._toggle_target_section()

    # ── show / hide the target platform section ──────────────────────────

    def _get_selected_mode(self) -> str:
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

    # ── save / start ─────────────────────────────────────────────────────

    def _collect_and_save(self) -> None:
        self.config.discord_bot_token = self.query_one("#inp_discord_token", Input).value.strip() or self.config.discord_bot_token
        self.config.discord_server_id = self.query_one("#inp_discord_server", Input).value.strip() or self.config.discord_server_id
        self.config.tool_mode = self._get_selected_mode()

        if self.config.tool_mode != "backup_only":
            self.config.target_platform = self._get_selected_platform()
            self.config.target_bot_token = self.query_one("#inp_target_token", Input).value.strip() or self.config.target_bot_token
            self.config.target_server_id = self.query_one("#inp_target_server", Input).value.strip() or self.config.target_server_id
        else:
            self.config.target_platform = "none"

        save_config(self.config, self.cfg_path)

    def _launch_mode(self) -> None:
        mode = self.config.tool_mode
        if mode == "direct_transfer":
            from src.ui.shuttle_screen import ShuttleScreen
            self.app.push_screen(ShuttleScreen(self.cfg_name, self.cfg_path))
        elif mode == "backup_transfer":
            from src.ui.backup_screen import BackupScreen
            self.app.push_screen(BackupScreen(self.cfg_name, self.cfg_path))
        else:  # backup_only
            from src.ui.backup_screen import BackupScreen
            self.app.push_screen(BackupScreen(self.cfg_name, self.cfg_path))

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_back":
            self.app.pop_screen()
        elif event.button.id == "btn_save":
            self._collect_and_save()
            self.notify("Configuration saved.", severity="information")
        elif event.button.id == "btn_start":
            self._collect_and_save()
            self._launch_mode()


# ──────────────────────────────────────────────────────────────────────────────
# App
# ──────────────────────────────────────────────────────────────────────────────

class ReaperApp(App):

    def on_mount(self) -> None:
        self.push_screen(ConfigSelectionScreen())


def run_disco_reaper_tui():
    app = ReaperApp()
    app.run()
