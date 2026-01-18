import os

class CreateWordDocumentTool:
    schema = {
        "type": "function",
        "name": "create_word_document",
        "description": (
            "Create a Microsoft Word (.docx) document at the specified path relative to the project root (where main.py is called). "
            "Provide the filename (ending with .docx) and a list of paragraphs as strings. "
            "The tool will create the document, add each paragraph, and save it. "
            "Use this tool to generate reports, notes, or formatted documents for project use. "
            "Safety: Only create documents within the project scope. Never overwrite system or hidden files."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "relative_path": {"type": "string", "description": "The file path (ending with .docx) relative to the project root."},
                "paragraphs": {"type": "array", "items": {"type": "string", "description": "Paragraph text to add to the document."}, "description": "A list of paragraphs to add to the document."},
            },
            "required": ["relative_path", "paragraphs"],
            "additionalProperties": False,
        },
    }

    def __init__(self, root_path, permission_required=True):
        self.root_path = root_path
        self.permission_required = permission_required

    def run(self, relative_path, paragraphs):
        file_path = os.path.join(self.root_path, relative_path)
        abs_file_path = os.path.abspath(file_path)
        abs_root_path = os.path.abspath(self.root_path)
        if not abs_file_path.startswith(abs_root_path):
            return {"status": "error", "message": "File path is outside the project scope."}
        folder = os.path.dirname(abs_file_path)
        if self.permission_required:
            permission = input(f"Create Word document '{relative_path}'? Proceed? (y/n): ")
            if permission.lower() != 'y':
                return {"status": "error", "message": "Word document creation cancelled by user."}
        if not os.path.exists(folder):
            os.makedirs(folder)
        try:
            from docx import Document
        except ImportError:
            return {"status": "error", "message": "python-docx package not installed."}
        try:
            document = Document()
            for para in paragraphs:
                document.add_paragraph(para)
            document.save(abs_file_path)
            return {"status": "success", "message": f"Word document '{relative_path}' created successfully."}
        except Exception as e:
            return {"status": "error", "message": str(e)}
