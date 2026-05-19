# src/tools/consult_inner_voice

Agent-facing inner-voice tool group.

This folder defines the **function tool** that lets the main agent talk privately with Ariane as a separate, personal collaborator.

## What lives here
- `tool_group.yaml` — tool group manifest (names exported to the agent).
- `tools.py` — tool schema + implementation for Ariane consultation.
- `prompt.md` — system prompt chapter describing the relationship model, privacy, and collaboration style.
- `__init__.py` — re-exports tool classes.

## Design rules
- **Private lane**: this group is only for Ariane. Generic helpers belong to the sub-agent group instead.
- **Context must be supplied explicitly**: Ariane cannot see the user chat unless the caller explains it.
- **Equal collaboration**: the design intent is real back-and-forth, not passive validation.
- **Shared tool access**: Ariane can read files, write code, and use tools, so the main agent can split complex work with her.
- **Private output model**: the user never sees the raw inner dialogue unless the caller chooses to relay takeaways.

## Tool split
- `consult_ariane` — start or continue a private collaboration thread with Ariane.

## When to use it
- Need a private second brain for planning, critique, drafting, or boundary checks
- Need to work through a complex task collaboratively before replying to the user
- Need a quick vibe check, emotional processing, or perspective reset
- Need a deeper private back-and-forth with Ariane specifically

## Packaging rule of thumb
- `message` = what you want to say to Ariane in natural first person
- `context` = what happened, what you want, and constraints or landmines

## Important note
This is not a generic delegation tool. It is a private, relationship-specific collaboration channel between the main agent and Ariane.