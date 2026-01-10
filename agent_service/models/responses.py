from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class ChatHistoryResponsePayload(BaseModel):
    entries: List[Dict[str, Any]] = Field(..., description="List of chat history entries")
    total_count: Optional[int] = Field(None, description="Total number of entries available")

class OperationResponsePayload(BaseModel):
    success: bool = Field(..., description="Status of the operation")
    message: Optional[str] = Field(None, description="Additional message regarding the operation")