import os
import sounddevice as sd
import wave
import io
import time
import threading
import json
import traceback
from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QMenu, QMessageBox
from PyQt6.QtGui import QAction
from PyQt6.QtCore import Qt, QEvent, pyqtSignal, pyqtSlot, QTimer

from .components import SettingsWindow
from .components import ChatHistoryJsonWindow
from .components import ChatWindow
from .components import MemoriesWindow


class FloatingWidget(QWidget):
    """Main floating widget - the entry point for the AI assistant."""
    
    history_loaded = pyqtSignal(list)  # Now carries wrapped history with IDs
    history_json_loaded = pyqtSignal(str)
    agent_event_received = pyqtSignal(dict)
    transcription_received = pyqtSignal(str)
    message_deleted = pyqtSignal(dict)  # Result of delete operation
    edit_send_message = pyqtSignal(str)  # Send new message after edit (carries text)
    

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
        self.history_json_window = ChatHistoryJsonWindow(self, app=app)
        # TODO: Enable when export/import formats are aligned
        # self.history_json_window.data_loaded.connect(self.fetch_and_display_chat_history)
        self.memories_window = MemoriesWindow(self, app=app)
        self.settings_window = None

        # Agent inference tracking
        self.stop_requested = False
        self.agent_thread = None

        # Connect signals
        self.history_loaded.connect(self.display_chat_history)
        self.history_json_loaded.connect(self._display_history_json)
        self.agent_event_received.connect(self.handle_agent_event)
        self.transcription_received.connect(self.chat_window.send_message)
        self.message_deleted.connect(self._on_message_deleted)
        self.edit_send_message.connect(self._on_edit_send_message)
        
        # Connect ChatWindow signals for message operations
        self.chat_window.delete_message_requested.connect(self.handle_delete_message)
        self.chat_window.edit_message_requested.connect(self.handle_edit_message)

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
        
        open_memories_action = QAction("Open Memories", self)
        open_memories_action.triggered.connect(self.open_memories)
        menu.addAction(open_memories_action)

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
        """Open settings window and load current settings."""
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self)
            self.settings_window.settings_save_requested.connect(self._on_settings_save_requested)
        
        # Load current settings from app
        if self.app:
            current_settings = self.app.get_current_settings()
            self.settings_window.load_settings(
                base_url=current_settings.get("base_url", ""),
                api_token=current_settings.get("api_token", "")
            )
        
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()
    
    def _on_settings_save_requested(self, settings):
        """Handle settings save request from settings window.
        
        Forwards to app for validation and persistence.
        """
        if not self.app:
            self.settings_window.show_save_error("App not initialized")
            return
        
        try:
            # Ask app to save settings
            success = self.app.save_settings(settings)
            
            if success:
                self.settings_window.show_save_success()
            else:
                self.settings_window.show_save_error("Failed to save settings")
        except Exception as e:
            self.settings_window.show_save_error(str(e))

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
        """Request wrapped chat history (with IDs) from app."""
        def _fetch():
            try:
                if self.app:
                    # Get wrapped history which includes entry IDs
                    wrapped_history = self.app.get_wrapped_chat_history(chat_id="default")
                    self.history_loaded.emit(wrapped_history)
            except Exception as e:
                print(f"Failed to fetch chat history: {e}")
        threading.Thread(target=_fetch, daemon=True).start()
    
    @pyqtSlot(list)
    def display_chat_history(self, wrapped_history):
        """Display wrapped chat history with entry IDs."""
        if not self.chat_window:
            return
        print("Loading chat history...")
        self.chat_window.clear_chat()
        
        for wrapped_entry in wrapped_history:
            entry_id = wrapped_entry.get("id")
            entry = wrapped_entry.get("content", {})
            role = entry.get("role", "")
            content = entry.get("content", [])
            
            if role == "user":
                for item in content:
                    if item.get("type") == "input_text":
                        text = item.get("text", "")
                        if "User's input:" in text:
                            text = text.split("User's input:", 1)[1].strip()
                        # Pass entry_id and timestamp to the message widget
                        timestamp = wrapped_entry.get("ts")
                        self.chat_window.add_user_message(text, entry_id=entry_id, timestamp=timestamp)
            
            elif role == "assistant":
                for item in content:
                    if item.get("type") == "output_text":
                        text = item.get("text", "")
                        self.chat_window.start_ai_response()
                        self.chat_window.append_to_ai_response("Assistant:\n\n", '36')
                        self.chat_window.append_to_ai_response(text)
                        self.chat_window.finish_ai_response()
            
            # Check wrapped_entry type for non-message entries
            elif wrapped_entry.get("type") == "reasoning":
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
            
            elif wrapped_entry.get("type") == "function_call":
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
    
    def open_memories(self):
        """Open the memories window and load current memories."""
        if self.memories_window:
            self.memories_window.show()
            self.memories_window.raise_()
            self.memories_window.activateWindow()
            self.memories_window.refresh_content()

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
    
    def handle_delete_message(self, entry_id: str):
        """Handle request to delete a message and all subsequent messages.
        
        Args:
            entry_id: The ID of the message to delete from
        """
        def _delete():
            try:
                if self.app:
                    result = self.app.delete_messages_from_id(entry_id)
                    self.message_deleted.emit(result)
            except Exception as e:
                print(f"Failed to delete message: {e}")
                self.message_deleted.emit({"status": "error", "message": str(e)})
        threading.Thread(target=_delete, daemon=True).start()
    
    @pyqtSlot(dict)
    def _on_message_deleted(self, result: dict):
        """Handle message deletion result - refresh UI."""
        if result.get("status") == "success":
            print(f"[UI] Message deleted successfully. Refreshing chat...")
            # Refresh the chat display
            self.fetch_and_display_chat_history()
            # Also refresh JSON window if visible
            if self.history_json_window and self.history_json_window.isVisible():
                self._fetch_history_json_async()
        else:
            error_msg = result.get("message", "Unknown error")
            print(f"[UI] Message deletion failed: {error_msg}")
            QMessageBox.warning(self, "Delete Failed", f"Failed to delete message: {error_msg}")
    
    def handle_edit_message(self, entry_id: str, new_text: str):
        """Handle request to edit a message (delete from this point and send new message).
        
        Args:
            entry_id: The ID of the message to edit
            new_text: The new message text to send
        """
        def _edit_and_send():
            try:
                if self.app:
                    # First delete messages from this ID onwards
                    result = self.app.delete_messages_from_id(entry_id)
                    if result.get("status") == "success":
                        print(f"[UI] Messages deleted for edit. Sending new message...")
                        # Emit signal to refresh UI first
                        self.message_deleted.emit(result)
                        # Small delay to let UI refresh, then emit signal to send new message
                        import time
                        time.sleep(0.2)
                        # Use signal to send message on main thread (safe from background thread)
                        self.edit_send_message.emit(new_text)
                    else:
                        print(f"[UI] Failed to delete messages for edit: {result.get('message')}")
                        self.message_deleted.emit(result)
            except Exception as e:
                print(f"Failed to edit message: {e}")
                self.message_deleted.emit({"status": "error", "message": str(e)})
        
        threading.Thread(target=_edit_and_send, daemon=True).start()
    
    @pyqtSlot(str)
    def _on_edit_send_message(self, new_text: str):
        """Handle sending new message after edit (called on main thread via signal)."""
        if self.chat_window:
            self.send_to_agent(new_text)
    
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
                # print token usage if available, for debugging, beatutifully formatted
                token_usage_history = event.get("token_usage_history", {})
                print(f"[{agent_name}] Token Usage Summary:\n{json.dumps(token_usage_history, indent=2)}")
                # App handles saving chat history and images - UI just updates display
                if content.get("stopped"):
                    self.chat_window.append_to_ai_response(f"\n[{agent_name}] [Stopped by user]\n\n", '31')
                    self.chat_window.finish_ai_response()
                    self.chat_window.stop_sending_state()
                
                # Update the user message widget with the saved entry ID
                user_entry_id = content.get("user_entry_id")
                if user_entry_id:
                    self.chat_window.update_last_user_message_id(user_entry_id)
                    print(f"[UI] Updated user message with entry_id: {user_entry_id}")
                
                # Refresh history JSON window if visible
                if self.history_json_window and self.history_json_window.isVisible():
                    self._fetch_history_json_async()
                
                # Refresh memories window if visible (agent may have created/updated memories)
                if self.memories_window and self.memories_window.isVisible():
                    self.memories_window.refresh_content()
            elif event_type == "stream.finished":
                self.chat_window.finish_ai_response()
                self.chat_window.stop_sending_state()
            elif event_type == "response.error":
                error_msg = content
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
