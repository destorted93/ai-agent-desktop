# src/tools/session

Agent-facing session tool group.

This folder defines the **function tools** that let an agent maintain lightweight session metadata and summarize bulky Runs so the conversation stays navigable and context-efficient over time.

## What lives here
- `tool_group.yaml` — tool group manifest (names exported to the agent).
- `tools.py` — tool schemas + implementations for session metadata and run summarization.
- `prompt.md` — system prompt chapter for correct session-tool usage.
- `__init__.py` — re-exports tool classes.

## Design rules
- **Session metadata is UI metadata**: titles and descriptions help humans and agents recognize what a session is about; they do not modify timeline messages.
- **Early + periodic refresh**: agents should usually set provisional metadata early, then update it again when the session meaning becomes stale because of a real pivot.
- **Compact summaries**: `run_summary` exists to keep the meaning of tool-heavy Runs without carrying the full raw output into future context.
- **Minimal side effects**: these tools do not edit prior messages or delete history.

## Tool split
- `set_session_meta` — set or refresh the current session’s title and description.
- `run_summary` — summarize a Run so future Runs can rely on the summary instead of full tool spam.

## When to use which tool
- Start of session: `set_session_meta` with a provisional title and description.
- Later session pivot: call `set_session_meta` again when the topic, phase, blocker, or dominant focus changed enough that the old metadata is now stale.
- Tool-heavy or search-heavy Run: `run_summary`
- Mandatory summary cases: after `search_confluence` or `rag_search`

## Practical workflow
1. set a provisional session title and description early
2. work normally
3. refresh session metadata when the session meaning changes
4. summarize bulky Runs so future Runs keep the findings without the full payload