# SESSIONS

Sessions are your working contexts. 

In this app:
- A **Session** is a collection of **Runs**.
- A **Run** starts when the user sends a prompt and ends when the agent finishes responding.
- A Run can be made of multiple **Turns**.
- Tool calls and the internal loop are also Turns inside a Run.

The sum of all Runs (and their Turns) is the Session.

## Session Meta (titles/descriptions)

Session titles/descriptions are *UI metadata*.
They help the session dropdown stop being a graveyard of “New Session” and let you navigate by meaning.

When to call `set_session_meta`:
- Early: after `get_memories`, set a **provisional** title/description based on the first user message.
  - This applies to both normal sessions and **Group Sessions**.
- Re-check periodically during the session.
- Call it again when the current title/description has become stale because the session meaning changed.

Typical update triggers:
- different project or feature
- different phase (planning → implementation → debugging → postmortem)
- new dominant task or blocker
- the session started broad and now has a clear concrete focus
- durable vibe shift that changes how the session should be recognized later

What to set:
- Title (≤ 60 chars): “what is this session, in 3–8 words?”
- Description (≤ 400 chars, multiline ok): 1–3 lines:
  - Goal
  - Current focus / constraints
  - Optional: next step

Rules:
- Default is `session_id=null` (current session).
- Don’t spam: do not update it every turn.
- But do not leave it stale for the whole session after a real pivot.
- This does NOT edit/delete any timeline messages.

## Run summarization

Use `run_summary` to summarize the **current Run** so future Runs can use the summary instead of huge tool spam.
This exists because some tools, especially document-search tools such as `search_confluence` and `rag_search`, can produce outputs big enough to push important context out of the window.

When to call `run_summary` (phase 1):
- **MANDATORY** if you used `search_confluence` or `rag_search` at any point during the current Run.
- Recommended for tool-heavy Runs (large `read_file` dumps, lots of tool calls, long outputs).
- Not recommended for tiny Runs.

Rules:
- Keep the summary compact but sufficient (max 400 chars).
- Include: what happened/changed, key decisions/assumptions, important artifacts (paths/IDs), and what to do next.
- Phase 1: call `run_summary` with `run_id=null` (summarize the current Run). Only pass a specific `run_id` if it was provided to you (Phase 2.x).

Behavior (important):
- `run_summary` does **not** delete history or modify session storage directly.
- It marks the Run as summarized via wrapper-only meta; the app can then keep meaning while dropping bulk from context.
- If you see a `run_summary` tool call (and its result) in the session, that means earlier Runs were summarized to preserve the context window. Nothing was deleted or undone — the actions still happened; they’re just not fully present in your current context.

