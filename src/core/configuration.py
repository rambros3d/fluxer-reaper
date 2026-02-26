from typing import Optional, Union
import yaml
from pathlib import Path
from pydantic import BaseModel, Field

class MigrationSettings(BaseModel):
    batch_size: int = Field(default=50)
    rate_limit_delay_seconds: int = Field(default=2)
    log_level: str = Field(default="INFO")

class AppConfig(BaseModel):
    discord_bot_token: str
    discord_server_id: str
    fluxer_bot_token: Optional[str] = Field(default=None)
    fluxer_community_id: Optional[str] = Field(default=None)
    fluxer_api_url: Optional[str] = Field(default=None)
    stoat_bot_token: Optional[str] = Field(default=None)
    stoat_server_id: Optional[str] = Field(default=None)
    stoat_api_url: Optional[str] = Field(default=None)
    migration: MigrationSettings = Field(default_factory=MigrationSettings)

def load_config(config_path: Union[str, Path] = "config.yaml") -> AppConfig:
    path = Path(config_path)
    if not path.exists():
        # Create dummy config if missing
        config = AppConfig(
            discord_bot_token="DISCORD_BOT_TOKEN",
            discord_server_id="DISCORD_SERVER_ID",
            fluxer_bot_token="FLUXER_BOT_TOKEN",
            fluxer_community_id="FLUXER_COMMUNITY_ID",
            fluxer_api_url="default",
            stoat_bot_token="STOAT_BOT_TOKEN",
            stoat_server_id="STOAT_SERVER_ID",
            stoat_api_url="default"
        )
        save_config(config, path)
        print(f"Created default configuration: {config_path}")
        return config
    
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    if not data:
        raise ValueError("Configuration file is empty or invalid YAML.")
        
    return AppConfig(**data)

def save_config(config: AppConfig, config_path: Union[str, Path] = "config.yaml"):
    path = Path(config_path)
    # Dump model to dictionary, excluding None values to keep the YAML clean
    data = config.model_dump(exclude_none=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
