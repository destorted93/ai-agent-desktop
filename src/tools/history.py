"""Chat history tools for the agent."""

from typing import List, Dict, Any
from ..storage import ChatHistoryManager


class GetChatHistoryMetadataTool:
    """Tool to get chat history metadata."""
    
    schema = {
        "type": "function",
        "name": "get_chat_history_metadata",
        "description": "Get metadata about chat history entries (IDs, timestamps, types, sizes).",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum entries to return.",
                },
            },
            "required": ["limit"],
            "additionalProperties": False,
        },
    }
    
    def run(self, limit: int = 50) -> Dict[str, Any]:
        manager = ChatHistoryManager()
        entries = manager.get_wrapped_history(limit=limit)
        
        # Return metadata only, not content
        metadata = [
            {
                "id": e["id"],
                "ts": e["ts"],
                "type": e["type"],
                "size": e["size"],
            }
            for e in entries
        ]
        
        return {"status": "success", "entries": metadata, "total": len(metadata)}


class GetChatHistoryEntryTool:
    """Tool to get a specific chat history entry."""
    
    schema = {
        "type": "function",
        "name": "get_chat_history_entry",
        "description": "Get a specific chat history entry by ID.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "entry_id": {
                    "type": "string",
                    "description": "The entry ID to retrieve.",
                },
            },
            "required": ["entry_id"],
            "additionalProperties": False,
        },
    }
    
    def run(self, entry_id: str) -> Dict[str, Any]:
        manager = ChatHistoryManager()
        entry = manager.get_entry(entry_id)
        
        if entry:
            return {"status": "success", "entry": entry}
        return {"status": "error", "message": "Entry not found"}


class DeleteChatHistoryEntriesTool:
    """Tool to delete chat history entries."""
    
    schema = {
        "type": "function",
        "name": "delete_chat_history_entries",
        "description": "Delete chat history entries by their IDs.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "entry_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of entry IDs to delete.",
                },
            },
            "required": ["entry_ids"],
            "additionalProperties": False,
        },
    }
    
    def run(self, entry_ids: List[str]) -> Dict[str, Any]:
        manager = ChatHistoryManager()
        return manager.delete_entries(entry_ids)


class GetChatHistoryStatsTool:
    """Tool to get chat history statistics."""
    
    schema = {
        "type": "function",
        "name": "get_chat_history_stats",
        "description": "Get statistics about chat history (total entries, size, type counts).",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    }
    
    def run(self, **kwargs) -> Dict[str, Any]:
        manager = ChatHistoryManager()
        stats = manager.get_stats()
        return {"status": "success", **stats}
