"""Settings bus handlers (extracted from src/app.py).

Move-first, refactor-later.
- Preserve bus topics.
- Preserve payload/response shapes.
- No UI imports.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List

from ..appcore.runtime_context import Runtime
from ..app_services.settings_helpers import (
    get_current_settings,
    save_settings,
    upsert_confluence_token,
    delete_confluence_token,
)


def register_settings_handlers(app: Any, bus: Any) -> List[Callable[[], None]]:
    """Register Settings bus handlers. Returns unsubscribe callables."""
    unsubs: List[Callable[[], None]] = []

    # Core settings
    unsubs.append(bus.subscribe("settings.cmd.get_current", lambda ev: bus_settings_get_current(app, ev)))
    unsubs.append(bus.subscribe("settings.cmd.save", lambda ev: bus_settings_save(app, ev)))

    # Confluence tokens (per base URL)
    unsubs.append(bus.subscribe("settings.cmd.confluence.upsert", lambda ev: bus_confluence_upsert(app, ev)))
    unsubs.append(bus.subscribe("settings.cmd.confluence.delete", lambda ev: bus_confluence_delete(app, ev)))

    return unsubs


def bus_settings_get_current(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    def work():
        try:
            app._bus_reply(reply_topic, {"status": "success", "settings": get_current_settings(app)})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_settings_save(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    settings = payload.get("settings")
    if not reply_topic:
        return
    if not isinstance(settings, dict):
        app._bus_reply(reply_topic, {"status": "error", "message": "settings must be an object"})
        return

    def work():
        try:
            ok = bool(save_settings(app, settings))
            if ok:
                Runtime.get_event_bus().publish("settings.changed", {"action": "saved"})
                app._bus_reply(reply_topic, {"status": "success"})
            else:
                app._bus_reply(reply_topic, {"status": "error", "message": "Failed to save settings"})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_confluence_upsert(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    base_url = payload.get("base_url")
    token = payload.get("token")

    if not reply_topic:
        return

    def work():
        try:
            nb = upsert_confluence_token(app, base_url=str(base_url or ""), token=str(token or ""))
            Runtime.get_event_bus().publish("settings.changed", {"action": "confluence_token_saved", "base_url": nb})
            app._bus_reply(
                reply_topic,
                {"status": "success", "base_url": nb, "settings": get_current_settings(app)},
            )
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_confluence_delete(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    base_url = payload.get("base_url")

    if not reply_topic:
        return

    def work():
        try:
            nb = delete_confluence_token(app, base_url=str(base_url or ""))
            Runtime.get_event_bus().publish("settings.changed", {"action": "confluence_token_deleted", "base_url": nb})
            app._bus_reply(
                reply_topic,
                {"status": "success", "base_url": nb, "settings": get_current_settings(app)},
            )
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()
