"""Single-agent runner (extracted from src/app.py).

Initial move is intentionally behavior-preserving.
We only split code to reduce the size of src/app.py.

Rules:
- No UI imports.
- Preserve session persistence + run_summary semantics.
- Preserve Stop behavior (graceful; persist partial progress).
"""

from __future__ import annotations

import os
import json

from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional


from ..app_services.run_context_helpers import _iter_with_run_context
from ..app_services.agent_reload import hot_apply_primary_agent


def run_agent(
    app: Any,
    message: Optional[str] = None,
    files: Optional[List[str]] = None,
    images: Optional[List[str]] = None,
    *,
    session_id: str,
    run_id: Optional[str] = None,
) -> Generator[Dict[str, Any], None, None]:
    """Run the primary agent and yield events.

    Extracted from Application.run_agent in src/app.py.
    """
    app._stop_requested = False
    started_at_utc = datetime.now(timezone.utc).replace(microsecond=0)

    if not getattr(app, "agent", None):
        yield {
            "type": "error",
            "agent_name": "System",
            "content": {"message": "Agent not initialized. Please configure API key in Settings."},
        }
        yield {"type": "stream.finished", "agent_name": "System", "content": {}}
        return

    # Hot-reload safety: ensure the live primary agent instance uses the latest saved spec.
    try:
        hot_apply_primary_agent(app, allow_during_inference=True)
    except Exception:
        pass

    # Session-scoped prompt caching key (OpenAI prompt_cache_key).
    # Keep Agent core agnostic: we resolve the key here and just set Agent.user_id.
    try:
        aid = getattr(app.agent, "agent_id", None)
        aid = str(aid).strip() if isinstance(aid, str) else ""
        if not aid:
            aid = "aria"
        ck = app.sessions_manager.get_or_create_prompt_cache_key(str(session_id), aid)
        setattr(app.agent, "user_id", str(ck))
    except Exception:
        # Never fail open to a stale previous run's cache key.
        try:
            setattr(app.agent, "user_id", f"default_user:{aid or 'aria'}")
        except Exception:
            pass

    try:
        # Build agent-visible history (filters reasoning/run_summary/system_notice).
        # Phase 1: also supports "summarized runs" compression via run_summary.description.
        try:
            history_for_agent = app.sessions_manager.get_messages_for_agent(session_id=session_id)
        except Exception:
            history = app.sessions_manager.get_messages(session_id=session_id)
            history_for_agent = [
                m
                for m in (history or [])
                if not (
                    isinstance(m, dict)
                    and m.get("type") in ("reasoning", "run_summary", "system_notice")
                )
            ]

        # Phase 1: persist the user's message immediately with wrapper-only meta (time),
        # but keep it OUT of model-visible content. The model receives the meta as an
        # extra input_text item in the *current* turn.
        received_at_utc = started_at_utc.isoformat().replace("+00:00", "Z")
        received_at_readable = started_at_utc.strftime("%a %b %d, %Y %H:%M:%S UTC")

        # Local (machine) timezone for human-friendly "today/yesterday" semantics.
        try:
            local_dt = started_at_utc.astimezone()
        except Exception:
            local_dt = None
        received_at_local_iso = None
        received_at_local_readable = None
        received_at_tz_offset_minutes = None
        try:
            if local_dt is not None:
                received_at_local_iso = local_dt.isoformat()
                received_at_local_readable = local_dt.strftime("%a %b %d, %Y %H:%M:%S %z")
                off = local_dt.utcoffset()
                if off is not None:
                    received_at_tz_offset_minutes = int(off.total_seconds() // 60)
        except Exception:
            received_at_local_iso = None
            received_at_local_readable = None
            received_at_tz_offset_minutes = None

        persisted_user_entry_id: Optional[str] = None
        try:
            # Persist a user message item for UI/session JSON.
            # CLEAN MODEL:
            # - user_item.content contains only the user's visible text (no Attached-files block, no input_image)
            # - files/dirs/images live in wrapper meta
            user_content: List[Dict[str, Any]] = []

            if isinstance(message, str) and message.strip():
                user_content.append({"type": "input_text", "text": str(message)})
            elif isinstance(message, list):
                # Best-effort: keep only input_text from a structured payload.
                for it in message:
                    if not isinstance(it, dict):
                        continue
                    if it.get("type") == "input_text" and isinstance(it.get("text"), str) and it.get("text"):
                        user_content.append({"type": "input_text", "text": it.get("text")})

            def _norm_meta_path(p: str) -> str:
                try:
                    s = os.path.normpath(str(p))
                    # Normalize drive-letter casing on Windows.
                    try:
                        if os.name == "nt":
                            drv, rest = os.path.splitdrive(s)
                            if drv:
                                s = drv.upper() + rest
                    except Exception:
                        pass
                    return s
                except Exception:
                    return str(p)

            atts: List[Dict[str, Any]] = []
            for fp in (files or []):
                if not isinstance(fp, str) or not fp:
                    continue
                fp2 = _norm_meta_path(fp)
                try:
                    if os.path.isdir(fp2):
                        atts.append({"kind": "dir", "path": fp2})
                    else:
                        atts.append({"kind": "file", "path": fp2})
                except Exception:
                    atts.append({"kind": "file", "path": fp2})

            image_atts: List[Dict[str, Any]] = []
            for b64 in (images or []):
                if isinstance(b64, str) and b64:
                    image_atts.append({"mime": "image/png", "b64": b64})

            user_item = {"role": "user", "content": user_content}

            wm0: Dict[int, Dict[str, Any]] = {
                0: {
                    "owner_id": "human",
                    "received_at_utc": received_at_utc,
                    "received_at_readable": received_at_readable,
                    "received_at_local_iso": received_at_local_iso,
                    "received_at_local_readable": received_at_local_readable,
                    "received_at_tz_offset_minutes": received_at_tz_offset_minutes,
                }
            }
            if atts:
                wm0[0]["attachments"] = atts
            if image_atts:
                wm0[0]["image_attachments"] = image_atts

            ids0 = app.sessions_manager.append_entries(
                session_id=session_id,
                entries=[user_item],
                wrap_meta_by_item_index=wm0,
                run_id=str(run_id) if run_id else None,
            )
            if ids0 and isinstance(ids0[0], str):
                persisted_user_entry_id = ids0[0]
        except Exception:
            persisted_user_entry_id = None

        # Build the model-visible message payload from wrapper meta (no duplication in persisted content).
        message_for_agent: Any = message
        try:
            items: List[Dict[str, Any]] = []

            if isinstance(received_at_local_readable, str) and received_at_local_readable:
                items.append({"type": "input_text", "text": f"META(received_at_local=\"{received_at_local_readable}\")\n"})
            items.append({"type": "input_text", "text": f"META(received_at_utc=\"{received_at_readable}\")\n"})

            try:
                files_meta = [a.get("path") for a in (atts or []) if isinstance(a, dict) and a.get("kind") == "file" and isinstance(a.get("path"), str) and a.get("path")]
                dirs_meta = [a.get("path") for a in (atts or []) if isinstance(a, dict) and a.get("kind") == "dir" and isinstance(a.get("path"), str) and a.get("path")]
                if files_meta:
                    items.append({"type": "input_text", "text": f"META(files={files_meta})\n"})
                if dirs_meta:
                    items.append({"type": "input_text", "text": f"META(dirs={dirs_meta})\n"})
            except Exception:
                pass

            did_prefix = False
            if isinstance(message, list):
                # Keep only user-provided items (no implicit attached-files block here).
                for it in message:
                    if not isinstance(it, dict):
                        continue
                    if it.get("type") == "input_text" and isinstance(it.get("text"), str):
                        txt = it.get("text")
                        if isinstance(txt, str) and txt.strip() and (not did_prefix):
                            s = txt.lstrip()
                            if (not s.startswith("META(")) and (not s.lower().startswith("human:")) and (not s.lower().startswith("user:")):
                                it = dict(it)
                                it["text"] = f"Human: {txt}"
                                did_prefix = True
                    items.append(it)
            elif isinstance(message, str) and message.strip():
                s = str(message).lstrip()
                if s.lower().startswith("human:") or s.lower().startswith("user:"):
                    items.append({"type": "input_text", "text": str(message)})
                else:
                    items.append({"type": "input_text", "text": f"Human: {str(message)}"})

            for im in (image_atts or []):
                if not isinstance(im, dict):
                    continue
                b64 = im.get("b64")
                mime = im.get("mime") or "image/png"
                if isinstance(b64, str) and b64:
                    items.append({"type": "input_image", "image_url": f"data:{mime};base64,{b64}"})

            message_for_agent = items

            # Debug: show the exact message payload we are sending (sanitized; no base64 dumps).
            try:
                def _summarize_user_items(lst: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
                    out = []
                    for it in (lst or []):
                        if not isinstance(it, dict):
                            continue
                        t = it.get("type")
                        if t == "input_text":
                            txt = it.get("text")
                            txt = str(txt) if isinstance(txt, str) else ""
                            out.append({"type": "input_text", "text": (txt[:300] + ("..." if len(txt) > 300 else ""))})
                        elif t == "input_image":
                            url = it.get("image_url")
                            url = str(url) if isinstance(url, str) else ""
                            # Don't print base64; just show mime + length.
                            mime = ""
                            ln = 0
                            if url.startswith("data:") and ";base64," in url:
                                try:
                                    mime = url.split(":", 1)[1].split(";", 1)[0]
                                    ln = len(url.split(",", 1)[1])
                                except Exception:
                                    mime = ""
                                    ln = 0
                            out.append({"type": "input_image", "mime": mime or "(unknown)", "b64_len": ln})
                        else:
                            out.append({"type": str(t or "(unknown)")})
                    return out

                if isinstance(message_for_agent, list):
                    print("[APP] user_message_for_agent:", json.dumps(_summarize_user_items(message_for_agent), ensure_ascii=False, indent=2))
            except Exception:
                pass
        except Exception:
            message_for_agent = message

        for event in _iter_with_run_context(
            {
                "agent_id": getattr(app.agent, "agent_id", None),
                "agent_name": getattr(app.agent, "name", None),
                "session_id": str(session_id) if isinstance(session_id, str) else None,
                "run_id": str(run_id) if isinstance(run_id, str) else None,
            },
            app.agent.run(
                message=message_for_agent,
                input_messages=history_for_agent,
                # Files/images are carried via wrapper meta and injected into message_for_agent.
                files=[],
                images=[],
                session_id=session_id,
            ),
        ):
            # IMPORTANT: do not short-circuit on app-level stop here.
            # We let the Agent emit a proper response.agent.done containing partial
            # session_items/wrap_meta so we can persist progress.

            if event.get("type") == "response.agent.done":
                content = event.get("content", {})
                # We persisted the real user message entry up-front. Use that id for UI actions.
                user_entry_id = persisted_user_entry_id

                session_items = content.get("session_items", [])
                wrap_meta_by_call_id = content.get("wrap_meta_by_call_id") if isinstance(content, dict) else None
                wrap_meta_by_item_index = content.get("wrap_meta_by_item_index") if isinstance(content, dict) else None

                def _drop_primary_user_item(
                    items: List[Dict[str, Any]],
                    meta_by_idx: Any,
                ) -> tuple[List[Dict[str, Any]], Dict[int, Dict[str, Any]]]:
                    """Drop the agent-created synthetic user item (dedupe).

                    We drop the first role=user item that is NOT injected.
                    """
                    meta_src = meta_by_idx if isinstance(meta_by_idx, dict) else {}
                    drop_idx = None
                    for i, it in enumerate(items or []):
                        if not isinstance(it, dict):
                            continue
                        if it.get("role") != "user":
                            continue
                        m = meta_src.get(i)
                        m = m if isinstance(m, dict) else {}
                        if bool(m.get("injected")):
                            continue
                        drop_idx = int(i)
                        break

                    if drop_idx is None:
                        return list(items or []), {int(k): dict(v) for k, v in meta_src.items() if isinstance(v, dict)}

                    kept_items = list(items[:drop_idx] + items[drop_idx + 1 :])

                    shifted: Dict[int, Dict[str, Any]] = {}
                    for k, v in meta_src.items():
                        try:
                            ki = int(k)
                        except Exception:
                            continue
                        if ki == drop_idx:
                            continue
                        if not isinstance(v, dict):
                            continue
                        nk = ki if ki < drop_idx else (ki - 1)
                        shifted[int(nk)] = dict(v)

                    return kept_items, shifted

                saved_entry_ids: List[str] = []
                if session_items:
                    try:
                        items_to_save = list(session_items)
                        item_meta_to_save = wrap_meta_by_item_index

                        # Only dedupe if we successfully persisted the user entry up-front.
                        if isinstance(user_entry_id, str) and user_entry_id:
                            items_to_save, item_meta_to_save = _drop_primary_user_item(
                                items_to_save,
                                item_meta_to_save,
                            )

                        ids_saved = app.sessions_manager.append_entries(
                            session_id=session_id,
                            entries=items_to_save,
                            wrap_meta_by_call_id=wrap_meta_by_call_id if isinstance(wrap_meta_by_call_id, dict) else None,
                            wrap_meta_by_item_index=item_meta_to_save if isinstance(item_meta_to_save, dict) else None,
                            run_id=str(run_id) if run_id else None,
                        )

                        saved_entry_ids = []
                        if isinstance(user_entry_id, str) and user_entry_id:
                            saved_entry_ids.append(str(user_entry_id))
                        saved_entry_ids.extend([str(x) for x in (ids_saved or []) if isinstance(x, str) and x])

                        try:
                            print(f"[APP] Saved {len(items_to_save)} entries to session")
                        except Exception:
                            pass
                    except Exception as e:
                        try:
                            print(f"[APP] Failed to save session: {e}")
                        except Exception:
                            pass

                # Save generated images
                generated_images = content.get("generated_images", [])
                if generated_images:
                    try:
                        app.sessions_manager.add_generated_images(session_id=session_id, images=generated_images)
                        try:
                            print(f"[APP] Saved {len(generated_images)} generated images")
                        except Exception:
                            pass
                    except Exception as e:
                        try:
                            print(f"[APP] Failed to save generated images: {e}")
                        except Exception:
                            pass

                # Append a run summary entry at end.
                run_summary_item = None
                try:
                    finished_at_utc = datetime.now(timezone.utc).replace(microsecond=0)

                    done_msg = content.get("message") if isinstance(content, dict) else None
                    done_msg = str(done_msg) if isinstance(done_msg, str) else ""

                    from ..app_services.run_summary import build_run_summary_item

                    run_summary_item, _ = build_run_summary_item(
                        session_id=str(session_id),
                        run_id=(str(run_id) if isinstance(run_id, str) and run_id else None),
                        started_at_utc=started_at_utc,
                        finished_at_utc=finished_at_utc,
                        stopped=bool(isinstance(content, dict) and content.get("stopped")),
                        done_message=done_msg,
                        saved_entry_ids=list(saved_entry_ids or []),
                        token_usage_history=getattr(app.agent, "token_usage_history", None),
                        sessions_manager=app.sessions_manager,
                        fs_revision_store=getattr(app, "fs_revision_store", None),
                    )

                    # If the agent called the run_summary tool during this run, stamp its description.
                    # Phase 2.1: can target either the current run_id or an older run_id.
                    try:
                        target_rid = ""
                        desc = ""
                        if isinstance(wrap_meta_by_call_id, dict):
                            for m in wrap_meta_by_call_id.values():
                                if not isinstance(m, dict):
                                    continue
                                d = m.get("run_summary_description")
                                rid2 = m.get("run_summary_target_run_id")
                                if isinstance(d, str) and d.strip() and isinstance(rid2, str) and rid2.strip():
                                    desc = d.strip()
                                    target_rid = rid2.strip()
                                    break

                        # If the target is this run, embed it in the current run_summary entry.
                        if desc and target_rid and str(target_rid) == str(run_id):
                            if isinstance(run_summary_item, dict):
                                run_summary_item["description"] = desc
                    except Exception:
                        target_rid = ""
                        desc = ""

                    app.sessions_manager.append_entries(
                        session_id=session_id,
                        entries=[run_summary_item],
                        run_id=str(run_id) if run_id else None,
                    )

                    # If the tool targeted an older run, patch that run_summary entry now.
                    try:
                        if desc and target_rid and str(target_rid) != str(run_id):
                            app.sessions_manager.set_run_summary_description(
                                session_id=str(session_id),
                                run_id=str(target_rid),
                                description=str(desc),
                            )
                    except Exception:
                        pass
                except Exception as e:
                    try:
                        print(f"[APP] Failed to append run_summary: {e}")
                    except Exception:
                        pass
                    run_summary_item = None

                # Update cached token stats in sessions index (best-effort).
                try:
                    app._update_session_token_stats_meta(session_id=session_id)
                except Exception:
                    pass

                # Add saved IDs to the event content for UI to use
                enriched_event = {
                    "type": event.get("type"),
                    "agent_name": event.get("agent_name"),
                    "content": {
                        **content,
                        "run_id": run_id,
                        "saved_entry_ids": saved_entry_ids,
                        "user_entry_id": user_entry_id,
                        "run_summary_item": run_summary_item,
                    },
                    "token_usage_history": app.agent.token_usage_history,
                }
                yield enriched_event
                continue

            yield event

        # Always finish the UI stream
        yield {"type": "stream.finished", "agent_name": "Agent", "content": {}}

    except Exception as e:
        try:
            print(f"[APP] Error running agent: {e}")
            import traceback

            traceback.print_exc()
        except Exception:
            pass

        # Best-effort: persist partial progress so a crash/error doesn't nuke the run.
        try:
            partial_items = []
            wrap_meta_by_call_id = None
            wrap_meta_by_item_index = None
            if app.agent is not None:
                partial_items = getattr(app.agent, "session_items_during_run", None) or []
                wrap_meta_by_call_id = getattr(app.agent, "wrap_meta_by_call_id", None)
                wrap_meta_by_item_index = getattr(app.agent, "wrap_meta_by_item_index", None)

            if isinstance(partial_items, list) and partial_items:
                try:
                    persisted_id = None
                    try:
                        persisted_id = persisted_user_entry_id
                    except Exception:
                        persisted_id = None

                    items_to_save = list(partial_items)
                    item_meta_to_save = wrap_meta_by_item_index

                    # If we already persisted the user message up-front, avoid duplicating it.
                    if isinstance(persisted_id, str) and persisted_id:
                        meta_src = item_meta_to_save if isinstance(item_meta_to_save, dict) else {}
                        drop_idx = None
                        for i, it in enumerate(items_to_save):
                            if not isinstance(it, dict) or it.get("role") != "user":
                                continue
                            m = meta_src.get(i)
                            m = m if isinstance(m, dict) else {}
                            if bool(m.get("injected")):
                                continue
                            drop_idx = int(i)
                            break

                        if drop_idx is not None:
                            items_to_save = list(items_to_save[:drop_idx] + items_to_save[drop_idx + 1 :])
                            shifted: Dict[int, Dict[str, Any]] = {}
                            for k, v in meta_src.items():
                                try:
                                    ki = int(k)
                                except Exception:
                                    continue
                                if ki == drop_idx:
                                    continue
                                if not isinstance(v, dict):
                                    continue
                                nk = ki if ki < drop_idx else (ki - 1)
                                shifted[int(nk)] = dict(v)
                            item_meta_to_save = shifted

                    app.sessions_manager.append_entries(
                        session_id=session_id,
                        entries=items_to_save,
                        wrap_meta_by_call_id=wrap_meta_by_call_id if isinstance(wrap_meta_by_call_id, dict) else None,
                        wrap_meta_by_item_index=item_meta_to_save if isinstance(item_meta_to_save, dict) else None,
                        run_id=str(run_id) if run_id else None,
                    )
                except Exception:
                    pass

            # Persist a visible marker (not part of agent context).
            try:
                ts = (
                    datetime.now(timezone.utc)
                    .replace(microsecond=0)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
                notice = {
                    "type": "system_notice",
                    "severity": "error",
                    "ts_utc": ts,
                    "title": "Run error",
                    "message": f"The run ended due to an error. Partial progress was saved.\n\n{str(e)}",
                    "action": {"name": "run_error", "run_id": str(run_id) if run_id else None},
                    "failed_transactions": [],
                }
                app.sessions_manager.append_entries(
                    session_id=session_id,
                    entries=[notice],
                    run_id=str(run_id) if run_id else None,
                )
            except Exception:
                pass
        except Exception:
            pass

        yield {
            "type": "response.error",
            "agent_name": "System",
            "content": {"message": f"Error: {str(e)}"},
        }
        yield {"type": "stream.finished", "agent_name": "System", "content": {}}
