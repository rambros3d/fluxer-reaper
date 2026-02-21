import sys
import asyncio
import logging
from src.ui.app import run_cli
from src.config import load_config

def setup_logging():
    try:
        config = load_config()
        log_level_str = config.migration.log_level.upper()
        level = getattr(logging, log_level_str, logging.INFO)
    except Exception:
        level = logging.INFO
        
    handlers = [logging.FileHandler('migration.log', mode='a')]
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
    setup_logging()
    try:
        asyncio.run(run_cli())
    except KeyboardInterrupt:
        print("\nOperation terminated by user.")
        sys.exit(0)
    except Exception as e:
        print(f"Failed to start tool: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
