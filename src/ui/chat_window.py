"""Chat window with message history and input."""

import base64
from typing import Optional, List, Callable

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QPushButton,
    QLabel, QApplication, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QSettings
from PyQt6.QtGui import QPixmap

from .styles import Styles
from .components import MultilineInput, MessageBubble, CodeBlockWidget, ScreenshotSelector


class ChatWindow(QWidget):
    """Main chat window with message history."""
    
    message_sent = pyqtSignal(str, list)  # message, screenshots
    stop_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("AI Chat")
        self.resize(600, 700)
        
        # Restore position
        settings = QSettings("ai-agent", "widget")
        pos = settings.value("chat_window_pos")
        if pos:
            try:
                x, y = map(int, str(pos).strip("()").split(","))
                self.move(x, y)
            except Exception:
                pass
        
        # Token counters
        self.token_usage = {
            "input": 0,
            "output": 0,
            "cached": 0,
            "reasoning": 0,
            "total": 0,
        }
        
        # Screenshots (max 5)
        self.screenshots: List[dict] = []
        self.max_screenshots = 5
        
        # State
        self.is_sending = False
        self.current_response_widget = None
        
        self._setup_ui()
        self.setStyleSheet(Styles.MAIN_WINDOW)
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 0, 5, 5)
        layout.setSpacing(0)
        
        # Toolbar
        toolbar = self._create_toolbar()
        layout.addWidget(toolbar)
        
        # Chat area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(Styles.SCROLL_AREA)
        
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.chat_layout.setSpacing(10)
        
        self.scroll_area.setWidget(self.chat_container)
        layout.addWidget(self.scroll_area)
        
        # Screenshots preview (hidden by default)
        self.screenshots_widget = self._create_screenshots_widget()
        layout.addWidget(self.screenshots_widget)
        
        # Input area
        input_layout = self._create_input_area()
        layout.addLayout(input_layout)
    
    def _create_toolbar(self) -> QWidget:
        toolbar = QWidget()
        toolbar.setStyleSheet(Styles.TOOLBAR)
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(10, 5, 10, 5)
        
        # New chat button
        new_btn = QPushButton("+")
        new_btn.setToolTip("New Chat")
        new_btn.setFixedSize(28, 28)
        new_btn.clicked.connect(self._on_new_chat)
        new_btn.setStyleSheet("""
            QPushButton {
                background-color: #3d3d3d;
                color: #4da6ff;
                border: none;
                border-radius: 6px;
                font-size: 18px;
            }
            QPushButton:hover { background-color: #4da6ff; color: white; }
        """)
        layout.addWidget(new_btn)
        
        layout.addStretch()
        
        # Token label
        self.token_label = QLabel(self._format_tokens())
        self.token_label.setStyleSheet(Styles.TOKEN_LABEL)
        self.token_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.token_label)
        
        layout.addStretch()
        
        # Screenshot button
        screenshot_btn = QPushButton("📸")
        screenshot_btn.setToolTip("Capture Screenshot")
        screenshot_btn.setFixedSize(32, 32)
        screenshot_btn.clicked.connect(self._capture_screenshot)
        screenshot_btn.setStyleSheet(Styles.ICON_BUTTON)
        layout.addWidget(screenshot_btn)
        
        # Clear button
        clear_btn = QPushButton("🗑️")
        clear_btn.setToolTip("Clear Chat")
        clear_btn.setFixedSize(32, 32)
        clear_btn.clicked.connect(self._on_clear_chat)
        clear_btn.setStyleSheet(Styles.ICON_BUTTON_DANGER)
        layout.addWidget(clear_btn)
        
        return toolbar
    
    def _create_screenshots_widget(self) -> QWidget:
        widget = QWidget()
        widget.hide()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)
        
        self.screenshots_container = QWidget()
        self.screenshots_container.setStyleSheet(f"""
            QWidget {{
                background-color: {Styles.BG_MEDIUM};
                border: 1px solid {Styles.BG_LIGHT};
                border-radius: 5px;
                padding: 5px;
            }}
        """)
        self.screenshots_layout = QHBoxLayout(self.screenshots_container)
        self.screenshots_layout.setSpacing(5)
        
        layout.addWidget(self.screenshots_container, 1)
        
        # Clear all button
        clear_btn = QPushButton("Clear All")
        clear_btn.setFixedHeight(24)
        clear_btn.clicked.connect(self._clear_screenshots)
        clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #3d3d3d;
                color: #ff6b6b;
                border: none;
                border-radius: 3px;
                font-size: 10px;
                padding: 4px 10px;
            }
            QPushButton:hover { background-color: #ff6b6b; color: white; }
        """)
        layout.addWidget(clear_btn)
        
        return widget
    
    def _create_input_area(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        
        self.input_field = MultilineInput()
        self.input_field.send_message.connect(self._send_message)
        
        self.send_button = QPushButton("➤")
        self.send_button.setFixedSize(40, 40)
        self.send_button.clicked.connect(self._on_send_click)
        self.send_button.setStyleSheet(Styles.SEND_BUTTON)
        
        layout.addWidget(self.input_field)
        layout.addWidget(self.send_button, alignment=Qt.AlignmentFlag.AlignBottom)
        
        return layout
    
    def _format_tokens(self) -> str:
        t = self.token_usage
        return f"I: {t['input']} | O: {t['output']} | C: {t['cached']} | R: {t['reasoning']} | T: {t['total']}"
    
    def update_token_usage(self, usage: dict):
        """Update token usage display."""
        self.token_usage = {
            "input": usage.get("input_tokens", 0),
            "output": usage.get("output_tokens", 0),
            "cached": usage.get("cached_tokens", 0),
            "reasoning": usage.get("reasoning_tokens", 0),
            "total": usage.get("total_tokens", 0),
        }
        self.token_label.setText(self._format_tokens())
    
    def add_user_message(self, text: str):
        """Add a user message bubble."""
        bubble = MessageBubble(text, is_user=True)
        self.chat_layout.addWidget(bubble)
        self._scroll_to_bottom()
    
    def add_assistant_message(self, text: str):
        """Add an assistant message bubble."""
        bubble = MessageBubble(text, is_user=False)
        self.chat_layout.addWidget(bubble)
        self._scroll_to_bottom()
    
    def start_streaming_response(self):
        """Start a streaming response area."""
        self.current_response_widget = QLabel("")
        self.current_response_widget.setWordWrap(True)
        self.current_response_widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.current_response_widget.setStyleSheet(Styles.ASSISTANT_MESSAGE)
        self.chat_layout.addWidget(self.current_response_widget)
        self._scroll_to_bottom()
    
    def append_streaming_text(self, text: str):
        """Append text to the current streaming response."""
        if self.current_response_widget:
            self.current_response_widget.setText(
                self.current_response_widget.text() + text
            )
            self._scroll_to_bottom()
    
    def finish_streaming_response(self):
        """Finish the current streaming response."""
        self.current_response_widget = None
    
    def add_code_block(self, code: str, language: str = ""):
        """Add a code block."""
        widget = CodeBlockWidget(code, language)
        self.chat_layout.addWidget(widget)
        self._scroll_to_bottom()
    
    def set_sending_state(self, is_sending: bool):
        """Update UI state for sending."""
        self.is_sending = is_sending
        self.send_button.setEnabled(not is_sending)
        self.send_button.setText("⏹" if is_sending else "➤")
        self.input_field.setEnabled(not is_sending)
    
    def clear_chat(self):
        """Clear all messages."""
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
    
    def _scroll_to_bottom(self):
        """Scroll to the bottom of the chat."""
        QTimer.singleShot(50, lambda: (
            self.scroll_area.verticalScrollBar().setValue(
                self.scroll_area.verticalScrollBar().maximum()
            )
        ))
    
    def _send_message(self):
        """Handle sending a message."""
        if self.is_sending:
            self.stop_requested.emit()
            return
        
        text = self.input_field.toPlainText().strip()
        if not text and not self.screenshots:
            return
        
        # Get screenshot data
        screenshots_b64 = [s["data"] for s in self.screenshots]
        
        # Clear input and screenshots
        self.input_field.clear_text()
        self._clear_screenshots()
        
        # Emit signal
        self.message_sent.emit(text, screenshots_b64)
    
    def _on_send_click(self):
        """Handle send button click."""
        if self.is_sending:
            self.stop_requested.emit()
        else:
            self._send_message()
    
    def _on_new_chat(self):
        """Handle new chat button."""
        reply = QMessageBox.question(
            self, "New Chat",
            "Start a new chat? This will clear the current conversation.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.clear_requested.emit()
    
    def _on_clear_chat(self):
        """Handle clear chat button."""
        reply = QMessageBox.question(
            self, "Clear Chat",
            "Clear all messages?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.clear_requested.emit()
    
    def _capture_screenshot(self):
        """Capture a screenshot."""
        if len(self.screenshots) >= self.max_screenshots:
            QMessageBox.warning(self, "Limit Reached", f"Maximum {self.max_screenshots} screenshots allowed.")
            return
        
        # Hide window temporarily
        self.hide()
        QTimer.singleShot(200, self._do_capture)
    
    def _do_capture(self):
        """Perform the actual capture."""
        screen = QApplication.primaryScreen()
        screenshot = screen.grabWindow(0)
        
        selector = ScreenshotSelector(screenshot)
        selector.screenshot_selected.connect(self._on_screenshot_selected)
        selector.screenshot_cancelled.connect(self._on_screenshot_cancelled)
        selector.showFullScreen()
    
    def _on_screenshot_selected(self, pixmap: QPixmap):
        """Handle screenshot selection."""
        self.show()
        
        # Convert to base64
        from PyQt6.QtCore import QBuffer, QIODevice
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, "PNG")
        b64 = base64.b64encode(buffer.data()).decode("utf-8")
        
        self.screenshots.append({"data": b64, "pixmap": pixmap})
        self._update_screenshots_display()
    
    def _on_screenshot_cancelled(self):
        """Handle screenshot cancellation."""
        self.show()
    
    def _update_screenshots_display(self):
        """Update the screenshots preview area."""
        # Clear existing
        while self.screenshots_layout.count():
            item = self.screenshots_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        # Add thumbnails
        for i, screenshot in enumerate(self.screenshots):
            thumb = QLabel()
            scaled = screenshot["pixmap"].scaled(
                60, 60,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            thumb.setPixmap(scaled)
            thumb.setStyleSheet("border: 1px solid #555; border-radius: 3px;")
            self.screenshots_layout.addWidget(thumb)
        
        self.screenshots_widget.setVisible(len(self.screenshots) > 0)
    
    def _clear_screenshots(self):
        """Clear all screenshots."""
        self.screenshots = []
        self._update_screenshots_display()
    
    def closeEvent(self, event):
        """Save position on close."""
        settings = QSettings("ai-agent", "widget")
        settings.setValue("chat_window_pos", f"({self.x()},{self.y()})")
        super().closeEvent(event)
