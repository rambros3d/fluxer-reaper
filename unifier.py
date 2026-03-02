import sys
import asyncio
import logging
from src.ui.unifier_tui import run_unifier_tui

def setup_logging():
    level = logging.INFO
    handlers = [logging.FileHandler('.unifier.log', mode='a')]
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
    if is_tty or os.environ.get("DISCO_UNIFIER_RELAUNCHED"):
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
    env["DISCO_UNIFIER_RELAUNCHED"] = "1"

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
    
    await run_unifier_tui()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nOperation terminated by user.")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
