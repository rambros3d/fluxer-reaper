import sys
import asyncio
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from src.config import load_config, save_config
from src.core.engine import MigrationEngine

console = Console()

class MigrationCLI:
    """Standard CLI app to manage the Discord to Fluxer migration."""
    
    def __init__(self):
        try:
            self.config = load_config()
        except Exception as e:
            console.print(f"[bold red]Failed to load config: {e}[/bold red]")
            sys.exit(1)

        self.engine = MigrationEngine(self.config)
        self.progress_callback_task = None
        self.tokens_valid = False

    async def validate_config(self):
        with console.status("[yellow]Validating tokens...[/yellow]"):
            self.validation_results = await self.engine.validate_all()
            self.tokens_valid = all(self.validation_results.values())

    async def run(self):
        console.print(Panel.fit("Discord Reaper", style="bold blue"))
        await self.validate_config()
        
        while True:
            console.print("\n[bold]Main Menu[/bold]")
            console.print("(1) Clone Server Template (Channels & Categories)")
            console.print("(2) Copy Roles & Permissions")
            console.print("(3) Copy Emojis & Stickers")
            console.print("(4) Migrate message history")
            
            val_status = "[bold green][VALID][/bold green]" if self.tokens_valid else "[bold red][INVALID][/bold red]"
            console.print(f"(5) Configuration {val_status}")
            
            console.print("(Q) Exit")
            
            choice = Prompt.ask("Select an option", choices=["1", "2", "3", "4", "5", "Q", "q"], default="1").upper()
            
            if choice == "1":
                await self.clone_server_template()
            elif choice == "2":
                await self.copy_roles()
            elif choice == "3":
                await self.copy_emojis()
            elif choice == "4":
                await self.migrate_message_history()
            elif choice == "5":
                await self.edit_configuration()
            elif choice == "Q":
                console.print("[yellow]Exiting tool...[/yellow]")
                break

    async def edit_configuration(self):
        console.print("\n[bold]Configuration Status:[/bold]")
        
        def get_status_str(is_valid):
            return "[bold green][VALID][/bold green]" if is_valid else "[bold red][INVALID][/bold red]"
            
        console.print(f"Discord Bot Token {get_status_str(self.validation_results.get('discord_token', False))}")
        console.print(f"Fluxer Bot Token {get_status_str(self.validation_results.get('fluxer_token', False))}")
        console.print(f"Discord Server ID {get_status_str(self.validation_results.get('discord_server', False))}")
        console.print(f"Fluxer Community ID {get_status_str(self.validation_results.get('fluxer_community', False))}")
        
        if not Confirm.ask("Edit now?"):
            return
            
        console.print("\n[bold]Configuration Editor[/bold] (leave blank to keep current)")
        
        d_token = Prompt.ask("Discord Bot Token", default=self.config.discord_bot_token)
        f_token = Prompt.ask("Fluxer Bot Token", default=self.config.fluxer_bot_token)
        d_server = Prompt.ask("Discord Server ID", default=self.config.discord_server_id)
        f_comm = Prompt.ask("Fluxer Community ID", default=self.config.fluxer_community_id)
        
        # Only rewrite if changed
        if (d_token != self.config.discord_bot_token or
            f_token != self.config.fluxer_bot_token or
            d_server != self.config.discord_server_id or
            f_comm != self.config.fluxer_community_id):
            
            self.config.discord_bot_token = d_token
            self.config.fluxer_bot_token = f_token
            self.config.discord_server_id = d_server
            self.config.fluxer_community_id = f_comm
            
            save_config(self.config)
            # Recreate engine with new config
            self.engine = MigrationEngine(self.config)
            
            # Re-validate
            console.print("[yellow]Validating new configuration...[/yellow]")
            await self.update_validation_status()

            console.print(f"\nDiscord Bot Token {get_status_str(self.validation_results.get('discord_token', False))}")
            console.print(f"Fluxer Bot Token {get_status_str(self.validation_results.get('fluxer_token', False))}")
            console.print(f"Discord Server ID {get_status_str(self.validation_results.get('discord_server', False))}")
            console.print(f"Fluxer Community ID {get_status_str(self.validation_results.get('fluxer_community', False))}")
            
            console.print("[bold green]Configuration updated and saved to config.yaml![/bold green]")
        else:
            console.print("[yellow]No changes made.[/yellow]")

    async def update_validation_status(self):
        self.validation_results = await self.engine.validate_all()
        self.tokens_valid = all(self.validation_results.values())

    async def clone_server_template(self):
        console.print("\n[yellow]Fetching server structure...[/yellow]")
        categories = []
        channels = []
        try:
            await self.engine.start_connections()
            categories = await self.engine.discord_reader.get_categories()
            channels = await self.engine.discord_reader.get_channels()
        except Exception as e:
            console.print(f"[bold red]Failed to fetch server structure: {e}[/bold red]")
            await self.engine.close_connections()
            return
            
        console.print("\n[bold]Server Template Preview:[/bold]")
        
        # Group channels by category
        import discord
        channels_by_cat = {}
        uncategorized = []
        
        for ch in channels:
            if ch.category_id:
                if ch.category_id not in channels_by_cat:
                    channels_by_cat[ch.category_id] = []
                channels_by_cat[ch.category_id].append(ch)
            else:
                uncategorized.append(ch)
                
        def print_channel(ch):
            if isinstance(ch, discord.TextChannel):
                color = "cyan"
            elif isinstance(ch, discord.VoiceChannel):
                color = "green"
            elif isinstance(ch, discord.ForumChannel):
                color = "magenta"
            else:
                color = "white"
            console.print(f"  [{color}]- {ch.name}[/{color}]")

        for cat in categories:
            console.print(f"[bold yellow]{cat.name}[/bold yellow]")
            cat_channels = channels_by_cat.get(cat.id, [])
            for ch in cat_channels:
                print_channel(ch)
                
        if uncategorized:
            console.print(f"[bold yellow]Uncategorized[/bold yellow]")
            for ch in uncategorized:
                print_channel(ch)
                
        console.print("")

        if not Confirm.ask("Are you sure you want to clone channels and categories?"):
            await self.engine.close_connections()
            return
            
        console.print("\n[bold green]Starting Channel Cloning...[/bold green]")
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console
            ) as progress:
                
                channel_task = progress.add_task("[cyan]Copying Channels...", total=100)
                
                async def update_progress(item_name: str, current: int, total: int):
                    progress.update(channel_task, total=total, completed=current, description=f"[cyan]Copying Channel: {item_name}")

                self.engine.is_running = True
                await self.engine.migrate_channels(progress_callback=update_progress)
                
            console.print("[bold green]Server Template cloned![/bold green]")
            
        except Exception as e:
            console.print(f"[bold red]Error during channel clone: {str(e)}[/bold red]")
        finally:
            await self.engine.close_connections()
            self.engine.is_running = False

    async def copy_roles(self):
        if not Confirm.ask("Are you sure you want to copy roles?"):
            return
            
        console.print("\n[bold green]Starting Role Migration...[/bold green]")
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console
            ) as progress:
                
                role_task = progress.add_task("[cyan]Copying Roles...", total=100)
                
                async def update_progress(item_name: str, current: int, total: int):
                    progress.update(role_task, total=total, completed=current, description=f"[cyan]Copying Role: {item_name}")

                await self.engine.start_connections()
                self.engine.is_running = True
                await self.engine.migrate_roles(progress_callback=update_progress)
                
            console.print("[bold green]Role migration complete![/bold green]")
            
        except Exception as e:
            console.print(f"[bold red]Error during role migration: {str(e)}[/bold red]")
        finally:
            await self.engine.close_connections()
            self.engine.is_running = False

    async def copy_emojis(self):
        if not Confirm.ask("Are you sure you want to copy emojis and stickers?"):
            return
            
        console.print("\n[bold green]Starting Emoji Migration...[/bold green]")
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console
            ) as progress:
                
                emoji_task = progress.add_task("[cyan]Copying Emojis...", total=100)
                
                async def update_progress(item_name: str, current: int, total: int):
                    progress.update(emoji_task, total=total, completed=current, description=f"[cyan]Copying Emoji: {item_name}")

                await self.engine.start_connections()
                self.engine.is_running = True
                await self.engine.migrate_emojis(progress_callback=update_progress)
                
            console.print("[bold green]Emoji migration complete![/bold green]")
            
        except Exception as e:
            console.print(f"[bold red]Error during emoji migration: {str(e)}[/bold red]")
        finally:
            await self.engine.close_connections()
            self.engine.is_running = False

    async def migrate_message_history(self):
        if not Confirm.ask("Are you sure you want to migrate message history?"):
            return
            
        console.print("\n[bold green]Starting Message History Migration...[/bold green]")
        try:
            await self.engine.start_connections()
            # Mock example of passing message progress.
            console.print("[cyan]Migrating messages for the first channel (Demo)...[/cyan]")
            channels = await self.engine.discord_reader.get_channels()
            if channels:
                self.engine.is_running = True
                await self.engine.migrate_messages(channels[0].id)
            console.print("[bold green]Message history migration complete![/bold green]")
        except Exception as e:
            console.print(f"[bold red]Error during message migration: {str(e)}[/bold red]")
        finally:
            await self.engine.close_connections()
            self.engine.is_running = False

async def run_cli():
    cli = MigrationCLI()
    await cli.run()
