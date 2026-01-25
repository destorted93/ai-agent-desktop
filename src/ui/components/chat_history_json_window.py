"""Chat history JSON viewer window."""

from .json_viewer_dialog import JsonViewerDialog


class ChatHistoryJsonWindow(JsonViewerDialog):
    """Window to display raw chat history JSON."""
    
    window_title = "Chat History (JSON)"
    settings_key = "history_json_window"
    default_filename_prefix = "chat_history"
    
    def __init__(self, parent=None):
        # Chat history is read-only (no in-place editing)
        super().__init__(parent, editable=False)
    
    def save_to_source(self, data) -> dict:
        """Chat history doesn't support direct editing."""
        return {"status": "error", "message": "Chat history editing is not supported"}
    
    def refresh_content(self):
        """Refresh not needed for chat history (parent widget handles this)."""
        pass
