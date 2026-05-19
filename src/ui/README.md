# src/ui

PyQt6 desktop UI.

`src/ui` is the **front-end** for the app: a floating desktop widget plus a set of windows (chat, settings, canvas studio, agents studio, docs, memories, inner voice).
The UI talks to the app **only via EventBus topics** (no direct imports of app runners/storage internals).

## Entry point
- `FloatingWidget` in `widget.py` is the root window and orchestrator.
- `src/ui/__init__.py` exports `FloatingWidget`.

## Design rules
- **Bus-only integration**: UI publishes commands and subscribes to reply topics.
- **No blocking IO on UI thread**: long operations happen in the app thread; UI only renders events.
- **State is in the app**: sessions/memories/docs/canvas live in storage; UI is a view/controller.

## Key files
- `widget.py` — root widget:
  - manages active session id + session list
  - handles “Send” → publishes `agent.cmd.run` and subscribes to per-run `stream_topic`
  - receives streamed agent events and forwards to `ChatWindow`
  - opens auxiliary windows (Settings, Memories, Documents, Inner Voice, Session JSON, Canvas Studio, Agents Studio)
  - provides UX: dock handle, inference animation, multi-monitor position safety
- `screen_utils.py` — multi-monitor safe positioning helpers (`validate_window_position`).

## EventBus contract (high level)

### Sending a message (single-session)
- subscribe: `agent.ui.stream.run.<run_id>`
- publish: `agent.cmd.run`

Payload includes:
- `session_id`, `message`, `files`, `images`, `run_id`, `stream_topic`

Stopping:
- publish: `agent.cmd.stop`

### Session management (from the root widget)
Most session operations are initiated by `FloatingWidget`:
- `session.cmd.list`
- `session.cmd.create`
- `session.cmd.delete`
- `session.cmd.set_active`
- `session.cmd.entries.get`
- `session.cmd.entries.clear`
- `session.cmd.entry.delete_from_id` (edit/delete UX)

Session meta:
- `session.cmd.meta.set` (title/description)
- `session.cmd.telemetry.set` (per-session telemetry toggle)

### Settings
- `settings.cmd.get_current`
- `settings.cmd.save`
- `settings.cmd.confluence.upsert`
- `settings.cmd.confluence.delete`

### Feature windows
Most feature windows talk to the app directly via bus topics:
- Documents/RAG: `documents.cmd.*` (+ event `vectordb.collections.changed`)
- Memories: `memories.cmd.*`
- Inner voice: `inner_voice.*` + `subagent.cmd.session.entries.get` (sub-history)
- Canvas Studio: `canvas.cmd.*` (+ `canvas.event.*` streams)
- Agents studio: `agents.cmd.*`

(Exact usage is documented per-window in `src/ui/components/README.md`.)

## Persistent UI preferences
The UI uses Qt `QSettings` for window positions and minor UI state.
Positions are validated with `screen_utils.validate_window_position` to survive monitor hotplug.

## Where to start
- Overall flow (send/stream) → `widget.py::send_to_agent` and `widget.py::handle_agent_event`
- Chat UI structure → `components/chat_window.py`
- Bus topics → `src/app_handlers/*` (the app-side endpoints)
