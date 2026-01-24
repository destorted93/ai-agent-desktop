"""Filesystem tools for the agent."""

import os
import re
import shutil
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional


def _is_safe_path(root: str, path: str) -> bool:
    """Check if path is within the root directory."""
    abs_root = os.path.abspath(root)
    abs_path = os.path.abspath(os.path.join(root, path))
    return abs_path.startswith(abs_root)


class ReadFolderTool:
    """Tool to read folder contents."""
    
    def __init__(self, root_path: str):
        self.root_path = root_path
        self.schema = {
            "type": "function",
            "name": "read_folder",
            "description": "List contents of a directory relative to the project root.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {
                        "type": "string",
                        "description": "Path relative to project root. Use '.' for root.",
                    },
                },
                "required": ["relative_path"],
                "additionalProperties": False,
            },
        }
    
    def run(self, relative_path: str) -> Dict[str, Any]:
        if not _is_safe_path(self.root_path, relative_path):
            return {"status": "error", "message": "Path outside project scope"}
        
        full_path = os.path.join(self.root_path, relative_path)
        if not os.path.isdir(full_path):
            return {"status": "error", "message": "Not a directory"}
        
        if not os.path.exists(full_path):
            return {"status": "error", "message": "Folder not found"}
        
        items = os.listdir(full_path)
        return {"status": "success", "items": items}


class ReadFileTool:
    """Tool to read file contents."""
    
    def __init__(self, root_path: str):
        self.root_path = root_path
        self.schema = {
            "type": "function",
            "name": "read_file",
            "description": "Read contents of a file. Optionally specify line range.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {
                        "type": "string",
                        "description": "File path relative to project root.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Start line (1-based, optional).",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "End line (1-based, inclusive, optional).",
                    },
                },
                "required": ["relative_path", "start_line", "end_line"],
                "additionalProperties": False,
            },
        }
    
    def run(
        self,
        relative_path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None
    ) -> Dict[str, Any]:
        if not _is_safe_path(self.root_path, relative_path):
            return {"status": "error", "message": "Path outside project scope"}
        
        full_path = os.path.join(self.root_path, relative_path)
        if not os.path.isfile(full_path):
            return {"status": "error", "message": "File not found"}
        
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            
            total_lines = len(lines)
            
            if start_line is not None and end_line is not None:
                start_idx = max(0, start_line - 1)
                end_idx = min(total_lines, end_line)
                content = "".join(lines[start_idx:end_idx])
            else:
                content = "".join(lines)
            
            return {
                "status": "success",
                "content": content,
                "total_lines": total_lines,
                "path": relative_path,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}


class WriteFileTool:
    """Tool to write file contents."""
    
    def __init__(self, root_path: str, permission_required: bool = False):
        self.root_path = root_path
        self.permission_required = permission_required
        self.schema = {
            "type": "function",
            "name": "write_file",
            "description": "Write content to a file (creates or overwrites).",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {
                        "type": "string",
                        "description": "File path relative to project root.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write.",
                    },
                },
                "required": ["relative_path", "content"],
                "additionalProperties": False,
            },
        }
    
    def run(self, relative_path: str, content: str) -> Dict[str, Any]:
        if not _is_safe_path(self.root_path, relative_path):
            return {"status": "error", "message": "Path outside project scope"}
        
        full_path = os.path.join(self.root_path, relative_path)
        
        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"status": "success", "message": f"Written to {relative_path}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CreateFolderTool:
    """Tool to create folders."""
    
    def __init__(self, root_path: str, permission_required: bool = False):
        self.root_path = root_path
        self.permission_required = permission_required
        self.schema = {
            "type": "function",
            "name": "create_folder",
            "description": "Create a directory (and parents if needed).",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {
                        "type": "string",
                        "description": "Directory path relative to project root.",
                    },
                },
                "required": ["relative_path"],
                "additionalProperties": False,
            },
        }
    
    def run(self, relative_path: str) -> Dict[str, Any]:
        if not _is_safe_path(self.root_path, relative_path):
            return {"status": "error", "message": "Path outside project scope"}
        
        full_path = os.path.join(self.root_path, relative_path)
        
        try:
            os.makedirs(full_path, exist_ok=True)
            return {"status": "success", "message": f"Created {relative_path}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class DeletePathsTool:
    """Tool to delete files or folders."""
    
    def __init__(self, root_path: str, permission_required: bool = False):
        self.root_path = root_path
        self.permission_required = permission_required
        self.schema = {
            "type": "function",
            "name": "delete_paths",
            "description": "Delete files or directories.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of paths relative to project root.",
                    },
                },
                "required": ["paths"],
                "additionalProperties": False,
            },
        }
    
    def run(self, paths: List[str]) -> List[Dict[str, Any]]:
        results = []
        for path in paths:
            if not _is_safe_path(self.root_path, path):
                results.append({"path": path, "status": "error", "message": "Outside scope"})
                continue
            
            full_path = os.path.join(self.root_path, path)
            try:
                if os.path.isdir(full_path):
                    shutil.rmtree(full_path)
                elif os.path.exists(full_path):
                    os.remove(full_path)
                else:
                    results.append({"path": path, "status": "error", "message": "Not found"})
                    continue
                results.append({"path": path, "status": "success"})
            except Exception as e:
                results.append({"path": path, "status": "error", "message": str(e)})
        return results


class InsertTextTool:
    """Tool to insert text at a specific location in a file."""
    
    def __init__(self, root_path: str, permission_required: bool = False):
        self.root_path = root_path
        self.permission_required = permission_required
        self.schema = {
            "type": "function",
            "name": "insert_text",
            "description": "Insert text at a specific line in a file.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {"type": "string", "description": "File path."},
                    "line": {"type": "integer", "description": "Line number (1-based) to insert before."},
                    "text": {"type": "string", "description": "Text to insert."},
                },
                "required": ["relative_path", "line", "text"],
                "additionalProperties": False,
            },
        }
    
    def run(self, relative_path: str, line: int, text: str) -> Dict[str, Any]:
        if not _is_safe_path(self.root_path, relative_path):
            return {"status": "error", "message": "Path outside project scope"}
        
        full_path = os.path.join(self.root_path, relative_path)
        
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            
            insert_idx = max(0, min(len(lines), line - 1))
            lines.insert(insert_idx, text if text.endswith("\n") else text + "\n")
            
            with open(full_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            
            return {"status": "success", "message": f"Inserted at line {line}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class ReplaceTextTool:
    """Tool to replace text in a file."""
    
    def __init__(self, root_path: str, permission_required: bool = False):
        self.root_path = root_path
        self.permission_required = permission_required
        self.schema = {
            "type": "function",
            "name": "replace_text",
            "description": "Replace occurrences of old_text with new_text in a file.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {"type": "string", "description": "File path."},
                    "old_text": {"type": "string", "description": "Text to find."},
                    "new_text": {"type": "string", "description": "Replacement text."},
                    "count": {"type": "integer", "description": "Max replacements (default: all)."},
                },
                "required": ["relative_path", "old_text", "new_text", "count"],
                "additionalProperties": False,
            },
        }
    
    def run(
        self,
        relative_path: str,
        old_text: str,
        new_text: str,
        count: Optional[int] = None
    ) -> Dict[str, Any]:
        if not _is_safe_path(self.root_path, relative_path):
            return {"status": "error", "message": "Path outside project scope"}
        
        full_path = os.path.join(self.root_path, relative_path)
        
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            if old_text not in content:
                return {"status": "error", "message": "Text not found"}
            
            if count is not None:
                new_content = content.replace(old_text, new_text, count)
            else:
                new_content = content.replace(old_text, new_text)
            
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            
            return {"status": "success", "message": "Text replaced"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class SearchInFileTool:
    """Tool to search for text in files."""
    
    def __init__(self, root_path: str):
        self.root_path = root_path
        self.schema = {
            "type": "function",
            "name": "search_in_file",
            "description": "Search for text or regex pattern in a file.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {"type": "string", "description": "File path."},
                    "pattern": {"type": "string", "description": "Search pattern."},
                    "is_regex": {"type": "boolean", "description": "Treat as regex (default: false)."},
                },
                "required": ["relative_path", "pattern", "is_regex"],
                "additionalProperties": False,
            },
        }
    
    def run(
        self,
        relative_path: str,
        pattern: str,
        is_regex: bool = False
    ) -> Dict[str, Any]:
        if not _is_safe_path(self.root_path, relative_path):
            return {"status": "error", "message": "Path outside project scope"}
        
        full_path = os.path.join(self.root_path, relative_path)
        
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            
            matches = []
            regex = re.compile(pattern, re.IGNORECASE) if is_regex else None
            
            for i, line in enumerate(lines, 1):
                if is_regex:
                    if regex.search(line):
                        matches.append({"line": i, "content": line.rstrip()})
                else:
                    if pattern.lower() in line.lower():
                        matches.append({"line": i, "content": line.rstrip()})
            
            return {"status": "success", "matches": matches, "count": len(matches)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class CopyPathsTool:
    """Tool to copy files or folders."""
    
    def __init__(self, root_path: str):
        self.root_path = root_path
        self.schema = {
            "type": "function",
            "name": "copy_paths",
            "description": "Copy files or directories.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "operations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string"},
                                "destination": {"type": "string"},
                            },
                            "required": ["source", "destination"],
                            "additionalProperties": False,
                        },
                        "description": "List of copy operations.",
                    },
                },
                "required": ["operations"],
                "additionalProperties": False,
            },
        }
    
    def run(self, operations: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        results = []
        for op in operations:
            src, dst = op["source"], op["destination"]
            if not _is_safe_path(self.root_path, src) or not _is_safe_path(self.root_path, dst):
                results.append({"source": src, "status": "error", "message": "Outside scope"})
                continue
            
            src_path = os.path.join(self.root_path, src)
            dst_path = os.path.join(self.root_path, dst)
            
            try:
                if os.path.isdir(src_path):
                    shutil.copytree(src_path, dst_path)
                else:
                    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                    shutil.copy2(src_path, dst_path)
                results.append({"source": src, "destination": dst, "status": "success"})
            except Exception as e:
                results.append({"source": src, "status": "error", "message": str(e)})
        return results


class RenamePathTool:
    """Tool to rename a file or folder."""
    
    def __init__(self, root_path: str):
        self.root_path = root_path
        self.schema = {
            "type": "function",
            "name": "rename_path",
            "description": "Rename a file or directory.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "old_path": {"type": "string", "description": "Current path."},
                    "new_path": {"type": "string", "description": "New path."},
                },
                "required": ["old_path", "new_path"],
                "additionalProperties": False,
            },
        }
    
    def run(self, old_path: str, new_path: str) -> Dict[str, Any]:
        if not _is_safe_path(self.root_path, old_path) or not _is_safe_path(self.root_path, new_path):
            return {"status": "error", "message": "Path outside project scope"}
        
        src = os.path.join(self.root_path, old_path)
        dst = os.path.join(self.root_path, new_path)
        
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.rename(src, dst)
            return {"status": "success", "message": f"Renamed {old_path} to {new_path}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class MovePathsTool:
    """Tool to move files or folders."""
    
    def __init__(self, root_path: str):
        self.root_path = root_path
        self.schema = {
            "type": "function",
            "name": "move_paths",
            "description": "Move files or directories.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "operations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string"},
                                "destination": {"type": "string"},
                            },
                            "required": ["source", "destination"],
                            "additionalProperties": False,
                        },
                        "description": "List of move operations.",
                    },
                },
                "required": ["operations"],
                "additionalProperties": False,
            },
        }
    
    def run(self, operations: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        results = []
        for op in operations:
            src, dst = op["source"], op["destination"]
            if not _is_safe_path(self.root_path, src) or not _is_safe_path(self.root_path, dst):
                results.append({"source": src, "status": "error", "message": "Outside scope"})
                continue
            
            src_path = os.path.join(self.root_path, src)
            dst_path = os.path.join(self.root_path, dst)
            
            try:
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                shutil.move(src_path, dst_path)
                results.append({"source": src, "destination": dst, "status": "success"})
            except Exception as e:
                results.append({"source": src, "status": "error", "message": str(e)})
        return results


class PathStatTool:
    """Tool to get file/folder statistics."""
    
    def __init__(self, root_path: str):
        self.root_path = root_path
        self.schema = {
            "type": "function",
            "name": "path_stat",
            "description": "Get information about a file or directory.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {"type": "string", "description": "Path to check."},
                },
                "required": ["relative_path"],
                "additionalProperties": False,
            },
        }
    
    def run(self, relative_path: str) -> Dict[str, Any]:
        if not _is_safe_path(self.root_path, relative_path):
            return {"status": "error", "message": "Path outside project scope"}
        
        full_path = os.path.join(self.root_path, relative_path)
        
        if not os.path.exists(full_path):
            return {"status": "error", "message": "Path not found"}
        
        try:
            stat = os.stat(full_path)
            return {
                "status": "success",
                "path": relative_path,
                "exists": True,
                "is_file": os.path.isfile(full_path),
                "is_dir": os.path.isdir(full_path),
                "size": stat.st_size,
                "modified": stat.st_mtime,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
