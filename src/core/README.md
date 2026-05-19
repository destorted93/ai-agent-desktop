# src/core

Core agent implementations.

This folder contains the primary **agentic loop** implementations used by the app.
Everything else (UI, buses, storage) is built around the **event stream contract** emitted by these agents.

## What lives here
- `agent.py` — main agent loop (Responses-style):
  - streams assistant output
  - detects + executes tool calls
  - supports tool-driven message injection (`__inject_message__`)
  - tracks per-turn token usage
- `chat_completions_agent.py` — compatibility runner:
  - uses Chat Completions streaming
  - adapts chunks into the same event stream contract
  - shares tool execution + injection behavior with `Agent`

## Design rules
- **UI-agnostic**: no Qt imports.
- **Tools are first-class events**: tool call + tool output are persisted and rendered.
- **Per-turn accounting**: token usage is tracked per turn; app runners roll it into per-run/session totals.

## Key mechanics

## The agentic loop (turn-by-turn)
A **Run** is executed as a sequence of **turns**. Each turn is conceptually:

1) **Model call**
- Build the agent-visible history (from storage) + tool schemas.
- Stream assistant tokens as `response.output_text.delta` / `response.output_text.done` events.

2) **Tool-call detection**
- If the model produced tool/function calls, they are emitted/persisted as `function_call` items.

3) **Tool execution**
- Tools are executed (serially).
- Each tool produces a persisted `function_call_output` item.

4) **Continue vs stop**
- If **any** tool calls occurred in the turn, the Run continues to the next turn.
- If **no** tool calls occurred, the agent typically finalizes with a last assistant message and stops.

Code entry points:
- `Agent.run()` — orchestrates streaming + turn loop
- `Agent._process_turn()` — one turn (model → tools → decision)

`ChatCompletionsAgent` mirrors the same loop but changes the model-streaming layer.

## Tool wrapper meta (what happens to tool “wrappers”)
Tool calls are persisted as normal OpenAI-ish items (`function_call` / `function_call_output`),
but the app also stores **wrapper-only metadata** alongside them.

Core collects wrapper meta in two structures:
- `wrap_meta_by_call_id[call_id]` — metadata attached to a tool call/output pair
- `wrap_meta_by_item_index[i]` — metadata attached to an injected message item

This wrapper meta is never sent to the model. It exists so the UI + accounting can be rich without polluting context.

Common wrapper fields (examples):
- `status`: `running` / `success` / `error` (drives tool status icons)
- `error_message`: shown in UI when a tool fails
- `survive`: whether the entry should be included in future agent context (context-shaping)
- `subagent_usage`: token totals for sub-agent runs (Ariane / other helpers)
- `transaction_id`: filesystem transaction linkage (diff/undo plumbing)

Where it goes next:
- The runner persists the message items into session storage as **wrapped entries**.
- Storage/UI uses wrapper meta for:
  - status badges
  - collapsible blocks
  - token stats rollups
  - diff/undo linkage

(See `src/storage/session.py` + `src/storage/sessions_manager.py` for wrapping + projection to agent context.)

## Tool injection (`__inject_message__`)
Tools may return a payload containing `__inject_message__`.
The agent runtime will:
- pop it from the tool result
- append it into `session_items_during_run` (often `role="user"` so the model can “see” images)
- attach wrapper meta via `wrap_meta_by_item_index` (e.g. `injected=True`, `origin_tool_name`)
- emit `response.injected_message` so the UI can render it

Used for:
- injecting images (canvas/image tools)
- injecting debug telemetry (optional)

## Token tracking
- `token_usage` — last model call usage (current turn)
- `token_usage_history` — per-turn history across the run

App runner responsibilities:
- persist a per-run `run_summary` entry (totals + subagents)
- update session meta totals

(See `src/app_services/run_summary.py` and `src/app_handlers/agent_run.py`.)

## Where to start
If you want the mental model fast:
- `Agent.run()` and `_process_turn()` in `agent.py`
- tool execution path + wrapper meta (`wrap_meta_by_call_id`, `wrap_meta_by_item_index`)
- injection handling (`__inject_message__`)
