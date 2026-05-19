"""Multi-session persistence (index + per-session encrypted logs).

KISS goals:
- Always have an active session after startup.
- Session entries live in per-session encrypted files: sessions/{session_id}.enc
- An encrypted index file tracks metadata + active pointer: sessions/index.enc

Health-check/reconcile on startup:
- Missing session files -> drop their index entries.
- Extra session files -> add index entries.
- Missing active -> pick the latest session and set active.

Note: legacy global inner-voice persistence remains separate (session_inner_voice.enc).
Session-scoped persistent sub-agent stores live under sessions/sub-agents/persistent/<session_id>/
and are cleaned up here when their parent session is deleted.
"""

from __future__ import annotations

import os

import threading
import uuid
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .transactions_manager import TransactionsManager
from .fs_diff import compute_transaction_diff_preview

from .secure import get_app_data_dir, read_encrypted_json, write_encrypted_json
from .session import SessionManager


def _now_utc_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _mtime_utc_iso(p: Path) -> str:
    try:
        ts = p.stat().st_mtime
        return (
            datetime.fromtimestamp(ts, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except Exception:
        return _now_utc_iso()


class SessionsManager:
    """Manages multiple Session logs + index metadata."""

    INDEX_SCHEMA_VERSION = 1

    def __init__(self, sessions_dir: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self.sessions_dir = sessions_dir or (get_app_data_dir() / "sessions")
        self.index_path = self.sessions_dir / "index.enc"

        # session_id -> SessionManager
        self._stores: Dict[str, SessionManager] = {}

        self._index: Dict[str, Any] = {}

        # Per-session transactions ledger (mini-git index)
        self.transactions_manager = TransactionsManager()
        self._load_and_reconcile()

    # -----------------------------------------------------------------
    # Index helpers
    # -----------------------------------------------------------------

    def _new_empty_index(self) -> Dict[str, Any]:
        return {
            "schema_version": self.INDEX_SCHEMA_VERSION,
            "active_session_id": None,
            "sessions": [],
        }

    def _read_index(self) -> Optional[Dict[str, Any]]:
        obj = read_encrypted_json(self.index_path)
        return obj if isinstance(obj, dict) else None

    def _write_index(self) -> None:
        write_encrypted_json(self.index_path, self._index)

    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.enc"

    def _list_session_files(self) -> List[str]:
        if not self.sessions_dir.exists():
            return []
        out: List[str] = []
        for p in self.sessions_dir.glob("*.enc"):
            if p.name == "index.enc":
                continue
            sid = p.stem
            if sid:
                out.append(sid)
        return sorted(set(out))

    def _find_latest_session_id(self, session_ids: List[str]) -> Optional[str]:
        best: Tuple[float, str] | None = None
        for sid in session_ids:
            p = self._session_path(sid)
            try:
                t = p.stat().st_mtime
            except Exception:
                t = 0.0
            if best is None or t > best[0]:
                best = (t, sid)
        return best[1] if best else None

    def _ensure_session_file_exists(self, session_id: str) -> None:
        p = self._session_path(session_id)
        if p.exists():
            return
        write_encrypted_json(p, [])

    def _create_session_meta(
        self,
        session_id: str,
        index_num: int,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        items_count: int = 0,
        title: Optional[str] = None,
        description: str = "",
        session_type: Optional[str] = None,
        participants: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        created_at = created_at or _now_utc_iso()
        updated_at = updated_at or created_at

        st = str(session_type).strip().lower() if isinstance(session_type, str) and session_type.strip() else "single"
        if st not in ("single", "group"):
            st = "single"

        if not isinstance(title, str) or not title.strip():
            title = "New Group Session" if st == "group" else "New Session"

        parts = participants if isinstance(participants, list) else []
        parts = [p for p in parts if isinstance(p, dict)]

        return {
            "session_id": session_id,
            "index": int(index_num),
            "title": title,
            "description": description or "",
            "created_at": created_at,
            "updated_at": updated_at,
            "items_count": int(items_count),
            # Session mode (back-compat: legacy sessions may not have this field).
            "type": st,
            # Per-session debug telemetry injection (default ON for new sessions).
            "telemetry_enabled": True,
            # Only meaningful for type='group'.
            "participants": parts,
        }

    def _next_index_num(self, sessions: List[Dict[str, Any]]) -> int:
        mx = 0
        for s in sessions:
            try:
                mx = max(mx, int(s.get("index") or 0))
            except Exception:
                pass
        return mx + 1

    def _reconcile_index_with_files(self) -> None:
        """Reconcile index entries vs actual session files."""
        sessions = self._index.get("sessions")
        if not isinstance(sessions, list):
            sessions = []
            self._index["sessions"] = sessions

        by_id: Dict[str, Dict[str, Any]] = {}
        for s in sessions:
            if isinstance(s, dict) and isinstance(s.get("session_id"), str):
                by_id[s["session_id"]] = s

        file_ids = set(self._list_session_files())

        # Drop orphan index entries.
        kept: List[Dict[str, Any]] = []
        for s in sessions:
            sid = s.get("session_id") if isinstance(s, dict) else None
            if isinstance(sid, str) and sid in file_ids:
                kept.append(s)
        sessions = kept
        self._index["sessions"] = sessions

        # Add missing index entries for extra files.
        known_ids = {s.get("session_id") for s in sessions if isinstance(s, dict)}
        known_ids = {sid for sid in known_ids if isinstance(sid, str)}
        extra_files = [sid for sid in file_ids if sid not in known_ids]
        if extra_files:
            next_idx = self._next_index_num(sessions)
            for sid in sorted(extra_files):
                p = self._session_path(sid)
                data = read_encrypted_json(p)
                count = len(data) if isinstance(data, list) else 0
                meta = self._create_session_meta(
                    session_id=sid,
                    index_num=next_idx,
                    created_at=_mtime_utc_iso(p),
                    updated_at=_mtime_utc_iso(p),
                    items_count=count,
                    title=f"Session {next_idx}",
                )
                sessions.append(meta)
                next_idx += 1

        # Ensure required fields exist.
        for s in sessions:
            if not isinstance(s, dict):
                continue
            if not s.get("title"):
                try:
                    s["title"] = "New Session"
                except Exception:
                    s["title"] = "Session"
            if "description" not in s:
                s["description"] = ""
            if "created_at" not in s or not s.get("created_at"):
                s["created_at"] = _now_utc_iso()
            if "updated_at" not in s or not s.get("updated_at"):
                s["updated_at"] = s.get("created_at") or _now_utc_iso()
            if "items_count" not in s or s.get("items_count") is None:
                s["items_count"] = 0

            # Group-session fields (back-compat: old sessions may not have these).
            st = s.get("type")
            if not isinstance(st, str):
                st = "single"
            st = st.strip().lower()
            if st not in ("single", "group"):
                st = "single"
            s["type"] = st

            if "participants" not in s or not isinstance(s.get("participants"), list):
                s["participants"] = []


        # Sort by index (stable ordering).
        def _key(m: Dict[str, Any]) -> int:
            try:
                return int(m.get("index") or 0)
            except Exception:
                return 0

        sessions.sort(key=_key)

        # Ensure at least one session exists.
        if not sessions:
            sid = str(uuid.uuid4())
            self._ensure_session_file_exists(sid)
            meta = self._create_session_meta(session_id=sid, index_num=1)
            sessions.append(meta)

        # Ensure active exists.
        active = self._index.get("active_session_id")
        existing_ids = [s.get("session_id") for s in sessions if isinstance(s, dict)]
        existing_ids = [sid for sid in existing_ids if isinstance(sid, str)]
        if not isinstance(active, str) or active not in existing_ids:
            picked = self._find_latest_session_id(existing_ids) or existing_ids[-1]
            self._index["active_session_id"] = picked

    def _migrate_legacy_session_if_present(self) -> None:
        legacy = get_app_data_dir() / "session.enc"
        if not legacy.exists():
            return

        # If we already renamed it, bail.
        if legacy.with_suffix(".enc.legacy.bak").exists():
            return

        active = self.get_active_session_id()
        if not active:
            return

        # If decrypt fails, don't destroy legacy.
        legacy_data = read_encrypted_json(legacy)
        if not isinstance(legacy_data, list):
            return

        # Only migrate if the target session is empty (prevents accidental duplication).
        tgt_path = self._session_path(active)
        tgt_data = read_encrypted_json(tgt_path)
        if isinstance(tgt_data, list) and len(tgt_data) > 0:
            # Target already has data; keep legacy untouched.
            return

        write_encrypted_json(tgt_path, legacy_data)

        # Update metadata for active session.
        self._touch_session_meta(active, items_count=len(legacy_data))

        # Rename legacy for safety.
        try:
            legacy.rename(legacy.with_suffix(".enc.legacy.bak"))
        except Exception:
            # Fallback: leave it there.
            pass

    def _load_and_reconcile(self) -> None:
        with self._lock:
            self.sessions_dir.mkdir(parents=True, exist_ok=True)

            idx = self._read_index()
            self._index = idx if idx else self._new_empty_index()

            self._reconcile_index_with_files()
            self._write_index()

            # Best-effort legacy migration.
            self._migrate_legacy_session_if_present()

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def list_sessions(self) -> List[Dict[str, Any]]:
        with self._lock:
            sessions = self._index.get("sessions")
            return list(sessions) if isinstance(sessions, list) else []

    def get_active_session_id(self) -> str:
        with self._lock:
            active = self._index.get("active_session_id")
            if isinstance(active, str) and active:
                return active
            # Should never happen after reconcile, but fail-safe.
            self._reconcile_index_with_files()
            self._write_index()
            active = self._index.get("active_session_id")
            return active if isinstance(active, str) else ""

    def set_active_session_id(self, session_id: str) -> None:
        with self._lock:
            if not isinstance(session_id, str) or not session_id:
                raise ValueError("session_id is required")

            sessions = self.list_sessions()
            ids = {s.get("session_id") for s in sessions if isinstance(s, dict)}
            if session_id not in ids:
                raise KeyError("Unknown session_id")

            self._index["active_session_id"] = session_id
            self._write_index()

    def create_new_session(
        self,
        *,
        session_type: Optional[str] = None,
        participants: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        with self._lock:
            sessions = self.list_sessions()
            next_idx = self._next_index_num(sessions)
            sid = str(uuid.uuid4())
            self._ensure_session_file_exists(sid)
            meta = self._create_session_meta(
                session_id=sid,
                index_num=next_idx,
                session_type=session_type,
                participants=participants,
            )
            sessions.append(meta)
            self._index["sessions"] = sessions
            self._index["active_session_id"] = sid
            self._write_index()
            return sid


    def delete_session(self, session_id: str) -> Dict[str, Any]:
        """Delete a full session log and keep the index in a valid state.

        Rules:
        - If the deleted session is active, switch to the next session in list order.
        - If the deleted session was the last remaining session, create a fresh replacement.
        - Evict any cached SessionManager for the deleted session.
        - Delete session-scoped persistent sub-agent stores under
          sessions/sub-agents/persistent/<session_id>/ (best-effort, warning on failure).
        """
        with self._lock:
            if not isinstance(session_id, str) or not session_id:
                return {"status": "error", "message": "session_id is required"}

            sessions = self._index.get("sessions")
            if not isinstance(sessions, list):
                sessions = []
                self._index["sessions"] = sessions

            doomed_idx = None
            doomed_meta = None
            for i, s in enumerate(sessions):
                if isinstance(s, dict) and s.get("session_id") == session_id:
                    doomed_idx = i
                    doomed_meta = s
                    break

            if doomed_idx is None or not isinstance(doomed_meta, dict):
                return {"status": "error", "message": "Unknown session_id"}

            active_before = self._index.get("active_session_id")
            active_before = active_before if isinstance(active_before, str) else ""
            deleting_active = active_before == session_id

            remaining_sessions = [
                s for s in sessions
                if isinstance(s, dict) and s.get("session_id") != session_id
            ]

            successor_id = active_before if (active_before and active_before != session_id) else ""
            if deleting_active and remaining_sessions:
                pick_idx = doomed_idx if doomed_idx < len(remaining_sessions) else (len(remaining_sessions) - 1)
                picked = remaining_sessions[pick_idx] if 0 <= pick_idx < len(remaining_sessions) else None
                sid2 = picked.get("session_id") if isinstance(picked, dict) else None
                successor_id = sid2 if isinstance(sid2, str) else ""

            created_replacement = False
            replacement_meta = None
            replacement_session_id = ""

            # If this was the last remaining session, prepare a fresh replacement first.
            # We only commit it into the index after the doomed session file is successfully removed.
            if deleting_active and not remaining_sessions:
                replacement_session_id = str(uuid.uuid4())
                next_idx = self._next_index_num(sessions)
                try:
                    self._ensure_session_file_exists(replacement_session_id)
                except Exception as e:
                    return {"status": "error", "message": f"Failed to create replacement session: {e}"}
                replacement_meta = self._create_session_meta(
                    session_id=replacement_session_id,
                    index_num=next_idx,
                )
                successor_id = replacement_session_id
                created_replacement = True

            doomed_path = self._session_path(session_id)
            try:
                if doomed_path.exists():
                    doomed_path.unlink()
            except Exception as e:
                if replacement_session_id:
                    try:
                        rp = self._session_path(replacement_session_id)
                        if rp.exists():
                            rp.unlink()
                    except Exception:
                        pass
                return {"status": "error", "message": f"Failed to delete session file: {e}"}

            cleanup_warnings: List[str] = []
            try:
                persistent_root = self.sessions_dir / "sub-agents" / "persistent" / str(session_id)
                if persistent_root.exists():
                    shutil.rmtree(persistent_root)
            except Exception as e:
                cleanup_warnings.append(f"Failed to delete persistent sub-agent stores for session {session_id}: {e}")

            self._stores.pop(session_id, None)

            new_sessions = list(remaining_sessions)
            if isinstance(replacement_meta, dict):
                new_sessions.append(replacement_meta)

            # Delete the per-session transactions ledger too (txns are not stored in the session log).
            try:
                self.transactions_manager.delete_session_ledger(session_id=str(session_id))
            except Exception as e:
                cleanup_warnings.append(f"Failed to delete transactions ledger for session {session_id}: {e}")

            # Fail-safe: never leave the index without at least one session.
            if not new_sessions:
                replacement_session_id = str(uuid.uuid4())
                next_idx = self._next_index_num(sessions)
                try:
                    self._ensure_session_file_exists(replacement_session_id)
                except Exception as e:
                    return {"status": "error", "message": f"Failed to create fallback session: {e}"}
                replacement_meta = self._create_session_meta(
                    session_id=replacement_session_id,
                    index_num=next_idx,
                )
                new_sessions.append(replacement_meta)
                successor_id = replacement_session_id
                created_replacement = True

            self._index["sessions"] = new_sessions

            valid_ids = {
                s.get("session_id")
                for s in new_sessions
                if isinstance(s, dict) and isinstance(s.get("session_id"), str)
            }
            valid_ids = {sid for sid in valid_ids if isinstance(sid, str) and sid}

            first_valid_id = ""
            for s in new_sessions:
                sid3 = s.get("session_id") if isinstance(s, dict) else None
                if isinstance(sid3, str) and sid3:
                    first_valid_id = sid3
                    break

            active_after = active_before
            if not isinstance(active_after, str) or not active_after or active_after == session_id or active_after not in valid_ids:
                if successor_id and successor_id in valid_ids:
                    active_after = successor_id
                else:
                    active_after = first_valid_id
            self._index["active_session_id"] = active_after
            self._write_index()

            result = {
                "status": "success",
                "deleted_session_id": str(session_id),
                "active_session_id": str(active_after),
                "created_replacement_session": bool(created_replacement),
                "replacement_session_id": (str(replacement_session_id) if replacement_session_id else None),
            }
            if cleanup_warnings:
                result["cleanup_warnings"] = cleanup_warnings
            return result

    def update_session_meta(
        self,
        session_id: str,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update session title/description in the sessions index.

        Notes:
        - This updates only the index metadata (sessions/index.enc), not the session log.
        - `updated_at` is refreshed.
        - Passing None leaves a field unchanged.
        """
        with self._lock:
            if not isinstance(session_id, str) or not session_id:
                return {"status": "error", "message": "session_id is required"}

            sessions = self._index.get("sessions")
            if not isinstance(sessions, list):
                sessions = []
                self._index["sessions"] = sessions

            found = None
            for s in sessions:
                if isinstance(s, dict) and s.get("session_id") == session_id:
                    found = s
                    break

            if found is None:
                return {"status": "error", "message": "Unknown session_id"}

            changed = False
            if isinstance(title, str):
                t = title.strip()
                if t:
                    found["title"] = t
                    changed = True
            if isinstance(description, str):
                found["description"] = description
                changed = True

            if not changed:
                return {"status": "error", "message": "Nothing to update (provide title and/or description)"}

            found["updated_at"] = _now_utc_iso()
            self._write_index()
            return {"status": "success", "session": dict(found)}


    def get_session_meta(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Return the session's index meta entry (from sessions/index.enc)."""
        with self._lock:
            if not isinstance(session_id, str) or not session_id:
                return None
            sessions = self._index.get("sessions")
            if not isinstance(sessions, list):
                return None
            for s in sessions:
                if isinstance(s, dict) and s.get("session_id") == session_id:
                    return dict(s)
        return None

    def get_session_store(self, session_id: str) -> SessionManager:
        with self._lock:
            if not isinstance(session_id, str) or not session_id:
                raise ValueError("session_id is required")

            sessions = self.list_sessions()
            known = {s.get("session_id") for s in sessions if isinstance(s, dict)}
            if session_id not in known:
                raise KeyError("Unknown session_id")

            if session_id not in self._stores:
                self._stores[session_id] = SessionManager(file_path=self._session_path(session_id))
            return self._stores[session_id]

    def get_entries_wrapped(self, session_id: str) -> List[Dict[str, Any]]:
        """Return wrapped entries for UI display.

        Note: transaction_ids are derived from the TransactionsManager ledger and are
        NOT persisted inside the session log.
        """
        with self._lock:
            store = self.get_session_store(session_id)
            raw = store.get_entries_wrapped(limit=None)
            # Shallow-copy so we don't mutate the persisted store entries.
            out = [dict(e) for e in (raw or []) if isinstance(e, dict)]

            # Attach derived txns for UI diff badges (best-effort).
            try:
                entry_ids = [e.get("id") for e in out if isinstance(e.get("id"), str)]
                entry_ids2 = [str(x) for x in entry_ids if isinstance(x, str) and x]
                m = self.transactions_manager.get_txn_map_for_entry_ids(session_id=str(session_id), entry_ids=entry_ids2)
                if isinstance(m, dict) and m:
                    for we in out:
                        eid = we.get("id")
                        if isinstance(eid, str) and eid in m:
                            we["transaction_ids"] = m.get(eid) or []
                            we["transaction_ids_derived"] = True
            except Exception:
                pass

            return out

    def get_messages(self, session_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            store = self.get_session_store(session_id)
            return store.get_messages(limit=None)

    def get_messages_for_agent(self, session_id: str) -> List[Dict[str, Any]]:
        """Return items suitable for agent context.

        Always excluded (global):
        - reasoning
        - run_summary (app entry)
        - system_notice

        If a run is summarized (its app run_summary.description is non-empty), then
        for that run_id we keep only:
        - the triggering user message (non-injected)
        - the run_summary tool call + its function_call_output
        - the last assistant message (non-injected)

        Everything else from that run (other tool calls/outputs, intermediate assistant
        messages, injected messages) is filtered from model context.

        This does NOT delete anything from storage.
        """

        wrapped = self.get_entries_wrapped(session_id=session_id)

        # Context pruning: some read-only tools can opt out of surviving into future agent context
        # by returning wrapper meta {"survive": false}.
        #
        # IMPORTANT: This does not modify storage or the UI timeline. It only affects the
        # projection used for model context in future runs.
        drop_call_ids: set[str] = set()
        try:
            for we in (wrapped or []):
                if not isinstance(we, dict):
                    continue
                if we.get("survive") is False:
                    c = we.get("content")
                    if isinstance(c, dict) and str(c.get("type") or "") in ("function_call", "function_call_output"):
                        cid = c.get("call_id")
                        if isinstance(cid, str) and cid:
                            drop_call_ids.add(cid)
        except Exception:
            drop_call_ids = set()

        def _inject_user_meta(we: Dict[str, Any], c: Dict[str, Any]) -> Dict[str, Any]:
            """Inject wrapper-only user-message meta into model-visible content.

            Canonical storage is wrapper-only. Model sees synthesized:
            - META(received_at=...)
            - META(files=[...]) / META(dirs=[...])
            - input_image items extracted from wrapper image_attachments

            Also: if wrapper attachments exist, we drop legacy "Attached files:" blocks from content to avoid duplication.
            """
            try:
                if not isinstance(we, dict) or not isinstance(c, dict):
                    return c
                if str(c.get("type") or "") != "message":
                    return c
                if str(c.get("role") or "") != "user":
                    return c
                if bool(we.get("injected")):
                    return c

                content_obj = c.get("content")

                # Build meta lines
                meta_items: List[Dict[str, Any]] = []

                ra_local = we.get("received_at_local_readable")
                ra_local = str(ra_local).strip() if isinstance(ra_local, str) else ""
                if ra_local:
                    meta_items.append({"type": "input_text", "text": f"META(received_at_local=\"{ra_local}\")\n"})

                ra_utc = we.get("received_at_readable")
                if not (isinstance(ra_utc, str) and ra_utc.strip()):
                    ra_utc = we.get("received_at_utc")
                ra_utc = str(ra_utc).strip() if isinstance(ra_utc, str) else ""
                if ra_utc:
                    meta_items.append({"type": "input_text", "text": f"META(received_at_utc=\"{ra_utc}\")\n"})

                def _norm_meta_path(p: str) -> str:
                    try:
                        import os

                        s = os.path.normpath(str(p))
                        if os.name == "nt":
                            try:
                                drv, rest = os.path.splitdrive(s)
                                if drv:
                                    s = drv.upper() + rest
                            except Exception:
                                pass
                        return s
                    except Exception:
                        return str(p)

                atts = we.get("attachments") if isinstance(we.get("attachments"), list) else []
                files_meta = [_norm_meta_path(a.get("path")) for a in (atts or []) if isinstance(a, dict) and a.get("kind") == "file" and isinstance(a.get("path"), str) and a.get("path")]
                dirs_meta = [_norm_meta_path(a.get("path")) for a in (atts or []) if isinstance(a, dict) and a.get("kind") == "dir" and isinstance(a.get("path"), str) and a.get("path")]
                if files_meta:
                    meta_items.append({"type": "input_text", "text": f"META(files={files_meta})\n"})
                if dirs_meta:
                    meta_items.append({"type": "input_text", "text": f"META(dirs={dirs_meta})\n"})

                # Extract legacy content items (text + images)
                legacy_items: List[Dict[str, Any]] = []
                if isinstance(content_obj, list):
                    legacy_items = [it for it in content_obj if isinstance(it, dict)]
                elif isinstance(content_obj, str) and content_obj:
                    legacy_items = [{"type": "input_text", "text": content_obj}]

                # Drop legacy attached-files block if wrapper attachments exist.
                if (files_meta or dirs_meta) and legacy_items:
                    cleaned: List[Dict[str, Any]] = []
                    for it in legacy_items:
                        if it.get("type") != "input_text":
                            cleaned.append(it)
                            continue
                        txt = it.get("text")
                        if not isinstance(txt, str):
                            cleaned.append(it)
                            continue
                        s = txt.strip()
                        if s.startswith("Attached files:") or "\nAttached files:" in s:
                            continue
                        cleaned.append(it)
                    legacy_items = cleaned

                # Images: prefer wrapper meta, fall back to legacy input_image items.
                injected_images: List[Dict[str, Any]] = []
                ims = we.get("image_attachments") if isinstance(we.get("image_attachments"), list) else []
                for im in ims:
                    if not isinstance(im, dict):
                        continue
                    b64 = im.get("b64")
                    mime = im.get("mime") or "image/png"
                    if isinstance(b64, str) and b64:
                        injected_images.append({"type": "input_image", "image_url": f"data:{mime};base64,{b64}"})

                if injected_images:
                    # remove legacy images to avoid duplicates
                    legacy_items = [it for it in legacy_items if it.get("type") != "input_image"]

                # If legacy already starts with META(...), don't add our meta_items again.
                has_legacy_meta = False
                if legacy_items:
                    first = legacy_items[0] if isinstance(legacy_items[0], dict) else None
                    if isinstance(first, dict) and first.get("type") == "input_text" and isinstance(first.get("text"), str):
                        if str(first.get("text") or "").startswith("META("):
                            has_legacy_meta = True

                # Prefix the human's actual text (agent-facing only).
                try:
                    did_prefix = False
                    for i, it in enumerate(list(legacy_items or [])):
                        if not isinstance(it, dict) or it.get("type") != "input_text":
                            continue
                        txt = it.get("text")
                        if not (isinstance(txt, str) and txt.strip()):
                            continue
                        s = txt.lstrip()
                        if s.startswith("META("):
                            continue
                        if s.lower().startswith("human:") or s.lower().startswith("user:"):
                            did_prefix = True
                            break
                        legacy_items[i] = {"type": "input_text", "text": f"Human: {txt}"}
                        did_prefix = True
                        break
                except Exception:
                    pass

                new_content = []
                if not has_legacy_meta:
                    new_content.extend(meta_items)
                new_content.extend(legacy_items)
                new_content.extend(injected_images)

                if not new_content:
                    return c

                c2 = dict(c)
                c2["content"] = new_content
                return c2
            except Exception:
                return c

        # Find summarized runs by looking at app run_summary entries.
        summarized_run_ids: set[str] = set()
        for we in (wrapped or []):
            if not isinstance(we, dict):
                continue
            c = we.get("content")
            if not isinstance(c, dict):
                continue
            if str(c.get("type") or "") != "run_summary":
                continue
            desc = c.get("description")
            if not (isinstance(desc, str) and desc.strip()):
                continue

            rid = we.get("run_id")
            if not isinstance(rid, str) or not rid.strip():
                rid = c.get("run_id")
            rid = str(rid).strip() if isinstance(rid, str) else ""
            if rid:
                summarized_run_ids.add(rid)

        if not summarized_run_ids:
            # Fast path: just do the classic global exclusions.
            out: List[Dict[str, Any]] = []
            for we in (wrapped or []):
                if not isinstance(we, dict):
                    continue
                c = we.get("content")
                if not isinstance(c, dict):
                    continue
                if c.get("type") in ("reasoning", "run_summary", "system_notice"):
                    continue

                # Drop tool calls/outputs that opted out of surviving into future context.
                if we.get("survive") is False:
                    continue

                # Also drop injected messages originating from a dropped tool call (e.g., images_get).
                if bool(we.get("injected")):
                    ocid = we.get("origin_tool_call_id")
                    if isinstance(ocid, str) and ocid in drop_call_ids:
                        continue

                out.append(_inject_user_meta(we, c))
            return out

        # Precompute keep-sets per summarized run.
        keep_entry_ids_by_run: Dict[str, set[str]] = {rid: set() for rid in summarized_run_ids}
        first_user_id: Dict[str, str] = {}
        last_assistant_id: Dict[str, str] = {}
        run_summary_call_ids: Dict[str, set[str]] = {rid: set() for rid in summarized_run_ids}

        # Pass 1: find first user, last assistant, run_summary call ids.
        for we in (wrapped or []):
            if not isinstance(we, dict):
                continue
            rid = we.get("run_id")
            rid = str(rid).strip() if isinstance(rid, str) else ""
            if rid not in summarized_run_ids:
                continue

            eid = we.get("id")
            if not isinstance(eid, str) or not eid:
                continue

            c = we.get("content")
            if not isinstance(c, dict):
                continue
            ctype = str(c.get("type") or "")

            if ctype == "message":
                role = str(c.get("role") or "")
                if role == "user" and not bool(we.get("injected")):
                    if rid not in first_user_id:
                        first_user_id[rid] = eid
                elif role == "assistant" and not bool(we.get("injected")):
                    last_assistant_id[rid] = eid

            if ctype == "function_call" and str(c.get("name") or "") == "run_summary":
                keep_entry_ids_by_run[rid].add(eid)
                cid = c.get("call_id")
                if isinstance(cid, str) and cid:
                    run_summary_call_ids[rid].add(cid)

        # Pass 2: keep function_call_output for run_summary calls.
        for we in (wrapped or []):
            if not isinstance(we, dict):
                continue
            rid = we.get("run_id")
            rid = str(rid).strip() if isinstance(rid, str) else ""
            if rid not in summarized_run_ids:
                continue

            eid = we.get("id")
            if not isinstance(eid, str) or not eid:
                continue

            c = we.get("content")
            if not isinstance(c, dict):
                continue
            if str(c.get("type") or "") != "function_call_output":
                continue

            cid = c.get("call_id")
            if isinstance(cid, str) and cid and cid in run_summary_call_ids.get(rid, set()):
                keep_entry_ids_by_run[rid].add(eid)

        # Always keep first user + last assistant when present.
        for rid, eid in first_user_id.items():
            keep_entry_ids_by_run.setdefault(rid, set()).add(eid)
        for rid, eid in last_assistant_id.items():
            keep_entry_ids_by_run.setdefault(rid, set()).add(eid)

        # Build final content list.
        out: List[Dict[str, Any]] = []
        for we in (wrapped or []):
            if not isinstance(we, dict):
                continue
            c = we.get("content")
            if not isinstance(c, dict):
                continue

            if c.get("type") in ("reasoning", "run_summary", "system_notice"):
                continue

            # Drop tool calls/outputs that opted out of surviving into future context.
            if we.get("survive") is False:
                continue

            # Also drop injected messages originating from a dropped tool call (e.g., images_get).
            if bool(we.get("injected")):
                ocid = we.get("origin_tool_call_id")
                if isinstance(ocid, str) and ocid in drop_call_ids:
                    continue

            rid = we.get("run_id")
            rid = str(rid).strip() if isinstance(rid, str) else ""
            if rid in summarized_run_ids:
                eid = we.get("id")
                if isinstance(eid, str) and eid in keep_entry_ids_by_run.get(rid, set()):
                    out.append(_inject_user_meta(we, c))
                continue

            out.append(_inject_user_meta(we, c))

        return out

    def set_run_summary_description(self, session_id: str, run_id: str, description: str) -> bool:
        """Update the app-generated run_summary entry's description for a given run_id.

        Phase 2.1 support: allows summarizing older runs later.
        Returns True if an entry was updated.
        """
        sid = str(session_id or "").strip()
        rid = str(run_id or "").strip()
        desc = str(description or "").strip()
        if not sid or not rid or not desc:
            return False

        with self._lock:
            store = self.get_session_store(sid)
            changed = False
            for we in (store.entries or []):
                if not isinstance(we, dict):
                    continue
                c = we.get("content")
                if not isinstance(c, dict) or str(c.get("type") or "") != "run_summary":
                    continue

                we_rid = we.get("run_id")
                if not isinstance(we_rid, str) or not we_rid.strip():
                    we_rid = c.get("run_id")
                we_rid = str(we_rid).strip() if isinstance(we_rid, str) else ""

                if we_rid != rid:
                    continue

                c2 = dict(c)
                c2["description"] = desc
                we["content"] = c2
                changed = True
                break

            if changed:
                store.save()
            return changed

    def replace_entries_wrapped(self, session_id: str, entries: List[Dict[str, Any]]) -> None:
        """Replace wrapped entries for a session.

        Defensive: strip transaction ids fields before persisting (txns live in the ledger).
        """
        def _strip_txn_fields(lst: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            for e in (lst or []):
                if not isinstance(e, dict):
                    continue
                e2 = dict(e)
                e2.pop("transaction_ids", None)
                e2.pop("transaction_ids_derived", None)
                # Also strip run_summary content.transaction_ids if present.
                c = e2.get("content")
                if isinstance(c, dict) and str(c.get("type") or "") == "run_summary":
                    c2 = dict(c)
                    c2.pop("transaction_ids", None)
                    e2["content"] = c2
                out.append(e2)
            return out

        with self._lock:
            store = self.get_session_store(session_id)
            store.entries = _strip_txn_fields(list(entries or []))
            store.save()
            self._touch_session_meta(session_id, items_count=len(store.entries))

    def clear_entries(self, session_id: str) -> None:
        with self._lock:
            store = self.get_session_store(session_id)
            store.clear()
            self._touch_session_meta(session_id, items_count=0)

    def append_entries(
        self,
        session_id: str,
        entries: List[Dict[str, Any]],
        wrap_meta_by_call_id: Optional[Dict[str, Dict[str, Any]]] = None,
        wrap_meta_by_item_index: Optional[Dict[Any, Dict[str, Any]]] = None,
        run_id: Optional[str] = None,
    ) -> List[str]:
        with self._lock:
            store = self.get_session_store(session_id)

            # Phase 1: link txn_ids to the main session entry_id at persist time.
            # We intentionally do NOT store transaction_ids in session logs.
            txns_by_index: List[List[str]] = []
            try:
                for e in (entries or []):
                    if isinstance(e, dict):
                        txns_by_index.append(store.extract_transaction_ids(e, wrap_meta_by_call_id=wrap_meta_by_call_id))
                    else:
                        txns_by_index.append([])
            except Exception:
                txns_by_index = [[] for _ in (entries or [])]

            ids = store.append_entries(
                entries or [],
                wrap_meta_by_call_id=wrap_meta_by_call_id,
                wrap_meta_by_item_index=wrap_meta_by_item_index,
                run_id=run_id,
            )

            # Link txn_ids -> entry_id in the per-session transactions ledger.
            try:
                for i, eid in enumerate(ids):
                    txs = txns_by_index[i] if i < len(txns_by_index) else []
                    if not txs:
                        continue
                    self.transactions_manager.link_txns_to_entry(
                        session_id=str(session_id),
                        entry_id=str(eid),
                        txn_ids=[str(t) for t in txs if isinstance(t, str) and t],
                        run_id=(str(run_id) if isinstance(run_id, str) and run_id else None),
                        actor=None,
                    )
            except Exception:
                pass

            # Special case: if an entry performed an undo (fs_undo_transaction), mark the original txn as undone.
            # This keeps the ledger's exactly-once state consistent even when the user/agent undoes manually.
            try:
                import json

                for e in (entries or []):
                    if not isinstance(e, dict):
                        continue
                    if str(e.get("type") or "") != "function_call_output":
                        continue
                    out_raw = e.get("output")
                    parsed = None
                    if isinstance(out_raw, dict):
                        parsed = out_raw
                    elif isinstance(out_raw, str) and out_raw.strip():
                        try:
                            parsed = json.loads(out_raw)
                        except Exception:
                            parsed = None
                    if not isinstance(parsed, dict):
                        continue

                    undone_id = parsed.get("undone_transaction_id")
                    undo_txn_id = parsed.get("undo_transaction_id")
                    st = parsed.get("status")
                    if isinstance(st, str) and st.strip().lower() != "success":
                        continue
                    if isinstance(undone_id, str) and undone_id and isinstance(undo_txn_id, str) and undo_txn_id:
                        try:
                            self.transactions_manager.mark_undone(
                                session_id=str(session_id),
                                txn_id=str(undone_id),
                                undo_txn_id=str(undo_txn_id),
                            )
                        except Exception:
                            pass
            except Exception:
                pass

            # Also apply undo mappings provided via wrapper meta (e.g., subagent calls that ran fs_undo_transaction).
            try:
                if isinstance(wrap_meta_by_call_id, dict):
                    for e in (entries or []):
                        if not isinstance(e, dict):
                            continue
                        if str(e.get("type") or "") != "function_call_output":
                            continue
                        cid = e.get("call_id")
                        if not isinstance(cid, str) or not cid:
                            continue
                        wm = wrap_meta_by_call_id.get(cid)
                        if not isinstance(wm, dict):
                            continue
                        ums = wm.get("undo_mappings")
                        if not isinstance(ums, list):
                            continue
                        for m in ums:
                            if not isinstance(m, dict):
                                continue
                            undone_id = m.get("undone_transaction_id")
                            undo_txn_id = m.get("undo_transaction_id")
                            if isinstance(undone_id, str) and undone_id and isinstance(undo_txn_id, str) and undo_txn_id:
                                try:
                                    self.transactions_manager.mark_undone(
                                        session_id=str(session_id),
                                        txn_id=str(undone_id),
                                        undo_txn_id=str(undo_txn_id),
                                    )
                                except Exception:
                                    pass
            except Exception:
                pass

            self._touch_session_meta(session_id, items_count=len(store.entries))
            return ids

    def get_entry(self, session_id: str, entry_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            store = self.get_session_store(session_id)
            return store.get_entry(entry_id)

    def delete_entries(self, session_id: str, entry_ids: List[str]) -> Dict[str, Any]:
        with self._lock:
            store = self.get_session_store(session_id)
            result = store.delete_entries(entry_ids)
            self._touch_session_meta(session_id, items_count=len(store.entries))
            return result


    def delete_entries_from_id(
        self,
        *,
        session_id: str,
        entry_id: str,
        undo_file_edits: bool = False,
        origin_action: str = "",
        fs_revision_store: Any = None,
        project_root: Optional[str] = None,
        sandbox_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delete an entry and all subsequent entries (tail) from a session.

        If undo_file_edits=True, undo filesystem transactions referenced by the deleted tail
        (newest -> oldest) exactly once (based on the MAIN session tail).

        Group sessions:
        - Participant stores are trimmed (tail-cut) but NEVER used for undo.
        """
        with self._lock:
            store = self.get_session_store(session_id)
            wrapped = store.get_entries_wrapped(limit=None)

            # Find index of the target entry
            target_idx = None
            for i, we in enumerate(wrapped):
                if isinstance(we, dict) and we.get("id") == entry_id:
                    target_idx = i
                    break

            if target_idx is None:
                return {"status": "error", "message": "Entry not found", "deleted_count": 0}

            tail = wrapped[target_idx:]
            target_entry = wrapped[target_idx] if 0 <= target_idx < len(wrapped) else None

            # ---------------------------------------------
            # Trim persistent sub-agent session logs referenced in the deleted tail
            # ---------------------------------------------
            try:
                subhistory_by_store: Dict[str, List[str]] = {}
                for we in tail:
                    if not isinstance(we, dict):
                        continue
                    sh = we.get("subhistory")
                    if not isinstance(sh, dict):
                        continue

                    md = str(sh.get("mode") or "").strip().lower()
                    if md != "persistent":
                        continue

                    sid = sh.get("store_id")
                    eids = sh.get("entry_ids")
                    if not isinstance(sid, str) or not sid:
                        continue
                    if not isinstance(eids, list) or not eids:
                        continue

                    for eid in eids:
                        if isinstance(eid, str) and eid:
                            subhistory_by_store.setdefault(sid, []).append(eid)

                for sid, eids in subhistory_by_store.items():
                    seen = set()
                    eids2 = [x for x in eids if isinstance(x, str) and x and not (x in seen or seen.add(x))]
                    if not eids2:
                        continue
                    try:
                        sm = SessionManager(store_id=str(sid))
                        sm.delete_entries(eids2)
                    except Exception:
                        pass
            except Exception:
                pass

            # ---------------------------------------------
            # Group sessions: trim participant stores
            # ---------------------------------------------
            did_tail_cut = False
            try:
                anchors_by_store: Dict[str, str] = {}
                gms = None
                if isinstance(target_entry, dict):
                    gms = target_entry.get("group_participant_mirrors")

                if isinstance(gms, list) and gms:
                    for it in gms:
                        if not isinstance(it, dict):
                            continue
                        sid = it.get("store_id")
                        eid = it.get("entry_id")
                        if isinstance(sid, str) and sid and isinstance(eid, str) and eid:
                            anchors_by_store.setdefault(str(sid), str(eid))

                if anchors_by_store:
                    for sid, eid in anchors_by_store.items():
                        try:
                            sm = SessionManager(store_id=str(sid))
                            # Tail-cut from the mirrored user message inside the participant store.
                            res = sm.delete_entries_from_id(str(eid))
                            if isinstance(res, dict) and res.get("status") == "success" and int(res.get("deleted_count") or 0) > 0:
                                did_tail_cut = True
                        except Exception:
                            pass
            except Exception:
                did_tail_cut = False

            # Fallback: best-effort by-id cleanup using mirror pointers in the tail.
            # (Useful if a caller ever deletes from a non-user entry.)
            if not did_tail_cut:
                try:
                    mirrors_by_store: Dict[str, List[str]] = {}
                    for we in tail:
                        if not isinstance(we, dict):
                            continue

                        gm = we.get("group_participant_mirror")
                        if isinstance(gm, dict):
                            sid = gm.get("store_id")
                            eid = gm.get("entry_id")
                            if isinstance(sid, str) and sid and isinstance(eid, str) and eid:
                                mirrors_by_store.setdefault(sid, []).append(eid)

                            ex = gm.get("extra_entry_ids")
                            if isinstance(sid, str) and sid and isinstance(ex, list):
                                for x in ex:
                                    if isinstance(x, str) and x:
                                        mirrors_by_store.setdefault(sid, []).append(x)

                        gms = we.get("group_participant_mirrors")
                        if isinstance(gms, list):
                            for it in gms:
                                if not isinstance(it, dict):
                                    continue
                                sid = it.get("store_id")
                                eid = it.get("entry_id")
                                if isinstance(sid, str) and sid and isinstance(eid, str) and eid:
                                    mirrors_by_store.setdefault(sid, []).append(eid)

                    for sid, eids in mirrors_by_store.items():
                        seen = set()
                        eids2 = [x for x in eids if isinstance(x, str) and x and not (x in seen or seen.add(x))]
                        if not eids2:
                            continue
                        try:
                            sm = SessionManager(store_id=str(sid))
                            sm.delete_entries(eids2)
                        except Exception:
                            pass
                except Exception:
                    pass

            # ---------------------------------------------
            # Optional: undo file edits (MAIN session tail only)
            # ---------------------------------------------
            failures: List[Dict[str, Any]] = []
            if bool(undo_file_edits) and tail and fs_revision_store is not None:
                # IMPORTANT: transaction ids are discovered via the TransactionsManager ledger,
                # not by scraping session logs.
                ids_to_delete = [we.get("id") for we in tail if isinstance(we, dict) and isinstance(we.get("id"), str)]
                ids_to_delete = [x for x in ids_to_delete if isinstance(x, str) and x]

                ordered_txns = self.transactions_manager.get_txn_ids_for_entry_ids(
                    session_id=str(session_id),
                    entry_ids=ids_to_delete,
                )

                proj_root = str(project_root).strip() if isinstance(project_root, str) and str(project_root).strip() else os.getcwd()
                sb_root = str(sandbox_root).strip() if isinstance(sandbox_root, str) and str(sandbox_root).strip() else None
                if sb_root is None:
                    try:
                        from .sandbox_storage import get_sandbox_root

                        sb_root = str(get_sandbox_root(ensure_exists=True))
                    except Exception:
                        sb_root = None

                for txn_id in reversed(ordered_txns):
                    # Exactly-once: skip already-undone txns (ledger truth).
                    try:
                        if self.transactions_manager.is_undone(session_id=str(session_id), txn_id=str(txn_id)):
                            continue
                    except Exception:
                        pass
                    try:
                        manifest = fs_revision_store.get_transaction(str(txn_id)) if fs_revision_store else None
                        tool = manifest.get("tool") if isinstance(manifest, dict) else None
                        args = manifest.get("args") if isinstance(manifest, dict) else None
                        scope = (args.get("scope") if isinstance(args, dict) else None) or "project"
                        scope = str(scope).strip().lower() if scope else "project"
                        if scope not in ("project", "sandbox"):
                            scope = "project"

                        rep_path = None
                        try:
                            changes = manifest.get("changes") if isinstance(manifest, dict) else None
                            if isinstance(changes, list) and changes:
                                ch0 = changes[0] if isinstance(changes[0], dict) else {}
                                after0 = ch0.get("after") if isinstance(ch0.get("after"), dict) else None
                                before0 = ch0.get("before") if isinstance(ch0.get("before"), dict) else None
                                rep_path = (after0.get("path") if isinstance(after0, dict) else None) or (before0.get("path") if isinstance(before0, dict) else None)
                        except Exception:
                            rep_path = None
                        if not isinstance(rep_path, str) or not rep_path:
                            rep_path = None

                        root = proj_root
                        if scope == "sandbox" and isinstance(sb_root, str) and sb_root:
                            root = sb_root

                        undo_txn_id = fs_revision_store.undo_transaction(str(root), str(txn_id))
                        try:
                            self.transactions_manager.mark_undone(
                                session_id=str(session_id),
                                txn_id=str(txn_id),
                                undo_txn_id=str(undo_txn_id) if isinstance(undo_txn_id, str) and undo_txn_id else None,
                            )
                        except Exception:
                            pass

                    except Exception as e:
                        prev = None
                        try:
                            p = compute_transaction_diff_preview(fs_revision_store, str(txn_id))
                            if isinstance(p, dict) and p.get("status") == "success":
                                prev = {
                                    "transaction_id": p.get("transaction_id"),
                                    "added_lines": int(p.get("added_lines", 0) or 0),
                                    "removed_lines": int(p.get("removed_lines", 0) or 0),
                                }
                        except Exception:
                            prev = None

                        scope = "project"
                        tool = None
                        rep_path = None
                        try:
                            manifest = fs_revision_store.get_transaction(str(txn_id)) if fs_revision_store else None
                            tool = manifest.get("tool") if isinstance(manifest, dict) else None
                            args = manifest.get("args") if isinstance(manifest, dict) else None
                            sc = (args.get("scope") if isinstance(args, dict) else None) or "project"
                            scope = str(sc).strip().lower() if sc else "project"
                            if scope not in ("project", "sandbox"):
                                scope = "project"
                            changes = manifest.get("changes") if isinstance(manifest, dict) else None
                            if isinstance(changes, list) and changes:
                                ch0 = changes[0] if isinstance(changes[0], dict) else {}
                                after0 = ch0.get("after") if isinstance(ch0.get("after"), dict) else None
                                before0 = ch0.get("before") if isinstance(ch0.get("before"), dict) else None
                                rep_path = (after0.get("path") if isinstance(after0, dict) else None) or (before0.get("path") if isinstance(before0, dict) else None)
                        except Exception:
                            pass

                        abs_path = None
                        try:
                            root = proj_root
                            if scope == "sandbox" and isinstance(sb_root, str) and sb_root:
                                root = sb_root
                            if isinstance(rep_path, str) and rep_path:
                                abs_path = os.path.abspath(os.path.join(str(root), rep_path))
                        except Exception:
                            abs_path = None

                        failures.append(
                            {
                                "transaction_id": str(txn_id),
                                "tool": (str(tool) if isinstance(tool, str) else None),
                                "scope": str(scope),
                                "path_label": (str(rep_path) if isinstance(rep_path, str) else None),
                                "abs_path": (str(abs_path) if isinstance(abs_path, str) else None),
                                "diff_preview": prev,
                                "error": str(e),
                            }
                        )

            # ---------------------------------------------
            # Delete main session tail
            # ---------------------------------------------
            ids_to_delete = [we.get("id") for we in tail if isinstance(we, dict) and isinstance(we.get("id"), str)]
            ids_to_delete = [x for x in ids_to_delete if isinstance(x, str) and x]
            result = store.delete_entries(ids_to_delete)
            self._touch_session_meta(session_id, items_count=len(store.entries))

            # Only on undo failures: append a system_notice (not part of agent context)
            if failures:
                try:
                    ts = _now_utc_iso()
                    origin = (origin_action or "").strip().lower()
                    if origin == "edit_message":
                        title = "Undo failed while editing message"
                        msg = "Some file edits could not be undone. The message was edited anyway."
                        action_name = "edit_message"
                    else:
                        title = "Undo failed while removing history"
                        msg = "Some file edits could not be undone. The history was removed anyway."
                        action_name = "delete_message"

                    notice = {
                        "type": "system_notice",
                        "severity": "error",
                        "ts_utc": ts,
                        "title": title,
                        "message": msg,
                        "action": {"name": action_name, "session_id": str(session_id), "entry_id": str(entry_id)},
                        "failed_transactions": failures,
                    }
                    store.append_entries([notice])
                    self._touch_session_meta(session_id, items_count=len(store.entries))
                except Exception:
                    pass

            # Keep return payload consistent even if we appended a notice.
            try:
                if failures and isinstance(result, dict):
                    result["remaining_count"] = int(len(store.entries))
            except Exception:
                pass

            return result
    def add_generated_images(self, session_id: str, images: List[Dict[str, Any]]) -> None:
        # Generated images are currently global; any store instance will write the same file.
        with self._lock:
            store = self.get_session_store(session_id)
            store.add_generated_images(images or [])

    def _touch_session_meta(self, session_id: str, items_count: Optional[int] = None) -> None:
        sessions = self._index.get("sessions")
        if not isinstance(sessions, list):
            return
        for s in sessions:
            if isinstance(s, dict) and s.get("session_id") == session_id:
                s["updated_at"] = _now_utc_iso()
                if items_count is not None:
                    s["items_count"] = int(items_count)
                break
        self._write_index()

    def patch_session_meta(
        self,
        session_id: str,
        patch: Dict[str, Any],
        *,
        touch_updated_at: bool = False,
    ) -> None:
        """Patch (update) a session's metadata entry in index.enc.

        This is intended for *derived/cached* metadata (e.g., token stats) that is
        computed from the session log. By default it does NOT change updated_at.
        """
        with self._lock:
            if not isinstance(session_id, str) or not session_id:
                raise ValueError("session_id is required")
            if not isinstance(patch, dict):
                return

            sessions = self._index.get("sessions")
            if not isinstance(sessions, list):
                return

            for s in sessions:
                if isinstance(s, dict) and s.get("session_id") == session_id:
                    for k, v in patch.items():
                        # Allow adding new optional fields without schema bumps.
                        s[str(k)] = v
                    if touch_updated_at:
                        s["updated_at"] = _now_utc_iso()
                    break

            self._write_index()

    # -----------------------------------------------------------------
    # Prompt caching key (OpenAI prompt_cache_key)
    # -----------------------------------------------------------------

    def get_or_create_prompt_cache_key(self, session_id: str, agent_id: str) -> str:
        """Return a stable per-(session_id, agent_id) prompt cache key.

        Stored in sessions/index.enc session meta under:
          prompt_cache_keys: { <agent_id>: <key>, ... }

        This is a derived/cost-optimization field:
        - persists across restarts and session switches
        - does NOT update session.updated_at (we treat it like cached stats)
        """
        with self._lock:
            sid = str(session_id).strip() if isinstance(session_id, str) else ""
            aid = str(agent_id).strip().lower() if isinstance(agent_id, str) else ""
            if not sid:
                raise ValueError("session_id is required")
            if not aid:
                raise ValueError("agent_id is required")

            sessions = self._index.get("sessions")
            if not isinstance(sessions, list):
                sessions = []
                self._index["sessions"] = sessions

            found = None
            for s in sessions:
                if isinstance(s, dict) and s.get("session_id") == sid:
                    found = s
                    break
            if found is None:
                raise KeyError("Unknown session_id")

            d = found.get("prompt_cache_keys")
            d = d if isinstance(d, dict) else {}

            existing = d.get(aid)
            if isinstance(existing, str) and existing.strip():
                return existing.strip()

            # Generate a fresh key (stable once persisted).
            new_key = f"pcache:{uuid.uuid4()}"
            d[aid] = new_key
            found["prompt_cache_keys"] = d
            self._write_index()
            return new_key

    # -----------------------------------------------------------------
    # Sub-agent stores
    # -----------------------------------------------------------------

    def _slug(self, name: str, *, fallback: str) -> str:
        s = (name or "").strip().lower()
        out2 = []
        for ch in s:
            if ch.isalnum() or ch in ("_", "-"):
                out2.append(ch)
            elif ch.isspace() or ch in ("/", "\\", "."):
                out2.append("_")
        s2 = "".join(out2).strip("_")
        return s2 or fallback

    def get_group_participant_store_id(self, *, session_id: str, agent_id: str) -> str:
        """Store id for a group participant's private mirrored store."""
        sid = str(session_id).strip()
        if not sid:
            raise ValueError("session_id is required")
        slug_agent = self._slug(str(agent_id), fallback="agent")
        return f"sessions/sub-agents/persistent/{sid}/group_{slug_agent}"

    def get_subagent_store_id(
        self,
        *,
        mode: str,
        parent_session_id: Optional[str],
        subagent_name: str,
        subagent_id: str,
        store_id: Optional[str] = None,
    ) -> str:
        """Return the store_id for a sub-agent transcript.

        Centralizes store-id conventions so callers don't hardcode paths.
        """
        if isinstance(store_id, str) and store_id.strip():
            return store_id.strip()

        md = str(mode or "").strip().lower()
        if md not in ("persistent", "run"):
            raise ValueError("mode must be 'persistent' or 'run'")

        slug_name = self._slug(str(subagent_name), fallback="subagent")
        psid = str(parent_session_id).strip() if isinstance(parent_session_id, str) else ""

        if md == "persistent":
            if psid:
                return f"sessions/sub-agents/persistent/{psid}/session_{slug_name}"

            # Legacy global inner-voice store remains separate.
            if str(subagent_name).strip().lower() == "ariane":
                return "session_inner_voice"

            return f"sessions/sub-agents/persistent/session_{slug_name}"

        sid = str(subagent_id).strip() if isinstance(subagent_id, str) else ""
        if not sid:
            sid = str(uuid.uuid4())
        return f"sessions/sub-agents/run/session_{sid}"


    def get_subagent_store(self, store_id: str):
        """Return a SessionManager for a sub-agent transcript store.

        NOTE: sub-agent stores are addressed by *store_id* (not a session_id in the main sessions index).
        This exists to keep SessionManager construction out of bus handlers.
        """
        from .session import SessionManager  # local import to avoid import cycles

        return SessionManager(store_id=str(store_id))
