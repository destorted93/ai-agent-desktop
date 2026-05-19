"""Group Session tools.

These tools support multi-participant "group session" orchestration (Discord-like channels)
without relying on free-form text conventions.

Phase 1: a deterministic PASS tool so the app runner can stop the loop when all
participants have nothing to add.

Phase 3: ask_human — a human-in-the-loop question tool for group sessions.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
import threading
import uuid

from ...appcore.runtime_context import Runtime


class GroupPassTool:
    schema = {
        "type": "function",
        "name": "group_pass",
        "description": (
            "In a group session, call this to explicitly PASS (skip replying) for the current round.\n\n"
            "Use it when you have no substantive next step, are intentionally yielding the floor, or want the loop to move toward completion.\n\n"
            "The `reason` is REQUIRED and will be broadcast to other participants. Treat it as your final note, handoff, or blocker summary for the turn.\n\n"
            "Important: if you call group_pass, do not also send a normal assistant reply. If you do, your reply may be ignored for broadcast."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Required. Brief reason/final note for passing; this will be broadcast to other participants.",
                }
            },
            # Strict schemas: list all fields in required; optional fields are nullable.
            "required": ["reason"],
            "additionalProperties": False,
        },
    }

    def run(self, reason: str) -> Dict[str, Any]:
        # Intentionally tiny; the app runner detects that this tool was called.
        r = (reason or "").strip()
        if not r:
            r = "pass"
        return {"status": "success", "reason": r}


class AskHumanTool:
    schema = {
        "type": "function",
        "name": "ask_human",
        "description": (
            "Ask the human a short question during a live GROUP SESSION loop.\n\n"
            "Use this for fast unblockers such as missing requirements, preferences, decisions, or approvals instead of burning multiple participant rounds on guesses.\n\n"
            "This pauses the current participant turn (and therefore the group loop) until the human replies, cancels, or times out.\n\n"
            "Visibility:\n"
            "- public: the runner will broadcast the Q->A to other participants (preferred for shared work decisions).\n"
            "- private: only you see the human reply (useful for personal answers, side-comments, jokes, venting, quick roasts, etc.).\n\n"
            "Stop behavior: if the human presses Stop while you are waiting, this tool cancels cleanly."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "minLength": 1,
                    "description": "One focused question to ask the human. Keep it short and decision-oriented.",
                },
                "visibility": {
                    "type": "string",
                    "enum": ["private", "public"],
                    "description": "private = only caller sees reply; public = runner broadcasts Q->A to other participants. Prefer public for shared work decisions.",
                },
                "timeout_seconds": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "description": "Optional timeout in seconds. Null uses the default. Use a shorter timeout only when a quick answer is enough.",
                },
            },
            # Strict schemas: list all fields in required; optional fields are nullable.
            "required": ["question", "visibility", "timeout_seconds"],
            "additionalProperties": False,
        },
    }

    def run(
        self,
        question: str,
        visibility: str,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        q = (question or "").strip()
        vis = (visibility or "").strip().lower()
        if not q:
            return {"status": "error", "message": "question is required"}
        if vis not in ("private", "public"):
            return {"status": "error", "message": "visibility must be 'private' or 'public'"}

        try:
            t = int(timeout_seconds) if timeout_seconds is not None else 300
        except Exception:
            t = 300
        if t <= 0:
            t = 300
        if t > 600:
            # Hard cap to avoid indefinite loop-pauses.
            t = 600

        bus = Runtime.get_event_bus()
        reply_topic = f"human.ui.reply.ask.{uuid.uuid4()}"
        done = threading.Event()
        result: Dict[str, Any] = {}
        unsub_reply = None
        unsub_stop = None

        def _finish(payload: Dict[str, Any]):
            nonlocal result, unsub_reply, unsub_stop
            result = payload if isinstance(payload, dict) else {"status": "error", "message": "Unexpected reply"}
            try:
                if unsub_reply:
                    unsub_reply()
            except Exception:
                pass
            unsub_reply = None
            try:
                if unsub_stop:
                    unsub_stop()
            except Exception:
                pass
            unsub_stop = None
            done.set()

        def _on_reply(ev):
            payload = getattr(ev, "payload", {}) or {}
            _finish(payload if isinstance(payload, dict) else {"status": "error", "message": "Unexpected reply"})

        def _on_stop(ev):
            # Any Stop cancels the wait (single inference at a time; safe).
            _finish({"status": "cancelled", "reason": "stopped"})

        unsub_reply = bus.subscribe(reply_topic, _on_reply)
        try:
            unsub_stop = bus.subscribe("agent.cmd.stop", _on_stop)
        except Exception:
            unsub_stop = None

        bus.publish(
            "human.cmd.ask",
            {
                "reply_topic": reply_topic,
                "question": q,
                "visibility": vis,
                "timeout_seconds": int(t),
            },
        )

        def _inject(text: str) -> Dict[str, Any]:
            return {
                "role": "user",
                "content": [{"type": "input_text", "text": str(text or "").strip()}],
            }

        if not done.wait(timeout=float(t)):
            try:
                if unsub_reply:
                    unsub_reply()
            except Exception:
                pass
            try:
                if unsub_stop:
                    unsub_stop()
            except Exception:
                pass

            out = {"status": "timeout", "question": q, "visibility": vis, "message": None}
            out["__inject_message__"] = _inject(f"[ask_human {vis}]\nQ: {q}\nA: (timeout)")
            return out

        st = str(result.get("status") or "").strip().lower() if isinstance(result, dict) else "error"

        if st == "success":
            msg = result.get("message")
            msg = str(msg) if isinstance(msg, str) else ""
            out = {"status": "success", "question": q, "visibility": vis, "message": msg}
            out["__inject_message__"] = _inject(f"[ask_human {vis}]\nQ: {q}\nA: {msg}")
            return out

        if st == "cancelled":
            out = {"status": "cancelled", "question": q, "visibility": vis, "message": None}
            out["__inject_message__"] = _inject(f"[ask_human {vis}]\nQ: {q}\nA: (cancelled)")
            return out

        # error-ish
        em = result.get("message") if isinstance(result, dict) else None
        out = {"status": "error", "message": str(em or "ask_human failed"), "question": q, "visibility": vis}
        out["__inject_message__"] = _inject(f"[ask_human {vis}]\nQ: {q}\nA: (error)")
        return out
