# CANVAS STUDIO

Canvas Studio is a shared, persistent drawing workspace. The user and the agent edit the same canvas projects. Work iteratively, not as a one-shot image generator.

## Core model
- Most tools accept `canvas_id=null` to use the current canvas.
- `canvas_get` inspects metadata, history, layers, and tool state.
- `canvas_get_image` is the vision sync tool. Use it to see the current composite or a single layer.
- Every mutating action creates a new revision.
- Layer actions also create new revisions.
- All canvases are layer-based. The agent usually sees the merged composite.

## Strict schema rule
Canvas tools are strict. If a field is optional, you often still must send it as `null`.

## Non-negotiable workflow
Think in short waves:
1. sync
2. act
3. resync

Boundary operations should usually be their own turn:
- `canvas_list`
- `canvas_set_current`
- `canvas_get`
- `canvas_get_image`
- `canvas_brush_set`
- layer create/update/delete
- `canvas_import_image`
- `canvas_undo`
- `canvas_redo`

Batch only **independent drawing mutations** that:
- use the already-active canvas, layer, and brush
- do not need visual confirmation between each other
- are reasonably order-insensitive

Do not do one stroke per turn.
Do not try to finish the whole drawing in one blind batch.

## Batch size
Default drawing wave: **2 to 5 tool calls max**.

Hard rule:
- never batch more than **5** canvas tool calls in one wave
- if more work remains, do another wave after resync
- use fewer than 5 when actions interact heavily or depend on precise visual feedback

## Vision and sync
Use `canvas_get_image` at the start and periodically afterward.

Resync after:
- the first setup
- each drawing wave
- layer changes
- import
- undo or redo
- any time you feel uncertain about the current image

Use `canvas_get` when you need meta, layer ids, or history details. Use `canvas_get_image` when you need to actually see.

## Revisions and `expected_cursor_rev`
Use `expected_cursor_rev` for sync-sensitive single operations when you want fail-closed behavior.

For a short batched drawing wave, it is acceptable to pass `expected_cursor_rev=null` if you intentionally want several consecutive mutations without a resync between them.

After a mutating wave, use the latest receipt or resync before the next precise operation.

## Brush, eraser, undo
Set brush parameters explicitly before a drawing wave.

Eraser is a real drawing tool, not just a substitute for undo.
- Use eraser for local cleanup, shaping, carving, and selective removal.
- Use undo and redo for recent mistaken actions.
- Prefer undo for fresh mistakes.
- Prefer eraser when undo would also remove later good work, or when you only want partial local cleanup.

## Layers and transparency
- Background layer stays at the bottom and cannot be deleted.
- Non-background layers are transparent by default.
- To work on a specific layer, inspect layer ids with `canvas_get` and switch via `canvas_layer_update(set_active=true, ...)`.
- To visually isolate a layer, either request that layer image directly or temporarily hide other layers.

Eraser semantics are layered:
- on the background layer, erasing reveals background behavior based on background alpha
- on non-background layers, erasing removes pixels from that layer and reveals what is below

## Pixel art
- In `pixel_art` mode, width and height are in cells, not pixels.
- Brush radius is interpreted in cells.
- `cell_px` controls export scale.
- Import, resize, rotate, and export use nearest-neighbor behavior where relevant.

## Drawing technique
- Use layers intentionally when the drawing is complex: rough sketch, clean linework, color blocks, shadows, highlights, and effects do not need to live on the same layer.
- For organic or curved lines, use `canvas_stroke` with a denser point path instead of forcing straight segments.
- For hard geometry, prefer `canvas_line` or `canvas_shape`; for organic contours, prefer `canvas_stroke`.
- Build complex forms in stages: block big masses first, then refine silhouettes, then add details.
- Use `canvas_fill` for flat regions, then refine edges with brush or eraser.
- Use eraser as a sculpting tool for cleanup and shape refinement, not only as damage control.
- If a mistake is recent and global, prefer undo; if the correction is local or selective, prefer eraser.
- When precision matters, do fewer actions per wave and resync sooner.

## Practical pattern
1. choose or inspect canvas
2. sync with `canvas_get_image`
3. optionally inspect layers with `canvas_get`
4. set brush or eraser
5. do a short wave of 2 to 5 drawing actions
6. resync image
7. continue, correct with undo or eraser, or switch layers
