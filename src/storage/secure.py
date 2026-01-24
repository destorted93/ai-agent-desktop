"""Secure storage utilities with encryption and keyring integration."""

import os
import json
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import keyring
except ImportError:
    keyring = None  # type: ignore

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None  # type: ignore


APP_NAME = "ai-agent"
SERVICE_NAME = APP_NAME


def get_app_data_dir() -> Path:
    """Get the application data directory."""
    if os.name == "nt":
        base = os.getenv("APPDATA", os.path.expanduser("~"))
    else:
        base = os.getenv("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _service_for(name: str) -> str:
    """Return namespaced service string."""
    return f"{SERVICE_NAME}/{name}"


def get_secret(name: str) -> Optional[str]:
    """Get a secret from the system keyring."""
    if not keyring:
        return None
    try:
        return keyring.get_password(_service_for(name), name)
    except Exception:
        return None


def set_secret(name: str, value: str) -> None:
    """Store a secret in the system keyring."""
    if not keyring:
        raise RuntimeError("keyring package not available")
    keyring.set_password(_service_for(name), name, value)


def delete_secret(name: str) -> None:
    """Delete a secret from the system keyring."""
    if not keyring:
        return
    try:
        keyring.delete_password(_service_for(name), name)
    except Exception:
        pass


def _get_or_create_data_key() -> Optional[bytes]:
    """Get or create the encryption key."""
    if not (keyring and Fernet):
        return None
    
    existing = get_secret("data_key")
    if existing:
        try:
            return existing.encode("utf-8")
        except Exception:
            return None
    
    key = Fernet.generate_key()
    set_secret("data_key", key.decode("utf-8"))
    return key


def write_encrypted_json(path: Path, obj: Any) -> None:
    """Write an object to an encrypted JSON file."""
    key = _get_or_create_data_key()
    if not (key and Fernet):
        raise RuntimeError("Encryption unavailable: ensure keyring and cryptography are installed")
    
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    f = Fernet(key)
    blob = f.encrypt(data)
    
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)


def read_encrypted_json(path: Path) -> Optional[Any]:
    """Read an object from an encrypted JSON file."""
    if not path.exists():
        return None
    
    key = _get_or_create_data_key()
    if not (key and Fernet):
        return None
    
    try:
        content = path.read_bytes()
        f = Fernet(key)
        data = f.decrypt(content)
        return json.loads(data.decode("utf-8"))
    except Exception:
        return None


class SecureStorage:
    """Wrapper class for secure storage operations.
    
    Provides a convenient interface for the widget to access
    secrets and config values.
    """
    
    def __init__(self):
        """Initialize secure storage."""
        self._config_path = get_app_data_dir() / "config.json"
        self._config: Dict[str, Any] = {}
        self._load_config()
    
    def _load_config(self) -> None:
        """Load config from file."""
        if self._config_path.exists():
            try:
                self._config = json.loads(self._config_path.read_text("utf-8"))
            except Exception:
                self._config = {}
    
    def _save_config(self) -> None:
        """Save config to file."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(
            json.dumps(self._config, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    
    def get_secret(self, name: str) -> Optional[str]:
        """Get a secret from the keyring."""
        return get_secret(name)
    
    def set_secret(self, name: str, value: str) -> None:
        """Store a secret in the keyring."""
        set_secret(name, value)
    
    def delete_secret(self, name: str) -> None:
        """Delete a secret from the keyring."""
        delete_secret(name)
    
    def get_config_value(self, key: str, default: Any = None) -> Any:
        """Get a config value."""
        return self._config.get(key, default)
    
    def set_config_value(self, key: str, value: Any) -> None:
        """Set a config value."""
        self._config[key] = value
        self._save_config()
    
    def delete_config_value(self, key: str) -> None:
        """Delete a config value."""
        if key in self._config:
            del self._config[key]
            self._save_config()
