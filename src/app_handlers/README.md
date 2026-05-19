# src/app_handlers

App-level orchestration handlers.

This package holds **bus handlers** + **run orchestrators** extracted from `src/app.py`.
It is the layer that connects:
- UI / external callers → **bus topics**
- bus topics → **runner functions**
- runner output → UI event stream + persisted session entries

## Design rules
- **No UI imports**.
- **Preserve bus contract** (topic strings + payload/response shapes) when refactoring.
- **Move-first, refactor-later**: many modules were extracted verbatim to shrink `app.py`.

## What lives here
### Runners
- `agent_run.py` — single-session runner: builds agent-visible history, persists the user message with wrapper meta (META), runs the agent loop, and persists `run_summary`.
- `group_session.py` — group-session runner: runs multiple agents sequentially and isolates participant context.

### Bus handlers (topic endpoints)
- `bus_agent_runtime.py` — `agent.cmd.run`, `agent.cmd.stop`, `agent.cmd.run_subagent` + streaming events to the UI.
- `bus_sessions.py` — session CRUD + entries get/set/clear/delete + group participants.
- `bus_session_stats.py` — compute session/run stats (token totals, tool histograms, etc.).
- `bus_session_meta.py` — session meta updates (title/description, telemetry toggle, etc.).
- `bus_canvas.py` — Canvas Studio commands.
- `bus_inner_voice.py` — inner-voice (Ariane) session access.
- `bus_app_lifecycle.py` — app lifecycle commands (e.g. `app.cmd.restart`).
- `bus_memories.py`, `bus_documents.py`, `bus_settings.py`, `bus_transcribe.py`, `bus_fs_diffs.py`, `bus_agents.py` — other feature buses.

## Mental model: UI → Run
1. UI publishes `agent.cmd.run` (includes `session_id`, `run_id`, and `stream_topic`).
2. `bus_agent_runtime.py` spawns a worker thread, sets inference-running guard, then calls either:
   - `agent_run.run_agent(...)` (single)
   - `group_session.run_group_session(...)` (group)
3. Runner yields events; handler publishes them to `stream_topic` for UI rendering.
4. Session persistence happens during the run (user message first; then tool/assistant items; then persisted `run_summary`).

## Notes
- **Telemetry injection** (debug feature) is enabled/disabled per session via `telemetry_enabled` in session meta and is enforced in `bus_agent_runtime.py` when a run starts.
