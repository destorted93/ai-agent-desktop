"""User memory persistence with encryption."""

from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Literal

from .secure import get_app_data_dir, write_encrypted_json, read_encrypted_json


# Valid memory categories
MemoryCategory = Literal["user", "self", "relationship"]
VALID_CATEGORIES = {"user", "self", "relationship"}


class MemoryManager:
    """Manages encrypted user memory persistence."""
    
    def __init__(self, file_path: Optional[Path] = None):
        """Initialize the memory manager.
        
        Args:
            file_path: Custom path for memory file (defaults to app data dir)
        """
        self.file_path = file_path or (get_app_data_dir() / "memories.enc")
        self.memories: List[Dict] = []
        self.load()
    
    def load(self) -> None:
        """Load memories from encrypted file."""
        data = read_encrypted_json(self.file_path)
        self.memories = data if isinstance(data, list) else []
    
    def save(self) -> Dict[str, Any]:
        """Save memories to encrypted file."""
        try:
            write_encrypted_json(self.file_path, self.memories)
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def get_memories(self) -> List[Dict]:
        """Get all memories."""
        return self.memories
    
    def get_memories_with_stats(self) -> Dict[str, Any]:
        """Get all memories with category statistics.
        
        Returns:
            Dict with 'memories' list and 'stats' showing count per category
        """
        stats = {"user": 0, "self": 0, "relationship": 0}
        for memory in self.memories:
            category = memory.get("category", "user")
            if category in stats:
                stats[category] += 1
        return {
            "memories": self.memories,
            "stats": stats,
            "total": len(self.memories)
        }
    
    def add_memory(self, text: str, category: str = "user") -> Dict[str, Any]:
        """Add a new memory.
        
        Args:
            text: Memory content
            category: One of 'user', 'self', or 'relationship'
        """
        try:
            # Validate category
            if category not in VALID_CATEGORIES:
                return {"status": "error", "message": f"Invalid category '{category}'. Must be one of: {', '.join(VALID_CATEGORIES)}"}
            
            new_id = str(len(self.memories) + 1)
            now = datetime.now()
            memory = {
                "id": new_id,
                "category": category,
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M"),
                "text": text,
            }
            self.memories.append(memory)
            result = self.save()
            if result["status"] == "success":
                return {"status": "success", "id": new_id, "memory": memory}
            return {"status": "error", "message": result.get("message", "Failed to save")}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def update_memory(self, memory_id: str, new_text: Optional[str] = None, new_category: Optional[str] = None) -> Dict[str, Any]:
        """Update an existing memory.
        
        Args:
            memory_id: ID of memory to update
            new_text: New text content (optional)
            new_category: New category (optional)
        """
        # Validate category if provided
        if new_category is not None and new_category not in VALID_CATEGORIES:
            return {"status": "error", "id": memory_id, "message": f"Invalid category '{new_category}'. Must be one of: {', '.join(VALID_CATEGORIES)}"}
        
        for memory in self.memories:
            if memory["id"] == memory_id:
                if new_text is not None:
                    memory["text"] = new_text
                if new_category is not None:
                    memory["category"] = new_category
                result = self.save()
                if result["status"] == "success":
                    return {"status": "success", "id": memory_id, "memory": memory}
                return {"status": "error", "id": memory_id, "message": result.get("message")}
        return {"status": "error", "id": memory_id, "message": "Memory not found"}
    
    def delete_memories(self, ids: List[str]) -> List[Dict[str, Any]]:
        """Delete memories by IDs."""
        found = {id_ for id_ in ids if any(m["id"] == id_ for m in self.memories)}
        self.memories = [m for m in self.memories if m["id"] not in found]
        
        # Renumber IDs
        for idx, memory in enumerate(self.memories, start=1):
            memory["id"] = str(idx)
        
        result = self.save()
        results = []
        for id_ in ids:
            if id_ in found:
                if result["status"] == "success":
                    results.append({"status": "success", "id": id_})
                else:
                    results.append({"status": "error", "id": id_, "message": result.get("message")})
            else:
                results.append({"status": "error", "id": id_, "message": "Memory not found"})
        return results
    
    def clear(self) -> Dict[str, Any]:
        """Clear all memories."""
        self.memories = []
        return self.save()
