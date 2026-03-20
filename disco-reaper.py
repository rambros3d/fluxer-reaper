import sys
import logging
from src.ui.main_app import run_disco_reaper_tui
from src.core.configuration import load_config

def setup_logging():
    try:
        config = load_config(create_if_missing=False)
        log_level_str = config.migration.log_level.upper()
        level = getattr(logging, log_level_str, logging.INFO)
    except Exception:
        level = logging.INFO
        
    handlers = [logging.FileHandler('.reaper.log', mode='a')]
    logging.basicConfig(
        format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
        datefmt='%H:%M:%S',
        level=level,
        handlers=handlers
    )
    # Suppress PIL debug logs which are very noisy during Lottie conversion
    logging.getLogger('PIL').setLevel(logging.INFO)

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

def setup_ssl():
    """Configures SSL certificate paths for frozen environments."""
    import os
    import sys
    
    # Only strictly necessary for frozen builds on Linux/macOS
    if not getattr(sys, 'frozen', False):
        return

    try:
        import certifi
        ca_file = certifi.where()
        os.environ["SSL_CERT_FILE"] = ca_file
        os.environ["REQUESTS_CA_BUNDLE"] = ca_file
    except ImportError:
        # Fallback to common Linux CA bundle paths
        ca_paths = [
            "/etc/ssl/certs/ca-certificates.crt",
            "/etc/pki/tls/certs/ca-bundle.crt",
            "/etc/ssl/ca-bundle.pem",
            "/etc/pki/tls/cacert.pem",
            "/etc/ssl/cert.pem",
        ]
        for path in ca_paths:
            if os.path.exists(path):
                os.environ["SSL_CERT_FILE"] = path
                os.environ["REQUESTS_CA_BUNDLE"] = path
                break

def main():
    import os
    # Ensure screenshots directory is configured (but not created yet)
    shot_path = os.path.abspath("screenshots")
    os.environ["TEXTUAL_SCREENSHOT_LOCATION"] = shot_path

    relaunch_in_terminal()
    setup_ssl()
    setup_logging()
    run_disco_reaper_tui()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOperation terminated by user.")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
