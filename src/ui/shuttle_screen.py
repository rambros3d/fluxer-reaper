"""
Shuttle Screen – native Textual TUI for direct server-to-server migration.
Ports every operation from the old Rich CLI (MigrationCLI) into Textual
Screens, Modals, and Workers.
"""

import sys
import asyncio
import discord
import logging
import re
import time
import aiohttp
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Header, Footer, Button, Static, Label, Input,
    Checkbox, RadioButton, ProgressBar, RichLog, Rule,
    ListItem, ListView, RadioSet,
)
from textual.screen import Screen, ModalScreen
from textual import work
from textual.worker import Worker, WorkerState

from src.core.configuration import load_config, save_config
from src.core.base import MigrationContext
from src.core.audit import log_audit_event

import src.fluxer.roles_permissions as fluxer_roles
import src.stoat.roles_permissions as stoat_roles
import src.fluxer.emoji_stickers as fluxer_emoji_stickers
import src.stoat.emoji_stickers as stoat_emoji_stickers
import src.fluxer.server_metadata as fluxer_metadata
import src.stoat.server_metadata as stoat_metadata
import src.fluxer.migrate_message as fluxer_migrate
import src.stoat.migrate_message as stoat_migrate


# ---------------------------------------------------------------------------
# Rate-limit handler (global, shared with logging subsystem)
# ---------------------------------------------------------------------------
global_rate_limit_msg = ""
global_rate_limit_expires = 0.0


class RateLimitHandler(logging.Handler):
    """Intercepts library logs to capture rate-limit messages."""

    def __init__(self):
        super().__init__()

    def emit(self, record):
        try:
            msg = record.getMessage()
            if "retry" in msg.lower() and ("rate limit" in msg.lower() or "429" in msg):
                match = re.search(r"in ([\d.]+)\s*(?:seconds?|s)", msg, re.IGNORECASE)
                if match:
                    seconds = match.group(1)
                    platform = "API"
                    if "discord" in record.name.lower():
                        platform = "Discord"
                    elif "fluxer" in record.name.lower():
                        platform = "Fluxer"
                    elif "stoat" in record.name.lower():
                        platform = "Stoat"
                    global global_rate_limit_msg, global_rate_limit_expires
                    global_rate_limit_msg = f"{platform} rate limit {seconds}s"
                    try:
                        global_rate_limit_expires = time.time() + float(seconds)
                    except ValueError:
                        pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Shared modals
# ---------------------------------------------------------------------------

class ProgressModal(ModalScreen[None]):
    """Modal to display progress for any long-running operation."""

    def compose(self) -> ComposeResult:
        with Vertical(id="progress_dialog"):
            yield Label("Operation Status", id="progress_status")
            yield ProgressBar(total=None, show_eta=False, id="progress_bar")
            yield RichLog(id="progress_log", highlight=True, markup=True)
            yield Button("Close", id="btn_close_progress", disabled=True)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn_close_progress":
            self.dismiss(None)

    def write(self, message: str):
        self.query_one("#progress_log", RichLog).write(message)

    def set_status(self, status: str):
        self.query_one("#progress_status", Label).update(status)

    def set_progress(self, current: int, total: int):
        bar = self.query_one("#progress_bar", ProgressBar)
        bar.update(total=total, progress=current)

    def allow_close(self):
        btn = self.query_one("#btn_close_progress", Button)
        btn.disabled = False
        btn.variant = "success"
        self.query_one("#progress_bar", ProgressBar).update(total=100, progress=100)


class ShuttleConfigModal(ModalScreen[dict]):
    """Modal for editing all shuttle-mode tokens & IDs."""

    def __init__(self, config, target_platform: str):
        super().__init__()
        self.config = config
        self.target_platform = target_platform

    def compose(self) -> ComposeResult:
        with Vertical(id="shuttle_config_dialog"):
            yield Label("Shuttle Configuration", id="config_title")
            yield Label("Discord Bot Token:")
            yield Input(value=self.config.discord_bot_token or "", id="inp_d_token")
            yield Label("Discord Server ID:")
            yield Input(value=self.config.discord_server_id or "", id="inp_d_server")

            plat_label = "Fluxer" if self.target_platform == "fluxer" else "Stoat"
            yield Label(f"{plat_label} Bot Token:")
            yield Input(value=self.config.target_bot_token or "", id="inp_t_token")
            yield Label(f"{plat_label} Server/Community ID:")
            yield Input(value=self.config.target_server_id or "", id="inp_t_server")

            with Horizontal(id="config_buttons"):
                yield Button("Save", variant="success", id="btn_save")
                yield Button("Cancel", variant="primary", id="btn_cancel")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn_save":
            self.dismiss({
                "d_token": self.query_one("#inp_d_token", Input).value,
                "d_server": self.query_one("#inp_d_server", Input).value,
                "t_token": self.query_one("#inp_t_token", Input).value,
                "t_server": self.query_one("#inp_t_server", Input).value,
            })
        elif event.button.id == "btn_cancel":
            self.dismiss(None)


class PlatformSelectModal(ModalScreen[str]):
    """Modal for selecting target platform (fluxer / stoat)."""

    def compose(self) -> ComposeResult:
        with Vertical(id="platform_select_dialog"):
            yield Label("Select Target Platform", id="platform_title")
            yield Button("Fluxer", variant="primary", id="btn_fluxer")
            yield Button("Stoat", variant="warning", id="btn_stoat")
            yield Rule()
            yield Button("Cancel", id="btn_cancel_platform")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn_fluxer":
            self.dismiss("fluxer")
        elif event.button.id == "btn_stoat":
            self.dismiss("stoat")
        elif event.button.id == "btn_cancel_platform":
            self.dismiss(None)


class SubMenuModal(ModalScreen[str]):
    """A generic sub-menu modal that presents a list of labelled buttons."""

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


class ConfirmModal(ModalScreen[bool]):
    """Simple Yes / No confirmation modal."""

    def __init__(self, message: str, danger: bool = False):
        super().__init__()
        self._message = message
        self._danger = danger

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm_dialog"):
            yield Label(self._message, id="confirm_msg")
            with Horizontal(id="confirm_buttons"):
                yield Button("Confirm", variant="error" if self._danger else "success", id="btn_yes")
                yield Button("Cancel", variant="primary", id="btn_no")

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "btn_yes")


class ChannelPickerModal(ModalScreen[int]):
    """Modal listing Discord channels for single-channel selection."""

    def __init__(self, channels: list, categories: dict, label: str = "Select Channel"):
        super().__init__()
        self._channels = channels
        self._categories = categories
        self._label = label

    def compose(self) -> ComposeResult:
        with Vertical(id="chanpick_dialog"):
            yield Label(self._label, id="chanpick_title")
            with VerticalScroll(id="chanpick_scroll"):
                cat_grouped: dict[int | None, list] = {}
                for c in self._channels:
                    cat_id = getattr(c, "category_id", None) if not isinstance(c, dict) else c.get("parent_id")
                    cat_grouped.setdefault(cat_id, []).append(c)

                for cat_id in sorted(cat_grouped, key=lambda k: self._categories.get(k, "") if k else ""):
                    if cat_id is not None and cat_id in self._categories:
                        yield Label(f"[cyan]{self._categories[cat_id]}[/cyan]", classes="category_header")
                    for c in cat_grouped[cat_id]:
                        if isinstance(c, dict):
                            name = c.get("name", "Unnamed")
                            cid = c.get("id")
                        else:
                            name = c.name
                            cid = c.id
                        yield RadioButton(name, value=False, id=f"chpk_{cid}")

            with Horizontal(id="chanpick_buttons"):
                yield Button("Select", variant="success", id="btn_pick_ok")
                yield Button("Cancel", id="btn_pick_cancel")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn_pick_cancel":
            self.dismiss(None)
        elif event.button.id == "btn_pick_ok":
            for rb in self.query(RadioButton):
                if rb.value and rb.id and rb.id.startswith("chpk_"):
                    self.dismiss(int(rb.id.split("_", 1)[1]))
                    return
            # Nothing selected
            return


# ---------------------------------------------------------------------------
# ShuttleScreen — main screen for Shuttle Mode
# ---------------------------------------------------------------------------

class ShuttleScreen(Screen):
    """Native Textual screen for Shuttle (direct migration) mode."""

    CSS = """
    #shuttle_scroll {
        align: center middle;
    }
    
    #shuttle_container {
        width: 80%;
        height: auto;
        min-height: 25;
        border: solid #4641D9;
        padding: 1 2;
        margin: 2 0;
    }
    #shuttle_title {
        text-style: bold;
        color: #4641D9;
        margin-bottom: 1;
        content-align: center middle;
        width: 100%;
    }
    #shuttle_info {
        height: auto;
        margin-bottom: 2;
        border: tall cyan;
        padding: 1;
    }
    #shuttle_actions {
        height: auto;
        layout: vertical;
        align: center top;
        margin-top: 1;
    }
    #shuttle_actions Button {
        width: 100%;
        margin-bottom: 1;
    }
    /* Modals */
    #shuttle_config_dialog, #platform_select_dialog, #submenu_dialog, #confirm_dialog {
        width: 60%;
        height: auto;
        max-height: 80%;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }
    #progress_dialog {
        width: 80%;
        height: 80%;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }
    #chanpick_dialog {
        width: 70%;
        height: 75%;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }
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
    #config_title, #platform_title, #submenu_title, #confirm_msg, #chanpick_title {
        text-style: bold; margin-bottom: 1;
    }
    #config_buttons, #confirm_buttons, #chanpick_buttons {
        height: auto; margin-top: 1;
    }
    #config_buttons Button, #confirm_buttons Button, #chanpick_buttons Button {
        width: 1fr; margin: 0 1;
    }
    #progress_status { text-style: bold; margin-bottom: 1; }
    #progress_bar { margin-bottom: 1; }
    #progress_log { height: 1fr; margin-bottom: 1; border: solid $primary; }
    RadioButton:focus { background: transparent; border: none; }
    RadioButton > .radio-button--label { padding: 0 1; }
    RadioButton:focus > .radio-button--label { background: transparent; text-style: none; }
    """

    BINDINGS = [
        ("q", "app.exit", "Quit"),
        ("b", "go_back", "Back"),
    ]

    def __init__(self, cfg_name: str, cfg_path: Path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cfg_name = cfg_name
        self.config_path = cfg_path
        self.config = load_config(self.config_path)
        self.target_platform: str | None = None
        self.engine: MigrationContext | None = None
        self.validation_results: dict = {}
        self.tokens_valid = False
        self.permissions_complete = False

        # Register rate-limit handler
        rl = RateLimitHandler()
        logging.getLogger("discord").addHandler(rl)
        logging.getLogger("fluxer").addHandler(rl)
        logging.getLogger("stoat").addHandler(rl)

    # ── helpers ──────────────────────────────────────────────────────────

    def _base_dir(self) -> str:
        return f"Reaper-{self.cfg_name}"

    def _rebuild_engine(self):
        self.engine = MigrationContext(self.config, self.target_platform)

    def _update_info_labels(self):
        d_name = self.validation_results.get("discord_server_name")
        d_disp = f"[green]\"{d_name}\"[/green]" if d_name else "[red]NOT SET UP[/red]"
        self.query_one("#lbl_discord", Label).update(f"Discord: {d_disp}")

        if self.target_platform == "fluxer":
            t_name = self.validation_results.get("target_community_name")
            t_disp = f"[green]\"{t_name}\"[/green]" if t_name else "[red]NOT SET UP[/red]"
            self.query_one("#lbl_target", Label).update(f"Fluxer: {t_disp}")
        else:
            t_name = self.validation_results.get("target_community_name")
            t_disp = f"[green]\"{t_name}\"[/green]" if t_name else "[red]NOT SET UP[/red]"
            self.query_one("#lbl_target", Label).update(f"Stoat: {t_disp}")

        if not self.tokens_valid:
            val = "[red][INVALID][/red]"
        elif not self.permissions_complete:
            val = "[yellow][PERMISSION MISSING][/yellow]"
        else:
            val = "[green][VALID][/green]"
        self.query_one("#lbl_status", Label).update(f"Status: {val}")

        enabled = self.tokens_valid
        for bid in ("#btn_clone", "#btn_roles", "#btn_emojis", "#btn_metadata", "#btn_messages", "#btn_danger"):
            self.query_one(bid, Button).disabled = not enabled

    # ── compose ──────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="shuttle_scroll"):
            with Container(id="shuttle_container"):
                yield Label("Shuttle Mode", id="shuttle_title")
                with Vertical(id="shuttle_info"):
                    yield Label("Discord: [yellow]Loading...[/yellow]", id="lbl_discord")
                    yield Label("Target: [yellow]Loading...[/yellow]", id="lbl_target")
                    yield Label("Status: [yellow]Validating...[/yellow]", id="lbl_status")
                with Vertical(id="shuttle_actions"):
                    yield Button("Clone Server Template", id="btn_clone", disabled=True)
                    yield Button("Copy Roles & Permissions", id="btn_roles", disabled=True)
                    yield Button("Copy Emojis & Stickers", id="btn_emojis", disabled=True)
                    yield Button("Sync Server Profile", id="btn_metadata", disabled=True)
                    yield Button("Migrate Message History", id="btn_messages", disabled=True)
                    yield Rule()
                    yield Button("Configuration", id="btn_config")
                    yield Button("Danger Zone ⚠", id="btn_danger", variant="error", disabled=True)
                    yield Rule()
                    yield Button("Back", id="btn_back")
        yield Footer()

    # ── lifecycle ────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        # Platform is already configured in config.yaml via ConfigScreen
        self.target_platform = self.config.target_platform or "fluxer"
        self._rebuild_engine()
        self.run_validate()

    def action_go_back(self):
        self.app.pop_screen()

    # ── button routing ───────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed):
        bid = event.button.id
        if bid == "btn_back":
            self.app.pop_screen()
        elif bid == "btn_config":
            self._open_config()
        elif bid == "btn_clone":
            self.run_clone_template()
        elif bid == "btn_roles":
            self._open_roles_menu()
        elif bid == "btn_emojis":
            self._open_emoji_menu()
        elif bid == "btn_metadata":
            self._open_metadata_menu()
        elif bid == "btn_messages":
            self.run_migrate_messages()
        elif bid == "btn_danger":
            self._open_danger_menu()

    # ── (0) validation ───────────────────────────────────────────────────

    @work(exclusive=True)
    async def run_validate(self) -> None:
        self.validation_results = {
            "discord_token": False, "discord_bot_name": None,
            "discord_server": False, "discord_server_name": None,
            "discord_intents": {}, "discord_permissions": {},
            "target_token": False, "target_bot_name": None,
            "target_community": False, "target_community_name": None,
            "target_permissions": {},
            "discord_timeout": False, "target_timeout": False,
        }
        self.tokens_valid = False
        self.permissions_complete = False

        fillers = [
            "DISCORD_BOT_TOKEN", "FLUXER_BOT_TOKEN", "STOAT_BOT_TOKEN",
            "TARGET_BOT_TOKEN",
            "000000000000000000", "DISCORD_SERVER_ID", "FLUXER_COMMUNITY_ID",
            "STOAT_SERVER_ID", "TARGET_SERVER_ID", "", None,
        ]
        d_dummy = self.config.discord_bot_token in fillers or self.config.discord_server_id in fillers
        t_dummy = (self.config.target_bot_token or "") in fillers or (self.config.target_server_id or "") in fillers

        tasks = {}
        if not d_dummy:
            tasks["discord"] = asyncio.create_task(self.engine.discord_reader.validate())
        if not t_dummy:
            tasks["target"] = asyncio.create_task(self.engine.writer.validate())

        all_tasks = list(tasks.values())
        try:
            done = set()
            if all_tasks:
                done, _ = await asyncio.wait(all_tasks, timeout=10.0)

            # Discord
            dt = tasks.get("discord")
            if dt and dt in done:
                res = dt.result()
                self.validation_results["discord_token"] = res.get("token", False)
                self.validation_results["discord_bot_name"] = res.get("bot_name")
                self.validation_results["discord_server"] = res.get("server", False)
                self.validation_results["discord_server_name"] = res.get("server_name")
                self.validation_results["discord_intents"] = res.get("intents", {})
                self.validation_results["discord_permissions"] = res.get("permissions", {})
            elif dt and dt not in done:
                self.validation_results["discord_timeout"] = True
                dt.cancel()

            # Target platform
            tt = tasks.get("target")
            if tt and tt in done:
                res = tt.result()
                self.validation_results["target_token"] = res.get("token", False)
                self.validation_results["target_bot_name"] = res.get("bot_name")
                self.validation_results["target_community"] = res.get("community", False)
                self.validation_results["target_community_name"] = res.get("community_name")
                self.validation_results["target_permissions"] = res.get("permissions", {})
            elif tt and tt not in done:
                self.validation_results["target_timeout"] = True
                tt.cancel()

            # Compute validity
            discord_ok = self.validation_results.get("discord_token") and self.validation_results.get("discord_server")
            target_ok = self.validation_results.get("target_token") and self.validation_results.get("target_community")
            self.tokens_valid = bool(discord_ok and target_ok)

            # Set state folder
            if self.tokens_valid:
                srv_id = self.config.target_server_id
                srv_name = self.validation_results.get("target_community_name", "unknown")
                if srv_id and srv_name:
                    safe = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", srv_name)
                    self.engine.state.set_folder(str(srv_id), safe, base_dir=self._base_dir())

            # Check permissions
            self.permissions_complete = True
            if self.tokens_valid:
                di = self.validation_results.get("discord_intents", {})
                dp = self.validation_results.get("discord_permissions", {})
                if not all([di.get("message_content"), dp.get("view_channel"), dp.get("read_message_history")]):
                    self.permissions_complete = False
                tp = self.validation_results.get("target_permissions", {})
                if tp and not all(tp.values()):
                    self.permissions_complete = False
        except Exception:
            pass
        finally:
            for t in all_tasks:
                if not t.done():
                    t.cancel()

        self._update_info_labels()

    # ── (1) clone server template ────────────────────────────────────────

    @work(exclusive=True)
    async def run_clone_template(self) -> None:
        if self.target_platform == "fluxer":
            from src.fluxer.clone_server import sync_channel_state, migrate_channels
        else:
            from src.stoat.clone_server import sync_channel_state, migrate_channels

        modal = ProgressModal()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)

        try:
            modal.set_status("Fetching server structure...")
            await self.engine.start_connections()
            await sync_channel_state(self.engine)
            categories = await self.engine.discord_reader.get_categories()
            channels = await self.engine.discord_reader.get_channels()

            cached = sum(1 for c in categories if self.engine.state.get_fluxer_category_id(str(c.id)))
            cached += sum(1 for c in channels if self.engine.state.get_fluxer_channel_id(str(c.id)))
            total = len(categories) + len(channels)

            modal.write(f"[yellow]Found {total} items, {cached} already cloned.[/yellow]")
            modal.set_status("Cloning channels...")

            async def update_progress(item_name, status, current, total):
                color = "cyan" if status == "Copying" else "yellow"
                modal.set_status(f"[{color}]{status}: {item_name}[/{color}]")
                modal.set_progress(current, total)

            self.engine.is_running = True
            cloned_info = await migrate_channels(self.engine, progress_callback=update_progress, force=False)

            modal.write("[bold green]Server Template cloned![/bold green]")
            if cloned_info and cloned_info.get("structure"):
                lines = ["Successfully cloned channels and categories from Discord:"]
                cats = sorted(cloned_info["structure"].keys(), key=lambda x: (x == "No Category", x))
                for cat_name in cats:
                    ch_names = cloned_info["structure"][cat_name]
                    if cat_name in cloned_info.get("categories_created", []) or ch_names:
                        lines.append(f"- **{cat_name}**")
                        for n in sorted(ch_names):
                            lines.append(f"  - {n}")
                await log_audit_event(self.engine, "Server Template Cloned", "\n".join(lines))
            else:
                await log_audit_event(self.engine, "Server Template Cloned", "No new items were cloned.")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
        finally:
            self.engine.is_running = False
            await self.engine.close_connections()
            modal.set_status("Finished.")
            modal.allow_close()

    # ── (2) roles & permissions ──────────────────────────────────────────

    def _open_roles_menu(self):
        options = [
            ("sub_clone_roles", "Clone Roles & Role Permissions", "primary"),
            ("sub_sync_perms", "Sync Channel & Category Permissions", "warning"),
        ]
        def on_result(choice):
            if choice == "sub_clone_roles":
                self.run_clone_roles()
            elif choice == "sub_sync_perms":
                self.run_sync_permissions()
        self.app.push_screen(SubMenuModal("Roles & Permissions", options), on_result)

    @work(exclusive=True)
    async def run_clone_roles(self) -> None:
        roles_mod = fluxer_roles if self.target_platform == "fluxer" else stoat_roles
        modal = ProgressModal()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)

        try:
            modal.set_status("Checking existing roles...")
            await self.engine.start_connections()
            await roles_mod.sync_roles_state(self.engine)
            roles = await self.engine.discord_reader.get_roles()

            cached = sum(1 for r in roles if self.engine.state.get_target_role_id(str(r.id)))
            modal.write(f"[yellow]Found {len(roles)} roles, {cached} already cloned.[/yellow]")
            modal.set_status("Cloning roles...")

            async def update(name, current, total):
                modal.set_status(f"[cyan]Copying: {name}[/cyan]")
                modal.set_progress(current, total)

            self.engine.is_running = True
            cloned = await roles_mod.migrate_roles(self.engine, progress_callback=update, force=False)

            modal.write("[bold green]Role migration complete![/bold green]")
            if cloned:
                desc = "Successfully cloned roles:\n" + "\n".join(f"- {r}" for r in cloned)
                await log_audit_event(self.engine, "Roles Cloned", desc)
            else:
                await log_audit_event(self.engine, "Roles Cloned", "No new roles were cloned.")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
        finally:
            self.engine.is_running = False
            await self.engine.close_connections()
            modal.set_status("Finished.")
            modal.allow_close()

    @work(exclusive=True)
    async def run_sync_permissions(self) -> None:
        roles_mod = fluxer_roles if self.target_platform == "fluxer" else stoat_roles
        modal = ProgressModal()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)

        try:
            modal.set_status("Syncing permissions...")
            await self.engine.start_connections()

            async def update(name, current, total):
                modal.set_status(f"[cyan]Syncing: {name}[/cyan]")
                modal.set_progress(current, total)

            self.engine.is_running = True
            synced = await roles_mod.sync_permissions(self.engine, progress_callback=update)

            modal.write("[bold green]Permission sync complete![/bold green]")
            if synced and synced.get("structure"):
                lines = ["Synchronized permission overrides:"]
                cats = sorted(synced["structure"].keys(), key=lambda x: (x == "No Category", x))
                for cat_name in cats:
                    ch_names = synced["structure"][cat_name]
                    if cat_name in synced.get("categories_synced", []) or ch_names:
                        lines.append(f"- **{cat_name}**")
                        for n in sorted(ch_names):
                            lines.append(f"  - {n}")
                await log_audit_event(self.engine, "Permissions Synced", "\n".join(lines))
            else:
                await log_audit_event(self.engine, "Permissions Synced", "No permissions synchronized.")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
        finally:
            self.engine.is_running = False
            await self.engine.close_connections()
            modal.set_status("Finished.")
            modal.allow_close()

    # ── (3) emojis & stickers ────────────────────────────────────────────

    def _open_emoji_menu(self):
        options = [
            ("sub_emoji", "Sync Emojis only", "primary"),
            ("sub_sticker", "Sync Stickers only", "primary"),
            ("sub_both", "Sync Emojis & Stickers", "success"),
        ]
        def on_result(choice):
            types = []
            if choice == "sub_emoji":
                types = ["Emoji"]
            elif choice == "sub_sticker":
                types = ["Sticker"]
            elif choice == "sub_both":
                types = ["Emoji", "Sticker"]
            if types:
                self.run_copy_emojis(types)
        self.app.push_screen(SubMenuModal("Emojis & Stickers", options), on_result)

    @work(exclusive=True)
    async def run_copy_emojis(self, types_to_include: list[str]) -> None:
        asset_mod = stoat_emoji_stickers if self.target_platform == "stoat" else fluxer_emoji_stickers
        modal = ProgressModal()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)

        try:
            modal.set_status("Checking existing assets...")
            await self.engine.start_connections()
            await asset_mod.sync_assets_state(self.engine)

            modal.set_status("Copying assets...")

            async def update(name, item_type, current, total):
                modal.set_status(f"[cyan]Copying {item_type}: {name}[/cyan]")
                modal.set_progress(current, total)

            self.engine.is_running = True
            cloned = await asset_mod.migrate_emojis(
                self.engine,
                progress_callback=update,
                types_to_include=types_to_include,
                force=False,
            )

            modal.write("[bold green]Asset migration complete![/bold green]")
            if cloned and (cloned.get("Emoji") or cloned.get("Sticker")):
                lines = []
                if cloned.get("Emoji"):
                    lines.append("Emojis cloned:")
                    for n in cloned["Emoji"]:
                        lines.append(f"- {n}")
                if cloned.get("Sticker"):
                    lines.append("Stickers cloned:")
                    for n in cloned["Sticker"]:
                        lines.append(f"- {n}")
                await log_audit_event(self.engine, "Emojis & Stickers Cloned", "\n".join(lines))
            else:
                await log_audit_event(self.engine, "Emojis & Stickers Cloned", "No new assets cloned.")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
        finally:
            self.engine.is_running = False
            await self.engine.close_connections()
            modal.set_status("Finished.")
            modal.allow_close()

    # ── (4) server metadata sync ─────────────────────────────────────────

    def _open_metadata_menu(self):
        options = [
            ("sub_name", "Sync Name only", "primary"),
            ("sub_icon", "Sync Icon only", "primary"),
            ("sub_banner", "Sync Banner only", "primary"),
            ("sub_all_meta", "Sync Everything", "success"),
        ]
        def on_result(choice):
            comps = []
            if choice == "sub_name":
                comps = ["name"]
            elif choice == "sub_icon":
                comps = ["icon"]
            elif choice == "sub_banner":
                comps = ["banner"]
            elif choice == "sub_all_meta":
                comps = ["name", "icon", "banner"]
            if comps:
                self.run_sync_metadata(comps)
        self.app.push_screen(SubMenuModal("Sync Server Profile", options), on_result)

    @work(exclusive=True)
    async def run_sync_metadata(self, components: list[str]) -> None:
        meta_mod = fluxer_metadata if self.target_platform == "fluxer" else stoat_metadata
        modal = ProgressModal()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)

        try:
            modal.set_status("Syncing server metadata...")
            await self.engine.start_connections()

            async def progress_cb(item, status):
                color = "green" if status == "DONE" else "red" if status == "ERROR" else "yellow"
                modal.write(f"{item} [[bold {color}]{status}[/bold {color}]]")

            cloned = await meta_mod.sync_server_metadata(self.engine, progress_cb, components=components)

            modal.write("[bold green]Server profile sync finished![/bold green]")

            lines = ["Synchronized Community profile:"]
            if "name" in cloned:
                lines.append(f"- **Name**: {cloned['name']}")
            if "icon" in cloned:
                lines.append("- **Icon**")
            if "banner" in cloned:
                lines.append("- **Banner**")
            files = []
            if "icon" in cloned:
                ext = "gif" if cloned["icon"].startswith(b"GIF") else "png"
                files.append({"filename": f"icon.{ext}", "data": cloned["icon"]})
            if "banner" in cloned:
                ext = "gif" if cloned["banner"].startswith(b"GIF") else "png"
                files.append({"filename": f"banner.{ext}", "data": cloned["banner"]})
            if cloned:
                await log_audit_event(self.engine, "Server Profile Synced", "\n".join(lines), files=files)
            else:
                await log_audit_event(self.engine, "Server Profile Synced", "Nothing synchronized.")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
        finally:
            await self.engine.close_connections()
            modal.set_status("Finished.")
            modal.allow_close()

    # ── (5) message migration ────────────────────────────────────────────

    @work(exclusive=True)
    async def run_migrate_messages(self) -> None:
        if not self.tokens_valid:
            return

        migrate_mod = fluxer_migrate if self.target_platform == "fluxer" else stoat_migrate
        platform_name = self.target_platform.capitalize()

        modal = ProgressModal()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)

        try:
            modal.set_status("Fetching channels...")
            await self.engine.start_connections()

            full_d = await self.engine.discord_reader.get_channels()
            d_channels = [c for c in full_d if c.type in [discord.ChannelType.text, discord.ChannelType.news]]
            d_cats = await self.engine.discord_reader.get_categories()
            d_cat_map = {c.id: c.name for c in d_cats}

            if not d_channels:
                modal.write("[yellow]No text channels found.[/yellow]")
                modal.allow_close()
                return

            # Pick source channel via modal
            self.app.pop_screen()  # pop progress temporarily

            loop = asyncio.get_running_loop()
            src_future = loop.create_future()

            def on_src(cid):
                if not src_future.done():
                    src_future.set_result(cid)

            self.app.push_screen(ChannelPickerModal(d_channels, d_cat_map, "Select Source Discord Channel"), on_src)
            src_id = await src_future

            if src_id is None:
                await self.engine.close_connections()
                return

            source_channel = next(c for c in d_channels if c.id == src_id)

            # Pick target channel
            modal2_status = ProgressModal()
            self.app.push_screen(modal2_status)
            await asyncio.sleep(0.1)
            modal2_status.set_status(f"Fetching {platform_name} channels...")

            full_f = await self.engine.writer.get_channels()
            f_channels = [c for c in full_f if c.get("name") not in ["reaper_logs", "reaper-logs"] and c.get("type") not in [2, 4]]

            if not f_channels:
                modal2_status.write(f"[yellow]No channels found in {platform_name} community.[/yellow]")
                modal2_status.allow_close()
                await self.engine.close_connections()
                return

            # Auto-match
            mapped_id = self.engine.state.get_fluxer_channel_id(str(source_channel.id))
            recommended = None
            if mapped_id:
                recommended = next((c for c in f_channels if str(c.get("id", "")) == mapped_id), None)
            if not recommended:
                recommended = next((c for c in f_channels if c.get("name") == source_channel.name), None)

            self.app.pop_screen()  # pop status

            target_cat_names = {str(c.get("id")): c.get("name") for c in full_f if c.get("type") == 4}

            tgt_future = loop.create_future()

            def on_tgt(cid):
                if not tgt_future.done():
                    tgt_future.set_result(cid)

            self.app.push_screen(ChannelPickerModal(f_channels, target_cat_names, f"Select Target {platform_name} Channel"), on_tgt)
            tgt_id = await tgt_future

            if tgt_id is None:
                await self.engine.close_connections()
                return

            target_channel = next(c for c in f_channels if c.get("id") == tgt_id)

            # Determine after_id
            after_id = None
            last_migrated = self.engine.state.get_last_message_id(str(source_channel.id))
            if last_migrated:
                after_id = int(last_migrated)

            # Analyze
            modal = ProgressModal()
            self.app.push_screen(modal)
            await asyncio.sleep(0.1)
            modal.set_status("Analyzing channel...")

            self.engine.is_running = True
            stats = {"messages": 0, "threads": 0, "attachments": 0}

            async def update_scan(count):
                modal.set_status(f"[cyan]Scanned {count} items...")

            stats = await migrate_mod.analyze_migration(
                self.engine,
                source_channel_id=source_channel.id,
                after_message_id=after_id,
                progress_callback=update_scan,
            )
            self.engine.is_running = False

            modal.write(f"[cyan]Messages: {stats['messages']}, Threads: {stats['threads']}, Attachments: {stats['attachments']}[/cyan]")
            modal.write(f"Migrating Discord [cyan]#{source_channel.name}[/cyan] → {platform_name} [green]#{target_channel.get('name')}[/green]")
            modal.set_status("Migrating messages...")

            total_messages = stats["messages"]
            self.engine.is_running = True

            async def update_msg(count):
                modal.set_status(f"[cyan]Migrated {count}/{total_messages} messages...")
                modal.set_progress(count, total_messages)

            result = await migrate_mod.migrate_messages(
                self.engine,
                source_channel_id=source_channel.id,
                target_channel_id=target_channel.get("id"),
                after_message_id=after_id,
                progress_callback=update_msg,
            )

            if self.engine.is_running:
                modal.write(f"[bold green]Success! {result['messages']} messages migrated.[/bold green]")
                event_title = "Message History Migrated"
            else:
                modal.write(f"[bold yellow]Interrupted! {result['messages']} messages migrated.[/bold yellow]")
                event_title = "Message History Migration Interrupted"

            lines = [f"Migrated Discord #{source_channel.name} → {platform_name} #{target_channel.get('name')}:"]
            lines.append(f"{result['messages']} messages, {result['attachments']} attachments, {result['threads']} threads")
            await log_audit_event(self.engine, event_title, "\n".join(lines))

        except Exception as e:
            err = str(e)
            if "MissingPermission" in err and "Masquerade" in err:
                modal.write("[bold red]Bot is missing the 'Masquerade' permission.[/bold red]")
            else:
                modal.write(f"[bold red]Error: {err}[/bold red]")
        finally:
            self.engine.is_running = False
            await self.engine.close_connections()
            modal.set_status("Finished.")
            modal.allow_close()

    # ── (6) configuration ────────────────────────────────────────────────

    def _open_config(self):
        def on_result(data: dict | None):
            if data is None:
                return
            self.config.discord_bot_token = data["d_token"] or self.config.discord_bot_token
            self.config.discord_server_id = data["d_server"] or self.config.discord_server_id
            self.config.target_bot_token = data["t_token"] or self.config.target_bot_token
            self.config.target_server_id = data["t_server"] or self.config.target_server_id
            save_config(self.config, self.config_path)
            self._rebuild_engine()
            self.run_validate()
        self.app.push_screen(ShuttleConfigModal(self.config, self.target_platform), on_result)

    # ── (7) danger zone ──────────────────────────────────────────────────

    def _open_danger_menu(self):
        options = [
            ("dz_del_channels", "Delete ALL Channels & Categories", "error"),
            ("dz_reset_perms", "Reset ALL Channel Permissions", "error"),
            ("dz_del_roles", "Delete ALL Roles", "error"),
            ("dz_del_assets", "Delete ALL Emojis & Stickers", "error"),
        ]
        def on_result(choice):
            if choice == "dz_del_channels":
                self._confirm_danger("Delete ALL channels and categories? This is IRREVERSIBLE.", self.run_dz_delete_channels)
            elif choice == "dz_reset_perms":
                self._confirm_danger("Reset ALL channel permissions? This is IRREVERSIBLE.", self.run_dz_reset_perms)
            elif choice == "dz_del_roles":
                self._confirm_danger("Delete ALL roles? This is IRREVERSIBLE.", self.run_dz_delete_roles)
            elif choice == "dz_del_assets":
                self._confirm_danger("Delete ALL emojis and stickers? This is IRREVERSIBLE.", self.run_dz_delete_assets)
        self.app.push_screen(SubMenuModal("⚠ DANGER ZONE ⚠", options), on_result)

    def _confirm_danger(self, message: str, callback):
        def on_confirm(confirmed: bool):
            if confirmed:
                callback()
        self.app.push_screen(ConfirmModal(message, danger=True), on_confirm)

    @work(exclusive=True)
    async def run_dz_delete_channels(self) -> None:
        if self.target_platform == "fluxer":
            from src.fluxer.danger_zone import danger_delete_all_channels
        else:
            from src.stoat.danger_zone import danger_delete_all_channels

        modal = ProgressModal()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)

        try:
            modal.set_status("[red]Deleting channels...")
            await self.engine.start_target_only()

            async def on_deleted(name, current, total):
                modal.set_status(f"[red]Deleting: {name}")
                modal.set_progress(current, total)

            count = await danger_delete_all_channels(self.engine, progress_callback=on_deleted)
            modal.write(f"[bold green]{count} channels/categories deleted.[/bold green]")
            await log_audit_event(self.engine, "Danger Zone: Channels Wiped", f"Deleted {count} channels and categories.")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
        finally:
            await self.engine.close_target_only()
            modal.set_status("Finished.")
            modal.allow_close()

    @work(exclusive=True)
    async def run_dz_reset_perms(self) -> None:
        if self.target_platform == "fluxer":
            from src.fluxer.danger_zone import danger_reset_channel_permissions
        else:
            from src.stoat.danger_zone import danger_reset_channel_permissions

        modal = ProgressModal()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)

        try:
            modal.set_status("[red]Resetting permissions...")
            await self.engine.start_target_only()

            async def on_reset(name, current, total):
                modal.set_status(f"[red]Resetting: {name}")
                modal.set_progress(current, total)

            count = await danger_reset_channel_permissions(self.engine, progress_callback=on_reset)
            modal.write(f"[bold green]Permissions reset on {count} items.[/bold green]")
            await log_audit_event(self.engine, "Danger Zone: Permissions Wiped", f"Reset permissions on {count} items.")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
        finally:
            await self.engine.close_target_only()
            modal.set_status("Finished.")
            modal.allow_close()

    @work(exclusive=True)
    async def run_dz_delete_roles(self) -> None:
        if self.target_platform == "fluxer":
            from src.fluxer.danger_zone import danger_delete_all_roles
        else:
            from src.stoat.danger_zone import danger_delete_all_roles

        modal = ProgressModal()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)

        try:
            modal.set_status("[red]Deleting roles...")
            await self.engine.start_target_only()

            async def on_deleted(name, current, total):
                modal.set_status(f"[red]Deleting role: {name}")
                modal.set_progress(current, total)

            count = await danger_delete_all_roles(self.engine, progress_callback=on_deleted)
            modal.write(f"[bold green]{count} roles deleted.[/bold green]")
            await log_audit_event(self.engine, "Danger Zone: Roles Wiped", f"Deleted {count} roles.")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
        finally:
            await self.engine.close_target_only()
            modal.set_status("Finished.")
            modal.allow_close()

    @work(exclusive=True)
    async def run_dz_delete_assets(self) -> None:
        if self.target_platform == "fluxer":
            from src.fluxer.danger_zone import danger_delete_all_emojis_and_stickers
        else:
            from src.stoat.danger_zone import danger_delete_all_emojis_and_stickers

        modal = ProgressModal()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)

        try:
            modal.set_status("[red]Deleting assets...")
            await self.engine.start_target_only()

            async def on_deleted(name, asset_type, current, total):
                modal.set_status(f"[red]Deleting {asset_type}: {name}")
                modal.set_progress(current, total)

            counts = await danger_delete_all_emojis_and_stickers(self.engine, progress_callback=on_deleted)
            modal.write(f"[bold green]{counts.get('emojis', 0)} emojis, {counts.get('stickers', 0)} stickers deleted.[/bold green]")
            await log_audit_event(self.engine, "Danger Zone: Assets Wiped", f"Deleted {counts.get('emojis', 0)} emojis and {counts.get('stickers', 0)} stickers.")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
        finally:
            await self.engine.close_target_only()
            modal.set_status("Finished.")
            modal.allow_close()
