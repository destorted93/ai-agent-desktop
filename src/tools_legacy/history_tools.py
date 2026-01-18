from chat_history import ChatHistoryManager

class GetChatHistoryMetadataTool:
    schema = {
        "type": "function",
        "name": "get_chat_history_metadata",
        "description": (
            "Retrieve metadata about all chat history entries (id, timestamp, type, size) without the full content. "
            "Use this to analyze conversation flow, identify large entries, or find SPECIFIC messages to manage. "
            "Returns a list with: id (unique identifier), ts (ISO timestamp), type (input_text/output_text/reasoning/function_call/function_call_output/message), size (bytes). "
            "\n\n"
            "Types: input_text=user messages, output_text=assistant text, reasoning=thinking process, function_call=tool calls, function_call_output=tool results, message=complete assistant response."
            "\n\n"
            "IMPORTANT: Do NOT call this tool if user wants to delete ALL chat history. "
            "For 'delete all', 'clear history', 'remove everything' requests, call delete_chat_history_entries with delete_all=true directly. "
            "Only use this tool when you need to identify SPECIFIC entries to delete (e.g., 'delete all reasoning entries', 'delete messages from today')."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    }

    def run(self, **kwargs):
        chat_history_manager = ChatHistoryManager()
        wrapped_history = chat_history_manager.get_wrapped_history()
        
        metadata = [
            {
                "id": entry["id"],
                "ts": entry["ts"],
                "type": entry["type"],
                "size": entry["size"]
            }
            for entry in wrapped_history
        ]
        
        return {
            "status": "success",
            "count": len(metadata),
            "entries": metadata
        }


class GetChatHistoryEntryTool:
    schema = {
        "type": "function",
        "name": "get_chat_history_entry",
        "description": (
            "Retrieve the full content of a specific chat history entry by its ID. "
            "Use after calling get_chat_history_metadata to inspect specific messages. "
            "Returns the complete wrapped entry including all metadata and content."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "entry_id": {
                    "type": "string",
                    "description": "The unique ID of the history entry to retrieve."
                }
            },
            "required": ["entry_id"],
            "additionalProperties": False,
        },
    }

    def run(self, entry_id):
        chat_history_manager = ChatHistoryManager()
        entry = chat_history_manager.get_entry_by_id(entry_id)
        
        if entry:
            return {
                "status": "success",
                "entry": entry
            }
        else:
            return {
                "status": "error",
                "message": f"Entry with id '{entry_id}' not found."
            }


class DeleteChatHistoryEntriesTool:
    schema = {
        "type": "function",
        "name": "delete_chat_history_entries",
        "description": (
            "Delete chat history entries by their IDs, or delete ALL entries at once. "
            "Use with EXTREME CAUTION - only when user explicitly requests deletion or when entries are genuinely problematic. "
            "Deleting entries can break conversation context. Always confirm with user before deletion. "
            "\n\n"
            "Two modes:\n"
            "1. DELETE SPECIFIC ENTRIES: Set entry_ids to a list of IDs to delete (use after get_chat_history_metadata)\n"
            "2. DELETE ALL: Set delete_all=true to clear entire chat history WITHOUT calling get_chat_history_metadata first\n"
            "\n"
            "When user asks to 'delete all', 'clear history', 'remove everything', use delete_all=true for efficiency.\n"
            "When user asks to delete specific types or messages, use get_chat_history_metadata first, then pass entry_ids.\n"
            "\n"
            "Best practice: use this to remove redundant, erroneous, or sensitive entries after careful review."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "entry_ids": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "description": "ID of an entry to delete."
                    },
                    "description": "List of entry IDs to delete from chat history. Optional if delete_all is true."
                },
                "delete_all": {
                    "type": "boolean",
                    "description": "Set to true to delete ALL chat history entries at once. When true, entry_ids is ignored."
                }
            },
            "required": ["entry_ids", "delete_all"],
            "additionalProperties": False,
        },
    }

    def run(self, entry_ids=None, delete_all=False):
        chat_history_manager = ChatHistoryManager()
        
        if delete_all:
            # Delete all entries efficiently
            initial_count = len(chat_history_manager.history)
            chat_history_manager.clear_history()
            return {
                "status": "success",
                "deleted_count": initial_count,
                "remaining_count": 0,
                "message": f"Deleted all {initial_count} entries from chat history."
            }
        elif entry_ids:
            # Delete specific entries by ID
            result = chat_history_manager.delete_entries_by_ids(entry_ids)
            return result
        else:
            # No action specified
            return {
                "status": "error",
                "message": "Must provide either entry_ids or set delete_all=true."
            }


class GetChatHistoryStatsTool:
    schema = {
        "type": "function",
        "name": "get_chat_history_stats",
        "description": (
            "Get statistical overview of the chat history: total entries, size distribution by type, "
            "oldest/newest timestamps, and total storage size. "
            "Useful for understanding conversation scope and identifying optimization opportunities."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    }

    def run(self, **kwargs):
        chat_history_manager = ChatHistoryManager()
        wrapped_history = chat_history_manager.get_wrapped_history()
        
        if not wrapped_history:
            return {
                "status": "success",
                "total_entries": 0,
                "total_size_bytes": 0,
                "stats_by_type": {},
                "oldest_entry": None,
                "newest_entry": None
            }
        
        total_size = sum(entry["size"] for entry in wrapped_history)
        stats_by_type = {}
        
        for entry in wrapped_history:
            entry_type = entry["type"]
            if entry_type not in stats_by_type:
                stats_by_type[entry_type] = {"count": 0, "total_size": 0}
            stats_by_type[entry_type]["count"] += 1
            stats_by_type[entry_type]["total_size"] += entry["size"]
        
        # Get oldest and newest timestamps
        timestamps = [entry["ts"] for entry in wrapped_history]
        oldest = min(timestamps)
        newest = max(timestamps)
        
        return {
            "status": "success",
            "total_entries": len(wrapped_history),
            "total_size_bytes": total_size,
            "total_size_kb": round(total_size / 1024, 2),
            "stats_by_type": stats_by_type,
            "oldest_entry": oldest,
            "newest_entry": newest
        }
