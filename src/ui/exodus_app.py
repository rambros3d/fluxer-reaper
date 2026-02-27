import sys
import asyncio
import logging
import time
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from src.core.configuration import load_config, save_config
from src.core.base import MigrationContext
from src.exodus.exporter import DiscordExporter

console = Console()

class ExodusCLI:
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

    async def run(self):
        await self.validate_config()

        while True:
            console.print("")
            console.print(Panel.fit("Discord Exodus - Server Exporter", style="bold green"))
            
            d_name = self.validation_results.get("discord_server_name")
            d_display = f"[bold green]\"{d_name}\"[/bold green]" if d_name else "[bold red]NOT CONNECTED[/bold red]"
            console.print(f"[bold cyan]Source Server:[/bold cyan] {d_display}")
            
            console.print("\n[bold]Main Menu[/bold]")
            console.print("(1) Setup Export Folder & Metadata")
            console.print("(2) Export Roles")
            console.print("(3) Export Emojis & Stickers")
            console.print("(4) Export Channels Structure")
            console.print("(5) Export All Message History (Long operation)")
            console.print("(C) Configuration")
            console.print("(Q) Exit")
            
            choice = Prompt.ask("\nSelect an option", choices=["1", "2", "3", "4", "5", "C", "Q"], default="Q", show_choices=False).upper()
            
            if choice == "1":
                await self.setup_export()
            elif choice == "2":
                await self.export_roles()
            elif choice == "3":
                await self.export_assets()
            elif choice == "4":
                await self.export_structure()
            elif choice == "5":
                await self.export_messages()
            elif choice == "C":
                await self.edit_configuration()
            elif choice == "Q":
                await self.engine.close_connections()
                break

    async def setup_export(self):
        if not self.tokens_valid: return
        try:
            await self.engine.discord_reader.start()
            with console.status("[yellow]Setting up export directory...[/yellow]"):
                meta = await self.exporter.setup()
                await self.exporter.export_metadata()
            console.print(f"[bold green]Export directory ready: {self.exporter.export_path}[/bold green]")
            console.print(f"Server: [bold]{meta['name']}[/bold] ({meta['id']})")
        except Exception as e:
            console.print(f"[bold red]Setup failed: {e}[/bold red]")
        finally:
            await self.engine.close_connections()

    async def export_roles(self):
        if not self.tokens_valid: return
        try:
            await self.engine.discord_reader.start()
            await self.exporter.setup()
            with console.status("[yellow]Exporting roles...[/yellow]"):
                roles = await self.exporter.export_roles()
            console.print(f"[bold green]Exported {len(roles)} roles to roles.json[/bold green]")
        except Exception as e:
            console.print(f"[bold red]Role export failed: {e}[/bold red]")
        finally:
            await self.engine.close_connections()

    async def export_assets(self):
        if not self.tokens_valid: return
        try:
            await self.engine.discord_reader.start()
            await self.exporter.setup()
            with console.status("[yellow]Exporting emojis and stickers...[/yellow]"):
                e_count, s_count = await self.exporter.export_emojis_stickers()
            console.print(f"[bold green]Exported {e_count} emojis and {s_count} stickers.[/bold green]")
        except Exception as e:
            console.print(f"[bold red]Asset export failed: {e}[/bold red]")
        finally:
            await self.engine.close_connections()

    async def export_structure(self):
        if not self.tokens_valid: return
        try:
            await self.engine.discord_reader.start()
            await self.exporter.setup()
            with console.status("[yellow]Exporting channel structure...[/yellow]"):
                struct = await self.exporter.export_channels_structure()
            console.print(f"[bold green]Exported channel hierarchy to channels_structure.json[/bold green]")
        except Exception as e:
            console.print(f"[bold red]Structure export failed: {e}[/bold red]")
        finally:
            await self.engine.close_connections()

    async def export_messages(self):
        if not self.tokens_valid: return
        try:
            await self.engine.discord_reader.start()
            await self.exporter.setup()
            channels = await self.engine.discord_reader.get_channels()
            
            console.print(f"\n[yellow]Found {len(channels)} channels to export.[/yellow]")
            if not Confirm.ask("Start message export? This may take a while.", default=True):
                return

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console
            ) as progress:
                
                overall_task = progress.add_task("[cyan]Exporting Channels...", total=len(channels))
                
                for chan in channels:
                    if chan.type not in [discord.ChannelType.text, discord.ChannelType.news, discord.ChannelType.voice]:
                        progress.advance(overall_task)
                        continue
                        
                    progress.update(overall_task, description=f"[cyan]Exporting: {chan.name}")
                    await self.exporter.export_channel_messages(chan.id)
                    # Also export threads for this channel
                    await self.exporter.export_threads(chan.id)
                    progress.advance(overall_task)

            console.print("[bold green]Message export complete![/bold green]")
        except Exception as e:
            console.print(f"[bold red]Message export failed: {e}[/bold red]")
        finally:
            await self.engine.close_connections()

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

async def run_exodus(config_path="config.yaml"):
    app = ExodusCLI(config_path)
    await app.run()
