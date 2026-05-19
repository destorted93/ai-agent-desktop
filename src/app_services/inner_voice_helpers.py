"""Inner Voice helpers.

Extracted from inner-voice methods in src/app.py.

Move-first, refactor-later.
- Preserve behavior.
- No UI imports.

Note: bus handlers remain in `src/app_handlers/bus_inner_voice.py`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional




def get_inner_voice_store(app: Any, session_id: Optional[str] = None):
    """Return the session store backing Ariane's persistent inner-voice transcript for the given user session.

    If session_id is None, we use the active user session.
    Fallback (legacy): if no session is available, use the old global inner-voice store.
    """
    sid = str(session_id).strip() if isinstance(session_id, str) else ""
    if not sid:
        try:
            sid = str(app.sessions_manager.get_active_session_id() or "")
        except Exception:
            sid = ""

    if sid:
        store_id = app.sessions_manager.get_subagent_store_id(
            mode="persistent",
            parent_session_id=str(sid),
            subagent_name="ariane",
            subagent_id="ariane",
        )
        return app.sessions_manager.get_subagent_store(store_id)

    # Legacy fallback (pre session-scoped Ariane)
    return app._inner_voice_session_manager


def get_inner_voice_session_entries_wrapped(app: Any, session_id: Optional[str] = None) -> List[Dict]:
    """Get Ariane's inner-voice session (wrapped entries) for the active user session."""
    try:
        sm = get_inner_voice_store(app, session_id=session_id)
        return sm.get_entries_wrapped(limit=None)
    except Exception as e:
        print(f"[App] Error getting inner voice history: {e}")
        return []


def clear_inner_voice_session(app: Any, session_id: Optional[str] = None) -> bool:
    """Clear Ariane's inner-voice session for the active user session."""
    try:
        sm = get_inner_voice_store(app, session_id=session_id)
        return bool(sm.clear())
    except Exception as e:
        print(f"[App] Error clearing inner voice history: {e}")
        return False


def set_inner_voice_session_entries(app: Any, entries: List[Dict], session_id: Optional[str] = None) -> Dict[str, Any]:
    """Replace Ariane's inner-voice session entries for the active user session."""
    try:
        sm = get_inner_voice_store(app, session_id=session_id)
        sm.entries = entries
        sm.save()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
