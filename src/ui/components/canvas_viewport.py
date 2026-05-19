"""Canvas viewport widget (zoom + pan + live stroke preview).

This widget is UI-only:
- It renders the current canvas image.
- It provides responsive local stroke preview.
- On stroke release, it calls `on_stroke_finished(points)` with points in canvas coords.

Persistence happens elsewhere (CanvasManager via bus).
"""

from __future__ import annotations

import base64
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QWheelEvent, QBrush, QPixmap, QPolygonF
from PyQt6.QtWidgets import QWidget


class CanvasViewport(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        # Render buffers (canvas-space)
        self._image: Optional[QImage] = None
        self._overlay: Optional[QImage] = None  # fully opaque strokes; composited with opacity factor

        self._status_text: str = "(no canvas)"

        # View transform
        self._zoom: float = 1.0
        self._pan: QPointF = QPointF(0.0, 0.0)  # view pixels
        self._manual_view: bool = False

        self._panning: bool = False
        self._pan_start: QPointF = QPointF(0.0, 0.0)
        self._drag_start: QPointF = QPointF(0.0, 0.0)

        # Brush (canvas-space)
        self._brush_radius: int = 12
        self._brush_rgba: Tuple[int, int, int, int] = (0, 0, 0, 255)
        self._brush_opacity: float = 1.0

        # Stroke capture

        # Canvas background (for eraser preview). Default: white.
        self._canvas_background_rgba: Tuple[int, int, int, int] = (255, 255, 255, 255)

        # Transparency/layers mode:
        # - show a checkerboard behind the rendered PNG
        # - preview eraser as alpha-erase (DestinationOut) rather than painting the background
        self._transparency_mode: bool = False
        self._checker_brush: Optional[QBrush] = None

        # Brush cursor (drawn inside the canvas area).
        # We hide the OS cursor (BlankCursor) only while hovering over the drawable image.
        self._hover_view_pos: Optional[QPointF] = None
        self._cursor_blank: bool = False
        # Future-proofing: different brush types may want different cursor shapes.
        self._brush_type: str = "round"
        self._drawing: bool = False

        # Interaction mode: 'draw' (default) or 'eyedropper'
        self._interaction_mode: str = "draw"

        # Pixel art mode (UI-only hints; backend owns semantics).
        # When enabled, the viewport:
        # - renders with crisp scaling (no smoothing)
        # - previews strokes/lines as cell-rect stamping (no AA)
        # - can draw an optional grid overlay (never baked into snapshots/export)
        self._pixel_art_enabled: bool = False
        self._pixel_show_grid: bool = True

        # Callback for eyedropper: (x: float, y: float) -> None
        self.on_pick_color: Optional[Callable[[float, float], None]] = None
        self._points: List[Dict[str, float]] = []
        self._last_canvas_pt: Optional[QPointF] = None

        # Callbacks
        self.on_stroke_finished: Optional[Callable[[List[Dict[str, float]]], None]] = None
        self.on_line_finished: Optional[Callable[[float, float, float, float], None]] = None
        self.on_shape_finished: Optional[Callable[[str, float, float, float, float, bool], None]] = None
        self.on_fill_clicked: Optional[Callable[[float, float], None]] = None

        # Drag-and-drop import callback: paths -> None
        self.on_drop_files: Optional[Callable[[List[str]], None]] = None

        # Import overlay state (Phase 2)
        self._import_active: bool = False
        self._import_crop_mode: bool = False
        self._import_img: Optional[QImage] = None  # source image (not canvas-sized)
        self._import_dest_rect: Optional[QRectF] = None  # canvas coords (x,y,w,h)
        self._import_crop_rect: Optional[QRectF] = None  # source pixel coords (l,t,w,h)
        self._import_rotation_deg: float = 0.0
        self._import_opacity: float = 1.0
        self._import_lock_ratio: bool = False
        self._import_drag_start_ratio: float = 1.0

        self._import_dragging: bool = False
        self._import_drag_kind: str = ""  # move|resize|rotate|crop
        self._import_drag_handle: str = ""  # e.g. 'tl','r','b'...
        self._import_drag_start_canvas: Optional[QPointF] = None
        self._import_drag_start_dest: Optional[QRectF] = None
        self._import_drag_start_crop: Optional[QRectF] = None
        self._import_drag_start_rot: float = 0.0
        self._import_drag_start_angle: float = 0.0

        try:
            self.setAcceptDrops(True)
        except Exception:
            pass

        # Shape tool state (UI reuses the old 'line' interaction mode)
        self._shape_kind: str = "line"  # line|rect|ellipse
        self._shape_filled: bool = False
        self._line_drawing: bool = False
        self._line_start: Optional[QPointF] = None
        self._line_end: Optional[QPointF] = None
        self._line_snapped: bool = False

        # Styling
        self.setStyleSheet("background-color: #111111; border: 1px solid #333; border-radius: 8px;")

    # -----------------------------
    # Public API
    # -----------------------------

    def set_status_text(self, text: str) -> None:
        self._status_text = str(text or "")
        self.update()

    def reset_view(self) -> None:
        self._manual_view = False
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.update()

    def set_pixel_art_mode(self, enabled: bool) -> None:
        """Enable/disable pixel-art rendering + previews."""
        self._pixel_art_enabled = bool(enabled)
        self.update()

    def set_show_grid(self, enabled: bool) -> None:
        """Toggle the pixel-art grid overlay (UI-only)."""
        self._pixel_show_grid = bool(enabled)
        self.update()

    def set_canvas_background_rgba(self, rgba: Optional[List[int]]) -> None:
        """Set the canvas background color for correct eraser preview.

        The stored canvas snapshots are currently flattened against the background,
        so the eraser previews by painting with this background color.
        """
        try:
            if isinstance(rgba, list) and len(rgba) == 4:
                r, g, b, a = [int(x) for x in rgba]
                self._canvas_background_rgba = (
                    max(0, min(255, r)),
                    max(0, min(255, g)),
                    max(0, min(255, b)),
                    max(0, min(255, a)),
                )
        except Exception:
            pass
        self.update()

    def set_transparency_mode(self, enabled: bool) -> None:
        """Enable transparency/layers mode rendering (checkerboard + alpha-erase preview)."""
        self._transparency_mode = bool(enabled)
        self.update()

    def _get_checker_brush(self) -> QBrush:
        if self._checker_brush is not None:
            return self._checker_brush

        # 16px checker tile (view-space; doesn't zoom with the canvas).
        tile = QPixmap(16, 16)
        c1 = QColor(38, 38, 38)
        c2 = QColor(58, 58, 58)
        tile.fill(c1)
        qp = QPainter(tile)
        try:
            qp.fillRect(0, 0, 8, 8, c2)
            qp.fillRect(8, 8, 8, 8, c2)
        finally:
            qp.end()

        self._checker_brush = QBrush(tile)
        return self._checker_brush

    def set_interaction_mode(self, mode: str) -> None:
        """Set viewport interaction mode.

        - 'draw': normal painting (brush cursor + stroke capture)
        - 'eyedropper': click to sample a color; no strokes
        """
        m = str(mode or "").strip().lower() or "draw"
        if m not in ("draw", "eyedropper", "line", "fill", "import"):
            m = "draw"
        self._interaction_mode = m
        # Force cursor recalculation on next move.
        if m != "draw":
            self._set_brush_cursor_active(False)
        self.update()

    def set_shape_kind(self, kind: str) -> None:
        k = str(kind or "").strip().lower()
        if k in ("rectangle", "rect"):
            k = "rect"
        elif k in ("ellipse", "circle"):
            k = "ellipse"
        elif k in ("line",):
            k = "line"
        else:
            k = "line"
        self._shape_kind = str(k)
        # Line cannot be filled.
        if self._shape_kind == "line":
            self._shape_filled = False
        self.update()

    def set_shape_filled(self, filled: bool) -> None:
        self._shape_filled = bool(filled) if str(getattr(self, "_shape_kind", "line")) != "line" else False
        self.update()

    def set_brush(
        self,
        *,
        rgba: Optional[List[int]] = None,
        radius: Optional[int] = None,
        opacity: Optional[float] = None,
        brush_type: Optional[str] = None,
    ) -> None:
        """Update the current brush parameters.

        Note: The viewport draws a brush-cursor preview (currently a circle) when the mouse
        hovers over the drawable canvas area.

        Future-proofing: pass brush_type to support alternate cursor shapes for future brushes.
        """
        try:
            if isinstance(radius, int) and radius > 0:
                self._brush_radius = int(radius)
        except Exception:
            pass

        try:
            if isinstance(opacity, (int, float)):
                self._brush_opacity = max(0.0, min(1.0, float(opacity)))
        except Exception:
            pass

        try:
            if isinstance(rgba, list) and len(rgba) == 4:
                r, g, b, a = [int(x) for x in rgba]
                self._brush_rgba = (
                    max(0, min(255, r)),
                    max(0, min(255, g)),
                    max(0, min(255, b)),
                    max(0, min(255, a)),
                )
        except Exception:
            pass

        try:
            if isinstance(brush_type, str) and brush_type.strip():
                self._brush_type = brush_type.strip()
        except Exception:
            pass

        self.update()

    def _set_brush_cursor_active(self, active: bool) -> None:
        """Set cursor for the current interaction mode."""
        m = str(getattr(self, "_interaction_mode", "draw") or "draw").strip().lower()
        a = bool(active)

        # Interaction tools: always show a normal cursor.
        if m in ("eyedropper", "line", "fill"):
            try:
                self._cursor_blank = False
                if a:
                    self.setCursor(Qt.CursorShape.CrossCursor)
                else:
                    self.setCursor(Qt.CursorShape.ArrowCursor)
            except Exception:
                pass
            return

        # Import overlay: normal arrow cursor.
        if m == "import":
            try:
                self._cursor_blank = False
                self.setCursor(Qt.CursorShape.ArrowCursor)
            except Exception:
                pass
            return

        # Draw mode: hide OS cursor while hovering over drawable canvas and draw our own ring.
        if a == bool(self._cursor_blank):
            return
        self._cursor_blank = a
        try:
            if a:
                self.setCursor(Qt.CursorShape.BlankCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
        except Exception:
            return

    def set_image_b64(self, png_b64: Optional[str]) -> None:
        # New authoritative image arrives -> clear overlay preview.
        self._overlay = None
        self._drawing = False
        self._points = []
        self._last_canvas_pt = None
        self._line_drawing = False
        self._line_start = None
        self._line_end = None
        self._line_snapped = False

        if not png_b64:
            self._image = None
            self._status_text = "(no image)"
            self._overlay = None
            self._hover_view_pos = None
            self._set_brush_cursor_active(False)
            self.update()
            return

        try:
            raw = base64.b64decode(str(png_b64))
        except Exception:
            self._image = None
            self._status_text = "(bad image)"
            self.update()
            return

        img = QImage()
        ok = img.loadFromData(raw, "PNG")
        if not ok or img.isNull():
            self._image = None
            self._status_text = "(bad image)"
            self.update()
            return

        # Normalize format for alpha blending.
        try:
            img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        except Exception:
            pass

        self._image = img
        self._status_text = ""
        self.update()

    # -----------------------------
    # Transform helpers
    # -----------------------------

    def _fit_scale(self) -> float:
        if self._image is None:
            return 1.0
        iw = max(1, int(self._image.width()))
        ih = max(1, int(self._image.height()))
        vw = max(1, int(self.width() - 10))
        vh = max(1, int(self.height() - 10))
        return max(0.01, min(vw / iw, vh / ih))

    def _scale(self) -> float:
        fs = self._fit_scale()
        z = float(self._zoom) if self._manual_view else 1.0
        return max(0.01, fs * z)

    def _top_left(self, scale: float) -> QPointF:
        if self._image is None:
            return QPointF(0.0, 0.0)
        iw = float(self._image.width())
        ih = float(self._image.height())
        vw = float(self.width())
        vh = float(self.height())
        x0 = (vw - iw * scale) / 2.0
        y0 = (vh - ih * scale) / 2.0
        if self._manual_view:
            x0 += float(self._pan.x())
            y0 += float(self._pan.y())
        return QPointF(x0, y0)

    def view_to_canvas(self, p: QPointF) -> Optional[QPointF]:
        """Map a view-space point to canvas-space.

        Returns None if the point is outside the drawable canvas bounds.
        """
        if self._image is None:
            return None
        s = self._scale()
        tl = self._top_left(s)
        x = (p.x() - tl.x()) / s
        y = (p.y() - tl.y()) / s
        if x < 0 or y < 0 or x > self._image.width() or y > self._image.height():
            return None
        return QPointF(float(x), float(y))

    def view_to_canvas_unbounded(self, p: QPointF) -> Optional[QPointF]:
        """Map a view-space point to canvas-space without bounds checks.

        This is used for import-overlay handles: we want to allow dragging/rotating/resizing
        even when the handle is outside the visible canvas bounds.
        """
        if self._image is None:
            return None
        s = self._scale()
        tl = self._top_left(s)
        x = (p.x() - tl.x()) / s
        y = (p.y() - tl.y()) / s
        return QPointF(float(x), float(y))

    # -----------------------------
    # Import overlay (Phase 2)
    # -----------------------------

    def clear_import_object(self) -> None:
        self._import_active = False
        self._import_crop_mode = False
        self._import_img = None
        self._import_dest_rect = None
        self._import_crop_rect = None
        self._import_rotation_deg = 0.0
        self._import_opacity = 1.0
        self._import_dragging = False
        self._import_drag_kind = ""
        self._import_drag_handle = ""
        self._import_drag_start_canvas = None
        self._import_drag_start_dest = None
        self._import_drag_start_crop = None
        self.update()

    def set_import_object(
        self,
        img: QImage,
        *,
        dest_rect: Tuple[float, float, float, float],
        crop_rect: Tuple[int, int, int, int],
        rotation_deg: float = 0.0,
        opacity: float = 1.0,
    ) -> None:
        """Set the pending import object (UI-only)."""
        try:
            if img.isNull():
                raise RuntimeError("null image")
            self._import_img = img
            x, y, w, h = dest_rect
            self._import_dest_rect = QRectF(float(x), float(y), float(w), float(h))
            l, t, rw, rh = crop_rect
            self._import_crop_rect = QRectF(float(l), float(t), float(rw), float(rh))
            self._import_rotation_deg = float(rotation_deg or 0.0)
            self._import_opacity = max(0.0, min(1.0, float(opacity)))
            self._import_active = True
            self._import_crop_mode = False
            self._import_dragging = False
        except Exception:
            self.clear_import_object()
            raise
        self.update()

    def set_import_crop_mode(self, enabled: bool) -> None:
        self._import_crop_mode = bool(enabled)
        self.update()

    def set_import_opacity(self, opacity: float) -> None:
        try:
            self._import_opacity = max(0.0, min(1.0, float(opacity)))
        except Exception:
            self._import_opacity = 1.0
        self.update()

    def set_import_lock_ratio(self, enabled: bool) -> None:
        self._import_lock_ratio = bool(enabled)
        self.update()

    def get_import_state(self) -> Dict[str, Any]:
        if not bool(self._import_active) or self._import_img is None or self._import_dest_rect is None or self._import_crop_rect is None:
            return {}

        dr = self._import_dest_rect
        cr = self._import_crop_rect

        # Crop rect is stored as (l,t,w,h) in source pixels; tool wants (l,t,r,b).
        l = int(round(float(cr.x())))
        t = int(round(float(cr.y())))
        r = int(round(float(cr.x() + cr.width())))
        b = int(round(float(cr.y() + cr.height())))

        return {
            "dest_rect": {"x": float(dr.x()), "y": float(dr.y()), "w": float(dr.width()), "h": float(dr.height())},
            "crop_rect": {"l": int(l), "t": int(t), "r": int(r), "b": int(b)},
            "rotation_deg": float(self._import_rotation_deg),
            "opacity": float(self._import_opacity),
        }

    def _import_center(self) -> QPointF:
        dr = self._import_dest_rect
        if dr is None:
            return QPointF(0.0, 0.0)
        return QPointF(float(dr.x() + dr.width() / 2.0), float(dr.y() + dr.height() / 2.0))

    def _rotate_xy(self, x: float, y: float, deg: float) -> Tuple[float, float]:
        th = math.radians(float(deg))
        c = math.cos(th)
        s = math.sin(th)
        return (float(x * c - y * s), float(x * s + y * c))

    def _to_local(self, p_canvas: QPointF) -> QPointF:
        """Convert a canvas point into import-local coords (rotation removed, origin at object center)."""
        c = self._import_center()
        dx = float(p_canvas.x() - c.x())
        dy = float(p_canvas.y() - c.y())
        lx, ly = self._rotate_xy(dx, dy, -float(self._import_rotation_deg))
        return QPointF(float(lx), float(ly))

    def _handle_size_canvas(self) -> float:
        s = max(0.01, float(self._scale()))
        return max(2.5, 7.0 / s)

    def _rotation_handle_pos_local(self) -> QPointF:
        dr = self._import_dest_rect
        if dr is None:
            return QPointF(0.0, 0.0)
        off = 22.0 / max(0.01, float(self._scale()))
        return QPointF(0.0, -float(dr.height()) / 2.0 - float(off))

    def _import_hit_test(self, p_local: QPointF) -> Tuple[str, str]:
        """Return (kind, handle) where kind is move|resize|rotate|crop."""
        if not bool(self._import_active) or self._import_dest_rect is None or self._import_crop_rect is None or self._import_img is None:
            return ("", "")

        w = float(self._import_dest_rect.width())
        h = float(self._import_dest_rect.height())
        hs = float(self._handle_size_canvas())
        tol = float(hs) * 1.2

        x = float(p_local.x())
        y = float(p_local.y())

        # Crop mode hit-testing
        # In crop mode, we reuse the *outer* transform handles (same as resize), but the
        # semantics are trim (dest shrinks) rather than scale.
        if bool(self._import_crop_mode):
            corners2 = {
                "tl": (-w / 2.0, -h / 2.0),
                "tr": (+w / 2.0, -h / 2.0),
                "bl": (-w / 2.0, +h / 2.0),
                "br": (+w / 2.0, +h / 2.0),
            }
            for name, (hx, hy) in corners2.items():
                if abs(x - hx) <= tol and abs(y - hy) <= tol:
                    return ("crop", name)

            if abs(y - (-h / 2.0)) <= tol and (-w / 2.0 - tol) <= x <= (w / 2.0 + tol):
                return ("crop", "t")
            if abs(y - (h / 2.0)) <= tol and (-w / 2.0 - tol) <= x <= (w / 2.0 + tol):
                return ("crop", "b")
            if abs(x - (-w / 2.0)) <= tol and (-h / 2.0 - tol) <= y <= (h / 2.0 + tol):
                return ("crop", "l")
            if abs(x - (w / 2.0)) <= tol and (-h / 2.0 - tol) <= y <= (h / 2.0 + tol):
                return ("crop", "r")

            # Inside -> move (still allowed)
            if (-w / 2.0) <= x <= (w / 2.0) and (-h / 2.0) <= y <= (h / 2.0):
                return ("move", "")

            return ("", "")

        # Transform mode hit-testing
        # Rotation handle
        rh = self._rotation_handle_pos_local()
        if abs(x - float(rh.x())) <= tol and abs(y - float(rh.y())) <= tol:
            return ("rotate", "")

        # Resize handles
        corners2 = {
            "tl": (-w / 2.0, -h / 2.0),
            "tr": (+w / 2.0, -h / 2.0),
            "bl": (-w / 2.0, +h / 2.0),
            "br": (+w / 2.0, +h / 2.0),
        }
        for name, (hx, hy) in corners2.items():
            if abs(x - hx) <= tol and abs(y - hy) <= tol:
                return ("resize", name)

        # Edges
        if abs(y - (-h / 2.0)) <= tol and (-w / 2.0 - tol) <= x <= (w / 2.0 + tol):
            return ("resize", "t")
        if abs(y - (h / 2.0)) <= tol and (-w / 2.0 - tol) <= x <= (w / 2.0 + tol):
            return ("resize", "b")
        if abs(x - (-w / 2.0)) <= tol and (-h / 2.0 - tol) <= y <= (h / 2.0 + tol):
            return ("resize", "l")
        if abs(x - (w / 2.0)) <= tol and (-h / 2.0 - tol) <= y <= (h / 2.0 + tol):
            return ("resize", "r")

        # Inside -> move
        if (-w / 2.0) <= x <= (w / 2.0) and (-h / 2.0) <= y <= (h / 2.0):
            return ("move", "")

        return ("", "")

    # -----------------------------
    # Paint
    # -----------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            # Pixel art wants hard edges everywhere (including grid + cursor overlays).
            aa = not bool(getattr(self, "_pixel_art_enabled", False))
            p.setRenderHint(QPainter.RenderHint.Antialiasing, bool(aa))

            # background
            p.fillRect(self.rect(), QColor(17, 17, 17))

            if self._image is None:
                if self._status_text:
                    p.setPen(QColor(160, 160, 160))
                    p.setFont(QFont("Segoe UI", 10))
                    p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._status_text)
                return

            s = self._scale()
            tl = self._top_left(s)

            # Scaling behavior:
            # - normal: smooth when downscaling
            # - pixel_art: never smooth (avoid blurred pixels)
            try:
                if bool(getattr(self, "_pixel_art_enabled", False)):
                    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                else:
                    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, bool(s < 1.0))
            except Exception:
                pass

            # Transparency indicator (checkerboard behind the image).
            if bool(getattr(self, "_transparency_mode", False)):
                try:
                    dest = QRectF(
                        float(tl.x()),
                        float(tl.y()),
                        float(self._image.width()) * float(s),
                        float(self._image.height()) * float(s),
                    )
                    p.save()
                    p.setClipRect(dest)
                    p.fillRect(dest, self._get_checker_brush())
                    p.restore()
                except Exception:
                    pass

            p.save()
            p.translate(tl)
            p.scale(s, s)

            # base image
            p.drawImage(0, 0, self._image)

            # overlay (opacity applied once)
            if self._overlay is not None:
                bt2 = str(self._brush_type or "").strip().lower()

                if bt2 == "eraser":
                    # Eraser preview uses background color; rgba alpha should not affect strength.
                    factor = max(0.0, min(1.0, float(self._brush_opacity)))
                else:
                    try:
                        rgba_a = float(self._brush_rgba[3]) / 255.0
                    except Exception:
                        rgba_a = 1.0
                    factor = max(0.0, min(1.0, float(self._brush_opacity) * rgba_a))

                p.setOpacity(factor)

                if bt2 == "eraser" and bool(getattr(self, "_transparency_mode", False)):
                    # Alpha-erase preview (matches layered semantics).
                    try:
                        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
                    except Exception:
                        pass
                    p.drawImage(0, 0, self._overlay)
                    try:
                        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                    except Exception:
                        pass

                else:
                    p.drawImage(0, 0, self._overlay)

                p.setOpacity(1.0)

            # Pending import overlay (UI-only until Apply).
            if bool(getattr(self, "_import_active", False)) and self._import_img is not None and self._import_dest_rect is not None and self._import_crop_rect is not None:
                try:
                    dr = self._import_dest_rect
                    cr = self._import_crop_rect
                    img = self._import_img

                    # Clamp crop to image bounds.
                    iw = int(img.width())
                    ih = int(img.height())
                    l = max(0.0, min(float(iw), float(cr.x())))
                    t = max(0.0, min(float(ih), float(cr.y())))
                    r = max(0.0, min(float(iw), float(cr.x() + cr.width())))
                    b = max(0.0, min(float(ih), float(cr.y() + cr.height())))
                    sw = max(1.0, float(r - l))
                    sh = max(1.0, float(b - t))

                    cx = float(dr.x() + dr.width() / 2.0)
                    cy = float(dr.y() + dr.height() / 2.0)
                    w = float(dr.width())
                    h = float(dr.height())

                    p.save()
                    p.translate(QPointF(cx, cy))
                    p.rotate(float(self._import_rotation_deg))
                    p.setOpacity(max(0.0, min(1.0, float(self._import_opacity))))

                    dst_local = QRectF(-w / 2.0, -h / 2.0, w, h)
                    src = QRectF(float(l), float(t), float(sw), float(sh))
                    p.drawImage(dst_local, img, src)
                    p.setOpacity(1.0)

                    # Outline + handles
                    hs = float(self._handle_size_canvas())
                    pen = QPen(QColor(77, 166, 255, 210))
                    pen.setWidthF(max(0.8, 1.2 / max(0.01, float(s))))
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(dst_local)

                    # Rotation handle (disabled in crop mode)
                    if not bool(getattr(self, "_import_crop_mode", False)):
                        rhp = self._rotation_handle_pos_local()
                        p.setBrush(QColor(77, 166, 255, 160))
                        p.drawEllipse(QPointF(float(rhp.x()), float(rhp.y())), hs * 0.9, hs * 0.9)
                        p.setBrush(Qt.BrushStyle.NoBrush)

                    # Outer handles (resize OR crop-trim use the same geometry)
                    pts = [
                        ("tl", QPointF(-w / 2.0, -h / 2.0)),
                        ("tr", QPointF(+w / 2.0, -h / 2.0)),
                        ("bl", QPointF(-w / 2.0, +h / 2.0)),
                        ("br", QPointF(+w / 2.0, +h / 2.0)),
                        ("t", QPointF(0.0, -h / 2.0)),
                        ("b", QPointF(0.0, +h / 2.0)),
                        ("l", QPointF(-w / 2.0, 0.0)),
                        ("r", QPointF(+w / 2.0, 0.0)),
                    ]
                    p.setBrush(QColor(17, 17, 17, 230))
                    for _name, pt in pts:
                        p.drawRect(QRectF(float(pt.x()) - hs / 2.0, float(pt.y()) - hs / 2.0, hs, hs))
                    p.setBrush(Qt.BrushStyle.NoBrush)

                    p.restore()
                except Exception:
                    # Don't let overlay rendering crash the viewport.
                    try:
                        p.restore()
                    except Exception:
                        pass

            # Pixel-art grid overlay (UI-only; never baked into snapshots/export).
            if bool(getattr(self, "_pixel_art_enabled", False)) and bool(getattr(self, "_pixel_show_grid", False)):
                try:
                    # In pixel_art model, 1 image pixel == 1 logical cell.
                    cell_view_px = float(s)
                    if cell_view_px >= 4.0:
                        w = int(self._image.width())
                        h = int(self._image.height())
                        p.save()
                        p.setOpacity(0.28)
                        pen = QPen(QColor(255, 255, 255, 90))
                        # Cosmetic pen: stays 1 device pixel regardless of zoom.
                        pen.setWidth(0)
                        p.setPen(pen)
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        for xx in range(0, w + 1):
                            p.drawLine(QPointF(float(xx), 0.0), QPointF(float(xx), float(h)))
                        for yy in range(0, h + 1):
                            p.drawLine(QPointF(0.0, float(yy)), QPointF(float(w), float(yy)))
                        p.restore()
                except Exception:
                    pass

            # Brush cursor preview (draw mode only).
            if (
                str(getattr(self, "_interaction_mode", "draw") or "draw").strip().lower() == "draw"
                and self._hover_view_pos is not None
                and not self._panning
            ):
                cpt = self.view_to_canvas(self._hover_view_pos)
                if cpt is not None:
                    if bool(getattr(self, "_pixel_art_enabled", False)):
                        # Pixel-art cursor: snap to cell and show the square stamp footprint.
                        try:
                            cx_i = int(math.floor(float(cpt.x())))
                            cy_i = int(math.floor(float(cpt.y())))
                        except Exception:
                            cx_i, cy_i = (0, 0)

                        half = max(0, int(self._brush_radius) - 1)
                        side = int(2 * half + 1)

                        bt = str(self._brush_type or "").strip().lower()

                        # Cursor color: brush color (effective alpha) or a neutral eraser outline.
                        if bt == "eraser":
                            col = QColor(235, 235, 235, 200)
                        else:
                            try:
                                r, g, b, _aa = self._brush_rgba
                            except Exception:
                                r, g, b = (255, 255, 255)
                            try:
                                a_eff = int(max(0, min(255, int(round(float(self._brush_opacity) * float(self._brush_rgba[3]))))))
                            except Exception:
                                a_eff = 200
                            col = QColor(int(r), int(g), int(b), int(a_eff))

                        pen = QPen(col)
                        # Cosmetic pen: 1 device pixel regardless of zoom.
                        pen.setWidth(0)
                        pen.setStyle(Qt.PenStyle.SolidLine)
                        p.setPen(pen)
                        p.setBrush(Qt.BrushStyle.NoBrush)

                        x0 = float(cx_i - half)
                        y0 = float(cy_i - half)
                        p.drawRect(QRectF(x0, y0, float(side), float(side)))

                    else:
                        try:
                            r, g, b, _aa = self._brush_rgba
                        except Exception:
                            r, g, b = (255, 255, 255)

                        # Keep outline thickness visually stable across zoom.
                        w_px = max(1.0, 1.0 / max(0.01, s))
                        br = float(self._brush_radius)

                        # Cursor should match brush color + effective opacity.
                        try:
                            a_eff = int(max(0, min(255, int(round(float(self._brush_opacity) * float(self._brush_rgba[3]))))))
                        except Exception:
                            a_eff = 200

                        col = QColor(int(r), int(g), int(b), int(a_eff))

                        bt = str(self._brush_type or "").strip().lower()

                        if bt == "eraser":
                            # Eraser cursor: outline ring + crosshair (no fill), so you can see what you're erasing.
                            a2 = max(80, min(220, int(a_eff)))
                            col2 = QColor(235, 235, 235, a2)
                            pen = QPen(col2)
                            pen.setWidthF(w_px)
                            pen.setStyle(Qt.PenStyle.SolidLine)
                            p.setPen(pen)
                            p.setBrush(Qt.BrushStyle.NoBrush)
                            p.drawEllipse(QPointF(cpt.x(), cpt.y()), br, br)

                            cx, cy = float(cpt.x()), float(cpt.y())
                            ln = max(6.0, min(14.0, br * 0.65))
                            p.drawLine(QPointF(cx - ln, cy), QPointF(cx + ln, cy))
                            p.drawLine(QPointF(cx, cy - ln), QPointF(cx, cy + ln))

                        elif bt in ("round", "circle", ""):
                            pen = QPen(col)
                            pen.setWidthF(w_px)
                            pen.setStyle(Qt.PenStyle.SolidLine)
                            p.setPen(pen)
                            p.setBrush(col)
                            p.drawEllipse(QPointF(cpt.x(), cpt.y()), br, br)

                        else:
                            # Unknown brush types: fall back to a filled dot + crosshair using same color/opacity.
                            pen = QPen(col)
                            pen.setWidthF(w_px)
                            p.setPen(pen)
                            p.setBrush(col)
                            cx, cy = float(cpt.x()), float(cpt.y())
                            p.drawEllipse(QPointF(cx, cy), max(2.0, br * 0.15), max(2.0, br * 0.15))
                            p.drawLine(QPointF(cx - 8, cy), QPointF(cx + 8, cy))
                            p.drawLine(QPointF(cx, cy - 8), QPointF(cx, cy + 8))

            p.restore()

            # Line tool angle readout (makes Shift-snap visibly meaningful).
            try:
                mode = str(getattr(self, "_interaction_mode", "draw") or "draw").strip().lower()
            except Exception:
                mode = "draw"

            if mode == "line" and bool(getattr(self, "_line_drawing", False)) and str(getattr(self, "_shape_kind", "line") or "line").strip().lower() == "line":
                try:
                    st = getattr(self, "_line_start", None)
                    en = getattr(self, "_line_end", None)
                    if st is not None and en is not None:
                        dx = float(en.x() - st.x())
                        dy = float(en.y() - st.y())
                        if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                            ang = math.degrees(math.atan2(dy, dx))
                            ang = (ang + 360.0) % 360.0
                            snapped = bool(getattr(self, "_line_snapped", False))
                            txt = f"{int(round(ang))}°" + (" (snap)" if snapped else "")

                            p.save()
                            p.setPen(QColor(210, 210, 210))
                            p.setFont(QFont("Segoe UI", 10))
                            p.drawText(14, 22, f"Line: {txt}")
                            p.restore()
                except Exception:
                    pass

            # Import overlay readout (rotation + size) so the user can be precise.
            try:
                if str(mode or "") == "import" and bool(getattr(self, "_import_active", False)) and self._import_dest_rect is not None:
                    dr = self._import_dest_rect
                    w2 = float(dr.width())
                    h2 = float(dr.height())
                    ang2 = float(getattr(self, "_import_rotation_deg", 0.0))
                    # Human-friendly signed degrees (-180..180] so -45° stays -45° (not 315°).
                    ang2 = ((ang2 + 180.0) % 360.0) - 180.0
                    crop_mode = bool(getattr(self, "_import_crop_mode", False))
                    crop_txt = ""
                    if crop_mode and self._import_crop_rect is not None:
                        try:
                            cw = int(round(float(self._import_crop_rect.width())))
                            ch = int(round(float(self._import_crop_rect.height())))
                            crop_txt = f" · crop {cw}×{ch}px"
                        except Exception:
                            crop_txt = " · crop"

                    txt2 = f"{int(round(w2))}×{int(round(h2))} · {int(round(ang2))}°{crop_txt}"

                    p.save()
                    p.setPen(QColor(210, 210, 210))
                    p.setFont(QFont("Segoe UI", 10))
                    p.drawText(14, 42, f"Import: {txt2}")
                    p.restore()
            except Exception:
                pass

        finally:
            p.end()

    # -----------------------------
    # Zoom / pan events
    # -----------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._image is None:
            return

        dy = 0
        try:
            dy = int(event.angleDelta().y())
        except Exception:
            dy = 0
        if dy == 0:
            return

        pos = event.position()
        anchor = self.view_to_canvas(pos)
        if anchor is None:
            return

        # enter manual mode
        self._manual_view = True

        factor = 1.1 if dy > 0 else (1.0 / 1.1)
        new_zoom = max(0.1, min(32.0, float(self._zoom) * factor))
        self._zoom = float(new_zoom)

        # Adjust pan so the anchor stays under the cursor.
        s = self._scale()
        tl = self._top_left(s)  # includes current pan

        # We want: cursor = top_left_no_pan + pan + anchor*s
        # Solve for pan.
        iw = float(self._image.width())
        ih = float(self._image.height())
        vw = float(self.width())
        vh = float(self.height())
        x0_base = (vw - iw * s) / 2.0
        y0_base = (vh - ih * s) / 2.0

        pan_x = pos.x() - x0_base - anchor.x() * s
        pan_y = pos.y() - y0_base - anchor.y() * s
        self._pan = QPointF(float(pan_x), float(pan_y))

        self.update()

    def mouseDoubleClickEvent(self, event):
        # Reset to fit.
        self.reset_view()

    def enterEvent(self, event) -> None:
        try:
            super().enterEvent(event)
        except Exception:
            pass
        self.update()

    def leaveEvent(self, event) -> None:
        try:
            super().leaveEvent(event)
        except Exception:
            pass
        self._hover_view_pos = None
        self._set_brush_cursor_active(False)
        self.update()

    def dragEnterEvent(self, event) -> None:
        try:
            md = event.mimeData()
        except Exception:
            md = None

        ok = False
        try:
            if md is not None and md.hasUrls():
                for u in md.urls():
                    try:
                        p = str(u.toLocalFile() or "").strip()
                    except Exception:
                        p = ""
                    if not p:
                        continue
                    lp = p.lower()
                    if lp.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
                        ok = True
                        break
        except Exception:
            ok = False

        try:
            if ok:
                event.acceptProposedAction()
            else:
                event.ignore()
        except Exception:
            pass

    def dropEvent(self, event) -> None:
        try:
            md = event.mimeData()
        except Exception:
            md = None
        paths: List[str] = []
        try:
            if md is not None and md.hasUrls():
                for u in md.urls():
                    try:
                        p = str(u.toLocalFile() or "").strip()
                    except Exception:
                        p = ""
                    if p:
                        paths.append(p)
        except Exception:
            paths = []

        if paths and callable(getattr(self, "on_drop_files", None)):
            try:
                self.on_drop_files(paths)
            except Exception:
                pass

        try:
            event.acceptProposedAction()
        except Exception:
            pass

    def mousePressEvent(self, event):
        if self._image is None:
            return

        if event.button() == Qt.MouseButton.MiddleButton:
            self._manual_view = True
            self._panning = True
            try:
                self._hover_view_pos = QPointF(event.position())
            except Exception:
                self._hover_view_pos = None
            self._set_brush_cursor_active(False)
            self._drag_start = event.position()
            self._pan_start = QPointF(float(self._pan.x()), float(self._pan.y()))
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        mode = str(getattr(self, "_interaction_mode", "draw") or "draw").strip().lower()

        # Import overlay: begin object manipulation.
        # Important: allow interaction even when handles are outside the canvas bounds.
        if mode == "import" and bool(getattr(self, "_import_active", False)):
            p_canvas = self.view_to_canvas_unbounded(event.position())
            if p_canvas is None:
                return
            try:
                local = self._to_local(QPointF(p_canvas))
                kind, handle = self._import_hit_test(local)
                if not kind:
                    return

                self._import_dragging = True
                self._import_drag_kind = str(kind)
                self._import_drag_handle = str(handle or "")
                self._import_drag_start_canvas = QPointF(p_canvas)
                self._import_drag_start_dest = QRectF(self._import_dest_rect) if self._import_dest_rect is not None else None
                self._import_drag_start_crop = QRectF(self._import_crop_rect) if self._import_crop_rect is not None else None
                self._import_drag_start_rot = float(self._import_rotation_deg)

                # Capture the ratio at drag start ("lock ratio" preserves this).
                if kind in ("resize", "crop") and self._import_drag_start_dest is not None:
                    try:
                        w0 = float(self._import_drag_start_dest.width())
                        h0 = float(self._import_drag_start_dest.height())
                        self._import_drag_start_ratio = float(w0 / h0) if h0 > 1e-6 else 1.0
                    except Exception:
                        self._import_drag_start_ratio = 1.0

                if kind == "rotate":
                    c = self._import_center()
                    dx = float(p_canvas.x() - c.x())
                    dy = float(p_canvas.y() - c.y())
                    self._import_drag_start_angle = float(math.atan2(dy, dx))

                self.update()
            except Exception:
                self._import_dragging = False
            return

        p_canvas = self.view_to_canvas(event.position())
        if p_canvas is None:
            return

        # Eyedropper mode: click samples color; no stroke capture.
        if mode == "eyedropper":
            if callable(self.on_pick_color):
                try:
                    self.on_pick_color(float(p_canvas.x()), float(p_canvas.y()))
                except Exception:
                    pass
            return

        # Fill mode: click floods a region.
        if mode == "fill":
            if callable(self.on_fill_clicked):
                try:
                    self.on_fill_clicked(float(p_canvas.x()), float(p_canvas.y()))
                except Exception:
                    pass
            return

        # Line mode: click-drag a straight segment.
        if mode == "line":
            self._line_drawing = True
            self._line_start = QPointF(p_canvas)
            self._line_end = QPointF(p_canvas)
            self._line_snapped = False

            # Prepare an empty overlay; we draw the preview only on mouse-move.
            if self._overlay is None:
                ov = QImage(self._image.size(), QImage.Format.Format_ARGB32_Premultiplied)
                ov.fill(QColor(0, 0, 0, 0))
                self._overlay = ov
            else:
                self._overlay.fill(QColor(0, 0, 0, 0))

            self.update()
            return

        # Draw mode: start a freehand stroke.
        self._drawing = True

        if self._overlay is None:
            ov = QImage(self._image.size(), QImage.Format.Format_ARGB32_Premultiplied)
            ov.fill(QColor(0, 0, 0, 0))
            self._overlay = ov

        # Pixel-art: snap points to cell centers (reduces jitter + matches backend stamping).
        if bool(getattr(self, "_pixel_art_enabled", False)):
            try:
                cx_i = int(math.floor(float(p_canvas.x())))
                cy_i = int(math.floor(float(p_canvas.y())))
            except Exception:
                cx_i, cy_i = (0, 0)
            pt0 = QPointF(float(cx_i) + 0.5, float(cy_i) + 0.5)
            self._points = [{"x": float(pt0.x()), "y": float(pt0.y())}]
            self._last_canvas_pt = QPointF(pt0)
        else:
            self._points = [{"x": float(p_canvas.x()), "y": float(p_canvas.y())}]
            self._last_canvas_pt = QPointF(p_canvas)

        # draw a dot
        self._draw_overlay_segment(self._last_canvas_pt, self._last_canvas_pt)
        self.update()

    def mouseMoveEvent(self, event):
        # Track hover for brush-cursor preview.
        try:
            self._hover_view_pos = QPointF(event.position())
        except Exception:
            self._hover_view_pos = None

        # While panning, keep the normal cursor.
        if self._panning:
            self._set_brush_cursor_active(False)
            delta = event.position() - self._drag_start
            self._pan = self._pan_start + delta
            self.update()
            return

        in_canvas = False
        try:
            in_canvas = (self._hover_view_pos is not None) and (self.view_to_canvas(self._hover_view_pos) is not None)
        except Exception:
            in_canvas = False
        self._set_brush_cursor_active(bool(in_canvas))

        # Line tool: live preview while dragging.
        try:
            mode = str(getattr(self, "_interaction_mode", "draw") or "draw").strip().lower()
        except Exception:
            mode = "draw"

        # Import overlay: live manipulation.
        if mode == "import" and bool(getattr(self, "_import_active", False)):
            if bool(getattr(self, "_import_dragging", False)):
                p_canvas = self.view_to_canvas_unbounded(event.position())
                if p_canvas is None:
                    return

                try:
                    kind = str(getattr(self, "_import_drag_kind", "") or "")
                    handle = str(getattr(self, "_import_drag_handle", "") or "")
                    start_canvas = getattr(self, "_import_drag_start_canvas", None)
                    start_dest = getattr(self, "_import_drag_start_dest", None)
                    start_crop = getattr(self, "_import_drag_start_crop", None)
                    rot0 = float(getattr(self, "_import_drag_start_rot", 0.0))

                    if start_canvas is None or start_dest is None:
                        return

                    c0 = QPointF(float(start_dest.x() + start_dest.width() / 2.0), float(start_dest.y() + start_dest.height() / 2.0))

                    if kind == "move":
                        dx = float(p_canvas.x() - start_canvas.x())
                        dy = float(p_canvas.y() - start_canvas.y())
                        self._import_dest_rect = QRectF(float(start_dest.x() + dx), float(start_dest.y() + dy), float(start_dest.width()), float(start_dest.height()))

                    elif kind == "rotate":
                        dx = float(p_canvas.x() - c0.x())
                        dy = float(p_canvas.y() - c0.y())
                        ang = float(math.atan2(dy, dx))
                        delta = float(ang - float(getattr(self, "_import_drag_start_angle", 0.0)))
                        # Normalize to [-pi, pi] so dragging across the atan2 wrap boundary doesn't flip direction.
                        delta = float(math.atan2(math.sin(delta), math.cos(delta)))
                        self._import_rotation_deg = float(rot0 + math.degrees(delta))

                    elif kind == "resize":
                        # Work in local object space (rotation removed).
                        self._import_rotation_deg = float(rot0)
                        self._import_dest_rect = QRectF(start_dest)
                        pt_local = self._to_local(QPointF(p_canvas))
                        x = float(pt_local.x())
                        y = float(pt_local.y())
                        w0 = float(start_dest.width())
                        h0 = float(start_dest.height())
                        L0, R0 = (-w0 / 2.0), (w0 / 2.0)
                        T0, B0 = (-h0 / 2.0), (h0 / 2.0)
                        L, R, T, B = (L0, R0, T0, B0)
                        min_w = 1.0
                        min_h = 1.0

                        if handle in ("tl", "bl", "l"):
                            L = min(float(x), float(R0 - min_w))
                        if handle in ("tr", "br", "r"):
                            R = max(float(x), float(L0 + min_w))
                        if handle in ("tl", "tr", "t"):
                            T = min(float(y), float(B0 - min_h))
                        if handle in ("bl", "br", "b"):
                            B = max(float(y), float(T0 + min_h))

                        # Edge-only handles
                        if handle == "l":
                            L = min(float(x), float(R0 - min_w))
                        if handle == "r":
                            R = max(float(x), float(L0 + min_w))
                        if handle == "t":
                            T = min(float(y), float(B0 - min_h))
                        if handle == "b":
                            B = max(float(y), float(T0 + min_h))

                        # Lock ratio (preserve ratio captured at drag start).
                        if bool(getattr(self, "_import_lock_ratio", False)) and str(handle or ""):
                            ratio = float(getattr(self, "_import_drag_start_ratio", 1.0) or 1.0)
                            ratio = max(1e-6, float(ratio))

                            def _fit_corner(w_c: float, h_c: float) -> Tuple[float, float]:
                                w_c = max(min_w, float(w_c))
                                h_c = max(min_h, float(h_c))
                                if (w_c / max(1e-6, h_c)) > ratio:
                                    return (float(h_c * ratio), float(h_c))
                                return (float(w_c), float(w_c / ratio))

                            if handle == "l":
                                w_c = float(R0 - L)
                                w_new, h_new = _fit_corner(w_c, w_c / ratio)
                                L = float(R0 - w_new)
                                T = float(-h_new / 2.0)
                                B = float(+h_new / 2.0)
                            elif handle == "r":
                                w_c = float(R - L0)
                                w_new, h_new = _fit_corner(w_c, w_c / ratio)
                                R = float(L0 + w_new)
                                T = float(-h_new / 2.0)
                                B = float(+h_new / 2.0)
                            elif handle == "t":
                                h_c = float(B0 - T)
                                w_new = float(max(min_w, h_c * ratio))
                                L = float(-w_new / 2.0)
                                R = float(+w_new / 2.0)
                            elif handle == "b":
                                h_c = float(B - T0)
                                w_new = float(max(min_w, h_c * ratio))
                                L = float(-w_new / 2.0)
                                R = float(+w_new / 2.0)
                            elif handle in ("tl", "tr", "bl", "br"):
                                if handle == "tl":
                                    w_c = float(R0 - L)
                                    h_c = float(B0 - T)
                                    w_new, h_new = _fit_corner(w_c, h_c)
                                    L = float(R0 - w_new)
                                    T = float(B0 - h_new)
                                elif handle == "tr":
                                    w_c = float(R - L0)
                                    h_c = float(B0 - T)
                                    w_new, h_new = _fit_corner(w_c, h_c)
                                    R = float(L0 + w_new)
                                    T = float(B0 - h_new)
                                elif handle == "bl":
                                    w_c = float(R0 - L)
                                    h_c = float(B - T0)
                                    w_new, h_new = _fit_corner(w_c, h_c)
                                    L = float(R0 - w_new)
                                    B = float(T0 + h_new)
                                elif handle == "br":
                                    w_c = float(R - L0)
                                    h_c = float(B - T0)
                                    w_new, h_new = _fit_corner(w_c, h_c)
                                    R = float(L0 + w_new)
                                    B = float(T0 + h_new)

                        new_w = float(R - L)
                        new_h = float(B - T)
                        shift_local_x = float((L + R) / 2.0)
                        shift_local_y = float((T + B) / 2.0)
                        sx, sy = self._rotate_xy(shift_local_x, shift_local_y, float(rot0))
                        c_new = QPointF(float(c0.x() + sx), float(c0.y() + sy))
                        self._import_dest_rect = QRectF(float(c_new.x() - new_w / 2.0), float(c_new.y() - new_h / 2.0), float(new_w), float(new_h))

                    elif kind == "crop" and start_crop is not None and self._import_img is not None:
                        # Crop = trim (not zoom).
                        # We reuse the outer handles (same geometry as resize) and update BOTH:
                        # - dest_rect (shrinks/moves)
                        # - crop_rect (in source pixels) so pixel scale stays constant.
                        self._import_rotation_deg = float(rot0)

                        pt_local = self._to_local(QPointF(p_canvas))
                        x = float(pt_local.x())
                        y = float(pt_local.y())

                        w0 = float(start_dest.width())
                        h0 = float(start_dest.height())
                        L0, R0 = (-w0 / 2.0), (w0 / 2.0)
                        T0, B0 = (-h0 / 2.0), (h0 / 2.0)
                        L, R, T, B = (L0, R0, T0, B0)
                        min_w = 1.0
                        min_h = 1.0

                        if handle in ("tl", "bl", "l"):
                            L = min(float(x), float(R0 - min_w))
                        if handle in ("tr", "br", "r"):
                            R = max(float(x), float(L0 + min_w))
                        if handle in ("tl", "tr", "t"):
                            T = min(float(y), float(B0 - min_h))
                        if handle in ("bl", "br", "b"):
                            B = max(float(y), float(T0 + min_h))

                        if handle == "l":
                            L = min(float(x), float(R0 - min_w))
                        if handle == "r":
                            R = max(float(x), float(L0 + min_w))
                        if handle == "t":
                            T = min(float(y), float(B0 - min_h))
                        if handle == "b":
                            B = max(float(y), float(T0 + min_h))

                        # Lock ratio (preserve ratio captured at drag start).
                        if bool(getattr(self, "_import_lock_ratio", False)) and str(handle or ""):
                            ratio = float(getattr(self, "_import_drag_start_ratio", 1.0) or 1.0)
                            ratio = max(1e-6, float(ratio))

                            def _fit_corner(w_c: float, h_c: float) -> Tuple[float, float]:
                                w_c = max(min_w, float(w_c))
                                h_c = max(min_h, float(h_c))
                                if (w_c / max(1e-6, h_c)) > ratio:
                                    return (float(h_c * ratio), float(h_c))
                                return (float(w_c), float(w_c / ratio))

                            if handle == "l":
                                w_c = float(R0 - L)
                                w_new, h_new = _fit_corner(w_c, w_c / ratio)
                                L = float(R0 - w_new)
                                T = float(-h_new / 2.0)
                                B = float(+h_new / 2.0)
                            elif handle == "r":
                                w_c = float(R - L0)
                                w_new, h_new = _fit_corner(w_c, w_c / ratio)
                                R = float(L0 + w_new)
                                T = float(-h_new / 2.0)
                                B = float(+h_new / 2.0)
                            elif handle == "t":
                                h_c = float(B0 - T)
                                w_new = float(max(min_w, h_c * ratio))
                                L = float(-w_new / 2.0)
                                R = float(+w_new / 2.0)
                            elif handle == "b":
                                h_c = float(B - T0)
                                w_new = float(max(min_w, h_c * ratio))
                                L = float(-w_new / 2.0)
                                R = float(+w_new / 2.0)
                            elif handle in ("tl", "tr", "bl", "br"):
                                if handle == "tl":
                                    w_c = float(R0 - L)
                                    h_c = float(B0 - T)
                                    w_new, h_new = _fit_corner(w_c, h_c)
                                    L = float(R0 - w_new)
                                    T = float(B0 - h_new)
                                elif handle == "tr":
                                    w_c = float(R - L0)
                                    h_c = float(B0 - T)
                                    w_new, h_new = _fit_corner(w_c, h_c)
                                    R = float(L0 + w_new)
                                    T = float(B0 - h_new)
                                elif handle == "bl":
                                    w_c = float(R0 - L)
                                    h_c = float(B - T0)
                                    w_new, h_new = _fit_corner(w_c, h_c)
                                    L = float(R0 - w_new)
                                    B = float(T0 + h_new)
                                elif handle == "br":
                                    w_c = float(R - L0)
                                    h_c = float(B - T0)
                                    w_new, h_new = _fit_corner(w_c, h_c)
                                    R = float(L0 + w_new)
                                    B = float(T0 + h_new)

                        new_w = float(R - L)
                        new_h = float(B - T)
                        shift_local_x = float((L + R) / 2.0)
                        shift_local_y = float((T + B) / 2.0)
                        sx, sy = self._rotate_xy(shift_local_x, shift_local_y, float(rot0))
                        c_new = QPointF(float(c0.x() + sx), float(c0.y() + sy))
                        self._import_dest_rect = QRectF(float(c_new.x() - new_w / 2.0), float(c_new.y() - new_h / 2.0), float(new_w), float(new_h))

                        # Trim deltas in local dest coords (canvas units)
                        trim_left = float(L - L0)
                        trim_right = float(R0 - R)
                        trim_top = float(T - T0)
                        trim_bottom = float(B0 - B)

                        # Convert dest-units -> source pixels based on start scale.
                        try:
                            px_per_cx = float(start_crop.width()) / max(1.0, float(start_dest.width()))
                            px_per_cy = float(start_crop.height()) / max(1.0, float(start_dest.height()))
                        except Exception:
                            px_per_cx = 1.0
                            px_per_cy = 1.0

                        l0 = float(start_crop.x())
                        t0 = float(start_crop.y())
                        r0 = float(start_crop.x() + start_crop.width())
                        b0 = float(start_crop.y() + start_crop.height())

                        l = float(l0 + trim_left * px_per_cx)
                        r = float(r0 - trim_right * px_per_cx)
                        t = float(t0 + trim_top * px_per_cy)
                        b = float(b0 - trim_bottom * px_per_cy)

                        # Clamp to image bounds
                        iw = float(self._import_img.width())
                        ih = float(self._import_img.height())
                        l = max(0.0, min(float(iw - 1.0), l))
                        t = max(0.0, min(float(ih - 1.0), t))
                        r = max(l + 1.0, min(float(iw), r))
                        b = max(t + 1.0, min(float(ih), b))

                        self._import_crop_rect = QRectF(float(l), float(t), float(r - l), float(b - t))

                except Exception:
                    pass

                self.update()
                return

            # Not dragging: keep repainting so hover handles are responsive.
            self.update()
            return

        if bool(getattr(self, "_line_drawing", False)) and mode == "line":
            p_canvas = self.view_to_canvas(event.position())
            if p_canvas is None:
                return

            start = getattr(self, "_line_start", None)
            if start is None:
                return

            end = QPointF(p_canvas)

            kind = str(getattr(self, "_shape_kind", "line") or "line").strip().lower()
            used_shift = False

            if kind == "line":
                snapped = False
                try:
                    if bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                        end = self._snap_to_45(start, end)
                        snapped = True
                except Exception:
                    snapped = False
                self._line_snapped = bool(snapped)

            else:
                # Rect/Ellipse: Shift locks aspect ratio (square/circle).
                try:
                    if bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                        dx = float(end.x() - start.x())
                        dy = float(end.y() - start.y())
                        d = max(abs(dx), abs(dy))
                        sx = 1.0 if dx >= 0.0 else -1.0
                        sy = 1.0 if dy >= 0.0 else -1.0
                        end = QPointF(float(start.x() + sx * d), float(start.y() + sy * d))
                        used_shift = True
                except Exception:
                    used_shift = False
                self._line_snapped = bool(used_shift)

            self._line_end = QPointF(end)
            if self._overlay is not None:
                self._overlay.fill(QColor(0, 0, 0, 0))
                if kind == "line":
                    self._draw_line_overlay(start, end)
                else:
                    self._draw_shape_overlay(kind, start, end, bool(getattr(self, "_shape_filled", False)))
            self.update()
            return

        # Not drawing: just repaint so the cursor ring follows the mouse.
        if not self._drawing:
            if in_canvas:
                self.update()
            return

        p_canvas = self.view_to_canvas(event.position())
        if p_canvas is None:
            return

        # Pixel-art: only record/draw when we enter a new cell.
        if bool(getattr(self, "_pixel_art_enabled", False)):
            try:
                cx_i = int(math.floor(float(p_canvas.x())))
                cy_i = int(math.floor(float(p_canvas.y())))
            except Exception:
                cx_i, cy_i = (0, 0)

            pt = QPointF(float(cx_i) + 0.5, float(cy_i) + 0.5)

            last = self._last_canvas_pt
            if last is not None:
                try:
                    lx = int(math.floor(float(last.x())))
                    ly = int(math.floor(float(last.y())))
                except Exception:
                    lx, ly = (cx_i, cy_i)
                if int(lx) == int(cx_i) and int(ly) == int(cy_i):
                    return

            self._points.append({"x": float(pt.x()), "y": float(pt.y())})
            if self._last_canvas_pt is not None:
                self._draw_overlay_segment(self._last_canvas_pt, pt)
            self._last_canvas_pt = QPointF(pt)
            self.update()
            return

        # Normal mode: record full-fidelity points.
        last = self._last_canvas_pt
        if last is not None:
            dx = p_canvas.x() - last.x()
            dy = p_canvas.y() - last.y()
            if (dx * dx + dy * dy) < 0.5:
                return

        self._points.append({"x": float(p_canvas.x()), "y": float(p_canvas.y())})

        if self._last_canvas_pt is not None:
            self._draw_overlay_segment(self._last_canvas_pt, p_canvas)
        self._last_canvas_pt = QPointF(p_canvas)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            # Restore brush cursor if still hovering over drawable area.
            in_canvas = False
            try:
                if self._hover_view_pos is not None and self._image is not None:
                    in_canvas = self.view_to_canvas(self._hover_view_pos) is not None
            except Exception:
                in_canvas = False
            self._set_brush_cursor_active(bool(in_canvas))
            self.update()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        # Line tool: commit the segment on release.
        try:
            mode = str(getattr(self, "_interaction_mode", "draw") or "draw").strip().lower()
        except Exception:
            mode = "draw"

        # Import overlay: end drag.
        if mode == "import" and bool(getattr(self, "_import_active", False)):
            try:
                self._import_dragging = False
                self._import_drag_kind = ""
                self._import_drag_handle = ""
                self._import_drag_start_canvas = None
                self._import_drag_start_dest = None
                self._import_drag_start_crop = None
            except Exception:
                pass
            self.update()
            return

        if bool(getattr(self, "_line_drawing", False)) and mode == "line":
            self._line_drawing = False
            start = getattr(self, "_line_start", None)
            # Prefer the last drag-updated endpoint (so Shift-snap works even if Shift isn't held at release).
            end = getattr(self, "_line_end", None)
            if end is None:
                try:
                    end = self.view_to_canvas(event.position())
                except Exception:
                    end = None
            if start is None or end is None:
                self._line_start = None
                self._line_end = None
                self._line_snapped = False
                return

            end2 = QPointF(end)
            kind = str(getattr(self, "_shape_kind", "line") or "line").strip().lower()

            used_shift = False
            if kind == "line":
                snapped = False
                try:
                    if bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                        end2 = self._snap_to_45(start, end2)
                        snapped = True
                except Exception:
                    snapped = False
                self._line_snapped = bool(snapped)
            else:
                try:
                    if bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                        dx = float(end2.x() - start.x())
                        dy = float(end2.y() - start.y())
                        d = max(abs(dx), abs(dy))
                        sx = 1.0 if dx >= 0.0 else -1.0
                        sy = 1.0 if dy >= 0.0 else -1.0
                        end2 = QPointF(float(start.x() + sx * d), float(start.y() + sy * d))
                        used_shift = True
                except Exception:
                    used_shift = False
                self._line_snapped = bool(used_shift)

            # If this was a click (no drag), do nothing.
            try:
                dx = float(end2.x() - start.x())
                dy = float(end2.y() - start.y())
                if (dx * dx + dy * dy) < 1.0:
                    self._line_start = None
                    self._line_end = None
                    self._line_snapped = False
                    if self._overlay is not None:
                        self._overlay.fill(QColor(0, 0, 0, 0))
                    self.update()
                    return
            except Exception:
                pass

            self._line_end = QPointF(end2)

            if kind == "line":
                if callable(self.on_line_finished):
                    try:
                        self.on_line_finished(float(start.x()), float(start.y()), float(end2.x()), float(end2.y()))
                    except Exception:
                        pass
            else:
                if callable(getattr(self, "on_shape_finished", None)):
                    try:
                        self.on_shape_finished(str(kind), float(start.x()), float(start.y()), float(end2.x()), float(end2.y()), bool(getattr(self, "_shape_filled", False)))
                    except Exception:
                        pass

            return

        if not self._drawing:
            return

        self._drawing = False
        pts = list(self._points)
        self._points = []
        self._last_canvas_pt = None

        # Keep overlay visible; it will be cleared when a new authoritative image arrives.
        if callable(self.on_stroke_finished) and pts:
            try:
                self.on_stroke_finished(pts)
            except Exception:
                pass

    # -----------------------------
    # Overlay drawing
    # -----------------------------

    def _snap_to_45(self, start: QPointF, end: QPointF) -> QPointF:
        """Snap end point to the nearest 45-degree angle from start."""
        dx = float(end.x() - start.x())
        dy = float(end.y() - start.y())
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return QPointF(end)

        ang = math.atan2(dy, dx)
        step = math.pi / 4.0
        snapped = round(ang / step) * step
        dist = (dx * dx + dy * dy) ** 0.5
        return QPointF(float(start.x() + math.cos(snapped) * dist), float(start.y() + math.sin(snapped) * dist))

    def _draw_line_overlay(self, a: QPointF, b: QPointF) -> None:
        if self._overlay is None:
            return

        # Pixel-art: preview using the same polygon-mask strategy as backend.
        if bool(getattr(self, "_pixel_art_enabled", False)):
            self._draw_pixel_overlay_line(a, b)
            return

        # Normal mode: Line tool uses round brush color + thickness.
        try:
            r, g, bb, _aa = self._brush_rgba
        except Exception:
            r, g, bb = (0, 0, 0)
        pen = QPen(QColor(int(r), int(g), int(bb), 255))
        pen.setWidthF(max(1.0, float(self._brush_radius) * 2.0))
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)

        qp = QPainter(self._overlay)
        try:
            qp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            qp.setPen(pen)
            qp.drawLine(a, b)
        finally:
            qp.end()

    def _draw_shape_overlay(self, kind: str, a: QPointF, b: QPointF, filled: bool) -> None:
        if self._overlay is None:
            return

        k = str(kind or "").strip().lower()
        if k in ("rectangle", "rect"):
            k = "rect"
        elif k in ("ellipse", "circle"):
            k = "ellipse"
        else:
            k = "rect"

        # Pixel-art: preview in cell space (no AA).
        if bool(getattr(self, "_pixel_art_enabled", False)):
            try:
                x0 = int(min(math.floor(float(a.x())), math.floor(float(b.x()))))
                y0 = int(min(math.floor(float(a.y())), math.floor(float(b.y()))))
                x1 = int(max(math.floor(float(a.x())), math.floor(float(b.x()))))
                y1 = int(max(math.floor(float(a.y())), math.floor(float(b.y()))))
            except Exception:
                x0 = y0 = x1 = y1 = 0

            ow = int(self._overlay.width())
            oh = int(self._overlay.height())
            x0 = max(0, min(int(ow) - 1, int(x0)))
            x1 = max(0, min(int(ow) - 1, int(x1)))
            y0 = max(0, min(int(oh) - 1, int(y0)))
            y1 = max(0, min(int(oh) - 1, int(y1)))

            half = max(0, int(self._brush_radius) - 1)
            t = max(1, int(half) * 2 + 1)
            col = self._pixel_overlay_color()

            qp = QPainter(self._overlay)
            try:
                qp.setRenderHint(QPainter.RenderHint.Antialiasing, False)

                if k == "rect":
                    for yy in range(int(y0), int(y1) + 1):
                        for xx in range(int(x0), int(x1) + 1):
                            if not bool(filled):
                                if (int(xx) - int(x0)) >= t and (int(x1) - int(xx)) >= t and (int(yy) - int(y0)) >= t and (int(y1) - int(yy)) >= t:
                                    continue
                            qp.fillRect(int(xx), int(yy), 1, 1, col)

                else:
                    cx = (float(x0) + float(x1) + 1.0) / 2.0
                    cy = (float(y0) + float(y1) + 1.0) / 2.0
                    rx = max(1e-6, (float(x1) - float(x0) + 1.0) / 2.0)
                    ry = max(1e-6, (float(y1) - float(y0) + 1.0) / 2.0)
                    rx2 = float(rx - float(t))
                    ry2 = float(ry - float(t))

                    for yy in range(int(y0), int(y1) + 1):
                        for xx in range(int(x0), int(x1) + 1):
                            x = float(xx) + 0.5
                            y = float(yy) + 0.5
                            dx = (x - cx) / rx
                            dy = (y - cy) / ry
                            if (dx * dx + dy * dy) > 1.0:
                                continue

                            if not bool(filled) and rx2 > 1e-6 and ry2 > 1e-6:
                                dx2 = (x - cx) / rx2
                                dy2 = (y - cy) / ry2
                                if (dx2 * dx2 + dy2 * dy2) <= 1.0:
                                    continue

                            qp.fillRect(int(xx), int(yy), 1, 1, col)

            finally:
                qp.end()

            return

        # Normal mode: vector-ish preview (AA).
        bt = str(self._brush_type or "").strip().lower()
        if bt == "eraser":
            r, g, bb, _aa = self._canvas_background_rgba
        else:
            r, g, bb, _aa = self._brush_rgba

        pen = QPen(QColor(int(r), int(g), int(bb), 255))
        pen.setWidthF(max(1.0, float(self._brush_radius) * 2.0))
        pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)

        rect = QRectF(
            float(min(a.x(), b.x())),
            float(min(a.y(), b.y())),
            float(abs(a.x() - b.x())),
            float(abs(a.y() - b.y())),
        )

        qp = QPainter(self._overlay)
        try:
            qp.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            if bool(filled):
                # Filled shapes ignore brush size: no stroke.
                qp.setPen(Qt.PenStyle.NoPen)
                qp.setBrush(QColor(int(r), int(g), int(bb), 255))
            else:
                qp.setPen(pen)
                qp.setBrush(Qt.BrushStyle.NoBrush)

            if k == "rect":
                qp.drawRect(rect)
            else:
                qp.drawEllipse(rect)
        finally:
            qp.end()

    def _draw_overlay_segment(self, a: QPointF, b: QPointF) -> None:
        if self._overlay is None:
            return

        # Pixel-art: preview as cell stamping (no AA).
        if bool(getattr(self, "_pixel_art_enabled", False)):
            self._draw_pixel_overlay_segment(a, b)
            return

        bt = str(self._brush_type or "").strip().lower()
        if bt == "eraser":
            r, g, bb, _aa = self._canvas_background_rgba
        else:
            r, g, bb, _aa = self._brush_rgba
        pen = QPen(QColor(int(r), int(g), int(bb), 255))
        pen.setWidthF(max(1.0, float(self._brush_radius) * 2.0))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        qp = QPainter(self._overlay)
        try:
            qp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            qp.setPen(pen)
            if abs(a.x() - b.x()) < 0.01 and abs(a.y() - b.y()) < 0.01:
                qp.drawPoint(a)
            else:
                qp.drawLine(a, b)
        finally:
            qp.end()

    def _pixel_overlay_color(self) -> QColor:
        """Overlay color for pixel-art preview (fully opaque; effective opacity applied in paintEvent)."""
        bt = str(self._brush_type or "").strip().lower()
        if bt == "eraser":
            # Transparency mode uses DestinationOut, so any opaque color works.
            if bool(getattr(self, "_transparency_mode", False)):
                return QColor(255, 255, 255, 255)
            r, g, b, _aa = self._canvas_background_rgba
            return QColor(int(r), int(g), int(b), 255)

        r, g, b, _aa = self._brush_rgba
        return QColor(int(r), int(g), int(b), 255)

    def _pixel_bresenham(self, x0: int, y0: int, x1: int, y1: int):
        x0 = int(x0)
        y0 = int(y0)
        x1 = int(x1)
        y1 = int(y1)
        dx = abs(x1 - x0)
        sx = 1 if x0 < x1 else -1
        dy = -abs(y1 - y0)
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        x, y = x0, y0
        while True:
            yield int(x), int(y)
            if x == x1 and y == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x += sx
            if e2 <= dx:
                err += dx
                y += sy

    def _draw_pixel_overlay_line(self, a: QPointF, b: QPointF) -> None:
        """Pixel-art line preview that matches backend (single polygon mask).

        Avoids the stamp-based preview which creates chunky/"crystal" artifacts.
        """
        if self._overlay is None:
            return

        col = self._pixel_overlay_color()

        try:
            ax = float(a.x())
            ay = float(a.y())
            bx = float(b.x())
            by = float(b.y())
        except Exception:
            ax = ay = bx = by = 0.0

        dx = float(bx - ax)
        dy = float(by - ay)
        dist = (dx * dx + dy * dy) ** 0.5
        if dist <= 1e-6:
            return

        thickness = max(1, int(int(self._brush_radius) * 2 - 1))
        hw = float(thickness) / 2.0

        ux = dx / dist
        uy = dy / dist
        px = -uy
        py = ux

        p1 = QPointF(float(ax + px * hw), float(ay + py * hw))
        p2 = QPointF(float(ax - px * hw), float(ay - py * hw))
        p3 = QPointF(float(bx - px * hw), float(by - py * hw))
        p4 = QPointF(float(bx + px * hw), float(by + py * hw))

        qp = QPainter(self._overlay)
        try:
            qp.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            qp.setPen(Qt.PenStyle.NoPen)
            qp.setBrush(col)
            qp.drawPolygon(QPolygonF([p1, p2, p3, p4]))
        finally:
            qp.end()

    def _draw_pixel_overlay_segment(self, a: QPointF, b: QPointF) -> None:
        if self._overlay is None:
            return

        try:
            ax = int(math.floor(float(a.x())))
            ay = int(math.floor(float(a.y())))
            bx = int(math.floor(float(b.x())))
            by = int(math.floor(float(b.y())))
        except Exception:
            ax, ay, bx, by = (0, 0, 0, 0)

        half = max(0, int(self._brush_radius) - 1)
        col = self._pixel_overlay_color()

        ow = int(self._overlay.width())
        oh = int(self._overlay.height())

        qp = QPainter(self._overlay)
        try:
            qp.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            for cx, cy in self._pixel_bresenham(ax, ay, bx, by):
                x0 = max(0, int(cx) - int(half))
                y0 = max(0, int(cy) - int(half))
                x1 = min(int(ow) - 1, int(cx) + int(half))
                y1 = min(int(oh) - 1, int(cy) + int(half))
                qp.fillRect(int(x0), int(y0), int(x1 - x0 + 1), int(y1 - y0 + 1), col)
        finally:
            qp.end()
