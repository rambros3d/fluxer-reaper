"""
BackupPane – self-contained backup-operations widget.
Embedded inside ModeScreen's "Backup" tab.
"""

import asyncio
import json
import re
import logging
import traceback
from pathlib import Path
from datetime import datetime
from typing import Any, Optional, Union, List, Dict, Callable

logger = logging.getLogger(__name__)

from textual.app import ComposeResult
from textual.containers import Container, Vertical, VerticalScroll, Horizontal
from textual.widgets import Button, Label, Rule
from textual import work

from src.core.configuration import load_config
from src.core.base import MigrationContext
from src.core.exporter import DiscordExporter
from src.ui.modals import ProgressScreen, ChannelSelectScreen, MessageIDInputModal


class BackupPane(Container):
    """Backup operations pane — profile, messages, sync."""

    DEFAULT_CSS = """
    BackupPane { height: auto; width: 100%; }
    BackupPane #bp_info {
        height: auto; border: tall cyan; padding: 1; margin-bottom: 1; layout: vertical;
    }
    #bp_info_split { height: auto; layout: horizontal; width: 100%; margin-bottom: 1; }
    .info_pane { width: 1fr; height: auto; }
    .info_pane Label { width: 100%; }
    .pane_header { text-style: bold; color: $accent; margin-bottom: 1; }
    .pane_status { text-style: bold; margin-top: 1; }
    #bp_info_split Rule { height: 100%; margin: 0 2; color: $accent; }
    #bp_lbl_backup { display: none; }
    
    BackupPane #bp_actions { height: auto; }
    BackupPane #bp_actions Button { width: 100%; margin-bottom: 1; }
    """

    def __init__(self, cfg_name: str, cfg_path: Path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cfg_name = cfg_name
        self.config_path = cfg_path
        self.config = load_config(cfg_path)
        self.engine = MigrationContext(self.config, target_platform=self.config.target_platform or "fluxer", base_dir=f"ReaperFiles-{self.cfg_name}")
        self.exporter = DiscordExporter(self.engine.discord_reader, base_dir=f"ReaperFiles-{self.cfg_name}")

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            with Vertical(id="bp_info"):
                with Horizontal(id="bp_info_split"):
                    with Vertical(classes="info_pane"):
                        yield Label("Discord", classes="pane_header")
                        yield Label("Server: -", id="bp_lbl_server")
                        yield Label("Source: -", id="bp_lbl_bot")
                        yield Label("Status: -", id="bp_lbl_d_status", classes="pane_status")
                    
                    yield Rule(orientation="vertical", id="bp_vrule")

                    with Vertical(classes="info_pane", id="bp_target_pane"):
                        yield Label("Target", classes="pane_header")
                        yield Label("Community: -", id="bp_lbl_t_comm")
                        yield Label("Bot: -", id="bp_lbl_t_bot")
                        yield Label("Status: -", id="bp_lbl_t_status", classes="pane_status")
                
                yield Label("", id="bp_lbl_backup")
            with Vertical(id="bp_actions"):
                yield Button("Backup Server Profile", id="bp_backup_profile", disabled=True, tooltip="Backup Discord server roles, emojis, and channel structure")
                yield Button("Backup Channel Messages", id="bp_backup_msgs", disabled=True, variant="primary", tooltip="Select and backup message history from text channels")
                yield Button("Update Existing Backup", id="bp_backup_sync", disabled=True, variant="success", tooltip="Scan for new messages\n& Update existing backup")

    def on_mount(self) -> None:
        self._validate()

    def reload_config(self) -> None:
        self.config = load_config(self.config_path)
        self.engine = MigrationContext(self.config, target_platform=self.config.target_platform or "fluxer", base_dir=f"ReaperFiles-{self.cfg_name}")
        self.exporter = DiscordExporter(self.engine.discord_reader, base_dir=f"ReaperFiles-{self.cfg_name}")
        self._validate()

    # ── validation ────────────────────────────────────────────────────────

    @work(exclusive=True)
    async def _validate(self) -> None:
        fillers = ["DISCORD_BOT_TOKEN", "000000000000000000", "DISCORD_SERVER_ID", "", None]
        d_token = self.config.discord_bot_token
        if d_token in fillers or self.config.discord_server_id in fillers:
            self._update_ui("[red]NOT CONFIGURED[/red]", "", "", False)
            return
        try:
            res = await self.engine.discord_reader.validate()
            valid = res.get("token", False) and res.get("server", False)
            server_name = res.get("server_name", "Unknown")
            bot_name = res.get("bot_name", "Unknown")
            s_text = f'[green]"{server_name}"[/green]' if valid else "[red]INVALID[/red]"
            b_text = f"[green]{bot_name}[/green]" if valid else "[red]INVALID[/red]"

            backup_text = ""
            info = self._get_backup_info()
            if info:
                backup_text = f"Last backup: [cyan]{info}[/cyan]"

            self._update_ui(s_text, b_text, backup_text, valid)
        except Exception as e:
            self._update_ui(f"[red]Error: {e}[/red]", "", "", False)

    def _get_backup_info(self) -> str | None:
        profile_file = Path(f"ReaperFiles-{self.cfg_name}") / "server_profile" / "profile.json"
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

    def _update_ui(self, server_text, bot_text, backup_text, enabled):
        try:
            self.query_one("#bp_lbl_server", Label).update(f"Server: {server_text}")
            self.query_one("#bp_lbl_bot", Label).update(f"Source: {bot_text}")
            
            # Status for Discord side
            d_status = "[green][VALID][/green]" if enabled else "[red][INVALID][/red]"
            self.query_one("#bp_lbl_d_status", Label).update(f"Status: {d_status}")

            # Legacy label for safety if called elsewhere
            self.query_one("#bp_lbl_backup", Label).update(f"Status: {backup_text}")
            
            # Hide target side in backup mode completely
            self.query_one("#bp_vrule").display = False
            self.query_one("#bp_target_pane").display = False
            
            for bid in ("#bp_backup_profile", "#bp_backup_msgs", "#bp_backup_sync"):
                self.query_one(bid, Button).disabled = not enabled
        except Exception:
            pass

    # ── button routing ────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "bp_backup_profile":
            self.run_backup_profile()
        elif bid == "bp_backup_msgs":
            self.run_backup_messages()
        elif bid == "bp_backup_sync":
            self.run_backup_sync()

    # ── workers ───────────────────────────────────────────────────────────

    @work(exclusive=True)
    async def run_backup_profile(self) -> None:
        modal = ProgressScreen(log_level=self.config.log_level)
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)
        modal.phase_progress()

        try:
            modal.set_status("Starting readers...")
            await self.engine.discord_reader.start()
            await self.exporter.setup()

            # Gather and print summary
            server = getattr(self.engine.discord_reader, 'guild', None)
            if server:
                modal.write(f"[bold cyan]Server Profile to Backup:[/bold cyan]")
                modal.write(f"  Name: [green]{server.name}[/green]")
                modal.write(f"  Icon: [green]{'Present' if server.icon else 'None'}[/green]")
                modal.write(f"  Roles: [green]{len(getattr(server, 'roles', []))}[/green]")
                modal.write(f"  Emojis: [green]{len(getattr(server, 'emojis', []))}[/green]")
                modal.write(f"  Channels: [green]{len(getattr(server, 'channels', []))}[/green]")
                modal.write("")

            modal.cancel_callback = lambda: setattr(self.engine, "is_running", False)
            modal.phase_progress()
            modal.set_status("Exporting Server Structure...")
            modal.write("[yellow]Backing up server profile & skeleton...[/yellow]")
            await self.exporter.export_metadata()
            await self.exporter.download_server_assets()

            modal.write("Exporting structure...")
            _, cat_count, chan_count = await self.exporter.export_channels_structure()

            modal.write("Exporting roles...")
            roles = await self.exporter.export_roles()

            modal.write("Exporting assets...")
            e_count, s_count = await self.exporter.export_assets()

            modal.write(f"[bold green]Server Profile backed up to: {self.exporter.export_path}[/bold green]")
            modal.write(f"- {len(roles)} roles, {e_count} emojis, {s_count} stickers.")
            modal.phase_report("Profile Backup", show_back=False)

        except self.engine.discord_reader.Forbidden as e:
            modal.write(f"[bold red]Backup failed: {e}[/bold red]")
            modal.phase_report("Profile Backup", "error")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
            modal.phase_report("Profile Backup", "error")
        finally:
            await self.engine.close_connections()

    @work(exclusive=True)
    async def run_backup_messages(self) -> None:
        modal_prog = ProgressScreen(log_level=self.config.log_level)
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
                if c.type in [
                    self.engine.discord_reader.CHANNEL_TYPE_TEXT,
                    self.engine.discord_reader.CHANNEL_TYPE_NEWS,
                    self.engine.discord_reader.CHANNEL_TYPE_FORUM
                ]
            ]

            if not eligible_channels:
                modal_prog.write("[yellow]No text/news channels found to backup.[/yellow]")
                modal_prog.allow_close()
                return

            any_found = False
            backed_up_ids = set()
            for chan in eligible_channels:
                if (self.exporter.export_path / "message_backup" / str(chan.id) / "messages.json").exists():
                    any_found = True
                    backed_up_ids.add(chan.id)

            self.app.pop_screen()

            while True:
                loop = asyncio.get_running_loop()
                future = loop.create_future()

                def check_channels(reply: dict | None) -> None:
                    if not future.done():
                        future.set_result(reply)

                self.app.push_screen(
                    ChannelSelectScreen(eligible_channels, cat_map, backed_up_ids, any_found),
                    check_channels,
                )

                reply = await future
                if not reply:
                    return

                selected_ids = reply["channels"]
                force_overwrite = reply["force"]
                selected_channels = [c for c in eligible_channels if c.id in selected_ids]

                # Phase 2: Confirmation
                modal_prog = ProgressScreen(log_level=self.config.log_level) # Re-instantiate to avoid Textual re-push UI freeze
                self.app.push_screen(modal_prog)
                await asyncio.sleep(0.1)
                
                new_channels = [c for c in selected_channels if c.id not in backed_up_ids]
                existing_channels = [c for c in selected_channels if c.id in backed_up_ids]

                server = getattr(self.engine.discord_reader, 'guild', None)
                if server:
                    modal_prog.write(f"[bold cyan]Server Profile:[/bold cyan]")
                    modal_prog.write(f"  Name: [green]{server.name}[/green]")
                    modal_prog.write(f"  Icon: [green]{'Present' if server.icon else 'None'}[/green]")
                    modal_prog.write("")

                modal_prog.set_status(f"Confirm to proceed with Backup of [bold]{len(selected_channels)}[/bold] channels")
                modal_prog.show_info(f"[cyan]Backup Channels[/cyan]", f"{len(new_channels)} new, {len(existing_channels)} existing")

                # Show categorized channel lists in the bottom log
                if new_channels:
                    modal_prog.write("[bold green]New Backups to be created:[/bold green]")
                    for idx, c in enumerate(new_channels):
                        modal_prog.write(f"  {idx+1}. #{c.name}")

                if existing_channels:
                    action = "Overwritten" if force_overwrite else "Updated"
                    modal_prog.write(f"[bold yellow]\nExisting backups to be {action}:[/bold yellow]")
                    for idx, c in enumerate(existing_channels):
                        modal_prog.write(f"  {idx+1}. #{c.name}")

                choice = await modal_prog.phase_wait_confirm(btn_start_label="Start Channel Backup", show_id=False)
                if choice == "btn_back":
                    modal_prog.dismiss()
                    continue
                elif choice == "btn_start_id":
                    loop = asyncio.get_running_loop()
                    future = loop.create_future()
                    def id_callback(res: int | None) -> None:
                        if not future.done():
                            future.set_result(res)
                            
                    id_modal = MessageIDInputModal(self.engine.discord_reader, selected_channels[0].id)
                    self.app.push_screen(id_modal, id_callback)
                    verified_id = await future
                    
                    if verified_id is None:
                        # User cancelled the ID input
                        continue
                        
                    after_id = verified_id
                elif choice == "btn_main_menu":
                    modal_prog.dismiss()
                    self.app.switch_screen("config_selection")
                    return
                
                # If we are here, proceeding either via Start First or Start from ID (after_id)
                if choice == "btn_start_first":
                    after_id = None
                break

            modal_prog.phase_progress()
            modal_prog.show_stats()
            
            # Reset running flag and set cancel callback
            self.exporter.is_running = True
            modal_prog.cancel_callback = lambda: setattr(self.exporter, "is_running", False)

            total_chans = len(selected_channels)
            modal_prog.set_status("Backing up messages...")
            modal_prog.write(f"[yellow]Starting backup for {total_chans} channels...[/yellow]")

            accumulated_msgs = 0

            for i, chan in enumerate(selected_channels):
                if not self.exporter.is_running:
                    modal_prog.write("[bold red]Backup cancelled by user.[/bold red]")
                    break
                await asyncio.sleep(0.01) # Yield to UI thread to keep it responsive
                
                backup_exists = (self.exporter.export_path / "message_backup" / str(chan.id) / "messages.json").exists()
                is_sync = backup_exists and not force_overwrite

                label = "Syncing Backup" if is_sync else "Backing up"
                modal_prog.set_item_status(f"[cyan]Processing ({i+1}/{total_chans}): #{chan.name}[/cyan]")
                modal_prog.set_progress(i, total_chans)
                modal_prog.write(f"[cyan]{label}: {chan.name}[/cyan]")
                logger.info(f"{label} for channel: #{chan.name} ({chan.id})")

                async def update_msg_count(name, count, author_name=None, message_preview=None):
                    modal_prog.update_stats(messages=str(count))
                    if author_name and message_preview:
                        modal_prog.write(f"[bold]{author_name}:[/bold] {message_preview}")

                accumulated_msgs = await self.exporter.export_channel_messages(
                    chan.id, progress_callback=update_msg_count, force=force_overwrite,
                    accumulated_count=accumulated_msgs, after_id=after_id
                )
                accumulated_msgs = await self.exporter.export_threads(
                    chan.id, progress_callback=update_msg_count, force=force_overwrite,
                    accumulated_count=accumulated_msgs
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

    @work(exclusive=True)
    async def run_backup_sync(self) -> None:
        modal_prog = ProgressScreen(log_level=self.config.log_level)
        self.app.push_screen(modal_prog)
        await asyncio.sleep(0.1)
        modal_prog.phase_progress()

        try:
            modal_prog.set_status("Starting sync...")
            await self.engine.discord_reader.start()
            await self.exporter.setup()

            # Gather and print summary
            server = getattr(self.engine.discord_reader, 'guild', None)
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
                    self.app.switch_screen("config_selection")
                return

            modal_prog.cancel_callback = lambda: setattr(self.engine, "is_running", False)
            modal_prog.phase_progress()
            modal_prog.set_status("Updating structure...")
            modal_prog.write("Updating structure...")
            await self.exporter.export_metadata()
            await self.exporter.download_server_assets()
            await self.exporter.export_channels_structure()
            await self.exporter.export_assets()

            all_channels = await self.engine.discord_reader.get_channels()
            eligible_channels = [
                c for c in all_channels
                if c.type in [
                    self.engine.discord_reader.CHANNEL_TYPE_TEXT,
                    self.engine.discord_reader.CHANNEL_TYPE_NEWS,
                    self.engine.discord_reader.CHANNEL_TYPE_FORUM
                ]
            ]

            selected_channels = [
                c for c in eligible_channels
                if (self.exporter.export_path / "message_backup" / str(c.id) / "messages.json").exists()
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
                
                for i, chan in enumerate(selected_channels):
                    if not self.exporter.is_running:
                        modal_prog.write("[bold red]Sync cancelled by user.[/bold red]")
                        break
                    await asyncio.sleep(0.01) # Yield to UI thread
                    
                    modal_prog.set_item_status(f"[cyan]Syncing ({i+1}/{total_chans}): #{chan.name}[/cyan]")
                    modal_prog.set_progress(i, total_chans)
                    modal_prog.write(f"[cyan]Syncing: {chan.name}[/cyan]")
                    logger.info(f"Syncing backup for channel: #{chan.name} ({chan.id})")

                    async def update_msg_count(name, count, author_name=None, message_preview=None):
                        modal_prog.update_stats(messages=str(count))
                        if author_name and message_preview:
                            modal_prog.write(f"[bold]{author_name}:[/bold] {message_preview}")

                    accumulated_msgs = await self.exporter.export_channel_messages(
                        chan.id, progress_callback=update_msg_count, force=False,
                        accumulated_count=accumulated_msgs
                    )
                    accumulated_msgs = await self.exporter.export_threads(
                        chan.id, progress_callback=update_msg_count, force=False,
                        accumulated_count=accumulated_msgs
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
