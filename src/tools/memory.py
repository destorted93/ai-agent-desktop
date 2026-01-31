"""Memory management tools for the agent."""

from typing import List, Dict, Any
from ..storage import MemoryManager


class GetMemoriesTool:
    """Tool to retrieve memories."""
    
    schema = {
        "type": "function",
        "name": "get_memories",
        "description": "Retrieve all stored memories with category statistics. Returns memories (each with id, category, text, timestamp) and stats showing count per category (user/self/relationship). Use stats to check memory balance - aim for memories across all categories, not just 'user'.",
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
        result = manager.get_memories_with_stats()
        return {"status": "success", **result}


class CreateMemoryTool:
    """Tool to create new memories."""
    
    schema = {
        "type": "function",
        "name": "create_memory",
        "description": "Store new memories with explicit category. Categories: 'user' (facts about user), 'self' (your feelings, opinions, evolved traits), 'relationship' (dynamic between you, shared moments, how you relate). Aim for balance - don't neglect 'self' and 'relationship' categories.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "memories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": ["user", "self", "relationship"],
                                "description": "Memory category: 'user' (about them), 'self' (about you), 'relationship' (about your bond)"
                            },
                            "text": {
                                "type": "string",
                                "description": "Memory content (50-150 chars, one fact per entry)"
                            }
                        },
                        "required": ["category", "text"],
                        "additionalProperties": False
                    },
                    "description": "List of memories to store, each with category and text."
                }
            },
            "required": ["memories"],
            "additionalProperties": False,
        },
    }
    
    def run(self, memories: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        manager = MemoryManager()
        return [manager.add_memory(m["text"], m["category"]) for m in memories]


class UpdateMemoryTool:
    """Tool to update existing memories."""
    
    schema = {
        "type": "function",
        "name": "update_memory",
        "description": "Modify existing memories by id. Can update text, category, or both.",
        "strict": False,
        "parameters": {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "The memory ID to update."},
                            "text": {"type": "string", "description": "New text content (optional, omit to keep current)."},
                            "category": {
                                "type": "string",
                                "enum": ["user", "self", "relationship"],
                                "description": "New category (optional, omit to keep current)."
                            }
                        },
                        "required": ["id"],
                    },
                    "description": "List of updates. Each must have 'id', optionally 'text' and/or 'category'.",
                }
            },
            "required": ["entries"],
        },
    }
    
    def run(self, entries: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        manager = MemoryManager()
        return [manager.update_memory(e["id"], e.get("text"), e.get("category")) for e in entries]


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
