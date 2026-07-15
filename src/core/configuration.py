from typing import Optional, Union
import yaml
from pathlib import Path
from pydantic import BaseModel, Field

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

def create_new_config(name: str) -> Path:
    """Creates a new configuration folder and default config file."""
    folder_path = Path(f"ReaperFiles-{name}")
    folder_path.mkdir(exist_ok=True)
    config_path = folder_path / "reaper_config.yaml"
    load_config(config_path) # creates default
    return folder_path
