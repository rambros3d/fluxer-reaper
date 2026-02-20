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

def main():
    setup_logging()
    try:
        asyncio.run(run_cli())
    except KeyboardInterrupt:
        print("\nOperation interrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"Failed to start tool: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
