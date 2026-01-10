from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional, List, Dict, Any
from datetime import datetime
import base64

class UserMessagePayload(BaseModel):
    """Initial user message with metadata about incoming data."""
    text: str = Field(..., min_length=1)
    file_paths: List[str] = Field(default_factory=list)
    screenshot_count: int = Field(0, ge=0, le=10)
    chat_id: str
    
    @field_validator('file_paths')
    @classmethod
    def validate_file_paths(cls, v):
        if len(v) > 20:
            raise ValueError("Maximum 20 file paths allowed")
        return v

class ScreenshotPayload(BaseModel):
    """Individual screenshot data."""
    index: int = Field(..., ge=0)
    data: str = Field(..., description="Base64 encoded image data")
    format: Literal["png", "jpg", "jpeg", "webp"] = "png"
    chat_id: str
    
    @field_validator('data')
    @classmethod
    def validate_base64(cls, v):
        try:
            base64.b64decode(v)
            return v
        except Exception:
            raise ValueError("Invalid base64 data")
    
    def get_decoded_data(self) -> bytes:
        """Get decoded image bytes."""
        return base64.b64decode(self.data)

class CancelRequestPayload(BaseModel):
    """Request to cancel ongoing operation."""
    chat_id: str
    reason: Optional[str] = None

# Client message envelopes
class UserMessageEvent(BaseModel):
    type: Literal["user_message"] = "user_message"
    payload: UserMessagePayload
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class ScreenshotEvent(BaseModel):
    type: Literal["screenshot"] = "screenshot"
    payload: ScreenshotPayload
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class CancelEvent(BaseModel):
    type: Literal["cancel"] = "cancel"
    payload: CancelRequestPayload
    timestamp: datetime = Field(default_factory=datetime.utcnow)

# ==================== Server -> Client Messages ====================

class ReasoningPayload(BaseModel):
    """AI reasoning/thinking output."""
    text: str
    step: Optional[int] = None

class DeltaPayload(BaseModel):
    """Incremental text chunk."""
    text: str
    index: int = 0

class FunctionCallPayload(BaseModel):
    """Function/tool call information."""
    name: str
    arguments: Dict[str, Any]
    call_id: Optional[str] = None

class ErrorPayload(BaseModel):
    """Error information."""
    message: str
    code: str = "UNKNOWN_ERROR"
    details: Optional[Dict[str, Any]] = None
    recoverable: bool = False

class StatusPayload(BaseModel):
    """Status update."""
    status: Literal["processing", "completed", "failed", "waiting_screenshots"]
    message: Optional[str] = None
    progress: Optional[float] = Field(None, ge=0, le=100)

class CompletePayload(BaseModel):
    """Completion signal."""
    chat_id: str
    total_tokens: Optional[int] = None
    finish_reason: Optional[str] = None

# Server response envelopes
class ReasoningResponse(BaseModel):
    type: Literal["reasoning"] = "reasoning"
    payload: ReasoningPayload
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class DeltaResponse(BaseModel):
    type: Literal["delta"] = "delta"
    payload: DeltaPayload
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class FunctionCallResponse(BaseModel):
    type: Literal["function_call"] = "function_call"
    payload: FunctionCallPayload
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class ErrorResponse(BaseModel):
    type: Literal["error"] = "error"
    payload: ErrorPayload
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class StatusResponse(BaseModel):
    type: Literal["status"] = "status"
    payload: StatusPayload
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class CompleteResponse(BaseModel):
    type: Literal["complete"] = "complete"
    payload: CompletePayload
    timestamp: datetime = Field(default_factory=datetime.utcnow)