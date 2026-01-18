"""Chat history persistence with encryption."""

import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from .secure import get_app_data_dir, write_encrypted_json, read_encrypted_json


class ChatHistoryManager:
    """Manages encrypted chat history persistence."""
    
    def __init__(self, file_path: Optional[Path] = None):
        """Initialize the chat history manager.
        
        Args:
            file_path: Custom path for history file (defaults to app data dir)
        """
        self.file_path = file_path or (get_app_data_dir() / "chat_history.enc")
        self.history: List[Dict] = []
        self.generated_images: List[Dict] = []
        self._images_path = get_app_data_dir() / "generated_images.json"
        
        self.load()
    
    def _wrap_entry(self, content: Dict) -> Dict:
        """Wrap a message in metadata envelope."""
        content_json = json.dumps(content, ensure_ascii=False)
        content_size = len(content_json.encode("utf-8"))
        
        # Determine entry type
        if "type" in content:
            entry_type = content["type"]
        elif "role" in content and isinstance(content.get("content"), list):
            first_item = content["content"][0] if content["content"] else {}
            entry_type = first_item.get("type", "unknown") if isinstance(first_item, dict) else "unknown"
        else:
            entry_type = "unknown"
        
        return {
            "id": str(uuid.uuid4()),
            "ts": datetime.now().isoformat(),
            "type": entry_type,
            "size": content_size,
            "content": content,
        }
    
    def _unwrap_entries(self, wrapped_entries: List[Dict]) -> List[Dict]:
        """Extract message objects from wrapped entries."""
        return [entry["content"] for entry in wrapped_entries]
    
    def load(self) -> None:
        """Load history from encrypted file."""
        data = read_encrypted_json(self.file_path)
        self.history = data if isinstance(data, list) else []
        
        # Load generated images
        if self._images_path.exists():
            try:
                self.generated_images = json.loads(self._images_path.read_text("utf-8"))
            except Exception:
                self.generated_images = []
    
    def save(self) -> None:
        """Save history to encrypted file."""
        write_encrypted_json(self.file_path, self.history)
    
    def get_history(self, limit: int = 50, offset: int = 0, chat_id: Optional[str] = None) -> List[Dict]:
        """Get unwrapped message list for API use.
        
        Args:
            limit: Maximum number of entries to return
            offset: Offset from start
            chat_id: Optional chat ID (ignored for now, single chat supported)
        """
        return self._unwrap_entries(self.history)
    
    def clear_history(self, chat_id: Optional[str] = None) -> bool:
        """Clear all history.
        
        Args:
            chat_id: Optional chat ID (ignored for now, single chat supported)
            
        Returns:
            True if successful
        """
        self.history = []
        self.save()
        return True
    
    def get_wrapped_history(self, limit: int = 50, offset: int = 0) -> List[Dict]:
        """Get full wrapped entries with metadata."""
        return self.history
    
    def add_entry(self, entry: Dict) -> str:
        """Add a single entry (wraps automatically)."""
        wrapped = self._wrap_entry(entry)
        self.history.append(wrapped)
        self.save()
        return wrapped["id"]
    
    def append_entries(self, entries: List[Dict]) -> List[str]:
        """Append multiple entries (wraps automatically)."""
        wrapped = [self._wrap_entry(e) for e in entries]
        self.history.extend(wrapped)
        self.save()
        return [e["id"] for e in wrapped]
    
    def delete_entries(self, entry_ids: List[str]) -> Dict[str, Any]:
        """Delete entries by their IDs."""
        if not isinstance(entry_ids, list):
            entry_ids = [entry_ids]
        
        original_count = len(self.history)
        self.history = [e for e in self.history if e["id"] not in entry_ids]
        deleted_count = original_count - len(self.history)
        
        if deleted_count > 0:
            self.save()
        
        return {
            "status": "success",
            "deleted_count": deleted_count,
            "remaining_count": len(self.history),
        }
    
    def get_entry(self, entry_id: str) -> Optional[Dict]:
        """Get a single wrapped entry by ID."""
        for entry in self.history:
            if entry["id"] == entry_id:
                return entry
        return None
    
    def clear(self) -> None:
        """Clear all history."""
        self.history = []
        self.save()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get chat history statistics."""
        total_size = sum(e.get("size", 0) for e in self.history)
        type_counts: Dict[str, int] = {}
        for e in self.history:
            t = e.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
        
        return {
            "total_entries": len(self.history),
            "total_size_bytes": total_size,
            "type_counts": type_counts,
        }
    
    # Generated images methods
    def add_generated_images(self, images: List[Dict]) -> None:
        """Add generated images."""
        if images:
            self.generated_images.extend(images)
            self._save_images()
    
    def get_generated_images(self) -> List[Dict]:
        """Get all generated images."""
        return self.generated_images
    
    def clear_generated_images(self) -> None:
        """Clear all generated images."""
        self.generated_images = []
        self._save_images()
    
    def _save_images(self) -> None:
        """Save generated images to file."""
        self._images_path.parent.mkdir(parents=True, exist_ok=True)
        self._images_path.write_text(
            json.dumps(self.generated_images, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
