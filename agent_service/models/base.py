from pydantic import BaseModel, Field
from typing import Generic, TypeVar, Any, Optional
from datetime import datetime

T = TypeVar('T')

class Envelope(BaseModel, Generic[T]):
    type: str = Field(..., description="Type of the envelope")
    payload: T = Field(..., description="Payload of the envelope")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

class ErrorPayload(BaseModel):
    message: str = Field(..., description="Error message")
    code: int = Field(..., description="Error code")
    details: dict[str, Any] = Field(default_factory=dict, description="Additional error details")