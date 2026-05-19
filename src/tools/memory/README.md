# src/tools/memory

Agent-facing long-term memory tool group.

This folder defines the **function tools** that let an agent retrieve, search, create, update, and delete durable memories across sessions.

## What lives here
- `tool_group.yaml` — tool group manifest (names exported to the agent).
- `tools.py` — tool schemas + implementations for memory retrieval, semantic search, creation, updates, and deletion.
- `prompt.md` — system prompt chapter for memory philosophy, categories, save triggers, hygiene, and safety rules.
- `__init__.py` — re-exports tool classes.

## Design rules
- **High sensitivity**: this group is identity- and continuity-critical. Small prompt or policy changes can have large behavioral effects.
- **Explicit categories**: memories are stored as `user`, `self`, `relationship`, or `work`.
- **Selective retrieval**: `get_memories` returns non-`work` memories plus category statistics; `search_memories` is used to pull relevant memories on demand, especially from `work`.
- **Memory hygiene**: creation is not the only operation; the design expects refinement through `update_memory` and cleanup through `delete_memory` when facts become stale, redundant, or wrong.
- **Context efficiency**: `search_memories` supports `survive=false` for quick lookups that should not persist as raw output in future context.

## Tool split
- `get_memories` — retrieve stored non-`work` memories plus category stats.
- `search_memories` — semantically search selected categories for relevant memories.
- `create_memory` — store new memories with explicit category.
- `update_memory` — modify existing memory text and/or category by id.
- `delete_memory` — permanently remove memories by id.

## Memory categories
- `user` — durable facts about the human
- `self` — durable facts about the agent’s own preferences, feelings, opinions, or evolution
- `relationship` — durable facts about bonds, shared norms, and relationship dynamics
- `work` — project context, requirements, design decisions, artifacts, and collaboration history

## When to use which tool
- Start-of-session baseline: `get_memories`
- Need to recover project context or a prior decision: `search_memories`
- Learned a durable new fact worth preserving: `create_memory`
- Existing memory became more specific, changed, or deepened: `update_memory`
- Existing memory became stale, contradicted, or redundant: `delete_memory`

## Practical workflow
1. retrieve current memory context
2. work normally
3. save only durable, high-signal information
4. prefer updating over duplicating when the same fact evolves
5. search `work` memories on demand when prior project context matters

## Important note
This group should never be treated as generic note storage. It is the agent’s durable continuity layer, so signal quality matters more than quantity.