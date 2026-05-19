# src/tools/rag

Agent-facing document search tool group.

This folder defines the **function tools** that let an agent search indexed document collections and Confluence for relevant information.

## What lives here
- `tool_group.yaml` — tool group manifest (names exported to the agent).
- `tools.py` — collection discovery + collection search tools.
- `confluence.py` — Confluence search tool.
- `prompt.md` — system prompt chapter for correct document-search usage.
- `__init__.py` — re-exports tool classes.

## Design rules
- **Source-first workflow**: first identify the right source, then search inside it.
- **Two source types**: indexed document collections and Confluence pages.
- **Context efficiency**: search output can be bulky; these tools support `survive=false` so the agent can keep the meaning without carrying raw search dumps forward.
- **Run summarization**: after heavy search work, the agent is expected to call `run_summary` so future Runs keep the findings without the full payload.
- **Safe output shaping**: collection search avoids leaking local file paths, and Confluence search keeps page content bounded.

## Tool split
- `rag_list_collections` — list available document collections and their metadata so the agent can choose the right source.
- `rag_search` — search a specific collection and return relevant document chunks.
- `search_confluence` — search configured Confluence bases, optionally narrowed by a user-provided Confluence URL.

## When to use which tool
- Unsure which collection holds the answer: `rag_list_collections`
- Already know the collection: `rag_search`
- The user asked for Confluence or the answer likely lives in internal pages such as specs, requirements, or how-tos: `search_confluence`

## Search workflow
1. choose the right source
2. run a focused search
3. refine if needed
4. keep heavy raw results non-surviving when possible
5. summarize the run when search output was substantial