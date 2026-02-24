import sys
import asyncio
import logging
from pathlib import Path
from src.ui.app import run_cli
from src.core.configuration import load_config

def select_config():
    from rich.console import Console
    from rich.prompt import Prompt
    from rich.panel import Panel
    
    console = Console()
    
    fluxer_exists = Path("fluxer.config.yaml").exists()
    stoat_exists = Path("stoat.config.yaml").exists()
    
    if fluxer_exists and not stoat_exists:
        return "fluxer.config.yaml"
    elif stoat_exists and not fluxer_exists:
        return "stoat.config.yaml"
    elif fluxer_exists and stoat_exists:
        console.print(Panel.fit(
            "[bold]Both Fluxer and Stoat configurations found.[/bold]\n"
            "Which one do you want to use?",
            title="[bold cyan]Configuration Selection[/bold cyan]"
        ))
        console.print("(1) [bold blue]Fluxer[/bold blue]")
        console.print("(2) [bold red]Stoat[/bold red]")
        console.print("(Q) [bold dim]Quit[/bold dim]")
        console.print("")
        
        choice = Prompt.ask("Select an option [[bold cyan]1/2/Q[/bold cyan]]", choices=["1", "2", "Q", "q"], show_choices=False).upper()
        if choice == "1":
            return "fluxer.config.yaml"
        elif choice == "2":
            return "stoat.config.yaml"
        else:
            sys.exit(0)
    else:
        console.print(Panel.fit(
            "[bold]First setup, Tool configuration[/bold]\n"
            "Which platform do you want to migrate to?",
            title="[bold cyan]Initial Setup[/bold cyan]"
        ))
        console.print("(1) [bold blue]Fluxer[/bold blue]")
        console.print("(2) [bold red]Stoat[/bold red]")
        console.print("(Q) [bold dim]Quit[/bold dim]")
        console.print("")
        
        choice = Prompt.ask("Select an option [[bold cyan]1/2/Q[/bold cyan]]", choices=["1", "2", "Q", "q"], show_choices=False).upper()
        if choice == "1":
            return "fluxer.config.yaml"
        elif choice == "2":
            return "stoat.config.yaml"
        else:
            sys.exit(0)

def setup_logging(config_path):
    try:
        config = load_config(config_path)
        log_level_str = config.migration.log_level.upper()
        level = getattr(logging, log_level_str, logging.INFO)
    except Exception:
        level = logging.INFO
        
    platform = config_path.split('.')[0]
    handlers = [logging.FileHandler(f'{platform}.migration.log', mode='a')]
    if level == logging.DEBUG:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
        datefmt='%H:%M:%S',
        level=level,
        handlers=handlers
    )

def relaunch_in_terminal():
    """Detects if running without a terminal on Linux and relaunches in one."""
    import os
    import sys
    import subprocess
    import shutil

    # Only attempt on Linux
    if sys.platform != "linux":
        return

    # Check if we have a TTY on stdin or stdout, or if already relaunched
    is_tty = sys.stdin.isatty() or sys.stdout.isatty()
    if is_tty or os.environ.get("FLUXER_REAPER_RELAUNCHED"):
        return

    # Diagnostic logging to help debug why it fails on some distros
    debug_log = "/tmp/reaper_terminal_debug.log"
    with open(debug_log, "a") as f:
        f.write(f"Relaunching... isatty={is_tty}, env={os.environ.get('FLUXER_REAPER_RELAUNCHED')}\n")

    # List of terminals to try with their specific execution flags
    terminals = [
        ("gnome-terminal", ["--"]),       # Modern GNOME Terminal
        ("ptyxis", ["--"]),               # Fedora/Modern GNOME
        ("x-terminal-emulator", ["-e"]),  # Ubuntu/Debian standard
        ("kgx", ["-e"]),                  # GNOME Console
        ("konsole", ["-e"]),
        ("xfce4-terminal", ["-e"]),
        ("lxterminal", ["-e"]),
        ("mate-terminal", ["-e"]),
        ("alacritty", ["-e"]),
        ("kitty", []),
        ("xterm", ["-e"]),
    ]

    # Resolve the absolute path to ourselves.
    # frozen=True means we are running from a PyInstaller bundle.
    if getattr(sys, 'frozen', False):
        executable = os.path.abspath(sys.argv[0])
    else:
        executable = sys.executable
        
    args = [executable] + sys.argv[1:]
    
    # Set env var to prevent loops
    env = os.environ.copy()
    env["FLUXER_REAPER_RELAUNCHED"] = "1"

    for term, cmd_args in terminals:
        if shutil.which(term):
            with open(debug_log, "a") as f:
                f.write(f"Found terminal: {term}\n")
            try:
                # Construct command: term [args] executable [sys.argv]
                subprocess.Popen([term] + cmd_args + args, env=env)
                sys.exit(0)
            except Exception as e:
                with open(debug_log, "a") as f:
                    f.write(f"Failed to launch {term}: {e}\n")
                continue

def main():
    relaunch_in_terminal()
    config_path = select_config()
    setup_logging(config_path)
    try:
        asyncio.run(run_cli(config_path))
    except KeyboardInterrupt:
        print("\nOperation terminated by user.")
        sys.exit(0)
    except Exception as e:
        print(f"Failed to start tool: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
