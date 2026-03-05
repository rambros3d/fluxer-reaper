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

logger = logging.getLogger(__name__)

from textual.app import ComposeResult
from textual.containers import Container, Vertical, VerticalScroll
from textual.widgets import Button, Label, Rule
from textual import work

from src.core.configuration import load_config
from src.core.base import MigrationContext
from src.disco_reaper.exporter import DiscordExporter
from src.ui.modals import ProgressScreen, ChannelSelectScreen


class BackupPane(Container):
    """Backup operations pane — profile, messages, sync."""

    DEFAULT_CSS = """
    BackupPane { height: auto; width: 100%; }
    BackupPane #bp_info {
        height: auto; border: tall cyan; padding: 1; margin-bottom: 1;
    }
    BackupPane #bp_actions { height: auto; }
    BackupPane #bp_actions Button { width: 100%; margin-bottom: 1; }
    """

    def __init__(self, cfg_name: str, cfg_path: Path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cfg_name = cfg_name
        self.config_path = cfg_path
        self.config = load_config(cfg_path)
        self.engine = MigrationContext(self.config, target_platform=self.config.target_platform or "fluxer")
        self.exporter = DiscordExporter(self.engine.discord_reader, base_dir=f"Reaper-{self.cfg_name}")

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            with Vertical(id="bp_info"):
                yield Label("Loading...", id="bp_lbl_server")
                yield Label("", id="bp_lbl_bot")
                yield Label("", id="bp_lbl_backup")
            with Vertical(id="bp_actions"):
                yield Button("Backup Server Profile", id="bp_backup_profile", disabled=True)
                yield Button("Backup Channel Messages", id="bp_backup_msgs", disabled=True, variant="primary")
                yield Button("Update Existing Backup", id="bp_backup_sync", disabled=True, variant="success")

    def on_mount(self) -> None:
        self._validate()

    def reload_config(self) -> None:
        self.config = load_config(self.config_path)
        self.engine = MigrationContext(self.config, target_platform=self.config.target_platform or "fluxer")
        self.exporter = DiscordExporter(self.engine.discord_reader, base_dir=f"Reaper-{self.cfg_name}")
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
        profile_file = Path(f"Reaper-{self.cfg_name}") / "server_profile" / "profile.json"
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
        self.query_one("#bp_lbl_server", Label).update(f"Source Server: {server_text}")
        self.query_one("#bp_lbl_bot", Label).update(f"Bot: {bot_text}")
        self.query_one("#bp_lbl_backup", Label).update(backup_text)
        for bid in ("#bp_backup_profile", "#bp_backup_msgs", "#bp_backup_sync"):
            self.query_one(bid, Button).disabled = not enabled

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
        modal = ProgressScreen(log_level=self.config.migration.log_level)
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)
        modal.phase_progress()

        try:
            modal.set_status("Starting readers...")
            await self.engine.discord_reader.start()
            await self.exporter.setup()

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
            modal.phase_report("Profile Backup")

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
        modal_prog = ProgressScreen(log_level=self.config.migration.log_level)
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
                modal_prog = ProgressScreen(log_level=self.config.migration.log_level) # Re-instantiate to avoid Textual re-push UI freeze
                self.app.push_screen(modal_prog)
                await asyncio.sleep(0.1)
                
                msg = "Backup Channels" if not force_overwrite else "Overwriting existing backups"
                target_preview = ", ".join([c.name for c in selected_channels[:3]])
                if len(selected_channels) > 3:
                    target_preview += "..."

                modal_prog.set_status(f"Confirm to proceed with Backup of [bold]{len(selected_channels)}[/bold] channels")
                modal_prog.show_info(f"[cyan]{msg}[/cyan]", f"Targets: {target_preview}")

                # Show full target list in the bottom log
                modal_prog.write("[bold]Target Channels:[/bold]")
                for idx, c in enumerate(selected_channels):
                    modal_prog.write(f"  {idx+1}. #{c.name}")

                choice = await modal_prog.phase_wait_confirm(btn_start_label="Start Channel Backup", show_id=False)
                if choice == "btn_back":
                    modal_prog.dismiss()
                    continue
                elif choice == "btn_main_menu":
                    modal_prog.dismiss()
                    self.app.switch_screen("config_selection")
                    return
                
                # Proceed to progress
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
                    accumulated_count=accumulated_msgs
                )
                accumulated_msgs = await self.exporter.export_threads(
                    chan.id, progress_callback=update_msg_count, force=force_overwrite,
                    accumulated_count=accumulated_msgs
                )

                modal_prog.write(f"[green]Completed: {chan.name}[/green]")

            if not self.exporter.is_running:
                modal_prog.set_item_status("[bold red]Backup Cancelled.[/bold red]")
                modal_prog.phase_report("Message Backup", "stopped")
                return

            modal_prog.set_progress(total_chans, total_chans)
            modal_prog.set_item_status("[bold green]Backup completed successfully![/bold green]")

            await self.exporter.export_metadata()
            modal_prog.write("[bold green]Message backup complete![/bold green]")
            logger.info("Message backup operation completed successfully.")
            modal_prog.phase_report("Message Backup")

        except Exception as e:
            logger.error(f"Message backup failed: {e}\n{traceback.format_exc()}")
            modal_prog.write(f"[bold red]Message backup failed: {e}[/bold red]")
            modal_prog.phase_report("Message Backup", "error")
        finally:
            await self.engine.close_connections()

    @work(exclusive=True)
    async def run_backup_sync(self) -> None:
        modal_prog = ProgressScreen(log_level=self.config.migration.log_level)
        self.app.push_screen(modal_prog)
        await asyncio.sleep(0.1)
        modal_prog.phase_progress()

        try:
            modal_prog.set_status("Starting sync...")
            await self.engine.discord_reader.start()
            await self.exporter.setup()

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
                    modal_prog.phase_report("Backup Sync", "stopped")
                    return

                modal_prog.set_progress(total_chans, total_chans)
                modal_prog.set_item_status("[bold green]Sync operation complete![/bold green]")

            await self.exporter.export_metadata()
            modal_prog.write("[bold green]Sync operation complete![/bold green]")
            logger.info("Sync operation completed successfully.")
            modal_prog.phase_report("Backup Sync")

        except Exception as e:
            logger.error(f"Sync failed: {e}\n{traceback.format_exc()}")
            modal_prog.write(f"[bold red]Sync failed: {e}[/bold red]")
            modal_prog.phase_report("Backup Sync", "error")
        finally:
            await self.engine.close_connections()
