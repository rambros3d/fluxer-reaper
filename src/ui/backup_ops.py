"""
BackupPane – self-contained backup-operations widget.
Embedded inside ModeScreen's "Backup" tab.
"""

import asyncio
import discord
import json
import re
from pathlib import Path
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Container, Vertical, VerticalScroll
from textual.widgets import Button, Label, Rule
from textual import work

from src.core.configuration import load_config
from src.core.base import MigrationContext
from src.disco_reaper.exporter import DiscordExporter
from src.ui.modals import ProgressModal, ChannelSelectModal


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
                yield Button("Backup Channel Messages", id="bp_backup_msgs", disabled=True)
                yield Button("Update & Sync Backup", id="bp_backup_sync", disabled=True)

    def on_mount(self) -> None:
        self._validate()

    def reload_config(self) -> None:
        self.config = load_config(self.config_path)
        self.engine = MigrationContext(self.config, target_platform=self.config.target_platform or "fluxer")
        self.exporter = DiscordExporter(self.engine.discord_reader, base_dir=f"Reaper-{self.cfg_name}")
        self._validate()

    # ── validation ────────────────────────────────────────────────────────

    @work(exclusive=True, thread=True)
    async def _validate(self) -> None:
        fillers = ["DISCORD_BOT_TOKEN", "000000000000000000", "DISCORD_SERVER_ID", "", None]
        d_token = self.config.discord_bot_token
        if d_token in fillers or self.config.discord_server_id in fillers:
            self.app.call_from_thread(self._update_ui, "[red]NOT CONFIGURED[/red]", "", "", False)
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

            self.app.call_from_thread(self._update_ui, s_text, b_text, backup_text, valid)
        except Exception as e:
            self.app.call_from_thread(self._update_ui, f"[red]Error: {e}[/red]", "", "", False)

    def _get_backup_info(self) -> str | None:
        profile_file = Path(f"Reaper-{self.cfg_name}") / "server_profile.json"
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

    @work(exclusive=True, thread=True)
    async def run_backup_profile(self) -> None:
        modal = ProgressModal()
        self.app.call_from_thread(self.app.push_screen, modal)
        await asyncio.sleep(0.1)

        try:
            self.app.call_from_thread(modal.set_status, "Starting readers...")
            await self.engine.discord_reader.start()
            await self.exporter.setup()

            self.app.call_from_thread(modal.write, "[yellow]Backing up server profile & skeleton...[/yellow]")
            await self.exporter.export_metadata()
            await self.exporter.download_server_assets()

            self.app.call_from_thread(modal.write, "Exporting structure...")
            _, cat_count, chan_count = await self.exporter.export_channels_structure()

            self.app.call_from_thread(modal.write, "Exporting assets...")
            e_count, s_count = await self.exporter.export_assets()

            self.app.call_from_thread(modal.write, f"[bold green]Server Profile backed up to: {self.exporter.export_path}[/bold green]")
            self.app.call_from_thread(modal.write, f"- {cat_count} categories & {chan_count} channels")
            self.app.call_from_thread(modal.write, f"- {e_count} emojis, {s_count} stickers.")

        except discord.Forbidden as e:
            self.app.call_from_thread(modal.write, f"[bold red]Backup failed: {e}[/bold red]")
        except Exception as e:
            self.app.call_from_thread(modal.write, f"[bold red]Error: {e}[/bold red]")
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
                self.app.call_from_thread(modal_prog.write, "[yellow]No text/news channels found to backup.[/yellow]")
                self.app.call_from_thread(modal_prog.allow_close)
                return

            any_found = False
            backed_up_ids = set()
            for chan in eligible_channels:
                if (self.exporter.export_path / "message_backup" / f"{chan.id}.json").exists():
                    any_found = True
                    backed_up_ids.add(chan.id)

            self.app.call_from_thread(self.app.pop_screen)

            loop = asyncio.get_running_loop()
            future = loop.create_future()

            def check_channels(reply: dict | None) -> None:
                if not future.done():
                    future.set_result(reply)

            self.app.call_from_thread(
                self.app.push_screen,
                ChannelSelectModal(eligible_channels, cat_map, backed_up_ids, any_found),
                check_channels,
            )

            reply = await future
            if not reply:
                return

            selected_ids = reply["channels"]
            force_overwrite = reply["force"]

            selected_channels = [c for c in eligible_channels if c.id in selected_ids]

            self.app.call_from_thread(self.app.push_screen, modal_prog)
            await asyncio.sleep(0.1)

            total_chans = len(selected_channels)
            self.app.call_from_thread(modal_prog.set_status, "Backing up messages...")
            self.app.call_from_thread(modal_prog.write, f"[yellow]Starting backup for {total_chans} channels...[/yellow]")

            for chan in selected_channels:
                backup_exists = (self.exporter.export_path / "message_backup" / f"{chan.id}.json").exists()
                is_sync = backup_exists and not force_overwrite

                label = "Syncing Backup" if is_sync else "Backing up"
                self.app.call_from_thread(modal_prog.write, f"[cyan]{label}: {chan.name}[/cyan]")

                async def update_msg_count(name, count):
                    self.app.call_from_thread(modal_prog.set_status, f"{name}: {count} messages")

                await self.exporter.export_channel_messages(chan.id, progress_callback=update_msg_count, force=force_overwrite)
                await self.exporter.export_threads(chan.id, progress_callback=update_msg_count, force=force_overwrite)

                self.app.call_from_thread(modal_prog.write, f"[green]Completed: {chan.name}[/green]")

            await self.exporter.export_metadata()
            self.app.call_from_thread(modal_prog.write, "[bold green]Message backup complete![/bold green]")

        except Exception as e:
            self.app.call_from_thread(modal_prog.write, f"[bold red]Message backup failed: {e}[/bold red]")
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

            self.app.call_from_thread(modal_prog.write, "Updating structure...")
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
                self.app.call_from_thread(modal_prog.write, "[yellow]No existing backups found to sync.[/yellow]")
            else:
                self.app.call_from_thread(modal_prog.write, f"[yellow]Syncing {len(selected_channels)} channels...[/yellow]")
                for chan in selected_channels:
                    self.app.call_from_thread(modal_prog.write, f"[cyan]Syncing: {chan.name}[/cyan]")

                    async def update_msg_count(name, count):
                        self.app.call_from_thread(modal_prog.set_status, f"{name}: {count} messages")

                    await self.exporter.export_channel_messages(chan.id, progress_callback=update_msg_count, force=False)
                    await self.exporter.export_threads(chan.id, progress_callback=update_msg_count, force=False)
                    self.app.call_from_thread(modal_prog.write, f"[green]Synced: {chan.name}[/green]")

            await self.exporter.export_metadata()
            self.app.call_from_thread(modal_prog.write, "[bold green]Sync operation complete![/bold green]")

        except Exception as e:
            self.app.call_from_thread(modal_prog.write, f"[bold red]Sync failed: {e}[/bold red]")
        finally:
            await self.engine.close_connections()
            self.app.call_from_thread(modal_prog.set_status, "Finished.")
            self.app.call_from_thread(modal_prog.allow_close)
