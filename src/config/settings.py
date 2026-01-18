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


class AgentSettings(BaseModel):
    """Agent-related settings."""
    
    model_name: str = Field(default="gpt-5", description="OpenAI model name")
    temperature: float = Field(default=1.0, ge=0.0, le=2.0, description="Model temperature")
    max_turns: int = Field(default=32, ge=1, le=100, description="Max agent turns per request")
    reasoning_effort: str = Field(default="medium", description="Reasoning effort (low/medium/high)")
    reasoning_summary: str = Field(default="auto", description="Reasoning summary mode")
    text_verbosity: str = Field(default="low", description="Response verbosity")
    stream_responses: bool = Field(default=True, description="Stream responses")
    custom_system_prompt: Optional[str] = Field(default=None, description="Custom system prompt override")


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


class Settings(BaseModel):
    """Main application settings."""
    
    # Identity
    agent_name: str = Field(default="Atlas", description="Agent display name")
    user_id: str = Field(default="default_user", description="User identifier")
    
    # API Configuration
    api_key: Optional[str] = Field(default=None, description="OpenAI API key (prefer env var)")
    base_url: Optional[str] = Field(default=None, description="Custom API base URL")
    
    # Sub-settings
    ui: UISettings = Field(default_factory=UISettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    transcribe: TranscribeSettings = Field(default_factory=TranscribeSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)
    tools: ToolSettings = Field(default_factory=ToolSettings)
    
    class Config:
        extra = "ignore"


# Global settings instance
_settings: Optional[Settings] = None


def get_config_path() -> Path:
    """Get the configuration file path."""
    # Check local config first, then app data
    local_config = Path("config.yaml")
    if local_config.exists():
        return local_config
    return get_app_data_dir() / "config.yaml"


def load_settings(config_path: Optional[Path] = None) -> Settings:
    """Load settings from YAML config file."""
    global _settings
    
    if config_path is None:
        config_path = get_config_path()
    
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            _settings = Settings(**data)
        except Exception as e:
            print(f"Warning: Could not load config from {config_path}: {e}")
            _settings = Settings()
    else:
        _settings = Settings()
    
    # Override with environment variables
    if os.getenv("OPENAI_API_KEY"):
        _settings.api_key = os.getenv("OPENAI_API_KEY")
    if os.getenv("OPENAI_BASE_URL"):
        _settings.base_url = os.getenv("OPENAI_BASE_URL")
    
    return _settings


def save_settings(settings: Settings, config_path: Optional[Path] = None) -> None:
    """Save settings to YAML config file."""
    if config_path is None:
        config_path = get_config_path()
    
    # Don't save API key to file (use env vars or keyring)
    data = settings.model_dump(exclude={"api_key"})
    
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def get_settings() -> Settings:
    """Get the global settings instance."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def update_settings(**kwargs) -> Settings:
    """Update settings with new values."""
    global _settings
    settings = get_settings()
    
    # Handle nested updates
    for key, value in kwargs.items():
        if hasattr(settings, key):
            if isinstance(value, dict) and hasattr(getattr(settings, key), "model_dump"):
                # Nested model - merge
                current = getattr(settings, key).model_dump()
                current.update(value)
                setattr(settings, key, type(getattr(settings, key))(**current))
            else:
                setattr(settings, key, value)
    
    save_settings(settings)
    return settings
