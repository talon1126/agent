import os
import re

import httpx

from app.message_schemas import MessageAgentResponse, MessageRequest, ToolCall
from app.order_status_tool import get_order_status
from app.transcription import transcribe_audio

ORDER_ID_PATTERN = re.compile(r"\bord_[0-9A-Za-z]+\b", re.IGNORECASE)
ORDER_STATUS_KEYWORDS = (
    "order",
    "订单",
    "物流",
    "delivery",
    "shipment",
    "tracking",
    "where",
)


def extract_order_id(text: str) -> str | None:
    match = ORDER_ID_PATTERN.search(text)
    return match.group(0).lower() if match else None


def infer_intent(text: str) -> str:
    lowered = text.lower()
    if any(keyword in lowered for keyword in ORDER_STATUS_KEYWORDS):
        return "order_status"
    return "unknown"


def normalize_message_text(request: MessageRequest) -> str:
    if request.message_type == "text":
        return (request.text or "").strip()
    return (request.transcript or request.text or "").strip()


def handle_message(
    request: MessageRequest,
    *,
    mock_api_url: str,
    http_client: httpx.Client | None = None,
) -> MessageAgentResponse:
    transcription = None
    normalized_text = normalize_message_text(request)

    if request.message_type == "audio" and not normalized_text:
        provider = os.getenv("TRANSCRIPTION_PROVIDER", "mock")
        model = os.getenv("TRANSCRIPTION_MODEL", "qwen3.6plus")
        transcription = transcribe_audio(
            provider=provider,
            model=model,
            audio_url=request.audio_url,
            audio_base64=request.audio_base64,
            mime_type=request.mime_type,
        )
        if transcription.error:
            return MessageAgentResponse(
                message_id=request.message_id,
                normalized_text="",
                intent="unknown",
                answer=transcription.error,
                requires_human=True,
                confidence=0.0,
                transcription=transcription,
                error=transcription.error,
            )
        normalized_text = transcription.transcript or ""

    intent = infer_intent(normalized_text)
    if intent != "order_status":
        return MessageAgentResponse(
            message_id=request.message_id,
            normalized_text=normalized_text,
            intent="unknown",
            answer=(
                "I can currently help check order status. Please provide an "
                "order status question with an order ID."
            ),
            requires_human=False,
            confidence=0.5,
            transcription=transcription,
        )

    order_id = request.order_id or extract_order_id(normalized_text)
    if not order_id:
        return MessageAgentResponse(
            message_id=request.message_id,
            normalized_text=normalized_text,
            intent="order_status",
            answer="Please provide the order ID so I can check the latest status.",
            requires_human=False,
            confidence=0.7,
            transcription=transcription,
        )

    try:
        tool_output = get_order_status(
            order_id=order_id,
            mock_api_url=mock_api_url,
            http_client=http_client,
        )
    except Exception as exc:
        return MessageAgentResponse(
            message_id=request.message_id,
            normalized_text=normalized_text,
            intent="order_status",
            tool_calls=[
                ToolCall(
                    tool_name="get_order_status",
                    input={"order_id": order_id},
                    status="failed",
                    error=str(exc),
                )
            ],
            answer=(
                "I could not retrieve the order status. A human teammate "
                "should review this request."
            ),
            requires_human=True,
            confidence=0.2,
            transcription=transcription,
            error=str(exc),
        )

    return MessageAgentResponse(
        message_id=request.message_id,
        normalized_text=normalized_text,
        intent="order_status",
        tool_calls=[
            ToolCall(
                tool_name="get_order_status",
                input={"order_id": order_id},
                output=tool_output,
                status="succeeded",
            )
        ],
        answer=tool_output["summary"],
        requires_human=False,
        confidence=0.9,
        transcription=transcription,
    )
