# src/tools/canvas

Agent-facing Canvas Studio tool group.

This folder defines the **function tools** that let an agent manipulate the same persistent canvases the UI uses.

## What lives here
- `tool_group.yaml` — tool group manifest (names exported to the agent).
- `tools.py` — tool schemas + implementations (calls into `src.canvas.CanvasManager`).
- `prompt.md` — system prompt chapter for correct Canvas Studio usage.
- `__init__.py` — re-exports tool classes.

## Design rules
- **Strict schemas**: tools are `strict=true`; optional fields must be nullable (use `null`).
- **Sync safety**: mutators accept `expected_cursor_rev` so the agent can fail-closed if the canvas changed.
- **Context efficiency (Phase 1)**: most mutating tools return a small **receipt** instead of full canvas meta.
  - Mutator receipt contains: `canvas_id`, `updated_at`, `mode`, `history{cursor_rev,max_rev,min_rev}`, `active_layer_id`.
  - Layer ops also return a slim `layers` list.
  - `canvas_get` is the explicit “inspect meta” tool (returns full meta).
  - `canvas_get_image` is the explicit “vision” tool (injects composite or single-layer image).

## EventBus notifications
Most tools publish lightweight UI refresh hints:
- `canvas.changed`
- `canvas.list.changed`

(Delivery is via the in-process EventBus; Canvas Studio listens and refreshes.)

## Filesystem side-effects
- `canvas_export_png` writes to `scope=project|sandbox` and uses `FsRevisionStore` transactions (+ diff previews).
- `canvas_import_image` reads from `scope=project|sandbox` and commits a new canvas revision.

## When to use which tool
- Inspect state: `canvas_get(canvas_id=null)`
- See the result: `canvas_get_image(canvas_id=null, layer_id=null, caption=..., max_side=...)`
- Draw: `canvas_stroke`, `canvas_line`, `canvas_shape`, `canvas_fill`
- Layers: `canvas_layer_create/update/delete`
