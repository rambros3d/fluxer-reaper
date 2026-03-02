import sys
import asyncio
import discord
import logging
import json
import re
from pathlib import Path
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll, Grid
from textual.widgets import Header, Footer, Button, Static, Label, Input, Checkbox, RadioButton, ProgressBar, RichLog, Rule
from textual.screen import Screen, ModalScreen
from textual import work

from src.core.configuration import load_config, save_config
from src.core.base import MigrationContext
from src.disco_reaper.exporter import DiscordExporter

import src.fluxer.roles_permissions as fluxer_roles
import src.stoat.roles_permissions as stoat_roles
import src.fluxer.emoji_stickers as fluxer_emoji_stickers
import src.stoat.emoji_stickers as stoat_emoji_stickers
import src.fluxer.server_metadata as fluxer_metadata
import src.stoat.server_metadata as stoat_metadata
import src.fluxer.migrate_message as fluxer_migrate
import src.stoat.migrate_message as stoat_migrate
from src.core.audit import log_audit_event

# -------------------------------------------------------------------------
# Shared Modals
# -------------------------------------------------------------------------

class ConfigModal(ModalScreen[dict]):
    """Modal for configuring token and server ID (Reaper focus but usable everywhere)."""
    
    def __init__(self, current_token: str, current_server: str):
        super().__init__()
        self.current_token = current_token
        self.current_server = current_server

    def compose(self) -> ComposeResult:
        with Vertical(id="config_dialog"):
            yield Label("Discord Configuration", id="config_title")
            yield Label("Discord Bot Token:")
            yield Input(value=self.current_token, id="token_input")
            yield Label("Discord Server ID:")
            yield Input(value=self.current_server, id="server_input")
            with Horizontal(id="config_buttons"):
                yield Button("Save", variant="success", id="btn_save")
                yield Button("Cancel", variant="primary", id="btn_cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_save":
            token = self.query_one("#token_input", Input).value
            server = self.query_one("#server_input", Input).value
            self.dismiss({"token": token, "server": server})
        elif event.button.id == "btn_cancel":
            self.dismiss(None)

class ChannelSelectModal(ModalScreen[dict]):
    """Modal for selecting channels using a simple checkbox list."""
    
    def __init__(self, channels: list, categories: dict, backed_up_ids: set, any_found: bool):
        super().__init__()
        self.channels_by_category = {}
        for c in channels:
            cat_id = getattr(c, 'category_id', None)
            if cat_id not in self.channels_by_category:
                self.channels_by_category[cat_id] = []
            self.channels_by_category[cat_id].append(c)
            
        self.categories = categories
        self.backed_up_ids = backed_up_ids
        self.any_found = any_found

    def compose(self) -> ComposeResult:
        with Vertical(id="channel_dialog"):
            yield Label(f"Select Channels to Backup", id="chan_title")
            
            with VerticalScroll(id="channel_list_scroll"):
                cat_ids = sorted([k for k in self.channels_by_category.keys() if k is not None], 
                               key=lambda k: self.categories.get(k, ""))
                
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
            
            with Horizontal(id="confirm_buttons"):
                if self.any_found:
                    yield Label("Existing backups found:", classes="label_warning")
                    yield Button("Sync", variant="success", id="btn_sync")
                    yield Button("Force Overwrite", variant="error", id="btn_force")
                else:
                    yield Button("Backup", variant="success", id="btn_backup")
                yield Button("Cancel", id="btn_cancel_chan")

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

class ProgressModal(ModalScreen[None]):
    """Modal to show progress of operations."""
    
    def compose(self) -> ComposeResult:
        with Vertical(id="progress_dialog"):
            yield Label("Operation Status", id="progress_status")
            yield ProgressBar(total=None, show_eta=False, id="progress_bar")
            yield RichLog(id="progress_log", highlight=True, markup=True)
            yield Button("Close", id="btn_close_progress", disabled=True)
            
    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "btn_close_progress":
            self.dismiss(None)
            
    def write_to_log(self, message: str):
        self.query_one("#progress_log", RichLog).write(message)
        
    def set_status(self, status: str):
        self.query_one("#progress_status", Label).update(status)
        
    def allow_close(self):
        btn = self.query_one("#btn_close_progress", Button)
        btn.disabled = False
        btn.variant = "success"
        pb = self.query_one("#progress_bar", ProgressBar)
        if pb.total is None: pb.total = 100
        pb.progress = pb.total

# -------------------------------------------------------------------------
# Reaper Screen
# -------------------------------------------------------------------------

class ReaperScreen(Screen):
    BINDINGS = [
        ("q", "app.exit", "Quit"),
        ("c", "config", "Config"),
        ("escape", "app.pop_screen", "Back"),
    ]

    def __init__(self):
        super().__init__()
        self.validation_results = {}
        self.tokens_valid = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="main_container"):
            yield Label("Disco Reaper - Server Backup Tool", id="title_label")
            with Vertical(id="info_container"):
                yield Label("Loading...", id="lbl_server")
                yield Label("", id="lbl_bot")
                yield Label("", id="lbl_backup")
            with Vertical(id="actions_container"):
                yield Button("Backup Server Profile", id="btn_backup_profile", disabled=True)
                yield Button("Backup Channel Messages", id="btn_backup_msgs", disabled=True)
                yield Button("Update & Sync Backup", id="btn_backup_sync", disabled=True)
                yield Rule()
                yield Button("Configuration", id="btn_config")
                yield Button("Back to Menu", id="btn_back")
        yield Footer()

    def on_mount(self) -> None:
        self.validate_config()
        
    @property
    def config(self): return self.app.config
    @property
    def engine(self): return self.app.reaper_engine
    @property
    def exporter(self): return self.app.exporter

    @work(exclusive=True)
    async def validate_config(self) -> None:
        def update_ui(server_text, bot_text, backup_text, buttons_enabled):
            self.query_one("#lbl_server", Label).update(f"Source Server: {server_text}")
            self.query_one("#lbl_bot", Label).update(f"Bot name: {bot_text}")
            self.query_one("#lbl_backup", Label).update(backup_text)
            for btn_id in ["#btn_backup_profile", "#btn_backup_msgs", "#btn_backup_sync"]:
                self.query_one(btn_id, Button).disabled = not buttons_enabled

        d_token = self.config.discord_bot_token
        fillers = ["DISCORD_BOT_TOKEN", "000000000000000000", "DISCORD_SERVER_ID", "", None]
        if d_token in fillers or self.config.discord_server_id in fillers:
            update_ui("[red]NOT CONNECTED[/red]", "[red]UNKNOWN[/red]", "", False)
            self.tokens_valid = False
            return

        update_ui("[yellow]Validating...[/yellow]", "[yellow]Validating...[/yellow]", "", False)
        
        try:
            res = await self.engine.discord_reader.validate()
        except Exception as e:
            res = {}
            update_ui(f"[red]Validation Error: {e}[/red]", "", "", False)
            self.tokens_valid = False
            return
            
        self.validation_results["discord_token"] = res.get("token", False)
        self.validation_results["discord_bot_name"] = res.get("bot_name")
        self.validation_results["discord_server"] = res.get("server", False)
        self.validation_results["discord_server_name"] = res.get("server_name")
        
        self.tokens_valid = res.get("token") and res.get("server")
        
        d_name = self.validation_results.get("discord_server_name")
        server_display = f"[green]\"{d_name}\"[/green]" if d_name else "[red]NOT CONNECTED[/red]"
        
        b_name = self.validation_results.get("discord_bot_name")
        bot_display = f"[green]\"{b_name}\"[/green]" if b_name else "[red]UNKNOWN[/red]"
        
        backup_text = ""
        if self.tokens_valid:
            backup_ts = self.get_backup_info()
            if backup_ts:
                backup_text = f"Backup Found: [yellow]{backup_ts}[/yellow]"
        
        update_ui(server_display, bot_display, backup_text, self.tokens_valid)

    def get_backup_info(self):
        d_name = self.validation_results.get("discord_server_name")
        d_id = self.config.discord_server_id
        if not d_name or not d_id:
            return None
            
        safe_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', d_name)
        export_path = Path(".") / f"EXPORT-{safe_name}-{d_id}"
        profile_file = export_path / "server_profile.json"
        
        if profile_file.exists():
            try:
                with open(profile_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    ts_str = data.get("last_backup")
                    if ts_str:
                        dt = datetime.fromisoformat(ts_str)
                        return dt.strftime("%d-%b-%Y %H:%M")
            except Exception:
                pass
        return None

    def action_config(self) -> None:
        self.open_config()

    def open_config(self) -> None:
        def check_reply(reply: dict | None) -> None:
            if reply is not None:
                self.config.discord_bot_token = reply["token"]
                self.config.discord_server_id = reply["server"]
                save_config(self.config, self.app.config_path)
                self.app.reinit_engines()
                self.validate_config()
                
        self.app.push_screen(
            ConfigModal(self.config.discord_bot_token, self.config.discord_server_id), 
            check_reply
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_back":
            self.app.pop_screen()
        elif event.button.id == "btn_config":
            self.open_config()
        elif event.button.id == "btn_backup_profile":
            self.run_backup_profile()
        elif event.button.id == "btn_backup_msgs":
            self.run_backup_messages()
        elif event.button.id == "btn_backup_sync":
            self.run_backup_sync()

    @work(exclusive=True)
    async def run_backup_profile(self) -> None:
        modal = ProgressModal()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)
        
        try:
            modal.set_status("Starting readers...")
            await self.engine.discord_reader.start()
            meta = await self.exporter.setup()
            
            modal.write_to_log("[yellow]Backing up server profile & skeleton...[/yellow]")
            await self.exporter.export_metadata()
            await self.exporter.download_server_assets()
            
            modal.write_to_log("Exporting structure...")
            _, cat_count, chan_count = await self.exporter.export_channels_structure()
            
            modal.write_to_log("Exporting assets...")
            e_count, s_count = await self.exporter.export_assets()
            
            modal.write_to_log(f"[bold green]Server Profile backed up to: {self.exporter.export_path}[/bold green]")
            modal.write_to_log(f"- {cat_count} categories & {chan_count} channels")
            modal.write_to_log(f"- {e_count} emojis, {s_count} stickers.")
            
        except discord.Forbidden as e:
            modal.write_to_log(f"[bold red]Backup failed: {e}[/bold red]")
        except Exception as e:
            modal.write_to_log(f"[bold red]Error: {e}[/bold red]")
        finally:
            await self.engine.close_connections()
            modal.set_status("Finished.")
            modal.allow_close()

    @work(exclusive=True)
    async def run_backup_messages(self) -> None:
        modal_prog = ProgressModal()
        self.app.push_screen(modal_prog)
        await asyncio.sleep(0.1)
        
        try:
            modal_prog.set_status("Fetching channels...")
            await self.engine.discord_reader.start()
            await self.exporter.setup()
            
            await self.exporter.export_channels_structure()
            all_channels = await self.engine.discord_reader.get_channels()
            all_categories = await self.engine.discord_reader.get_categories()
            cat_map = {c.id: c.name for c in all_categories}
            
            eligible_channels = [
                c for c in all_channels 
                if c.type in [discord.ChannelType.text, discord.ChannelType.news, discord.ChannelType.forum]
            ]
            
            if not eligible_channels:
                modal_prog.write_to_log("[yellow]No text/news channels found to backup.[/yellow]")
                modal_prog.allow_close()
                return
                
            any_found = False
            backed_up_ids = set()
            for chan in eligible_channels:
                if (self.exporter.export_path / "message_backup" / f"{chan.id}.json").exists():
                    any_found = True
                    backed_up_ids.add(chan.id)

            self.app.pop_screen()
            
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            
            def check_channels(reply: dict | None) -> None:
                if not future.done():
                    future.set_result(reply)
                    
            self.app.push_screen(
                ChannelSelectModal(eligible_channels, cat_map, backed_up_ids, any_found),
                check_channels
            )
            
            reply = await future
            if not reply:
                return
                
            selected_ids = reply["channels"]
            force_overwrite = reply["force"]
            
            selected_channels = [c for c in eligible_channels if c.id in selected_ids]
            
            self.app.push_screen(modal_prog)
            await asyncio.sleep(0.1)
            
            total_chans = len(selected_channels)
            modal_prog.set_status("Backing up messages...")
            modal_prog.write_to_log(f"[yellow]Starting backup for {total_chans} channels...[/yellow]")
            
            for chan in selected_channels:
                backup_exists = (self.exporter.export_path / "message_backup" / f"{chan.id}.json").exists()
                is_sync = backup_exists and not force_overwrite
                
                label = "Syncing Backup" if is_sync else "Backing up"
                modal_prog.write_to_log(f"[cyan]{label}: {chan.name}[/cyan]")
                
                async def update_msg_count(name, count):
                    modal_prog.set_status(f"{name}: {count} messages")
                
                await self.exporter.export_channel_messages(chan.id, progress_callback=update_msg_count, force=force_overwrite)
                await self.exporter.export_threads(chan.id, progress_callback=update_msg_count, force=force_overwrite)
                
                modal_prog.write_to_log(f"[green]Completed: {chan.name}[/green]")
                
            await self.exporter.export_metadata()
            modal_prog.write_to_log("[bold green]Message backup complete![/bold green]")
            
        except Exception as e:
            modal_prog.write_to_log(f"[bold red]Message backup failed: {e}[/bold red]")
        finally:
            await self.engine.close_connections()
            modal_prog.set_status("Finished.")
            modal_prog.allow_close()

    @work(exclusive=True)
    async def run_backup_sync(self) -> None:
        modal_prog = ProgressModal()
        self.app.push_screen(modal_prog)
        await asyncio.sleep(0.1)
        
        try:
            modal_prog.set_status("Starting sync...")
            await self.engine.discord_reader.start()
            await self.exporter.setup()
            
            modal_prog.write_to_log("Updating structure...")
            await self.exporter.export_metadata()
            await self.exporter.download_server_assets()
            await self.exporter.export_channels_structure()
            await self.exporter.export_assets()
            
            all_channels = await self.engine.discord_reader.get_channels()
            eligible_channels = [
                c for c in all_channels 
                if c.type in [discord.ChannelType.text, discord.ChannelType.news, discord.ChannelType.forum]
            ]
            
            selected_channels = [
                c for c in eligible_channels 
                if (self.exporter.export_path / "message_backup" / f"{c.id}.json").exists()
            ]
            
            if not selected_channels:
                modal_prog.write_to_log("[yellow]No existing backups found to sync.[/yellow]")
            else:
                modal_prog.write_to_log(f"[yellow]Syncing {len(selected_channels)} channels...[/yellow]")
                for chan in selected_channels:
                    modal_prog.write_to_log(f"[cyan]Syncing: {chan.name}[/cyan]")
                    
                    async def update_msg_count(name, count):
                        modal_prog.set_status(f"{name}: {count} messages")
                        
                    await self.exporter.export_channel_messages(chan.id, progress_callback=update_msg_count, force=False)
                    await self.exporter.export_threads(chan.id, progress_callback=update_msg_count, force=False)
                    modal_prog.write_to_log(f"[green]Synced: {chan.name}[/green]")
                    
            await self.exporter.export_metadata()
            modal_prog.write_to_log("[bold green]Sync operation complete![/bold green]")
            
        except Exception as e:
            modal_prog.write_to_log(f"[bold red]Sync failed: {e}[/bold red]")
        finally:
            await self.engine.close_connections()
            modal_prog.set_status("Finished.")
            modal_prog.allow_close()

# -------------------------------------------------------------------------
# Shuttle Screen
# -------------------------------------------------------------------------

class ShuttleConfigModal(ModalScreen[dict]):
    """Modal for configuring Shuttle targeted platform and tokens."""
    
    def __init__(self, config):
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        with Vertical(id="config_dialog"):
            yield Label("Shuttle Target Configuration", id="config_title")
            
            yield Label("Platform:")
            with Horizontal(id="platform_radios"):
                yield RadioButton("Fluxer", id="opt_fluxer", value=not self.config.use_stoat)
                yield RadioButton("Stoat", id="opt_stoat", value=self.config.use_stoat)
                
            yield Label("Fluxer Bot Token:")
            yield Input(value=self.config.fluxer_bot_token or "", id="fluxer_token")
            yield Label("Fluxer Community ID:")
            yield Input(value=self.config.fluxer_community_id or "", id="fluxer_community")
            yield Label("Stoat Bot Token:")
            yield Input(value=self.config.stoat_bot_token or "", id="stoat_token")
            yield Label("Stoat Server ID:")
            yield Input(value=self.config.stoat_server_id or "", id="stoat_server")
            
            with Horizontal(id="config_buttons"):
                yield Button("Save", variant="success", id="btn_save")
                yield Button("Cancel", variant="primary", id="btn_cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_save":
            is_stoat = self.query_one("#opt_stoat", RadioButton).value
            
            f_token = self.query_one("#fluxer_token", Input).value
            f_comm = self.query_one("#fluxer_community", Input).value
            s_token = self.query_one("#stoat_token", Input).value
            s_server = self.query_one("#stoat_server", Input).value
            
            # Save dummy if empty
            if f_token == "": f_token = None
            if f_comm == "": f_comm = None
            if s_token == "": s_token = None
            if s_server == "": s_server = None
            
            self.dismiss({
                "use_stoat": is_stoat,
                "fluxer_token": f_token,
                "fluxer_community": f_comm,
                "stoat_token": s_token,
                "stoat_server": s_server
            })
        elif event.button.id == "btn_cancel":
            self.dismiss(None)


class ShuttleScreen(Screen):
    BINDINGS = [
        ("q", "app.exit", "Quit"),
        ("c", "config", "Config"),
        ("escape", "app.pop_screen", "Back"),
    ]

    def __init__(self):
        super().__init__()
        self.validation_results = {}
        self.tokens_valid = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="main_container"):
            yield Label("", id="shuttle_title_label")
            with Vertical(id="info_container"):
                yield Label("Loading...", id="lbl_target")
                yield Label("Loading...", id="lbl_discord")
                yield Label("", id="lbl_perms")
            with Vertical(id="actions_container"):
                yield Button("Clone Server Template (Channels)", id="btn_clone", disabled=True)
                yield Button("Copy Roles & Permissions", id="btn_roles", disabled=True)
                yield Button("Copy Emojis & Stickers", id="btn_emojis", disabled=True)
                yield Button("Sync Content & Server Meta", id="btn_meta", disabled=True)
                yield Button("Migrate message history", id="btn_history", disabled=True)
                yield Rule()
                yield Button("Shuttle & Platform Configuration", id="btn_config")
                yield Button("Danger Zone \u26a0", id="btn_danger", variant="error", disabled=True)
                yield Button("Back to Menu", id="btn_back")
        yield Footer()

    def on_mount(self) -> None:
        self.validate_config()
        
    @property
    def config(self): return self.app.config
    
    @property
    def engine(self): 
        is_stoat = getattr(self.config, 'use_stoat', False)
        return self.app.stoat_engine if is_stoat else self.app.fluxer_engine

    @work(exclusive=True)
    async def validate_config(self) -> None:
        is_stoat = getattr(self.config, 'use_stoat', False)
        platform_name = "Stoat" if is_stoat else "Fluxer"
        
        def update_ui(target_text, discord_text, perms_text, buttons_enabled):
            self.query_one("#shuttle_title_label", Label).update(f"Server Shuttle Tools ({platform_name})")
            self.query_one("#lbl_target", Label).update(f"Target {platform_name}: {target_text}")
            self.query_one("#lbl_discord", Label).update(f"Source Discord: {discord_text}")
            self.query_one("#lbl_perms", Label).update(f"Permission Status: {perms_text}")
            for btn_id in ["#btn_clone", "#btn_roles", "#btn_emojis", "#btn_meta", "#btn_history", "#btn_danger"]:
                self.query_one(btn_id, Button).disabled = not buttons_enabled

        update_ui("[yellow]Validating...[/yellow]", "[yellow]Validating...[/yellow]", "[yellow]Validating...[/yellow]", False)
        
        engine = self.engine
        
        d_token = self.config.discord_bot_token
        fillers = ["DISCORD_BOT_TOKEN", "FLUXER_BOT_TOKEN", "STOAT_BOT_TOKEN", "000000000000000000", "DISCORD_SERVER_ID", "FLUXER_COMMUNITY_ID", "STOAT_SERVER_ID", "", None]
        
        discord_dummy = d_token in fillers or self.config.discord_server_id in fillers
        if is_stoat:
            target_dummy = self.config.stoat_bot_token in fillers or self.config.stoat_server_id in fillers
        else:
            target_dummy = self.config.fluxer_bot_token in fillers or self.config.fluxer_community_id in fillers
            
        discord_task = None
        if not discord_dummy:
            discord_task = asyncio.create_task(engine.discord_reader.validate())

        target_task = None
        if not target_dummy:
            target_task = asyncio.create_task(engine.writer.validate())

        tasks_to_wait = [t for t in [discord_task, target_task] if t is not None]
        
        try:
            done = set()
            if tasks_to_wait:
                done, _ = await asyncio.wait(tasks_to_wait, timeout=10.0, return_when=asyncio.ALL_COMPLETED)
                
            d_res = discord_task.result() if discord_task in done else {}
            t_res = target_task.result() if target_task in done else {}

            self.validation_results["discord_token"] = d_res.get("token", False)
            self.validation_results["discord_server"] = d_res.get("server", False)
            self.validation_results["discord_server_name"] = d_res.get("server_name")
            self.validation_results["discord_intents"] = d_res.get("intents", {})
            self.validation_results["discord_permissions"] = d_res.get("permissions", {})

            self.validation_results["target_token"] = t_res.get("token", False)
            self.validation_results["target_server"] = t_res.get("community", False)
            self.validation_results["target_server_name"] = t_res.get("community_name")
            self.validation_results["target_permissions"] = t_res.get("permissions", {})

            # Formatting
            d_name = self.validation_results.get("discord_server_name")
            d_display = f"[green]\"{d_name}\"[/green]" if d_name else "[red]NOT CONNECTED[/red]"
            
            t_name = self.validation_results.get("target_server_name")
            t_display = f"[green]\"{t_name}\"[/green]" if t_name else "[red]NOT CONNECTED[/red]"
            
            discord_valid = self.validation_results.get("discord_token") and self.validation_results.get("discord_server")
            target_valid = self.validation_results.get("target_token") and self.validation_results.get("target_server")
            self.tokens_valid = discord_valid and target_valid

            # Set up folder cache
            if self.tokens_valid and t_name:
                t_id = self.config.stoat_server_id if is_stoat else self.config.fluxer_community_id
                safe_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', t_name)
                engine.state.set_folder(str(t_id), safe_name)

            # Check permissions
            self.permissions_complete = True
            if self.tokens_valid:
                d_intents = self.validation_results.get("discord_intents", {})
                d_perms = self.validation_results.get("discord_permissions", {})
                if not all([d_intents.get("message_content"), d_perms.get("view_channel"), d_perms.get("read_message_history")]):
                    self.permissions_complete = False
                
                t_perms = self.validation_results.get("target_permissions", {})
                if not all(t_perms.values()) if t_perms else True:
                    self.permissions_complete = False
                    
            if not self.tokens_valid:
                perms_display = "[red]INVALID TOKENS[/red]"
            elif not self.permissions_complete:
                perms_display = "[yellow]MISSING REQUIRED PERMISSIONS[/yellow]"
            else:
                perms_display = "[green]VALID[/green]"

            update_ui(t_display, d_display, perms_display, self.tokens_valid)

        except Exception as e:
            update_ui(f"[red]Validation Error: {e}[/red]", "", "", False)
            self.tokens_valid = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_back":
            self.app.pop_screen()
        elif event.button.id == "btn_config":
            def check_reply(reply: dict | None) -> None:
                if reply is not None:
                    self.config.use_stoat = reply["use_stoat"]
                    self.config.fluxer_bot_token = reply["fluxer_token"]
                    self.config.fluxer_community_id = reply["fluxer_community"]
                    self.config.stoat_bot_token = reply["stoat_token"]
                    self.config.stoat_server_id = reply["stoat_server"]
                    save_config(self.config, self.app.config_path)
                    self.app.reinit_engines()
                    self.validate_config()
            self.app.push_screen(ShuttleConfigModal(self.config), check_reply)
        elif event.button.id == "btn_clone":
            self.run_clone()
        elif event.button.id == "btn_roles":
            self.run_roles()
        elif event.button.id == "btn_emojis":
            self.run_emojis()
        elif event.button.id == "btn_meta":
            self.run_meta()
        elif event.button.id == "btn_history":
            pass # Implement later

    @work(exclusive=True)
    async def run_clone(self) -> None:
        modal_prog = ProgressModal()
        self.app.push_screen(modal_prog)
        await asyncio.sleep(0.1)
        
        is_stoat = getattr(self.config, 'use_stoat', False)
        if not is_stoat:
            from src.fluxer.clone_server import sync_channel_state, migrate_channels
        else:
            from src.stoat.clone_server import sync_channel_state, migrate_channels
            
        try:
            modal_prog.set_status("Fetching structure...")
            await self.engine.start_connections()
            modal_prog.write_to_log("Syncing channel state...")
            await sync_channel_state(self.engine)
            
            categories = await self.engine.discord_reader.get_categories()
            channels = await self.engine.discord_reader.get_channels()
            
            async def update_progress(item_name: str, status: str, current: int, total: int):
                modal_prog.set_status(f"{status} Channel: {item_name} ({current}/{total})")
                
            modal_prog.write_to_log(f"[yellow]Starting Channel Cloning...[/yellow]")
            self.engine.is_running = True
            
            cloned_info = await migrate_channels(self.engine, progress_callback=update_progress, force=False)
            
            modal_prog.write_to_log("[bold green]Server Template cloned![/bold green]")
            
            if cloned_info and cloned_info.get("structure"):
                await log_audit_event(self.engine, "Server Template Cloned", "Successfully cloned target structure.")
            else:
                await log_audit_event(self.engine, "Server Template Cloned", "No new channels were cloned.")
                
        except Exception as e:
            modal_prog.write_to_log(f"[bold red]Error during channel clone: {e}[/bold red]")
        finally:
            await self.engine.close_connections()
            self.engine.is_running = False
            modal_prog.set_status("Finished.")
            modal_prog.allow_close()

    @work(exclusive=True)
    async def run_roles(self) -> None:
        modal_prog = ProgressModal()
        self.app.push_screen(modal_prog)
        await asyncio.sleep(0.1)
        
        is_stoat = getattr(self.config, 'use_stoat', False)
        roles_mod = stoat_roles if is_stoat else fluxer_roles
        
        try:
            modal_prog.set_status("Fetching roles...")
            await self.engine.start_connections()
            modal_prog.write_to_log("Syncing role state...")
            await roles_mod.sync_roles_state(self.engine)
            
            async def update_progress(item_name: str, current: int, total: int):
                modal_prog.set_status(f"Copying Role: {item_name} ({current}/{total})")
                
            modal_prog.write_to_log(f"[yellow]Starting Role Migration...[/yellow]")
            self.engine.is_running = True
            
            cloned_roles = await roles_mod.migrate_roles(self.engine, progress_callback=update_progress, force=False)
            
            modal_prog.write_to_log("[bold green]Role migration complete![/bold green]")
            
            if cloned_roles:
                await log_audit_event(self.engine, "Roles Cloned", "Successfully cloned roles.")
            else:
                await log_audit_event(self.engine, "Roles Cloned", "No new roles were cloned.")
                
        except Exception as e:
            modal_prog.write_to_log(f"[bold red]Error during role clone: {e}[/bold red]")
        finally:
            await self.engine.close_connections()
            self.engine.is_running = False
            modal_prog.set_status("Finished.")
            modal_prog.allow_close()

    @work(exclusive=True)
    async def run_emojis(self) -> None:
        modal_prog = ProgressModal()
        self.app.push_screen(modal_prog)
        await asyncio.sleep(0.1)
        
        is_stoat = getattr(self.config, 'use_stoat', False)
        emo_mod = stoat_emoji_stickers if is_stoat else fluxer_emoji_stickers
        
        try:
            modal_prog.set_status("Fetching emojis...")
            await self.engine.start_connections()
            modal_prog.write_to_log("Syncing emoji & sticker state...")
            await emo_mod.sync_emojis_state(self.engine)
            await emo_mod.sync_stickers_state(self.engine)
            
            async def update_progress(item_name: str, item_type: str, current: int, total: int):
                modal_prog.set_status(f"Copying {item_type}: {item_name} ({current}/{total})")
                
            modal_prog.write_to_log(f"[yellow]Starting Asset Migration...[/yellow]")
            self.engine.is_running = True
            
            # Using force=False
            res_e, res_s = await emo_mod.migrate_emojis(self.engine, progress_callback=update_progress, force=False)
            
            modal_prog.write_to_log("[bold green]Asset migration complete![/bold green]")
            await log_audit_event(self.engine, "Assets Cloned", f"E: {res_e} S: {res_s}")
            
        except Exception as e:
            modal_prog.write_to_log(f"[bold red]Error during asset migrate: {e}[/bold red]")
        finally:
            await self.engine.close_connections()
            self.engine.is_running = False
            modal_prog.set_status("Finished.")
            modal_prog.allow_close()

    @work(exclusive=True)
    async def run_meta(self) -> None:
        modal_prog = ProgressModal()
        self.app.push_screen(modal_prog)
        await asyncio.sleep(0.1)
        
        is_stoat = getattr(self.config, 'use_stoat', False)
        meta_mod = stoat_metadata if is_stoat else fluxer_metadata
        
        try:
            modal_prog.set_status("Connecting to platforms...")
            await self.engine.start_connections()
            
            def cb(item, status):
                modal_prog.write_to_log(f"{item}: {status}")
                
            modal_prog.write_to_log(f"[yellow]Starting Meta Migration...[/yellow]")
            self.engine.is_running = True
            
            await meta_mod.migrate_server_metadata(self.engine, progress_callback=cb)
            
            modal_prog.write_to_log("[bold green]Meta migration complete![/bold green]")
            
        except Exception as e:
            modal_prog.write_to_log(f"[bold red]Error during meta migrate: {e}[/bold red]")
        finally:
            await self.engine.close_connections()
            self.engine.is_running = False
            modal_prog.set_status("Finished.")
            modal_prog.allow_close()


# -------------------------------------------------------------------------
# Main Home Screen
# -------------------------------------------------------------------------

class HomeScreen(Screen):
    BINDINGS = [
        ("q", "app.exit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="home_container"):
            yield Label("Unified Discord Toolkit", id="home_title")
            with Grid(id="home_grid"):
                yield Button("Reaper Mode\n(Export from Discord)", id="btn_reaper", variant="success")
                yield Button("Shuttle Mode\n(Restore to Fluxer/Stoat)", id="btn_shuttle", variant="primary")
            yield Rule()
            yield Button("Exit", id="btn_exit", variant="error")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_reaper":
            self.app.push_screen(ReaperScreen())
        elif event.button.id == "btn_shuttle":
            self.app.push_screen(ShuttleScreen())
        elif event.button.id == "btn_exit":
            self.app.exit()

# -------------------------------------------------------------------------
# App Definition
# -------------------------------------------------------------------------

class UnifierApp(App):
    CSS = """
    Screen { align: center middle; }
    
    #title_label, #shuttle_title_label, #home_title {
        text-style: bold;
        color: green;
        margin-bottom: 1;
        content-align: center middle;
        width: 100%;
    }
    
    #home_title { color: magenta; margin-bottom: 2; text-style: bold italic; }
    
    #main_container, #home_container {
        width: 80%;
        height: 80%;
        border: solid green;
        padding: 1 2;
    }
    #home_container { border: double magenta; }
    
    #home_grid {
        grid-size: 2 1;
        grid-rows: 1fr;
        grid-columns: 1fr;
        grid-gutter: 2;
        padding: 1;
        height: 12;
    }
    #home_grid Button {
        height: 100%;
        width: 100%;
    }
    
    #info_container {
        height: auto;
        margin-bottom: 2;
        border: tall cyan;
        padding: 1;
    }
    
    #actions_container {
        height: auto;
        layout: vertical;
        align: center top;
        margin-top: 1;
    }
    
    Button {
        width: 100%;
        margin-bottom: 1;
    }
    
    #config_dialog {
        width: 70%;
        height: 80%;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }
    #config_title { text-style: bold; margin-bottom: 1; }
    #config_buttons { height: auto; margin-top: 1; }
    #config_buttons Button { width: 1fr; margin: 0 1; }
    
    #channel_dialog {
        width: 80%;
        height: 80%;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }
    #chan_title { text-style: bold; margin-bottom: 1; }
    
    .category_header {
        margin-top: 1;
        background: $primary 10%;
        text-style: bold;
        padding-left: 1;
    }

    #channel_list_scroll {
        height: 1fr;
        border: solid $primary;
        margin-bottom: 1;
        padding: 0 1;
    }

    #select_all_buttons { height: auto; margin-bottom: 1; }
    #select_all_buttons Button { width: auto; margin-right: 1; }

    #confirm_buttons { height: auto; margin-top: 1; }
    #confirm_buttons Button { width: auto; margin: 0 1; }
    
    #progress_dialog {
        width: 80%;
        height: 80%;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }
    #progress_status { text-style: bold; margin-bottom: 1; }
    #progress_bar { margin-bottom: 1; }
    #progress_log { height: 1fr; margin-bottom: 1; border: solid $primary; }
    .label_warning { color: yellow; margin-right: 1; content-align: center middle;}
    
    #platform_radios { height: auto; margin-bottom: 1; }
    #platform_radios RadioButton { width: auto; margin-right: 2; }
    """
    
    def __init__(self, config_path="config.yaml"):
        super().__init__()
        self.config_path = config_path
        self.config = load_config(self.config_path)
        self.reinit_engines()

    def reinit_engines(self):
        self.reaper_engine = MigrationContext(self.config, target_platform="fluxer")
        self.exporter = DiscordExporter(self.reaper_engine.discord_reader)
        
        # Determine active shuttle engine
        is_stoat = getattr(self.config, 'use_stoat', False)
        self.fluxer_engine = MigrationContext(self.config, "fluxer")
        self.stoat_engine = MigrationContext(self.config, "stoat")

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())

async def run_unifier_tui(config_path="config.yaml"):
    app = UnifierApp(config_path)
    await app.run_async()
