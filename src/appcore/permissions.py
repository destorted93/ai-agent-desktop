"""Permissions (V1 placeholder).

This module will become the single authority for mutating-operation permissions.

Baby step for now:
- Provide a small object we can hang off Runtime.
- Keep current behavior (tools still receive permission_required flags in constructors).

Future direction:
- per-run/per-session/global policies
- allow/deny prompts owned by the UI/app (not tools)
- audit logging
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class PermissionsState:
    filesystem_permission_required: bool = False
    terminal_permission_required: bool = False

    # Safety toggles (placeholder; we will evolve this)
    deny_windows_c_drive: bool = True


class PermissionsManager:
    def __init__(self) -> None:
        self.state = PermissionsState()

    def set_from_config(
        self,
        *,
        filesystem_permission_required: Optional[bool] = None,
        terminal_permission_required: Optional[bool] = None,
        deny_windows_c_drive: Optional[bool] = None,
    ) -> None:
        if isinstance(filesystem_permission_required, bool):
            self.state.filesystem_permission_required = filesystem_permission_required
        if isinstance(terminal_permission_required, bool):
            self.state.terminal_permission_required = terminal_permission_required
        if isinstance(deny_windows_c_drive, bool):
            self.state.deny_windows_c_drive = deny_windows_c_drive
