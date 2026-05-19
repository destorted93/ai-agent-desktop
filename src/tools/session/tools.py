"""Session-level tools.

These tools operate on the *session metadata* (title/description) stored in the
Sessions index (sessions/index.enc).

They intentionally do NOT touch the session timeline/entries.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
import threading
import uuid

from ...appcore.runtime_context import Runtime
from ...appcore.run_context import get_run_context


class SetSessionMetaTool:
    schema = {
        "type": "function",
        "name": "set_session_meta",
        "description": (
            "Set your chat session’s title and/or description (session metadata).\n\n"
            "When to use:\n"
            "- At the start of a session (same vibe as get_memories): give this session a real name.\n"
            "- Revisit it later when the current title/description becomes stale because the session meaning changed: new project, new phase, new blocker, or a clearer focus.\n"
            "- Do not spam it every turn, but do not leave stale metadata unchanged after a real pivot.\n\n"
            "Rules:\n"
            "- Keep titles short and scannable (max 60 chars).\n"
            "- Keep descriptions compact but useful (max 400 chars; can be multiline).\n\n"
            "Behavior:\n"
            "- If session_id is null, updates the active session.\n"
            "- Updates the session dropdown label + hover tooltip (description).\n"
            "- Does NOT edit/delete any timeline messages."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": "Target session_id. If null, uses the active session.",
                },
                "title": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": "New session title. If null, leave unchanged.",
                },
                "description": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": "New session description. If null, leave unchanged.",
                },
            },
            "required": ["session_id", "title", "description"],
            "additionalProperties": False,
        },
    }

    def run(
        self,
        session_id: Optional[str],
        title: Optional[str],
        description: Optional[str],
    ) -> Dict[str, Any]:
        # Hard caps (keep the UI readable; avoid giant tool spam)
        MAX_TITLE = 60
        MAX_DESC = 400

        # At least one field must be provided.
        if not isinstance(title, str) and not isinstance(description, str):
            return {"status": "error", "message": "Provide title and/or description"}

        # Normalize + validate title.
        if isinstance(title, str):
            t = title.strip()
            if not t:
                return {"status": "error", "message": "Title cannot be empty"}
            if len(t) > MAX_TITLE:
                return {"status": "error", "message": f"Title too long (max {MAX_TITLE} chars)"}
            title = t

        # Validate description (multiline allowed; empty string clears).
        if isinstance(description, str) and len(description) > MAX_DESC:
            return {"status": "error", "message": f"Description too long (max {MAX_DESC} chars)"}

        bus = Runtime.get_event_bus()

        # Resolve session_id if omitted.
        sid = str(session_id).strip() if isinstance(session_id, str) else ""
        if not sid:
            reply_topic = f"session.tool.reply.active.get.{uuid.uuid4()}"
            done = threading.Event()
            out: Dict[str, Any] = {}
            unsub = None

            def _on_reply(ev):
                nonlocal out, unsub
                try:
                    if unsub:
                        unsub()
                except Exception:
                    pass
                unsub = None
                payload = getattr(ev, "payload", {}) or {}
                out = payload if isinstance(payload, dict) else {}
                done.set()

            unsub = bus.subscribe(reply_topic, _on_reply)
            bus.publish("session.cmd.active.get", {"reply_topic": reply_topic})
            if not done.wait(timeout=10.0):
                try:
                    if unsub:
                        unsub()
                except Exception:
                    pass
                return {"status": "error", "message": "Timeout resolving active session"}

            sid = out.get("active_session_id") if isinstance(out.get("active_session_id"), str) else ""
            sid = sid.strip() if isinstance(sid, str) else ""
            if not sid:
                return {"status": "error", "message": "No active session"}

        reply_topic = f"session.tool.reply.meta.set.{uuid.uuid4()}"
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
            "session.cmd.meta.set",
            {
                "reply_topic": reply_topic,
                "session_id": sid,
                "title": title,
                "description": description,
            },
        )

        if not done.wait(timeout=10.0):
            try:
                if unsub:
                    unsub()
            except Exception:
                pass
            return {"status": "error", "message": "Timeout setting session meta"}

        # The bus handler returns a full session meta entry ("session": {...}),
        # but the agent doesn't need that blob (and it clutters tool receipts).
        if not isinstance(result, dict):
            return {"status": "error", "message": "Unexpected result"}
        if result.get("status") != "success":
            msg = result.get("message")
            msg = msg if isinstance(msg, str) and msg.strip() else "Failed to set session meta"
            return {"status": "error", "message": msg}
        return {"status": "success"}


class RunSummaryTool:
    """Run summarization tool.

        This tool is intentionally *minimal*:
        - It does NOT modify session storage directly.
        - It returns wrapper-only meta (`__wrap_meta__`) so the app can:
        - stamp the current run_summary entry's `description`
        - later filter older runs from agent context based on that description.

        Phase 1/2.1 semantics
        - run_id is optional.
        - If run_id is null: summarize the current run.
        - If run_id is provided: summarize that run id (Phase 2.1: caller must supply the id; no discovery yet).

        Why it exists
        - Tool outputs can be huge and pollute future runs.
        - A good run summary lets us keep the *meaning* while dropping the bulk.

        Checklist (what a good summary includes)
        - What you changed/did (high-level)
        - Key decisions/assumptions
        - Artifacts/paths/IDs created (if any)
        - What to do next / what to be careful about
    """
    schema = {
        "type": "function",
        "name": "run_summary",
        "description": (
            "Summarize a Run so future Runs can use the summary instead of full tool spam. "
            "This does not delete or undo anything; it marks the Run as summarized so the app can preserve the context window by dropping bulk from future context.\n\n"
            "Targeting:\n"
            "- Pass run_id=null to summarize the current Run (default; Phase 1).\n"
            "- Pass a specific run_id only if the caller already knows it (Phase 2.x).\n\n"
            "When to use (phase 1 guidance):\n"
            "- MANDATORY if you used search_confluence or rag_search at any point during the current Run.\n"
            "- Recommended for tool-heavy Runs (large read_file dumps, long Confluence markdown, lots of tool calls).\n"
            "- Not recommended for tiny Runs (avoid spamming run_summary every time).\n\n"
            "Tip: include minimal facts needed to avoid re-fetching next Run: key links/page IDs, paths, decisions/assumptions, mutable operations, and next steps. (max 400 chars)"
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "A concise but sufficient summary of what happened in this run. Include the key facts, decisions, important paths/IDs/links, and next steps without re-reading all the tool outputs. (max 400 chars)",
                },
                "run_id": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": "Target Run id. If null, summarizes the current Run (Phase 1 default). If provided, summarizes that specific run_id (Phase 2.x: caller must supply; no discovery/enumeration yet).",
                },
            },
            "required": ["description", "run_id"],
            "additionalProperties": False,
        },
    }


    def run(self, description: str, run_id: Optional[str]) -> Dict[str, Any]:
        desc = (description or "").strip()
        if not desc:
            return {"status": "error", "message": "description is required"}

        ctx = get_run_context()
        current_run_id = str(ctx.run_id).strip() if isinstance(ctx.run_id, str) and ctx.run_id else ""

        target = str(run_id).strip() if isinstance(run_id, str) else ""

        # If run_id is omitted, target the current run.
        target_run_id = target or current_run_id

        if target and not target_run_id:
            return {"status": "error", "message": "run_id is empty"}

        out: Dict[str, Any] = {
            "status": "success",
            "run_id": (target_run_id if target_run_id else None),
        }

        # Wrapper-only: app consumes this at response.agent.done time.
        out["__wrap_meta__"] = {
            "run_summary_description": desc,
            "run_summary_target_run_id": (target_run_id if target_run_id else None),
        }

        return out
