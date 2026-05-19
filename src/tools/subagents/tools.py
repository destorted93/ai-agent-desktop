"""Generic sub-agent runner tool.

This tool is the unified entry point for running:
- persistent sub-agents (their own long-lived private session store)
- one-shot/run sub-agents (no continuity; run log still persists for UI rehydrate)

Implementation is intentionally thin: it delegates orchestration to app.py via the
in-process EventBus (agent.cmd.run_subagent).
"""

from __future__ import annotations

from typing import Any, Dict, Optional
import threading
import uuid

from ...appcore.runtime_context import Runtime


class RunSubagentTool:
    schema = {
        "type": "function",
        "name": "run_subagent",
        "description": (
            "Run a sub-agent as a child run and stream its actions live into the main chat window as a subtree under this tool call. "
            "Use this when a subtask can be delegated cleanly to preserve the parent context window, reduce token burn, or isolate specialized work.\n\n"
            "You MUST provide the sub-agent name and a message/context.\n\n"
            "Important: For Ariane specifically, prefer calling `consult_ariane` (the family door) instead of `run_subagent`. Use `run_subagent` for other sub-agents.\n\n"
            "Modes:\n"
            "- persistent: the sub-agent has its own isolated session for the current parent session, so it can be called again later with continuity.\n"
            "- run: one-shot sub-agent (no continuity). The run is still persisted so the UI can rehydrate after restart.\n\n"
            "One-shot system prompt:\n"
            "- If mode=run, you must also provide `instructions` (the system prompt to use for that one-shot agent). A good minimal structure is ROLE, RULES, and STRUCTURED OUTPUT.\n\n"
            "Privacy model:\n"
            "- Only the final sub-agent message is returned to the main agent.\n"
            "- The sub-agent trace (tool calls, outputs, diffs) is stored as wrapper/UI metadata and rendered in the UI, but is not inserted into the parent model context.\n"
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "subagent_name": {
                    "type": "string",
                    "description": "Display name of the sub-agent to run (used for routing + UI).",
                },
                "mode": {
                    "type": "string",
                    "enum": ["persistent", "run"],
                    "description": "persistent = continuity via a long-lived session store; run = one-shot (no continuity).",
                },
                "message": {
                    "type": "string",
                    "description": "The message you want to send to the sub-agent.",
                },
                "context": {
                    "type": "string",
                    "description": "Extra context for the sub-agent (e.g., 3-line context packet).",
                },
                "instructions": {
                    "type": "string",
                    "description": "Required if mode=run. One-shot system prompt/instructions for the sub-agent.",
                },
            },
            # NOTE: instructions is required at the schema level (OpenAI tool strictness).
            # For mode='persistent', instructions may be an empty string and will be ignored.
            "required": ["subagent_name", "mode", "message", "context", "instructions"],
            "additionalProperties": False,
        },
    }

    def run(
        self,
        subagent_name: str,
        mode: str,
        message: str,
        context: str,
        instructions: Optional[str] = None,
        # Internal-only injected args (never provided by the model)
        _call_id: Optional[str] = None,
        _parent_stream_topic: Optional[str] = None,
        _parent_run_id: Optional[str] = None,
        _parent_session_id: Optional[str] = None,
        _allow_ariane: bool = False,
    ) -> Dict[str, Any]:
        # Basic validation
        sa = (subagent_name or "").strip()
        md = (mode or "").strip().lower()
        if not sa:
            return {"status": "error", "message": "subagent_name is required"}
        if md not in ("persistent", "run"):
            return {"status": "error", "message": "mode must be 'persistent' or 'run'"}
        if md == "run":
            if not isinstance(instructions, str) or not instructions.strip():
                return {"status": "error", "message": "instructions is required when mode='run'"}


        # Ariane is special: use the family door.
        if sa.lower() == "ariane" and not bool(_allow_ariane):
            return {
                "status": "error",
                "message": "To talk to Ariane, use consult_ariane (the family door) instead of run_subagent.",
            }
        # Must be called from within an app run to stream to UI.
        if not (isinstance(_parent_stream_topic, str) and _parent_stream_topic and isinstance(_call_id, str) and _call_id):
            return {"status": "error", "message": "run_subagent must be called from within an active run"}

        # Compose what the sub-agent sees.
        composed = str(message or "")
        if isinstance(context, str) and context.strip():
            composed = f"{composed}\n\nContext:\n{context.strip()}"

        bus = Runtime.get_event_bus()
        reply_topic = f"agent.ui.reply.run_subagent.{uuid.uuid4()}"
        done = threading.Event()
        result: Dict[str, Any] = {}
        unsub = None

        def _on_reply(ev):
            nonlocal result, unsub
            try:
                if unsub:
                    unsub()
            except Exception:
                pass
            unsub = None
            payload = getattr(ev, "payload", {}) or {}
            result = payload if isinstance(payload, dict) else {"status": "error", "message": "Unexpected reply"}
            done.set()

        unsub = bus.subscribe(reply_topic, _on_reply)

        bus.publish(
            "agent.cmd.run_subagent",
            {
                "reply_topic": reply_topic,
                "parent_stream_topic": str(_parent_stream_topic),
                "parent_call_id": str(_call_id),
                "parent_run_id": str(_parent_run_id) if _parent_run_id else None,
                "parent_session_id": str(_parent_session_id) if _parent_session_id else None,
                "subagent_name": str(sa),
                "mode": str(md),
                "message": str(composed),
                "instructions": (str(instructions) if isinstance(instructions, str) else None),
            },
        )

        # Allow long-running sub-agent work (tool-heavy scans/refactors). Stop still propagates.
        if not done.wait(timeout=1800.0):
            try:
                if unsub:
                    unsub()
            except Exception:
                pass
            return {"status": "error", "message": "Sub-agent run timed out"}

        if not isinstance(result, dict) or result.get("status") != "success":
            msg = result.get("message") if isinstance(result, dict) else None
            return {"status": "error", "message": str(msg or "Sub-agent run failed")}

        saved_entry_ids = result.get("saved_entry_ids") if isinstance(result.get("saved_entry_ids"), list) else []
        txns = result.get("transaction_ids") if isinstance(result.get("transaction_ids"), list) else []
        undo_mappings = result.get("undo_mappings") if isinstance(result.get("undo_mappings"), list) else []
        inner_text = result.get("inner_message") if isinstance(result.get("inner_message"), str) else ""
        store_id = result.get("store_id") if isinstance(result.get("store_id"), str) else None
        subagent_id = result.get("subagent_id") if isinstance(result.get("subagent_id"), str) else None

        run_status = result.get("run_status") if isinstance(result.get("run_status"), str) else "success"
        error_message = result.get("error_message") if isinstance(result.get("error_message"), str) else None

        token_totals = result.get("token_usage_totals") if isinstance(result.get("token_usage_totals"), dict) else None
        token_last_turn = result.get("token_usage_last_turn") if isinstance(result.get("token_usage_last_turn"), dict) else None

        wrap_meta: Dict[str, Any] = {
            "subhistory": {
                "store_id": store_id,
                "entry_ids": saved_entry_ids,
                "subagent_name": str(sa),
                "mode": str(md),
            },
            "transaction_ids": [t for t in txns if isinstance(t, str) and t],
            "undo_mappings": [m for m in undo_mappings if isinstance(m, dict)],
            # Wrapper-only usage receipt (so the parent run_summary can include subagent cost
            # without polluting model-visible tool output).
            "subagent_usage": {
                "subagent_name": str(sa),
                "subagent_id": (str(subagent_id) if isinstance(subagent_id, str) and subagent_id else None),
                "mode": str(md),
                "run_status": str(run_status or "success"),
                "error_message": (str(error_message) if isinstance(error_message, str) and error_message else None),
                "token_usage_totals": token_totals,
                "token_usage_last_turn": token_last_turn,
            },
        }
        if md == "run" and isinstance(subagent_id, str) and subagent_id:
            wrap_meta["subhistory"]["subagent_id"] = subagent_id

        out: Dict[str, Any] = {
            "status": "success",
            "subagent_message": inner_text,
            "subagent_run_status": str(run_status or "success"),
            "subagent_error": (str(error_message) if isinstance(error_message, str) and error_message else None),
        }
        out["__wrap_meta__"] = wrap_meta
        return out


class GetSubagentsListTool:
    schema = {
        "type": "function",
        "name": "get_subagents_list",
        "description": (
            "List available sub-agents that can be run via run_subagent. "
            "Use this when selecting an existing helper before deciding whether to reuse one or define a one-shot run helper. Returns only: id, name, description."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "include_protected": {
                    "anyOf": [{"type": "boolean"}, {"type": "null"}],
                    "description": (
                        "If true, include protected agents (primary/family) in the results. "
                        "If false or null, return only role='subagent' agents."
                    ),
                },
                "survive": {
                    "anyOf": [{"type": "boolean"}, {"type": "null"}],
                    "description": "If false, this call/output will be excluded from future agent context (UI/persistence unchanged). Null/omitted defaults to true.",
                },
            },
            "required": ["include_protected", "survive"],
            "additionalProperties": False,
        },
    }

    def run(self, include_protected: Optional[bool] = None, survive: Optional[bool] = None) -> Dict[str, Any]:
        bus = Runtime.get_event_bus()
        reply_topic = f"agents.tool.reply.list.{uuid.uuid4()}"
        done = threading.Event()
        result: Dict[str, Any] = {}
        unsub = None

        def _on_reply(ev):
            nonlocal result, unsub
            try:
                if unsub:
                    unsub()
            except Exception:
                pass
            unsub = None
            payload = getattr(ev, "payload", {}) or {}
            result = payload if isinstance(payload, dict) else {"status": "error", "message": "Unexpected reply"}
            done.set()

        unsub = bus.subscribe(reply_topic, _on_reply)
        bus.publish("agents.cmd.list", {"reply_topic": reply_topic})

        if not done.wait(timeout=10.0):
            try:
                if unsub:
                    unsub()
            except Exception:
                pass
            return {"status": "error", "message": "Timeout listing sub-agents"}

        if not isinstance(result, dict) or result.get("status") != "success":
            return result if isinstance(result, dict) else {"status": "error", "message": "Unexpected result"}

        agents = result.get("agents") if isinstance(result.get("agents"), list) else []

        inc = bool(include_protected) if isinstance(include_protected, bool) else False
        if not inc:
            agents = [a for a in agents if isinstance(a, dict) and str(a.get("role") or "") == "subagent"]

        # Keep it minimal on purpose: id + name + description.
        slim = []
        for a in agents:
            if not isinstance(a, dict):
                continue
            slim.append(
                {
                    "id": str(a.get("id") or ""),
                    "name": str(a.get("display_name") or a.get("id") or ""),
                    "description": (str(a.get("description")) if a.get("description") is not None else None),
                }
            )

        out = {"status": "success", "agents": slim}
        if survive is False:
            out["__wrap_meta__"] = {"survive": False}
        return out


