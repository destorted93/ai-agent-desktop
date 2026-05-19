"""Session storage for a single session.

Stores *wrapped entries* (encrypted) and provides:
- UI-facing wrapped entry list
- API-facing message list (OpenAI-ish items)

Note: this is a modular-monolith project; keep this module focused on session
wrapping/unwrapping + history shaping.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .secure import read_encrypted_json, write_encrypted_json


class SessionManager:
    def __init__(self, file_path=None, store_id: Optional[str] = None):
        from pathlib import Path
        from .secure import get_app_data_dir

        if file_path is None:
            base = get_app_data_dir() / "sessions"
            base.mkdir(parents=True, exist_ok=True)
            if store_id is None:
                store_id = "session_default"
            file_path = base / f"{store_id}.enc"

        self.file_path = Path(file_path)
        self.store_id = store_id
        self.entries: List[Dict[str, Any]] = []

        # Generated images cache
        self._images_path = self.file_path.with_suffix(".images.json")
        self.generated_images: List[Dict[str, Any]] = []

        self.load()

    # =====================================================================
    # Internal helpers
    # =====================================================================

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _truncate(self, s: str, n: int = 300) -> str:
        if not isinstance(s, str):
            return ""
        s = s.strip()
        return s if len(s) <= n else s[: n - 3] + "..."

    def _parse_tool_output(self, output_value: Any) -> Tuple[Any, Optional[str]]:
        """Parse a tool output field.

        Returns (parsed_obj, parse_error_message).
        """
        if output_value is None:
            return None, None
        if isinstance(output_value, (dict, list)):
            return output_value, None
        if isinstance(output_value, str):
            s = output_value.strip()
            if not s:
                return None, None
            try:
                return json.loads(s), None
            except Exception as e:
                return None, f"Failed to parse tool output JSON: {e}"
        return None, f"Unexpected tool output type: {type(output_value).__name__}"

    def _collect_transaction_ids(self, obj: Any, out: List[str]) -> None:
        """Recursively collect filesystem transaction ids from a parsed tool result."""
        if isinstance(obj, dict):
            for k in ("transaction_id", "undo_transaction_id"):
                v = obj.get(k)
                if isinstance(v, str) and v:
                    out.append(v)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and item:
                            out.append(item)
            for v in obj.values():
                self._collect_transaction_ids(v, out)
            return

        if isinstance(obj, list):
            for item in obj:
                self._collect_transaction_ids(item, out)
            return

    def _derive_result_status_and_message(
        self,
        entry_kind: str,
        content: Dict[str, Any],
    ) -> Tuple[str, Optional[str], List[str]]:
        """Derive semantic result status/message for tool outputs.

        Only `function_call_output` is inspected.
        Returns (result_status, result_message, transaction_ids).
        """
        if entry_kind != "function_call_output":
            return "unknown", None, []

        parsed, parse_err = self._parse_tool_output(content.get("output"))
        if parse_err:
            return "unknown", self._truncate(parse_err), []

        result_status = "unknown"
        result_message: Optional[str] = None

        if isinstance(parsed, dict):
            st = parsed.get("status")
            if isinstance(st, str) and st in ("success", "error"):
                result_status = st
            if result_status == "error":
                msg = parsed.get("message") or parsed.get("error")
                if isinstance(msg, str) and msg.strip():
                    result_message = self._truncate(msg.strip())

            txns: List[str] = []
            self._collect_transaction_ids(parsed, txns)
            seen = set()
            txns = [t for t in txns if not (t in seen or seen.add(t))]
            return result_status, result_message, txns

        if isinstance(parsed, list):
            statuses: List[str] = []
            first_err_msg: Optional[str] = None
            txns: List[str] = []

            for item in parsed:
                if isinstance(item, dict):
                    st = item.get("status")
                    if isinstance(st, str):
                        statuses.append(st)
                        if st == "error" and first_err_msg is None:
                            msg = item.get("message") or item.get("error")
                            if isinstance(msg, str) and msg.strip():
                                first_err_msg = msg.strip()
                    self._collect_transaction_ids(item, txns)

            if statuses:
                if any(s == "error" for s in statuses):
                    result_status = "error"
                elif all(s == "success" for s in statuses):
                    result_status = "success"

            if result_status == "error" and first_err_msg:
                result_message = self._truncate(first_err_msg)

            seen = set()
            txns = [t for t in txns if not (t in seen or seen.add(t))]
            return result_status, result_message, txns

        return "unknown", None, []

    def _wrap_entry(
        self,
        entry: Dict[str, Any],
        *,
        wrap_meta_by_call_id: Optional[Dict[str, Dict[str, Any]]] = None,
        run_id: Optional[str] = None,
        item_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Wrap a raw item (OpenAI-ish) with metadata used by the UI."""
        content = entry if isinstance(entry, dict) else {"type": "unknown", "raw": str(entry)}
        entry_kind = str(content.get("type") or "unknown")

        wrapped: Dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "ts": self._now_iso(),
            "kind": entry_kind,
            "exec_status": "unknown",
            "result_status": "unknown",
            "result_message": None,
            "run_id": str(run_id) if isinstance(run_id, str) and run_id else None,
            "content": content,
        }

        # Per-item wrapper meta (e.g. injected message flags)
        if isinstance(item_meta, dict) and item_meta:
            try:
                for k, v in item_meta.items():
                    if k in ("id", "ts", "kind", "exec_status", "result_status", "result_message", "run_id", "content"):
                        continue
                    wrapped[k] = v
            except Exception:
                pass

        # Tool output: derive semantic status + message + transaction ids
        try:
            rs, rm, _ = self._derive_result_status_and_message(entry_kind, content)
            wrapped["result_status"] = rs
            wrapped["result_message"] = rm
        except Exception:
            pass

        # Merge call_id wrapper meta (side-channel) if present.
        # IMPORTANT: apply to BOTH function_call and function_call_output so call/output can be treated consistently.
        try:
            if isinstance(content, dict) and entry_kind in ("function_call", "function_call_output"):
                call_id = content.get("call_id")
                if isinstance(call_id, str) and call_id and isinstance(wrap_meta_by_call_id, dict):
                    meta = wrap_meta_by_call_id.get(call_id)
                    if isinstance(meta, dict) and meta:
                        for k, v in meta.items():
                            # Never overwrite core wrapper keys
                            if k in wrapped:
                                continue
                            wrapped[k] = v
        except Exception:
            pass

        return wrapped

    def _unwrap_entries(self, wrapped_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for we in wrapped_entries or []:
            if isinstance(we, dict) and isinstance(we.get("content"), dict):
                out.append(we["content"])
        return out

    def extract_transaction_ids(
        self,
        entry: Dict[str, Any],
        *,
        wrap_meta_by_call_id: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[str]:
        """Extract filesystem transaction ids from a raw OpenAI-ish item.

        This is used by SessionsManager to link fs revision transaction ids into the
        per-session TransactionsManager ledger.

        Current behavior:
        - Only function_call_output entries can contain transaction ids.
        - Transaction ids are discovered by parsing the tool output JSON and recursively
          collecting fields like transaction_id / undo_transaction_id.
        - Additionally, some tools (notably run_subagent/consult_ariane) return transaction ids
          via wrapper-only meta (wrap_meta_by_call_id[call_id]["transaction_ids"]).
        """
        try:
            if not isinstance(entry, dict):
                return []
            if str(entry.get("type") or "") != "function_call_output":
                return []

            parsed, _err = self._parse_tool_output(entry.get("output"))
            txns: List[str] = []
            self._collect_transaction_ids(parsed, txns)

            # Wrapper-only meta may also contain txn ids (e.g., run_subagent).
            try:
                if isinstance(wrap_meta_by_call_id, dict):
                    cid = entry.get("call_id")
                    cid = str(cid).strip() if isinstance(cid, str) else ""
                    if cid:
                        wm = wrap_meta_by_call_id.get(cid)
                        if isinstance(wm, dict):
                            v = wm.get("transaction_ids")
                            if isinstance(v, str) and v:
                                txns.append(v)
                            elif isinstance(v, list):
                                for t in v:
                                    if isinstance(t, str) and t:
                                        txns.append(t)

                            dp = wm.get("diff_preview")
                            if isinstance(dp, dict):
                                tid = dp.get("transaction_id")
                                if isinstance(tid, str) and tid:
                                    txns.append(tid)
            except Exception:
                pass

            # Dedupe in order
            seen = set()
            out = [t for t in txns if isinstance(t, str) and t and not (t in seen or seen.add(t))]
            return out
        except Exception:
            return []

    # =====================================================================
    # Public API
    # =====================================================================

    def load(self) -> None:
        data = read_encrypted_json(self.file_path)
        self.entries = data if isinstance(data, list) else []

        if self._images_path.exists():
            try:
                self.generated_images = json.loads(self._images_path.read_text("utf-8"))
            except Exception:
                self.generated_images = []

    def save(self) -> None:
        write_encrypted_json(self.file_path, self.entries)

    def get_messages(self, limit: int = 50, offset: int = 0, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        items = self._unwrap_entries(self.entries)
        if offset < 0:
            offset = 0
        if limit is None or limit <= 0:
            return items[offset:]
        return items[offset : offset + limit]

    def get_entries_wrapped(self, limit: int = 50, offset: int = 0, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if offset < 0:
            offset = 0
        if limit is None or limit <= 0:
            return self.entries[offset:]
        return self.entries[offset : offset + limit]

    def get_entry(self, entry_id: str) -> Optional[Dict[str, Any]]:
        """Return a single wrapped entry by id (or None)."""
        target = str(entry_id or "").strip()
        if not target:
            return None
        for e in (self.entries or []):
            if isinstance(e, dict) and e.get("id") == target:
                return e
        return None

    def build_history_for_agent(self) -> List[Dict[str, Any]]:
        """Build history items for the agent.

        Phase 1: compress summarized runs.

        A run is considered summarized if its app-generated run_summary entry has a non-empty `description`.
        For summarized runs, keep only:
        - the first user message in the run
        - the last assistant message in the run
        - the run_summary tool call + its tool output

        Always exclude: reasoning, run_summary (app-generated), system_notice.
        """

        wrapped = [e for e in (self.entries or []) if isinstance(e, dict) and isinstance(e.get("content"), dict)]

        # Identify summarized run_ids (run_summary content is app-generated).
        summarized_run_ids = set()
        for we in wrapped:
            c = we.get("content") or {}
            if not isinstance(c, dict):
                continue
            if str(c.get("type") or "") == "run_summary":
                desc = c.get("description")
                if isinstance(desc, str) and desc.strip():
                    rid = we.get("run_id")
                    if isinstance(rid, str) and rid:
                        summarized_run_ids.add(rid)

        # For summarized runs, pick the minimal subset of indices to keep.
        keep_indices = set()
        if summarized_run_ids:
            # Pre-index per run
            by_run: Dict[str, List[tuple]] = {}
            for idx, we in enumerate(wrapped):
                rid = we.get("run_id")
                if isinstance(rid, str) and rid in summarized_run_ids:
                    by_run.setdefault(rid, []).append((idx, we))

            for rid, items in by_run.items():
                # first user msg
                first_user = None
                last_assistant = None
                run_summary_call_ids: set = set()

                for idx, we in items:
                    kind = str(we.get("kind") or "")
                    c = we.get("content") if isinstance(we.get("content"), dict) else {}

                    if kind == "message" and isinstance(c, dict):
                        role = str(c.get("role") or "").lower()
                        if role == "user" and first_user is None:
                            # Avoid keeping tool-injected user messages.
                            if not bool(we.get("injected")):
                                first_user = idx
                        if role == "assistant":
                            last_assistant = idx

                    if kind == "function_call" and isinstance(c, dict):
                        if str(c.get("name") or "") == "run_summary":
                            cid = c.get("call_id")
                            if isinstance(cid, str) and cid:
                                run_summary_call_ids.add(cid)
                                keep_indices.add(idx)

                # keep corresponding outputs
                if run_summary_call_ids:
                    for idx, we in items:
                        if str(we.get("kind") or "") == "function_call_output":
                            c = we.get("content") if isinstance(we.get("content"), dict) else {}
                            cid = c.get("call_id") if isinstance(c, dict) else None
                            if isinstance(cid, str) and cid in run_summary_call_ids:
                                keep_indices.add(idx)

                if first_user is not None:
                    keep_indices.add(first_user)
                if last_assistant is not None:
                    keep_indices.add(last_assistant)

        out: List[Dict[str, Any]] = []

        for idx, we in enumerate(wrapped):
            c = we.get("content")
            if not isinstance(c, dict):
                continue

            # Always exclude these kinds/types from agent context.
            t = c.get("type")
            if isinstance(t, str) and t in ("reasoning", "run_summary", "system_notice"):
                continue

            rid = we.get("run_id")
            if isinstance(rid, str) and rid in summarized_run_ids:
                if idx not in keep_indices:
                    continue

            out.append(c)

        return out

    def add_entry(
        self,
        entry: Dict[str, Any],
        wrap_meta_by_call_id: Optional[Dict[str, Dict[str, Any]]] = None,
        run_id: Optional[str] = None,
        item_meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        wrapped = self._wrap_entry(entry, wrap_meta_by_call_id=wrap_meta_by_call_id, run_id=run_id, item_meta=item_meta)
        self.entries.append(wrapped)
        self.save()
        return wrapped["id"]

    def append_entries(
        self,
        entries: List[Dict[str, Any]],
        wrap_meta_by_call_id: Optional[Dict[str, Dict[str, Any]]] = None,
        wrap_meta_by_item_index: Optional[Dict[Any, Dict[str, Any]]] = None,
        run_id: Optional[str] = None,
    ) -> List[str]:

        def _item_meta(i: int) -> Optional[Dict[str, Any]]:
            if not isinstance(wrap_meta_by_item_index, dict):
                return None
            m = wrap_meta_by_item_index.get(i)
            if m is None:
                m = wrap_meta_by_item_index.get(str(i))
            return m if isinstance(m, dict) else None

        wrapped = [
            self._wrap_entry(e, wrap_meta_by_call_id=wrap_meta_by_call_id, run_id=run_id, item_meta=_item_meta(i))
            for i, e in enumerate(entries)
        ]
        self.entries.extend(wrapped)
        self.save()
        return [e["id"] for e in wrapped]

    def clear(self, session_id: Optional[str] = None) -> bool:
        self.entries = []
        self.save()
        return True

    def delete_entries(self, entry_ids: List[str]) -> Dict[str, Any]:
        if not isinstance(entry_ids, list):
            entry_ids = [entry_ids]

        original_count = len(self.entries)
        ids = set()
        for x in entry_ids or []:
            if isinstance(x, str) and x:
                ids.add(x)
        self.entries = [e for e in self.entries if e.get("id") not in ids]
        deleted_count = original_count - len(self.entries)

        if deleted_count > 0:
            self.save()

        return {
            "status": "success",
            "deleted_count": deleted_count,
            "remaining_count": len(self.entries),
        }

    def delete_entries_from_id(self, entry_id: str) -> Dict[str, Any]:
        """Delete entry_id and all subsequent entries (tail) within this single store."""
        target = str(entry_id or "").strip()
        if not target:
            return {"status": "error", "message": "Entry id is required", "deleted_count": 0}

        idx = None
        for i, e in enumerate(self.entries):
            if isinstance(e, dict) and e.get("id") == target:
                idx = i
                break

        if idx is None:
            return {"status": "error", "message": "Entry not found", "deleted_count": 0}

        original_count = len(self.entries)
        self.entries = self.entries[:idx]
        deleted_count = original_count - len(self.entries)

        if deleted_count > 0:
            self.save()

        return {"status": "success", "deleted_count": deleted_count, "remaining_count": len(self.entries)}
