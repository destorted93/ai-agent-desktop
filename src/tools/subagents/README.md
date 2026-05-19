# src/tools/subagents

Agent-facing sub-agent tool group.

This folder defines the **function tools** that let an agent discover, configure, and run helper agents as child runs.

## What lives here
- `tool_group.yaml` — tool group manifest (names exported to the agent).
- `tools.py` — tool schemas + implementations for helper discovery and child-run execution.
- `prompt.md` — system prompt chapter for correct sub-agent usage.
- `__init__.py` — re-exports tool classes.

## Design rules
- **Two doors**: Ariane is handled through `consult_ariane`; generic helpers use `run_subagent`.
- **Context preservation**: only the helper’s final message returns to the parent model context. The full trace stays in the UI subtree and wrapper metadata.
- **Two execution modes**: `persistent` gives a helper its own isolated session for the current parent session; `run` is a one-shot helper with no continuity after completion.
- **On-the-fly helper definition**: in `run` mode, `instructions` is effectively the helper definition or system prompt.
- **Divide and conquer**: this group exists to offload cleanly separable work, especially when it would otherwise bloat the parent context or burn tokens.

## Tool split
- `get_subagents_list` — list existing helpers so the caller can choose one.
- `run_subagent` — execute a helper in `persistent` or `run` mode.

## When to use which tool
- Unsure whether an existing helper already fits: `get_subagents_list`
- Need continuity with the same helper later in this user session: `run_subagent(mode="persistent")`
- Need a one-shot helper for a single task: `run_subagent(mode="run")`
- Need Ariane specifically: use `consult_ariane`, not this group

## Delegation pattern
1. decide whether the task is worth delegating
2. check for an existing helper if needed
3. choose `persistent` or `run`
4. provide clear `instructions`, `message`, and `context`
5. integrate the helper’s final result in the parent run

## Packaging rule of thumb
- `instructions` = role, rules, and expected output
- `message` = exact task and success criteria
- `context` = files, paths, findings, constraints, and risks