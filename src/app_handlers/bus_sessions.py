"""Sessions bus handlers (extracted from src/app.py).

Move-first, refactor-later.
- Preserve bus topics.
- Preserve payload/response shapes.
- No UI imports.

Covers:
- session.cmd.active.get / set
- session.cmd.list / create_new
- session.cmd.entries.get_wrapped / set_wrapped / clear / delete_from_id
- session.cmd.delete
- session.cmd.group.participants.set
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List

from ..appcore.runtime_context import Runtime


def register_sessions_handlers(app: Any, bus: Any) -> List[Callable[[], None]]:
    unsubs: List[Callable[[], None]] = []

    unsubs.append(bus.subscribe("session.cmd.active.get", lambda ev: bus_session_active_get(app, ev)))
    unsubs.append(bus.subscribe("session.cmd.active.set", lambda ev: bus_session_active_set(app, ev)))

    unsubs.append(bus.subscribe("session.cmd.list", lambda ev: bus_session_list(app, ev)))
    unsubs.append(bus.subscribe("session.cmd.create_new", lambda ev: bus_session_create_new(app, ev)))

    unsubs.append(bus.subscribe("session.cmd.entries.get_wrapped", lambda ev: bus_session_entries_get_wrapped(app, ev)))
    unsubs.append(bus.subscribe("session.cmd.entries.set_wrapped", lambda ev: bus_session_entries_set_wrapped(app, ev)))
    unsubs.append(bus.subscribe("session.cmd.entries.clear", lambda ev: bus_session_entries_clear(app, ev)))
    unsubs.append(bus.subscribe("session.cmd.entries.delete_from_id", lambda ev: bus_session_entries_delete_from_id(app, ev)))

    unsubs.append(bus.subscribe("session.cmd.delete", lambda ev: bus_session_delete(app, ev)))
    unsubs.append(bus.subscribe("session.cmd.group.participants.set", lambda ev: bus_session_group_participants_set(app, ev)))

    # Group participant stores
    unsubs.append(bus.subscribe("session.cmd.group.participant_stores.list", lambda ev: bus_session_group_participant_stores_list(app, ev)))

    return unsubs


def bus_session_active_get(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    def work():
        try:
            sid = app.sessions_manager.get_active_session_id()
            app._bus_reply(reply_topic, {"status": "success", "active_session_id": sid})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_session_active_set(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    session_id = payload.get("session_id")
    if not reply_topic:
        return
    if not session_id:
        app._bus_reply(reply_topic, {"status": "error", "message": "session_id is required"})
        return

    def work():
        try:
            if app._is_inference_running():
                app._bus_reply(reply_topic, {"status": "error", "message": "Agent is currently running"})
                return
            app.sessions_manager.set_active_session_id(str(session_id))
            Runtime.get_event_bus().publish(
                "session.active.changed",
                {"active_session_id": str(session_id)},
            )
            app._bus_reply(reply_topic, {"status": "success", "active_session_id": str(session_id)})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_session_list(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    def work():
        try:
            active = app.sessions_manager.get_active_session_id()
            sessions = app.sessions_manager.list_sessions()
            app._bus_reply(
                reply_topic,
                {"status": "success", "active_session_id": active, "sessions": sessions},
            )
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_session_create_new(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    def work():
        try:
            if app._is_inference_running():
                app._bus_reply(reply_topic, {"status": "error", "message": "Agent is currently running"})
                return
            st = payload.get("session_type")
            if st is None:
                st = payload.get("type")
            parts = payload.get("participants")
            sid = app.sessions_manager.create_new_session(
                session_type=(str(st).strip().lower() if isinstance(st, str) and st.strip() else None),
                participants=(parts if isinstance(parts, list) else None),
            )
            Runtime.get_event_bus().publish(
                "session.active.changed",
                {"active_session_id": sid},
            )
            Runtime.get_event_bus().publish(
                "session.list.changed",
                {"action": "created", "active_session_id": sid},
            )
            app._bus_reply(reply_topic, {"status": "success", "active_session_id": sid, "session_id": sid})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()



def bus_session_group_participants_set(app: Any, event) -> None:
    """Set participants list for a group session (index metadata)."""
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    session_id = payload.get("session_id")
    participants = payload.get("participants")

    if not reply_topic:
        return
    if not isinstance(session_id, str) or not session_id.strip():
        app._bus_reply(reply_topic, {"status": "error", "message": "session_id is required"})
        return
    if not isinstance(participants, list):
        app._bus_reply(reply_topic, {"status": "error", "message": "participants must be a list"})
        return

    def work():
        try:
            try:
                app.config.load()
            except Exception:
                pass

            cleaned = []
            seen_ids = set()
            for p in participants:
                if not isinstance(p, dict):
                    continue
                aid = p.get("agent_id") or p.get("id") or p.get("name")
                aid = str(aid).strip() if isinstance(aid, str) else ""
                if not aid:
                    continue
                if aid in seen_ids:
                    continue
                seen_ids.add(aid)

                spec = app.config.get_agent(aid)
                if spec is None:
                    continue

                dn = p.get("display_name") or p.get("display") or spec.display_name or aid
                dn = str(dn).strip() if isinstance(dn, str) and str(dn).strip() else str(aid)

                cleaned.append({"agent_id": aid, "display_name": dn})

            if not cleaned:
                app._bus_reply(reply_topic, {"status": "error", "message": "No valid participants selected"})
                return

            app.sessions_manager.patch_session_meta(
                str(session_id).strip(),
                {"type": "group", "participants": cleaned},
                touch_updated_at=True,
            )

            updated = app.sessions_manager.get_session_meta(str(session_id).strip())

            Runtime.get_event_bus().publish(
                "session.list.changed",
                {"action": "group_participants_updated", "session_id": str(session_id).strip(), "active_session_id": app.sessions_manager.get_active_session_id()},
            )

            app._bus_reply(reply_topic, {"status": "success", "session": updated})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()



def bus_session_entries_get_wrapped(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    session_id = payload.get("session_id")
    if not reply_topic:
        return
    if not session_id:
        app._bus_reply(reply_topic, {"status": "error", "message": "session_id is required"})
        return

    def work():
        try:
            hist = app.get_session_entries_wrapped(session_id=session_id)
            app._bus_reply(reply_topic, {"status": "success", "entries": hist})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_session_entries_set_wrapped(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    session_id = payload.get("session_id")
    entries = payload.get("entries")
    if not reply_topic:
        return
    if not session_id:
        app._bus_reply(reply_topic, {"status": "error", "message": "session_id is required"})
        return
    if not isinstance(entries, list):
        app._bus_reply(reply_topic, {"status": "error", "message": "entries must be a list"})
        return

    def work():
        try:
            result = app.set_session_entries(entries, session_id=session_id)
            if isinstance(result, dict) and result.get("status") == "success":
                try:
                    app._update_session_token_stats_meta(session_id=session_id)
                except Exception:
                    pass
                Runtime.get_event_bus().publish("session.entries.changed", {"session_id": session_id, "action": "set"})
            app._bus_reply(reply_topic, result if isinstance(result, dict) else {"status": "error", "message": "Unexpected result"})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_session_entries_clear(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    session_id = payload.get("session_id")
    if not reply_topic:
        return

    def work():
        try:
            ok = bool(app.clear_session(session_id=session_id))
            if ok:
                try:
                    app._update_session_token_stats_meta(session_id=session_id)
                except Exception:
                    pass
                Runtime.get_event_bus().publish("session.entries.changed", {"session_id": session_id, "action": "cleared"})
                app._bus_reply(reply_topic, {"status": "success"})
            else:
                app._bus_reply(reply_topic, {"status": "error", "message": "Failed to clear session"})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_session_entries_delete_from_id(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    session_id = payload.get("session_id")
    entry_id = payload.get("entry_id")
    undo_file_edits = bool(payload.get("undo_file_edits"))
    origin_action = payload.get("origin_action")
    origin_action = str(origin_action).strip() if isinstance(origin_action, str) else ""
    if not reply_topic:
        return
    if not session_id:
        app._bus_reply(reply_topic, {"status": "error", "message": "session_id is required"})
        return
    if not entry_id:
        app._bus_reply(reply_topic, {"status": "error", "message": "entry_id is required"})
        return

    def work():
        try:
            result = app.delete_entries_from_id(
                entry_id=str(entry_id),
                session_id=session_id,
                undo_file_edits=bool(undo_file_edits),
                origin_action=origin_action,
            )
            if isinstance(result, dict) and result.get("status") == "success":
                try:
                    app._update_session_token_stats_meta(session_id=session_id)
                except Exception:
                    pass
                Runtime.get_event_bus().publish(
                    "session.entries.changed",
                    {"session_id": session_id, "action": "deleted_from_id", "entry_id": str(entry_id)},
                )
            app._bus_reply(reply_topic, result if isinstance(result, dict) else {"status": "error", "message": "Unexpected result"})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_session_delete(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    session_id = payload.get("session_id")
    if not reply_topic:
        return
    if not session_id:
        app._bus_reply(reply_topic, {"status": "error", "message": "session_id is required"})
        return

    def work():
        try:
            if app._is_inference_running():
                app._bus_reply(reply_topic, {"status": "error", "message": "Agent is currently running"})
                return

            prev_active = ""
            try:
                prev_active = str(app.sessions_manager.get_active_session_id() or "")
            except Exception:
                prev_active = ""

            result = app.delete_session(session_id=str(session_id))
            if isinstance(result, dict) and result.get("status") == "success":
                new_active = result.get("active_session_id")
                new_active = str(new_active) if isinstance(new_active, str) and new_active else ""

                if new_active and new_active != prev_active:
                    Runtime.get_event_bus().publish(
                        "session.active.changed",
                        {
                            "active_session_id": new_active,
                            "previous_session_id": prev_active,
                            "deleted_session_id": str(session_id),
                        },
                    )

                Runtime.get_event_bus().publish(
                    "session.list.changed",
                    {
                        "action": "deleted",
                        "deleted_session_id": str(session_id),
                        "active_session_id": new_active,
                    },
                )

            app._bus_reply(reply_topic, result if isinstance(result, dict) else {"status": "error", "message": "Unexpected result"})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_session_group_participant_stores_list(app: Any, event) -> None:
    """List group participant store_ids for a given session.

    payload:
      - session_id: str
      - reply_topic: str

    response:
      - sources: [{key,label,tooltip,agent_id,display_name}]
    """
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    session_id = payload.get("session_id")
    if not reply_topic:
        return
    if not isinstance(session_id, str) or not session_id.strip():
        app._bus_reply(reply_topic, {"status": "error", "message": "session_id is required"})
        return

    def work():
        try:
            sid = str(session_id).strip()
            meta = app.sessions_manager.get_session_meta(sid) or {}
            parts = meta.get("participants") if isinstance(meta, dict) else None
            parts = parts if isinstance(parts, list) else []

            sources: List[Dict[str, Any]] = []
            for p in parts:
                if not isinstance(p, dict):
                    continue
                aid = p.get("agent_id") or p.get("id") or p.get("name")
                aid = str(aid).strip() if isinstance(aid, str) else ""
                if not aid:
                    continue
                dn = p.get("display_name") or p.get("display") or aid
                dn = str(dn).strip() if isinstance(dn, str) and str(dn).strip() else aid

                key = app.sessions_manager.get_group_participant_store_id(session_id=sid, agent_id=aid)
                sources.append(
                    {
                        "key": key,
                        "label": f"{dn} (group participant)",
                        "tooltip": key,
                        "agent_id": aid,
                        "display_name": dn,
                    }
                )

            app._bus_reply(reply_topic, {"status": "success", "sources": sources})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()
