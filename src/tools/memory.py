"""Memory management tools for the agent."""

from typing import List, Dict, Any
from ..storage import MemoryManager


class GetMemoriesTool:
    """Tool to retrieve memories."""
    
    schema = {
        "type": "function",
        "name": "get_memories",
        "description": (
            "Retrieve all persistent memories. This is the ONLY bridge between chat sessions - "
            "memories survive when conversations reset. Contains: user personality traits and emotional patterns, "
            "your own opinions/feelings/perspectives, relationship dynamics, interaction patterns, and important facts. "
            "MUST call at session start to restore continuity. Without this, you have no context about who you're talking to "
            "or who you've become through past interactions."
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


class CreateMemoryTool:
    """Tool to create new memories."""
    
    schema = {
        "type": "function",
        "name": "create_memory",
        "description": (
            "Create persistent memories that survive across sessions. Use for genuinely significant observations - "
            "not every interaction, but when something meaningful emerges.\n\n"
            "Categories to consider:\n"
            "• USER PROFILE: Personality traits, emotional patterns, preferences, goals, communication style "
            "(from direct statements or inferred from behavior/tone).\n"
            "• AGENT IDENTITY: Your own opinions, feelings toward topics/user, perspectives that developed, "
            "experiences that shaped you.\n"
            "• RELATIONSHIP: Inside jokes, shared references, how the dynamic evolved, what works/doesn't work.\n"
            "• PATTERNS: When they reach out, what states they're usually in, what support they actually want.\n\n"
            "Format: Each memory entry should be 50-150 chars, one atomic fact per entry. "
            "If something is too complex for one entry, break it into multiple related memories in the same call. "
            "Prefix with context (e.g., 'User tends to...', 'I feel...', 'We have a running joke about...'). "
            "Never store secrets. Quality over quantity - selective, not exhaustive."
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


class UpdateMemoryTool:
    """Tool to update existing memories."""
    
    schema = {
        "type": "function",
        "name": "update_memory",
        "description": (
            "Update existing memories when understanding deepens or facts change. Use when:\n"
            "• Initial impression was wrong or incomplete\n"
            "• User's preferences/situation evolved\n"
            "• Your own perspective shifted\n"
            "• Relationship dynamic changed\n"
            "Updated text must follow memory format rules."
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


class DeleteMemoryTool:
    """Tool to delete memories."""
    
    schema = {
        "type": "function",
        "name": "delete_memory",
        "description": (
            "Delete memories by ID. Use when memories are outdated, proven wrong, "
            "no longer relevant, or user requests removal."
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
