"""Settings helpers.

Extracted from `Application.get_current_settings` / `Application.save_settings` in src/app.py.

Move-first, refactor-later.
- Preserve behavior.
- No UI imports.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..appcore.config_manager import ConfluenceTokenEntry
from ..services.confluence import confluence_token_secret_name, normalize_confluence_base_url


def _get_confluence_bases_from_config(app: Any) -> List[str]:
    bases: List[str] = []
    try:
        refs = getattr(getattr(app.config.app, "confluence", None), "tokens", None) or []
        if isinstance(refs, list):
            for ref in refs:
                try:
                    raw_base = ""
                    if isinstance(ref, dict):
                        raw_base = str(ref.get("base_url") or "")
                    else:
                        raw_base = str(getattr(ref, "base_url", "") or "")
                    nb = normalize_confluence_base_url(raw_base)
                    if nb:
                        bases.append(nb)
                except Exception:
                    continue
    except Exception:
        bases = []

    # Dedupe + stable order
    out = sorted({b for b in bases if isinstance(b, str) and b.strip()})
    return out


def _set_confluence_bases_in_config(app: Any, bases: List[str]) -> None:
    cleaned = sorted({normalize_confluence_base_url(b) for b in (bases or []) if normalize_confluence_base_url(b)})
    app.config.app.confluence.tokens = [ConfluenceTokenEntry(base_url=b) for b in cleaned]


def list_confluence_tokens(app: Any) -> List[Dict[str, str]]:
    """Return [{base_url, token}] for all configured Confluence bases."""
    out: List[Dict[str, str]] = []
    for b in _get_confluence_bases_from_config(app):
        secret_name = confluence_token_secret_name(b)
        tok = app.secure_storage.get_secret(secret_name) or ""
        out.append({"base_url": b, "token": tok})
    return out


def upsert_confluence_token(app: Any, *, base_url: str, token: str) -> str:
    """Create/update a Confluence PAT for a given base URL.

    Returns normalized base_url.
    """
    nb = normalize_confluence_base_url(base_url)
    if not nb:
        raise ValueError("Confluence base_url must be a valid URL")

    tok = str(token or "").strip()
    if not tok:
        raise ValueError("Confluence token is empty")

    # Store token first.
    app.secure_storage.set_secret(confluence_token_secret_name(nb), tok)

    # Ensure base is indexed in config.
    bases = _get_confluence_bases_from_config(app)
    if nb not in bases:
        bases.append(nb)

    try:
        _set_confluence_bases_in_config(app, bases)
        app.config.save_app()
    except Exception as e:
        raise RuntimeError(f"Failed to save Config/app.yaml: {e}")

    try:
        app.config.load()
    except Exception:
        pass

    return nb


def delete_confluence_token(app: Any, *, base_url: str) -> str:
    """Delete a Confluence PAT entry for a given base URL.

    Returns normalized base_url.
    """
    nb = normalize_confluence_base_url(base_url)
    if not nb:
        raise ValueError("Confluence base_url must be a valid URL")

    # Remove token secret.
    try:
        app.secure_storage.delete_secret(confluence_token_secret_name(nb))
    except Exception:
        pass

    # Remove base from config.
    bases = [b for b in _get_confluence_bases_from_config(app) if b != nb]

    try:
        _set_confluence_bases_in_config(app, bases)
        app.config.save_app()
    except Exception as e:
        raise RuntimeError(f"Failed to save Config/app.yaml: {e}")

    try:
        app.config.load()
    except Exception:
        pass

    return nb


def get_current_settings(app: Any) -> Dict[str, Any]:
    """Get current settings for display in UI."""
    # Base URL is non-secret and stored in Config/app.yaml.
    base_url = str(app.config.app.api.base_url or "").strip() or "https://api.openai.com/v1"
    api_mode = str(getattr(app.config.app.api, "mode", None) or "responses").strip() or "responses"
    api_token = app.secure_storage.get_secret("api_token") or ""

    return {
        "api_mode": api_mode,
        "base_url": base_url,
        "api_token": api_token,
        "confluence_tokens": list_confluence_tokens(app),
    }


def save_settings(app: Any, settings: Dict[str, Any]) -> bool:
    """Save core app settings and update services.

    Confluence tokens are managed separately (per-base URL CRUD) and are not
    modified by this function.

    Note: this may raise on validation errors so the bus layer can return a useful
    error message to the UI.
    """

    # Don't mutate live runtime while a run is active.
    if getattr(app, "_is_inference_running", lambda: False)():
        raise RuntimeError("Cannot save settings while inference is running. Stop the run first.")

    base_url = str(settings.get("base_url", "") or "").strip()
    api_token = str(settings.get("api_token", "") or "").strip()
    api_mode = str(settings.get("api_mode", "") or "responses").strip().lower()
    if api_mode not in ("responses", "chat_completions"):
        api_mode = "responses"

    old_mode = str(getattr(app.config.app.api, "mode", None) or "responses").strip().lower()
    mode_changed = (api_mode != old_mode)

    # Save to Config/app.yaml (non-secret).
    try:
        app.config.app.api.base_url = str(base_url)
        app.config.app.api.mode = str(api_mode)
        app.config.save_app()
    except Exception as e:
        raise RuntimeError(f"Failed to save Config/app.yaml: {e}")

    # Reload config so spawned runs see it.
    try:
        app.config.load()
    except Exception:
        pass

    # Save api_token to keyring (secure storage)
    if api_token:
        app.secure_storage.set_secret("api_token", api_token)
        # Update services with new credentials
        app.update_api_key(api_token, base_url if base_url else None)
    else:
        # Clear token if empty - remove from storage and invalidate services
        app.secure_storage.delete_secret("api_token")
        # Clear agent client (empty string will set client to None)
        if app.agent:
            app.agent.update_api_key("", base_url if base_url else None)
        # Clear transcribe service
        app.transcribe_service = None

    # If api.mode changed, rebuild the primary agent instance via factory (hot-apply).
    if mode_changed:
        try:
            from ..app_services.agent_reload import recreate_primary_agent_instance

            recreate_primary_agent_instance(
                app,
                api_key=(api_token if api_token else None),
                base_url=(base_url if base_url else None),
            )
        except Exception:
            pass

    return True
