from pydantic import BaseModel
from typing import Literal

from .base import Envelope, ErrorPayload
from .requests import *
from .responses import *

from .websocket import (
    # Client -> Server
    UserMessageEvent,
    UserMessagePayload,
    ScreenshotEvent,
    ScreenshotPayload,
    CancelEvent,
    CancelRequestPayload,
    
    # Server -> Client  
    ReasoningResponse,
    ReasoningPayload,
    DeltaResponse,
    DeltaPayload,
    FunctionCallResponse,
    FunctionCallPayload,
    ErrorResponse as WSErrorResponse,
    ErrorPayload as WSErrorPayload,
    StatusResponse,
    StatusPayload,
    CompleteResponse,
    CompletePayload,
)

class ChatHistoryRequest(BaseModel):
    type: Literal["chat_history_request"]
    payload: ChatHistoryRequestPayload

class ChatHistoryResponse(BaseModel):
    type: Literal["chat_history_response"]
    payload: ChatHistoryResponsePayload

class DeleteHistoryRequest(BaseModel):
    type: Literal["delete_history_request"]
    payload: DeleteHistoryRequestPayload

class UpdateSettingsRequest(BaseModel):
    type: Literal["update_settings_request"]
    payload: UpdateSettingsRequestPayload

class OperationResponse(BaseModel):
    type: Literal["operation_response"]
    payload: OperationResponsePayload

class ErrorResponse(BaseModel):
    type: Literal["error_response"]
    payload: ErrorPayload

__all__ = [
    "ChatHistoryRequestPayload",
    "ChatHistoryResponsePayload",
    "DeleteHistoryRequestPayload",
    "UpdateSettingsRequestPayload",
    "OperationResponsePayload",
    "ErrorPayload",

    "ChatHistoryRequest",
    "ChatHistoryResponse",
    "DeleteHistoryRequest",
    "UpdateSettingsRequest",
    "OperationResponse",
    "ErrorResponse",

    # WebSocket
    "UserMessageEvent",
    "UserMessagePayload",
    "ScreenshotEvent",
    "ScreenshotPayload",
    "CancelEvent",
    "CancelRequestPayload",
    "ReasoningResponse",
    "ReasoningPayload",
    "DeltaResponse",
    "DeltaPayload",
    "FunctionCallResponse",
    "FunctionCallPayload",
    "WSErrorResponse",
    "WSErrorPayload",
    "StatusResponse",
    "StatusPayload",
    "CompleteResponse",
    "CompletePayload",
]