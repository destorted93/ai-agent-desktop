"""Sandbox storage root.

This is a dedicated app-data area intended for Aria/Ariane (and future sub-agents)
private artifacts (e.g., canvases, drafts, scratch files).

We keep the path logic in ONE place.
"""

from __future__ import annotations

from pathlib import Path

from .secure import get_app_data_dir


_SANDBOX_DIRNAME = "Sandbox"


def get_sandbox_root(ensure_exists: bool = True) -> Path:
    """Return the absolute sandbox root path under the app data dir.

    If ensure_exists=True, the directory is created (and parents) if missing.
    Raises if it cannot be created.
    """
    root = get_app_data_dir() / _SANDBOX_DIRNAME
    if ensure_exists:
        root.mkdir(parents=True, exist_ok=True)
        if not root.exists() or not root.is_dir():
            raise RuntimeError(f"Sandbox root is not available: {root}")
    return root
