import sys
import asyncio
import discord
import logging
import json
import re
from pathlib import Path
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, Button, Static, Label, Input, Checkbox, RadioButton, ProgressBar, RichLog, Rule
from textual.screen import Screen, ModalScreen
from textual import work
from textual.worker import Worker, WorkerState

from src.core.configuration import load_config, save_config
from src.core.base import MigrationContext
from src.disco_reaper.exporter import DiscordExporter

class ConfigModal(ModalScreen[dict]):
    """Modal for configuring token and server ID."""
    
    def __init__(self, current_token: str, current_server: str):
        super().__init__()
        self.current_token = current_token
        self.current_server = current_server

    def compose(self) -> ComposeResult:
        with Vertical(id="config_dialog"):
            yield Label("Configuration", id="config_title")
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
        # Group channels by category
        self.channels_by_category = {}
        for c in channels:
            cat_id = getattr(c, 'category_id', None)
            if cat_id not in self.channels_by_category:
                self.channels_by_category[cat_id] = []
            self.channels_by_category[cat_id].append(c)
            
        self.categories = categories # id -> name
        self.backed_up_ids = backed_up_ids
        self.any_found = any_found

    def compose(self) -> ComposeResult:
        with Vertical(id="channel_dialog"):
            yield Label(f"Select Channels to Backup", id="chan_title")
            
            with VerticalScroll(id="channel_list_scroll"):
                # Group by category
                cat_ids = sorted([k for k in self.channels_by_category.keys() if k is not None], 
                               key=lambda k: self.categories.get(k, ""))
                
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
    """Modal to show progress of backup."""
    
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
        self.query_one("#progress_bar", ProgressBar).update(total=100, progress=100)

class MainScreen(Screen):
    """Main screen of the application."""
    
    BINDINGS = [
        ("q", "app.exit", "Quit"),
        ("c", "config", "Config"),
    ]

    def __init__(self):
        super().__init__()
        self.config_path = "config.yaml"
        self.config = load_config(self.config_path)
        self.engine = MigrationContext(self.config, target_platform="fluxer")
        self.exporter = DiscordExporter(self.engine.discord_reader)
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
                yield Button("Exit", id="btn_exit", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        self.validate_config()
        
    @work(exclusive=True, thread=True)
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
            self.app.call_from_thread(update_ui, "[red]NOT CONNECTED[/red]", "[red]UNKNOWN[/red]", "", False)
            self.tokens_valid = False
            return

        self.app.call_from_thread(update_ui, "[yellow]Validating...[/yellow]", "[yellow]Validating...[/yellow]", "", False)
        
        try:
            res = await self.engine.discord_reader.validate()
        except Exception as e:
            res = {}
            self.app.call_from_thread(update_ui, f"[red]Validation Error: {e}[/red]", "", "", False)
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
        
        self.app.call_from_thread(update_ui, server_display, bot_display, backup_text, self.tokens_valid)

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
                save_config(self.config, self.config_path)
                self.engine = MigrationContext(self.config, target_platform="fluxer")
                self.exporter = DiscordExporter(self.engine.discord_reader)
                self.validate_config()
                
        self.app.push_screen(
            ConfigModal(self.config.discord_bot_token, self.config.discord_server_id), 
            check_reply
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_exit":
            self.app.exit()
        elif event.button.id == "btn_config":
            self.open_config()
        elif event.button.id == "btn_backup_profile":
            self.run_backup_profile()
        elif event.button.id == "btn_backup_msgs":
            self.run_backup_messages()
        elif event.button.id == "btn_backup_sync":
            self.run_backup_sync()
            
    @work(exclusive=True, thread=True)
    async def run_backup_profile(self) -> None:
        modal = ProgressModal()
        self.app.call_from_thread(self.app.push_screen, modal)
        await asyncio.sleep(0.1) # Wait for mount
        
        try:
            self.app.call_from_thread(modal.set_status, "Starting readers...")
            await self.engine.discord_reader.start()
            meta = await self.exporter.setup()
            
            self.app.call_from_thread(modal.write_to_log, "[yellow]Backing up server profile & skeleton...[/yellow]")
            await self.exporter.export_metadata()
            await self.exporter.download_server_assets()
            
            self.app.call_from_thread(modal.write_to_log, "Exporting structure...")
            _, cat_count, chan_count = await self.exporter.export_channels_structure()
            
            self.app.call_from_thread(modal.write_to_log, "Exporting assets...")
            e_count, s_count = await self.exporter.export_assets()
            
            self.app.call_from_thread(modal.write_to_log, f"[bold green]Server Profile backed up to: {self.exporter.export_path}[/bold green]")
            self.app.call_from_thread(modal.write_to_log, f"- {cat_count} categories & {chan_count} channels")
            self.app.call_from_thread(modal.write_to_log, f"- {e_count} emojis, {s_count} stickers.")
            
        except discord.Forbidden as e:
            self.app.call_from_thread(modal.write_to_log, f"[bold red]Backup failed: {e}[/bold red]")
        except Exception as e:
            self.app.call_from_thread(modal.write_to_log, f"[bold red]Error: {e}[/bold red]")
        finally:
            await self.engine.close_connections()
            self.app.call_from_thread(modal.set_status, "Finished.")
            self.app.call_from_thread(modal.allow_close)

    @work(exclusive=True, thread=True)
    async def run_backup_messages(self) -> None:
        modal_prog = ProgressModal()
        self.app.call_from_thread(self.app.push_screen, modal_prog)
        await asyncio.sleep(0.1)
        
        try:
            self.app.call_from_thread(modal_prog.set_status, "Fetching channels...")
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
                self.app.call_from_thread(modal_prog.write_to_log, "[yellow]No text/news channels found to backup.[/yellow]")
                self.app.call_from_thread(modal_prog.allow_close)
                return
                
            any_found = False
            backed_up_ids = set()
            for chan in eligible_channels:
                if (self.exporter.export_path / "message_backup" / f"{chan.id}.json").exists():
                    any_found = True
                    backed_up_ids.add(chan.id)

            # Need to ask for channels. We temporarily pop the progress modal, push channel select, wait for result, push progress again.
            self.app.call_from_thread(self.app.pop_screen)
            
            # Setup a future to await modal response
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            
            def check_channels(reply: dict | None) -> None:
                if not future.done():
                    future.set_result(reply)
                    
            self.app.call_from_thread(
                self.app.push_screen,
                ChannelSelectModal(eligible_channels, cat_map, backed_up_ids, any_found),
                check_channels
            )
            
            reply = await future
            if not reply:
                # Cancelled
                return
                
            selected_ids = reply["channels"]
            force_overwrite = reply["force"]
            
            selected_channels = [c for c in eligible_channels if c.id in selected_ids]
            
            self.app.call_from_thread(self.app.push_screen, modal_prog)
            await asyncio.sleep(0.1)
            
            total_chans = len(selected_channels)
            self.app.call_from_thread(modal_prog.set_status, "Backing up messages...")
            self.app.call_from_thread(modal_prog.write_to_log, f"[yellow]Starting backup for {total_chans} channels...[/yellow]")
            
            for chan in selected_channels:
                backup_exists = (self.exporter.export_path / "message_backup" / f"{chan.id}.json").exists()
                is_sync = backup_exists and not force_overwrite
                
                label = "Syncing Backup" if is_sync else "Backing up"
                self.app.call_from_thread(modal_prog.write_to_log, f"[cyan]{label}: {chan.name}[/cyan]")
                
                async def update_msg_count(name, count):
                    self.app.call_from_thread(modal_prog.set_status, f"{name}: {count} messages")
                
                await self.exporter.export_channel_messages(chan.id, progress_callback=update_msg_count, force=force_overwrite)
                await self.exporter.export_threads(chan.id, progress_callback=update_msg_count, force=force_overwrite)
                
                self.app.call_from_thread(modal_prog.write_to_log, f"[green]Completed: {chan.name}[/green]")
                
            await self.exporter.export_metadata()
            self.app.call_from_thread(modal_prog.write_to_log, "[bold green]Message backup complete![/bold green]")
            
        except Exception as e:
            self.app.call_from_thread(modal_prog.write_to_log, f"[bold red]Message backup failed: {e}[/bold red]")
        finally:
            await self.engine.close_connections()
            self.app.call_from_thread(modal_prog.set_status, "Finished.")
            self.app.call_from_thread(modal_prog.allow_close)

    @work(exclusive=True, thread=True)
    async def run_backup_sync(self) -> None:
        modal_prog = ProgressModal()
        self.app.call_from_thread(self.app.push_screen, modal_prog)
        await asyncio.sleep(0.1)
        
        try:
            self.app.call_from_thread(modal_prog.set_status, "Starting sync...")
            await self.engine.discord_reader.start()
            await self.exporter.setup()
            
            self.app.call_from_thread(modal_prog.write_to_log, "Updating structure...")
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
                self.app.call_from_thread(modal_prog.write_to_log, "[yellow]No existing backups found to sync.[/yellow]")
            else:
                self.app.call_from_thread(modal_prog.write_to_log, f"[yellow]Syncing {len(selected_channels)} channels...[/yellow]")
                for chan in selected_channels:
                    self.app.call_from_thread(modal_prog.write_to_log, f"[cyan]Syncing: {chan.name}[/cyan]")
                    
                    async def update_msg_count(name, count):
                        self.app.call_from_thread(modal_prog.set_status, f"{name}: {count} messages")
                        
                    await self.exporter.export_channel_messages(chan.id, progress_callback=update_msg_count, force=False)
                    await self.exporter.export_threads(chan.id, progress_callback=update_msg_count, force=False)
                    self.app.call_from_thread(modal_prog.write_to_log, f"[green]Synced: {chan.name}[/green]")
                    
            await self.exporter.export_metadata()
            self.app.call_from_thread(modal_prog.write_to_log, "[bold green]Sync operation complete![/bold green]")
            
        except Exception as e:
            self.app.call_from_thread(modal_prog.write_to_log, f"[bold red]Sync failed: {e}[/bold red]")
        finally:
            await self.engine.close_connections()
            self.app.call_from_thread(modal_prog.set_status, "Finished.")
            self.app.call_from_thread(modal_prog.allow_close)

class ReaperUI(App):
    CSS = """
    Screen {
        align: center middle;
    }
    
    #title_label {
        text-style: bold;
        color: green;
        margin-bottom: 1;
        content-align: center middle;
        width: 100%;
    }
    
    #main_container {
        width: 80%;
        height: 80%;
        border: solid green;
        padding: 1 2;
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
        width: 60%;
        height: 60%;
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

    RadioButton:focus {
        background: transparent;
        border: none;
        color: $text;
    }
    RadioButton > .radio-button--label {
        padding: 0 1;
    }
    RadioButton:focus > .radio-button--label {
        background: transparent;
        text-style: none;
    }
    
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
    """
    
    def on_mount(self) -> None:
        self.push_screen(MainScreen())

async def run_disco_reaper_tui(config_path="config.yaml"):
    app = ReaperUI()
    await app.run_async()
