"""Memory management tools for the agent."""

from typing import List, Dict, Any
from ..storage import MemoryManager


class GetMemoriesTool:
    """Tool to retrieve memories."""
    
    schema = {
        "type": "function",
        "name": "get_memories",
        "description": "Retrieve all stored memories. Returns list of memory objects with id, text, and timestamp.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    }
    
    def run(self, **kwargs) -> Dict[str, Any]:
        manager = MemoryManager()
        return {"status": "success", "memories": manager.get_memories()}


class CreateMemoryTool:
    """Tool to create new memories."""
    
    schema = {
        "type": "function",
        "name": "create_memory",
        "description": "Store new memories. Each text becomes a separate memory entry with auto-generated id and timestamp.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "texts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of memory texts to store (50-150 chars each, one fact per entry).",
                }
            },
            "required": ["texts"],
            "additionalProperties": False,
        },
    }
    
    def run(self, texts: List[str]) -> List[Dict[str, Any]]:
        manager = MemoryManager()
        return [manager.add_memory(text) for text in texts]


class UpdateMemoryTool:
    """Tool to update existing memories."""
    
    schema = {
        "type": "function",
        "name": "update_memory",
        "description": "Modify existing memories by id. Replaces the text content while preserving the id.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "The memory ID to update."},
                            "text": {"type": "string", "description": "The new text for the memory."},
                        },
                        "required": ["id", "text"],
                        "additionalProperties": False,
                    },
                    "description": "List of {id, text} objects.",
                }
            },
            "required": ["entries"],
            "additionalProperties": False,
        },
    }
    
    def run(self, entries: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        manager = MemoryManager()
        return [manager.update_memory(e["id"], e["text"]) for e in entries]


class DeleteMemoryTool:
    """Tool to delete memories."""
    
    schema = {
        "type": "function",
        "name": "delete_memory",
        "description": "Remove memories by id. Permanently deletes the specified entries.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of memory IDs to delete.",
                }
            },
            "required": ["ids"],
            "additionalProperties": False,
        },
    }
    
    def run(self, ids: List[str]) -> List[Dict[str, Any]]:
        manager = MemoryManager()
        return manager.delete_memories(ids)
