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
        self.validation_results = {
            "discord_token": False, "discord_bot_name": None,
            "discord_server": False, "discord_server_name": None,
            "fluxer_token": False, "fluxer_bot_name": None,
            "fluxer_community": False, "fluxer_community_name": None
        }
        self.tokens_valid = False

        discord_task = asyncio.create_task(self.engine.discord_reader.validate())
        fluxer_task = asyncio.create_task(self.engine.fluxer_writer.validate())

        try:
            with console.status("[yellow]Validating tokens...[/yellow]"):
                start_time = asyncio.get_event_loop().time()
                done, pending = await asyncio.wait(
                    [discord_task, fluxer_task], 
                    timeout=3.0,
                    return_when=asyncio.ALL_COMPLETED
                )

                # Process Discord Result
                if discord_task in done:
                    try:
                        res = discord_task.result()
                        self.validation_results["discord_token"] = res.get("token", False)
                        self.validation_results["discord_bot_name"] = res.get("bot_name")
                        self.validation_results["discord_server"] = res.get("server", False)
                        self.validation_results["discord_server_name"] = res.get("server_name")
                        if not res.get("token"):
                            console.print("[bold red]Discord Token validation failed (Invalid Token).[/bold red]")
                        elif not res.get("server"):
                            console.print("[bold red]Discord Server ID validation failed (Invalid/Inaccessible ID).[/bold red]")
                    except Exception as e:
                        console.print(f"[bold red]Discord validation failed with error: {e}[/bold red]")
                else:
                    console.print("[bold red]Discord bot token validation timed out after 3 seconds.[/bold red]")
                    discord_task.cancel()

                # Process Fluxer Result
                if fluxer_task in done:
                    try:
                        res = fluxer_task.result()
                        self.validation_results["fluxer_token"] = res.get("token", False)
                        self.validation_results["fluxer_bot_name"] = res.get("bot_name")
                        self.validation_results["fluxer_community"] = res.get("community", False)
                        self.validation_results["fluxer_community_name"] = res.get("community_name")
                        if not res.get("token"):
                            console.print("[bold red]Fluxer Token validation failed (Invalid Token).[/bold red]")
                        elif not res.get("community"):
                            console.print("[bold red]Fluxer Community ID validation failed (Invalid/Inaccessible ID).[/bold red]")
                    except Exception as e:
                        console.print(f"[bold red]Fluxer validation failed with error: {e}[/bold red]")
                else:
                    console.print("[bold red]Fluxer bot token validation timed out after 3 seconds.[/bold red]")
                    fluxer_task.cancel()

                self.tokens_valid = all(self.validation_results.values())

        except Exception as e:
            console.print(f"[bold red]Validation system failure: {e}[/bold red]")
        finally:
            # Ensure tasks are cleaned up
            for t in [discord_task, fluxer_task]:
                if not t.done(): t.cancel()

    async def run(self):
        await self.validate_config()
        
        while True:
            console.print("")
            console.print(Panel.fit("Fluxer Reaper", style="bold blue"))            
            d_name = self.validation_results.get("discord_server_name")
            d_display = f"[bold green]\"{d_name}\"[/bold green]" if d_name else "[bold red]NOT SET UP[/bold red]"
            
            f_name = self.validation_results.get("fluxer_community_name")
            f_display = f"[bold green]\"{f_name}\"[/bold green]" if f_name else "[bold red]NOT SET UP[/bold red]"
            
            console.print(f"[bold cyan]Discord Server:[/bold cyan] {d_display}")
            console.print(f"[bold magenta]Fluxer Community:[/bold magenta] {f_display}")
            console.print("[bold]Main Menu[/bold]")            
            console.print("(1) Clone Server Template (Channels & Categories)")
            console.print("(2) Copy Roles & Permissions")
            console.print("(3) Copy Emojis & Stickers")
            console.print("(4) Sync Server Name, Logo and Banner")
            console.print("(5) Migrate message history [NOT IMPLEMENTED]")
            
            val_status = "[bold green][VALID][/bold green]" if self.tokens_valid else "[bold red][INVALID][/bold red]"
            console.print(f"(6) Configuration {val_status}")
            
            console.print("(Q) Exit")
            
            choice = Prompt.ask("Select an option", choices=["1", "2", "3", "4", "5", "6", "Q", "q"], default="Q").upper()
            
            if choice == "1":
                await self.clone_server_template()
            elif choice == "2":
                await self.copy_roles()
            elif choice == "3":
                await self.copy_emojis()
            elif choice == "4":
                await self.sync_server_metadata()
            elif choice == "5":
                await self.migrate_message_history()
            elif choice == "6":
                await self.edit_configuration()
            elif choice == "Q":
                console.print("[yellow]Exiting tool...[/yellow]")
                break

    async def edit_configuration(self):
        await self.validate_config()
        console.print("\n[bold]Configuration Status:[/bold]")
        
        def get_status_str(is_valid, name=None):
            status = "[bold green][VALID][/bold green]" if is_valid else "[bold red][INVALID][/bold red]"
            if is_valid and name:
                return f"{status} \"{name}\""
            return status
            
        console.print(f"Discord Bot Token {get_status_str(self.validation_results.get('discord_token', False), self.validation_results.get('discord_bot_name'))}")
        console.print(f"Fluxer Bot Token {get_status_str(self.validation_results.get('fluxer_token', False), self.validation_results.get('fluxer_bot_name'))}")
        console.print(f"Discord Server ID {get_status_str(self.validation_results.get('discord_server', False), self.validation_results.get('discord_server_name'))}")
        console.print(f"Fluxer Community ID {get_status_str(self.validation_results.get('fluxer_community', False), self.validation_results.get('fluxer_community_name'))}")
        
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
        await self.validate_config()

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
        console.print("\n[bold]Role & Permission Options[/bold]")
        console.print("(1) Copy Roles & Role Permissions")
        console.print("(2) Sync Category Permissions & Channel Permissions")
        console.print("(B) Back")
        
        choice = Prompt.ask("Select an option", choices=["1", "2", "B", "b"], default="B").upper()
        
        if choice == "B":
            return

        if choice == "1":
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
        
        elif choice == "2":
            console.print("\n[bold green]Syncing Category & Channel Permissions...[/bold green]")
            try:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    console=console
                ) as progress:
                    
                    perm_task = progress.add_task("[cyan]Syncing Permissions...", total=100)
                    
                    async def update_progress(item_name: str, current: int, total: int):
                        progress.update(perm_task, total=total, completed=current, description=f"[cyan]Syncing: {item_name}")
    
                    await self.engine.start_connections()
                    self.engine.is_running = True
                    await self.engine.sync_permissions(progress_callback=update_progress)
                    
                console.print("[bold green]Permission synchronization complete![/bold green]")
                
            except Exception as e:
                console.print(f"[bold red]Error during permission sync: {str(e)}[/bold red]")
            finally:
                await self.engine.close_connections()
                self.engine.is_running = False

    async def copy_emojis(self):
        console.print("\n[yellow]Fetching emojis and stickers...[/yellow]")
        try:
            await self.engine.start_connections()
            emojis = await self.engine.discord_reader.get_emojis()
            stickers = await self.engine.discord_reader.get_stickers()
            
            console.print(f"\n[bold]Custom emojis found: {len(emojis)}[/bold]")
            for e in emojis:
                console.print(f"  - Emoji: {e.name}")
            
            console.print(f"[bold]Custom stickers found: {len(stickers)}[/bold]")
            for s in stickers:
                console.print(f"  - Sticker: {s.name}")
            
            console.print("\n(1) Copy Emojis only")
            console.print("(2) Copy Stickers only")
            console.print("(3) Copy Emojis and Stickers")
            console.print("(B) Back")
            
            choice = Prompt.ask("Select an option", choices=["1", "2", "3", "B", "b"], default="B").upper()
            
            if choice == "B":
                return

            types_to_include = []
            if choice == "1":
                types_to_include = ["Emoji"]
            elif choice == "2":
                types_to_include = ["Sticker"]
            elif choice == "3":
                types_to_include = ["Emoji", "Sticker"]

            console.print("\n[bold green]Starting Migration...[/bold green]")
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console
            ) as progress:
                
                emoji_task = progress.add_task("[cyan]Copying Assets...", total=100)
                
                async def update_progress(item_name: str, item_type: str, current: int, total: int):
                    progress.update(emoji_task, total=total, completed=current, description=f"[cyan]Copying {item_type}: {item_name}")

                self.engine.is_running = True
                await self.engine.migrate_emojis(progress_callback=update_progress, types_to_include=types_to_include)
                
            console.print("[bold green]Migration complete![/bold green]")
            
        except Exception as e:
            console.print(f"[bold red]Error during emoji migration: {str(e)}[/bold red]")
        finally:
            await self.engine.close_connections()
            self.engine.is_running = False

    async def sync_server_metadata(self):
        console.print("\n[yellow]Fetching server metadata...[/yellow]")
        try:
            await self.engine.start_connections()
            metadata = await self.engine.discord_reader.get_server_metadata()
            
            name = metadata.get("name")
            icon_status = "[bold green]FOUND[/bold green]" if metadata.get("icon_url") else "[yellow]NOT FOUND[/yellow]"
            banner_status = "[bold green]FOUND[/bold green]" if metadata.get("banner_url") else "[yellow]NOT FOUND[/yellow]"
            
            console.print(f"\n[bold]Server Name:[/bold] {name}")
            console.print(f"[bold]Server Icon:[/bold] {icon_status}")
            console.print(f"[bold]Server Banner:[/bold] {banner_status}")
            
            console.print("\n(1) Sync Name only")
            console.print("(2) Sync Icon only")
            console.print("(3) Sync Banner only")
            console.print("(4) Sync Everything")
            console.print("(B) Back")
            
            choice = Prompt.ask("Select an option", choices=["1", "2", "3", "4", "B", "b"], default="B").upper()
            
            if choice == "B":
                return

            components = []
            if choice == "1":
                components = ["name"]
            elif choice == "2":
                components = ["icon"]
            elif choice == "3":
                components = ["banner"]
            elif choice == "4":
                components = ["name", "icon", "banner"]

            console.print("\n[bold green]Syncing Server Metadata...[/bold green]")
            
            async def progress_callback(item: str, status: str):
                color = "green" if status == "DONE" else "red" if status == "ERROR" else "yellow"
                console.print(f"{item} [[bold {color}]{status}[/bold {color}]]")

            await self.engine.sync_server_metadata(progress_callback, components=components)
            console.print("[bold green]Server metadata sync finished![/bold green]")
        except Exception as e:
            console.print(f"[bold red]Error during metadata sync: {str(e)}[/bold red]")
        finally:
            await self.engine.close_connections()

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
