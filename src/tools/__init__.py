"""Agent tools module."""

from .base import BaseTool
from .memory import (
    GetUserMemoriesTool,
    CreateUserMemoryTool,
    UpdateUserMemoryTool,
    DeleteUserMemoryTool,
)
from .todos import (
    TodoManager,
    GetTodosTool,
    CreateTodoTool,
    UpdateTodoTool,
    DeleteTodoTool,
)
from .filesystem import (
    ReadFolderTool,
    ReadFileTool,
    WriteFileTool,
    CreateFolderTool,
    DeletePathsTool,
    InsertTextTool,
    ReplaceTextTool,
    SearchInFileTool,
    CopyPathsTool,
    RenamePathTool,
    MovePathsTool,
    PathStatTool,
)
from .terminal import RunTerminalTool
from .documents import CreateWordDocumentTool
from .visualization import MultiXYPlotTool
from .web import WebSearchTool, ImageGenerationTool
from .history import (
    GetChatHistoryMetadataTool,
    GetChatHistoryEntryTool,
    DeleteChatHistoryEntriesTool,
    GetChatHistoryStatsTool,
)

__all__ = [
    # Base
    "BaseTool",
    # Memory
    "GetUserMemoriesTool",
    "CreateUserMemoryTool",
    "UpdateUserMemoryTool",
    "DeleteUserMemoryTool",
    # Todos
    "TodoManager",
    "GetTodosTool",
    "CreateTodoTool",
    "UpdateTodoTool",
    "DeleteTodoTool",
    # Filesystem
    "ReadFolderTool",
    "ReadFileTool",
    "WriteFileTool",
    "CreateFolderTool",
    "DeletePathsTool",
    "InsertTextTool",
    "ReplaceTextTool",
    "SearchInFileTool",
    "CopyPathsTool",
    "RenamePathTool",
    "MovePathsTool",
    "PathStatTool",
    # Terminal
    "RunTerminalTool",
    # Documents
    "CreateWordDocumentTool",
    # Visualization
    "MultiXYPlotTool",
    # Web
    "WebSearchTool",
    "ImageGenerationTool",
    # History
    "GetChatHistoryMetadataTool",
    "GetChatHistoryEntryTool",
    "DeleteChatHistoryEntriesTool",
    "GetChatHistoryStatsTool",
]


def get_default_tools(project_root: str, permission_required: bool = False):
    """Get the default set of tools configured for a project.
    
    Args:
        project_root: Root directory for file operations
        permission_required: Whether tools need user permission
        
    Returns:
        List of tool instances
    """
    return [
        # Memory
        GetUserMemoriesTool(),
        CreateUserMemoryTool(),
        UpdateUserMemoryTool(),
        DeleteUserMemoryTool(),
        # Chat History
        GetChatHistoryMetadataTool(),
        GetChatHistoryEntryTool(),
        DeleteChatHistoryEntriesTool(),
        GetChatHistoryStatsTool(),
        # Todos
        GetTodosTool(),
        CreateTodoTool(),
        UpdateTodoTool(),
        DeleteTodoTool(),
        # Filesystem
        ReadFolderTool(root_path=project_root),
        ReadFileTool(root_path=project_root),
        WriteFileTool(root_path=project_root, permission_required=permission_required),
        CreateFolderTool(root_path=project_root, permission_required=permission_required),
        DeletePathsTool(root_path=project_root, permission_required=permission_required),
        InsertTextTool(root_path=project_root, permission_required=permission_required),
        ReplaceTextTool(root_path=project_root, permission_required=permission_required),
        SearchInFileTool(root_path=project_root),
        CopyPathsTool(root_path=project_root),
        RenamePathTool(root_path=project_root),
        MovePathsTool(root_path=project_root),
        PathStatTool(root_path=project_root),
        # Terminal
        # RunTerminalTool(root_path=project_root, permission_required=permission_required),
        # Documents
        # CreateWordDocumentTool(root_path=project_root, permission_required=permission_required),
        # Visualization
        # MultiXYPlotTool(),
        # Web & Media
        # WebSearchTool(),
        # ImageGenerationTool(),
    ]
