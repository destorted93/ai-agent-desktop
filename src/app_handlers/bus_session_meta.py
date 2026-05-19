"""Session meta bus handlers (extracted from src/app.py).

Move-first, refactor-later.
- Preserve bus topic strings.
- Preserve payload/response shapes.
- No UI imports.

Covers:
- session.cmd.meta.set
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List

from ..appcore.runtime_context import Runtime


def register_session_meta_handlers(app: Any, bus: Any) -> List[Callable[[], None]]:
    unsubs: List[Callable[[], None]] = []

    unsubs.append(bus.subscribe("session.cmd.meta.set", lambda ev: bus_session_meta_set(app, ev)))
    unsubs.append(bus.subscribe("session.cmd.telemetry.set", lambda ev: bus_session_telemetry_set(app, ev)))

    return unsubs


def bus_session_meta_set(app: Any, event) -> None:
    """Update session title/description (index metadata).

    Payload:
      - reply_topic (required)
      - session_id (optional; default: active)
      - title (optional)
      - description (optional)

    Reply:
      - status
      - session (updated session meta)
    """
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    session_id = payload.get("session_id")
    title = payload.get("title")
    description = payload.get("description")
    if not reply_topic:
        return

    def work():
        try:
            sid = str(session_id).strip() if isinstance(session_id, str) else ""
            if not sid:
                sid = app.sessions_manager.get_active_session_id()

            t = title if isinstance(title, str) else None
            d = description if isinstance(description, str) else None

            result = app.sessions_manager.update_session_meta(sid, title=t, description=d)
            if isinstance(result, dict) and result.get("status") == "success":
                Runtime.get_event_bus().publish(
                    "session.list.changed",
                    {"action": "meta_updated", "session_id": sid, "active_session_id": app.sessions_manager.get_active_session_id()},
                )
            app._bus_reply(reply_topic, result if isinstance(result, dict) else {"status": "error", "message": "Unexpected result"})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_session_telemetry_set(app: Any, event) -> None:
    """Enable/disable per-run injected telemetry for a session.

    Payload:
      - reply_topic (required)
      - session_id (optional; default: active)
      - enabled (required; bool)

    Reply:
      - status
      - session (updated session meta)
    """
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    session_id = payload.get("session_id")
    enabled = payload.get("enabled")

    if not reply_topic:
        return

    def work():
        try:
            sid = str(session_id).strip() if isinstance(session_id, str) else ""
            if not sid:
                sid = app.sessions_manager.get_active_session_id()

            if enabled is None or not isinstance(enabled, (bool, int)):
                app._bus_reply(reply_topic, {"status": "error", "message": "enabled must be a bool"})
                return

            # Safety: do not allow toggling while inference is running.
            try:
                if app._is_inference_running():
                    app._bus_reply(reply_topic, {"status": "error", "message": "Agent is currently running"})
                    return
            except Exception:
                pass

            val = bool(enabled)
            app.sessions_manager.patch_session_meta(sid, {"telemetry_enabled": val}, touch_updated_at=True)
            updated = app.sessions_manager.get_session_meta(sid)

            Runtime.get_event_bus().publish(
                "session.list.changed",
                {"action": "meta_updated", "session_id": sid, "active_session_id": app.sessions_manager.get_active_session_id()},
            )

            app._bus_reply(reply_topic, {"status": "success", "session": updated})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()
