"""Group Session runner (extracted from src/app.py).

Initial move is intentionally *verbatim* behavior-wise.
Refactor later, once we have a clean seam.

Rules:
- No UI imports.
- No bus topic changes.
- Preserve broadcast cursor semantics + mirror pointer meta.
"""

from __future__ import annotations

import uuid
import os
import json
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional

from ..core import Agent
from ..app_services.agent_factory import create_agent, get_api_mode_from_app
from ..app_services.agent_reload import ensure_config_loaded
from ..tools import get_default_tools


from ..app_services.run_context_helpers import _iter_with_run_context


def run_group_session(
    app: Any,
    message: Optional[str] = None,
    files: Optional[List[str]] = None,
    images: Optional[List[str]] = None,
    *,
    session_id: str,
    run_id: Optional[str] = None,
) -> Generator[Dict[str, Any], None, None]:
    """Run a "group session" turn: multiple participant agents run sequentially.

    Extracted from Application.run_group_session in src/app.py.
    """
    app._stop_requested = False

    # Hot-reload safety: ensure we use newest model/tool settings.
    try:
        ensure_config_loaded(app)
    except Exception:
        pass
    started_at_utc = datetime.now(timezone.utc).replace(microsecond=0)
    started_at_iso = started_at_utc.isoformat().replace("+00:00", "Z")
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

    # Credentials check (same failure mode as single runs)
    api_key = None
    try:
        api_key = app.secure_storage.get_secret("api_token") or None
    except Exception:
        api_key = None

    if not api_key:
        yield {
            "type": "response.error",
            "agent_name": "System",
            "content": {"message": "No API client configured. Please set API key."},
        }
        yield {"type": "stream.finished", "agent_name": "System", "content": {}}
        return

    # Load group meta (participants)
    try:
        meta = app.sessions_manager.get_session_meta(str(session_id))
    except Exception:
        meta = None

    raw_parts: List[Dict[str, Any]] = []
    if isinstance(meta, dict) and isinstance(meta.get("participants"), list):
        raw_parts = [p for p in meta.get("participants") if isinstance(p, dict)]

    # Defensive security gateway:
    # - dedupe agent_ids (duplicate ids collide on owner_id + participant store path)
    # - drop unknown/invalid agent_ids (shouldn't happen, but session meta can be edited/corrupted)
    try:
        ensure_config_loaded(app)
    except Exception:
        pass

    parts: List[Dict[str, Any]] = []
    seen_ids = set()
    for p in raw_parts:
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
        parts.append({"agent_id": aid, "display_name": dn})

    # Fail-safe: if misconfigured (or everything got filtered), fall back to single-agent behavior.
    if not parts:
        for ev in app.run_agent(
            message=message,
            files=files,
            images=images,
            session_id=session_id,
            run_id=run_id,
        ):
            yield ev
        return

    # --- helpers ---
    def _now_utc_iso() -> str:
        return (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    def _norm_owner_id(agent_id: str) -> str:
        aid = str(agent_id or "").strip()
        return f"agent:{aid}" if aid else "agent"

    def _owner_of_wrapped(we: Dict[str, Any]) -> str:
        oid = we.get("owner_id")
        if isinstance(oid, str) and oid:
            return oid
        # Legacy heuristic
        entry = we.get("content") if isinstance(we.get("content"), dict) else {}
        role = entry.get("role") if isinstance(entry, dict) else None
        if role == "user":
            return "human"
        if role == "assistant":
            return "agent:aria"
        t = entry.get("type") if isinstance(entry, dict) else None
        if t in ("function_call", "function_call_output"):
            return "agent:aria"
        return "runner"

    def _build_history_for_owner(*, owner_id: str, exclude_entry_id: Optional[str]) -> List[Dict[str, Any]]:
        """Build API history for a participant.

        Critical rule: do NOT feed other participants' assistant/tool items into this participant's context.
        """
        try:
            wrapped = app.sessions_manager.get_entries_wrapped(session_id=session_id)
        except Exception:
            wrapped = []

        # Match SessionsManager.get_messages_for_agent():
        # - drop any wrapped entry with survive == False
        # - also drop injected messages whose origin_tool_call_id belongs to a dropped tool call
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

        out: List[Dict[str, Any]] = []
        for we in (wrapped or []):
            if not isinstance(we, dict):
                continue
            if exclude_entry_id and we.get("id") == exclude_entry_id:
                continue

            kind = we.get("kind")
            if isinstance(kind, str) and kind in ("reasoning", "run_summary", "system_notice"):
                continue

            # Drop tool calls/outputs that opted out of surviving into future context.
            if we.get("survive") is False:
                continue

            # Also drop injected messages originating from a dropped tool call (e.g., images_get).
            if bool(we.get("injected")):
                ocid = we.get("origin_tool_call_id")
                if isinstance(ocid, str) and ocid in drop_call_ids:
                    continue

            entry = we.get("content") if isinstance(we.get("content"), dict) else {}
            if not isinstance(entry, dict):
                continue

            # Filter by role/type + owner.
            oid = _owner_of_wrapped(we)
            role = entry.get("role")
            typ = entry.get("type")

            if role == "assistant":
                if oid != owner_id:
                    continue

            if typ in ("function_call", "function_call_output", "reasoning"):
                if oid != owner_id:
                    continue

            if role == "user" and bool(we.get("injected")):
                # Tool-injected user-role items are owned; don't spam other participants.
                if oid not in (owner_id, "human"):
                    continue

            out.append(entry)

        return out

    def _build_history_for_store(*, store: Any, exclude_entry_id: Optional[str]) -> List[Dict[str, Any]]:
        """Build API history for a participant from their own persistent store.

        We keep this store in sync with the main session via mirrored entries.
        """
        try:
            wrapped = store.get_entries_wrapped(limit=None)
        except Exception:
            wrapped = []

        # Match SessionsManager.get_messages_for_agent():
        # - drop any wrapped entry with survive == False
        # - also drop injected messages whose origin_tool_call_id belongs to a dropped tool call
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

        out: List[Dict[str, Any]] = []
        for we in (wrapped or []):
            if not isinstance(we, dict):
                continue
            if exclude_entry_id and we.get("id") == exclude_entry_id:
                continue

            kind = we.get("kind")
            if isinstance(kind, str) and kind in ("reasoning", "run_summary", "system_notice"):
                continue

            # Drop tool calls/outputs that opted out of surviving into future context.
            if we.get("survive") is False:
                continue

            # Also drop injected messages originating from a dropped tool call (e.g., images_get).
            if bool(we.get("injected")):
                ocid = we.get("origin_tool_call_id")
                if isinstance(ocid, str) and ocid in drop_call_ids:
                    continue

            entry = we.get("content") if isinstance(we.get("content"), dict) else {}
            if not isinstance(entry, dict):
                continue

            # Also filter raw item types that should not be fed to the model.
            t = entry.get("type")
            if isinstance(t, str) and t in ("reasoning", "run_summary", "system_notice"):
                continue

            # Inject wrapper-only meta into model-visible user messages.
            try:
                if (
                    str(entry.get("type") or "") == "message"
                    and str(entry.get("role") or "") == "user"
                    and not bool(we.get("injected"))
                ):
                    co = entry.get("content")
                    legacy_items: List[Dict[str, Any]] = []
                    if isinstance(co, list):
                        legacy_items = [it for it in co if isinstance(it, dict)]
                    elif isinstance(co, str) and co:
                        legacy_items = [{"type": "input_text", "text": co}]

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

                    atts2 = we.get("attachments") if isinstance(we.get("attachments"), list) else []
                    files_meta = [_norm_meta_path(a.get("path")) for a in (atts2 or []) if isinstance(a, dict) and a.get("kind") == "file" and isinstance(a.get("path"), str) and a.get("path")]
                    dirs_meta = [_norm_meta_path(a.get("path")) for a in (atts2 or []) if isinstance(a, dict) and a.get("kind") == "dir" and isinstance(a.get("path"), str) and a.get("path")]
                    if files_meta:
                        meta_items.append({"type": "input_text", "text": f"META(files={files_meta})\n"})
                    if dirs_meta:
                        meta_items.append({"type": "input_text", "text": f"META(dirs={dirs_meta})\n"})

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
                        legacy_items = [it for it in legacy_items if it.get("type") != "input_image"]

                    # If legacy already starts with META(...), don't add our meta_items again.
                    has_legacy_meta = False
                    if legacy_items:
                        first = legacy_items[0]
                        if isinstance(first, dict) and first.get("type") == "input_text" and isinstance(first.get("text"), str):
                            if str(first.get("text") or "").startswith("META("):
                                has_legacy_meta = True

                    # Prefix the human's actual text (agent-facing only).
                    try:
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
                                break
                            legacy_items[i] = {"type": "input_text", "text": f"Human: {txt}"}
                            break
                    except Exception:
                        pass

                    new_content = []
                    if not has_legacy_meta:
                        new_content.extend(meta_items)
                    new_content.extend(legacy_items)
                    new_content.extend(injected_images)

                    if new_content:
                        e2 = dict(entry)
                        e2["content"] = new_content
                        entry = e2
            except Exception:
                pass

            out.append(entry)

        return out

    def _shift_item_meta(meta_by_idx: Any, drop_n: int) -> Dict[int, Dict[str, Any]]:
        """Shift wrap_meta_by_item_index by -drop_n after dropping leading items."""
        if not isinstance(meta_by_idx, dict):
            return {}
        out2: Dict[int, Dict[str, Any]] = {}
        for k, v in meta_by_idx.items():
            try:
                ki = int(k)
            except Exception:
                continue
            if ki < drop_n:
                continue
            if not isinstance(v, dict):
                continue
            out2[int(ki - drop_n)] = dict(v)
        return out2

    # --- persist the human user message ONCE ---
    # CLEAN MODEL:
    # - user_item.content contains only the user's visible text
    # - files/dirs/images live in wrapper meta
    user_content: List[Dict[str, Any]] = []

    if isinstance(message, str) and message.strip():
        user_content.append({"type": "input_text", "text": str(message)})

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

    # Group participants get their own real persistent stores on disk (session-scoped).
    # We mirror the user's message into each participant store, then persist the user message
    # once into the main session with wrapper-only mirror pointers for safe tail-trim.
    participant_store_info_by_owner: Dict[str, Dict[str, Any]] = {}


    mirrors: List[Dict[str, Any]] = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        aid = p.get("agent_id") or p.get("id") or p.get("name")
        aid = str(aid).strip() if isinstance(aid, str) else ""
        if not aid:
            continue
        oid = _norm_owner_id(aid)
        store_id = app.sessions_manager.get_group_participant_store_id(session_id=str(session_id), agent_id=aid)

        try:
            sm = app.sessions_manager.get_subagent_store(str(store_id))
        except Exception:
            continue

        try:
            wm_u = {
                0: {
                    "owner_id": "human",
                    "group_round": 0,
                    "group_session": True,
                    "received_at_utc": started_at_iso,
                    "received_at_readable": received_at_readable,
                    "received_at_local_iso": received_at_local_iso,
                    "received_at_local_readable": received_at_local_readable,
                    "received_at_tz_offset_minutes": received_at_tz_offset_minutes,
                }
            }
            if atts:
                wm_u[0]["attachments"] = atts
            if image_atts:
                wm_u[0]["image_attachments"] = image_atts
            # Mark this as mirrored so tail-delete can trim deterministically via pointers.
            wm_u[0]["group_participant_mirror_source"] = "main_user_message"
            pid_list = sm.append_entries(
                entries=[user_item],
                wrap_meta_by_item_index=wm_u,
                run_id=str(run_id) if run_id else None,
            )
            pid = pid_list[0] if pid_list else None
        except Exception:
            pid = None

        participant_store_info_by_owner[oid] = {
            "agent_id": aid,
            "owner_id": oid,
            "store_id": str(store_id),
            "store": sm,
            "mirrored_user_entry_id": (str(pid) if isinstance(pid, str) and pid else None),
        }

        if isinstance(pid, str) and pid:
            mirrors.append({"store_id": str(store_id), "entry_id": str(pid), "agent_id": aid, "owner_id": oid})

    user_ids: List[str] = []
    try:
        wm0 = {
            0: {
                "owner_id": "human",
                "group_round": 0,
                "group_session": True,
                "received_at_utc": started_at_iso,
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
        if mirrors:
            wm0[0]["group_participant_mirrors"] = mirrors

        user_ids = app.sessions_manager.append_entries(
            session_id=session_id,
            entries=[user_item],
            wrap_meta_by_item_index=wm0,
            run_id=str(run_id) if run_id else None,
        )
    except Exception:
        user_ids = []

    user_entry_id = user_ids[0] if user_ids else None
    saved_entry_ids: List[str] = list(user_ids)

    merged_wrap_meta_by_call_id: Dict[str, Dict[str, Any]] = {}

    # --- run group participants (sequential) ---
    all_tools = get_default_tools()
    base_url = str(app.config.app.api.base_url or "").strip()

    # Token aggregation across participants
    totals_main = {
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    totals_subagents = {
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }

    # Per-participant usage breakdown for this group run (persisted into the final run_summary).
    # Keyed by agent_id (stable); includes display_name for UI.
    participant_usage: Dict[str, Dict[str, Any]] = {}

    # Owner display map (for broadcast formatting)
    owner_display: Dict[str, str] = {}
    try:
        for p in parts:
            if not isinstance(p, dict):
                continue
            aid = p.get("agent_id") or p.get("id") or p.get("name")
            aid = str(aid).strip() if isinstance(aid, str) else ""
            if not aid:
                continue
            dn = p.get("display_name") or p.get("display") or aid
            dn = str(dn).strip() if isinstance(dn, str) and str(dn).strip() else aid
            owner_display[_norm_owner_id(aid)] = dn
    except Exception:
        owner_display = {}

    def _extract_last_assistant_text(items: List[Dict[str, Any]]) -> str:
        try:
            for it in reversed(items or []):
                if isinstance(it, dict) and it.get("role") == "assistant":
                    c = it.get("content")
                    if isinstance(c, str):
                        return c.strip()
                    if isinstance(c, list):
                        parts2 = []
                        for p2 in c:
                            if isinstance(p2, dict):
                                if isinstance(p2.get("text"), str):
                                    parts2.append(p2.get("text"))
                                elif p2.get("type") == "output_text" and isinstance(p2.get("text"), str):
                                    parts2.append(p2.get("text"))
                        return "".join(parts2).strip()
                    if isinstance(c, dict):
                        if isinstance(c.get("text"), str):
                            return c.get("text").strip()
            return ""
        except Exception:
            return ""

    def _extract_group_pass_reason(items: List[Dict[str, Any]]) -> str:
        """Extract group_pass(reason) from function_call arguments (best-effort)."""
        try:
            for it in items or []:
                if not isinstance(it, dict):
                    continue
                if it.get("type") != "function_call" or it.get("name") != "group_pass":
                    continue

                args = it.get("arguments")
                if args is None:
                    args = it.get("args")

                data: Dict[str, Any] = {}
                if isinstance(args, dict):
                    data = args
                elif isinstance(args, str) and args.strip():
                    try:
                        data = json.loads(args)
                    except Exception:
                        data = {}

                rsn = data.get("reason") if isinstance(data, dict) else None
                if isinstance(rsn, str):
                    rsn2 = rsn.strip()
                    return rsn2
            return ""
        except Exception:
            return ""

    def _extract_ask_human_public_events(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract ask_human public Q/A events from function_call + function_call_output pairs."""
        out: List[Dict[str, Any]] = []
        try:
            calls: List[Dict[str, Any]] = []
            outputs: Dict[str, Any] = {}

            for it in items or []:
                if isinstance(it, dict) and it.get("type") == "function_call_output":
                    cid = it.get("call_id")
                    if isinstance(cid, str) and cid:
                        outputs[cid] = it.get("output")

            for it in items or []:
                if not isinstance(it, dict):
                    continue
                if it.get("type") != "function_call" or it.get("name") != "ask_human":
                    continue
                cid = it.get("call_id")
                if not isinstance(cid, str) or not cid:
                    continue

                args = it.get("arguments")
                if args is None:
                    args = it.get("args")

                data: Dict[str, Any] = {}
                if isinstance(args, dict):
                    data = args
                elif isinstance(args, str) and args.strip():
                    try:
                        data = json.loads(args)
                    except Exception:
                        data = {}

                vis = str(data.get("visibility") or "").strip().lower() if isinstance(data, dict) else ""
                if vis != "public":
                    continue

                q = data.get("question") if isinstance(data, dict) else None
                q = str(q) if isinstance(q, str) else ""
                q = " ".join(q.strip().split())
                if not q:
                    continue

                # Parse tool output for status/message.
                st = "error"
                ans = ""
                raw_out = outputs.get(cid)
                if isinstance(raw_out, str) and raw_out.strip():
                    try:
                        parsed = json.loads(raw_out)
                    except Exception:
                        parsed = None
                    if isinstance(parsed, dict):
                        st = str(parsed.get("status") or "error").strip().lower()
                        msg = parsed.get("message")
                        if isinstance(msg, str):
                            ans = " ".join(msg.strip().split())
                # Truncate to keep broadcasts sane.
                if len(q) > 300:
                    q = q[:297] + "..."
                if len(ans) > 300:
                    ans = ans[:297] + "..."

                out.append({"kind": "human", "question": q, "answer": ans, "status": st})

            return out
        except Exception:
            return out

    def _compose_broadcast_events(
        events: List[Dict[str, Any]],
        *,
        exclude_owner_id: str,
    ) -> List[Dict[str, Any]]:
        """Compose broadcast from an ordered list of events.

        Each event is one of:
          - reply:  {"owner_id": "agent:...", "kind": "reply", "text": "..."}
          - pass:   {"owner_id": "agent:...", "kind": "pass", "reason": "..."}
          - human:  {"owner_id": "agent:...", "kind": "human", "question": "...", "answer": "...", "status": "success|cancelled|timeout|error"}

        We exclude events from the target owner (exclude-self) so a participant does not
        receive their own prior output as a broadcast.
        """
        items: List[Dict[str, Any]] = []

        lines: List[str] = []
        for ev in (events or []):
            if not isinstance(ev, dict):
                continue
            oid = ev.get("owner_id")
            if not isinstance(oid, str) or not oid:
                continue
            if oid == exclude_owner_id:
                continue

            kind = ev.get("kind")
            kind = str(kind).strip().lower() if isinstance(kind, str) and kind.strip() else "reply"

            nm = owner_display.get(oid) or oid

            if kind == "pass":
                rsn = ev.get("reason")
                if not isinstance(rsn, str) or not rsn.strip():
                    continue
                rsn2 = " ".join(rsn.strip().split())
                lines.append(f"{nm} called group_pass: {rsn2}")
                continue

            if kind == "human":
                q = ev.get("question")
                a = ev.get("answer")
                st2 = ev.get("status")
                q = " ".join(str(q or "").strip().split())
                a = " ".join(str(a or "").strip().split())
                st2 = str(st2 or "").strip().lower()
                if not q:
                    continue
                if st2 == "success" and a:
                    lines.append(f"{nm} asked the human: {q} -> {a}")
                elif st2 in ("cancelled", "canceled"):
                    lines.append(f"{nm} asked the human: {q} -> (cancelled)")
                elif st2 == "timeout":
                    lines.append(f"{nm} asked the human: {q} -> (timeout)")
                else:
                    lines.append(f"{nm} asked the human: {q} -> (no reply)")
                continue

            txt = ev.get("text")
            if not isinstance(txt, str) or not txt.strip():
                continue
            lines.append(f"{nm}: {txt.strip()}")

        if not lines:
            return []

        items.append(
            {
                "type": "input_text",
                "text": (
                    "[Broadcast from other participants]\n"
                    "Respond to the messages below (address participants by name), or call group_pass(reason=...) if you have nothing new."
                ),
            }
        )

        for ln in lines:
            items.append({"type": "input_text", "text": ln})

        return items

    # Phase 1 default is a single round (no auto back-and-forth).
    # When we enable Phase 2 loop semantics, we can raise this via session meta.
    # Stop mechanism: loop until everyone calls group_pass (or we hit a hard safety cap).
    hard_cap_rounds = 50

    # Cursor-based broadcast inbox (prevents rebroadcast loops across rounds):
    # - broadcast_events is an ordered log of participant events (replies + passes).
    # - delivered_cursor_by_owner tracks how much of broadcast_events each participant has "seen".
    broadcast_events: List[Dict[str, Any]] = []
    delivered_cursor_by_owner: Dict[str, int] = {}
    try:
        for p in parts:
            if not isinstance(p, dict):
                continue
            aid = p.get("agent_id") or p.get("id") or p.get("name")
            aid = str(aid).strip() if isinstance(aid, str) else ""
            if not aid:
                continue
            delivered_cursor_by_owner[_norm_owner_id(aid)] = 0
    except Exception:
        delivered_cursor_by_owner = {}

    def _pending_events_for(owner_id: str) -> List[Dict[str, Any]]:
        try:
            start = int(delivered_cursor_by_owner.get(owner_id, 0) or 0)
        except Exception:
            start = 0
        if start < 0:
            start = 0
        pending = broadcast_events[start:]
        # Exclude-self happens in _compose_broadcast_events.
        return pending if isinstance(pending, list) else []

    round_idx = 0

    while True:
        if app._stop_requested:
            break
        if round_idx >= hard_cap_rounds:
            break

        # Stop if no participant has any undelivered broadcast messages.
        if round_idx > 0:
            any_pending = False
            try:
                for p in parts:
                    if not isinstance(p, dict):
                        continue
                    aid = p.get("agent_id") or p.get("id") or p.get("name")
                    aid = str(aid).strip() if isinstance(aid, str) else ""
                    if not aid:
                        continue
                    oid = _norm_owner_id(aid)
                    pend = _pending_events_for(str(oid))
                    # If there's at least one pending event from someone else, this participant has work to see.
                    if any(isinstance(ev, dict) and ev.get("owner_id") != oid for ev in (pend or [])):
                        any_pending = True
                        break
            except Exception:
                any_pending = False
            if not any_pending:
                break

        replies_this_round: Dict[str, str] = {}
        passes_this_round: Dict[str, bool] = {}

        broadcast_len_before_round = int(len(broadcast_events))

        owner_ids_this_round: List[str] = []

        for p_idx, p in enumerate(parts):
            if app._stop_requested:
                break
            agent_id = p.get("agent_id") or p.get("id") or p.get("name")
            agent_id = str(agent_id).strip() if isinstance(agent_id, str) else ""
            if not agent_id:
                continue

            display_name = p.get("display_name") or p.get("display") or agent_id
            display_name = str(display_name).strip() if isinstance(display_name, str) and str(display_name).strip() else agent_id

            owner_id = _norm_owner_id(agent_id)
            try:
                if owner_id and owner_id not in owner_ids_this_round:
                    owner_ids_this_round.append(str(owner_id))
            except Exception:
                pass

            # Round input (cursor-based broadcast inbox):
            # - Round 0: participant0 gets the human user's message as the turn message.
            # - Everyone else: receive only *undelivered* broadcast replies (and see the user message in history).
            input_payload: Any = None

            if round_idx == 0 and p_idx == 0:
                # Phase 1: give participant0 the same time-context as everyone else.
                # (They exclude the mirrored user entry from history, so inject meta here.)
                has_any_input = bool(
                    str(message or "").strip()
                    or (isinstance(files, list) and len(files) > 0)
                    or (isinstance(images, list) and len(images) > 0)
                )
                if not has_any_input:
                    input_payload = ""
                else:
                    items0: List[Dict[str, Any]] = []
                    if isinstance(received_at_local_readable, str) and received_at_local_readable:
                        items0.append({"type": "input_text", "text": f"META(received_at_local=\"{received_at_local_readable}\")\n"})
                    items0.append({"type": "input_text", "text": f"META(received_at_utc=\"{received_at_readable}\")\n"})

                    try:
                        files_meta = [a.get("path") for a in (atts or []) if isinstance(a, dict) and a.get("kind") == "file" and isinstance(a.get("path"), str) and a.get("path")]
                        dirs_meta = [a.get("path") for a in (atts or []) if isinstance(a, dict) and a.get("kind") == "dir" and isinstance(a.get("path"), str) and a.get("path")]
                        if files_meta:
                            items0.append({"type": "input_text", "text": f"META(files={files_meta})\n"})
                        if dirs_meta:
                            items0.append({"type": "input_text", "text": f"META(dirs={dirs_meta})\n"})
                    except Exception:
                        pass

                    if isinstance(message, str) and str(message).strip():
                        s = str(message).lstrip()
                        if s.lower().startswith("human:") or s.lower().startswith("user:"):
                            items0.append({"type": "input_text", "text": str(message)})
                        else:
                            items0.append({"type": "input_text", "text": f"Human: {str(message)}"})

                    for im in (image_atts or []):
                        if not isinstance(im, dict):
                            continue
                        b64 = im.get("b64")
                        mime = im.get("mime") or "image/png"
                        if isinstance(b64, str) and b64:
                            items0.append({"type": "input_image", "image_url": f"data:{mime};base64,{b64}"})

                    input_payload = items0
            else:
                pending = _pending_events_for(owner_id)
                input_payload = _compose_broadcast_events(pending, exclude_owner_id=owner_id)

            # If there's nothing to broadcast to this participant (e.g., only their own reply existed last round),
            # we STILL run them with an explicit "no new messages" broadcast so rounds don't desync.
            if isinstance(input_payload, str):
                if not input_payload.strip():
                    # Round 0 participant0: only skip if EVERYTHING is empty (message + files + images).
                    if round_idx == 0 and p_idx == 0:
                        has_any_input = bool(
                            str(message or "").strip()
                            or (isinstance(files, list) and len(files) > 0)
                            or (isinstance(images, list) and len(images) > 0)
                        )
                        if not has_any_input:
                            continue
                    # Otherwise run with a "no new messages" broadcast; user message is in history.
                    input_payload = [
                        {
                            "type": "input_text",
                            "text": (
                                "[Broadcast from other participants]\n"
                                "(Broadcast inbox is empty for you right now.) "
                                "Reply to the human user's message, or call group_pass(reason=...) if you have nothing new."
                            ),
                        }
                    ]
            elif isinstance(input_payload, list):
                if not input_payload:
                    # If there is nothing to broadcast, still run. Round 0 participant0 may still have
                    # meaningful input via attachments/images.
                    if round_idx == 0 and p_idx == 0:
                        has_any_input = bool(
                            str(message or "").strip()
                            or (isinstance(files, list) and len(files) > 0)
                            or (isinstance(images, list) and len(images) > 0)
                        )
                        if not has_any_input:
                            continue
                    input_payload = [
                        {
                            "type": "input_text",
                            "text": (
                                "[Broadcast from other participants]\n"
                                "(Broadcast inbox is empty for you right now.) "
                                "Reply to the human user's message, or call group_pass(reason=...) if you have nothing new."
                            ),
                        }
                    ]
            else:
                continue

            # UI boundary (so sequential participants don't smear together)
            yield {
                "type": "group.participant.started",
                "agent_name": str(display_name),
                "content": {"owner_id": owner_id, "ts": _now_utc_iso(), "round": int(round_idx)},
            }

            # Resolve agent spec
            try:
                ensure_config_loaded(app)
            except Exception:
                pass

            spec = app.config.get_agent(str(agent_id))
            if spec is None:
                yield {
                    "type": "response.error",
                    "agent_name": "System",
                    "content": {"message": f"Unknown group participant agent_id '{agent_id}'"},
                }
                continue

            room_has_ariane = False
            try:
                for _pp in (parts or []):
                    if not isinstance(_pp, dict):
                        continue
                    _aid = _pp.get("agent_id") or _pp.get("id") or ""
                    _dn = _pp.get("display_name") or _pp.get("name") or ""
                    _aid = str(_aid).strip().lower() if isinstance(_aid, str) else ""
                    _dn = str(_dn).strip().lower() if isinstance(_dn, str) else ""
                    if _aid == "ariane" or _dn == "ariane":
                        room_has_ariane = True
                        break
            except Exception:
                room_has_ariane = False

            # Runtime spec copy: enable the whole group_session tool group (prompt chapter + tools)
            spec_rt = spec
            try:
                spec_rt = spec.model_copy(deep=True)  # pydantic v2
            except Exception:
                try:
                    spec_rt = spec.copy(deep=True)  # pydantic v1
                except Exception:
                    spec_rt = spec

            try:
                sel = getattr(getattr(spec_rt, "tools", None), "groups", None)
                groups_map = dict(sel) if isinstance(sel, dict) else {}

                def _ensure_tools(gid: str, tools: List[str]) -> None:
                    g = str(gid).strip().lower()
                    if not g:
                        return
                    cur = groups_map.get(g)
                    cur_list = [str(x).strip() for x in (cur or []) if isinstance(x, str) and str(x).strip()]
                    cur_set = {x for x in cur_list}
                    for t in (tools or []):
                        tn = str(t).strip()
                        if tn and tn not in cur_set:
                            cur_list.append(tn)
                            cur_set.add(tn)
                    groups_map[g] = cur_list

                # Always enable the group-session protocol tools.
                _ensure_tools("group_session", ["group_pass", "ask_human"])

                # Phase 3: allow run_subagent for all participants.
                _ensure_tools("subagents", ["run_subagent", "get_subagents_list"])

                # consult_ariane is allowed ONLY for Aria, and only when Ariane is not already in the room.
                if str(spec_rt.id).strip().lower() == "aria" and not bool(room_has_ariane):
                    _ensure_tools("consult_inner_voice", ["consult_ariane"])
                else:
                    # Strip it defensively even if someone misconfigured the agent.
                    try:
                        groups_map.pop("consult_inner_voice", None)
                    except Exception:
                        pass

                spec_rt.tools.groups = groups_map
            except Exception:
                pass

            agent_config = app.config.build_runtime_config(
                spec_rt,
                allow_memory=True,
                allow_session_meta=(str(spec_rt.id).lower() == "aria"),
                allow_recursion=True,
            )
            # Dynamic group-session context (must be injected at runtime).
            try:
                extra = ""
                try:
                    if str(getattr(spec_rt, "role", "")).strip().lower() == "family":
                        extra = "In this group session, you MAY address the human user directly.\n"
                    if str(spec_rt.id).strip().lower() == "aria" and bool(room_has_ariane):
                        extra = (extra or "") + "Ariane is already a participant in this room. Collaborate with her here; do not call consult_ariane.\n"
                except Exception:
                    extra = ""

                try:
                    names = []
                    for _oid, _dn in (owner_display or {}).items():
                        if isinstance(_dn, str) and _dn.strip():
                            names.append(_dn.strip())
                    # Keep it compact.
                    uniq = []
                    seen = set()
                    for n in names:
                        if n not in seen:
                            seen.add(n)
                            uniq.append(n)
                    participants_csv = ", ".join(uniq[:8])
                except Exception:
                    participants_csv = ""

                ctx = (
                    "\n\n# Group Session Runtime Context\n\n"
                    f"You are speaking as: {str(display_name)}.\n"
                    + (f"Other participants: {participants_csv}.\n" if participants_csv else "")
                    + (extra or "")
                )
                agent_config.instructions = (str(agent_config.instructions or "") + ctx).strip()
            except Exception:
                pass

            tools = app.config.filter_tools(
                all_tools,
                spec_rt,
                allow_memory=True,
                # Only Aria may set session title/description; other participants should not rename sessions.
                allow_session_meta=(str(spec_rt.id).lower() == "aria"),
                allow_recursion=True,
            )

            # Phase 3 policy: consult_ariane is exclusive to Aria and hidden when Ariane is already in the room.
            try:
                if str(spec_rt.id).strip().lower() != "aria" or bool(room_has_ariane):
                    filtered = []
                    for t in (tools or []):
                        nm = None
                        try:
                            sch = getattr(t, "schema", None)
                            if isinstance(sch, dict):
                                nm = sch.get("name")
                        except Exception:
                            nm = None
                        if str(nm or "") == "consult_ariane":
                            continue
                        filtered.append(t)
                    tools = filtered
            except Exception:
                pass

            # Session-scoped prompt caching key (OpenAI prompt_cache_key): stable per (session_id, agent_id)
            cache_key = None
            try:
                cache_key = app.sessions_manager.get_or_create_prompt_cache_key(str(session_id), str(spec_rt.id))
            except Exception:
                cache_key = f"default_user:{spec_rt.id}"

            api_mode = get_api_mode_from_app(app)

            agent = create_agent(
                api_key=api_key,
                base_url=base_url,
                name=str(display_name),
                tools=tools,
                user_id=str(cache_key),
                config=agent_config,
                agent_id=str(spec_rt.id),
                api_mode=api_mode,
            )

            # History comes from the participant's own persistent store (session-scoped),
            # so they have a real standalone transcript like persistent sub-agents.
            ps = participant_store_info_by_owner.get(owner_id) if isinstance(participant_store_info_by_owner, dict) else None
            exclude_pid = None
            try:
                # Round0: only participant0 receives the user message as the turn message,
                # so only participant0 should exclude the mirrored user entry from history.
                if round_idx == 0 and p_idx == 0 and isinstance(ps, dict):
                    exclude_pid = ps.get("mirrored_user_entry_id")
                    exclude_pid = str(exclude_pid) if isinstance(exclude_pid, str) and exclude_pid else None
            except Exception:
                exclude_pid = None

            if isinstance(ps, dict) and ps.get("store") is not None:
                history_for_agent = _build_history_for_store(store=ps["store"], exclude_entry_id=exclude_pid)
            else:
                # Fallback: legacy main-session scan.
                exclude_id = user_entry_id if (round_idx == 0 and p_idx == 0) else None
                history_for_agent = _build_history_for_owner(owner_id=owner_id, exclude_entry_id=exclude_id)

            done_content = None

            # Phase 3: allow nested sub-agent streaming from group participants.
            # The core Agent injects _parent_stream_topic/_parent_run_id/_parent_session_id into run_subagent/consult_ariane
            # from these fields.
            try:
                setattr(agent, "_active_stream_topic", getattr(app, "_active_stream_topic", None))
                setattr(agent, "_active_run_id", str(run_id) if run_id else None)
                setattr(agent, "_active_session_id", str(session_id))
            except Exception:
                pass

            # Make Stop work like normal runs: register the current participant agent so stop_agent() can stop it.
            try:
                app._active_group_agent = agent
            except Exception:
                pass
            # Files/images are carried via wrapper meta on the mirrored user entry (and injected into history/payload).
            files_for_turn = []
            images_for_turn = []
            for ev in _iter_with_run_context(
                {
                    "agent_id": getattr(agent, "agent_id", None),
                    "agent_name": getattr(agent, "name", None),
                    "parent_session_id": str(session_id) if isinstance(session_id, str) else None,
                    "parent_run_id": str(run_id) if isinstance(run_id, str) else None,
                },
                agent.run(
                    message=input_payload,
                    input_messages=history_for_agent,
                    files=files_for_turn,
                    images=images_for_turn,
                    session_id=None,
                ),
            ):
                if not isinstance(ev, dict):
                    continue
                if ev.get("type") == "response.agent.done":
                    done_content = ev.get("content") if isinstance(ev.get("content"), dict) else {}
                    break
                # Forward all non-done events (streaming tool calls/outputs/text)
                yield ev

            # Clear active participant pointer.
            try:
                app._active_group_agent = None
            except Exception:
                pass

            if not isinstance(done_content, dict):
                continue

            # Merge wrap_meta_by_call_id (for diff badges, subhistory links, etc.)
            try:
                wm_call = done_content.get("wrap_meta_by_call_id")
                if isinstance(wm_call, dict):
                    for k, v in wm_call.items():
                        if isinstance(k, str) and isinstance(v, dict):
                            merged_wrap_meta_by_call_id[k] = v
            except Exception:
                pass

            # Persist this participant's items:
            # - Into the participant's own persistent store (includes the user input for the round).
            # - Into the shared main session (EXCLUDES the user input; main stays human+outputs only).
            session_items = done_content.get("session_items") if isinstance(done_content.get("session_items"), list) else []

            has_user0 = bool(session_items and isinstance(session_items[0], dict) and session_items[0].get("role") == "user")
            drop_main = 1 if has_user0 else 0

            # Round 0:
            # - participant0's user input is the human user's message, already mirrored into the participant store,
            #   so skip the agent-run-created user item to avoid duplication.
            # - later participants may receive a broadcast user message (we WANT it persisted into their store).
            # Round 1+: input is broadcast; we WANT it persisted.
            drop_store = 1 if (round_idx == 0 and p_idx == 0 and has_user0) else 0

            main_items = session_items[drop_main:]
            store_items = session_items[drop_store:]

            wm_raw = done_content.get("wrap_meta_by_item_index")
            wm_store = _shift_item_meta(wm_raw, drop_n=drop_store)
            wm_main = _shift_item_meta(wm_raw, drop_n=drop_main)
            wm_call = done_content.get("wrap_meta_by_call_id") if isinstance(done_content.get("wrap_meta_by_call_id"), dict) else None

            # Stamp group_round into wrapper meta for both participant store and main session.
            try:
                if isinstance(wm_store, dict):
                    for k in list(wm_store.keys()):
                        if not isinstance(wm_store.get(k), dict):
                            continue
                        wm_store[k]["group_round"] = int(round_idx)
                        wm_store[k]["group_session"] = True
                        # For participant stores, tag ownership as the participant (even for broadcast user entries).
                        wm_store[k].setdefault("owner_id", str(owner_id))
            except Exception:
                pass

            try:
                if isinstance(wm_main, dict):
                    for k in list(wm_main.keys()):
                        if not isinstance(wm_main.get(k), dict):
                            continue
                        wm_main[k]["group_round"] = int(round_idx)
                        wm_main[k]["group_session"] = True
            except Exception:
                pass

            # Determine reply vs pass (PASS is a protocol keyword; we still persist it for reload consistency)
            last_text = _extract_last_assistant_text(main_items)
            lt = str(last_text).strip() if isinstance(last_text, str) else ""

            # Mirror into participant store
            mirror_ids: List[str] = []
            broadcast_store_entry_id: Optional[str] = None
            ps = participant_store_info_by_owner.get(owner_id) if isinstance(participant_store_info_by_owner, dict) else None
            try:
                if store_items and isinstance(ps, dict) and ps.get("store") is not None:
                    mirror_ids = ps["store"].append_entries(
                        entries=store_items,
                        wrap_meta_by_call_id=wm_call,
                        wrap_meta_by_item_index=wm_store if isinstance(wm_store, dict) else None,
                        run_id=str(run_id) if run_id else None,
                    )
                    if drop_store == 0 and has_user0 and mirror_ids:
                        broadcast_store_entry_id = str(mirror_ids[0])
            except Exception:
                mirror_ids = []
                broadcast_store_entry_id = None

            # Persist into main session (exclude the round user input), stamped with owner_id + mirror pointers.
            stamped_item_meta: Dict[int, Dict[str, Any]] = {}
            store_offset = int(drop_main - drop_store)

            for i in range(len(main_items)):
                base = wm_main.get(i, {}) if isinstance(wm_main.get(i), dict) else {}
                m = dict(base)
                m["owner_id"] = owner_id
                m["group_round"] = int(round_idx)
                m["group_session"] = True

                # Wrapper-only pointer so delete/edit-tail can trim participant stores correctly.
                try:
                    store_i = i + store_offset
                    if isinstance(ps, dict) and mirror_ids and 0 <= store_i < len(mirror_ids):
                        sid2 = ps.get("store_id")
                        if isinstance(sid2, str) and sid2:
                            gm = {
                                "store_id": sid2,
                                "entry_id": str(mirror_ids[store_i]),
                                "agent_id": str(agent_id),
                                "owner_id": str(owner_id),
                            }
                            # Also trim the broadcast user message when this tail is deleted.
                            if i == 0 and isinstance(broadcast_store_entry_id, str) and broadcast_store_entry_id:
                                gm["extra_entry_ids"] = [str(broadcast_store_entry_id)]
                            m["group_participant_mirror"] = gm
                except Exception:
                    pass

                stamped_item_meta[i] = m


            ids2 = []
            try:
                if main_items:
                    ids2 = app.sessions_manager.append_entries(
                        session_id=session_id,
                        entries=main_items,
                        wrap_meta_by_call_id=wm_call,
                        wrap_meta_by_item_index=stamped_item_meta,
                        run_id=str(run_id) if run_id else None,
                    )
                    saved_entry_ids.extend(ids2)
            except Exception:
                pass


            # Per-participant run receipt (shows what this participant changed in this turn).
            # Uses the same RunReceiptBlock UI as normal sessions.
            try:
                if ids2 and getattr(app, "fs_revision_store", None) is not None:
                    txn_ids = app.sessions_manager.transactions_manager.get_txn_ids_for_entry_ids(
                        session_id=str(session_id),
                        entry_ids=[str(x) for x in ids2 if isinstance(x, str) and x],
                    )
                    txn_ids = [str(t) for t in (txn_ids or []) if isinstance(t, str) and t]

                    if txn_ids:
                        from ..storage.fs_diff import compute_run_diff_index

                        idx = compute_run_diff_index(app.fs_revision_store, txn_ids)
                        if isinstance(idx, dict) and idx.get("status") == "success":
                            files_all = idx.get("files") if isinstance(idx.get("files"), list) else []

                            # Ephemeral entries (created+deleted in the same turn) are hidden by default.
                            non_ephemeral = []
                            try:
                                non_ephemeral = [
                                    f for f in files_all
                                    if not (isinstance(f, dict) and bool(f.get("ephemeral")))
                                ]
                            except Exception:
                                non_ephemeral = list(files_all)

                            eph_count = max(0, len(files_all) - len(non_ephemeral))

                            # Only show a receipt if there was any meaningful change.
                            if non_ephemeral or eph_count:
                                # Unique run_id for the per-participant receipt (used by the diff viewer).
                                turn_run_id = f"grpturn:{run_id}:{owner_id}:{round_idx}:{uuid.uuid4().hex[:8]}"

                                run_summary_item = {
                                    "type": "run_summary",
                                    "run_id": str(turn_run_id),
                                    "run_status": "success",
                                    "started_at": None,
                                    "finished_at": _now_utc_iso(),
                                    "duration_ms": 0,
                                    "entries_count": int(len(ids2)),
                                    "turns_count": None,
                                    "token_usage_totals": {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0},
                                    "token_usage_totals_main": {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0},
                                    "token_usage_totals_subagents": {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0},
                                    "token_usage_totals_total": {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0},
                                    "subagent_usage_breakdown": {},
                                    "token_usage_last_turn": None,
                                    "transaction_count": int(len(txn_ids)),
                                    "files_changed": list(files_all),
                                    "files_changed_preview": list(non_ephemeral[:3]),
                                    "files_changed_count": int(len(non_ephemeral)),
                                    "files_ephemeral_count": int(eph_count),
                                    "diff_totals": idx.get("diff_totals") if isinstance(idx.get("diff_totals"), dict) else None,
                                    "description": f"{str(display_name)} — turn receipt",
                                }

                                # Persist it so reload shows the same receipt.
                                try:
                                    app.sessions_manager.append_entries(
                                        session_id=session_id,
                                        entries=[run_summary_item],
                                        wrap_meta_by_item_index={
                                            0: {
                                                "owner_id": str(owner_id),
                                                "group_round": int(round_idx),
                                                "group_session": True,
                                            }
                                        },
                                        run_id=str(run_id) if run_id else None,
                                    )
                                except Exception:
                                    pass

                                # Stream it live (no reload required).
                                yield {
                                    "type": "group.participant.ended",
                                    "agent_name": str(display_name),
                                    "content": {
                                        "owner_id": str(owner_id),
                                        "round": int(round_idx),
                                        "run_summary_item": run_summary_item,
                                    },
                                }
            except Exception:
                pass
            # Stop mechanism: explicit group_pass tool call.
            did_pass = False
            try:
                for it in main_items:
                    if isinstance(it, dict) and it.get("type") == "function_call" and it.get("name") == "group_pass":
                        did_pass = True
                        break
            except Exception:
                did_pass = False

            passes_this_round[owner_id] = bool(did_pass)

            # Broadcast semantics:
            # - If ask_human(visibility='public') was called, broadcast the Q/A event(s).
            # - If group_pass() was called, treat the turn as PASS and broadcast the pass reason (not the assistant reply).
            # - Otherwise, broadcast the assistant reply text (if any).
            try:
                for he in _extract_ask_human_public_events(main_items):
                    if isinstance(he, dict):
                        he2 = dict(he)
                        he2["owner_id"] = str(owner_id)
                        broadcast_events.append(he2)
            except Exception:
                pass

            if did_pass:
                pr = _extract_group_pass_reason(main_items)
                pr = pr.strip() if isinstance(pr, str) else ""
                if not pr:
                    # Fallback: if they wrote a reply anyway, use it as the "reason" so others aren't blind.
                    pr = lt or "pass"
                try:
                    broadcast_events.append({"owner_id": str(owner_id), "kind": "pass", "reason": str(pr)})
                except Exception:
                    pass
            else:
                # Only count as a reply if participant did not pass and produced assistant text.
                if lt:
                    replies_this_round[owner_id] = lt
                    try:
                        broadcast_events.append({"owner_id": str(owner_id), "kind": "reply", "text": str(lt)})
                    except Exception:
                        pass

            # Mark that this participant has now "seen" everything up to the current broadcast tail.
            # (Exclude-self is applied when composing; this cursor prevents rebroadcast loops.)
            try:
                delivered_cursor_by_owner[str(owner_id)] = int(len(broadcast_events))
            except Exception:
                pass

            # Token totals for this participant + per-participant breakdown (Main/Subs/Total).
            try:
                th = getattr(agent, "token_usage_history", None)
                th = th if isinstance(th, dict) else {}

                p_main = {k: 0 for k in list(totals_main.keys())}
                for v in th.values():
                    if not isinstance(v, dict):
                        continue
                    for k in list(totals_main.keys()):
                        try:
                            dv = int(v.get(k, 0) or 0)
                        except Exception:
                            dv = 0
                        totals_main[k] += dv
                        p_main[k] += dv

                # Sub-agent totals for this participant turn (derived from wrap_meta_by_call_id).
                p_sub = {k: 0 for k in list(totals_subagents.keys())}
                try:
                    if isinstance(wm_call, dict):
                        for cid, meta2 in wm_call.items():
                            if not isinstance(cid, str) or not cid:
                                continue
                            if not isinstance(meta2, dict):
                                continue
                            su = meta2.get("subagent_usage")
                            if not isinstance(su, dict):
                                continue
                            tu2 = su.get("token_usage_totals")
                            if not isinstance(tu2, dict):
                                continue
                            for k in list(p_sub.keys()):
                                try:
                                    p_sub[k] += int(tu2.get(k, 0) or 0)
                                except Exception:
                                    pass
                except Exception:
                    p_sub = {k: 0 for k in list(totals_subagents.keys())}

                for k in list(totals_subagents.keys()):
                    totals_subagents[k] += int(p_sub.get(k, 0) or 0)

                # Last turn usage for this participant (main)
                p_last_main = None
                try:
                    if th:
                        def _to_int(x):
                            try:
                                return int(x)
                            except Exception:
                                return -1
                        last_k = max(th.keys(), key=_to_int)
                        lt2 = th.get(last_k)
                        p_last_main = lt2 if isinstance(lt2, dict) else None
                except Exception:
                    p_last_main = None

                # Persist breakdown keyed by agent_id.
                key_aid = str(agent_id)
                rec = participant_usage.get(key_aid) if isinstance(participant_usage.get(key_aid), dict) else None
                if rec is None:
                    rec = {
                        "agent_id": key_aid,
                        "owner_id": str(owner_id),
                        "display_name": str(display_name),
                        "token_usage_totals_main": {k: 0 for k in list(totals_main.keys())},
                        "token_usage_totals_subagents": {k: 0 for k in list(totals_subagents.keys())},
                        "token_usage_totals_total": {k: 0 for k in list(totals_main.keys())},
                        # Back-compat (Phase 2): token_usage_totals == TOTAL
                        "token_usage_totals": {k: 0 for k in list(totals_main.keys())},
                        "token_usage_last_turn_main": None,
                        # Back-compat (Phase 2): token_usage_last_turn == main last turn
                        "token_usage_last_turn": None,
                    }

                tu_m = rec.get("token_usage_totals_main") if isinstance(rec.get("token_usage_totals_main"), dict) else {k: 0 for k in list(totals_main.keys())}
                tu_s = rec.get("token_usage_totals_subagents") if isinstance(rec.get("token_usage_totals_subagents"), dict) else {k: 0 for k in list(totals_subagents.keys())}
                for k in list(totals_main.keys()):
                    tu_m[k] = int(tu_m.get(k, 0) or 0) + int(p_main.get(k, 0) or 0)
                for k in list(totals_subagents.keys()):
                    tu_s[k] = int(tu_s.get(k, 0) or 0) + int(p_sub.get(k, 0) or 0)

                tu_t = {k: int(tu_m.get(k, 0) or 0) + int(tu_s.get(k, 0) or 0) for k in list(totals_main.keys())}

                rec["token_usage_totals_main"] = tu_m
                rec["token_usage_totals_subagents"] = tu_s
                rec["token_usage_totals_total"] = tu_t
                rec["token_usage_totals"] = tu_t

                if isinstance(p_last_main, dict):
                    rec["token_usage_last_turn_main"] = p_last_main
                    rec["token_usage_last_turn"] = p_last_main

                participant_usage[key_aid] = rec

            except Exception:
                pass

        # Stop conditions (app-owned):
        # - If everyone passed, stop.
        # - If everyone except participant0 passed, stop.
        # - If nobody produced an assistant reply (tools-only / silence), stop.
        #
        # Note: we still broadcast PASS reasons during the round so later participants can see them,
        # but we avoid starting a new round just to propagate PASS notes back to earlier participants.
        try:
            if owner_ids_this_round:
                all_passed = all(bool(passes_this_round.get(oid, False)) for oid in owner_ids_this_round)
                if all_passed:
                    break

                if len(owner_ids_this_round) > 1:
                    owner0 = owner_ids_this_round[0]
                    others = owner_ids_this_round[1:]
                    if (not bool(passes_this_round.get(owner0, False))) and all(bool(passes_this_round.get(oid, False)) for oid in others):
                        break

            if not replies_this_round:
                break
        except Exception:
            # Fail closed: if unsure, stop rather than loop-spam.
            try:
                if int(len(broadcast_events)) == int(broadcast_len_before_round):
                    break
            except Exception:
                break

        # If Stop was requested, stop after persisting progress.
        if app._stop_requested:
            break

        # Prepare for next round.
        round_idx += 1

    # Final run summary (group run): compute consolidated diffs across ALL participants.
    finished_at_utc = datetime.now(timezone.utc).replace(microsecond=0)
    duration_ms = int((finished_at_utc - started_at_utc).total_seconds() * 1000)

    run_summary_item: Dict[str, Any] = {
        "type": "run_summary",
        "run_id": str(run_id) if run_id else None,
        "run_status": ("stopped" if bool(app._stop_requested) else "success"),
        "started_at": started_at_iso,
        "finished_at": finished_at_utc.isoformat().replace("+00:00", "Z"),
        "duration_ms": int(duration_ms),
        "entries_count": len(saved_entry_ids),
        "turns_count": None,
        "token_usage_totals": {k: int(totals_main.get(k, 0) or 0) + int(totals_subagents.get(k, 0) or 0) for k in list(totals_main.keys())},
        "token_usage_totals_main": totals_main,
        "token_usage_totals_subagents": totals_subagents,
        "token_usage_totals_total": {k: int(totals_main.get(k, 0) or 0) + int(totals_subagents.get(k, 0) or 0) for k in list(totals_main.keys())},
        "subagent_usage_breakdown": {},
        "token_usage_last_turn": None,
        "transaction_count": 0,
        "files_changed": [],
        "files_changed_preview": [],
        "files_changed_count": 0,
        "files_ephemeral_count": 0,
        "diff_totals": None,
        "description": "",
    }

    try:
        # Reuse the single-run summary builder so diff computation matches normal sessions.
        from ..app_services.run_summary import build_run_summary_item

        # build_run_summary_item expects a token_usage_history; in group runs we already have
        # totals, so we provide a 1-turn synthetic history.
        token_hist = {0: dict(totals_main)}

        done_msg = "Group run stopped." if bool(app._stop_requested) else "Group run completed."

        rs2, _ = build_run_summary_item(
            session_id=str(session_id),
            run_id=(str(run_id) if isinstance(run_id, str) and run_id else None),
            started_at_utc=started_at_utc,
            finished_at_utc=finished_at_utc,
            stopped=bool(app._stop_requested),
            done_message=str(done_msg),
            saved_entry_ids=list(saved_entry_ids or []),
            token_usage_history=token_hist,
            sessions_manager=app.sessions_manager,
            fs_revision_store=getattr(app, "fs_revision_store", None),
        )

        if isinstance(rs2, dict) and rs2.get("type") == "run_summary":
            # Group runs are multi-participant; turns_count is not meaningful as a single number.
            rs2["turns_count"] = None

            # Per-participant breakdown (Main/Subs/Total + last-turn main) for UI.
            rs2["group_participant_usage"] = participant_usage

            run_summary_item = rs2
    except Exception:
        # Belt-and-suspenders: if diff building fails, still report transaction_count.
        try:
            ordered_txns = []
            if run_id:
                ordered_txns = app.sessions_manager.transactions_manager.get_txn_ids_for_run(
                    session_id=str(session_id),
                    run_id=str(run_id),
                )
            ordered_txns = [t for t in (ordered_txns or []) if isinstance(t, str) and t]
            run_summary_item["transaction_count"] = int(len(ordered_txns))
        except Exception:
            pass

    try:
        app.sessions_manager.append_entries(
            session_id=session_id,
            entries=[run_summary_item],
            run_id=str(run_id) if run_id else None,
        )
    except Exception:
        pass

    # Update cached token stats (best-effort)
    try:
        app._update_session_token_stats_meta(session_id=session_id)
    except Exception:
        pass

    yield {
        "type": "response.agent.done",
        "agent_name": "System",
        "content": {
            "message": ("Group run stopped." if bool(app._stop_requested) else "Group run completed."),
            "duration_seconds": float(duration_ms) / 1000.0,
            "session_items": [],
            "generated_images": [],
            "stopped": bool(app._stop_requested),
            "wrap_meta_by_call_id": merged_wrap_meta_by_call_id,
            "wrap_meta_by_item_index": {},
            "run_id": run_id,
            "saved_entry_ids": saved_entry_ids,
            "user_entry_id": user_entry_id,
            "run_summary_item": run_summary_item,
        },
    }

    yield {"type": "stream.finished", "agent_name": "System", "content": {}}
