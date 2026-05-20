from typing import Any, Literal

from pydantic import BaseModel, Field

MessageType = Literal["text", "audio"]
Intent = Literal["order_status", "unknown"]


class MessageRequest(BaseModel):
    message_id: str
    source: str
    message_type: MessageType
    text: str | None = None
    transcript: str | None = None
    audio_url: str | None = None
    audio_base64: str | None = None
    mime_type: str | None = None
    customer_id: str | None = None
    order_id: str | None = None
    created_at: str


class TranscriptionResult(BaseModel):
    provider: str
    model: str
    transcript: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class ToolCall(BaseModel):
    tool_name: str
    input: dict[str, Any]
    output: dict[str, Any] | None = None
    status: Literal["succeeded", "failed", "skipped"]
    error: str | None = None


class MessageAgentResponse(BaseModel):
    message_id: str
    normalized_text: str
    intent: Intent
    tool_calls: list[ToolCall] = Field(default_factory=list)
    answer: str
    requires_human: bool
    confidence: float = Field(ge=0, le=1)
    transcription: TranscriptionResult | None = None
    error: str | None = None


class AfterSalesFastPathRequest(BaseModel):
    message_id: str
    session_id: str
    text: str
    order_id: str | None = None
    chat_id: str | None = None
    sender_id: str | None = None


class AfterSalesFastPathResponse(BaseModel):
    message_id: str
    session_id: str
    input_text: str
    handled: bool
    reason: str
    answer: str
    order_id: str | None = None
    chat_id: str | None = None
    sender_id: str | None = None
    confidence: float = Field(ge=0, le=1)
    tool_calls: list[ToolCall] = Field(default_factory=list)
