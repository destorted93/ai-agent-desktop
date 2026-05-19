# src/canvas

Canvas Studio backend: persistent, Sandbox-backed canvas projects (raster + history + layers).

## What lives here
- `canvas_manager.py` — **source of truth** for canvas storage + rendering + history.
- `brushes.py` — stroke tool model (`ToolState/ToolSettings`) + rendering engines (round/eraser/alpha-eraser).
- `__init__.py` — exports `CanvasManager`, `StrokeToolType`, `ToolSettings`, `ToolState`.

## Storage layout (app-data Sandbox)
Root: `Sandbox/canvases/`
- `index.json` — canvas index for listing (sorted by `updated_at`).
- `current.json` — current canvas id.
- `Sandbox/canvases/<canvas_id>/`
  - `canvas.json` — canvas metadata + history cursor (`history.cursor_rev/max_rev`).
  - `actions.jsonl` — append-only action log (debug/forensics).
  - `history/snapshots/<rev>.png` — **composited** snapshot per revision.
  - `history/layers/<layer_id>/snapshots/<rev>.png` — per-layer snapshot per revision.
  - `history/layers_state/<rev>.json` — versioned layer stack (visibility/opacity/order/active).

## Core concepts
- **History**: every edit creates a new integer revision (`rev`).
  - `cursor_rev` is the currently selected revision.
  - `undo/redo` moves the cursor.
  - mutators support `expected_cursor_rev` for fail-closed sync.
- **Layers** (always enabled):
  - a pinned **Background** layer (`role='background'`) always exists.
  - background alpha controls *true* transparency behavior.
- **Modes**:
  - `normal`: pixel coordinates.
  - `pixel_art`: logical grid (coords are **cells**); `pixel_art.cell_px` affects display/export scaling.

## Rendering semantics (high-level)
- `brushes.py` engines:
  - `RoundBrushEngine`: alpha-composites a brush-color overlay from an `L` mask.
  - `EraserEngine`: legacy “paint with background color”.
  - `AlphaEraserEngine`: subtracts from alpha channel (true erase; used for layers/transparent bg).
- Shapes/lines/fill/strokes in `CanvasManager` write to the **active layer**, then re-composite.

## Integration points (outside this folder)
- UI bus handlers: `src/app_handlers/bus_canvas.py` (topics `canvas.cmd.*`).
- Canvas Studio UI: `src/ui/components/canvas_studio.py` + `canvas_viewport.py`.
- Agent tool group: `src/tools/canvas/` (schemas + prompt chapter).

## Contributor notes
- Prefer adding new drawing features in `CanvasManager` first (storage + history + snapshots), then wire UI/tools.
- Keep tool types strict (`StrokeToolType`)—don’t add enums without implemented semantics.
