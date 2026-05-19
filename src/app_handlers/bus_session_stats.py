"""Session stats bus handlers (extracted from src/app.py).

Move-first, refactor-later.
- Preserve bus topic strings.
- Preserve payload/response shapes.
- No UI imports.

Covers:
- session.cmd.stats.get_token_usage
- session.cmd.stats.get_run_usage

Also holds the helper used by runners to update cached stats in session meta.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional


def register_session_stats_handlers(app: Any, bus: Any) -> List[Callable[[], None]]:
    unsubs: List[Callable[[], None]] = []

    unsubs.append(bus.subscribe("session.cmd.stats.get_token_usage", lambda ev: bus_session_stats_get_token_usage(app, ev)))
    unsubs.append(bus.subscribe("session.cmd.stats.get_run_usage", lambda ev: bus_session_stats_get_run_usage(app, ev)))

    return unsubs


def _safe_int(v: Any) -> int:
    try:
        if v is None:
            return 0
        return int(v)
    except Exception:
        return 0


def compute_session_usage_stats(app: Any, session_id: str) -> Dict[str, Any]:
    """Compute usage stats from persisted wrapped entries.

    - Raw Consumption: TOTAL = (sum of main run_summary main usage) + (sum of sub-agent usage from wrapper meta).
    - Context Window: token_usage_last_turn from the latest run_summary entry (main agent).
    - Tools: total tool calls + errors + per-tool histogram.
    - Runs: count + total duration + total turns.

    Defensive by default: missing/partial entries are tolerated.
    """
    wrapped = app.sessions_manager.get_entries_wrapped(session_id=session_id)

    # Session type matters for how we interpret run_summary entries (group sessions emit extra per-turn receipts).
    is_group_session = False
    try:
        meta = app.sessions_manager.get_session_meta(str(session_id))
        is_group_session = bool(isinstance(meta, dict) and str(meta.get("type") or "").strip().lower() == "group")
    except Exception:
        is_group_session = False

    token_totals_total = {
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    token_totals_main = {
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    token_totals_subagents = {
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }

    # Sub-agent usage breakdowns (normal sessions):
    # - raw totals for ALL sub-agents (persistent + one-shot) should still roll up into Subs/Total.
    # - an extra debug breakdown for persistent sub-agents (Ariane + future persistent subs).
    derived_subagents_raw_totals = {
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    persistent_subagents_raw_totals: Dict[str, Dict[str, int]] = {}
    persistent_subagents_context_last_turn: Dict[str, Dict[str, Any]] = {}
    _seen_subagent_call_ids: set = set()

    run_summary_count = 0
    run_duration_total_ms = 0
    run_turns_total = 0
    last_summary_content: Optional[Dict[str, Any]] = None

    # Group sessions: aggregate per-participant usage from the final group run_summary entries.
    group_participants_raw_main: Dict[str, Dict[str, int]] = {}
    group_participants_raw_subagents: Dict[str, Dict[str, int]] = {}
    group_participants_raw_total: Dict[str, Dict[str, int]] = {}
    # Back-compat alias: totals (Phase 2)
    group_participants_raw_totals: Dict[str, Dict[str, int]] = group_participants_raw_total

    group_participants_context_main_last_turn: Dict[str, Dict[str, Any]] = {}
    # Back-compat alias: context_last_turn (Phase 2)
    group_participants_context_last_turn: Dict[str, Dict[str, Any]] = group_participants_context_main_last_turn

    group_participants_display: Dict[str, str] = {}

    tool_calls_total = 0
    tool_errors_total = 0
    tool_counts: Dict[str, int] = {}

    for we in wrapped or []:
        if not isinstance(we, dict):
            continue

        kind = we.get("kind")

        # Tool calls histogram.
        if kind == "function_call":
            tool_calls_total += 1
            c = we.get("content")
            if isinstance(c, dict):
                nm = c.get("name")
                if isinstance(nm, str) and nm:
                    tool_counts[nm] = tool_counts.get(nm, 0) + 1

        # Tool call output errors.
        if kind == "function_call_output":
            if we.get("result_status") == "error":
                tool_errors_total += 1

            # Sub-agent usage accounting (wrapper meta):
            # - always roll ALL sub-agent usage into Subs/Total (persistent + one-shot)
            # - also expose per-persistent-subagent debug breakdown
            try:
                su = we.get("subagent_usage")
                if isinstance(su, dict):
                    c = we.get("content")
                    call_id = c.get("call_id") if isinstance(c, dict) else None
                    call_id = str(call_id).strip() if isinstance(call_id, str) else ""

                    if call_id and call_id in _seen_subagent_call_ids:
                        su = None
                    else:
                        if call_id:
                            _seen_subagent_call_ids.add(call_id)

                        tu = su.get("token_usage_totals")
                        if isinstance(tu, dict):
                            for k in list(derived_subagents_raw_totals.keys()):
                                derived_subagents_raw_totals[k] += _safe_int(tu.get(k))

                        mode = str(su.get("mode") or "").strip().lower()
                        if mode == "persistent":
                            nm = su.get("subagent_name")
                            nm = str(nm).strip() if isinstance(nm, str) and str(nm).strip() else "subagent"

                            if isinstance(tu, dict):
                                cur = persistent_subagents_raw_totals.get(nm)
                                if not isinstance(cur, dict):
                                    cur = {
                                        "input_tokens": 0,
                                        "cached_tokens": 0,
                                        "output_tokens": 0,
                                        "reasoning_tokens": 0,
                                        "total_tokens": 0,
                                    }
                                for k in list(cur.keys()):
                                    cur[k] = int(cur.get(k, 0) or 0) + _safe_int(tu.get(k, 0))
                                persistent_subagents_raw_totals[nm] = cur

                            lt = su.get("token_usage_last_turn")
                            if isinstance(lt, dict):
                                persistent_subagents_context_last_turn[nm] = lt
            except Exception:
                pass

        # Run summaries.
        if kind != "run_summary":
            continue

        c = we.get("content")
        if not isinstance(c, dict):
            continue

        # Group sessions emit extra per-participant turn receipts (run_id starts with 'grpturn:').
        # Those are UI receipts only and must NOT affect session-level token totals/runs.
        try:
            rid = c.get("run_id")
            if is_group_session and isinstance(rid, str) and rid.startswith("grpturn:"):
                continue
        except Exception:
            pass

        run_summary_count += 1
        last_summary_content = c

        # Phase 2: group participant usage breakdown (per group run).
        try:
            gpu = c.get("group_participant_usage")
            if is_group_session and isinstance(gpu, dict):
                for aid, rec in gpu.items():
                    if not isinstance(aid, str) or not aid:
                        continue
                    if not isinstance(rec, dict):
                        continue

                    dn = rec.get("display_name")
                    if isinstance(dn, str) and dn.strip():
                        group_participants_display[aid] = dn.strip()

                    tu_m = rec.get("token_usage_totals_main")
                    tu_s = rec.get("token_usage_totals_subagents")
                    tu_t = rec.get("token_usage_totals_total")
                    if not isinstance(tu_t, dict):
                        tu_t = rec.get("token_usage_totals")

                    def _acc(dst: Dict[str, Dict[str, int]], aid2: str, tu2: Any) -> None:
                        if not isinstance(tu2, dict):
                            return
                        cur = dst.get(aid2)
                        if not isinstance(cur, dict):
                            cur = {
                                "input_tokens": 0,
                                "cached_tokens": 0,
                                "output_tokens": 0,
                                "reasoning_tokens": 0,
                                "total_tokens": 0,
                            }
                        for k in list(cur.keys()):
                            cur[k] = int(cur.get(k, 0) or 0) + _safe_int(tu2.get(k, 0))
                        dst[aid2] = cur

                    _acc(group_participants_raw_main, aid, tu_m)
                    _acc(group_participants_raw_subagents, aid, tu_s)
                    _acc(group_participants_raw_total, aid, tu_t)

                    lt = rec.get("token_usage_last_turn_main")
                    if not isinstance(lt, dict):
                        lt = rec.get("token_usage_last_turn")
                    if isinstance(lt, dict):
                        group_participants_context_main_last_turn[aid] = lt
        except Exception:
            pass

        # Back-compat: token_usage_totals is treated as TOTAL.
        tu_total = c.get("token_usage_totals_total")
        if not isinstance(tu_total, dict):
            tu_total = c.get("token_usage_totals")
        tu_main = c.get("token_usage_totals_main")
        tu_sub = c.get("token_usage_totals_subagents")

        if isinstance(tu_total, dict):
            for k in list(token_totals_total.keys()):
                token_totals_total[k] += _safe_int(tu_total.get(k))

        if isinstance(tu_main, dict):
            for k in list(token_totals_main.keys()):
                token_totals_main[k] += _safe_int(tu_main.get(k))
        elif isinstance(tu_total, dict):
            # Older run summaries: treat all usage as main.
            for k in list(token_totals_main.keys()):
                token_totals_main[k] += _safe_int(tu_total.get(k))

        if isinstance(tu_sub, dict):
            for k in list(token_totals_subagents.keys()):
                token_totals_subagents[k] += _safe_int(tu_sub.get(k))

        run_duration_total_ms += _safe_int(c.get("duration_ms"))
        run_turns_total += _safe_int(c.get("turns_count"))

    context_last_turn = None
    if isinstance(last_summary_content, dict):
        lt = last_summary_content.get("token_usage_last_turn")
        context_last_turn = lt if isinstance(lt, dict) else None

    tool_hist = [
        {"name": k, "count": int(v)}
        for k, v in sorted(tool_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    # Prefer wrapper-derived sub-agent totals (covers both persistent + one-shot and does not
    # depend on run_summary having correct subagent accounting).
    token_totals_subagents = dict(derived_subagents_raw_totals)

    # Total = main + subagents (computed deterministically).
    token_totals_total = {
        k: _safe_int(token_totals_main.get(k)) + _safe_int(token_totals_subagents.get(k))
        for k in list(token_totals_total.keys())
    }

    return {
        "session_id": session_id,
        "raw_totals": token_totals_total,
        "raw_totals_main": token_totals_main,
        "raw_totals_subagents": token_totals_subagents,
        "context_last_turn": context_last_turn,
        # Persistent sub-agent breakdown (for UI visibility of who is nearing context limits).
        "persistent_subagents_raw_totals": persistent_subagents_raw_totals,
        "persistent_subagents_context_last_turn": persistent_subagents_context_last_turn,
        "run_summary_count": run_summary_count,
        "run_duration_total_ms": int(run_duration_total_ms),
        "run_turns_total": int(run_turns_total),
        "tool_calls_total": int(tool_calls_total),
        "tool_errors_total": int(tool_errors_total),
        "tool_hist": tool_hist,
        "tool_distinct": int(len(tool_counts)),
        # Group participant breakdowns (only meaningful when session type=='group').
        "group_participants_raw_main": group_participants_raw_main,
        "group_participants_raw_subagents": group_participants_raw_subagents,
        "group_participants_raw_total": group_participants_raw_total,
        # Back-compat aliases
        "group_participants_raw_totals": group_participants_raw_totals,
        "group_participants_context_main_last_turn": group_participants_context_main_last_turn,
        "group_participants_context_last_turn": group_participants_context_last_turn,
        "group_participants_display": group_participants_display,
    }


def compute_run_usage_stats(app: Any, session_id: str, run_id: str) -> Dict[str, Any]:
    """Compute usage stats for a single run_id within a session (defensive)."""
    wrapped = app.sessions_manager.get_entries_wrapped(session_id=session_id)

    tool_calls_total = 0
    tool_errors_total = 0
    tool_counts: Dict[str, int] = {}

    # Derive sub-agent totals for this run directly from wrapper meta (covers persistent + one-shot).
    derived_subagents_raw_totals = {
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    _seen_subagent_call_ids: set = set()

    run_summary_content: Optional[Dict[str, Any]] = None
    entries_count = 0

    for we in wrapped or []:
        if not isinstance(we, dict):
            continue
        if we.get("run_id") != run_id:
            continue

        entries_count += 1
        kind = we.get("kind")

        if kind == "function_call":
            tool_calls_total += 1
            c = we.get("content")
            if isinstance(c, dict):
                nm = c.get("name")
                if isinstance(nm, str) and nm:
                    tool_counts[nm] = tool_counts.get(nm, 0) + 1

        elif kind == "function_call_output":
            if we.get("result_status") == "error":
                tool_errors_total += 1

            # Sub-agent usage for this run (wrapper meta)
            try:
                su = we.get("subagent_usage")
                if isinstance(su, dict):
                    c = we.get("content")
                    call_id = c.get("call_id") if isinstance(c, dict) else None
                    call_id = str(call_id).strip() if isinstance(call_id, str) else ""

                    if call_id and call_id in _seen_subagent_call_ids:
                        su = None
                    else:
                        if call_id:
                            _seen_subagent_call_ids.add(call_id)

                        tu = su.get("token_usage_totals")
                        if isinstance(tu, dict):
                            for k in list(derived_subagents_raw_totals.keys()):
                                derived_subagents_raw_totals[k] += _safe_int(tu.get(k))
            except Exception:
                pass

        elif kind == "run_summary":
            c = we.get("content")
            if isinstance(c, dict):
                run_summary_content = c

    tool_hist = [
        {"name": k, "count": int(v)}
        for k, v in sorted(tool_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    token_totals_total = {
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    token_totals_main = {
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    token_totals_subagents = {
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    context_last_turn = None
    duration_ms = 0
    turns_count = 0
    run_status = None
    started_at = None
    finished_at = None

    if isinstance(run_summary_content, dict):
        # Back-compat: token_usage_totals is treated as TOTAL.
        tu_total = run_summary_content.get("token_usage_totals_total")
        if not isinstance(tu_total, dict):
            tu_total = run_summary_content.get("token_usage_totals")
        tu_main = run_summary_content.get("token_usage_totals_main")
        tu_sub = run_summary_content.get("token_usage_totals_subagents")

        if isinstance(tu_total, dict):
            for k in list(token_totals_total.keys()):
                token_totals_total[k] = _safe_int(tu_total.get(k))

        if isinstance(tu_main, dict):
            for k in list(token_totals_main.keys()):
                token_totals_main[k] = _safe_int(tu_main.get(k))
        elif isinstance(tu_total, dict):
            for k in list(token_totals_main.keys()):
                token_totals_main[k] = _safe_int(tu_total.get(k))

        if isinstance(tu_sub, dict):
            for k in list(token_totals_subagents.keys()):
                token_totals_subagents[k] = _safe_int(tu_sub.get(k))

        lt = run_summary_content.get("token_usage_last_turn")
        context_last_turn = lt if isinstance(lt, dict) else None

        duration_ms = _safe_int(run_summary_content.get("duration_ms"))
        turns_count = _safe_int(run_summary_content.get("turns_count"))

        rs = run_summary_content.get("run_status")
        run_status = rs if isinstance(rs, str) else None

        sa = run_summary_content.get("started_at")
        fa = run_summary_content.get("finished_at")
        started_at = sa if isinstance(sa, str) else None
        finished_at = fa if isinstance(fa, str) else None

    # Prefer wrapper-derived sub-agent totals for this run.
    token_totals_subagents = dict(derived_subagents_raw_totals)
    token_totals_total = {
        k: _safe_int(token_totals_main.get(k)) + _safe_int(token_totals_subagents.get(k))
        for k in list(token_totals_total.keys())
    }

    return {
        "session_id": session_id,
        "run_id": run_id,
        "entries_count": int(entries_count),
        # Back-compat: token_usage_totals is TOTAL.
        "token_usage_totals": token_totals_total,
        "token_usage_totals_main": token_totals_main,
        "token_usage_totals_subagents": token_totals_subagents,
        "token_usage_totals_total": token_totals_total,
        "token_usage_last_turn": context_last_turn,
        "duration_ms": int(duration_ms),
        "turns_count": int(turns_count),
        "run_status": run_status,
        "started_at": started_at,
        "finished_at": finished_at,
        "tool_calls_total": int(tool_calls_total),
        "tool_errors_total": int(tool_errors_total),
        "tool_hist": tool_hist,
        "tool_distinct": int(len(tool_counts)),
    }


def bus_session_stats_get_run_usage(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    session_id = payload.get("session_id")
    entry_id = payload.get("entry_id")

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
            wrapped = app.sessions_manager.get_entry(session_id=str(session_id), entry_id=str(entry_id))
            if not isinstance(wrapped, dict):
                app._bus_reply(reply_topic, {"status": "error", "message": "Entry not found"})
                return

            run_id = wrapped.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                app._bus_reply(reply_topic, {"status": "error", "message": "No run_id for this entry"})
                return

            stats = compute_run_usage_stats(app, session_id=str(session_id), run_id=str(run_id))
            app._bus_reply(reply_topic, {"status": "success", **stats})

        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def update_session_token_stats_meta(app: Any, session_id: str) -> None:
    """Recompute token stats and store them in sessions index meta (cache)."""
    try:
        stats = compute_session_usage_stats(app, session_id=session_id)
        # Cache-ish derived fields in sessions index meta.
        tool_hist = stats.get("tool_hist") if isinstance(stats.get("tool_hist"), list) else []
        top_tools = tool_hist[:3]
        patch = {
            # Back-compat: token_stats_raw_totals is TOTAL.
            "token_stats_raw_totals": stats.get("raw_totals"),
            "token_stats_raw_totals_main": stats.get("raw_totals_main"),
            "token_stats_raw_totals_subagents": stats.get("raw_totals_subagents"),
            "token_stats_context_last_turn": stats.get("context_last_turn"),
            "token_stats_run_summary_count": int(stats.get("run_summary_count") or 0),
            "tool_stats_total_calls": int(stats.get("tool_calls_total") or 0),
            "tool_stats_total_errors": int(stats.get("tool_errors_total") or 0),
            "tool_stats_distinct": int(stats.get("tool_distinct") or 0),
            "tool_stats_top": top_tools,
            "run_stats_duration_total_ms": int(stats.get("run_duration_total_ms") or 0),
            "run_stats_turns_total": int(stats.get("run_turns_total") or 0),
        }
        app.sessions_manager.patch_session_meta(session_id=session_id, patch=patch, touch_updated_at=False)
    except Exception:
        # Derived cache only. Never fail the user-facing operation.
        pass


def bus_session_stats_get_token_usage(app: Any, event) -> None:
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
            stats = compute_session_usage_stats(app, session_id=str(session_id))
            app._bus_reply(
                reply_topic,
                {
                    "status": "success",
                    "session_id": stats.get("session_id"),
                    # Back-compat: raw_consumption is TOTAL.
                    "raw_consumption": stats.get("raw_totals"),
                    "raw_consumption_main": stats.get("raw_totals_main"),
                    "raw_consumption_subagents": stats.get("raw_totals_subagents"),
                    "context_window": stats.get("context_last_turn"),
                    # Phase 1: persistent sub-agent breakdown (one-shots are totals-only).
                    "raw_consumption_persistent_subagents": stats.get("persistent_subagents_raw_totals"),
                    "context_window_persistent_subagents": stats.get("persistent_subagents_context_last_turn"),
                    "raw_consumption_group_participants_main": stats.get("group_participants_raw_main"),
                    "raw_consumption_group_participants_subagents": stats.get("group_participants_raw_subagents"),
                    "raw_consumption_group_participants_total": stats.get("group_participants_raw_total"),
                    # Back-compat: total-only
                    "raw_consumption_group_participants": stats.get("group_participants_raw_totals"),

                    "context_window_group_participants_main": stats.get("group_participants_context_main_last_turn"),
                    # Back-compat: main-only
                    "context_window_group_participants": stats.get("group_participants_context_last_turn"),

                    "group_participants_display": stats.get("group_participants_display"),
                    "run_summary_count": stats.get("run_summary_count"),
                    "run_duration_total_ms": stats.get("run_duration_total_ms"),
                    "run_turns_total": stats.get("run_turns_total"),
                    "tool_calls_total": stats.get("tool_calls_total"),
                    "tool_errors_total": stats.get("tool_errors_total"),
                    "tool_distinct": stats.get("tool_distinct"),
                    "tool_hist": stats.get("tool_hist"),
                },
            )
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()
