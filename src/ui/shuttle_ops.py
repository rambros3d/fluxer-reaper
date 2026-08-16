"""
OperationPane – unified operations widget for both Backup and Migration.
Handles Discord backups and multi-platform migrations.
"""

import asyncio
import logging
import re
import time
import aiohttp
import traceback
from pathlib import Path
from typing import Any, Optional, Union, List, Dict, Callable

from textual.app import ComposeResult
from textual.containers import Container, Vertical, Horizontal, VerticalScroll
from textual.widgets import Button, Label, Rule, LoadingIndicator
from textual import work

from src.core.configuration import load_config
from src.core.base import MigrationContext
from src.core.audit import log_audit_event
from src.core.exporter import DiscordExporter
from src.ui.modals import (
    ProgressScreen, SubMenuModal, ChannelPickerScreen, OptionSelectModal, MessageIDInputModal,
    ChannelSelectScreen
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


class OperationPane(Container):
    """Unified operations pane — Backup, Clone, Sync, Migrate."""

    DEFAULT_CSS = """
    OperationPane { height: auto; width: 100%; }
    OperationPane #op_info {
        height: auto; border: tall yellow; padding: 1 1 0 1; margin-bottom: 1; layout: vertical;
    }
    #op_info_split { height: auto; layout: horizontal; width: 100%; margin-bottom: 1; }
    .info_pane { width: 1fr; height: auto; }
    .info_pane Label { width: 100%; }
    .pane_header { text-style: bold; color: $accent; margin-bottom: 1; }
    .status_row { height: auto; min-height: 1; width: 100%; margin-top: 1; }
    .status_row Label { width: 100%; height: auto; }
    .status_row LoadingIndicator { width: 8; height: 1; margin: 0; min-width: 8; }
    .pane_status { text-style: bold; }
    #op_info_split Rule { height: 100%; margin: 0 2; color: $accent; }
    #op_lbl_backup { display: none; }
    
    OperationPane #op_actions { height: auto; }
    OperationPane #op_actions Button { width: 100%; margin-bottom: 1; }
    #footer_rule { margin: 0; }
    """

    def __init__(self, cfg_name: str, cfg_path: Path, view_mode: str = "shuttle", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cfg_name = cfg_name
        self.config_path = cfg_path
        self.view_mode = view_mode  # "backup" or "shuttle" (migrate)
        self.config = load_config(cfg_path)
        self.target_platform = self.config.target_platform or "fluxer"
        self.engine: MigrationContext | None = None
        self.exporter: DiscordExporter | None = None
        self.validation_results: dict = {
            "source_validating": True,
            "target_validating": True,
        }
        self.tokens_valid = False
        self.permissions_complete = False
        self.has_backup = False

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            with Vertical(id="op_info"):
                with Horizontal(id="op_info_split"):
                    with Vertical(classes="info_pane"):
                        yield Label(f"{self.config.source_platform.capitalize()} (Source)", classes="pane_header text-glow")
                        yield Label("Server: [yellow]Loading...[/yellow]", id="op_lbl_d_server")
                        if self.view_mode == "backup":
                            yield Label("Source: [yellow]Loading...[/yellow]", id="op_lbl_d_bot")
                        else:
                            yield Label("Bot: [yellow]Loading...[/yellow]", id="op_lbl_d_bot")
                            
                        with Horizontal(classes="status_row"):
                            yield LoadingIndicator(id="op_d_loader")
                            yield Label("", id="op_lbl_d_status", classes="pane_status")
                    
                    yield Rule(orientation="vertical", id="op_vrule")

                    with Vertical(classes="info_pane", id="op_target_pane"):
                        yield Label("Target", id="op_lbl_t_header", classes="pane_header")
                        yield Label("Community: [yellow]Loading...[/yellow] ", id="op_lbl_t_comm")
                        yield Label("Bot: [yellow]Loading...[/yellow]", id="op_lbl_t_bot")
                        
                        with Horizontal(classes="status_row"):
                            yield LoadingIndicator(id="op_t_loader")
                            yield Label("", id="op_lbl_t_status", classes="pane_status")
                
                yield Label("", id="op_lbl_backup")

            with Vertical(id="op_actions"):
                if self.view_mode == "backup":
                    yield Button("Backup Channel Messages", id="op_backup_msgs", disabled=True, tooltip="Select and backup message history from text channels")
                    yield Button("Update Existing Backup", id="op_backup_sync", disabled=True, variant="success", tooltip="Scan for new messages\n& Update existing backup")
                    yield Rule(id="op_backup_stats_rule")
                    yield Button("Backup Stats", id="op_backup_stats", variant="primary", flat=True, disabled=True, tooltip="View detailed statistics, storage, and entity metrics for the current backup profile")
                else:
                    yield Button("Clone Server Template", id="op_clone", disabled=True, tooltip="Clone server roles, categories, and channels to the target community")
                    yield Button("Sync Server Settings", id="op_sync", disabled=True, tooltip="Sync emojis, stickers, server name, and icon to the target community")
                    yield Button("Migrate Message History", id="op_messages", disabled=True, variant="primary", tooltip="Migrate message history from Discord to the target platform")
                    yield Button("Waterfall Migration", id="op_waterfall", disabled=True, variant="primary", tooltip="Migrate all messages globally in chronological order to prevent broken links.\n(Available for Local Backups)")
                    yield Rule(id="footer_rule")
                    yield Button("Danger Zone ⚠", id="op_danger", variant="error", disabled=True, flat=True, tooltip="Dangerous operations:\ndelete channels, roles, emojis on target\n(use with caution)")
                
                if self.cfg_name == "AutoTest":
                    yield Button("RUN AUTO TEST", id="op_autotest", variant="warning", flat="false", tooltip="Execute automated test sequence for the AutoTest profile")

    def on_mount(self) -> None:
        self._rebuild_engine()
        self._update_info_labels()
        # Wait for DOM to be stable before first validation
        self.call_after_refresh(self.run_validate)

    def on_show(self) -> None:
        """Re-validate when the pane regains visibility."""
        if self.view_mode == "backup" or self.config.tool_mode == "backup_transfer":
            if self.view_mode == "shuttle":
                # Re-run path discovery in case a new backup was just made
                self._rebuild_engine()
            self.run_validate()

    def reload_config(self) -> None:
        self.config = load_config(self.config_path)
        self.target_platform = self.config.target_platform or "fluxer"
        self._rebuild_engine()
        self.run_validate()

    def _base_dir(self) -> str:
        # If the config file is in the root, our base directory is current (.)
        if str(self.config_path) == "reaper_config.yaml":
            return "."
        return f"ReaperFiles-{self.cfg_name}"


    # REDESIGN
    def _rebuild_engine(self):
        # In backup_transfer mode, the Backup tab reads from LIVE discord, 
        # while the Shuttle tab reads from the LOCAL BACKUP.
        if self.view_mode == "backup":
            source = "live"
        else:
            source = "backup" if self.config.tool_mode == "backup_transfer" else "live"
            
        self.engine = MigrationContext(self.config, self.target_platform, source_mode=source, base_dir=self._base_dir())
        if self.view_mode == "backup":
            self.exporter = DiscordExporter(self.engine.source_reader, base_dir=self._base_dir())

    def _get_backup_info(self) -> str | None:
        if not self.config or not self.config.source_server_id:
            return None
        
        backup_str = f"SOURCE_BACKUP-{self.config.source_server_id}"
                      
        target_dir = Path(self._base_dir()) / backup_str
        if not target_dir.exists():
            return None
            
        db_file = target_dir / "backup.db"
        if not db_file.exists():
            return None
        try:
            from datetime import datetime
            from src.core.backup_database import BackupDatabase
            db = BackupDatabase(db_file)
            profile = db.get_guild_profile()
            if profile:
                ts_str = profile.get("last_backup")
                if ts_str:
                    dt = datetime.fromisoformat(ts_str)
                    return dt.strftime("%d-%b-%Y %H:%M")
        except Exception:
            pass
        return None

    # ── labels ────────────────────────────────────────────────────────────

        # ── labels ────────────────────────────────────────────────────────────

    # REDESIGN - DISCORD HEAVY
    def _update_info_labels(self):
        if not self.is_mounted:
            return
        v = self.validation_results

        # Source
        d_name = v.get("source_server_name")
        d_bot = v.get("source_bot_name")
        
        is_val_d = v.get("source_validating") or v.get("source_token") is None
        if is_val_d:
            s_disp, b_disp = "[yellow]Validating...[/yellow]", "[yellow]Validating...[/yellow]"
        elif v.get("source_timeout"):
            s_disp, b_disp = "[red]TIMEOUT[/red]", "[red]TIMEOUT[/red]"
        elif v.get("source_token") and v.get("source_server"):
            s_disp = f'[cyan]"{d_name}"[/cyan]'
            b_disp = f'[green]{d_bot}[/green]'
        elif v.get("source_token") and not v.get("source_server"):
            s_disp, b_disp = "[red]SERVER NOT SELECTED[/red]", f"[green]{d_bot}[/green]"
        elif v.get("source_token") is False:
            if self.config.tool_mode == "backup_transfer" and self.view_mode == "shuttle":
                if not self.config.source_server_id:
                    s_disp, b_disp = "[red]SERVER NOT SELECTED[/red]", "[red]SERVER NOT SELECTED[/red]"
                else:
                    s_disp, b_disp = "[red]NOT FOUND[/red]", "[red]NOT FOUND[/red]"
            else:
                if not self.config.source_bot_token:
                    s_disp, b_disp = "[red]NOT SET UP[/red]", "[red]NOT SET UP[/red]"
                else:
                    s_disp, b_disp = "[red]INVALID TOKEN[/red]", "[red]INVALID TOKEN[/red]"
        else:
            s_disp, b_disp = "[red]NOT SET UP[/red]", "[red]NOT SET UP[/red]"
            
        for lbl in self.query("#op_lbl_d_server"): lbl.update(f"Server: {s_disp}")
        if self.view_mode == "backup":
            for lbl in self.query("#op_lbl_d_bot"): lbl.update(f"Source: {b_disp}")
        elif self.config.tool_mode == "backup_transfer":
            for lbl in self.query("#op_lbl_d_bot"): lbl.update(f"Source: {b_disp}")
        else:
            for lbl in self.query("#op_lbl_d_bot"): lbl.update(f"Bot: {b_disp}")

        # Source Side Status
        d_err = v.get("source_error")
        di = v.get("discord_intents", {})
        dp = v.get("source_permissions", {})
        
        d_missing = []
        if d_err is None and v.get("source_token") and v.get("source_server"):
            if not di.get("message_content"): d_missing.append("Message Content Intent")
            if not di.get("members"): d_missing.append("Server Members Intent")
            
            if not dp.get("view_channel"): d_missing.append("View Channels")
            if not dp.get("read_messages"): d_missing.append("Read Messages")
            if not dp.get("read_message_history"): d_missing.append("Read Message History")

        if is_val_d:
            d_status = ""
            for ldr in self.query("#op_d_loader"): ldr.display = True
            for lbl in self.query("#op_lbl_d_status"): lbl.display = False
        else:
            for ldr in self.query("#op_d_loader"): ldr.display = False
            for lbl in self.query("#op_lbl_d_status"): lbl.display = True
            
            if v.get("source_token") and v.get("source_server") and not d_missing:
                d_status = "STATUS: [green]VALID[/green]"
            elif v.get("source_token") and not v.get("source_server"):
                d_status = "[red]SERVER NOT SET[/red]"
            elif v.get("source_timeout"):
                d_status = "[red]TIMEOUT[/red]"
            elif d_err:
                d_status = f"[red]{d_err}[/red]"
            elif d_missing:
                d_status = f"[yellow]MISSING: {', '.join(d_missing)}[/yellow]"
            elif v.get("source_token") is False:
                if self.config.tool_mode == "backup_transfer" and self.view_mode == "shuttle":
                    d_status = "[yellow]Local Backup[/yellow] [red]Not Found[/red]"
                else:
                    d_status = "[red]INVALID[/red]"
            else:
                d_status = ""
                
        for lbl in self.query("#op_lbl_d_status"): 
            lbl.update(f"{d_status}")

        # Target / Backup Info
        if self.view_mode == "backup":
            backup_text = v.get("backup_info_text", "")
            for lbl in self.query("#op_lbl_backup"):
                lbl.update(backup_text)
                lbl.display = bool(backup_text)
            
            # Hide target side in backup mode completely
            for rle in self.query("#op_vrule"): rle.display = False
            for pne in self.query("#op_target_pane"): pne.display = False
            
            enabled = (v.get("source_token") and v.get("source_server") and not d_missing)
            for btn in self.query("#op_backup_msgs"):
                btn.disabled = not enabled
            for btn in self.query("#op_backup_sync"):
                btn.display = self.has_backup
                btn.disabled = not (enabled and self.has_backup)
            for btn in self.query("#op_autotest"):
                btn.disabled = not enabled
                
            for btn in self.query("#op_backup_stats"):
                btn.display = self.has_backup
                btn.disabled = not self.has_backup
            for rle in self.query("#op_backup_stats_rule"): rle.display = self.has_backup
        else:
            # Show target side in shuttle mode
            for rle in self.query("#op_vrule"): rle.display = True
            for pne in self.query("#op_target_pane"): pne.display = True
            
            # Target
            plat = "Fluxer (target)" if self.target_platform == "fluxer" else "Stoat (target)" 
            t_name = v.get("target_community_name")
            t_bot = v.get("target_bot_name")
            
            for lbl in self.query("#op_lbl_t_header"): lbl.update(plat)
            
            is_val_t = v.get("target_validating") or v.get("target_token") is None
            if is_val_t:
                c_disp, tb_disp = "[yellow]Validating...[/yellow]", "[yellow]Validating...[/yellow]"
            elif v.get("target_timeout"):
                c_disp, tb_disp = "[red]TIMEOUT[/red]", "[red]TIMEOUT[/red]"
            elif v.get("target_token") and v.get("target_community"):
                c_disp = f'[cyan]"{t_name}"[/cyan]'
                tb_disp = f'[green]{t_bot}[/green]'
            elif v.get("target_token") is False:
                c_disp, tb_disp = "[red]INVALID TOKEN[/red]", "[red]INVALID TOKEN[/red]"
            else:
                c_disp, tb_disp = "[red]NOT SET UP[/red]", "[red]NOT SET UP[/red]"
                
            for lbl in self.query("#op_lbl_t_comm"): lbl.update(f"Community: {c_disp}")
            for lbl in self.query("#op_lbl_t_bot"): lbl.update(f"Bot: {tb_disp}")

            # Target Side Status
            t_err = v.get("target_error")
            tp = v.get("target_permissions", {})
            
            t_missing = []
            if t_err is None and v.get("target_token") and v.get("target_community"):
                if tp:
                    t_missing = [k.replace('_', ' ').title() for k, val_p in tp.items() if not val_p]

            if is_val_t:
                t_status = ""
                for ldr in self.query("#op_t_loader"): ldr.display = True
                for lbl in self.query("#op_lbl_t_status"): lbl.display = False
            else:
                for ldr in self.query("#op_t_loader"): ldr.display = False
                for lbl in self.query("#op_lbl_t_status"): lbl.display = True
            
                if v.get("target_token") and v.get("target_community") and not t_missing:
                    t_status = "STATUS: [green]VALID[/green]"
                elif v.get("target_timeout"):
                    t_status = "ERROR: [red]TIMEOUT[/red]"
                elif t_err:
                    t_status = f"ERROR: [red]{t_err}[/red]"
                elif t_missing:
                    t_status = f"[yellow]MISSING: {', '.join(t_missing)} Permission[/yellow]"
                elif v.get("target_token") is False:
                    t_status = "ERROR: [red]INVALID[/red]"
                else:
                    t_status = ""
            
            for lbl in self.query("#op_lbl_t_status"): 
                lbl.update(f"{t_status}")

            # Buttons
            for bid in ("#op_clone", "#op_sync", "#op_messages", "#op_waterfall", "#op_danger", "#op_autotest"):
                for btn in self.query(bid): btn.disabled = not self.tokens_valid

    # ── validation ────────────────────────────────────────────────────────

    @work(exclusive=True)
    async def run_validate(self) -> None:
        if not self.is_mounted:
            return
        
        try:
            plat = "Fluxer" if self.target_platform == "fluxer" else "Stoat"
            # Use query().first() or check presence to avoid NoMatches crashes
            for lbl in self.query("#op_lbl_t_header"): lbl.update(plat)
            for lbl in self.query("#op_lbl_d_server"): lbl.update("Server: [yellow]Validating...[/yellow]")
            for lbl in self.query("#op_lbl_d_bot"): lbl.update("Source: [yellow]Validating...[/yellow]" if self.view_mode == "backup" else "Bot: [yellow]Validating...[/yellow]")
            for lbl in self.query("#op_lbl_t_comm"): lbl.update("Community: [yellow]Validating...[/yellow]")
            for lbl in self.query("#op_lbl_t_bot"): lbl.update("Bot: [yellow]Validating...[/yellow]")
            
            # Disable all operation buttons while validation is in progress
            if self.view_mode == "shuttle":
                for bid in ("#op_clone", "#op_sync", "#op_messages", "#op_waterfall", "#op_danger", "#op_autotest"):
                    for btn in self.query(bid): btn.disabled = True
            elif self.view_mode == "backup":
                for bid in ("#op_backup_msgs", "#op_backup_sync", "#op_autotest"):
                    for btn in self.query(bid): btn.disabled = True
        except Exception as e:
            logger.error(f"Error in run_validate setup: {e}")

        info = self._get_backup_info()
        self.validation_results = {
            "source_validating": True,
            "target_validating": True,
            "source_token": None, "source_bot_name": None,
            "source_server": None, "source_server_name": None,
            "discord_intents": {}, "source_permissions": {},
            "source_error": None,
            "target_token": None, "target_bot_name": None,
            "target_community": None, "target_community_name": None,
            "target_permissions": {},
            "target_error": None,
            "source_timeout": False, "target_timeout": False,
            "backup_info_text": f"Last backup: [cyan]{info}[/cyan]" if info else "",
        }
        self.tokens_valid = False
        self.permissions_complete = False
        self.has_backup = bool(info)

        # Check what we have
        has_d_token = bool(self.config.source_bot_token)
        has_d_server = bool(self.config.source_server_id)
        
        if self.target_platform == "stoat":
            has_t_token = bool(self.config.stoat_bot_token)
            has_t_server = bool(self.config.stoat_server_id)
        else:
            has_t_token = bool(self.config.fluxer_bot_token)
            has_t_server = bool(self.config.fluxer_server_id)

        # Flag which operations are being validated
        validating_source = False
        validating_target = False

        # 1. Determine Source validating status
        if self.config.tool_mode == "backup_transfer" and self.view_mode == "shuttle":
            if has_d_server: 
                validating_source = True
            else:
                self.validation_results["source_token"] = False
                self.validation_results["source_server"] = False
        else:
            if has_d_token:
                validating_source = True
            else:
                self.validation_results["source_token"] = False

        # 2. Determine Target validating status
        if self.view_mode == "shuttle" and has_t_token:
            validating_target = True
        elif self.view_mode == "shuttle":
            self.validation_results["target_token"] = False

        self.validation_results["source_validating"] = validating_source
        self.validation_results["target_validating"] = validating_target
        
        # Trigger the UI spinners instantly
        self._update_info_labels()

        async def check_source():
            try:
                import asyncio
                res = await asyncio.wait_for(self.engine.source_reader.validate(), timeout=10.0)
                self.validation_results["source_token"] = res.get("token", False)
                self.validation_results["source_bot_name"] = res.get("bot_name")
                self.validation_results["source_server"] = res.get("server", False)
                self.validation_results["source_server_name"] = res.get("server_name")
                self.validation_results["discord_intents"] = res.get("intents", {})
                self.validation_results["source_permissions"] = res.get("permissions", {})
                self.validation_results["source_error"] = res.get("error_reason")
            except asyncio.TimeoutError:
                self.validation_results["source_timeout"] = True
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.validation_results["source_error"] = str(e)
            finally:
                self.validation_results["source_validating"] = False
                self._check_and_update()

        async def check_target():
            try:
                import asyncio
                res = await asyncio.wait_for(self.engine.writer.validate(), timeout=10.0)
                self.validation_results["target_token"] = res.get("token", False)
                self.validation_results["target_bot_name"] = res.get("bot_name")
                self.validation_results["target_community"] = res.get("community", False)
                self.validation_results["target_community_name"] = res.get("community_name")
                self.validation_results["target_permissions"] = res.get("permissions", {})
                self.validation_results["target_error"] = res.get("error_reason")
            except asyncio.TimeoutError:
                self.validation_results["target_timeout"] = True
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.validation_results["target_error"] = str(e)
            finally:
                self.validation_results["target_validating"] = False
                self._check_and_update()

        coros = []
        if validating_source: coros.append(check_source())
        if validating_target: coros.append(check_target())
        
        try:
            if coros:
                import asyncio
                await asyncio.gather(*coros)
            else:
                self._check_and_update()
        except asyncio.CancelledError:
            pass

    def _check_and_update(self) -> None:
        """Called safely on the main thread after any validation task finishes."""
        v = self.validation_results
        source_ok = v.get("source_token") and v.get("source_server")
        
        if self.view_mode == "backup":
            self.tokens_valid = bool(source_ok)
            # Check for backup regardless of token validity
            info = self._get_backup_info()
            if info:
                self.validation_results["backup_info_text"] = f"Last backup: [cyan]{info}[/cyan]"
                self.has_backup = True
        else:
            target_ok = v.get("target_token") and v.get("target_community")
            self.tokens_valid = bool(source_ok and target_ok)
            
            # Post validation adjustments
            if self.tokens_valid:
                is_backup = (self.config.tool_mode == "backup_transfer")
                for btn in self.query("#op_waterfall"):
                    btn.disabled = not is_backup
                    btn.display = is_backup

        self._update_info_labels()

    # ── button routing ────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if not bid or not bid.startswith("op_"):
            return
            
        # Migration Routing
        if bid == "op_clone":
            self._open_clone_menu()
        elif bid == "op_sync":
            self._open_sync_menu()
        elif bid == "op_messages":
            self.run_migrate_messages()
        elif bid == "op_waterfall":
            self.run_waterfall_migration()
        elif bid == "op_danger":
            self._open_danger_menu()
            
        # Backup Routing
        elif bid == "op_backup_msgs":
            self.run_backup_messages()
        elif bid == "op_backup_sync":
            self.run_backup_sync()
        elif bid == "op_backup_stats":
            from src.ui.backup_stats import BackupStatsScreen
            target_dir = Path(self._base_dir()) / f"SOURCE_BACKUP-{self.config.source_server_id}"
            self.app.push_screen(BackupStatsScreen(self.cfg_name, target_dir))
        elif bid == "op_autotest" or bid == "btn_autotest":
            self.run_autotest_sequence()

    @work(exclusive=True)
    async def run_autotest_sequence(self) -> None:
        """Entry point for the AUTO TEST sequence."""
        if not self.tokens_valid:
            return

        modal = ProgressScreen(log_level=self.config.log_level)
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)

        try:
            if self.view_mode == "shuttle":
                await self._run_migration_autotest_logic(modal)
            elif self.view_mode == "backup":
                await self._run_backup_autotest_logic(modal)
            
            modal.phase_report("AUTO TEST Complete", show_back=False)
            modal.write("[bold green]Full automated test sequence finished successfully![/bold green]")
        except Exception as e:
            logger.error(f"Auto-Test Error: {e}\n{traceback.format_exc()}")
            modal.write(f"[bold red]Error: {e}[/bold red]")
            modal.phase_report("Auto-Test", "error", show_back=False)
        finally:
            self.engine.is_running = False
            await self.engine.close_connections()

    async def _run_migration_autotest_logic(self, modal: ProgressScreen) -> None:
        """Executes the full migration test sequence."""
        modal.set_status("AUTO TEST: Launching Migration Sequence...")
        modal.write("[bold yellow]Starting Migration Auto-Test...[/bold yellow]")
        
        migrate_mod = fluxer_migrate if self.target_platform == "fluxer" else stoat_migrate
        
        # 1. Connect and initialize state
        modal.set_status("Connecting and Initializing State...")
        await self.engine.start_connections()
        self.engine.is_running = True
        
        # Initialize state database early to avoid Warnings
        tid = self.config.fluxer_server_id if self.target_platform == "fluxer" else self.config.stoat_server_id
        tgt_info = await self.engine.writer.validate()
        self.engine.ensure_state_initialized(str(tid or ""), tgt_info.get("community_name", "Target"))
        
        # 2. Danger Zone Clean
        modal.write("\n[bold red]Phase 1: Danger Zone Cleanup[/bold red]")
        await self._logic_dz_delete_channels(modal)
        await self._logic_dz_reset_perms(modal)
        await self._logic_dz_delete_roles(modal)
        await self._logic_dz_delete_assets(modal)
        
        # 3. Clone Roles & Permissions
        modal.write("\n[bold cyan]Phase 2: Cloning Roles & Permissions[/bold cyan]")
        await self._logic_clone_roles(modal, force=True)
        
        # 4. Clone Assets (Logo, Banner, Emojis, Stickers)
        modal.write("\n[bold cyan]Phase 3: Syncing Metadata & Assets[/bold cyan]")
        await self._logic_copy_assets(modal, ["Emoji", "Sticker"], force=True)
        await self._logic_sync_metadata(modal, ["name", "icon", "banner"])
        
        # 5. Clone Template (Structure)
        modal.write("\n[bold cyan]Phase 4: Cloning Server Structure (1/2)[/bold cyan]")
        await self._logic_clone_channels(modal, force=True)
        await self._logic_sync_permissions(modal)
        
        # 6. Waterfall Migration (Only for backups)
        if self.engine.source_mode == "backup" :
            modal.write("\n[bold cyan]Phase 5: Waterfall Message Migration[/bold cyan]")
            await self._logic_waterfall_migration(modal=modal, is_autotest=True)
        else:
            modal.write("\n[bold yellow]Phase 5: Skipping Waterfall (Live mode selected)[/bold yellow]")
        
        # 7. Individual Channel Migration (Automated)
        modal.write("\n[bold cyan]Phase 7: Individual Channel Migration[/bold cyan]")
        await self._logic_autotest_migrate_all_channels(modal=modal)

    async def _run_backup_autotest_logic(self, modal: ProgressScreen) -> None:
        """Executes the full backup test sequence."""
        import shutil
        modal.set_status("AUTO TEST: Launching Backup Sequence...")
        modal.write("[bold yellow]Starting Backup Auto-Test...[/bold yellow]")
        
        # 1. Clear old backup directory completely
        server_dir = Path(self._base_dir()) / f"SOURCE_BACKUP-{self.config.source_server_id}"
        if server_dir.exists():
            modal.write(f"[yellow]Deleting existing backup directory: {server_dir}[/yellow]")
            try:
                shutil.rmtree(server_dir)
            except Exception as e:
                modal.write(f"[red]Warning: Could not delete directory: {e}[/red]")
        
        # 2. Setup & Metadata
        modal.set_status("Initializing Discord connection...")
        await self.engine.source_reader.start()
        await self.exporter.setup()
        self.exporter.is_running = True
        
        modal.write("\n[bold cyan]Phase 1: Full Server Backup[/bold cyan]")
        
        # 3. Use unified backup logic in autotest mode
        all_channels = await self.engine.source_reader.get_channels()
        eligible_channels = [
            c for c in all_channels
            if c.type in [
                self.engine.source_reader.CHANNEL_TYPE_TEXT,
                self.engine.source_reader.CHANNEL_TYPE_NEWS,
                self.engine.source_reader.CHANNEL_TYPE_FORUM
            ]
        ]
        
        await self._logic_full_backup(
            modal=modal,
            selected_channels=eligible_channels,
            force_overwrite=True,
            is_autotest=True
        )

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
        modal = ProgressScreen(log_level=self.config.log_level)
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)
        connections_started = False
        try:
            # Phase 1: Connect early to fetch both source and target structure for preview
            modal.set_status("Connecting to Source and Target Servers for Preview...")
            try:
                await self.engine.start_connections()
                connections_started = True
                # Sync all entities before preview/confirmation
                modal.set_status("Synchronizing entity mappings...")
                await self._perform_auto_matching()
            except Exception as e:
                logger.warning(f"Could not pre-connect for Clone preview: {e}")

            # Show info container early
            modal.show_info("[bold cyan]Clone Template Ready[/bold cyan]", f"{len(selections)} categories/roles selected.")

            modal.set_status(f"Awaiting Confirmation for {len(selections)} Operations...")
            
            # Fetch and display live preview auto-matching already ran above
            preview = await self._fetch_clone_preview(selections) if connections_started else {}

            if connections_started:
                src_server = getattr(self.engine.source_reader, 'guild', None)
                tgt_server_info = await self.engine.writer.validate()
                tgt_server_name = tgt_server_info.get("community_name", "target community")
                
                if src_server:
                    # Fetch live counts from the reader (works for both Discord and Fluxer)
                    try:
                        src_roles = await self.engine.source_reader.get_roles()
                        src_emojis = await self.engine.source_reader.get_emojis()
                        src_channels = await self.engine.source_reader.get_channels()
                    except Exception:
                        src_roles, src_emojis, src_channels = [], [], []

                    modal.write(f"[bold cyan]Source Server Profile:[/bold cyan]")
                    modal.write(f"  Name: [green]{src_server.name}[/green]")
                    modal.write(f"  Icon: [green]{'Present' if src_server.icon else 'None'}[/green]")
                    modal.write(f"  Roles: [green]{len(src_roles)}[/green]")
                    modal.write(f"  Emojis: [green]{len(src_emojis)}[/green]")
                    modal.write(f"  Channels: [green]{len(src_channels)}[/green]")
                    modal.write("")
                modal.write(f"[bold cyan]Target Community:[/bold cyan] [green]{tgt_server_name}[/green]\n")

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
                btn_id_label="Force Clone",
                show_id=True,
                btn_start_tooltip="Clone without creating duplicates",
                btn_id_tooltip="Force clone everything\n(may create duplicates)"
            )
            if choice == "btn_back":
                modal.dismiss()
                self._open_clone_menu()
                return
            elif choice == "btn_main_menu":
                modal.dismiss()
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
            
            modal.phase_report("Clone Template Complete", show_back=False)
            report = self._format_clone_report(results)
            modal.write(report)
        except Exception as e:
            logger.error(f"Batch Cloning Error: {e}\n{traceback.format_exc()}")
            modal.write(f"[bold red]Error: {e}[/bold red]")
            modal.phase_report("Batch Operation", "error", show_back=False)
        finally:
            self.engine.is_running = False
            # Ensure we only close if we actually started them and no other task is inheriting
            await self.engine.close_connections()

    @work(exclusive=True)
    async def run_batch_sync(self, selections: list[str]) -> None:
        modal = ProgressScreen(log_level=self.config.log_level)
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)
        try:
            # Connect early to get metadata
            modal.set_status("Connecting to Source and Target Servers for Preview...")
            connections_started = False
            try:
                await self.engine.start_connections()
                connections_started = True
            except Exception as e:
                logger.warning(f"Could not pre-connect for Sync preview: {e}")

            if connections_started:
                src_server = getattr(self.engine.source_reader, 'guild', None)
                tgt_server_info = await self.engine.writer.validate()
                tgt_server_name = tgt_server_info.get("community_name", "target community")
                
                if src_server:
                    # Fetch live counts from the reader (works for both Discord and Fluxer)
                    try:
                        src_roles = await self.engine.source_reader.get_roles()
                        src_emojis = await self.engine.source_reader.get_emojis()
                        src_channels = await self.engine.source_reader.get_channels()
                    except Exception:
                        src_roles, src_emojis, src_channels = [], [], []

                    modal.write(f"[bold cyan]Source Server Profile:[/bold cyan]")
                    modal.write(f"  Name: [green]{src_server.name}[/green]")
                    modal.write(f"  Icon: [green]{'Present' if src_server.icon else 'None'}[/green]")
                    modal.write(f"  Roles: [green]{len(src_roles)}[/green]")
                    modal.write(f"  Emojis: [green]{len(src_emojis)}[/green]")
                    modal.write(f"  Channels: [green]{len(src_channels)}[/green]")
                    modal.write("")
                modal.write(f"[bold cyan]Target Community:[/bold cyan] [green]{tgt_server_name}[/green]\n")

            # Show info container
            modal.show_info("[bold yellow]Sync Ready[/bold yellow]", "Comparing server configurations...")

            modal.set_status("Awaiting Confirmation to Sync Server Settings...")
            choice = await modal.phase_wait_confirm(
                btn_start_label="Start Syncing",
                btn_id_label="Force Sync",
                show_id=True,
                btn_start_tooltip="Sync new assets only",
                btn_id_tooltip="Force sync assets\n(may create duplicates)"
            )
            if choice == "btn_back":
                modal.dismiss()
                self._open_sync_menu()
                return
            elif choice == "btn_main_menu":
                modal.dismiss()
                return

            force_mode = (choice == "btn_start_id")
            modal.cancel_callback = lambda: setattr(self.engine, "is_running", False)
            modal.phase_progress()
            modal.set_status("Syncing Server Settings")
            if not connections_started:
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
            
            modal.phase_report("Sync Settings Complete", show_back=False)
            report = self._format_sync_report(results)
            modal.write(report)
        except Exception as e:
            logger.error(f"Batch Sync Error: {e}\n{traceback.format_exc()}")
            modal.write(f"[bold red]Error: {e}[/bold red]")
            modal.phase_report("Batch Operation", "error", show_back=False)
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
        categories = await self.engine.source_reader.get_categories()
        channels = await self.engine.source_reader.get_channels()
        
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
        # Ensure the migration state database is initialised so that
        # name-based matching in sync_assets_state is persisted.
        tid = self.config.fluxer_server_id if self.target_platform == "fluxer" else self.config.stoat_server_id
        if tid and not self.engine.state.db:
            try:
                tgt_info = await self.engine.writer.validate()
                name = tgt_info.get("community_name", "Target") if tgt_info else "Target"
            except Exception:
                name = "Target"
            self.engine.ensure_state_initialized(str(tid), name)

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
    async def run_migrate_messages(self, modal: ProgressScreen | None = None) -> None:
        await self._logic_migrate_messages(modal)

    async def _logic_autotest_migrate_all_channels(self, modal: ProgressScreen) -> None:
        """Automated name-based channel migration for Auto-Test."""
        migrate_mod = fluxer_migrate if self.target_platform == "fluxer" else stoat_migrate
        
        # 1. Matching
        modal.set_status("Auto-matching channels by name...")
        await self._perform_auto_matching()
        
        # 2. Get channels
        d_channels = await self.engine.source_reader.get_channels()
        text_channels = [c for c in d_channels if c.type in [
            self.engine.source_reader.CHANNEL_TYPE_TEXT, 
            self.engine.source_reader.CHANNEL_TYPE_NEWS
        ]]
        
        modal.write(f"[bold cyan]Auto-Test: Found {len(text_channels)} channels to migrate.[/bold cyan]")
        
        # 3. Migrate loop
        for i, ch in enumerate(text_channels):
            if not self.engine.is_running: break
            
            tgt_id = self.engine.state.get_target_channel_id(str(ch.id))
            if not tgt_id:
                modal.write(f"[yellow]Skipping #{ch.name} (no target mapping found)[/yellow]")
                continue
                
            modal.write(f"\n[bold]Migrating #{ch.name} ({i+1}/{len(text_channels)}) -> Target ID {tgt_id}[/bold]")
            
            # Analyze (silent)
            stats = await migrate_mod.analyze_migration(self.engine, source_channel_id=ch.id, after_message_id=None)
            ch_total = stats["messages"]
            
            async def update_indiv(curr):
                c = curr["messages"]
                modal.set_progress(c, ch_total or 100)
                modal.set_item_status(f"#{ch.name}: {c}/{ch_total} messages")
            
            await migrate_mod.migrate_messages(
                self.engine,
                source_channel_id=ch.id,
                target_channel_id=tgt_id,
                after_message_id=None,
                progress_callback=update_indiv
            )
            
        modal.write("\n[bold green]Automated channel migration complete.[/bold green]")

    async def _logic_migrate_messages(self, modal: ProgressScreen | None = None, is_autotest: bool = False) -> None:
        if not self.tokens_valid:
            return

        migrate_mod = fluxer_migrate if self.target_platform == "fluxer" else stoat_migrate
        platform_name = self.target_platform.capitalize()
        source_name = getattr(self.engine.config, "source_platform", "discord").capitalize()

        if not modal:
            modal = ProgressScreen(log_level=self.config.log_level)
            self.app.push_screen(modal)
            await asyncio.sleep(0.1)

        try:
            # Show info container
            modal.show_info("[bold cyan]Message Migration Ready[/bold cyan]", "Checking channel permissions...")

            modal.set_status("Connecting to Servers...")
            await self.engine.start_connections()
            
            # Sync all entities before confirmation
            modal.set_status("Synchronizing entity mappings...")
            await self._perform_auto_matching()

            full_d = await self.engine.source_reader.get_channels()
            
            # If reading from backup, only show channels that have actual message backup data
            if getattr(self.engine, "source_mode", "live") == "backup" and hasattr(self.engine.source_reader, "get_backed_up_channel_ids"):
                valid_ids = await self.engine.source_reader.get_backed_up_channel_ids()
                full_d = [c for c in full_d if c.id in valid_ids]
                
            d_channels = [c for c in full_d if c.type in [self.engine.source_reader.CHANNEL_TYPE_TEXT, self.engine.source_reader.CHANNEL_TYPE_NEWS]]
            d_cats = await self.engine.source_reader.get_categories()
            d_cat_map = {c.id: c.name for c in d_cats}

            if not d_channels:
                modal.write("[yellow]No text channels found.[/yellow]")
                modal.allow_close()
                return

            # Fetch target channels
            modal.set_status(f"Fetching {platform_name} channels...")
            
            full_f = await self.engine.writer.get_channels()
            f_channels = [c for c in full_f if str(c.get("name")).lower() not in ["reaper-logs", "reaper_logs", "reaperfiles-logs"] and c.get("type") not in [2, 4]]

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

                self.app.push_screen(ChannelPickerScreen(d_channels, d_cat_map, f_channels, target_cat_names, platform_name, src_name=source_name, all_tgt_channels=full_f), on_pick)
                res = await pick_future

                if res is None:
                    await self.engine.close_connections()
                    return
                
                # Handle result from channel picker
                # Normal: (src_id, tgt_id) - 2-tuple
                # Create new: (src_id, "create_new", channel_name) - 3-tuple
                # Enter ID: (src_id, tgt_id, channel_dict) - 3-tuple
                
                pending_create_name = None  # Deferred channel creation
                
                if len(res) == 3 and res[1] == "create_new":
                    src_id, _, chan_name = res
                    source_channel = next(c for c in d_channels if c.id == src_id)
                    # Don't create yet — defer until user confirms migration
                    pending_create_name = chan_name
                    target_channel = {"id": "__pending__", "name": chan_name, "type": 0}
                
                elif len(res) == 3:
                    src_id, _, chan_dict = res
                    source_channel = next(c for c in d_channels if c.id == src_id)
                    target_channel = chan_dict
                
                else:
                    src_id, tgt_id = res
                    source_channel = next(c for c in d_channels if c.id == src_id)
                    target_channel = next(c for c in f_channels if c.get("id") == tgt_id)

                # 2. Analyze
                modal = ProgressScreen(log_level=self.config.log_level)
                self.app.push_screen(modal)
                await asyncio.sleep(0.1)

                # Determine after_id status (skip for pending channels)
                if pending_create_name:
                    last_migrated = None
                    has_previous = False
                else:
                    last_migrated = self.engine.state.get_last_message_id(str(target_channel.get('id')))
                    has_previous = bool(last_migrated)

                src_server = getattr(self.engine.source_reader, 'guild', None)
                tgt_server_info = await self.engine.writer.validate()
                tgt_server_name = tgt_server_info.get("community_name", "target community")
                
                # ENSURE INITIALIZED for mapping lookup in analyze/migrate
                tid = self.engine.config.fluxer_server_id if self.target_platform == "fluxer" else self.engine.config.stoat_server_id
                self.engine.ensure_state_initialized(str(tid or ""), tgt_server_name)

                if src_server:
                    modal.write(f"[bold cyan]Source Server Profile:[/bold cyan]")
                    modal.write(f"  Name: [green]{src_server.name}[/green]")
                    modal.write(f"  Icon: [green]{'Present' if src_server.icon else 'None'}[/green]")
                    modal.write("")
                modal.write(f"[bold cyan]Target Community:[/bold cyan] [green]{tgt_server_name}[/green]\n")

                modal.set_status("Analyzing channel...")
                modal.show_stats()
                # Show buttons early so user can skip analysis
                modal.show_early_buttons(
                    show_continue=has_previous,
                    show_id=True,
                    btn_start_label="Start from\nFirst Message",
                    btn_continue_label="Continue\nMigration",
                    btn_id_label="Start from\nmessage ID",
                    btn_start_tooltip="Start migrating from the earliest available message",
                    btn_continue_tooltip="Resume from the last successfully migrated message",
                    btn_id_tooltip="Start migrating from a specific Discord message ID"
                )

                self.engine.is_running = True
                stats_analysis = {"messages": 0, "threads": 0, "attachments": 0}

                async def update_scan(current_stats):
                    modal.set_status(f"[cyan]Scanned {current_stats['messages']} items...")

                logger.info(f"Analyzing message history for {source_name} #{source_channel.name}...")
                
                # Run analysis and wait for confirmation concurrently
                analysis_task = asyncio.create_task(migrate_mod.analyze_migration(
                    self.engine,
                    source_channel_id=source_channel.id,
                    after_message_id=int(last_migrated) if last_migrated else None,
                    progress_callback=update_scan,
                ))

                # Fetch and display message previews as background tasks
                async def fetch_previews():
                    try:
                        first_msg_task = asyncio.create_task(self.engine.source_reader.get_first_message(source_channel.id))
                        prev_msg_task = None
                        if has_previous and last_migrated:
                            prev_msg_task = asyncio.create_task(self.engine.source_reader.get_message(source_channel.id, int(last_migrated)))
                        
                        first_msg = await first_msg_task
                        if first_msg:
                            content = first_msg.content or (f"[dim]({len(first_msg.attachments)} attachments)[/dim]" if first_msg.attachments else "[dim](no content)[/dim]")
                            modal.write("[bold cyan]Start from first message:[/bold cyan]")
                            modal.write(f"[bold]{first_msg.author.display_name}:[/bold] {content[:200]}\n")
                            
                        if prev_msg_task:
                            try:
                                prev_msg = await prev_msg_task
                                if prev_msg:
                                    content = prev_msg.content or (f"[dim]({len(prev_msg.attachments)} attachments)[/dim]" if prev_msg.attachments else "[dim](no content)[/dim]")
                                    modal.write("[bold yellow]Continue from previous migration:[/bold yellow]")
                                    modal.write(f"[bold]{prev_msg.author.display_name}:[/bold] {content[:200]}\n")
                            except Exception as e:
                                logger.warning(f"Could not fetch previous message {last_migrated}: {e}")
                    except Exception as e:
                        logger.warning(f"Error fetching message previews: {e}")

                preview_task = asyncio.create_task(fetch_previews())
                
                # Cleanup function for confirmation phase
                def cleanup_preview():
                    if not preview_task.done():
                        preview_task.cancel()

                # Create a task for waiting for confirmation
                confirm_task = asyncio.create_task(modal.phase_wait_confirm(
                    show_continue=has_previous,
                    show_id=True,
                    btn_start_label="Start from\nFirst Message",
                    btn_continue_label="Continue\nMigration",
                    btn_id_label="Start from\nmessage ID",
                    btn_start_tooltip="Start migrating from the earliest available message",
                    btn_continue_tooltip="Resume from the last successfully migrated message",
                    btn_id_tooltip="Start migrating from a specific Discord message ID"
                ))

                # Wait for either analysis to finish OR user to make a choice
                done, pending = await asyncio.wait(
                    [analysis_task, confirm_task], 
                    return_when=asyncio.FIRST_COMPLETED
                )

                if confirm_task in done:
                    # User clicked a button early
                    choice = confirm_task.result()
                    if not analysis_task.done():
                        analysis_task.cancel()
                        logger.info("Analysis cancelled by early user choice.")
                else:
                    # Analysis finished first
                    stats_analysis = analysis_task.result()
                    logger.info(f"Analysis complete: {stats_analysis['messages']} new messages found.")
                    
                    # Update stats in UI
                    modal.update_stats(
                        messages=stats_analysis['messages'],
                        threads=stats_analysis['threads'],
                        files=stats_analysis['attachments']
                    )
                    m_status = "[bold yellow]Previous Migration Detected[/bold yellow]" if has_previous else "[bold cyan]No previous migration data.[/bold cyan]"
                    i_status = f"[bold]{stats_analysis['messages']}[/bold] New Messages, [bold]{stats_analysis['threads']}[/bold] Threads."
                    modal.show_info(m_status, i_status)
                    
                    modal.set_status(f"Awaiting Confirmation to migrate {source_name} [cyan]#{source_channel.name}[/cyan] → {platform_name} [green]#{target_channel.get('name')}[/green]")
                    
                    # Now wait for the choice if not already made
                    choice = await confirm_task

                self.engine.is_running = False
                cleanup_preview()
                logger.info(f"User confirmation choice: {choice}")

                if choice == "btn_back":
                    modal.dismiss()
                    continue # Return to channel picker
                elif choice == "btn_main_menu":
                    modal.dismiss()
                    self.engine.is_running = False
                    await self.engine.close_connections()
                    return
                    
                after_id = None
                if choice == "btn_continue" and last_migrated:
                    logger.info("Proceeding with 'Continue Migration' (incremental sink).")
                    after_id = int(last_migrated)
                elif choice == "btn_start_id":
                    loop = asyncio.get_running_loop()
                    future = loop.create_future()
                    def id_callback(res: int | None) -> None:
                        if not future.done():
                            future.set_result(res)
                            
                    id_modal = MessageIDInputModal(self.engine.source_reader, source_channel.id)
                    self.app.push_screen(id_modal, id_callback)
                    verified_id = await future
                    
                    if verified_id is None:
                        # User cancelled the ID input, stay on the progress modal
                        logger.info("User cancelled 'Start from ID' input.")
                        continue
                        
                    logger.info(f"Proceeding with 'Start from ID': {verified_id}")
                    after_id = verified_id
                else:
                    logger.info("Proceeding with 'Start from First' (clean sink).")
                    after_id = None
                    # Clear previous tracking data for this channel
                    self.engine.state.clear_channel_data(target_channel.get("id"))
                
                is_inclusive = (choice == "btn_start_id")
                
                # If after_id changed from the initial analysis, we must re-analyze 
                # to get the correct total count for the UI fraction (e.g. Messages: 8/8 instead of 8/1)
                initial_after = int(last_migrated) if last_migrated else None

                # User selected a different start point, transition UI immediately
                if after_id != initial_after:
                    modal.phase_progress() # Hide buttons immediately
                    if choice == "btn_start_first":
                        modal.set_status("Starting from first message...")
                    elif choice == "btn_start_id":
                        modal.set_status(f"Starting from ID [cyan]{after_id}[/cyan]...")
                    else:
                        modal.set_status("Re-analyzing channel from new starting point...")

                    try:
                        self.engine.is_running = True
                        
                        # Ensure state is initialized (database exists)
                        if self.target_platform == "stoat":
                            tid = self.config.stoat_server_id
                        else:
                            tid = self.config.fluxer_server_id
                        self.engine.ensure_state_initialized(str(tid or ""), platform_name)

                        stats_analysis = await migrate_mod.analyze_migration(
                            self.engine,
                            source_channel_id=source_channel.id,
                            after_message_id=after_id,
                            inclusive=is_inclusive,
                            progress_callback=update_scan,
                        )
                        modal.update_stats(
                            messages=stats_analysis['messages'],
                            threads=stats_analysis['threads'],
                            files=stats_analysis['attachments']
                        )
                    except Exception as e:
                        logger.warning(f"Failed to re-analyze for correct totals: {e}")

                # If we are here, we are proceeding with migration
                break

            # Create the channel now if it was deferred
            if pending_create_name:
                modal.set_status(f"Creating channel [green]#{pending_create_name}[/green]...")
                try:
                    new_id = await self.engine.writer.create_channel(name=pending_create_name)
                    logger.info(f"Created new channel '{pending_create_name}' with ID: {new_id}")
                    target_channel = {"id": new_id, "name": pending_create_name, "type": 0}
                    f_channels.append(target_channel)
                except Exception as e:
                    logger.error(f"Failed to create channel '{pending_create_name}': {e}")
                    modal.write(f"[bold red]Failed to create channel: {e}[/bold red]")
                    modal.phase_report("Channel Creation", status="error")
                    return

            # Phase 3: Progress
            modal.cancel_callback = lambda: setattr(self.engine, "is_running", False)
            modal.phase_progress()
            modal.set_status("Migrating messages...")

            total_messages = stats_analysis["messages"]
            total_threads = stats_analysis["threads"]
            total_attachments = stats_analysis["attachments"]
            
            modal.set_status(f"Migrating: [cyan]#{source_channel.name}[/cyan] → [green]#{target_channel.get('name')}[/green]")
            source_label = getattr(self.engine.config, "source_platform", "discord").capitalize()
            modal.write(f"[bold cyan]Migration Started:[/bold cyan] {source_label} [cyan]#{source_channel.name}[/cyan] → {platform_name} [green]#{target_channel.get('name')}[/green]")
            modal.write(f"[dim]Stats: {total_messages} messages, {total_threads} threads, {total_attachments} files[/dim]\n")
            
            logger.info(f"Execution started for #{source_channel.name} -> {platform_name} @ {target_channel.get('name')}")
            self.engine.is_running = True

            async def update_msg(current_stats):
                c_msgs = current_stats["messages"]
                c_threads = current_stats["threads"]
                c_files = current_stats["attachments"]
                
                msg_stat = f"{c_msgs}/{total_messages}" if total_messages > 0 else str(c_msgs)
                thr_stat = f"{c_threads}/{total_threads}" if total_threads > 0 else str(c_threads)
                fil_stat = f"{c_files}/{total_attachments}" if total_attachments > 0 else str(c_files)

                modal.set_item_status(f"[cyan]Migrated {msg_stat} messages...")
                modal.set_progress(c_msgs, total_messages or 100) # Fallback total for bar animation
                
                modal.update_stats(
                    messages=msg_stat,
                    threads=thr_stat,
                    files=fil_stat
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
                inclusive=is_inclusive,
                progress_callback=update_msg,
            )

            if self.engine.is_running:
                modal.write(f"[bold green]Success! {result['messages']} messages migrated.[/bold green]")
                event_title = "Message Migration"
                modal.phase_report(event_title, show_back=False)
            else:
                modal.write(f"[bold yellow]Interrupted! {result['messages']} messages migrated.[/bold yellow]")
                event_title = "Message Migration"
                modal.phase_report(event_title, "stopped", show_back=False)

            lines = [f"Migrated {source_label} #{source_channel.name} → {platform_name} #{target_channel.get('name')}:"]
            lines.append(f"{result['messages']} messages, {result['attachments']} attachments, {result['threads']} threads")
            await log_audit_event(self.engine, event_title, "\n".join(lines))

        except Exception as e:
            err = str(e)
            if "MissingPermission" in err and "Masquerade" in err:
                modal.write("[bold red]Bot is missing the 'Masquerade' permission.[/bold red]")
            else:
                modal.write(f"[bold red]Error: {err}[/bold red]")
            modal.phase_report("Message Migration", "error", show_back=False)
            logger.error(f"Migration Error: {traceback.format_exc()}")
        finally:
            self.engine.is_running = False
            await self.engine.close_connections()

    @work(exclusive=True)
    async def run_waterfall_migration(self, modal: ProgressScreen | None = None) -> None:
        await self._logic_waterfall_migration(modal)


    async def _logic_waterfall_migration(self, modal: ProgressScreen | None = None, is_autotest: bool = False) -> None:
        if not self.tokens_valid:
            return

        migrate_mod = fluxer_migrate if self.target_platform == "fluxer" else stoat_migrate
        platform_name = self.target_platform.capitalize()
        
        if not modal:
            modal = ProgressScreen(log_level=self.config.log_level)
            self.app.push_screen(modal)
            await asyncio.sleep(0.1)

        try:
            modal.show_info("[bold cyan]Waterfall Migration Ready[/bold cyan]", "Checking mapping and missing channels...")
            modal.set_status("Connecting to Servers...")
            await self.engine.start_connections()
            
            # Ensure writer is validated before auto-matching (fixes NoneType role fetch error)
            await self.engine.writer.validate()

            modal.set_status("Synchronizing entity mappings...")
            await self._perform_auto_matching()

            # 1. Missing channels check
            if hasattr(self.engine.source_reader, "get_all_channels"):
                full_d = await self.engine.source_reader.get_all_channels()
                # Include TEXT (0), CATEGORY (4), and NEWS (5)
                d_channels = [c for c in full_d if c.type in [0, 4, 5]]
            else:
                full_d = await self.engine.source_reader.get_channels()
                d_channels = [c for c in full_d if c.type in [0, 4, 5]]
            missing_channels = []
            for d in d_channels:
                if d.type == 4:
                    tgt_id = self.engine.state.get_target_category_id(str(d.id))
                else:
                    tgt_id = self.engine.state.get_target_channel_id(str(d.id))
                if not tgt_id:
                    missing_channels.append(d)

            if missing_channels:
                modal.write(f"\n[bold yellow]Found {len(missing_channels)} backed-up channels/categories missing from target platform:[/bold yellow]")
                for mc in missing_channels:
                    prefix = "[bold cyan]📁[/bold cyan] " if mc.type == 4 else "[bold white]#[/bold white] "
                    modal.write(f"  {prefix}{mc.name}")
                
                if is_autotest:
                    choice = "btn_start_first"
                    modal.write("[bold cyan]Auto-Test: Automatically cloning missing channels.[/bold cyan]")
                else:
                    choice = await modal.phase_wait_confirm(
                        show_continue=False,
                        show_id=True,
                        btn_start_label="Clone missing channels",
                        btn_id_label="Skip missing channels",
                        btn_start_variant="primary",
                        btn_start_tooltip=f"Automatically create {len(missing_channels)} entities on target",
                        btn_id_tooltip="Start migration without these channels"
                    )
                
                if choice == "btn_back":
                    modal.dismiss()
                    await self.engine.close_connections()
                    return
                elif choice == "btn_main_menu":
                    modal.dismiss()
                    await self.engine.close_connections()
                    return
                
                if choice == "btn_start_first":
                    modal.set_status("Cloning missing categories and channels...")
                    # Sort so categories (type 4) come first
                    missing_channels.sort(key=lambda x: 0 if x.type == 4 else 1)
                    
                    for mc in missing_channels:
                        try:
                            parent_target_id = None
                            if mc.type == 4:
                                modal.write(f"Creating category '[bold cyan]{mc.name}[/bold cyan]'...")
                                new_id = await self.engine.writer.create_channel(name=mc.name, type=4)
                                self.engine.state.set_target_category_mapping(str(mc.id), new_id)
                                modal.write(f"[green]Created Category {mc.name} ({new_id})[/green]")
                            else:
                                if hasattr(mc, 'category_id') and mc.category_id:
                                    parent_target_id = self.engine.state.get_target_category_id(str(mc.category_id))
                                
                                modal.write(f"Creating channel '#{mc.name}'...")
                                new_id = await self.engine.writer.create_channel(name=mc.name, parent_id=parent_target_id)
                                self.engine.state.set_target_channel_id(str(mc.id), new_id, self.engine.target_platform)
                                modal.write(f"[green]Created Channel {mc.name} ({new_id})[/green]")
                        except Exception as e:
                            logger.error(f"Failed to create {mc.name}: {e}\n{traceback.format_exc()}")
                            modal.write(f"[red]Failed to create {mc.name}: {e}[/red]")
                elif choice == "btn_id":
                    # Skip missing channels: remove them from the active list
                    missing_ids = {str(c.id) for c in missing_channels}
                    d_channels = [c for c in d_channels if str(c.id) not in missing_ids]
            
            # 2. Resumption check
            all_mapped_tgt_ids = []
            # Check regular text channels (exclude categories for resume check)
            for c in d_channels:
                if c.type == 4: continue
                did = str(c.id)
                tid = self.engine.state.get_target_channel_id(did)
                if tid: all_mapped_tgt_ids.append(tid)
            
            # Also check threads (filtering to only include those belonging to active channels)
            active_channel_ids = {str(c.id) for c in d_channels}
            if hasattr(self.engine.source_reader, "get_active_threads"):
                threads = await self.engine.source_reader.get_active_threads()
                for t in threads:
                    pid = str(getattr(t, 'parent_id', getattr(t, 'channel_id', None)))
                    if pid not in active_channel_ids: continue
                    tid = self.engine.state.get_target_channel_id(str(t.id))
                    if tid: all_mapped_tgt_ids.append(tid)
            
            # 2.5 Filter by actual content (Only for BackupReader)
            # If a channel has NO messages in the backup, it will always be at 0 progress.
            # We exclude those from the global MIN calculation to avoid pulling it to 0.
            if hasattr(self.engine.source_reader, "get_backed_up_channel_ids"):
                backed_up_src_ids = await self.engine.source_reader.get_backed_up_channel_ids()
                backed_up_src_ids_str = {str(sid) for sid in backed_up_src_ids}
                
                filtered_tgt_ids = []
                # Find which target IDs belong to source channels that HAVE messages
                for c in d_channels: # (d_channels is already filtered for skipped)
                    if str(c.id) in backed_up_src_ids_str:
                        tid = self.engine.state.get_target_channel_id(str(c.id))
                        if tid: filtered_tgt_ids.append(tid)
                
                # Also check threads
                if hasattr(self.engine.source_reader, "threads"):
                    for t in self.engine.source_reader.threads:
                        if str(t.id) in backed_up_src_ids_str:
                            tid = self.engine.state.get_target_channel_id(str(t.id))
                            if tid: filtered_tgt_ids.append(tid)
                
                if filtered_tgt_ids:
                    all_mapped_tgt_ids = filtered_tgt_ids
                
            # 2.6 Resume Point: Calculate from global channel minimums
            min_last_id = self.engine.state.get_global_min_last_message_id(all_mapped_tgt_ids)
            
            modal.write(f"\n[bold cyan]Waterfall Migration Resume Point:[/bold cyan]")
            if min_last_id is not None:
                modal.write(f"Minimum unmigrated message ID found: [green]{min_last_id}[/green]")
            else:
                modal.write("No previous migration state found. Starting from the beginning.")
            
            if is_autotest:
                choice = "btn_continue" if min_last_id is not None else "btn_start_first"
                modal.write(f"[bold cyan]Auto-Test: Automatically choosing {choice.replace('btn_', '').replace('_', ' ')}.[/bold cyan]")
            else:
                choice = await modal.phase_wait_confirm(
                    show_continue=min_last_id is not None,
                    show_id=False,
                    btn_start_label="Start From Beginning",
                    btn_start_tooltip="Wipes migration progress and restarts from the beginning; may create duplicates",
                    btn_start_variant="default" if min_last_id is not None else "primary",
                    btn_continue_label=f"Continue from ID {min_last_id if min_last_id is not None else 0}" if min_last_id is not None else "Continue Migration",
                    btn_continue_tooltip="Fastest"
                )
            
            if choice == "btn_back":
                modal.dismiss()
                await self.engine.close_connections()
                return
            elif choice == "btn_main_menu":
                modal.dismiss()
                await self.engine.close_connections()
                return
                
            after_id = None
            if choice == "btn_start_first":
                logger.info("Proceeding with 'Start from Beginning' (global clean sink).")
                self.engine.state.clear_all_migration_data()
                after_id = None
            elif choice == "btn_continue" and min_last_id is not None:
                after_id = int(min_last_id)
            
            # Phase 3: Progress
            modal.cancel_callback = lambda: setattr(self.engine, "is_running", False)
            modal.phase_progress()
            modal.set_status("Migrating messages Globally...")
            
            self.engine.is_running = True
            
            # Ensure state is initialized (database exists)
            if self.target_platform == "stoat":
                tid = self.config.stoat_server_id
            else:
                tid = self.config.fluxer_server_id
            self.engine.ensure_state_initialized(str(tid or ""), platform_name)

            modal.show_stats()
            modal.write("Scanning global footprint for totals ...")
            stats_analysis = await migrate_mod.analyze_global_migration(self.engine, after_message_id=after_id)
            total_messages = stats_analysis["messages"]
            
            modal.write(f"[bold cyan]Global Migration Started:[/bold cyan] {total_messages} total messages to process.")
            modal.update_stats(messages=f"0/{total_messages}", threads=str(stats_analysis["threads"]), files=str(stats_analysis["attachments"]))
            
            async def update_msg(current_stats):
                c_msgs = current_stats["messages"]
                c_threads = current_stats["threads"]
                c_files = current_stats["attachments"]
                
                msg_stat = f"{c_msgs}/{total_messages}" if total_messages > 0 else str(c_msgs)
                modal.set_progress(c_msgs, total_messages or 100)
                modal.update_stats(messages=msg_stat, threads=str(c_threads), files=str(c_files))
                
                content = current_stats.get("last_message_content", "")
                author = current_stats.get("last_message_author", "Unknown")
                if content:
                    disp_content = (content[:100] + '...') if len(content) > 100 else content
                    modal.write(f"[bold]{author}:[/bold] {disp_content}")

            result = await migrate_mod.migrate_global_messages(
                self.engine,
                after_message_id=after_id,
                inclusive=False,
                progress_callback=update_msg,
            )

            if self.engine.is_running:
                modal.write(f"[bold green]Success! {result['messages']} messages migrated globally.[/bold green]")
                modal.phase_report("Waterfall Migration", show_back=False)
            else:
                modal.write(f"[bold yellow]Interrupted! {result['messages']} messages migrated.[/bold yellow]")
                modal.phase_report("Waterfall Migration", "stopped", show_back=False)
                
            lines = [f"Migrated Server Globally → {platform_name}:"]
            lines.append(f"{result['messages']} messages, {result['attachments']} attachments, {result['threads']} threads")
            await log_audit_event(self.engine, "Waterfall Migration", "\n".join(lines))

        except Exception as e:
            err = str(e)
            modal.write(f"[bold red]Error: {err}[/bold red]")
            modal.phase_report("Waterfall Migration", "error", show_back=False)
            
            logger.error(traceback.format_exc())
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
        modal = ProgressScreen(log_level=self.config.log_level)
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)
        target_started = False
        try:
            # Phase 1: Connect early to fetch real item names for preview
            modal.set_status("Connecting to Target Server for Preview...")
            try:
                await self.engine.start_target_only()
                target_started = True
                
                # Sync all entities before confirmation (even in danger zone)
                modal.set_status("Synchronizing entity mappings...")
                await self._perform_auto_matching()
            except Exception as e:
                logger.warning(f"Could not pre-connect for DZ preview: {e}")

            if target_started:
                tgt_server_info = await self.engine.writer.validate()
                tgt_server_name = tgt_server_info.get("community_name", "target community")
                modal.write(f"[bold red]Target Community:[/bold red] [green]{tgt_server_name}[/green]")
                modal.write(f"[bold red]WARNING: THE ACTIONS BELOW WILL DELETE DATA PERMANENTLY IN: {tgt_server_name}![/bold red]")
                modal.write("")
            else:
                modal.write("[bold red]WARNING: THIS WILL DELETE DATA PERMANENTLY! MUST PROCEED TO CONTINUE.[/bold red]")
                modal.write("")

            # Show info container
            modal.show_info("[bold red]Danger Zone Ready[/bold red]", "Fetching target entities...")
            modal.set_status(f"Awaiting Confirmation for {len(selections)} Destructive Operations...")

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

            choice = await modal.phase_wait_confirm(
                btn_start_label="WIPE ALL DATA", 
                show_id=False, 
                btn_start_variant="error",
                btn_start_tooltip="WARNING\nIrreversible Operation!\nProceed with Caution"
            )
            if choice == "btn_back":
                modal.dismiss()
                self._open_danger_menu()
                return
            elif choice == "btn_main_menu":
                modal.dismiss()
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
            
            modal.phase_report("Danger Zone Operations Complete", show_back=False)
            modal.write("[bold green]All selected destructive operations finished.[/bold green]")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
            modal.phase_report("Danger Zone Batch", "error", show_back=False)
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
                protected = ["reaperfiles-logs", "reaper_logs", "reaper-logs"]
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
                    community_id = self.engine.config.fluxer_server_id
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
                    community_id = self.engine.config.fluxer_server_id
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
    async def _perform_auto_matching(self):
        """Matches Discord entities (roles, channels, emojis, stickers) with target platform items by name."""
        if not self.engine:
            return
        
        reader = self.engine.source_reader
        writer = self.engine.writer
        is_fluxer = self.target_platform == "fluxer"

        # 1. Fetch target data for comparison
        target_roles_map = {}
        target_chans_map = {}
        target_cats_map = {}
        target_emojis_map = {}
        target_stickers_map = {}
        try:
            if is_fluxer:
                tid = self.engine.config.fluxer_server_id
                target_roles_raw = await writer.client.get_guild_roles(tid)
                target_roles_map = {r.get("name", "").lower(): str(r.get("id")) for r in target_roles_raw}
                
                target_emojis_raw = await writer.client.get_guild_emojis(tid)
                target_emojis_map = {e.get("name", "").lower(): str(e.get("id")) for e in target_emojis_raw}
                
                # RE-INITIALIZE STATE if we found community info
                # This ensures mapping persistence even if validate_all was skipped
                try:
                    community_info = await writer.client.get_guild(tid)
                    if community_info:
                        self.engine.ensure_state_initialized(str(tid or ""), community_info.get("name", "Target"))
                except Exception:
                    pass

                try:
                    target_stickers_raw = await writer.client.get_guild_stickers(tid)
                    target_stickers_map = {s.get("name", "").lower(): str(s.get("id")) for s in target_stickers_raw}
                except Exception:
                    pass
            else:
                server = await writer._get_server()
                target_roles_map = {r.name.lower(): str(r.id) for r in server.roles.values()}
                
                target_emojis_raw = await server.fetch_emojis()
                target_emojis_map = {e.name.lower(): str(e.id) for e in target_emojis_raw}

                # RE-INITIALIZE STATE
                tid = self.engine.config.stoat_server_id
                self.engine.ensure_state_initialized(str(tid or ""), server.name)
            
            target_chans_raw = await writer.get_channels()
            target_chans_map = {c.get("name", "").lower(): str(c.get("id")) for c in target_chans_raw if c.get("type") != 4}
            target_cats_map = {c.get("name", "").lower(): str(c.get("id")) for c in target_chans_raw if c.get("type") == 4}
        except Exception as e:
            logger.warning(f"Auto-matching: failed to fetch target data: {e}")
            return # Cannot match without target data

        # 1.5 Cleanup deleted entities from mapping database
        # This prevents "Ghost" mappings to channels/roles that were deleted on target
        valid_chan_ids = {str(c.get("id")) for c in target_chans_raw}
        valid_cat_ids = {str(c.get("id")) for c in target_chans_raw if c.get("type") == 4}
        valid_role_ids = set(target_roles_map.values())
        valid_emoji_ids = set(target_emojis_map.values())

        # Channels
        for src_id, tgt_id in self.engine.state.channel_map.items():
            if str(tgt_id) not in valid_chan_ids:
                logger.info(f"Auto-matching: clearing deleted channel mapping {src_id} -> {tgt_id}")
                self.engine.state.remove_target_channel_mapping(src_id)

        # Categories
        for src_id, tgt_id in self.engine.state.category_map.items():
            if str(tgt_id) not in valid_cat_ids:
                logger.info(f"Auto-matching: clearing deleted category mapping {src_id} -> {tgt_id}")
                self.engine.state.remove_category_mapping(src_id)

        # Roles
        for src_id, tgt_id in self.engine.state.role_map.items():
            if str(tgt_id) not in valid_role_ids:
                logger.info(f"Auto-matching: clearing deleted role mapping {src_id} -> {tgt_id}")
                self.engine.state.remove_role_mapping(src_id)

        # Emojis
        for src_id, tgt_id in self.engine.state.emoji_map.items():
            if str(tgt_id) not in valid_emoji_ids:
                # Emojis might be URLs in some platforms, but we check if they are IDs first
                if isinstance(tgt_id, str) and tgt_id.isdigit():
                    logger.info(f"Auto-matching: clearing deleted emoji mapping {src_id} -> {tgt_id}")
                    self.engine.state.remove_emoji_mapping(src_id)

        # 2. Match entities
        try:
            # Roles
            src_roles = await reader.get_roles()
            for r in src_roles:
                name_l = r.name.lower()
                if name_l in target_roles_map and not self.engine.state.get_target_role_id(r.id):
                    logger.info(f"Auto-matched Role: {r.name} -> {target_roles_map[name_l]}")
                    self.engine.state.set_target_role_mapping(r.id, target_roles_map[name_l])
            
            # Categories
            src_cats = await reader.get_categories()
            for cat in src_cats:
                name_l = cat.name.lower()
                if name_l in target_cats_map and not self.engine.state.get_target_category_id(cat.id):
                    logger.info(f"Auto-matched Category: {cat.name} -> {target_cats_map[name_l]}")
                    self.engine.state.set_target_category_mapping(cat.id, target_cats_map[name_l])
            
            # Channels
            src_channels = await reader.get_channels()
            for ch in src_channels:
                name_l = ch.name.lower()
                if name_l in target_chans_map and not self.engine.state.get_target_channel_id(ch.id):
                    logger.info(f"Auto-matched Channel: {ch.name} -> {target_chans_map[name_l]}")
                    self.engine.state.set_target_channel_mapping(ch.id, target_chans_map[name_l])
            
            # Emojis
            src_emojis = await reader.get_emojis()
            for e in src_emojis:
                name_l = e.name.lower()
                if name_l in target_emojis_map and not self.engine.state.get_target_emoji_id(e.id):
                    logger.info(f"Auto-matched Emoji: {e.name} -> {target_emojis_map[name_l]}")
                    self.engine.state.set_target_emoji_mapping(e.id, target_emojis_map[name_l])
            
            # Stickers
            if is_fluxer:
                src_stickers = await reader.get_stickers()
                for s in src_stickers:
                    name_l = s.name.lower()
                    if name_l in target_stickers_map and not self.engine.state.get_target_sticker_id(s.id):
                        logger.info(f"Auto-matched Sticker: {s.name} -> {target_stickers_map[name_l]}")
                        self.engine.state.set_target_sticker_mapping(s.id, target_stickers_map[name_l])
        except Exception as e:
            logger.error(f"Auto-matching error: {e}\n{traceback.format_exc()}")
            logger.warning(f"Auto-matching error: {e}")

        return {
            "target_roles": target_roles_map,
            "target_channels": target_chans_map,
            "target_categories": target_cats_map,
            "target_emojis": target_emojis_map,
            "target_stickers": target_stickers_map
        }

    async def _fetch_clone_preview(self, selections: list[str]) -> dict[str, Any]:
        """Fetches preview data from Discord (source server) for cloning confirmation,
        comparing with existing mappings in SQLite for presence highlighting."""
        preview = {}
        reader = self.engine.source_reader
        
        # We rely on the global auto-match that ran during connection
        mapping_ch = self.engine.state.channel_map
        mapping_cat = self.engine.state.category_map
        mapping_role = self.engine.state.role_map

        try:
            if "sub_clone_roles" in selections:
                roles = await reader.get_roles()
                # Highlight if existing in mapping
                preview["roles"] = [(r.name, str(r.id) in mapping_role) for r in roles]
        except Exception as e:
            logger.warning(f"Clone Preview: failed to fetch roles: {e}")

        try:
            if "sub_clone_channels" in selections:
                src_categories = await reader.get_categories()
                src_channels = await reader.get_channels()
                
                # Build hierarchy for preview
                structure = {}
                for cat in src_categories:
                    cat_exists = str(cat.id) in mapping_cat
                    structure[cat.id] = (cat.name, cat_exists, [])
                
                for ch in src_channels:
                    ch_exists = str(ch.id) in mapping_ch
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



    # ── backup workers ───────────────────────────────────────────────────
    @work(exclusive=True)
    async def run_backup_messages(self) -> None:
        """UI entry point for full backup."""
        modal_prog = ProgressScreen(log_level=self.config.log_level)
        self.app.push_screen(modal_prog)
        await asyncio.sleep(0.1)

        try:
            modal_prog.set_status("Fetching channels...")
            await self.engine.source_reader.start()
            await self.exporter.setup()

            all_channels = await self.engine.source_reader.get_channels()
            all_categories = await self.engine.source_reader.get_categories()
            cat_map = {c.id: c.name for c in all_categories}

            eligible_channels = [
                c for c in all_channels
                if c.type in [
                    self.engine.source_reader.CHANNEL_TYPE_TEXT,
                    self.engine.source_reader.CHANNEL_TYPE_NEWS,
                    self.engine.source_reader.CHANNEL_TYPE_FORUM
                ]
            ]

            if not eligible_channels:
                modal_prog.write("[yellow]No text/news channels found to backup.[/yellow]")
                modal_prog.allow_close()
                return

            # Analyze which are already backed up
            backed_up_ids = set()
            any_found = False
            if self.exporter.db:
                channel_stats = self.exporter.db.get_stats_by_channel()
                for chan in eligible_channels:
                    if chan.id in channel_stats:
                        any_found = True
                        backed_up_ids.add(chan.id)

            # Manual selection
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            def check_channels(reply: dict | None) -> None:
                if not future.done(): future.set_result(reply)

            self.app.push_screen(
                ChannelSelectScreen(eligible_channels, cat_map, backed_up_ids, any_found),
                check_channels,
            )

            reply = await future
            if not reply: return

            selected_ids = reply["channels"]
            force_overwrite = reply["force"]
            selected_channels = [c for c in eligible_channels if c.id in selected_ids]

            # Confirmation phase
            new_channels = [c for c in selected_channels if c.id not in backed_up_ids]
            existing_channels = [c for c in selected_channels if c.id in backed_up_ids]

            modal_confirm = ProgressScreen(log_level=self.config.log_level)
            self.app.push_screen(modal_confirm)
            await asyncio.sleep(0.1)

            modal_confirm.set_status(f"Confirm to proceed with Backup of [bold]{len(selected_channels)}[/bold] channels")
            modal_confirm.show_info(f"[cyan]Backup Channels[/cyan]", f"{len(new_channels)} new, {len(existing_channels)} existing")

            choice = await modal_confirm.phase_wait_confirm(btn_start_label="Start Channel Backup", show_id=False)
            if choice != "btn_start_first":
                modal_confirm.dismiss()
                return

            modal_confirm.cancel_callback = lambda: setattr(self.exporter, "is_running", False)
            modal_confirm.phase_progress()

            await self._logic_full_backup(
                modal=modal_confirm,
                selected_channels=selected_channels,
                force_overwrite=force_overwrite,
                is_autotest=False
            )

        except Exception as e:
            logger.error(f"Backup Error: {traceback.format_exc()}")
            modal_prog.write(f"[bold red]Backup failed: {e}[/bold red]")
            modal_prog.phase_report("Backup", "error", show_back=False)
        finally:
            await self.engine.close_connections()

    async def _logic_full_backup(self, modal: ProgressScreen, selected_channels: list, force_overwrite: bool, is_autotest: bool = False) -> None:
        """Non-interactive core backup logic."""
        if not self.exporter.is_running:
            self.exporter.is_running = True

        try:
            # 1. Metadata and Assets
            modal.set_status("Exporting Server Profile...")
            await self.exporter.export_metadata()
            await self.exporter.download_server_assets()
            await self.exporter.export_channels_structure()
            await self.exporter.export_roles()
            await self.exporter.export_assets()

            # Pre-fetch all members once for role resolution during message export
            modal.set_status("Pre-fetching server members...")
            await self.exporter.prefetch_members()

            # 2. Channel Messages
            total_chans = len(selected_channels)
            modal.write(f"\n[bold cyan]Backing up {total_chans} channels...[/bold cyan]")
            modal.show_stats()

            for i, chan in enumerate(selected_channels):
                if not self.exporter.is_running: break
                
                modal.set_item_status(f"[cyan]Processing ({i+1}/{total_chans}): #{chan.name}[/cyan]")
                modal.set_progress(i, total_chans)
                modal.write(f"[cyan]Backing up: #{chan.name}[/cyan]")

                async def update_backup(name, count, author_name=None, message_preview=None, thread_count=0, file_count=0):
                    modal.update_stats(messages=str(count), threads=str(thread_count), files=str(file_count))
                    if author_name and message_preview and count % 20 == 0:
                        modal.write(f"[dim]{author_name}:[/dim] {message_preview}")

                await self.exporter.export_channel_messages(
                    chan.id, progress_callback=update_backup, force=force_overwrite
                )
                modal.write(f"[green]Completed: #{chan.name}[/green]")

            modal.set_progress(total_chans, total_chans)
            modal.write("[bold green]Backup complete![/bold green]")
            modal.phase_report("Full Backup", show_back=False)

        except Exception as e:
            modal.write(f"[bold red]Core backup failed: {e}[/bold red]")
            logger.error(f"Core Backup Error: {traceback.format_exc()}")
            raise e

            modal_prog.phase_progress()
            modal_prog.show_stats()
            
            # Reset running flag and set cancel callback
            self.exporter.is_running = True
            modal_prog.cancel_callback = lambda: setattr(self.exporter, "is_running", False)

            total_chans = len(selected_channels)
            modal_prog.set_status("Backing up messages...")
            modal_prog.write(f"[yellow]Starting backup for {total_chans} channels...[/yellow]")

            accumulated_msgs = 0
            accumulated_threads = 0
            accumulated_files = 0

            for i, chan in enumerate(selected_channels):
                if not self.exporter.is_running:
                    modal_prog.write("[bold red]Backup cancelled by user.[/bold red]")
                    break
                await asyncio.sleep(0.01) # Yield to UI thread to keep it responsive
                
                backup_exists = chan.id in backed_up_ids
                is_sync = backup_exists and not force_overwrite

                label = "Syncing Backup" if is_sync else "Backing up"
                modal_prog.set_item_status(f"[cyan]Processing ({i+1}/{total_chans}): #{chan.name}[/cyan]")
                modal_prog.set_progress(i, total_chans)
                modal_prog.write(f"[cyan]{label}: {chan.name}[/cyan]")
                logger.info(f"{label} for channel: #{chan.name} ({chan.id})")

                _msg_log_counter = 0
                async def update_msg_count(name, count, author_name=None, message_preview=None, thread_count=0, file_count=0):
                    nonlocal _msg_log_counter
                    modal_prog.update_stats(messages=str(count), threads=str(thread_count), files=str(file_count))
                    _msg_log_counter += 1
                    if author_name and message_preview and _msg_log_counter % 10 == 0:
                        modal_prog.write(f"[bold]{author_name}:[/bold] {message_preview}")

                accumulated_msgs, accumulated_threads, accumulated_files = await self.exporter.export_channel_messages(
                    chan.id, progress_callback=update_msg_count, force=force_overwrite,
                    accumulated_count=accumulated_msgs, accumulated_threads=accumulated_threads, accumulated_files=accumulated_files,
                    after_id=after_id
                )

                modal_prog.write(f"[green]Completed: {chan.name}[/green]")

            if not self.exporter.is_running:
                modal_prog.set_item_status("[bold red]Backup Cancelled.[/bold red]")
                modal_prog.phase_report("Message Backup", "stopped", show_back=False)
                return

            modal_prog.set_progress(total_chans, total_chans)
            modal_prog.set_item_status("[bold green]Backup completed successfully![/bold green]")

            await self.exporter.export_metadata()
            modal_prog.write("[bold green]Message backup complete![/bold green]")
            logger.info("Message backup operation completed successfully.")
            modal_prog.phase_report("Message Backup", show_back=False)

        except Exception as e:
            logger.error(f"Message backup failed: {e}\n{traceback.format_exc()}")
            modal_prog.write(f"[bold red]Message backup failed: {e}[/bold red]")
            modal_prog.phase_report("Message Backup", "error", show_back=False)
        finally:
            await self.engine.close_connections()
            self.run_validate()


    @work(exclusive=True)
    async def run_backup_sync(self) -> None:
        modal_prog = ProgressScreen(log_level=self.config.log_level)
        self.app.push_screen(modal_prog)
        await asyncio.sleep(0.1)
        modal_prog.phase_progress()

        try:
            modal_prog.set_status("Starting sync...")
            await self.engine.source_reader.start()
            await self.exporter.setup()

            # Gather and print summary
            server = getattr(self.engine.source_reader, 'guild', None)
            if server:
                modal_prog.write(f"[bold cyan]Server Profile to Sync:[/bold cyan]")
                modal_prog.write(f"  Name: [green]{server.name}[/green]")
                modal_prog.write(f"  Icon: [green]{'Present' if server.icon else 'None'}[/green]")
                modal_prog.write(f"  Roles: [green]{len(getattr(server, 'roles', []))}[/green]")
                modal_prog.write(f"  Emojis: [green]{len(getattr(server, 'emojis', []))}[/green]")
                modal_prog.write(f"  Channels: [green]{len(getattr(server, 'channels', []))}[/green]")
                modal_prog.write("\n[dim]This operation will update the profile and scan existing backed-up channels for new messages.[/dim]")
                modal_prog.write("")

            modal_prog.show_info("[bold green]Sync Ready[/bold green]", f"Overview: {len(server.channels) if server else '?'} Channels")
            modal_prog.set_status("Awaiting Confirmation to Sync Profile and Messages...")

            choice = await modal_prog.phase_wait_confirm(
                btn_start_label="Start Sync",
                show_id=False
            )
            if choice in ("btn_back", "btn_main_menu"):
                modal_prog.dismiss()
                self.engine.is_running = False
                await self.engine.close_connections()
                if choice == "btn_main_menu":
                    pass
                return

            modal_prog.cancel_callback = lambda: setattr(self.engine, "is_running", False)
            modal_prog.phase_progress()
            modal_prog.set_status("Updating Server Profile & Structure...")
            
            modal_prog.write("[yellow]Updating server metadata...[/yellow]")
            await self.exporter.export_metadata()
            
            modal_prog.write("[yellow]Syncing server assets (icon/banner)...[/yellow]")
            await self.exporter.download_server_assets()
            
            modal_prog.write("[yellow]Syncing server structure & channels...[/yellow]")
            await self.exporter.export_channels_structure()
            
            modal_prog.write("[yellow]Syncing roles & permissions...[/yellow]")
            roles = await self.exporter.export_roles()
            
            modal_prog.write("[yellow]Syncing custom emojis & stickers...[/yellow]")
            e_count, s_count = await self.exporter.export_assets()
            
            modal_prog.write(f"[bold green]Profile Sync Complete:[/bold green] {len(roles)} roles, {e_count} emojis, {s_count} stickers.")
            modal_prog.write("")

            all_channels = await self.engine.source_reader.get_channels()
            eligible_channels = [
                c for c in all_channels
                if c.type in [
                    self.engine.source_reader.CHANNEL_TYPE_TEXT,
                    self.engine.source_reader.CHANNEL_TYPE_NEWS,
                    self.engine.source_reader.CHANNEL_TYPE_FORUM
                ]
            ]

            # Get channels that have messages in the database
            backed_up_channel_ids = set()
            if self.exporter.db:
                backed_up_channel_ids = set(self.exporter.db.get_stats_by_channel().keys())
            
            selected_channels = [
                c for c in eligible_channels
                if c.id in backed_up_channel_ids
            ]

            if not selected_channels:
                modal_prog.write("[yellow]No existing backups found to sync.[/yellow]")
            else:
                total_chans = len(selected_channels)
                modal_prog.show_stats()
                modal_prog.set_status("Syncing messages...")
                modal_prog.write(f"[yellow]Syncing {total_chans} channels...[/yellow]")
                
                # Reset running flag and set cancel callback
                self.exporter.is_running = True
                modal_prog.cancel_callback = lambda: setattr(self.exporter, "is_running", False)
                
                accumulated_msgs = 0
                accumulated_threads = 0
                accumulated_files = 0
                
                for i, chan in enumerate(selected_channels):
                    if not self.exporter.is_running:
                        modal_prog.write("[bold red]Sync cancelled by user.[/bold red]")
                        break
                    await asyncio.sleep(0.01) # Yield to UI thread
                    
                    modal_prog.set_item_status(f"[cyan]Syncing ({i+1}/{total_chans}): #{chan.name}[/cyan]")
                    modal_prog.set_progress(i, total_chans)
                    modal_prog.write(f"[cyan]Syncing: {chan.name}[/cyan]")
                    logger.info(f"Syncing backup for channel: #{chan.name} ({chan.id})")

                    _msg_log_counter = 0
                    async def update_msg_count(name, count, author_name=None, message_preview=None, thread_count=0, file_count=0):
                        nonlocal _msg_log_counter
                        modal_prog.update_stats(messages=str(count), threads=str(thread_count), files=str(file_count))
                        _msg_log_counter += 1
                        if author_name and message_preview and _msg_log_counter % 10 == 0:
                            modal_prog.write(f"[bold]{author_name}:[/bold] {message_preview}")

                    accumulated_msgs, accumulated_threads, accumulated_files = await self.exporter.export_channel_messages(
                        chan.id, progress_callback=update_msg_count, force=False,
                        accumulated_count=accumulated_msgs, accumulated_threads=accumulated_threads, accumulated_files=accumulated_files
                    )
                    modal_prog.write(f"[green]Synced: {chan.name}[/green]")
                
                if not self.exporter.is_running:
                    modal_prog.set_item_status("[bold red]Sync Cancelled.[/bold red]")
                    modal_prog.phase_report("Backup Sync", "stopped", show_back=False)
                    return

                modal_prog.set_progress(total_chans, total_chans)
                modal_prog.set_item_status("[bold green]Sync operation complete![/bold green]")

            await self.exporter.export_metadata()
            modal_prog.write("[bold green]Sync operation complete![/bold green]")
            logger.info("Sync operation completed successfully.")
            modal_prog.phase_report("Backup Sync", show_back=False)

        except Exception as e:
            logger.error(f"Sync failed: {e}\n{traceback.format_exc()}")
            modal_prog.write(f"[bold red]Sync failed: {e}[/bold red]")
            modal_prog.phase_report("Backup Sync", "error", show_back=False)
        finally:
            await self.engine.close_connections()
            self.run_validate()
