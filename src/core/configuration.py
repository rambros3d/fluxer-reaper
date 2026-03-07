from typing import Optional, Union
import yaml
from pathlib import Path
from pydantic import BaseModel, Field

class MigrationSettings(BaseModel):
    batch_size: int = Field(default=100)
    rate_limit_delay_seconds: int = Field(default=2)
    log_level: str = Field(default="DEBUG")

class AppConfig(BaseModel):
    discord_bot_token: Optional[str] = Field(default=None)
    discord_server_id: Optional[str] = Field(default=None)
    tool_mode: str = Field(default="direct_transfer")  # direct_transfer | backup_transfer | backup_only
    target_platform: str = Field(default="fluxer")       # fluxer | stoat | none
    target_bot_token: Optional[str] = Field(default=None)
    target_server_id: Optional[str] = Field(default=None)
    target_api_url: Optional[str] = Field(default="default")
    migration: MigrationSettings = Field(default_factory=MigrationSettings)

    # ── backward‑compat shims (read‑only) ────────────────────────────────
    # The rest of the codebase (fluxer/stoat modules) still reads these.
    # They all delegate to the unified target_* fields.

    @property
    def use_fluxer(self) -> bool:
        return self.target_platform == "fluxer"

    @property
    def use_stoat(self) -> bool:
        return self.target_platform == "stoat"

    @property
    def fluxer_bot_token(self) -> Optional[str]:
        return self.target_bot_token if self.target_platform == "fluxer" else None

    @property
    def fluxer_community_id(self) -> Optional[str]:
        return self.target_server_id if self.target_platform == "fluxer" else None

    @property
    def fluxer_api_url(self) -> Optional[str]:
        return self.target_api_url if self.target_platform == "fluxer" else None

    @property
    def stoat_bot_token(self) -> Optional[str]:
        return self.target_bot_token if self.target_platform == "stoat" else None

    @property
    def stoat_server_id(self) -> Optional[str]:
        return self.target_server_id if self.target_platform == "stoat" else None

    @property
    def stoat_api_url(self) -> Optional[str]:
        return self.target_api_url if self.target_platform == "stoat" else None

def load_config(config_path: Union[str, Path] = "config.yaml", create_if_missing: bool = True) -> AppConfig:
    path = Path(config_path)
    if not path.exists():
        if not create_if_missing:
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
            
        config = AppConfig()
        save_config(config, path)
        print(f"Created default configuration: {config_path}")
        return config
    
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    if not data:
        raise ValueError("Configuration file is empty or invalid YAML.")

    # ── migrate legacy configs that still have separate fluxer/stoat fields ──
    if "fluxer_bot_token" in data or "stoat_bot_token" in data:
        if data.get("fluxer_bot_token") and data["fluxer_bot_token"] not in ("FLUXER_BOT_TOKEN", None):
            data.setdefault("target_platform", "fluxer")
            data.setdefault("target_bot_token", data["fluxer_bot_token"])
            data.setdefault("target_server_id", data.get("fluxer_community_id"))
            data.setdefault("target_api_url", data.get("fluxer_api_url", "default"))
        elif data.get("stoat_bot_token") and data["stoat_bot_token"] not in ("STOAT_BOT_TOKEN", None):
            data.setdefault("target_platform", "stoat")
            data.setdefault("target_bot_token", data["stoat_bot_token"])
            data.setdefault("target_server_id", data.get("stoat_server_id"))
            data.setdefault("target_api_url", data.get("stoat_api_url", "default"))
        # Remove legacy keys so they don't conflict with the model
        for key in ("fluxer_bot_token", "fluxer_community_id", "fluxer_api_url",
                     "stoat_bot_token", "stoat_server_id", "stoat_api_url",
                     "use_fluxer", "use_stoat"):
            data.pop(key, None)

    return AppConfig(**data)

def save_config(config: AppConfig, config_path: Union[str, Path] = "config.yaml"):
    path = Path(config_path)
    data = config.model_dump(exclude_none=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)

def get_available_configs() -> list[str]:
    """Returns a list of available configuration names from `ReaperFiles-*` folders."""
    configs = []
    for item in Path(".").iterdir():
        if item.is_dir() and item.name.startswith("ReaperFiles-"):
            config_name = item.name[len("ReaperFiles-"):]
            if (item / "config.yaml").exists():
                configs.append(config_name)
    return sorted(configs)

def create_new_config(name: str) -> Path:
    """Creates a new configuration folder and default config file."""
    folder_path = Path(f"ReaperFiles-{name}")
    folder_path.mkdir(exist_ok=True)
    config_path = folder_path / "config.yaml"
    load_config(config_path) # creates default
    return folder_path
