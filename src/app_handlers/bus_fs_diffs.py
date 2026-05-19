"""FS diff bus handlers (extracted from src/app.py).

Move-first, refactor-later.
- Preserve bus topics.
- Preserve payload/response shapes.
- No UI imports.

Covers:
- fs_revisions.cmd.get_diff
- fs_revisions.cmd.get_diff_index
- fs_revisions.cmd.get_diff_sbs_file
- fs_revisions.cmd.get_run_diff_index
- fs_revisions.cmd.get_run_diff_sbs_file
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional


def register_fs_diffs_handlers(app: Any, bus: Any) -> List[Callable[[], None]]:
    """Register fs diff bus handlers. Returns unsubscribe callables."""
    unsubs: List[Callable[[], None]] = []

    unsubs.append(bus.subscribe("fs_revisions.cmd.get_diff", lambda ev: bus_fs_revisions_get_diff(app, ev)))
    unsubs.append(bus.subscribe("fs_revisions.cmd.get_diff_index", lambda ev: bus_fs_revisions_get_diff_index(app, ev)))
    unsubs.append(bus.subscribe("fs_revisions.cmd.get_diff_sbs_file", lambda ev: bus_fs_revisions_get_diff_sbs_file(app, ev)))

    unsubs.append(bus.subscribe("fs_revisions.cmd.get_run_diff_index", lambda ev: bus_fs_revisions_get_run_diff_index(app, ev)))
    unsubs.append(bus.subscribe("fs_revisions.cmd.get_run_diff_sbs_file", lambda ev: bus_fs_revisions_get_run_diff_sbs_file(app, ev)))

    return unsubs


def bus_fs_revisions_get_diff(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    txn_id = payload.get("transaction_id")

    if not reply_topic:
        return
    if not txn_id:
        app._bus_reply(reply_topic, {"status": "error", "message": "transaction_id is required"})
        return

    def work():
        try:
            if not getattr(app, "fs_revision_store", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "Fs revision store not available"})
                return

            # Optional limits (defensive; caller may omit).
            try:
                max_file_bytes = int(payload.get("max_file_bytes") or 0) or None
            except Exception:
                max_file_bytes = None
            try:
                max_diff_lines = int(payload.get("max_diff_lines") or 0) or None
            except Exception:
                max_diff_lines = None
            try:
                max_files = int(payload.get("max_files") or 0) or None
            except Exception:
                max_files = None

            kwargs = {}
            if max_file_bytes is not None:
                kwargs["max_file_bytes"] = max_file_bytes
            if max_diff_lines is not None:
                kwargs["max_diff_lines"] = max_diff_lines
            if max_files is not None:
                kwargs["max_files"] = max_files

            from ..storage.fs_diff import compute_transaction_diff

            result = compute_transaction_diff(app.fs_revision_store, str(txn_id), **kwargs)
            app._bus_reply(reply_topic, result if isinstance(result, dict) else {"status": "error", "message": "Unexpected result"})

        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_fs_revisions_get_diff_index(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    txn_id = payload.get("transaction_id")

    if not reply_topic:
        return
    if not txn_id:
        app._bus_reply(reply_topic, {"status": "error", "message": "transaction_id is required"})
        return

    def work():
        try:
            if not getattr(app, "fs_revision_store", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "Fs revision store not available"})
                return

            try:
                max_files = int(payload.get("max_files") or 0) or None
            except Exception:
                max_files = None

            kwargs = {}
            if max_files is not None:
                kwargs["max_files"] = max_files

            from ..storage.fs_diff import compute_transaction_diff_index

            result = compute_transaction_diff_index(app.fs_revision_store, str(txn_id), **kwargs)
            app._bus_reply(reply_topic, result if isinstance(result, dict) else {"status": "error", "message": "Unexpected result"})

        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_fs_revisions_get_diff_sbs_file(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    txn_id = payload.get("transaction_id")
    file_key = payload.get("file_key")

    if not reply_topic:
        return
    if not txn_id:
        app._bus_reply(reply_topic, {"status": "error", "message": "transaction_id is required"})
        return
    if not file_key:
        app._bus_reply(reply_topic, {"status": "error", "message": "file_key is required"})
        return

    def work():
        try:
            if not getattr(app, "fs_revision_store", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "Fs revision store not available"})
                return

            # Optional limits (defensive)
            try:
                max_file_bytes = int(payload.get("max_file_bytes") or 0) or None
            except Exception:
                max_file_bytes = None
            try:
                max_lines_per_file = int(payload.get("max_lines_per_file") or 0) or None
            except Exception:
                max_lines_per_file = None

            kwargs = {}
            if max_file_bytes is not None:
                kwargs["max_file_bytes"] = max_file_bytes
            if max_lines_per_file is not None:
                kwargs["max_lines_per_file"] = max_lines_per_file

            from ..storage.fs_diff import compute_transaction_diff_sbs_file

            result = compute_transaction_diff_sbs_file(
                app.fs_revision_store,
                str(txn_id),
                str(file_key),
                **kwargs,
            )
            app._bus_reply(reply_topic, result if isinstance(result, dict) else {"status": "error", "message": "Unexpected result"})

        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


# --- Run-level consolidated diffs (Phase B) ---


def _find_run_summary_content(app: Any, session_id: str, run_id: str) -> Optional[Dict[str, Any]]:
    """Locate the persisted run_summary content dict for a given run_id (best-effort)."""
    try:
        wrapped = app.sessions_manager.get_entries_wrapped(session_id=session_id)
    except Exception:
        wrapped = []

    if not isinstance(wrapped, list):
        return None

    for we in reversed(wrapped):
        if not isinstance(we, dict):
            continue
        if we.get("kind") != "run_summary":
            continue
        content = we.get("content")
        if not isinstance(content, dict):
            continue
        if str(content.get("run_id") or "") == str(run_id):
            return content

    return None


def bus_fs_revisions_get_run_diff_index(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    session_id = payload.get("session_id")
    run_id = payload.get("run_id")

    if not reply_topic:
        return
    if not session_id:
        app._bus_reply(reply_topic, {"status": "error", "message": "session_id is required"})
        return
    if not run_id:
        app._bus_reply(reply_topic, {"status": "error", "message": "run_id is required"})
        return

    def work():
        try:
            if not getattr(app, "fs_revision_store", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "Fs revision store not available"})
                return

            rs = _find_run_summary_content(app, session_id=str(session_id), run_id=str(run_id))

            # Preferred: serve the persisted run index.
            if isinstance(rs, dict):
                files = rs.get("files_changed")
                if (
                    isinstance(files, list)
                    and len(files) > 0
                    and isinstance(files[0], dict)
                    and ("before_blob" in files[0] or "after_blob" in files[0])
                ):
                    app._bus_reply(
                        reply_topic,
                        {
                            "status": "success",
                            "run_id": str(run_id),
                            "transaction_ids": app.sessions_manager.transactions_manager.get_txn_ids_for_run(session_id=str(session_id), run_id=str(run_id)),
                            "files": files,
                            "diff_totals": rs.get("diff_totals") if isinstance(rs.get("diff_totals"), dict) else None,
                        },
                    )
                    return

            # Fallback: compute on-demand.
            txn_ids = app.sessions_manager.transactions_manager.get_txn_ids_for_run(session_id=str(session_id), run_id=str(run_id))

            from ..storage.fs_diff import compute_run_diff_index

            idx = compute_run_diff_index(app.fs_revision_store, txn_ids)
            if isinstance(idx, dict):
                idx["run_id"] = str(run_id)
            app._bus_reply(reply_topic, idx if isinstance(idx, dict) else {"status": "error", "message": "Unexpected result"})

        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_fs_revisions_get_run_diff_sbs_file(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    session_id = payload.get("session_id")
    run_id = payload.get("run_id")
    file_key = payload.get("file_key")

    if not reply_topic:
        return
    if not session_id:
        app._bus_reply(reply_topic, {"status": "error", "message": "session_id is required"})
        return
    if not run_id:
        app._bus_reply(reply_topic, {"status": "error", "message": "run_id is required"})
        return
    if not file_key:
        app._bus_reply(reply_topic, {"status": "error", "message": "file_key is required"})
        return

    def work():
        try:
            if not getattr(app, "fs_revision_store", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "Fs revision store not available"})
                return

            rs = _find_run_summary_content(app, session_id=str(session_id), run_id=str(run_id))

            files_index = None
            if isinstance(rs, dict):
                files = rs.get("files_changed")
                if (
                    isinstance(files, list)
                    and files
                    and isinstance(files[0], dict)
                    and ("before_blob" in files[0] or "after_blob" in files[0])
                ):
                    files_index = files

            if files_index is None:
                txn_ids = app.sessions_manager.transactions_manager.get_txn_ids_for_run(session_id=str(session_id), run_id=str(run_id))
                from ..storage.fs_diff import compute_run_diff_index

                idx = compute_run_diff_index(app.fs_revision_store, txn_ids)
                files_index = idx.get("files") if isinstance(idx, dict) else []

            from ..storage.fs_diff import compute_run_diff_sbs_file

            result = compute_run_diff_sbs_file(
                app.fs_revision_store,
                run_id=str(run_id),
                file_key=str(file_key),
                files_index=files_index if isinstance(files_index, list) else [],
            )
            app._bus_reply(reply_topic, result if isinstance(result, dict) else {"status": "error", "message": "Unexpected result"})

        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()
