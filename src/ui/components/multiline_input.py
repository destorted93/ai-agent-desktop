from typing import Optional
import math
import os

from PyQt6.QtWidgets import QApplication, QTextEdit
from PyQt6.QtGui import QKeyEvent, QPixmap, QImage
from PyQt6.QtCore import Qt, QEvent, pyqtSignal, QUrl


class MultilineInput(QTextEdit):
    """Custom QTextEdit that sends message on Enter and adds newline on Shift+Enter."""

    send_message = pyqtSignal()
    paste_image = pyqtSignal(QPixmap)  # New signal for pasted images
    paste_files = pyqtSignal(list)  # New signal for pasted files

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setPlaceholderText("Type your message or drag & drop files...")
        self.setAcceptDrops(True)

        # Enable word wrap
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Heights
        self.base_height = 40  # Standard single-line input height
        self.max_lines = 10

        font_metrics = self.fontMetrics()
        self.line_height = font_metrics.lineSpacing()

        # When set, this becomes the minimum height used by adjust_height()
        # (useful for "expand on focus" UX without fighting QTextEdit's dynamic sizing).
        self.min_height_override: Optional[int] = None

        self.setFixedHeight(self.base_height)

        # Connect to textChanged to adjust height dynamically
        self.textChanged.connect(self.adjust_height)

    def set_min_height_override(self, height: Optional[int]) -> None:
        """Override the minimum height used for auto-resizing.

        - None => normal compact behavior (min = base_height)
        - int  => expanded behavior (min = height)
        """
        self.min_height_override = int(height) if height else None
        self.adjust_height()

    def adjust_height(self):
        """Adjust height based on content, up to max_lines."""
        min_h = int(self.min_height_override) if self.min_height_override else int(self.base_height)
        max_h = int(min_h + (self.line_height * (self.max_lines - 1)))

        # Empty text should never inflate the input.
        txt = (self.toPlainText() or "")
        if not txt.strip():
            if min_h != self.height():
                self.setFixedHeight(min_h)
            return

        # Estimate document height using document layout size (not the widget's current height).
        try:
            w = max(1, int(self.viewport().width()))
            self.document().setTextWidth(w)
            doc_h = float(self.document().documentLayout().documentSize().height())
        except Exception:
            doc_h = float(self.document().size().height())

        # Add padding (16px total - 8px top + 8px bottom from stylesheet)
        new_height = int(math.ceil(doc_h) + 16)

        # Constrain between min and max height
        new_height = max(min_h, min(new_height, max_h))

        if new_height != self.height():
            self.setFixedHeight(new_height)

    def keyPressEvent(self, event: QKeyEvent):
        """Handle Enter and Shift+Enter differently, plus Ctrl+V for paste."""
        # Handle Ctrl+V for paste
        if event.key() == Qt.Key.Key_V and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.handle_paste()
            event.accept()
            return

        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                # Shift+Enter: insert newline
                super().keyPressEvent(event)
            else:
                # Plain Enter: send message
                self.send_message.emit()
                event.accept()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        """Disable Ctrl+wheel zoom to avoid Qt font warnings; let parent handle scrolling."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.ignore()
            return
        super().wheelEvent(event)

    def handle_paste(self):
        """Handle paste event - check for images or files in clipboard."""
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()

        # Check for image data first (highest priority)
        if mime_data.hasImage():
            image = clipboard.image()
            if not image.isNull():
                pixmap = QPixmap.fromImage(image)
                self.paste_image.emit(pixmap)
                return

        # Check for file URLs (from file explorer)
        if mime_data.hasUrls():
            urls = mime_data.urls()
            file_paths = []
            for url in urls:
                if url.isLocalFile():
                    path = url.toLocalFile()
                    file_paths.append(path)

            if file_paths:
                self.paste_files.emit(file_paths)
                return

        # If no image or files, handle as normal text paste
        if mime_data.hasText():
            super().keyPressEvent(
                QKeyEvent(
                    QEvent.Type.KeyPress,
                    Qt.Key.Key_V,
                    Qt.KeyboardModifier.ControlModifier,
                )
            )

    # ------------------------------
    # Drag & drop (files/images)
    # ------------------------------

    # NOTE (drag/drop): This widget intentionally implements its own drop parsing because
    # drops onto the *input* should attach files/images (emit paste_files/paste_image)
    # instead of inserting paths as text. Similar path-parsing logic exists in
    # ChatWindow.dropEvent so VS Code (text/plain paths) works when dropping onto the
    # chat window/history area too. If you change one, update the other.

    _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

    def _paths_from_text(self, text: str) -> list[str]:
        """Parse dropped plain-text into local filesystem paths.

        This is mainly for VS Code (and similar apps) which often drag-drop as text.
        """
        if not isinstance(text, str):
            return []

        out: list[str] = []
        for raw in (text or "").splitlines():
            s = (raw or "").strip()
            if not s:
                continue

            # Strip quotes (common when dragging into terminals/editors)
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

            # Only treat it as an attachment if it actually exists.
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
        return ext in self._IMAGE_EXTS

    def dragEnterEvent(self, event):
        try:
            md = event.mimeData()
            if md and (md.hasImage() or md.hasUrls()):
                event.acceptProposedAction()
                return
            if md and md.hasText():
                paths = self._paths_from_text(md.text())
                if paths:
                    event.acceptProposedAction()
                    return
        except Exception:
            pass

        # Fallback: allow normal text dragging behavior.
        return super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        try:
            md = event.mimeData()
            if md and (md.hasImage() or md.hasUrls()):
                event.acceptProposedAction()
                return
            if md and md.hasText():
                paths = self._paths_from_text(md.text())
                if paths:
                    event.acceptProposedAction()
                    return
        except Exception:
            pass

        return super().dragMoveEvent(event)

    def dropEvent(self, event):
        """Drop into the input should attach files/images (not paste their path as text)."""
        try:
            md = event.mimeData()
            if not md:
                return super().dropEvent(event)

            # 1) Raw image data (e.g., from browser)
            if md.hasImage():
                try:
                    img = md.imageData()
                    pm = None
                    if isinstance(img, QPixmap):
                        pm = img
                    elif isinstance(img, QImage):
                        pm = QPixmap.fromImage(img)
                    if pm is not None and not pm.isNull():
                        self.paste_image.emit(pm)
                        event.acceptProposedAction()
                        return
                except Exception:
                    pass

            # 2) File URLs (Explorer/Finder)
            file_paths: list[str] = []
            if md.hasUrls():
                try:
                    for url in md.urls() or []:
                        try:
                            if url.isLocalFile():
                                p = url.toLocalFile()
                                if p:
                                    file_paths.append(p)
                        except Exception:
                            continue
                except Exception:
                    pass

            # 3) Plain text paths (VS Code drag-drop)
            if (not file_paths) and md.hasText():
                file_paths = self._paths_from_text(md.text())

            if file_paths:
                # Split images vs other files/folders
                non_images: list[str] = []
                for p in file_paths:
                    if self._is_image_path(p) and os.path.isfile(p):
                        pm = QPixmap(p)
                        if not pm.isNull():
                            self.paste_image.emit(pm)
                            continue
                    non_images.append(p)

                if non_images:
                    self.paste_files.emit(non_images)

                event.acceptProposedAction()
                return

        except Exception:
            pass

        # Fallback: behave like a normal text editor.
        return super().dropEvent(event)

    def clear_text(self):
        """Clear the text content."""
        self.clear()
        self.adjust_height()
