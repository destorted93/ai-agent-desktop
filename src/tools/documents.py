"""Document creation tools."""

import os
from typing import List, Dict, Any


class CreateWordDocumentTool:
    """Tool to create Word documents."""
    
    def __init__(self, root_path: str, permission_required: bool = False):
        self.root_path = root_path
        self.permission_required = permission_required
        self.schema = {
            "type": "function",
            "name": "create_word_document",
            "description": (
                "Create a Microsoft Word (.docx) document with the specified paragraphs."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {
                        "type": "string",
                        "description": "File path (ending with .docx) relative to project root.",
                    },
                    "paragraphs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of paragraphs to add.",
                    },
                },
                "required": ["relative_path", "paragraphs"],
                "additionalProperties": False,
            },
        }
    
    def run(self, relative_path: str, paragraphs: List[str]) -> Dict[str, Any]:
        abs_file = os.path.abspath(os.path.join(self.root_path, relative_path))
        abs_root = os.path.abspath(self.root_path)
        
        if not abs_file.startswith(abs_root):
            return {"status": "error", "message": "Path outside project scope"}
        
        try:
            from docx import Document
        except ImportError:
            return {"status": "error", "message": "python-docx not installed"}
        
        try:
            os.makedirs(os.path.dirname(abs_file), exist_ok=True)
            doc = Document()
            for para in paragraphs:
                doc.add_paragraph(para)
            doc.save(abs_file)
            return {"status": "success", "message": f"Created {relative_path}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
