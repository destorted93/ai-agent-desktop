from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class ChatHistoryRequestPayload(BaseModel):
    chat_id: str = Field(..., description="Unique identifier for the chat")
    limit: Optional[int] = Field(50, description="Maximum number of chat history entries to retrieve")
    offset: Optional[int] = Field(0, description="Offset for pagination of chat history entries")

class DeleteHistoryRequestPayload(BaseModel):
    chat_id: str = Field(..., description="Unique identifier for the chat")

class UpdateSettingsRequestPayload(BaseModel):
    settings: Dict[str, Any] = Field(..., description="Settings to be updated on the agent service")