# src/ui/components

Reusable PyQt6 windows + widgets.

This folder contains the concrete UI pieces used by `FloatingWidget` (`src/ui/widget.py`).
Most components:
- are standalone windows/dialogs (`QDialog` / `QWidget`)
- talk to the app via **EventBus** (`Runtime.get_event_bus()`)
- keep only transient view state (the app/storage are the source of truth)

## Key components

## Chat
- `chat_window.py` — primary chat UI:
  - message timeline rendering (user/assistant)
  - tool call blocks (collapsible, with status icons)
  - injected message cards (images/telemetry; collapsed by default)
  - composer (`MultilineInput`) + attachments (file/dir chips + screenshots)
  - “Usage + context window” panel + run receipts
  - session toolbar (new session, JSON viewer, telemetry toggle)
  - edit/delete message UX (delete-from-id + optional undo)

Bus topics used (direct or via parent widget):
- `agent.cmd.run` (published by `FloatingWidget`; chat window triggers the send)
- `agent.cmd.stop` (stop inference)
- `session.cmd.stats.get_token_usage`, `session.cmd.stats.get_run_usage` (usage panel)
- `subagent.cmd.session.entries.get` (render subagent sub-history)

Supporting widgets:
- `multiline_input.py` — chat composer with shortcuts
- `screenshot_selector.py` — interactive screenshot capture region UI
- `emoji_picker.py` — emoji picker
- `mouse_tracker.py` — helper for global mouse position/drag tracking

## Session inspection
- `session_json_window.py` — raw session JSON viewer (reads via bus)
- `json_viewer_dialog.py` — generic JSON pretty viewer

Bus topics used:
- `session.cmd.entries.get`
- `subagent.cmd.session.entries.get` (inner voice store JSON)

## Settings
- `settings_window.py` — config UI (base url, api token, runner mode, confluence tokens).
  Emits signals; parent/widget persists via bus.

Bus topics used (via parent `FloatingWidget`):
- `settings.cmd.get_current`
- `settings.cmd.save`
- `settings.cmd.confluence.upsert`
- `settings.cmd.confluence.delete`

## Memories
- `memories_window.py` — browse/search/edit long-term memories.

Bus topics used:
- `memories.cmd.get_memories`
- `memories.cmd.search_memories`
- `memories.cmd.create_memory`
- `memories.cmd.update_memory`
- `memories.cmd.delete_memory`

## Documents (RAG)
- `documents_window.py` — manage document collections + ingestion + chunk viewers.

Bus topics used:
- `documents.cmd.list_collections`
- `documents.cmd.get_chunks`
- `documents.cmd.delete_collection`
- `documents.cmd.create_collection_from_files` (with `progress_topic`)

Events subscribed:
- `vectordb.collections.changed` (debounced refresh)

## Inner Voice
- `inner_voice_window.py` — read-only view of Aria ↔ Ariane dialogue.
- `inner_voice_session_json_window.py` — raw JSON for inner voice session.

Bus topics used:
- `inner_voice.cmd.get_or_create_session`
- `inner_voice.cmd.entries.get`
- `inner_voice.cmd.entries.clear`

## Canvas Studio
- `canvas_studio.py` — canvas manager + drawing UI entry point
- `canvas_viewport.py` — pan/zoom viewport + brush cursor
- `canvas_picker_dialog.py` — canvas selection dialog

Bus topics used:
- `canvas.cmd.list`
- `canvas.cmd.create`
- `canvas.cmd.rename`
- `canvas.cmd.delete`
- `canvas.cmd.export_png`
- `canvas.cmd.get`
- `canvas.cmd.get_image` (injects user `input_image`)
- `canvas.cmd.layer.create`, `canvas.cmd.layer.update`, `canvas.cmd.layer.delete`
- `canvas.cmd.brush.set`, `canvas.cmd.stroke`, `canvas.cmd.line`, `canvas.cmd.shape`, `canvas.cmd.fill`
- `canvas.cmd.undo`, `canvas.cmd.redo`

Events subscribed (live canvas updates):
- `canvas.event.*`

## Agents Studio
- `agents_studio.py` — CRUD agent definitions + hot reload.

Bus topics used:
- `agents.cmd.list`
- `agents.cmd.get`
- `agents.cmd.save`
- `agents.cmd.delete`
- `agents.cmd.reload`

## Notes
- Many windows use `validate_window_position()` to avoid off-screen restores.
- Most UI rendering is “optimistic” (update UI first, then reconcile with bus replies).

## Where to start
- Message rendering + streamed events → `chat_window.py`
- Canvas UI → `canvas_studio.py` + `canvas_viewport.py`
- Agent catalog editing → `agents_studio.py`
