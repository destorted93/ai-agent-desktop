"""Application settings with YAML configuration support."""

import os
from pathlib import Path
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
import yaml


def get_app_data_dir() -> Path:
    """Get the application data directory."""
    if os.name == "nt":
        base = os.getenv("APPDATA", os.path.expanduser("~"))
    else:
        base = os.getenv("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    path = Path(base) / "ai-agent"
    path.mkdir(parents=True, exist_ok=True)
    return path


class APISettings(BaseModel):
    """API connection settings."""
    
    base_url: Optional[str] = Field(default="", description="API base URL")


class UISettings(BaseModel):
    """UI-related settings."""
    
    theme: str = Field(default="dark", description="UI theme (dark/light)")
    widget_opacity: float = Field(default=0.95, ge=0.1, le=1.0, description="Widget opacity")
    widget_width: int = Field(default=60, description="Widget width in pixels")
    widget_height: int = Field(default=60, description="Widget height in pixels")
    chat_width: int = Field(default=600, description="Chat window width")
    chat_height: int = Field(default=700, description="Chat window height")
    always_on_top: bool = Field(default=True, description="Keep windows on top")
    font_size: int = Field(default=13, description="Base font size")
    show_token_usage: bool = Field(default=True, description="Show token usage in UI")


class TranscribeSettings(BaseModel):
    """Transcription settings."""
    
    model: str = Field(default="gpt-4o-transcribe", description="Transcription model")
    language: str = Field(default="en", description="Default language")
    sample_rate: int = Field(default=16000, description="Audio sample rate")


class TTSSettings(BaseModel):
    """Text-to-speech settings."""
    
    model: str = Field(default="gpt-4o-mini-tts", description="TTS model")
    voice: str = Field(default="coral", description="Voice preset")
    format: str = Field(default="mp3", description="Audio format")


class EmbeddingSettings(BaseModel):
    """Embedding settings for RAG/Vector Database."""
    
    model: str = Field(default="text-embedding-3-small", description="Embedding model")


class ToolSettings(BaseModel):
    """Tool-related settings."""
    
    enabled_tools: List[str] = Field(
        default=[
            "memory", "todos", "filesystem", "terminal",
            "documents", "visualization", "web", "image_generation"
        ],
        description="List of enabled tool categories"
    )
    terminal_permission_required: bool = Field(default=False, description="Require permission for terminal commands")
    filesystem_permission_required: bool = Field(default=False, description="Require permission for file writes")
    project_root: Optional[str] = Field(default=None, description="Project root directory (defaults to CWD)")


class AppConfig(BaseModel):
    """Application configuration (non-agent settings)."""
    
    # Identity
    agent_name: str = Field(default="Djasha", description="Agent display name")
    user_id: str = Field(default="default_user", description="User identifier")
    
    # Sub-settings
    api: APISettings = Field(default_factory=APISettings)
    ui: UISettings = Field(default_factory=UISettings)
    transcribe: TranscribeSettings = Field(default_factory=TranscribeSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    tools: ToolSettings = Field(default_factory=ToolSettings)
    
    class Config:
        extra = "ignore"


# Global config instance
_config: Optional[AppConfig] = None


def get_config_path() -> Path:
    """Get the configuration file path."""
    # Check local config first, then app data
    local_config = Path("config.yaml")
    if local_config.exists():
        return local_config
    return get_app_data_dir() / "config.yaml"


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    """Load app config from YAML file."""
    global _config
    
    if config_path is None:
        config_path = get_config_path()
    
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            _config = AppConfig(**data)
        except Exception as e:
            print(f"Warning: Could not load config from {config_path}: {e}")
            _config = AppConfig()
    else:
        _config = AppConfig()
    
    return _config


def get_app_config() -> AppConfig:
    """Get the global app config instance."""
    global _config
    if _config is None:
        _config = load_config()
    return _config
