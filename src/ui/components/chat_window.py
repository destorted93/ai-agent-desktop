import base64
import json
import html
import math
from typing import Any, List, Optional, Dict
import os
import sys
import subprocess
import traceback
from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QHBoxLayout, QMenu,  QScrollArea, QLabel, QSizePolicy, QLayout, QDialog, QMessageBox, QTextBrowser, QSizePolicy, QCheckBox
from PyQt6.QtWidgets import QSplitter, QListWidget, QListWidgetItem, QTextEdit, QDialogButtonBox, QFileDialog, QWidgetAction, QGridLayout, QTableView, QHeaderView, QFrame
from PyQt6.QtGui import QAction, QFont, QTextOption, QPixmap, QSyntaxHighlighter, QTextCharFormat, QColor, QKeySequence
from PyQt6.QtCore import Qt, QPoint, QEvent, QTimer, QRect, QSize, pyqtSignal, QAbstractTableModel, QItemSelectionModel, QUrl

import markdown
import re
from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.lexers.agile import PythonLexer
from pygments.formatters import HtmlFormatter

from .multiline_input import MultilineInput
from .screenshot_selector import ScreenshotSelector
from .emoji_picker import EmojiPickerWidget
from ..screen_utils import validate_window_position
from PyQt6.QtWidgets import QToolButton
from PyQt6.QtGui import QTextCursor

from ...storage.sandbox_storage import get_sandbox_root
from .canvas_studio import CanvasStudioWindow
from .agents_studio import AgentsStudioWindow



def _build_attachment_chip_widget(
    *,
    path: str,
    kind: Optional[str] = None,
    removable: bool = False,
    on_remove=None,
    on_open=None,
    text_color: str = "#d4d4d4",
    bg: str = "rgba(255,255,255,0.10)",
    bg_hover: str = "rgba(255,255,255,0.16)",
) -> Optional[QWidget]:
    """Reusable attachment chip widget (files/folders).

    Used by:
    - ChatWindow composer (pre-send)
    - EditMessageDialog
    - Chat bubbles
    """
    try:
        if not path or not isinstance(path, str):
            return None

        k = str(kind).strip().lower() if isinstance(kind, str) else ""
        try:
            is_dir = (k == "dir") or (k != "file" and os.path.isdir(path))
        except Exception:
            is_dir = (k == "dir")

        icon_text = "📁" if is_dir else "📄"
        name = os.path.basename(path.rstrip("/\\"))
        if is_dir:
            name = name + "/"

        chip = QWidget()
        chip.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        try:
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
        except Exception:
            pass
        chip.setStyleSheet(
            f"QWidget {{ background-color: {bg}; border-radius: 10px; }}"
            f"QWidget:hover {{ background-color: {bg_hover}; }}"
        )

        hl = QHBoxLayout(chip)
        # Compact pill shape.
        hl.setContentsMargins(6, 1, 5, 1)
        hl.setSpacing(5)

        lbl = QLabel(f"{icon_text} {name}")
        lbl.setStyleSheet(f"QLabel {{ color: {text_color}; font-size: 9pt; background: transparent; }}")
        lbl.setToolTip(path)
        hl.addWidget(lbl)

        if removable and callable(on_remove):
            rm = QToolButton()
            rm.setText("✖")
            rm.setFixedSize(12, 12)
            rm.setToolTip(f"Remove {name}")
            rm.setStyleSheet(
                "QToolButton { background: transparent; color: rgba(255,255,255,0.75); border: none; }"
                "QToolButton:hover { color: #ff6b6b; }"
            )
            rm.clicked.connect(lambda _=False, p=path: on_remove(p))
            hl.addWidget(rm)

        if callable(on_open):
            chip.mousePressEvent = (lambda ev, p=path: on_open(p))  # type: ignore[attr-defined]

        chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        chip.adjustSize()
        return chip

    except Exception:
        return None

class EditMessageDialog(QDialog):
    """Dialog for editing a user message (text + attached images + file/folder attachments) before resending."""

    def __init__(
        self,
        current_text: str,
        images: Optional[List[str]] = None,
        files: Optional[List[Dict[str, Any]]] = None,
        max_images: int = 5,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Edit Message")

        # Enable drag & drop (files/images) into the edit dialog.
        try:
            self.setAcceptDrops(True)
        except Exception:
            pass
        self.setModal(True)
        self.resize(560, 500)
        self.setWindowFlags(Qt.WindowType.Window)

        self._max_images = int(max_images) if max_images else 5
        self._images: List[str] = []
        for img in (images or []):
            norm = self._normalize_image_str(img)
            if norm:
                self._images.append(norm)

        layout = QVBoxLayout(self)

        # File/folder attachments
        self._files: List[Dict[str, Any]] = []
        for it in (files or []):
            if not isinstance(it, dict):
                continue
            p = it.get("path")
            if not isinstance(p, str) or not p.strip():
                continue
            kind = it.get("kind")
            kind = str(kind).strip().lower() if isinstance(kind, str) else ""
            if kind not in ("file", "dir"):
                # Best-effort infer
                try:
                    kind = "dir" if os.path.isdir(p) else "file"
                except Exception:
                    kind = "file"
            self._files.append({"kind": kind, "path": p.strip()})
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # Info label
        info_label = QLabel(
            "Edit your message below. This will delete the original message\n"
            "and all responses after it, then send your edited message."
        )
        info_label.setStyleSheet("QLabel { color: #888888; font-size: 9pt; }")
        layout.addWidget(info_label)

        # Text editor
        self.text_edit = QTextEdit()
        # Give the editor a comfortable default size so the dialog isn't overly cramped.
        self.text_edit.setMinimumHeight(180)
        self.text_edit.setPlainText(current_text or "")
        self.text_edit.setStyleSheet(
            """
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 10px;
                font-size: 10pt;
                font-family: 'Segoe UI', sans-serif;
            }
            """
        )
        layout.addWidget(self.text_edit)

        # Files section
        self.files_header = QLabel("")
        self.files_header.setStyleSheet("QLabel { color: #b5b5b5; font-size: 9pt; }")
        layout.addWidget(self.files_header)

        self.files_container = QWidget()
        self.files_container.setStyleSheet("QWidget { background: transparent; }")
        self.files_layout = FlowLayout(self.files_container, margin=0, spacing=8)

        self.files_scroll = QScrollArea()
        self.files_scroll.setWidgetResizable(True)
        self.files_scroll.setWidget(self.files_container)
        self.files_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.files_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.files_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.files_scroll.setMaximumHeight(120)
        layout.addWidget(self.files_scroll)

        file_btn_style = (
            "QPushButton { background-color: #3d3d3d; color: #d4d4d4; border: none; border-radius: 5px; padding: 6px 14px; font-size: 9pt; }"
            "QPushButton:hover { background-color: #4d4d4d; }"
            "QPushButton:disabled { background-color: #2a2a2a; color: #666666; }"
        )

        file_controls_row = QWidget()
        file_controls_layout = QHBoxLayout(file_controls_row)
        file_controls_layout.setContentsMargins(0, 0, 0, 0)
        file_controls_layout.setSpacing(8)

        self.add_files_btn2 = QPushButton("Add Files/Folders…")
        self.add_files_btn2.setToolTip("Attach files or folders")
        self.add_files_btn2.setStyleSheet(file_btn_style)

        self.clear_files_btn2 = QPushButton("Clear Files")
        self.clear_files_btn2.setToolTip("Remove all attached files/folders")
        self.clear_files_btn2.setStyleSheet(file_btn_style)

        file_controls_layout.addWidget(self.add_files_btn2)
        file_controls_layout.addWidget(self.clear_files_btn2)
        file_controls_layout.addStretch(1)

        layout.addWidget(file_controls_row)

        # Images section
        self.images_header = QLabel("")
        self.images_header.setStyleSheet("QLabel { color: #b5b5b5; font-size: 9pt; }")
        layout.addWidget(self.images_header)

        # Thumbnails (scrollable so additional rows don't get clipped)
        self.thumbs_container = QWidget()
        self.thumbs_container.setStyleSheet("QWidget { background: transparent; border: none; }")
        self.thumbs_layout = FlowLayout(self.thumbs_container, margin=0, spacing=8)

        self.thumbs_scroll = QScrollArea()
        self.thumbs_scroll.setWidgetResizable(True)
        self.thumbs_scroll.setWidget(self.thumbs_container)
        self.thumbs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thumbs_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.thumbs_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        # Reserve enough space by default so the thumbnails area isn't too cramped.
        self.thumbs_scroll.setMinimumHeight(160)
        self.thumbs_scroll.setMaximumHeight(260)
        layout.addWidget(self.thumbs_scroll)

        controls_row = QWidget()
        controls_layout = QHBoxLayout(controls_row)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)

        self.paste_btn = QPushButton("Paste Image")
        self.paste_btn.setToolTip("Paste an image from clipboard")
        self.paste_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #3d3d3d;
                color: #d4d4d4;
                border: none;
                border-radius: 5px;
                padding: 6px 14px;
                font-size: 9pt;
            }
            QPushButton:hover { background-color: #4d4d4d; }
            QPushButton:disabled { background-color: #2a2a2a; color: #666666; }
            """
        )

        self.add_files_btn = QPushButton("Add Image…")
        self.add_files_btn.setToolTip("Add image file(s)")
        self.add_files_btn.setStyleSheet(self.paste_btn.styleSheet())

        self.clear_images_btn = QPushButton("Clear Images")
        self.clear_images_btn.setToolTip("Remove all attached images")
        self.clear_images_btn.setStyleSheet(self.paste_btn.styleSheet())

        controls_layout.addWidget(self.paste_btn)
        controls_layout.addWidget(self.add_files_btn)
        controls_layout.addWidget(self.clear_images_btn)
        controls_layout.addStretch(1)

        layout.addWidget(controls_row)

        # Button box
        button_box = QDialogButtonBox()

        # Track which edit mode the user picked.
        self._undo_file_edits = False

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #3d3d3d;
                color: #d4d4d4;
                border: none;
                border-radius: 5px;
                padding: 8px 20px;
                font-size: 10pt;
            }
            QPushButton:hover { background-color: #4d4d4d; }
            """
        )

        self.keep_btn = QPushButton("Edit message and keep file edits")
        self.keep_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #0e639c;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 20px;
                font-size: 10pt;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #1177bb; }
            QPushButton:disabled { background-color: #2a2a2a; color: #666666; }
            """
        )

        # Default action (Enter) should be the safe one.
        try:
            self.keep_btn.setDefault(True)
        except Exception:
            pass

        # Use the same "danger" tint family as the delete-with-undo affordance.
        self.undo_btn = QPushButton("Edit message and undo file edits")
        self.undo_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #ff7b7b;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 20px;
                font-size: 10pt;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #ff6b6b; }
            QPushButton:disabled { background-color: #2a2a2a; color: #666666; }
            """
        )

        button_box.addButton(self.cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)
        button_box.addButton(self.keep_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.addButton(self.undo_btn, QDialogButtonBox.ButtonRole.AcceptRole)

        self.cancel_btn.clicked.connect(self.reject)

        def _accept_keep():
            self._undo_file_edits = False
            self.accept()

        def _accept_undo():
            self._undo_file_edits = True
            self.accept()

        self.keep_btn.clicked.connect(_accept_keep)
        self.undo_btn.clicked.connect(_accept_undo)

        layout.addWidget(button_box)

        # Style the dialog
        self.setStyleSheet("QDialog { background-color: #252526; }")

        # Wire up
        self.paste_btn.clicked.connect(self._add_image_from_clipboard)
        self.add_files_btn.clicked.connect(self._add_images_from_files)
        self.clear_images_btn.clicked.connect(self._clear_images)
        self.text_edit.textChanged.connect(self._update_send_enabled)

        self.add_files_btn2.clicked.connect(self._add_files_or_folders)
        self.clear_files_btn2.clicked.connect(self._clear_files)
        self._refresh_thumbnails()
        self._refresh_files()
        self._update_send_enabled()

        # Focus text edit and select all
        self.text_edit.setFocus()
        self.text_edit.selectAll()

    def _normalize_image_str(self, img: str) -> Optional[str]:
        if not img or not isinstance(img, str):
            return None
        s = img.strip()
        if not s:
            return None
        # Accept both raw base64 and data URLs.
        if s.startswith("data:image"):
            if "," not in s:
                return None
            s = s.split(",", 1)[1].strip()
        return s or None

    def _pixmap_from_b64(self, b64: str) -> Optional[QPixmap]:
        b64 = self._normalize_image_str(b64)
        if not b64:
            return None
        try:
            raw = base64.b64decode(b64)
        except Exception:
            return None
        pm = QPixmap()
        ok = pm.loadFromData(raw)
        return pm if ok and not pm.isNull() else None

    def _pixmap_to_b64_png(self, pixmap: QPixmap) -> Optional[str]:
        try:
            from PyQt6.QtCore import QBuffer, QIODevice

            if pixmap is None or pixmap.isNull():
                return None
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            pixmap.save(buffer, "PNG")
            buffer.close()
            return base64.b64encode(buffer.data()).decode("utf-8")
        except Exception:
            return None

    def _refresh_files(self) -> None:
        # Clear old widgets
        while self.files_layout.count() > 0:
            item = self.files_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        # Allow click-to-open by delegating to the parent ChatWindow when present.
        open_cb = None
        try:
            par = self.parent()
            if par is not None and hasattr(par, "_open_path_in_explorer"):
                open_cb = lambda p, _par=par: _par._open_path_in_explorer(p)  # type: ignore[attr-defined]
        except Exception:
            open_cb = None

        # Rebuild
        for it in list(self._files):
            if not isinstance(it, dict):
                continue
            path = it.get("path")
            kind = it.get("kind")
            if not isinstance(path, str) or not path:
                continue

            chip = _build_attachment_chip_widget(
                path=path,
                kind=(str(kind) if isinstance(kind, str) else None),
                removable=True,
                on_remove=self._remove_file,
                on_open=open_cb,
                text_color="#d4d4d4",
                bg="rgba(255,255,255,0.08)",
                bg_hover="rgba(255,255,255,0.14)",
            )
            if chip is not None:
                self.files_layout.addWidget(chip)

        self._update_header()
        self._update_send_enabled()

    def _remove_file(self, path: str) -> None:
        if not path:
            return
        kept = []
        for it in (self._files or []):
            if isinstance(it, dict) and it.get("path") != path:
                kept.append(it)
        self._files = kept
        self._refresh_files()

    def _clear_files(self) -> None:
        self._files = []
        self._refresh_files()

    _DROP_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

    def _paths_from_text(self, text: str) -> List[str]:
        """Parse dropped plain-text into local filesystem paths (VS Code style)."""
        if not isinstance(text, str):
            return []

        out: List[str] = []
        for raw in (text or "").splitlines():
            s = (raw or "").strip()
            if not s:
                continue

            # Strip quotes
            if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
                s = s[1:-1].strip()

            if not s:
                continue

            # Handle file:// URLs
            if s.lower().startswith("file:"):
                try:
                    url = QUrl(s)
                    if url.isLocalFile():
                        s = url.toLocalFile()
                except Exception:
                    pass

            try:
                if os.path.exists(s):
                    out.append(s)
            except Exception:
                pass

        return out

    def _extract_drop_paths(self, mime) -> List[str]:
        paths: List[str] = []

        try:
            if mime and mime.hasUrls():
                for url in mime.urls() or []:
                    try:
                        if url.isLocalFile():
                            p = url.toLocalFile()
                            if p:
                                paths.append(p)
                    except Exception:
                        continue
        except Exception:
            pass

        # Fallback: VS Code often uses text/plain
        if not paths:
            try:
                if mime and mime.hasText():
                    paths = self._paths_from_text(mime.text())
            except Exception:
                pass

        return paths

    def _is_image_path(self, path: str) -> bool:
        try:
            ext = os.path.splitext(path)[1].lower()
        except Exception:
            ext = ""
        return ext in self._DROP_IMAGE_EXTS

    def _add_files_from_paths(self, paths: List[str]) -> None:
        out: List[Dict[str, Any]] = list(getattr(self, "_files", []) or [])
        seen = {it.get("path") for it in out if isinstance(it, dict)}

        for p in (paths or []):
            if not isinstance(p, str) or not p:
                continue
            if p in seen:
                continue
            seen.add(p)
            try:
                kind = "dir" if os.path.isdir(p) else "file"
            except Exception:
                kind = "file"
            out.append({"kind": kind, "path": p})

        self._files = out
        self._refresh_files()

    def dragEnterEvent(self, event):
        try:
            md = event.mimeData()
            if md and (md.hasImage() or md.hasUrls()):
                event.acceptProposedAction()
                return
            if md and md.hasText():
                if self._paths_from_text(md.text()):
                    event.acceptProposedAction()
                    return
        except Exception:
            pass
        event.ignore()

    def dragMoveEvent(self, event):
        try:
            md = event.mimeData()
            if md and (md.hasImage() or md.hasUrls()):
                event.acceptProposedAction()
                return
            if md and md.hasText():
                if self._paths_from_text(md.text()):
                    event.acceptProposedAction()
                    return
        except Exception:
            pass
        event.ignore()

    def dropEvent(self, event):
        md = None
        try:
            md = event.mimeData()
        except Exception:
            md = None

        handled_any = False

        # 1) Raw image data
        try:
            if md and md.hasImage() and len(self._images) < int(self._max_images):
                img = md.imageData()
                pm = None
                try:
                    from PyQt6.QtGui import QImage

                    if isinstance(img, QPixmap):
                        pm = img
                    elif isinstance(img, QImage):
                        pm = QPixmap.fromImage(img)
                except Exception:
                    pm = None

                if pm is not None and not pm.isNull():
                    b64 = self._pixmap_to_b64_png(pm)
                    if b64:
                        self._images.append(b64)
                        handled_any = True
        except Exception:
            pass

        # 2) Files/folders (URLs or text paths)
        paths = self._extract_drop_paths(md)
        if paths:
            for p in paths:
                try:
                    # If it's an image file and we have slots, treat it as an image attachment.
                    if (
                        self._is_image_path(p)
                        and os.path.isfile(p)
                        and len(self._images) < int(self._max_images)
                    ):
                        pm = QPixmap(p)
                        if not pm.isNull():
                            b64 = self._pixmap_to_b64_png(pm)
                            if b64:
                                self._images.append(b64)
                                handled_any = True
                                continue

                    # Otherwise treat as file/folder attachment.
                    if os.path.exists(p):
                        self._add_files_from_paths([p])
                        handled_any = True
                except Exception:
                    continue

        if handled_any:
            try:
                self._refresh_thumbnails()
            except Exception:
                pass
            try:
                self._refresh_files()
            except Exception:
                pass
            event.acceptProposedAction()
        else:
            event.ignore()

    def _add_files_or_folders(self) -> None:
        """Show the same small "Attach" chooser as the main chat (+ menu)."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Attach")
        dlg.setModal(True)
        dlg.setStyleSheet("QDialog { background-color: #252526; }")

        picked_paths = {"paths": []}

        v = QVBoxLayout(dlg)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        info = QLabel("Pick files or a folder to attach.\n(Images picked here are treated as files.)")
        info.setStyleSheet("QLabel { color: #b5b5b5; font-size: 9pt; }")
        v.addWidget(info)

        row = QWidget()
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(10)

        btn_files = QPushButton("Files…")
        btn_folder = QPushButton("Folder…")
        for b in (btn_files, btn_folder):
            b.setStyleSheet(
                "QPushButton { background-color: #3d3d3d; color: #d4d4d4; border: none; border-radius: 6px; padding: 8px 14px; font-size: 10pt; }"
                "QPushButton:hover { background-color: #4d4d4d; }"
            )

        row_l.addWidget(btn_files)
        row_l.addWidget(btn_folder)
        row_l.addStretch(1)
        v.addWidget(row)

        def _pick_files():
            try:
                paths, _ = QFileDialog.getOpenFileNames(self, "Select file(s)", "", "All Files (*)")
            except Exception:
                paths = []
            if paths:
                picked_paths["paths"] = list(paths)
                dlg.accept()

        def _pick_folder():
            try:
                folder = QFileDialog.getExistingDirectory(self, "Select folder")
            except Exception:
                folder = ""
            if folder:
                picked_paths["paths"] = [folder]
                dlg.accept()

        btn_files.clicked.connect(_pick_files)
        btn_folder.clicked.connect(_pick_folder)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            paths = picked_paths.get("paths") or []
            if paths:
                self._add_files_from_paths(paths)

    def _update_header(self) -> None:
        n = len(self._images)
        self.images_header.setText(f"Images ({n}/{self._max_images})")

        can_add = n < self._max_images
        self.paste_btn.setEnabled(can_add)
        self.add_files_btn.setEnabled(can_add)
        self.clear_images_btn.setEnabled(n > 0)

        # Files
        nf = len(self._files)
        self.files_header.setText(f"Files/Folders ({nf})")
        self.clear_files_btn2.setEnabled(nf > 0)

    def _update_send_enabled(self) -> None:
        txt = (self.text_edit.toPlainText() or "").strip()
        ok = bool(txt) or (len(self._images) > 0) or (len(getattr(self, "_files", []) or []) > 0)
        try:
            self.keep_btn.setEnabled(ok)
        except Exception:
            pass
        try:
            self.undo_btn.setEnabled(ok)
        except Exception:
            pass

    def _refresh_thumbnails(self) -> None:
        # Clear old widgets
        while self.thumbs_layout.count() > 0:
            item = self.thumbs_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        # Rebuild
        for idx, b64 in enumerate(list(self._images)):
            pm = self._pixmap_from_b64(b64)
            if pm is None:
                continue

            tile = QWidget()
            v = QVBoxLayout(tile)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(4)

            thumb = pm.scaled(
                220,
                140,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            lbl = QLabel()
            lbl.setPixmap(thumb)
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.setStyleSheet(
                "QLabel { border: none; border-radius: 0px; background: transparent; }"
            )
            lbl.mousePressEvent = (lambda event, p=pm: self.parent().show_screenshot_fullsize(p) if self.parent() else None)

            rm = QToolButton()
            rm.setText("✕")
            rm.setToolTip("Remove image")
            rm.setStyleSheet(
                "QToolButton { background-color: rgba(40,40,40,160); color: #d4d4d4; border: 1px solid #444; border-radius: 10px; padding: 2px 6px; }"
                "QToolButton:hover { background-color: #c83232; color: white; }"
            )
            rm.clicked.connect(lambda _=False, i=idx: self._remove_image(i))

            row = QWidget()
            row_l = QHBoxLayout(row)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.addStretch(1)
            row_l.addWidget(rm)

            v.addWidget(lbl)
            v.addWidget(row)

            self.thumbs_layout.addWidget(tile)

        self._update_header()

        # Force geometry recalculation so new rows show up immediately.
        try:
            self.thumbs_container.adjustSize()
            self.thumbs_container.updateGeometry()
            if hasattr(self, "thumbs_scroll") and self.thumbs_scroll:
                self.thumbs_scroll.widget().adjustSize()
                self.thumbs_scroll.viewport().update()
        except Exception:
            pass
        self._update_send_enabled()

    def _remove_image(self, index: int) -> None:
        if 0 <= index < len(self._images):
            self._images.pop(index)
            self._refresh_thumbnails()

    def _clear_images(self) -> None:
        self._images.clear()
        self._refresh_thumbnails()

    def _add_image_from_clipboard(self) -> None:
        if len(self._images) >= self._max_images:
            return
        cb = QApplication.clipboard()
        pm = cb.pixmap()
        if pm is None or pm.isNull():
            img = cb.image()
            if img is not None and not img.isNull():
                pm = QPixmap.fromImage(img)

        if pm is None or pm.isNull():
            QMessageBox.information(self, "No Image", "Clipboard doesn't contain an image.")
            return

        b64 = self._pixmap_to_b64_png(pm)
        if not b64:
            QMessageBox.warning(self, "Paste Error", "Failed to paste image.")
            return

        self._images.append(b64)
        self._refresh_thumbnails()


    def _add_images_from_files(self) -> None:
        if len(self._images) >= self._max_images:
            return

        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select image(s)",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All Files (*)",
        )
        if not paths:
            return

        slots = max(0, self._max_images - len(self._images))
        for p in paths[:slots]:
            pm = QPixmap(p)
            if pm.isNull():
                continue
            b64 = self._pixmap_to_b64_png(pm)
            if b64:
                self._images.append(b64)

        self._refresh_thumbnails()

    def get_text(self) -> str:
        return self.text_edit.toPlainText()

    def get_images_b64(self) -> List[str]:
        # Return raw base64 strings (no data URL prefix) to feed Agent.run(images=...)
        return list(self._images)

    def get_files(self) -> List[Dict[str, Any]]:
        return list(getattr(self, "_files", []) or [])

    def get_undo_file_edits(self) -> bool:
        """Whether the user picked the 'undo file edits' edit mode."""
        return bool(getattr(self, "_undo_file_edits", False))


class FlowLayout(QLayout):
    """Custom layout that wraps items to multiple lines like a flow layout."""
    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self.item_list = []

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item):
        self.item_list.append(item)

    def count(self):
        return len(self.item_list)

    def itemAt(self, index):
        if 0 <= index < len(self.item_list):
            return self.item_list[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self.item_list):
            return self.item_list.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        height = self._do_layout(QRect(0, 0, width, 0), True)
        return height

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.item_list:
            size = size.expandedTo(item.minimumSize())
        margin = self.contentsMargins()
        size += QSize(margin.left() + margin.right(), margin.top() + margin.bottom())
        return size

    def _do_layout(self, rect, test_only):
        x = rect.x()
        y = rect.y()
        line_height = 0
        spacing = self.spacing()

        for item in self.item_list:
            widget = item.widget()
            space_x = spacing
            space_y = spacing
            
            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y()





class NoWheelTextBrowser(QTextBrowser):
    """Text browser that does not consume mouse-wheel events.

    This keeps scrolling consistent (the outer chat scroll area scrolls), and avoids
    Qt font/zoom weirdness when wheel events hit embedded QTextBrowsers.
    """

    def wheelEvent(self, event):
        event.ignore()


    def resizeEvent(self, event):
        super().resizeEvent(event)
        cb = getattr(self, "_auto_height_cb", None)
        if callable(cb):
            cb()


def reveal_in_file_explorer(path: str) -> None:
    """Open the OS file explorer focused on the given path.

    Windows: explorer <folder>  OR  explorer /select,<file>
    macOS: open -R <file>
    Linux: xdg-open <folder>

    Best-effort; fails silently.

    Important Windows quirk:
    - If `p` contains forward slashes (e.g. d:/Repos/...), explorer.exe may treat
      parts as command switches and open the default Documents folder.
      Normalize aggressively.
    """
    try:
        if not path:
            return

        p = str(path)
        try:
            p = os.path.normpath(p)
        except Exception:
            pass

        try:
            p = os.path.abspath(p)
        except Exception:
            pass

        try:
            p = p.rstrip("/\\")
        except Exception:
            pass

        # If target doesn't exist, fall back to its parent folder.
        try:
            if not os.path.exists(p):
                p = os.path.dirname(p)
        except Exception:
            return

        if sys.platform.startswith("win"):
            try:
                if os.path.isdir(p):
                    subprocess.Popen(["explorer", p])
                else:
                    subprocess.Popen(["explorer", "/select,", p])
            except Exception:
                try:
                    os.startfile(p)  # type: ignore[attr-defined]
                except Exception:
                    pass
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", p])
        else:
            folder = p if os.path.isdir(p) else os.path.dirname(p)
            subprocess.Popen(["xdg-open", folder])
    except Exception:
        pass


class ClickablePathBadge(QLabel):
    """A tiny, subtle clickable badge used in CollapsibleBlock headers."""

    def __init__(self, text: str, abs_path: str, parent=None):
        super().__init__(text, parent)
        self.abs_path = abs_path
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(abs_path)
        self.setStyleSheet(
            "QLabel {"
            "  color: #b5b5b5;"
            "  border: 1px solid rgba(255,255,255,0.18);"
            "  border-radius: 4px;"
            "  padding: 1px 6px;"
            "  font-size: 8pt;"
            "}"
            "QLabel:hover {"
            "  color: #ffffff;"
            "  border-color: rgba(255,255,255,0.35);"
            "  background-color: rgba(0,0,0,0.10);"
            "}"
        )

    def mousePressEvent(self, event):
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                reveal_in_file_explorer(self.abs_path)
                event.accept()
                return
        except Exception:
            pass
        super().mousePressEvent(event)



class ClickableCountBadge(QLabel):
    """A tiny clickable numeric badge.

    Used to show the number of items touched by a tool call, with optional hover preview
    and click-to-open details.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.detail_title: str = "Items"
        self.detail_text: str = ""
        self._popup: Optional[QDialog] = None
        try:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        except Exception:
            pass

    def mousePressEvent(self, event):
        try:
            if event.button() != Qt.MouseButton.LeftButton:
                return super().mousePressEvent(event)

            dt = str(getattr(self, "detail_text", "") or "")
            if not dt.strip():
                event.accept()
                return

            # Close any existing popup first.
            try:
                if getattr(self, "_popup", None) is not None:
                    try:
                        self._popup.close()
                    except Exception:
                        pass
                    try:
                        self._popup.deleteLater()
                    except Exception:
                        pass
                    self._popup = None
            except Exception:
                pass

            # Popup panel (auto-closes on outside click)
            dlg = QDialog(self, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
            self._popup = dlg
            dlg.setModal(False)
            dlg.setObjectName("count_badge_popup")

            lay = QVBoxLayout(dlg)
            lay.setContentsMargins(10, 10, 10, 10)
            lay.setSpacing(8)

            te = QTextEdit()
            te.setReadOnly(True)
            try:
                te.setFont(QFont("Consolas", 10))
            except Exception:
                pass
            te.setPlainText(dt)
            lay.addWidget(te, 1)

            row = QHBoxLayout()
            copy_btn = QPushButton("Copy")
            def _copy():
                try:
                    QApplication.clipboard().setText(dt)
                except Exception:
                    pass
            copy_btn.clicked.connect(_copy)
            row.addWidget(copy_btn)
            row.addStretch(1)
            lay.addLayout(row)

            dlg.setStyleSheet(
                "QDialog#count_badge_popup { background: #1e1e1e; border: 1px solid #2a2a2a; border-radius: 8px; }"
                "QTextEdit { background: transparent; color: #d4d4d4; border: none; }"
            )

            # Position under the badge.
            try:
                pos = self.mapToGlobal(QPoint(0, self.height() + 4))
                dlg.move(pos)
            except Exception:
                pass

            # Size: scale with line count (small lists stay small; large lists cap and scroll).
            try:
                from PyQt6.QtGui import QFontMetrics
                fm = QFontMetrics(te.font())
                line_h = int(fm.lineSpacing() or 14)
                line_count = max(1, dt.count("\n") + 1)
                visible_lines = min(line_count, 22)
                te_h = int(visible_lines * line_h + 18)
                te.setFixedHeight(max(90, min(te_h, 420)))

                # Dialog height = text area + margins + copy row.
                dlg_h = int(te.height() + 10 + 10 + 8 + 44)
                dlg.resize(720, max(140, min(dlg_h, 520)))
            except Exception:
                dlg.resize(720, 520)

            # Clear reference when it closes.
            try:
                dlg.destroyed.connect(lambda *_: setattr(self, "_popup", None))
            except Exception:
                pass

            dlg.show()
            event.accept()
            return
        except Exception:
            return super().mousePressEvent(event)



class ClickableDiffBadge(QLabel):
    """A tiny clickable diff preview badge (e.g. +12/-3).

    Uses RichText to color + green and - red.
    """

    def __init__(self, label: str, transaction_id: str, on_click, parent=None):
        super().__init__("", parent)
        self.transaction_id = transaction_id
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"Show diff ({label})")
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setStyleSheet(
            "QLabel {"
            "  border: 1px solid rgba(255,255,255,0.18);"
            "  border-radius: 4px;"
            "  padding: 1px 6px;"
            "  font-size: 8pt;"
            "  font-family: Consolas, monospace;"
            "}"
            "QLabel:hover {"
            "  border-color: rgba(255,255,255,0.35);"
            "  background-color: rgba(0,0,0,0.10);"
            "}"
        )
        self.set_badge_label(label)

    def set_badge_label(self, label: str) -> None:
        s = (label or "").strip()
        if "/" in s:
            left, right = s.split("/", 1)
        else:
            left, right = s, ""

        def esc(x: str) -> str:
            return (
                x.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;")
            )

        green = "#3fb950"
        red = "#ff7b72"
        slash = "#d4d4d4"

        html = (
            f"<span style='color:{green}; font-weight:600'>{esc(left)}</span>"
            + (f"<span style='color:{slash}'>/</span>" if right else "")
            + (f"<span style='color:{red}; font-weight:600'>{esc(right)}</span>" if right else "")
        )
        super().setText(html)

    def mousePressEvent(self, event):
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                if callable(self._on_click):
                    self._on_click(self.transaction_id)
                event.accept()
                return
        except Exception:
            pass
        super().mousePressEvent(event)


class DiffHighlighter(QSyntaxHighlighter):
    """Minimal diff highlighter for unified diffs."""

    def __init__(self, document):
        super().__init__(document)

        def fmt(color_hex: str) -> QTextCharFormat:
            f = QTextCharFormat()
            f.setForeground(QColor(color_hex))
            return f

        self._fmt_add = fmt("#3fb950")
        self._fmt_del = fmt("#ff7b72")
        self._fmt_hunk = fmt("#79c0ff")
        self._fmt_header = fmt("#9aa4b2")

    def highlightBlock(self, text: str) -> None:
        try:
            if text.startswith("+++") or text.startswith("---"):
                self.setFormat(0, len(text), self._fmt_header)
                return
            if text.startswith("@@"):
                self.setFormat(0, len(text), self._fmt_hunk)
                return
            if text.startswith("+") and not text.startswith("+++"):
                self.setFormat(0, len(text), self._fmt_add)
                return
            if text.startswith("-") and not text.startswith("---"):
                self.setFormat(0, len(text), self._fmt_del)
                return
        except Exception:
            return




class SideBySideDiffTableModel(QAbstractTableModel):
    """A virtualized side-by-side diff model (Beyond Compare-ish).

    Columns: L# | Left | R# | Right

    Rows are *aligned* using SequenceMatcher opcodes (insert/delete produce
    blank rows on the opposite side).
    """

    COL_LNO = 0
    COL_LTXT = 1
    COL_RNO = 2
    COL_RTXT = 3

    def __init__(self, rows: List[Dict[str, Any]], parent=None):
        super().__init__(parent)
        self._rows = rows or []

        # Colors tuned for our dark UI.
        self._bg_del = QColor(140, 60, 60, 120)
        self._bg_add = QColor(60, 140, 85, 120)
        self._bg_none = None

    def rowCount(self, parent=None) -> int:  # type: ignore[override]
        return len(self._rows)

    def columnCount(self, parent=None) -> int:  # type: ignore[override]
        return 4

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # type: ignore[override]
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if section == self.COL_LNO:
                return "L#"
            if section == self.COL_LTXT:
                return "Before"
            if section == self.COL_RNO:
                return "R#"
            if section == self.COL_RTXT:
                return "After"
        return None

    def flags(self, index):  # type: ignore[override]
        if not index.isValid():
            return Qt.ItemFlag.ItemIsEnabled
        col = index.column()
        if col in (self.COL_LTXT, self.COL_RTXT):
            return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        # Line number gutters: visible but not selectable.
        return Qt.ItemFlag.ItemIsEnabled

    def data(self, index, role: int = Qt.ItemDataRole.DisplayRole):  # type: ignore[override]
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()
        if row < 0 or row >= len(self._rows):
            return None

        r = self._rows[row]
        kind = r.get("kind")

        if role == Qt.ItemDataRole.DisplayRole:
            if col == self.COL_LNO:
                v = r.get("lno")
                return "-" if v is None else str(v)
            if col == self.COL_LTXT:
                return r.get("left") or ""
            if col == self.COL_RNO:
                v = r.get("rno")
                return "-" if v is None else str(v)
            if col == self.COL_RTXT:
                return r.get("right") or ""

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (self.COL_LNO, self.COL_RNO):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.BackgroundRole:
            # Replace = red on left, green on right.
            if kind in ("delete", "replace") and col in (self.COL_LNO, self.COL_LTXT):
                return self._bg_del
            if kind in ("insert", "replace") and col in (self.COL_RNO, self.COL_RTXT):
                return self._bg_add
            return None

        return None




class RunReceiptBlock(QWidget):
    """Compact end-of-run diff receipt.

    UX rules (Phase B):
    - Click path -> reveal in file explorer.
    - Click +X/-Y -> open the consolidated run diff viewer.
    - No preview/expand modes (show all files).
    """

    def __init__(
        self,
        run_summary: Dict[str, Any],
        parent=None,
        *,
        project_root: str,
        on_open_run_diff=None,
    ):
        super().__init__(parent)
        self._run_summary = run_summary if isinstance(run_summary, dict) else {}
        self._project_root = str(project_root or os.getcwd())
        self._on_open_run_diff = on_open_run_diff

        self._files = self._run_summary.get("files_changed") if isinstance(self._run_summary.get("files_changed"), list) else []
        count = int(self._run_summary.get("files_changed_count") or 0)
        eph_count = int(self._run_summary.get("files_ephemeral_count") or 0)
        diff_totals = self._run_summary.get("diff_totals") if isinstance(self._run_summary.get("diff_totals"), dict) else None
        run_id = str(self._run_summary.get("run_id") or "")

        self._show_ephemeral = False
        self._ephemeral_rows: List[QWidget] = []

        self.setStyleSheet(
            "QWidget { background-color: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.14); border-radius: 8px; }"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        # Header
        hdr = QWidget()
        hdr_l = QHBoxLayout(hdr)
        hdr_l.setContentsMargins(0, 0, 0, 0)
        hdr_l.setSpacing(8)

        # Title should reflect that our diff index can include both files and directory-only ops.
        file_count = 0
        dir_count = 0
        try:
            for f in (self._files or []):
                if not isinstance(f, dict):
                    continue
                kb = str(f.get("kind_before") or "")
                ka = str(f.get("kind_after") or "")
                if kb == "dir" or ka == "dir" or bool(f.get("dir_only")):
                    dir_count += 1
                else:
                    file_count += 1
        except Exception:
            file_count = count
            dir_count = 0

        if file_count > 0 and dir_count > 0:
            title_txt = f"Run receipt — {file_count} file(s), {dir_count} dir(s)"
        elif dir_count > 0:
            title_txt = f"Run receipt — {dir_count} dir(s)"
        elif file_count > 0:
            title_txt = f"Run receipt — {file_count} file(s)"
        else:
            title_txt = "Run receipt — no net file changes"
        title = QLabel(title_txt)
        title.setStyleSheet("QLabel { color: #d4d4d4; font-size: 9pt; font-weight: 600; border: none; background: transparent; }")
        hdr_l.addWidget(title, 0)

        # Status pill (visible after restart; especially useful for stopped/error runs).
        try:
            rs = str(self._run_summary.get("run_status") or "").strip().lower()
        except Exception:
            rs = ""

        if rs and rs not in ("success", "completed"):
            try:
                pill = QLabel(rs.upper())
                if rs == "stopped":
                    bg = "rgba(255, 160, 80, 0.25)"
                    fg = "#ffb07a"
                elif rs == "error":
                    bg = "rgba(255, 90, 90, 0.25)"
                    fg = "#ff7b7b"
                else:
                    bg = "rgba(180, 180, 180, 0.18)"
                    fg = "#b5b5b5"

                pill.setStyleSheet(
                    f"QLabel {{ background-color: {bg}; color: {fg}; border: 1px solid rgba(255,255,255,0.16); border-radius: 8px; padding: 1px 6px; font-size: 8pt; font-weight: 700; }}"
                )
                hdr_l.addWidget(pill, 0)
            except Exception:
                pass
        hdr_l.addStretch(1)

        # Ephemeral toggle (created+deleted during run; net-zero)
        self._btn_ephemeral = None
        if eph_count > 0:
            self._btn_ephemeral = QPushButton(f"Temporary ({eph_count})")
            self._btn_ephemeral.setStyleSheet(
                "QPushButton { background: transparent; color: #b5b5b5; border: none; font-size: 8pt; text-decoration: underline; padding: 0px; }"
                "QPushButton:hover { color: #ffffff; }"
            )
            self._btn_ephemeral.clicked.connect(self._toggle_ephemeral)
            hdr_l.addWidget(self._btn_ephemeral, 0)

        # Run total diff badge (opens run-scoped viewer)
        try:
            if isinstance(diff_totals, dict):
                add = int(diff_totals.get("added_lines", 0) or 0)
                rem = int(diff_totals.get("removed_lines", 0) or 0)
                b = ClickableDiffBadge(f"+{add}/-{rem}", run_id or "__run__", lambda _tid=None: self._open_run_diff(None))
                b.setToolTip("Show diffs for this run")
                hdr_l.addWidget(b, 0)
        except Exception:
            pass

        lay.addWidget(hdr)

        # File rows
        for f in (self._files or []):
            if not isinstance(f, dict):
                continue

            path = f.get("path_after") or f.get("path_before")
            if not isinstance(path, str) or not path:
                continue

            # Resolve to absolute for explorer reveal (respect sandbox scope).
            base_root = self._project_root
            try:
                sc = f.get("scope")
                if isinstance(sc, str) and sc.strip().lower() == "sandbox":
                    base_root = str(get_sandbox_root(ensure_exists=True))
            except Exception:
                base_root = self._project_root

            abs_path = os.path.abspath(os.path.join(str(base_root), path))

            # Per-file diff label.
            label = None
            try:
                if bool(f.get("counts_unknown")) or bool(f.get("is_binary")) or bool(f.get("too_large")):
                    label = "+?/-?"
                else:
                    add = int(f.get("added_lines", 0) or 0)
                    rem = int(f.get("removed_lines", 0) or 0)
                    label = f"+{add}/-{rem}"
            except Exception:
                label = "+?/-?"

            file_key = f.get("file_key")
            file_key = str(file_key) if isinstance(file_key, str) and file_key else None

            row = QWidget()
            row_l = QHBoxLayout(row)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(8)

            # Path badge (reveal in explorer)
            row_l.addWidget(ClickablePathBadge(self._short(path), abs_path), 0)
            row_l.addStretch(1)

            # Diff badge (open viewer)
            if file_key:
                db = ClickableDiffBadge(str(label), run_id or "__run__", lambda _tid=None, fk=file_key: self._open_run_diff(fk))
                db.setToolTip("Show consolidated diff for this file")
                row_l.addWidget(db, 0)

            # Hide ephemeral rows by default (created+deleted within run).
            try:
                if bool(f.get("ephemeral")):
                    row.setVisible(False)
                    self._ephemeral_rows.append(row)
            except Exception:
                pass

            lay.addWidget(row)

    def _short(self, p: str, max_len: int = 72) -> str:
        s = (p or "").strip()
        if len(s) <= max_len:
            return s
        return "…" + s[-(max_len - 1):]

    def _toggle_ephemeral(self) -> None:
        try:
            self._show_ephemeral = not bool(getattr(self, "_show_ephemeral", False))
            for w in getattr(self, "_ephemeral_rows", []) or []:
                try:
                    w.setVisible(self._show_ephemeral)
                except Exception:
                    pass
        except Exception:
            pass

    def _open_run_diff(self, file_key: Optional[str]) -> None:
        if callable(self._on_open_run_diff):
            try:
                self._on_open_run_diff(file_key)
            except Exception:
                return


class SideBySideDiffViewerDialog(QDialog):
    def __init__(
        self,
        transaction_id: Optional[str] = None,
        parent=None,
        initial_file_key: Optional[str] = None,
        *,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        include_ephemeral: bool = False,
    ):
        super().__init__(parent)

        self._scope = "txn" if transaction_id else "run"
        self._txn_id = str(transaction_id) if transaction_id else None
        self._session_id = str(session_id) if session_id else None
        self._run_id = str(run_id) if run_id else None
        self._initial_file_key = str(initial_file_key) if initial_file_key else None

        if self._scope == "run" and (not self._session_id or not self._run_id):
            # Fail closed into a readable empty state.
            self._scope = "txn"
            self._txn_id = "(invalid run scope)"
        self.setWindowTitle("Diff (Side-by-Side)")
        self.resize(1100, 720)
        # Give the dialog real window chrome controls (Windows maximize/minimize).
        try:
            self.setWindowFlags(
                self.windowFlags()
                | Qt.WindowType.WindowMinimizeButtonHint
                | Qt.WindowType.WindowMaximizeButtonHint
            )
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
        except Exception:
            pass
        self.setStyleSheet("QDialog { background-color: #252526; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        title_text = str(self._txn_id or "") if self._scope == "txn" else f"run:{self._run_id}"
        self._title = QLabel(title_text)
        self._title.setStyleSheet("QLabel { color: #b5b5b5; font-size: 9pt; }")
        layout.addWidget(self._title)

        splitter = QSplitter()
        self._outer_splitter = splitter
        splitter.setStyleSheet("QSplitter { background: transparent; }")

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(
            "QListWidget { background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #3d3d3d; }"
            "QListWidget::item:selected { background-color: #0e639c; color: white; }"
        )

        # Right panel: selected file name + side-by-side views.
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        self._file_label = QLabel("")
        self._file_label.setStyleSheet("QLabel { color: #d4d4d4; font-size: 10pt; }")
        right_layout.addWidget(self._file_label)


        # Controls row (navigation + filter)
        controls = QWidget()
        controls_l = QHBoxLayout(controls)
        controls_l.setContentsMargins(0, 0, 0, 0)
        controls_l.setSpacing(8)

        self._btn_prev = QPushButton("Prev")
        self._btn_next = QPushButton("Next")
        for b in (self._btn_prev, self._btn_next):
            b.setStyleSheet(
                "QPushButton { background-color: #3d3d3d; color: #d4d4d4; border: none; border-radius: 5px; padding: 4px 10px; font-size: 9pt; }"
                "QPushButton:hover { background-color: #4d4d4d; }"
            )

        self._chk_diffs_only = QCheckBox("Diffs only")
        self._chk_diffs_only.setStyleSheet("QCheckBox { color: #d4d4d4; font-size: 9pt; }")


        self._chk_ephemeral = QCheckBox("Temp")
        self._chk_ephemeral.setStyleSheet("QCheckBox { color: #d4d4d4; font-size: 9pt; }")
        self._chk_ephemeral.setChecked(bool(include_ephemeral))
        self._chk_ephemeral.setVisible(False)
        self._diff_context_lines = 3  # best-practice default (git-style)
        self._lbl_context = QLabel(f"context: {self._diff_context_lines}")
        self._lbl_context.setStyleSheet("QLabel { color: #888888; font-size: 9pt; }")

        controls_l.addWidget(self._btn_prev)
        controls_l.addWidget(self._btn_next)
        controls_l.addSpacing(10)
        controls_l.addWidget(self._chk_diffs_only)
        controls_l.addWidget(self._chk_ephemeral)
        controls_l.addWidget(self._lbl_context)
        controls_l.addStretch(1)

        right_layout.addWidget(controls)
        inner = QSplitter(Qt.Orientation.Horizontal)
        inner.setStyleSheet("QSplitter { background: transparent; }")

        self.left_table = QTableView()
        self.right_table = QTableView()

        for t in (self.left_table, self.right_table):
            t.setStyleSheet(
                "QTableView { background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #3d3d3d; gridline-color: #2a2a2a; }"
            )
            t.setWordWrap(False)
            t.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
            t.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
            t.verticalHeader().setVisible(False)
            t.setShowGrid(False)
            try:
                t.setFont(QFont("Consolas", 10))
            except Exception:
                pass

        # Static separator bar between Before and After panes.
        self._sep = QFrame()
        self._sep.setFrameShape(QFrame.Shape.VLine)
        self._sep.setFrameShadow(QFrame.Shadow.Sunken)
        self._sep.setStyleSheet("QFrame { color: rgba(255,255,255,0.25); background: rgba(255,255,255,0.15); }")
        self._sep.setFixedWidth(2)

        inner.addWidget(self.left_table)
        inner.addWidget(self._sep)
        inner.addWidget(self.right_table)
        inner.setSizes([480, 2, 480])

        right_layout.addWidget(inner, 1)

        splitter.addWidget(self.list_widget)
        splitter.addWidget(right_panel)
        splitter.setSizes([160, 940])

        layout.addWidget(splitter, 1)


        # Scroll sync (kept simple; good enough for aligned rows).
        try:
            # Vertical sync
            self.left_table.verticalScrollBar().valueChanged.connect(self.right_table.verticalScrollBar().setValue)
            self.right_table.verticalScrollBar().valueChanged.connect(self.left_table.verticalScrollBar().setValue)

            # Horizontal sync (avoid feedback loops)
            def _sync_lr(v: int) -> None:
                try:
                    rb = self.right_table.horizontalScrollBar()
                    if rb.value() != v:
                        rb.setValue(v)
                except Exception:
                    pass

            def _sync_rl(v: int) -> None:
                try:
                    lb = self.left_table.horizontalScrollBar()
                    if lb.value() != v:
                        lb.setValue(v)
                except Exception:
                    pass

            self.left_table.horizontalScrollBar().valueChanged.connect(_sync_lr)
            self.right_table.horizontalScrollBar().valueChanged.connect(_sync_rl)
        except Exception:
            pass
        self._files: List[Dict[str, Any]] = []

        self.list_widget.currentRowChanged.connect(self._on_file_selected)

        # State
        self._rows_full: List[Dict[str, Any]] = []
        self._rows_active: List[Dict[str, Any]] = []
        self._files_all: List[Dict[str, Any]] = []
        self._change_rows: List[int] = []

        # Wire controls
        self._btn_prev.clicked.connect(lambda: self._jump_change(-1))
        self._btn_next.clicked.connect(lambda: self._jump_change(1))
        self._chk_diffs_only.stateChanged.connect(lambda _=0: self._apply_rows_view())
        self._chk_ephemeral.stateChanged.connect(lambda _=0: self._apply_file_list_filter())

        # Load index immediately.
        self._load_index_and_populate()

    # ------------------ bus helpers ------------------

    def keyPressEvent(self, event):
        # Ctrl+C copy support for multi-row selections.
        try:
            if event.matches(QKeySequence.StandardKey.Copy):
                self._copy_selection_to_clipboard()
                return
        except Exception:
            pass
        return super().keyPressEvent(event)

    def _copy_selection_to_clipboard(self) -> None:
        try:
            sm = None
            if hasattr(self, "left_table") and self.left_table:
                sm = self.left_table.selectionModel()
            if sm is None:
                return

            rows = sorted({idx.row() for idx in sm.selectedRows(1)})
            if not rows:
                # fallback: any selected index
                rows = sorted({idx.row() for idx in sm.selectedIndexes()})

            if not rows:
                return

            lines_out: List[str] = []
            model = sm.model()

            for r in rows:
                left_txt = model.data(model.index(r, SideBySideDiffTableModel.COL_LTXT), Qt.ItemDataRole.DisplayRole) or ""
                right_txt = model.data(model.index(r, SideBySideDiffTableModel.COL_RTXT), Qt.ItemDataRole.DisplayRole) or ""
                lines_out.append(f"{left_txt}\t{right_txt}".rstrip())

            txt = "\n".join(lines_out).rstrip() + "\n"
            cb = QApplication.clipboard()
            cb.setText(txt)
        except Exception:
            return

    def _set_model(self, model: QAbstractTableModel) -> None:
        """Apply a model to both panes and keep selection synchronized."""
        if not model:
            return

        try:
            sel = QItemSelectionModel(model)
        except Exception:
            sel = None

        # Left pane: L# + Before
        self.left_table.setModel(model)
        self.left_table.setColumnHidden(SideBySideDiffTableModel.COL_RNO, True)
        self.left_table.setColumnHidden(SideBySideDiffTableModel.COL_RTXT, True)

        # Right pane: R# + After
        self.right_table.setModel(model)
        self.right_table.setColumnHidden(SideBySideDiffTableModel.COL_LNO, True)
        self.right_table.setColumnHidden(SideBySideDiffTableModel.COL_LTXT, True)

        if sel is not None:
            try:
                self.left_table.setSelectionModel(sel)
                self.right_table.setSelectionModel(sel)
            except Exception:
                pass

        for t in (self.left_table, self.right_table):
            try:
                # Allow horizontal scrolling for long code lines.
                t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
                try:
                    t.setHorizontalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
                except Exception:
                    pass
                hh = t.horizontalHeader()
                hh.setStretchLastSection(False)
                hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
                hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft)
            except Exception:
                pass

        try:
            # Column widths for gutters
            self.left_table.setColumnWidth(SideBySideDiffTableModel.COL_LNO, 55)
            self.right_table.setColumnWidth(SideBySideDiffTableModel.COL_RNO, 55)

            # Text columns: initialize; then we auto-size based on current rows.
            self.left_table.setColumnWidth(SideBySideDiffTableModel.COL_LTXT, 900)
            self.right_table.setColumnWidth(SideBySideDiffTableModel.COL_RTXT, 900)
        except Exception:
            pass

    def _autosize_text_columns(self) -> None:
        """Set text column widths based on the longest line in the current active rows.

        QTableView only scrolls *columns*, not inside-cell text, so if the text column is
        too narrow you can never scroll far enough to see long code lines.
        """
        try:
            rows = getattr(self, "_rows_active", None) or getattr(self, "_rows_full", None) or []
            if not isinstance(rows, list) or not rows:
                return

            fm = None
            try:
                fm = self.left_table.fontMetrics()
            except Exception:
                fm = None

            def w(s: str) -> int:
                if not isinstance(s, str):
                    s = str(s)
                if fm is not None:
                    try:
                        return int(fm.horizontalAdvance(s))
                    except Exception:
                        pass
                return int(len(s) * 8)

            max_px = 900
            for r in rows:
                if not isinstance(r, dict):
                    continue
                max_px = max(max_px, w(r.get("left") or ""))
                max_px = max(max_px, w(r.get("right") or ""))

            # Padding + cap (keeps insane one-liners from allocating silly widths)
            max_px = int(min(max_px + 40, 8000))

            self.left_table.setColumnWidth(SideBySideDiffTableModel.COL_LTXT, max_px)
            self.right_table.setColumnWidth(SideBySideDiffTableModel.COL_RTXT, max_px)
        except Exception:
            return

    def _bus_request(self, topic: str, payload: Dict[str, Any], timeout_ms: int = 8000) -> Dict[str, Any]:
        try:
            import time
            import uuid
            from PyQt6.QtWidgets import QApplication
            from ...appcore.runtime_context import Runtime

            bus = Runtime.get_event_bus()
            reply_topic = f"fs_revisions.ui.reply.{topic}.{uuid.uuid4()}"
            result: Dict[str, Any] = {}
            unsub = None

            def _on_reply(ev):
                nonlocal result, unsub
                try:
                    if unsub:
                        unsub()
                except Exception:
                    pass
                unsub = None
                pl = getattr(ev, "payload", {}) or {}
                result = pl if isinstance(pl, dict) else {"status": "error", "message": "Unexpected reply"}

            unsub = bus.subscribe(reply_topic, _on_reply)
            bus.publish(topic, {**(payload or {}), "reply_topic": reply_topic})

            deadline = time.time() + (timeout_ms / 1000.0)
            while not result and time.time() < deadline:
                try:
                    bus.pump(max_events=50)
                except Exception:
                    pass
                try:
                    QApplication.processEvents()
                except Exception:
                    pass
                time.sleep(0.01)

            if not result:
                try:
                    if unsub:
                        unsub()
                except Exception:
                    pass
                return {"status": "error", "message": "Timeout"}

            return result

        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ------------------ load/render ------------------

    def _compute_change_rows(self) -> None:
        """Compute row indices for navigating between diff blocks."""
        rows = self._rows_active or []
        out: List[int] = []
        last_was_change = False
        for i, r in enumerate(rows):
            kind = r.get("kind")
            is_change = kind in ("insert", "delete", "replace")
            if is_change and not last_was_change:
                out.append(i)
            last_was_change = is_change
        self._change_rows = out

        # Enable/disable nav buttons.
        try:
            ok = bool(out)
            self._btn_prev.setEnabled(ok)
            self._btn_next.setEnabled(ok)
        except Exception:
            pass

    def _filter_rows_diffs_only(self, rows_full: List[Dict[str, Any]], context: int) -> List[Dict[str, Any]]:
        if not rows_full:
            return []
        if context is None:
            context = 0
        context = max(0, int(context))

        # Collect indices around changed rows.
        keep = set()
        for i, r in enumerate(rows_full):
            kind = r.get("kind")
            if kind in ("insert", "delete", "replace"):
                lo = max(0, i - context)
                hi = min(len(rows_full) - 1, i + context)
                for j in range(lo, hi + 1):
                    keep.add(j)

        if not keep:
            return []

        return [rows_full[i] for i in range(len(rows_full)) if i in keep]

    def _apply_file_list_filter(self) -> None:
        """Apply run-scope filtering (hide temporary missing→missing artifacts by default) and repopulate list."""
        try:
            files = list(self._files_all or [])
            if self._scope == "run" and not self._chk_ephemeral.isChecked():
                files = [f for f in files if not (isinstance(f, dict) and bool(f.get("ephemeral")))]

            self._files = files

            # Repopulate list widget.
            self.list_widget.clear()
            for f in (self._files or []):
                if not isinstance(f, dict):
                    continue
                pb = f.get("path_before")
                pa = f.get("path_after")
                full_label = str(pa or pb or "(unknown)")
                base = full_label.replace("\\", "/").split("/")[-1]
                it = QListWidgetItem(str(base))
                it.setToolTip(full_label)
                self.list_widget.addItem(it)
                try:
                    fk = f.get("file_key")
                    if isinstance(fk, str):
                        it.setData(Qt.ItemDataRole.UserRole, fk)
                except Exception:
                    pass

            # If there's only one file, hide the list.
            try:
                if len(self._files) <= 1:
                    self.list_widget.setVisible(False)
                    try:
                        self._outer_splitter.setSizes([0, 1])
                    except Exception:
                        pass
                else:
                    self.list_widget.setVisible(True)
                    try:
                        self._outer_splitter.setSizes([160, 940])
                    except Exception:
                        pass
            except Exception:
                pass

            if self.list_widget.count() > 0:
                # Prefer initial_file_key if provided.
                if self._initial_file_key:
                    try:
                        for i in range(self.list_widget.count()):
                            it = self.list_widget.item(i)
                            if it is not None and it.data(Qt.ItemDataRole.UserRole) == self._initial_file_key:
                                self.list_widget.setCurrentRow(i)
                                break
                        else:
                            self.list_widget.setCurrentRow(0)
                    except Exception:
                        self.list_widget.setCurrentRow(0)
                else:
                    try:
                        cur = self.list_widget.currentRow()
                        if cur is None or cur < 0:
                            self.list_widget.setCurrentRow(0)
                    except Exception:
                        self.list_widget.setCurrentRow(0)
            else:
                self._set_message_model("(No files)")
        except Exception:
            return

    def _apply_rows_view(self) -> None:
        """Apply current view settings (diff-only toggle) to the active file."""
        rows_full = list(self._rows_full or [])
        if not rows_full:
            self._rows_active = []
            self._set_message_model("")
            return

        rows_active = rows_full
        if self._chk_diffs_only.isChecked():
            rows_active = self._filter_rows_diffs_only(rows_full, self._diff_context_lines)
            if not rows_active:
                # No diffs (or nothing kept) -> show a friendly empty state.
                rows_active = [{"lno": None, "left": "(No diffs)", "rno": None, "right": "", "kind": "equal"}]

        self._rows_active = rows_active
        model = SideBySideDiffTableModel(rows_active, parent=self.left_table)
        self._set_model(model)
        self._compute_change_rows()
        try:
            self._autosize_text_columns()
        except Exception:
            pass

    def _jump_change(self, direction: int) -> None:
        """Jump to next/prev diff block.

        Old behavior used rowAt(0) (top visible row) which is flaky and can make
        navigation appear broken. We instead anchor off the current selection.
        """
        if not self._change_rows:
            return

        def _current_row() -> int:
            try:
                sm = self.left_table.selectionModel()
                if sm is not None:
                    rows = sm.selectedRows()
                    if rows:
                        return int(rows[0].row())
            except Exception:
                pass
            try:
                idx = self.left_table.currentIndex()
                if idx is not None and idx.isValid():
                    return int(idx.row())
            except Exception:
                pass
            try:
                r0 = self.left_table.rowAt(0)
                return int(r0) if r0 is not None and r0 >= 0 else 0
            except Exception:
                return 0

        cur = _current_row()

        # Find the last change-row <= current row.
        pos = -1
        try:
            for i, r in enumerate(self._change_rows):
                if int(r) <= int(cur):
                    pos = i
                else:
                    break
        except Exception:
            pos = -1

        if direction >= 0:
            pos = (pos + 1) % len(self._change_rows)
        else:
            pos = (pos - 1) % len(self._change_rows)

        target = self._change_rows[pos]

        try:
            self.left_table.selectRow(int(target))
        except Exception:
            pass

        try:
            idx_l = self.left_table.model().index(int(target), SideBySideDiffTableModel.COL_LTXT)
            idx_r = self.right_table.model().index(int(target), SideBySideDiffTableModel.COL_RTXT)
            self.left_table.scrollTo(idx_l, QTableView.ScrollHint.PositionAtCenter)
            self.right_table.scrollTo(idx_r, QTableView.ScrollHint.PositionAtCenter)
        except Exception:
            pass


        # UX: keep horizontal scroll user-controlled. After jumping between hunks,
        # snap back to hard-left so line starts are visible.
        try:
            self.left_table.horizontalScrollBar().setValue(0)
            self.right_table.horizontalScrollBar().setValue(0)
        except Exception:
            pass

    def _load_index_and_populate(self) -> None:
        topic = "fs_revisions.cmd.get_diff_index" if self._scope == "txn" else "fs_revisions.cmd.get_run_diff_index"
        payload = (
            {"transaction_id": self._txn_id}
            if self._scope == "txn"
            else {"session_id": self._session_id, "run_id": self._run_id}
        )
        res = self._bus_request(topic, payload, timeout_ms=8000)

        if not isinstance(res, dict) or res.get("status") != "success":
            msg = (res.get("message") if isinstance(res, dict) else None) or "Failed to load diff index"
            base = str(self._txn_id or "") if self._scope == "txn" else f"run:{self._run_id}"
            self._title.setText(f"{base}   ({msg})")
            self._set_message_model(str(msg))
            return

        self._files_all = res.get("files") if isinstance(res.get("files"), list) else []
        tool = res.get("tool")
        base = str(self._txn_id or "") if self._scope == "txn" else f"run:{self._run_id}"
        suffix = (tool or "txn") if self._scope == "txn" else "run"
        self._title.setText(f"{base}   ({suffix})")

        # Ephemeral toggle only relevant for run-scope.
        try:
            if self._scope == "run":
                eph = sum(1 for f in (self._files_all or []) if isinstance(f, dict) and bool(f.get("ephemeral")))
                self._chk_ephemeral.setVisible(bool(eph))
            else:
                self._chk_ephemeral.setVisible(False)
        except Exception:
            try:
                self._chk_ephemeral.setVisible(False)
            except Exception:
                pass

        self._apply_file_list_filter()


    def _on_file_selected(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._files):
            self._set_message_model("")
            return

        f = self._files[idx]
        if not isinstance(f, dict):
            self._set_message_model("(Invalid entry)")
            return

        file_key = f.get("file_key")
        if not isinstance(file_key, str) or not file_key:
            self._set_message_model("(Missing file key)")
            return

        # Put the full path on top of the panes.
        try:
            pb = f.get("path_before")
            pa = f.get("path_after")
            self._file_label.setText(str(pa or pb or "(unknown file)"))
        except Exception:
            pass

        topic = "fs_revisions.cmd.get_diff_sbs_file" if self._scope == "txn" else "fs_revisions.cmd.get_run_diff_sbs_file"
        payload = (
            {"transaction_id": self._txn_id, "file_key": file_key}
            if self._scope == "txn"
            else {"session_id": self._session_id, "run_id": self._run_id, "file_key": file_key}
        )
        res = self._bus_request(topic, payload, timeout_ms=12000)

        if not isinstance(res, dict) or res.get("status") != "success":
            msg = (res.get("message") if isinstance(res, dict) else None) or "Failed to load file diff"
            self._set_message_model(str(msg))
            return

        if res.get("is_binary"):
            self._set_message_model("Binary file; cannot render")
            return
        if res.get("too_large"):
            msg = res.get("message") or "Too large to render"
            self._set_message_model(str(msg))
            return

        before_lines = res.get("before_lines") if isinstance(res.get("before_lines"), list) else []
        after_lines = res.get("after_lines") if isinstance(res.get("after_lines"), list) else []
        opcodes = res.get("opcodes") if isinstance(res.get("opcodes"), list) else []

        # Build aligned rows (Beyond Compare style).
        rows: List[Dict[str, Any]] = []

        try:
            for oc in opcodes:
                if not isinstance(oc, (list, tuple)) or len(oc) != 5:
                    continue
                tag, i1, i2, j1, j2 = oc
                tag = str(tag)
                i1 = int(i1)
                i2 = int(i2)
                j1 = int(j1)
                j2 = int(j2)

                if tag == "equal":
                    n = min(i2 - i1, j2 - j1)
                    for k in range(n):
                        li = i1 + k
                        rj = j1 + k
                        rows.append({
                            "lno": li + 1,
                            "left": str(before_lines[li]) if li < len(before_lines) else "",
                            "rno": rj + 1,
                            "right": str(after_lines[rj]) if rj < len(after_lines) else "",
                            "kind": "equal",
                        })

                elif tag == "delete":
                    for li in range(i1, i2):
                        rows.append({
                            "lno": li + 1,
                            "left": str(before_lines[li]) if li < len(before_lines) else "",
                            "rno": None,
                            "right": "",
                            "kind": "delete",
                        })

                elif tag == "insert":
                    for rj in range(j1, j2):
                        rows.append({
                            "lno": None,
                            "left": "",
                            "rno": rj + 1,
                            "right": str(after_lines[rj]) if rj < len(after_lines) else "",
                            "kind": "insert",
                        })

                elif tag == "replace":
                    n = max(i2 - i1, j2 - j1)
                    for k in range(n):
                        li = i1 + k
                        rj = j1 + k
                        rows.append({
                            "lno": (li + 1) if li < i2 else None,
                            "left": str(before_lines[li]) if li < i2 and li < len(before_lines) else "",
                            "rno": (rj + 1) if rj < j2 else None,
                            "right": str(after_lines[rj]) if rj < j2 and rj < len(after_lines) else "",
                            "kind": "replace",
                        })

        except Exception:
            rows = []

        if not rows:
            self._set_message_model("(No diff)")
            return

        self._rows_full = rows
        self._apply_rows_view()
        try:
            self._autosize_text_columns()
        except Exception:
            pass


        # UX: when switching files, start at hard-left.
        try:
            self.left_table.horizontalScrollBar().setValue(0)
            self.right_table.horizontalScrollBar().setValue(0)
        except Exception:
            pass
    def _set_message_model(self, msg: str) -> None:
        rows = [{"lno": None, "left": str(msg or ""), "rno": None, "right": "", "kind": "equal"}]
        self._rows_full = rows
        self._chk_diffs_only.setChecked(False)
        self._apply_rows_view()
        try:
            self._autosize_text_columns()
        except Exception:
            pass
class DiffViewerDialog(QDialog):
    def __init__(self, diff_result: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Diff")
        self.resize(900, 650)
        self.setStyleSheet("QDialog { background-color: #252526; }")


        files = diff_result.get("files") if isinstance(diff_result, dict) else None
        files = files if isinstance(files, list) else []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        splitter = QSplitter()
        splitter.setStyleSheet("QSplitter { background: transparent; }")

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(
            "QListWidget { background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #3d3d3d; }"
            "QListWidget::item:selected { background-color: #0e639c; color: white; }"
        )

        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setStyleSheet(
            "QTextEdit { background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #3d3d3d; font-family: Consolas, monospace; font-size: 10pt; }"
        )
        self._highlighter = DiffHighlighter(self.text.document())

        splitter.addWidget(self.list_widget)
        splitter.addWidget(self.text)
        splitter.setSizes([260, 640])

        layout.addWidget(splitter, 1)

        # Populate list
        for idx, f in enumerate(files):
            if not isinstance(f, dict):
                continue
            pb = f.get("path_before")
            pa = f.get("path_after")
            label = pa or pb or "(unknown)"
            add = f.get("added_lines", 0)
            rem = f.get("removed_lines", 0)
            try:
                add_i = int(add or 0)
                rem_i = int(rem or 0)
            except Exception:
                add_i = 0
                rem_i = 0

            suffix = f"   +{add_i}/-{rem_i}" if (add_i or rem_i) else ""
            it = QListWidgetItem(str(label) + suffix)
            it.setData(Qt.ItemDataRole.UserRole, idx)
            self.list_widget.addItem(it)

        def _render_idx(i: int) -> None:
            if i < 0 or i >= len(files):
                self.text.setPlainText("")
                return
            f = files[i]
            if not isinstance(f, dict):
                self.text.setPlainText("")
                return

            if f.get("diff"):
                self.text.setPlainText(str(f.get("diff")))
                return

            # Summary fallback
            if f.get("note"):
                self.text.setPlainText(str(f.get("note")))
                return
            if f.get("is_binary"):
                self.text.setPlainText("Binary changed")
                return
            if f.get("too_large"):
                self.text.setPlainText("Too large to diff")
                return
            self.text.setPlainText("(No diff)")

        self.list_widget.currentRowChanged.connect(_render_idx)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

class CollapsibleBlock(QWidget):
    """A one-line header that expands/collapses a text body on click."""

    def __init__(
        self,
        title: str,
        body_text: str = "",
        collapsed: bool = True,
        header_color: str = "#3d3d3d",
        parent=None,
    ):
        super().__init__(parent)
        self._expanded = not collapsed

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header_row = QWidget()
        header_row.setObjectName("collapsible_header_row")
        # Important: scope styling to THIS widget only.
        # Using a plain `QWidget { ... }` selector leaks borders/backgrounds into child widgets
        # and makes weird extra "bubbles" around the status/title area.
        # Style is applied dynamically so the header + body feel like one expanding card.
        self._header_row = header_row
        self._header_color = header_color
        header_layout = QHBoxLayout(header_row)
        self._header_layout = header_layout
        self._path_badge: Optional[ClickablePathBadge] = None
        self._scope_badge: Optional[QLabel] = None

        # Optional status badge (used for tool calls: pending/success/error).
        self._status_badge: Optional[QLabel] = None
        self._diff_badge: Optional[ClickableDiffBadge] = None
        header_layout.setContentsMargins(8, 4, 8, 4)
        # Optional lead badge (used for special tool kinds, e.g., consult_ariane).
        self._lead_badge: Optional[QLabel] = None
        header_layout.setSpacing(6)

        # Status badge (tool state)
        self._status_badge = QLabel("")
        self._status_badge.setFixedWidth(14)
        self._status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_badge.setStyleSheet(
            "QLabel { border: none; background: transparent; padding: 0px; color: rgba(255,255,255,0.55); font-size: 11pt; }"
        )

        # Lead badge (special marker, e.g. Ariane glyph)
        self._lead_badge = QLabel("")
        self._lead_badge.setFixedWidth(14)
        self._lead_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lead_badge.setVisible(False)
        self._lead_badge.setStyleSheet(
            "QLabel { border: none; background: transparent; padding: 0px; color: #cba6f7; font-size: 10pt; font-weight: 700; }"
        )
        header_layout.addWidget(self._status_badge, 0, alignment=Qt.AlignmentFlag.AlignLeft)
        header_layout.addWidget(self._lead_badge, 0, alignment=Qt.AlignmentFlag.AlignLeft)

        # Arrow toggle (expand/collapse)
        self.toggle_btn = QToolButton()
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(self._expanded)
        self.toggle_btn.setArrowType(Qt.ArrowType.DownArrow if self._expanded else Qt.ArrowType.RightArrow)
        self.toggle_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.toggle_btn.setText("")
        self.toggle_btn.setStyleSheet(
            "QToolButton { border: none; background: transparent; padding: 0px; }"
            "QToolButton:hover { color: #ffffff; }"
        )
        self.toggle_btn.toggled.connect(self.set_expanded)
        header_layout.addWidget(self.toggle_btn, 0, alignment=Qt.AlignmentFlag.AlignLeft)

        # Title: stable position right after the status + arrow (doesn't shift when badges appear).
        self._title_label = QLabel(title or "")
        self._title_label.setStyleSheet(
            "QLabel { border: none; background: transparent; color: #d4d4d4; font-size: 9pt; font-family: 'Segoe UI', sans-serif; }"
        )
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # Clicking the title toggles expand/collapse (nice UX).
        def _title_click(_ev=None):
            try:
                self.toggle_btn.setChecked(not self.toggle_btn.isChecked())
            except Exception:
                pass
        self._title_label.mousePressEvent = _title_click  # type: ignore[assignment]

        # Count badge (used for multi-item tools; e.g., batched filesystem ops)
        self._count_badge = ClickableCountBadge("")
        self._count_badge.setVisible(False)
        self._count_badge.setStyleSheet(
            "QLabel { border: 1px solid rgba(255,255,255,0.22); border-radius: 9px; padding: 0px 6px; font-size: 8pt; font-weight: 700; color: rgba(255,255,255,0.85); background: rgba(0,0,0,0.12); }"
            "QLabel:hover { border-color: rgba(255,255,255,0.40); background: rgba(255,255,255,0.10); color: #ffffff; }"
        )

        # Spacers let us center *some* titles (memory tools) without affecting badge alignment.
        self._title_spacer_left = QWidget()
        self._title_spacer_left.setStyleSheet("QWidget { background: transparent; }")
        self._title_spacer_right = QWidget()
        self._title_spacer_right.setStyleSheet("QWidget { background: transparent; }")

        header_layout.addWidget(self._title_spacer_left, 0)
        header_layout.addWidget(self._title_label, 0)
        header_layout.addWidget(self._count_badge, 0)
        header_layout.addWidget(self._title_spacer_right, 1)

        # Default title alignment is left.
        self.set_title_alignment("left")

        layout.addWidget(header_row)

        self.body = NoWheelTextBrowser()
        self.body.setReadOnly(True)
        self.body.setOpenExternalLinks(True)
        self.body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        opt = self.body.document().defaultTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.body.document().setDefaultTextOption(opt)

        # Use an explicit point-size font (prevents QFont::setPointSize(-1) warnings).
        self.body.setFont(QFont('Consolas', 10))

        # Body style is dynamic: render as a single expandable card (header + body).
        self._apply_body_style()

        self.body.raw_text = ""
        if body_text:
            self.set_body_text(body_text)

        self.body.document().contentsChanged.connect(self._adjust_body_height)
        self.body._auto_height_cb = self._adjust_body_height

        self._body_row = QFrame()
        self._body_row.setObjectName("collapsible_body_row")
        row_layout = QHBoxLayout(self._body_row)
        row_layout.setContentsMargins(8, 6, 8, 8)
        row_layout.setSpacing(0)
        row_layout.addWidget(self.body, 1)

        layout.addWidget(self._body_row)
        self._body_row.setVisible(self._expanded)
        if self._expanded:
            self._adjust_body_height()
    def _adjust_body_height(self) -> None:
        """Grow/shrink the body to fit content (no internal scrolling)."""
        if not self.body.isVisible():
            return
        w = self.body.viewport().width()
        if w <= 0:
            QTimer.singleShot(0, self._adjust_body_height)
            return

        doc = self.body.document()
        doc.setTextWidth(w)

        try:
            height = doc.documentLayout().documentSize().height()
        except Exception:
            height = doc.size().height()

        height = math.ceil(float(height))
        # Body has no internal border/padding; keep the auto-height tight.
        self.body.setFixedHeight(int(height + 8))

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self.toggle_btn.setArrowType(Qt.ArrowType.DownArrow if self._expanded else Qt.ArrowType.RightArrow)
        try:
            self._body_row.setVisible(self._expanded)
        except Exception:
            self.body.setVisible(self._expanded)
        try:
            self._accent_bar.setVisible(self._expanded)
        except Exception:
            pass
        self._apply_body_style()

    def _apply_body_style(self) -> None:
        # Goal: expanded tool blocks should feel like the same widget grows (like injected cards),
        # not like a second "bubble" appears.
        try:
            hc = str(getattr(self, "_header_color", "#3d3d3d"))
            bot_rad = "0px" if getattr(self, "_expanded", False) else "6px"
            self._header_row.setStyleSheet(
                f"""
                #collapsible_header_row {{
                    background-color: {hc};
                    border: 1px solid #2a2a2a;
                    border-top-left-radius: 6px;
                    border-top-right-radius: 6px;
                    border-bottom-left-radius: {bot_rad};
                    border-bottom-right-radius: {bot_rad};
                }}
                """
            )
        except Exception:
            pass

        try:
            if getattr(self, "_body_row", None) is not None:
                if getattr(self, "_expanded", False):
                    self._body_row.setStyleSheet(
                        """
                        QFrame#collapsible_body_row {
                            background-color: #1e1e1e;
                            border-left: 1px solid #2a2a2a;
                            border-right: 1px solid #2a2a2a;
                            border-bottom: 1px solid #2a2a2a;
                            border-top: 0px;
                            border-bottom-left-radius: 6px;
                            border-bottom-right-radius: 6px;
                        }
                        """
                    )
                else:
                    self._body_row.setStyleSheet("QFrame#collapsible_body_row { border: none; background: transparent; }")
        except Exception:
            pass

        try:
            self.body.setStyleSheet(
                """
                QTextBrowser {
                    background: transparent;
                    color: #d4d4d4;
                    border: none;
                    padding: 0px;
                    font-family: 'Consolas', 'Courier New', monospace;
                    font-size: 10pt;
                }
                """
            )
        except Exception:
            pass

        if getattr(self, "_expanded", False):
            self._adjust_body_height()

    def append_text(self, text: str) -> None:
        if text is None:
            return
        if not isinstance(text, str):
            text = str(text)

        cursor = self.body.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.body.setTextCursor(cursor)

        self.body.raw_text = (getattr(self.body, "raw_text", "") + text)
        self._adjust_body_height()

    def set_title(self, title: str) -> None:
        try:
            if hasattr(self, "_title_label") and self._title_label is not None:
                self._title_label.setText(title or "")
            else:
                self.toggle_btn.setText(title or "")
        except Exception:
            pass

    def set_count_badge(
        self,
        count: Optional[int],
        tooltip: Optional[str] = None,
        details_title: Optional[str] = None,
        details_text: Optional[str] = None,
    ) -> None:
        """Set (or clear) a tiny numeric count badge next to the title.

        - tooltip: hover preview
        - details_text: full list shown on click
        """
        try:
            b = getattr(self, "_count_badge", None)
            if b is None:
                return

            if count is None:
                b.setText("")
                b.setVisible(False)
                b.setToolTip("")
                try:
                    b.detail_text = ""
                except Exception:
                    pass
                return

            try:
                n = int(count)
            except Exception:
                n = -1

            if n < 0:
                b.setText("")
                b.setVisible(False)
                b.setToolTip("")
                try:
                    b.detail_text = ""
                except Exception:
                    pass
                return

            b.setText(str(n))
            b.setVisible(True)
            if isinstance(tooltip, str) and tooltip:
                # Tooltips may treat '<...>' as rich text; render safely and predictably.
                try:
                    b.setToolTip("<pre>" + html.escape(tooltip) + "</pre>")
                except Exception:
                    b.setToolTip(tooltip)
            else:
                b.setToolTip("")

            try:
                if isinstance(details_title, str) and details_title.strip():
                    b.detail_title = details_title.strip()
            except Exception:
                pass

            try:
                b.detail_text = str(details_text or "")
            except Exception:
                pass
        except Exception:
            return


    def set_lead_badge(self, text: Optional[str], tooltip: Optional[str] = None) -> None:
        """Set (or clear) a tiny lead badge in the header (left side).

        Used for special markers like consult_ariane.
        """
        try:
            b = getattr(self, "_lead_badge", None)
            if b is None:
                return

            s = (text or "").strip()
            if not s:
                b.setText("")
                b.setVisible(False)
                b.setToolTip("")
                return

            b.setText(s)
            b.setVisible(True)
            if isinstance(tooltip, str) and tooltip:
                b.setToolTip(tooltip)
        except Exception:
            return

    def set_scope_badge(self, label: Optional[str], tooltip: Optional[str] = None) -> None:
        """Attach (or clear) a small scope badge on the header row (e.g., SANDBOX)."""
        try:
            s = (label or "").strip()
            if not s:
                if self._scope_badge is not None:
                    self._scope_badge.setParent(None)
                    self._scope_badge.deleteLater()
                    self._scope_badge = None
                return

            if self._scope_badge is None:
                b = QLabel(s)
                b.setStyleSheet(
                    "QLabel { border: 1px solid rgba(255,255,255,0.22); border-radius: 4px; padding: 1px 6px; font-size: 8pt; font-weight: 700; color: rgba(255,255,255,0.85); background: rgba(0,0,0,0.12); }"
                )
                self._scope_badge = b
            else:
                self._scope_badge.setText(s)

            # Ensure stable ordering among right-side badges.
            # We want SANDBOX to appear *left* of diff/path badges regardless of call order.
            try:
                b = self._scope_badge
                if b is not None:
                    idx_candidates = []
                    try:
                        if getattr(self, "_diff_badge", None) is not None:
                            idx_candidates.append(int(self._header_layout.indexOf(self._diff_badge)))
                    except Exception:
                        pass
                    try:
                        if getattr(self, "_path_badge", None) is not None:
                            idx_candidates.append(int(self._header_layout.indexOf(self._path_badge)))
                    except Exception:
                        pass

                    idx_candidates = [i for i in idx_candidates if i is not None and int(i) >= 0]
                    insert_at = min(idx_candidates) if idx_candidates else self._header_layout.count()

                    # Remove if currently placed elsewhere.
                    try:
                        cur_idx = int(self._header_layout.indexOf(b))
                    except Exception:
                        cur_idx = -1
                    if cur_idx != -1:
                        self._header_layout.removeWidget(b)

                    self._header_layout.insertWidget(insert_at, b, 0, alignment=Qt.AlignmentFlag.AlignRight)
            except Exception:
                pass

            if isinstance(tooltip, str):
                self._scope_badge.setToolTip(tooltip)
        except Exception:
            return

    def set_path_badge(self, label: Optional[str], abs_path: Optional[str]) -> None:
        """Attach (or clear) a clickable path badge on the header row."""
        try:
            # Clear
            if not label or not abs_path:
                if self._path_badge is not None:
                    self._path_badge.setParent(None)
                    self._path_badge.deleteLater()
                    self._path_badge = None
                return

            if self._path_badge is not None:
                self._path_badge.setText(label)
                self._path_badge.abs_path = abs_path
                self._path_badge.setToolTip(abs_path)
                return

            badge = ClickablePathBadge(label, abs_path)
            self._path_badge = badge
            # Place on the right side of the header row.
            self._header_layout.addWidget(badge, 0, alignment=Qt.AlignmentFlag.AlignRight)
        except Exception:
            pass

    def set_title_alignment(self, align: str) -> None:
        """Align the title area ('left' or 'center').

        Implementation: adjust stretch factors around the title label so it stays
        stable and badges remain hard-right.
        """
        try:
            a = (align or "").strip().lower()
            if a not in ("left", "center"):
                a = "left"

            # If we don't have the new layout bits (older sessions), do nothing.
            if not hasattr(self, "_title_spacer_left") or not hasattr(self, "_title_spacer_right"):
                return

            idx_l = self._header_layout.indexOf(self._title_spacer_left)
            idx_r = self._header_layout.indexOf(self._title_spacer_right)

            if a == "center":
                self._header_layout.setStretch(idx_l, 1)
                self._header_layout.setStretch(idx_r, 1)
                try:
                    self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                except Exception:
                    pass
            else:
                # left
                self._header_layout.setStretch(idx_l, 0)
                self._header_layout.setStretch(idx_r, 1)
                try:
                    self._title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                except Exception:
                    pass
        except Exception:
            pass

    def set_diff_badge(self, label: Optional[str], transaction_id: Optional[str], on_click) -> None:
        """Attach (or clear) a clickable diff preview badge on the header row."""
        try:
            if not label or not transaction_id:
                if self._diff_badge is not None:
                    self._diff_badge.setParent(None)
                    self._diff_badge.deleteLater()
                    self._diff_badge = None
                return

            if self._diff_badge is not None:
                # Update existing badge
                try:
                    if hasattr(self._diff_badge, "set_badge_label"):
                        self._diff_badge.set_badge_label(label)
                    else:
                        self._diff_badge.setText(label)
                except Exception:
                    pass
                self._diff_badge.transaction_id = transaction_id
                return

            badge = ClickableDiffBadge(label, transaction_id, on_click)
            self._diff_badge = badge

            # Insert diff badge just before the (rightmost) path badge when present,
            # so the path stays on the far right like in tool-call blocks.
            try:
                if self._path_badge is not None:
                    idx = self._header_layout.indexOf(self._path_badge)
                    if idx != -1:
                        self._header_layout.insertWidget(idx, badge, 0, alignment=Qt.AlignmentFlag.AlignRight)
                        return
            except Exception:
                pass

            self._header_layout.addWidget(badge, 0, alignment=Qt.AlignmentFlag.AlignRight)
        except Exception:
            pass

    def set_body_text(self, text: str) -> None:
        text = text or ""
        self.body.raw_text = text
        self.body.setPlainText(text)
        self._adjust_body_height()

    def set_status(self, status: Optional[str]) -> None:
        """Set a small status badge in the header.

        Intended statuses:
        - None/"": clear
        - "pending": grey dot
        - "success": green check
        - "error": red x
        """
        try:
            if not getattr(self, "_status_badge", None):
                return

            st = (status or "").strip().lower()
            if not st:
                self._status_badge.setText("")
                self._status_badge.setStyleSheet("QLabel { color: rgba(255,255,255,0.55); font-size: 11pt; }")
                return

            if st == "pending":
                self._status_badge.setText("●")
                self._status_badge.setStyleSheet("QLabel { color: rgba(200,200,200,0.55); font-size: 10pt; }")
                return

            if st == "success":
                self._status_badge.setText("✓")
                self._status_badge.setStyleSheet("QLabel { color: rgba(70, 200, 120, 0.95); font-size: 12pt; }")
                return

            if st == "error":
                self._status_badge.setText("✕")
                self._status_badge.setStyleSheet("QLabel { color: rgba(220, 90, 90, 0.95); font-size: 12pt; }")
                return

            # Fallback
            self._status_badge.setText("●")
            self._status_badge.setStyleSheet("QLabel { color: rgba(255,255,255,0.55); font-size: 10pt; }")
        except Exception:
            return

class ChatWindow(QWidget):
    """Separate chat window that maintains its state."""
    
    # Signals for message operations (emitted to parent FloatingWidget)
    delete_message_requested = pyqtSignal(str, bool)  # entry_id, undo_file_edits
    edit_message_requested = pyqtSignal(str, str, object, bool, object)  # entry_id, new_text, images_b64, undo_file_edits, files
    
    def __init__(self, parent=None, widget=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Window)
        self.setWindowTitle("AI Chat")
        self.resize(700, 750)
        # Restore last position if available
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        saved_pos = settings.value("chat_window_pos", None)
        validated_pos = validate_window_position(saved_pos, 700, 750)
        if validated_pos is not None:
            x, y = validated_pos
            self.move(x, y)

        # set token counters
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_tokens = 0
        self.reasoning_tokens = 0
        self.total_tokens = 0
        
        # Enable drag and drop
        self.setAcceptDrops(True)
        self.dropped_files = []
        
        # Screenshot state - now supports multiple screenshots (max 5)
        self.screenshots = []  # List of {"data": base64, "pixmap": QPixmap}
        self.max_screenshots = 5
        
        # Sending state tracking
        self.is_sending = False
        self.send_animation_timer = QTimer()
        self.send_animation_timer.timeout.connect(self.animate_sending)
        self.send_animation_step = 0
        
        # Chat display area
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 0, 5, 5)
        layout.setSpacing(0)
        self._pre_first_signal_widget = None
        self._pre_first_signal_label = None

        # Pre-first-signal indicator (shows while waiting for the first streamed event)
        self._pre_first_signal_has_seen_signal = True
        self.pre_first_signal_timer = QTimer()
        self.pre_first_signal_timer.timeout.connect(self.animate_pre_first_signal)
        self.pre_first_signal_step = 0
        
        # Top toolbar with session dropdown, new chat button, token label, screenshot and clear buttons
        toolbar = QWidget()
        toolbar.setStyleSheet("""
            QWidget {
                background-color: #2d2d2d;
                border-bottom: 1px solid #3d3d3d;
            }
        """)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 5, 10, 5)

        # New chat button (leftmost)
        self.new_chat_button = QPushButton("+")
        self.new_chat_button.setToolTip("Start New Session")
        self.new_chat_button.setFixedSize(28, 28)
        self.new_chat_button.setStyleSheet("""
            QPushButton {
                background-color: #3d3d3d;
                color: #D6B36A !important;
                border: 1px solid #4a4a4a;
                border-radius: 6px;
                font-size: 12pt;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: #D6B36A;
                color: #1e1e1e !important;
                border: 1px solid #E0C07A;
            }
        """)
        toolbar_layout.addWidget(self.new_chat_button)

        # Session dropdown
        from PyQt6.QtWidgets import QComboBox
        self.session_dropdown = QComboBox()
        self.session_dropdown.setObjectName("session_dropdown")
        self.session_dropdown.setFixedHeight(28)
        self.session_dropdown.setStyleSheet("""
            QComboBox#session_dropdown {
                background-color: #23272e;
                color: #d4d4d4;
                border: 1px solid #3d3d3d;
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 10pt;
                min-width: 250px;
            }

            QComboBox#session_dropdown::drop-down {
                border: none;
                width: 22px;
            }

            QComboBox#session_dropdown QAbstractItemView {
                background-color: #23272e;
                color: #d4d4d4;
                border: 1px solid #3d3d3d;
                selection-background-color: #56344F;
                selection-color: white;
                outline: 0;
            }
            QComboBox#session_dropdown QAbstractItemView::item {
                padding: 6px 10px;
            }

            /* Make the popup scrollbar not look like Windows 98 */
            QComboBox#session_dropdown QAbstractItemView QScrollBar:vertical {
                background: #1f2329;
                width: 10px;
                margin: 2px;
                border: none;
            }
            QComboBox#session_dropdown QAbstractItemView QScrollBar::handle:vertical {
                background: #6A4662;
                min-height: 24px;
                border-radius: 4px;
            }
            QComboBox#session_dropdown QAbstractItemView QScrollBar::handle:vertical:hover {
                background: #7A5672;
            }
            QComboBox#session_dropdown QAbstractItemView QScrollBar::add-line:vertical,
            QComboBox#session_dropdown QAbstractItemView QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QComboBox#session_dropdown QAbstractItemView QScrollBar::add-page:vertical,
            QComboBox#session_dropdown QAbstractItemView QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)
        # Populated by FloatingWidget via bus (multi-session)
        self._prev_session_dropdown_index = -1

        # Wire multi-session controls
        self.new_chat_button.clicked.connect(self._on_new_session_clicked)
        self.session_dropdown.currentIndexChanged.connect(self._on_session_dropdown_changed)

        toolbar_layout.addWidget(self.session_dropdown)

        # Group session: participants button
        self.participants_button = QPushButton("👥")
        self.participants_button.setToolTip("Group participants")
        self.participants_button.setFixedSize(32, 28)
        self.participants_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #d4d4d4;
                border: none;
                border-radius: 6px;
                font-size: 12pt;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
            }
        """)
        self.participants_button.clicked.connect(self._on_participants_clicked)
        self.participants_button.setVisible(False)
        toolbar_layout.addWidget(self.participants_button)

        # Left stretch
        toolbar_layout.addStretch(1)

        # Usage/consumption popup button (centered)
        # Icon-only (matches the rest of the toolbar vibe)
        self.usage_button = QPushButton("📊")
        self.usage_button.setToolTip("Usage + context window")
        self.usage_button.setFixedSize(32, 32)
        self.usage_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #d4d4d4;
                border: none;
                border-radius: 6px;
                font-size: 12pt;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
                color: #4da6ff;
            }
        """)
        self.usage_button.clicked.connect(self._on_usage_clicked)
        toolbar_layout.addWidget(self.usage_button)

        # Telemetry toggle (per-session)
        self.telemetry_button = QPushButton("📡")
        self.telemetry_button.setToolTip("Telemetry injection (per session)")
        self.telemetry_button.setFixedSize(32, 32)
        self.telemetry_button.setCheckable(True)
        # Session meta decides initial state; missing => ON.
        self.telemetry_button.setChecked(True)
        # Visual feedback: match the New Session button accent when enabled.
        self.telemetry_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #6f7782;
                border: 1px solid transparent;
                border-radius: 6px;
                font-size: 12pt;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.10);
                color: #D6B36A;
                border: 1px solid rgba(214, 179, 106, 0.45);
            }
            QPushButton:checked {
                background-color: #3d3d3d;
                color: #D6B36A;
                border: 1px solid #4a4a4a;
            }
            QPushButton:checked:hover {
                background-color: rgba(255, 255, 255, 0.10);
                color: #D6B36A;
                border: 1px solid rgba(214, 179, 106, 0.45);
            }
        """)
        self.telemetry_button.setToolTip("Telemetry injection (per session)\nEnabled: shows injected telemetry cards during runs")
        self.telemetry_button.toggled.connect(self._on_telemetry_toggled)
        toolbar_layout.addWidget(self.telemetry_button)

        # Right stretch
        toolbar_layout.addStretch(1)

        # JSON History button
        self.json_button = QPushButton("{ }")
        self.json_button.setToolTip("View Raw Session JSON")
        self.json_button.setFixedSize(32, 32)
        self.json_button.clicked.connect(self.open_json_viewer)
        self.json_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888888 !important;
                border: none;
                border-radius: 5px;
                font-size: 10pt;
                font-weight: bold;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
                color: #4da6ff !important;
            }
        """)
        toolbar_layout.addWidget(self.json_button)

        # Screenshot button (right)
        self.screenshot_button = QPushButton("📸")
        self.screenshot_button.setToolTip("Capture Screenshot")
        self.screenshot_button.setFixedSize(32, 32)
        self.screenshot_button.clicked.connect(self.capture_screenshot)
        self.screenshot_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888888 !important;
                border: none;
                border-radius: 5px;
                font-size: 12pt;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
                color: #4da6ff !important;
            }
        """)
        toolbar_layout.addWidget(self.screenshot_button)

        # Canvas Studio button (right)
        self.canvas_studio_button = QPushButton("🎨")
        self.canvas_studio_button.setToolTip("Open Canvas Studio")
        self.canvas_studio_button.setFixedSize(32, 32)
        self.canvas_studio_button.clicked.connect(self.open_canvas_studio)
        self.canvas_studio_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888888 !important;
                border: none;
                border-radius: 5px;
                font-size: 12pt;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
                color: #bb86fc !important;
            }
        """)
        toolbar_layout.addWidget(self.canvas_studio_button)

        # Agents Studio button (right)
        self.agents_studio_button = QPushButton("🤖")
        self.agents_studio_button.setToolTip("Open Agents Studio")
        self.agents_studio_button.setFixedSize(32, 32)
        self.agents_studio_button.clicked.connect(self.open_agents_studio)
        self.agents_studio_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888888 !important;
                border: none;
                border-radius: 5px;
                font-size: 12pt;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
                color: #bb86fc !important;
            }
        """)
        toolbar_layout.addWidget(self.agents_studio_button)

        # Delete current session button (right)
        self.clear_button = QPushButton("🗑️")
        self.clear_button.setToolTip("Delete Current Session")
        self.clear_button.setFixedSize(32, 32)
        self.clear_button.clicked.connect(self.request_delete_session)
        self.clear_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888888 !important;
                border: none;
                border-radius: 5px;
                font-size: 12pt;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
                color: #ff6b6b !important;
            }
        """)
        toolbar_layout.addWidget(self.clear_button)

        layout.addWidget(toolbar)
        
        # Scrollable chat display
        self.scrollable_area = QScrollArea()
        self.scrollable_area.setWidgetResizable(True)
        self.scrollable_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        try:
            # Prevent horizontal "centering" behavior when content overflows.
            self.scrollable_area.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        except Exception:
            pass


        # Make sure the container can expand to the viewport width (reduces weird reflow on dynamic inserts).
        try:
            sp = self.chat_container.sizePolicy()
            sp.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
            self.chat_container.setSizePolicy(sp)
        except Exception:
            pass
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        # Let chat alignment be dynamic (bottom when short, top when scrollable) to avoid weird empty gaps.
        self.chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.chat_layout.setSpacing(10)
        
        self.scrollable_area.setWidget(self.chat_container)
        layout.addWidget(self.scrollable_area)
        
        # Attached files area (hidden by default)
        self.attached_files_widget = QWidget()
        self.attached_files_widget.hide()
        # Make sure hidden preview areas don’t reserve layout space (prevents “mystery gaps”).
        try:
            sp = self.attached_files_widget.sizePolicy()
            sp.setRetainSizeWhenHidden(False)
            self.attached_files_widget.setSizePolicy(sp)
        except Exception:
            pass

        attached_files_main_layout = QHBoxLayout(self.attached_files_widget)
        # Keep this compact; the chips already provide visual weight.
        attached_files_main_layout.setContentsMargins(2, 2, 2, 2)
        attached_files_main_layout.setSpacing(4)

        # Container for file chips - uses flow layout
        self.files_container = QWidget()
        self.files_container.setStyleSheet("""
            QWidget {
                background-color: #2d2d2d;
                border: 1px solid rgba(255,255,255,0.10);
                border-radius: 8px;
                padding: 2px;
            }
        """)

        # Use FlowLayout for wrapping
        self.files_layout = FlowLayout(self.files_container, margin=2, spacing=4)
        
        attached_files_main_layout.addWidget(self.files_container, 1)
        
        # Clear all button
        self.clear_all_btn = QPushButton("Clear All")
        self.clear_all_btn.setFixedHeight(24)
        self.clear_all_btn.setToolTip("Clear all attached files")
        self.clear_all_btn.clicked.connect(self.clear_attached_files)
        self.clear_all_btn.setStyleSheet("""
            QPushButton {
                background-color: #3d3d3d;
                color: #ff6b6b !important;
                border: none;
                border-radius: 3px;
                font-size: 9pt;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: #ff6b6b;
                color: white !important;
            }
        """)
        
        attached_files_main_layout.addWidget(self.clear_all_btn)
        layout.addWidget(self.attached_files_widget)
        
        # Screenshots preview area (hidden by default)
        self.screenshots_widget = QWidget()
        self.screenshots_widget.hide()
        screenshots_main_layout = QHBoxLayout(self.screenshots_widget)
        # Make sure hidden preview areas don’t reserve layout space (prevents “mystery gaps”).
        try:
            sp = self.screenshots_widget.sizePolicy()
            sp.setRetainSizeWhenHidden(False)
            self.screenshots_widget.setSizePolicy(sp)
        except Exception:
            pass

        screenshots_main_layout.setContentsMargins(5, 5, 5, 5)
        screenshots_main_layout.setSpacing(5)
        
        # Container for screenshot thumbnails - uses flow layout
        self.screenshots_container = QWidget()
        self.screenshots_container.setStyleSheet("""
            QWidget {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 5px;
            }
        """)
        
        # Use FlowLayout for wrapping
        self.screenshots_layout = FlowLayout(self.screenshots_container, margin=5, spacing=5)
        
        screenshots_main_layout.addWidget(self.screenshots_container, 1)
        
        # Clear all screenshots button
        self.clear_screenshots_btn = QPushButton("Clear All")
        self.clear_screenshots_btn.setFixedHeight(24)
        self.clear_screenshots_btn.setToolTip("Clear all screenshots")
        self.clear_screenshots_btn.clicked.connect(self.clear_all_screenshots)
        self.clear_screenshots_btn.setStyleSheet("""
            QPushButton {
                background-color: #3d3d3d;
                color: #ff6b6b !important;
                border: none;
                border-radius: 3px;
                font-size: 9pt;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: #ff6b6b;
                color: white !important;
            }
        """)
        
        screenshots_main_layout.addWidget(self.clear_screenshots_btn)
        layout.addWidget(self.screenshots_widget)
        
        # Composer (expand-on-focus)
        self._composer_popup_open = False
        self._composer_expanded = False
        self._composer_expanded_min_h = 120

        self.composer_widget = QWidget()
        self.composer_widget.setObjectName("composer_widget")
        composer_layout = QHBoxLayout(self.composer_widget)
        composer_layout.setContentsMargins(0, 0, 0, 0)
        composer_layout.setSpacing(8)
        try:
            composer_layout.setAlignment(Qt.AlignmentFlag.AlignBottom)
        except Exception:
            pass

        # + (attach)
        self.plus_button = QToolButton()
        self.plus_button.setText("+")
        self.plus_button.setFixedSize(32, 32)
        self.plus_button.setToolTip("Attach")
        self.plus_button.setStyleSheet(
            "QToolButton { background-color: transparent; color: #4da6ff; border: none; font-size: 18pt; }"
            "QToolButton:hover { color: white; }"
        )

        self.attach_menu = QMenu(self)
        attach_files_folders = QAction("Files / Folders…", self)
        attach_images = QAction("Images…", self)
        self.attach_menu.addAction(attach_files_folders)
        self.attach_menu.addAction(attach_images)
        attach_files_folders.triggered.connect(self.open_files_folders_picker)
        attach_images.triggered.connect(self.open_images_picker)
        self.attach_menu.aboutToShow.connect(lambda: self._set_composer_popup_open(True))
        self.attach_menu.aboutToHide.connect(lambda: self._set_composer_popup_open(False))

        self.plus_button.setMenu(self.attach_menu)
        self.plus_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        # Emoji
        self.emoji_button = QToolButton()
        self.emoji_button.setText("🙂")
        self.emoji_button.setFixedSize(32, 32)
        self.emoji_button.setToolTip("Emoji")
        self.emoji_button.setStyleSheet(
            "QToolButton { background-color: transparent; color: #4da6ff; border: none; font-size: 14pt; }"
            "QToolButton:hover { color: white; }"
        )

        self.emoji_menu = QMenu(self)
        self.emoji_menu.aboutToShow.connect(lambda: self._set_composer_popup_open(True))
        self._emoji_recent = []
        self._emoji_picker_widget = None
        self.emoji_menu.setStyleSheet(
            "QMenu { background-color: #1e1e1e; border: 1px solid #333333; border-radius: 10px; padding: 6px; }"
        )
        self.emoji_menu.aboutToHide.connect(lambda: self._set_composer_popup_open(False))
        self._build_emoji_menu()
        self.emoji_button.clicked.connect(self._show_emoji_menu)

        # Left controls (stacked) — saves horizontal space for the input
        self.composer_left_column = QWidget()
        left_layout = QVBoxLayout(self.composer_left_column)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        left_layout.addStretch(1)
        left_layout.addWidget(self.plus_button)
        left_layout.addWidget(self.emoji_button)

        # Text input
        self.input_field = MultilineInput()
        # Don't auto-focus this on window open (keeps composer compact until an intentional click/tab).
        try:
            self.input_field.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        except Exception:
            pass
        self.input_field.send_message.connect(self.send_message)
        self.input_field.paste_image.connect(self.handle_paste_image)
        self.input_field.paste_files.connect(self.handle_paste_files)
        self.input_field.installEventFilter(self)

        # Send
        self.send_button = QPushButton("➤")
        self.send_button.clicked.connect(self.handle_send_button_click)
        self._apply_send_button_visuals()

        composer_layout.addWidget(self.composer_left_column)
        composer_layout.addWidget(self.input_field, 1)
        composer_layout.addWidget(self.send_button, alignment=Qt.AlignmentFlag.AlignBottom)

        layout.addWidget(self.composer_widget)

        # Keep bottom panels compact; give all extra space to the chat scroll area.
        try:
            layout.setStretchFactor(self.scrollable_area, 1)
            layout.setStretchFactor(self.attached_files_widget, 0)
            layout.setStretchFactor(self.screenshots_widget, 0)
            layout.setStretchFactor(self.composer_widget, 0)
        except Exception:
            pass

        try:
            for w in (self.attached_files_widget, self.screenshots_widget, self.composer_widget):
                sp = w.sizePolicy()
                sp.setVerticalPolicy(QSizePolicy.Policy.Maximum)
                w.setSizePolicy(sp)
        except Exception:
            pass

        self._set_composer_expanded(False)
        
        # Store session
        self.session_entries = []
        self.current_ai_widget = None
        
        # Track pending user message (sent but not yet saved to storage)
        # Collapsible streaming blocks
        self.current_reasoning_block = None
        self._tool_calls_by_id = {}
        self._tool_call_widgets_by_id = {}  # call_id -> CollapsibleBlock widget
        self._tool_output_widgets_by_call_id = {}  # call_id -> last output block widget
        # Wrapper-only tool metadata keyed by call_id (e.g., Ariane subhistory links).
        self._wrap_meta_by_call_id = {}
        # call_id -> list[QWidget] (indent containers) for subhistory rendering
        self._subhistory_widgets_by_call_id = {}
        # Live sub-agent streaming state (parent_call_id -> state)
        # call_id -> list[QWidget] injected-message widgets (e.g., tool-injected user images)
        self._injected_widgets_by_call_id = {}
        self._live_subagent_state_by_parent_call_id = {}
        self._reasoning_line_buffer = ""
        self._last_reasoning_title = None
        self.pending_user_message_widget = None
        self._reasoning_in_fence = False
        self._reasoning_header_base = "Thinking"
        # Scroll/alignment throttles
        self._scroll_pending = False
        self._chat_pin_bottom = None
        self._reasoning_title_rx = re.compile(r"^\s*(?:[-*]\s+)?\*\*(.+?)\*\*\s*$")
        
        # Store widget reference (use widget param if provided, otherwise fallback to parent for backward compatibility)
        self.parent_widget = widget if widget is not None else parent

        # Kill Ctrl+wheel zoom inside the chat window (prevents QFont::setPointSize(-1) spam).
        qt_app = QApplication.instance()
        if qt_app:
            qt_app.installEventFilter(self)

    # -----------------------------------------------------------------
    # Sessions (multi-session UI)
    # -----------------------------------------------------------------

    def set_session_list(self, sessions_meta: List[Dict[str, Any]], active_session_id: Optional[str]) -> None:
        """Populate the dropdown with real sessions.

        We store the UUID `session_id` as itemData, so we never rely on titles.
        """
        try:
            self.session_dropdown.blockSignals(True)
            self.session_dropdown.clear()

            sessions = sessions_meta if isinstance(sessions_meta, list) else []
            for s in sessions:
                if not isinstance(s, dict):
                    continue
                sid = s.get("session_id")
                if not isinstance(sid, str) or not sid:
                    continue

                title = s.get("title")
                if not isinstance(title, str) or not title.strip():
                    title = "New Session"

                desc = s.get("description")
                desc = desc if isinstance(desc, str) else ""

                # Session telemetry toggle (default enabled)
                # Telemetry: default ON if missing
                tele = True
                try:
                    if s.get("telemetry_enabled") is not None:
                        tele = bool(s.get("telemetry_enabled"))
                except Exception:
                    tele = True

                _type = str(s.get("type") or "single").strip().lower()
                if _type == "group":
                    title = "👥 " + title.strip()
                else:
                    title = "🦊 " + title.strip()

                self.session_dropdown.addItem(str(title), sid)
                i = self.session_dropdown.count() - 1
                try:
                    tip = f"Title: {str(title).strip()}"
                    if desc.strip():
                        tip = tip + "\n\nDescription:\n" + desc.strip()
                    self.session_dropdown.setItemData(i, tip, Qt.ItemDataRole.ToolTipRole)
                except Exception:
                    pass


            # Toggle group-only controls based on active session type.
            try:
                active_meta = None
                if active_session_id:
                    for s in sessions:
                        if isinstance(s, dict) and s.get("session_id") == active_session_id:
                            active_meta = s
                            break
                st = str(active_meta.get("type") or "single").strip().lower() if isinstance(active_meta, dict) else "single"
                self.participants_button.setVisible(st == "group")

                # Telemetry toggle state (per-session; default ON if missing)
                tele_on = True
                try:
                    if isinstance(active_meta, dict) and active_meta.get("telemetry_enabled") is not None:
                        tele_on = bool(active_meta.get("telemetry_enabled"))
                except Exception:
                    tele_on = True
                try:
                    if hasattr(self, "telemetry_button"):
                        self.telemetry_button.blockSignals(True)
                        self.telemetry_button.setChecked(bool(tele_on))
                finally:
                    try:
                        self.telemetry_button.blockSignals(False)
                    except Exception:
                        pass

            except Exception:
                try:
                    self.participants_button.setVisible(False)
                except Exception:
                    pass
            # Select active
            if active_session_id:
                idx = self.session_dropdown.findData(active_session_id)
                if idx >= 0:
                    self.session_dropdown.setCurrentIndex(idx)

            # Tooltip on the current session title = full title + description
            try:
                cur_idx = self.session_dropdown.currentIndex()
                cur_tip = self.session_dropdown.itemData(cur_idx, Qt.ItemDataRole.ToolTipRole)
                self.session_dropdown.setToolTip(str(cur_tip) if isinstance(cur_tip, str) else "")
            except Exception:
                pass

            self._prev_session_dropdown_index = self.session_dropdown.currentIndex()
        finally:
            try:
                self.session_dropdown.blockSignals(False)
            except Exception:
                pass


    def _on_participants_clicked(self) -> None:
        if self.is_sending:
            return
        try:
            sid = self.session_dropdown.currentData()
            sid = str(sid) if isinstance(sid, str) else ""
            if not sid:
                return

            # Ask parent widget to open the picker (it owns bus calls + refresh).
            if self.parent_widget and hasattr(self.parent_widget, "open_group_participants_picker"):
                self.parent_widget.open_group_participants_picker(sid)
        except Exception:
            return

    def _on_new_session_clicked(self) -> None:
        if self.is_sending:
            return

        # Minimal chooser: normal Session vs Group Session (Phase 1).
        try:
            menu = QMenu(self)
            act_single = menu.addAction("🦊 New Session")
            act_group = menu.addAction("👥 New Group Session")

            pt = self.new_chat_button.mapToGlobal(QPoint(0, self.new_chat_button.height()))
            chosen = menu.exec(pt)

            if chosen == act_group:
                if self.parent_widget and hasattr(self.parent_widget, "request_new_group_session"):
                    self.parent_widget.request_new_group_session()
                return

            # Default: single
            if self.parent_widget and hasattr(self.parent_widget, "request_new_session"):
                self.parent_widget.request_new_session()
        except Exception:
            if self.parent_widget and hasattr(self.parent_widget, "request_new_session"):
                self.parent_widget.request_new_session()


    def _on_session_dropdown_changed(self, idx: int) -> None:
        # Block switching while running: show toast and revert.
        if self.is_sending:
            try:
                self._show_toast("Currently running")
            except Exception:
                pass
            try:
                self.session_dropdown.blockSignals(True)
                if self._prev_session_dropdown_index >= 0:
                    self.session_dropdown.setCurrentIndex(self._prev_session_dropdown_index)
            finally:
                try:
                    self.session_dropdown.blockSignals(False)
                except Exception:
                    pass
            return

        try:
            # Hover tooltip should reflect the current session's full title + description.
            try:
                tip = self.session_dropdown.itemData(idx, Qt.ItemDataRole.ToolTipRole)
                self.session_dropdown.setToolTip(str(tip) if isinstance(tip, str) else "")
            except Exception:
                pass

            sid = self.session_dropdown.itemData(idx)
        except Exception:
            sid = None

        if not isinstance(sid, str) or not sid:
            self._prev_session_dropdown_index = self.session_dropdown.currentIndex()
            return

        self._prev_session_dropdown_index = idx
        if self.parent_widget and hasattr(self.parent_widget, "request_set_active_session"):
            self.parent_widget.request_set_active_session(sid)
    

    # -----------------------------------------------------------------
    # Usage / token stats popup
    # -----------------------------------------------------------------

    def _fetch_token_usage_stats(self, session_id: str, timeout_ms: int = 2000) -> Dict[str, Any]:
        """Fetch computed token usage stats for a session via EventBus (sync)."""
        try:
            import time
            import uuid
            from PyQt6.QtWidgets import QApplication
            from ...appcore.runtime_context import Runtime

            if not session_id:
                return {"status": "error", "message": "session_id is required"}

            bus = Runtime.get_event_bus()
            reply_topic = f"session.ui.reply.stats.token_usage.{uuid.uuid4()}"
            result: Dict[str, Any] = {}
            unsub = None

            def _on_reply(ev):
                nonlocal result, unsub
                try:
                    if unsub:
                        unsub()
                except Exception:
                    pass
                unsub = None
                payload = getattr(ev, "payload", {}) or {}
                result = payload if isinstance(payload, dict) else {"status": "error"}

            unsub = bus.subscribe(reply_topic, _on_reply)
            bus.publish(
                "session.cmd.stats.get_token_usage",
                {"session_id": session_id, "reply_topic": reply_topic},
            )

            deadline = time.time() + (timeout_ms / 1000.0)
            while not result and time.time() < deadline:
                try:
                    bus.pump(max_events=50)
                except Exception:
                    pass
                try:
                    QApplication.processEvents()
                except Exception:
                    pass
                time.sleep(0.01)

            if not result:
                try:
                    if unsub:
                        unsub()
                except Exception:
                    pass
                return {"status": "error", "message": "Timeout"}

            return result

        except Exception as e:
            return {"status": "error", "message": str(e)}


    def _fetch_run_usage_stats(self, session_id: str, entry_id: str, timeout_ms: int = 2000) -> Dict[str, Any]:
        """Fetch run usage stats (for the run that produced a given entry_id)."""
        try:
            import time
            import uuid
            from PyQt6.QtWidgets import QApplication
            from ...appcore.runtime_context import Runtime

            if not session_id:
                return {"status": "error", "message": "session_id is required"}
            if not entry_id:
                return {"status": "error", "message": "entry_id is required"}

            bus = Runtime.get_event_bus()
            reply_topic = f"session.ui.reply.stats.run_usage.{uuid.uuid4()}"
            result: Dict[str, Any] = {}
            unsub = None

            def _on_reply(ev):
                nonlocal result, unsub
                try:
                    if unsub:
                        unsub()
                except Exception:
                    pass
                unsub = None
                payload = getattr(ev, "payload", {}) or {}
                result = payload if isinstance(payload, dict) else {"status": "error"}

            unsub = bus.subscribe(reply_topic, _on_reply)
            bus.publish(
                "session.cmd.stats.get_run_usage",
                {"session_id": session_id, "entry_id": entry_id, "reply_topic": reply_topic},
            )

            deadline = time.time() + (timeout_ms / 1000.0)
            while not result and time.time() < deadline:
                try:
                    bus.pump(max_events=50)
                except Exception:
                    pass
                try:
                    QApplication.processEvents()
                except Exception:
                    pass
                time.sleep(0.01)

            if not result:
                try:
                    if unsub:
                        unsub()
                except Exception:
                    pass
                return {"status": "error", "message": "Timeout"}

            return result

        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _show_run_usage_popup(self, entry_id: str, anchor_widget: Optional[QWidget] = None) -> None:
        sid = getattr(self.parent_widget, "active_session_id", None) if self.parent_widget else None
        if not isinstance(sid, str) or not sid:
            self._show_toast("No active session")
            return

        stats = self._fetch_run_usage_stats(session_id=sid, entry_id=str(entry_id or ""))
        if not isinstance(stats, dict) or stats.get("status") != "success":
            msg = (stats.get("message") if isinstance(stats, dict) else None) or "Failed to fetch run stats"
            self._show_toast(str(msg))
            return

        # Extract
        run_id = stats.get("run_id") if isinstance(stats.get("run_id"), str) else None
        raw_total = stats.get("token_usage_totals_total") if isinstance(stats.get("token_usage_totals_total"), dict) else None
        raw_main = stats.get("token_usage_totals_main") if isinstance(stats.get("token_usage_totals_main"), dict) else None
        raw_sub = stats.get("token_usage_totals_subagents") if isinstance(stats.get("token_usage_totals_subagents"), dict) else None
        raw = stats.get("token_usage_totals") if isinstance(stats.get("token_usage_totals"), dict) else {}
        if raw_total is None:
            raw_total = raw
        ctx = stats.get("token_usage_last_turn") if isinstance(stats.get("token_usage_last_turn"), dict) else None

        duration_ms = stats.get("duration_ms")
        turns_count = stats.get("turns_count")
        tool_calls_total = stats.get("tool_calls_total")
        tool_errors_total = stats.get("tool_errors_total")
        tool_distinct = stats.get("tool_distinct")
        tool_hist = stats.get("tool_hist") if isinstance(stats.get("tool_hist"), list) else []

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #23272e;
                color: #d4d4d4;
                border: 1px solid #3d3d3d;
            }
        """)

        panel = QWidget()
        panel.setStyleSheet("QWidget { background-color: #23272e; color: #d4d4d4; }")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        title = QLabel("This run")
        title.setStyleSheet("QLabel { color: #ffffff; font-size: 10pt; font-weight: bold; }")
        lay.addWidget(title)

        if run_id:
            rid = QLabel(f"run_id: {run_id[:8]}…")
            rid.setStyleSheet("QLabel { color: #9aa4b2; font-size: 9pt; font-family: Consolas, monospace; }")
            lay.addWidget(rid)

        # Compact meta
        if duration_ms is not None:
            dur = QLabel(f"Run time: {self._fmt_duration_ms_short(duration_ms)}")
            dur.setStyleSheet("QLabel { color: #9aa4b2; font-size: 9pt; }")
            lay.addWidget(dur)

        if turns_count is not None:
            trn = QLabel(f"Turns: {self._fmt_int_dot(turns_count)}")
            trn.setStyleSheet("QLabel { color: #9aa4b2; font-size: 9pt; }")
            lay.addWidget(trn)

        if tool_calls_total is not None:
            errs = self._fmt_int_dot(tool_errors_total) if tool_errors_total is not None else "—"
            tc = QLabel(f"Tool calls: {self._fmt_int_dot(tool_calls_total)}   Errors: {errs}")
            tc.setStyleSheet("QLabel { color: #9aa4b2; font-size: 9pt; }")
            lay.addWidget(tc)

        if isinstance(tool_hist, list) and tool_hist:
            top3 = [it.get("name") for it in tool_hist[:3] if isinstance(it, dict) and isinstance(it.get("name"), str)]
            top3 = [t for t in top3 if t]
            rest = 0
            try:
                rest = max(0, int(tool_distinct or 0) - len(top3))
            except Exception:
                rest = 0
            summary = " · ".join(top3) if top3 else "—"
            if rest > 0:
                summary = summary + f"  (+{rest})"

            top = QLabel(f"Top tools: {summary}")
            top.setObjectName("top_tools_label_run")
            top.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
            top.setMouseTracking(True)
            try:
                top.setCursor(Qt.CursorShape.PointingHandCursor)
            except Exception:
                pass
            top.setStyleSheet("""
                QLabel#top_tools_label_run {
                    color: #9aa4b2;
                    font-size: 9pt;
                    padding: 2px 4px;
                    border-radius: 4px;
                }
                QLabel#top_tools_label_run:hover {
                    background-color: rgba(255, 255, 255, 0.08);
                    color: #d4d4d4;
                }
            """)
            top.setToolTip(self._fmt_tool_hist_tooltip(tool_hist, limit=20))
            lay.addWidget(top)

        # Raw Consumption
        g1 = QLabel("Raw Consumption")
        g1.setStyleSheet("QLabel { color: #ffffff; font-weight: bold; }")
        lay.addWidget(g1)

        # Raw consumption breakdown (main + subagents + total)
        if isinstance(raw_main, dict) or isinstance(raw_sub, dict):
            raw_line_main = QLabel("Main  " + self._fmt_tokens_line(raw_main))
            raw_line_sub = QLabel("Subs  " + self._fmt_tokens_line(raw_sub))
            raw_line_total = QLabel("Total " + self._fmt_tokens_line(raw_total))
            for w in (raw_line_main, raw_line_sub, raw_line_total):
                w.setStyleSheet("QLabel { color: #d4d4d4; font-family: Consolas, monospace; }")
                lay.addWidget(w)
        else:
            raw_line = QLabel(self._fmt_tokens_line(raw))
            raw_line.setStyleSheet("QLabel { color: #d4d4d4; font-family: Consolas, monospace; }")
            lay.addWidget(raw_line)

        # Context Window
        g2 = QLabel("Context Window")
        g2.setStyleSheet("QLabel { color: #ffffff; font-weight: bold; }")
        lay.addWidget(g2)

        ctx_line = QLabel(self._fmt_tokens_line(ctx))
        ctx_line.setStyleSheet("QLabel { color: #d4d4d4; font-family: Consolas, monospace; }")
        lay.addWidget(ctx_line)

        act = QWidgetAction(menu)
        act.setDefaultWidget(panel)
        menu.addAction(act)

        self._run_usage_menu = menu

        try:
            if anchor_widget is not None:
                pos = anchor_widget.mapToGlobal(QPoint(0, anchor_widget.height()))
                menu.popup(pos)
            else:
                menu.popup(self.mapToGlobal(QPoint(0, 0)))
        except Exception:
            menu.popup(self.mapToGlobal(QPoint(0, 0)))
    def _fmt_int_dot(self, n: Any) -> str:
        """Format integers with dot as thousands separator (e.g. 124.457.245)."""
        try:
            n2 = int(n)
        except Exception:
            return "—"
        return f"{n2:,}".replace(",", ".")

    def _fmt_duration_ms_short(self, ms: Any) -> str:
        try:
            ms_i = int(ms or 0)
        except Exception:
            ms_i = 0
        if ms_i <= 0:
            return "—"
        s = ms_i / 1000.0
        if s < 60:
            return f"{s:.1f}s"
        m = int(s // 60)
        ss = int(s % 60)
        if m < 60:
            return f"{m}m {ss:02d}s"
        h = int(m // 60)
        mm = int(m % 60)
        return f"{h}h {mm:02d}m"

    def _fmt_tool_hist_tooltip(self, hist: List[Dict[str, Any]], limit: int = 20) -> str:
        if not isinstance(hist, list) or not hist:
            return "No tool calls yet."
        lines = []
        lim = max(0, int(limit or 0))
        for it in hist[:lim]:
            if not isinstance(it, dict):
                continue
            nm = it.get("name")
            ct = it.get("count")
            if not isinstance(nm, str) or not nm:
                continue
            lines.append(f"{nm}: {self._fmt_int_dot(ct)}")
        rest = len([x for x in hist if isinstance(x, dict)]) - len(lines)
        if rest > 0:
            lines.append(f"… +{rest} more")
        return "\n".join(lines) if lines else "No tool calls yet."

    def _fmt_tokens_line(self, d: Optional[Dict[str, Any]]) -> str:
        if not isinstance(d, dict):
            return "I: —  O: —  C: —  R: —  T: —"
        return (
            "I: " + self._fmt_int_dot(d.get("input_tokens", 0))
            + "  O: " + self._fmt_int_dot(d.get("output_tokens", 0))
            + "  C: " + self._fmt_int_dot(d.get("cached_tokens", 0))
            + "  R: " + self._fmt_int_dot(d.get("reasoning_tokens", 0))
            + "  T: " + self._fmt_int_dot(d.get("total_tokens", 0))
        )

    def _on_usage_clicked(self) -> None:
        sid = getattr(self.parent_widget, "active_session_id", None) if self.parent_widget else None
        if not isinstance(sid, str) or not sid:
            self._show_toast("No active session")
            return

        stats = self._fetch_token_usage_stats(session_id=sid)
        if not isinstance(stats, dict) or stats.get("status") != "success":
            msg = (stats.get("message") if isinstance(stats, dict) else None) or "Failed to fetch stats"
            self._show_toast(str(msg))
            return

        raw_total = stats.get("raw_consumption") if isinstance(stats.get("raw_consumption"), dict) else {}
        raw_main = stats.get("raw_consumption_main") if isinstance(stats.get("raw_consumption_main"), dict) else None
        raw_sub = stats.get("raw_consumption_subagents") if isinstance(stats.get("raw_consumption_subagents"), dict) else None
        ctx = stats.get("context_window") if isinstance(stats.get("context_window"), dict) else None

        raw_persistent_subs = stats.get("raw_consumption_persistent_subagents") if isinstance(stats.get("raw_consumption_persistent_subagents"), dict) else {}
        ctx_persistent_subs = stats.get("context_window_persistent_subagents") if isinstance(stats.get("context_window_persistent_subagents"), dict) else {}

        group_participants_raw_main = stats.get("raw_consumption_group_participants_main") if isinstance(stats.get("raw_consumption_group_participants_main"), dict) else {}
        group_participants_raw_sub = stats.get("raw_consumption_group_participants_subagents") if isinstance(stats.get("raw_consumption_group_participants_subagents"), dict) else {}
        group_participants_raw_total = stats.get("raw_consumption_group_participants_total") if isinstance(stats.get("raw_consumption_group_participants_total"), dict) else {}
        group_participants_ctx_main = stats.get("context_window_group_participants_main") if isinstance(stats.get("context_window_group_participants_main"), dict) else {}
        group_participants_display = stats.get("group_participants_display") if isinstance(stats.get("group_participants_display"), dict) else {}

        n = stats.get("run_summary_count")

        tool_calls_total = stats.get("tool_calls_total")
        tool_errors_total = stats.get("tool_errors_total")
        tool_distinct = stats.get("tool_distinct")
        tool_hist = stats.get("tool_hist") if isinstance(stats.get("tool_hist"), list) else []

        run_duration_total_ms = stats.get("run_duration_total_ms")
        run_turns_total = stats.get("run_turns_total")

        # Use a QMenu as a frameless popup panel that closes on outside click.
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #23272e;
                color: #d4d4d4;
                border: 1px solid #3d3d3d;
            }
        """)

        panel = QWidget()
        panel.setStyleSheet("QWidget { background-color: #23272e; color: #d4d4d4; }")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        title = QLabel("Usage")
        title.setStyleSheet("QLabel { color: #ffffff; font-size: 10pt; font-weight: bold; }")
        lay.addWidget(title)

        # Runs / tools quick stats (kept compact)
        if n is not None:
            try:
                n2 = int(n)
            except Exception:
                n2 = None
            if n2 is not None:
                sub = QLabel(f"Runs: {self._fmt_int_dot(n2)}")
                sub.setStyleSheet("QLabel { color: #9aa4b2; font-size: 9pt; }")
                lay.addWidget(sub)

        if run_duration_total_ms is not None:
            dur = QLabel(f"Run time: {self._fmt_duration_ms_short(run_duration_total_ms)}")
            dur.setStyleSheet("QLabel { color: #9aa4b2; font-size: 9pt; }")
            lay.addWidget(dur)

        if run_turns_total is not None:
            trn = QLabel(f"Turns: {self._fmt_int_dot(run_turns_total)}")
            trn.setStyleSheet("QLabel { color: #9aa4b2; font-size: 9pt; }")
            lay.addWidget(trn)

        if tool_calls_total is not None:
            errs = self._fmt_int_dot(tool_errors_total) if tool_errors_total is not None else "—"
            tc = QLabel(f"Tool calls: {self._fmt_int_dot(tool_calls_total)}   Errors: {errs}")
            tc.setStyleSheet("QLabel { color: #9aa4b2; font-size: 9pt; }")
            lay.addWidget(tc)

        # Top tools (hover for full breakdown)
        if isinstance(tool_hist, list) and tool_hist:
            top3 = [it.get("name") for it in tool_hist[:3] if isinstance(it, dict) and isinstance(it.get("name"), str)]
            top3 = [t for t in top3 if t]
            rest = 0
            try:
                rest = max(0, int(tool_distinct or 0) - len(top3))
            except Exception:
                rest = 0
            summary = " · ".join(top3) if top3 else "—"
            if rest > 0:
                summary = summary + f"  (+{rest})"

            top = QLabel(f"Top tools: {summary}")
            top.setObjectName("top_tools_label")
            top.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
            top.setMouseTracking(True)
            try:
                top.setCursor(Qt.CursorShape.PointingHandCursor)
            except Exception:
                pass
            top.setStyleSheet("""
                QLabel#top_tools_label {
                    color: #9aa4b2;
                    font-size: 9pt;
                    padding: 2px 4px;
                    border-radius: 4px;
                }
                QLabel#top_tools_label:hover {
                    background-color: rgba(255, 255, 255, 0.08);
                    color: #d4d4d4;
                }
            """)
            top.setToolTip(self._fmt_tool_hist_tooltip(tool_hist, limit=20))
            lay.addWidget(top)

        # Raw Consumption
        g1 = QLabel("Raw Consumption")
        g1.setStyleSheet("QLabel { color: #ffffff; font-weight: bold; }")
        lay.addWidget(g1)

        st = "single"
        try:
            st = getattr(self.parent_widget, "active_session_type", "single") if self.parent_widget else "single"
            st = str(st).strip().lower()
        except Exception:
            st = "single"

        if st == "group":
            # Group sessions: participants are peers. Show per-participant Main/Subs/Total.
            raw_line_total = QLabel("Total " + self._fmt_tokens_line(raw_total))
            raw_line_total.setStyleSheet("QLabel { color: #d4d4d4; font-family: Consolas, monospace; }")
            lay.addWidget(raw_line_total)

            try:
                if isinstance(group_participants_raw_total, dict) and group_participants_raw_total:
                    ph = QLabel("Participants")
                    ph.setStyleSheet("QLabel { color: #9aa4b2; font-size: 9pt; font-weight: bold; }")
                    lay.addWidget(ph)

                    def _p_sort(aid: str):
                        try:
                            d = group_participants_raw_total.get(aid)
                            if isinstance(d, dict):
                                return -int(d.get("total_tokens") or 0)
                        except Exception:
                            pass
                        return 0

                    aids = [k for k in group_participants_raw_total.keys() if isinstance(k, str) and k]
                    aids.sort(key=_p_sort)

                    for aid in aids[:8]:
                        dn = group_participants_display.get(aid)
                        nm = str(dn).strip() if isinstance(dn, str) and dn.strip() else aid

                        line_m = QLabel(f"{nm}  Main  " + self._fmt_tokens_line(group_participants_raw_main.get(aid)))
                        line_s = QLabel(f"{nm}  Subs  " + self._fmt_tokens_line(group_participants_raw_sub.get(aid)))
                        line_t = QLabel(f"{nm}  Total " + self._fmt_tokens_line(group_participants_raw_total.get(aid)))

                        for w in (line_m, line_s, line_t):
                            w.setStyleSheet("QLabel { color: #d4d4d4; font-family: Consolas, monospace; }")
                            lay.addWidget(w)
            except Exception:
                pass

            # Context Window (per participant, main)
            g2 = QLabel("Context Window")
            g2.setStyleSheet("QLabel { color: #ffffff; font-weight: bold; }")
            lay.addWidget(g2)

            try:
                if isinstance(group_participants_raw_total, dict) and group_participants_raw_total and isinstance(group_participants_ctx_main, dict):
                    def _p_sort2(aid: str):
                        try:
                            d = group_participants_raw_total.get(aid)
                            if isinstance(d, dict):
                                return -int(d.get("total_tokens") or 0)
                        except Exception:
                            pass
                        return 0

                    aids2 = [k for k in group_participants_raw_total.keys() if isinstance(k, str) and k]
                    aids2.sort(key=_p_sort2)

                    for aid in aids2[:8]:
                        dn = group_participants_display.get(aid)
                        nm = str(dn).strip() if isinstance(dn, str) and dn.strip() else aid
                        line = QLabel(f"{nm}  " + self._fmt_tokens_line(group_participants_ctx_main.get(aid)))
                        line.setStyleSheet("QLabel { color: #d4d4d4; font-family: Consolas, monospace; }")
                        lay.addWidget(line)
                else:
                    ctx_line = QLabel(self._fmt_tokens_line(ctx))
                    ctx_line.setStyleSheet("QLabel { color: #d4d4d4; font-family: Consolas, monospace; }")
                    lay.addWidget(ctx_line)
            except Exception:
                ctx_line = QLabel(self._fmt_tokens_line(ctx))
                ctx_line.setStyleSheet("QLabel { color: #d4d4d4; font-family: Consolas, monospace; }")
                lay.addWidget(ctx_line)

        else:
            # Normal sessions: main + subs + total
            if isinstance(raw_main, dict) or isinstance(raw_sub, dict):
                raw_line_main = QLabel("Main  " + self._fmt_tokens_line(raw_main))
                raw_line_sub = QLabel("Subs  " + self._fmt_tokens_line(raw_sub))
                raw_line_total = QLabel("Total " + self._fmt_tokens_line(raw_total))
                for w in (raw_line_main, raw_line_sub, raw_line_total):
                    w.setStyleSheet("QLabel { color: #d4d4d4; font-family: Consolas, monospace; }")
                    lay.addWidget(w)
            else:
                raw_line = QLabel(self._fmt_tokens_line(raw_total))
                raw_line.setStyleSheet("QLabel { color: #d4d4d4; font-family: Consolas, monospace; }")
                lay.addWidget(raw_line)

            # Persistent sub-agents (mode='persistent')
            try:
                if isinstance(raw_persistent_subs, dict) and raw_persistent_subs:
                    subh = QLabel("Persistent subs")
                    subh.setStyleSheet("QLabel { color: #9aa4b2; font-size: 9pt; font-weight: bold; }")
                    lay.addWidget(subh)

                    def _sort_key(nm: str):
                        try:
                            d = raw_persistent_subs.get(nm)
                            if isinstance(d, dict):
                                return -int(d.get("total_tokens") or 0)
                        except Exception:
                            pass
                        return 0

                    names = [k for k in raw_persistent_subs.keys() if isinstance(k, str) and k]
                    names.sort(key=_sort_key)

                    for nm in names[:8]:
                        line = QLabel(f"{nm}  " + self._fmt_tokens_line(raw_persistent_subs.get(nm)))
                        line.setStyleSheet("QLabel { color: #d4d4d4; font-family: Consolas, monospace; }")
                        lay.addWidget(line)
            except Exception:
                pass

            # Context Window
            g2 = QLabel("Context Window")
            g2.setStyleSheet("QLabel { color: #ffffff; font-weight: bold; }")
            lay.addWidget(g2)

            ctx_line = QLabel(self._fmt_tokens_line(ctx))
            ctx_line.setStyleSheet("QLabel { color: #d4d4d4; font-family: Consolas, monospace; }")
            lay.addWidget(ctx_line)

            # Context Window per persistent sub-agent (mode='persistent')
            try:
                if isinstance(raw_persistent_subs, dict) and raw_persistent_subs and isinstance(ctx_persistent_subs, dict):
                    def _sort_key_ctx(nm: str):
                        try:
                            d = raw_persistent_subs.get(nm)
                            if isinstance(d, dict):
                                return -int(d.get("total_tokens") or 0)
                        except Exception:
                            pass
                        return 0

                    names2 = [k for k in raw_persistent_subs.keys() if isinstance(k, str) and k]
                    names2.sort(key=_sort_key_ctx)

                    for nm in names2[:8]:
                        line = QLabel(f"{nm}  " + self._fmt_tokens_line(ctx_persistent_subs.get(nm)))
                        line.setStyleSheet("QLabel { color: #d4d4d4; font-family: Consolas, monospace; }")
                        lay.addWidget(line)
            except Exception:
                pass

        act = QWidgetAction(menu)
        act.setDefaultWidget(panel)
        menu.addAction(act)

        # Keep a ref so it doesn't get GC'd while open.
        self._usage_menu = menu

        # Show under the button.
        try:
            btn = getattr(self, "usage_button", None)
            if btn is None:
                menu.popup(self.mapToGlobal(QPoint(0, 0)))
                return
            pos = btn.mapToGlobal(QPoint(0, btn.height()))
            menu.popup(pos)
        except Exception:
            menu.popup(self.mapToGlobal(QPoint(0, 0)))

    # ------------------------------
    # Image helpers (shared across user bubbles + injected widgets)
    # ------------------------------

    def _pixmap_from_image_str(self, img: str) -> Optional[QPixmap]:
        """Decode either a data URL or raw base64 string into a QPixmap (or None)."""
        if not img or not isinstance(img, str):
            return None
        s = img.strip()
        if not s:
            return None
        # Accept both raw base64 and data URLs.
        if s.startswith("data:image"):
            if "," not in s:
                return None
            s = s.split(",", 1)[1].strip()
        try:
            raw = base64.b64decode(s)
        except Exception:
            return None

        pm = QPixmap()
        ok = pm.loadFromData(raw)
        return pm if ok and (not pm.isNull()) else None

    def _make_attachment_chip(
        self,
        *,
        path: str,
        kind: Optional[str] = None,
        removable: bool = False,
        on_remove=None,
        text_color: str = "#d4d4d4",
        bg: str = "rgba(255,255,255,0.10)",
        bg_hover: str = "rgba(255,255,255,0.16)",
    ) -> Optional[QWidget]:
        return _build_attachment_chip_widget(
            path=path,
            kind=kind,
            removable=removable,
            on_remove=on_remove,
            on_open=self._open_path_in_explorer,
            text_color=text_color,
            bg=bg,
            bg_hover=bg_hover,
        )

    def _open_path_in_explorer(self, path: str) -> None:
        """Open a file/folder in the OS file explorer (best-effort).

        Note: On Windows, passing paths with forward slashes (e.g. d:/Repos/...) can
        get interpreted as command switches by explorer.exe, which often results in
        it opening the default Documents folder. Normalize aggressively.
        """
        try:
            if not path or not isinstance(path, str):
                return

            p = str(path)

            # Normalize first (prevents explorer treating "/something" as a flag).
            try:
                p = os.path.normpath(p)
            except Exception:
                pass

            # Avoid trailing separators confusing selection/open.
            try:
                p = p.rstrip("/\\")
            except Exception:
                pass

            # If it doesn't exist, don't launch explorer to a random default.
            try:
                if not os.path.exists(p):
                    return
            except Exception:
                return

            if sys.platform.startswith("win"):
                try:
                    if os.path.isdir(p):
                        subprocess.Popen(["explorer", p])
                    else:
                        subprocess.Popen(["explorer", "/select,", p])
                except Exception:
                    try:
                        os.startfile(p)  # type: ignore[attr-defined]
                    except Exception:
                        pass
                return

            if sys.platform == "darwin":
                subprocess.Popen(["open", p])
                return

            subprocess.Popen(["xdg-open", p])
        except Exception:
            return

    def _build_attachment_chips_widget(
        self,
        attachments: List[Dict[str, Any]],
        *,
        removable: bool = False,
        on_remove=None,
        text_color: str = "#d4d4d4",
        bg: str = "rgba(255,255,255,0.10)",
        bg_hover: str = "rgba(255,255,255,0.16)",
    ) -> Optional[QWidget]:
        """Build FlowLayout chips for file/folder attachments.

        attachments: [{"kind": "file"|"dir", "path": "..."}, ...]
        """
        try:
            if not isinstance(attachments, list) or not attachments:
                return None

            cont = QWidget()
            cont.setStyleSheet("QWidget { background: transparent; border: none; }")
            lay = FlowLayout(cont, margin=0, spacing=6)

            for att in attachments:
                if not isinstance(att, dict):
                    continue
                path = att.get("path")
                kind = att.get("kind")

                chip = self._make_attachment_chip(
                    path=str(path or ""),
                    kind=(str(kind) if isinstance(kind, str) else None),
                    removable=bool(removable),
                    on_remove=on_remove,
                    text_color=text_color,
                    bg=bg,
                    bg_hover=bg_hover,
                )
                if chip is not None:
                    lay.addWidget(chip)

            if lay.count() <= 0:
                try:
                    cont.deleteLater()
                except Exception:
                    pass
                return None

            return cont
        except Exception:
            return None

    def _build_image_thumbnails_widget(self, images: Optional[List[str]]) -> Optional[QWidget]:
        """Build a FlowLayout of clickable thumbnails that open show_screenshot_fullsize()."""
        if not images:
            return None

        thumbs_container = QWidget()
        thumbs_layout = FlowLayout(thumbs_container, margin=0, spacing=6)
        thumbs_container.setStyleSheet("QWidget { background: transparent; border: none; }")

        for img in images:
            pm = self._pixmap_from_image_str(img)
            if pm is None:
                continue
            thumb = pm.scaled(
                220,
                140,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            lbl = QLabel()
            lbl.setPixmap(thumb)
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.setStyleSheet("QLabel { border: none; border-radius: 0px; background: transparent; }")
            lbl.mousePressEvent = (lambda event, p=pm: self.show_screenshot_fullsize(p))
            thumbs_layout.addWidget(lbl)

        if thumbs_layout.count() <= 0:
            try:
                thumbs_container.deleteLater()
            except Exception:
                pass
            return None

        return thumbs_container

    def add_user_message(
        self,
        text,
        entry_id=None,
        timestamp=None,
        edit_text: Optional[str] = None,
        images: Optional[List[str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ):
        """Add user message to chat (right-aligned, max 80% width) with hover actions.

        Args:
            text: The message text to display (what the user sees in the bubble)
            entry_id: Optional entry ID from storage (for delete/edit operations)
            timestamp: Optional ISO timestamp string (defaults to now)
            edit_text: Optional text to use for edit/resend (defaults to `text`)
            images: Optional list of image data URLs (display)
            attachments: Optional list of attachment dicts (e.g. {"kind": "file"|"dir", "path": "C:/..."})
        """
        from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QPushButton, QWidget, QLabel
        from datetime import datetime
        
        msg_widget = QWidget()
        msg_widget.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        
        # Parse or create timestamp
        if timestamp:
            try:
                ts = datetime.fromisoformat(timestamp)
            except:
                ts = datetime.now()
        else:
            ts = datetime.now()
        
        # Store entry_id, text, and timestamp as properties on the widget for later access
        edit_source_text = edit_text if edit_text is not None else text

        # Wrapper-only attachments (files/folders) stored in the session entry wrapper meta.
        # Each item looks like: {"kind": "file"|"dir", "path": "C:/..."}
        msg_widget.message_attachments = attachments or []
        msg_widget.entry_id = entry_id
        msg_widget.message_text = edit_source_text
        msg_widget.timestamp = ts
        # Store images (if any) on the widget so Edit can resend them.
        msg_widget.message_images = images or []
        msg_widget.images_b64 = []
        if images:
            for img in images:
                if not img or not isinstance(img, str):
                    continue
                s = img.strip()
                if s.startswith("data:image") and "," in s:
                    s = s.split(",", 1)[1].strip()
                if s:
                    msg_widget.images_b64.append(s)

        
        msg_layout = QHBoxLayout(msg_widget)

        msg_layout.setContentsMargins(0, 0, 0, 0)

        # Spacer for right alignment (20% of width)
        msg_layout.addStretch(1)

        # Message box (80% of width)
        msg_box = QWidget()
        msg_box_layout = QVBoxLayout(msg_box)
        msg_box_layout.setContentsMargins(0, 0, 0, 0)
        msg_box_layout.setSpacing(2)

        # Bubble container (so text + thumbnails share one bubble)
        bubble = QWidget()
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(10, 10, 10, 10)
        bubble_layout.setSpacing(8)
        bubble.setObjectName("user_message_bubble")
        bubble.setStyleSheet(
            "QWidget#user_message_bubble { background-color: #56344F; border: 1px solid #6A4662; border-radius: 10px; }"
        )


        # Optional file/folder attachment chips (clickable)
        try:
            if attachments:
                chips = self._build_attachment_chips_widget(attachments)
                if chips is not None:
                    bubble_layout.addWidget(chips)
        except Exception:
            pass
        text = text or ""
        if str(text).strip():
            msg_label = QLabel(text)
            msg_label.setWordWrap(True)
            msg_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            msg_label.setStyleSheet("""
                QLabel {
                    background: transparent;
                    color: white;
                    font-size: 10pt;
                    border: none;
                    border-radius: 0px;
                    padding: 0px;
                    margin: 0px;
                }
            """)
            bubble_layout.addWidget(msg_label)

        # Optional screenshot thumbnails
        thumbs_container = self._build_image_thumbnails_widget(images)
        if thumbs_container is not None:
            bubble_layout.addWidget(thumbs_container)

        msg_box_layout.addWidget(bubble)
        
        # Timestamp label (subtle, right-aligned)
        time_label = QLabel(ts.strftime("%H:%M"))
        time_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        time_label.setToolTip(ts.strftime("%Y-%m-%d %H:%M:%S"))
        time_label.setStyleSheet("""
            QLabel {
                color: #888888;
                font-size: 9pt;
                padding-right: 5px;
                background: transparent;
            }
        """)
        msg_box_layout.addWidget(time_label)

        # Actions row (invisible by default, shown on hover - uses opacity to avoid layout shift)
        actions_row = QWidget()
        actions_row.setFixedHeight(22)  # Fixed height to prevent layout shift
        actions_layout = QHBoxLayout(actions_row)
        actions_layout.setContentsMargins(0, 2, 0, 0)
        actions_layout.setSpacing(6)

        style_sheet = """
            QPushButton {
                background-color: rgba(40, 40, 40, 120);
                border: none;
                border-radius: 11px;
                padding: 0px;
                font-size: 10pt;
            }
            QPushButton:hover {
                background-color: #4da6ff;
            }
        """
        
        style_sheet_disabled = """
            QPushButton {
                background-color: rgba(40, 40, 40, 60);
                border: none;
                border-radius: 11px;
                padding: 0px;
                font-size: 10pt;
                color: #666666;
            }
        """

        # Align actions to the right
        actions_layout.addStretch(1)

        # Copy button - copies message text to clipboard (always enabled)
        copy_btn = QPushButton("📋")
        copy_btn.setToolTip("Copy message")
        copy_btn.setFixedSize(22, 22)
        copy_btn.setStyleSheet(style_sheet)
        copy_btn.clicked.connect(lambda: self._copy_message(text))
        actions_layout.addWidget(copy_btn)

        # Info button - run details for this message
        info_btn = QPushButton("ℹ")
        info_btn.setFixedSize(22, 22)
        if entry_id:
            info_btn.setToolTip("Run details")
            info_btn.setStyleSheet(style_sheet)
            info_btn.clicked.connect(lambda: self._show_run_usage_popup(entry_id, anchor_widget=info_btn))
        else:
            info_btn.setToolTip("Run details unavailable (message not yet saved)")
            info_btn.setStyleSheet(style_sheet_disabled)
            info_btn.setEnabled(False)
        actions_layout.addWidget(info_btn)

        # Edit button - opens edit dialog, then deletes and resends
        edit_btn = QPushButton("✏️")
        edit_btn.setFixedSize(22, 22)
        if entry_id:
            edit_btn.setToolTip("Edit message (will regenerate response)")
            edit_btn.setStyleSheet(style_sheet)
            edit_btn.clicked.connect(lambda: self._edit_message(entry_id, edit_source_text, getattr(msg_widget, "images_b64", []), getattr(msg_widget, "message_attachments", [])))
        else:
            edit_btn.setToolTip("Cannot edit - message not yet saved")
            edit_btn.setStyleSheet(style_sheet_disabled)
            edit_btn.setEnabled(False)
        actions_layout.addWidget(edit_btn)

        # Remove button - deletes this message and all subsequent
        remove_btn = QPushButton("🗑️")
        remove_btn.setFixedSize(22, 22)
        if entry_id:
            remove_btn.setToolTip("Remove message and all responses after it")
            remove_btn.setStyleSheet(style_sheet)
            remove_btn.clicked.connect(lambda: self._show_delete_menu(entry_id, edit_source_text, anchor_widget=remove_btn))
        else:
            remove_btn.setToolTip("Cannot remove - message not yet saved")
            remove_btn.setStyleSheet(style_sheet_disabled)
            remove_btn.setEnabled(False)
        actions_layout.addWidget(remove_btn)
        
        # Store button references for later enabling (when entry_id becomes available)
        msg_widget.info_btn = info_btn
        msg_widget.edit_btn = edit_btn
        msg_widget.delete_btn = remove_btn
        msg_widget.style_sheet_enabled = style_sheet
        
        # Track as pending if no entry_id yet
        if entry_id is None:
            self.pending_user_message_widget = msg_widget

        # Start with buttons invisible (but still in layout to prevent shifting)
        for btn in [copy_btn, info_btn, edit_btn, remove_btn]:
            btn.setVisible(False)
        
        msg_box_layout.addWidget(actions_row)

        # Prevent shifting: actions row is always present with fixed height
        msg_box.setStyleSheet("""
            QWidget {
                margin-bottom: 0px;
            }
        """)

        msg_layout.addWidget(msg_box, 4)

        # Hover event handling - toggle button visibility instead of hiding the row
        def eventFilter(obj, event):
            if event.type() == QEvent.Type.Enter:
                for btn in [copy_btn, info_btn, edit_btn, remove_btn]:
                    btn.setVisible(True)
            elif event.type() == QEvent.Type.Leave:
                for btn in [copy_btn, info_btn, edit_btn, remove_btn]:
                    btn.setVisible(False)
            return False
        msg_box.installEventFilter(msg_box)
        msg_box.eventFilter = eventFilter

        self.chat_layout.addWidget(msg_widget)
        self.scroll_to_bottom()
    

    # ------------------------------
    # Injected messages (tool side-effects)
    # ------------------------------

    def handle_injected_message_event(self, payload: Any) -> None:
        """Handle a live injected message event from Agent (response.injected_message)."""
        try:
            if not isinstance(payload, dict):
                return

            origin_call_id = payload.get("origin_call_id")
            origin_tool_name = payload.get("origin_tool_name")
            msg = payload.get("message")
            if not isinstance(msg, dict):
                return

            role = str(msg.get("role") or "").lower()
            if role != "user":
                return

            parts = msg.get("content")
            if not isinstance(parts, list):
                parts = []

            texts: List[str] = []
            images: List[str] = []
            for it in parts:
                if not isinstance(it, dict):
                    continue
                t = it.get("type")
                if t == "input_text":
                    txt = (it.get("text") or "")
                    if isinstance(txt, str) and txt.strip():
                        texts.append(txt.strip())
                elif t == "input_image":
                    url = it.get("image_url")
                    if isinstance(url, str) and url.strip():
                        images.append(url.strip())

            self.add_injected_message(
                text="\n\n".join(texts).strip(),
                images=images,
                origin_call_id=(str(origin_call_id) if isinstance(origin_call_id, str) and origin_call_id else None),
                origin_tool_name=(str(origin_tool_name) if isinstance(origin_tool_name, str) and origin_tool_name else None),
                entry_id=None,
                timestamp=None,
            )
        except Exception:
            return

    def add_injected_message(
        self,
        *,
        text: str = "",
        images: Optional[List[str]] = None,
        origin_call_id: Optional[str] = None,
        origin_tool_name: Optional[str] = None,
        entry_id: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> None:
        """Render a tool-injected user-role message as an injected/AI widget.

        - Not shown as a user bubble
        - Inserted right after the originating tool call (when origin_call_id is known)
        - Images are clickable (reuses show_screenshot_fullsize)
        """
        from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QToolButton
        from datetime import datetime

        # Parse timestamp (optional)
        ts_obj = None
        if timestamp:
            try:
                ts_obj = datetime.fromisoformat(str(timestamp))
            except Exception:
                ts_obj = None

        card = QFrame()
        card.setObjectName("injected_message_card")
        card.setStyleSheet(
            """
            QFrame#injected_message_card {
                background-color: #1f2326;
                border: 1px solid #333333;
                border-radius: 10px;
            }
            """
        )

        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(8)

        # Header
        hdr = QWidget()
        hdr_l = QHBoxLayout(hdr)
        hdr_l.setContentsMargins(0, 0, 0, 0)
        hdr_l.setSpacing(8)

        title = "Injected"
        if origin_tool_name:
            title = f"Injected — {origin_tool_name}"

        # Collapsible toggle (collapsed by default)
        toggle_btn = QToolButton()
        toggle_btn.setCheckable(True)
        toggle_btn.setChecked(False)
        toggle_btn.setArrowType(Qt.ArrowType.RightArrow)
        toggle_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        toggle_btn.setStyleSheet("QToolButton { border: none; background: transparent; padding: 0px; }")
        hdr_l.addWidget(toggle_btn, 0, alignment=Qt.AlignmentFlag.AlignLeft)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("QLabel { color: #9aa4b2; font-size: 9pt; font-weight: bold; }")
        hdr_l.addWidget(title_lbl)
        hdr_l.addStretch(1)

        if ts_obj is not None:
            t = QLabel(ts_obj.strftime("%H:%M"))
            t.setStyleSheet("QLabel { color: #6f7782; font-size: 9pt; }")
            t.setToolTip(ts_obj.strftime("%Y-%m-%d %H:%M:%S"))
            hdr_l.addWidget(t)

        lay.addWidget(hdr)

        # Body (collapsible)
        body = QWidget()
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(0, 0, 0, 0)
        body_l.setSpacing(8)

        # Text (caption)
        if isinstance(text, str) and text.strip():
            txt_lbl = QLabel(text.strip())
            txt_lbl.setWordWrap(True)
            txt_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            txt_lbl.setStyleSheet("QLabel { color: #d4d4d4; font-size: 10pt; }")
            body_l.addWidget(txt_lbl)

        # Images (reuse same thumbnail builder as user bubbles)
        thumbs = self._build_image_thumbnails_widget(images or [])
        if thumbs is not None:
            body_l.addWidget(thumbs)

        has_body = body_l.count() > 0
        if not has_body:
            toggle_btn.setVisible(False)
            body.setVisible(False)
        else:
            body.setVisible(False)

            def _set_expanded(expanded: bool) -> None:
                body.setVisible(bool(expanded))
                toggle_btn.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
                try:
                    self.scroll_to_bottom()
                except Exception:
                    pass

            toggle_btn.toggled.connect(_set_expanded)

            def _toggle(_ev=None):
                try:
                    toggle_btn.setChecked(not toggle_btn.isChecked())
                except Exception:
                    pass

            title_lbl.mousePressEvent = _toggle  # type: ignore[assignment]
            hdr.mousePressEvent = _toggle  # type: ignore[assignment]

        lay.addWidget(body)

        # Insert into chat layout
        def _insert_after_tool(call_id: str, w: QWidget) -> bool:
            try:
                parent_block = self._tool_call_widgets_by_id.get(call_id) or self._tool_output_widgets_by_call_id.get(call_id)
                if parent_block is None:
                    return False

                idx = self.chat_layout.indexOf(parent_block)
                insert_at = (idx + 1) if idx != -1 else self.chat_layout.count()

                existing = self._injected_widgets_by_call_id.get(call_id, []) or []
                insert_at += len(existing)

                self.chat_layout.insertWidget(insert_at, w)
                self._injected_widgets_by_call_id.setdefault(call_id, []).append(w)
                return True
            except Exception:
                return False

        inserted = False
        if isinstance(origin_call_id, str) and origin_call_id:
            inserted = _insert_after_tool(origin_call_id, card)

        if not inserted:
            self.chat_layout.addWidget(card)

        try:
            self.scroll_to_bottom()
        except Exception:
            pass


    # ------------------------------
    # System notices (non-agent entries)
    # ------------------------------

    def add_system_notice(self, wrapped_entry: Dict[str, Any]) -> None:
        """Render a persisted system_notice entry as UI blocks.

        These entries are app-generated (not from the agent) and are not part of agent context.
        """
        try:
            if not isinstance(wrapped_entry, dict):
                return
            content = wrapped_entry.get("content") if isinstance(wrapped_entry.get("content"), dict) else {}
            if not isinstance(content, dict):
                return
            if content.get("type") != "system_notice":
                return

            title = str(content.get("title") or "System notice")
            msg = str(content.get("message") or "")

            main = CollapsibleBlock(
                title=f"System Error: {title}",
                body_text=(msg or ""),
                collapsed=True,
                header_color="#2f1a1a",
            )
            try:
                main.set_status("error")
            except Exception:
                pass
            self.chat_layout.addWidget(main)

            failed = content.get("failed_transactions")
            if not isinstance(failed, list) or not failed:
                self.scroll_to_bottom()
                return

            def _short(label: str, max_len: int = 48) -> str:
                s = str(label or "")
                if len(s) <= max_len:
                    return s
                return "…" + s[-(max_len - 1):]

            for ft in failed:
                if not isinstance(ft, dict):
                    continue

                tool = ft.get("tool")
                tool = str(tool) if isinstance(tool, str) and tool else "fs"
                scope = ft.get("scope")
                scope = str(scope).strip().lower() if isinstance(scope, str) else "project"

                err = str(ft.get("error") or "Undo failed")

                blk = CollapsibleBlock(
                    title=f"Undo failed — {tool}",
                    body_text=err,
                    collapsed=True,
                    header_color="#2a1a1a",
                )
                try:
                    blk.set_status("error")
                except Exception:
                    pass

                # SANDBOX badge
                try:
                    if scope == "sandbox":
                        blk.set_scope_badge("SANDBOX", tooltip="App Sandbox scope")
                except Exception:
                    pass

                # Diff badge (reuses existing viewer)
                txn_id = ft.get("transaction_id")
                prev = ft.get("diff_preview") if isinstance(ft.get("diff_preview"), dict) else None
                try:
                    if isinstance(txn_id, str) and txn_id:
                        add = int(prev.get("added_lines", 0) or 0) if isinstance(prev, dict) else 0
                        rem = int(prev.get("removed_lines", 0) or 0) if isinstance(prev, dict) else 0
                        label = f"+{self._fmt_int_dot(add)}/-{self._fmt_int_dot(rem)}"
                        blk.set_diff_badge(label, str(txn_id), self._open_diff_viewer_for_transaction)
                except Exception:
                    pass

                # Path badge
                try:
                    lab = ft.get("path_label")
                    absp = ft.get("abs_path")
                    if isinstance(lab, str) and lab and isinstance(absp, str) and absp:
                        blk.set_path_badge(_short(lab), absp)
                except Exception:
                    pass

                self.chat_layout.addWidget(blk)

            self.scroll_to_bottom()
        except Exception:
            return

    def update_last_user_message_id(self, entry_id: str):
        """Update the entry_id of the pending user message widget.
        
        This is called after the agent finishes and the message is saved to storage,
        so the user can now edit/delete this message.
        
        Args:
            entry_id: The storage entry ID for this message
        """
        if self.pending_user_message_widget is None:
            print(f"[ChatWindow] Warning: No pending user message widget to update")
            return
        
        widget = self.pending_user_message_widget
        widget.entry_id = entry_id
        self._enable_message_actions(widget)
        
        # Clear the pending reference
        self.pending_user_message_widget = None
        print(f"[ChatWindow] Updated widget entry_id to: {entry_id}")
    
    def _enable_message_actions(self, msg_widget):
        """Enable the edit and delete buttons for a message widget after it gets an ID."""
        entry_id = msg_widget.entry_id
        text = msg_widget.message_text
        style_sheet = getattr(msg_widget, 'style_sheet_enabled', '')
        
        # Enable info button
        if hasattr(msg_widget, 'info_btn'):
            btn = msg_widget.info_btn
            btn.setEnabled(True)
            btn.setToolTip("Run details")
            btn.setStyleSheet(style_sheet)
            try:
                btn.clicked.disconnect()
            except Exception:
                pass
            btn.clicked.connect(lambda checked, eid=entry_id, b=btn: self._show_run_usage_popup(eid, anchor_widget=b))

        # Enable edit button
        if hasattr(msg_widget, 'edit_btn'):
            btn = msg_widget.edit_btn
            btn.setEnabled(True)
            btn.setToolTip("Edit message (will regenerate response)")
            btn.setStyleSheet(style_sheet)
            try:
                btn.clicked.disconnect()
            except:
                pass
            imgs = getattr(msg_widget, "images_b64", [])
            atts = getattr(msg_widget, "message_attachments", [])
            btn.clicked.connect(lambda checked, eid=entry_id, t=text, im=imgs, a=atts: self._edit_message(eid, t, im, a))
        
        # Enable delete button
        if hasattr(msg_widget, 'delete_btn'):
            btn = msg_widget.delete_btn
            btn.setEnabled(True)
            btn.setToolTip("Remove message and all responses after it")
            btn.setStyleSheet(style_sheet)
            try:
                btn.clicked.disconnect()
            except:
                pass
            btn.clicked.connect(lambda checked, eid=entry_id, t=text, b=btn: self._show_delete_menu(eid, t, anchor_widget=b))
    
    def _copy_message(self, text: str):
        """Copy message text to clipboard."""
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self._show_toast("Copied!")
        print(f"[ChatWindow] Copied message to clipboard")
    
    def _show_toast(self, message: str, duration_ms: int = 1000):
        """Show a floating toast notification that fades out."""
        toast = QLabel(message, self)
        toast.setStyleSheet("""
            QLabel {
                background-color: rgba(50, 50, 50, 230);
                color: #4da6ff;
                padding: 8px 16px;
                border-radius: 6px;
                font-size: 10pt;
                font-weight: bold;
            }
        """)
        toast.adjustSize()
        
        # Position at bottom center of chat window
        x = (self.width() - toast.width()) // 2
        y = self.height() - toast.height() - 80
        toast.move(x, y)
        toast.show()
        
        # Fade out and delete after duration
        QTimer.singleShot(duration_ms, toast.deleteLater)
    
    def _show_delete_menu(self, entry_id: str, text: str, *, anchor_widget: Optional[QWidget] = None) -> None:
        """Show a small menu for delete behavior (keep edits vs undo edits)."""
        if not entry_id:
            QMessageBox.warning(
                self,
                "Cannot Delete",
                "This message doesn't have an ID (it may be a new unsaved message)."
            )
            return

        menu = QMenu(self)

        # Style the menu itself; the "undo" item tint is handled via a QWidgetAction
        # because QMenu/QAction stylesheet selectors are unreliable across platforms.
        try:
            menu.setStyleSheet(
                "QMenu { background-color: #1e1e1e; border: 1px solid #333333; border-radius: 8px; padding: 4px; }"
                "QMenu::item { padding: 6px 22px; }"
                "QMenu::item:selected { background-color: rgba(255,255,255,0.08); }"
            )
        except Exception:
            pass

        act_keep = QAction("Remove and keep file edits", menu)
        menu.addAction(act_keep)

        # Undo item: custom widget so we can tint the text reliably.
        act_undo_action = None
        try:
            undo_widget = QWidget()
            undo_widget.setObjectName("undo_delete_menu_item")
            undo_widget.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
            undo_widget.setCursor(Qt.CursorShape.PointingHandCursor)
            undo_widget.setStyleSheet(
                "QWidget#undo_delete_menu_item { background: transparent; border-radius: 4px; }"
                "QWidget#undo_delete_menu_item:hover { background-color: rgba(255,255,255,0.08); }"
            )

            hl = QHBoxLayout(undo_widget)
            hl.setContentsMargins(22, 6, 22, 6)
            hl.setSpacing(0)

            lbl = QLabel("Remove and undo file edits")
            lbl.setStyleSheet("QLabel { color: #ff7b7b; }")
            hl.addWidget(lbl)

            wa = QWidgetAction(menu)
            wa.setDefaultWidget(undo_widget)
            menu.addAction(wa)

            def _on_undo_click(ev):
                try:
                    menu.close()
                except Exception:
                    pass
                self._delete_message(entry_id, text, undo_file_edits=True)

            undo_widget.mousePressEvent = _on_undo_click  # type: ignore[assignment]

        except Exception:
            # Fallback: plain action if widget action fails.
            act_undo_action = QAction("Remove and undo file edits", menu)
            menu.addAction(act_undo_action)

        act_keep.triggered.connect(lambda: self._delete_message(entry_id, text, undo_file_edits=False))
        if act_undo_action is not None:
            act_undo_action.triggered.connect(lambda: self._delete_message(entry_id, text, undo_file_edits=True))

        # Anchor to the clicked button.
        try:
            if anchor_widget is not None:
                pos = anchor_widget.mapToGlobal(QPoint(0, anchor_widget.height()))
                menu.exec(pos)
                return
        except Exception:
            pass

        # Fallback
        try:
            menu.exec(self.mapToGlobal(QPoint(0, 0)))
        except Exception:
            pass

    def _delete_message(self, entry_id: str, text: str, *, undo_file_edits: bool = False) -> None:
        """Request deletion of message and all subsequent messages.

        If undo_file_edits=True, the app will attempt to undo filesystem transactions
        associated with the deleted tail (best-effort).
        """
        if not entry_id:
            QMessageBox.warning(
                self,
                "Cannot Delete",
                "This message doesn't have an ID (it may be a new unsaved message)."
            )
            return

        # Confirm deletion
        extra = "\n\nThis will also attempt to undo file edits made after this message." if undo_file_edits else ""
        reply = QMessageBox.question(
            self,
            "Delete Message",
            (
                f"Delete this message and all messages after it?{extra}\n\n\"{text[:100]}{'...' if len(text) > 100 else ''}\"\n\nThis action cannot be undone."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            print(f"[ChatWindow] Requesting delete from entry_id: {entry_id} (undo_file_edits={bool(undo_file_edits)})")
            self.delete_message_requested.emit(entry_id, bool(undo_file_edits))
    
    def _edit_message(
        self,
        entry_id: str,
        current_text: str,
        images_b64: Optional[List[str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ):
        """Open edit dialog and request edit operation."""
        if not entry_id:
            QMessageBox.warning(
                self,
                "Cannot Edit",
                "This message doesn't have an ID (it may be a new unsaved message)."
            )
            return
        
        # Show edit dialog
        dialog = EditMessageDialog(
            current_text,
            images=(images_b64 or []),
            files=(attachments or []),
            max_images=getattr(self, "max_screenshots", 5),
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_text = (dialog.get_text() or "").strip()
            new_images = dialog.get_images_b64() or []
            new_files = dialog.get_files() if hasattr(dialog, "get_files") else []
            undo_file_edits = bool(dialog.get_undo_file_edits()) if hasattr(dialog, "get_undo_file_edits") else False

            if new_text or new_images or new_files:
                print(f"[ChatWindow] Requesting edit for entry_id: {entry_id} (undo_file_edits={undo_file_edits})")
                self.edit_message_requested.emit(entry_id, new_text, new_images, undo_file_edits, new_files)
                # Scroll to bottom after edit is initiated
                self.scroll_to_bottom()
            else:
                QMessageBox.warning(self, "Empty Message", "Message cannot be empty (unless you attach an image or file).")
    
    class CodeBlockWidget(QWidget):
        """Custom widget for displaying code blocks with copy button and syntax highlighting."""
        
        def __init__(self, code, language="", parent=None):
            super().__init__(parent)
            self.code = code
            self.language = language
            self.setup_ui()
        
        def setup_ui(self):
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 5, 0, 5)
            layout.setSpacing(0)
            
            # Header with language and copy button
            header = QWidget()
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(10, 5, 10, 5)
            header_layout.setSpacing(10)
            
            lang_label = QLabel(self.language.upper() if self.language else "CODE")
            lang_label.setStyleSheet("""
                QLabel {
                    color: #888;
                    font-size: 9pt;
                    font-weight: bold;
                }
            """)
            
            copy_btn = QPushButton("Copy")
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.clicked.connect(self.copy_code)
            copy_btn.setStyleSheet("""
                QPushButton {
                    background-color: #3a3a3a;
                    color: #d4d4d4;
                    border: 1px solid #555;
                    border-radius: 4px;
                    padding: 4px 12px;
                    font-size: 9pt;
                }
                QPushButton:hover {
                    background-color: #4a4a4a;
                    border-color: #666;
                }
                QPushButton:pressed {
                    background-color: #2a2a2a;
                }
            """)
            
            header_layout.addWidget(lang_label)
            header_layout.addStretch()
            header_layout.addWidget(copy_btn)
            
            header.setStyleSheet("""
                QWidget {
                    background-color: #2a2a2a;
                    border-top-left-radius: 6px;
                    border-top-right-radius: 6px;
                }
            """)
            
            # Code display
            code_display = NoWheelTextBrowser()
            code_display.setReadOnly(True)
            code_display.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            code_display.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            
            code_font = QFont('Consolas', 10)
            code_display.setFont(code_font)
            
            code_display.setStyleSheet("""
                QTextBrowser {
                    background-color: #272822;
                    color: #d4d4d4;
                    border: none;
                    border-bottom-left-radius: 6px;
                    border-bottom-right-radius: 6px;
                    padding: 10px;
                    font-family: 'Consolas', 'Courier New', monospace;
                }
                QScrollBar:horizontal {
                    background-color: #272822;
                    height: 10px;
                }
                QScrollBar::handle:horizontal {
                    background-color: #3a3a3a;
                    border-radius: 5px;
                }
                QScrollBar::handle:horizontal:hover {
                    background-color: #4a4a4a;
                }
            """)
            
            highlighted_html = self.get_highlighted_code()
            code_display.setHtml(highlighted_html)
            
            def adjust_height():
                doc = code_display.document()
                doc.setTextWidth(code_display.viewport().width())
                doc_height = doc.size().height()
                final_height = max(int(doc_height + 30), 50)
                code_display.setMinimumHeight(final_height)
                code_display.setMaximumHeight(final_height)
            
            QTimer.singleShot(10, adjust_height)
            code_display.document().contentsChanged.connect(adjust_height)
            code_display._auto_height_cb = adjust_height
            
            layout.addWidget(header)
            layout.addWidget(code_display)
            
            self.setStyleSheet("""
                CodeBlockWidget {
                    border: 1px solid #3a3a3a;
                    border-radius: 6px;
                }
            """)
        
        def get_highlighted_code(self):
            """Apply syntax highlighting using Pygments."""
            try:
                if self.language:
                    lexer = get_lexer_by_name(self.language, stripall=True)
                else:
                    try:
                        lexer = guess_lexer(self.code)
                    except:
                        lexer = PythonLexer()
            except:
                lexer = PythonLexer()
            
            formatter = HtmlFormatter(style='monokai', noclasses=True, nowrap=False, linenos=False)
            highlighted = highlight(self.code, lexer, formatter)
            
            html = f"""
            <style>
                body {{
                    margin: 0;
                    padding: 0;
                    background-color: #272822;
                    font-family: 'Consolas', 'Courier New', monospace;
                    font-size: 10pt;
                    line-height: 1.4;
                }}
                .highlight {{
                    margin: 0;
                    padding: 0;
                }}
                .highlight pre {{
                    margin: 0;
                    padding: 0;
                    background-color: transparent;
                    white-space: pre;
                    line-height: 1.4;
                }}
            </style>
            {highlighted}
            """
            
            return html
        
        def copy_code(self):
            """Copy code to clipboard."""
            clipboard = QApplication.clipboard()
            clipboard.setText(self.code)
            
            sender = self.sender()
            original_text = sender.text()
            sender.setText("Copied!")
            QTimer.singleShot(1500, lambda: sender.setText(original_text))


    # --- Group session markers (round + participant) ---

    def _group_color_for_owner(self, owner_id: str) -> str:
        """Pick a stable accent color for a participant (avoid blue)."""
        try:
            s = str(owner_id or "")
        except Exception:
            s = ""
        # No blue palette.
        palette = ["#bb86fc", "#4dd4ac", "#f6c177", "#ff6b6b", "#c586c0"]
        try:
            idx = abs(hash(s)) % len(palette)
            return palette[idx]
        except Exception:
            return palette[0]

    def add_group_round_marker(self, round_idx: int) -> None:
        from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel
        try:
            ri = int(round_idx)
        except Exception:
            ri = 0

        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 10, 0, 6)
        lay.setSpacing(0)

        lbl = QLabel(f"Round {ri + 1}")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            "QLabel { color: rgba(255,255,255,0.55); font-size: 9pt; font-weight: bold; }"
        )

        # Thin divider line effect via background on container.
        w.setStyleSheet("QWidget { background: transparent; border-top: 1px solid rgba(255,255,255,0.10); }")
        lay.addWidget(lbl, 1)

        self.chat_layout.addWidget(w)
        self.scroll_to_bottom()


    def add_group_section_spacer(self, height_px: int = 14) -> None:
        """Add a small vertical spacer between participant sections (used when a participant only ran tools)."""
        from PyQt6.QtWidgets import QWidget

        try:
            h = int(height_px)
        except Exception:
            h = 14
        if h <= 0:
            h = 8

        w = QWidget()
        w.setFixedHeight(h)
        w.setStyleSheet("QWidget { background: transparent; }")
        self.chat_layout.addWidget(w)
        try:
            self.scroll_to_bottom()
        except Exception:
            pass

    def add_group_participant_marker(self, name: str, *, owner_id: str, round_idx: int) -> None:
        from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel

        nm = str(name or "Participant").strip() or "Participant"
        accent = self._group_color_for_owner(owner_id)

        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(8, 6, 8, 4)
        lay.setSpacing(8)

        pill = QLabel(nm)
        pill.setStyleSheet(
            f"""
            QLabel {{
                color: #d4d4d4;
                background-color: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.10);
                border-left: 4px solid {accent};
                border-radius: 8px;
                padding: 4px 10px;
                font-size: 10pt;
                font-weight: bold;
            }}
            """
        )

        lay.addWidget(pill, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)

        self.chat_layout.addWidget(w)
        self.scroll_to_bottom()


    def add_collapsible_block(
        self,
        title: str,
        body_text: str = "",
        collapsed: bool = True,
        header_color: str = "#3d3d3d",
    ):
        """Add a collapsible one-liner + expandable body block to the chat."""
        block = CollapsibleBlock(
            title=title,
            body_text=body_text,
            collapsed=collapsed,
            header_color=header_color,
        )
        self.chat_layout.addWidget(block)
        self.scroll_to_bottom()
        return block

    # --- Streaming helpers (reasoning / tools) ---

    def _normalize_reasoning_header_base(self, header: str) -> str:
        header = (header or "Thinking").strip()
        if "Thinking" in header:
            prefix = header.split("Thinking", 1)[0]
            return (prefix + "Thinking").strip()
        return header.rstrip(". ")

    def _update_reasoning_block_header(self) -> None:
        if self.current_reasoning_block is None:
            return
        base = (self._reasoning_header_base or "Thinking").strip()
        if self._last_reasoning_title:
            self.current_reasoning_block.set_title(f"{base} — {self._last_reasoning_title}")
        else:
            self.current_reasoning_block.set_title(base + "…")

    def _scan_reasoning_titles(self, delta: str) -> None:
        if delta is None:
            return
        s = str(delta).replace("\r\n", "\n").replace("\r", "\n")
        self._reasoning_line_buffer += s

        while "\n" in self._reasoning_line_buffer:
            line, rest = self._reasoning_line_buffer.split("\n", 1)
            self._reasoning_line_buffer = rest

            line = line.strip("\r")
            stripped = line.strip()

            # Ignore markdown code fences
            if stripped.startswith("```"):
                self._reasoning_in_fence = not self._reasoning_in_fence
                continue
            if self._reasoning_in_fence:
                continue

            m = self._reasoning_title_rx.match(line)
            if not m:
                continue

            title = (m.group(1) or "").strip()
            if title and title != self._last_reasoning_title:
                self._last_reasoning_title = title
                self._update_reasoning_block_header()

    def extract_last_reasoning_title(self, text: str) -> Optional[str]:
        if not text:
            return None
        in_fence = False
        last = None
        for raw_line in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            line = raw_line.strip("\r")
            stripped = line.strip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            m = self._reasoning_title_rx.match(line)
            if m:
                candidate = (m.group(1) or "").strip()
                if candidate:
                    last = candidate
        return last

    def add_reasoning_summary_block(self, summary_text: str, header: str = "Thinking…"):
        base = self._normalize_reasoning_header_base(header)
        last = self.extract_last_reasoning_title(summary_text)
        title = f"{base} — {last}" if last else (base + "…")
        return self.add_collapsible_block(
            title=title,
            body_text=summary_text or "",
            collapsed=True,
            header_color="#2f2a1a",
        )

    def start_reasoning_block(self, title: str = "Thinking…"):
        self._reasoning_line_buffer = ""
        self._last_reasoning_title = None
        self._reasoning_in_fence = False
        self._reasoning_header_base = self._normalize_reasoning_header_base(title)

        self.current_reasoning_block = self.add_collapsible_block(
            title=self._reasoning_header_base + "…",
            body_text="",
            collapsed=True,
            header_color="#2f2a1a",
        )
        return self.current_reasoning_block

    def append_to_reasoning(self, text: str) -> None:
        if self.current_reasoning_block is None:
            self.start_reasoning_block()
        self._scan_reasoning_titles(text)
        self.current_reasoning_block.append_text(text)

    def finish_reasoning(self) -> None:
        # Flush any remaining partial line (in case the last title doesn't end with a newline).
        tail = (self._reasoning_line_buffer or "").strip("\r\n")
        if tail and (not self._reasoning_in_fence):
            m = self._reasoning_title_rx.match(tail)
            if m:
                title = (m.group(1) or "").strip()
                if title:
                    self._last_reasoning_title = title
        self._reasoning_line_buffer = ""

        self._update_reasoning_block_header()
        self.current_reasoning_block = None


    def _get_project_root_for_paths(self) -> str:
        # Best effort: prefer configured project root, fall back to cwd.
        try:
            app = getattr(self.parent_widget, "app", None)
            cfg = getattr(app, "app_config", None)
            tools_cfg = getattr(cfg, "tools", None)
            root = getattr(tools_cfg, "project_root", None)
            if root:
                return str(root)
        except Exception:
            pass
        return os.getcwd()

    def _extract_primary_path_badge(self, tool_name: Optional[str], args_text: str) -> Optional[tuple[str, str]]:
        """Return (label, abs_path) for filesystem-ish tool calls.

        Label is what we show in the header; abs_path is what we open in explorer.
        """
        tool = (tool_name or "").strip()
        s = (args_text or "").strip()
        if not tool or not s:
            return None

        try:
            obj = json.loads(s)
        except Exception:
            return None

        if not isinstance(obj, dict):
            return None

        # Resolve base root for explorer reveal based on scope.
        scope = None
        try:
            scope = obj.get("scope")
        except Exception:
            scope = None

        root = self._get_project_root_for_paths()
        if isinstance(scope, str) and scope.strip().lower() == "sandbox":
            try:
                root = str(get_sandbox_root(ensure_exists=True))
            except Exception:
                root = self._get_project_root_for_paths()

        def _abs(rel: str) -> str:
            return os.path.abspath(os.path.join(root, rel))


        def _short(label: str, max_len: int = 48) -> str:
            s2 = label or ""
            if len(s2) <= max_len:
                return s2
            # Keep the end of the path (usually the filename) visible.
            return "…" + s2[-(max_len - 1):]
        # Common case: single relative_path
        rel = obj.get("relative_path")
        if isinstance(rel, str) and rel.strip():
            return (_short(rel.strip()), _abs(rel.strip()))

        # read_folder uses relative_path too (covered above)

        # delete_paths
        if tool == "delete_paths":
            paths = obj.get("paths")
            if isinstance(paths, list) and paths:
                p0 = str(paths[0])
                suffix = f" (+{len(paths) - 1})" if len(paths) > 1 else ""
                return (_short(p0 + suffix), _abs(p0))

        # copy/move ops: show destination (most relevant to find the result)
        if tool in ("copy_paths", "move_paths"):
            ops = obj.get("operations")
            if isinstance(ops, list) and ops:
                first = ops[0] if isinstance(ops[0], dict) else {}
                dst = first.get("destination")
                if isinstance(dst, str) and dst.strip():
                    suffix = f" (+{len(ops) - 1})" if len(ops) > 1 else ""
                    return (_short(dst.strip() + suffix), _abs(dst.strip()))

        # rename: show old -> new, open new
        if tool == "rename_path":
            old_p = obj.get("old_path")
            new_p = obj.get("new_path")
            if isinstance(old_p, str) and isinstance(new_p, str) and new_p.strip():
                label = f"{old_p.strip()} → {new_p.strip()}"
                return (_short(label), _abs(new_p.strip()))

        # fs_search: start_path
        if tool == "fs_search":
            sp = obj.get("start_path")
            if isinstance(sp, str) and sp.strip():
                return (_short(sp.strip()), _abs(sp.strip()))

        # move_paths/rename_path already handled; for everything else, nothing.

    def _maybe_attach_scope_badge(self, block: QWidget, args_text: str) -> None:
        """Attach a SANDBOX badge if args indicate scope='sandbox'.

        Used for main tool-call blocks, live sub-agent blocks, and rehydrated subhistory.
        """
        try:
            if block is None:
                return
            obj = None
            try:
                obj = json.loads((args_text or "").strip() or "{}")
            except Exception:
                obj = None
            sc = obj.get("scope") if isinstance(obj, dict) else None
            if isinstance(sc, str) and sc.strip().lower() == "sandbox" and hasattr(block, "set_scope_badge"):
                block.set_scope_badge("SANDBOX", tooltip="App Sandbox scope")
        except Exception:
            return

        return None

    def _format_tool_args(self, args_text: str) -> str:
        s = (args_text or "").strip()
        if not s:
            return ""

        # strip optional prefix
        if s.lower().startswith("arguments:"):
            s = s.split(":", 1)[1].strip()

        try:
            obj = json.loads(s)
        except Exception:
            return s

        if isinstance(obj, dict):
            # Redact huge / sensitive payloads (prevents tool-call spam and accidental leaks).
            REDACT_KEYS = {
                "content",
                "text",
                "old_text",
                "new_text",
                "image_url",
                "images",
                "screenshots_b64",
                "files",
            }

            lines: List[str] = []
            for k, v in obj.items():
                kk = str(k)

                if kk in REDACT_KEYS:
                    if isinstance(v, str):
                        vv = f"<redacted: {len(v)} chars>"
                    elif isinstance(v, (list, tuple)):
                        vv = f"<redacted: {len(v)} items>"
                    else:
                        vv = "<redacted>"
                    lines.append(f"{kk}: {vv}")
                    continue

                if isinstance(v, str):
                    vv = v if len(v) <= 400 else (v[:200] + f" … <{len(v)} chars>")
                elif isinstance(v, (list, tuple)):
                    vv = json.dumps(v, ensure_ascii=False) if len(v) <= 10 else f"<{len(v)} items>"
                else:
                    vv = json.dumps(v, ensure_ascii=False)

                lines.append(f"{kk}: {vv}")

            return "\n".join(lines)

        return json.dumps(obj, ensure_ascii=False, indent=2)

    def _format_tool_output(self, output_text: Any) -> str:
        if output_text is None:
            return ""

        if not isinstance(output_text, str):
            try:
                output_text = json.dumps(output_text, ensure_ascii=False, indent=2)
            except Exception:
                output_text = str(output_text)

        s = output_text.strip()
        if not s:
            return ""

        try:
            obj = json.loads(s)
        except Exception:
            return s

        return json.dumps(obj, ensure_ascii=False, indent=2)

    def _derive_tool_result_status(self, output_text: Any) -> Optional[str]:
        """Best-effort: return 'success'/'error'/None from a tool output payload."""
        try:
            if output_text is None:
                return None

            obj = output_text
            if isinstance(output_text, str):
                s = output_text.strip()
                if s:
                    try:
                        obj = json.loads(s)
                    except Exception:
                        return None

            if isinstance(obj, dict):
                st = obj.get("status")
                if isinstance(st, str):
                    st = st.strip().lower()
                    if st in ("success", "error"):
                        return st

            if isinstance(obj, list):
                any_err = False
                any_succ = False
                for it in obj:
                    if isinstance(it, dict):
                        st = it.get("status")
                        if isinstance(st, str):
                            st = st.strip().lower()
                            if st == "error":
                                any_err = True
                            elif st == "success":
                                any_succ = True
                if any_err:
                    return "error"
                if any_succ:
                    return "success"

            return None
        except Exception:
            return None

    def _extract_tool_items_count(self, tool_name: Optional[str], output_text: Any) -> Optional[int]:
        """Return a small integer to show next to the tool name (items touched).

        This is UI-only sugar. We infer it from the TOOL OUTPUT payload.
        """
        try:
            tn = (tool_name or "").strip()
            if not tn:
                return None

            obj = output_text
            if isinstance(obj, str):
                s = obj.strip()
                if not s:
                    return None
                try:
                    obj = json.loads(s)
                except Exception:
                    return None

            # Batched filesystem reads/searches return a summary object with `count` / `results`.
            if tn in ("read_folder", "read_file", "fs_search") and isinstance(obj, dict):
                c = obj.get("count")
                if isinstance(c, int):
                    return int(c)
                res = obj.get("results")
                if isinstance(res, list):
                    return int(len(res))

            # These tools return a list of per-item results (sometimes with a trailing wrap_meta dict).
            if tn in ("delete_paths", "copy_paths", "move_paths") and isinstance(obj, list):
                return int(sum(1 for it in obj if isinstance(it, dict) and ("status" in it)))

            return None
        except Exception:
            return None

    def _extract_tool_touched_items(self, tool_name: Optional[str], output_text: Any) -> Optional[List[str]]:
        """Extract a list of touched item labels from tool OUTPUT.

        Used for count-badge hover/click previews (without expanding the tool block).
        """
        try:
            tn = (tool_name or "").strip()
            if not tn:
                return None

            obj = output_text
            if isinstance(obj, str):
                s = obj.strip()
                if not s:
                    return None
                try:
                    obj = json.loads(s)
                except Exception:
                    return None

            items: List[str] = []

            if tn == "read_folder" and isinstance(obj, dict):
                res = obj.get("results")
                if isinstance(res, list):
                    for it in res:
                        if isinstance(it, dict):
                            p = it.get("relative_path")
                            st = it.get("status")
                            if isinstance(p, str):
                                items.append(f"{p} ({st})" if isinstance(st, str) and st else p)
                return items or None

            if tn == "read_file" and isinstance(obj, dict):
                res = obj.get("results")
                if isinstance(res, list):
                    for it in res:
                        if not isinstance(it, dict):
                            continue
                        p = it.get("relative_path")
                        st = it.get("status")
                        sl = None
                        el = None
                        try:
                            sl = (it.get("slice") or {}).get("start_line") if isinstance(it.get("slice"), dict) else None
                            el = (it.get("slice") or {}).get("end_line") if isinstance(it.get("slice"), dict) else None
                        except Exception:
                            sl = None
                            el = None

                        if isinstance(p, str):
                            rng = ""
                            if sl is not None or el is not None:
                                rng = f" [{sl if sl is not None else ''}..{el if el is not None else ''}]"
                            base = p + rng
                            items.append(f"{base} ({st})" if isinstance(st, str) and st else base)
                return items or None

            if tn == "fs_search" and isinstance(obj, dict):
                res = obj.get("results")
                if isinstance(res, list):
                    for it in res:
                        if isinstance(it, dict):
                            p = it.get("path")
                            k = it.get("kind")
                            if isinstance(p, str):
                                mc = it.get("match_count")
                                if isinstance(k, str) and k:
                                    if isinstance(mc, int):
                                        items.append(f"{p} ({k}, {mc})")
                                    else:
                                        items.append(f"{p} ({k})")
                                else:
                                    items.append(p)
                return items or None

            if tn == "delete_paths" and isinstance(obj, list):
                for it in obj:
                    if isinstance(it, dict) and ("status" in it):
                        p = it.get("path")
                        st = it.get("status")
                        if isinstance(p, str):
                            items.append(f"{p} ({st})" if isinstance(st, str) and st else p)
                return items or None

            if tn in ("copy_paths", "move_paths") and isinstance(obj, list):
                for it in obj:
                    if isinstance(it, dict) and ("status" in it):
                        src = it.get("source")
                        dst = it.get("destination")
                        st = it.get("status")
                        label = None
                        if isinstance(src, str) and isinstance(dst, str):
                            label = f"{src} → {dst}"
                        elif isinstance(dst, str):
                            label = dst
                        elif isinstance(src, str):
                            label = src
                        if label:
                            items.append(f"{label} ({st})" if isinstance(st, str) and st else label)
                return items or None

            return None
        except Exception:
            return None

    def add_run_receipt_block(self, run_summary: Dict[str, Any]) -> None:
        """Append a compact run receipt block (from a run_summary item)."""
        try:
            if not isinstance(run_summary, dict):
                return
            if run_summary.get("type") != "run_summary":
                return

            files = run_summary.get("files_changed") if isinstance(run_summary.get("files_changed"), list) else []
            visible_count = int(run_summary.get("files_changed_count") or 0)
            eph_count = int(run_summary.get("files_ephemeral_count") or 0)
            # Render receipts for file changes as usual, but also render receipts for
            # stopped/error runs even if there were no net file changes.
            try:
                rs = str(run_summary.get("run_status") or "").strip().lower()
            except Exception:
                rs = ""
            if visible_count <= 0 and eph_count <= 0 and rs in ("", "success", "completed"):
                return

            project_root = self._get_project_root_for_paths()
            session_id = getattr(self.parent_widget, "active_session_id", None) if self.parent_widget else None
            run_id = run_summary.get("run_id")

            # Back-compat / safety: older sessions may store Phase-A `files_changed` entries
            # without path_before/path_after/added_lines/removed_lines. In that case, fetch
            # the consolidated run diff index on demand so this receipt can show file rows.
            try:
                needs_fetch = False
                if files and isinstance(files[0], dict):
                    if ("path_after" not in files[0]) and ("path_before" not in files[0]):
                        needs_fetch = True

                if needs_fetch and isinstance(session_id, str) and session_id and isinstance(run_id, str) and run_id:
                    import time, uuid
                    from ...appcore.runtime_context import Runtime

                    bus = Runtime.get_event_bus()
                    reply_topic = f"fs_revisions.ui.reply.get_run_diff_index.{uuid.uuid4()}"
                    result: Dict[str, Any] = {}
                    unsub = None

                    def _on_reply(ev):
                        nonlocal result, unsub
                        try:
                            if unsub:
                                unsub()
                        except Exception:
                            pass
                        unsub = None
                        pl = getattr(ev, "payload", {}) or {}
                        result = pl if isinstance(pl, dict) else {"status": "error"}

                    unsub = bus.subscribe(reply_topic, _on_reply)
                    bus.publish(
                        "fs_revisions.cmd.get_run_diff_index",
                        {"reply_topic": reply_topic, "session_id": session_id, "run_id": run_id},
                    )

                    deadline = time.time() + 2.0
                    while not result and time.time() < deadline:
                        try:
                            bus.pump(max_events=50)
                        except Exception:
                            pass
                        try:
                            QApplication.processEvents()
                        except Exception:
                            pass
                        time.sleep(0.01)

                    try:
                        if unsub:
                            unsub()
                    except Exception:
                        pass

                    if isinstance(result, dict) and result.get("status") == "success":
                        rr = dict(run_summary)
                        rr["files_changed"] = result.get("files") if isinstance(result.get("files"), list) else []
                        # Back-compat: older run summaries didn’t include ephemeral marking.
                        # Keep count as total here (UI will still show rows), but don’t block rendering.
                        rr["files_changed_count"] = int(len(rr["files_changed"]))
                        rr["files_ephemeral_count"] = int(rr.get("files_ephemeral_count") or 0)
                        if isinstance(result.get("diff_totals"), dict):
                            rr["diff_totals"] = result.get("diff_totals")
                        run_summary = rr
                        files = rr.get("files_changed") if isinstance(rr.get("files_changed"), list) else []
            except Exception:
                pass

            def _open_run_diff(initial_file_key: Optional[str]) -> None:
                try:
                    if not isinstance(session_id, str) or not session_id:
                        return
                    if not isinstance(run_id, str) or not run_id:
                        return

                    # If the user clicked an ephemeral entry, auto-enable Temp in the viewer
                    # so the initial file is actually visible/selected.
                    include_ephemeral = False
                    try:
                        fk = str(initial_file_key) if initial_file_key else None
                        if fk and isinstance(files, list):
                            for ff in files:
                                if isinstance(ff, dict) and str(ff.get("file_key") or "") == fk:
                                    include_ephemeral = bool(ff.get("ephemeral"))
                                    break
                    except Exception:
                        include_ephemeral = False

                    dlg = SideBySideDiffViewerDialog(
                        parent=self,
                        session_id=session_id,
                        run_id=run_id,
                        initial_file_key=(str(initial_file_key) if initial_file_key else None),
                        include_ephemeral=bool(include_ephemeral),
                    )
                    dlg.exec()
                except Exception:
                    return

            w = RunReceiptBlock(
                run_summary,
                parent=self,
                project_root=project_root,
                on_open_run_diff=_open_run_diff,
            )
            self.chat_layout.addWidget(w)
            self.scroll_to_bottom()
        except Exception:
            return


    # -----------------------------------------------------------------
    # Live sub-agent streaming (render as subtree under parent tool-call)
    # -----------------------------------------------------------------

    def handle_subagent_event(self, event: Dict[str, Any]) -> None:
        """Handle a streamed sub-agent event.

        Events are delivered on the same stream_topic as the main run but tagged with:
          - source="subagent"
          - parent_call_id
          - subagent_name

        We render tool calls/outputs as an indented subtree under the parent tool-call block.
        """
        try:
            if not isinstance(event, dict):
                return

            parent_call_id = event.get("parent_call_id")
            if not isinstance(parent_call_id, str) or not parent_call_id:
                return

            subagent_name = event.get("subagent_name")
            if not isinstance(subagent_name, str) or not subagent_name:
                subagent_name = str(event.get("agent_name") or "Subagent")

            state = self._live_subagent_state_by_parent_call_id.get(parent_call_id)
            if not isinstance(state, dict):
                state = {
                    "widgets": [],
                    "tool_meta_by_call_id": {},  # call_id -> {name, arguments}
                    "block_by_call_id": {},
                }
                self._live_subagent_state_by_parent_call_id[parent_call_id] = state

            def indent(w: QWidget) -> QWidget:
                row = QWidget()
                lay = QHBoxLayout(row)
                lay.setContentsMargins(14, 0, 0, 0)
                lay.setSpacing(0)
                lay.addWidget(w, 1)
                return row

            def insert_widget(w: QWidget) -> None:
                parent_block = self._tool_call_widgets_by_id.get(parent_call_id) or self._tool_output_widgets_by_call_id.get(parent_call_id)
                if parent_block is None:
                    self.chat_layout.addWidget(w)
                    return

                try:
                    idx = self.chat_layout.indexOf(parent_block)
                    insert_at = (idx + 1) if idx != -1 else self.chat_layout.count()
                    insert_at += len(state.get("widgets") or [])
                    self.chat_layout.insertWidget(insert_at, w)
                except Exception:
                    self.chat_layout.addWidget(w)

            et = event.get("type")
            content = event.get("content") if isinstance(event.get("content"), dict) else {}

            # Tool call (function_call)
            if et == "response.output_item.done":
                item = content.get("item") if isinstance(content.get("item"), dict) else {}
                if item.get("type") == "function_call":
                    func_name = item.get("name", "")
                    func_args = item.get("arguments", "")
                    call_id = item.get("call_id")

                    if isinstance(call_id, str) and call_id:
                        state["tool_meta_by_call_id"][call_id] = {"name": str(func_name), "arguments": str(func_args)}

                    b = CollapsibleBlock(
                        title=f"[{subagent_name}] Tool Call: {func_name}",
                        body_text=self._format_tool_args(str(func_args)),
                        collapsed=True,
                        header_color="#262030",
                    )
                    try:
                        if hasattr(b, "set_status"):
                            b.set_status("pending")
                    except Exception:
                        pass


                    # Scope badge (SANDBOX)
                    try:
                        self._maybe_attach_scope_badge(b, str(func_args))
                    except Exception:
                        pass
                    # Path badge
                    try:
                        badge = self._extract_primary_path_badge(str(func_name), str(func_args))
                        if badge:
                            label, abs_path = badge
                            b.set_path_badge(label, abs_path)
                    except Exception:
                        pass

                    w = indent(b)
                    insert_widget(w)
                    state["widgets"].append(w)
                    if isinstance(call_id, str) and call_id:
                        state["block_by_call_id"][call_id] = b

                    try:
                        self.scroll_to_bottom()
                    except Exception:
                        pass
                return

            # Tool output (synthetic)
            if et == "response.tool_output":
                call_id = content.get("call_id")
                tool_name = content.get("name") or ""
                args = content.get("arguments") or ""
                out = content.get("output")
                wrap_meta = content.get("wrap_meta") if isinstance(content.get("wrap_meta"), dict) else None

                if not isinstance(call_id, str) or not call_id:
                    return

                b = state["block_by_call_id"].get(call_id)
                if b is None:
                    # Orphan output: create a call block so output isn't floating.
                    b = CollapsibleBlock(
                        title=f"[{subagent_name}] Tool Call: {tool_name}",
                        body_text=self._format_tool_args(str(args)),
                        collapsed=True,
                        header_color="#262030",
                    )
                    try:
                        if hasattr(b, "set_status"):
                            b.set_status("pending")
                    except Exception:
                        pass
                    w = indent(b)
                    insert_widget(w)
                    state["widgets"].append(w)
                    state["block_by_call_id"][call_id] = b

                # Status
                try:
                    st = self._derive_tool_result_status(out)
                    if hasattr(b, "set_status"):
                        b.set_status(st or "success")
                except Exception:
                    pass

                # Items-touched count badge + hover/click details
                try:
                    cnt = self._extract_tool_items_count(str(tool_name), out)
                    items = self._extract_tool_touched_items(str(tool_name), out) or []
                    if cnt is None and items:
                        cnt = len(items)

                    tip = None
                    details = None
                    if items:
                        preview_n = 12
                        preview = items[:preview_n]
                        rest = len(items) - len(preview)
                        lines = ["Touched items:"] + [f"- {x}" for x in preview]
                        if rest > 0:
                            lines.append(f"… (+{rest} more)")
                        tip = "\n".join(lines)
                        max_details = 500
                        show = items[:max_details]
                        details = "\n".join([f"- {x}" for x in show])
                        if len(items) > len(show):
                            details += f"\n… (+{len(items) - len(show)} more)"

                    if hasattr(b, "set_count_badge"):
                        b.set_count_badge(cnt, tooltip=tip, details_title="Touched items", details_text=details)
                except Exception:
                    pass

                # Append output
                try:
                    out_body = self._format_tool_output(out)
                    suffix = "\n\nOutput:\n" + (out_body or "")
                    if hasattr(b, "append_text"):
                        b.append_text(suffix)
                    else:
                        prev = getattr(b.body, "raw_text", "") if hasattr(b, "body") else ""
                        if hasattr(b, "set_body_text"):
                            b.set_body_text((prev or "") + suffix)
                except Exception:
                    pass

                # Scope badge (SANDBOX)
                try:
                    self._maybe_attach_scope_badge(b, str(args))
                except Exception:
                    pass

                # Path badge
                try:
                    badge = self._extract_primary_path_badge(str(tool_name), str(args))
                    if badge:
                        label, abs_path = badge
                        b.set_path_badge(label, abs_path)
                except Exception:
                    pass

                # Diff badge
                try:
                    if isinstance(wrap_meta, dict):
                        dp = wrap_meta.get("diff_preview")
                        if isinstance(dp, dict):
                            txn_id = dp.get("transaction_id")
                            if isinstance(txn_id, str) and txn_id:
                                add = int(dp.get("added_lines", 0) or 0)
                                rem = int(dp.get("removed_lines", 0) or 0)
                                label = f"+{self._fmt_int_dot(add)}/-{self._fmt_int_dot(rem)}"
                                if hasattr(b, "set_diff_badge"):
                                    b.set_diff_badge(label, txn_id, self._open_diff_viewer_for_transaction)
                except Exception:
                    pass

                try:
                    self.scroll_to_bottom()
                except Exception:
                    pass
                return

        except Exception:
            return

    def add_tool_call_block(self, title: str, args_text: str = "", call_id: Optional[str] = None, tool_name: Optional[str] = None):
        # Remember tool call metadata so we can label/show outputs later.
        if call_id:
            self._tool_calls_by_id[call_id] = {
                "name": tool_name or title,
                "arguments": args_text or "",
            }

        body = self._format_tool_args(args_text)


        # run_subagent: show the target agent name in the header (so it’s scannable in the timeline).
        subagent_name = None
        if tool_name == "run_subagent":
            try:
                obj = json.loads((args_text or "").strip() or "{}")
                if isinstance(obj, dict):
                    sa = obj.get("subagent_name")
                    if isinstance(sa, str) and sa.strip():
                        subagent_name = sa.strip()
            except Exception:
                subagent_name = None

            if subagent_name:
                title = f"{title} — {subagent_name}"
        mem_tools = {"get_memories", "create_memory", "update_memory", "delete_memory"}
        header_color = "#2f2a1a" if (tool_name in mem_tools) else "#2a1f2f"

        block = self.add_collapsible_block(
            title=title,
            body_text=body,
            collapsed=True,
            header_color=header_color,
        )
        try:
            if hasattr(block, "set_status"):
                block.set_status("pending")
        except Exception:
            pass

        # Special marker for Ariane (either consult_ariane legacy or run_subagent).
        try:
            if tool_name == "consult_ariane" and hasattr(block, "set_lead_badge"):
                block.set_lead_badge("✶", tooltip="Ariane")
        except Exception:
            pass

        # Ariane via run_subagent.
        try:
            if tool_name == "run_subagent" and isinstance(subagent_name, str) and subagent_name.lower() == "ariane" and hasattr(block, "set_lead_badge"):
                block.set_lead_badge("✶", tooltip="Ariane")
        except Exception:
            pass


        # Scope badge (SANDBOX)
        try:
            self._maybe_attach_scope_badge(block, args_text)
        except Exception:
            pass
        # Title alignment preference: most tools read like a left-aligned log,
        # but memory tools look cleaner when centered.
        try:
            if tool_name in mem_tools and hasattr(block, "set_title_alignment"):
                block.set_title_alignment("center")
            elif hasattr(block, "set_title_alignment"):
                block.set_title_alignment("left")
        except Exception:
            pass

        if call_id:
            self._tool_call_widgets_by_id[call_id] = block


        # Add a compact clickable path badge for filesystem tools.
        try:
            badge = self._extract_primary_path_badge(tool_name, args_text)
            if badge:
                label, abs_path = badge
                block.set_path_badge(label, abs_path)
        except Exception:
            pass
        return block

    def add_tool_output_block(self, title: str, output_text: Any, call_id: Optional[str] = None, args_text: Optional[str] = None):
        """Attach tool output to the existing tool-call block when possible.

        This keeps the UI calmer (one widget per tool call) and reduces Qt widget churn.
        """
        # Try to pair output with known call metadata.
        tool_name = None
        if call_id and call_id in self._tool_calls_by_id:
            tool_name = self._tool_calls_by_id[call_id].get("name")
            if args_text is None:
                args_text = self._tool_calls_by_id[call_id].get("arguments")

        # Preferred path: update the existing call widget in-place.
        if call_id and call_id in self._tool_call_widgets_by_id:
            block = self._tool_call_widgets_by_id.get(call_id)
            if block is not None:
                # Idempotency: the UI can receive multiple tool-output events for the same call_id.
                if getattr(block, "_tool_output_attached", False):
                    return block
                setattr(block, "_tool_output_attached", True)

                try:
                    st = self._derive_tool_result_status(output_text)
                    if hasattr(block, "set_status"):
                        block.set_status(st or "success")
                except Exception:
                    pass

                # Items-touched count badge + hover/click details
                try:
                    cnt = self._extract_tool_items_count(tool_name, output_text)
                    items = self._extract_tool_touched_items(tool_name, output_text) or []
                    if cnt is None and items:
                        cnt = len(items)

                    tip = None
                    details = None
                    if items:
                        preview_n = 12
                        preview = items[:preview_n]
                        rest = len(items) - len(preview)
                        lines = ["Touched items:"] + [f"- {x}" for x in preview]
                        if rest > 0:
                            lines.append(f"… (+{rest} more)")
                        tip = "\n".join(lines)
                        max_details = 500
                        show = items[:max_details]
                        details = "\n".join([f"- {x}" for x in show])
                        if len(items) > len(show):
                            details += f"\n… (+{len(items) - len(show)} more)"

                    if hasattr(block, "set_count_badge"):
                        block.set_count_badge(cnt, tooltip=tip, details_title="Touched items", details_text=details)
                except Exception:
                    pass

                try:
                    # Append output to the same body.
                    out_body = self._format_tool_output(output_text)
                    suffix = "\n\nOutput:\n" + (out_body or "")
                    if hasattr(block, "append_text"):
                        block.append_text(suffix)
                    else:
                        # Fallback
                        prev = getattr(block.body, "raw_text", "") if hasattr(block, "body") else ""
                        if hasattr(block, "set_body_text"):
                            block.set_body_text((prev or "") + suffix)
                except Exception:
                    pass

                # Ensure filesystem badge exists (in case the call block was created from an orphan tool_output event).
                try:
                    if tool_name and args_text:
                        badge = self._extract_primary_path_badge(tool_name, args_text)
                        if badge:
                            label, abs_path = badge
                            block.set_path_badge(label, abs_path)
                except Exception:
                    pass

                # Diff preview + subhistory toggle live on the same block.
                try:
                    self._attach_subhistory_toggle(block, call_id)
                    self._attach_diff_preview_badge(block, call_id)
                except Exception:
                    pass

                # Treat this as the output widget too (so later meta updates find it).
                try:
                    self._tool_output_widgets_by_call_id[call_id] = block
                except Exception:
                    pass

                self.scroll_to_bottom()
                return block

        # Fallback: orphan output -> create a standalone output block.
        body = "Output:\n" + self._format_tool_output(output_text)
        block = CollapsibleBlock(
            title=(title if not tool_name else f"{title}: {tool_name}"),
            body_text=body,
            collapsed=True,
            header_color="#1e2a32",
        )

        try:
            st = self._derive_tool_result_status(output_text)
            if hasattr(block, "set_status"):
                block.set_status(st or "success")
        except Exception:
            pass

        # Add a compact clickable path badge for filesystem tools.
        try:
            if tool_name and args_text:
                badge = self._extract_primary_path_badge(tool_name, args_text)
                if badge:
                    label, abs_path = badge
                    block.set_path_badge(label, abs_path)
        except Exception:
            pass

        self.chat_layout.addWidget(block)
        if call_id:
            self._tool_output_widgets_by_call_id[call_id] = block

        try:
            if call_id:
                self._attach_subhistory_toggle(block, call_id)
                self._attach_diff_preview_badge(block, call_id)
        except Exception:
            pass

        self.scroll_to_bottom()
        return block



    # --- Wrapper-meta integration (subagent / Ariane tool traces) ---


    # --- Diff receipts (per filesystem transaction) ---

    def _fetch_fs_revision_diff(self, transaction_id: str, timeout_ms: int = 5000) -> Dict[str, Any]:
        """Fetch a diff for a fs revision transaction via EventBus (sync)."""
        try:
            import time
            import uuid
            from PyQt6.QtWidgets import QApplication
            from ...appcore.runtime_context import Runtime

            if not transaction_id:
                return {"status": "error", "message": "transaction_id is required"}

            bus = Runtime.get_event_bus()
            reply_topic = f"fs_revisions.ui.reply.get_diff.{uuid.uuid4()}"
            result: Dict[str, Any] = {}
            unsub = None

            def _on_reply(ev):
                nonlocal result, unsub
                try:
                    if unsub:
                        unsub()
                except Exception:
                    pass
                unsub = None
                payload = getattr(ev, "payload", {}) or {}
                result = payload if isinstance(payload, dict) else {"status": "error"}

            unsub = bus.subscribe(reply_topic, _on_reply)
            bus.publish(
                "fs_revisions.cmd.get_diff",
                {"transaction_id": transaction_id, "reply_topic": reply_topic},
            )

            deadline = time.time() + (timeout_ms / 1000.0)
            while not result and time.time() < deadline:
                try:
                    bus.pump(max_events=50)
                except Exception:
                    pass
                try:
                    QApplication.processEvents()
                except Exception:
                    pass
                time.sleep(0.01)

            if not result:
                try:
                    if unsub:
                        unsub()
                except Exception:
                    pass
                return {"status": "error", "message": "Timeout"}

            return result

        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _open_diff_viewer_for_transaction(self, transaction_id: str) -> None:
        try:
            dlg = SideBySideDiffViewerDialog(transaction_id=str(transaction_id), parent=self)
            dlg.exec()
        except Exception as e:
            try:
                self._show_toast(f"Failed to open diff viewer: {e}")
            except Exception:
                pass

    def _attach_diff_preview_badge(self, block: QWidget, call_id: Optional[str]) -> None:
        if not call_id or not isinstance(call_id, str):
            return
        meta = self._wrap_meta_by_call_id.get(call_id)
        if not isinstance(meta, dict):
            return

        dp = meta.get("diff_preview")
        if not isinstance(dp, dict):
            return

        txn_id = dp.get("transaction_id")
        if not isinstance(txn_id, str) or not txn_id:
            return

        try:
            add = int(dp.get("added_lines", 0) or 0)
            rem = int(dp.get("removed_lines", 0) or 0)
        except Exception:
            add = 0
            rem = 0

        label = f"+{self._fmt_int_dot(add)}/-{self._fmt_int_dot(rem)}"

        try:
            if hasattr(block, "set_diff_badge"):
                block.set_diff_badge(label, txn_id, self._open_diff_viewer_for_transaction)
        except Exception:
            pass
    def update_wrap_meta_by_call_id(self, meta: Any) -> None:
        """Merge wrapper-only tool metadata keyed by call_id.

        This metadata lives *outside* the OpenAI-visible content payload and is used
        only for GUI features (sub-agent tool traces, undo receipts, etc.).
        """
        if not isinstance(meta, dict):
            return

        for call_id, payload in meta.items():
            if not isinstance(call_id, str) or not call_id:
                continue
            if isinstance(payload, dict):
                self._wrap_meta_by_call_id[call_id] = payload

        # Attach to any already-rendered tool output widgets.
        for call_id in list(self._wrap_meta_by_call_id.keys()):
            block = self._tool_output_widgets_by_call_id.get(call_id)
            if block is not None:
                self._attach_subhistory_toggle(block, call_id)
                self._attach_diff_preview_badge(block, call_id)

    def _attach_subhistory_toggle(self, block: QWidget, call_id: Optional[str]) -> None:
        """If this call has a subhistory, expand/collapse will show an indented sublist."""
        if not call_id or not isinstance(call_id, str):
            return
        meta = self._wrap_meta_by_call_id.get(call_id)
        if not isinstance(meta, dict):
            return
        sub = meta.get("subhistory")
        if not isinstance(sub, dict):
            return
        entry_ids = sub.get("entry_ids")
        if not isinstance(entry_ids, list) or not entry_ids:
            return

        # Only hook once.
        if getattr(block, "_subhistory_hooked", False):
            return
        setattr(block, "_subhistory_hooked", True)

        try:
            if hasattr(block, "toggle_btn"):
                block.toggle_btn.toggled.connect(
                    lambda expanded, cid=call_id, b=block: self._toggle_subhistory(expanded, cid, b)
                )
        except Exception:
            pass

    def _toggle_subhistory(self, expanded: bool, call_id: str, parent_block: QWidget) -> None:
        if not call_id:
            return

        if not expanded:
            for w in self._subhistory_widgets_by_call_id.get(call_id, []) or []:
                try:
                    w.setVisible(False)
                except Exception:
                    pass
            return

        # Already built -> just show.
        if call_id in self._subhistory_widgets_by_call_id:
            for w in self._subhistory_widgets_by_call_id.get(call_id, []) or []:
                try:
                    w.setVisible(True)
                except Exception:
                    pass
            return

        meta = self._wrap_meta_by_call_id.get(call_id) or {}
        sub = meta.get("subhistory") if isinstance(meta, dict) else None
        if not isinstance(sub, dict):
            return
        entry_ids = sub.get("entry_ids")
        if not isinstance(entry_ids, list) or not entry_ids:
            return

        store_id = None
        try:
            store_id = sub.get("store_id") or sub.get("name")
        except Exception:
            store_id = None
        if not isinstance(store_id, str) or not store_id:
            store_id = "session_inner_voice"

        inner_hist = self._fetch_subagent_session_entries_wrapped(store_id)
        if not isinstance(inner_hist, list) or not inner_hist:
            return

        # Preserve the subhistory order as recorded.
        by_id = {e.get("id"): e for e in inner_hist if isinstance(e, dict) and isinstance(e.get("id"), str)}
        ordered = [by_id.get(eid) for eid in entry_ids if isinstance(eid, str) and eid in by_id]
        ordered = [e for e in ordered if isinstance(e, dict)]

        sa_name = None
        try:
            sa_name = sub.get("subagent_name")
        except Exception:
            sa_name = None
        if not isinstance(sa_name, str) or not sa_name:
            sa_name = "Ariane"

        widgets = self._render_subhistory_tool_blocks(ordered, subagent_name=str(sa_name))
        if not widgets:
            return

        # Insert right under the parent output block.
        try:
            idx = self.chat_layout.indexOf(parent_block)
            insert_at = (idx + 1) if idx != -1 else self.chat_layout.count()
            for w in widgets:
                self.chat_layout.insertWidget(insert_at, w)
                insert_at += 1
        except Exception:
            for w in widgets:
                self.chat_layout.addWidget(w)


        # Force a geometry pass: large dynamic inserts can temporarily confuse the scroll area,
        # especially with horizontal scrollbars hidden.
        try:
            self._reflow_chat_layout()
        except Exception:
            pass
        self._subhistory_widgets_by_call_id[call_id] = widgets

    def _fetch_subagent_session_entries_wrapped(self, store_id: str, timeout_ms: int = 5000) -> List[Dict[str, Any]]:
        """Fetch wrapped session entries for a sub-agent store via the in-process EventBus (sync)."""
        try:
            import time
            import uuid
            from PyQt6.QtWidgets import QApplication
            from ...appcore.runtime_context import Runtime

            bus = Runtime.get_event_bus()
            reply_topic = f"subagent.ui.reply.session.entries.get.{uuid.uuid4()}"
            result: Dict[str, Any] = {}
            unsub = None

            def _on_reply(ev):
                nonlocal result, unsub
                try:
                    if unsub:
                        unsub()
                except Exception:
                    pass
                unsub = None
                payload = getattr(ev, "payload", {}) or {}
                result = payload if isinstance(payload, dict) else {"status": "error"}

            unsub = bus.subscribe(reply_topic, _on_reply)
            bus.publish("subagent.cmd.session.entries.get", {"reply_topic": reply_topic, "store_id": str(store_id)})

            deadline = time.time() + (timeout_ms / 1000.0)
            while not result and time.time() < deadline:
                try:
                    bus.pump(max_events=50)
                except Exception:
                    pass
                try:
                    QApplication.processEvents()
                except Exception:
                    pass
                time.sleep(0.01)

            if not result:
                try:
                    if unsub:
                        unsub()
                except Exception:
                    pass
                return []

            if result.get("status") != "success":
                return []

            entries = result.get("entries")
            return entries if isinstance(entries, list) else []

        except Exception:
            return []

    def _render_subhistory_tool_blocks(self, wrapped_entries: List[Dict[str, Any]], subagent_name: str = "Ariane") -> List[QWidget]:
        """Render sub-agent tool call/output entries as an indented sublist.

        Match main UX: one block per tool call; outputs merge into the call block.
        """
        from PyQt6.QtWidgets import QWidget, QHBoxLayout

        tool_meta_by_call_id: Dict[str, Dict[str, str]] = {}
        block_by_call_id: Dict[str, CollapsibleBlock] = {}
        out: List[QWidget] = []

        def indent(w: QWidget) -> QWidget:
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(14, 0, 0, 0)
            lay.setSpacing(0)
            lay.addWidget(w, 1)
            return row

        def short_label(label: str, max_len: int = 28) -> str:
            s = str(label or "")
            if len(s) <= max_len:
                return s
            return "…" + s[-(max_len - 1):]

        for we in wrapped_entries or []:
            if not isinstance(we, dict):
                continue

            et = we.get("kind")
            content = we.get("content") if isinstance(we.get("content"), dict) else {}

            if et == "function_call":
                func_name = content.get("name", "")
                func_args = content.get("arguments", "")
                cid = content.get("call_id")

                if isinstance(cid, str) and cid:
                    tool_meta_by_call_id[cid] = {"name": str(func_name), "arguments": str(func_args)}

                b = CollapsibleBlock(
                    title=f"[{subagent_name}] Tool Call: {func_name}",
                    body_text=self._format_tool_args(str(func_args)),
                    collapsed=True,
                    header_color="#262030",
                )
                try:
                    b.set_status("pending")
                except Exception:
                    pass

                try:
                    badge = self._extract_primary_path_badge(str(func_name), str(func_args))
                    if badge:
                        label, abs_path = badge
                        b.set_path_badge(short_label(label), abs_path)
                except Exception:
                    pass

                # Scope badge (SANDBOX)
                try:
                    self._maybe_attach_scope_badge(b, str(func_args))
                except Exception:
                    pass

                out.append(indent(b))
                if isinstance(cid, str) and cid:
                    block_by_call_id[cid] = b

            elif et == "function_call_output":
                cid = content.get("call_id")
                output_text = content.get("output")

                if isinstance(cid, str) and cid and cid in block_by_call_id:
                    b = block_by_call_id[cid]
                    if getattr(b, "_tool_output_attached", False):
                        continue
                    setattr(b, "_tool_output_attached", True)

                    try:
                        st = we.get("result_status")
                        if isinstance(st, str) and st:
                            b.set_status(st)
                        else:
                            b.set_status(self._derive_tool_result_status(output_text) or "success")
                    except Exception:
                        pass

                    try:
                        b.append_text("\n\nOutput:\n" + (self._format_tool_output(output_text) or ""))
                    except Exception:
                        pass

                    try:
                        dp = we.get("diff_preview")
                        if isinstance(dp, dict):
                            txn_id = dp.get("transaction_id")
                            add = int(dp.get("added_lines", 0) or 0)
                            rem = int(dp.get("removed_lines", 0) or 0)
                            if isinstance(txn_id, str) and txn_id:
                                b.set_diff_badge(f"+{add}/-{rem}", txn_id, self._open_diff_viewer_for_transaction)
                    except Exception:
                        pass

                    continue

                meta = tool_meta_by_call_id.get(cid or "", {})
                tool_name = meta.get("name") or ""
                b = CollapsibleBlock(
                    title=f"[{subagent_name}] Tool Output" + (f": {tool_name}" if tool_name else ""),
                    body_text="Output:\n" + self._format_tool_output(output_text),
                    collapsed=True,
                    header_color="#1f2730",
                )
                try:
                    st = we.get("result_status")
                    b.set_status(st if isinstance(st, str) and st else (self._derive_tool_result_status(output_text) or "success"))
                except Exception:
                    pass
                out.append(indent(b))

        return out

    def start_ai_response(self):
        """Start a new AI response section - initially just show markdown."""
        # Create a simple text browser for streaming content
        self.current_ai_widget = NoWheelTextBrowser()
        self.current_ai_widget.setReadOnly(True)
        self.current_ai_widget.setOpenExternalLinks(True)
        self.current_ai_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.current_ai_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.current_ai_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        
        # Avoid 0-height widgets (can cause temporary overlap during fast streaming)
        # while still letting us auto-grow to content.
        self.current_ai_widget.setMinimumHeight(20)
        
        font = QFont('Consolas', 10)
        self.current_ai_widget.setFont(font)
        
        self.current_ai_widget.setStyleSheet("""
            QTextBrowser {
                background-color: transparent;
                color: #d4d4d4;
                border: none;
                padding: 5px;
                font-size: 10pt;
                font-family: 'Consolas', 'Courier New', monospace;
            }
        """)
        
        # Store raw markdown
        self.current_ai_widget.raw_markdown = ""
        # Keep height in sync with width changes to avoid bottom "blank gap" artifacts.
        w = self.current_ai_widget
        w._auto_height_cb = lambda w=w: self.adjust_simple_text_height(w)
        
        # Context menu
        self.current_ai_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.current_ai_widget.customContextMenuRequested.connect(
            lambda pos: self.show_text_context_menu(pos, self.current_ai_widget)
        )
        
        # Auto-adjust height on content change.
        # NOTE: schedule on the next Qt tick; measuring document height immediately after setHtml()
        # can be stale.
        w._auto_height_pending = False

        def _schedule_auto_height(w=w):
            if getattr(w, "_auto_height_pending", False):
                return
            w._auto_height_pending = True

            def _do(w=w):
                w._auto_height_pending = False
                try:
                    self.adjust_simple_text_height(w)
                except Exception:
                    pass
                try:
                    self.scroll_to_bottom()
                except Exception:
                    pass

            QTimer.singleShot(50, _do)

        w.document().contentsChanged.connect(_schedule_auto_height)
        
        self.chat_layout.addWidget(self.current_ai_widget)
        # Force an initial height pass on the next tick.
        QTimer.singleShot(0, lambda w=self.current_ai_widget: self.adjust_simple_text_height(w))
        self.scroll_to_bottom()
        
        return self.current_ai_widget

    def adjust_simple_text_height(self, text_browser):
        """Adjust text browser height to fit content."""
        if text_browser is None:
            return

        w = text_browser.viewport().width()
        if w <= 0:
            QTimer.singleShot(0, lambda tb=text_browser: self.adjust_simple_text_height(tb))
            return

        doc = text_browser.document()
        doc.setTextWidth(w)

        # QSizeF height can be a bit optimistic; use the layout's documentSize and pad generously.
        try:
            height = doc.documentLayout().documentSize().height()
        except Exception:
            height = doc.size().height()

        height = math.ceil(float(height))
        text_browser.setFixedHeight(int(height + 40))

    def append_to_ai_response(self, text, color=None):
        """Append streaming text cheaply.

        During streaming we **do not** re-render markdown on each delta.
        We just append plain text and do the expensive markdown render once in finish_ai_response().
        """
        if self.current_ai_widget is None:
            self.start_ai_response()

        if text is None:
            return
        if not isinstance(text, str):
            text = str(text)
        if not text:
            return

        # Keep the raw markdown source (plain text; no inline HTML spans).
        self.current_ai_widget.raw_markdown = getattr(self.current_ai_widget, "raw_markdown", "") + text

        # Append to the widget as plain text.
        try:
            cursor = self.current_ai_widget.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(text)
            self.current_ai_widget.setTextCursor(cursor)
        except Exception:
            # Fallback
            try:
                self.current_ai_widget.setPlainText(self.current_ai_widget.toPlainText() + text)
            except Exception:
                pass

        # Throttle auto-height recalcs.
        try:
            if not getattr(self.current_ai_widget, "_auto_height_pending", False):
                self.current_ai_widget._auto_height_pending = True

                def _do(tb=self.current_ai_widget):
                    tb._auto_height_pending = False
                    try:
                        self.adjust_simple_text_height(tb)
                    except Exception:
                        pass
                    # Height changes update scrollbar range asynchronously; scroll again after resize.
                    try:
                        self.scroll_to_bottom()
                    except Exception:
                        pass

                QTimer.singleShot(50, _do)
        except Exception:
            pass

        self.scroll_to_bottom()

    def finish_ai_response(self):
        """Called when AI response is complete - now parse and replace with code block widgets."""
        if self.current_ai_widget is None:
            return
        
        # Get the raw markdown
        raw_markdown = getattr(self.current_ai_widget, 'raw_markdown', '')
        
        if not raw_markdown:
            # If we created a streaming widget but never wrote any text into it (e.g. tools-only turn),
            # remove it to avoid leaving a blank vertical gap.
            try:
                self.chat_layout.removeWidget(self.current_ai_widget)
            except Exception:
                pass
            try:
                self.current_ai_widget.deleteLater()
            except Exception:
                pass
            self.current_ai_widget = None
            return
        
        # Check if there are any code blocks
        has_code_blocks = '```' in raw_markdown
        
        if not has_code_blocks:
            # No code blocks: render markdown once (now that streaming is done).
            try:
                html = markdown.markdown(
                    raw_markdown,
                    extensions=['nl2br', 'sane_lists', 'extra', 'fenced_code']
                )
                styled_html = f"""
                <style>
                    body {{
                        font-family: 'Consolas', 'Courier New', monospace;
                        font-size: 10pt;
                        color: #d4d4d4;
                        line-height: 1.5;
                        margin: 0;
                        padding: 0;
                    }}
                    p {{ margin: 0 0 10px 0; }}
                    code {{ background-color: #2d2d2d; padding: 2px 6px; border-radius: 3px; font-family: 'Consolas', monospace; }}
                    pre {{ background-color: #2d2d2d; padding: 10px; border-radius: 5px; overflow-x: auto; }}
                    pre code {{ background-color: transparent; padding: 0; }}
                    a {{ color: #58a6ff; }}
                </style>
                {html}
                """
                self.current_ai_widget.setHtml(styled_html)

                def _post(w=self.current_ai_widget):
                    try:
                        self.adjust_simple_text_height(w)
                    except Exception:
                        pass
                    try:
                        self.scroll_to_bottom()
                    except Exception:
                        pass

                QTimer.singleShot(0, _post)
            except Exception:
                pass

            self.current_ai_widget = None
            return
        
        # Remove the simple text widget
        self.chat_layout.removeWidget(self.current_ai_widget)
        self.current_ai_widget.deleteLater()
        
        # Create container with separate widgets for text and code blocks
        msg_box = QWidget()
        msg_box_layout = QVBoxLayout(msg_box)
        msg_box_layout.setContentsMargins(0, 0, 0, 0)
        msg_box_layout.setSpacing(0)
        
        content_container = QWidget()
        content_layout = QVBoxLayout(content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)
        
        msg_box_layout.addWidget(content_container)
        
        # Parse and render with code blocks
        self.render_markdown_with_code_blocks(raw_markdown, content_layout)
        
        # Add to chat layout
        self.chat_layout.addWidget(msg_box)
        
        # Reset current widget
        self.current_ai_widget = None
        
        self.scroll_to_bottom()

    def render_markdown_with_code_blocks(self, markdown_text, target_layout):
        """Render markdown, extracting code blocks into separate widgets."""
        
        # Extract code blocks with regex
        code_block_pattern = r'```(\w*)\n(.*?)```'
        
        parts = []
        last_end = 0
        
        for match in re.finditer(code_block_pattern, markdown_text, re.DOTALL):
            # Add text before code block
            if match.start() > last_end:
                parts.append(('text', markdown_text[last_end:match.start()]))
            
            # Add code block
            language = match.group(1)
            code = match.group(2).strip()
            parts.append(('code', code, language))
            
            last_end = match.end()
        
        # Add remaining text
        if last_end < len(markdown_text):
            parts.append(('text', markdown_text[last_end:]))
        
        # Render each part
        for part in parts:
            if part[0] == 'text' and part[1].strip():
                text_widget = self.create_text_widget(part[1])
                target_layout.addWidget(text_widget)
            elif part[0] == 'code':
                code_widget = self.CodeBlockWidget(part[1], part[2])
                target_layout.addWidget(code_widget)

    def create_text_widget(self, markdown_text):
        """Create a text widget for non-code markdown content."""
        text_browser = NoWheelTextBrowser()
        text_browser.setReadOnly(True)
        text_browser.setOpenExternalLinks(True)
        text_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        text_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        text_browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        
        font = QFont('Consolas', 10)
        text_browser.setFont(font)
        
        text_browser.setStyleSheet("""
            QTextBrowser {
                background-color: transparent;
                color: #d4d4d4;
                border: none;
                padding: 5px;
                font-size: 10pt;
                font-family: 'Consolas', 'Courier New', monospace;
            }
        """)

        # Keep height in sync with content + width changes.
        text_browser._auto_height_cb = lambda tb=text_browser: self.adjust_simple_text_height(tb)
        
        text_browser.raw_markdown = markdown_text
        
        text_browser.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        text_browser.customContextMenuRequested.connect(
            lambda pos: self.show_text_context_menu(pos, text_browser)
        )
        
        # Convert markdown to HTML with tables extension
        html = markdown.markdown(
            markdown_text,
            extensions=[
                'nl2br',
                'sane_lists',
                'extra',       # Includes tables, footnotes, etc.
                'tables',      # Explicit table support
                'attr_list',   # Attribute lists
                'def_list'     # Definition lists
            ]
        )
        
        # Enhanced styling for all markdown elements
        styled_html = f"""
        <style>
            body {{
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 10pt;
                color: #d4d4d4;
                line-height: 1.6;
                margin: 0;
                padding: 0;
            }}
            p {{ margin: 0 0 10px 0; }}
            h1, h2, h3, h4, h5, h6 {{ color: #ffffff; margin-top: 16px; margin-bottom: 8px; font-weight: 600; line-height: 1.25; }}
            h1 {{ font-size: 2em; border-bottom: 1px solid #444; padding-bottom: 8px; }}
            h2 {{ font-size: 1.5em; border-bottom: 1px solid #444; padding-bottom: 6px; }}
            h3 {{ font-size: 1.25em; }}
            code {{ background-color: #2d2d2d; padding: 2px 6px; border-radius: 3px; font-family: 'Consolas', monospace; font-size: 0.9em; }}
            a {{ color: #58a6ff; text-decoration: none; }}
            ul, ol {{ margin: 8px 0; padding-left: 24px; }}
            li {{ margin: 4px 0; }}
            blockquote {{ border-left: 4px solid #58a6ff; background-color: #2d2d2d; margin: 12px 0; padding: 8px 16px; color: #c9d1d9; font-style: italic; }}
            table {{ border-collapse: collapse; width: 100%; margin: 12px 0; background-color: #1e1e1e; border: 1px solid #3a3a3a; }}
            th {{ padding: 10px 12px; text-align: left; font-weight: 600; color: #ffffff; border-bottom: 2px solid #444; }}
            td {{ padding: 8px 12px; border-bottom: 1px solid #2d2d2d; }}
            hr {{ border: none; border-top: 1px solid #444; margin: 16px 0; }}
            strong, b {{ font-weight: 600; color: #ffffff; }}
            em, i {{ font-style: italic; color: #c9d1d9; }}
        </style>
        {html}
        """
        
        text_browser.setHtml(styled_html)
        
        # Adjust height to content (after layout has a real width)
        text_browser._auto_height_pending = False

        def _schedule_auto_height(tb=text_browser):
            if getattr(tb, "_auto_height_pending", False):
                return
            tb._auto_height_pending = True

            def _do(tb=tb):
                tb._auto_height_pending = False
                try:
                    self.adjust_simple_text_height(tb)
                except Exception:
                    pass
                try:
                    self.scroll_to_bottom()
                except Exception:
                    pass

            QTimer.singleShot(50, _do)

        text_browser.document().contentsChanged.connect(_schedule_auto_height)
        QTimer.singleShot(50, _schedule_auto_height)
        
        return text_browser

    def show_text_context_menu(self, pos, text_browser):
        """Show context menu with copy options."""
        if text_browser is None:
            return
        menu = QMenu(self)
        copy_action = QAction("Copy", self)
        copy_action.triggered.connect(lambda: text_browser.copy())
        menu.addAction(copy_action)
        select_all_action = QAction("Select All", self)
        select_all_action.triggered.connect(lambda: text_browser.selectAll())
        menu.addAction(select_all_action)
        menu.addSeparator()
        copy_raw = QAction("Copy as Markdown", self)
        copy_raw.triggered.connect(lambda: self.copy_raw_markdown(text_browser))
        menu.addAction(copy_raw)
        menu.exec(text_browser.mapToGlobal(pos))

    def copy_raw_markdown(self, text_browser):
        """Copy the raw markdown text from the specific text browser widget."""
        clipboard = QApplication.clipboard()
        raw_text = getattr(text_browser, 'raw_markdown', '')
        clipboard.setText(raw_text)
    

    def _sync_chat_alignment(self):
        """Keep chat layout top-down (professional chat behavior).

        We intentionally do **not** pin short chats to the bottom. That old behavior
        caused random jitter/gaps during streaming due to transient scrollbar max changes.
        """
        try:
            self.chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        except Exception:
            pass


    def _reflow_chat_layout(self) -> None:
        """Force Qt to recompute layout/geometry after big dynamic inserts.

        This prevents the occasional "content shifts left" glitch when expanding
        subhistory blocks while horizontal scrollbars are hidden.
        """
        try:
            if getattr(self, "chat_container", None) is not None:
                self.chat_container.updateGeometry()
                self.chat_container.adjustSize()
        except Exception:
            pass

        try:
            if getattr(self, "scrollable_area", None) is not None:
                self.scrollable_area.viewport().update()
                # Snap horizontal scroll back to 0 (even if hidden).
                hsb = self.scrollable_area.horizontalScrollBar()
                if hsb is not None:
                    hsb.setValue(0)
                    QTimer.singleShot(0, lambda hsb=hsb: hsb.setValue(0))
                    QTimer.singleShot(50, lambda hsb=hsb: hsb.setValue(0))
        except Exception:
            pass

    def scroll_to_bottom(self):
        """Scroll to the bottom of the chat (throttled)."""
        if getattr(self, "_scroll_pending", False):
            return
        self._scroll_pending = True
        QTimer.singleShot(50, self._do_scroll)

    def _do_scroll(self):
        """Actually perform the scroll."""
        self._scroll_pending = False

        scroll = getattr(self, "scrollable_area", None)
        if not scroll:
            return

        sb = scroll.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())
            # Do it again next tick after Qt applies pending layout/size changes.
            QTimer.singleShot(0, lambda sb=sb: sb.setValue(sb.maximum()))
    
    # --- Composer UX (expand/collapse, attach menu, emoji grid) ---

    def _set_composer_popup_open(self, is_open: bool) -> None:
        self._composer_popup_open = bool(is_open)

    def _send_button_size(self) -> int:
        # Slightly smaller than the previous "big bubble" to match the original feel.
        return 48 if getattr(self, "_composer_expanded", False) else 40

    def _apply_send_button_visuals(self) -> None:
        """Apply non-sending visuals (size + normal style). Sending visuals are handled elsewhere."""
        size = self._send_button_size()
        radius = int(size / 2)
        self.send_button.setFixedSize(size, size)
        self.send_button.setStyleSheet(
            f"""
            QPushButton {{
                background-color: #56344F;
                color: white;
                border: none;
                border-radius: {radius}px;
                font-size: 12pt;
            }}
            QPushButton:hover {{
                background-color: #6A4662;
            }}
            QPushButton:disabled {{
                background-color: #444444;
                color: #888888;
            }}
            """
        )

    def _set_composer_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        self._composer_expanded = expanded

        # Buttons only show in expanded mode (keeps the compact composer clean)
        if hasattr(self, "composer_left_column") and self.composer_left_column:
            self.composer_left_column.setVisible(expanded)
        else:
            self.plus_button.setVisible(expanded)
            self.emoji_button.setVisible(expanded)

        # Input grows even when empty (only while expanded)
        self.input_field.set_min_height_override(self._composer_expanded_min_h if expanded else None)


        # Send button becomes "big circle" in expanded mode
        if not getattr(self, "is_sending", False):
            self._apply_send_button_visuals()

    def _composer_is_effectively_empty(self) -> bool:
        txt = (self.input_field.toPlainText() or "").strip()
        if txt:
            return False
        if getattr(self, "dropped_files", None):
            if len(self.dropped_files) > 0:
                return False
        if getattr(self, "screenshots", None):
            if len(self.screenshots) > 0:
                return False
        return True

    def _maybe_collapse_composer_after_focus_out(self) -> None:
        if getattr(self, "_composer_popup_open", False):
            return
        if not self._composer_is_effectively_empty():
            return

        fw = QApplication.focusWidget()
        if fw is not None and self.composer_widget.isAncestorOf(fw):
            return

        self._set_composer_expanded(False)

    def _build_emoji_menu(self) -> None:
        # Build once; refresh the "Recent" tab right before showing.
        self.emoji_menu.clear()

        recent = list(getattr(self, "_emoji_recent", []) or [])
        panel = EmojiPickerWidget(recent_emojis=recent, cols=8, parent=self.emoji_menu)
        panel.emojiSelected.connect(self._insert_emoji)

        action = QWidgetAction(self.emoji_menu)
        action.setDefaultWidget(panel)
        self.emoji_menu.addAction(action)

        self._emoji_picker_widget = panel

    def _show_emoji_menu(self) -> None:
        # Keep "Recent" fresh.
        try:
            if getattr(self, "_emoji_picker_widget", None) is not None:
                self._emoji_picker_widget.set_recent_emojis(getattr(self, "_emoji_recent", []))
        except Exception:
            pass

        # Anchor under the emoji button
        pos = self.emoji_button.mapToGlobal(QPoint(0, self.emoji_button.height()))
        self.emoji_menu.popup(pos)

    def _remember_recent_emoji(self, emoji: str) -> None:
        try:
            recent = list(getattr(self, "_emoji_recent", []) or [])
            if emoji in recent:
                recent.remove(emoji)
            recent.insert(0, emoji)
            del recent[24:]
            self._emoji_recent = recent
        except Exception:
            pass

    def _insert_emoji(self, emoji: str) -> None:
        self._remember_recent_emoji(emoji)
        self.emoji_menu.close()
        self.input_field.setFocus(Qt.FocusReason.OtherFocusReason)
        cursor = self.input_field.textCursor()
        cursor.insertText(emoji)
        self.input_field.setTextCursor(cursor)

    def open_files_folders_picker(self) -> None:
        """Attach files/folders as *file attachments* (no thumbnail).

        Even if the selected items are images, they are treated as files here.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("Attach")
        dlg.setModal(True)
        dlg.setStyleSheet("QDialog { background-color: #252526; }")

        picked_paths = {"paths": []}

        v = QVBoxLayout(dlg)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        info = QLabel("Pick files or a folder to attach.\n(Images picked here are treated as files.)")
        info.setStyleSheet("QLabel { color: #b5b5b5; font-size: 9pt; }")
        v.addWidget(info)

        row = QWidget()
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(10)

        btn_files = QPushButton("Files…")
        btn_folder = QPushButton("Folder…")
        for b in (btn_files, btn_folder):
            b.setStyleSheet(
                "QPushButton { background-color: #3d3d3d; color: #d4d4d4; border: none; border-radius: 6px; padding: 8px 14px; font-size: 10pt; }"
                "QPushButton:hover { background-color: #4d4d4d; }"
            )

        row_l.addWidget(btn_files)
        row_l.addWidget(btn_folder)
        row_l.addStretch(1)
        v.addWidget(row)

        def _pick_files():
            self._set_composer_popup_open(True)
            try:
                paths, _ = QFileDialog.getOpenFileNames(self, "Select file(s)", "", "All Files (*)")
            finally:
                self._set_composer_popup_open(False)
            if paths:
                picked_paths["paths"] = list(paths)
                dlg.accept()

        def _pick_folder():
            self._set_composer_popup_open(True)
            try:
                folder = QFileDialog.getExistingDirectory(self, "Select folder")
            finally:
                self._set_composer_popup_open(False)
            if folder:
                picked_paths["paths"] = [folder]
                dlg.accept()

        btn_files.clicked.connect(_pick_files)
        btn_folder.clicked.connect(_pick_folder)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            paths = picked_paths.get("paths") or []
            if paths:
                self.handle_paste_files(paths)

    def open_images_picker(self) -> None:
        """Attach images as thumbnails (max 5), same behavior as clipboard/screenshot."""
        slots = max(0, int(self.max_screenshots) - len(self.screenshots))
        if slots <= 0:
            QMessageBox.warning(
                self,
                "Maximum Images",
                f"You can attach a maximum of {self.max_screenshots} images per message.",
            )
            return

        self._set_composer_popup_open(True)
        try:
            paths, _ = QFileDialog.getOpenFileNames(
                self,
                "Select image(s)",
                "",
                "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All Files (*)",
            )
        finally:
            self._set_composer_popup_open(False)

        if not paths:
            return

        if len(paths) > slots:
            QMessageBox.information(
                self,
                "Image Limit",
                f"Only the first {slots} image(s) were attached (max {self.max_screenshots}).",
            )

        for p in list(paths)[:slots]:
            pm = QPixmap(p)
            if pm.isNull():
                continue
            self.handle_paste_image(pm)

    # -----------------------------------------------------------------
    # Pre-first-signal indicator (cute "..." ticker)
    # -----------------------------------------------------------------

    def _show_pre_first_signal_indicator(self) -> None:
        """Show a tiny placeholder in the chat timeline until the first agent event arrives."""
        try:
            # Reset state
            self._pre_first_signal_has_seen_signal = False
            self.pre_first_signal_step = 0

            # If a previous placeholder exists, remove it.
            try:
                self._hide_pre_first_signal_indicator()
                self._pre_first_signal_has_seen_signal = False
            except Exception:
                pass

            from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel
            from PyQt6.QtCore import Qt, QTimer

            container = QWidget()
            lay = QHBoxLayout(container)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)

            bubble = QLabel("…")
            bubble.setStyleSheet(
                "QLabel { "
                "background-color: rgba(255, 255, 255, 18); "
                "color: rgba(255, 255, 255, 160); "
                "padding: 6px 10px; "
                "border-radius: 10px; "
                "font-size: 11px; "
                "}"
            )

            lay.addWidget(bubble, alignment=Qt.AlignmentFlag.AlignLeft)
            lay.addStretch(1)

            # Attach so we can update/remove it later.
            self._pre_first_signal_widget = container
            self._pre_first_signal_label = bubble

            # Add to the chat timeline.
            try:
                if hasattr(self, "chat_layout") and self.chat_layout is not None:
                    self.chat_layout.addWidget(container)
            except Exception:
                pass

            # Start animation
            try:
                if hasattr(self, "pre_first_signal_timer"):
                    self.pre_first_signal_timer.start(250)
            except Exception:
                pass

            try:
                QTimer.singleShot(0, self._do_scroll)
            except Exception:
                pass

        except Exception:
            pass

    def _hide_pre_first_signal_indicator(self) -> None:
        try:
            if hasattr(self, "pre_first_signal_timer"):
                self.pre_first_signal_timer.stop()
        except Exception:
            pass

        try:
            w = getattr(self, "_pre_first_signal_widget", None)
            if w is not None:
                try:
                    if hasattr(self, "chat_layout") and self.chat_layout is not None:
                        self.chat_layout.removeWidget(w)
                except Exception:
                    pass
                try:
                    w.setParent(None)
                    w.deleteLater()
                except Exception:
                    pass
        finally:
            try:
                self._pre_first_signal_widget = None
                self._pre_first_signal_label = None
            except Exception:
                pass

        try:
            self._pre_first_signal_has_seen_signal = True
        except Exception:
            pass

    def mark_first_agent_signal(self) -> None:
        """Hide the placeholder once we receive any streamed event (tool/text/error/subagent)."""
        try:
            if bool(getattr(self, "_pre_first_signal_has_seen_signal", True)):
                return
            self._hide_pre_first_signal_indicator()
        except Exception:
            pass

    def animate_pre_first_signal(self) -> None:
        try:
            if bool(getattr(self, "_pre_first_signal_has_seen_signal", True)):
                self._hide_pre_first_signal_indicator()
                return

            self.pre_first_signal_step = (int(getattr(self, "pre_first_signal_step", 0)) + 1) % 4
            dots = ["", ".", "..", "..."]
            txt = dots[self.pre_first_signal_step]

            lab = getattr(self, "_pre_first_signal_label", None)
            if lab is not None:
                lab.setText(txt or "…")
        except Exception:
            pass

    def handle_send_button_click(self):
        """Handle send button click - either send message or stop inference."""
        if self.is_sending:
            self.stop_inference()
        else:
            self.send_message()
    
    def send_message(self, text=None):
        """Send message from input field."""
        if text is None:
            text = self.input_field.toPlainText().strip()

        files_list = self.dropped_files.copy()
        
        if (text or self.screenshots) and self.parent_widget:
            self.input_field.clear_text()
            self.clear_attached_files()
            screenshot_data_list = [s["data"] for s in self.screenshots]
            self.parent_widget.send_to_agent(text, files_list, screenshot_data_list)
            self.clear_all_screenshots()
            self._set_composer_expanded(False)
            QTimer.singleShot(100, self._do_scroll)
    
    def start_sending_state(self):
        """Start the sending animation state and disable UI interactions."""
        self.is_sending = True
        self.send_animation_step = 0
        self.send_button.setText("⠋")
        self.send_animation_timer.start(100)
        self.input_field.setEnabled(False)
        self.screenshot_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.new_chat_button.setEnabled(False)

        # Show a small indicator until the first streamed event arrives.
        self._show_pre_first_signal_indicator()

    def stop_sending_state(self):
        """Stop the sending animation and return to normal state."""
        self.is_sending = False
        self.send_animation_timer.stop()
        self.send_button.setText("➤")
        self._apply_send_button_visuals()
        self.input_field.setEnabled(True)
        self.screenshot_button.setEnabled(True)
        self.clear_button.setEnabled(True)
        self.new_chat_button.setEnabled(True)

        self._hide_pre_first_signal_indicator()

    def animate_sending(self):
        """Clean rotating spinner animation."""
        self.send_animation_step = (self.send_animation_step + 1) % 8
        spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧"]
        self.send_button.setText(spinner_chars[self.send_animation_step])
        size = self._send_button_size()
        radius = int(size / 2)
        self.send_button.setFixedSize(size, size)
        self.send_button.setStyleSheet(
            f"""
            QPushButton {{
                background-color: #c83232;
                color: white;
                border: none;
                border-radius: {radius}px;
                font-size: 12pt;
            }}
            QPushButton:hover {{
                background-color: #d84444;
            }}
            """
        )
    
    def stop_inference(self):
        """Stop the AI inference by notifying parent widget."""
        print("Stop inference requested")

        # If the user stops before the first streamed event arrives, hide the indicator immediately.
        self._hide_pre_first_signal_indicator()

        if self.parent_widget:
            self.parent_widget.stop_agent_inference()
        # Keep the UI in "sending" state until we actually receive the terminal
        # stream.finished / response.agent.done events. Otherwise Stop looks fake and
        # allows overlapping runs.

    
    def request_delete_session(self):
        """Request parent to permanently delete the current session with confirmation."""
        reply = QMessageBox.question(
            self,
            'Delete Session',
            (
                'Delete the current session permanently?\n\n'
                'This removes the full session log and its linked persistent sub-agent sessions. '
                'If this was the last session, a new empty one will be created automatically.\n\n'
                'This action cannot be undone.'
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes and self.parent_widget:
            self.parent_widget.delete_current_session()
    
    def clear_chat(self):
        """Clear all chat messages from UI."""
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.current_ai_widget = None
        self._tool_calls_by_id = {}
        self._tool_call_widgets_by_id = {}
        self._tool_output_widgets_by_call_id = {}
        self._wrap_meta_by_call_id = {}
        self._subhistory_widgets_by_call_id = {}
        self._injected_widgets_by_call_id = {}
        self._live_subagent_state_by_parent_call_id = {}
        self._reasoning_line_buffer = ""
        self._last_reasoning_title = None
        self._reasoning_in_fence = False
        self.current_reasoning_block = None

        # Clear any waiting-for-first-signal placeholder (best-effort)
        try:
            self._hide_pre_first_signal_indicator()
        except Exception:
            pass
        self.session_entries = []
    

    def reset_stream_state(self):
        """Reset per-run UI state (call/args caches, streaming pointers)."""
        self.current_ai_widget = None
        self.current_reasoning_block = None
        self._reasoning_line_buffer = ""
        self._last_reasoning_title = None
        self._reasoning_in_fence = False
        self._tool_calls_by_id = {}

    def eventFilter(self, obj, event):
        # Composer expand/collapse
        try:
            if obj is getattr(self, "input_field", None):
                if event.type() == QEvent.Type.FocusIn:
                    # Don't expand just because the window was activated/opened.
                    # Expand only on intentional focus (mouse click/tab/shortcut).
                    reason = None
                    try:
                        reason = event.reason()
                    except Exception:
                        reason = None

                    if reason in (
                        Qt.FocusReason.MouseFocusReason,
                        Qt.FocusReason.TabFocusReason,
                        Qt.FocusReason.ShortcutFocusReason,
                        Qt.FocusReason.BacktabFocusReason,
                    ):
                        self._set_composer_expanded(True)
                elif event.type() == QEvent.Type.FocusOut:
                    QTimer.singleShot(0, self._maybe_collapse_composer_after_focus_out)
        except Exception:
            pass

        # Eat Ctrl+wheel zoom inside this window; keep scrolling normal.
        try:
            if event.type() == QEvent.Type.Wheel:
                mods = event.modifiers()
                if mods & Qt.KeyboardModifier.ControlModifier:
                    # Only if the wheel event is happening on a widget that belongs to this ChatWindow.
                    if isinstance(obj, QWidget) and (obj is self or self.isAncestorOf(obj)):
                        event.accept()
                        return True
        except Exception:
            pass

        return super().eventFilter(obj, event)

    # ------------------------------
    # Drag & drop (files/images)
    # ------------------------------

    # NOTE (drag/drop): This ChatWindow has its own path-parsing for drag/drop because
    # drops onto the *window/history* should add file attachments (self.dropped_files)
    # and/or image attachments (self.screenshots). Similar path-parsing logic exists in
    # MultilineInput.dropEvent so VS Code (text/plain paths) works when dropping onto
    # the text input too. If you change one, update the other.

    _DROP_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

    def _paths_from_text(self, text: str) -> List[str]:
        """Parse dropped plain-text into local filesystem paths.

        VS Code often drag-drops as text/plain (paths), not as URLs.
        """
        if not isinstance(text, str):
            return []

        out: List[str] = []
        for raw in (text or "").splitlines():
            s = (raw or "").strip()
            if not s:
                continue

            # Strip quotes
            if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
                s = s[1:-1].strip()

            if not s:
                continue

            # Handle file:// URLs explicitly (don't feed raw Windows paths to QUrl)
            if s.lower().startswith("file:"):
                try:
                    url = QUrl(s)
                    if url.isLocalFile():
                        s = url.toLocalFile()
                except Exception:
                    pass

            try:
                if os.path.exists(s):
                    out.append(s)
            except Exception:
                pass

        return out

    def _is_image_path(self, path: str) -> bool:
        try:
            ext = os.path.splitext(path)[1].lower()
        except Exception:
            ext = ""
        return ext in self._DROP_IMAGE_EXTS

    def _extract_drop_paths(self, mime) -> List[str]:
        paths: List[str] = []

        try:
            if mime and mime.hasUrls():
                for url in mime.urls() or []:
                    try:
                        if url.isLocalFile():
                            p = url.toLocalFile()
                            if p:
                                paths.append(p)
                    except Exception:
                        continue
        except Exception:
            pass

        # If no URLs, try plain text paths (VS Code)
        if not paths:
            try:
                if mime and mime.hasText():
                    paths = self._paths_from_text(mime.text())
            except Exception:
                pass

        return paths

    def dragEnterEvent(self, event):
        try:
            md = event.mimeData()
            if md and (md.hasImage() or md.hasUrls()):
                event.acceptProposedAction()
                return
            if md and md.hasText():
                if self._paths_from_text(md.text()):
                    event.acceptProposedAction()
                    return
        except Exception:
            pass

        event.ignore()

    def dragMoveEvent(self, event):
        try:
            md = event.mimeData()
            if md and (md.hasImage() or md.hasUrls()):
                event.acceptProposedAction()
                return
            if md and md.hasText():
                if self._paths_from_text(md.text()):
                    event.acceptProposedAction()
                    return
        except Exception:
            pass

        event.ignore()

    def dropEvent(self, event):
        md = None
        try:
            md = event.mimeData()
        except Exception:
            md = None

        handled_any = False
        added_files = 0

        # 1) Raw image data (e.g., from browser)
        try:
            if md and md.hasImage():
                img = md.imageData()
                pm = None
                try:
                    # QMimeData.imageData() is usually a QImage
                    from PyQt6.QtGui import QImage

                    if isinstance(img, QPixmap):
                        pm = img
                    elif isinstance(img, QImage):
                        pm = QPixmap.fromImage(img)
                except Exception:
                    pm = None

                if pm is not None and not pm.isNull():
                    self.handle_paste_image(pm)
                    handled_any = True
        except Exception:
            pass

        # 2) Files/folders (URLs or text paths)
        paths = self._extract_drop_paths(md)
        if paths:
            for p in paths:
                try:
                    # Image file -> image attachment (if we still have slots)
                    if (
                        self._is_image_path(p)
                        and os.path.isfile(p)
                        and len(self.screenshots) < int(self.max_screenshots)
                    ):
                        pm = QPixmap(p)
                        if not pm.isNull():
                            self.handle_paste_image(pm)
                            handled_any = True
                            continue

                    # Otherwise -> file attachment
                    if p not in self.dropped_files:
                        self.dropped_files.append(p)
                        added_files += 1
                        handled_any = True
                except Exception:
                    continue

        if added_files > 0:
            self.update_attached_files_display()

        if handled_any:
            event.acceptProposedAction()
        else:
            event.ignore()
    
    def update_attached_files_display(self):
        """Update the display of attached files."""
        while self.files_layout.count():
            item = self.files_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if self.dropped_files:
            for path in self.dropped_files:
                try:
                    kind = "dir" if os.path.isdir(path) else "file"
                except Exception:
                    kind = "file"

                chip = self._make_attachment_chip(
                    path=str(path),
                    kind=kind,
                    removable=True,
                    on_remove=self.remove_file,
                    text_color="#d4d4d4",
                    bg="rgba(255,255,255,0.08)",
                    bg_hover="rgba(255,255,255,0.14)",
                )
                if chip is not None:
                    self.files_layout.addWidget(chip)

            self.attached_files_widget.show()
        else:
            self.attached_files_widget.hide()
    
    def remove_file(self, file_path):
        if file_path in self.dropped_files:
            self.dropped_files.remove(file_path)
            self.update_attached_files_display()
    
    def clear_attached_files(self):
        self.dropped_files.clear()
        self.update_attached_files_display()
    
    def capture_screenshot(self):
        """Capture a screenshot of the entire screen."""
        if len(self.screenshots) >= self.max_screenshots:
            QMessageBox.warning(self, "Maximum Screenshots", f"You can attach a maximum of {self.max_screenshots} screenshots per message.")
            return
        
        try:
            self.hide()
            if self.parent_widget:
                self.parent_widget.hide()
            QTimer.singleShot(300, self._perform_screenshot)
        except Exception as e:
            print(f"Screenshot error: {e}")
            QMessageBox.warning(self, "Screenshot Error", f"Failed to capture screenshot: {str(e)}")
    
    def _perform_screenshot(self):
        try:
            from PyQt6.QtGui import QGuiApplication
            screens = QGuiApplication.screens()
            if not screens:
                raise RuntimeError("No screens detected")
            
            # Show overlays on all screens so selection can happen anywhere
            self.selection_overlays = []
            
            def on_selected(pixmap):
                self._teardown_overlays()
                self._handle_screenshot_selection(pixmap)
            
            def on_cancelled():
                self._teardown_overlays()
                self._handle_screenshot_cancelled()
            
            for screen in screens:
                shot = screen.grabWindow(0)
                overlay = ScreenshotSelector(shot)
                overlay.screenshot_selected.connect(on_selected)
                overlay.screenshot_cancelled.connect(on_cancelled)
                overlay.setGeometry(screen.geometry())
                overlay.show()
                self.selection_overlays.append(overlay)
        except Exception as e:
            self.show()
            print(f"Screenshot error: {e}")
            traceback.print_exc()
            QMessageBox.warning(self, "Screenshot Error", f"Failed to capture screenshot: {str(e)}")
    
    def _teardown_overlays(self):
        overlays = getattr(self, "selection_overlays", [])
        for ov in overlays:
            try:
                ov.close()
            except Exception:
                pass
        self.selection_overlays = []
    
    def _handle_screenshot_selection(self, selected_pixmap):
        try:
            import base64
            from PyQt6.QtCore import QBuffer, QIODevice
            
            self._teardown_overlays()
            if self.parent_widget:
                self.parent_widget.show()
            self.show()
            self.raise_()
            self.activateWindow()
            
            if selected_pixmap:
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                selected_pixmap.save(buffer, "PNG")
                buffer.close()
                screenshot_data = base64.b64encode(buffer.data()).decode('utf-8')
                self.screenshots.append({"data": screenshot_data, "pixmap": selected_pixmap})
                self.update_screenshots_display()
        except Exception as e:
            print(f"Screenshot processing error: {e}")
            traceback.print_exc()
    
    def _handle_screenshot_cancelled(self):
        self._teardown_overlays()
        if self.parent_widget:
            self.parent_widget.show()
        self.show()
        self.raise_()
        self.activateWindow()
    
    def update_screenshots_display(self):
        while self.screenshots_layout.count():
            item = self.screenshots_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        if self.screenshots:
            for idx, screenshot in enumerate(self.screenshots):
                thumb_widget = QWidget()
                thumb_layout = QVBoxLayout(thumb_widget)
                thumb_layout.setContentsMargins(2, 2, 2, 2)
                thumb_layout.setSpacing(2)
                
                thumb_label = QLabel()
                thumb_pixmap = screenshot["pixmap"].scaled(80, 60, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                thumb_label.setPixmap(thumb_pixmap)
                thumb_label.setStyleSheet("QLabel { background-color: #2d2d2d; border: 2px solid #4da6ff; border-radius: 3px; padding: 2px; } QLabel:hover { border: 2px solid #66b3ff; }")
                thumb_label.setCursor(Qt.CursorShape.PointingHandCursor)
                thumb_label.mousePressEvent = lambda event, p=screenshot["pixmap"]: self.show_screenshot_fullsize(p)
                
                remove_btn = QPushButton("✖")
                remove_btn.setFixedSize(16, 16)
                remove_btn.setToolTip(f"Remove screenshot {idx + 1}")
                remove_btn.clicked.connect(lambda checked, i=idx: self.remove_screenshot(i))
                remove_btn.setStyleSheet("QPushButton { background-color: #ff6b6b; color: white !important; border: none; border-radius: 8px; font-size: 9pt; } QPushButton:hover { background-color: #ff5555; }")
                
                thumb_layout.addWidget(thumb_label, alignment=Qt.AlignmentFlag.AlignCenter)
                thumb_layout.addWidget(remove_btn, alignment=Qt.AlignmentFlag.AlignCenter)
                thumb_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                thumb_widget.adjustSize()
                self.screenshots_layout.addWidget(thumb_widget)
            
            self.screenshots_widget.show()
        else:
            self.screenshots_widget.hide()

        # Nudge Qt to recompute layout immediately; showing/hiding this widget while streaming
        # can otherwise cause one-frame geometry glitches.
        try:
            if self.layout():
                self.layout().invalidate()
                self.layout().activate()
        except Exception:
            pass
    
    def show_screenshot_fullsize(self, pixmap):
        # Fit-to-window preview by default (no annoying scroll for large images).
        # If the user wants 1:1 pixels, they can just enlarge the window.
        class _PreviewDialog(QDialog):
            def __init__(self, pm: QPixmap, parent=None):
                super().__init__(parent)
                self._orig = pm
                self.setWindowTitle("Screenshot Preview")
                self.setModal(False)
                self.resize(1000, 700)

                layout = QVBoxLayout(self)
                layout.setContentsMargins(0, 0, 0, 0)

                self.scroll = QScrollArea()
                self.scroll.setWidgetResizable(True)

                self.label = QLabel()
                self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.label.setStyleSheet("QLabel { background-color: #111111; }")
                self.scroll.setWidget(self.label)

                layout.addWidget(self.scroll)
                self._apply_fit()

            def _apply_fit(self):
                if self._orig is None or self._orig.isNull():
                    return
                vp = self.scroll.viewport().size()
                if vp.width() <= 0 or vp.height() <= 0:
                    return
                fitted = self._orig.scaled(
                    vp,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.label.setPixmap(fitted)
                self.label.adjustSize()

            def resizeEvent(self, event):
                super().resizeEvent(event)
                self._apply_fit()

        dlg = _PreviewDialog(pixmap, parent=self)
        dlg.show()
    
    def remove_screenshot(self, index):
        if 0 <= index < len(self.screenshots):
            self.screenshots.pop(index)
            self.update_screenshots_display()
    
    def clear_all_screenshots(self):
        self.screenshots.clear()
        self.update_screenshots_display()
    
    def open_json_viewer(self):
        """Open the session JSON viewer window."""
        # Get the history JSON window from parent widget
        if self.parent_widget and hasattr(self.parent_widget, 'session_json_window'):
            json_window = self.parent_widget.session_json_window
            json_window.refresh_content()
            json_window.show()
            json_window.raise_()
            json_window.activateWindow()
    

    def open_canvas_studio(self) -> None:
        """Open the Canvas Studio window (separate component)."""
        try:
            w = getattr(self, "_canvas_studio_window", None)
            if w is None:
                # Create as a top-level window (no Qt parent) so it gets its own taskbar entry.
                # (Owned/parented dialogs on Windows often don't appear separately in the taskbar.)
                w = CanvasStudioWindow(parent=None)
                setattr(self, "_canvas_studio_window", w)
            w.show()
            w.raise_()
            w.activateWindow()
        except Exception:
            return

    def open_agents_studio(self) -> None:
        """Open the Agents Studio window (separate component)."""
        try:
            w = getattr(self, "_agents_studio_window", None)
            if w is None:
                # Create as a top-level window (no Qt parent) so it gets its own taskbar entry.
                w = AgentsStudioWindow(parent=None)
                setattr(self, "_agents_studio_window", w)
            w.show()
            w.raise_()
            w.activateWindow()
        except Exception:
            return

    def handle_paste_image(self, pixmap):
        """Handle pasted image from clipboard (Ctrl+V)."""
        if len(self.screenshots) >= self.max_screenshots:
            QMessageBox.warning(self, "Maximum Screenshots", 
                f"You can attach a maximum of {self.max_screenshots} screenshots per message.")
            return
        
        try:
            import base64
            from PyQt6.QtCore import QBuffer, QIODevice

            # Convert pixmap to base64
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            pixmap.save(buffer, "PNG")
            buffer.close()
            screenshot_data = base64.b64encode(buffer.data()).decode('utf-8')
            
            # Add to screenshots list
            self.screenshots.append({"data": screenshot_data, "pixmap": pixmap})
            self.update_screenshots_display()
            
            # Show brief feedback in console
            print(f"✓ Image pasted from clipboard ({pixmap.width()}x{pixmap.height()})")
            
        except Exception as e:
            print(f"✗ Paste image error: {e}")
            traceback.print_exc()
            QMessageBox.warning(self, "Paste Error", f"Failed to paste image: {str(e)}")
    
    def handle_paste_files(self, file_paths):
        """Handle pasted files/folders from clipboard (Ctrl+V)."""
        try:
            added_count = 0
            for path in file_paths:
                if path not in self.dropped_files:
                    self.dropped_files.append(path)
                    added_count += 1
            
            if added_count > 0:
                self.update_attached_files_display()
                print(f"✓ Pasted {added_count} file(s)/folder(s) from clipboard")
            else:
                print(f"ℹ All pasted files were already attached")
            
        except Exception as e:
            print(f"✗ Paste files error: {e}")
            traceback.print_exc()
            QMessageBox.warning(self, "Paste Error", f"Failed to paste files: {str(e)}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
    
    def closeEvent(self, event):
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        settings.setValue("chat_window_pos", (self.pos().x(), self.pos().y()))
        self.hide()
        event.ignore()

    def hideEvent(self, event):
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        settings.setValue("chat_window_pos", (self.pos().x(), self.pos().y()))
        super().hideEvent(event)

    def showEvent(self, event):
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        saved_pos = settings.value("chat_window_pos", None)
        validated_pos = validate_window_position(saved_pos, 600, 700)
        if validated_pos is not None:
            x, y = validated_pos
            self.move(x, y)
        super().showEvent(event)
        self.scroll_to_bottom()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            if self.windowState() & Qt.WindowState.WindowMinimized:
                QTimer.singleShot(0, self._hide_on_minimize)
        super().changeEvent(event)

    def _hide_on_minimize(self):
        # Mimic clicking X: hide without quitting the app
        self.setWindowState(Qt.WindowState.WindowNoState)
        self.hide()

    def _on_telemetry_toggled(self, checked: bool) -> None:
        if getattr(self, "is_sending", False):
            try:
                self._show_toast("Currently running")
            except Exception:
                pass
            try:
                self.telemetry_button.blockSignals(True)
                self.telemetry_button.setChecked(not bool(checked))
            except Exception:
                pass
            finally:
                try:
                    self.telemetry_button.blockSignals(False)
                except Exception:
                    pass
            return

        try:
            if not self.parent_widget:
                return
            if hasattr(self.parent_widget, "request_set_session_telemetry"):
                self.parent_widget.request_set_session_telemetry(enabled=bool(checked))
        except Exception:
            pass
