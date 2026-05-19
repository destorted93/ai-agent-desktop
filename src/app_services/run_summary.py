"""Run-summary builder.

Purpose
- Centralize the logic that builds the persisted `run_summary` item.
- Keep behavior identical to the previous inline implementation in src/app.py.

Design constraints
- No GUI imports.
- Accept app-owned dependencies (SessionsManager, FsRevisionStore) as params.
- Defensive: never raise on best-effort fields (diff previews, subagent breakdown).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return int(default)
        return int(v)
    except Exception:
        return int(default)


def _last_turn_usage(token_hist: Dict[Any, Any]) -> Optional[Dict[str, Any]]:
    """Return the last-turn usage dict from token_usage_history."""
    if not isinstance(token_hist, dict) or not token_hist:
        return None

    def _to_int(x: Any) -> int:
        try:
            return int(x)
        except Exception:
            return -1

    try:
        last_k = max(token_hist.keys(), key=_to_int)
        lt = token_hist.get(last_k)
        return lt if isinstance(lt, dict) else None
    except Exception:
        return None


def _sum_token_hist(token_hist: Dict[Any, Any]) -> Dict[str, int]:
    totals = {
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    if not isinstance(token_hist, dict):
        return totals

    for v in token_hist.values():
        if not isinstance(v, dict):
            continue
        for k in list(totals.keys()):
            totals[k] += _safe_int(v.get(k, 0), 0)

    return totals


def build_run_summary_item(
    *,
    session_id: str,
    run_id: Optional[str],
    started_at_utc: datetime,
    finished_at_utc: datetime,
    stopped: bool,
    done_message: str,
    saved_entry_ids: List[str],
    token_usage_history: Dict[Any, Any],
    sessions_manager: Any,
    fs_revision_store: Any,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Build the run_summary item and also return a small "ui_enrichment" dict.

    Returns (run_summary_item, ui_enrichment)
      - run_summary_item: persisted content dict (type=run_summary)
      - ui_enrichment: best-effort extra computed values (files_changed, previews, etc.)

    Note: This function is intentionally tolerant of missing dependencies;
    callers should treat it as best-effort and never let it fail the run.
    """
    started_at_iso = started_at_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    finished_at_iso = finished_at_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    duration_ms = int((finished_at_utc - started_at_utc).total_seconds() * 1000)

    msg = done_message or ""
    msg = msg if isinstance(msg, str) else ""

    if bool(stopped):
        status = "stopped"
    elif msg.lower().startswith("error"):
        status = "error"
    else:
        status = "success"

    token_hist = token_usage_history if isinstance(token_usage_history, dict) else {}

    main_totals = _sum_token_hist(token_hist)

    # Sub-agent usage comes from wrapper-only meta on persisted wrapped entries.
    # Count each invocation once (dedupe by tool call_id) to avoid double-counting when
    # wrapper meta is attached to both function_call and function_call_output.
    subagent_totals = {k: 0 for k in list(main_totals.keys())}
    subagent_breakdown: Dict[str, Dict[str, int]] = {}
    _seen_subagent_call_ids: set = set()

    try:
        for eid in (saved_entry_ids or []):
            wrapped = sessions_manager.get_entry(session_id=session_id, entry_id=str(eid))
            if not isinstance(wrapped, dict):
                continue

            su = wrapped.get("subagent_usage")
            if not isinstance(su, dict):
                continue

            c = wrapped.get("content")
            call_id = c.get("call_id") if isinstance(c, dict) else None
            call_id = str(call_id).strip() if isinstance(call_id, str) else ""

            if call_id and call_id in _seen_subagent_call_ids:
                continue
            if call_id:
                _seen_subagent_call_ids.add(call_id)

            tu = su.get("token_usage_totals")
            if not isinstance(tu, dict):
                continue

            for k in list(subagent_totals.keys()):
                subagent_totals[k] += _safe_int(tu.get(k, 0), 0)

            nm = su.get("subagent_name")
            nm = nm if isinstance(nm, str) and nm else "subagent"

            bd = subagent_breakdown.get(nm) or {k: 0 for k in list(subagent_totals.keys())}
            for k in list(subagent_totals.keys()):
                bd[k] += _safe_int(tu.get(k, 0), 0)
            subagent_breakdown[nm] = bd
    except Exception:
        pass

    total_totals = {
        k: _safe_int(main_totals.get(k, 0), 0) + _safe_int(subagent_totals.get(k, 0), 0)
        for k in list(main_totals.keys())
    }

    last_turn = _last_turn_usage(token_hist)

    # Transactions are now ledger-driven.
    ordered_txns: List[str] = []
    try:
        if run_id:
            ordered_txns = sessions_manager.transactions_manager.get_txn_ids_for_run(
                session_id=str(session_id),
                run_id=str(run_id),
            )

        # Fallback: if run_index is missing/empty for some reason, derive txns from the
        # actual entries saved in this run (entry_index is the more fundamental mapping).
        if (not ordered_txns) and saved_entry_ids:
            ordered_txns = sessions_manager.transactions_manager.get_txn_ids_for_entry_ids(
                session_id=str(session_id),
                entry_ids=[str(e) for e in (saved_entry_ids or []) if isinstance(e, str) and e],
            )
    except Exception:
        ordered_txns = []

    # Keep behavior aligned with the original app.py implementation: no dedupe here.
    ordered_txns = [str(t) for t in (ordered_txns or []) if isinstance(t, str) and t]

    files_changed: List[Dict[str, Any]] = []
    files_changed_preview: List[Dict[str, Any]] = []
    diff_totals = None
    files_changed_count_override: Optional[int] = None
    files_ephemeral_count_override: Optional[int] = None

    try:
        if ordered_txns and fs_revision_store is not None:
            # Import lazily to keep this module lightweight at import time.
            from ..storage.fs_diff import compute_run_diff_index

            idx = compute_run_diff_index(fs_revision_store, ordered_txns)
            if isinstance(idx, dict) and idx.get("status") == "success":
                files_all = idx.get("files") if isinstance(idx.get("files"), list) else []
                files_changed = files_all

                # Ephemeral = missing->missing (created+deleted) hidden by default in UI.
                non_ephemeral = []
                try:
                    non_ephemeral = [
                        f
                        for f in files_all
                        if not (isinstance(f, dict) and bool(f.get("ephemeral")))
                    ]
                    eph_count = max(0, len(files_all) - len(non_ephemeral))
                except Exception:
                    non_ephemeral = files_all
                    eph_count = 0

                files_changed_preview = list(non_ephemeral[:3])
                diff_totals = idx.get("diff_totals") if isinstance(idx.get("diff_totals"), dict) else None
                files_changed_count_override = int(len(non_ephemeral))
                files_ephemeral_count_override = int(eph_count)
    except Exception:
        # best-effort only
        files_changed = []
        files_changed_preview = []
        diff_totals = None

    run_summary_item: Dict[str, Any] = {
        "type": "run_summary",
        "run_id": str(run_id) if run_id else None,
        "run_status": status,
        "started_at": started_at_iso,
        "finished_at": finished_at_iso,
        "duration_ms": duration_ms,
        "entries_count": len(list(saved_entry_ids or [])),
        "turns_count": len(token_hist),
        # Back-compat: token_usage_totals == TOTAL
        "token_usage_totals": total_totals,
        "token_usage_totals_main": main_totals,
        "token_usage_totals_subagents": subagent_totals,
        "token_usage_totals_total": total_totals,
        "subagent_usage_breakdown": subagent_breakdown,
        "token_usage_last_turn": last_turn,
        "transaction_count": int(len(ordered_txns)),
        "files_changed": files_changed,
        "files_changed_preview": files_changed_preview,
        "files_changed_count": int(files_changed_count_override if files_changed_count_override is not None else len(files_changed)),
        "files_ephemeral_count": int(files_ephemeral_count_override if files_ephemeral_count_override is not None else 0),
        "diff_totals": diff_totals,
        "description": "",
    }

    ui_enrichment = {
        "ordered_txns": ordered_txns,
        "files_changed": files_changed,
        "files_changed_preview": files_changed_preview,
        "diff_totals": diff_totals,
    }

    return run_summary_item, ui_enrichment
