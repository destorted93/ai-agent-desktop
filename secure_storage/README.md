# Secure Storage

Simple, shared helpers for storing configuration, secrets, and encrypted data for the desktop agent.

**What It Provides**
- Config file management (non-secrets)
- Secret storage in the OS keychain
- Encrypted JSON read/write for data-at-rest

**Where Things Live**
- Config (non‑secrets): `%APPDATA%/ai-agent-desktop/config.json`
- Secrets (keychain): Windows Credential Manager entries
  - Service: `ai-agent-desktop/<name>`
  - Username: `<name>`
  - Examples: `ai-agent-desktop/api_token`, `ai-agent-desktop/data_key`
- Encrypted files: `%APPDATA%/ai-agent-desktop/*.enc`
  - Chat history: `chat_history.enc`
  - Memories: `memories.enc`
  - Encryption key: fetched from keychain as `data_key`

**Security Model (Plain Words)**
- Secrets are stored in Windows Credential Manager, protected by your Windows login.
- Files on disk are encrypted with a symmetric key (`data_key`) retrieved from the keychain.
- This protects against offline file theft. Any process running as your user can still request the secrets.

**API**
- `app_data_dir() -> Path`
  - Returns `%APPDATA%/ai-agent-desktop`, creating it if needed.
- `config_path() -> Path`
  - Returns the path to `config.json`.
- `load_config() -> dict`
  - Reads `config.json` if present; otherwise `{}`.
- `save_config(cfg: dict) -> None`
  - Atomic write of `config.json`.
- `get_secret(name: str) -> Optional[str]`
  - Reads secret `name` from keychain service `ai-agent-desktop/<name>` with username `<name>`.
- `set_secret(name: str, value: str) -> None`
  - Writes secret `name` to the keychain.
- `delete_secret(name: str) -> None`
  - Removes secret `name` from the keychain.
- `write_encrypted_json(path: Path, obj: Any) -> None`
  - Encrypts JSON with Fernet and writes to `path`. Requires `keyring` and `cryptography`.
- `read_encrypted_json(path: Path) -> Optional[Any]`
  - Decrypts and returns JSON from `path`; returns `None` if missing or invalid.

**Usage Examples**

- Store and read the API token:
```
from secure_storage import set_secret, get_secret

set_secret("api_token", "sk-your-token")
token = get_secret("api_token")
```

- Save and load encrypted chat history:
```
from pathlib import Path
from secure_storage import app_data_dir, write_encrypted_json, read_encrypted_json

hist_path = app_data_dir() / "chat_history.enc"
write_encrypted_json(hist_path, [{"role": "user", "content": "Hello"}])
msgs = read_encrypted_json(hist_path)
```

- Config (non-secret):
```
from secure_storage import load_config, save_config

cfg = load_config()
cfg["base_url"] = "https://api.example.com"
save_config(cfg)
```

**Notes**
- Dependencies: `keyring` (Windows Credential Manager) and `cryptography` (Fernet).
- Errors: Encrypted read/write requires both packages; otherwise operations return `None` (read) or raise `RuntimeError` (write).
- Naming: Secrets always use `ai-agent-desktop/<name>` as the service and `<name>` as the username. This keeps Credential Manager tidy and predictable.

