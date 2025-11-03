import os
import json
from pathlib import Path
from typing import Any, Optional

try:
    import keyring  # Windows Credential Manager / macOS Keychain / Secret Service
except Exception:  # pragma: no cover
    keyring = None  # type: ignore

try:
    from cryptography.fernet import Fernet
except Exception:  # pragma: no cover
    Fernet = None  # type: ignore


APP_NAME = "ai-agent-desktop"
SERVICE_NAME = APP_NAME

def _service_for(name: str) -> str:
    """Return namespaced service string, e.g., ai-agent-desktop/api_token."""
    return f"{SERVICE_NAME}/{name}"


def app_data_dir() -> Path:
    base = os.getenv("APPDATA") or os.path.expanduser("~/.config")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return app_data_dir() / "config.json"


def load_config() -> dict:
    p = config_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(cfg: dict) -> None:
    p = config_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def get_secret(name: str) -> Optional[str]:
    if not keyring:
        return None
    try:
        return keyring.get_password(_service_for(name), name)
    except Exception:
        return None


def set_secret(name: str, value: str) -> None:
    if not keyring:
        raise RuntimeError("keyring package not available")
    keyring.set_password(_service_for(name), name, value)


def delete_secret(name: str) -> None:
    if not keyring:
        return
    try:
        keyring.delete_password(_service_for(name), name)
    except Exception:
        pass


def _get_or_create_data_key() -> Optional[bytes]:
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
    key = _get_or_create_data_key()
    if not (key and Fernet):
        raise RuntimeError("Encryption unavailable: ensure keyring and cryptography are installed")
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    f = Fernet(key)
    blob = f.encrypt(data)
    path.write_bytes(blob)


def read_encrypted_json(path: Path) -> Optional[Any]:
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
