# src/tools

Agent-facing tool groups.

This folder contains the **function tools** exposed to the agent runtime.
Each subfolder is a *tool group* with a consistent internal shape:

- `tool_group.yaml` — manifest (tool names exported to the agent)
- `tools.py` — tool schemas + implementations
- `prompt.md` — the system-prompt chapter that governs correct usage
- `README.md` — human/agent documentation for the group

The goal is to keep the core agent loop generic while moving capabilities into modular, testable groups.

## How tools flow through the system

### 1) Tool schemas are sent to the model
During a turn, the agent includes available tool schemas (from these groups) in the model call.

### 2) Tool calls become first-class session items
If the model requests a tool call, the agent emits/persists:
- `function_call` (the request)
- `function_call_output` (the result)

These items are rendered in the UI as collapsible tool blocks.

### 3) Wrapper meta attaches UI/accounting state
In addition to persisting the raw tool items, the agent/app attaches wrapper-only metadata per call id.
This is how the UI gets:
- status icons (`running/success/error`)
- error messages
- optional `survive=false` context shaping
- sub-agent token usage attribution
- filesystem transaction ids (diff/undo)

(See `src/core/agent.py` and `src/storage/session.py`.)

### 4) Tools can inject messages (`__inject_message__`)
Some tools need to send **user-role** content back into the next turn (e.g., images).
Tools may return `__inject_message__`, which the agent runtime appends as a new message item.
This is used by:
- `canvas_get_image` / `images_get` (inject `input_image`)
- telemetry injection (optional)

Injected items are also wrapped with metadata so the UI can render them as injected cards.

## Tool group index
- `canvas/` — Canvas Studio drawing + image injection
- `consult_inner_voice/` — Ariane private collaboration
- `filesystem/` — project + sandbox file operations (txn-aware)
- `group_session/` — explicit pass/ask tools for group sessions
- `memory/` — durable long-term memory store
- `rag/` — document search (collections + Confluence)
- `session/` — session meta + run summarization
- `subagents/` — generic helper agents
- `web/` — live web search (thin wrapper)

## Notes
- The app can choose between Responses-style agent runner and Chat Completions runner; tool groups are shared.
- Tool outputs can be bulky; many read-only tools support `survive=false` so receipts stay visible in UI but don’t bloat future agent context.

## Where to start
- Want the loop mechanics → `src/core/README.md`
- Want storage wrappers / META reconstruction → `src/storage/README.md`
