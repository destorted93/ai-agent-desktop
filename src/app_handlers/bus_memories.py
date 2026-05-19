"""Memories bus handlers (extracted from src/app.py).

Move-first, refactor-later.
- Preserve bus topics.
- Preserve payload/response shapes.
- No UI imports.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List

from ..appcore.runtime_context import Runtime
from ..storage import MemoryManager


def register_memories_handlers(app: Any, bus: Any) -> List[Callable[[], None]]:
    """Register Memories bus handlers. Returns unsubscribe callables."""
    unsubs: List[Callable[[], None]] = []

    # Primary store (app.memory_manager)
    unsubs.append(bus.subscribe("memories.cmd.get_all", lambda ev: bus_memories_get_all(app, ev)))
    unsubs.append(bus.subscribe("memories.cmd.set_all", lambda ev: bus_memories_set_all(app, ev)))
    unsubs.append(bus.subscribe("memories.cmd.clear_all", lambda ev: bus_memories_clear_all(app, ev)))

    # Multi-store memories (per-agent)
    unsubs.append(bus.subscribe("memories.cmd.list_stores", lambda ev: bus_memories_list_stores(app, ev)))
    unsubs.append(bus.subscribe("memories.cmd.get_store", lambda ev: bus_memories_get_store(app, ev)))
    unsubs.append(bus.subscribe("memories.cmd.set_store", lambda ev: bus_memories_set_store(app, ev)))
    unsubs.append(bus.subscribe("memories.cmd.clear_store", lambda ev: bus_memories_clear_store(app, ev)))

    return unsubs


def bus_memories_get_all(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    def work():
        try:
            mem = app.get_memories()
            app._bus_reply(reply_topic, {"status": "success", "memories": mem})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_memories_set_all(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    memories = payload.get("memories")
    if not reply_topic:
        return
    if not isinstance(memories, list):
        app._bus_reply(reply_topic, {"status": "error", "message": "memories must be a list"})
        return

    def work():
        try:
            result = app.set_memories(memories)
            if isinstance(result, dict) and result.get("status") == "success":
                Runtime.get_event_bus().publish("memories.changed", {"action": "set_all"})
            app._bus_reply(reply_topic, result if isinstance(result, dict) else {"status": "error", "message": "Unexpected result"})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_memories_clear_all(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    def work():
        try:
            app.memory_manager.clear()
            Runtime.get_event_bus().publish("memories.changed", {"action": "cleared"})
            app._bus_reply(reply_topic, {"status": "success"})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_memories_list_stores(app: Any, event) -> None:
    """List available per-agent memory stores under app-data Memories/."""
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    def work():
        try:
            from ..storage.memory import list_memory_stores

            stores = list_memory_stores(include_legacy=True)
            app._bus_reply(reply_topic, {"status": "success", "stores": stores})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_memories_get_store(app: Any, event) -> None:
    """Get memories from a specific per-agent store."""
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    agent_id = payload.get("agent_id")
    if not reply_topic:
        return
    if not isinstance(agent_id, str) or not agent_id.strip():
        app._bus_reply(reply_topic, {"status": "error", "message": "agent_id is required"})
        return

    def work():
        try:
            from ..storage.memory import get_legacy_memory_path

            aid = str(agent_id).strip().lower()
            if aid == "legacy":
                mm = MemoryManager(file_path=get_legacy_memory_path())
            else:
                mm = MemoryManager(agent_id=aid)
            mm.load()
            mem = mm.get_memories()
            app._bus_reply(reply_topic, {"status": "success", "agent_id": aid, "memories": mem})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_memories_set_store(app: Any, event) -> None:
    """Replace memories in a specific per-agent store."""
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    agent_id = payload.get("agent_id")
    memories = payload.get("memories")
    if not reply_topic:
        return
    if not isinstance(agent_id, str) or not agent_id.strip():
        app._bus_reply(reply_topic, {"status": "error", "message": "agent_id is required"})
        return
    if not isinstance(memories, list):
        app._bus_reply(reply_topic, {"status": "error", "message": "memories must be a list"})
        return

    def work():
        try:
            from ..storage.memory import get_legacy_memory_path

            aid = str(agent_id).strip().lower()
            if aid == "legacy":
                mm = MemoryManager(file_path=get_legacy_memory_path())
            else:
                mm = MemoryManager(agent_id=aid)

            mm.memories = memories
            result = mm.save()
            if isinstance(result, dict) and result.get("status") == "success":
                Runtime.get_event_bus().publish("memories.changed", {"action": "set_store", "agent_id": aid})
            app._bus_reply(reply_topic, result if isinstance(result, dict) else {"status": "error", "message": "Unexpected result"})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_memories_clear_store(app: Any, event) -> None:
    """Clear a specific per-agent memory store."""
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    agent_id = payload.get("agent_id")
    if not reply_topic:
        return
    if not isinstance(agent_id, str) or not agent_id.strip():
        app._bus_reply(reply_topic, {"status": "error", "message": "agent_id is required"})
        return

    def work():
        try:
            from ..storage.memory import get_legacy_memory_path

            aid = str(agent_id).strip().lower()
            if aid == "legacy":
                mm = MemoryManager(file_path=get_legacy_memory_path())
            else:
                mm = MemoryManager(agent_id=aid)

            res = mm.clear()
            if isinstance(res, dict) and res.get("status") == "success":
                Runtime.get_event_bus().publish("memories.changed", {"action": "cleared_store", "agent_id": aid})
            app._bus_reply(reply_topic, res if isinstance(res, dict) else {"status": "error", "message": "Unexpected result"})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()
