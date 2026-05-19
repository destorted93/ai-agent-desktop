"""Canvas Studio window.

V1 intent:
- Separate component (keep chat_window.py sane).
- Talks to the app via EventBus (canvas.cmd.*).

This is a minimal-but-real first UI pass:
- list canvases
- create canvas
- delete canvas
- set current canvas

Drawing UI comes next.
"""

from __future__ import annotations

import base64
import time
import uuid
from typing import Any, Dict, Optional, List


from PyQt6.QtCore import Qt, QTimer, QPointF, QPoint, QEvent
from PyQt6.QtGui import QFont, QPixmap, QPainter, QPen, QColor, QKeySequence, QShortcut, QImage, QAction, QActionGroup
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QColorDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QButtonGroup,
    QCheckBox,
    QRadioButton,
    QScrollArea,
    QMenu,
    QPlainTextEdit,
)

from .canvas_picker_dialog import CanvasPickerDialog
from .canvas_viewport import CanvasViewport

from ...appcore.runtime_context import Runtime
from ..screen_utils import validate_window_position


class CanvasInfoPopup(QDialog):
    """A small popup panel showing the current canvas metadata.

    It uses Qt.Popup so it auto-hides when clicking outside.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        try:
            self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        except Exception:
            pass
        self.setModal(False)
        self.setStyleSheet(
            "QDialog { background-color: #1e1e1e; }"
            "QLabel { color: #d4d4d4; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        title = QLabel("Canvas Info")
        title.setStyleSheet("font-weight: 700; font-size: 12px;")
        root.addWidget(title)

        self._form = QFormLayout()
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self._form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        self._form.setHorizontalSpacing(14)
        self._form.setVerticalSpacing(6)
        root.addLayout(self._form)

        def _val_label() -> QLabel:
            lab = QLabel("")
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lab.setStyleSheet("color: #d4d4d4;")
            return lab

        self.name_v = _val_label()
        self.id_v = _val_label()
        self.size_v = _val_label()
        self.updated_v = _val_label()
        self.history_v = _val_label()

        # Brush row: swatch + text
        self.brush_row = QWidget()
        br_l = QHBoxLayout(self.brush_row)
        br_l.setContentsMargins(0, 0, 0, 0)
        br_l.setSpacing(8)
        self.brush_swatch = QLabel("")
        self.brush_swatch.setFixedSize(16, 16)
        self.brush_swatch.setStyleSheet("border: 1px solid rgba(255,255,255,0.22); border-radius: 4px; background: rgba(0,0,0,1);")
        self.brush_text = _val_label()
        br_l.addWidget(self.brush_swatch)
        br_l.addWidget(self.brush_text, 1)

        self._form.addRow("Name", self.name_v)
        self._form.addRow("Canvas ID", self.id_v)
        self._form.addRow("Size", self.size_v)
        self._form.addRow("Updated", self.updated_v)
        self._form.addRow("History", self.history_v)
        self._form.addRow("Brush", self.brush_row)

        self.setFixedWidth(420)

    def set_canvas_meta(self, meta: Optional[Dict[str, Any]], *, canvas_id: Optional[str]) -> None:
        cid = str(canvas_id or "").strip() or "—"
        if not isinstance(meta, dict):
            self.name_v.setText("—")
            self.id_v.setText(cid)
            self.size_v.setText("—")
            self.updated_v.setText("—")
            self.history_v.setText("—")
            self.brush_text.setText("—")
            self.brush_swatch.setStyleSheet("border: 1px solid rgba(255,255,255,0.22); border-radius: 4px; background: rgba(0,0,0,0);")
            return

        nm = str(meta.get("name") or "Untitled")
        w = meta.get("width")
        h = meta.get("height")
        upd = str(meta.get("updated_at") or meta.get("created_at") or "")

        hist = meta.get("history") if isinstance(meta.get("history"), dict) else {}
        cur = hist.get("cursor_rev")
        mx = hist.get("max_rev")

        br = meta.get("current_brush") if isinstance(meta.get("current_brush"), dict) else {}
        rgba = br.get("rgba") if isinstance(br.get("rgba"), list) and len(br.get("rgba")) == 4 else [0, 0, 0, 255]
        rad = br.get("radius")
        op = br.get("opacity")

        try:
            r, g, b, a = [int(x) for x in rgba]
        except Exception:
            r, g, b, a = (0, 0, 0, 255)
        a_f = max(0.0, min(1.0, float(a) / 255.0))

        self.name_v.setText(nm)
        self.id_v.setText(cid)
        self.size_v.setText(f"{w}×{h}")
        self.updated_v.setText(upd)
        self.history_v.setText(f"cursor_rev={cur} / max_rev={mx}")
        btype = str(br.get("type") or "round")
        self.brush_text.setText(f"type={btype} · rgba({r},{g},{b},{a}) · radius={rad} · opacity={op}")
        self.brush_swatch.setStyleSheet(
            f"border: 1px solid rgba(255,255,255,0.22); border-radius: 4px; background: rgba({r},{g},{b},{a_f});"
        )


def _rgba_to_css(rgba: tuple[int, int, int, int]) -> str:
    r, g, b, a = rgba
    return f"rgba({r},{g},{b},{a/255.0})"




# NOTE: _LegacyCanvasViewport has been removed in favor of CanvasViewport
# (src/ui/components/canvas_viewport.py), which supports pan/zoom and a brush cursor.

class NewCanvasDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Canvas")
        self.setModal(True)
        self.resize(460, 300)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Untitled")

        # Mode selector
        self.mode_group = QButtonGroup(self)
        self.mode_normal_rb = QRadioButton("Normal")
        self.mode_pixel_rb = QRadioButton("Pixel Art")
        self.mode_normal_rb.setChecked(True)
        self.mode_group.addButton(self.mode_normal_rb)
        self.mode_group.addButton(self.mode_pixel_rb)

        mode_row = QWidget()
        mode_row_l = QHBoxLayout(mode_row)
        mode_row_l.setContentsMargins(0, 0, 0, 0)
        mode_row_l.setSpacing(12)
        mode_row_l.addWidget(self.mode_normal_rb)
        mode_row_l.addWidget(self.mode_pixel_rb)
        mode_row_l.addStretch(1)

        # Dimensions
        self.w_spin = QSpinBox()
        self.w_spin.setRange(1, 16384)
        self.w_spin.setValue(256)

        self.h_spin = QSpinBox()
        self.h_spin.setRange(1, 16384)
        self.h_spin.setValue(256)

        # Pixel art scale hint (does not change logical snapshot resolution)
        self.cell_px_spin = QSpinBox()
        self.cell_px_spin.setRange(1, 256)
        self.cell_px_spin.setValue(8)
        self.cell_px_spin.setEnabled(False)

        self.transparent_cb = QCheckBox("Transparent")
        self.transparent_cb.setChecked(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        form = QFormLayout()
        form.addRow("Name:", self.name_input)
        form.addRow("Mode:", mode_row)

        self._w_label = QLabel("Width:")
        self._h_label = QLabel("Height:")
        form.addRow(self._w_label, self.w_spin)
        form.addRow(self._h_label, self.h_spin)
        form.addRow("Cell px:", self.cell_px_spin)
        form.addRow("Background:", self.transparent_cb)
        layout.addLayout(form)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

        try:
            self.mode_normal_rb.toggled.connect(self._apply_mode_ui)
            self.mode_pixel_rb.toggled.connect(self._apply_mode_ui)
        except Exception:
            pass
        self._apply_mode_ui()

    def _apply_mode_ui(self) -> None:
        """Update labels + enabled states based on selected mode."""
        try:
            is_pixel = bool(self.mode_pixel_rb.isChecked())
            self.cell_px_spin.setEnabled(bool(is_pixel))
            self._w_label.setText("Grid W:" if is_pixel else "Width:")
            self._h_label.setText("Grid H:" if is_pixel else "Height:")
        except Exception:
            pass

    def get_values(self) -> Dict[str, Any]:
        mode = "pixel_art" if bool(self.mode_pixel_rb.isChecked()) else "normal"
        return {
            "name": (self.name_input.text() or "").strip() or None,
            "mode": mode,
            "width": int(self.w_spin.value()),
            "height": int(self.h_spin.value()),
            "cell_px": int(self.cell_px_spin.value()) if mode == "pixel_art" else None,
            "transparent_background": bool(self.transparent_cb.isChecked()),
        }


class NewLayerDialog(QDialog):
    def __init__(self, parent=None, *, title: str = "New Layer", name: Optional[str] = None, description: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle(str(title or "New Layer"))
        self.setModal(True)
        self.resize(460, 300)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Layer name")
        if isinstance(name, str):
            self.name_input.setText(name)

        self.desc_input = QPlainTextEdit()
        try:
            self.desc_input.setPlaceholderText("(optional) Layer description")
        except Exception:
            pass
        if isinstance(description, str):
            self.desc_input.setPlainText(description)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        form = QFormLayout()
        form.addRow("Name:", self.name_input)
        form.addRow("Description:", self.desc_input)
        layout.addLayout(form)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def get_values(self) -> Dict[str, Any]:
        nm = (self.name_input.text() or "").strip() or None
        try:
            desc = (self.desc_input.toPlainText() or "").strip() or None
        except Exception:
            desc = None
        return {"name": nm, "description": desc}


class ExportGifDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export GIF")
        self.setModal(True)
        self.resize(420, 180)

        self.frame_ms_spin = QSpinBox()
        self.frame_ms_spin.setRange(10, 10000)
        self.frame_ms_spin.setValue(120)
        try:
            self.frame_ms_spin.setSuffix(" ms")
        except Exception:
            pass

        self.loop_cb = QCheckBox("Loop forever")
        self.loop_cb.setChecked(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        form = QFormLayout()
        form.addRow("Frame duration:", self.frame_ms_spin)
        form.addRow("", self.loop_cb)
        layout.addLayout(form)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def get_values(self) -> Dict[str, Any]:
        try:
            frame_ms = int(self.frame_ms_spin.value())
        except Exception:
            frame_ms = 120
        return {"frame_duration_ms": frame_ms, "loop_forever": bool(self.loop_cb.isChecked())}


class LayerDescriptionPopup(QDialog):
    """Small hover popup for layer descriptions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        try:
            # ToolTip semantics: do not steal focus or consume the first click.
            self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        except Exception:
            pass
        self.setModal(False)
        try:
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        except Exception:
            pass
        try:
            # Critical: let clicks pass through to the layer row beneath.
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        except Exception:
            pass
        self.setStyleSheet(
            "QDialog { background-color: #1e1e1e; }"
            "QLabel { color: #d4d4d4; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self.title = QLabel("Layer")
        self.title.setStyleSheet("font-weight: 700; font-size: 12px;")
        root.addWidget(self.title)

        self.desc = QLabel("")
        self.desc.setWordWrap(True)
        self.desc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.desc.setStyleSheet("color: #d4d4d4;")
        root.addWidget(self.desc)

        self.setFixedWidth(320)

    def show_for(self, *, anchor_widget: QWidget, name: str, description: str) -> None:
        self.title.setText(str(name or "Layer"))
        self.desc.setText(str(description or ""))
        try:
            gp = anchor_widget.mapToGlobal(QPoint(0, anchor_widget.height()))
            self.move(gp.x() + 6, gp.y() + 6)
        except Exception:
            pass
        self.show()


class CanvasStudioWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Canvas Studio")
        self.setModal(False)
        self.resize(1080, 720)
        self.setWindowFlags(Qt.WindowType.Window)

        self._brush_commit_timer: Optional[QTimer] = None
        self._bus_pump_timer: Optional[QTimer] = None

        self._setup_ui()
        validate_window_position(self)

        # First load
        self.refresh_list()


        # Live refresh when tools/subagents modify canvases.
        self._canvas_refresh_timer: Optional[QTimer] = None
        self._bus_unsubs: List[Any] = []

        # Fallback poll (in case EventBus delivery gets starved): keeps the studio feeling live.
        self._live_poll_timer: Optional[QTimer] = None
        self._live_poll_inflight: bool = False
        self._live_poll_last_index_ts: Optional[str] = None
        try:
            bus = Runtime.get_event_bus()
            self._bus_unsubs.append(bus.subscribe("canvas.changed", self._on_canvas_bus_event))
            self._bus_unsubs.append(bus.subscribe("canvas.list.changed", self._on_canvas_bus_event))
        except Exception:
            pass

        # Local EventBus pump (belt-and-suspenders).
        #
        # The app normally pumps the bus globally from Application.run(). In practice,
        # we still want Canvas Studio to behave correctly even if that pump is not
        # running (or if some UI flow starves it). Without pumping, external events
        # like tool-driven canvas updates won't reach this window until you press
        # Refresh (which performs its own pump while waiting for replies).
        try:
            self._bus_pump_timer = QTimer(self)
            self._bus_pump_timer.setInterval(50)
            self._bus_pump_timer.timeout.connect(lambda: Runtime.get_event_bus().pump(max_events=50))
            self._bus_pump_timer.start()
        except Exception:
            self._bus_pump_timer = None

        # Fallback polling: refresh list/meta when something changes on disk but events don't arrive.
        try:
            self._live_poll_timer = QTimer(self)
            self._live_poll_timer.setInterval(450)
            self._live_poll_timer.timeout.connect(self._live_poll_tick)
            self._live_poll_timer.start()
        except Exception:
            self._live_poll_timer = None

        # Shortcuts
        try:
            self._sc_undo = QShortcut(QKeySequence("Ctrl+Z"), self)
            self._sc_undo.activated.connect(self.undo)
            self._sc_redo = QShortcut(QKeySequence("Ctrl+Y"), self)
            self._sc_redo.activated.connect(self.redo)
            self._sc_redo2 = QShortcut(QKeySequence("Ctrl+Shift+Z"), self)
            self._sc_redo2.activated.connect(self.redo)
        except Exception:
            pass


    def closeEvent(self, event) -> None:
        try:
            if getattr(self, "_bus_pump_timer", None) is not None:
                self._bus_pump_timer.stop()
        except Exception:
            pass

        try:
            if getattr(self, "_live_poll_timer", None) is not None:
                self._live_poll_timer.stop()
        except Exception:
            pass

        try:
            for u in list(getattr(self, "_bus_unsubs", []) or []):
                try:
                    u()
                except Exception:
                    pass
            self._bus_unsubs = []
        except Exception:
            pass

        try:
            if getattr(self, "_canvas_refresh_timer", None) is not None:
                self._canvas_refresh_timer.stop()
        except Exception:
            pass

        try:
            super().closeEvent(event)
        except Exception:
            return

    def _live_poll_tick(self) -> None:
        """Fallback polling for live updates.

        This keeps Canvas Studio responsive even if EventBus delivery of canvas.changed events
        is delayed/starved. We keep it conservative (debounced + only refresh on change).
        """
        try:
            if not bool(self.isVisible()):
                return
            if bool(getattr(self, "_live_poll_inflight", False)):
                return
        except Exception:
            pass

        self._live_poll_inflight = True

        def _done(resp: Dict[str, Any]) -> None:
            try:
                self._live_poll_inflight = False
            except Exception:
                pass

            if not isinstance(resp, dict) or resp.get("status") != "success":
                return

            canvases = resp.get("canvases") if isinstance(resp.get("canvases"), list) else []
            cur = resp.get("current_canvas_id")
            cur_id = str(cur).strip() if isinstance(cur, str) else ""

            # Detect list/index changes by newest updated_at.
            newest_ts = None
            try:
                if canvases and isinstance(canvases[0], dict):
                    newest_ts = str(canvases[0].get("updated_at") or "") or None
            except Exception:
                newest_ts = None

            if newest_ts and newest_ts != getattr(self, "_live_poll_last_index_ts", None):
                self._live_poll_last_index_ts = newest_ts
                self.refresh_list()
                return

            # Detect active-canvas switch.
            if cur_id and cur_id != str(getattr(self, "_active_canvas_id", None) or ""):
                self.refresh_list()
                return

            # Detect active canvas content changes by comparing updated_at.
            try:
                cm = getattr(self, "_last_canvas_meta", None)
                cur_meta_ts = str(cm.get("updated_at") or "") if isinstance(cm, dict) else ""
            except Exception:
                cur_meta_ts = ""

            try:
                active = str(getattr(self, "_active_canvas_id", None) or "")
                ts2 = ""
                for it in canvases:
                    if not isinstance(it, dict):
                        continue
                    if str(it.get("canvas_id") or "") == active:
                        ts2 = str(it.get("updated_at") or "")
                        break
                if ts2 and ts2 != cur_meta_ts:
                    self._schedule_canvas_refresh("selection")
            except Exception:
                pass

        self._request("canvas.cmd.list", {}, _done)

    def _schedule_canvas_refresh(self, mode: str = "selection") -> None:
        """Debounce external refreshes (tool-driven changes)."""
        try:
            if self._canvas_refresh_timer is None:
                self._canvas_refresh_timer = QTimer(self)
                self._canvas_refresh_timer.setSingleShot(True)
            else:
                try:
                    self._canvas_refresh_timer.stop()
                except Exception:
                    pass

            def _run():
                try:
                    if str(mode) == "list":
                        self.refresh_list()
                    else:
                        # If something is selected, refresh it; otherwise refresh list.
                        if self._current_canvas_id():
                            self._refresh_current_canvas()
                        else:
                            self.refresh_list()
                except Exception:
                    pass

            try:
                self._canvas_refresh_timer.timeout.disconnect()
            except Exception:
                pass
            self._canvas_refresh_timer.timeout.connect(_run)
            self._canvas_refresh_timer.start(80)
        except Exception:
            return

    def _on_canvas_bus_event(self, ev) -> None:
        """Handle canvas mutations coming from tools/subagents (not this window)."""
        try:
            pl = getattr(ev, "payload", {}) or {}
            action = str(pl.get("action") or "")
            cid = pl.get("canvas_id")
            cur = pl.get("current_canvas_id")

            selected = self._current_canvas_id()

            # If current changed, refresh list (so selection/current badge updates).
            if action in ("create", "delete", "duplicate", "set_current", "rename"):
                self._schedule_canvas_refresh("list")
                return

            # Otherwise refresh only if it matches the selected canvas.
            if selected and (selected == cid or selected == cur):
                self._schedule_canvas_refresh("selection")
        except Exception:
            return
    # -------------------------
    # UI
    # -------------------------

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("Canvas Studio")
        title.setStyleSheet("font-size: 14px; font-weight: 700;")
        root.addWidget(title)

        self.sub = QLabel("A persistent studio (Sandbox-backed).")
        self.sub.setStyleSheet("color: #9aa4b2;")
        root.addWidget(self.sub)

        # Toolbar row
        bar = QHBoxLayout()
        self.new_btn = QPushButton("New")
        self.del_btn = QPushButton("Delete")
        self.canvases_btn = QPushButton("🗂️")
        self.rename_btn = QPushButton("✏️")
        self.dup_btn = QPushButton("📄")
        self.refresh_btn = QPushButton("Refresh")
        self.undo_btn = QPushButton("↩️")
        self.redo_btn = QPushButton("↪️")
        self.export_btn = QPushButton("💾")
        self.import_btn = QPushButton("📥")
        self.copy_btn = QPushButton("📋")
        self.info_btn = QPushButton("ℹ️")

        for b in (self.new_btn, self.del_btn, self.canvases_btn, self.rename_btn, self.dup_btn, self.refresh_btn, self.undo_btn, self.redo_btn, self.export_btn, self.import_btn, self.copy_btn, self.info_btn):
            b.setFixedHeight(30)
            b.setStyleSheet(
                "QPushButton { background-color: #3a3a3a; color: #d4d4d4; border: 1px solid #555; border-radius: 6px; padding: 6px 14px; }"
                "QPushButton:hover { background-color: #4a4a4a; }"
            )

        self.new_btn.setStyleSheet(
            "QPushButton { background-color: #0e639c; color: white; border: 1px solid #0e639c; border-radius: 6px; padding: 6px 14px; }"
            "QPushButton:hover { background-color: #1177bb; }"
        )
        self.del_btn.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: #ff6b6b; border: 1px solid #555; border-radius: 6px; padding: 6px 14px; }"
            "QPushButton:hover { background-color: #ff6b6b; color: white; }"
        )


        # Icon-only buttons: bigger, clearer glyphs without making the buttons bigger.
        icon_btn_css = (
            "QPushButton { background-color: #3a3a3a; color: #d4d4d4; border: 1px solid #555; border-radius: 6px; padding: 0px; font-size: 14pt; }"
            "QPushButton:hover { background-color: #4a4a4a; }"
        )
        for b in (self.canvases_btn, self.rename_btn, self.dup_btn, self.undo_btn, self.redo_btn, self.export_btn, self.import_btn, self.copy_btn, self.info_btn):
            try:
                b.setStyleSheet(icon_btn_css)
            except Exception:
                pass

        self.canvases_btn.setToolTip("Choose canvas")
        self.canvases_btn.setFixedWidth(40)
        self.rename_btn.setToolTip("Rename")
        self.dup_btn.setToolTip("Duplicate")
        self.rename_btn.setFixedWidth(40)
        self.dup_btn.setFixedWidth(40)
        self.undo_btn.setToolTip("Undo (Ctrl+Z)")
        self.redo_btn.setToolTip("Redo (Ctrl+Y / Ctrl+Shift+Z)")
        self.undo_btn.setFixedWidth(40)
        self.redo_btn.setFixedWidth(40)
        self.export_btn.setToolTip("Export…")
        self.import_btn.setToolTip("Import image…")
        self.copy_btn.setToolTip("Copy to clipboard")
        self.export_btn.setFixedWidth(40)
        self.import_btn.setFixedWidth(40)
        self.copy_btn.setFixedWidth(40)
        self.info_btn.setToolTip("Canvas info")
        self.info_btn.setFixedWidth(40)

        bar.addWidget(self.new_btn)
        bar.addWidget(self.del_btn)
        bar.addWidget(self.canvases_btn)
        bar.addWidget(self.rename_btn)
        bar.addWidget(self.dup_btn)
        bar.addWidget(self.refresh_btn)
        bar.addWidget(self.undo_btn)
        bar.addWidget(self.redo_btn)
        bar.addWidget(self.export_btn)
        bar.addWidget(self.import_btn)
        bar.addWidget(self.copy_btn)
        bar.addWidget(self.info_btn)
        bar.addStretch(1)
        root.addLayout(bar)

        # Main panel: tools palette (left) + settings + viewport (right)
        main = QHBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(10)

        # Tool palette (vertical)
        tools_wrap = QWidget()
        tools_l = QVBoxLayout(tools_wrap)
        tools_l.setContentsMargins(0, 0, 0, 0)
        tools_l.setSpacing(8)

        tools_title = QLabel("Tools")
        tools_title.setStyleSheet("font-weight: 700; color: #d4d4d4;")
        tools_l.addWidget(tools_title)

        self._tool_btn_group = QButtonGroup(self)
        try:
            self._tool_btn_group.setExclusive(True)
        except Exception:
            pass

        # Tool registry (v1)
        # Keep this registry tiny + strict; add tools only when backend semantics exist.
        self._tool_specs = [
            {"tool": "round", "label": "Brush", "icon": "🖌️", "settings": ["color", "size", "opacity"]},
            {"tool": "eraser", "label": "Eraser", "icon": "🧽", "settings": ["size", "opacity"]},
            {"tool": "line", "label": "Line", "icon": "📏", "settings": ["color", "size", "opacity"]},
            {"tool": "fill", "label": "Fill", "icon": "🪣", "settings": ["color", "opacity"]},
            {"tool": "eyedropper", "label": "Eyedropper", "icon": "🎯", "settings": []},
        ]

        self._tool_buttons: Dict[str, QPushButton] = {}

        tool_btn_css = (
            "QPushButton { background-color: #2b2b2b; color: #d4d4d4; border: 1px solid #444; border-radius: 10px; padding: 0px; font-size: 16pt; }"
            "QPushButton:hover { background-color: #3a3a3a; }"
            "QPushButton:checked { background-color: rgba(77,166,255,0.22); border: 1px solid rgba(77,166,255,0.55); }"
        )

        for i, spec in enumerate(self._tool_specs):
            t = str(spec.get("tool") or "").strip()
            if not t:
                continue
            b = QPushButton(str(spec.get("icon") or ""))
            b.setCheckable(True)
            b.setToolTip(str(spec.get("label") or t))
            b.setFixedSize(44, 44)
            b.setStyleSheet(tool_btn_css)
            self._tool_btn_group.addButton(b, i)
            self._tool_buttons[t] = b
            tools_l.addWidget(b)

        tools_l.addStretch(1)
        main.addWidget(tools_wrap, 0)

        # Layers (Phase 2)
        self.layers_group = QGroupBox("Layers")
        self.layers_group.setFixedWidth(170)
        self.layers_group.setStyleSheet(
            "QGroupBox { color: #d4d4d4; border: 1px solid #333; border-radius: 8px; margin-top: 6px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        )
        layers_l = QVBoxLayout(self.layers_group)
        layers_l.setContentsMargins(10, 10, 10, 10)
        layers_l.setSpacing(6)

        # Scrollable list
        self._layers_scroll = QScrollArea()
        try:
            self._layers_scroll.setWidgetResizable(True)
        except Exception:
            pass

        self._layers_list_widget = QWidget()
        self._layers_list_layout = QVBoxLayout(self._layers_list_widget)
        self._layers_list_layout.setContentsMargins(0, 0, 0, 0)
        self._layers_list_layout.setSpacing(4)
        self._layers_scroll.setWidget(self._layers_list_widget)
        layers_l.addWidget(self._layers_scroll, 1)

        # Controls row
        controls = QWidget()
        cl = QHBoxLayout(controls)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)

        def _mk_btn(txt: str, tip: str) -> QPushButton:
            b = QPushButton(txt)
            b.setFixedSize(28, 26)
            b.setToolTip(tip)
            b.setStyleSheet(
                "QPushButton { background-color: #2b2b2b; color: #d4d4d4; border: 1px solid #444; border-radius: 6px; padding: 0px; }"
                "QPushButton:hover { background-color: #3a3a3a; }"
                "QPushButton:disabled { color: #666; border-color: #333; background-color: #1f1f1f; }"
            )
            return b

        self.layer_add_btn = _mk_btn("+", "New layer")
        self.layer_dup_btn = _mk_btn("⧉", "Duplicate layer")
        self.layer_del_btn = _mk_btn("🗑", "Delete layer")
        self.layer_up_btn = _mk_btn("↑", "Move layer up")
        self.layer_down_btn = _mk_btn("↓", "Move layer down")

        cl.addWidget(self.layer_add_btn)
        cl.addWidget(self.layer_dup_btn)
        cl.addWidget(self.layer_del_btn)
        cl.addStretch(1)
        cl.addWidget(self.layer_up_btn)
        cl.addWidget(self.layer_down_btn)
        layers_l.addWidget(controls, 0)

        # Wiring
        self.layer_add_btn.clicked.connect(self._ui_layer_create)
        self.layer_dup_btn.clicked.connect(self._ui_layer_duplicate)
        self.layer_del_btn.clicked.connect(self._ui_layer_delete)
        self.layer_up_btn.clicked.connect(lambda: self._ui_layer_move(delta=+1))
        self.layer_down_btn.clicked.connect(lambda: self._ui_layer_move(delta=-1))

        main.addWidget(self.layers_group, 0)

        # Right side: Settings + Canvas
        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(6)

        self.settings_group = QGroupBox("Settings")
        self.settings_group.setStyleSheet(
            "QGroupBox { color: #d4d4d4; border: 1px solid #333; border-radius: 8px; margin-top: 6px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        )
        self._settings_root = QVBoxLayout(self.settings_group)
        self._settings_root.setContentsMargins(10, 10, 10, 10)
        self._settings_root.setSpacing(10)

        # Settings widgets are rebuilt per-tool.
        self._settings_widgets: Dict[str, Any] = {}

        right_l.addWidget(self.settings_group, 0)

        rtitle = QLabel("Canvas")
        rtitle.setStyleSheet("font-weight: 700;")
        right_l.addWidget(rtitle)

        # Import overlay controls (Phase 2). Hidden unless an import is active.
        self.import_strip = QWidget()
        isl = QHBoxLayout(self.import_strip)
        isl.setContentsMargins(0, 0, 0, 0)
        isl.setSpacing(8)

        self.import_strip_label = QLabel("Import:")
        self.import_strip_label.setStyleSheet("color: #9aa4b2;")
        self.import_crop_cb = QCheckBox("Crop")
        self.import_lock_ratio_cb = QCheckBox("Lock ratio")
        try:
            self.import_lock_ratio_cb.setChecked(True)
        except Exception:
            pass
        self.import_opacity_label = QLabel("Opacity")
        self.import_opacity_label.setStyleSheet("color: #9aa4b2;")
        self.import_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.import_opacity_slider.setRange(0, 100)
        self.import_opacity_slider.setValue(100)
        self.import_apply_btn = QPushButton("Apply")
        self.import_cancel_btn = QPushButton("Cancel")

        for b in (self.import_apply_btn, self.import_cancel_btn):
            b.setFixedHeight(28)
            b.setStyleSheet(
                "QPushButton { background-color: #2b2b2b; color: #d4d4d4; border: 1px solid #444; border-radius: 8px; padding: 4px 12px; }"
                "QPushButton:hover { background-color: #3a3a3a; }"
            )
        self.import_apply_btn.setStyleSheet(
            "QPushButton { background-color: #0e639c; color: white; border: 1px solid #0e639c; border-radius: 8px; padding: 4px 12px; }"
            "QPushButton:hover { background-color: #1177bb; }"
        )

        isl.addWidget(self.import_strip_label)
        isl.addWidget(self.import_crop_cb)
        isl.addWidget(self.import_lock_ratio_cb)
        isl.addSpacing(8)
        isl.addWidget(self.import_opacity_label)
        isl.addWidget(self.import_opacity_slider, 1)
        isl.addStretch(1)
        isl.addWidget(self.import_apply_btn)
        isl.addWidget(self.import_cancel_btn)

        self.import_strip.setVisible(False)
        right_l.addWidget(self.import_strip, 0)

        self.viewport = CanvasViewport()
        right_l.addWidget(self.viewport, 1)

        main.addWidget(right, 1)
        root.addLayout(main, 1)

        # Wire actions
        self.refresh_btn.clicked.connect(self.refresh_list)
        self.new_btn.clicked.connect(self.create_canvas)
        self.del_btn.clicked.connect(self.delete_selected)
        self.rename_btn.clicked.connect(self.rename_selected)
        self.dup_btn.clicked.connect(self.duplicate_selected)
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn.clicked.connect(self.redo)
        self.export_btn.clicked.connect(self._show_export_menu)
        self.import_btn.clicked.connect(self.import_image)
        self.copy_btn.clicked.connect(self.copy_to_clipboard)
        self.canvases_btn.clicked.connect(self.open_canvas_picker)
        self.info_btn.clicked.connect(self.toggle_info_popup)

        # Disable canvas-dependent actions until a canvas is selected.
        try:
            self.undo_btn.setEnabled(False)
            self.redo_btn.setEnabled(False)
            self.export_btn.setEnabled(False)
            self.import_btn.setEnabled(False)
            self.copy_btn.setEnabled(False)
            self.rename_btn.setEnabled(False)
            self.dup_btn.setEnabled(False)
            self.del_btn.setEnabled(False)
        except Exception:
            pass

        # Wire tool selection
        try:
            for t, btn in self._tool_buttons.items():
                btn.toggled.connect(lambda checked, tool=t: self._on_tool_toggled(tool, checked))
        except Exception:
            pass

        # Line tool doubles as a Shape tool (line/rect/ellipse) via a tiny popover.
        try:
            b_line = self._tool_buttons.get("line")
            if b_line is not None:
                b_line.clicked.connect(self._ui_show_line_shape_menu)
        except Exception:
            pass

        self.viewport.on_stroke_finished = self._on_stroke_finished
        self.viewport.on_line_finished = self._on_line_finished
        self.viewport.on_shape_finished = self._on_shape_finished
        self.viewport.on_fill_clicked = self._on_fill_clicked

        # Import overlay wiring (Phase 2)
        try:
            self.viewport.on_drop_files = self._ui_import_files_dropped
        except Exception:
            pass
        try:
            self.import_crop_cb.toggled.connect(self._ui_import_set_crop_mode)
            self.import_lock_ratio_cb.toggled.connect(self._ui_import_set_lock_ratio)
            self.import_opacity_slider.valueChanged.connect(self._ui_import_set_opacity)
            self.import_apply_btn.clicked.connect(self._ui_import_apply)
            self.import_cancel_btn.clicked.connect(self._ui_import_cancel)
        except Exception:
            pass

        # Tool/settings state
        self._tool_settings_cache: Dict[str, Dict[str, Any]] = {
            "round": {"rgba": (0, 0, 0, 255), "radius": 12, "opacity": 1.0},
            "eraser": {"rgba": (0, 0, 0, 255), "radius": 24, "opacity": 1.0},
            # Interaction tools (line/fill) reuse the Brush settings but need a cache key for selection.
            "line": {"shape": "line", "filled": False},
            "fill": {},
            # Eyedropper is a momentary selection; it has no settings.
            "eyedropper": {},
        }
        self._active_tool_type: str = "round"

        # Eyedropper callback
        try:
            self.viewport.on_pick_color = self._on_pick_color
        except Exception:
            pass

        # Sync preview brush immediately.
        self._apply_tool_to_viewport()
        self._rebuild_settings_panel()
        self._set_tool_button_checked(self._active_tool_type)

        self._last_cursor_rev: Optional[int] = None
        self._active_canvas_id: Optional[str] = None
        self._last_canvas_index: List[Dict[str, Any]] = []
        self._last_canvas_meta: Optional[Dict[str, Any]] = None
        self._canvas_picker: Optional[CanvasPickerDialog] = None
        self._info_popup: Optional[CanvasInfoPopup] = None
        self._layer_desc_popup: Optional[LayerDescriptionPopup] = None
        self._layer_desc_hover_id: Optional[str] = None

        # Pixel art UI state (window-local; not persisted into canvas meta).
        self._pixel_show_grid: bool = True
        self._pixel_cell_px: int = 1

        # Import overlay state (Phase 2)
        self._import_active: bool = False
        self._import_image_b64: Optional[str] = None
        self._import_target_layer_id: Optional[str] = None
        self._import_expected_cursor_rev: Optional[int] = None

    # -------------------------
    # Bus helper
    # -------------------------

    def _request(self, cmd_topic: str, payload: Dict[str, Any], on_done, *, timeout_s: float = 2.5) -> None:
        """Fire a bus request and deliver response to on_done(payload_dict)."""
        bus = Runtime.get_event_bus()
        reply_topic = f"canvas.ui.reply.{uuid.uuid4()}"

        done: Dict[str, Any] = {"_ready": False, "payload": None}
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            try:
                if unsub:
                    unsub()
            except Exception:
                pass
            unsub = None
            pl = getattr(ev, "payload", {}) or {}
            done["payload"] = pl if isinstance(pl, dict) else {"status": "error", "message": "bad payload"}
            done["_ready"] = True

        unsub = bus.subscribe(reply_topic, _on_reply)
        bus.publish(cmd_topic, dict(payload or {}, reply_topic=reply_topic))

        # Poll pump in UI thread until reply arrives (or timeout).
        t0 = time.time()
        timer = QTimer(self)
        timer.setInterval(15)

        def _tick():
            try:
                bus.pump(max_events=50)
            except Exception:
                pass

            if done.get("_ready"):
                try:
                    timer.stop()
                    timer.deleteLater()
                except Exception:
                    pass
                try:
                    on_done(done.get("payload") or {})
                except Exception:
                    pass
                return

            if (time.time() - t0) > float(timeout_s or 2.5):
                try:
                    if unsub:
                        unsub()
                except Exception:
                    pass
                try:
                    timer.stop()
                    timer.deleteLater()
                except Exception:
                    pass
                try:
                    on_done({"status": "error", "message": "timeout waiting for reply"})
                except Exception:
                    pass

        timer.timeout.connect(_tick)
        timer.start()

    # -------------------------
    # Actions
    # -------------------------


    def undo(self) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return

        def _done(resp: Dict[str, Any]) -> None:
            if isinstance(resp, dict) and resp.get("status") == "success":
                self._on_selection_changed()
            else:
                try:
                    QMessageBox.warning(self, "Undo", str(resp))
                except Exception:
                    pass

        self._request("canvas.cmd.undo", {"canvas_id": cid, "steps": 1}, _done)

    def redo(self) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return

        def _done(resp: Dict[str, Any]) -> None:
            if isinstance(resp, dict) and resp.get("status") == "success":
                self._on_selection_changed()
            else:
                try:
                    QMessageBox.warning(self, "Redo", str(resp))
                except Exception:
                    pass

        self._request("canvas.cmd.redo", {"canvas_id": cid, "steps": 1}, _done)

    def refresh_list(self) -> None:
        self._request("canvas.cmd.list", {}, self._apply_list)

    def _apply_list(self, resp: Dict[str, Any]) -> None:
        if resp.get("status") != "success":
            try:
                self.viewport.set_status_text("(canvas list unavailable)")
            except Exception:
                pass
            self._active_canvas_id = None
            self._last_canvas_index = []
            self._last_canvas_meta = None
            self._update_window_title()
            try:
                self.viewport.set_image_b64(None)
            except Exception:
                pass
            self._last_cursor_rev = None
            try:
                self._set_canvas_actions_enabled(False)
            except Exception:
                pass
            return

        canvases = resp.get("canvases") if isinstance(resp.get("canvases"), list) else []
        self._last_canvas_index = canvases

        cur = resp.get("current_canvas_id")
        cur_id = str(cur).strip() if isinstance(cur, str) else ""

        # Enforce the "there should always be a current canvas" vibe:
        # if current is missing but canvases exist, auto-set the first one.
        if not cur_id and canvases:
            try:
                cid0 = str((canvases[0] or {}).get("canvas_id") or "").strip()
            except Exception:
                cid0 = ""
            if cid0:
                self._request("canvas.cmd.set_current", {"canvas_id": cid0}, lambda _r: self.refresh_list())
                return

        self._active_canvas_id = cur_id or None

        # Avoid showing a stale name for a different canvas while meta is loading.
        try:
            if isinstance(self._last_canvas_meta, dict) and str(self._last_canvas_meta.get("canvas_id") or "") != str(self._active_canvas_id or ""):
                self._last_canvas_meta = None
        except Exception:
            pass

        self._update_window_title()

        # Refresh meta + image for the current canvas (or clear if none).
        self._refresh_current_canvas()

    def _show_export_menu(self) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return

        m = QMenu(self)
        a_png = m.addAction("Export PNG…")
        a_gif = m.addAction("Export GIF…")

        try:
            gp = self.export_btn.mapToGlobal(QPoint(0, int(self.export_btn.height())))
        except Exception:
            gp = self.mapToGlobal(QPoint(0, 0))

        try:
            act = m.exec(gp)
        except Exception:
            act = None

        if act == a_png:
            self.export_png()
        elif act == a_gif:
            self.export_gif()

    def export_png(self) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return

        # Choose destination
        default_name = f"{cid}.png"
        try:
            path, _ = QFileDialog.getSaveFileName(self, "Export Canvas as PNG", default_name, "PNG Images (*.png)")
        except Exception:
            path = ""
        if not path:
            return
        if not path.lower().endswith(".png"):
            path = path + ".png"

        def _done(resp: Dict[str, Any]) -> None:
            if not isinstance(resp, dict) or resp.get("status") != "success":
                QMessageBox.warning(self, "Export PNG", str(resp))
                return
            b64 = resp.get("png_b64")
            if not isinstance(b64, str) or not b64:
                QMessageBox.warning(self, "Export PNG", "Missing image data")
                return
            try:
                raw = base64.b64decode(b64)

                img = QImage()
                ok = img.loadFromData(raw, "PNG")
                if not ok or img.isNull():
                    raise RuntimeError("Could not decode PNG")

                # Pixel-art export hint: scale by cell_px (nearest-like).
                try:
                    cm = getattr(self, "_last_canvas_meta", None)
                    is_pixel = bool(isinstance(cm, dict) and str(cm.get("mode") or "").strip().lower() == "pixel_art")
                except Exception:
                    is_pixel = False

                try:
                    cell_px = int(getattr(self, "_pixel_cell_px", 1) or 1)
                except Exception:
                    cell_px = 1

                if bool(is_pixel) and int(cell_px) > 1:
                    img = img.scaled(
                        int(img.width()) * int(cell_px),
                        int(img.height()) * int(cell_px),
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.FastTransformation,
                    )

                ok2 = img.save(path, "PNG")
                if not ok2:
                    raise RuntimeError("Could not save PNG")

                QMessageBox.information(self, "Export PNG", f"Saved: {path}")
            except Exception as e:
                QMessageBox.warning(self, "Export PNG", f"Failed to save: {e}")

        self._request("canvas.cmd.get_image", {"canvas_id": cid}, _done)

    def export_gif(self) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return

        dlg = ExportGifDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        opts = dlg.get_values()
        try:
            frame_ms = int(opts.get("frame_duration_ms") or 120)
        except Exception:
            frame_ms = 120
        loop_forever = bool(opts.get("loop_forever"))

        default_name = f"{cid}.gif"
        try:
            path, _ = QFileDialog.getSaveFileName(self, "Export Canvas as GIF", default_name, "GIF Images (*.gif)")
        except Exception:
            path = ""
        if not path:
            return
        if not path.lower().endswith(".gif"):
            path = path + ".gif"

        def _done(resp: Dict[str, Any]) -> None:
            if not isinstance(resp, dict) or resp.get("status") != "success":
                QMessageBox.warning(self, "Export GIF", str(resp))
                return
            b64 = resp.get("gif_b64")
            if not isinstance(b64, str) or not b64:
                QMessageBox.warning(self, "Export GIF", "Missing image data")
                return
            try:
                raw = base64.b64decode(b64)
                with open(path, "wb") as f:
                    f.write(raw)
                QMessageBox.information(self, "Export GIF", f"Saved: {path}")
            except Exception as e:
                QMessageBox.warning(self, "Export GIF", f"Failed to save: {e}")

        self._request(
            "canvas.cmd.get_gif",
            {"canvas_id": cid, "frame_duration_ms": frame_ms, "loop_forever": loop_forever},
            _done,
            timeout_s=10.0,
        )

    def _pick_import_layer_id(self) -> Optional[str]:
        """Pick target layer for imports.

        Rule: import goes to the active layer, unless there are no user-created layers,
        in which case it goes to Background.
        """
        try:
            info = self._layers_from_meta(getattr(self, "_last_canvas_meta", None))
        except Exception:
            info = {"enabled": False, "layers": [], "active": None, "bg": None}

        layers = info.get("layers") if isinstance(info.get("layers"), list) else []
        bg = info.get("bg") if isinstance(info.get("bg"), dict) else None
        active = info.get("active") if isinstance(info.get("active"), str) else None

        has_user_layers = False
        for l in layers:
            if not isinstance(l, dict):
                continue
            role = str(l.get("role") or "").strip().lower()
            if role and role != "background":
                has_user_layers = True
                break

        if not has_user_layers and isinstance(bg, dict):
            lid = str(bg.get("layer_id") or "").strip()
            return lid or None

        return (str(active).strip() if isinstance(active, str) and active.strip() else None)

    def _set_import_lock(self, active: bool) -> None:
        self._import_active = bool(active)
        try:
            self.import_strip.setVisible(bool(active))
        except Exception:
            pass

        # Lock down tool/layer UI during an active import.
        try:
            for _t, btn in (getattr(self, "_tool_buttons", {}) or {}).items():
                try:
                    btn.setEnabled(not bool(active))
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self.layers_group.setEnabled(not bool(active))
        except Exception:
            pass

        try:
            # Toolbar actions that would desync expected_cursor_rev.
            self.undo_btn.setEnabled(not bool(active))
            self.redo_btn.setEnabled(not bool(active))
            self.import_btn.setEnabled(not bool(active))
        except Exception:
            pass

        # Ensure viewport mode is correct.
        try:
            self._apply_tool_to_viewport()
        except Exception:
            pass

        # Best-effort restore normal action enablement when leaving import.
        if not bool(active):
            try:
                self._set_canvas_actions_enabled(bool(self._current_canvas_id()))
            except Exception:
                pass

    def import_image(self) -> None:
        """Start an interactive import (file picker)."""
        cid = self._current_canvas_id()
        if not cid:
            return

        if bool(getattr(self, "_import_active", False)):
            # Already importing; force user to Apply/Cancel.
            return

        try:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Import Image",
                "",
                "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All Files (*)",
            )
        except Exception:
            path = ""
        if not path:
            return

        self._ui_start_import_from_path(path)

    def _ui_import_files_dropped(self, paths: List[str]) -> None:
        """Drag & drop handler from the viewport."""
        if not paths:
            return
        if bool(getattr(self, "_import_active", False)):
            return
        self._ui_start_import_from_path(str(paths[0]))

    def _ui_start_import_from_path(self, path: str) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return

        p = str(path or "").strip()
        if not p:
            return

        # Reject GIF explicitly (Phase 2 scope).
        if p.lower().endswith(".gif"):
            QMessageBox.warning(self, "Import Image", "GIF import is not supported.")
            return

        try:
            with open(p, "rb") as f:
                raw = f.read()
        except Exception as e:
            QMessageBox.warning(self, "Import Image", f"Failed to read file: {e}")
            return

        # Decode for preview.
        img = QImage()
        ok = False
        try:
            ok = img.loadFromData(raw)
        except Exception:
            ok = False
        if not ok or img.isNull():
            QMessageBox.warning(self, "Import Image", "Could not decode image")
            return

        try:
            img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        except Exception:
            pass

        # Determine initial placement.
        try:
            cm = getattr(self, "_last_canvas_meta", None)
            cw = int(cm.get("width") or 0) if isinstance(cm, dict) else 0
            ch = int(cm.get("height") or 0) if isinstance(cm, dict) else 0
        except Exception:
            cw, ch = (0, 0)

        iw = int(img.width())
        ih = int(img.height())

        # Pixel-art import sizing heuristic:
        # In pixel_art mode, 1 canvas unit == 1 cell (logical pixel). cell_px is view/export scale.
        # So importing an image "at true size" should default to true *display* size:
        # dest_w_cells = img_w_px / cell_px.
        try:
            is_pixel = bool(isinstance(cm, dict) and str(cm.get("mode") or "").strip().lower() == "pixel_art")
        except Exception:
            is_pixel = False

        try:
            cell_px = int(getattr(self, "_pixel_cell_px", 1) or 1)
        except Exception:
            cell_px = 1
        cell_px = max(1, min(256, int(cell_px)))

        dest_w = float(iw)
        dest_h = float(ih)
        if bool(is_pixel) and int(cell_px) > 1:
            dest_w = max(1.0, float(iw) / float(cell_px))
            dest_h = max(1.0, float(ih) / float(cell_px))

        x0 = (float(cw) - float(dest_w)) / 2.0
        y0 = (float(ch) - float(dest_h)) / 2.0

        # Store state for Apply.
        import base64 as _b64

        self._import_image_b64 = _b64.b64encode(raw).decode("utf-8")
        self._import_expected_cursor_rev = int(self._last_cursor_rev) if self._last_cursor_rev is not None else None
        self._import_target_layer_id = self._pick_import_layer_id()

        # Reset strip controls.
        try:
            self.import_crop_cb.setChecked(False)
            self.import_opacity_slider.setValue(100)
        except Exception:
            pass

        # Activate viewport overlay.
        try:
            self.viewport.set_import_object(
                img,
                dest_rect=(x0, y0, float(dest_w), float(dest_h)),
                crop_rect=(0, 0, int(iw), int(ih)),
                rotation_deg=0.0,
                opacity=1.0,
            )
            try:
                self.viewport.set_import_lock_ratio(bool(self.import_lock_ratio_cb.isChecked()))
            except Exception:
                pass
        except Exception:
            QMessageBox.warning(self, "Import Image", "Failed to start import overlay")
            return

        self._set_import_lock(True)

    def _ui_import_set_crop_mode(self, enabled: bool) -> None:
        if not bool(getattr(self, "_import_active", False)):
            return
        try:
            self.viewport.set_import_crop_mode(bool(enabled))
        except Exception:
            pass

    def _ui_import_set_lock_ratio(self, enabled: bool) -> None:
        # Allow toggling even before an import is started (so the preference is ready).
        try:
            self.viewport.set_import_lock_ratio(bool(enabled))
        except Exception:
            pass

    def _ui_import_set_opacity(self, v: int) -> None:
        if not bool(getattr(self, "_import_active", False)):
            return
        try:
            op = max(0.0, min(1.0, float(int(v)) / 100.0))
        except Exception:
            op = 1.0
        try:
            self.viewport.set_import_opacity(float(op))
        except Exception:
            pass

    def _ui_import_cancel(self) -> None:
        if not bool(getattr(self, "_import_active", False)):
            return
        try:
            self.viewport.clear_import_object()
        except Exception:
            pass

        self._import_image_b64 = None
        self._import_target_layer_id = None
        self._import_expected_cursor_rev = None

        self._set_import_lock(False)

    def _ui_import_apply(self) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return
        if not bool(getattr(self, "_import_active", False)):
            return

        b64 = getattr(self, "_import_image_b64", None)
        if not isinstance(b64, str) or not b64:
            QMessageBox.warning(self, "Import Image", "Missing import image data")
            return

        try:
            st = self.viewport.get_import_state()
        except Exception:
            st = None

        if not isinstance(st, dict) or not isinstance(st.get("dest_rect"), dict):
            QMessageBox.warning(self, "Import Image", "Missing import state")
            return

        payload = {
            "canvas_id": cid,
            "layer_id": getattr(self, "_import_target_layer_id", None),
            "image_b64": b64,
            "dest_rect": st.get("dest_rect"),
            "crop_rect": st.get("crop_rect"),
            "rotation_deg": st.get("rotation_deg"),
            "opacity": st.get("opacity"),
            "expected_cursor_rev": getattr(self, "_import_expected_cursor_rev", None),
        }

        def _done(resp: Dict[str, Any]) -> None:
            if not isinstance(resp, dict) or resp.get("status") != "success":
                QMessageBox.warning(self, "Import Image", str(resp))
                return

            # Clear overlay and refresh canvas.
            try:
                self.viewport.clear_import_object()
            except Exception:
                pass
            self._import_image_b64 = None
            self._import_target_layer_id = None
            self._import_expected_cursor_rev = None
            self._set_import_lock(False)
            self._refresh_current_canvas()

        self._request("canvas.cmd.image.import_apply", payload, _done, timeout_s=10.0)

    def copy_to_clipboard(self) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return

        def _done(resp: Dict[str, Any]) -> None:
            if not isinstance(resp, dict) or resp.get("status") != "success":
                QMessageBox.warning(self, "Copy to Clipboard", str(resp))
                return
            b64 = resp.get("png_b64")
            if not isinstance(b64, str) or not b64:
                QMessageBox.warning(self, "Copy to Clipboard", "Missing image data")
                return
            try:
                raw = base64.b64decode(b64)
                img = QPixmap()
                ok = img.loadFromData(raw, "PNG")
                if not ok or img.isNull():
                    QMessageBox.warning(self, "Copy to Clipboard", "Could not decode PNG")
                    return

                # Pixel-art export hint: scale by cell_px (nearest-like).
                try:
                    cm = getattr(self, "_last_canvas_meta", None)
                    is_pixel = bool(isinstance(cm, dict) and str(cm.get("mode") or "").strip().lower() == "pixel_art")
                except Exception:
                    is_pixel = False

                try:
                    cell_px = int(getattr(self, "_pixel_cell_px", 1) or 1)
                except Exception:
                    cell_px = 1

                if bool(is_pixel) and int(cell_px) > 1:
                    img = img.scaled(
                        int(img.width()) * int(cell_px),
                        int(img.height()) * int(cell_px),
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.FastTransformation,
                    )

                cb = QApplication.clipboard()
                cb.setPixmap(img)
            except Exception as e:
                QMessageBox.warning(self, "Copy to Clipboard", f"Failed: {e}")

        self._request("canvas.cmd.get_image", {"canvas_id": cid}, _done)

    def create_canvas(self) -> None:
        dlg = NewCanvasDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.get_values()

        bg = [255, 255, 255, 0] if bool(v.get("transparent_background")) else [255, 255, 255, 255]

        payload = {
            "width": int(v["width"]),
            "height": int(v["height"]),
            "name": v.get("name"),
            "background_rgba": bg,
            "set_current": True,
            "mode": v.get("mode") or "normal",
            "cell_px": v.get("cell_px"),
        }

        def _done(resp: Dict[str, Any]) -> None:
            if resp.get("status") != "success":
                QMessageBox.warning(self, "Create Canvas", str(resp))
                return
            self.refresh_list()

        self._request("canvas.cmd.create", payload, _done)


    def rename_selected(self) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return

        # Best-effort: use last fetched canvas meta.
        current_name = None
        try:
            if isinstance(getattr(self, "_last_canvas_meta", None), dict):
                current_name = str(self._last_canvas_meta.get("name") or "").strip() or None
        except Exception:
            current_name = None

        try:
            new_name, ok = QInputDialog.getText(self, "Rename Canvas", "Name:", text=(current_name or ""))
        except Exception:
            return
        if not ok:
            return
        new_name = (new_name or "").strip()
        if not new_name:
            return

        def _done(resp: Dict[str, Any]) -> None:
            if not isinstance(resp, dict) or resp.get("status") != "success":
                QMessageBox.warning(self, "Rename", str(resp))
                return
            self.refresh_list()

        self._request("canvas.cmd.rename", {"canvas_id": cid, "name": new_name}, _done)

    def duplicate_selected(self) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return

        # Default name: "<current> Copy"
        default_name = None
        try:
            nm = None
            if isinstance(getattr(self, "_last_canvas_meta", None), dict):
                nm = str(self._last_canvas_meta.get("name") or "").strip() or None
            default_name = (nm + " Copy") if nm else None
        except Exception:
            default_name = None

        try:
            name, ok = QInputDialog.getText(self, "Duplicate Canvas", "Name:", text=(default_name or ""))
        except Exception:
            return
        if not ok:
            return
        name = (name or "").strip() or None

        def _done(resp: Dict[str, Any]) -> None:
            if not isinstance(resp, dict) or resp.get("status") != "success":
                QMessageBox.warning(self, "Duplicate", str(resp))
                return
            self.refresh_list()

        self._request("canvas.cmd.duplicate", {"source_canvas_id": cid, "name": name, "set_current": True}, _done)

    def delete_selected(self) -> None:
        """Delete the current canvas."""
        cid = self._current_canvas_id()
        if not cid:
            return

        nm = None
        try:
            if isinstance(getattr(self, "_last_canvas_meta", None), dict):
                nm = str(self._last_canvas_meta.get("name") or "").strip() or None
        except Exception:
            nm = None

        label = (f"'{nm}' ({cid})" if nm else cid)
        ok = QMessageBox.question(self, "Delete Canvas", f"Delete canvas {label}?")
        if ok != QMessageBox.StandardButton.Yes:
            return

        def _done(resp: Dict[str, Any]) -> None:
            if resp.get("status") != "success":
                QMessageBox.warning(self, "Delete Canvas", str(resp))
                return
            self.refresh_list()

        self._request("canvas.cmd.delete", {"canvas_id": cid}, _done)

    def _set_canvas_actions_enabled(self, enabled: bool) -> None:
        try:
            self.undo_btn.setEnabled(bool(enabled))
            self.redo_btn.setEnabled(bool(enabled))
            self.export_btn.setEnabled(bool(enabled))
            self.import_btn.setEnabled(bool(enabled))
            self.copy_btn.setEnabled(bool(enabled))
            self.rename_btn.setEnabled(bool(enabled))
            self.dup_btn.setEnabled(bool(enabled))
            self.del_btn.setEnabled(bool(enabled))
        except Exception:
            pass


    # -------------------------
    # Layers UI (Phase 2)
    # -------------------------

    def _layers_from_meta(self, canvas: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Return normalized layer info from canvas meta.

        Layers list order in meta is bottom->top.
        """
        if not isinstance(canvas, dict):
            return {"enabled": False, "layers": [], "active": None, "bg": None}
        enabled = bool(canvas.get("layers_enabled"))
        st = canvas.get("layers") if isinstance(canvas.get("layers"), dict) else {}
        layers = st.get("layers") if isinstance(st.get("layers"), list) else []
        layers = [l for l in layers if isinstance(l, dict) and str(l.get("layer_id") or "").strip()]
        active = st.get("active_layer_id")
        active = str(active).strip() if isinstance(active, str) and active.strip() else (str(layers[-1].get("layer_id")) if layers else None)

        bg = None
        for l in layers:
            if str(l.get("role") or "").strip().lower() == "background":
                bg = str(l.get("layer_id"))
                break
        if bg is None and layers:
            bg = str(layers[0].get("layer_id"))

        return {"enabled": bool(enabled), "layers": layers, "active": active, "bg": bg}

    def _rebuild_layers_panel(self, canvas: Optional[Dict[str, Any]]) -> None:
        """Rebuild the Layers panel list + enable/disable controls."""
        lay = getattr(self, "_layers_list_layout", None)
        if lay is None:
            return

        # Clear current list.
        try:
            self._clear_layout(lay)
        except Exception:
            pass

        info = self._layers_from_meta(canvas)
        enabled = bool(info.get("enabled")) and bool(canvas)
        layers: List[Dict[str, Any]] = info.get("layers") if isinstance(info.get("layers"), list) else []
        active_id = info.get("active")
        bg_id = info.get("bg")

        # Default: disabled state.
        if not enabled or not layers:
            hint = QLabel("(no layers)")
            hint.setStyleSheet("color: #9aa4b2;")
            lay.addWidget(hint)
            lay.addStretch(1)
            try:
                self.layer_add_btn.setEnabled(False)
                self.layer_dup_btn.setEnabled(False)
                self.layer_del_btn.setEnabled(False)
                self.layer_up_btn.setEnabled(False)
                self.layer_down_btn.setEnabled(False)
            except Exception:
                pass
            return

        # Render rows top->bottom.
        layers_top = list(reversed(layers))

        row_css = (
            "QPushButton { text-align: left; background: transparent; border: 1px solid transparent; padding: 3px 6px; border-radius: 6px; color: #d4d4d4; }"
            "QPushButton:hover { background: rgba(255,255,255,0.06); border-color: rgba(255,255,255,0.10); }"
        )
        row_active_css = (
            "QPushButton { text-align: left; background: rgba(77,166,255,0.18); border: 1px solid rgba(77,166,255,0.45); padding: 3px 6px; border-radius: 6px; color: #d4d4d4; }"
        )

        for lyr in layers_top:
            lid = str(lyr.get("layer_id") or "").strip()
            if not lid:
                continue
            role = str(lyr.get("role") or "layer").strip().lower()
            is_bg = (lid == str(bg_id or "")) or (role == "background")

            nm = str(lyr.get("name") or ("Background" if is_bg else "Layer"))
            desc = None
            try:
                d0 = lyr.get("description")
                desc = (str(d0).strip() if isinstance(d0, str) else None)
            except Exception:
                desc = None
            vis = bool(lyr.get("visible", True))

            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(6)

            eye = QCheckBox("")
            eye.setChecked(bool(vis))
            eye.setToolTip("Visible")
            eye.setFixedWidth(18)
            eye.setEnabled(not is_bg)

            def _on_vis_changed(v: int, layer_id: str = lid) -> None:
                try:
                    self._ui_layer_set_visible(layer_id, bool(v))
                except Exception:
                    pass

            eye.stateChanged.connect(_on_vis_changed)
            hl.addWidget(eye, 0)

            btn = QPushButton(("[BG] " if is_bg else "") + nm)
            btn.setStyleSheet(row_active_css if (str(active_id or "") == lid) else row_css)
            btn.setFixedHeight(24)

            # Hover description popup (for agent-friendly layer semantics).
            try:
                btn.setProperty("layer_id", lid)
                btn.setProperty("layer_name", nm)
                btn.setProperty("layer_desc", desc or "")
                btn.installEventFilter(self)
                if desc:
                    btn.setToolTip(desc)
            except Exception:
                pass

            # Context menu for rename/description.
            try:
                btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                btn.customContextMenuRequested.connect(lambda pos, layer_id=lid, anchor=btn: self._ui_layer_context_menu(layer_id=layer_id, anchor=anchor, pos=pos))
            except Exception:
                pass

            def _on_select(_checked: bool = False, layer_id: str = lid) -> None:
                try:
                    self._ui_layer_set_active(layer_id)
                except Exception:
                    pass

            btn.clicked.connect(_on_select)
            hl.addWidget(btn, 1)

            lay.addWidget(row)

        lay.addStretch(1)

        # Enable controls based on active layer.
        idx_active = None
        for i, lyr in enumerate(layers):
            if str(lyr.get("layer_id") or "").strip() == str(active_id or ""):
                idx_active = int(i)
                break

        try:
            self.layer_add_btn.setEnabled(True)
            self.layer_dup_btn.setEnabled(bool(active_id))
            self.layer_del_btn.setEnabled(bool(active_id) and str(active_id) != str(bg_id))
            self.layer_up_btn.setEnabled(bool(idx_active is not None and idx_active < (len(layers) - 1) and str(active_id) != str(bg_id)))
            self.layer_down_btn.setEnabled(bool(idx_active is not None and idx_active > 1 and str(active_id) != str(bg_id)))
        except Exception:
            pass

    def _ui_layer_create(self) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return
        exp = self._last_cursor_rev

        dlg = NewLayerDialog(self, title="New Layer")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.get_values()
        name = v.get("name")
        desc = v.get("description")

        payload = {
            "canvas_id": cid,
            "name": (str(name) if isinstance(name, str) else None),
            "description": (str(desc) if isinstance(desc, str) else None),
            "set_active": True,
            "source_layer_id": None,
            "expected_cursor_rev": int(exp) if exp is not None else None,
        }

        def _done(resp: Dict[str, Any]) -> None:
            if not isinstance(resp, dict) or resp.get("status") != "success":
                try:
                    QMessageBox.warning(self, "New Layer", str(resp))
                except Exception:
                    pass
                return
            self._refresh_current_canvas()

        self._request("canvas.cmd.layer.create", payload, _done)

    def _ui_layer_duplicate(self) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return
        exp = self._last_cursor_rev

        cm = getattr(self, "_last_canvas_meta", None)
        info = self._layers_from_meta(cm if isinstance(cm, dict) else None)
        active_id = info.get("active")
        if not active_id:
            return

        payload = {
            "canvas_id": cid,
            "name": None,
            "description": None,
            "set_active": True,
            "source_layer_id": str(active_id),
            "expected_cursor_rev": int(exp) if exp is not None else None,
        }

        def _done(resp: Dict[str, Any]) -> None:
            if not isinstance(resp, dict) or resp.get("status") != "success":
                try:
                    QMessageBox.warning(self, "Duplicate Layer", str(resp))
                except Exception:
                    pass
                return
            self._refresh_current_canvas()

        self._request("canvas.cmd.layer.create", payload, _done)

    def _ui_layer_delete(self) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return
        exp = self._last_cursor_rev

        cm = getattr(self, "_last_canvas_meta", None)
        info = self._layers_from_meta(cm if isinstance(cm, dict) else None)
        active_id = info.get("active")
        bg_id = info.get("bg")
        if not active_id or str(active_id) == str(bg_id):
            return

        ok = QMessageBox.question(self, "Delete Layer", "Delete the active layer?")
        if ok != QMessageBox.StandardButton.Yes:
            return

        payload = {"canvas_id": cid, "layer_id": str(active_id), "expected_cursor_rev": int(exp) if exp is not None else None}

        def _done(resp: Dict[str, Any]) -> None:
            if not isinstance(resp, dict) or resp.get("status") != "success":
                try:
                    QMessageBox.warning(self, "Delete Layer", str(resp))
                except Exception:
                    pass
                return
            self._refresh_current_canvas()

        self._request("canvas.cmd.layer.delete", payload, _done)

    def _ui_layer_move(self, *, delta: int) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return
        exp = self._last_cursor_rev

        cm = getattr(self, "_last_canvas_meta", None)
        info = self._layers_from_meta(cm if isinstance(cm, dict) else None)
        layers: List[Dict[str, Any]] = info.get("layers") if isinstance(info.get("layers"), list) else []
        active_id = info.get("active")
        bg_id = info.get("bg")
        if not active_id or str(active_id) == str(bg_id):
            return

        idx = None
        for i, lyr in enumerate(layers):
            if str(lyr.get("layer_id") or "").strip() == str(active_id):
                idx = int(i)
                break
        if idx is None:
            return

        dst = max(1, min(len(layers) - 1, int(idx) + int(delta)))
        if dst == idx:
            return

        payload = {
            "canvas_id": cid,
            "layer_id": str(active_id),
            "name": None,
            "description": None,
            "clear_description": None,
            "visible": None,
            "opacity": None,
            "move_to_index": int(dst),
            "set_active": True,
            "expected_cursor_rev": int(exp) if exp is not None else None,
        }

        def _done(resp: Dict[str, Any]) -> None:
            if not isinstance(resp, dict) or resp.get("status") != "success":
                try:
                    QMessageBox.warning(self, "Move Layer", str(resp))
                except Exception:
                    pass
                return
            self._refresh_current_canvas()

        self._request("canvas.cmd.layer.update", payload, _done)

    def _ui_layer_set_active(self, layer_id: str) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return
        exp = self._last_cursor_rev

        payload = {
            "canvas_id": cid,
            "layer_id": str(layer_id),
            "name": None,
            "description": None,
            "clear_description": None,
            "visible": None,
            "opacity": None,
            "move_to_index": None,
            "set_active": True,
            "expected_cursor_rev": int(exp) if exp is not None else None,
        }

        def _done(resp: Dict[str, Any]) -> None:
            if not isinstance(resp, dict) or resp.get("status") != "success":
                return
            self._refresh_current_canvas()

        self._request("canvas.cmd.layer.update", payload, _done)

    def _ui_layer_set_visible(self, layer_id: str, visible: bool) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return
        exp = self._last_cursor_rev

        payload = {
            "canvas_id": cid,
            "layer_id": str(layer_id),
            "name": None,
            "description": None,
            "clear_description": None,
            "visible": bool(visible),
            "opacity": None,
            "move_to_index": None,
            "set_active": None,
            "expected_cursor_rev": int(exp) if exp is not None else None,
        }

        def _done(resp: Dict[str, Any]) -> None:
            if not isinstance(resp, dict) or resp.get("status") != "success":
                return
            self._refresh_current_canvas()

        self._request("canvas.cmd.layer.update", payload, _done)

    def _ui_layer_context_menu(self, *, layer_id: str, anchor: QWidget, pos: QPoint) -> None:
        """Right-click menu for layer rename/description."""
        cm = getattr(self, "_last_canvas_meta", None)
        info = self._layers_from_meta(cm if isinstance(cm, dict) else None)
        layers: List[Dict[str, Any]] = info.get("layers") if isinstance(info.get("layers"), list) else []
        lyr = None
        for it in layers:
            if str(it.get("layer_id") or "").strip() == str(layer_id or "").strip():
                lyr = it
                break
        if not isinstance(lyr, dict):
            return

        nm = str(lyr.get("name") or "Layer")
        desc = str(lyr.get("description") or "") if isinstance(lyr.get("description"), str) else ""

        menu = QMenu(self)
        a_rename = menu.addAction("Rename")
        a_desc = menu.addAction("Edit description")
        a_clear = menu.addAction("Clear description")

        try:
            act = menu.exec(anchor.mapToGlobal(pos))
        except Exception:
            act = menu.exec()
        if act == a_rename:
            self._ui_layer_rename(layer_id=str(layer_id), current_name=nm)
        elif act == a_desc:
            self._ui_layer_edit_description(layer_id=str(layer_id), current_description=desc)
        elif act == a_clear:
            self._ui_layer_clear_description(layer_id=str(layer_id))

    def _ui_layer_rename(self, *, layer_id: str, current_name: str) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return
        exp = self._last_cursor_rev
        try:
            new_name, ok = QInputDialog.getText(self, "Rename Layer", "Name:", text=(current_name or ""))
        except Exception:
            return
        if not ok:
            return
        new_name = (new_name or "").strip()
        if not new_name:
            return

        payload = {
            "canvas_id": cid,
            "layer_id": str(layer_id),
            "name": str(new_name),
            "description": None,
            "clear_description": None,
            "visible": None,
            "opacity": None,
            "move_to_index": None,
            "set_active": None,
            "expected_cursor_rev": int(exp) if exp is not None else None,
        }

        self._request("canvas.cmd.layer.update", payload, lambda r: self._refresh_current_canvas())

    def _ui_layer_edit_description(self, *, layer_id: str, current_description: str) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return
        exp = self._last_cursor_rev

        dlg = NewLayerDialog(self, title="Edit Layer Description", name=None, description=current_description)
        # Hide name input for this mode.
        try:
            dlg.name_input.hide()
        except Exception:
            pass

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.get_values()
        desc = v.get("description")
        desc_s = (str(desc) if isinstance(desc, str) else "").strip()

        if not desc_s:
            # treat empty as clear
            payload = {
                "canvas_id": cid,
                "layer_id": str(layer_id),
                "name": None,
                "description": None,
                "clear_description": True,
                "visible": None,
                "opacity": None,
                "move_to_index": None,
                "set_active": None,
                "expected_cursor_rev": int(exp) if exp is not None else None,
            }
        else:
            payload = {
                "canvas_id": cid,
                "layer_id": str(layer_id),
                "name": None,
                "description": desc_s,
                "clear_description": None,
                "visible": None,
                "opacity": None,
                "move_to_index": None,
                "set_active": None,
                "expected_cursor_rev": int(exp) if exp is not None else None,
            }

        self._request("canvas.cmd.layer.update", payload, lambda r: self._refresh_current_canvas())

    def _ui_layer_clear_description(self, *, layer_id: str) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return
        exp = self._last_cursor_rev
        payload = {
            "canvas_id": cid,
            "layer_id": str(layer_id),
            "name": None,
            "description": None,
            "clear_description": True,
            "visible": None,
            "opacity": None,
            "move_to_index": None,
            "set_active": None,
            "expected_cursor_rev": int(exp) if exp is not None else None,
        }
        self._request("canvas.cmd.layer.update", payload, lambda r: self._refresh_current_canvas())

    def eventFilter(self, obj: Any, event: Any) -> bool:
        """Hover handler for layer description popup."""
        try:
            if isinstance(obj, QPushButton) and obj.property("layer_id"):
                et = event.type()
                if et == QEvent.Type.Enter:
                    desc = str(obj.property("layer_desc") or "").strip()
                    if desc:
                        if self._layer_desc_popup is None:
                            self._layer_desc_popup = LayerDescriptionPopup(self)
                        nm = str(obj.property("layer_name") or "Layer")
                        self._layer_desc_popup.show_for(anchor_widget=obj, name=nm, description=desc)
                elif et == QEvent.Type.Leave:
                    pop = getattr(self, "_layer_desc_popup", None)
                    if pop is not None:
                        try:
                            pop.hide()
                        except Exception:
                            pass
        except Exception:
            pass
        try:
            return super().eventFilter(obj, event)
        except Exception:
            return False


    def _update_window_title(self) -> None:
        """Keep the window title synced with the current canvas."""
        try:
            cid = self._current_canvas_id()
            nm = None
            if isinstance(getattr(self, "_last_canvas_meta", None), dict):
                nm = str(self._last_canvas_meta.get("name") or "").strip() or None
            if cid and nm:
                self.setWindowTitle(f"Canvas Studio — {nm} ({cid})")
            elif cid:
                self.setWindowTitle(f"Canvas Studio — {cid}")
            else:
                self.setWindowTitle("Canvas Studio")
        except Exception:
            try:
                self.setWindowTitle("Canvas Studio")
            except Exception:
                pass
    def _refresh_current_canvas(self) -> None:
        cid = self._current_canvas_id()
        if not cid:
            self._update_window_title()
            try:
                self.viewport.set_image_b64(None)
                self.viewport.set_status_text("(no canvas)")
                self.viewport.set_transparency_mode(False)
                self.viewport.set_pixel_art_mode(False)
                self.viewport.set_show_grid(False)
            except Exception:
                pass
            self._last_cursor_rev = None
            self._last_canvas_meta = None
            try:
                self._rebuild_layers_panel(None)
            except Exception:
                pass
            self._set_canvas_actions_enabled(False)
            return

        self._set_canvas_actions_enabled(True)

        # Fetch metadata
        def _done_get(resp: Dict[str, Any]) -> None:
            if resp.get("status") != "success":
                self._last_cursor_rev = None
                self._last_canvas_meta = None
                self._update_window_title()
                try:
                    self.viewport.set_status_text("(canvas meta unavailable)")
                except Exception:
                    pass
                self._update_info_popup_if_open()
                return

            canvas = resp.get("canvas")
            self._last_canvas_meta = canvas if isinstance(canvas, dict) else None
            self._update_window_title()

            # Sync canvas background color (needed so eraser preview matches actual erase).
            # Also: enable transparency UI when background is transparent OR when layers are enabled.
            try:
                bg_alpha = 255
                bg = canvas.get("background") if isinstance(canvas, dict) else None
                if isinstance(bg, dict):
                    rgba = [int(bg.get("r", 255)), int(bg.get("g", 255)), int(bg.get("b", 255)), int(bg.get("a", 255))]
                    bg_alpha = int(rgba[3]) if len(rgba) == 4 else 255
                    self.viewport.set_canvas_background_rgba(rgba)

                layers_enabled = bool(canvas.get("layers_enabled")) if isinstance(canvas, dict) else False

                # v1 sanity: layers are enabled for both opaque and transparent canvases.
                # Only show checkerboard / alpha-erase preview when the canvas background is actually transparent.
                self.viewport.set_transparency_mode(bool(int(bg_alpha) < 255))

                # Pixel-art mode UI hints (crisp rendering + optional grid overlay).
                try:
                    is_pixel = str(canvas.get("mode") or "").strip().lower() == "pixel_art"
                except Exception:
                    is_pixel = False

                self.viewport.set_pixel_art_mode(bool(is_pixel))

                cell_px = 1
                try:
                    pa = canvas.get("pixel_art") if isinstance(canvas, dict) else None
                    if isinstance(pa, dict) and pa.get("cell_px") is not None:
                        cell_px = max(1, min(256, int(pa.get("cell_px") or 1)))
                except Exception:
                    cell_px = 1
                self._pixel_cell_px = int(cell_px)

                self.viewport.set_show_grid(bool(is_pixel and bool(getattr(self, "_pixel_show_grid", True))))
            except Exception:
                pass
            self._update_info_popup_if_open()

            # Sync brush (UI controls + live preview)
            self._apply_tool_state_from_canvas_meta(canvas)

            # Rebuild layers panel (Phase 2)
            try:
                self._rebuild_layers_panel(canvas)
            except Exception:
                pass

            try:
                hist = canvas.get("history") if isinstance(canvas, dict) else None
                if isinstance(hist, dict):
                    self._last_cursor_rev = int(hist.get("cursor_rev", 0) or 0)
            except Exception:
                self._last_cursor_rev = None

        self._request("canvas.cmd.get", {"canvas_id": cid}, _done_get)

        # Fetch image
        def _done_img(resp: Dict[str, Any]) -> None:
            if resp.get("status") != "success":
                self.viewport.set_status_text("(image unavailable)")
                return
            try:
                self._last_cursor_rev = int(resp.get("cursor_rev", 0) or 0)
            except Exception:
                self._last_cursor_rev = None
            # Belt-and-suspenders: image replies can race meta replies.
            try:
                cm = getattr(self, "_last_canvas_meta", None)
                is_pixel = bool(isinstance(cm, dict) and str(cm.get("mode") or "").strip().lower() == "pixel_art")
            except Exception:
                is_pixel = False
            try:
                self.viewport.set_pixel_art_mode(bool(is_pixel))
                self.viewport.set_show_grid(bool(is_pixel and bool(getattr(self, "_pixel_show_grid", True))))
            except Exception:
                pass

            self.viewport.set_image_b64(resp.get("png_b64"))

        self._request("canvas.cmd.get_image", {"canvas_id": cid}, _done_img)
        self._update_info_popup_if_open()

    # Back-compat: old name used throughout this file.
    def _on_selection_changed(self) -> None:
        self._refresh_current_canvas()

    def _on_stroke_finished(self, points: List[Dict[str, float]]) -> None:
        """Called by the viewport when the user finishes a mouse stroke."""
        cid = self._current_canvas_id()
        if not cid:
            return

        payload = {
            "canvas_id": cid,
            "points": points,
            "expected_cursor_rev": self._last_cursor_rev,
        }

        def _done(resp: Dict[str, Any]) -> None:
            if resp.get("status") != "success":
                QMessageBox.warning(self, "Stroke", str(resp))
                return
            # Refresh meta + image
            self._refresh_current_canvas()

        self._request("canvas.cmd.stroke", payload, _done)

    def _on_line_finished(self, x1: float, y1: float, x2: float, y2: float) -> None:
        """Called by the viewport when the user finishes a click-drag line."""
        cid = self._current_canvas_id()
        if not cid:
            return

        payload = {
            "canvas_id": cid,
            "x1": float(x1),
            "y1": float(y1),
            "x2": float(x2),
            "y2": float(y2),
            "expected_cursor_rev": self._last_cursor_rev,
        }

        def _done(resp: Dict[str, Any]) -> None:
            if resp.get("status") != "success":
                QMessageBox.warning(self, "Line", str(resp))
                return
            self._refresh_current_canvas()

        self._request("canvas.cmd.line", payload, _done)

    def _on_shape_finished(self, shape: str, x1: float, y1: float, x2: float, y2: float, filled: bool) -> None:
        """Called by the viewport when the user finishes a click-drag shape (rect/ellipse)."""
        cid = self._current_canvas_id()
        if not cid:
            return

        payload = {
            "canvas_id": cid,
            "shape": str(shape or ""),
            "x1": float(x1),
            "y1": float(y1),
            "x2": float(x2),
            "y2": float(y2),
            "filled": bool(filled),
            "expected_cursor_rev": self._last_cursor_rev,
        }

        def _done(resp: Dict[str, Any]) -> None:
            if resp.get("status") != "success":
                QMessageBox.warning(self, "Shape", str(resp))
                return
            self._refresh_current_canvas()

        self._request("canvas.cmd.shape", payload, _done)

    def _on_fill_clicked(self, x: float, y: float) -> None:
        """Called by the viewport when the user clicks to bucket-fill."""
        cid = self._current_canvas_id()
        if not cid:
            return

        payload = {
            "canvas_id": cid,
            "x": float(x),
            "y": float(y),
            "alpha_threshold": 20,
            "expected_cursor_rev": self._last_cursor_rev,
        }

        def _done(resp: Dict[str, Any]) -> None:
            if resp.get("status") != "success":
                QMessageBox.warning(self, "Fill", str(resp))
                return
            self._refresh_current_canvas()

        self._request("canvas.cmd.fill", payload, _done)


    # -------------------------
    # Shape menu (Line tool)
    # -------------------------

    def _ui_show_line_shape_menu(self) -> None:
        """Popover to switch Line tool between line/rect/ellipse (+ fill toggle)."""
        try:
            btn = self._tool_buttons.get("line")
        except Exception:
            btn = None
        if btn is None:
            return

        try:
            st = self._tool_settings_cache.get("line") if isinstance(getattr(self, "_tool_settings_cache", None), dict) else None
        except Exception:
            st = None
        if not isinstance(st, dict):
            st = {"shape": "line", "filled": False}
            try:
                if isinstance(getattr(self, "_tool_settings_cache", None), dict):
                    self._tool_settings_cache["line"] = st
            except Exception:
                pass

        cur_shape = str(st.get("shape") or "line").strip().lower()
        if cur_shape in ("rectangle", "rect"):
            cur_shape = "rect"
        elif cur_shape in ("ellipse", "circle"):
            cur_shape = "ellipse"
        else:
            cur_shape = "line"
        cur_filled = bool(st.get("filled", False))

        m = QMenu(self)
        g = QActionGroup(m)
        try:
            g.setExclusive(True)
        except Exception:
            pass

        def _add_shape(k: str, label: str) -> None:
            act = QAction(label, m)
            act.setCheckable(True)
            act.setChecked(cur_shape == k)
            try:
                g.addAction(act)
            except Exception:
                pass
            act.triggered.connect(lambda _checked=False, kk=k: self._ui_set_line_shape_kind(kk))
            m.addAction(act)

        _add_shape("line", "Line")
        _add_shape("rect", "Rectangle")
        _add_shape("ellipse", "Ellipse")

        m.addSeparator()

        fill_act = QAction("Fill", m)
        fill_act.setCheckable(True)
        fill_act.setChecked(cur_filled)
        fill_act.setEnabled(cur_shape != "line")
        fill_act.toggled.connect(self._ui_set_line_shape_filled)
        m.addAction(fill_act)

        try:
            pos = btn.mapToGlobal(QPoint(0, int(btn.height())))
        except Exception:
            pos = None
        try:
            if pos is not None:
                m.exec(pos)
            else:
                m.exec()
        except Exception:
            pass

    def _ui_set_line_shape_kind(self, kind: str) -> None:
        try:
            st = self._tool_settings_cache.get("line") if isinstance(getattr(self, "_tool_settings_cache", None), dict) else None
        except Exception:
            st = None
        if not isinstance(st, dict):
            return

        k = str(kind or "").strip().lower()
        if k in ("rectangle", "rect"):
            k = "rect"
        elif k in ("ellipse", "circle"):
            k = "ellipse"
        else:
            k = "line"

        st["shape"] = str(k)
        if k == "line":
            st["filled"] = False

        try:
            self.viewport.set_shape_kind(k)
            self.viewport.set_shape_filled(bool(st.get("filled", False)))
        except Exception:
            pass

        # Rebuild settings so Size can be disabled for Fill.
        try:
            self._rebuild_settings_panel()
        except Exception:
            pass

    def _ui_set_line_shape_filled(self, filled: bool) -> None:
        try:
            st = self._tool_settings_cache.get("line") if isinstance(getattr(self, "_tool_settings_cache", None), dict) else None
        except Exception:
            st = None
        if not isinstance(st, dict):
            return

        # Line can't be filled.
        if str(st.get("shape") or "line").strip().lower() == "line":
            st["filled"] = False
        else:
            st["filled"] = bool(filled)

        try:
            self.viewport.set_shape_filled(bool(st.get("filled", False)))
        except Exception:
            pass

        # Rebuild settings so Size can be disabled for Fill.
        try:
            self._rebuild_settings_panel()
        except Exception:
            pass

    # -------------------------
    # Brush UX
    # -------------------------



    def _on_pick_color(self, x: float, y: float) -> None:
        """Eyedropper click handler (viewport -> bus -> update brush color)."""
        cid = self._current_canvas_id()
        if not cid:
            return

        exp = self._last_cursor_rev

        def _done(resp: Dict[str, Any]) -> None:
            if not isinstance(resp, dict) or resp.get("status") != "success":
                return
            rgba = resp.get("rgba")
            if not (isinstance(rgba, list) and len(rgba) == 4):
                return

            # Apply picked color to the Brush tool settings.
            try:
                br = self._tool_settings_cache.get("round") if isinstance(getattr(self, "_tool_settings_cache", None), dict) else None
                if not isinstance(br, dict):
                    br = {"rgba": (0, 0, 0, 255), "radius": 12, "opacity": 1.0}
                br["rgba"] = tuple(int(v) for v in rgba)
                self._tool_settings_cache["round"] = br
            except Exception:
                pass

            # Switch back to Brush and commit.
            try:
                self._set_active_tool("round", commit=False)
                self._commit_tool_settings_to_backend()
            except Exception:
                pass

        self._request(
            "canvas.cmd.sample_color",
            {
                "canvas_id": cid,
                "x": float(x),
                "y": float(y),
                "expected_cursor_rev": int(exp) if exp is not None else None,
            },
            _done,
        )
    # -------------------------
    # Tools + Settings UX (v2)
    # -------------------------

    def _set_tool_button_checked(self, tool_type: str) -> None:
        try:
            for t, btn in (getattr(self, "_tool_buttons", {}) or {}).items():
                try:
                    btn.blockSignals(True)
                    btn.setChecked(str(t) == str(tool_type))
                finally:
                    btn.blockSignals(False)
        except Exception:
            return

    def _on_tool_toggled(self, tool_type: str, checked: bool) -> None:
        if not checked:
            return
        # While an import overlay is active, block tool switching (prevents desync/confusion).
        if bool(getattr(self, "_import_active", False)):
            try:
                self._set_tool_button_checked(str(getattr(self, "_active_tool_type", "round") or "round"))
            except Exception:
                pass
            return
        self._set_active_tool(tool_type, commit=True)

    def _set_active_tool(self, tool_type: str, *, commit: bool) -> None:
        t = str(tool_type or "").strip().lower()
        if not t:
            return
        cache = getattr(self, "_tool_settings_cache", None)
        if not isinstance(cache, dict) or t not in cache:
            return

        self._active_tool_type = t
        self._set_tool_button_checked(t)
        self._rebuild_settings_panel()
        self._apply_tool_to_viewport()

        if commit and t in ("round", "eraser", "line", "fill"):
            # Line + Fill should still keep the backend's current tool = round,
            # so draw_line/fill_bucket behave predictably.
            self._commit_tool_settings_to_backend()

    def _settings_key_for_tool(self, tool_type: str) -> str:
        """Map UI tool selection to the settings bucket it should use.

        Line + Fill reuse the Brush (round) settings.
        """
        t = str(tool_type or "").strip().lower() or "round"
        if t in ("line", "fill"):
            return "round"
        return t

    def _get_active_tool_settings(self) -> Dict[str, Any]:
        cache = getattr(self, "_tool_settings_cache", None)
        if not isinstance(cache, dict):
            cache = {}

        t_ui = str(getattr(self, "_active_tool_type", "round") or "round")
        t = self._settings_key_for_tool(t_ui)

        s = cache.get(t)
        if isinstance(s, dict):
            return s
        cache[t] = {"rgba": (0, 0, 0, 255), "radius": 12, "opacity": 1.0}
        self._tool_settings_cache = cache
        return cache[t]

    def _apply_tool_to_viewport(self) -> None:
        try:
            # Import overlay mode takes over the viewport until Apply/Cancel.
            if bool(getattr(self, "_import_active", False)):
                self.viewport.set_interaction_mode("import")
                return

            tool_ui = str(getattr(self, "_active_tool_type", "round") or "round").strip().lower()

            # Interaction mode
            if tool_ui == "eyedropper":
                self.viewport.set_interaction_mode("eyedropper")
                return
            if tool_ui == "line":
                self.viewport.set_interaction_mode("line")
                try:
                    st = self._tool_settings_cache.get("line") if isinstance(getattr(self, "_tool_settings_cache", None), dict) else None
                except Exception:
                    st = None
                if not isinstance(st, dict):
                    st = {"shape": "line", "filled": False}
                    try:
                        if isinstance(getattr(self, "_tool_settings_cache", None), dict):
                            self._tool_settings_cache["line"] = st
                    except Exception:
                        pass
                try:
                    self.viewport.set_shape_kind(str(st.get("shape") or "line"))
                    self.viewport.set_shape_filled(bool(st.get("filled", False)))
                except Exception:
                    pass
            elif tool_ui == "fill":
                self.viewport.set_interaction_mode("fill")
            else:
                self.viewport.set_interaction_mode("draw")

            # Brush preview/settings
            # Line + Fill reuse the Brush (round) settings.
            s = self._get_active_tool_settings()
            rgba = s.get("rgba") if isinstance(s.get("rgba"), (list, tuple)) else (0, 0, 0, 255)
            r, g, b, a = [int(x) for x in rgba]
            radius = int(s.get("radius") or 12)
            opacity = float(s.get("opacity") if s.get("opacity") is not None else 1.0)

            brush_type = "eraser" if tool_ui == "eraser" else "round"

            self.viewport.set_brush(
                rgba=[r, g, b, a],
                radius=max(1, radius),
                opacity=max(0.0, min(1.0, opacity)),
                brush_type=brush_type,
            )
        except Exception:
            return

    def _clear_layout(self, layout) -> None:
        try:
            while layout.count():
                it = layout.takeAt(0)
                w = it.widget() if it is not None else None
                if w is not None:
                    try:
                        w.setParent(None)
                        w.deleteLater()
                    except Exception:
                        pass
                child_l = it.layout() if it is not None else None
                if child_l is not None:
                    try:
                        self._clear_layout(child_l)
                    except Exception:
                        pass
        except Exception:
            return

    def _set_color_button_style(self, btn: QPushButton, rgba: tuple[int, int, int, int]) -> None:
        try:
            css = _rgba_to_css(rgba)
            btn.setStyleSheet(
                "QPushButton { border: 1px solid rgba(255,255,255,0.22); border-radius: 6px; background: " + css + "; }"
                "QPushButton:hover { border-color: rgba(255,255,255,0.45); }"
            )
        except Exception:
            pass

    def _rebuild_settings_panel(self) -> None:
        """Rebuild the Settings panel for the currently selected tool."""
        try:
            self._settings_widgets = {}
            root = getattr(self, "_settings_root", None)
            if root is None:
                return

            self._clear_layout(root)

            tool = str(getattr(self, "_active_tool_type", "round") or "round")
            settings = self._get_active_tool_settings()

            # Discover setting rows from the tool registry.

            tool_l = str(tool or "").strip().lower()
            if tool_l == "eyedropper":
                tip = QLabel("Click the canvas to pick a color for the Brush.")
                tip.setStyleSheet("color: #9aa4b2;")
                root.addWidget(tip)
                return
            allowed = None
            try:
                for spec in (getattr(self, "_tool_specs", []) or []):
                    if str(spec.get("tool") or "") == tool:
                        allowed = spec.get("settings")
                        break
            except Exception:
                allowed = None
            allowed = allowed if isinstance(allowed, list) else ["color", "size", "opacity"]

            # Pixel art: per-window grid toggle (UI overlay only).
            try:
                cm = getattr(self, "_last_canvas_meta", None)
                is_pixel = bool(isinstance(cm, dict) and str(cm.get("mode") or "").strip().lower() == "pixel_art")
            except Exception:
                is_pixel = False

            if bool(is_pixel):
                row = QWidget()
                row_l = QHBoxLayout(row)
                row_l.setContentsMargins(0, 0, 0, 0)
                row_l.setSpacing(10)

                lab = QLabel("Pixel Art")
                lab.setStyleSheet("color: #9aa4b2;")
                row_l.addWidget(lab)

                cb = QCheckBox("Show grid")
                cb.setChecked(bool(getattr(self, "_pixel_show_grid", True)))

                def _on_grid_changed(v: int) -> None:
                    try:
                        self._pixel_show_grid = bool(v)
                        self.viewport.set_show_grid(bool(self._pixel_show_grid))
                    except Exception:
                        pass

                cb.stateChanged.connect(_on_grid_changed)
                row_l.addWidget(cb)
                row_l.addStretch(1)
                root.addWidget(row)

            # Quick tips for interaction tools.
            if tool_l == "line":
                tip = QLabel("Click-drag to draw a shape. Click the Line button again to pick Line/Rect/Ellipse. Shift: snap for Line; lock ratio for Rect/Ellipse.")
                tip.setStyleSheet("color: #9aa4b2;")
                root.addWidget(tip)
            elif tool_l == "fill":
                tip = QLabel("Click to fill a transparent region (alpha-threshold). Close outlines to prevent leaks.")
                tip.setStyleSheet("color: #9aa4b2;")
                root.addWidget(tip)

            # Color
            if "color" in allowed:
                row = QWidget()
                row_l = QHBoxLayout(row)
                row_l.setContentsMargins(0, 0, 0, 0)
                row_l.setSpacing(10)

                lab = QLabel("Color")
                lab.setStyleSheet("color: #9aa4b2;")
                row_l.addWidget(lab)

                btn = QPushButton("")
                btn.setFixedSize(42, 22)
                btn.setToolTip("Tool color")
                rgba = settings.get("rgba") if isinstance(settings.get("rgba"), (list, tuple)) else (0, 0, 0, 255)
                try:
                    rgba_t = tuple(int(x) for x in rgba)
                except Exception:
                    rgba_t = (0, 0, 0, 255)
                self._set_color_button_style(btn, rgba_t)  # type: ignore[arg-type]

                btn.clicked.connect(self._pick_color_for_active_tool)
                row_l.addWidget(btn)
                row_l.addStretch(1)

                self._settings_widgets["color_btn"] = btn
                root.addWidget(row)

            # Size
            if "size" in allowed:
                row = QWidget()
                row_l = QHBoxLayout(row)
                row_l.setContentsMargins(0, 0, 0, 0)
                row_l.setSpacing(10)

                size = int(settings.get("radius") or 12)

                # Filled rect/ellipse: size (stroke thickness) is ignored.
                size_disabled = False
                try:
                    if tool_l == "line":
                        st_line = self._tool_settings_cache.get("line") if isinstance(getattr(self, "_tool_settings_cache", None), dict) else None
                        if isinstance(st_line, dict):
                            sh = str(st_line.get("shape") or "line").strip().lower()
                            if sh in ("rectangle", "rect"):
                                sh = "rect"
                            elif sh in ("ellipse", "circle"):
                                sh = "ellipse"
                            if sh in ("rect", "ellipse") and bool(st_line.get("filled", False)):
                                size_disabled = True
                except Exception:
                    size_disabled = False

                lab_txt = f"{'Size (cells)' if bool(is_pixel) else 'Size'}: {max(1, size)}"
                if bool(size_disabled):
                    lab_txt = ("Size (disabled for Fill)" if not bool(is_pixel) else "Size (cells) (disabled for Fill)")

                size_lab = QLabel(lab_txt)
                size_lab.setStyleSheet("color: #9aa4b2;")
                row_l.addWidget(size_lab)

                sl = QSlider(Qt.Orientation.Horizontal)
                sl.setRange(1, 256)
                sl.setValue(max(1, size))
                sl.setFixedWidth(240)
                sl.setEnabled(not bool(size_disabled))
                sl.valueChanged.connect(self._on_size_changed)
                sl.sliderReleased.connect(self._commit_tool_settings_to_backend)
                row_l.addWidget(sl)
                row_l.addStretch(1)

                self._settings_widgets["size_label"] = size_lab
                self._settings_widgets["size_slider"] = sl
                root.addWidget(row)

            # Opacity
            if "opacity" in allowed:
                row = QWidget()
                row_l = QHBoxLayout(row)
                row_l.setContentsMargins(0, 0, 0, 0)
                row_l.setSpacing(10)

                op = float(settings.get("opacity") if settings.get("opacity") is not None else 1.0)
                op_pct = int(round(max(0.0, min(1.0, op)) * 100))
                op_lab = QLabel(f"Opacity: {op_pct}%")
                op_lab.setStyleSheet("color: #9aa4b2;")
                row_l.addWidget(op_lab)

                sl = QSlider(Qt.Orientation.Horizontal)
                sl.setRange(0, 100)
                sl.setValue(op_pct)
                sl.setFixedWidth(220)
                sl.valueChanged.connect(self._on_opacity_changed)
                sl.sliderReleased.connect(self._commit_tool_settings_to_backend)
                row_l.addWidget(sl)
                row_l.addStretch(1)

                self._settings_widgets["opacity_label"] = op_lab
                self._settings_widgets["opacity_slider"] = sl
                root.addWidget(row)

        except Exception:
            return

    def _pick_color_for_active_tool(self) -> None:
        try:
            tool_ui = str(getattr(self, "_active_tool_type", "round") or "round")
            tool_key = self._settings_key_for_tool(tool_ui)
            s = self._get_active_tool_settings()
            rgba = s.get("rgba") if isinstance(s.get("rgba"), (list, tuple)) else (0, 0, 0, 255)
            r, g, b, a = [int(x) for x in rgba]
            col = QColorDialog.getColor(QColor(r, g, b, a), self, "Tool Color")
            if not col.isValid():
                return

            s["rgba"] = (int(col.red()), int(col.green()), int(col.blue()), 255)
            self._tool_settings_cache[tool_key] = s

            btn = self._settings_widgets.get("color_btn")
            if isinstance(btn, QPushButton):
                self._set_color_button_style(btn, s["rgba"])  # type: ignore[arg-type]

            self._apply_tool_to_viewport()
            self._commit_tool_settings_to_backend()
        except Exception:
            return

    def _on_size_changed(self, v: int) -> None:
        try:
            tool_ui = str(getattr(self, "_active_tool_type", "round") or "round")
            tool_key = self._settings_key_for_tool(tool_ui)
            s = self._get_active_tool_settings()
            s["radius"] = max(1, int(v))
            self._tool_settings_cache[tool_key] = s

            lab = self._settings_widgets.get("size_label")
            if isinstance(lab, QLabel):
                try:
                    cm = getattr(self, "_last_canvas_meta", None)
                    is_pixel = bool(isinstance(cm, dict) and str(cm.get("mode") or "").strip().lower() == "pixel_art")
                except Exception:
                    is_pixel = False
                lab.setText(f"{'Size (cells)' if bool(is_pixel) else 'Size'}: {int(s['radius'])}")

            self._apply_tool_to_viewport()
            self._schedule_settings_commit()
        except Exception:
            pass

    def _on_opacity_changed(self, v: int) -> None:
        try:
            vv = max(0, min(100, int(v)))
            tool_ui = str(getattr(self, "_active_tool_type", "round") or "round")
            tool_key = self._settings_key_for_tool(tool_ui)
            s = self._get_active_tool_settings()
            s["opacity"] = float(vv) / 100.0
            self._tool_settings_cache[tool_key] = s

            lab = self._settings_widgets.get("opacity_label")
            if isinstance(lab, QLabel):
                lab.setText(f"Opacity: {vv}%")

            self._apply_tool_to_viewport()
            self._schedule_settings_commit()
        except Exception:
            pass

    def _schedule_settings_commit(self) -> None:
        """Debounce settings commits so the backend stays in sync without spamming."""
        try:
            if self._brush_commit_timer is None:
                self._brush_commit_timer = QTimer(self)
                self._brush_commit_timer.setSingleShot(True)
                self._brush_commit_timer.timeout.connect(self._commit_tool_settings_to_backend)
            try:
                self._brush_commit_timer.stop()
            except Exception:
                pass
            self._brush_commit_timer.start(180)
        except Exception:
            return

    def _commit_tool_settings_to_backend(self) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return

        tool_ui = str(getattr(self, "_active_tool_type", "round") or "round")
        tool_l = str(tool_ui or "").strip().lower()
        if tool_l not in ("round", "eraser", "line", "fill"):
            return

        # Line + Fill reuse round settings, but still need the backend current tool = round.
        s = self._get_active_tool_settings()
        rgba = s.get("rgba") if isinstance(s.get("rgba"), (list, tuple)) else (0, 0, 0, 255)
        try:
            r, g, b, a = [int(x) for x in rgba]
        except Exception:
            r, g, b, a = (0, 0, 0, 255)

        payload = {
            "canvas_id": cid,
            "rgba": [r, g, b, a],
            "radius": int(s.get("radius") or 12),
            "opacity": float(s.get("opacity") if s.get("opacity") is not None else 1.0),
            "brush_type": ("eraser" if tool_l == "eraser" else "round"),
        }

        def _done(resp: Dict[str, Any]) -> None:
            if not isinstance(resp, dict) or resp.get("status") != "success":
                return
            self._update_info_popup_if_open()

        self._request("canvas.cmd.brush_set", payload, _done)

    def _apply_tool_state_from_canvas_meta(self, canvas: Any) -> None:
        """Sync local tool selection + settings cache from backend canvas meta."""
        try:
            if not isinstance(canvas, dict):
                return

            ts = canvas.get("tool_state") if isinstance(canvas.get("tool_state"), dict) else None

            cur_tool = "round"
            round_settings = None
            eraser_settings = None

            if isinstance(ts, dict):
                try:
                    cur_tool = str(ts.get("current_tool") or "round").strip().lower() or "round"
                except Exception:
                    cur_tool = "round"

                tset = ts.get("tool_settings") if isinstance(ts.get("tool_settings"), dict) else {}
                if isinstance(tset, dict):
                    round_settings = tset.get("round")
                    eraser_settings = tset.get("eraser")

            cb = canvas.get("current_brush") if isinstance(canvas.get("current_brush"), dict) else {}

            def _norm(d: Any, *, fallback: Dict[str, Any]) -> Dict[str, Any]:
                out = dict(fallback)
                if isinstance(d, dict):
                    rgba2 = d.get("rgba") if isinstance(d.get("rgba"), list) else None
                    if isinstance(rgba2, list) and len(rgba2) == 4:
                        try:
                            out["rgba"] = tuple(int(x) for x in rgba2)
                        except Exception:
                            pass
                    if d.get("radius") is not None:
                        try:
                            out["radius"] = max(1, int(d.get("radius")))
                        except Exception:
                            pass
                    if d.get("opacity") is not None:
                        try:
                            out["opacity"] = max(0.0, min(1.0, float(d.get("opacity"))))
                        except Exception:
                            pass
                return out

            base_round = _norm(cb, fallback={"rgba": (0, 0, 0, 255), "radius": 12, "opacity": 1.0})
            round_final = _norm(round_settings, fallback=base_round)

            base_eraser = {"rgba": round_final.get("rgba", (0, 0, 0, 255)), "radius": 24, "opacity": 1.0}
            eraser_final = _norm(eraser_settings, fallback=base_eraser)

            self._tool_settings_cache["round"] = round_final
            self._tool_settings_cache["eraser"] = eraser_final

            if cur_tool not in ("round", "eraser"):
                cur_tool = "round"

            # For older canvases: honor current_brush.type if present.
            try:
                if isinstance(cb, dict) and isinstance(cb.get("type"), str) and cb.get("type").strip():
                    t2 = str(cb.get("type")).strip().lower()
                    if t2 in ("round", "eraser"):
                        cur_tool = t2
            except Exception:
                pass

            # Keep interaction-tool selection (line/fill/eyedropper) stable across refreshes.
            try:
                cur_ui = str(getattr(self, "_active_tool_type", "round") or "round").strip().lower()
            except Exception:
                cur_ui = "round"

            if cur_ui in ("line", "fill", "eyedropper"):
                self._set_tool_button_checked(cur_ui)
            else:
                self._active_tool_type = cur_tool
                self._set_tool_button_checked(cur_tool)

            self._rebuild_settings_panel()
            self._apply_tool_to_viewport()
        except Exception:
            return

    def _refresh_brush_swatch(self) -> None:
        try:
            css = _rgba_to_css(self._brush_rgba)
            self.brush_color_btn.setStyleSheet(
                "QPushButton { border: 1px solid rgba(255,255,255,0.22); border-radius: 6px; background: " + css + "; }"
                "QPushButton:hover { border-color: rgba(255,255,255,0.45); }"
            )
        except Exception:
            pass

    def _pick_brush_color(self) -> None:
        try:
            r, g, b, a = self._brush_rgba
            col = QColorDialog.getColor(QColor(r, g, b, a), self, "Brush Color")
            if not col.isValid():
                return
            self._brush_rgba = (int(col.red()), int(col.green()), int(col.blue()), 255)
            self._refresh_brush_swatch()

            # Update live preview immediately.
            self.viewport.set_brush(rgba=list(self._brush_rgba), radius=int(self._brush_radius), opacity=float(self._brush_opacity))

            # Commit immediately (color pick is discrete, not spammy).
            self._commit_brush_to_backend()
        except Exception:
            return

    def _on_brush_size_changed(self, v: int) -> None:
        try:
            self._brush_radius = max(1, int(v))
            self.brush_size_label.setText(f"Size: {self._brush_radius}")
            self.viewport.set_brush(rgba=list(self._brush_rgba), radius=int(self._brush_radius), opacity=float(self._brush_opacity))
            self._schedule_brush_commit()
        except Exception:
            pass

    def _on_brush_opacity_changed(self, v: int) -> None:
        try:
            vv = max(0, min(100, int(v)))
            self._brush_opacity = float(vv) / 100.0
            self.brush_opacity_label.setText(f"Opacity: {vv}%")
            self.viewport.set_brush(rgba=list(self._brush_rgba), radius=int(self._brush_radius), opacity=float(self._brush_opacity))
            self._schedule_brush_commit()
        except Exception:
            pass

    def _current_canvas_id(self) -> Optional[str]:
        cid = str(getattr(self, "_active_canvas_id", None) or "").strip()
        return cid or None

    def open_canvas_picker(self) -> None:
        """Open the canvas picker (always refreshes list right before showing)."""
        def _done(resp: Dict[str, Any]) -> None:
            if not isinstance(resp, dict) or resp.get("status") != "success":
                try:
                    QMessageBox.warning(self, "Canvases", str(resp))
                except Exception:
                    pass
                return

            canvases = resp.get("canvases") if isinstance(resp.get("canvases"), list) else []
            cur = resp.get("current_canvas_id")

            try:
                self._canvas_picker = CanvasPickerDialog(parent=self)
                self._canvas_picker.set_canvases(canvases=canvases, current_canvas_id=(str(cur) if isinstance(cur, str) else None))
                if self._canvas_picker.exec() != QDialog.DialogCode.Accepted:
                    return
                picked = self._canvas_picker.selected_canvas_id()
            except Exception:
                return

            if not picked:
                return

            def _done_set(r2: Dict[str, Any]) -> None:
                if not isinstance(r2, dict) or r2.get("status") != "success":
                    try:
                        QMessageBox.warning(self, "Set Current Canvas", str(r2))
                    except Exception:
                        pass
                    return
                # Reload everything (meta + image + buttons).
                self.refresh_list()

            self._request("canvas.cmd.set_current", {"canvas_id": picked}, _done_set)

        self._request("canvas.cmd.list", {}, _done)

    def _schedule_brush_commit(self) -> None:
        """Debounce brush commits so the backend stays in sync without spamming."""
        try:
            if self._brush_commit_timer is None:
                self._brush_commit_timer = QTimer(self)
                self._brush_commit_timer.setSingleShot(True)
                self._brush_commit_timer.timeout.connect(self._commit_brush_to_backend)
            try:
                self._brush_commit_timer.stop()
            except Exception:
                pass
            self._brush_commit_timer.start(180)
        except Exception:
            return

    # -------------------------
    # Info popup
    # -------------------------

    def _update_info_popup_if_open(self) -> None:
        try:
            pop = getattr(self, "_info_popup", None)
            if pop is not None and pop.isVisible():
                pop.set_canvas_meta(self._last_canvas_meta, canvas_id=self._current_canvas_id())
        except Exception:
            pass

    def toggle_info_popup(self) -> None:
        """Toggle the info dropdown for the current canvas."""
        try:
            pop = getattr(self, "_info_popup", None)
            if pop is not None and pop.isVisible():
                pop.close()
                return

            if pop is None:
                pop = CanvasInfoPopup(parent=self)
                self._info_popup = pop

            pop.set_canvas_meta(self._last_canvas_meta, canvas_id=self._current_canvas_id())

            try:
                anchor = self.info_btn.mapToGlobal(self.info_btn.rect().bottomLeft())
                x = int(anchor.x()) - max(0, int(pop.width() - self.info_btn.width()))
                y = int(anchor.y()) + 6
                pop.move(x, y)
            except Exception:
                pass

            pop.show()
        except Exception:
            return

    def _commit_brush_to_backend(self) -> None:
        cid = self._current_canvas_id()
        if not cid:
            return

        payload = {
            "canvas_id": cid,
            "rgba": list(self._brush_rgba),
            "radius": int(self._brush_radius),
            "opacity": float(self._brush_opacity),
        }

        def _done(resp: Dict[str, Any]) -> None:
            # Don’t pop errors for transient slider changes.
            if not isinstance(resp, dict) or resp.get("status") != "success":
                return
            self._update_info_popup_if_open()

        self._request("canvas.cmd.brush_set", payload, _done)

    def _apply_brush_from_canvas_meta(self, canvas: Any) -> None:
        try:
            br = canvas.get("current_brush") if isinstance(canvas, dict) else None
            if not isinstance(br, dict):
                return

            rgba = br.get("rgba") if isinstance(br.get("rgba"), list) else None
            rad = br.get("radius")
            op = br.get("opacity")

            if isinstance(rgba, list) and len(rgba) == 4:
                r, g, b, a = [int(x) for x in rgba]
                self._brush_rgba = (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)), max(0, min(255, a)))

            if rad is not None:
                self._brush_radius = max(1, int(rad))

            if op is not None:
                self._brush_opacity = max(0.0, min(1.0, float(op)))

            # Sync UI controls
            try:
                self.brush_size_slider.blockSignals(True)
                self.brush_size_slider.setValue(int(self._brush_radius))
            finally:
                self.brush_size_slider.blockSignals(False)

            try:
                self.brush_opacity_slider.blockSignals(True)
                self.brush_opacity_slider.setValue(int(round(self._brush_opacity * 100)))
            finally:
                self.brush_opacity_slider.blockSignals(False)

            self.brush_size_label.setText(f"Size: {int(self._brush_radius)}")
            self.brush_opacity_label.setText(f"Opacity: {int(round(self._brush_opacity * 100))}%")
            self._refresh_brush_swatch()

            # Sync live preview brush
            br_type = "round"
            try:
                if isinstance(br, dict) and isinstance(br.get("type"), str) and br.get("type").strip():
                    br_type = str(br.get("type")).strip()
            except Exception:
                br_type = "round"
            self.viewport.set_brush(rgba=list(self._brush_rgba), radius=int(self._brush_radius), opacity=float(self._brush_opacity), brush_type=br_type)
        except Exception:
            return

    # (List-based canvas selection removed; use the 🗂️ picker button instead.)
