# src/tools/group_session

Agent-facing group-session tool group.

This folder defines the **function tools** that support multi-agent group sessions with explicit pass control and human-in-the-loop clarification.

## What lives here
- `tool_group.yaml` — tool group manifest (names exported to the agent).
- `tools.py` — tool schemas + implementations for explicit pass signaling and asking the human questions during a live loop.
- `prompt.md` — system prompt chapter describing the group-session protocol, role split, and stop behavior.
- `__init__.py` — re-exports tool classes.

## Design rules
- **Protocol over improvisation**: this group exists so multi-agent loops do not rely only on free-form text conventions.
- **Explicit loop control**: `group_pass` is how participants signal they are done for the current round.
- **Human-in-the-loop**: `ask_human` is the cheap way to pause for a clarification instead of burning more participant rounds.
- **Real team behavior**: participants are peers who should choose temporary roles, split work deliberately, review each other, and change shape when the phase changes.
- **Role discipline**: the broader prompt expects only a small active working set, with most agents staying quiet unless needed.
- **Write safety**: only the designated implementer should perform write or side-effect actions.

## Tool split
- `group_pass` — explicitly pass for the current round with a broadcast reason.
- `ask_human` — ask the human a short question during a live group-session loop.

## When to use which tool
- Done for the current round or have nothing substantive to add: `group_pass`
- Need a clarification, preference, decision, or approval from the human without collapsing the whole workflow: `ask_human`
- Need the whole room to wait on the user: spokesperson asks, everyone else passes

## Practical workflow
1. resync and understand the current task
2. choose the current working mode and temporary roles
3. keep only the active working set involved; others pass
4. implement, review, brainstorm, or split work in turns as needed
4. use `ask_human` for fast clarifications when needed
5. use `group_pass` to let the orchestrator end the loop cleanly