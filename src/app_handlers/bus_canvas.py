"""Canvas Studio bus handlers (extracted from src/app.py).

Move-first, refactor-later.
- Preserve bus topics.
- Preserve payload/response shapes.
- No UI imports.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List


def register_canvas_handlers(app: Any, bus: Any) -> List[Callable[[], None]]:
    """Register Canvas Studio bus handlers. Returns unsubscribe callables."""
    unsubs: List[Callable[[], None]] = []

    unsubs.append(bus.subscribe("canvas.cmd.list", lambda ev: bus_canvas_list(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.create", lambda ev: bus_canvas_create(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.delete", lambda ev: bus_canvas_delete(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.set_current", lambda ev: bus_canvas_set_current(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.get", lambda ev: bus_canvas_get(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.get_image", lambda ev: bus_canvas_get_image(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.get_gif", lambda ev: bus_canvas_get_gif(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.brush_set", lambda ev: bus_canvas_brush_set(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.stroke", lambda ev: bus_canvas_stroke(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.line", lambda ev: bus_canvas_line(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.shape", lambda ev: bus_canvas_shape(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.fill", lambda ev: bus_canvas_fill(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.undo", lambda ev: bus_canvas_undo(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.redo", lambda ev: bus_canvas_redo(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.rename", lambda ev: bus_canvas_rename(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.duplicate", lambda ev: bus_canvas_duplicate(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.sample_color", lambda ev: bus_canvas_sample_color(app, ev)))

    # Layers (Phase 2)
    unsubs.append(bus.subscribe("canvas.cmd.layer.create", lambda ev: bus_canvas_layer_create(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.layer.update", lambda ev: bus_canvas_layer_update(app, ev)))
    unsubs.append(bus.subscribe("canvas.cmd.layer.delete", lambda ev: bus_canvas_layer_delete(app, ev)))

    # Image import (Phase 2)
    unsubs.append(bus.subscribe("canvas.cmd.image.import_apply", lambda ev: bus_canvas_image_import_apply(app, ev)))

    return unsubs


def bus_canvas_list(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return
            canvases = app.canvas_manager.list_canvases()
            cur = app.canvas_manager.get_current_canvas_id()
            app._bus_reply(reply_topic, {"status": "success", "current_canvas_id": cur, "count": len(canvases), "canvases": canvases})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_create(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    width = payload.get("width")
    height = payload.get("height")
    name = payload.get("name")
    bg = payload.get("background_rgba")
    set_current = bool(payload.get("set_current", True))
    mode = payload.get("mode")
    cell_px = payload.get("cell_px")

    if width is None or height is None:
        app._bus_reply(reply_topic, {"status": "error", "message": "width and height are required"})
        return

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return
            meta = app.canvas_manager.create_canvas(
                width=int(width),
                height=int(height),
                background_rgba=tuple(bg) if isinstance(bg, (list, tuple)) else (255, 255, 255, 255),
                name=str(name) if isinstance(name, str) and name.strip() else None,
                set_current=bool(set_current),
                actor="user",
                mode=str(mode) if isinstance(mode, str) and mode.strip() else "normal",
                cell_px=int(cell_px) if cell_px is not None else None,
            )
            app._bus_reply(reply_topic, {"status": "success", "canvas": meta, "current_canvas_id": app.canvas_manager.get_current_canvas_id()})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_delete(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    canvas_id = payload.get("canvas_id")
    if not reply_topic:
        return
    if not isinstance(canvas_id, str) or not canvas_id.strip():
        app._bus_reply(reply_topic, {"status": "error", "message": "canvas_id is required"})
        return

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return
            app.canvas_manager.delete_canvas(canvas_id=str(canvas_id), actor="user")
            app._bus_reply(reply_topic, {"status": "success", "current_canvas_id": app.canvas_manager.get_current_canvas_id()})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_set_current(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    canvas_id = payload.get("canvas_id")
    if not reply_topic:
        return

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return
            app.canvas_manager.set_current_canvas_id(str(canvas_id) if isinstance(canvas_id, str) and canvas_id.strip() else None)
            app._bus_reply(reply_topic, {"status": "success", "current_canvas_id": app.canvas_manager.get_current_canvas_id()})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_get(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    canvas_id = payload.get("canvas_id")
    if not reply_topic:
        return

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return
            cid = app.canvas_manager.resolve_canvas_id(str(canvas_id) if isinstance(canvas_id, str) else None)
            if not cid:
                app._bus_reply(reply_topic, {"status": "error", "message": "No current canvas"})
                return
            meta = app.canvas_manager.load_canvas_meta(cid)
            if not meta:
                app._bus_reply(reply_topic, {"status": "error", "message": "Canvas not found"})
                return
            app._bus_reply(reply_topic, {"status": "success", "canvas_id": cid, "canvas": meta})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_get_image(app: Any, event) -> None:
    """Return the current canvas image as base64 PNG."""
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    canvas_id = payload.get("canvas_id")
    if not reply_topic:
        return

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return

            cid, meta, png_bytes = app.canvas_manager.get_current_image_png_bytes(
                canvas_id=str(canvas_id) if isinstance(canvas_id, str) and canvas_id.strip() else None
            )
            import base64

            hist = meta.get("history") if isinstance(meta, dict) and isinstance(meta.get("history"), dict) else {}
            cursor = int(hist.get("cursor_rev", 0) or 0)

            app._bus_reply(
                reply_topic,
                {
                    "status": "success",
                    "canvas_id": cid,
                    "png_b64": base64.b64encode(png_bytes).decode("utf-8"),
                    "bytes": len(png_bytes),
                    "cursor_rev": cursor,
                },
            )
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_get_gif(app: Any, event) -> None:
    """Return an animated GIF (base64) representing the current layers stack."""
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    canvas_id = payload.get("canvas_id")
    frame_duration_ms = payload.get("frame_duration_ms")
    loop_forever = payload.get("loop_forever")

    try:
        frame_ms_i = int(frame_duration_ms) if frame_duration_ms is not None else 120
    except Exception:
        frame_ms_i = 120
    frame_ms_i = max(10, min(10000, int(frame_ms_i)))

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return

            cid, meta, gif_bytes = app.canvas_manager.get_export_gif_bytes(
                canvas_id=str(canvas_id) if isinstance(canvas_id, str) and canvas_id.strip() else None,
                frame_duration_ms=int(frame_ms_i),
                loop_forever=bool(loop_forever),
            )

            import base64

            hist = meta.get("history") if isinstance(meta, dict) and isinstance(meta.get("history"), dict) else {}
            cursor = int(hist.get("cursor_rev", 0) or 0)

            app._bus_reply(
                reply_topic,
                {
                    "status": "success",
                    "canvas_id": cid,
                    "gif_b64": base64.b64encode(gif_bytes).decode("utf-8"),
                    "bytes": len(gif_bytes),
                    "cursor_rev": cursor,
                },
            )
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_sample_color(app: Any, event) -> None:
    """Sample a pixel RGBA from the current canvas snapshot."""
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    canvas_id = payload.get("canvas_id")
    x = payload.get("x")
    y = payload.get("y")
    expected = payload.get("expected_cursor_rev")

    if x is None or y is None:
        app._bus_reply(reply_topic, {"status": "error", "message": "x and y are required"})
        return

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return

            result = app.canvas_manager.sample_color(
                canvas_id=str(canvas_id) if isinstance(canvas_id, str) and canvas_id.strip() else None,
                x=float(x),
                y=float(y),
                expected_cursor_rev=int(expected) if expected is not None else None,
            )
            app._bus_reply(reply_topic, result if isinstance(result, dict) else {"status": "error", "message": "Unexpected result"})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


# ---------------------------
# Image import (Phase 2)
# ---------------------------

def bus_canvas_image_import_apply(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    canvas_id = payload.get("canvas_id")
    layer_id = payload.get("layer_id")
    image_b64 = payload.get("image_b64")

    dest_rect = payload.get("dest_rect")
    crop_rect = payload.get("crop_rect")
    rotation_deg = payload.get("rotation_deg")
    opacity = payload.get("opacity")
    expected = payload.get("expected_cursor_rev")

    if not isinstance(image_b64, str) or not image_b64.strip():
        app._bus_reply(reply_topic, {"status": "error", "message": "image_b64 is required"})
        return
    if not isinstance(dest_rect, dict):
        app._bus_reply(reply_topic, {"status": "error", "message": "dest_rect is required"})
        return

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return

            import base64

            try:
                img_bytes = base64.b64decode(image_b64)
            except Exception:
                app._bus_reply(reply_topic, {"status": "error", "message": "Invalid image_b64"})
                return

            meta = app.canvas_manager.import_image_apply(
                canvas_id=str(canvas_id) if isinstance(canvas_id, str) and canvas_id.strip() else None,
                layer_id=str(layer_id) if isinstance(layer_id, str) and layer_id.strip() else None,
                image_bytes=img_bytes,
                dest_rect=dest_rect,
                crop_rect=crop_rect,
                rotation_deg=float(rotation_deg or 0.0),
                opacity=float(opacity if opacity is not None else 1.0),
                actor="user",
                expected_cursor_rev=int(expected) if expected is not None else None,
            )
            app._bus_reply(reply_topic, {"status": "success", "canvas": meta})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_brush_set(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    rgba = payload.get("rgba")
    radius = payload.get("radius")
    opacity = payload.get("opacity")
    brush_type = payload.get("brush_type")
    canvas_id = payload.get("canvas_id")

    if not isinstance(rgba, (list, tuple)) or len(rgba) != 4:
        app._bus_reply(reply_topic, {"status": "error", "message": "rgba must be [r,g,b,a]"})
        return

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return
            meta = app.canvas_manager.set_brush(
                canvas_id=str(canvas_id) if isinstance(canvas_id, str) and canvas_id.strip() else None,
                rgba=tuple(rgba),
                radius=int(radius or 12),
                opacity=float(opacity if opacity is not None else 1.0),
                actor="user",
                brush_type=(str(brush_type) if isinstance(brush_type, str) and brush_type.strip() else None),
            )
            app._bus_reply(reply_topic, {"status": "success", "canvas": meta})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_stroke(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    pts = payload.get("points")
    canvas_id = payload.get("canvas_id")
    expected = payload.get("expected_cursor_rev")

    if not isinstance(pts, list) or len(pts) < 1:
        app._bus_reply(reply_topic, {"status": "error", "message": "points must be a list"})
        return

    points: List[tuple[float, float]] = []
    for p in pts:
        if isinstance(p, dict):
            try:
                points.append((float(p.get("x")), float(p.get("y"))))
            except Exception:
                continue

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return
            meta = app.canvas_manager.draw_stroke(
                canvas_id=str(canvas_id) if isinstance(canvas_id, str) and canvas_id.strip() else None,
                points=points,
                actor="user",
                expected_cursor_rev=int(expected) if expected is not None else None,
            )
            app._bus_reply(reply_topic, {"status": "success", "canvas": meta})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_line(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    canvas_id = payload.get("canvas_id")
    x1 = payload.get("x1")
    y1 = payload.get("y1")
    x2 = payload.get("x2")
    y2 = payload.get("y2")
    expected = payload.get("expected_cursor_rev")

    if x1 is None or y1 is None or x2 is None or y2 is None:
        app._bus_reply(reply_topic, {"status": "error", "message": "x1,y1,x2,y2 are required"})
        return

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return
            meta = app.canvas_manager.draw_line(
                canvas_id=str(canvas_id) if isinstance(canvas_id, str) and canvas_id.strip() else None,
                x1=float(x1),
                y1=float(y1),
                x2=float(x2),
                y2=float(y2),
                actor="user",
                expected_cursor_rev=int(expected) if expected is not None else None,
            )
            app._bus_reply(reply_topic, {"status": "success", "canvas": meta})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_shape(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    canvas_id = payload.get("canvas_id")
    shape = payload.get("shape")
    x1 = payload.get("x1")
    y1 = payload.get("y1")
    x2 = payload.get("x2")
    y2 = payload.get("y2")
    filled = payload.get("filled")
    expected = payload.get("expected_cursor_rev")

    if x1 is None or y1 is None or x2 is None or y2 is None:
        app._bus_reply(reply_topic, {"status": "error", "message": "x1,y1,x2,y2 are required"})
        return

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return
            meta = app.canvas_manager.draw_shape(
                canvas_id=str(canvas_id) if isinstance(canvas_id, str) and canvas_id.strip() else None,
                shape=str(shape or ""),
                x1=float(x1),
                y1=float(y1),
                x2=float(x2),
                y2=float(y2),
                filled=bool(filled),
                actor="user",
                expected_cursor_rev=int(expected) if expected is not None else None,
            )
            app._bus_reply(reply_topic, {"status": "success", "canvas": meta})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_fill(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    canvas_id = payload.get("canvas_id")
    x = payload.get("x")
    y = payload.get("y")
    alpha_threshold = payload.get("alpha_threshold")
    expected = payload.get("expected_cursor_rev")

    if x is None or y is None:
        app._bus_reply(reply_topic, {"status": "error", "message": "x and y are required"})
        return

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return
            meta = app.canvas_manager.fill_bucket(
                canvas_id=str(canvas_id) if isinstance(canvas_id, str) and canvas_id.strip() else None,
                x=float(x),
                y=float(y),
                alpha_threshold=int(alpha_threshold) if alpha_threshold is not None else None,
                actor="user",
                expected_cursor_rev=int(expected) if expected is not None else None,
            )
            app._bus_reply(reply_topic, {"status": "success", "canvas": meta})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_undo(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    steps = payload.get("steps", 1)
    canvas_id = payload.get("canvas_id")

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return
            meta = app.canvas_manager.undo(
                canvas_id=str(canvas_id) if isinstance(canvas_id, str) and canvas_id.strip() else None,
                steps=int(steps or 1),
                actor="user",
            )
            app._bus_reply(reply_topic, {"status": "success", "canvas": meta})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_redo(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    steps = payload.get("steps", 1)
    canvas_id = payload.get("canvas_id")

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return
            meta = app.canvas_manager.redo(
                canvas_id=str(canvas_id) if isinstance(canvas_id, str) and canvas_id.strip() else None,
                steps=int(steps or 1),
                actor="user",
            )
            app._bus_reply(reply_topic, {"status": "success", "canvas": meta})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_rename(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    canvas_id = payload.get("canvas_id")
    name = payload.get("name")
    if not reply_topic:
        return
    if not isinstance(canvas_id, str) or not canvas_id.strip():
        app._bus_reply(reply_topic, {"status": "error", "message": "canvas_id is required"})
        return
    if not isinstance(name, str) or not name.strip():
        app._bus_reply(reply_topic, {"status": "error", "message": "name is required"})
        return

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return
            meta = app.canvas_manager.rename_canvas(canvas_id=str(canvas_id), name=str(name), actor="user")
            app._bus_reply(reply_topic, {"status": "success", "canvas": meta})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_duplicate(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    source_canvas_id = payload.get("source_canvas_id")
    name = payload.get("name")
    set_current = bool(payload.get("set_current", True))
    if not reply_topic:
        return
    if not isinstance(source_canvas_id, str) or not source_canvas_id.strip():
        app._bus_reply(reply_topic, {"status": "error", "message": "source_canvas_id is required"})
        return

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return
            meta = app.canvas_manager.duplicate_canvas(
                source_canvas_id=str(source_canvas_id),
                name=(str(name) if isinstance(name, str) else None),
                set_current=bool(set_current),
                actor="user",
            )
            app._bus_reply(
                reply_topic,
                {"status": "success", "canvas": meta, "current_canvas_id": app.canvas_manager.get_current_canvas_id()},
            )
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


# ----------------------------
# Layers (Phase 2)
# ----------------------------


def bus_canvas_layer_create(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    canvas_id = payload.get("canvas_id")
    name = payload.get("name")
    description = payload.get("description")
    set_active = bool(payload.get("set_active", True))
    source_layer_id = payload.get("source_layer_id")
    expected = payload.get("expected_cursor_rev")

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return
            meta = app.canvas_manager.layer_create(
                canvas_id=str(canvas_id) if isinstance(canvas_id, str) and canvas_id.strip() else None,
                name=str(name) if isinstance(name, str) else None,
                description=str(description) if isinstance(description, str) else None,
                set_active=bool(set_active),
                source_layer_id=str(source_layer_id) if isinstance(source_layer_id, str) and source_layer_id.strip() else None,
                expected_cursor_rev=int(expected) if expected is not None else None,
                actor="user",
            )
            app._bus_reply(reply_topic, {"status": "success", "canvas": meta})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_layer_update(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    canvas_id = payload.get("canvas_id")
    layer_id = payload.get("layer_id")
    if not isinstance(layer_id, str) or not layer_id.strip():
        app._bus_reply(reply_topic, {"status": "error", "message": "layer_id is required"})
        return

    expected = payload.get("expected_cursor_rev")

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return

            meta = app.canvas_manager.layer_update(
                canvas_id=str(canvas_id) if isinstance(canvas_id, str) and canvas_id.strip() else None,
                layer_id=str(layer_id),
                name=(str(payload.get("name")) if isinstance(payload.get("name"), str) else None),
                description=(str(payload.get("description")) if isinstance(payload.get("description"), str) else None),
                clear_description=(bool(payload.get("clear_description")) if ("clear_description" in payload and payload.get("clear_description") is not None) else None),
                visible=(bool(payload.get("visible")) if ("visible" in payload and payload.get("visible") is not None) else None),
                opacity=(float(payload.get("opacity")) if ("opacity" in payload and payload.get("opacity") is not None) else None),
                move_to_index=(int(payload.get("move_to_index")) if ("move_to_index" in payload and payload.get("move_to_index") is not None) else None),
                set_active=(bool(payload.get("set_active")) if ("set_active" in payload and payload.get("set_active") is not None) else None),
                expected_cursor_rev=int(expected) if expected is not None else None,
                actor="user",
            )
            app._bus_reply(reply_topic, {"status": "success", "canvas": meta})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()


def bus_canvas_layer_delete(app: Any, event) -> None:
    payload = getattr(event, "payload", {}) or {}
    reply_topic = payload.get("reply_topic")
    if not reply_topic:
        return

    canvas_id = payload.get("canvas_id")
    layer_id = payload.get("layer_id")
    expected = payload.get("expected_cursor_rev")

    if not isinstance(layer_id, str) or not layer_id.strip():
        app._bus_reply(reply_topic, {"status": "error", "message": "layer_id is required"})
        return

    def work():
        try:
            if not getattr(app, "canvas_manager", None):
                app._bus_reply(reply_topic, {"status": "error", "message": "CanvasManager not initialized"})
                return

            meta = app.canvas_manager.layer_delete(
                canvas_id=str(canvas_id) if isinstance(canvas_id, str) and canvas_id.strip() else None,
                layer_id=str(layer_id),
                expected_cursor_rev=int(expected) if expected is not None else None,
                actor="user",
            )
            app._bus_reply(reply_topic, {"status": "success", "canvas": meta})
        except Exception as e:
            app._bus_reply(reply_topic, {"status": "error", "message": str(e)})

    threading.Thread(target=work, daemon=True).start()
