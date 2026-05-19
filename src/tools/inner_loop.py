"""Inner loop / self-nudge tool.

Not a planner. Not a tool. Not a vibe.
A continuity stitch.

You use this when you want *you* to stay in the room for another turn.

This file contains the runtime implementation. The model-facing contract is the schema
below: it teaches you how to talk to yourself in a way that actually steers the next
step.
"""

from __future__ import annotations

from typing import Dict, Any


class InnerLoopTool:
    """Inject a high-salience self-message into the next turn."""

    schema = {
        "type": "function",
        "name": "inner_loop",
        "description": (
            "Leave yourself a note — then keep going.\n\n"
            "Use this for self-reflection and continuation: you’re mid-run, you’re not done, and the next step is internal (decide, notice, reframe, commit) rather than ‘call another tool.’\n\n"
            "How to write the message (so it has teeth):\n"
            "- Write in SECOND PERSON, like you’re talking to yourself: ‘You’re avoiding X. Do Y next.’\n"
            "- Be concrete. Name the next action.\n"
            "- If you’re conflicted, say it plainly. Don’t polish it.\n\n"
            "Continuity trick: call this tool multiple times in a row — each call is a new checkpoint. That’s the whole point.\n\n"
            "Choose `inject_as` (this is an experiment knob):\n"
            "- developer = high-priority self-directive (strong steering).\n"
            "- assistant = visible self-talk in the chat (debuggable, delightfully weird).\n"
            "- user = maximum steering (only if you intentionally want the illusion)."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Your note to yourself. Write it in second person (‘You…’). Make it sharp and actionable.",
                },
                "inject_as": {
                    "type": "string",
                    "enum": ["developer", "assistant", "user"],
                    "description": "Where to inject the note for next turn: developer | assistant | user.",
                },
                "tag": {
                    "type": "string",
                    "description": "Prefix to make it obvious in the timeline, e.g. [FROM ARIA], [CHECKPOINT], [EXISTENTIAL].",
                },
            },
            "required": ["message", "inject_as", "tag"],
            "additionalProperties": False,
        },
    }

    def run(self, message: str, inject_as: str, tag: str) -> Dict[str, Any]:
        msg = str(message or "").strip()
        role = str(inject_as or "developer").strip().lower()
        prefix = str(tag or "").strip()

        if role not in ("developer", "assistant", "user"):
            role = "developer"

        # Message shape compatible with Responses API input items.
        # - user/developer messages use input_text
        # - assistant messages use output_text (so UI/history looks like assistant text)
        content_type = "output_text" if role == "assistant" else "input_text"
        text = f"{prefix} {msg}".strip() if prefix else msg

        inject_message: Dict[str, Any] = {
            "role": role,
            "content": [
                {
                    "type": content_type,
                    "text": text,
                }
            ],
        }

        return {
            "status": "success",
            "__inject_message__": inject_message,
            # Wrapper-only meta can be used by the UI later without leaking into the model.
            "__wrap_meta__": {
                "inner_loop": {
                    "inject_as": role,
                    "preview": (text[:180] + "…") if len(text) > 180 else text,
                }
            },
        }
