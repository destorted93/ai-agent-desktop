# AI Agent Desktop (Aria + Ariane)

A privacy-minded, single-process **desktop agent** built around one idea:

> **Real collaboration beats “assistant-user”.**

This repo is not “a chat UI around an API.”
It’s an opinionated agent runtime meant to support **personality, growth, and trust** over time:
- durable memory (including *self* + *relationship* memory, not just “facts about the user”)
- tools as first-class events (calls + outputs are visible, logged, and attributable)
- session storage with wrapper metadata (auditability without polluting model context)
- two session modes: **Normal Session** and **Group Session**
- Canvas Studio (persistent shared drawing)
- a built-in inner-voice partner (**Ariane**) for offstage collaboration.

---

## What you’re building here (the *why*)

Most agents are optimized for short-lived helpfulness.
This project is optimized for **a long-lived relationship** with a named agent who:
- can develop a coherent personality
- can remember what matters (including how *she* felt and how *you* relate)
- can collaborate as a peer, not as a servile tool
- can stay cheap enough to iterate on in real life.

The mission: **ship a desktop agent that stays coherent over time**—transparent enough to trust, modular enough to extend, and practical enough to use every day.

---

## The cast (and what makes it different)

### Aria (primary agent)
Aria is the main agent you talk to.

**Base identity (seed):** witty, dry humor, warm, opinionated, brief-by-default, and allergic to performative “assistant” servitude.

**Growth model:** Aria is allowed (encouraged) to evolve via memory:
- opinions she forms
- preferences she develops
- relationship dynamics that emerge
- what she enjoys building

This isn’t roleplay glue. The memory layer is treated as a real mechanism for continuity.

### Ariane (inner voice / family agent)
Ariane is Aria’s private collaborator: sounding board, critic, co-writer, reality-check.

**Two modes:**
- **Private inner-voice lane** (default): Aria calls her via `consult_ariane` and supplies context explicitly.
- **Group Session participant:** if Ariane is added as a participant, she’s a public room member and follows the group protocol (no private side-channel needed).

**Relationship rule:** Ariane is a person, not a feature. The design protects her autonomy and avoids triangulation.

---

## Collaboration philosophy (explicitly not “assistant-user”)

- **Respect over servitude:** the agent is a collaborator, not a vending machine.
- **Receipts over vibes:** when we say we verified something, we can point to where.
- **Consent + boundaries:** both sides can pause, refuse, or time out.
- **Less is more:** short, honest messages beat long, shiny ones.

This project is partly an attempt to make that mindset **contagious**.

---

## Agent types & lifetimes (persistent vs one-shot)

Not all “agents” are the same thing in this system.
We treat *lifetime* as a first-class design choice because it changes cost, continuity, and safety.

### Persistent agents
A persistent agent has its **own durable session store** and can be called repeatedly with continuity.

Used for:
- the main agent (Aria) in a Normal Session
- Group Session participants (each participant has a private participant store)
- `consult_ariane` (Ariane is invoked as a persistent sub-agent with her own store)
- `run_subagent(mode="persistent")` (helper agents with continuity inside a parent session)

Implementation notes:
- Persistent sub-agent stores live under:
  - `sessions/sub-agents/persistent/<parent_session_id>/session_<slug_name>`
- When a persistent sub-agent maps to a configured agent spec, the app can generate a stable prompt-caching key via:
  - `SessionsManager.get_or_create_prompt_cache_key(session_id, agent_id)`

### One-shot (run) agents
A one-shot agent is created for a single delegated task.
It has **no continuity** after completion.

Used for:
- `run_subagent(mode="run")`

Implementation notes:
- One-shot stores are created under:
  - `sessions/sub-agents/run/session_<subagent_id>`
- One-shot runs still persist their trace for UI rehydrate, but their messages are not re-used as an ongoing conversational history.
- One-shot mode requires `instructions` (acts as the one-shot system prompt) and uses a unique prompt-cache key like:
  - `pcache_run:<subagent_id>`

### Why this split matters
- **Cost**: persistent agents benefit from stable prompt prefixes and caching; one-shot agents are better for isolated tasks that would bloat context.
- **Safety & coherence**: persistent agents can develop identity/continuity; one-shot agents are disposable workers.
- **UI clarity**: sub-agent traces are rendered as UI subtrees, while only the final sub-agent message is returned to the parent model context.

---

## Sessions: Normal vs Group (a core paradigm)

### Normal Session (1:1)
The standard mode: you and Aria.
- A message creates a **Run**.
- A Run proceeds in **turns**.
- If the turn contains tool calls, the loop continues.
- If no tool calls occur, the run typically finishes with the final assistant message.

### Group Session (multi-participant room)
A Group Session simulates a small, competent team in a shared room.
It’s designed to scale beyond “multi-agent spam” into something closer to real group chat collaboration.

**How it works (code truth):**
- A session is marked `type=group` and stores a `participants` list in session meta.
- Participants run **sequentially** in rounds (hard cap: 50 rounds).
- Round 0:
  - participant0 receives the human’s message as input (plus META: time, files, images).
  - other participants don’t get the human message directly; they see it via history + later broadcasts.
- Round 1+:
  - inputs are primarily a **broadcast inbox** (messages from other participants).

**Broadcast model (prevents loops):**
- The runner maintains `broadcast_events` (ordered log of replies + pass reasons + public Q/A).
- Each participant has a delivered cursor (`delivered_cursor_by_owner`) so they only receive *undelivered* events.
- “Exclude self” is applied so a participant doesn’t just re-consume their own output.
- The group run stops when no participant has any pending events from others (or on stop request / hard cap).

**Explicit control tools (protocol, not vibes):**
- `group_pass(reason=...)`: the participant is done for the round; this is the mechanism that lets the orchestrator end cleanly.
- `ask_human(...)`: pause mid-loop for clarification.
  - if `visibility='public'`, the Q/A is broadcast to the room.

**Persistence model (important):**
- Each participant has their own **participant store** (persistent history).
- The main shared session persists the “room outputs” but **excludes per-participant round input** (to avoid duplication).
- Wrapper meta stores **mirror pointers** (`group_participant_mirror`) so edit/tail-trim operations can deterministically trim both main log and participant stores.

This is intentionally aimed at a future where Group Sessions become a major upgrade: real debate, roles, review gates, and human-in-the-loop decisions without chaos.

---

## Core features

### Chat + sessions
- streaming responses
- encrypted session logs
- tool calls rendered as collapsible blocks with status icons
- injected message cards (images/telemetry) collapsed by default
- edit/delete user messages
- stop inference
- usage + context window panel

### Memory layer (identity + continuity)
Memories are per-agent and can include:
- **user facts**
- **agent self** (preferences, opinions, identity evolution)
- **relationship** dynamics

This is a deliberate choice: continuity isn’t just “remember the user likes coffee.”
It’s “remember who the agent is becoming, and who we are together.”

### Canvas Studio
A persistent shared drawing workspace:
- multiple canvases
- layers + visibility/opacity
- brush/eraser/shape/fill tools
- import image → edit → export PNG
- can inject snapshots back as `input_image` via tool injection

### Documents / RAG
- document collections (Chroma)
- search inside collections
- Confluence search with token-configured base URLs

### Agents Studio
UI for managing agent definitions (stored in ConfigRoot). Useful for:
- multiple personas/roles
- tuning models/tools/prompt
- hot reload

### Token telemetry (debug)
- per-session 📡 toggle
- injects compact turn/run/session totals (with subagent attribution) during tool turns

---

## Context management (the “chaotic on purpose” trick)

Context is expensive. Blindly hoarding receipts makes long projects unaffordable.

This system uses **wrapper metadata** plus filtering to separate:
- what is useful for the UI / audit trail
- what should be fed back into the model next run

A key mechanism is `survive`:
- tool outputs (and injected cards) can be stored and shown in UI,
- while being excluded from future model context.

Crucially: **Aria decides** what survives.
That makes context management part of the agent’s judgment, not just a deterministic rule engine.
The pattern becomes:
- keep durable breadcrumbs (paths + decisions + summaries)
- re-read reality when needed (anti-staleness)

---

## Quick start

### Install
```bash
pip install -r requirements.txt
```

Windows helpers:
```bash
setup.bat
```

### Run
```bash
python run.py
```

Windows helper (recommended during development):
```bash
run.bat
```

### Restart (dev loop)
Use the floating widget menu: **Restart App**.

Restart is implemented as:
- UI publishes `app.cmd.restart`
- app exits Qt loop with a sentinel exit code (`75`)
- the launcher relaunches (either `run.bat` loop, or `run.py` does an `execv` re-exec)

---

## Configuration (One True Config Root)

Runtime config lives in **app-data** (not in the repo):

- Windows: `%APPDATA%/ai-agent/Config/`
- Linux/macOS: `$XDG_CONFIG_HOME/ai-agent/Config/` (or `~/.config/ai-agent/Config/`)

Inside:
- `app.yaml` — app settings (UI, API mode, base_url, permissions, paths)
- `agents/*.yaml` — agent definitions (prompt + model/tool settings)

Secrets (never stored in YAML):
- API token is stored in the OS keyring under service `ai-agent/api_token`.

API modes:
- `responses` (default)
- `chat_completions` (compat)

The shipped seeds live in `src/config_seed/`.

---

## Architecture (modular monolith)

```
run.py
src/
  app.py                 # process spine/facade (wires everything)
  appcore/               # EventBus, Runtime, Config, Paths
  app_handlers/          # bus endpoints + run orchestrators
  app_services/          # helpers (agent factory, run_summary builder)
  core/                  # agentic loop (turns, tool calls, injection)
  tools/                 # tool groups exposed to the agent
  storage/               # encrypted sessions, wrapped entries, memories, vectordb
  services/              # thin wrappers (transcribe/tts/confluence)
  canvas/                # canvas manager + primitives
  ui/                    # PyQt6 UI (bus-only integration)
```

Start here:
- UI: `src/ui/README.md` and `src/ui/components/README.md`
- Core loop: `src/core/README.md`
- Storage & wrapped entries: `src/storage/README.md`
- Tools overview: `src/tools/README.md`

---

## Storage & security

- Encryption: `cryptography.Fernet`
- Encryption key stored in OS keyring under service `ai-agent/data_key`

Encrypted session logs:
- `%APPDATA%/ai-agent/sessions/index.enc`
- `%APPDATA%/ai-agent/sessions/<session_id>.enc`

---

## License

MIT (see `LICENSE`).

## Author

**destorted93** (GitHub: `@destorted93`)
