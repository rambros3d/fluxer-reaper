import sys
import asyncio
import logging
from src.ui.disco_reaper_app import run_disco_reaper
from src.core.configuration import load_config

def setup_logging():
    try:
        config = load_config()
        log_level_str = config.migration.log_level.upper()
        level = getattr(logging, log_level_str, logging.INFO)
    except Exception:
        level = logging.INFO
        
    handlers = [logging.FileHandler('disco-reaper.log', mode='a')]
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

    if sys.platform != "linux":
        return

    is_tty = sys.stdin.isatty() or sys.stdout.isatty()
    if is_tty or os.environ.get("DISCO_REAPER_RELAUNCHED"):
        return

    terminals = [
        ("gnome-terminal", ["--"]),
        ("ptyxis", ["--"]),
        ("x-terminal-emulator", ["-e"]),
        ("kgx", ["-e"]),
        ("konsole", ["-e"]),
        ("xfce4-terminal", ["-e"]),
        ("xterm", ["-e"]),
    ]

    executable = sys.executable if not getattr(sys, 'frozen', False) else os.path.abspath(sys.argv[0])
    args = [executable] + sys.argv[1:]
    
    env = os.environ.copy()
    env["DISCO_REAPER_RELAUNCHED"] = "1"

    for term, cmd_args in terminals:
        if shutil.which(term):
            try:
                subprocess.Popen([term] + cmd_args + args, env=env)
                sys.exit(0)
            except Exception:
                continue

async def main():
    relaunch_in_terminal()
    setup_logging()
    try:
        await run_disco_reaper()
    except KeyboardInterrupt:
        print("\nExiting...")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
