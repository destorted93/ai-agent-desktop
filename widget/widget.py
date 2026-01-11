# Put all three controls in the same row
import sys
import os
import sounddevice as sd

import wave
import requests
import io
import time
import threading
import json
import asyncio
import websockets
import traceback
from PyQt6.QtWidgets import (QApplication, QWidget, QPushButton, QVBoxLayout, 
                              QHBoxLayout, QMenu, QTextEdit, QLineEdit, QScrollArea,
                              QLabel, QFrame, QSizePolicy, QLayout, QDialog, QMessageBox, QTextBrowser, QSizePolicy)
from PyQt6.QtGui import QAction, QTextCursor, QFont, QTextOption, QKeyEvent, QPainter, QColor, QPen, QPixmap
from PyQt6.QtCore import Qt, QPoint, QEvent, pyqtSignal, QObject, QThread, pyqtSlot, QTimer, QRect, QSize

import markdown
import re
from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.lexers.agile import PythonLexer
from pygments.formatters import HtmlFormatter

from agent_service import AgentService


class ScreenshotSelector(QWidget):
    """Overlay widget for selecting a screen area."""
    screenshot_selected = pyqtSignal(QPixmap)
    screenshot_cancelled = pyqtSignal()
    
    def __init__(self, screenshot):
        super().__init__()
        self.screenshot = screenshot
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
        
        # Draw the screenshot
        painter.drawPixmap(0, 0, self.screenshot)
        
        # Draw semi-transparent overlay
        overlay_color = QColor(0, 0, 0, 100)
        painter.fillRect(self.rect(), overlay_color)
        
        # If selecting, draw the selection rectangle
        if self.start_pos and self.end_pos:
            selection_rect = QRect(self.start_pos, self.end_pos).normalized()
            
            # Clear the selection area (show original screenshot)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(selection_rect, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            # Avoid implicit scaling by drawing the source rect at its top-left
            painter.drawPixmap(selection_rect.topLeft(), self.screenshot, selection_rect)
            
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
                selected_pixmap = self.screenshot.copy(selection_rect)
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
        """Handle Enter and Shift+Enter differently."""
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
        
        # Styling
        # self.setStyleSheet("""
        #     QWidget {
        #         background-color: #1e1e1e;
        #         color: #ffffff;
        #     }
        #     QTextEdit {
        #         background-color: #2d2d2d;
        #         color: #ffffff;
        #         border: 1px solid #3d3d3d;
        #         border-radius: 5px;
        #         padding: 8px;
        #         font-size: 13px;
        #     }
        #     QPushButton {
        #         background-color: #0e639c;
        #         color: white;
        #         border: none;
        #         border-radius: 5px;
        #         padding: 8px 16px;
        #         font-size: 13px;
        #     }
        #     QPushButton:hover {
        #         background-color: #1177bb;
        #     }
        #     QScrollArea {
        #         border: none;
        #     }
        # """)
        
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
            
            /* Paragraphs */
            p {{
                margin: 0 0 10px 0;
            }}
            
            /* Headings */
            h1, h2, h3, h4, h5, h6 {{
                color: #ffffff;
                margin-top: 16px;
                margin-bottom: 8px;
                font-weight: 600;
                line-height: 1.25;
            }}
            h1 {{ font-size: 2em; border-bottom: 1px solid #444; padding-bottom: 8px; }}
            h2 {{ font-size: 1.5em; border-bottom: 1px solid #444; padding-bottom: 6px; }}
            h3 {{ font-size: 1.25em; }}
            h4 {{ font-size: 1em; }}
            h5 {{ font-size: 0.875em; }}
            h6 {{ font-size: 0.85em; color: #999; }}
            
            /* Inline code */
            code {{
                background-color: #2d2d2d;
                padding: 2px 6px;
                border-radius: 3px;
                font-family: 'Consolas', monospace;
                font-size: 0.9em;
            }}
            
            /* Links */
            a {{
                color: #58a6ff;
                text-decoration: none;
            }}
            a:hover {{
                text-decoration: underline;
            }}
            
            /* Lists */
            ul, ol {{
                margin: 8px 0;
                padding-left: 24px;
            }}
            ul ul, ol ol, ul ol, ol ul {{
                margin: 4px 0;
            }}
            li {{
                margin: 4px 0;
            }}
            
            /* Blockquotes */
            blockquote {{
                border-left: 4px solid #58a6ff;
                background-color: #2d2d2d;
                margin: 12px 0;
                padding: 8px 16px;
                color: #c9d1d9;
                font-style: italic;
            }}
            blockquote p {{
                margin: 4px 0;
            }}
            
            /* Tables */
            table {{
                border-collapse: collapse;
                width: 100%;
                margin: 12px 0;
                background-color: #1e1e1e;
                border: 1px solid #3a3a3a;
                border-radius: 6px;
                overflow: hidden;
            }}
            
            thead {{
                background-color: #2a2a2a;
            }}
            
            th {{
                padding: 10px 12px;
                text-align: left;
                font-weight: 600;
                color: #ffffff;
                border-bottom: 2px solid #444;
                border-right: 1px solid #3a3a3a;
            }}
            
            th:last-child {{
                border-right: none;
            }}
            
            td {{
                padding: 8px 12px;
                border-bottom: 1px solid #2d2d2d;
                border-right: 1px solid #2d2d2d;
            }}
            
            td:last-child {{
                border-right: none;
            }}
            
            tr:last-child td {{
                border-bottom: none;
            }}
            
            tr:hover {{
                background-color: #252525;
            }}
            
            /* Horizontal rule */
            hr {{
                border: none;
                border-top: 1px solid #444;
                margin: 16px 0;
            }}
            
            /* Strong and emphasis */
            strong, b {{
                font-weight: 600;
                color: #ffffff;
            }}
            
            em, i {{
                font-style: italic;
                color: #c9d1d9;
            }}
            
            /* Strikethrough */
            del, s {{
                text-decoration: line-through;
                color: #888;
            }}
            
            /* Definition lists */
            dl {{
                margin: 12px 0;
            }}
            dt {{
                font-weight: 600;
                margin-top: 8px;
            }}
            dd {{
                margin-left: 24px;
                margin-bottom: 8px;
            }}
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
        # Use QTimer to ensure scroll happens after layout updates
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

        # If text is not provided, get it from input field, that means the user clicked send button
        if text is None:
            text = self.input_field.toPlainText().strip()

        files_list = self.dropped_files.copy()
        
        # Require either text or screenshots
        if (text or self.screenshots) and self.parent_widget:
            self.input_field.clear_text()
            self.clear_attached_files()
            # Pass text and list of screenshot data
            screenshot_data_list = [s["data"] for s in self.screenshots]
            self.parent_widget.send_to_agent(text, files_list, screenshot_data_list)
            # Clear screenshots after sending
            self.clear_all_screenshots()
            # Scroll with longer delay to ensure user message is fully rendered
            QTimer.singleShot(100, self._do_scroll)
    
    def start_sending_state(self):
        """Start the sending animation state and disable UI interactions."""
        self.is_sending = True
        self.send_animation_step = 0
        self.send_button.setText("⠋")  # Clean spinner
        self.send_animation_timer.start(100)  # Update every 100ms (same as recording)
        
        # Disable input and buttons
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
        
        # Re-enable input and buttons
        self.input_field.setEnabled(True)
        self.screenshot_button.setEnabled(True)
        self.clear_button.setEnabled(True)
    
    def animate_sending(self):
        """Clean rotating spinner animation - same as recording."""
        self.send_animation_step = (self.send_animation_step + 1) % 8
        
        # Standard Unicode spinner
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
        """Request parent to clear chat (both locally and on server) with confirmation."""
        from PyQt6.QtWidgets import QMessageBox
        
        reply = QMessageBox.question(
            self,
            'Clear Chat History',
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
        """Handle drag enter event."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    
    def dragMoveEvent(self, event):
        """Handle drag move event."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    
    def dropEvent(self, event):
        """Handle drop event - extract file/folder paths."""
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
        """Update the display of attached files with individual remove buttons."""
        # Clear existing file widgets
        while self.files_layout.count():
            item = self.files_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        if self.dropped_files:
            # Create a tag for each file
            for path in self.dropped_files:
                file_widget = QWidget()
                file_layout = QHBoxLayout(file_widget)
                file_layout.setContentsMargins(8, 2, 4, 2)
                file_layout.setSpacing(4)
                
                # Icon and filename
                if os.path.isdir(path):
                    icon_text = "📁"
                    name = os.path.basename(path) + "/"
                else:
                    icon_text = "�"
                    name = os.path.basename(path)
                
                file_label = QLabel(f"{icon_text} {name}")
                file_label.setStyleSheet("""
                    QLabel {
                        color: #d4d4d4;
                        font-size: 11px;
                        background-color: transparent;
                    }
                """)
                file_label.setToolTip(path)
                
                # Remove button for this specific file - small and close
                remove_btn = QPushButton("✖")
                remove_btn.setFixedSize(14, 14)
                remove_btn.setToolTip(f"Remove {name}")
                remove_btn.clicked.connect(lambda checked, p=path: self.remove_file(p))
                remove_btn.setStyleSheet("""
                    QPushButton {
                        background-color: transparent;
                        color: #888888 !important;
                        border: none;
                        font-size: 9px;
                        padding: 0px;
                    }
                    QPushButton:hover {
                        color: #ff6b6b !important;
                    }
                """)
                
                file_layout.addWidget(file_label)
                file_layout.addWidget(remove_btn)
                
                # Set fixed size policy to prevent stretching
                file_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                file_widget.adjustSize()
                
                # Style the file widget as a compact chip
                file_widget.setStyleSheet("""
                    QWidget {
                        background-color: #3d3d3d;
                        border-radius: 10px;
                    }
                    QWidget:hover {
                        background-color: #4d4d4d;
                    }
                """)
                
                # Add to flow layout
                self.files_layout.addWidget(file_widget)
            
            self.attached_files_widget.show()
        else:
            self.attached_files_widget.hide()
    
    def remove_file(self, file_path):
        """Remove a specific file from attached files."""
        if file_path in self.dropped_files:
            self.dropped_files.remove(file_path)
            self.update_attached_files_display()
    
    def clear_attached_files(self):
        """Clear all attached files."""
        self.dropped_files.clear()
        self.update_attached_files_display()
    
    def capture_screenshot(self):
        """Capture a screenshot of the entire screen - user can crop later."""
        # Check if max screenshots reached
        if len(self.screenshots) >= self.max_screenshots:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "Maximum Screenshots",
                f"You can attach a maximum of {self.max_screenshots} screenshots per message."
            )
            return
        
        try:
            import base64
            from io import BytesIO
            
            # Hide the chat window AND parent widget temporarily
            self.hide()
            if self.parent_widget:
                self.parent_widget.hide()
            QTimer.singleShot(300, self._perform_screenshot)
            
        except Exception as e:
            print(f"Screenshot error: {e}")
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "Screenshot Error",
                f"Failed to capture screenshot: {str(e)}"
            )
    
    def _perform_screenshot(self):
        """Actually perform the screenshot after window is hidden."""
        try:
            # Capture the entire screen using Qt
            screen = QApplication.primaryScreen()
            full_screenshot = screen.grabWindow(0)
            
            # Show selection overlay
            from PyQt6.QtCore import QRect
            self.selection_overlay = ScreenshotSelector(full_screenshot)
            self.selection_overlay.screenshot_selected.connect(self._handle_screenshot_selection)
            self.selection_overlay.screenshot_cancelled.connect(self._handle_screenshot_cancelled)
            self.selection_overlay.showFullScreen()
                
        except Exception as e:
            self.show()
            print(f"Screenshot error: {e}")
            import traceback
            traceback.print_exc()
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "Screenshot Error",
                f"Failed to capture screenshot: {str(e)}"
            )
    
    def _handle_screenshot_selection(self, selected_pixmap):
        """Handle the selected screenshot area."""
        try:
            import base64
            from PyQt6.QtCore import QBuffer, QIODevice
            
            # Show windows again
            if self.parent_widget:
                self.parent_widget.show()
            self.show()
            self.raise_()
            self.activateWindow()
            
            if selected_pixmap:
                # Convert QPixmap to base64 using QBuffer
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                selected_pixmap.save(buffer, "PNG")
                buffer.close()
                
                screenshot_data = base64.b64encode(buffer.data()).decode('utf-8')
                
                # Add to screenshots list
                self.screenshots.append({
                    "data": screenshot_data,
                    "pixmap": selected_pixmap
                })
                
                # Update display
                self.update_screenshots_display()
                
        except Exception as e:
            print(f"Screenshot processing error: {e}")
            import traceback
            traceback.print_exc()
    
    def _handle_screenshot_cancelled(self):
        """Handle screenshot cancellation."""
        if self.parent_widget:
            self.parent_widget.show()
        self.show()
        self.raise_()
        self.activateWindow()
    
    def update_screenshots_display(self):
        """Update the display of screenshot thumbnails."""
        # Clear existing thumbnails
        while self.screenshots_layout.count():
            item = self.screenshots_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        if self.screenshots:
            # Create thumbnail for each screenshot
            for idx, screenshot in enumerate(self.screenshots):
                thumb_widget = QWidget()
                thumb_layout = QVBoxLayout(thumb_widget)
                thumb_layout.setContentsMargins(2, 2, 2, 2)
                thumb_layout.setSpacing(2)
                
                # Thumbnail image (clickable)
                thumb_label = QLabel()
                thumb_pixmap = screenshot["pixmap"].scaled(
                    80, 60,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                thumb_label.setPixmap(thumb_pixmap)
                thumb_label.setStyleSheet("""
                    QLabel {
                        background-color: #2d2d2d;
                        border: 2px solid #4da6ff;
                        border-radius: 3px;
                        padding: 2px;
                    }
                    QLabel:hover {
                        border: 2px solid #66b3ff;
                    }
                """)
                thumb_label.setCursor(Qt.CursorShape.PointingHandCursor)
                thumb_label.mousePressEvent = lambda event, p=screenshot["pixmap"]: self.show_screenshot_fullsize(p)
                
                # Remove button
                remove_btn = QPushButton("✖")
                remove_btn.setFixedSize(16, 16)
                remove_btn.setToolTip(f"Remove screenshot {idx + 1}")
                remove_btn.clicked.connect(lambda checked, i=idx: self.remove_screenshot(i))
                remove_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #ff6b6b;
                        color: white !important;
                        border: none;
                        border-radius: 8px;
                        font-size: 10px;
                        padding: 0px;
                    }
                    QPushButton:hover {
                        background-color: #ff5555;
                    }
                """)
                
                thumb_layout.addWidget(thumb_label, alignment=Qt.AlignmentFlag.AlignCenter)
                thumb_layout.addWidget(remove_btn, alignment=Qt.AlignmentFlag.AlignCenter)
                
                thumb_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                thumb_widget.adjustSize()
                
                self.screenshots_layout.addWidget(thumb_widget)
            
            self.screenshots_widget.show()
        else:
            self.screenshots_widget.hide()
    
    def show_screenshot_fullsize(self, pixmap):
        """Show screenshot in a separate window at full size."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QScrollArea
        
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
        """Remove a specific screenshot."""
        if 0 <= index < len(self.screenshots):
            self.screenshots.pop(index)
            self.update_screenshots_display()
    
    def clear_all_screenshots(self):
        """Clear all screenshots."""
        self.screenshots.clear()
        self.update_screenshots_display()
    
    def closeEvent(self, event):
        """Override close to just hide the window and save position."""
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        pos = self.pos()
        settings.setValue("chat_window_pos", (pos.x(), pos.y()))
        self.hide()
        event.ignore()

    def hideEvent(self, event):
        """Save position when hidden."""
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        pos = self.pos()
        settings.setValue("chat_window_pos", (pos.x(), pos.y()))
        super().hideEvent(event)

    def showEvent(self, event):
        """Restore position when shown, if available."""
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


class SettingsWindow(QDialog):
    """Lightweight settings window to be filled later."""
    def __init__(self, parent=None, agent_url=None, agent_service=None):
        super().__init__(parent)
        self.agent_url = agent_url or "http://127.0.0.1:6002"
        self.agent_service = agent_service

        self.setWindowTitle("AI Agent Settings")
        self.setModal(False)
        # Keep it small and simple; can be resized later
        self.resize(380, 260)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)

        # Use shared secure storage package
        from secure_storage import load_config, save_config, get_secret, set_secret, delete_secret

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("Settings (coming soon)")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        desc = QLabel("Configure your provider and credentials. Tokens are stored securely using the OS keychain.")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Base URL
        url_label = QLabel("Base URL")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://api.example.com")
        layout.addWidget(url_label)
        layout.addWidget(self.url_input)

        # API token (visible for now per request)
        token_label = QLabel("API Token")
        self.token_input = QLineEdit()
        self.token_input.setPlaceholderText("Enter token (leave empty to clear)")
        layout.addWidget(token_label)
        layout.addWidget(self.token_input)

        # Buttons
        buttons = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.close_btn = QPushButton("Close")
        buttons.addStretch(1)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

        # Load base URL and token if existing
        cfg = load_config()
        existing_base_url = cfg.get("base_url", "")
        if existing_base_url:
            # Set existing URL
            self.url_input.setText(existing_base_url)
        existing_token = get_secret("api_token")
        if existing_token:
            # Show the token plainly for now (testing/dev UX)
            self.token_input.setText(existing_token)

        def on_save():
            url = self.url_input.text().strip()
            token = self.token_input.text().strip()

            self.url_input.setText(url)
            self.token_input.setText(token)

            settings = {
                "base_url": url,
                "api_token": token,
            }

            # Notify server that the settings have changed
            if self.agent_service:
                response = self.agent_service.update_settings(settings)
                if response:
                    QMessageBox.information(self, "Settings", "Settings updated on server.")
                else:
                    QMessageBox.warning(self, "Settings", "Failed to update settings on server.")

        self.save_btn.clicked.connect(on_save)
        self.close_btn.clicked.connect(self.close)

        layout.addStretch(1)
        

class Gadget(QWidget):
    # Signals for thread-safe UI updates
    history_loaded = pyqtSignal(list)
    agent_event_received = pyqtSignal(dict)
    transcription_received = pyqtSignal(str)
    

    def __init__(self):
        super().__init__()

        # Recording state
        self.is_recording = False
        self.frames = []
        self.samplerate = 44100
        self.channels = 1
        self.filename = "recording.wav"
        # Language selection (ISO-639-1); default 'en'
        self.selected_language = "en"

        # Long press state
        self.press_start_time = None
        self.long_press_threshold = 1000  # 1 second in milliseconds
        self.long_press_timer = QTimer()
        self.long_press_timer.setSingleShot(True)
        self.long_press_timer.timeout.connect(self.on_long_press)
        self.ready_to_record = False  # Indicates button held long enough, waiting for release

        # Animation state
        self.recording_animation_timer = QTimer()
        self.recording_animation_timer.timeout.connect(self.animate_recording)
        self.animation_step = 0

        # Init AgentService
        self.agent_service = AgentService()

        # Chat window
        self.chat_window = ChatWindow(self)
        self.chat_window.hide()
        # Settings window (lazy created)
        self.settings_window = None
        self.agent_url = os.environ.get("AGENT_URL", "http://127.0.0.1:6002")

        # WebSocket tracking for cancellation
        self.current_websocket = None
        self.stop_requested = False

        # Connect signals to slots for thread-safe UI updates
        self.history_loaded.connect(self.display_chat_history)
        self.agent_event_received.connect(self.handle_agent_event)
        self.transcription_received.connect(self.chat_window.send_message)

        # Transparent, always-on-top window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # --- Layout ---
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # Single unified button
        self.main_btn = QPushButton("🤖")
        self.main_btn.setFixedSize(56, 56)

        button_style = """
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
        """
        self.main_btn.setStyleSheet(button_style)

        # Install event filter for custom mouse handling
        self.main_btn.installEventFilter(self)

        layout.addWidget(self.main_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # Dragging state
        self.drag_position = None
        self._drag_offset = None
        self._dragging = False
        self._press_global_pos = None

        # Restore last position if available
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        pos = settings.value("window_pos", None)
        if pos is not None:
            try:
                x, y = map(int, str(pos).strip('()').split(','))
                self.move(x, y)
            except Exception:
                # fallback to default
                screen = QApplication.primaryScreen().availableGeometry()
                self.adjustSize()
                self.move(
                    screen.width() - self.width() - 20,
                    screen.height() - self.height() - 40,
                )
        else:
            # Default position (bottom right)
            screen = QApplication.primaryScreen().availableGeometry()
            self.adjustSize()
            self.move(
                screen.width() - self.width() - 20,
                screen.height() - self.height() - 40,
            )

    # --- Dragging events ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self.drag_position:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_position = None

    def eventFilter(self, obj, event):
        # Handle main button events
        if obj == self.main_btn:
            # Check if chat window is sending - block most interactions
            is_chat_sending = self.chat_window and self.chat_window.is_sending
            
            # Left button press - start timer for long press
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._press_global_pos = event.globalPosition().toPoint()
                self._drag_offset = self._press_global_pos - self.frameGeometry().topLeft()
                self._dragging = False
                self.press_start_time = time.time()
                
                # Start long press timer only if not recording and not sending
                if not self.is_recording and not is_chat_sending:
                    self.long_press_timer.start(self.long_press_threshold)
                
                return False
            
            # Right button press - show menu (blocked during sending)
            elif event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
                if not is_chat_sending:
                    self.show_menu()
                return True
            
            # Mouse move - handle dragging
            elif event.type() == QEvent.Type.MouseMove and (event.buttons() & Qt.MouseButton.LeftButton):
                if self._press_global_pos is not None:
                    current = event.globalPosition().toPoint()
                    if not self._dragging:
                        if (current - self._press_global_pos).manhattanLength() >= QApplication.startDragDistance():
                            self._dragging = True
                            # Cancel long press if we start dragging
                            self.long_press_timer.stop()
                            # Reset ready to record state
                            if self.ready_to_record:
                                self.ready_to_record = False
                                self.main_btn.setText("🤖")
                    
                    if self._dragging and self._drag_offset is not None:
                        self.move(current - self._drag_offset)
                        return True
                return False
            
            # Left button release - handle click, start recording, or stop recording
            elif event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self.long_press_timer.stop()
                was_dragging = self._dragging
                self._press_global_pos = None
                self._drag_offset = None
                self._dragging = False
                
                if not was_dragging:
                    if self.is_recording:
                        # Stop recording
                        self.stop_recording()
                    elif self.ready_to_record:
                        # User held long enough and now released - start recording
                        self.ready_to_record = False
                        self.start_recording()
                    else:
                        # Short click - toggle chat if it wasn't held long enough
                        if self.press_start_time and (time.time() - self.press_start_time) < (self.long_press_threshold / 1000.0):
                            self.toggle_chat_window()
                
                self.press_start_time = None
                return True if was_dragging else False

        return super().eventFilter(obj, event)

    def on_long_press(self):
        """Called when button is held for long press threshold - show ready to record indicator."""
        if not self.is_recording and not self._dragging:
            self.ready_to_record = True
            # Change icon to indicate ready to record (but don't start yet)
            self.main_btn.setText("🎙️")
    
    def animate_recording(self):
        """Clean rotating spinner animation."""
        self.animation_step = (self.animation_step + 1) % 8
        
        # Standard Unicode spinner
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
        from PyQt6.QtWidgets import QMessageBox
        menu = QMenu(self)

        # Language block
        langs = [
            ("en", "English"),
            ("ro", "Romanian"),
            ("ru", "Russian"),
            ("de", "German"),
            ("fr", "French"),
            ("es", "Spanish"),
        ]

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
        clear_chat_action = QAction("Clear Chat History", self)
        clear_chat_action.triggered.connect(self.clear_chat_all)
        menu.addAction(clear_chat_action)

        menu.addSeparator()
        close_action = QAction("Close", self)
        close_action.triggered.connect(self.quit_app)
        menu.addAction(close_action)

        menu.addSeparator()
        restart_action = QAction("Restart App", self)
        restart_action.triggered.connect(self.restart_app)
        menu.addAction(restart_action)

        # Show menu below the main button
        menu.exec(self.main_btn.mapToGlobal(self.main_btn.rect().bottomLeft()))

    def restart_app(self):
        """Restart the entire application by launching the .bat file again."""
        import subprocess
        import sys
        import os

        WIDGET_LAUNCH_MODE = os.environ.get("WIDGET_LAUNCH_MODE", None)

        if WIDGET_LAUNCH_MODE:
            root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            bat_path = os.path.join(root_dir, f'{WIDGET_LAUNCH_MODE}.bat')
            bat_path = os.path.abspath(bat_path)
            # Launch main .bat detached
            subprocess.Popen(
                ['cmd.exe', '/c', bat_path],
                cwd=root_dir,
                creationflags=subprocess.DETACHED_PROCESS)
            # Exit current app
            self.quit_app()
        else:
            # pop up message box that restart is not available
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                "Restart Not Available",
                "Restart is only available when launched via a .bat file."
            )

    def open_settings(self):
        """Open the small settings window (non-modal)."""
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self, agent_url=self.agent_url, agent_service=self.agent_service)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def _set_language(self, code: str):
        allowed = {"en", "ro", "ru", "de", "fr", "es"}
        if code not in allowed:
            code = "en"
        self.selected_language = code
        # Update checks if actions exist
        if hasattr(self, "_lang_actions"):
            for c, act in self._lang_actions.items():
                act.setChecked(c == code)

    def toggle_chat_window(self):
        """Open or close the chat window."""
        if self.chat_window is None:
            self.chat_window = ChatWindow(self)
        
        if self.chat_window.isVisible():
            self.chat_window.hide()
        else:
            # Position chat window above the widget
            self.position_chat_window()
            self.chat_window.show()
            self.chat_window.raise_()
            self.chat_window.activateWindow()
            # Fetch chat history when opening
            self.fetch_and_display_chat_history()
    
    def position_chat_window(self):
        """Position chat window centered above the widget."""
        if not self.chat_window:
            return
        
        # Get widget geometry
        widget_rect = self.frameGeometry()
        widget_x = widget_rect.x()
        widget_y = widget_rect.y()
        widget_width = widget_rect.width()
        
        # Get chat window size
        chat_width = self.chat_window.width()
        chat_height = self.chat_window.height()
        
        # Get screen geometry
        screen = QApplication.primaryScreen().availableGeometry()
        
        # Calculate position: centered horizontally with widget, above it
        chat_x = widget_x + (widget_width - chat_width) // 2
        chat_y = widget_y - chat_height - 10  # 10px gap above widget
        
        # Ensure chat window stays within screen bounds
        if chat_x < screen.x():
            chat_x = screen.x() + 10
        elif chat_x + chat_width > screen.x() + screen.width():
            chat_x = screen.x() + screen.width() - chat_width - 10
        
        if chat_y < screen.y():
            chat_y = screen.y() + 10
        
        self.chat_window.move(chat_x, chat_y)
    
    def fetch_and_display_chat_history(self):
        """Fetch chat history from agent service and display it."""
        def _fetch():
            try:
                history = self.agent_service.get_history(chat_id="default")
                # Emit signal to display on main thread
                self.history_loaded.emit(history)
            except Exception as e:
                print(f"Failed to fetch chat history: {e}")
        threading.Thread(target=_fetch, daemon=True).start()
    
    @pyqtSlot(list)
    def display_chat_history(self, history):
        """Display chat history in the chat window."""
        if not self.chat_window:
            return
        
        print("Loading chat history...")
        
        self.chat_window.clear_chat()
        
        for entry in history:
            role = entry.get("role", "")
            content = entry.get("content", [])
            
            if role == "user":
                # Display user message
                for item in content:
                    if item.get("type") == "input_text":
                        text = item.get("text", "")
                        # Extract actual user input (remove timestamp prefix if present)
                        if "User's input:" in text:
                            text = text.split("User's input:", 1)[1].strip()
                        self.chat_window.add_user_message(text)
            
            elif role == "assistant":
                # Display assistant response - use the SAME approach as streaming
                for item in content:
                    if item.get("type") == "output_text":
                        text = item.get("text", "")
                        # Create fresh widget for each text block (like streaming does)
                        self.chat_window.start_ai_response()
                        # Append the text exactly as streaming does
                        self.chat_window.append_to_ai_response("Assistant:\n\n", '36')
                        self.chat_window.append_to_ai_response(text)
                        self.chat_window.finish_ai_response()
            
            elif entry.get("type") == "reasoning":
                # Display reasoning only if it has actual content
                summary = entry.get("summary", "")
                if summary:  # Only display if summary exists and is not empty
                    # Handle summary as string or list
                    if isinstance(summary, list):
                        summary_text = "\n\n".join(str(s.get("text", s)) for s in summary)
                    else:
                        summary_text = str(summary.get("text", summary))
                    
                    # Only display if there's actual text after conversion
                    if summary_text.strip():
                        # Create widget same as streaming
                        self.chat_window.start_ai_response()
                        self.chat_window.append_to_ai_response("Thinking:\n\n", '33')
                        self.chat_window.append_to_ai_response(summary_text)
                        self.chat_window.finish_ai_response()
            
            elif entry.get("type") == "function_call":
                # Display function call same as streaming
                func_name = entry.get("name", "")
                func_args = entry.get("arguments", "")
                self.chat_window.start_ai_response()
                self.chat_window.append_to_ai_response(
                    f"[Function Call] {func_name}\n",
                    '35'
                )
                if func_args:
                    self.chat_window.append_to_ai_response(f"Arguments: {func_args}\n\n")
                self.chat_window.finish_ai_response()
        
        # Force layout update after loading all history
        if self.chat_window:
            # Use delay for scrolling to ensure all layouts complete
            QTimer.singleShot(100, self.chat_window.scroll_to_bottom)
    
    def _adjust_all_widget_heights(self):
        """Adjust heights of all AI response widgets in chat."""
        if not self.chat_window:
            return
        
        # Force all QTextEdit widgets to recalculate their heights
        for i in range(self.chat_window.chat_layout.count()):
            widget = self.chat_window.chat_layout.itemAt(i).widget()
            if isinstance(widget, QTextEdit):
                self.chat_window.adjust_widget_height(widget)
        
        # Force container update
        self.chat_window.chat_container.updateGeometry()
        self.chat_window.update()
    
    def clear_chat_all(self):
        """Clear chat history both locally and on the server."""

        reply = QMessageBox.question(
                self,
                'Clear Chat History',
                'Are you sure you want to clear all chat history?\n\nThis action cannot be undone.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
        if reply == QMessageBox.StandardButton.Yes:
            # Clear local UI
            if self.chat_window:
                self.chat_window.clear_chat()
            
            # Send request to server to clear history
            def _clear_on_server():
                try:
                    success = self.agent_service.clear_history(chat_id="default")
                    if success:
                        print("Chat history cleared on server")
                    else:
                        print(f"Failed to clear chat history on server")
                except Exception as e:
                    print(f"Failed to clear chat history on server: {e}")
            threading.Thread(target=_clear_on_server, daemon=True).start()
    
    def stop_agent_inference(self):
        """Stop the current agent inference."""
        self.stop_requested = True
        if self.current_websocket:
            # Send stop message through websocket
            def _send_stop():
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self._send_stop_signal())
                    loop.close()
                except Exception as e:
                    print(f"Failed to send stop signal: {e}")
            
            threading.Thread(target=_send_stop, daemon=True).start()
    
    async def _send_stop_signal(self):
        """Send stop signal through the current websocket."""
        if self.current_websocket:
            try:
                await self.current_websocket.send(json.dumps({"type": "stop"}))
                print("Stop signal sent to agent")
            except Exception as e:
                print(f"Error sending stop signal: {e}")
    
    def send_to_agent(self, text, files_list=None, screenshots_data=None):
        """Send text and optional screenshots to the agent service and handle streaming response via WebSocket."""
        if not self.chat_window:
            return
        
        # Add user message to chat (with file context if any)
        display_text = text if text else f"[{len(screenshots_data) if screenshots_data else 0} Screenshot(s)]"
        self.chat_window.add_user_message(display_text)
        
        # Start sending state (shows stop button)
        self.chat_window.start_sending_state()
        
        # Start AI response
        self.chat_window.start_ai_response()
        
        def _stream():
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                loop.run_until_complete(self._websocket_stream(text, files_list, screenshots_data))
            except Exception as e:
                print(f"Error in websocket stream: {e}")
                traceback.print_exc()
                self.agent_event_received.emit({
                    "type": "error",
                    "message": f"Failed to communicate with agent: {e}"
                })
                self.agent_event_received.emit({"type": "stream.finished"})
            finally:
                loop.close()
        
        threading.Thread(target=_stream, daemon=True).start()
    
    async def _websocket_stream(self, text, files_list=None, screenshots_data=None):
        """Handle WebSocket streaming communication with chunked screenshot sending."""
        # Convert http:// to ws://
        ws_url = self.agent_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/chat/ws"
        
        # Reset stop flag
        self.stop_requested = False
        
        try:
            # Increase timeouts and max message size for screenshots
            async with websockets.connect(
                ws_url, 
                ping_interval=None,  # Disable automatic ping/pong
                close_timeout=10,
                max_size=10 * 1024 * 1024  # 10MB max message size
            ) as websocket:
                # Track the current websocket for cancellation
                self.current_websocket = websocket
                # Send initial message WITHOUT screenshots
                payload = {
                    "type": "message",
                    "message": text,
                    "files": files_list or [],
                    "has_screenshots": bool(screenshots_data),
                    "screenshot_count": len(screenshots_data) if screenshots_data else 0
                }
                await websocket.send(json.dumps(payload))
                
                # Send each screenshot as a separate message to avoid size limits
                if screenshots_data:
                    for idx, screenshot_b64 in enumerate(screenshots_data):
                        screenshot_payload = {
                            "type": "screenshot",
                            "index": idx,
                            "data": screenshot_b64
                        }
                        await websocket.send(json.dumps(screenshot_payload))
                        # Small delay to ensure order
                        await asyncio.sleep(0.01)
                    
                    # Signal all screenshots sent
                    await websocket.send(json.dumps({"type": "screenshots_complete"}))
                
                # Receive streaming events
                async for message in websocket:
                    try:
                        # Check if stop was requested
                        if self.stop_requested:
                            print("Stop requested, breaking event loop")
                            break
                        
                        event = json.loads(message)
                        
                        # Check for stop acknowledgment
                        if event.get("type") == "stop.acknowledged":
                            print("Stop acknowledged by server")
                            continue
                        
                        # Check for completion
                        if event.get("type") == "stream.finished":
                            self.agent_event_received.emit(event)
                            break
                        
                        # Check if agent was stopped
                        if event.get("type") == "response.agent.done" and event.get("stopped"):
                            print("Agent stopped by user request")
                            self.agent_event_received.emit(event)
                            break
                        
                        # Emit event to UI thread
                        self.agent_event_received.emit(event)
                        
                    except json.JSONDecodeError as je:
                        print(f"JSON decode error: {je}")
                    except Exception as ee:
                        print(f"Error processing event: {ee}")
                        traceback.print_exc()
                        
        except websockets.exceptions.WebSocketException as e:
            print(f"WebSocket error: {e}")
            raise
        except Exception as e:
            print(f"Error in websocket stream: {e}")
            traceback.print_exc()
            raise
        finally:
            # Clear the websocket reference
            self.current_websocket = None
            self.stop_requested = False
    
    @pyqtSlot(dict)
    def handle_agent_event(self, event):
        """Handle streaming events from the agent (thread-safe via signal)."""
        if not self.chat_window:
            return
        
        try:
            event_type = event.get("type", "")
            
            if event_type == "response.reasoning_summary_part.added":
                self.chat_window.append_to_ai_response(f"[{event["agent_name"]}] Thinking:\n\n", '33')
            
            elif event_type == "response.reasoning_summary_text.delta":
                delta = event['content'].get("delta", "")
                self.chat_window.append_to_ai_response(delta)
            
            elif event_type == "response.reasoning_summary_text.done":
                self.chat_window.append_to_ai_response("\n\n")
            
            elif event_type == "response.content_part.added":
                self.chat_window.append_to_ai_response(f"[{event["agent_name"]}] Assistant:\n\n", '36')
            
            elif event_type == "response.output_text.delta":
                delta = event['content'].get("delta", "")
                self.chat_window.append_to_ai_response(delta)
            
            elif event_type == "response.output_text.done":
                self.chat_window.append_to_ai_response("\n\n")
            
            elif event_type == "response.output_item.done":
                item = event['content'].get("item", {})
                # Ensure item is a dictionary
                if isinstance(item, dict):
                    item_type = item.get("type", "")
                    if item_type == "function_call":
                        func_name = item.get("name", "")
                        func_args = item.get("arguments", "")
                        # Create a new response widget for function call
                        self.chat_window.finish_ai_response()
                        self.chat_window.start_ai_response()
                        self.chat_window.append_to_ai_response(
                            f"[{event["agent_name"]}] [Function Call] {func_name}\n",
                            '35'
                        )
                        if func_args:
                            self.chat_window.append_to_ai_response(f"[{event["agent_name"]}] Arguments: {func_args}\n\n")
                        self.chat_window.finish_ai_response()
                        # Start a new widget for the next response
                        self.chat_window.start_ai_response()
            
            elif event_type == "response.image_generation_call.generating":
                self.chat_window.append_to_ai_response(f"\n[{event["agent_name"]}] [Image Generation]...\n", '34')
            
            elif event_type == "response.image_generation_call.completed":
                self.chat_window.append_to_ai_response(f"[{event["agent_name"]}] [Image Generation] Completed\n\n", '34')
            
            elif event_type == "response.completed":
                # Skip displaying usage info - not needed in chat UI
                pass
            
            elif event_type == "response.agent.done":
                # Check if it was stopped by user
                if event['content'].get("stopped"):
                    self.chat_window.append_to_ai_response(f"\n[{event["agent_name"]}] [Stopped by user]\n\n", '31')
                    self.chat_window.finish_ai_response()
                    self.chat_window.stop_sending_state()
                # Otherwise skip displaying agent done message - not needed in chat UI
            
            elif event_type == "stream.finished":
                # Custom event to finish AI response
                self.chat_window.finish_ai_response()
                # Stop sending animation
                self.chat_window.stop_sending_state()
            
            elif event_type == "error":
                # Custom error event
                error_msg = event['content'].get("message", "Unknown error")
                self.chat_window.append_to_ai_response(f"\n[{event["agent_name"]}] [Error] {error_msg}\n\n", '31')
                self.chat_window.finish_ai_response()
                # Stop sending animation on error
                self.chat_window.stop_sending_state()
        
        except Exception as e:
            print(f"[{event["agent_name"]}] Error in handle_agent_event: {e}")
            import traceback
            traceback.print_exc()

    def quit_app(self):
        """Quit the entire application cleanly."""

        reply = QMessageBox.question(
                self,
                'Close Application',
                'Are you sure you want to close the widget and all services?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
        if reply == QMessageBox.StandardButton.Yes:
            # Ensure audio stream stops before quitting
            try:
                if hasattr(self, "stream") and getattr(self, "stream") is not None:
                    try:
                        self.stream.stop()
                    except Exception:
                        pass
            finally:
                # Close chat window
                if self.chat_window:
                    self.chat_window.close()
                app = QApplication.instance()
                if app is not None:
                    app.quit()

    def closeEvent(self, event):
        # Save window position
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        pos = self.pos()
        settings.setValue("window_pos", (pos.x(), pos.y()))

        # Cleanup audio stream on window close via window controls
        try:
            if hasattr(self, "stream") and getattr(self, "stream") is not None:
                try:
                    self.stream.stop()
                except Exception:
                    pass
            # Close chat window
            if self.chat_window:
                self.chat_window.close()
            event.accept()
        except Exception as e:
            print(f"Error during closeEvent: {e}")
            event.accept()

    # --- Recording logic ---
    def start_recording(self):
        self.is_recording = True
        self.frames = []  # store raw bytes for exact PCM output

        def callback(indata, frames, time, status):
            if self.is_recording:
                # indata will be int16; store raw bytes
                self.frames.append(indata.copy().tobytes())

        # Ensure any previous stream is stopped/closed
        if hasattr(self, "stream") and getattr(self, "stream") is not None:
            try:
                self.stream.stop()
            except Exception:
                pass
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        # Use 16-bit PCM directly to match WAV settings and avoid conversion artifacts
        self.stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=self.channels,
            dtype="int16",
            blocksize=512,
            latency="low",
            callback=callback,
        )
        self.stream.start()

        # Start recording animation - clean spinner
        self.animation_step = 0
        self.main_btn.setText("⠋")
        self.recording_animation_timer.start(100)  # 100ms for smooth rotation

    def stop_recording(self):
        self.is_recording = False
        
        # Stop animation and restore icon with original style
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
        if hasattr(self, "stream") and getattr(self, "stream") is not None:
            try:
                # Abort immediately to avoid waiting for buffer drain
                self.stream.abort()
            except Exception:
                pass
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        t1 = time.perf_counter()

        # Build WAV in-memory and send to the transcribe service in background
        def _send():
            try:
                buf = io.BytesIO()
                with wave.open(buf, "wb") as wf:
                    wf.setnchannels(self.channels)
                    wf.setsampwidth(2)  # 16-bit PCM
                    wf.setframerate(self.samplerate)
                    wf.writeframes(b"".join(self.frames))
                buf.seek(0)
                t2 = time.perf_counter()

                url = os.environ.get("TRANSCRIBE_URL", "http://127.0.0.1:6001/upload")
                files = {"file": (self.filename, buf, "audio/wav")}
                data = {"language": self.selected_language}
                r = requests.post(url, data=data, files=files)
                try:
                    data = r.json()
                except Exception:
                    data = {"raw": r.text}
                t3 = time.perf_counter()
                server_ms = data.get("metrics", {}).get("total_ms") if isinstance(data, dict) else None
                print(
                    "Transcribe response:", data,
                    " timings(s): abort+close=", round(t1 - t0, 3),
                    " build_wav=", round(t2 - t1, 3),
                    " post+resp=", round(t3 - t2, 3),
                    " server_ms=", server_ms,
                )
                
                # If transcription successful, open chat if not visible and send to agent
                if isinstance(data, dict) and "text" in data:
                    transcribed_text = data["text"]
                    if transcribed_text:
                        # Open chat window if not visible
                        if not self.chat_window or not self.chat_window.isVisible():
                            self.toggle_chat_window()
                        # Use signal to call send_to_agent on main thread
                        self.transcription_received.emit(transcribed_text)
                
            except Exception as e:
                print("Upload failed:", e)

        threading.Thread(target=_send, daemon=True).start()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    gadget = Gadget()
    gadget.show()
    sys.exit(app.exec())