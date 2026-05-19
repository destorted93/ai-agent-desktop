# src/storage

Encrypted persistence layer.

This package owns the **durable state** of the app: sessions (logs), session index, memories, vector DB, sandbox root, and filesystem revision history.

## Design rules
- Data is stored as **encrypted JSON** under the app data directory.
- Favor **append-only logs** + derived indexes (safer than in-place mutation).
- Keep the storage API UI-agnostic.

## Key concepts
### Wrapped entries
The app persists OpenAI-ish message items (user/assistant/tool) as **wrapped entries**:
- `id`, `ts`, `kind`, `run_id`
- `content` (the raw item)
- wrapper-only fields used by UI and accounting (attachments, injected flags, subagent_usage, survive, etc.)

This allows:
- clean model-visible payloads
- rich UI rendering (status icons, collapsible blocks)
- side-channel metadata without polluting the model context

### Sessions index vs session logs
- `SessionsManager` manages multiple sessions:
  - encrypted index: `sessions/index.enc` (meta + active pointer)
  - per-session encrypted logs: `sessions/<session_id>.enc`
- `SessionManager` manages a single session log file and wrapping/unwrapping.

### META reconstruction
User messages are stored cleanly (text only).
Attachments + timestamps live in wrapper meta.
When building agent context, `SessionsManager.get_messages_for_agent()` reconstructs model-visible `META(...)` blocks from wrapper meta.

### Transactions ledger (mini-git)
Filesystem tools can emit `transaction_id`s.
`TransactionsManager` links txn_ids to entries and runs so we can:
- compute per-run diffs
- support undo/redo semantics

## What lives here
- `secure.py` — app data dir + encryption helpers (`read_encrypted_json`, `write_encrypted_json`, secrets).
- `sessions_manager.py` — multi-session manager + index reconcile + history shaping for the agent.
- `session.py` — single session log manager (wrap/unwrap items, derive tool statuses).
- `transactions_manager.py` — txn index (entry_index + run_index) used by fs diffs.
- `fs_revisions.py` / `fs_diff.py` — revision store + diff/index computation.
- `memory.py` — long-term memory store (per-agent routing via `RunContext`).
- `vectordb.py` — VectorDB/Chroma management for RAG + memory indexing.
- `sandbox_storage.py` — sandbox root helpers.

## Where to start
- Session persistence + wrapping: `session.py` then `sessions_manager.py`
- Token stats rollups from persisted logs: `src/app_handlers/bus_session_stats.py::compute_session_usage_stats`
- Memory routing: `memory.py` + `src/appcore/run_context.py`
