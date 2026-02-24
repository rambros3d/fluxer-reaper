import sys
import asyncio
import logging
import re
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from src.core.configuration import load_config, save_config
from src.core.base import MigrationContext
from src.core.clone_server import sync_channel_state, migrate_channels
from src.core.roles_permissions import sync_roles_state, sync_permissions, migrate_roles
from src.core.emoji_stickers import sync_assets_state, migrate_emojis
from src.core.server_metadata import sync_server_metadata
from src.core.migrate_message import analyze_migration, migrate_messages
from src.core.danger_zone import danger_remove_logo_and_banner, danger_delete_all_channels, danger_reset_channel_permissions, danger_delete_all_roles, danger_delete_all_emojis_and_stickers
from src.core.audit import log_audit_event

class RateLimitHandler(logging.Handler):
    """Intersects library logs to print clean rate limit messages."""
    def __init__(self):
        super().__init__()
        self._last_print = ""

    def emit(self, record):
        try:
            msg = record.getMessage()
            # Detect rate limit messages from discord.py or fluxer.py
            if "retry" in msg.lower() and ("rate limit" in msg.lower() or "429" in msg):
                # Extract seconds using regex: supports "retry in 5.50s" and "Retrying in 5.50 seconds"
                match = re.search(r"in ([\d.]+)\s*(?:seconds?|s)", msg, re.IGNORECASE)
                if match:
                    seconds = match.group(1)
                    platform = "discord" if "discord" in record.name.lower() else "fluxer"
                    
                    # Format the message
                    new_msg = f"{platform} API rate limit: will retry after {seconds}"
                    
                    # Avoid spamming the exact same message if nothing changed
                    if new_msg == self._last_print:
                        return
                    
                    self._last_print = new_msg
                    
                    # Use rich console to print on the same line.
                    # end="\r" works with rich's internal live-update handling.
                    # We add some padding to clear old text.
                    console.print(f"{new_msg}   ", end="\r")
        except Exception:
            pass

console = Console()

class MigrationCLI:
    """Standard CLI app to manage the Discord to Fluxer migration."""
    
    def __init__(self):
        try:
            self.config = load_config()
        except Exception as e:
            console.print(f"[bold red]Failed to load config: {e}[/bold red]")
            sys.exit(1)

        self.engine = MigrationContext(self.config)
        self.progress_callback_task = None
        self.tokens_valid = False

        # Register rate limit interceptor for both libraries
        rl_handler = RateLimitHandler()
        logging.getLogger("discord").addHandler(rl_handler)
        logging.getLogger("fluxer").addHandler(rl_handler)

    async def validate_config(self):
        self.validation_results = {
            "discord_token": False, "discord_bot_name": None,
            "discord_server": False, "discord_server_name": None,
            "discord_intents": {}, "discord_permissions": {},
            "fluxer_token": False, "fluxer_bot_name": None,
            "fluxer_community": False, "fluxer_community_name": None,
            "fluxer_permissions": {},
            "discord_timeout": False, "fluxer_timeout": False
        }
        self.tokens_valid = False

        discord_task = asyncio.create_task(self.engine.discord_reader.validate())
        fluxer_task = asyncio.create_task(self.engine.fluxer_writer.validate())

        try:
            with console.status("[yellow]Validating tokens...[/yellow]"):
                start_time = asyncio.get_event_loop().time()
                done, pending = await asyncio.wait(
                    [discord_task, fluxer_task], 
                    timeout=10.0,
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
                        else:
                            # Store intents & permissions for later UI display
                            self.validation_results["discord_intents"] = res.get("intents", {})
                            self.validation_results["discord_permissions"] = res.get("permissions", {})

                    except Exception as e:
                        console.print(f"[bold red]Discord validation failed with error: {e}[/bold red]")
                else:
                    console.print("[bold red]Discord bot token validation timed out after 10 seconds.[/bold red]")
                    self.validation_results["discord_timeout"] = True
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
                        else:
                            # Store permissions
                            self.validation_results["fluxer_permissions"] = res.get("permissions", {})

                    except Exception as e:
                        console.print(f"[bold red]Fluxer validation failed with error: {e}[/bold red]")
                else:
                    console.print("[bold red]Fluxer bot token validation timed out after 10 seconds.[/bold red]")
                    self.validation_results["fluxer_timeout"] = True
                    fluxer_task.cancel()

                # Only tokens and server/community existence are strictly required for 'tokens_valid'
                self.tokens_valid = (
                    self.validation_results.get("discord_token") and 
                    self.validation_results.get("discord_server") and 
                    self.validation_results.get("fluxer_token") and 
                    self.validation_results.get("fluxer_community")
                )

                # Check if all permissions are actually granted
                self.permissions_complete = True
                if self.tokens_valid:
                    # Discord
                    d_intents = self.validation_results.get("discord_intents", {})
                    d_perms = self.validation_results.get("discord_permissions", {})
                    if not all([d_intents.get("message_content"), d_perms.get("view_channel"), d_perms.get("read_message_history")]):
                        self.permissions_complete = False
                    
                    # Fluxer
                    f_perms = self.validation_results.get("fluxer_permissions", {})
                    if not all(f_perms.values()) if f_perms else True:
                        self.permissions_complete = False

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
            d_display = f"[bold green]\"{d_name}\"[/bold green]" if d_name else ("[bold yellow]TIMEOUT ERROR[/bold yellow]" if self.validation_results.get("discord_timeout") else "[bold red]NOT SET UP[/bold red]")
            
            f_name = self.validation_results.get("fluxer_community_name")
            f_display = f"[bold green]\"{f_name}\"[/bold green]" if f_name else ("[bold yellow]TIMEOUT ERROR[/bold yellow]" if self.validation_results.get("fluxer_timeout") else "[bold red]NOT SET UP[/bold red]")
            
            console.print(f"[bold cyan]Discord Server:[/bold cyan] {d_display}")
            console.print(f"[bold #4641D9]Fluxer Community:[/bold #4641D9] {f_display}")
            console.print("[bold]Main Menu[/bold]")            
            console.print("(1) Clone Server Template (Channels & Categories)")
            console.print("(2) Copy Roles & Permissions")
            console.print("(3) Copy Emojis & Stickers")
            console.print("(4) Sync Server Name, Logo and Banner")
            console.print("(5) Migrate message history")
            
            if not self.tokens_valid:
                val_status = "[bold red][INVALID][/bold red]"
            elif not self.permissions_complete:
                val_status = "[bold yellow][PERMISSION MISSING][/bold yellow]"
            else:
                val_status = "[bold green][VALID][/bold green]"
            
            console.print(f"(6) Configuration {val_status}")
            console.print("(7) [red]Danger Zone ⚠[/red]")
            
            console.print("(Q) Exit")
            
            choice = Prompt.ask("\nSelect an option [1/2/3/4/5/6/7/Q]", choices=["1", "2", "3", "4", "5", "6", "7", "Q", "q"], default="Q", show_choices=False).upper()
            
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
            elif choice == "7":
                await self.danger_zone()
            elif choice == "Q":
                console.print("[yellow]Exiting tool...[/yellow]")
                await self.engine.close_connections()
                break

    async def edit_configuration(self):
        await self.validate_config()

        # Display Required Permissions FIRST
        def fmt(name, val):
            if val is None: return f"[dim]{name}[/dim]" # Unknown
            return f"[green]{name}[/green]" if val else f"[red]{name} [MISSING][/red]"

        d_intents = self.validation_results.get("discord_intents", {})
        d_perms = self.validation_results.get("discord_permissions", {})
        f_perms = self.validation_results.get("fluxer_permissions", {})

        perm_table = Table(show_header=True, header_style="bold magenta", box=None, padding=(0, 2))
        perm_table.add_column("Bot Type")
        perm_table.add_column("Required Permissions & Intents")
        
        perm_table.add_row(
            "[bold #5865F2]Discord Bot[/bold #5865F2]", 
            f"• [bold]Intents:[/bold] Server Members, {fmt('Message Content', d_intents.get('message_content'))}\n"
            f"• [bold]Permissions:[/bold] {fmt('View Channels', d_perms.get('view_channel'))}, {fmt('Read Message History', d_perms.get('read_message_history'))}"
        )
        perm_table.add_row(
            "[bold #4641D9]Fluxer Bot[/bold #4641D9]", 
            f"• [bold]Permissions:[/bold] {fmt('Manage Channels', f_perms.get('manage_channels'))}, {fmt('Manage Messages', f_perms.get('manage_messages'))},\n"
            f"  {fmt('Manage Roles', f_perms.get('manage_roles'))}, {fmt('Manage Emojis/Stickers', f_perms.get('manage_emojis_stickers'))}, {fmt('Manage Webhooks', f_perms.get('manage_webhooks'))}"
        )
        
        console.print("\n") # Add spacing before panel
        console.print(Panel(perm_table, title="[bold]Required Bot Permissions[/bold]", expand=False, border_style="dim"))

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

        console.print("\n(1) Edit tokens")
        console.print("(2) Edit API url (for self hosted instances)")
        console.print("(B) Back")
        
        menu_choice = Prompt.ask("\nSelect an option [1/2/B]", choices=["1", "2", "B", "b"], default="B", show_choices=False).upper()
        
        if menu_choice == "B":
            return
            
        if menu_choice == "2":
            console.print("[yellow]Not implemented yet.[/yellow]")
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
            self.engine = MigrationContext(self.config)
            
            # Re-validate
            console.print("[yellow]Validating new configuration...[/yellow]")
            await self.update_validation_status()

            console.print(f"\nDiscord Bot Token {get_status_str(self.validation_results.get('discord_token', False))}")
            console.print(f"Fluxer Bot Token {get_status_str(self.validation_results.get('fluxer_token', False))}")
            console.print(f"Discord Server ID {get_status_str(self.validation_results.get('discord_server', False))}")
            console.print(f"Fluxer Community ID {get_status_str(self.validation_results.get('fluxer_community', False))}")
            
            console.print("[bold green]Configuration updated and saved to fluxer.config.yaml![/bold green]")
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
            with console.status("[yellow]Syncing Fluxer channel state...[/yellow]"):
                await sync_channel_state(self.engine)
            categories = await self.engine.discord_reader.get_categories()
            channels = await self.engine.discord_reader.get_channels()
        except Exception as e:
            console.print(f"[bold red]Failed to fetch server structure: {e}[/bold red]")
            await self.engine.close_connections()
            return
            
        table = Table(show_header=True, header_style="bold #4641D9")
        table.add_column("Type", width=12)
        table.add_column("Discord Name")
        table.add_column("Status", justify="right")

        # Group channels by category for status check
        missing_by_cat = {}
        missing_uncategorized = []
        for ch in channels:
            cat_id = str(ch.category_id) if ch.category_id else None
            if not self.engine.state.get_fluxer_channel_id(str(ch.id)):
                if cat_id:
                    if cat_id not in missing_by_cat: missing_by_cat[cat_id] = []
                    missing_by_cat[cat_id].append(ch)
                else:
                    missing_uncategorized.append(ch)

        # Build the unified preview table
        for cat in categories:
            cat_id_str = str(cat.id)
            fluxer_cat_id = self.engine.state.get_fluxer_category_id(cat_id_str)
            
            status = f"[cyan]{fluxer_cat_id}[/cyan]" if fluxer_cat_id else "[bold red]Missing[/bold red]"
            table.add_row("[bold yellow]Category[/bold yellow]", f"[bold yellow]{cat.name}[/bold yellow]", status)
            
            # Show ALL channels under this category
            cat_channels = [ch for ch in channels if str(ch.category_id) == cat_id_str]
            for ch in cat_channels:
                fluxer_ch_id = self.engine.state.get_fluxer_channel_id(str(ch.id))
                ch_status = f"[cyan]{fluxer_ch_id}[/cyan]" if fluxer_ch_id else "[red]Missing[/red]"
                table.add_row("  Channel", ch.name, ch_status)
        
        # Show Uncategorized
        uncategorized = [ch for ch in channels if not ch.category_id]
        if uncategorized:
            table.add_row("Uncategorized", "", "")
            for ch in uncategorized:
                fluxer_ch_id = self.engine.state.get_fluxer_channel_id(str(ch.id))
                ch_status = f"[cyan]{fluxer_ch_id}[/cyan]" if fluxer_ch_id else "[red]Missing[/red]"
                table.add_row("  Channel", ch.name, ch_status)

        console.print(table)
        console.print("")

        # Check for existing mappings to determine if we should suggest a force re-copy
        cached_count = sum(1 for cat in categories if self.engine.state.get_fluxer_category_id(str(cat.id)))
        cached_count += sum(1 for ch in channels if self.engine.state.get_fluxer_channel_id(str(ch.id)))
        all_ids_len = len(categories) + len(channels)
        
        force = False
        if cached_count > 0:
            console.print(f"[yellow]\u26a0  {cached_count}/{all_ids_len} item(s) already in fluxer.state.json cache.[/yellow]")
            # End of table consolidated section, back to standard flow logic.

            console.print("[bold green](Y) Continue with only missing items[/bold green]")
            console.print("[bold red](F) Force re-clone, creates duplicate channels![/bold red]")
            console.print("[bold yellow](B) Back[/bold yellow]")
            
            choice = Prompt.ask("Select an option [Y/F/B]", choices=["Y", "y", "F", "f", "B", "b"], default="Y", show_choices=False).upper()
            
            if choice == "B":
                await self.engine.close_connections()
                return
            elif choice == "F":
                force = True
            # if 'Y', force remains False and we continue
        else:
            if not Confirm.ask("Clone channels and categories?", default=True):
                await self.engine.close_connections()
                return
            if not Confirm.ask("Are you sure?", default=True):
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
                
                async def update_progress(item_name: str, status: str, current: int, total: int):
                    color = "cyan" if status == "Copying" else "yellow"
                    progress.update(channel_task, total=total, completed=current, description=f"[{color}]{status} Channel: {item_name}")

                self.engine.is_running = True
                cloned_info = await migrate_channels(self.engine, progress_callback=update_progress, force=force)
                
            console.print("[bold green]Server Template cloned![/bold green]")
            
            if cloned_info and cloned_info.get("structure"):
                lines = ["Successfully cloned channels and categories from Discord:"]
                # Sort categories but keep "No Category" at the bottom if it exists
                cats = sorted(cloned_info["structure"].keys(), key=lambda x: (x == "No Category", x))
                for cat_name in cats:
                    channel_names = cloned_info["structure"][cat_name]
                    # Only print if the category itself was created OR it has channels created under it
                    if cat_name in cloned_info["categories_created"] or channel_names:
                        lines.append(f"- **{cat_name}**")
                        for ch_name in sorted(channel_names):
                            lines.append(f"  - {ch_name}")
                
                audit_desc = "\n".join(lines)
                await log_audit_event(self.engine, "Server Template Cloned", audit_desc)
            else:
                await log_audit_event(self.engine, "Server Template Cloned", "No new channels or categories were cloned.")
            
        except Exception as e:
            console.print(f"[bold red]Error during channel clone: {str(e)}[/bold red]")
        finally:
            await self.engine.close_connections()
            self.engine.is_running = False

    async def copy_roles(self):
        console.print("\n[bold]Role & Permission Options[/bold]")
        console.print("(1) Clone Roles & Role Permissions")
        console.print("(2) Sync Category Permissions & Channel Permissions")
        console.print("(B) Back")
        
        choice = Prompt.ask("Select an option [1/2/B]", choices=["1", "2", "B", "b"], default="B", show_choices=False).upper()
        
        if choice == "B":
            return

        if choice == "1":
            try:
                await self.engine.start_connections()
                
                with console.status("[yellow]Checking Fluxer for existing roles...[/yellow]"):
                    await sync_roles_state(self.engine)
                
                roles = await self.engine.discord_reader.get_roles()
                
                table = Table(show_header=True, header_style="bold #4641D9")
                table.add_column("Discord Role")
                table.add_column("Status", justify="center")
                table.add_column("Fluxer ID", justify="right")

                cached_count = 0
                for r in roles:
                    fluxer_id = self.engine.state.get_fluxer_role_id(str(r.id))
                    status = "[bold green]NEW[/bold green]"
                    fid_str = "[dim]N/A[/dim]"
                    if fluxer_id:
                        status = "[dim]already copied[/dim]"
                        fid_str = f"[cyan]{fluxer_id}[/cyan]"
                        cached_count += 1
                    table.add_row(r.name, status, fid_str)
                
                console.print(table)

                force = False
                if cached_count > 0:
                    console.print(f"\n[yellow]{cached_count} role(s) already in state cache.[/yellow]")
                    console.print("[bold green](Y) Sync missing roles only[/bold green]")
                    console.print("[bold red](F) Force Overwrite[/bold red]")
                    console.print("[bold yellow](B) Back[/bold yellow]")
                    
                    sub_choice = Prompt.ask("Select an option [Y/F/B]", choices=["Y", "y", "F", "f", "B", "b"], default="Y", show_choices=False).upper()
                    
                    if sub_choice == "B":
                        return
                    elif sub_choice == "F":
                        force = True
                else:
                    if not Confirm.ask("Clone roles?", default=True):
                        await self.engine.close_connections()
                        return
                    if not Confirm.ask("Are you sure?", default=True):
                        await self.engine.close_connections()
                        return

                console.print("\n[bold green]Starting Role Migration...[/bold green]")
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    console=console
                ) as progress:
                    
                    role_task = progress.add_task("[cyan]Syncing Roles...", total=100)
                    
                    async def update_progress(item_name: str, current: int, total: int):
                        progress.update(role_task, total=total, completed=current, description=f"[cyan]Syncing Role: {item_name}")
    
                    self.engine.is_running = True
                    cloned_roles = await migrate_roles(self.engine, progress_callback=update_progress, force=force)
                    
                console.print("[bold green]Role migration complete![/bold green]")
                if cloned_roles:
                    audit_desc = "Successfully cloned these roles and their baseline permissions:\n" + "\n".join(f"- {r}" for r in cloned_roles)
                    await log_audit_event(self.engine, "Roles Cloned", audit_desc)
                else:
                    await log_audit_event(self.engine, "Roles Cloned", "No new roles were cloned.")
                
            except Exception as e:
                console.print(f"[bold red]Error during role migration: {str(e)}[/bold red]")
            finally:
                await self.engine.close_connections()
                self.engine.is_running = False
        
        elif choice == "2":
            try:
                await self.engine.start_connections()
                
                with console.status("[yellow]Analyzing categories and channels...[/yellow]"):
                    categories = await self.engine.discord_reader.get_categories()
                    channels = await self.engine.discord_reader.get_channels()
                
                mapped_cats = sum(1 for c in categories if self.engine.state.get_fluxer_category_id(str(c.id)))
                mapped_chs = sum(1 for c in channels if self.engine.state.get_fluxer_channel_id(str(c.id)))
                total_mapped = mapped_cats + mapped_chs
                
                console.print(f"\n[yellow]Ready to sync permissions for {total_mapped} items ({mapped_cats} categories, {mapped_chs} channels).[/yellow]")
                console.print("[bold green](Y) Proceed with Permission synchronization[/bold green]")
                console.print("[bold yellow](B) Back[/bold yellow]")
                
                sub_choice = Prompt.ask("Select an option [Y/B]", choices=["Y", "y", "B", "b"], default="Y", show_choices=False).upper()
                
                if sub_choice == "B":
                    return

                console.print("\n[bold green]Syncing Category & Channel Permissions...[/bold green]")
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
    
                    self.engine.is_running = True
                    synced_info = await sync_permissions(self.engine, progress_callback=update_progress)
                    
                console.print("[bold green]Permission synchronization complete![/bold green]")
                
                if synced_info and synced_info.get("structure"):
                    lines = ["Successfully synchronized channel and category permission overrides:"]
                    # Sort categories but keep "No Category" at the bottom
                    cats = sorted(synced_info["structure"].keys(), key=lambda x: (x == "No Category", x))
                    for cat_name in cats:
                        channel_names = synced_info["structure"][cat_name]
                        # For permissions, we show everything that was successfully synced
                        if cat_name in synced_info["categories_synced"] or channel_names:
                            lines.append(f"- **{cat_name}**")
                            for ch_name in sorted(channel_names):
                                lines.append(f"  - {ch_name}")
                    
                    audit_desc = "\n".join(lines)
                    await log_audit_event(self.engine, "Permissions Synced", audit_desc)
                else:
                    await log_audit_event(self.engine, "Permissions Synced", "No permissions were synchronized.")
                
            except Exception as e:
                console.print(f"[bold red]Error during permission sync: {str(e)}[/bold red]")
            finally:
                await self.engine.close_connections()
                self.engine.is_running = False

    async def copy_emojis(self):
        console.print("\n[yellow]Fetching emojis and stickers...[/yellow]")
        try:
            await self.engine.start_connections()
            
            with console.status("[yellow]Checking Fluxer for existing emojis and stickers...[/yellow]"):
                await sync_assets_state(self.engine)
                
            emojis = await self.engine.discord_reader.get_emojis()
            stickers = await self.engine.discord_reader.get_stickers()

            table = Table(show_header=True, header_style="bold #4641D9")
            table.add_column("Type", width=10)
            table.add_column("Name")
            table.add_column("Status", justify="right")

            cached_emojis = 0
            for e in emojis:
                already = self.engine.state.get_fluxer_emoji_id(str(e.id))
                status = "[dim]already copied[/dim]" if already else "[bold green]NEW[/bold green]"
                if already: cached_emojis += 1
                table.add_row("Emoji", e.name, status)

            cached_stickers = 0
            for s in stickers:
                already = self.engine.state.get_fluxer_sticker_id(str(s.id))
                status = "[dim]already copied[/dim]" if already else "[bold green]NEW[/bold green]"
                if already: cached_stickers += 1
                table.add_row("Sticker", s.name, status)

            console.print(table)

            # Warn if everything is already in the state cache
            force = False
            total_items = len(emojis) + len(stickers)
            cached_count = cached_emojis + cached_stickers



            console.print("\n(1) Sync Emojis only")
            console.print("(2) Sync Stickers only")
            console.print("(3) Sync Emojis and Stickers")
            console.print("(B) Back")

            choice = Prompt.ask("Select an option [1/2/3/B]", choices=["1", "2", "3", "B", "b"], default="B", show_choices=False).upper()

            if choice == "B":
                return

            types_to_include = []
            if choice == "1":
                types_to_include = ["Emoji"]
            elif choice == "2":
                types_to_include = ["Sticker"]
            elif choice == "3":
                types_to_include = ["Emoji", "Sticker"]

            # Ask about force re-copy only if there are cached items in scope
            cached_in_scope = 0
            if "Emoji" in types_to_include:
                cached_in_scope += sum(1 for e in emojis if self.engine.state.get_fluxer_emoji_id(str(e.id)))
            if "Sticker" in types_to_include:
                cached_in_scope += sum(1 for s in stickers if self.engine.state.get_fluxer_sticker_id(str(s.id)))

            force = False
            if cached_in_scope > 0:
                console.print(f"\n[yellow]{cached_in_scope} item(s) already in state cache.[/yellow]")
                console.print("[bold green](Y) Copy missing items only[/bold green]")
                console.print("[bold red](F) Force Overwrite[/bold red]")
                console.print("[bold yellow](B) Back[/bold yellow]")
                
                choice = Prompt.ask("Select an option [Y/F/B]", choices=["Y", "y", "F", "f", "B", "b"], default="Y", show_choices=False).upper()
                
                if choice == "B":
                    return
                elif choice == "F":
                    force = True
                # if 'Y', force remains False and we continue

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
                cloned_assets = await migrate_emojis(
                    self.engine,
                    progress_callback=update_progress,
                    types_to_include=types_to_include,
                    force=force
                )

            console.print("[bold green]Migration complete![/bold green]")
            if cloned_assets and (cloned_assets.get("Emoji") or cloned_assets.get("Sticker")):
                lines = []
                if cloned_assets.get("Emoji"):
                    lines.append("Successfully cloned these emojis:")
                    for name, e_id in cloned_assets["Emoji"].items():
                        lines.append(f"- {name} <:{name}:{e_id}>")
                if cloned_assets.get("Sticker"):
                    if lines: lines.append("")
                    lines.append("Successfully cloned these stickers:")
                    for name, s_id in cloned_assets["Sticker"].items():
                        lines.append(f"- {name}")
                
                audit_desc = "\n".join(lines)
                await log_audit_event(self.engine, "Emojis & Stickers Cloned", audit_desc)
            else:
                await log_audit_event(self.engine, "Emojis & Stickers Cloned", "No new emojis or stickers were cloned.")

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
            
            banner_status = "[bold green]FOUND[/bold green]" if metadata.get("banner_url") else "[yellow]NOT FOUND[/yellow]"
            
            table = Table(show_header=False, box=None)
            table.add_column("Property", style="bold")
            table.add_column("Value")
            
            table.add_row("Server Name:", name)
            table.add_row("Server Icon:", icon_status)
            table.add_row("Server Banner:", banner_status)
            
            console.print(Panel(table, title="[bold]Discord Server Metadata[/bold]", expand=False))
            
            console.print("\n(1) Sync Name only")
            console.print("(2) Sync Icon only")
            console.print("(3) Sync Banner only")
            console.print("(4) Sync Everything")
            console.print("(B) Back")
            
            choice = Prompt.ask("Select an option [1/2/3/4/B]", choices=["1", "2", "3", "4", "B", "b"], default="B", show_choices=False).upper()
            
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

            cloned_data = await sync_server_metadata(self.engine, progress_callback, components=components)
            console.print("[bold green]Server profile sync finished![/bold green]")

            audit_lines = ["Successfully synchronized Community profile:"]
            if "name" in cloned_data:
                audit_lines.append(f"- **Name**: {cloned_data['name']}")
            if "icon" in cloned_data:
                audit_lines.append(f"- **Icon**")
            if "banner" in cloned_data:
                audit_lines.append(f"- **Banner**")
                
            files = []
            if "icon" in cloned_data:
                icon_ext = "gif" if cloned_data["icon"].startswith(b"GIF") else "png"
                files.append({
                    "filename": f"icon.{icon_ext}",
                    "data": cloned_data["icon"]
                })
            if "banner" in cloned_data:
                banner_ext = "gif" if cloned_data["banner"].startswith(b"GIF") else "png"
                files.append({
                    "filename": f"banner.{banner_ext}",
                    "data": cloned_data["banner"]
                })
                
            audit_desc = "\n".join(audit_lines)
            if cloned_data:
                await log_audit_event(self.engine, "Server Profile Synced", audit_desc, files=files)
            else:
                await log_audit_event(self.engine, "Server Profile Synced", "No profile information was synchronized.")
        except Exception as e:
            console.print(f"[bold red]Error during metadata sync: {str(e)}[/bold red]")
        finally:
            await self.engine.close_connections()

    async def migrate_message_history(self):
        console.print("\n[bold]Message History Migration[/bold]")
        
        if not self.tokens_valid:
             console.print("[bold red]Error: You must have a valid configuration (Option 6) to migrate messages.[/bold red]")
             return

        try:
            with console.status("[yellow]Fetching Discord channels...[/yellow]"):
                await self.engine.start_connections()
                d_channels = await self.engine.discord_reader.get_channels()
            
            if not d_channels:
                console.print("[yellow]No text channels found in Discord server.[/yellow]")
                return
            
            console.print("\n[bold]Select Source Discord Channel:[/bold]")
            for i, ch in enumerate(d_channels):
                console.print(f"({i+1}) {ch.name}")
            
            console.print("(B) Back")
            d_choices = [str(i+1) for i in range(len(d_channels))] + ["B", "b"]
            
            prompt_msg = f"Select Discord Channel [[bold cyan](1-{len(d_channels)})/B[/bold cyan]]"
            d_choice = Prompt.ask(prompt_msg, choices=d_choices, show_choices=False).upper()
            
            if d_choice == "B":
                return
            
            source_channel = d_channels[int(d_choice) - 1]

            # 2. Select Target Fluxer Channel
            with console.status("[yellow]Fetching Fluxer channels...[/yellow]"):
                f_channels = await self.engine.fluxer_writer.get_channels()
            if not f_channels:
                console.print("[yellow]No channels found in Fluxer community.[/yellow]")
                return

            # Determine recommended channel
            recommended_channel = None
            # Priority 1: Check state.json mapping
            mapped_id = self.engine.state.get_fluxer_channel_id(str(source_channel.id))
            if mapped_id:
                recommended_channel = next((c for c in f_channels if str(c.get('id', '')) == mapped_id), None)
            
            # Priority 2: Check name match
            if not recommended_channel:
                recommended_channel = next((c for c in f_channels if c.get('name') == source_channel.name), None)
                
            console.print("\n[bold]Select Target Fluxer Channel:[/bold]")
            
            for i, ch in enumerate(f_channels):
                console.print(f"({i+1}) {ch.get('name', 'Unnamed Channel')}")

            f_choices = [str(i+1) for i in range(len(f_channels))] + ["B", "b", "N", "n"]
            
            if recommended_channel:
                console.print(f"(Y) [bold green]{recommended_channel.get('name')}[/bold green] (auto-matched)")
                f_choices += ["Y", "y"]

            console.print("(N) Create new channel")
            console.print("(B) Back")

            # Build simplified prompt string
            prompt_parts = [f"(1-{len(f_channels)})", "B", "N"]
            if recommended_channel:
                prompt_parts.append("Y")
            
            prompt_msg = f"Select Fluxer Channel [[bold cyan]{'/'.join(prompt_parts)}[/bold cyan]]"
            
            f_choice = Prompt.ask(prompt_msg, choices=f_choices, show_choices=False, default="Y" if recommended_channel else None).upper()
            
            if f_choice == "B":
                return
            
            if f_choice == "Y" and recommended_channel:
                target_channel = recommended_channel
            elif f_choice == "N":
                # Check if a channel with this name already exists
                target_channel = next((c for c in f_channels if c.get('name') == source_channel.name), None)
                if not target_channel:
                    with console.status(f"[yellow]Creating Fluxer channel #{source_channel.name}...[/yellow]"):
                        parent_id = None
                        if source_channel.category_id:
                            parent_id = self.engine.state.get_fluxer_category_id(str(source_channel.category_id))
                        
                        topic = getattr(source_channel, 'topic', "") or ""
                        nsfw = getattr(source_channel, 'nsfw', False)
                        slowmode = getattr(source_channel, 'slowmode_delay', 0)
                        
                        new_id = await self.engine.fluxer_writer.create_channel(
                            name=source_channel.name,
                            topic=topic,
                            type=0,
                            parent_id=parent_id,
                            nsfw=nsfw,
                            slowmode_delay=slowmode
                        )
                        if new_id:
                            self.engine.state.set_channel_mapping(str(source_channel.id), new_id)
                            # Refresh list to get the channel object
                            f_channels = await self.engine.fluxer_writer.get_channels()
                            target_channel = next((c for c in f_channels if str(c.get('id')) == new_id), None)
                        else:
                            console.print("[bold red]Failed to create channel.[/bold red]")
                            return
            else:
                target_channel = f_channels[int(f_choice) - 1]

            # 3. Handle Starting Message
            with console.status("[yellow]Fetching first message...[/yellow]"):
                first_msg = await self.engine.discord_reader.get_first_message(source_channel.id)
            after_id = None
            
            if first_msg:
                # Link format: https://discord.com/channels/GUILD_ID/CHANNEL_ID/MESSAGE_ID
                first_msg_link = f"https://discord.com/channels/{self.config.discord_server_id}/{source_channel.id}/{first_msg.id}"
                server_name = self.validation_results.get("discord_server_name", "server")
                
                # Check for existing migration
                last_migrated_id = self.engine.state.get_last_message_id(str(source_channel.id))
                next_msg = None
                if last_migrated_id:
                    try:
                        # Try to fetch the immediate next message
                        async for msg in self.engine.discord_reader.fetch_message_history(source_channel.id, limit=1, after_id=int(last_migrated_id)):
                            next_msg = msg
                            break
                    except Exception as e:
                        # Assuming logger is defined elsewhere, if not, this would be an error.
                        # For this context, I'll assume it's available or a print is acceptable.
                        # If not, a simple print(f"Warning: Could not fetch next message after {last_migrated_id}: {e}") could be used.
                        # As per instructions, I'll use logger.warning.
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.warning(f"Could not fetch next message after {last_migrated_id}: {e}")

                while True:
                    console.print(f"\n[bold green]Channel Found![/bold green]")
                    console.print(f"Oldest Message")
                    console.print(f"Link: {first_msg_link}")
                    console.print(f"Author: [blue]{first_msg.author.name}[/blue]")
                    console.print(f"Content Preview: {first_msg.content[:100]}")
                    
                    choices = ["M", "m", "B", "b"]
                    prompt_str = "Start migration"
                    
                    if next_msg:
                        next_msg_link = f"https://discord.com/channels/{self.config.discord_server_id}/{source_channel.id}/{next_msg.id}"
                        console.print(f"\n[bold yellow]Continue Next message[/bold yellow]")
                        console.print(f"Link: {next_msg_link}")
                        console.print(f"Author: [blue]{next_msg.author.name}[/blue]")
                        console.print(f"Content Preview: {next_msg.content[:100]}")
                        
                        console.print("\n(F) [red]Force start from oldest message[/red]")
                        console.print("(M) Provide Specific message link or ID")
                        console.print("(Y) [green]Continue Migration[/green]")
                        console.print("(B) Back")
                        
                        choices.extend(["Y", "y", "F", "f"])
                        prompt_str = "Select an option [Y/F/M/B]"
                        default_choice = "Y"
                    else:
                        console.print("\n(Y) [green]Yes, start from oldest message[/green]")
                        console.print("(M) Provide Specific message link or ID")
                        console.print("(B) Back")
                        
                        choices.extend(["Y", "y"])
                        prompt_str = "Start migration [Y/M/B]"
                        default_choice = "Y"
                    
                    start_mode = Prompt.ask(prompt_str, choices=choices, default=default_choice, show_choices=False).upper()
                    
                    if start_mode == "B":
                        return
                    elif start_mode == "Y":
                        if next_msg:
                            after_id = int(last_migrated_id)
                        break
                    elif start_mode == "F":
                        # User wants to ignore the previous migration and start from beginning
                        after_id = None
                        break
                    elif start_mode == "M":
                        prompt_msg = "Enter Discord Message Link or Message ID (or 'B' to go back)"
                        valid_message_found = False
                        while True:
                            custom_link = Prompt.ask(prompt_msg)
                            if str(custom_link).upper() in ["B", "BACK"]:
                                break
                            try:
                                # Extract message ID from end of link
                                custom_id = int(str(custom_link).strip().split("/")[-1])
                                # Check if message exists to give feedback
                                msg = await self.engine.discord_reader.get_message(source_channel.id, custom_id)
                                if msg:
                                    # To include this message, we start 'after' the ID before it
                                    after_id = custom_id - 1
                                    console.print(f"\n[green]Confirmed: Starting from {msg.author.name}'s message.[/green]")
                                    valid_message_found = True
                                    break
                                else:
                                    console.print("[red]Could not find message. Ensure it exists in this channel.[/red]")
                            except ValueError:
                                console.print("[red]Invalid ID or Link. Please provide a valid numeric ID or Discord Message URL.[/red]")
                            except Exception as e:
                                console.print(f"[red]Error fetching message: {str(e)}[/red]")
                                
                        if valid_message_found:
                            break
            else:
                console.print("[yellow]Source channel appears to be empty. Nothing to migrate.[/yellow]")
                return

            # 4. Analysis and Confirmation
            console.print("\n[yellow]Analyzing channel content...[/yellow]")
            self.engine.is_running = True
            stats = {"messages": 0, "threads": 0, "attachments": 0}
            try:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console
                ) as progress:
                    task = progress.add_task("[cyan]Scanning history...", total=None)
                    async def update_scan_progress(count: int):
                        progress.update(task, description=f"[cyan]Scanned {count} items...")
                    
                    stats = await analyze_migration(
                        self.engine,
                        source_channel_id=source_channel.id,
                        after_message_id=after_id,
                        progress_callback=update_scan_progress
                    )
            finally:
                self.engine.is_running = False

            table = Table(show_header=True, header_style="bold #4641D9", title="Migration Summary & Estimates")
            table.add_column("Item", width=15)
            table.add_column("Count", justify="right", width=10)
            table.add_column("Overhead/Details")

            msg_time = stats['messages'] * self.config.migration.rate_limit_delay_seconds
            table.add_row(
                "Messages", 
                f"[bold cyan]{stats['messages']}[/bold cyan]", 
                f"~{msg_time}s delay (rate limiting), {stats['messages']} API writes"
            )
            table.add_row(
                "Threads", 
                f"[bold cyan]{stats['threads']}[/bold cyan]", 
                f"{stats['threads'] * 2} extra marker messages, {stats['threads']} extra history fetches"
            )
            table.add_row(
                "Attachments", 
                f"[bold cyan]{stats['attachments']}[/bold cyan]", 
                f"{stats['attachments']} downloads and uploads (bandwidth & API calls)"
            )

            console.print("")
            console.print(table)

            if not Confirm.ask(f"\nMigrate messages from Discord [cyan]#{source_channel.name}[/cyan] to Fluxer [#4641D9]#{target_channel.get('name')}[/#4641D9]?"):
                return
            
            # 5. Migration Execution
            console.print("\n[bold green]Starting Migration...[/bold green]")
            self.engine.is_running = True
            
            total_messages = stats['messages']
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console
            ) as progress:
                task = progress.add_task(f"[cyan]Migrating 0/{total_messages} messages...", total=total_messages)
                
                async def update_msg_progress(count: int):
                    progress.update(task, completed=count, description=f"[cyan]Migrated {count}/{total_messages} messages...")

                result_stats = await migrate_messages(
                    self.engine,
                    source_channel_id=source_channel.id,
                    target_channel_id=target_channel.get("id"),
                    after_message_id=after_id,
                    progress_callback=update_msg_progress
                )
                
            console.print(f"\n[bold green]Success! {result_stats['messages']} messages migrated to {target_channel.get('name')}.[/bold green]")
            
            lines = [f"Successfully migrated messages from Discord [cyan]#{source_channel.name}[/cyan] to [blue]#{target_channel.get('name')}[/blue]:"]
            lines.append(f"\n**Stats:**")
            lines.append(f"- {result_stats['messages']} messages")
            lines.append(f"- {result_stats['attachments']} attachments")
            lines.append(f"- {result_stats['threads']} threads")
            
            if result_stats.get('first_message_url') or result_stats.get('last_message_url'):
                lines.append(f"\n**Message Info:**")
                if result_stats.get('first_message_url'):
                    lines.append(f"- First message: <{result_stats['first_message_url']}>")
                if result_stats.get('last_message_url'):
                    lines.append(f"- Last message: <{result_stats['last_message_url']}>")
            
            audit_desc = "\n".join(lines)
            await log_audit_event(self.engine, "Message History Migrated", audit_desc)

        except Exception as e:
            console.print(f"[bold red]Migration encountered an error: {str(e)}[/bold red]")
        finally:
            self.engine.is_running = False
            await self.engine.close_connections()

    async def danger_zone(self):
        """Danger Zone – irreversible destructive operations on the Fluxer community."""
        console.print("")
        console.print(Panel.fit(
            "[bold red]\u26a0  DANGER ZONE \u26a0[/bold red]\n"
            "[yellow]These actions are PERMANENT and IRREVERSIBLE on your Fluxer community.[/yellow]\n"
            "[yellow]Always double-check before confirming.[/yellow]",
            style="bold red"
        ))
        console.print("(1) Delete all Channels & Categories")
        console.print("(2) Reset Channel & Category Permissions")
        console.print("(3) Delete all Roles  [dim](bot role is protected)[/dim]")
        console.print("(4) Delete all custom Emojis & Stickers")
        console.print("(B) Back")

        choice = Prompt.ask(
            "Select a Danger Zone option [1/2/3/4/B]",
            choices=["1", "2", "3", "4", "B", "b"],
            default="B",
            show_choices=False
        ).upper()

        if choice == "B":
            return

        # ---- (1) Delete all Channels & Categories ----
        if choice == "1":
            console.print("")
            community_name = self.validation_results.get("fluxer_community_name", "Unknown")
            console.print(f"[bold red]This will DELETE every channel and category in the Fluxer community: \"{community_name}\"[/bold red]")
            if not Confirm.ask("[bold red]Are you absolutely sure?[/bold red]"):
                return
            if not Confirm.ask("[bold red]Last chance \u2013 this cannot be undone. Continue?[/bold red]"):
                return
            try:
                await self.engine.start_fluxer_only()
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    console=console
                ) as progress:
                    del_task = progress.add_task("[red]Deleting channels...", total=100)

                    async def on_channel_deleted(name: str, current: int, total: int):
                        progress.update(del_task, total=total, completed=current,
                                        description=f"[red]Deleting: {name}")

                    count = await danger_delete_all_channels(self.engine, progress_callback=on_channel_deleted)
                console.print(f"[bold green]{count} channels/categories deleted.[/bold green]")
                await log_audit_event(self.engine, "Danger Zone: Channels Wiped", f"Deleted {count} channels and categories from the community.")
                console.print("[bold green]Done.[/bold green]")
            except Exception as e:
                console.print(f"[bold red]Error: {e}[/bold red]")
            finally:
                await self.engine.close_fluxer_only()

        # ---- (2) Reset Channel & Category Permissions ----
        elif choice == "2":
            console.print("")
            community_name = self.validation_results.get("fluxer_community_name", "Unknown")
            console.print(f"[bold red]This will RESET all permission overwrites on every channel and category in the Fluxer community: \"{community_name}\"[/bold red]")
            if not Confirm.ask("[bold red]Are you absolutely sure?[/bold red]"):
                return
            if not Confirm.ask("[bold red]Last chance \u2013 all custom permission overrides will be wiped. Continue?[/bold red]"):
                return
            try:
                await self.engine.start_fluxer_only()
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    console=console
                ) as progress:
                    perm_task = progress.add_task("[red]Resetting permissions...", total=100)

                    async def on_perm_reset(name: str, current: int, total: int):
                        progress.update(perm_task, total=total, completed=current,
                                        description=f"[red]Resetting: {name}")

                    count = await danger_reset_channel_permissions(self.engine, progress_callback=on_perm_reset)
                console.print(f"[bold green]Permissions reset on {count} channels/categories.[/bold green]")
                await log_audit_event(self.engine, "Danger Zone: Permissions Wiped", f"Administrators reset permissions on {count} channels and categories.")
                console.print("[bold green]Done.[/bold green]")
            except Exception as e:
                console.print(f"[bold red]Error: {e}[/bold red]")
            finally:
                await self.engine.close_fluxer_only()

        # ---- (3) Delete all Roles ----
        elif choice == "3":
            console.print("")
            community_name = self.validation_results.get("fluxer_community_name", "Unknown")
            console.print(f"[bold red]This will DELETE all roles in the Fluxer community: \"{community_name}\"[/bold red]")
            console.print("[dim]Managed roles (including the bot's own role) and @everyone are automatically protected.[/dim]")
            if not Confirm.ask("[bold red]Are you absolutely sure?[/bold red]"):
                return
            if not Confirm.ask("[bold red]Last chance \u2013 confirm permanent role deletion?[/bold red]"):
                return
            try:
                await self.engine.start_fluxer_only()
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    console=console
                ) as progress:
                    role_task = progress.add_task("[red]Deleting roles...", total=100)

                    async def on_role_deleted(name: str, current: int, total: int):
                        progress.update(role_task, total=total, completed=current,
                                        description=f"[red]Deleting role: {name}")

                    count = await danger_delete_all_roles(self.engine, progress_callback=on_role_deleted)
                console.print(f"[bold green]{count} roles deleted.[/bold green]")
                await log_audit_event(self.engine, "Danger Zone: Roles Wiped", f"Deleted {count} roles from the community.")
                console.print("[bold green]Done.[/bold green]")
            except Exception as e:
                console.print(f"[bold red]Error: {e}[/bold red]")
            finally:
                await self.engine.close_fluxer_only()

        # ---- (4) Delete all Emojis & Stickers ----
        elif choice == "4":
            console.print("")
            community_name = self.validation_results.get("fluxer_community_name", "Unknown")
            console.print(f"[bold red]This will DELETE all custom emojis and stickers in the Fluxer community: \"{community_name}\"[/bold red]")
            if not Confirm.ask("[bold red]Are you absolutely sure?[/bold red]"):
                return
            if not Confirm.ask("[bold red]Last chance \u2013 this cannot be undone. Continue?[/bold red]"):
                return
            try:
                await self.engine.start_fluxer_only()
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    console=console
                ) as progress:
                    asset_task = progress.add_task("[red]Deleting assets...", total=100)

                    async def on_asset_deleted(name: str, asset_type: str, current: int, total: int):
                        progress.update(asset_task, total=total, completed=current,
                                        description=f"[red]Deleting {asset_type}: {name}")

                    counts = await danger_delete_all_emojis_and_stickers(self.engine, progress_callback=on_asset_deleted)
                console.print(f"[bold green]{counts.get('emojis', 0)} emojis deleted.[/bold green]")
                console.print(f"[bold green]{counts.get('stickers', 0)} stickers deleted.[/bold green]")
                await log_audit_event(self.engine, "Danger Zone: Assets Wiped", f"Deleted {counts.get('emojis', 0)} emojis and {counts.get('stickers', 0)} stickers.")
                console.print("[bold green]Done.[/bold green]")
            except Exception as e:
                console.print(f"[bold red]Error: {e}[/bold red]")
            finally:
                await self.engine.close_fluxer_only()



async def run_cli():
    cli = MigrationCLI()
    await cli.run()
