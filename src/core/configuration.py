from typing import Optional, Union
import yaml
from pathlib import Path
from pydantic import BaseModel, Field

class AppConfig(BaseModel):
    discord_bot_token: Optional[str] = Field(default=None)
    discord_server_id: Optional[str] = Field(default=None)
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
    def target_bot_token(self) -> Optional[str]:
        return self.fluxer_bot_token if self.target_platform == "fluxer" else self.stoat_bot_token

    @property
    def target_server_id(self) -> Optional[str]:
        return self.fluxer_server_id if self.target_platform == "fluxer" else self.stoat_server_id

    @property
    def target_api_url(self) -> Optional[str]:
        return self.fluxer_api_url if self.target_platform == "fluxer" else self.stoat_api_url
    
    @property
    def fluxer_community_id(self) -> Optional[str]:
        return self.fluxer_server_id

def load_config(config_path: Union[str, Path] = "config.yaml", create_if_missing: bool = True) -> AppConfig:
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

    # ── migrate legacy configs that used single target fields ──
    if "fluxer_community_id" in data:
        data.setdefault("fluxer_server_id", data.pop("fluxer_community_id"))

    if "target_bot_token" in data or "target_server_id" in data:
        platform = data.get("target_platform", "fluxer")
        if platform == "fluxer":
            data.setdefault("fluxer_bot_token", data.get("target_bot_token"))
            data.setdefault("fluxer_server_id", data.get("target_server_id"))
            data.setdefault("fluxer_api_url", data.get("target_api_url"))
        elif platform == "stoat":
            data.setdefault("stoat_bot_token", data.get("target_bot_token"))
            data.setdefault("stoat_server_id", data.get("target_server_id"))
            data.setdefault("stoat_api_url", data.get("target_api_url"))
            
        for key in ("target_bot_token", "target_server_id", "target_api_url", "use_fluxer", "use_stoat"):
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
