import logging
from typing import Optional, Union
import yaml
from pathlib import Path
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature flags — toggle these to enable/disable platform-specific behaviour
# ---------------------------------------------------------------------------

# When True, Fluxer-source configs are restricted to direct-transfer mode
# only (backup and backup-transfer modes are hidden/disabled).
# Set to False once backup support for Fluxer sources is implemented.
FLUXER_SOURCE_DISABLE_BACKUP_MODES = True


class AppConfig(BaseModel):
    source_platform: str = Field(default="discord")  # discord | fluxer | none
    source_bot_token: Optional[str] = Field(default=None)  
    source_server_id: Optional[str] = Field(default=None)  
    source_api_url: Optional[str] = Field(default=None)     # If self-hostable like fluxer
    tool_mode: str = Field(default="direct_transfer")  # direct_transfer | backup_transfer | backup_only
    target_platform: str = Field(default="fluxer")       # fluxer | stoat | none
    fluxer_bot_token: Optional[str] = Field(default=None)
    fluxer_server_id: Optional[str] = Field(default=None)
    fluxer_api_url: Optional[str] = Field(default=None)
    stoat_bot_token: Optional[str] = Field(default=None)
    stoat_server_id: Optional[str] = Field(default=None)
    stoat_api_url: Optional[str] = Field(default=None)
    anonymize_users: bool = Field(default=False)
    log_level: str = Field(default="INFO")

def load_config(config_path: Union[str, Path] = "reaper_config.yaml", create_if_missing: bool = True) -> AppConfig:
    path = Path(config_path)
    if not path.exists():
        if not create_if_missing:
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
            
        config = AppConfig()
        # DO NOT auto-save here, it might overwrite valid data if path is transiently wrong
        # print(f"Created default configuration: {config_path}")
        return config
    
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data:
        raise ValueError("Configuration file is empty or invalid YAML.")

    return AppConfig(**data)

def save_config(config: AppConfig, config_path: Union[str, Path] = "reaper_config.yaml"):
    path = Path(config_path)
    data = config.model_dump(exclude_none=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)

def get_available_configs() -> list[str]:
    """Returns a list of available configuration names. 
    If reaper_config.yaml exists in CWD, returns ['.'] to signify standalone mode."""
    if Path("reaper_config.yaml").exists():
        return ["."]
        
    configs = []
    for item in Path(".").iterdir():
        if item.is_dir() and item.name.startswith("ReaperFiles-"):
            config_name = item.name[len("ReaperFiles-"):]
            if (item / "reaper_config.yaml").exists():
                configs.append(config_name)
    return sorted(configs)

def create_new_config(name: str, source_platform: str = "discord") -> Path:
    """Creates a new configuration folder and saves a default config file with the chosen source platform."""
    folder_path = Path(f"ReaperFiles-{name}")
    folder_path.mkdir(exist_ok=True)
    config_path = folder_path / "reaper_config.yaml"
    config = AppConfig(source_platform=source_platform)
    save_config(config, config_path)
    return folder_path


def delete_config(name: str) -> bool:
    """Delete a ReaperFiles-* configuration folder and all its contents.
    Returns True on success, False on failure or if config doesn't exist."""
    import shutil
    folder_path = Path(f"ReaperFiles-{name}")
    if not folder_path.exists() or not folder_path.is_dir():
        return False
    try:
        shutil.rmtree(folder_path)
        return True
    except Exception:
        return False


def scan_config_data(name: str) -> dict[str, list[str]]:
    """Scan a config folder for extra data files (databases, backups).
    Returns a dict like {'db': ['file.db'], 'backups': ['Disc2Flux_BACKUP-123']}.
    Empty dict if nothing extra found."""
    import shutil
    folder = Path(f"ReaperFiles-{name}")
    if not folder.exists():
        return {}

    result: dict[str, list[str]] = {}
    for item in sorted(folder.iterdir()):
        if item.name == "reaper_config.yaml":
            continue
        if item.suffix == ".db":
            result.setdefault("db", []).append(item.name)
        elif item.is_dir() and "_BACKUP" in item.name:
            result.setdefault("backups", []).append(item.name)
    return result


def save_config_data(name: str, dest_dir: str | Path = "saved-data") -> Path | None:
    """Copy db and backup folders from a config to a safe destination.
    Creates dest_dir/{name}/ and copies the files there.
    Returns the destination path, or None on failure."""
    import shutil
    folder = Path(f"ReaperFiles-{name}")
    if not folder.exists():
        return None

    dest = Path(dest_dir) / name
    dest.mkdir(parents=True, exist_ok=True)

    copied = 0
    for item in folder.iterdir():
        if item.name == "reaper_config.yaml":
            continue
        if item.suffix == ".db" or (item.is_dir() and "_BACKUP" in item.name):
            target = dest / item.name
            if item.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
            copied += 1

    return dest if copied > 0 else None


def clone_config(source_name: str, new_name: str, new_source_platform: str | None = None) -> Path | None:
    """Clone an existing configuration to a new name, optionally changing the source platform.
    If the source platform is changed, source-specific tokens/IDs are wiped.
    Returns the new folder path, or None if the source doesn't exist."""
    source_path = Path(f"ReaperFiles-{source_name}") / "reaper_config.yaml"
    if not source_path.exists():
        return None

    config = load_config(source_path)
    old_platform = config.source_platform

    # If the platform is explicitly changed, wipe source credentials
    if new_source_platform and new_source_platform != old_platform:
        config.source_platform = new_source_platform
        config.source_bot_token = None
        config.source_server_id = None
        config.source_api_url = None

    folder_path = Path(f"ReaperFiles-{new_name}")
    if folder_path.exists():
        logger.warning(
            "clone_config: destination 'ReaperFiles-%s' already exists — "
            "its reaper_config.yaml will be overwritten.",
            new_name,
        )
    folder_path.mkdir(exist_ok=True)
    new_config_path = folder_path / "reaper_config.yaml"
    save_config(config, new_config_path)
    return folder_path
