"""App lifecycle bus handlers.

Goal: UI can request a full app restart/quit via the in-process EventBus.
UI must not call app methods directly.

Topics:
- app.cmd.restart

Restart model (Windows-friendly dev loop):
- app exits Qt event loop with a special exit code.
- run.bat can relaunch when it sees that exit code.

No imports from src.ui.
"""

from __future__ import annotations

from typing import Any, Callable, List


RESTART_EXIT_CODE = 75  # arbitrary; used by run.bat loop


def register_app_lifecycle_handlers(app: Any, bus: Any) -> List[Callable[[], None]]:
    unsubs: List[Callable[[], None]] = []
    unsubs.append(bus.subscribe("app.cmd.restart", lambda ev: bus_app_restart(app, ev)))
    return unsubs


def bus_app_restart(app: Any, event) -> None:
    """Request a full app restart.

    Payload (optional):
      - exit_code (int) : override restart exit code
      - reason (str)

    Note: this handler is invoked on the UI thread (EventBus is pumped by a Qt timer).
    """
    payload = getattr(event, "payload", {}) or {}

    # Best-effort: stop any active inference/streams so we don't leave work half-open.
    try:
        if hasattr(app, "stop_agent"):
            app.stop_agent()
    except Exception:
        pass

    code = payload.get("exit_code")
    try:
        code = int(code) if code is not None else RESTART_EXIT_CODE
    except Exception:
        code = RESTART_EXIT_CODE

    # Exit the Qt loop with a distinctive exit code.
    try:
        if getattr(app, "qt_app", None) is not None:
            app.qt_app.exit(code)
            return
    except Exception:
        pass

    # Fallback: hard-exit (should be rare).
    try:
        raise SystemExit(code)
    except Exception:
        pass
