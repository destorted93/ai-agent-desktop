"""Memories JSON viewer window with edit capability."""

from .json_viewer_dialog import JsonViewerDialog


class MemoriesWindow(JsonViewerDialog):
    """Window to display and edit user memories JSON."""
    
    window_title = "User Memories"
    settings_key = "memories_window"
    default_filename_prefix = "memories"
    
    def __init__(self, parent=None, app=None):
        """Initialize the memories window.
        
        Args:
            parent: Parent widget
            app: Application instance for data access
        """
        self._app = app
        # Memories are editable
        super().__init__(parent, editable=True)
    
    def set_app(self, app):
        """Set the application reference."""
        self._app = app
    
    def save_to_source(self, data) -> dict:
        """Save memories data back to storage.
        
        Args:
            data: List of memory dicts to save
            
        Returns:
            Dict with 'status' key ('success' or 'error')
        """
        if not self._app:
            return {"status": "error", "message": "Application not available"}
        
        try:
            result = self._app.set_memories(data)
            return result
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def refresh_content(self):
        """Refresh memories from storage."""
        if not self._app:
            self.set_json_text("[]")
            return
        
        try:
            import json
            memories = self._app.get_memories()
            json_text = json.dumps(memories, indent=2, ensure_ascii=False)
            self.set_json_text(json_text)
        except Exception as e:
            self.set_json_text(f"// Error loading memories: {e}")
