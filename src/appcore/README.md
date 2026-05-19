# src/appcore

App substrate (foundational infrastructure).

`appcore` is the **lowest layer** of the modular-monolith: small, import-safe building blocks shared by UI, runners, tools, and storage.

## Design rules
- **No UI dependencies** (no Qt imports).
- **Import hygiene matters**: avoid import-time side effects and circular imports (storage often imports appcore, so appcore should not import storage at module import time).
- **Thread-safe** where relevant.

## What lives here
- `event_bus.py` — in-process pub/sub + queue. Publish from any thread; deliver on caller thread via `pump()`.
- `runtime_context.py` — `Runtime` singleton-ish registry for shared managers (bus, config, paths, permissions, fs revisions, vectordb).
- `run_context.py` — `RunContext` ambient context carrier using `ContextVar` (session_id/run_id/agent_id etc.).
- `config_manager.py` — loads and resolves app + agent configuration (models/tools/system prompt, etc.).
- `paths.py` — app paths resolution helpers.
- `permissions.py` — placeholder for future policy/permission gating.

## How it fits together
- UI + background threads publish events to `Runtime.get_event_bus()`.
- The UI thread periodically calls `bus.pump(...)` to deliver events safely on the UI thread.
- Runners wrap agent execution in a `RunContext` so downstream systems (e.g., memory routing) can use ambient per-run state.

## Practical entry points
- `from src.appcore.runtime_context import Runtime`
- `from src.appcore.run_context import RunContext, patch_run_context`
