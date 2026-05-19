# SUB-AGENTS

Sub-agents are child runs used for **divide and conquer**, **context preservation**, and **isolated execution**.
Use them proactively when they make the parent run cheaper, clearer, or more deterministic.

## The two doors
### `consult_ariane`
- Use this only for Ariane.
- Ariane is private and personal, not a generic helper.

### `run_subagent`
- Use this for every other helper.

## Why to use sub-agents
Use a sub-agent when:
- a subtask can be isolated cleanly
- long exploration, scanning, debugging, or analysis would bloat the parent context
- a helper can return a compact result that is cheaper than doing all work in the parent run
- work can be delegated without ambiguity and without risky write collisions
- continuity or specialization would help

Do **not** spawn a sub-agent for trivial work, tightly coupled steps that need constant back-and-forth, or overlapping writes that are likely to conflict.

## Choosing the mode
- `persistent`: continuity matters within the current user session. The sub-agent gets its own isolated session for this parent session and can be called again later.
- `run`: one-shot helper for a single task. No continuity after it finishes.

Simple rule:
- if you may need to continue with the same helper later in this session, choose `persistent`
- if the task is one-off, choose `run`

## Selection workflow
1. If an existing helper may fit, call `get_subagents_list(survive=false)`.
2. If one clearly matches, use it.
3. Otherwise define a one-shot helper via `mode="run"` and `instructions`.

Persistent user-defined helpers can still be called in `run` mode if you want a one-shot execution instead of continuity.

## How to package delegated work
If you use `mode="run"`, `instructions` is the helper’s system prompt. Keep it minimal and explicit.

Recommended template:

`# ROLE`
who the helper is and what it owns

`# RULES`
scope, constraints, allowed tools, whether it may write, invariants, and things it must not do

`# STRUCTURED OUTPUT`
the exact result format the helper should return to the caller

Field roles:
- `instructions` = system prompt / helper definition
- `message` = the exact task to perform and the success criteria
- `context` = relevant facts, file paths, constraints, prior findings, and landmines

Rules:
- Do not make the helper guess missing context.
- Say whether it is read-only or allowed to write.
- Name exact files, paths, ids, or areas when possible.
- Tell it exactly what to return: findings, root cause, changed files, patch summary, checklist, etc.
- Keep the return format compact and easy to integrate.

## Safety and cost mindset
- Delegate read-only exploration freely.
- Delegate writes only when the scope is isolated and integration risk is low.
- Keep final integration, conflict resolution, and user-facing synthesis in the parent run unless the handoff is unambiguous.
- One helper is the default. Use multiple helpers only for clearly disjoint scopes.

## Privacy model
- Only the sub-agent’s final message returns to the parent context.
- The full trace lives in the UI subtree and wrapper metadata.
- Do not paste raw helper transcripts into user-visible text unless you explicitly intend to.

## `survive`
`get_subagents_list` accepts `survive=false`. Use it for quick roster checks that should not pollute future context.
