"""
ShuttlePane – self-contained shuttle (migration) operations widget.
Embedded inside ModeScreen's "Migrate" tab.
"""

import asyncio
import discord
import logging
import re
import time
import aiohttp
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Vertical, VerticalScroll
from textual.widgets import Button, Label, Rule
from textual import work

from src.core.configuration import load_config
from src.core.base import MigrationContext
from src.core.audit import log_audit_event
from src.ui.modals import (
    ProgressScreen, SubMenuModal, ChannelPickerScreen, OptionSelectModal,
)

import src.fluxer.roles_permissions as fluxer_roles
import src.stoat.roles_permissions as stoat_roles
import src.fluxer.emoji_stickers as fluxer_emoji_stickers
import src.stoat.emoji_stickers as stoat_emoji_stickers
import src.fluxer.server_metadata as fluxer_metadata
import src.stoat.server_metadata as stoat_metadata
import src.fluxer.migrate_message as fluxer_migrate
import src.stoat.migrate_message as stoat_migrate


# ---------------------------------------------------------------------------
# Rate-limit handler (global, shared with logging subsystem)
# ---------------------------------------------------------------------------
global_rate_limit_msg = ""
global_rate_limit_expires = 0.0


class RateLimitHandler(logging.Handler):
    """Intercepts library logs to capture rate-limit messages."""

    def __init__(self):
        super().__init__()

    def emit(self, record):
        global global_rate_limit_msg, global_rate_limit_expires
        msg = record.getMessage()
        if "rate" in msg.lower() and "limit" in msg.lower():
            global_rate_limit_msg = msg
            try:
                parts = msg.split()
                for i, p in enumerate(parts):
                    if p.lower() in ("retry_after", "retry"):
                        secs = float(parts[i + 1].strip("s,."))
                        global_rate_limit_expires = time.time() + secs
                        break
                    if p.lower() == "after":
                        secs = float(parts[i + 1].strip("s,."))
                        global_rate_limit_expires = time.time() + secs
                        break
                else:
                    for p in parts:
                        try:
                            secs = float(p.strip("s,."))
                            if 0 < secs < 3600:
                                global_rate_limit_expires = time.time() + secs
                                break
                        except ValueError:
                            continue
            except Exception:
                pass


class ShuttlePane(Container):
    """Shuttle (migration) operations pane — clone, roles, emojis, metadata, messages, danger zone."""

    DEFAULT_CSS = """
    ShuttlePane { height: auto; width: 100%; }
    ShuttlePane #sp_info {
        height: auto; border: tall cyan; padding: 1; margin-bottom: 1;
    }
    ShuttlePane #sp_actions { height: auto; }
    ShuttlePane #sp_actions Button { width: 100%; margin-bottom: 1; }
    """

    def __init__(self, cfg_name: str, cfg_path: Path, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cfg_name = cfg_name
        self.config_path = cfg_path
        self.config = load_config(cfg_path)
        self.target_platform = self.config.target_platform or "fluxer"
        self.engine: MigrationContext | None = None
        self.validation_results: dict = {}
        self.tokens_valid = False
        self.permissions_complete = False

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            with Vertical(id="sp_info"):
                yield Label("Discord: [yellow]Loading...[/yellow]", id="sp_lbl_discord")
                yield Label("Target: [yellow]Loading...[/yellow]", id="sp_lbl_target")
                yield Label("Status: [yellow]Validating...[/yellow]", id="sp_lbl_status")
            with Vertical(id="sp_actions"):
                yield Button("Clone Server Template", id="sp_clone", disabled=True)
                yield Button("Sync Server Settings", id="sp_sync", disabled=True)
                yield Button("Migrate Message History", id="sp_messages", disabled=True)
                yield Rule()
                yield Button("Danger Zone ⚠", id="sp_danger", variant="error", disabled=True)

    def on_mount(self) -> None:
        self._rebuild_engine()
        self.run_validate()

    def reload_config(self) -> None:
        self.config = load_config(self.config_path)
        self.target_platform = self.config.target_platform or "fluxer"
        self._rebuild_engine()
        self.run_validate()

    def _base_dir(self) -> str:
        return f"Reaper-{self.cfg_name}"

    def _rebuild_engine(self):
        self.engine = MigrationContext(self.config, self.target_platform)

    # ── labels ────────────────────────────────────────────────────────────

    def _update_info_labels(self):
        v = self.validation_results

        # Discord
        d_name = v.get("discord_server_name")
        d_bot = v.get("discord_bot_name")
        if v.get("discord_timeout"):
            d_disp = "[red]TIMEOUT[/red]"
        elif d_name and v.get("discord_token") and v.get("discord_server"):
            d_disp = f'[green]"{d_name}"[/green]  Bot: [green]{d_bot}[/green]'
        elif v.get("discord_token") is False:
            d_disp = "[red]INVALID TOKEN[/red]"
        else:
            d_disp = "[red]NOT SET UP[/red]"
        self.query_one("#sp_lbl_discord", Label).update(f"Discord: {d_disp}")

        # Target
        plat = "Fluxer" if self.target_platform == "fluxer" else "Stoat"
        t_name = v.get("target_community_name")
        if v.get("target_timeout"):
            t_disp = "[red]TIMEOUT[/red]"
        elif t_name and v.get("target_token") and v.get("target_community"):
            t_disp = f'[green]"{t_name}"[/green]'
        elif v.get("target_token") is False:
            t_disp = "[red]INVALID TOKEN[/red]"
        else:
            t_disp = "[red]NOT SET UP[/red]"
        self.query_one("#sp_lbl_target", Label).update(f"{plat}: {t_disp}")

        # Status
        if not self.tokens_valid:
            val = "[red][INVALID][/red]"
        elif not self.permissions_complete:
            val = "[yellow][MISSING PERMISSIONS][/yellow]"
        else:
            val = "[green][VALID][/green]"
        self.query_one("#sp_lbl_status", Label).update(f"Status: {val}")

        # Buttons
        for bid in ("#sp_clone", "#sp_sync", "#sp_messages", "#sp_danger"):
            self.query_one(bid, Button).disabled = not self.tokens_valid

    # ── validation ────────────────────────────────────────────────────────

    @work(exclusive=True)
    async def run_validate(self) -> None:
        self.validation_results = {
            "discord_token": False, "discord_bot_name": None,
            "discord_server": False, "discord_server_name": None,
            "discord_intents": {}, "discord_permissions": {},
            "target_token": False, "target_bot_name": None,
            "target_community": False, "target_community_name": None,
            "target_permissions": {},
            "discord_timeout": False, "target_timeout": False,
        }
        self.tokens_valid = False
        self.permissions_complete = False

        fillers = [
            "DISCORD_BOT_TOKEN", "FLUXER_BOT_TOKEN", "STOAT_BOT_TOKEN",
            "TARGET_BOT_TOKEN",
            "000000000000000000", "DISCORD_SERVER_ID", "FLUXER_COMMUNITY_ID",
            "STOAT_SERVER_ID", "TARGET_SERVER_ID", "", None,
        ]
        d_dummy = self.config.discord_bot_token in fillers or self.config.discord_server_id in fillers
        t_dummy = (self.config.target_bot_token or "") in fillers or (self.config.target_server_id or "") in fillers

        tasks = {}
        if not d_dummy:
            tasks["discord"] = asyncio.create_task(self.engine.discord_reader.validate())
        if not t_dummy:
            tasks["target"] = asyncio.create_task(self.engine.writer.validate())

        all_tasks = list(tasks.values())
        try:
            done = set()
            if all_tasks:
                done, _ = await asyncio.wait(all_tasks, timeout=10.0)

            dt = tasks.get("discord")
            if dt and dt in done:
                res = dt.result()
                self.validation_results["discord_token"] = res.get("token", False)
                self.validation_results["discord_bot_name"] = res.get("bot_name")
                self.validation_results["discord_server"] = res.get("server", False)
                self.validation_results["discord_server_name"] = res.get("server_name")
                self.validation_results["discord_intents"] = res.get("intents", {})
                self.validation_results["discord_permissions"] = res.get("permissions", {})
            elif dt and dt not in done:
                self.validation_results["discord_timeout"] = True
                dt.cancel()

            tt = tasks.get("target")
            if tt and tt in done:
                res = tt.result()
                self.validation_results["target_token"] = res.get("token", False)
                self.validation_results["target_bot_name"] = res.get("bot_name")
                self.validation_results["target_community"] = res.get("community", False)
                self.validation_results["target_community_name"] = res.get("community_name")
                self.validation_results["target_permissions"] = res.get("permissions", {})
            elif tt and tt not in done:
                self.validation_results["target_timeout"] = True
                tt.cancel()

            discord_ok = self.validation_results.get("discord_token") and self.validation_results.get("discord_server")
            target_ok = self.validation_results.get("target_token") and self.validation_results.get("target_community")
            self.tokens_valid = bool(discord_ok and target_ok)

            if self.tokens_valid:
                srv_id = self.config.target_server_id
                srv_name = self.validation_results.get("target_community_name", "unknown")
                if srv_id and srv_name:
                    safe = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", srv_name)
                    self.engine.state.set_folder(str(srv_id), safe, base_dir=self._base_dir())

            self.permissions_complete = True
            if self.tokens_valid:
                di = self.validation_results.get("discord_intents", {})
                dp = self.validation_results.get("discord_permissions", {})
                if not all([di.get("message_content"), dp.get("view_channel"), dp.get("read_message_history")]):
                    self.permissions_complete = False
                tp = self.validation_results.get("target_permissions", {})
                if tp and not all(tp.values()):
                    self.permissions_complete = False
        except Exception:
            pass
        finally:
            for t in all_tasks:
                if not t.done():
                    t.cancel()

        self._update_info_labels()

    # ── button routing ────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if not bid or not bid.startswith("sp_"):
            return
        if bid == "sp_clone":
            self._open_clone_menu()
        elif bid == "sp_sync":
            self._open_sync_menu()
        elif bid == "sp_messages":
            self.run_migrate_messages()
        elif bid == "sp_danger":
            self._open_danger_menu()

    # ── (1) clone server template (combined) ─────────────────────────────

    def _open_clone_menu(self):
        options = [
            ("sub_clone_roles", "Clone Roles & Role Permissions"),
            ("sub_clone_channels", "Clone Channels & Categories"),
            ("sub_sync_perms", "Sync Channel & Category Permissions"),
        ]
        def on_result(choices):
            if choices:
                # Order defined: Roles -> Channels -> Permissions
                ordered = [c for c in ["sub_clone_roles", "sub_clone_channels", "sub_sync_perms"] if c in choices]
                self.run_batch_clone(ordered)
        self.app.push_screen(OptionSelectModal("Clone Server Template", options), on_result)

    # ── (2) sync server settings (combined) ────────────────────────────

    def _open_sync_menu(self):
        options = [
            ("sub_emoji", "Sync Emojis"),
            ("sub_sticker", "Sync Stickers"),
            ("sub_name", "Sync Server Name"),
            ("sub_icon", "Sync Server Icon"),
            ("sub_banner", "Sync Server Banner"),
        ]
        def on_result(choices):
            if choices:
                self.run_batch_sync(choices)
        self.app.push_screen(OptionSelectModal("Sync Server Settings", options), on_result)

    # ── batch workers ──────────────────────────────────────────────────

    @work(exclusive=True)
    async def run_batch_clone(self, selections: list[str]) -> None:
        modal = ProgressScreen()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)
        try:
            modal.set_status(f"Awaiting Confirmation for {len(selections)} Operations...")
            choice = await modal.phase_wait_confirm()
            if choice == "btn_back":
                modal.dismiss()
                return
            elif choice == "btn_main_menu":
                modal.dismiss()
                self.app.switch_screen("config_selection")
                return

            modal.cancel_callback = lambda: setattr(self.engine, "is_running", False)
            modal.phase_progress()
            await self.engine.start_connections()
            self.engine.is_running = True

            for sel in selections:
                if not self.engine.is_running: break
                if sel == "sub_clone_roles":
                    await self._logic_clone_roles(modal)
                elif sel == "sub_clone_channels":
                    await self._logic_clone_channels(modal)
                elif sel == "sub_sync_perms":
                    await self._logic_sync_permissions(modal)
            
            modal.phase_report("Clone Template Complete")
            modal.write("[bold green]All selected cloning operations finished.[/bold green]")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
            modal.phase_report("Batch Operation", "error")
        finally:
            self.engine.is_running = False
            await self.engine.close_connections()

    @work(exclusive=True)
    async def run_batch_sync(self, selections: list[str]) -> None:
        modal = ProgressScreen()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)
        try:
            modal.set_status("Awaiting Confirmation to Sync Server Settings...")
            choice = await modal.phase_wait_confirm()
            if choice == "btn_back":
                modal.dismiss()
                return
            elif choice == "btn_main_menu":
                modal.dismiss()
                self.app.switch_screen("config_selection")
                return

            modal.cancel_callback = lambda: setattr(self.engine, "is_running", False)
            modal.phase_progress()
            await self.engine.start_connections()
            self.engine.is_running = True

            # Separate asset sync from metadata sync
            asset_types = []
            if "sub_emoji" in selections: asset_types.append("Emoji")
            if "sub_sticker" in selections: asset_types.append("Sticker")
            if asset_types:
                await self._logic_copy_assets(modal, asset_types)
            
            meta_comps = []
            if "sub_name" in selections: meta_comps.append("name")
            if "sub_icon" in selections: meta_comps.append("icon")
            if "sub_banner" in selections: meta_comps.append("banner")
            if meta_comps:
                await self._logic_sync_metadata(modal, meta_comps)
            
            modal.phase_report("Sync Settings Complete")
            modal.write("[bold green]All selected synchronization operations finished.[/bold green]")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
            modal.phase_report("Batch Operation", "error")
        finally:
            self.engine.is_running = False
            await self.engine.close_connections()

    # ── logic blocks (internal) ────────────────────────────────────────

    async def _logic_clone_channels(self, modal: ProgressScreen):
        if self.target_platform == "fluxer":
            from src.fluxer.clone_server import sync_channel_state, migrate_channels
        else:
            from src.stoat.clone_server import sync_channel_state, migrate_channels
        
        modal.set_status("Processing Server Structure...")
        await sync_channel_state(self.engine)
        categories = await self.engine.discord_reader.get_categories()
        channels = await self.engine.discord_reader.get_channels()
        
        async def update_progress(item_name, status, current, total):
            color = "cyan" if status == "Copying" else "yellow"
            modal.set_status(f"[{color}]{status}: {item_name}[/{color}]")
            modal.set_progress(current, total)
        
        cloned_info = await migrate_channels(self.engine, progress_callback=update_progress, force=False)
        if cloned_info and cloned_info.get("structure"):
            lines = ["Successfully cloned channels and categories:"]
            cats = sorted(cloned_info["structure"].keys(), key=lambda x: (x == "No Category", x))
            for cat_name in cats:
                ch_names = cloned_info["structure"][cat_name]
                if cat_name in cloned_info.get("categories_created", []) or ch_names:
                    lines.append(f"- **{cat_name}**")
                    for n in sorted(ch_names): lines.append(f"  - {n}")
            await log_audit_event(self.engine, "Channels Cloned", "\n".join(lines))

    async def _logic_clone_roles(self, modal: ProgressScreen):
        roles_mod = fluxer_roles if self.target_platform == "fluxer" else stoat_roles
        modal.set_status("Processing Roles...")
        await roles_mod.sync_roles_state(self.engine)
        
        async def update(name, current, total):
            modal.set_status(f"[cyan]Copying Role: {name}[/cyan]")
            modal.set_progress(current, total)
        
        cloned = await roles_mod.migrate_roles(self.engine, progress_callback=update, force=False)
        if cloned:
            await log_audit_event(self.engine, "Roles Cloned", "Successfully cloned roles:\n" + "\n".join(f"- {r}" for r in cloned))

    async def _logic_sync_permissions(self, modal: ProgressScreen):
        roles_mod = fluxer_roles if self.target_platform == "fluxer" else stoat_roles
        modal.set_status("Syncing Permissions...")
        
        async def update(name, current, total):
            modal.set_status(f"[cyan]Syncing Perms: {name}[/cyan]")
            modal.set_progress(current, total)
            
        synced = await roles_mod.sync_permissions(self.engine, progress_callback=update)
        if synced and synced.get("structure"):
            lines = ["Synchronized permission overrides:"]
            cats = sorted(synced["structure"].keys(), key=lambda x: (x == "No Category", x))
            for cat_name in cats:
                ch_names = synced["structure"][cat_name]
                if cat_name in synced.get("categories_synced", []) or ch_names:
                    lines.append(f"- **{cat_name}**")
                    for n in sorted(ch_names): lines.append(f"  - {n}")
            await log_audit_event(self.engine, "Permissions Synced", "\n".join(lines))

    async def _logic_copy_assets(self, modal: ProgressScreen, types_to_include: list[str]):
        asset_mod = stoat_emoji_stickers if self.target_platform == "stoat" else fluxer_emoji_stickers
        modal.set_status("Processing Assets...")
        await asset_mod.sync_assets_state(self.engine)
        
        async def update(name, item_type, current, total):
            modal.set_status(f"[cyan]Copying {item_type}: {name}[/cyan]")
            modal.set_progress(current, total)
            
        cloned = await asset_mod.migrate_emojis(self.engine, progress_callback=update, types_to_include=types_to_include, force=False)
        if cloned and (cloned.get("Emoji") or cloned.get("Sticker")):
            lines = []
            if cloned.get("Emoji"):
                lines.append("Emojis cloned:"); lines.extend([f"- {n}" for n in cloned["Emoji"]])
            if cloned.get("Sticker"):
                lines.append("Stickers cloned:"); lines.extend([f"- {n}" for n in cloned["Sticker"]])
            await log_audit_event(self.engine, "Assets Cloned", "\n".join(lines))

    async def _logic_sync_metadata(self, modal: ProgressScreen, components: list[str]):
        meta_mod = fluxer_metadata if self.target_platform == "fluxer" else stoat_metadata
        modal.set_status("Syncing Server Profile...")
        
        async def progress_cb(item, status):
            color = "green" if status == "DONE" else "red" if status == "ERROR" else "yellow"
            modal.write(f"{item} [[bold {color}]{status}[/bold {color}]]")
            
        cloned = await meta_mod.sync_server_metadata(self.engine, progress_cb, components=components)
        if cloned:
            lines = ["Synchronized profile traits:"]
            if "name" in cloned: lines.append(f"- **Name**: {cloned['name']}")
            if "icon" in cloned: lines.append("- **Icon**")
            if "banner" in cloned: lines.append("- **Banner**")
            # Prepare files for audit log
            files = []
            if "icon" in cloned:
                ext = "gif" if cloned["icon"].startswith(b"GIF") else "png"
                files.append({"filename": f"icon.{ext}", "data": cloned["icon"]})
            if "banner" in cloned:
                ext = "gif" if cloned["banner"].startswith(b"GIF") else "png"
                files.append({"filename": f"banner.{ext}", "data": cloned["banner"]})
            await log_audit_event(self.engine, "Profile Synced", "\n".join(lines), files=files)

    # ── (5) message migration ─────────────────────────────────────────────

    @work(exclusive=True)
    async def run_migrate_messages(self) -> None:
        if not self.tokens_valid:
            return

        migrate_mod = fluxer_migrate if self.target_platform == "fluxer" else stoat_migrate
        platform_name = self.target_platform.capitalize()

        modal = ProgressScreen()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)

        try:
            modal.set_status("Fetching channels...")
            await self.engine.start_connections()

            full_d = await self.engine.discord_reader.get_channels()
            d_channels = [c for c in full_d if c.type in [discord.ChannelType.text, discord.ChannelType.news]]
            d_cats = await self.engine.discord_reader.get_categories()
            d_cat_map = {c.id: c.name for c in d_cats}

            if not d_channels:
                modal.write("[yellow]No text channels found.[/yellow]")
                modal.allow_close()
                return

            # Fetch target channels
            modal.set_status(f"Fetching {platform_name} channels...")
            
            full_f = await self.engine.writer.get_channels()
            f_channels = [c for c in full_f if c.get("name") not in ["reaper_logs", "reaper-logs"] and c.get("type") not in [2, 4]]

            if not f_channels:
                modal.write(f"[yellow]No channels found in {platform_name} community.[/yellow]")
                modal.allow_close()
                await self.engine.close_connections()
                return

            self.app.pop_screen()

            target_cat_names = {str(c.get("id")): c.get("name") for c in full_f if c.get("type") == 4}

            while True:
                loop = asyncio.get_running_loop()
                pick_future = loop.create_future()

                def on_pick(result):
                    if not pick_future.done():
                        pick_future.set_result(result)

                self.app.push_screen(ChannelPickerScreen(d_channels, d_cat_map, f_channels, target_cat_names, platform_name), on_pick)
                res = await pick_future

                if res is None:
                    await self.engine.close_connections()
                    return
                
                src_id, tgt_id = res
                source_channel = next(c for c in d_channels if c.id == src_id)
                target_channel = next(c for c in f_channels if c.get("id") == tgt_id)

                # Determine after_id status
                last_migrated = self.engine.state.get_last_message_id(str(target_channel.get('id')))
                has_previous = bool(last_migrated)
                
                # Analyze
                modal = ProgressScreen()
                self.app.push_screen(modal)
                await asyncio.sleep(0.1)
                modal.set_status("Analyzing channel...")
                modal.show_stats()

                self.engine.is_running = True
                stats_analysis = {"messages": 0, "threads": 0, "attachments": 0}

                async def update_scan(current_stats):
                    modal.set_status(f"[cyan]Scanned {current_stats['messages']} items...")

                stats_analysis = await migrate_mod.analyze_migration(
                    self.engine,
                    source_channel_id=source_channel.id,
                    after_message_id=int(last_migrated) if last_migrated else None,
                    progress_callback=update_scan,
                )
                self.engine.is_running = False

                # Set initial total stats for the confirmation block
                modal.update_stats(
                    messages=stats_analysis['messages'],
                    threads=stats_analysis['threads'],
                    files=stats_analysis['attachments']
                )

                # Setup the info container
                m_status = "[bold yellow]Previous Migration Detected[/bold yellow]" if has_previous else "[bold cyan]No previous migration data.[/bold cyan]"
                i_status = f"[bold]{stats_analysis['messages']}[/bold] New Messages, [bold]{stats_analysis['threads']}[/bold] Threads."
                modal.show_info(m_status, i_status)

                modal.set_status(f"Awaiting Confirmation to migrate Discord [cyan]#{source_channel.name}[/cyan] → {platform_name} [green]#{target_channel.get('name')}[/green]")

                # Phase 2: Confirmation
                choice = await modal.phase_wait_confirm(show_continue=has_previous)
                if choice == "btn_back":
                    modal.dismiss()
                    continue # Return to channel picker
                elif choice == "btn_main_menu":
                    modal.dismiss()
                    self.app.switch_screen("config_selection")
                    self.engine.is_running = False
                    await self.engine.close_connections()
                    return
                    
                after_id = None
                if choice == "btn_continue" and last_migrated:
                    after_id = int(last_migrated)
                elif choice == "btn_start_id":
                    # Fallback to full for now since we don't have an ID input dialog yet
                    modal.write("[yellow]Custom Message ID start not fully implemented, starting from beginning.[/yellow]")
                    after_id = None
                
                # If we are here, we are proceeding with migration
                break

            # Phase 3: Progress
            modal.cancel_callback = lambda: setattr(self.engine, "is_running", False)
            modal.phase_progress()
            modal.set_status("Migrating messages...")

            total_messages = stats_analysis["messages"]
            total_threads = stats_analysis["threads"]
            total_attachments = stats_analysis["attachments"]
            
            self.engine.is_running = True

            async def update_msg(current_stats):
                c_msgs = current_stats["messages"]
                c_threads = current_stats["threads"]
                c_files = current_stats["attachments"]
                
                modal.set_status(f"[cyan]Migrated {c_msgs}/{total_messages} messages...")
                modal.set_progress(c_msgs, total_messages)
                
                modal.update_stats(
                    messages=f"{c_msgs}/{total_messages}",
                    threads=f"{c_threads}/{total_threads}",
                    files=f"{c_files}/{total_attachments}"
                )
                
                # optionally show a scrolling trace if the backend provided it
                modal.write_live(f"Migrated message #{c_msgs}")

            result = await migrate_mod.migrate_messages(
                self.engine,
                source_channel_id=source_channel.id,
                target_channel_id=target_channel.get("id"),
                after_message_id=after_id,
                progress_callback=update_msg,
            )

            if self.engine.is_running:
                modal.write(f"[bold green]Success! {result['messages']} messages migrated.[/bold green]")
                event_title = "Message Migration"
                modal.phase_report(event_title)
            else:
                modal.write(f"[bold yellow]Interrupted! {result['messages']} messages migrated.[/bold yellow]")
                event_title = "Message Migration"
                modal.phase_report(event_title, "stopped")

            lines = [f"Migrated Discord #{source_channel.name} → {platform_name} #{target_channel.get('name')}:"]
            lines.append(f"{result['messages']} messages, {result['attachments']} attachments, {result['threads']} threads")
            await log_audit_event(self.engine, event_title, "\n".join(lines))

        except Exception as e:
            err = str(e)
            if "MissingPermission" in err and "Masquerade" in err:
                modal.write("[bold red]Bot is missing the 'Masquerade' permission.[/bold red]")
            else:
                modal.write(f"[bold red]Error: {err}[/bold red]")
            modal.phase_report("Message Migration", "error")
        finally:
            self.engine.is_running = False
            await self.engine.close_connections()

    # ── (6) danger zone ───────────────────────────────────────────────────

    def _open_danger_menu(self):
        options = [
            ("dz_del_channels", "Delete ALL Channels & Categories", "error"),
            ("dz_reset_perms", "Reset ALL Channel Permissions", "error"),
            ("dz_del_roles", "Delete ALL Roles", "error"),
            ("dz_del_assets", "Delete ALL Emojis & Stickers", "error"),
        ]
        def on_result(choice):
            if choice == "dz_del_channels":
                self._confirm_danger("Delete ALL channels and categories? This is IRREVERSIBLE.", self.run_dz_delete_channels)
            elif choice == "dz_reset_perms":
                self._confirm_danger("Reset ALL channel permissions? This is IRREVERSIBLE.", self.run_dz_reset_perms)
            elif choice == "dz_del_roles":
                self._confirm_danger("Delete ALL roles? This is IRREVERSIBLE.", self.run_dz_delete_roles)
            elif choice == "dz_del_assets":
                self._confirm_danger("Delete ALL emojis and stickers? This is IRREVERSIBLE.", self.run_dz_delete_assets)
        self.app.push_screen(SubMenuModal("⚠ DANGER ZONE ⚠", options), on_result)

    def _confirm_danger(self, message: str, callback):
        async def do_confirm():
            modal = ProgressScreen()
            self.app.push_screen(modal)
            await asyncio.sleep(0.1)
            
            modal.set_status(f"[bold red]DANGER:[/bold red] {message}")
            modal.write("[bold red]WARNING: THIS WILL DELETE DATA PERMANENTLY! MUST PROCEED TO CONTINUE.[/bold red]")
            
            choice = await modal.phase_wait_confirm()
            modal.dismiss()
            if choice == "btn_start_first":
                callback()
            elif choice == "btn_main_menu":
                self.app.switch_screen("config_selection")

        asyncio.create_task(do_confirm())

    @work(exclusive=True)
    async def run_dz_delete_channels(self) -> None:
        if self.target_platform == "fluxer":
            from src.fluxer.danger_zone import danger_delete_all_channels
        else:
            from src.stoat.danger_zone import danger_delete_all_channels

        modal = ProgressScreen()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)
        modal.cancel_callback = lambda: setattr(self.engine, "is_running", False)
        modal.phase_progress()

        try:
            modal.set_status("[red]Deleting channels...")
            await self.engine.start_target_only()

            async def on_deleted(name, current, total):
                modal.set_status(f"[red]Deleting: {name}")
                modal.set_progress(current, total)

            count = await danger_delete_all_channels(self.engine, progress_callback=on_deleted)
            
            modal.phase_report("Danger Zone: Channels Wiped")
            modal.write(f"[bold green]Success! {count} channels/categories deleted.[/bold green]")
            await log_audit_event(self.engine, "Danger Zone: Channels Wiped", f"Deleted {count} channels and categories.")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
            modal.phase_report("Delete Channels", "error")
        finally:
            await self.engine.close_target_only()

    @work(exclusive=True)
    async def run_dz_reset_perms(self) -> None:
        if self.target_platform == "fluxer":
            from src.fluxer.danger_zone import danger_reset_channel_permissions
        else:
            from src.stoat.danger_zone import danger_reset_channel_permissions

        modal = ProgressScreen()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)
        modal.cancel_callback = lambda: setattr(self.engine, "is_running", False)
        modal.phase_progress()

        try:
            modal.set_status("[red]Resetting permissions...")
            await self.engine.start_target_only()

            async def on_reset(name, current, total):
                modal.set_status(f"[red]Resetting: {name}")
                modal.set_progress(current, total)

            count = await danger_reset_channel_permissions(self.engine, progress_callback=on_reset)
            
            modal.phase_report("Danger Zone: Permissions Wiped")
            modal.write(f"[bold green]Success! Permissions reset on {count} items.[/bold green]")
            await log_audit_event(self.engine, "Danger Zone: Permissions Wiped", f"Reset permissions on {count} items.")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
            modal.phase_report("Wipe Permissions", "error")
        finally:
            await self.engine.close_target_only()

    @work(exclusive=True)
    async def run_dz_delete_roles(self) -> None:
        if self.target_platform == "fluxer":
            from src.fluxer.danger_zone import danger_delete_all_roles
        else:
            from src.stoat.danger_zone import danger_delete_all_roles

        modal = ProgressScreen()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)
        modal.cancel_callback = lambda: setattr(self.engine, "is_running", False)
        modal.phase_progress()

        try:
            modal.set_status("[red]Deleting roles...")
            await self.engine.start_target_only()

            async def on_deleted(name, current, total):
                modal.set_status(f"[red]Deleting role: {name}")
                modal.set_progress(current, total)

            count = await danger_delete_all_roles(self.engine, progress_callback=on_deleted)
            
            modal.phase_report("Danger Zone: Roles Wiped")
            modal.write(f"[bold green]Success! {count} roles deleted.[/bold green]")
            await log_audit_event(self.engine, "Danger Zone: Roles Wiped", f"Deleted {count} roles.")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
            modal.phase_report("Wipe Roles", "error")
        finally:
            await self.engine.close_target_only()

    @work(exclusive=True)
    async def run_dz_delete_assets(self) -> None:
        if self.target_platform == "fluxer":
            from src.fluxer.danger_zone import danger_delete_all_emojis_and_stickers
        else:
            from src.stoat.danger_zone import danger_delete_all_emojis_and_stickers

        modal = ProgressScreen()
        self.app.push_screen(modal)
        await asyncio.sleep(0.1)
        modal.cancel_callback = lambda: setattr(self.engine, "is_running", False)
        modal.phase_progress()

        try:
            modal.set_status("[red]Deleting assets...")
            await self.engine.start_target_only()

            async def on_deleted(name, asset_type, current, total):
                modal.set_status(f"[red]Deleting {asset_type}: {name}")
                modal.set_progress(current, total)

            counts = await danger_delete_all_emojis_and_stickers(self.engine, progress_callback=on_deleted)
            
            modal.phase_report("Danger Zone: Assets Wiped")
            modal.write(f"[bold green]Success! {counts.get('emojis', 0)} emojis, {counts.get('stickers', 0)} stickers deleted.[/bold green]")
            await log_audit_event(self.engine, "Danger Zone: Assets Wiped", f"Deleted {counts.get('emojis', 0)} emojis and {counts.get('stickers', 0)} stickers.")
        except Exception as e:
            modal.write(f"[bold red]Error: {e}[/bold red]")
            modal.phase_report("Wipe Assets", "error")
        finally:
            await self.engine.close_target_only()
