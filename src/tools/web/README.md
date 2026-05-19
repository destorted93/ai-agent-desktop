# src/tools/web

Agent-facing web-search tool group.

This folder defines the **function tool** that lets an agent access up-to-date external information through web search.

## What lives here
- `tool_group.yaml` — tool group manifest (names exported to the agent).
- `tools.py` — tool schema wrapper for the built-in web search tool.
- `prompt.md` — system prompt chapter describing when web search should be used.
- `__init__.py` — re-exports tool classes.

## Design rules
- **Use only when freshness matters**: this group is for information that may be missing, changing, or time-sensitive.
- **Prefer authoritative sources**: official docs, vendor pages, standards, and primary references are preferred over secondary summaries.
- **Keep it minimal**: the implementation is intentionally thin because execution is handled by the model platform’s built-in web search capability.

## Tool split
- `web_search` — perform live web search for current external information.

## When to use it
- The user asked for current or recent information
- The answer depends on external facts that may have changed
- Local repo context and existing memory are insufficient

## Important note
This group is intentionally small. Most behavior lives in prompt guidance rather than in a large local wrapper implementation.