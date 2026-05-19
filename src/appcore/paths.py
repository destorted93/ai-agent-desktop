"""Paths/roots (V1).

This is the single authority for resolving:
- project_root (project scope)
- sandbox_root (Sandbox scope)
- app_data_root (where Config/sessions/fs_revisions live)

Design goals:
- Keep call sites dumb.
- Avoid threading roots through every tool constructor.
- Centralize safety decisions later (allowlists/deny lists).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..storage.secure import get_app_data_dir
from ..storage.sandbox_storage import get_sandbox_root


@dataclass
class PathsState:
    # If set, overrides config/app.yaml project_root.
    project_root_override: Optional[str] = None


class PathsManager:
    def __init__(self) -> None:
        self.state = PathsState()

    def set_project_root(self, project_root: str) -> None:
        if isinstance(project_root, str) and project_root.strip():
            self.state.project_root_override = project_root.strip()

    def get_app_data_root(self) -> str:
        return str(get_app_data_dir())

    def get_sandbox_root(self, *, ensure_exists: bool = True) -> str:
        return str(get_sandbox_root(ensure_exists=ensure_exists))

    def get_project_root(self, *, config_project_root: Optional[str] = None) -> str:
        # Explicit override wins.
        if isinstance(self.state.project_root_override, str) and self.state.project_root_override.strip():
            return str(Path(self.state.project_root_override).resolve())

        # Config value next.
        if isinstance(config_project_root, str) and config_project_root.strip():
            return str(Path(config_project_root).resolve())

        # Fallback: cwd.
        return str(Path(os.getcwd()).resolve())
