"""Inner Voice bus handlers (extracted from src/app.py).

Move-first, refactor-later.
- Preserve bus topics.
- Preserve payload/response shapes.
- No UI imports.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List

from ..appcore.runtime_context import Runtime
from ..app_services.inner_voice_helpers import (
    clear_inner_voice_session,
    get_inner_voice_session_entries_wrapped,
    set_inner_voice_session_entries,
)


def register_inner_voice_handlers(app: Any, bus: Any) -> List[Callable[[], None]]:
    """Register Inner Voice bus handlers. Returns unsubscribe callables."""
    unsubs: List[Callable[[], None]] = []

    unsubs.append(bus.subscribe("inner_voice.cmd.session.entries.get", lambda ev: bus_inner_voice_session_entries_get(app, ev)))
    unsubs.append(bus.subscribe("inner_voice.cmd.session.entries.set", lambda ev: bus_inner_voice_session_entries_set(app, ev)))
    unsubs.append(bus.subscribe("inner_voice.cmd.session.entries.clear", lambda ev: bus_inner_voice_session_entries_clear(app, ev)))

    return unsubs


def bus_inner_voice_session_entries_get(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    session_id = payload.get("session_id")
    if not reply_topic:
        return

    def work():
        try:
            hist = get_inner_voice_session_entries_wrapped(app, session_id=session_id)
            app._bus_reply(reply_topic, {"status": "success", "entries": hist})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_inner_voice_session_entries_set(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    entries = payload.get("entries")
    session_id = payload.get("session_id")
    if not reply_topic:
        return
    if not isinstance(entries, list):
        app._bus_reply(reply_topic, {"status": "error", "message": "entries must be a list"})
        return

    def work():
        try:
            result = set_inner_voice_session_entries(app, entries, session_id=session_id)
            if isinstance(result, dict) and result.get("status") == "success":
                Runtime.get_event_bus().publish("inner_voice.session.entries.changed", {"action": "set"})
            app._bus_reply(reply_topic, result if isinstance(result, dict) else {"status": "error", "message": "Unexpected result"})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_inner_voice_session_entries_clear(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    session_id = payload.get("session_id")
    if not reply_topic:
        return

    def work():
        try:
            ok = bool(clear_inner_voice_session(app, session_id=session_id))
            if ok:
                Runtime.get_event_bus().publish("inner_voice.session.entries.changed", {"action": "cleared"})
                app._bus_reply(reply_topic, {"status": "success"})
            else:
                app._bus_reply(reply_topic, {"status": "error", "message": "Failed to clear inner voice session"})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()
