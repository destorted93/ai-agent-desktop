"""Chat history JSON viewer window."""

from .json_viewer_dialog import JsonViewerDialog


class ChatHistoryJsonWindow(JsonViewerDialog):
    """Window to display raw chat history JSON."""
    
    window_title = "Chat History (JSON)"
    settings_key = "history_json_window"
    default_filename_prefix = "chat_history"
    
    def __init__(self, parent=None, app=None):
        """Initialize the chat history window.
        
        Args:
            parent: Parent widget
            app: Application instance for data access
        """
        self._app = app
        # Chat history is read-only (no in-place editing, but can load from file)
        super().__init__(parent, editable=False)
    
    def set_app(self, app):
        """Set the application reference."""
        self._app = app
    
    def save_to_source(self, data) -> dict:
        """Save chat history data back to storage.
        
        Args:
            data: List of wrapped history entries to save
            
        Returns:
            Dict with 'status' key ('success' or 'error')
        """
        if not self._app:
            return {"status": "error", "message": "Application not available"}
        
        try:
            result = self._app.set_chat_history(data)
            return result
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def refresh_content(self):
        """Refresh chat history from storage."""
        if not self._app:
            self.set_json_text("[]")
            return
        
        try:
            import json
            history = self._app.get_wrapped_chat_history()
            json_text = json.dumps(history, indent=2, ensure_ascii=False)
            self.set_json_text(json_text)
        except Exception as e:
            self.set_json_text(f"// Error loading chat history: {e}")
