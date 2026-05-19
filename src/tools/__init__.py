"""Agent tools module."""


from ..appcore.runtime_context import Runtime
from .base import BaseTool
from .memory import (
    GetMemoriesTool,
    SearchMemoriesTool,
    CreateMemoryTool,
    UpdateMemoryTool,
    DeleteMemoryTool,
)
from .subagents import RunSubagentTool, GetSubagentsListTool
from .consult_inner_voice import ConsultInnerVoiceTool
from .inner_loop import InnerLoopTool
from .filesystem import (
    ReadFolderTool,
    ReadFileTool,
    WriteFileTool,
    CreateFolderTool,
    DeletePathsTool,
    ReplaceTextTool,
    DeleteLinesTool,
    TransferLinesTool,
    FsSearchTool,
    CopyPathsTool,
    RenamePathTool,
    MovePathsTool,
    PathStatTool,
    FsListTransactionsTool,
    FsUndoTransactionTool,
    ImagesGetTool,
)
from .rag import RagListCollectionsTool, RagSearchTool, SearchConfluenceTool
from .web import WebSearchTool
from .canvas import (
    CanvasCreateTool,
    CanvasListTool,
    CanvasSetCurrentTool,
    CanvasGetTool,
    CanvasGetImageTool,
    CanvasExportPngTool,
    CanvasImportImageTool,
    CanvasSampleColorTool,
    CanvasBrushSetTool,
    CanvasStrokeTool,
    CanvasLineTool,
    CanvasShapeTool,
    CanvasFillTool,
    CanvasUndoTool,
    CanvasRedoTool,
    CanvasDeleteTool,
    CanvasRenameTool,
    CanvasDuplicateTool,
    CanvasLayerCreateTool,
    CanvasLayerUpdateTool,
    CanvasLayerDeleteTool,
)
from .session import SetSessionMetaTool, RunSummaryTool
from .group_session import GroupPassTool, AskHumanTool

__all__ = [
    # Base
    "BaseTool",
    # Memory
    "GetMemoriesTool",
    "SearchMemoriesTool",
    "CreateMemoryTool",
    "UpdateMemoryTool",
    "DeleteMemoryTool",
    # Inner Voice
    "ConsultInnerVoiceTool",
    # Inner Loop
    "InnerLoopTool",
    # Sub-agents
    "RunSubagentTool",
    "GetSubagentsListTool",
    # Filesystem
    "ReadFolderTool",
    "ReadFileTool",
    "WriteFileTool",
    "CreateFolderTool",
    # Agents
    "DeletePathsTool",
    "ReplaceTextTool",
    "DeleteLinesTool",
    "TransferLinesTool",
    "FsSearchTool",
    "CopyPathsTool",
    "RenamePathTool",
    "MovePathsTool",
    "PathStatTool",
    "FsListTransactionsTool",
    "FsUndoTransactionTool",
    "ImagesGetTool",
    # RAG
    "RagListCollectionsTool",
    "RagSearchTool",
    # Confluence
    "SearchConfluenceTool",
    # Web
    "WebSearchTool",
    # Canvas
    "CanvasCreateTool",
    "CanvasListTool",
    "CanvasSetCurrentTool",
    "CanvasGetTool",
    "CanvasGetImageTool",
    "CanvasExportPngTool",
    "CanvasImportImageTool",
    "CanvasSampleColorTool",
    "CanvasBrushSetTool",
    "CanvasStrokeTool",
    "CanvasLineTool",
    "CanvasShapeTool",
    "CanvasFillTool",
    "CanvasUndoTool",
    "CanvasRedoTool",
    "CanvasDeleteTool",
    "CanvasRenameTool",
    "CanvasDuplicateTool",
    "CanvasLayerCreateTool",
    "CanvasLayerUpdateTool",
    "CanvasLayerDeleteTool",
    # Session
    "SetSessionMetaTool",
    "RunSummaryTool",
    # Group Session
    "GroupPassTool",
    "AskHumanTool",
]


def get_default_tools():
    """Get the default set of tools configured for a project.
 
    Returns:
        List of tool instances
    """

    tools = [
        # Memory
        GetMemoriesTool(),
        SearchMemoriesTool(),
        CreateMemoryTool(),
        UpdateMemoryTool(),
        DeleteMemoryTool(),
        # RAG
        RagListCollectionsTool(),
        RagSearchTool(),
        # Confluence
        SearchConfluenceTool(),
        # Canvas
        CanvasCreateTool(),
        CanvasListTool(),
        CanvasSetCurrentTool(),
        CanvasGetTool(),
        CanvasGetImageTool(),
        CanvasExportPngTool(),
        CanvasImportImageTool(),
        CanvasSampleColorTool(),
        CanvasBrushSetTool(),
        CanvasStrokeTool(),
        CanvasLineTool(),
        CanvasShapeTool(),
        CanvasFillTool(),
        CanvasUndoTool(),
        CanvasRedoTool(),
        CanvasDeleteTool(),
        CanvasRenameTool(),
        CanvasDuplicateTool(),
        CanvasLayerCreateTool(),
        CanvasLayerUpdateTool(),
        CanvasLayerDeleteTool(),
        # Agents
        ConsultInnerVoiceTool(),
        GetSubagentsListTool(),
        RunSubagentTool(),
        # InnerLoopTool(),
        # Session
        SetSessionMetaTool(),
        RunSummaryTool(),
        # Group Session
        GroupPassTool(),
        AskHumanTool(),
        # Filesystem
        ReadFolderTool(),
        ReadFileTool(),
        ImagesGetTool(),
        WriteFileTool(),
        CreateFolderTool(),
        DeletePathsTool(),
        ReplaceTextTool(),
        DeleteLinesTool(),
        TransferLinesTool(),
        FsSearchTool(),
        CopyPathsTool(),
        RenamePathTool(),
        MovePathsTool(),
        PathStatTool(),
        # Web & Media
        WebSearchTool(),
    ]

    # Fs revisions: keep these tools present for consistency; they return a clean error if
    # FsRevisionStore is unavailable.
    tools.extend([
        FsListTransactionsTool(),
        FsUndoTransactionTool(),
    ])

    return tools
