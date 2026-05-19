"""Inner Voice Chat Window - Read-only view of Aria's conversation with her inner voice."""

from PyQt6.QtWidgets import (QWidget, QPushButton, QVBoxLayout, QHBoxLayout, 
                              QScrollArea, QLabel, QSizePolicy, QTextBrowser,
                              QMenu, QMessageBox)
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtCore import Qt, QTimer, QEvent, pyqtSignal
from datetime import datetime
import re
import markdown
from pygments import highlight
import uuid
from ...appcore.runtime_context import Runtime
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.lexers.agile import PythonLexer
from pygments.formatters import HtmlFormatter

from .chat_window import FlowLayout
from .inner_voice_session_json_window import InnerVoiceSessionJsonWindow
from ..screen_utils import validate_window_position


class InnerVoiceWindow(QWidget):
    """Read-only chat window showing Aria's conversation with her inner voice."""
    
    clear_requested = pyqtSignal()  # Signal to request clearing the inner voice history
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Window)
        self.setWindowTitle("Inner Voice Chat - Aria's Private Dialogue")
        self.resize(650, 750)
        
        # Restore last position if available
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        saved_pos = settings.value("inner_voice_window_pos", None)
        validated_pos = validate_window_position(saved_pos, 650, 750)
        if validated_pos is not None:
            x, y = validated_pos
            self.move(x, y)
        
        # Chat display area
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 0, 5, 5)
        layout.setSpacing(0)
        
        # Top toolbar
        toolbar = QWidget()
        toolbar.setStyleSheet("""
            QWidget {
                background-color: #2d2d2d;
                border-bottom: 1px solid #3d3d3d;
            }
        """)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 5, 10, 5)
        
        # Title label
        title_label = QLabel("🧠 Inner Voice Dialogue")
        title_label.setStyleSheet("""
            QLabel {
                color: #bb86fc;
                font-size: 14px;
                font-weight: bold;
                background: transparent;
            }
        """)
        toolbar_layout.addWidget(title_label)
        
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
                font-size: 14px;
                font-weight: bold;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
                color: #4da6ff !important;
            }
        """)
        toolbar_layout.addWidget(self.json_button)
        
        # Refresh button
        self.refresh_button = QPushButton("🔄")
        self.refresh_button.setToolTip("Refresh Session")
        self.refresh_button.setFixedSize(32, 32)
        self.refresh_button.clicked.connect(self.load_and_display_session)
        self.refresh_button.setStyleSheet("""
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
        toolbar_layout.addWidget(self.refresh_button)
        
        # Clear button
        self.clear_button = QPushButton("🗑️")
        self.clear_button.setToolTip("Clear Inner Voice History")
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
        
        # Info banner
        info_banner = QLabel(
            "This is a read-only view of Aria's private conversation with her inner voice. "
            "The user cannot see or participate in this dialogue."
        )
        info_banner.setWordWrap(True)
        info_banner.setStyleSheet("""
            QLabel {
                background-color: #2d2d30;
                color: #bb86fc;
                padding: 10px;
                font-size: 11px;
                border-bottom: 1px solid #3d3d3d;
            }
        """)
        layout.addWidget(info_banner)
        
        # Scrollable chat display
        self.scrollable_area = QScrollArea()
        self.scrollable_area.setWidgetResizable(True)

        # Auto-refresh when the active user session changes (Inner Voice should track current session).
        try:
            self._bus = Runtime.get_event_bus()
            self._session_active_unsub = self._bus.subscribe(
                "session.active.changed",
                lambda ev: self.load_and_display_session(),
            )
        except Exception:
            self._bus = None
            self._session_active_unsub = None
        self.scrollable_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.chat_layout.setSpacing(10)
        
        self.scrollable_area.setWidget(self.chat_container)
        layout.addWidget(self.scrollable_area)
        
        # JSON viewer window
        self.json_viewer = InnerVoiceSessionJsonWindow(self)
        # Connect data_cleared signal to refresh this window
        self.json_viewer.data_cleared.connect(self._on_session_cleared)
        
        # Load initial session
        self.load_and_display_session()
    
    def load_and_display_session(self):
        """Load and display the inner voice session (via event bus)."""
        bus = Runtime.get_event_bus()
        reply_topic = f"inner_voice.ui.reply.session.entries.get.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            payload = getattr(ev, "payload", {}) or {}
            if not isinstance(payload, dict) or payload.get("status") != "success":
                msg = payload.get("message", "Failed to load inner voice session") if isinstance(payload, dict) else "Failed to load inner voice session"
                print(f"[InnerVoiceWindow] {msg}")
                return

            entries = payload.get("entries", [])
            if not isinstance(entries, list):
                entries = []
            self._render_entries(entries)

        unsub = bus.subscribe(reply_topic, _on_reply)
        bus.publish("inner_voice.cmd.session.entries.get", {"reply_topic": reply_topic})

    def _render_entries(self, entries):
        try:
            print(f"[InnerVoiceWindow] Loaded {len(entries)} entries from inner voice session")

            # Clear current display
            self.clear_chat_display()

            # Display each message
            for idx, entry in enumerate(entries):
                content = entry.get("content", {}) if isinstance(entry, dict) else {}
                role = content.get("role", "unknown") if isinstance(content, dict) else "unknown"
                entry_type = entry.get("kind", "unknown") if isinstance(entry, dict) else "unknown"

                print(f"[InnerVoiceWindow] Entry {idx}: type={entry_type}, role={role}")

                if role == "user":
                    # Aria speaking to her inner voice
                    self.add_aria_message(content)
                elif role == "assistant":
                    # Inner voice responding
                    self.add_inner_voice_message(content)

                # Check wrapped_entry type for non-message entries (reasoning, function calls)
                elif entry_type == "reasoning":
                    summary = content.get("summary", "")
                    if summary:
                        if isinstance(summary, list):
                            summary_text = "\n\n".join(str(s.get("text", s)) for s in summary)
                        else:
                            summary_text = str(summary.get("text", summary))
                        if summary_text.strip():
                            self.add_reasoning_message(summary_text)

                elif entry_type == "function_call":
                    func_name = content.get("name", "")
                    func_args = content.get("arguments", "")
                    self.add_function_call_message(func_name, func_args)

                elif entry_type == "function_call_output":
                    output = content.get("output", "")
                    if output:
                        self.add_function_output_message(output)

                elif entry_type == "message":
                    if role == "assistant":
                        self.add_inner_voice_message(content)
                    elif role == "user":
                        self.add_aria_message(content)
                    else:
                        print(f"[InnerVoiceWindow] Skipping message entry with unknown role: {role}")

                else:
                    if role != "assistant" and role != "user":
                        print(f"[InnerVoiceWindow] Skipping entry with role: {role}, type: {entry_type}")

            self.scroll_to_bottom()

        except Exception as e:
            print(f"[InnerVoiceWindow] Error rendering session: {e}")
            import traceback
            traceback.print_exc()

    def add_aria_message(self, content):
        """Add a message from Aria (right-aligned, blue)."""
        msg_widget = QWidget()
        msg_layout = QHBoxLayout(msg_widget)
        msg_layout.setContentsMargins(0, 0, 0, 0)
        
        # Spacer for right alignment
        msg_layout.addStretch(1)
        
        # Message box
        msg_box = QWidget()
        msg_box_layout = QVBoxLayout(msg_box)
        msg_box_layout.setContentsMargins(0, 0, 0, 0)
        msg_box_layout.setSpacing(2)
        
        # Extract text from content
        text = self._extract_text_from_content(content)
        
        if not text or not text.strip():
            print(f"[InnerVoiceWindow] Warning: Empty Aria message, content structure: {content}")
            text = "[Empty message]"
        
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
        
        # Label
        aria_label = QLabel("Aria")
        aria_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        aria_label.setStyleSheet("""
            QLabel {
                color: #4da6ff;
                font-size: 10px;
                font-weight: bold;
                padding-right: 5px;
                background: transparent;
            }
        """)
        msg_box_layout.addWidget(aria_label)
        
        msg_layout.addWidget(msg_box, 4)
        
        self.chat_layout.addWidget(msg_widget)
    
    def add_inner_voice_message(self, content):
        """Add a message from inner voice (left-aligned, purple)."""
        msg_widget = QWidget()
        msg_layout = QHBoxLayout(msg_widget)
        msg_layout.setContentsMargins(0, 0, 0, 0)
        
        # Message box
        msg_box = QWidget()
        msg_box_layout = QVBoxLayout(msg_box)
        msg_box_layout.setContentsMargins(0, 0, 0, 0)
        msg_box_layout.setSpacing(2)
        
        # Extract text and handle tool calls
        text_parts = self._extract_text_and_tools(content)
        
        for part in text_parts:
            if part["type"] == "text":
                self._add_text_widget(part["content"], msg_box_layout)
            elif part["type"] == "tool_call":
                self._add_tool_call_widget(part["content"], msg_box_layout)
        
        # Label
        voice_label = QLabel("Inner Voice")
        voice_label.setStyleSheet("""
            QLabel {
                color: #bb86fc;
                font-size: 10px;
                font-weight: bold;
                padding-left: 5px;
                background: transparent;
            }
        """)
        msg_box_layout.addWidget(voice_label)
        
        msg_layout.addWidget(msg_box, 4)
        
        # Spacer for left alignment
        msg_layout.addStretch(1)
        
        self.chat_layout.addWidget(msg_widget)
    
    def add_reasoning_message(self, text):
        """Add a reasoning/thinking message (center-aligned, yellow)."""
        msg_widget = QWidget()
        msg_layout = QVBoxLayout(msg_widget)
        msg_layout.setContentsMargins(0, 0, 0, 0)
        msg_layout.setSpacing(2)
        
        # Header
        header_label = QLabel("🧠 Thinking")
        header_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        header_label.setStyleSheet("""
            QLabel {
                color: #ffcc00;
                font-size: 11px;
                font-weight: bold;
                padding-left: 5px;
                background: transparent;
            }
        """)
        msg_layout.addWidget(header_label)
        
        # Message text
        text_label = QLabel(text)
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_label.setStyleSheet("""
            QLabel {
                background-color: #3a3a2a;
                color: #ffcc00;
                border-left: 3px solid #ffcc00;
                border-radius: 5px;
                padding: 10px;
                font-size: 12px;
                font-style: italic;
            }
        """)
        msg_layout.addWidget(text_label)
        
        self.chat_layout.addWidget(msg_widget)
    
    def add_function_call_message(self, func_name, func_args):
        """Add a function call message (center-aligned, magenta)."""
        msg_widget = QWidget()
        msg_layout = QVBoxLayout(msg_widget)
        msg_layout.setContentsMargins(0, 0, 0, 0)
        msg_layout.setSpacing(4)
        
        # Function name header
        header_label = QLabel(f"🔧 Tool Call: {func_name}")
        header_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        header_label.setStyleSheet("""
            QLabel {
                color: #ff00ff;
                font-size: 11px;
                font-weight: bold;
                padding-left: 5px;
                background: transparent;
            }
        """)
        msg_layout.addWidget(header_label)
        
        # Arguments (if present)
        if func_args:
            args_label = QLabel(f"Arguments: {func_args}")
            args_label.setWordWrap(True)
            args_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            args_label.setStyleSheet("""
                QLabel {
                    background-color: #2a2a3a;
                    color: #d4d4d4;
                    border-left: 3px solid #ff00ff;
                    border-radius: 5px;
                    padding: 8px;
                    font-size: 11px;
                    font-family: 'Consolas', monospace;
                }
            """)
            msg_layout.addWidget(args_label)
        
        self.chat_layout.addWidget(msg_widget)
    
    def add_function_output_message(self, output):
        """Add a function call output message (center-aligned, cyan)."""
        msg_widget = QWidget()
        msg_layout = QVBoxLayout(msg_widget)
        msg_layout.setContentsMargins(0, 0, 0, 0)
        msg_layout.setSpacing(4)
        
        # Output header
        header_label = QLabel("↩️ Function Output")
        header_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        header_label.setStyleSheet("""
            QLabel {
                color: #00bfff;
                font-size: 11px;
                font-weight: bold;
                padding-left: 5px;
                background: transparent;
            }
        """)
        msg_layout.addWidget(header_label)
        
        # Output content
        output_text = str(output)
        if len(output_text) > 500:
            output_text = output_text[:500] + "..."
        
        output_label = QLabel(output_text)
        output_label.setWordWrap(True)
        output_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        output_label.setStyleSheet("""
            QLabel {
                background-color: #2a3a3a;
                color: #d4d4d4;
                border-left: 3px solid #00bfff;
                border-radius: 5px;
                padding: 8px;
                font-size: 11px;
                font-family: 'Consolas', monospace;
            }
        """)
        msg_layout.addWidget(output_label)
        
        self.chat_layout.addWidget(msg_widget)
    
    def _extract_text_from_content(self, content):
        """Extract text from message content (handles both string and array formats)."""
        if isinstance(content.get("content"), str):
            return content["content"]
        elif isinstance(content.get("content"), list):
            text_parts = []
            for item in content["content"]:
                if isinstance(item, dict):
                    # Handle input_text (from user/Aria) or text type
                    if item.get("type") == "input_text":
                        text = item.get("text", "")
                        # Remove "User's input:" prefix if present (from main agent format)
                        if "User's input:" in text:
                            text = text.split("User's input:", 1)[1].strip()
                        text_parts.append(text)
                    elif item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
            return "\n".join(text_parts)
        return ""
    
    def _extract_text_and_tools(self, content):
        """Extract text and tool calls from message content."""
        parts = []
        
        # Handle text content
        if isinstance(content.get("content"), str):
            parts.append({"type": "text", "content": content["content"]})
        elif isinstance(content.get("content"), list):
            for item in content["content"]:
                if isinstance(item, dict):
                    # Handle output_text (from assistant) or text type
                    if item.get("type") == "output_text":
                        text = item.get("text", "")
                        if text.strip():
                            parts.append({"type": "text", "content": text})
                    elif item.get("type") == "text":
                        text = item.get("text", "")
                        if text.strip():
                            parts.append({"type": "text", "content": text})
        
        # Handle tool calls
        if "tool_calls" in content:
            for tool_call in content["tool_calls"]:
                parts.append({"type": "tool_call", "content": tool_call})
        
        return parts if parts else [{"type": "text", "content": ""}]
    
    def _add_text_widget(self, text, layout):
        """Add a text widget to the layout."""
        if not text.strip():
            return
        
        text_label = QLabel(text)
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_label.setStyleSheet("""
            QLabel {
                background-color: #3d3d3d;
                color: #d4d4d4;
                border-radius: 10px;
                padding: 10px;
                font-size: 13px;
            }
        """)
        layout.addWidget(text_label)
    
    def _add_tool_call_widget(self, tool_call, layout):
        """Add a tool call widget to the layout."""
        tool_name = tool_call.get("function", {}).get("name", "unknown")
        tool_args = tool_call.get("function", {}).get("arguments", "{}")
        
        tool_widget = QWidget()
        tool_layout = QVBoxLayout(tool_widget)
        tool_layout.setContentsMargins(8, 8, 8, 8)
        tool_layout.setSpacing(4)
        
        # Tool name
        name_label = QLabel(f"🔧 {tool_name}")
        name_label.setStyleSheet("""
            QLabel {
                color: #ffcc00;
                font-size: 12px;
                font-weight: bold;
                background: transparent;
            }
        """)
        tool_layout.addWidget(name_label)
        
        # Tool arguments (collapsed by default)
        args_label = QLabel(f"Arguments: {tool_args[:100]}..." if len(tool_args) > 100 else f"Arguments: {tool_args}")
        args_label.setWordWrap(True)
        args_label.setStyleSheet("""
            QLabel {
                color: #888888;
                font-size: 11px;
                font-family: 'Consolas', monospace;
                background: transparent;
            }
        """)
        tool_layout.addWidget(args_label)
        
        tool_widget.setStyleSheet("""
            QWidget {
                background-color: #2a2a2a;
                border-left: 3px solid #ffcc00;
                border-radius: 5px;
            }
        """)
        
        layout.addWidget(tool_widget)
    
    def clear_chat_display(self):
        """Clear all messages from the display."""
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
    
    def request_clear_chat(self):
        """Request to clear the inner voice session (via event bus)."""
        reply = QMessageBox.question(
            self,
            "Clear Inner Voice History",
            "Are you sure you want to clear Aria's inner voice session?\n\nThis action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        bus = Runtime.get_event_bus()
        reply_topic = f"inner_voice.ui.reply.session.entries.clear.{uuid.uuid4()}"
        unsub = None

        def _on_reply(ev):
            nonlocal unsub
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
                unsub = None

            payload = getattr(ev, "payload", {}) or {}
            if isinstance(payload, dict) and payload.get("status") == "success":
                self.clear_chat_display()
                print("[InnerVoiceWindow] Cleared inner voice history")
            else:
                msg = payload.get("message", "Failed to clear history") if isinstance(payload, dict) else "Failed to clear history"
                print(f"[InnerVoiceWindow] Error clearing history: {msg}")
                QMessageBox.warning(self, "Error", f"Failed to clear history: {msg}")

        unsub = bus.subscribe(reply_topic, _on_reply)
        bus.publish("inner_voice.cmd.session.entries.clear", {"reply_topic": reply_topic})
    
    def _on_session_cleared(self):
        """Handle history cleared signal from JSON window."""
        # Refresh the display
        self.load_and_display_session()
    
    def open_json_viewer(self):
        """Open the JSON history viewer window."""
        if self.json_viewer:
            self.json_viewer.refresh_content()
            self.json_viewer.show()
            self.json_viewer.raise_()
            self.json_viewer.activateWindow()
    
    def scroll_to_bottom(self):
        """Scroll to the bottom of the chat."""
        QTimer.singleShot(10, self._do_scroll)
    
    def _do_scroll(self):
        """Actually perform the scroll."""
        scroll = self.findChild(QScrollArea)
        if scroll:
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    
    def closeEvent(self, event):
        """Save position on close."""
        from PyQt6.QtCore import QSettings

        settings = QSettings("ai-agent", "widget")
        settings.setValue("inner_voice_window_pos", (self.pos().x(), self.pos().y()))
        self.hide()
        event.ignore()
    
    def showEvent(self, event):
        """Restore position and refresh on show."""
        from PyQt6.QtCore import QSettings
        settings = QSettings("ai-agent", "widget")
        saved_pos = settings.value("inner_voice_window_pos", None)
        validated_pos = validate_window_position(saved_pos, 650, 750)
        if validated_pos is not None:
            x, y = validated_pos
            self.move(x, y)
        
        # Auto-refresh when window is shown
        self.load_and_display_session()
        super().showEvent(event)
