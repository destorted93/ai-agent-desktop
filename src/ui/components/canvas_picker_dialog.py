"""Canvas picker dialog for Canvas Studio.

This dialog lets you choose the current canvas without permanently occupying screen space.
It always pulls previews on-demand while you change selection.
"""

from __future__ import annotations

import base64
import time
import uuid
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...appcore.runtime_context import Runtime


class CanvasPickerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose Canvas")
        self.setModal(True)
        self.resize(860, 560)

        self._canvases: List[Dict[str, Any]] = []

        # Preview state
        self._preview_orig: Optional[QPixmap] = None
        self._preview_timer: Optional[QTimer] = None
        self._preview_req_token: int = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("Canvases")
        title.setStyleSheet("font-size: 12px; font-weight: 700;")
        root.addWidget(title)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.search.setStyleSheet(
            "QLineEdit { background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #333; border-radius: 8px; padding: 8px 10px; }"
        )
        root.addWidget(self.search)

        body = QHBoxLayout()
        body.setSpacing(12)

        # Left: list
        self.list = QListWidget()
        self.list.setStyleSheet(
            "QListWidget { background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #333; border-radius: 8px; }"
            "QListWidget::item { padding: 8px; }"
            "QListWidget::item:selected { background-color: rgba(77,166,255,0.22); }"
        )
        body.addWidget(self.list, 1)

        # Right: preview
        preview_wrap = QWidget()
        pw_l = QVBoxLayout(preview_wrap)
        pw_l.setContentsMargins(0, 0, 0, 0)
        pw_l.setSpacing(6)

        ptitle = QLabel("Preview")
        ptitle.setStyleSheet("font-weight: 700;")
        pw_l.addWidget(ptitle)

        self.preview = QLabel("Select a canvas")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet(
            "QLabel { background-color: #111111; color: #9aa4b2; border: 1px solid #333; border-radius: 8px; }"
        )
        self.preview.setMinimumSize(360, 260)
        pw_l.addWidget(self.preview, 1)

        self.preview_meta = QLabel("")
        self.preview_meta.setStyleSheet("color: #9aa4b2;")
        self.preview_meta.setWordWrap(True)
        pw_l.addWidget(self.preview_meta, 0)

        body.addWidget(preview_wrap, 1)

        root.addLayout(body, 1)

        self.list.itemSelectionChanged.connect(self._on_selection_changed)
        self.list.itemDoubleClicked.connect(lambda _it: self.accept())

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

        self.setStyleSheet("QDialog { background-color: #252526; }")

    # -----------------------------
    # Public API
    # -----------------------------

    def set_canvases(self, canvases: List[Dict[str, Any]], current_canvas_id: Optional[str]) -> None:
        self._canvases = [c for c in (canvases or []) if isinstance(c, dict)]
        self.list.clear()

        cur = str(current_canvas_id or "")
        for c in self._canvases:
            cid = str(c.get("canvas_id") or "")
            name = str(c.get("name") or "Untitled")
            w = c.get("width")
            h = c.get("height")
            label = f"{name}  ({w}×{h})"
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, cid)
            it.setFont(QFont("Segoe UI", 10))
            if cur and cid == cur:
                it.setText(f"✓ {label}")
                it.setSelected(True)
            self.list.addItem(it)

        # Ensure selected item is visible.
        try:
            items = self.list.selectedItems()
            if items:
                self.list.scrollToItem(items[0])
        except Exception:
            pass

        # Reset search.
        try:
            self.search.setText("")
        except Exception:
            pass

        # Kick preview if something was pre-selected.
        self._on_selection_changed()

    def selected_canvas_id(self) -> Optional[str]:
        items = self.list.selectedItems()
        if not items:
            return None
        cid = str(items[0].data(Qt.ItemDataRole.UserRole) or "").strip()
        return cid or None

    # -----------------------------
    # Preview
    # -----------------------------

    def resizeEvent(self, event) -> None:
        try:
            super().resizeEvent(event)
        except Exception:
            pass
        self._apply_preview_fit()

    def _apply_preview_fit(self) -> None:
        pm = self._preview_orig
        if pm is None or pm.isNull():
            return
        try:
            w = max(1, int(self.preview.width() - 16))
            h = max(1, int(self.preview.height() - 16))
            fitted = pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.preview.setPixmap(fitted)
            self.preview.setText("")
        except Exception:
            pass

    def _on_selection_changed(self) -> None:
        cid = self.selected_canvas_id()
        if not cid:
            self._preview_orig = None
            try:
                self.preview.setPixmap(QPixmap())
            except Exception:
                pass
            self.preview.setText("Select a canvas")
            self.preview_meta.setText("")
            return

        # Best-effort meta line from cached list.
        try:
            for c in self._canvases:
                if str(c.get("canvas_id") or "") == cid:
                    nm = str(c.get("name") or "Untitled")
                    w = c.get("width")
                    h = c.get("height")
                    self.preview_meta.setText(f"{nm}  ({w}×{h})")
                    break
        except Exception:
            pass

        # Debounce preview fetch a little (arrow-key browsing).
        try:
            if self._preview_timer is None:
                self._preview_timer = QTimer(self)
                self._preview_timer.setSingleShot(True)
                self._preview_timer.timeout.connect(self._fetch_preview_for_selected)
            self._preview_timer.stop()
            self._preview_timer.start(80)
        except Exception:
            self._fetch_preview_for_selected()

    def _fetch_preview_for_selected(self) -> None:
        cid = self.selected_canvas_id()
        if not cid:
            return

        self._preview_req_token += 1
        token = int(self._preview_req_token)

        try:
            self.preview.setText("Loading…")
            self.preview.setPixmap(QPixmap())
        except Exception:
            pass

        bus = Runtime.get_event_bus()
        reply_topic = f"canvas.picker.reply.get_image.{uuid.uuid4().hex}"
        done = {"ready": False, "payload": None}
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
            done["ready"] = True

        unsub = bus.subscribe(reply_topic, _on_reply)
        bus.publish("canvas.cmd.get_image", {"canvas_id": cid, "reply_topic": reply_topic})

        t0 = time.time()
        timer = QTimer(self)
        timer.setInterval(15)

        def _tick():
            # Ensure delivery even if the global pump is starved.
            try:
                bus.pump(max_events=50)
            except Exception:
                pass

            # If selection changed, abandon.
            if token != self._preview_req_token:
                try:
                    timer.stop()
                    timer.deleteLater()
                except Exception:
                    pass
                try:
                    if unsub:
                        unsub()
                except Exception:
                    pass
                return

            if done.get("ready"):
                try:
                    timer.stop()
                    timer.deleteLater()
                except Exception:
                    pass

                resp = done.get("payload") or {}
                if not isinstance(resp, dict) or resp.get("status") != "success":
                    self.preview.setText("(preview unavailable)")
                    return

                b64 = resp.get("png_b64")
                if not isinstance(b64, str) or not b64:
                    self.preview.setText("(preview unavailable)")
                    return

                try:
                    raw = base64.b64decode(b64)
                    pm = QPixmap()
                    ok = pm.loadFromData(raw, "PNG")
                    if not ok or pm.isNull():
                        self.preview.setText("(bad preview)")
                        return
                    self._preview_orig = pm
                    self._apply_preview_fit()
                except Exception:
                    self.preview.setText("(preview error)")
                return

            if (time.time() - t0) > 2.5:
                try:
                    timer.stop()
                    timer.deleteLater()
                except Exception:
                    pass
                try:
                    if unsub:
                        unsub()
                except Exception:
                    pass
                self.preview.setText("(preview timeout)")

        timer.timeout.connect(_tick)
        timer.start()

    # -----------------------------
    # Filter
    # -----------------------------

    def _apply_filter(self) -> None:
        q = (self.search.text() or "").strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            if not q:
                it.setHidden(False)
                continue
            txt = (it.text() or "").lower()
            it.setHidden(q not in txt)
