import re
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Header, Footer, Button, Label, Input, ListItem,
    ListView, Rule, RadioButton, RadioSet, Select,
)
from textual.screen import Screen, ModalScreen

from src.core.configuration import (
    get_available_configs, create_new_config, load_config, save_config,
)


# ──────────────────────────────────────────────────────────────────────────────
# Modal: create a new ReaperFiles-* config folder
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
            yield Input(placeholder="e.g. MyServer", id="new_config_input", tooltip="Enter a unique name for this config")
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
# Screen 1: pick (or create) a ReaperFiles-* config
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
    #config_sel_actions { height: auto; margin-top: 0; }
    #config_sel_actions Button { width: 1fr; margin: 0 1; }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="config_sel_container"):
            yield Label("Disco Reaper — Select Configuration", id="config_sel_title")
            with VerticalScroll(id="config_list_container"):
                yield ListView(id="config_list")
            with Horizontal(id="config_sel_actions"):
                yield Button("New Config", id="btn_new_config", variant="success", tooltip="Create a new configuration folder")
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
            lv.append(ListItem(Label(c), name=c))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        cfg_name = event.item.name
        cfg_path = Path(f"ReaperFiles-{cfg_name}") / "config.yaml"
        from src.ui.mode_screen import ModeScreen
        self.app.push_screen(ModeScreen(cfg_name, cfg_path))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_new_config":
            def cb(name: str | None):
                if name:
                    create_new_config(name)
                    self.refresh_configs()
                    # Immediately open the ConfigScreen for the new config
                    cfg_path = Path(f"ReaperFiles-{name}") / "config.yaml"
                    def on_config_saved(saved: bool = False):
                        if saved:
                            self.refresh_configs()
                            # Navigate into the ModeScreen
                            from src.ui.mode_screen import ModeScreen
                            self.app.push_screen(ModeScreen(name, cfg_path))
                    self.app.push_screen(ConfigScreen(name, cfg_path), on_config_saved)
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
    #target_section { height: auto; }
    #cfg_actions { height: auto; margin-top: 0; margin-bottom: 0; dock: bottom; }
    #cfg_actions Button { width: 1fr; margin: 0 1; }
    #footer_rule { margin: 0; }
    .fetch_row { height: auto; align: left middle; margin-bottom: 1; }
    .fetch_row Input { width: 1fr; }
    .fetch_row Button { width: auto; margin-left: 1; }
    #inp_discord_server { margin-bottom: 1; }
    """

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self, cfg_name: str, cfg_path: Path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cfg_name = cfg_name
        self.cfg_path = cfg_path
        self.config = load_config(cfg_path)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="cfg_outer"):
            with Container(id="cfg_container"):
                yield Label(f"Configuration — {self.cfg_name}", id="cfg_title")

                with VerticalScroll(id="cfg_scroll"):
                    # ── Discord ──────────────────────────────────────────────
                    yield Label("Discord Bot Token:", classes="field_label")
                    with Horizontal(classes="fetch_row"):
                        yield Input(
                            value=self.config.discord_bot_token or "",
                            id="inp_discord_token",
                            password=True,
                            placeholder="Paste Bot Token here",
                            tooltip="Enter your Discord BOT token from the Developer Portal"
                        )
                        yield Button("Validate", id="btn_fetch_guilds", variant="primary", tooltip="Verify token and fetch available Discord servers")
                    
                    yield Label("Server ID:", classes="field_label")
                    yield Select(
                        options=[],
                        id="inp_discord_server",
                        prompt="Validate Bot Token"
                    )

                    # ── Reaper Mode ──────────────────────────────────────────
                    yield Label("Reaper Mode", classes="section_title")
                    cur_mode = self.config.tool_mode or "backup_only"
                    with RadioSet(id="mode_radio"):
                        yield RadioButton(
                            "Shuttle Transfer  (direct migration)",
                            id="radio_direct",
                            value=(cur_mode == "direct_transfer")
                        )
                        yield RadioButton(
                            "Backup & Migrate  (backup first, then migrate)",
                            id="radio_backup",
                            value=(cur_mode == "backup_transfer")
                        )
                        yield RadioButton(
                            "Backup Only       (local backup, no migration)",
                            id="radio_bkonly",
                            value=(cur_mode == "backup_only")
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
                        yield Label("Bot Token:", classes="field_label")
                        with Horizontal(classes="fetch_row"):
                            yield Input(
                                value=self.config.target_bot_token or "",
                                id="inp_target_token",
                                password=True,
                                placeholder="Paste Target Bot Token",
                                tooltip="Enter the Bot token for the target platform"
                            )
                            yield Button("Validate", id="btn_fetch_target_servers", variant="primary", tooltip="Verify token and fetch available communities")
                        
                        yield Label("Community / Server ID:", classes="field_label")
                        yield Select(
                            options=[],
                            id="inp_target_server",
                            prompt="Validate Bot Token"
                        )
                        
                        yield Label("Target API URL:", classes="field_label")
                        yield Input(
                            value=self.config.target_api_url if (self.config.target_api_url and self.config.target_api_url != "default") else "",
                            id="inp_target_api",
                            placeholder="Leave this Empty for official instance",
                            tooltip="Enter the custom API url\nfor self hosted instances"
                        )

                yield Rule(id="footer_rule")
                with Horizontal(id="cfg_actions"):
                    yield Button("Save Configuration", variant="success", id="btn_save", tooltip="Save all changes to config.yaml")
                    yield Button("Back", id="btn_back")
        yield Footer()

    def on_mount(self) -> None:
        self._toggle_target_section()
        # If we have a token, try to populate the select widget on mount
        if self.config.discord_bot_token:
            self.run_worker(self._do_fetch_guilds(self.config.discord_bot_token, initial=True))
        
        # Also auto-fetch target servers if mode is not backup_only
        if self._get_selected_mode() != "backup_only":
            if self.config.target_bot_token:
                platform = self.config.target_platform
                if platform != "none":
                    self.run_worker(self._do_fetch_target_servers(
                        token=self.config.target_bot_token,
                        api_url=self.config.target_api_url,
                        platform=platform,
                        initial=True
                    ))

    async def _do_fetch_guilds(self, token: str, initial: bool = False) -> None:
        """Background worker to fetch guilds and update the Select widget."""
        from src.core.discord_reader import DiscordReader
        try:
            guilds = await DiscordReader.fetch_guilds(token)
            
            if not guilds:
                self.query_one("#btn_fetch_guilds", Button).variant = "warning"
                self.query_one("#inp_discord_server", Select).prompt = "No servers found"
                if not initial:
                    self.notify("No Discord servers found or invalid token.", severity="warning")
                return

            self.query_one("#btn_fetch_guilds", Button).variant = "success"
            options = [(name, gid) for name, gid in guilds]
            select_widget = self.query_one("#inp_discord_server", Select)
            select_widget.prompt = "Select a server"
            select_widget.set_options(options)
            
            # Restore saved value if it exists in the fetched list
            saved_id = self.config.discord_server_id
            if saved_id and any(gid == saved_id for _, gid in guilds):
                select_widget.value = saved_id
        except Exception as e:
            self.query_one("#btn_fetch_guilds", Button).variant = "warning"
            self.query_one("#inp_discord_server", Select).prompt = "Invalid token"
            if not initial:
                self.notify(f"Failed to fetch Discord servers: {e}", severity="error")

    async def _do_fetch_target_servers(self, token: str = None, api_url: str = None, platform: str = None, initial: bool = False) -> None:
        """Background worker to fetch target platform servers."""
        if not platform:
            platform = self._get_selected_platform()
        if not token:
            token = self.query_one("#inp_target_token", Input).value.strip()
        if not api_url:
            api_url = self.query_one("#inp_target_api", Input).value.strip() or "default"

        if not token:
            return

        servers = []
        try:
            if platform == "fluxer":
                from src.fluxer.writer import FluxerWriter
                servers = await FluxerWriter.fetch_guilds(token, api_url)
            elif platform == "stoat":
                from src.stoat.writer import StoatWriter
                servers = await StoatWriter.fetch_guilds(token, api_url)
            else:
                return
        except Exception as e:
            logger.error(f"Failed to fetch {platform} servers: {e}")
            try:
                self.query_one("#btn_fetch_target_servers", Button).variant = "warning"
                self.query_one("#inp_target_server", Select).prompt = "Invalid token"
            except Exception as dom_err:
                logger.debug(f"Could not update target server UI elements: {dom_err}")

            if not initial:
                self.notify(f"Failed to fetch {platform} servers: {e}", severity="error")
            return

        if not servers:
            try:
                self.query_one("#btn_fetch_target_servers", Button).variant = "warning"
                self.query_one("#inp_target_server", Select).prompt = "No servers found"
            except Exception:
                pass
            if not initial:
                self.notify(f"No {platform} servers found or invalid token.", severity="warning")
            return

        try:
            self.query_one("#btn_fetch_target_servers", Button).variant = "success"
            options = [(label, sid) for label, sid in servers]
            select_widget = self.query_one("#inp_target_server", Select)
            select_widget.prompt = "Select a server"
            select_widget.set_options(options)

            # Restore saved value
            saved_id = self.config.target_server_id
            if saved_id and any(sid == saved_id for _, sid in servers):
                select_widget.value = saved_id
        except Exception as dom_err:
            logger.debug(f"Could not update target server DOM: {dom_err}")


    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_fetch_guilds":
            token = self.query_one("#inp_discord_token", Input).value.strip()
            if not token:
                self.notify("Please enter a valid Bot Token first.", severity="error")
                return
            self.run_worker(self._do_fetch_guilds(token))
        elif event.button.id == "btn_fetch_target_servers":
            token = self.query_one("#inp_target_token", Input).value.strip()
            if not token:
                self.notify("Please enter a valid Target Platform Token first.", severity="error")
                return
            self.run_worker(self._do_fetch_target_servers())
        elif event.button.id == "btn_back":
            self.app.pop_screen()
        elif event.button.id == "btn_save":
            self._collect_and_save()
            self.notify("Configuration saved.", severity="information")
            self.dismiss(True)

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
        # 1. Discord Section
        self.config.discord_bot_token = self.query_one("#inp_discord_token", Input).value.strip() or None
        
        d_select = self.query_one("#inp_discord_server", Select)
        if d_select.value not in (Select.BLANK, Select.NULL):
            self.config.discord_server_id = str(d_select.value)

        # 2. Mode
        self.config.tool_mode = self._get_selected_mode()

        # 3. Target Section
        if self.config.tool_mode != "backup_only":
            self.config.target_platform = self._get_selected_platform()
            self.config.target_bot_token = self.query_one("#inp_target_token", Input).value.strip() or None
            
            t_select = self.query_one("#inp_target_server", Select)
            if t_select.value not in (Select.BLANK, Select.NULL):
                self.config.target_server_id = str(t_select.value)
            
            target_api = self.query_one("#inp_target_api", Input).value.strip()
            self.config.target_api_url = target_api or None
        else:
            self.config.target_platform = "none"

        save_config(self.config, self.cfg_path)

    def _launch_mode(self) -> None:
        pass # No longer needed



# ──────────────────────────────────────────────────────────────────────────────
# App
# ──────────────────────────────────────────────────────────────────────────────

class ReaperApp(App):
    SCREENS = {
        "config_selection": ConfigSelectionScreen,
    }

    def on_mount(self) -> None:
        self.push_screen("config_selection")
        self.theme = "dracula"

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
