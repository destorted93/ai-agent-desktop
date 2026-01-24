import sys
import os
import sounddevice as sd
import wave
import io
import time
import threading
import json
import traceback
from PyQt6.QtWidgets import (QApplication, QWidget, QPushButton, QVBoxLayout, 
                              QHBoxLayout, QMenu, QTextEdit, QLineEdit, QScrollArea,
                              QLabel, QFrame, QSizePolicy, QLayout, QDialog, QMessageBox, QFileDialog, QTextBrowser, QSizePolicy)
from PyQt6.QtGui import QAction, QTextCursor, QFont, QTextOption, QKeyEvent, QPainter, QColor, QPen, QPixmap, QSyntaxHighlighter, QTextCharFormat, QTextDocument
from PyQt6.QtCore import Qt, QPoint, QEvent, pyqtSignal, QObject, QThread, pyqtSlot, QTimer, QRect, QSize

import markdown
import re
from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.lexers.agile import PythonLexer
from pygments.formatters import HtmlFormatter


class ScreenshotSelector(QWidget):
    """Overlay widget for selecting a screen area."""
    screenshot_selected = pyqtSignal(QPixmap)
    screenshot_cancelled = pyqtSignal()
    
    def __init__(self, screenshot):
        super().__init__()
        self.screenshot = screenshot
        self.dpr = max(1.0, float(screenshot.devicePixelRatio()))
        self.start_pos = None
        self.end_pos = None
        self.selecting = False
        
        # Set up fullscreen transparent overlay
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        
    def paintEvent(self, event):
        """Draw the screenshot with selection overlay."""
        painter = QPainter(self)
        
        # Draw the screenshot (Qt will scale by devicePixelRatio automatically)
        painter.drawPixmap(0, 0, self.screenshot)
        
        # Draw semi-transparent overlay
        overlay_color = QColor(0, 0, 0, 100)
        painter.fillRect(self.rect(), overlay_color)
        
        # If selecting, draw the selection rectangle
        if self.start_pos and self.end_pos:
            selection_rect = QRect(self.start_pos, self.end_pos).normalized()
            
            # Map to pixmap pixels for source rect (handles HiDPI correctly)
            src_rect = QRect(
                int(selection_rect.x() * self.dpr),
                int(selection_rect.y() * self.dpr),
                int(selection_rect.width() * self.dpr),
                int(selection_rect.height() * self.dpr),
            )
            
            # Clear the selection area (show original screenshot)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(selection_rect, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            
            # Draw the un-dimmed selection preview with accurate source rect
            painter.drawPixmap(selection_rect.topLeft(), self.screenshot, src_rect)
            
            # Draw selection border
            pen = QPen(QColor(0, 150, 255), 2, Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.drawRect(selection_rect)
            
            # Draw dimensions text
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(
                selection_rect.x(),
                selection_rect.y() - 5,
                f"{selection_rect.width()}x{selection_rect.height()}"
            )
    
    def mousePressEvent(self, event):
        """Start selection."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_pos = event.pos()
            self.end_pos = event.pos()
            self.selecting = True
            self.update()
    
    def mouseMoveEvent(self, event):
        """Update selection."""
        if self.selecting:
            self.end_pos = event.pos()
            self.update()
    
    def mouseReleaseEvent(self, event):
        """Finish selection."""
        if event.button() == Qt.MouseButton.LeftButton and self.selecting:
            self.selecting = False
            self.end_pos = event.pos()
            
            # Get the selected area
            selection_rect = QRect(self.start_pos, self.end_pos).normalized()
            
            # Must have some minimum size
            if selection_rect.width() > 10 and selection_rect.height() > 10:
                # Copy using source rect in pixmap pixel coords (HiDPI safe)
                src_rect = QRect(
                    int(selection_rect.x() * self.dpr),
                    int(selection_rect.y() * self.dpr),
                    int(selection_rect.width() * self.dpr),
                    int(selection_rect.height() * self.dpr),
                )
                selected_pixmap = self.screenshot.copy(src_rect)
                # Normalize DPR on the cropped pixmap so downstream uses logical pixels
                selected_pixmap.setDevicePixelRatio(1.0)
                self.screenshot_selected.emit(selected_pixmap)
                self.close()
            else:
                # Too small, cancel
                self.screenshot_cancelled.emit()
                self.close()
    
    def keyPressEvent(self, event):
        """Cancel selection on Escape."""
        if event.key() == Qt.Key.Key_Escape:
            self.screenshot_cancelled.emit()
            self.close()


class MultilineInput(QTextEdit):
    """Custom QTextEdit that sends message on Enter and adds newline on Shift+Enter."""
    send_message = pyqtSignal()
    paste_image = pyqtSignal(QPixmap)  # New signal for pasted images
    paste_files = pyqtSignal(list)     # New signal for pasted files
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setPlaceholderText("Type your message or drag & drop files...")
        
        # Enable word wrap
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # Set fixed comfortable height matching original QLineEdit (with padding from stylesheet)
        self.base_height = 40  # Standard single-line input height
        self.max_lines = 10
        
        font_metrics = self.fontMetrics()
        self.line_height = font_metrics.lineSpacing()
        self.max_height = self.base_height + (self.line_height * (self.max_lines - 1))
        
        self.setFixedHeight(self.base_height)
        
        # Connect to textChanged to adjust height dynamically
        self.textChanged.connect(self.adjust_height)
        
    def adjust_height(self):
        """Adjust height based on content, up to max_lines."""
        # Get document height and calculate needed height
        doc_height = int(self.document().size().height())
        
        # Add padding (16px total - 8px top + 8px bottom from stylesheet)
        new_height = doc_height + 16
        
        # Constrain between base and max height
        new_height = max(self.base_height, min(new_height, self.max_height))
        
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
            super().keyPressEvent(QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_V,
                Qt.KeyboardModifier.ControlModifier
            ))
    
    def clear_text(self):
        """Clear the text content."""
        self.clear()
        # Reset to base height
        self.setFixedHeight(self.base_height)


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


class ChatWindow(QWidget):
    """Separate chat window that maintains its state."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("AI Chat")
        self.resize(600, 700)
        # Restore last position if available
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        pos = settings.value("chat_window_pos", None)
        if pos is not None:
            try:
                x, y = map(int, str(pos).strip('()').split(','))
                self.move(x, y)
            except Exception:
                pass  # fallback to default position

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
        
        # Top toolbar with chat history dropdown, new chat button, token label, screenshot and clear buttons
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
        self.new_chat_button.setToolTip("Start New Chat")
        self.new_chat_button.setFixedSize(28, 28)
        self.new_chat_button.setStyleSheet("""
            QPushButton {
                background-color: #3d3d3d;
                color: #4da6ff !important;
                border: none;
                border-radius: 6px;
                font-size: 18px;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: #4da6ff;
                color: white !important;
            }
        """)
        toolbar_layout.addWidget(self.new_chat_button)

        # Chat history dropdown (dummy for now)
        from PyQt6.QtWidgets import QComboBox
        self.chat_history_dropdown = QComboBox()
        self.chat_history_dropdown.setFixedHeight(28)
        self.chat_history_dropdown.setStyleSheet("""
            QComboBox {
                background-color: #23272e;
                color: #d4d4d4;
                border: 1px solid #3d3d3d;
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 13px;
                min-width: 120px;
            }
            QComboBox QAbstractItemView {
                background-color: #23272e;
                color: #d4d4d4;
                selection-background-color: #4da6ff;
                selection-color: white;
            }
        """)
        # Dummy chat history entries
        self.chat_history_dropdown.addItems([
            "Chat 1",
            "Chat 2",
            "Chat 3"
            ])
        toolbar_layout.addWidget(self.chat_history_dropdown)

        # Left stretch
        toolbar_layout.addStretch(1)

        # Token usage label (centered)
        self.token_label = QLabel(f"Tokens - I: {self.input_tokens} | O: {self.output_tokens} | C: {self.cached_tokens} | R: {self.reasoning_tokens} | T: {self.total_tokens}")
        self.token_label.setStyleSheet("""
            QLabel {
                color: #ffcc00;
                font-size: 13px;
                background: transparent;
                font-weight: bold;
            }
        """)
        self.token_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        toolbar_layout.addWidget(self.token_label)

        # Right stretch
        toolbar_layout.addStretch(1)

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
                font-size: 18px;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
                color: #4da6ff !important;
            }
        """)
        toolbar_layout.addWidget(self.screenshot_button)

        # Clear chat button (right)
        self.clear_button = QPushButton("🗑️")
        self.clear_button.setToolTip("Clear Chat History")
        self.clear_button.setFixedSize(32, 32)
        self.clear_button.clicked.connect(self.request_clear_chat)
        self.clear_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #888888 !important;
                border: none;
                border-radius: 5px;
                font-size: 18px;
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

        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.chat_layout.setSpacing(10)
        
        self.scrollable_area.setWidget(self.chat_container)
        layout.addWidget(self.scrollable_area)
        
        # Attached files area (hidden by default)
        self.attached_files_widget = QWidget()
        self.attached_files_widget.hide()
        attached_files_main_layout = QHBoxLayout(self.attached_files_widget)
        attached_files_main_layout.setContentsMargins(5, 5, 5, 5)
        attached_files_main_layout.setSpacing(5)
        
        # Container for file chips - uses flow layout
        self.files_container = QWidget()
        self.files_container.setStyleSheet("""
            QWidget {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 5px;
            }
        """)
        
        # Use FlowLayout for wrapping
        self.files_layout = FlowLayout(self.files_container, margin=5, spacing=5)
        
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
                font-size: 10px;
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
                font-size: 10px;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: #ff6b6b;
                color: white !important;
            }
        """)
        
        screenshots_main_layout.addWidget(self.clear_screenshots_btn)
        layout.addWidget(self.screenshots_widget)
        
        # Input area
        input_layout = QHBoxLayout()
        self.input_field = MultilineInput()
        self.input_field.send_message.connect(self.send_message)
        self.input_field.paste_image.connect(self.handle_paste_image)
        self.input_field.paste_files.connect(self.handle_paste_files)
        
        self.send_button = QPushButton("➤")
        self.send_button.setFixedSize(40, 40)
        self.send_button.clicked.connect(self.handle_send_button_click)
        self.send_button.setStyleSheet("""
            QPushButton {
                background-color: #0e639c;
                color: white;
                border: none;
                border-radius: 20px;
                font-size: 18px;
            }
            QPushButton:hover {
                background-color: #1177bb;
            }
            QPushButton:disabled {
                background-color: #444444;
                color: #888888;
            }
        """)
        
        input_layout.addWidget(self.input_field)
        input_layout.addWidget(self.send_button, alignment=Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(input_layout)
        
        # Store chat history
        self.chat_history = []
        self.current_ai_widget = None
        
        self.parent_widget = parent
    
    def add_user_message(self, text):
        """Add user message to chat (right-aligned, max 80% width) with hover actions."""
        from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QPushButton, QWidget, QLabel
        msg_widget = QWidget()
        msg_widget.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        msg_layout = QHBoxLayout(msg_widget)
        msg_layout.setContentsMargins(0, 0, 0, 0)

        # Spacer for right alignment (20% of width)
        msg_layout.addStretch(1)

        # Message box (80% of width)
        msg_box = QWidget()
        msg_box_layout = QVBoxLayout(msg_box)
        msg_box_layout.setContentsMargins(0, 0, 0, 0)
        msg_box_layout.setSpacing(0)

        msg_label = QLabel(text)
        msg_label.setWordWrap(True)
        msg_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        msg_label.setStyleSheet("""
            QLabel {
                background-color: #0e639c;
                color: white;
                border-radius: 10px;
                padding: 10px;
                font-size: 13px;
            }
        """)
        msg_box_layout.addWidget(msg_label)

        # Actions row (hidden by default, shown on hover)
        actions_row = QWidget()
        actions_layout = QHBoxLayout(actions_row)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(6)


        from PyQt6.QtWidgets import QMessageBox
        def show_action_popup(action_name):
            # Find the index of this message widget in the chat layout
            idx = -1
            for i in range(self.chat_layout.count()):
                if self.chat_layout.itemAt(i).widget() is msg_widget:
                    idx = i
                    break
            QMessageBox.information(
                self,
                f"{action_name} Message",
                f"Action: {action_name}\nIndex: {idx}\nText: {text}"
            )

        style_sheet = """
            QPushButton {
                background-color: rgba(40, 40, 40, 120);
                border: none;
                border-radius: 11px;
                padding: 0px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #4da6ff;
            }
        """

        # Align actions to the right
        actions_layout.addStretch(1)

        # Use Unicode emoji for more intuitive icons
        # Copy: 📋, Edit: ✏️, Remove: 🗑️
        copy_btn = QPushButton("📋")
        copy_btn.setToolTip("Copy message")
        copy_btn.setFixedSize(22, 22)
        copy_btn.setStyleSheet(style_sheet)
        copy_btn.clicked.connect(lambda: show_action_popup("Copy message"))
        actions_layout.addWidget(copy_btn)

        edit_btn = QPushButton("✏️")
        edit_btn.setToolTip("Edit message")
        edit_btn.setFixedSize(22, 22)
        edit_btn.setStyleSheet(style_sheet)
        edit_btn.clicked.connect(lambda: show_action_popup("Edit message"))
        actions_layout.addWidget(edit_btn)

        remove_btn = QPushButton("🗑️")
        remove_btn.setToolTip("Remove message")
        remove_btn.setFixedSize(22, 22)
        remove_btn.setStyleSheet(style_sheet)
        remove_btn.clicked.connect(lambda: show_action_popup("Remove message"))
        actions_layout.addWidget(remove_btn)

        actions_row.hide()
        msg_box_layout.addWidget(actions_row)

        # Prevent shifting: actions row is always present but hidden, so layout height is stable
        msg_box.setStyleSheet("""
            QWidget {
                margin-bottom: 0px;
            }
        """)

        msg_layout.addWidget(msg_box, 4)

        # Hover event handling
        def eventFilter(obj, event):
            if event.type() == QEvent.Type.Enter:
                actions_row.show()
            elif event.type() == QEvent.Type.Leave:
                actions_row.hide()
            return False
        msg_box.installEventFilter(msg_box)
        msg_box.eventFilter = eventFilter

        self.chat_layout.addWidget(msg_widget)
        self.scroll_to_bottom()
    
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
                    font-size: 11px;
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
                    font-size: 11px;
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
            code_display = QTextBrowser()
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
                    font-size: 13px;
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


    def start_ai_response(self):
        """Start a new AI response section - initially just show markdown."""
        # Create a simple text browser for streaming content
        self.current_ai_widget = QTextBrowser()
        self.current_ai_widget.setReadOnly(True)
        self.current_ai_widget.setOpenExternalLinks(True)
        self.current_ai_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.current_ai_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.current_ai_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        
        font = QFont('Consolas', 10)
        self.current_ai_widget.setFont(font)
        
        self.current_ai_widget.setStyleSheet("""
            QTextBrowser {
                background-color: transparent;
                color: #d4d4d4;
                border: none;
                padding: 5px;
                font-size: 13px;
                font-family: 'Consolas', 'Courier New', monospace;
            }
        """)
        
        # Store raw markdown
        self.current_ai_widget.raw_markdown = ""
        
        # Context menu
        self.current_ai_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.current_ai_widget.customContextMenuRequested.connect(
            lambda pos: self.show_text_context_menu(pos, self.current_ai_widget)
        )
        
        # Auto-adjust height on content change
        self.current_ai_widget.document().contentsChanged.connect(
            lambda: self.adjust_simple_text_height(self.current_ai_widget)
        )
        
        self.chat_layout.addWidget(self.current_ai_widget)
        self.scroll_to_bottom()
        
        return self.current_ai_widget

    def adjust_simple_text_height(self, text_browser):
        """Adjust text browser height to fit content."""
        doc = text_browser.document()
        doc.setTextWidth(text_browser.viewport().width())
        height = doc.size().height()
        text_browser.setFixedHeight(int(height + 20))

    def append_to_ai_response(self, text, color=None):
        """Append text to the current AI response - just render as markdown, don't parse code blocks yet."""
        if self.current_ai_widget is None:
            self.start_ai_response()
        
        if not isinstance(text, str):
            text = str(text)
        
        # Append to stored markdown
        if color:
            color_map = {
                '33': '#ffcc00',
                '36': '#00bfff',
                '35': '#ff00ff',
                '34': '#1e90ff',
                '32': '#00ff00',
                '31': '#ff0000',
            }
            html_color = color_map.get(color, '#d4d4d4')
            colored_text = f'<span style="color: {html_color};">{text}</span>'
            self.current_ai_widget.raw_markdown += colored_text
        else:
            self.current_ai_widget.raw_markdown += text
        
        # Render as simple markdown (no code block extraction yet)
        html = markdown.markdown(
            self.current_ai_widget.raw_markdown,
            extensions=['nl2br', 'sane_lists', 'extra', 'fenced_code']
        )
        
        styled_html = f"""
        <style>
            body {{
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 13px;
                color: #d4d4d4;
                line-height: 1.5;
                margin: 0;
                padding: 0;
            }}
            p {{
                margin: 0 0 10px 0;
            }}
            code {{
                background-color: #2d2d2d;
                padding: 2px 6px;
                border-radius: 3px;
                font-family: 'Consolas', monospace;
            }}
            pre {{
                background-color: #2d2d2d;
                padding: 10px;
                border-radius: 5px;
                overflow-x: auto;
            }}
            pre code {{
                background-color: transparent;
                padding: 0;
            }}
            a {{
                color: #58a6ff;
            }}
        </style>
        {html}
        """
        
        self.current_ai_widget.setHtml(styled_html)
        self.scroll_to_bottom()

    def finish_ai_response(self):
        """Called when AI response is complete - now parse and replace with code block widgets."""
        if self.current_ai_widget is None:
            return
        
        # Get the raw markdown
        raw_markdown = getattr(self.current_ai_widget, 'raw_markdown', '')
        
        if not raw_markdown:
            self.current_ai_widget = None
            return
        
        # Check if there are any code blocks
        has_code_blocks = '```' in raw_markdown
        
        if not has_code_blocks:
            # No code blocks, just leave the simple markdown rendering
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
        text_browser = QTextBrowser()
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
                font-size: 13px;
                font-family: 'Consolas', 'Courier New', monospace;
            }
        """)
        
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
                font-size: 13px;
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
        
        # Adjust height to content
        doc = text_browser.document()
        doc.setTextWidth(text_browser.viewport().width())
        height = doc.size().height()
        text_browser.setFixedHeight(int(height + 20))
        
        return text_browser

    def show_text_context_menu(self, pos, text_browser):
        """Show context menu with copy options."""
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
    
    def scroll_to_bottom(self):
        """Scroll to the bottom of the chat."""
        QTimer.singleShot(10, self._do_scroll)
    
    def _do_scroll(self):
        """Actually perform the scroll."""
        scroll = self.findChild(QScrollArea)
        if scroll:
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    
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
    
    def stop_sending_state(self):
        """Stop the sending animation and return to normal state."""
        self.is_sending = False
        self.send_animation_timer.stop()
        self.send_button.setText("➤")
        self.send_button.setStyleSheet("""
            QPushButton {
                background-color: #0e639c;
                color: white;
                border: none;
                border-radius: 20px;
                font-size: 18px;
            }
            QPushButton:hover {
                background-color: #1177bb;
            }
            QPushButton:disabled {
                background-color: #444444;
                color: #888888;
            }
        """)
        self.input_field.setEnabled(True)
        self.screenshot_button.setEnabled(True)
        self.clear_button.setEnabled(True)
    
    def animate_sending(self):
        """Clean rotating spinner animation."""
        self.send_animation_step = (self.send_animation_step + 1) % 8
        spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧"]
        self.send_button.setText(spinner_chars[self.send_animation_step])
        self.send_button.setStyleSheet("""
            QPushButton {
                background-color: #c83232;
                color: white;
                border: none;
                border-radius: 20px;
                font-size: 18px;
            }
            QPushButton:hover {
                background-color: #d84444;
            }
        """)
    
    def stop_inference(self):
        """Stop the AI inference by notifying parent widget."""
        print("Stop inference requested")
        if self.parent_widget:
            self.parent_widget.stop_agent_inference()
        self.stop_sending_state()
    
    def request_clear_chat(self):
        """Request parent to clear chat with confirmation."""
        reply = QMessageBox.question(
            self, 'Clear Chat History',
            'Are you sure you want to clear all chat history?\n\nThis action cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes and self.parent_widget:
            self.parent_widget.clear_chat_all()
    
    def clear_chat(self):
        """Clear all chat messages from UI."""
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.current_ai_widget = None
        self.chat_history = []
    
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    
    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    
    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            for url in urls:
                if url.isLocalFile():
                    path = url.toLocalFile()
                    if path not in self.dropped_files:
                        self.dropped_files.append(path)
            self.update_attached_files_display()
            event.acceptProposedAction()
    
    def update_attached_files_display(self):
        """Update the display of attached files."""
        while self.files_layout.count():
            item = self.files_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        if self.dropped_files:
            for path in self.dropped_files:
                file_widget = QWidget()
                file_layout = QHBoxLayout(file_widget)
                file_layout.setContentsMargins(8, 2, 4, 2)
                file_layout.setSpacing(4)
                
                if os.path.isdir(path):
                    icon_text = "📁"
                    name = os.path.basename(path) + "/"
                else:
                    icon_text = "📄"
                    name = os.path.basename(path)
                
                file_label = QLabel(f"{icon_text} {name}")
                file_label.setStyleSheet("QLabel { color: #d4d4d4; font-size: 11px; background-color: transparent; }")
                file_label.setToolTip(path)
                
                remove_btn = QPushButton("✖")
                remove_btn.setFixedSize(14, 14)
                remove_btn.setToolTip(f"Remove {name}")
                remove_btn.clicked.connect(lambda checked, p=path: self.remove_file(p))
                remove_btn.setStyleSheet("QPushButton { background-color: transparent; color: #888888 !important; border: none; font-size: 9px; } QPushButton:hover { color: #ff6b6b !important; }")
                
                file_layout.addWidget(file_label)
                file_layout.addWidget(remove_btn)
                file_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                file_widget.adjustSize()
                file_widget.setStyleSheet("QWidget { background-color: #3d3d3d; border-radius: 10px; } QWidget:hover { background-color: #4d4d4d; }")
                self.files_layout.addWidget(file_widget)
            
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
                remove_btn.setStyleSheet("QPushButton { background-color: #ff6b6b; color: white !important; border: none; border-radius: 8px; font-size: 10px; } QPushButton:hover { background-color: #ff5555; }")
                
                thumb_layout.addWidget(thumb_label, alignment=Qt.AlignmentFlag.AlignCenter)
                thumb_layout.addWidget(remove_btn, alignment=Qt.AlignmentFlag.AlignCenter)
                thumb_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                thumb_widget.adjustSize()
                self.screenshots_layout.addWidget(thumb_widget)
            
            self.screenshots_widget.show()
        else:
            self.screenshots_widget.hide()
    
    def show_screenshot_fullsize(self, pixmap):
        dialog = QDialog(self)
        dialog.setWindowTitle("Screenshot Preview")
        dialog.setModal(False)
        dialog.resize(800, 600)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        label = QLabel()
        label.setPixmap(pixmap)
        label.setScaledContents(False)
        scroll.setWidget(label)
        layout.addWidget(scroll)
        dialog.show()
    
    def remove_screenshot(self, index):
        if 0 <= index < len(self.screenshots):
            self.screenshots.pop(index)
            self.update_screenshots_display()
    
    def clear_all_screenshots(self):
        self.screenshots.clear()
        self.update_screenshots_display()
    
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
        pos = settings.value("chat_window_pos", None)
        if pos is not None:
            try:
                x, y = map(int, str(pos).strip('()').split(','))
                self.move(x, y)
            except Exception:
                pass
        super().showEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            if self.windowState() & Qt.WindowState.WindowMinimized:
                QTimer.singleShot(0, self._hide_on_minimize)
        super().changeEvent(event)

    def _hide_on_minimize(self):
        # Mimic clicking X: hide without quitting the app
        self.setWindowState(Qt.WindowState.WindowNoState)
        self.hide()


class SettingsWindow(QDialog):
    """Settings window for API configuration."""
    settings_saved = pyqtSignal(dict)
    
    def __init__(self, parent=None, secure_storage=None):
        super().__init__(parent)
        self.secure_storage = secure_storage
        self.setWindowTitle("AI Agent Settings")
        self.setModal(False)
        self.resize(380, 260)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("Settings")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        desc = QLabel("Configure your provider and credentials. Tokens are stored securely using the OS keychain.")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        url_label = QLabel("Base URL")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://api.openai.com/v1")
        layout.addWidget(url_label)
        layout.addWidget(self.url_input)

        token_label = QLabel("API Token")
        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText("Enter token (leave empty to clear)")
        layout.addWidget(token_label)
        layout.addWidget(self.token_input)

        buttons = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.close_btn = QPushButton("Close")
        buttons.addStretch(1)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

        self._load_settings()
        self.save_btn.clicked.connect(self._on_save)
        self.close_btn.clicked.connect(self.close)
        layout.addStretch(1)
    
    def _load_settings(self):
        if self.secure_storage:
            existing_base_url = self.secure_storage.get_config_value("base_url", "")
            if existing_base_url:
                self.url_input.setText(existing_base_url)
            existing_token = self.secure_storage.get_secret("api_token")
            if existing_token:
                self.token_input.setText(existing_token)

    def _on_save(self):
        url = self.url_input.text().strip()
        token = self.token_input.text().strip()
        settings = {"base_url": url, "api_token": token}
        
        if self.secure_storage:
            self.secure_storage.set_config_value("base_url", url)
            if token:
                self.secure_storage.set_secret("api_token", token)
            else:
                self.secure_storage.delete_secret("api_token")
        
        self.settings_saved.emit(settings)
        QMessageBox.information(self, "Settings", "Settings saved successfully.")


class ChatHistoryJsonWindow(QDialog):
    """Debug window to display raw chat history JSON."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chat History (JSON)")
        self.setModal(False)
        self.resize(900, 700)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Top action bar with buttons
        actions = QHBoxLayout()
        actions.addStretch(1)
        
        # Search button
        self.search_btn = QPushButton("🔍 Search")
        self.search_btn.setToolTip("Search (Ctrl+F)")
        self.search_btn.clicked.connect(self.toggle_search_panel)
        self.search_btn.setStyleSheet("""
            QPushButton {
                background-color: #3a3a3a;
                color: #d4d4d4;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border-color: #666;
            }
        """)
        actions.addWidget(self.search_btn)
        
        self.save_as_btn = QPushButton("Save As…")
        self.save_as_btn.setStyleSheet("""
            QPushButton {
                background-color: #3a3a3a;
                color: #d4d4d4;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border-color: #666;
            }
        """)
        actions.addWidget(self.save_as_btn)
        
        self.close_btn = QPushButton("Close")
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: #3a3a3a;
                color: #d4d4d4;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border-color: #666;
            }
        """)
        actions.addWidget(self.close_btn)
        layout.addLayout(actions)

        # Search panel (hidden by default)
        self.search_panel = QWidget()
        self.search_panel.hide()
        search_layout = QHBoxLayout(self.search_panel)
        search_layout.setContentsMargins(5, 5, 5, 5)
        search_layout.setSpacing(5)
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find...")
        self.search_input.textChanged.connect(self.perform_search)
        self.search_input.returnPressed.connect(self.find_next)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: #2d2d2d;
                color: #d4d4d4;
                border: 1px solid #3d3d3d;
                border-radius: 3px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QLineEdit:focus {
                border-color: #007acc;
            }
        """)
        search_layout.addWidget(self.search_input)
        
        # Match counter
        self.match_label = QLabel("No matches")
        self.match_label.setStyleSheet("""
            QLabel {
                color: #888;
                font-size: 11px;
                padding: 0 5px;
            }
        """)
        search_layout.addWidget(self.match_label)
        
        # Previous button
        self.prev_btn = QPushButton("▲")
        self.prev_btn.setToolTip("Previous match (Shift+Enter)")
        self.prev_btn.setFixedSize(24, 24)
        self.prev_btn.clicked.connect(self.find_previous)
        self.prev_btn.setStyleSheet("""
            QPushButton {
                background-color: #3a3a3a;
                color: #d4d4d4;
                border: 1px solid #555;
                border-radius: 3px;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
            }
            QPushButton:disabled {
                color: #555;
                background-color: #2a2a2a;
            }
        """)
        search_layout.addWidget(self.prev_btn)
        
        # Next button
        self.next_btn = QPushButton("▼")
        self.next_btn.setToolTip("Next match (Enter)")
        self.next_btn.setFixedSize(24, 24)
        self.next_btn.clicked.connect(self.find_next)
        self.next_btn.setStyleSheet("""
            QPushButton {
                background-color: #3a3a3a;
                color: #d4d4d4;
                border: 1px solid #555;
                border-radius: 3px;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
            }
            QPushButton:disabled {
                color: #555;
                background-color: #2a2a2a;
            }
        """)
        search_layout.addWidget(self.next_btn)
        
        # Close search button
        self.close_search_btn = QPushButton("✕")
        self.close_search_btn.setToolTip("Close (Esc)")
        self.close_search_btn.setFixedSize(24, 24)
        self.close_search_btn.clicked.connect(self.close_search_panel)
        self.close_search_btn.setStyleSheet("""
            QPushButton {
                background-color: #3a3a3a;
                color: #d4d4d4;
                border: 1px solid #555;
                border-radius: 3px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
            }
        """)
        search_layout.addWidget(self.close_search_btn)
        
        self.search_panel.setStyleSheet("""
            QWidget {
                background-color: #252526;
                border-bottom: 1px solid #3d3d3d;
            }
        """)
        layout.addWidget(self.search_panel)

        # Text editor
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setAcceptRichText(False)
        self.text.setFont(QFont("Consolas", 10))
        layout.addWidget(self.text)

        # Search state
        self.search_matches = []
        self.current_match_index = -1
        self.search_highlight_format = QTextCharFormat()
        self.search_highlight_format.setBackground(QColor("#614d1e"))
        self.current_highlight_format = QTextCharFormat()
        self.current_highlight_format.setBackground(QColor("#007acc"))
        
        self._highlighter = JsonSyntaxHighlighter(self.text.document())
        self.save_as_btn.clicked.connect(self.save_as)
        self.close_btn.clicked.connect(self.close)
        
        # Install event filter for Ctrl+F and Esc shortcuts
        self.installEventFilter(self)
        self.search_input.installEventFilter(self)

    def eventFilter(self, obj, event):
        """Handle keyboard shortcuts for search."""
        if event.type() == QEvent.Type.KeyPress:
            # Ctrl+F to open search
            if event.key() == Qt.Key.Key_F and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                self.toggle_search_panel()
                return True
            
            # Esc to close search (when search input has focus)
            if obj == self.search_input and event.key() == Qt.Key.Key_Escape:
                self.close_search_panel()
                return True
            
            # Shift+Enter for previous match
            if obj == self.search_input and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                    self.find_previous()
                    return True
        
        return super().eventFilter(obj, event)
    
    def toggle_search_panel(self):
        """Toggle search panel visibility."""
        if self.search_panel.isVisible():
            self.close_search_panel()
        else:
            self.search_panel.show()
            self.search_input.setFocus()
            # Select all text in search input for easy replacement
            self.search_input.selectAll()
    
    def close_search_panel(self):
        """Close search panel and clear highlights."""
        self.search_panel.hide()
        self.clear_search_highlights()
        self.search_input.clear()
        self.text.setFocus()
    
    def perform_search(self):
        """Perform search and highlight all matches."""
        search_text = self.search_input.text()
        
        # Clear previous highlights
        self.clear_search_highlights()
        
        if not search_text:
            self.match_label.setText("No matches")
            self.search_matches = []
            self.current_match_index = -1
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            return
        
        # Get document and cursor
        document = self.text.document()
        cursor = QTextCursor(document)
        
        # Find all matches (case-insensitive)
        self.search_matches = []
        flags = QTextDocument.FindFlag(0)  # No flags = case-insensitive
        
        while True:
            cursor = document.find(search_text, cursor, flags)
            if cursor.isNull():
                break
            self.search_matches.append(cursor)
        
        # Update UI based on results
        if self.search_matches:
            # Highlight all matches
            for i, match_cursor in enumerate(self.search_matches):
                extra_selection = QTextEdit.ExtraSelection()
                extra_selection.cursor = match_cursor
                
                # First match gets special highlight
                if i == 0:
                    extra_selection.format = self.current_highlight_format
                else:
                    extra_selection.format = self.search_highlight_format
                
                # Store for later
                if i == 0:
                    self.text.setExtraSelections([extra_selection])
                else:
                    current_selections = self.text.extraSelections()
                    current_selections.append(extra_selection)
                    self.text.setExtraSelections(current_selections)
            
            # Set current match to first
            self.current_match_index = 0
            self.match_label.setText(f"{self.current_match_index + 1} of {len(self.search_matches)}")
            
            # Scroll to first match
            self.text.setTextCursor(self.search_matches[0])
            self.text.ensureCursorVisible()
            
            # Enable navigation buttons
            self.prev_btn.setEnabled(True)
            self.next_btn.setEnabled(True)
        else:
            self.match_label.setText("No matches")
            self.current_match_index = -1
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
    
    def find_next(self):
        """Navigate to next match."""
        if not self.search_matches:
            return
        
        # Move to next match (wrap around)
        self.current_match_index = (self.current_match_index + 1) % len(self.search_matches)
        self.highlight_current_match()
    
    def find_previous(self):
        """Navigate to previous match."""
        if not self.search_matches:
            return
        
        # Move to previous match (wrap around)
        self.current_match_index = (self.current_match_index - 1) % len(self.search_matches)
        self.highlight_current_match()
    
    def highlight_current_match(self):
        """Highlight the current match and scroll to it."""
        if not self.search_matches or self.current_match_index < 0:
            return
        
        # Update match label
        self.match_label.setText(f"{self.current_match_index + 1} of {len(self.search_matches)}")
        
        # Re-highlight all matches with current one special
        selections = []
        for i, match_cursor in enumerate(self.search_matches):
            extra_selection = QTextEdit.ExtraSelection()
            extra_selection.cursor = match_cursor
            
            if i == self.current_match_index:
                extra_selection.format = self.current_highlight_format
            else:
                extra_selection.format = self.search_highlight_format
            
            selections.append(extra_selection)
        
        self.text.setExtraSelections(selections)
        
        # Scroll to current match
        self.text.setTextCursor(self.search_matches[self.current_match_index])
        self.text.ensureCursorVisible()
    
    def clear_search_highlights(self):
        """Clear all search highlights."""
        self.text.setExtraSelections([])
        self.search_matches = []
        self.current_match_index = -1
    
    def set_json_text(self, text: str):
        self.text.setPlainText(text)

    def closeEvent(self, event):
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        settings.setValue("history_json_window_pos", (self.pos().x(), self.pos().y()))
        self.hide()
        event.ignore()

    def hideEvent(self, event):
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        settings.setValue("history_json_window_pos", (self.pos().x(), self.pos().y()))
        super().hideEvent(event)

    def showEvent(self, event):
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        pos = settings.value("history_json_window_pos", None)
        if pos is not None:
            try:
                x, y = map(int, str(pos).strip('()').split(','))
                self.move(x, y)
            except Exception:
                pass
        super().showEvent(event)

    def save_as(self):
        import datetime
        default_name = f"chat_history_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filters = "JSON Files (*.json);;Text Files (*.txt);;All Files (*.*)"
        file_path, selected_filter = QFileDialog.getSaveFileName(self, "Save Chat History", default_name, filters, "JSON Files (*.json)")
        if not file_path:
            return
        if selected_filter.startswith("JSON") and os.path.splitext(file_path)[1] == "":
            file_path += ".json"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self.text.toPlainText())
                if not self.text.toPlainText().endswith("\n"):
                    f.write("\n")
        except Exception as e:
            QMessageBox.warning(self, "Save Failed", f"Could not save file:\n{e}")


class JsonSyntaxHighlighter(QSyntaxHighlighter):
    """JSON syntax highlighter."""

    def __init__(self, document):
        super().__init__(document)
        self._key_format = QTextCharFormat()
        self._key_format.setForeground(QColor("#9cdcfe"))
        self._string_format = QTextCharFormat()
        self._string_format.setForeground(QColor("#ce9178"))
        self._number_format = QTextCharFormat()
        self._number_format.setForeground(QColor("#b5cea8"))
        self._bool_format = QTextCharFormat()
        self._bool_format.setForeground(QColor("#569cd6"))
        self._bool_format.setFontWeight(QFont.Weight.DemiBold)
        self._null_format = QTextCharFormat()
        self._null_format.setForeground(QColor("#c586c0"))
        self._null_format.setFontWeight(QFont.Weight.DemiBold)
        self._punct_format = QTextCharFormat()
        self._punct_format.setForeground(QColor("#d4d4d4"))
        self._re_key = re.compile(r'"([^"\\]|\\.)*"(?=\s*:)')
        self._re_string = re.compile(r'"([^"\\]|\\.)*"')
        self._re_number = re.compile(r'\b-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?\b')
        self._re_bool = re.compile(r'\b(?:true|false)\b')
        self._re_null = re.compile(r'\bnull\b')
        self._re_punct = re.compile(r'[\{\}\[\]:,]')

    def highlightBlock(self, text: str):
        for m in self._re_punct.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._punct_format)
        for m in self._re_number.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._number_format)
        for m in self._re_bool.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._bool_format)
        for m in self._re_null.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._null_format)
        for m in self._re_string.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._string_format)
        for m in self._re_key.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._key_format)


class FloatingWidget(QWidget):
    """Main floating widget - the entry point for the AI assistant."""
    
    history_loaded = pyqtSignal(list)
    history_json_loaded = pyqtSignal(str)
    agent_event_received = pyqtSignal(dict)
    transcription_received = pyqtSignal(str)
    

    def __init__(self, app=None):
        super().__init__()
        
        # Store reference to the app (the backbone/orchestrator)
        self.app = app

        # Recording state
        self.is_recording = False
        self.frames = []
        self.samplerate = 44100
        self.channels = 1
        self.filename = "recording.wav"
        self.selected_language = "en"

        # Long press state
        self.press_start_time = None
        self.long_press_threshold = 1000
        self.long_press_timer = QTimer()
        self.long_press_timer.setSingleShot(True)
        self.long_press_timer.timeout.connect(self.on_long_press)
        self.ready_to_record = False

        # Animation state
        self.recording_animation_timer = QTimer()
        self.recording_animation_timer.timeout.connect(self.animate_recording)
        self.animation_step = 0

        # Chat window
        self.chat_window = ChatWindow(self)
        self.chat_window.hide()
        self.history_json_window = ChatHistoryJsonWindow(self)
        self.settings_window = None

        # Agent inference tracking
        self.stop_requested = False
        self.agent_thread = None

        # Connect signals
        self.history_loaded.connect(self.display_chat_history)
        self.history_json_loaded.connect(self._display_history_json)
        self.agent_event_received.connect(self.handle_agent_event)
        self.transcription_received.connect(self.chat_window.send_message)

        # Transparent, always-on-top window
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        self.main_btn = QPushButton("🤖")
        self.main_btn.setFixedSize(56, 56)
        self.main_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(50, 50, 50, 200);
                color: white;
                border-radius: 28px;
                font-size: 28px;
                border: 2px solid rgba(255, 255, 255, 0.1);
            }
            QPushButton:hover {
                background-color: rgba(70, 70, 70, 220);
                border: 2px solid rgba(255, 255, 255, 0.2);
            }
        """)
        self.main_btn.installEventFilter(self)
        layout.addWidget(self.main_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # Dragging state
        self.drag_position = None
        self._drag_offset = None
        self._dragging = False
        self._press_global_pos = None

        # Restore position
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        pos = settings.value("window_pos", None)
        if pos is not None:
            try:
                x, y = map(int, str(pos).strip('()').split(','))
                self.move(x, y)
            except Exception:
                screen = QApplication.primaryScreen().availableGeometry()
                self.adjustSize()
                self.move(screen.width() - self.width() - 20, screen.height() - self.height() - 40)
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            self.adjustSize()
            self.move(screen.width() - self.width() - 20, screen.height() - self.height() - 40)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self.drag_position:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_position = None

    def eventFilter(self, obj, event):
        if obj == self.main_btn:
            is_chat_sending = self.chat_window and self.chat_window.is_sending
            
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._press_global_pos = event.globalPosition().toPoint()
                self._drag_offset = self._press_global_pos - self.frameGeometry().topLeft()
                self._dragging = False
                self.press_start_time = time.time()
                if not self.is_recording and not is_chat_sending:
                    self.long_press_timer.start(self.long_press_threshold)
                return False
            
            elif event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
                if not is_chat_sending:
                    self.show_menu()
                return True
            
            elif event.type() == QEvent.Type.MouseMove and (event.buttons() & Qt.MouseButton.LeftButton):
                if self._press_global_pos is not None:
                    current = event.globalPosition().toPoint()
                    if not self._dragging:
                        if (current - self._press_global_pos).manhattanLength() >= QApplication.startDragDistance():
                            self._dragging = True
                            self.long_press_timer.stop()
                            if self.ready_to_record:
                                self.ready_to_record = False
                                self.main_btn.setText("🤖")
                    if self._dragging and self._drag_offset is not None:
                        self.move(current - self._drag_offset)
                        return True
                return False
            
            elif event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self.long_press_timer.stop()
                was_dragging = self._dragging
                self._press_global_pos = None
                self._drag_offset = None
                self._dragging = False
                
                if not was_dragging:
                    if self.is_recording:
                        self.stop_recording()
                    elif self.ready_to_record:
                        self.ready_to_record = False
                        self.start_recording()
                    else:
                        if self.press_start_time and (time.time() - self.press_start_time) < (self.long_press_threshold / 1000.0):
                            self.toggle_chat_window()
                
                self.press_start_time = None
                return True if was_dragging else False

        return super().eventFilter(obj, event)

    def on_long_press(self):
        if not self.is_recording and not self._dragging:
            self.ready_to_record = True
            self.main_btn.setText("🎙️")
    
    def animate_recording(self):
        self.animation_step = (self.animation_step + 1) % 8
        spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧"]
        self.main_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(80, 80, 80, 200);
                color: #ff4444;
                border-radius: 28px;
                font-size: 28px;
                border: 2px solid rgba(255, 80, 80, 0.6);
            }
        """)
        self.main_btn.setText(spinner_chars[self.animation_step])
    
    def show_menu(self):
        menu = QMenu(self)
        langs = [("en", "English"), ("ro", "Romanian"), ("ru", "Russian"), ("de", "German"), ("fr", "French"), ("es", "Spanish")]
        lang_menu = QMenu("Language", self)
        self._lang_actions = {}
        for code, label in langs:
            act = QAction(f"{label} ({code})", self)
            act.setCheckable(True)
            act.setChecked(code == self.selected_language)
            act.triggered.connect(lambda checked, c=code: self._set_language(c))
            lang_menu.addAction(act)
            self._lang_actions[code] = act
        menu.addMenu(lang_menu)

        menu.addSeparator()
        settings_action = QAction("Settings…", self)
        settings_action.triggered.connect(self.open_settings)
        menu.addAction(settings_action)

        menu.addSeparator()
        open_history_action = QAction("Open Chat History", self)
        open_history_action.triggered.connect(self.open_chat_history)
        menu.addAction(open_history_action)

        menu.addSeparator()
        clear_chat_action = QAction("Clear Chat History", self)
        clear_chat_action.triggered.connect(self.clear_chat_all)
        menu.addAction(clear_chat_action)

        menu.addSeparator()
        restart_action = QAction("Restart App", self)
        restart_action.triggered.connect(self.restart_app)
        menu.addAction(restart_action)

        menu.addSeparator()
        close_action = QAction("Close", self)
        close_action.triggered.connect(self.quit_app)
        menu.addAction(close_action)

        menu.exec(self.main_btn.mapToGlobal(self.main_btn.rect().bottomLeft()))

    def restart_app(self):
        import subprocess
        WIDGET_LAUNCH_MODE = os.environ.get("WIDGET_LAUNCH_MODE", None)
        if WIDGET_LAUNCH_MODE:
            root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            bat_path = os.path.join(root_dir, f'{WIDGET_LAUNCH_MODE}.bat')
            subprocess.Popen(['cmd.exe', '/c', bat_path], cwd=root_dir, creationflags=subprocess.DETACHED_PROCESS)
            self.quit_app()
        else:
            QMessageBox.information(self, "Restart Not Available", "Restart is only available when launched via a .bat file.")

    def open_settings(self):
        if self.settings_window is None:
            secure_storage = self.app.secure_storage if self.app else None
            self.settings_window = SettingsWindow(self, secure_storage=secure_storage)
            self.settings_window.settings_saved.connect(self._on_settings_saved)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()
    
    def _on_settings_saved(self, settings):
        """Forward settings to app."""
        if self.app and settings.get("api_token"):
            self.app.update_api_key(settings["api_token"], settings.get("base_url"))

    def _set_language(self, code: str):
        allowed = {"en", "ro", "ru", "de", "fr", "es"}
        if code not in allowed:
            code = "en"
        self.selected_language = code
        if hasattr(self, "_lang_actions"):
            for c, act in self._lang_actions.items():
                act.setChecked(c == code)

    def toggle_chat_window(self):
        if self.chat_window is None:
            self.chat_window = ChatWindow(self)
        
        if self.chat_window.isVisible():
            self.chat_window.hide()
        else:
            self.position_chat_window()
            self.chat_window.show()
            self.chat_window.raise_()
            self.chat_window.activateWindow()
            self.fetch_and_display_chat_history()
    
    def position_chat_window(self):
        if not self.chat_window:
            return
        widget_rect = self.frameGeometry()
        chat_width = self.chat_window.width()
        chat_height = self.chat_window.height()
        screen = QApplication.primaryScreen().availableGeometry()
        chat_x = widget_rect.x() + (widget_rect.width() - chat_width) // 2
        chat_y = widget_rect.y() - chat_height - 10
        if chat_x < screen.x():
            chat_x = screen.x() + 10
        elif chat_x + chat_width > screen.x() + screen.width():
            chat_x = screen.x() + screen.width() - chat_width - 10
        if chat_y < screen.y():
            chat_y = screen.y() + 10
        self.chat_window.move(chat_x, chat_y)
    
    def fetch_and_display_chat_history(self):
        """Request chat history from app."""
        def _fetch():
            try:
                if self.app:
                    history = self.app.get_chat_history(chat_id="default")
                    self.history_loaded.emit(history)
            except Exception as e:
                print(f"Failed to fetch chat history: {e}")
        threading.Thread(target=_fetch, daemon=True).start()
    
    @pyqtSlot(list)
    def display_chat_history(self, history):
        if not self.chat_window:
            return
        print("Loading chat history...")
        self.chat_window.clear_chat()
        
        for entry in history:
            role = entry.get("role", "")
            content = entry.get("content", [])
            
            if role == "user":
                for item in content:
                    if item.get("type") == "input_text":
                        text = item.get("text", "")
                        if "User's input:" in text:
                            text = text.split("User's input:", 1)[1].strip()
                        self.chat_window.add_user_message(text)
            
            elif role == "assistant":
                for item in content:
                    if item.get("type") == "output_text":
                        text = item.get("text", "")
                        self.chat_window.start_ai_response()
                        self.chat_window.append_to_ai_response("Assistant:\n\n", '36')
                        self.chat_window.append_to_ai_response(text)
                        self.chat_window.finish_ai_response()
            
            elif entry.get("type") == "reasoning":
                summary = entry.get("summary", "")
                if summary:
                    if isinstance(summary, list):
                        summary_text = "\n\n".join(str(s.get("text", s)) for s in summary)
                    else:
                        summary_text = str(summary.get("text", summary))
                    if summary_text.strip():
                        self.chat_window.start_ai_response()
                        self.chat_window.append_to_ai_response("Thinking:\n\n", '33')
                        self.chat_window.append_to_ai_response(summary_text)
                        self.chat_window.finish_ai_response()
            
            elif entry.get("type") == "function_call":
                func_name = entry.get("name", "")
                func_args = entry.get("arguments", "")
                self.chat_window.start_ai_response()
                self.chat_window.append_to_ai_response(f"[Function Call] {func_name}\n", '35')
                if func_args:
                    self.chat_window.append_to_ai_response(f"Arguments: {func_args}\n\n")
                self.chat_window.finish_ai_response()
        
        if self.chat_window:
            QTimer.singleShot(100, self.chat_window.scroll_to_bottom)

    def open_chat_history(self):
        if self.history_json_window:
            self.history_json_window.show()
            self.history_json_window.raise_()
            self.history_json_window.activateWindow()
        self._fetch_history_json_async()

    def _fetch_history_json_async(self):
        """Request chat history JSON from app."""
        def _fetch():
            try:
                if self.app:
                    history = self.app.get_chat_history(chat_id="default")
                    json_text = json.dumps(history, indent=2, ensure_ascii=False)
                    self.history_json_loaded.emit(json_text)
            except Exception as e:
                print(f"Failed to fetch chat history: {e}")
        threading.Thread(target=_fetch, daemon=True).start()

    @pyqtSlot(str)
    def _display_history_json(self, json_text: str):
        if self.history_json_window:
            self.history_json_window.set_json_text(json_text)
    
    def clear_chat_all(self):
        """Request app to clear chat history."""
        reply = QMessageBox.question(self, 'Clear Chat History',
            'Are you sure you want to clear all chat history?\n\nThis action cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            if self.chat_window:
                self.chat_window.clear_chat()
            if self.history_json_window:
                self.history_json_window.set_json_text("[]")
            
            def _clear_storage():
                try:
                    if self.app:
                        success = self.app.clear_chat_history(chat_id="default")
                        if success:
                            print("Chat history cleared")
                            if self.history_json_window and self.history_json_window.isVisible():
                                self._fetch_history_json_async()
                except Exception as e:
                    print(f"Failed to clear chat history: {e}")
            threading.Thread(target=_clear_storage, daemon=True).start()
    
    def stop_agent_inference(self):
        """Request app to stop agent."""
        self.stop_requested = True
        if self.app:
            self.app.stop_agent()
        print("Stop inference requested")
    
    def send_to_agent(self, text, files_list=None, screenshots_data=None):
        """Send message to app which runs the agent."""
        if not self.chat_window:
            return
        
        display_text = text if text else f"[{len(screenshots_data) if screenshots_data else 0} Screenshot(s)]"
        self.chat_window.add_user_message(display_text)
        self.chat_window.start_sending_state()
        self.chat_window.start_ai_response()
        self.stop_requested = False
        
        def _run_agent():
            try:
                if not self.app:
                    self.agent_event_received.emit({
                        "type": "error", "agent_name": "System",
                        "content": {"message": "App not initialized."}
                    })
                    self.agent_event_received.emit({"type": "stream.finished", "agent_name": "System", "content": {}})
                    return
                
                print(f"[UI] Requesting app to run agent with message: {text[:50] if text else 'None'}...")
                event_count = 0
                
                # Call app.run_agent - the app handles everything including saving history
                for event in self.app.run_agent(
                    message=text,
                    files=files_list,
                    images=screenshots_data,
                    chat_id="default"
                ):
                    event_count += 1
                    event_type = event.get("type", "unknown")
                    print(f"[UI] Event #{event_count}: {event_type}")
                    
                    if self.stop_requested:
                        print("[UI] Stop requested, breaking event loop")
                        break
                    
                    self.agent_event_received.emit(event)
                
                print(f"[UI] Agent event loop finished. Total events: {event_count}")
                    
            except Exception as e:
                print(f"[UI] Error in agent thread: {e}")
                traceback.print_exc()
                self.agent_event_received.emit({
                    "type": "error", "agent_name": "System",
                    "content": {"message": f"Error: {str(e)}"}
                })
                self.agent_event_received.emit({"type": "stream.finished", "agent_name": "System", "content": {}})
            finally:
                self.stop_requested = False
        
        self.agent_thread = threading.Thread(target=_run_agent, daemon=True)
        self.agent_thread.start()
    
    @pyqtSlot(dict)
    def handle_agent_event(self, event):
        if not self.chat_window:
            return
        
        try:
            event_type = event.get("type", "")
            agent_name = event.get("agent_name", "Agent")
            content = event.get("content", {})
            
            print(f"[DEBUG] handle_agent_event: {event_type}")
            
            if event_type == "response.reasoning_summary_part.added":
                self.chat_window.append_to_ai_response(f"[{agent_name}] Thinking:\n\n", '33')
            elif event_type == "response.reasoning_summary_text.delta":
                self.chat_window.append_to_ai_response(content.get("delta", ""))
            elif event_type == "response.reasoning_summary_text.done":
                self.chat_window.append_to_ai_response("\n\n")
            elif event_type == "response.content_part.added":
                self.chat_window.append_to_ai_response(f"[{agent_name}] Assistant:\n\n", '36')
            elif event_type == "response.output_text.delta":
                self.chat_window.append_to_ai_response(content.get("delta", ""))
            elif event_type == "response.output_text.done":
                self.chat_window.append_to_ai_response("\n\n")
            elif event_type == "response.output_item.done":
                item = content.get("item", {})
                if isinstance(item, dict) and item.get("type") == "function_call":
                    func_name = item.get("name", "")
                    func_args = item.get("arguments", "")
                    self.chat_window.finish_ai_response()
                    self.chat_window.start_ai_response()
                    self.chat_window.append_to_ai_response(f"[{agent_name}] [Function Call] {func_name}\n", '35')
                    if func_args:
                        self.chat_window.append_to_ai_response(f"[{agent_name}] Arguments: {func_args}\n\n")
                    self.chat_window.finish_ai_response()
                    self.chat_window.start_ai_response()
            elif event_type == "response.image_generation_call.generating":
                self.chat_window.append_to_ai_response(f"\n[{agent_name}] [Image Generation]...\n", '34')
            elif event_type == "response.image_generation_call.completed":
                self.chat_window.append_to_ai_response(f"[{agent_name}] [Image Generation] Completed\n\n", '34')
            elif event_type == "response.agent.done":
                # App handles saving chat history and images - UI just updates display
                if content.get("stopped"):
                    self.chat_window.append_to_ai_response(f"\n[{agent_name}] [Stopped by user]\n\n", '31')
                    self.chat_window.finish_ai_response()
                    self.chat_window.stop_sending_state()
                # Refresh history JSON window if visible
                if self.history_json_window and self.history_json_window.isVisible():
                    self._fetch_history_json_async()
            elif event_type == "stream.finished":
                self.chat_window.finish_ai_response()
                self.chat_window.stop_sending_state()
            elif event_type == "error":
                error_msg = content.get("message", "Unknown error")
                self.chat_window.append_to_ai_response(f"\n[{agent_name}] [Error] {error_msg}\n\n", '31')
                self.chat_window.finish_ai_response()
                self.chat_window.stop_sending_state()
        except Exception as e:
            print(f"Error in handle_agent_event: {e}")
            traceback.print_exc()

    def quit_app(self):
        reply = QMessageBox.question(self, 'Close Application',
            'Are you sure you want to close the application?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                if hasattr(self, "stream") and self.stream is not None:
                    try:
                        self.stream.stop()
                    except Exception:
                        pass
            finally:
                if self.chat_window:
                    self.chat_window.close()
                app = QApplication.instance()
                if app is not None:
                    app.quit()

    def closeEvent(self, event):
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        settings.setValue("window_pos", (self.pos().x(), self.pos().y()))
        try:
            if hasattr(self, "stream") and self.stream is not None:
                try:
                    self.stream.stop()
                except Exception:
                    pass
            if self.chat_window:
                self.chat_window.close()
            event.accept()
        except Exception as e:
            print(f"Error during closeEvent: {e}")
            event.accept()

    def start_recording(self):
        self.is_recording = True
        self.frames = []

        def callback(indata, frames, time, status):
            if self.is_recording:
                self.frames.append(indata.copy().tobytes())

        if hasattr(self, "stream") and self.stream is not None:
            try:
                self.stream.stop()
            except Exception:
                pass
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        self.stream = sd.InputStream(samplerate=self.samplerate, channels=self.channels, dtype="int16", blocksize=512, latency="low", callback=callback)
        self.stream.start()
        self.animation_step = 0
        self.main_btn.setText("⠋")
        self.recording_animation_timer.start(100)

    def stop_recording(self):
        self.is_recording = False
        self.recording_animation_timer.stop()
        self.main_btn.setText("🤖")
        self.main_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(50, 50, 50, 200);
                color: white;
                border-radius: 28px;
                font-size: 28px;
                border: 2px solid rgba(255, 255, 255, 0.1);
            }
            QPushButton:hover {
                background-color: rgba(70, 70, 70, 220);
                border: 2px solid rgba(255, 255, 255, 0.2);
            }
        """)
        
        t0 = time.perf_counter()
        if hasattr(self, "stream") and self.stream is not None:
            try:
                self.stream.abort()
            except Exception:
                pass
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        t1 = time.perf_counter()

        def _transcribe():
            try:
                buf = io.BytesIO()
                with wave.open(buf, "wb") as wf:
                    wf.setnchannels(self.channels)
                    wf.setsampwidth(2)
                    wf.setframerate(self.samplerate)
                    wf.writeframes(b"".join(self.frames))
                buf.seek(0)
                t2 = time.perf_counter()

                if self.app:
                    result = self.app.transcribe(audio_data=buf.read(), language=self.selected_language)
                    t3 = time.perf_counter()
                    print("Transcribe response:", result, " timings(s): abort+close=", round(t1 - t0, 3), " build_wav=", round(t2 - t1, 3), " transcribe=", round(t3 - t2, 3))
                    
                    if result and result.get("text"):
                        transcribed_text = result["text"]
                        if transcribed_text:
                            self.transcription_received.emit(transcribed_text)
                else:
                    print("App not available for transcription")
            except Exception as e:
                print("Transcription failed:", e)
                traceback.print_exc()

        threading.Thread(target=_transcribe, daemon=True).start()
