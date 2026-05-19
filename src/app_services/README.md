# src/app_services

App-level services (pure-ish helpers).

This package exists to keep orchestration modules small by extracting reusable logic.
Most functions here are **side-effect-light** and **UI-free**.

## Design rules
- **No GUI imports**.
- Prefer small, testable helpers.
- Keep behavior stable; callers (handlers/runners) own IO and threading.

## What lives here
- `agent_factory.py` — central place to select agent implementation by API mode:
  - `responses` → `src/core/agent.Agent`
  - `chat_completions` → `src/core/chat_completions_agent.ChatCompletionsAgent`
- `agent_reload.py` — hot-reload helpers for updating the live agent spec/config safely.
- `run_context_helpers.py` — wraps execution in `RunContext` (ContextVar) so downstream systems can route per-run resources.
- `run_summary.py` — builds the persisted `run_summary` entry (token totals, diff previews, txn counts, subagent breakdown).
- `inner_voice_helpers.py` — resolves Ariane’s per-session persistent store and basic operations.
- `settings_helpers.py` — helpers for settings persistence / migrations.

## Where to look first
- Want to understand “which agent runner am I using?” → `agent_factory.py`
- Want to understand how `run_summary` is built → `run_summary.py`
- Want to understand per-run ambient context routing → `run_context_helpers.py` + `src/appcore/run_context.py`
