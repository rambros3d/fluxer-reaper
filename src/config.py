import yaml
from pathlib import Path
from pydantic import BaseModel, Field

class MigrationSettings(BaseModel):
    batch_size: int = Field(default=50)
    rate_limit_delay_seconds: int = Field(default=2)
    log_level: str = Field(default="INFO")

class AppConfig(BaseModel):
    discord_bot_token: str
    fluxer_bot_token: str
    discord_server_id: str
    fluxer_community_id: str
    migration: MigrationSettings = Field(default_factory=MigrationSettings)

def load_config(config_path: str | Path = "config.yaml") -> AppConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    if not data:
        raise ValueError("Configuration file is empty or invalid YAML.")
        
    return AppConfig(**data)

def save_config(config: AppConfig, config_path: str | Path = "config.yaml"):
    path = Path(config_path)
    # Dump model to dictionary
    data = config.model_dump()
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
