import os
import traceback
from PyQt6.QtWidgets import (QApplication, QWidget, QPushButton, QVBoxLayout, 
                              QHBoxLayout, QMenu,  QScrollArea,
                              QLabel, QSizePolicy, QLayout, QDialog, QMessageBox, QTextBrowser, QSizePolicy,
                              QTextEdit, QDialogButtonBox)
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtCore import Qt, QPoint, QEvent, QTimer, QRect, QSize, pyqtSignal

import markdown
import re
from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.lexers.agile import PythonLexer
from pygments.formatters import HtmlFormatter

from .multiline_input import MultilineInput
from .screenshot_selector import ScreenshotSelector


class EditMessageDialog(QDialog):
    """Dialog for editing a user message before resending."""
    
    def __init__(self, current_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Message")
        self.setModal(True)
        self.resize(500, 300)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        # Info label
        info_label = QLabel("Edit your message below. This will delete the original message\nand all responses after it, then send your edited message.")
        info_label.setStyleSheet("QLabel { color: #888888; font-size: 12px; }")
        layout.addWidget(info_label)
        
        # Text editor
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(current_text)
        self.text_edit.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 10px;
                font-size: 13px;
                font-family: 'Segoe UI', sans-serif;
            }
        """)
        layout.addWidget(self.text_edit)
        
        # Button box
        button_box = QDialogButtonBox()
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #3d3d3d;
                color: #d4d4d4;
                border: none;
                border-radius: 5px;
                padding: 8px 20px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #4d4d4d;
            }
        """)
        
        self.send_btn = QPushButton("Send Edited Message")
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: #0e639c;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 20px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1177bb;
            }
        """)
        
        button_box.addButton(self.cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)
        button_box.addButton(self.send_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        
        self.cancel_btn.clicked.connect(self.reject)
        self.send_btn.clicked.connect(self.accept)
        
        layout.addWidget(button_box)
        
        # Style the dialog
        self.setStyleSheet("""
            QDialog {
                background-color: #252526;
            }
        """)
        
        # Focus text edit and select all
        self.text_edit.setFocus()
        self.text_edit.selectAll()
    
    def get_text(self) -> str:
        """Get the edited text."""
        return self.text_edit.toPlainText()


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
    
    # Signals for message operations (emitted to parent FloatingWidget)
    delete_message_requested = pyqtSignal(str)  # entry_id
    edit_message_requested = pyqtSignal(str, str)  # entry_id, new_text
    
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
        
        # Track pending user message (sent but not yet saved to storage)
        self.pending_user_message_widget = None
        
        self.parent_widget = parent
    
    def add_user_message(self, text, entry_id=None, timestamp=None):
        """Add user message to chat (right-aligned, max 80% width) with hover actions.
        
        Args:
            text: The message text to display
            entry_id: Optional entry ID from storage (for delete/edit operations)
            timestamp: Optional ISO timestamp string (defaults to now)
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
        msg_widget.entry_id = entry_id
        msg_widget.message_text = text
        msg_widget.timestamp = ts
        
        msg_layout = QHBoxLayout(msg_widget)
        msg_layout.setContentsMargins(0, 0, 0, 0)

        # Spacer for right alignment (20% of width)
        msg_layout.addStretch(1)

        # Message box (80% of width)
        msg_box = QWidget()
        msg_box_layout = QVBoxLayout(msg_box)
        msg_box_layout.setContentsMargins(0, 0, 0, 0)
        msg_box_layout.setSpacing(2)

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
        
        # Timestamp label (subtle, right-aligned)
        time_label = QLabel(ts.strftime("%H:%M"))
        time_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        time_label.setToolTip(ts.strftime("%Y-%m-%d %H:%M:%S"))
        time_label.setStyleSheet("""
            QLabel {
                color: #888888;
                font-size: 10px;
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
                font-size: 14px;
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
                font-size: 14px;
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

        # Edit button - opens edit dialog, then deletes and resends
        edit_btn = QPushButton("✏️")
        edit_btn.setFixedSize(22, 22)
        if entry_id:
            edit_btn.setToolTip("Edit message (will regenerate response)")
            edit_btn.setStyleSheet(style_sheet)
            edit_btn.clicked.connect(lambda: self._edit_message(entry_id, text))
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
            remove_btn.clicked.connect(lambda: self._delete_message(entry_id, text))
        else:
            remove_btn.setToolTip("Cannot remove - message not yet saved")
            remove_btn.setStyleSheet(style_sheet_disabled)
            remove_btn.setEnabled(False)
        actions_layout.addWidget(remove_btn)
        
        # Store button references for later enabling (when entry_id becomes available)
        msg_widget.edit_btn = edit_btn
        msg_widget.delete_btn = remove_btn
        msg_widget.style_sheet_enabled = style_sheet
        
        # Track as pending if no entry_id yet
        if entry_id is None:
            self.pending_user_message_widget = msg_widget

        # Start with buttons invisible (but still in layout to prevent shifting)
        for btn in [copy_btn, edit_btn, remove_btn]:
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
                for btn in [copy_btn, edit_btn, remove_btn]:
                    btn.setVisible(True)
            elif event.type() == QEvent.Type.Leave:
                for btn in [copy_btn, edit_btn, remove_btn]:
                    btn.setVisible(False)
            return False
        msg_box.installEventFilter(msg_box)
        msg_box.eventFilter = eventFilter

        self.chat_layout.addWidget(msg_widget)
        self.scroll_to_bottom()
    
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
            btn.clicked.connect(lambda checked, eid=entry_id, t=text: self._edit_message(eid, t))
        
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
            btn.clicked.connect(lambda checked, eid=entry_id, t=text: self._delete_message(eid, t))
    
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
                font-size: 13px;
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
    
    def _delete_message(self, entry_id: str, text: str):
        """Request deletion of message and all subsequent messages."""
        if not entry_id:
            QMessageBox.warning(
                self,
                "Cannot Delete",
                "This message doesn't have an ID (it may be a new unsaved message)."
            )
            return
        
        # Confirm deletion
        reply = QMessageBox.question(
            self,
            "Delete Message",
            f"Delete this message and all messages after it?\n\n\"{text[:100]}{'...' if len(text) > 100 else ''}\"\n\nThis action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            print(f"[ChatWindow] Requesting delete from entry_id: {entry_id}")
            self.delete_message_requested.emit(entry_id)
    
    def _edit_message(self, entry_id: str, current_text: str):
        """Open edit dialog and request edit operation."""
        if not entry_id:
            QMessageBox.warning(
                self,
                "Cannot Edit",
                "This message doesn't have an ID (it may be a new unsaved message)."
            )
            return
        
        # Show edit dialog
        dialog = EditMessageDialog(current_text, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_text = dialog.get_text()
            if new_text and new_text.strip():
                print(f"[ChatWindow] Requesting edit for entry_id: {entry_id}")
                self.edit_message_requested.emit(entry_id, new_text.strip())
                # Scroll to bottom after edit is initiated
                self.scroll_to_bottom()
            else:
                QMessageBox.warning(self, "Empty Message", "Message cannot be empty.")
    
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
        
        # Start with zero height - will expand as content is added
        self.current_ai_widget.setFixedHeight(0)
        
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
        text_browser.setFixedHeight(int(height + 25))

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
        text_browser.setFixedHeight(int(height + 25))
        
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
