"""Todo management tools for the agent."""

import os
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

from ..storage import get_app_data_dir


class TodoManager:
    """Manages todo items with file persistence."""
    
    def __init__(self, file_path: Optional[Path] = None):
        self.file_path = file_path or (get_app_data_dir() / "todos.json")
        self.todos: List[Dict] = []
        self.load()
    
    def load(self) -> None:
        """Load todos from file."""
        if self.file_path.exists():
            try:
                data = json.loads(self.file_path.read_text("utf-8"))
                self.todos = data if isinstance(data, list) else []
            except Exception:
                self.todos = []
        else:
            self.todos = []
    
    def save(self) -> Dict[str, Any]:
        """Save todos to file."""
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            self.file_path.write_text(
                json.dumps(self.todos, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def get_todos(self) -> List[Dict]:
        """Get all todos."""
        return self.todos
    
    def add_todo(self, text: str, status: str = "new") -> Dict[str, Any]:
        """Add a new todo."""
        try:
            new_id = str(len(self.todos) + 1)
            now = datetime.now()
            todo = {
                "id": new_id,
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M"),
                "text": text,
                "status": status,
            }
            self.todos.append(todo)
            result = self.save()
            if result["status"] == "success":
                return {"status": "success", "id": new_id, "todo": todo}
            return {"status": "error", "message": result.get("message")}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def update_todo(
        self,
        todo_id: str,
        new_text: Optional[str] = None,
        new_status: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update an existing todo."""
        for todo in self.todos:
            if todo["id"] == todo_id:
                updated = False
                if new_text is not None:
                    todo["text"] = new_text
                    updated = True
                if new_status is not None:
                    if isinstance(new_status, bool):
                        todo["status"] = "done" if new_status else "new"
                    else:
                        todo["status"] = new_status
                    updated = True
                if not updated:
                    return {"status": "error", "id": todo_id, "message": "No updates provided"}
                result = self.save()
                if result["status"] == "success":
                    return {"status": "success", "id": todo_id, "todo": todo}
                return {"status": "error", "id": todo_id, "message": result.get("message")}
        return {"status": "error", "id": todo_id, "message": "Todo not found"}
    
    def delete_todos(self, ids: List[str]) -> List[Dict[str, Any]]:
        """Delete todos by IDs."""
        found = {id_ for id_ in ids if any(t["id"] == id_ for t in self.todos)}
        self.todos = [t for t in self.todos if t["id"] not in found]
        
        # Renumber IDs
        for idx, todo in enumerate(self.todos, start=1):
            todo["id"] = str(idx)
        
        result = self.save()
        results = []
        for id_ in ids:
            if id_ in found:
                if result["status"] == "success":
                    results.append({"status": "success", "id": id_})
                else:
                    results.append({"status": "error", "id": id_, "message": result.get("message")})
            else:
                results.append({"status": "error", "id": id_, "message": "Todo not found"})
        return results
    
    def clear(self) -> Dict[str, Any]:
        """Clear all todos."""
        self.todos = []
        return self.save()


class GetTodosTool:
    """Tool to retrieve todos."""
    
    schema = {
        "type": "function",
        "name": "get_todos",
        "description": (
            "Retrieve the current ordered to-do items (id, date, time, text, status). "
            "ALWAYS call before creating new todos or modifying existing ones."
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
        manager = TodoManager()
        return {"status": "success", "todos": manager.get_todos()}


class CreateTodoTool:
    """Tool to create new todos."""
    
    schema = {
        "type": "function",
        "name": "create_todo",
        "description": (
            "Add one or more atomic to-do items. Provide an ordered list of item texts. "
            "ALWAYS call get_todos before creating to avoid duplicates."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "texts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of todo texts to create.",
                }
            },
            "required": ["texts"],
            "additionalProperties": False,
        },
    }
    
    def run(self, texts: List[str]) -> List[Dict[str, Any]]:
        manager = TodoManager()
        return [manager.add_todo(text) for text in texts]


class UpdateTodoTool:
    """Tool to update existing todos."""
    
    schema = {
        "type": "function",
        "name": "update_todo",
        "description": "Update todo items by ID. Can update text and/or status.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Todo ID to update."},
                            "text": {"type": "string", "description": "New text (optional)."},
                            "status": {"type": "string", "description": "New status: 'new' or 'done' (optional)."},
                        },
                        "required": ["id", "text", "status"],
                        "additionalProperties": False,
                    },
                    "description": "List of todo updates.",
                }
            },
            "required": ["entries"],
            "additionalProperties": False,
        },
    }
    
    def run(self, entries: List[Dict]) -> List[Dict[str, Any]]:
        manager = TodoManager()
        return [
            manager.update_todo(e["id"], e.get("text"), e.get("status"))
            for e in entries
        ]


class DeleteTodoTool:
    """Tool to delete todos."""
    
    schema = {
        "type": "function",
        "name": "delete_todo",
        "description": "Delete todo items by their IDs.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of todo IDs to delete.",
                }
            },
            "required": ["ids"],
            "additionalProperties": False,
        },
    }
    
    def run(self, ids: List[str]) -> List[Dict[str, Any]]:
        manager = TodoManager()
        return manager.delete_todos(ids)
