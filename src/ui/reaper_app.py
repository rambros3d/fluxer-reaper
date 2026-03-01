import sys
import asyncio
import discord
import logging
import time
import re
import json
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from src.core.configuration import load_config, save_config
from src.core.base import MigrationContext
from src.disco_reaper.exporter import DiscordExporter

console = Console()

class DiscoReaperCLI:
    """CLI app to manage Discord server data export."""
    
    def __init__(self, config_path="config.yaml"):
        self.config_path = config_path
        try:
            self.config = load_config(self.config_path)
            # We only need the Discord side of MigrationContext
            self.engine = MigrationContext(self.config, target_platform="fluxer")
            self.exporter = DiscordExporter(self.engine.discord_reader)
        except Exception as e:
            console.print(f"[bold red]Failed to load config: {e}[/bold red]")
            sys.exit(1)

        self.tokens_valid = False

    async def validate_config(self):
        self.validation_results = {
            "discord_token": False, "discord_bot_name": None,
            "discord_server": False, "discord_server_name": None,
            "discord_timeout": False
        }
        
        d_token = self.config.discord_bot_token
        fillers = ["DISCORD_BOT_TOKEN", "000000000000000000", "DISCORD_SERVER_ID", "", None]
        discord_dummy = d_token in fillers or self.config.discord_server_id in fillers

        if discord_dummy:
            console.print("[bold yellow]Discord setup incomplete in config.yaml[/bold yellow]")
            return False

        try:
            with console.status("[yellow]Validating Discord token...[/yellow]"):
                res = await self.engine.discord_reader.validate()
                self.validation_results["discord_token"] = res.get("token", False)
                self.validation_results["discord_bot_name"] = res.get("bot_name")
                self.validation_results["discord_server"] = res.get("server", False)
                self.validation_results["discord_server_name"] = res.get("server_name")
                
                if not res.get("token"):
                    console.print("[bold red]Discord Token validation failed.[/bold red]")
                elif not res.get("server"):
                    console.print("[bold red]Discord Server ID validation failed.[/bold red]")
                else:
                    self.tokens_valid = True
                    return True
        except Exception as e:
            console.print(f"[bold red]Discord validation failed: {e}[/bold red]")
        
        return False
        
    def get_backup_info(self):
        """Checks for existing backup and returns formatted timestamp."""
        d_name = self.validation_results.get("discord_server_name")
        d_id = self.config.discord_server_id
        if not d_name or not d_id or d_id == "DISCORD_SERVER_ID":
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

    async def run(self):
        await self.validate_config()

        while True:
            console.print("")
            console.print(Panel.fit("Disco Reaper - Server Backup Tool", style="bold green"))
            
            d_name = self.validation_results.get("discord_server_name")
            d_display = f"[bold green]\"{d_name}\"[/bold green]" if d_name else "[bold red]NOT CONNECTED[/bold red]"
            console.print(f"[bold cyan]Source Server:[/bold cyan] {d_display}")
            
            b_name = self.validation_results.get("discord_bot_name")
            b_display = f"[bold green]\"{b_name}\"[/bold green]" if b_name else "[bold red]UNKNOWN[/bold red]"
            console.print(f"[bold cyan]Bot name:[/bold cyan] {b_display}")
            
            backup_ts = self.get_backup_info()
            if backup_ts:
                console.print(f"[bold cyan]Backup Found:[/bold cyan] [bold yellow]{backup_ts}[/bold yellow]")
            
            console.print("\n[bold]Main Menu[/bold]")
            console.print("(1) Backup Server Profile")
            console.print("(2) Backup Channel Messages")
            console.print("(3) Update & Sync Backup")
            console.print("(4) Configuration")
            console.print("(Q) Exit")
            
            choice = Prompt.ask("\nSelect an option", choices=["1", "2", "3", "4", "Q"], default="Q", show_choices=False).upper()
            
            if choice == "1":
                await self.backup_server_profile()
            elif choice == "2":
                await self.backup_messages()
            elif choice == "3":
                await self.sync_backup()
            elif choice == "4":
                await self.edit_configuration()
            elif choice == "Q":
                await self.engine.close_connections()
                break

    async def backup_server_profile(self):
        """Option 1: Backup name, banner, logo, roles, structure, and custom assets."""
        if not self.tokens_valid: return
        try:
            await self.engine.discord_reader.start()
            meta = await self.exporter.setup()
            
            with console.status("[yellow]Backing up server profile & skeleton...[/yellow]"):
                # 1. Profile & Assets
                await self.exporter.export_metadata()
                await self.exporter.download_server_assets()
                
                # 2. Roles
                try:
                    role_data = await self.exporter.export_roles()
                    r_count = len(role_data)
                except discord.Forbidden:
                    r_count = 0
                    console.print("[yellow]Warning: Could not backup roles (Missing Access).[/yellow]")
                
                # 3. Structure
                _, cat_count, chan_count = await self.exporter.export_channels_structure()
                
                # 4. Emojis & Stickers
                e_count, s_count = await self.exporter.export_assets()

            console.print(f"[bold green]Server Profile backed up to: {self.exporter.export_path}[/bold green]")
            console.print("\n[bold]Profile Backup Stats:[/bold]")
            console.print("- name, logo & banner")
            console.print(f"- {cat_count} categories & {chan_count} channels")
            console.print(f"- {r_count} roles")
            console.print(f"- {e_count} emojis, {s_count} stickers.")
        except discord.Forbidden as e:
            console.print(f"[bold red]Backup failed: {e}[/bold red]")
            console.print("[yellow]Hint: Missing permissions. Check bot 'Guilds' and 'Channel' access.[/yellow]")
        except Exception as e:
            console.print(f"[bold red]Backup failed: {e}[/bold red]")
        finally:
            await self.engine.close_connections()

    async def backup_messages(self, force_overwrite=None, skip_found_prompt=False, auto_sync_existing=False):
        """Option 2: Backup Channels structure and all message history."""
        if not self.tokens_valid: return
        try:
            await self.engine.discord_reader.start()
            await self.exporter.setup()
            
            # 1. Export structure first
            with console.status("[yellow]Exporting channel structure...[/yellow]"):
                await self.exporter.export_channels_structure()
            
            # 2. Select Channels
            with console.status("[yellow]Fetching channels & categories...[/yellow]"):
                all_channels = await self.engine.discord_reader.get_channels()
                all_categories = await self.engine.discord_reader.get_categories()
                cat_map = {c.id: c.name for c in all_categories}

            # Filter for exportable channels
            eligible_channels = [
                c for c in all_channels 
                if c.type in [discord.ChannelType.text, discord.ChannelType.news]
            ]
            
            if not eligible_channels:
                console.print("[yellow]No text/news channels found to backup.[/yellow]")
                return

            selected_channels = []
            
            if auto_sync_existing:
                selected_channels = [
                    c for c in eligible_channels 
                    if (self.exporter.export_path / "message_backup" / f"{c.id}.json").exists()
                ]
                if not selected_channels:
                    console.print("[yellow]No existing backups found to sync.[/yellow]")
                    return
                # In auto-sync mode, we always use sync (force_overwrite=False) and skip prompts
                force_overwrite = False
                skip_found_prompt = True
            else:
                console.print(f"\n[bold]Select Channels to Backup ({len(eligible_channels)} total):[/bold]")
                
                # Identify duplicate names to show categories for them
                name_counts = {}
                for chan in eligible_channels:
                    name_counts[chan.name] = name_counts.get(chan.name, 0) + 1
                
                for i, chan in enumerate(eligible_channels):
                    display_name = chan.name
                    if name_counts[chan.name] > 1:
                        cat_name = cat_map.get(chan.category_id)
                        if cat_name:
                            display_name = f"{chan.name} [{cat_name}]"
                    
                    # Check for existing backup
                    found_prefix = ""
                    backup_file = self.exporter.export_path / "message_backup" / f"{chan.id}.json"
                    if backup_file.exists():
                        found_prefix = "[green][FOUND][/green] "
                    
                    console.print(f"({i+1}) {found_prefix}{display_name}")
                
                console.print("(A) [bold green]All Channels[/bold green]")
                console.print("(B) Back")

                choices = [str(i+1) for i in range(len(eligible_channels))] + ["A", "B", "a", "b"]
                selection_input = Prompt.ask("\nSelect option or indices (e.g. 1,2,5)", default="B")
                
                if selection_input.upper() == "B":
                    return
                
                if selection_input.upper() == "A":
                    selected_channels = eligible_channels
                else:
                    try:
                        # Handle multiple indices like '1,2,5'
                        indices = [int(i.strip()) - 1 for i in selection_input.split(",") if i.strip().isdigit()]
                        selected_channels = [eligible_channels[i] for i in indices if 0 <= i < len(eligible_channels)]
                    except Exception:
                        console.print("[red]Invalid selection. Aborting.[/red]")
                        return

            if not selected_channels:
                console.print("[yellow]No valid channels selected.[/yellow]")
                return

            # Check if any have [FOUND]
            any_found = False
            for chan in selected_channels:
                if (self.exporter.export_path / "message_backup" / f"{chan.id}.json").exists():
                    any_found = True
                    break
            
            if force_overwrite is None:
                force_overwrite = False
                if any_found and not skip_found_prompt:
                    console.print("\n[bold yellow]Existing backup(s) found for some selected channels.[/bold yellow]")
                    console.print("(Y) Update & Sync Backup")
                    console.print("(F) [bold red]Force Overwrite Backup[/bold red]")
                    console.print("(B) Back")
                    
                    sync_choice = Prompt.ask("\nSelect option", choices=["Y", "F", "B"], default="Y").upper()
                    if sync_choice == "B":
                        return
                    force_overwrite = (sync_choice == "F")

            console.print(f"\n[yellow]Starting backup for {len(selected_channels)} channels...[/yellow]")
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TextColumn("- [bold yellow]{task.fields[msg_count]} {task.fields[suffix]}"),
                console=console
            ) as progress:
                for chan in selected_channels:
                    # Determine if it's a sync or fresh backup
                    backup_exists = (self.exporter.export_path / "message_backup" / f"{chan.id}.json").exists()
                    is_sync = backup_exists and not force_overwrite
                    
                    label = "Syncing Backup" if is_sync else "Backing up"
                    suffix = "new messages" if is_sync else "messages"
                    
                    # Create a specific task for each channel to keep it on its own line
                    task_id = progress.add_task(f"[cyan]{label}: {chan.name}", total=None, msg_count=0, suffix=suffix)
                    
                    async def update_msg_count(name, count, tid=task_id):
                        progress.update(tid, msg_count=count)
                    
                    await self.exporter.export_channel_messages(chan.id, progress_callback=update_msg_count, force=force_overwrite)
                    await self.exporter.export_threads(chan.id, progress_callback=update_msg_count, force=force_overwrite)
                    
                    # Mark as finished (stop spinner)
                    progress.stop_task(task_id)
                    progress.update(task_id, description=f"[green]Completed: {chan.name}")

            console.print("[bold green]Message backup complete![/bold green]")
            # Update last_backup in server_profile.json
            await self.exporter.export_metadata()
        except Exception as e:
            console.print(f"[bold red]Message backup failed: {e}[/bold red]")
        finally:
            await self.engine.close_connections()

    async def sync_backup(self):
        """Option 3: Update & Sync Backup (Automated incremental sync for existing backups)."""
        console.print("[yellow]Syncing backup... (Updating existing data)[/yellow]")
        await self.backup_server_profile()
        # Automatically select and sync existing backups
        await self.backup_messages(auto_sync_existing=True)

    async def edit_configuration(self):
        # reuse or implement simplified version of edit_configuration from app.py
        console.print("\n[bold]Configuration Editor[/bold]")
        d_token = Prompt.ask("Discord Bot Token", default=self.config.discord_bot_token)
        d_server = Prompt.ask("Discord Server ID", default=self.config.discord_server_id)
        
        if d_token != self.config.discord_bot_token or d_server != self.config.discord_server_id:
            self.config.discord_bot_token = d_token
            self.config.discord_server_id = d_server
            save_config(self.config, self.config_path)
            self.engine = MigrationContext(self.config, target_platform="fluxer")
            self.exporter = DiscordExporter(self.engine.discord_reader)
            await self.validate_config()
            console.print("[bold green]Config updated![/bold green]")
        else:
            console.print("[yellow]No changes.[/yellow]")

async def run_disco_reaper(config_path="config.yaml"):
    app = DiscoReaperCLI(config_path)
    await app.run()
