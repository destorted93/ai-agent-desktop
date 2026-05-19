"""Agent + sub-agent runtime bus handlers (extracted from src/app.py).

Move-first, refactor-later.
- Preserve bus topics.
- Preserve payload/response shapes.
- No UI imports.

Covers:
- agent.cmd.run
- agent.cmd.run_subagent
- agent.cmd.stop
- subagent.cmd.session.entries.get
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional

from ..appcore.config_manager import AgentRuntimeConfig
from ..appcore.runtime_context import Runtime
from ..core import Agent
from ..app_services.agent_factory import create_agent, get_api_mode_from_app
from ..app_services.agent_reload import ensure_config_loaded
from ..storage import SecureStorage
from ..tools import get_default_tools


from ..app_services.run_context_helpers import _iter_with_run_context


def register_agent_runtime_handlers(app: Any, bus: Any) -> List[Callable[[], None]]:
    unsubs: List[Callable[[], None]] = []

    unsubs.append(bus.subscribe("agent.cmd.run", lambda ev: bus_agent_run(app, ev)))
    unsubs.append(bus.subscribe("agent.cmd.run_subagent", lambda ev: bus_agent_run_subagent(app, ev)))
    unsubs.append(bus.subscribe("agent.cmd.stop", lambda ev: bus_agent_stop(app, ev)))

    unsubs.append(bus.subscribe("subagent.cmd.session.entries.get", lambda ev: bus_subagent_session_entries_get(app, ev)))

    return unsubs


def bus_agent_run(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    stream_topic = payload.get("stream_topic")
    reply_topic = payload.get("reply_topic")

    session_id = payload.get("session_id")
    message = payload.get("message")
    files = payload.get("files")
    images = payload.get("images")
    run_id = payload.get("run_id")

    # stream_topic is required; reply_topic is optional.
    if not stream_topic:
        if reply_topic:
            app._bus_reply(reply_topic, {"status": "error", "message": "stream_topic is required"})
        return

    if not session_id:
        if reply_topic:
            app._bus_reply(reply_topic, {"status": "error", "message": "session_id is required"})
        return

    def work():
        bus = Runtime.get_event_bus()

        # Prevent overlapping main runs (shared Agent instance + shared stop flag).
        try:
            if app._is_inference_running():
                bus.publish(
                    stream_topic,
                    {
                        "type": "response.error",
                        "agent_name": "System",
                        "content": {"message": "Agent is currently running"},
                    },
                )
                bus.publish(stream_topic, {"type": "stream.finished", "agent_name": "System", "content": {}})
                if reply_topic:
                    app._bus_reply(reply_topic, {"status": "error", "message": "Agent is currently running", "run_id": run_id})
                return
        except Exception:
            pass

        app._set_inference_running(True)
        try:
            bus.publish("agent.state.changed", {"run_id": run_id, "session_id": session_id, "state": "started"})

            # Expose parent run context to tools.
            try:
                if app.agent is not None:
                    setattr(app.agent, "_active_stream_topic", str(stream_topic))
                    setattr(app.agent, "_active_run_id", str(run_id) if run_id else None)
                    setattr(app.agent, "_active_session_id", str(session_id))
            except Exception:
                pass

            # Group sessions reuse the same pipeline but run multiple agents sequentially.
            try:
                meta = app.sessions_manager.get_session_meta(str(session_id))
            except Exception:
                meta = None
            is_group = bool(isinstance(meta, dict) and str(meta.get("type") or "").strip().lower() == "group")

            # POC: enable auto telemetry injection in the agent loop (normal sessions only).
            # This injects a user-role __telemetry__ message after each tool turn.
            try:
                if app.agent is not None and not is_group:
                    # NOTE: Telemetry injection is a debugging feature; keep it easy to toggle.
                    tele_on = True
                    try:
                        if isinstance(meta, dict):
                            # Default ON if missing.
                            tele_on = bool(meta.get("telemetry_enabled", True))
                    except Exception:
                        tele_on = True
                    setattr(app.agent, "_auto_telemetry_inject_enabled", bool(tele_on))
                    baseline = {}
                    if isinstance(meta, dict):
                        # Cached totals as-of run start.
                        baseline = {
                            "token_stats_raw_totals": meta.get("token_stats_raw_totals"),
                            "token_stats_raw_totals_main": meta.get("token_stats_raw_totals_main"),
                            "token_stats_raw_totals_subagents": meta.get("token_stats_raw_totals_subagents"),
                            "token_stats_context_last_turn": meta.get("token_stats_context_last_turn"),
                            "token_stats_run_summary_count": meta.get("token_stats_run_summary_count"),
                            "tool_stats_total_calls": meta.get("tool_stats_total_calls"),
                            "tool_stats_total_errors": meta.get("tool_stats_total_errors"),
                            "tool_stats_distinct": meta.get("tool_stats_distinct"),
                            "run_stats_turns_total": meta.get("run_stats_turns_total"),
                        }
                    setattr(app.agent, "_auto_telemetry_session_baseline", baseline)
            except Exception:
                pass

            # Expose the active stream topic to nested tool calls (e.g., subagents) even in group runs.
            try:
                setattr(app, "_active_stream_topic", str(stream_topic))
            except Exception:
                pass

            runner = app.run_group_session if is_group else app.run_agent

            for ev in runner(
                message=message,
                files=files,
                images=images,
                session_id=session_id,
                run_id=run_id,
            ):
                bus.publish(stream_topic, ev)

            try:
                setattr(app, "_active_stream_topic", None)
            except Exception:
                pass

            bus.publish("agent.state.changed", {"run_id": run_id, "session_id": session_id, "state": "finished"})
            if reply_topic:
                app._bus_reply(reply_topic, {"status": "success", "run_id": run_id})

        except Exception as e:
            bus.publish("agent.state.changed", {"run_id": run_id, "session_id": session_id, "state": "error"})
            bus.publish(
                stream_topic,
                {
                    "type": "response.error",
                    "agent_name": "System",
                    "content": {"message": str(e)},
                },
            )
            bus.publish(stream_topic, {"type": "stream.finished", "agent_name": "System", "content": {}})
            if reply_topic:
                app._bus_reply(reply_topic, {"status": "error", "message": str(e), "run_id": run_id})
        finally:
            # Clear run context hints.
            try:
                if app.agent is not None:
                    setattr(app.agent, "_active_stream_topic", None)
                    setattr(app.agent, "_active_run_id", None)
                    setattr(app.agent, "_active_session_id", None)
            except Exception:
                pass

            app._set_inference_running(False)

    threading.Thread(target=work, daemon=True).start()


def bus_subagent_session_entries_get(app: Any, event) -> None:
    """Fetch wrapped entries from an arbitrary sub-agent session store."""
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    store_id = payload.get("store_id")

    if not reply_topic:
        return
    if not isinstance(store_id, str) or not store_id:
        app._bus_reply(reply_topic, {"status": "error", "message": "store_id is required"})
        return

    def work():
        try:
            sm = app.sessions_manager.get_subagent_store(str(store_id))
            entries = sm.get_entries_wrapped(limit=None)
            app._bus_reply(reply_topic, {"status": "success", "store_id": str(store_id), "entries": entries})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_agent_run_subagent(app: Any, event) -> None:
    """Run a sub-agent as a child run and stream events into the parent stream topic."""
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")

    parent_stream_topic = payload.get("parent_stream_topic")
    parent_call_id = payload.get("parent_call_id")
    parent_run_id = payload.get("parent_run_id")
    parent_session_id = payload.get("parent_session_id")
    subagent_name = payload.get("subagent_name")
    mode = (payload.get("mode") or "").strip().lower()
    store_id = payload.get("store_id")
    message = payload.get("message")
    instructions = payload.get("instructions")

    if not reply_topic:
        return

    if not isinstance(parent_stream_topic, str) or not parent_stream_topic:
        app._bus_reply(reply_topic, {"status": "error", "message": "parent_stream_topic is required"})
        return
    if not isinstance(parent_call_id, str) or not parent_call_id:
        app._bus_reply(reply_topic, {"status": "error", "message": "parent_call_id is required"})
        return
    if not isinstance(subagent_name, str) or not subagent_name:
        app._bus_reply(reply_topic, {"status": "error", "message": "subagent_name is required"})
        return
    if mode not in ("persistent", "run"):
        app._bus_reply(reply_topic, {"status": "error", "message": "mode must be 'persistent' or 'run'"})
        return
    if not isinstance(message, str) or not message.strip():
        app._bus_reply(reply_topic, {"status": "error", "message": "message is required"})
        return

    if mode == "run" and (not isinstance(instructions, str) or not instructions.strip()):
        app._bus_reply(reply_topic, {"status": "error", "message": "instructions is required for mode='run'"})
        return

    bus = Runtime.get_event_bus()

    def _extract_text(content_obj: Any) -> str:
        if isinstance(content_obj, str):
            return content_obj
        if isinstance(content_obj, list):
            parts: List[str] = []
            for it in content_obj:
                if isinstance(it, dict):
                    t = it.get("type")
                    if t == "output_text" and isinstance(it.get("text"), str):
                        parts.append(it["text"])
                    elif isinstance(it.get("text"), str):
                        parts.append(it["text"])
            return "".join(parts).strip()
        if isinstance(content_obj, dict):
            if isinstance(content_obj.get("text"), str):
                return content_obj["text"].strip()
            if isinstance(content_obj.get("content"), str):
                return content_obj["content"].strip()
        return ""

    def _tag(ev: Dict[str, Any], *, subagent_id: str) -> Dict[str, Any]:
        out = dict(ev or {})
        out["source"] = "subagent"
        out["subagent_name"] = str(subagent_name)
        out["subagent_id"] = str(subagent_id)
        out["parent_call_id"] = str(parent_call_id)
        out["mode"] = str(mode)
        return out

    def work():
        app._set_inference_running(True)

        subagent_id = str(uuid.uuid4())
        eff_store_id = None
        store = None
        agent = None
        saved_entry_ids: List[str] = []
        inner_text = ""
        sub_run_status = "error"
        sub_error_message = None

        try:

            def _slug(name: str) -> str:
                s = (name or "").strip().lower()
                out2 = []
                for ch in s:
                    if ch.isalnum() or ch in ("_", "-"):
                        out2.append(ch)
                    elif ch.isspace() or ch in ("/", "\\", "."):
                        out2.append("_")
                s2 = "".join(out2).strip("_")
                return s2 or "subagent"

            slug_name = _slug(str(subagent_name))

            psid = str(parent_session_id).strip() if isinstance(parent_session_id, str) else ""

            # Store id conventions live in SessionsManager.
            try:
                eff_store_id = app.sessions_manager.get_subagent_store_id(
                    mode=str(mode),
                    parent_session_id=psid,
                    subagent_name=str(subagent_name),
                    subagent_id=str(subagent_id),
                    store_id=(str(store_id).strip() if isinstance(store_id, str) and store_id.strip() else None),
                )
            except Exception as e:
                app._bus_reply(reply_topic, {"status": "error", "message": f"Failed to compute subagent store_id: {e}"})
                return

            store = app.sessions_manager.get_subagent_store(str(eff_store_id))

            secure_storage = SecureStorage()
            api_key = secure_storage.get_secret("api_token") or None

            try:
                ensure_config_loaded(app)
            except Exception:
                pass

            base_url = str(app.config.app.api.base_url or "").strip()

            agent_id = app.config.resolve_agent_id(str(subagent_name))
            spec = app.config.get_agent(agent_id) if agent_id else None
            spec_from_template = False

            # For one-shot runs with unknown names, use the one-shot template (Config/one_shot.yaml).
            if mode == "run" and spec is None:
                try:
                    spec = app.config.get_one_shot_template()
                except Exception:
                    spec = None
                spec_from_template = spec is not None

            if spec is None:
                # Never fall back to a hardcoded default agent here.
                # If we can't resolve a spec, it's a configuration error.
                if mode == "persistent":
                    app._bus_reply(
                        reply_topic,
                        {
                            "status": "error",
                            "message": f"Unknown persistent sub-agent '{subagent_name}'. Create Config/agents/{slug_name}.yaml first (frontmatter id='{slug_name}').",
                        },
                    )
                else:
                    app._bus_reply(
                        reply_topic,
                        {
                            "status": "error",
                            "message": "Missing Config/one_shot.yaml (one-shot template). Create it via Agents Studio → One-shot template.",
                        },
                    )
                return

            # Build runtime config from the resolved spec.
            if spec_from_template:
                agent_display_name = str(subagent_name)
                allow_memory = True
                allow_session_meta = True
            else:
                agent_display_name = str(spec.display_name)
                allow_memory = (str(mode).lower() == "persistent")
                allow_session_meta = False

            agent_config = app.config.build_runtime_config(
                spec,
                allow_memory=allow_memory,
                allow_session_meta=allow_session_meta,
                allow_recursion=False,
            )

            # Compose final instructions consistently (no duplicated inline hacks).
            parts: List[str] = [f"You are {agent_display_name}."]
            if isinstance(agent_config.instructions, str) and agent_config.instructions.strip():
                parts.append(agent_config.instructions.strip())
            if mode == "run" and isinstance(instructions, str) and instructions.strip():
                parts.append(f"# One-shot instructions\n{instructions.strip()}")
            agent_config.instructions = "\n\n".join(parts).strip()

            # Prompt caching key
            agent_user_id = None
            try:
                md0 = str(mode).strip().lower() if isinstance(mode, str) else ""
                if md0 == "run":
                    agent_user_id = f"pcache_run:{subagent_id}"
                else:
                    if spec is not None and psid:
                        agent_user_id = app.sessions_manager.get_or_create_prompt_cache_key(psid, str(spec.id))
                    else:
                        agent_user_id = f"pcache_persistent:{subagent_id}"
            except Exception:
                if str(mode).strip().lower() == "run":
                    agent_user_id = f"pcache_run:{subagent_id}"
                else:
                    agent_user_id = f"pcache_persistent:{subagent_id}"


            all_tools = get_default_tools()

            tools = app.config.filter_tools(
                all_tools,
                spec,
                allow_memory=allow_memory,
                allow_session_meta=allow_session_meta,
                allow_recursion=False,
            )

            api_mode = get_api_mode_from_app(app)

            agent = create_agent(
                api_key=api_key,
                base_url=base_url,
                name=str(agent_display_name),
                tools=tools,
                user_id=agent_user_id,
                config=agent_config,
                agent_id=(str(spec.id) if (not bool(spec_from_template)) else str(slug_name)),
                api_mode=api_mode,
            )

            # Register this sub-agent run under the parent run_id so Stop can propagate.
            try:
                app._register_active_subagent(parent_run_id, subagent_id, agent)
            except Exception:
                pass
            try:
                setattr(agent, "_active_stream_topic", str(parent_stream_topic))
                setattr(agent, "_active_run_id", str(subagent_id))
            except Exception:
                pass

            # History
            history = []
            if mode == "persistent":
                history = store.get_messages(limit=None)

            history_for_agent = [
                m
                for m in (history or [])
                if not (
                    isinstance(m, dict)
                    and m.get("type") in ("reasoning", "run_summary", "system_notice")
                )
            ]

            inner_text = ""
            saved_entry_ids = []

            sub_run_status = "success"
            sub_error_message = None
            session_items: List[Dict[str, Any]] = []
            wrap_meta_by_call_id = None

            for ev in _iter_with_run_context(
                {
                    "agent_id": getattr(agent, "agent_id", None),
                    "agent_name": getattr(agent, "name", None),
                    "parent_session_id": (str(parent_session_id) if isinstance(parent_session_id, str) else None),
                    "parent_run_id": (str(parent_run_id) if isinstance(parent_run_id, str) else None),
                    "parent_call_id": (str(parent_call_id) if isinstance(parent_call_id, str) else None),
                    "mode": str(mode) if isinstance(mode, str) else None,
                },
                agent.run(
                    message=str(message),
                    input_messages=history_for_agent,
                    files=[],
                    images=[],
                    session_id=None,
                ),
            ):
                if not isinstance(ev, dict):
                    continue

                # Forward into the parent stream (tagged for UI subtree routing).
                try:
                    bus.publish(parent_stream_topic, _tag(ev, subagent_id=subagent_id))
                except Exception:
                    pass

                if ev.get("type") == "response.agent.done":
                    content = ev.get("content", {}) or {}
                    try:
                        done_msg = content.get("message") if isinstance(content, dict) else None
                        done_msg = str(done_msg) if isinstance(done_msg, str) else ""
                        if bool(content.get("stopped")):
                            sub_run_status = "stopped"
                        elif done_msg.lower().startswith("error"):
                            sub_run_status = "error"
                            sub_error_message = done_msg
                    except Exception:
                        pass

                    session_items = content.get("session_items", []) or []
                    wrap_meta_by_call_id = content.get("wrap_meta_by_call_id") if isinstance(content, dict) else None
                    wrap_meta_by_item_index = content.get("wrap_meta_by_item_index") if isinstance(content, dict) else None

                    if session_items:
                        try:
                            saved_entry_ids = store.append_entries(
                                session_items,
                                wrap_meta_by_call_id=wrap_meta_by_call_id if isinstance(wrap_meta_by_call_id, dict) else None,
                                wrap_meta_by_item_index=wrap_meta_by_item_index if isinstance(wrap_meta_by_item_index, dict) else None,
                                run_id=str(subagent_id),
                            )
                        except Exception:
                            saved_entry_ids = []

                    try:
                        for it in reversed(session_items):
                            if isinstance(it, dict) and it.get("role") == "assistant":
                                inner_text = _extract_text(it.get("content"))
                                if inner_text:
                                    break
                    except Exception:
                        pass

                    break

            # Collect fs transaction ids
            txns: List[str] = []
            try:
                for it in (session_items or []):
                    if not isinstance(it, dict):
                        continue
                    txns.extend(store.extract_transaction_ids(it, wrap_meta_by_call_id=wrap_meta_by_call_id if isinstance(wrap_meta_by_call_id, dict) else None))
                seen = set()
                txns = [t for t in txns if isinstance(t, str) and t and not (t in seen or seen.add(t))]
            except Exception:
                txns = []

            # Collect undo mappings
            undo_mappings: List[Dict[str, str]] = []
            try:
                for it in (session_items or []):
                    if not isinstance(it, dict):
                        continue
                    if str(it.get("type") or "") != "function_call_output":
                        continue
                    out_raw = it.get("output")
                    if not isinstance(out_raw, str) or not out_raw.strip():
                        continue
                    try:
                        parsed = json.loads(out_raw)
                    except Exception:
                        parsed = None
                    if not isinstance(parsed, dict):
                        continue
                    st = parsed.get("status")
                    if isinstance(st, str) and st.strip().lower() != "success":
                        continue
                    undone_id = parsed.get("undone_transaction_id")
                    undo_txn_id = parsed.get("undo_transaction_id")
                    if isinstance(undone_id, str) and undone_id and isinstance(undo_txn_id, str) and undo_txn_id:
                        undo_mappings.append({"undone_transaction_id": str(undone_id), "undo_transaction_id": str(undo_txn_id)})

                seen_um = set()
                cleaned = []
                for m in undo_mappings:
                    key = (m.get("undone_transaction_id"), m.get("undo_transaction_id"))
                    if key in seen_um:
                        continue
                    seen_um.add(key)
                    cleaned.append(m)
                undo_mappings = cleaned
            except Exception:
                undo_mappings = []

            # Token usage (sub-agent)
            token_totals = {
                "input_tokens": 0,
                "cached_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
            }
            token_last_turn = None
            try:
                th = getattr(agent, "token_usage_history", None)
                th = th if isinstance(th, dict) else {}
                for v in th.values():
                    if not isinstance(v, dict):
                        continue
                    for k in list(token_totals.keys()):
                        try:
                            token_totals[k] += int(v.get(k, 0) or 0)
                        except Exception:
                            pass

                if th:
                    def _to_int(x):
                        try:
                            return int(x)
                        except Exception:
                            return -1
                    last_k = max(th.keys(), key=_to_int)
                    lt = th.get(last_k)
                    token_last_turn = lt if isinstance(lt, dict) else None
            except Exception:
                token_totals = {
                    "input_tokens": 0,
                    "cached_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_tokens": 0,
                    "total_tokens": 0,
                }
                token_last_turn = None

            app._bus_reply(
                reply_topic,
                {
                    "status": "success",
                    "subagent_name": str(subagent_name),
                    "subagent_id": str(subagent_id),
                    "mode": str(mode),
                    "store_id": (str(eff_store_id) if isinstance(eff_store_id, str) and eff_store_id else None),
                    "saved_entry_ids": saved_entry_ids,
                    "transaction_ids": txns,
                    "undo_mappings": undo_mappings,
                    "inner_message": inner_text,
                    "run_status": str(sub_run_status or "success"),
                    "error_message": (str(sub_error_message) if isinstance(sub_error_message, str) and sub_error_message else None),
                    "token_usage_totals": token_totals,
                    "token_usage_last_turn": token_last_turn,
                },
            )

        except Exception as e:
            try:
                sub_run_status = "error"
                sub_error_message = str(e)

                session_items2 = []
                try:
                    session_items2 = getattr(agent, "session_items_during_run", None) or []
                except Exception:
                    session_items2 = []

                try:
                    if store is not None and isinstance(session_items2, list) and session_items2:
                        wrap_meta_by_call_id2 = getattr(agent, "wrap_meta_by_call_id", None)
                        wrap_meta_by_item_index2 = getattr(agent, "wrap_meta_by_item_index", None)
                        try:
                            saved_entry_ids = store.append_entries(
                                session_items2,
                                wrap_meta_by_call_id=wrap_meta_by_call_id2 if isinstance(wrap_meta_by_call_id2, dict) else None,
                                wrap_meta_by_item_index=wrap_meta_by_item_index2 if isinstance(wrap_meta_by_item_index2, dict) else None,
                                run_id=str(subagent_id),
                            )
                        except Exception:
                            pass
                except Exception:
                    pass

                try:
                    for it in reversed(session_items2 or []):
                        if isinstance(it, dict) and it.get("role") == "assistant":
                            inner_text = _extract_text(it.get("content"))
                            if inner_text:
                                break
                except Exception:
                    pass

                txns: List[str] = []
                try:
                    for it in (session_items2 or []):
                        if not isinstance(it, dict):
                            continue
                        txns.extend(store.extract_transaction_ids(it, wrap_meta_by_call_id=getattr(agent, "wrap_meta_by_call_id", None)))
                    seen = set()
                    txns = [t for t in txns if isinstance(t, str) and t and not (t in seen or seen.add(t))]
                except Exception:
                    txns = []

                undo_mappings: List[Dict[str, str]] = []
                try:
                    for it in (session_items2 or []):
                        if not isinstance(it, dict):
                            continue
                        if str(it.get("type") or "") != "function_call_output":
                            continue
                        out_raw = it.get("output")
                        if not isinstance(out_raw, str) or not out_raw.strip():
                            continue
                        try:
                            parsed = json.loads(out_raw)
                        except Exception:
                            parsed = None
                        if not isinstance(parsed, dict):
                            continue
                        st = parsed.get("status")
                        if isinstance(st, str) and st.strip().lower() != "success":
                            continue
                        undone_id = parsed.get("undone_transaction_id")
                        undo_txn_id = parsed.get("undo_transaction_id")
                        if isinstance(undone_id, str) and undone_id and isinstance(undo_txn_id, str) and undo_txn_id:
                            undo_mappings.append({"undone_transaction_id": str(undone_id), "undo_transaction_id": str(undo_txn_id)})

                    seen_um = set()
                    cleaned = []
                    for m in undo_mappings:
                        key = (m.get("undone_transaction_id"), m.get("undo_transaction_id"))
                        if key in seen_um:
                            continue
                        seen_um.add(key)
                        cleaned.append(m)
                    undo_mappings = cleaned
                except Exception:
                    undo_mappings = []

                token_totals = {
                    "input_tokens": 0,
                    "cached_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_tokens": 0,
                    "total_tokens": 0,
                }
                token_last_turn = None
                try:
                    th = getattr(agent, "token_usage_history", None)
                    th = th if isinstance(th, dict) else {}
                    for v in th.values():
                        if not isinstance(v, dict):
                            continue
                        for k in list(token_totals.keys()):
                            try:
                                token_totals[k] += int(v.get(k, 0) or 0)
                            except Exception:
                                pass

                    if th:
                        def _to_int(x):
                            try:
                                return int(x)
                            except Exception:
                                return -1
                        last_k = max(th.keys(), key=_to_int)
                        lt = th.get(last_k)
                        token_last_turn = lt if isinstance(lt, dict) else None
                except Exception:
                    token_totals = {
                        "input_tokens": 0,
                        "cached_tokens": 0,
                        "output_tokens": 0,
                        "reasoning_tokens": 0,
                        "total_tokens": 0,
                    }
                    token_last_turn = None

                app._bus_reply(
                    reply_topic,
                    {
                        "status": "success",
                        "subagent_name": str(subagent_name),
                        "subagent_id": str(subagent_id),
                        "mode": str(mode),
                        "store_id": (str(eff_store_id) if isinstance(eff_store_id, str) and eff_store_id else None),
                        "saved_entry_ids": saved_entry_ids,
                        "transaction_ids": txns,
                        "undo_mappings": undo_mappings,
                        "inner_message": inner_text,
                        "run_status": "error",
                        "error_message": (str(sub_error_message) if isinstance(sub_error_message, str) and sub_error_message else None),
                        "token_usage_totals": token_totals,
                        "token_usage_last_turn": token_last_turn,
                    },
                )
                return

            except Exception:
                app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

        finally:
            try:
                app._unregister_active_subagent(parent_run_id, subagent_id)
            except Exception:
                pass
            app._set_inference_running(False)

    threading.Thread(target=work, daemon=True).start()


def bus_agent_stop(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    run_id = payload.get("run_id")
    session_id = payload.get("session_id")

    def work():
        try:
            Runtime.get_event_bus().publish(
                "agent.state.changed",
                {"run_id": run_id, "session_id": session_id, "state": "stopping"},
            )
            app.stop_agent()
            app._stop_active_subagents(run_id)
            if reply_topic:
                app._bus_reply(reply_topic, {"status": "success"})
        except Exception as e:
            if reply_topic:
                app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()
