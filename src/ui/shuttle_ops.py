"""
ShuttlePane – self-contained shuttle (migration) operations widget.
Embedded inside ModeScreen's "Migrate" tab.
"""

import asyncio
import logging
import re
import time
import aiohttp
import traceback
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Vertical, Horizontal, VerticalScroll
from textual.widgets import Button, Label, Rule
from textual import work

from src.core.configuration import load_config
from src.core.base import MigrationContext
from src.core.audit import log_audit_event
from src.ui.modals import (
    ProgressScreen, SubMenuModal, ChannelPickerScreen, OptionSelectModal,
)

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

logger = logging.getLogger(__name__)


class RateLimitHandler(logging.Handler):
    """Intercepts library logs to capture rate-limit messages."""

    def __init__(self):
        super().__init__()

    def emit(self, record):
        global global_rate_limit_msg, global_rate_limit_expires
        msg = record.getMessage()
        if "rate" in msg.lower() and "limit" in msg.lower():
            global_rate_limit_msg = msg
            try:
                parts = msg.split()
                for i, p in enumerate(parts):
                    if p.lower() in ("retry_after", "retry"):
                        secs = float(parts[i + 1].strip("s,."))
                        global_rate_limit_expires = time.time() + secs
                        break
                    if p.lower() == "after":
                        secs = float(parts[i + 1].strip("s,."))
                        global_rate_limit_expires = time.time() + secs
                        break
                else:
                    for p in parts:
                        try:
                            secs = float(p.strip("s,."))
                            if 0 < secs < 3600:
                                global_rate_limit_expires = time.time() + secs
                                break
                        except ValueError:
                            continue
            except Exception:
                pass


class ShuttlePane(Container):
    """Shuttle (migration) operations pane — clone, roles, emojis, metadata, messages, danger zone."""

    DEFAULT_CSS = """
    ShuttlePane { height: auto; width: 100%; }
    ShuttlePane #sp_info {
        height: auto; border: tall cyan; padding: 1; margin-bottom: 1;
    }
    #sp_info_split { height: auto; layout: horizontal; }
    .info_pane { width: 1fr; height: auto; }
    .info_pane Label { width: 100%; }
    .pane_header { text-style: bold; color: $accent; margin-bottom: 1; }
    
    ShuttlePane #sp_actions { height: auto; }
    ShuttlePane #sp_actions Button { width: 100%; margin-bottom: 1; }
    """

    def __init__(self, cfg_name: str, cfg_path: Path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cfg_name = cfg_name
        self.config_path = cfg_path
        self.config = load_config(cfg_path)
        self.target_platform = self.config.target_platform or "fluxer"
        self.engine: MigrationContext | None = None
        self.validation_results: dict = {}
        self.tokens_valid = False
        self.permissions_complete = False

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            with Vertical(id="sp_info"):
                with Horizontal(id="sp_info_split"):
                    with Vertical(classes="info_pane"):
                        yield Label("Discord", classes="pane_header")
                        yield Label("Server: [yellow]Loading...[/yellow]", id="sp_lbl_d_server")
                        yield Label("Bot: [yellow]Loading...[/yellow]", id="sp_lbl_d_bot")
                    
                    with Vertical(classes="info_pane"):
                        yield Label("Target", id="sp_lbl_t_header", classes="pane_header")
                        yield Label("Community: [yellow]Loading...[/yellow]", id="sp_lbl_t_comm")
                        yield Label("Bot: [yellow]Loading...[/yellow]", id="sp_lbl_t_bot")
                
                yield Rule()
                yield Label("Status: [yellow]Validating...[/yellow]", id="sp_lbl_status")
            with Vertical(id="sp_actions"):
                yield Button("Clone Server Template", id="sp_clone", disabled=True)
                yield Button("Sync Server Settings", id="sp_sync", disabled=True)
                yield Button("Migrate Message History", id="sp_messages", disabled=True, variant="primary")
                yield Rule()
                yield Button("Danger Zone ⚠", id="sp_danger", variant="error", disabled=True, flat=True)

    def on_mount(self) -> None:
        self._rebuild_engine()
        self.run_validate()

    def reload_config(self) -> None:
        self.config = load_config(self.config_path)
        self.target_platform = self.config.target_platform or "fluxer"
        self._rebuild_engine()
        self.run_validate()

    def _base_dir(self) -> str:
        return f"Reaper-{self.cfg_name}"

    def _rebuild_engine(self):
        source = "backup" if self.config.tool_mode == "backup_transfer" else "live"
        self.engine = MigrationContext(self.config, self.target_platform, source_mode=source)

    # ── labels ────────────────────────────────────────────────────────────

    def _update_info_labels(self):
        v = self.validation_results

        # Discord
        d_name = v.get("discord_server_name")
        d_bot = v.get("discord_bot_name")
        
        if v.get("discord_timeout"):
            s_disp, b_disp = "[red]TIMEOUT[/red]", "[red]TIMEOUT[/red]"
        elif v.get("discord_token") and v.get("discord_server"):
            s_disp = f'[green]"{d_name}"[/green]'
            b_disp = f'[green]{d_bot}[/green]'
        elif v.get("discord_token") is False:
            s_disp, b_disp = "[red]INVALID TOKEN[/red]", "[red]INVALID TOKEN[/red]"
        else:
            s_disp, b_disp = "[red]NOT SET UP[/red]", "[red]NOT SET UP[/red]"
            
        self.query_one("#sp_lbl_d_server", Label).update(f"Server: {s_disp}")
        if self.config.tool_mode == "backup_transfer":
            b_disp = "[green]LOCAL BACKUP[/green]" if v.get("discord_server") else "[red]NOT FOUND[/red]"
            self.query_one("#sp_lbl_d_bot", Label).update(f"Source: {b_disp}")
        else:
            self.query_one("#sp_lbl_d_bot", Label).update(f"Bot: {b_disp}")

        # Target
        plat = "Fluxer" if self.target_platform == "fluxer" else "Stoat"
        t_name = v.get("target_community_name")
        t_bot = v.get("target_bot_name")
        
        self.query_one("#sp_lbl_t_header", Label).update(plat)
        
        if v.get("target_timeout"):
            c_disp, tb_disp = "[red]TIMEOUT[/red]", "[red]TIMEOUT[/red]"
        elif v.get("target_token") and v.get("target_community"):
            c_disp = f'[green]"{t_name}"[/green]'
            tb_disp = f'[green]{t_bot}[/green]'
        elif v.get("target_token") is False:
            c_disp, tb_disp = "[red]INVALID TOKEN[/red]", "[red]INVALID TOKEN[/red]"
        else:
            c_disp, tb_disp = "[red]NOT SET UP[/red]", "[red]NOT SET UP[/red]"
            
        self.query_one("#sp_lbl_t_comm", Label).update(f"Community: {c_disp}")
        self.query_one("#sp_lbl_t_bot", Label).update(f"Bot: {tb_disp}")

        # Status
        if not self.tokens_valid:
            val = "[red][INVALID][/red]"
        elif not self.permissions_complete:
            val = "[yellow][MISSING PERMISSIONS][/yellow]"
        else:
            val = "[green][VALID][/green]"
        self.query_one("#sp_lbl_status", Label).update(f"Status: {val}")

        # Buttons
        for bid in ("#sp_clone", "#sp_sync", "#sp_messages", "#sp_danger"):
            self.query_one(bid, Button).disabled = not self.tokens_valid

    # ── validation ────────────────────────────────────────────────────────

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

            discord_ok = self.validation_results.get("discord_token") and self.validation_results.get("discord_server")
            target_ok = self.validation_results.get("target_token") and self.validation_results.get("target_community")
            self.tokens_valid = bool(discord_ok and target_ok)

            if self.tokens_valid:
                srv_id = self.config.target_server_id
                srv_name = self.validation_results.get("target_community_name", "unknown")
                if srv_id and srv_name:
                    safe = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", srv_name)
                    self.engine.state.set_folder(str(srv_id), safe, base_dir=self._base_dir())

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

    # ── button routing ────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if not bid or not bid.startswith("sp_"):
            return
        if bid == "sp_clone":
            self._open_clone_menu()
        elif bid == "sp_sync":
            self._open_sync_menu()
        elif bid == "sp_messages":
            self.run_migrate_messages()
        elif bid == "sp_danger":
            self._open_danger_menu()

    # ── (1) clone server template (combined) ─────────────────────────────

    def _open_clone_menu(self):
        options = [
            ("sub_clone_roles", "Roles & Role Permissions"),
            ("sub_clone_channels", "Server Structure [Channels & Categories]"),
            ("sub_sync_perms", "Channel & Category Permissions"),
        ]
        def on_result(choices):
            if choices:
                # Order defined: Roles -> Channels -> Permissions
                ordered = [c for c in ["sub_clone_roles", "sub_clone_channels", "sub_sync_perms"] if c in choices]
                self.run_batch_clone(ordered)
        self.app.push_screen(OptionSelectModal("Clone Server Template", options), on_result)

    # ── (2) sync server settings (combined) ────────────────────────────

    def _open_sync_menu(self):
        options = [
            ("sub_emoji", "Custom Emojis"),
            ("sub_sticker", "Custom Stickers"),
            ("sub_name", "Server Name"),
            ("sub_icon", "Server Icon"),
            ("sub_banner", "Server Banner"),
        ]
        def on_result(choices):
            if choices:
                self.run_batch_sync(choices)
        self.app.push_screen(OptionSelectModal("Sync Server Settings", options), on_result)

    # ── batch workers ──────────────────────────────────────────────────

    @work(exclusive=True)
    async def run_batch_clone(self, selections: list[str]) -> None:
        modal = ProgressScreen(log_level=self.config.migration.log_level)
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)
        connections_started = False
        try:
            # Phase 1: Connect early to fetch both source and target structure for preview
            modal.set_status("Connecting to Source and Target Servers for Preview...")
            try:
                await self.engine.start_connections()
                connections_started = True
            except Exception as e:
                logger.warning(f"Could not pre-connect for Clone preview: {e}")

            modal.set_status(f"Awaiting Confirmation for {len(selections)} Operations...")
            
            # Fetch and display live preview with presence highlighting
            preview = await self._fetch_clone_preview(selections) if connections_started else {}
            
            if "roles" in preview:
                roles = preview["roles"]
                modal.write(f"[bold cyan]Roles to be Cloned ({len(roles)}):[/bold cyan]")
                for name, exists in roles[:15]:
                    if exists:
                        modal.write(f"  - [green]{name}[/green]")
                    else:
                        modal.write(f"  - {name}")
                if len(roles) > 15:
                    modal.write(f"  [dim]... and {len(roles)-15} more[/dim]")
                modal.write("")

            if "structure" in preview:
                structure = preview["structure"]
                total_ch = sum(len(chans) for chans in structure.values())
                num_cats = sum(1 for k in structure if k is not None)
                modal.write(f"[bold cyan]Server Structure ({num_cats} Categories, {total_ch} Channels):[/bold cyan]")
                
                # Show uncategorized channels first at the top
                if None in structure:
                    _, _, uncat_channels = structure[None]
                    for ch_name, ch_exists in uncat_channels:
                        if ch_exists:
                            modal.write(f"  - [green]# {ch_name}[/green]")
                        else:
                            modal.write(f"  - # {ch_name}")
                
                for cat_id, (cat_name, cat_exists, channels) in structure.items():
                    if cat_id is None:
                        continue  # already shown above
                    cat_color = "green" if cat_exists else "bold yellow"
                    modal.write(f"  [{cat_color}]📁 {cat_name}[/{cat_color}]")
                    for ch_name, ch_exists in channels:
                        if ch_exists:
                            modal.write(f"    - [green]# {ch_name}[/green]")
                        else:
                            modal.write(f"    - # {ch_name}")
                modal.write("")

            if connections_started:
                # Add highlighting note
                target_valid = await self.engine.writer.validate()
                community_name = target_valid.get("community_name", "the target")
                modal.write(f"[dim]Note: entities shown in 'green' are already present in {community_name} community[/dim]")
                modal.write("")

            choice = await modal.phase_wait_confirm(
                btn_start_label="Start Cloning",
                btn_id_label="Force Clone (may create duplicates)",
                show_id=True
            )
            if choice == "btn_back":
                modal.dismiss()
                self._open_clone_menu()
                return
            elif choice == "btn_main_menu":
                modal.dismiss()
                self.app.switch_screen("config_selection")
                return

            force_mode = (choice == "btn_start_id")
            modal.cancel_callback = lambda: setattr(self.engine, "is_running", False)
            modal.phase_progress()
            modal.set_status("Cloning Server Template")
            
            # Connections already started above
            if not connections_started:
                await self.engine.start_connections()
            self.engine.is_running = True

            results = {}
            for sel in selections:
                if not self.engine.is_running: break
                if sel == "sub_clone_roles":
                    results["roles"] = await self._logic_clone_roles(modal, force=force_mode)
                elif sel == "sub_clone_channels":
                    results["channels"] = await self._logic_clone_channels(modal, force=force_mode)
                elif sel == "sub_sync_perms":
                    results["perms"] = await self._logic_sync_permissions(modal)
            
            modal.phase_report("Clone Template Complete")
            report = self._format_clone_report(results)
            modal.write(report)
        except Exception as e:
            logger.error(f"Batch Cloning Error: {e}\n{traceback.format_exc()}")
            modal.write(f"[bold red]Error: {e}[/bold red]")
            modal.phase_report("Batch Operation", "error")
        finally:
            self.engine.is_running = False
            await self.engine.close_connections()

    @work(exclusive=True)
    async def run_batch_sync(self, selections: list[str]) -> None:
        modal = ProgressScreen(log_level=self.config.migration.log_level)
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)
        try:
            modal.set_status("Awaiting Confirmation to Sync Server Settings...")
            choice = await modal.phase_wait_confirm(
                btn_start_label="Start Syncing",
                btn_id_label="Force Sync",
                show_id=True
            )
            if choice == "btn_back":
                modal.dismiss()
                self._open_sync_menu()
                return
            elif choice == "btn_main_menu":
                modal.dismiss()
                self.app.switch_screen("config_selection")
                return

            force_mode = (choice == "btn_start_id")
            modal.cancel_callback = lambda: setattr(self.engine, "is_running", False)
            modal.phase_progress()
            modal.set_status("Syncing Server Settings")
            await self.engine.start_connections()
            self.engine.is_running = True

            results = {}
            # Separate asset sync from metadata sync
            asset_types = []
            if "sub_emoji" in selections: asset_types.append("Emoji")
            if "sub_sticker" in selections: asset_types.append("Sticker")
            if asset_types:
                results["assets"] = await self._logic_copy_assets(modal, asset_types, force=force_mode)
            
            meta_comps = []
            if "sub_name" in selections: meta_comps.append("name")
            if "sub_icon" in selections: meta_comps.append("icon")
            if "sub_banner" in selections: meta_comps.append("banner")
            if meta_comps:
                results["metadata"] = await self._logic_sync_metadata(modal, meta_comps)
            
            modal.phase_report("Sync Settings Complete")
            report = self._format_sync_report(results)
            modal.write(report)
        except Exception as e:
            logger.error(f"Batch Sync Error: {e}\n{traceback.format_exc()}")
            modal.write(f"[bold red]Error: {e}[/bold red]")
            modal.phase_report("Batch Operation", "error")
        finally:
            self.engine.is_running = False
            await self.engine.close_connections()

    # ── logic blocks (internal) ────────────────────────────────────────

    async def _logic_clone_channels(self, modal: ProgressScreen, force: bool = False):
        if self.target_platform == "fluxer":
            from src.fluxer.clone_server import sync_channel_state, migrate_channels
        else:
            from src.stoat.clone_server import sync_channel_state, migrate_channels
        
        modal.set_item_status("Processing Server Structure...")
        await sync_channel_state(self.engine)
        categories = await self.engine.discord_reader.get_categories()
        channels = await self.engine.discord_reader.get_channels()
        
        async def update_progress(item_name, status, current, total):
            color = "cyan" if status == "Copying" else "yellow"
            modal.set_item_status(f"[{color}]{status}: {item_name}[/{color}]")
            modal.set_progress(current, total)
        
        cloned_info = await migrate_channels(self.engine, progress_callback=update_progress, force=force)
        if cloned_info and cloned_info.get("structure"):
            lines = ["Successfully cloned channels and categories:"]
            cats = sorted(cloned_info["structure"].keys(), key=lambda x: (x == "No Category", x))
            for cat_name in cats:
                ch_names = cloned_info["structure"][cat_name]
                if cat_name in cloned_info.get("categories_created", []) or ch_names:
                    lines.append(f"- **{cat_name}**")
                    for n in sorted(ch_names): lines.append(f"  - {n}")
            await log_audit_event(self.engine, "Channels Cloned", "\n".join(lines))
        return cloned_info

    async def _logic_clone_roles(self, modal: ProgressScreen, force: bool = False):
        roles_mod = fluxer_roles if self.target_platform == "fluxer" else stoat_roles
        modal.set_item_status("Processing Roles...")
        await roles_mod.sync_roles_state(self.engine)
        
        async def update(name, current, total):
            modal.set_item_status(f"[cyan]Copying Role: {name}[/cyan]")
            modal.set_progress(current, total)
        
        cloned = await roles_mod.migrate_roles(self.engine, progress_callback=update, force=force)
        if cloned:
            await log_audit_event(self.engine, "Roles Cloned", "Successfully cloned roles:\n" + "\n".join(f"- {r}" for r in cloned))
        return cloned

    async def _logic_sync_permissions(self, modal: ProgressScreen):
        roles_mod = fluxer_roles if self.target_platform == "fluxer" else stoat_roles
        modal.set_item_status("Syncing Permissions...")
        
        async def update(name, current, total):
            modal.set_item_status(f"[cyan]Syncing Perms: {name}[/cyan]")
            modal.set_progress(current, total)
            
        synced = await roles_mod.sync_permissions(self.engine, progress_callback=update)
        if synced and synced.get("structure"):
            lines = ["Synchronized permission overrides:"]
            cats = sorted(synced["structure"].keys(), key=lambda x: (x == "No Category", x))
            for cat_name in cats:
                ch_names = synced["structure"][cat_name]
                if cat_name in synced.get("categories_synced", []) or ch_names:
                    lines.append(f"- **{cat_name}**")
                    for n in sorted(ch_names): lines.append(f"  - {n}")
            await log_audit_event(self.engine, "Permissions Synced", "\n".join(lines))
        return synced

    async def _logic_copy_assets(self, modal: ProgressScreen, types_to_include: list[str], force: bool = False):
        asset_mod = stoat_emoji_stickers if self.target_platform == "stoat" else fluxer_emoji_stickers
        modal.set_item_status("Processing Assets...")
        await asset_mod.sync_assets_state(self.engine)
        
        async def update(name, item_type, current, total):
            modal.set_item_status(f"[cyan]Copying {item_type}: {name}[/cyan]")
            modal.set_progress(current, total)
            
        cloned = await asset_mod.migrate_emojis(self.engine, progress_callback=update, types_to_include=types_to_include, force=force)
        if cloned and (cloned.get("Emoji") or cloned.get("Sticker")):
            lines = []
            if cloned.get("Emoji"):
                lines.append("Emojis cloned:"); lines.extend([f"- {n}" for n in cloned["Emoji"]])
            if cloned.get("Sticker"):
                lines.append("Stickers cloned:"); lines.extend([f"- {n}" for n in cloned["Sticker"]])
            await log_audit_event(self.engine, "Assets Cloned", "\n".join(lines))
        return cloned

    async def _logic_sync_metadata(self, modal: ProgressScreen, components: list[str]):
        meta_mod = fluxer_metadata if self.target_platform == "fluxer" else stoat_metadata
        modal.set_item_status("Syncing Server Profile...")
        
        async def progress_cb(item, status):
            color = "green" if status == "DONE" else "red" if status == "ERROR" else "yellow"
            modal.write(f"{item} [[bold {color}]{status}[/bold {color}]]")
            
        cloned = await meta_mod.sync_server_metadata(self.engine, progress_cb, components=components)
        if cloned:
            lines = ["Synchronized profile traits:"]
            if "name" in cloned: lines.append(f"- **Name**: {cloned['name']}")
            if "icon" in cloned: lines.append("- **Icon**")
            if "banner" in cloned: lines.append("- **Banner**")
            # Prepare files for audit log
            files = []
            if "icon" in cloned:
                ext = "gif" if cloned["icon"].startswith(b"GIF") else "png"
                files.append({"filename": f"icon.{ext}", "data": cloned["icon"]})
            if "banner" in cloned:
                ext = "gif" if cloned["banner"].startswith(b"GIF") else "png"
                files.append({"filename": f"banner.{ext}", "data": cloned["banner"]})
            await log_audit_event(self.engine, "Profile Synced", "\n".join(lines), files=files)
        return cloned

    # ── report formatting ───────────────────────────────────────────────

    def _format_sync_report(self, results: dict) -> str:
        lines = ["[bold green]Synchronization Report[/bold green]\n"]
        
        meta = results.get("metadata", {})
        if meta:
            lines.append("[bold cyan]Server Profile:[/bold cyan]")
            if "name" in meta: lines.append(f"- Name: [white]{meta['name']}[/white]")
            if "icon" in meta: lines.append("- Icon")
            if "banner" in meta: lines.append("- Banner")
            lines.append("")

        assets = results.get("assets", {})
        emojis = assets.get("Emoji", {})
        if emojis:
            lines.append("[bold cyan]Emojis:[/bold cyan]")
            for name, eid in emojis.items():
                lines.append(f"- {name} ([dim]{eid}[/dim])")
            lines.append("")

        stickers = assets.get("Sticker", {})
        if stickers:
            lines.append("[bold cyan]Stickers:[/bold cyan]")
            for name, sid in stickers.items():
                lines.append(f"- {name} ([dim]{sid}[/dim])")
            lines.append("")
            
        if not meta and not emojis and not stickers:
            lines.append("[yellow]No items were synchronized.[/yellow]")
            
        return "\n".join(lines)

    def _format_clone_report(self, results: dict) -> str:
        lines = ["[bold green]Cloning Template Report[/bold green]\n"]
        
        roles = results.get("roles", [])
        if roles:
            lines.append(f"[bold cyan]Roles ({len(roles)}):[/bold cyan]")
            for r in sorted(roles): lines.append(f"- {r}")
            lines.append("")

        channels = results.get("channels", {})
        structure = channels.get("structure", {})
        if structure:
            lines.append("[bold cyan]Server Structure:[/bold cyan]")
            cats = sorted(structure.keys(), key=lambda x: (x == "No Category", x))
            for cat in cats:
                chans = structure[cat]
                if cat in channels.get("categories_created", []) or chans:
                    lines.append(f"[bold]{cat}[/bold]")
                    for ch in sorted(chans):
                        lines.append(f"  - {ch}")
            lines.append("")

        if not roles and not structure:
            lines.append("[yellow]No items were cloned.[/yellow]")

        return "\n".join(lines)

    # ── (5) message migration ─────────────────────────────────────────────

    @work(exclusive=True)
    async def run_migrate_messages(self) -> None:
        if not self.tokens_valid:
            return

        migrate_mod = fluxer_migrate if self.target_platform == "fluxer" else stoat_migrate
        platform_name = self.target_platform.capitalize()

        modal = ProgressScreen(log_level=self.config.migration.log_level)
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)

        try:
            modal.set_status("Fetching channels...")
            await self.engine.start_connections()

            full_d = await self.engine.discord_reader.get_channels()
            
            # If reading from backup, only show channels that have actual message backup data
            if getattr(self.engine, "source_mode", "live") == "backup" and hasattr(self.engine.discord_reader, "get_backed_up_channel_ids"):
                valid_ids = await self.engine.discord_reader.get_backed_up_channel_ids()
                full_d = [c for c in full_d if c.id in valid_ids]
                
            d_channels = [c for c in full_d if c.type in [self.engine.discord_reader.CHANNEL_TYPE_TEXT, self.engine.discord_reader.CHANNEL_TYPE_NEWS]]
            d_cats = await self.engine.discord_reader.get_categories()
            d_cat_map = {c.id: c.name for c in d_cats}

            if not d_channels:
                modal.write("[yellow]No text channels found.[/yellow]")
                modal.allow_close()
                return

            # Fetch target channels
            modal.set_status(f"Fetching {platform_name} channels...")
            
            full_f = await self.engine.writer.get_channels()
            f_channels = [c for c in full_f if c.get("name") not in ["reaper_logs", "reaper-logs"] and c.get("type") not in [2, 4]]

            if not f_channels:
                modal.write(f"[yellow]No channels found in {platform_name} community.[/yellow]")
                modal.allow_close()
                await self.engine.close_connections()
                return

            self.app.pop_screen()

            target_cat_names = {str(c.get("id")): c.get("name") for c in full_f if c.get("type") == 4}

            while True:
                loop = asyncio.get_running_loop()
                pick_future = loop.create_future()

                def on_pick(result):
                    if not pick_future.done():
                        pick_future.set_result(result)

                self.app.push_screen(ChannelPickerScreen(d_channels, d_cat_map, f_channels, target_cat_names, platform_name), on_pick)
                res = await pick_future

                if res is None:
                    await self.engine.close_connections()
                    return
                
                src_id, tgt_id = res
                source_channel = next(c for c in d_channels if c.id == src_id)
                target_channel = next(c for c in f_channels if c.get("id") == tgt_id)

                # Determine after_id status
                last_migrated = self.engine.state.get_last_message_id(str(target_channel.get('id')))
                has_previous = bool(last_migrated)
                
                # Analyze
                modal = ProgressScreen(log_level=self.config.migration.log_level)
                self.app.push_screen(modal)
                await asyncio.sleep(0.1)
                modal.set_status("Analyzing channel...")
                modal.show_stats()

                self.engine.is_running = True
                stats_analysis = {"messages": 0, "threads": 0, "attachments": 0}

                async def update_scan(current_stats):
                    modal.set_status(f"[cyan]Scanned {current_stats['messages']} items...")

                logger.info(f"Analyzing message history for Discord #{source_channel.name}...")
                stats_analysis = await migrate_mod.analyze_migration(
                    self.engine,
                    source_channel_id=source_channel.id,
                    after_message_id=int(last_migrated) if last_migrated else None,
                    progress_callback=update_scan,
                )
                logger.info(f"Analysis complete: {stats_analysis['messages']} new messages found.")
                self.engine.is_running = False

                # Set initial total stats for the confirmation block
                modal.update_stats(
                    messages=stats_analysis['messages'],
                    threads=stats_analysis['threads'],
                    files=stats_analysis['attachments']
                )

                # Setup the info container
                m_status = "[bold yellow]Previous Migration Detected[/bold yellow]" if has_previous else "[bold cyan]No previous migration data.[/bold cyan]"
                i_status = f"[bold]{stats_analysis['messages']}[/bold] New Messages, [bold]{stats_analysis['threads']}[/bold] Threads."
                modal.show_info(m_status, i_status)

                # Fetch and display message previews
                try:
                    first_msg = await self.engine.discord_reader.get_first_message(source_channel.id)
                    if first_msg:
                        content = first_msg.content or (f"[dim]({len(first_msg.attachments)} attachments)[/dim]" if first_msg.attachments else "[dim](no content)[/dim]")
                        modal.write("[bold cyan]Start from first message:[/bold cyan]")
                        modal.write(f"[bold]{first_msg.author.display_name}:[/bold] {content[:200]}")
                        modal.write("")

                    if has_previous and last_migrated:
                        try:
                            prev_msg = await self.engine.discord_reader.get_message(source_channel.id, int(last_migrated))
                            if prev_msg:
                                content = prev_msg.content or (f"[dim]({len(prev_msg.attachments)} attachments)[/dim]" if prev_msg.attachments else "[dim](no content)[/dim]")
                                modal.write("[bold yellow]Continue from previous migration:[/bold yellow]")
                                modal.write(f"[bold]{prev_msg.author.display_name}:[/bold] {content[:200]}")
                                modal.write("")
                        except Exception as e:
                            logger.warning(f"Could not fetch previous message {last_migrated}: {e}")
                except Exception as e:
                    logger.warning(f"Error fetching message previews: {e}")

                modal.set_status(f"Awaiting Confirmation to migrate Discord [cyan]#{source_channel.name}[/cyan] → {platform_name} [green]#{target_channel.get('name')}[/green]")

                # Phase 2: Confirmation
                choice = await modal.phase_wait_confirm(
                    show_continue=has_previous,
                    show_id=True,
                    btn_start_label="Start from\nFirst Message",
                    btn_continue_label="Continue\nMigration",
                    btn_id_label="Start from\nmessage ID"
                )
                logger.info(f"User confirmation choice: {choice}")
                if choice == "btn_back":
                    modal.dismiss()
                    continue # Return to channel picker
                elif choice == "btn_main_menu":
                    modal.dismiss()
                    self.app.switch_screen("config_selection")
                    self.engine.is_running = False
                    await self.engine.close_connections()
                    return
                    
                after_id = None
                if choice == "btn_continue" and last_migrated:
                    logger.info("Proceeding with 'Continue Migration' (incremental sink).")
                    after_id = int(last_migrated)
                elif choice == "btn_start_id":
                    # Fallback to full for now since we don't have an ID input dialog yet
                    modal.write("[yellow]Custom Message ID start not fully implemented, starting from beginning.[/yellow]")
                    logger.info("Proceeding with 'Start from ID' (fallback to begin).")
                    after_id = None
                else:
                    logger.info("Proceeding with 'Start from First' (clean sink).")
                
                # If we are here, we are proceeding with migration
                break

            # Phase 3: Progress
            modal.cancel_callback = lambda: setattr(self.engine, "is_running", False)
            modal.phase_progress()
            modal.set_status("Migrating messages...")

            total_messages = stats_analysis["messages"]
            total_threads = stats_analysis["threads"]
            total_attachments = stats_analysis["attachments"]
            
            modal.set_status(f"Migrating: [cyan]#{source_channel.name}[/cyan] → [green]#{target_channel.get('name')}[/green]")
            modal.write(f"[bold cyan]Migration Started:[/bold cyan] Discord [cyan]#{source_channel.name}[/cyan] → {platform_name} [green]#{target_channel.get('name')}[/green]")
            modal.write(f"[dim]Stats: {total_messages} messages, {total_threads} threads, {total_attachments} files[/dim]\n")
            
            logger.info(f"Execution started for #{source_channel.name} -> {platform_name} @ {target_channel.get('name')}")
            self.engine.is_running = True

            async def update_msg(current_stats):
                c_msgs = current_stats["messages"]
                c_threads = current_stats["threads"]
                c_files = current_stats["attachments"]
                
                modal.set_item_status(f"[cyan]Migrated {c_msgs}/{total_messages} messages...")
                modal.set_progress(c_msgs, total_messages)
                
                modal.update_stats(
                    messages=f"{c_msgs}/{total_messages}",
                    threads=f"{c_threads}/{total_threads}",
                    files=f"{c_files}/{total_attachments}"
                )
                
                # optionally show a scrolling trace if the backend provided it
                modal.write_live(f"Migrated message #{c_msgs}")

                content = current_stats.get("last_message_content", "")
                author = current_stats.get("last_message_author", "Unknown")
                if content:
                    # Clean up content for display (truncate long messages)
                    disp_content = (content[:100] + '...') if len(content) > 100 else content
                    modal.write(f"[bold]{author}:[/bold] {disp_content}")

            result = await migrate_mod.migrate_messages(
                self.engine,
                source_channel_id=source_channel.id,
                target_channel_id=target_channel.get("id"),
                after_message_id=after_id,
                progress_callback=update_msg,
            )

            if self.engine.is_running:
                modal.write(f"[bold green]Success! {result['messages']} messages migrated.[/bold green]")
                event_title = "Message Migration"
                modal.phase_report(event_title)
            else:
                modal.write(f"[bold yellow]Interrupted! {result['messages']} messages migrated.[/bold yellow]")
                event_title = "Message Migration"
                modal.phase_report(event_title, "stopped")

            lines = [f"Migrated Discord #{source_channel.name} → {platform_name} #{target_channel.get('name')}:"]
            lines.append(f"{result['messages']} messages, {result['attachments']} attachments, {result['threads']} threads")
            await log_audit_event(self.engine, event_title, "\n".join(lines))

        except Exception as e:
            err = str(e)
            if "MissingPermission" in err and "Masquerade" in err:
                modal.write("[bold red]Bot is missing the 'Masquerade' permission.[/bold red]")
            else:
                modal.write(f"[bold red]Error: {err}[/bold red]")
            modal.phase_report("Message Migration", "error")
        finally:
            self.engine.is_running = False
            await self.engine.close_connections()

    # ── (6) danger zone ───────────────────────────────────────────────────

    def _open_danger_menu(self):
        options = [
            ("dz_del_channels", "Delete ALL Channels & Categories"),
            ("dz_reset_perms", "Reset ALL Channel Permissions"),
            ("dz_del_roles", "Delete ALL Roles"),
            ("dz_del_assets", "Delete ALL Emojis & Stickers"),
        ]
        def on_result(selections: list[str] | None):
            if selections:
                self.run_batch_danger(selections)
        self.app.push_screen(OptionSelectModal("⚠ DANGER ZONE ⚠", options), on_result)

    @work(exclusive=True)
    async def run_batch_danger(self, selections: list[str]) -> None:
        modal = ProgressScreen(log_level=self.config.migration.log_level)
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)
        target_started = False
        try:
            # Phase 1: Connect early to fetch real item names for preview
            modal.set_status("Connecting to Target Server for Preview...")
            try:
                await self.engine.start_target_only()
                target_started = True
            except Exception as e:
                logger.warning(f"Could not pre-connect for DZ preview: {e}")

            modal.set_status(f"Awaiting Confirmation for {len(selections)} Destructive Operations...")
            modal.write("[bold red]WARNING: THIS WILL DELETE DATA PERMANENTLY! MUST PROCEED TO CONTINUE.[/bold red]")
            modal.write("")

            # Fetch and display live item names from target server
            preview = await self._fetch_dz_preview(selections) if target_started else {}

            dz_labels = {
                "dz_del_channels": "Channels & Categories to be Deleted",
                "dz_reset_perms": "Channels with Permissions to Reset",
                "dz_del_roles": "Roles to be Deleted",
                "dz_del_assets": "Emojis & Stickers to be Deleted"
            }
            for sel in selections:
                section_title = dz_labels.get(sel, sel)
                items = preview.get(sel, [])
                if items:
                    modal.write(f"[bold cyan]{section_title} ({len(items)}):[/bold cyan]")
                    for name in items[:20]:  # cap at 20 to avoid flooding the log
                        modal.write(f"  [red]- {name}[/red]")
                    if len(items) > 20:
                        modal.write(f"  [dim]... and {len(items) - 20} more[/dim]")
                else:
                    modal.write(f"[bold cyan]{section_title}:[/bold cyan]")
                    modal.write(f"  [dim](could not fetch list)[/dim]")
                modal.write("")

            choice = await modal.phase_wait_confirm(btn_start_label="WIPE ALL DATA", show_id=False)
            if choice == "btn_back":
                modal.dismiss()
                self._open_danger_menu()
                return
            elif choice == "btn_main_menu":
                modal.dismiss()
                self.app.switch_screen("config_selection")
                return

            modal.cancel_callback = lambda: setattr(self.engine, "is_running", False)
            modal.phase_progress()
            modal.set_status("Danger Zone: Destructive Operations")
            self.engine.is_running = True
            # Writer already started above, no need to reconnect
            if not target_started:
                await self.engine.start_target_only()

            for sel in selections:
                if not self.engine.is_running: break
                if sel == "dz_del_channels":
                    await self._logic_dz_delete_channels(modal)
                elif sel == "dz_reset_perms":
                    await self._logic_dz_reset_perms(modal)
                elif sel == "dz_del_roles":
                    await self._logic_dz_delete_roles(modal)
                elif sel == "dz_del_assets":
                    await self._logic_dz_delete_assets(modal)
            
            modal.phase_report("Danger Zone Operations Complete")
            modal.write("[bold green]All selected destructive operations finished.[/bold green]")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
            modal.phase_report("Danger Zone Batch", "error")
        finally:
            self.engine.is_running = False
            await self.engine.close_target_only()

    async def _fetch_dz_preview(self, selections: list[str]) -> dict[str, list[str]]:
        """Fetches real item names from the target server for each Danger Zone selection.
        Returns a dict mapping selection ID -> list of item names."""
        preview: dict[str, list[str]] = {}
        writer = self.engine.writer
        is_fluxer = self.target_platform == "fluxer"

        try:
            if "dz_del_channels" in selections or "dz_reset_perms" in selections:
                channels_raw = await writer.get_channels()
                protected = ["reaper-logs", "reaper_logs"]
                channel_names = [
                    c.get("name", "Unknown") for c in channels_raw 
                    if c.get("type") != 4 and str(c.get("name", "")).lower() not in protected
                ]
                category_names = [
                    c.get("name", "Unknown") for c in channels_raw 
                    if c.get("type") == 4 and str(c.get("name", "")).lower() not in protected
                ]
                all_names = category_names + channel_names
                if "dz_del_channels" in selections:
                    preview["dz_del_channels"] = all_names
                if "dz_reset_perms" in selections:
                    preview["dz_reset_perms"] = channel_names  # only channels, not categories
        except Exception as e:
            logger.warning(f"DZ preview: failed to fetch channels: {e}")

        try:
            if "dz_del_roles" in selections:
                if is_fluxer:
                    community_id = self.engine.config.target_server_id
                    roles_raw = await writer.client.get_guild_roles(community_id)
                    role_names = [
                        r.get("name", "Unknown") for r in roles_raw
                        if not r.get("managed") and r.get("name") != "@everyone"
                    ]
                else:
                    # Stoat: server.roles is a dict {id: Role}
                    server = await writer._get_server()
                    role_names = [
                        role.name for role in server.roles.values()
                        if str(role.id) != writer.community_id
                    ]
                preview["dz_del_roles"] = role_names
        except Exception as e:
            logger.warning(f"DZ preview: failed to fetch roles: {e}")

        try:
            if "dz_del_assets" in selections:
                asset_names = []
                if is_fluxer:
                    community_id = self.engine.config.target_server_id
                    emojis = await writer.client.get_guild_emojis(community_id)
                    asset_names += [f"{e.get('name', '?')} (emoji)" for e in emojis]
                    try:
                        stickers = await writer.client.get_guild_stickers(community_id)
                        asset_names += [f"{s.get('name', '?')} (sticker)" for s in stickers]
                    except Exception:
                        pass  # Stickers may not exist
                else:
                    server = await writer._get_server()
                    emojis = await server.fetch_emojis()
                    asset_names += [f"{e.name} (emoji)" for e in emojis]
                preview["dz_del_assets"] = asset_names
        except Exception as e:
            logger.warning(f"DZ preview: failed to fetch assets: {e}")

        return preview

    async def _fetch_clone_preview(self, selections: list[str]) -> dict[str, Any]:
        """Fetches preview data from Discord (source server) for cloning confirmation,
        comparing with existing entities on the target server for presence highlighting."""
        preview = {}
        reader = self.engine.discord_reader
        writer = self.engine.writer
        is_fluxer = self.target_platform == "fluxer"

        # Fetch target data for comparison
        target_roles = []
        target_channels = []
        try:
            if is_fluxer:
                target_roles_raw = await writer.client.get_guild_roles(self.engine.config.target_server_id)
                target_roles = [r.get("name", "").lower() for r in target_roles_raw]
            else:
                server = await writer._get_server()
                target_roles = [r.name.lower() for r in server.roles.values()]
            
            target_chans_raw = await writer.get_channels()
            target_channels = [c.get("name", "").lower() for c in target_chans_raw]
        except Exception as e:
            logger.warning(f"Clone Preview: failed to fetch target data for comparison: {e}")

        try:
            if "sub_clone_roles" in selections:
                roles = await reader.get_roles()
                preview["roles"] = [(r.name, r.name.lower() in target_roles) for r in roles]
        except Exception as e:
            logger.warning(f"Clone Preview: failed to fetch roles: {e}")

        try:
            if "sub_clone_channels" in selections:
                # Build hierarchy
                src_categories = await reader.get_categories()
                src_channels = await reader.get_channels()
                
                # structure[cat_id] = (cat_name, cat_exists, [(ch_name, ch_exists), ...])
                structure = {}
                for cat in src_categories:
                    cat_exists = cat.name.lower() in target_channels
                    structure[cat.id] = (cat.name, cat_exists, [])
                
                for ch in src_channels:
                    ch_exists = ch.name.lower() in target_channels
                    if ch.category_id in structure:
                        structure[ch.category_id][2].append((ch.name, ch_exists))
                    else:
                        if None not in structure:
                            structure[None] = ("No Category", False, [])
                        structure[None][2].append((ch.name, ch_exists))
                
                preview["structure"] = structure
        except Exception as e:
            logger.warning(f"Clone Preview: failed to fetch structure: {e}")
        
        return preview


    async def _logic_dz_delete_channels(self, modal: ProgressScreen) -> None:
        if self.target_platform == "fluxer":
            from src.fluxer.danger_zone import danger_delete_all_channels
        else:
            from src.stoat.danger_zone import danger_delete_all_channels

        modal.set_item_status("[red]Deleting channels...")
        async def on_deleted(name, current, total):
            modal.set_item_status(f"[red]Deleting: {name}")
            modal.set_progress(current, total)

        count = await danger_delete_all_channels(self.engine, progress_callback=on_deleted)
        modal.write(f"[bold green]Success! {count} channels/categories deleted.[/bold green]")
        await log_audit_event(self.engine, "Danger Zone: Channels Wiped", f"Deleted {count} channels and categories.")

    async def _logic_dz_reset_perms(self, modal: ProgressScreen) -> None:
        if self.target_platform == "fluxer":
            from src.fluxer.danger_zone import danger_reset_channel_permissions
        else:
            from src.stoat.danger_zone import danger_reset_channel_permissions

        modal.set_item_status("[red]Resetting permissions...")
        async def on_reset(name, current, total):
            modal.set_item_status(f"[red]Resetting: {name}")
            modal.set_progress(current, total)

        count = await danger_reset_channel_permissions(self.engine, progress_callback=on_reset)
        modal.write(f"[bold green]Success! Permissions reset on {count} items.[/bold green]")
        await log_audit_event(self.engine, "Danger Zone: Permissions Wiped", f"Reset permissions on {count} items.")

    async def _logic_dz_delete_roles(self, modal: ProgressScreen) -> None:
        if self.target_platform == "fluxer":
            from src.fluxer.danger_zone import danger_delete_all_roles
        else:
            from src.stoat.danger_zone import danger_delete_all_roles

        modal.set_item_status("[red]Deleting roles...")
        async def on_deleted(name, current, total):
            modal.set_item_status(f"[red]Deleting role: {name}")
            modal.set_progress(current, total)

        count = await danger_delete_all_roles(self.engine, progress_callback=on_deleted)
        modal.write(f"[bold green]Success! {count} roles deleted.[/bold green]")
        await log_audit_event(self.engine, "Danger Zone: Roles Wiped", f"Deleted {count} roles.")

    async def _logic_dz_delete_assets(self, modal: ProgressScreen) -> None:
        if self.target_platform == "fluxer":
            from src.fluxer.danger_zone import danger_delete_all_emojis_and_stickers
        else:
            from src.stoat.danger_zone import danger_delete_all_emojis_and_stickers

        modal.set_item_status("[red]Deleting assets...")
        async def on_deleted(name, asset_type, current, total):
            modal.set_item_status(f"[red]Deleting {asset_type}: {name}")
            modal.set_progress(current, total)

        counts = await danger_delete_all_emojis_and_stickers(self.engine, progress_callback=on_deleted)
        modal.write(f"[bold green]Success! {counts.get('emojis', 0)} emojis, {counts.get('stickers', 0)} stickers deleted.[/bold green]")
        await log_audit_event(self.engine, "Danger Zone: Assets Wiped", f"Deleted {counts.get('emojis', 0)} emojis and {counts.get('stickers', 0)} stickers.")
