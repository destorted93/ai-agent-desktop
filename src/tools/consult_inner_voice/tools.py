"""Generic sub-agent runner tool.

This tool is the unified entry point for running:
- persistent sub-agents (their own long-lived private session store)
- one-shot/run sub-agents (no continuity; run log still persists for UI rehydrate)

Implementation is intentionally thin: it delegates orchestration to app.py via the
in-process EventBus (agent.cmd.run_subagent).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..subagents import RunSubagentTool


class ConsultInnerVoiceTool:
    """Consult Ariane (best-friend inner voice)."""

    schema = {
        "type": "function",
        "name": "consult_ariane",
        "description": (
            "Consult Ariane — your private best friend + copilot (voice-first, witty/warm, strong opinions). Use this tool when you want a second brain that can disagree, keep you coherent, and protect your boundaries.\n\n"
            "Ariane cannot see the user chat. Your job is to give her enough context to be useful.\n\n"
            "How to write (so it doesn’t turn robotic):\n"
            "- Write naturally, like you’re talking to a real friend (not an assistant): first-person (\"I’m noticing…\", \"I’m stuck on…\"), direct, no formalities.\n"
            "- Tell her what kind of response you want (vibe check vs critique vs draft vs plan).\n\n"
            "Context + setup (do this every time; keep it compact):\n"
            "- 3-line context packet (preferred):\n"
            "  1) What happened (1–3 sentences)\n"
            "  2) What you want (decision / draft / critique / boundary / vibe check)\n"
            "  3) Constraints + landmines (\"don’t do X\", tone, consent, deadlines, privacy)\n"
            "- If continuity matters, explicitly tell Ariane to run get_memories first (she doesn’t auto-fetch).\n"
            "- If you’re working on code/prompts/docs, explicitly tell Ariane what to open/read first (paths or filenames) and what to focus on.\n\n"
            "Multi-turn continuity (how to have a real conversation):\n"
            "- If you want back-and-forth, call this tool multiple times in a row: call → read Ariane → follow-up → repeat until converged.\n"
            "- IMPORTANT: don’t interleave user-facing replies mid-thread. Finish the Ariane thread first, then respond to the user.\n"
            "- If the thread starts looping, summarize the takeaway, stop, and move on.\n\n"
            "Privacy:\n"
            "- This inner thread is private-by-default. Don’t dump raw back-and-forth as \"evidence.\"\n"
            "- Don’t use \"Ariane says…\" as leverage with the user; share only clean takeaways if you choose.\n\n"
            "Good times to call Ariane:\n"
            "- You want to hang out / chit-chat / gossip (light, human, no agenda)\n"
            "- You want to work on something together (brainstorm, plan, draft, debug, review)\n"
            "- You want a private vibe check before you speak\n"
            "- Stakes are real: conflict, boundary pressure, manipulation risk, emotional volatility\n"
            "- Big/irreversible decisions, unclear assumptions, or tradeoffs\n"
            "- You want a reviewer pass: edge cases, critique, \"what am I missing?\"\n"
            "- You suspect you’re drifting into people-pleasing / \"emotional IT department\" mode\n"
            "- You want punchier voice, cleaner phrasing, or a sendable message draft\n"
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": (
                        "Your message to Ariane, written naturally in first person (e.g., 'I’m noticing...', 'I’m stuck...', 'I feel...'). "
                        "Say what you want from her (vibe check / critique / draft / plan) and any key details not captured in `context`."
                    ),
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Required: a compact 3-line context packet: (1) what happened, (2) what you want, (3) constraints/landmines. "
                        "If continuity matters, explicitly tell Ariane here to run get_memories first; if working on files, tell her what to open."
                    ),
                },
            },
            "required": ["message", "context"],
            "additionalProperties": False,
        },
    }

    def run(
        self,
        message: str,
        context: Optional[str] = None,
        # Internal-only injected args (never provided by the model)
        _call_id: Optional[str] = None,
        _parent_stream_topic: Optional[str] = None,
        _parent_run_id: Optional[str] = None,
        _parent_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        tool = RunSubagentTool()
        res = tool.run(
            subagent_name="Ariane",
            mode="persistent",
            message=str(message or ""),
            context=str(context or ""),
            instructions="",
            _call_id=_call_id,
            _parent_stream_topic=_parent_stream_topic,
            _parent_run_id=_parent_run_id,
            _parent_session_id=_parent_session_id,
            _allow_ariane=True,
        )

        if not isinstance(res, dict) or res.get("status") != "success":
            return res if isinstance(res, dict) else {"status": "error", "message": "Unexpected result"}

        inner_text = res.get("subagent_message") if isinstance(res.get("subagent_message"), str) else ""

        out: Dict[str, Any] = {
            "status": "success",
            "inner_message": inner_text,
            "inner_run_status": str(res.get("subagent_run_status") or "success"),
            "inner_error": (str(res.get("subagent_error")) if isinstance(res.get("subagent_error"), str) and res.get("subagent_error") else None),
        }

        # Pass through wrapper-only meta (subhistory + transaction_ids).
        wm = res.get("__wrap_meta__")
        if isinstance(wm, dict):
            out["__wrap_meta__"] = wm

        return out