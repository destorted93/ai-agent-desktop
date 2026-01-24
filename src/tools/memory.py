"""Memory management tools for the agent."""

from typing import List, Dict, Any
from ..storage import MemoryManager


class GetUserMemoriesTool:
    """Tool to retrieve user memories."""
    
    schema = {
        "type": "function",
        "name": "get_user_memories",
        "description": (
            "Retrieve the user's long-term memories. Entries are concise (50-150 chars) and include "
            "important facts, preferences, explicit requests, and patterns over past interactions. "
            "ALWAYS call silently at conversation start to understand user context."
        ),
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


class CreateUserMemoryTool:
    """Tool to create new user memories."""
    
    schema = {
        "type": "function",
        "name": "create_user_memory",
        "description": (
            "Create one or more memory entries for the user. "
            "Use ONLY for durable, valuable facts: preferences, goals, constraints, ongoing projects, "
            "strong dislikes, or explicit 'remember this' requests. "
            "Format: English; one line; start with 'User ...'; one fact per memory; 50-150 chars. "
            "Never store secrets (passwords, API keys, etc). Avoid duplication."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "texts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "A list of memory texts to save.",
                }
            },
            "required": ["texts"],
            "additionalProperties": False,
        },
    }
    
    def run(self, texts: List[str]) -> List[Dict[str, Any]]:
        manager = MemoryManager()
        return [manager.add_memory(text) for text in texts]


class UpdateUserMemoryTool:
    """Tool to update existing user memories."""
    
    schema = {
        "type": "function",
        "name": "update_user_memory",
        "description": (
            "Update existing user memories. Use when you discover a mistake in stored information, "
            "when facts evolve, or when the user explicitly requests changes. "
            "Updated text must follow all memory rules."
        ),
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
                    "description": "List of memory updates with id and new text.",
                }
            },
            "required": ["entries"],
            "additionalProperties": False,
        },
    }
    
    def run(self, entries: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        manager = MemoryManager()
        return [manager.update_memory(e["id"], e["text"]) for e in entries]


class DeleteUserMemoryTool:
    """Tool to delete user memories."""
    
    schema = {
        "type": "function",
        "name": "delete_user_memory",
        "description": (
            "Delete user memories by their IDs. Use when memories are outdated, incorrect, "
            "or the user requests removal."
        ),
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
