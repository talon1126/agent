import httpx

from app.message_agent import handle_message
from app.message_agent import extract_order_id, infer_intent, normalize_message_text
from app.message_schemas import MessageRequest
from app.transcription import transcribe_audio


def test_extracts_order_id_from_text():
    assert extract_order_id("Please check order ord_100 for me") == "ord_100"


def test_uses_explicit_order_id_first():
    request = MessageRequest(
        message_id="msg_1",
        source="internal_test",
        message_type="text",
        text="Where is my order?",
        order_id="ord_100",
        created_at="2026-05-19T10:00:00Z",
    )
    assert normalize_message_text(request) == "Where is my order?"
    assert infer_intent(request.text or "") == "order_status"


def test_audio_request_uses_transcript_text():
    request = MessageRequest(
        message_id="msg_audio_1",
        source="internal_test",
        message_type="audio",
        transcript="Please check ord_100",
        mime_type="audio/mpeg",
        created_at="2026-05-19T10:00:00Z",
    )
    assert normalize_message_text(request) == "Please check ord_100"


def test_mock_transcription_returns_deterministic_text():
    result = transcribe_audio(
        provider="mock",
        model="mock-transcriber",
        audio_url=None,
        audio_base64="bW9jayBhdWRpbw==",
        mime_type="audio/mpeg",
    )
    assert result.transcript == "Please check order ord_100"
    assert result.provider == "mock"
    assert result.error is None


def test_qwen_transcription_without_endpoint_returns_configuration_error(monkeypatch):
    monkeypatch.delenv("QWEN_API_ENDPOINT", raising=False)
    monkeypatch.delenv("QWEN_API_KEY", raising=False)

    result = transcribe_audio(
        provider="qwen",
        model="qwen3.6plus",
        audio_url="https://example.com/audio.mp3",
        audio_base64=None,
        mime_type="audio/mpeg",
    )
    assert result.transcript is None
    assert (
        result.error
        == "Qwen transcription is not configured. Provide QWEN_API_ENDPOINT and QWEN_API_KEY."
    )


def test_handle_message_calls_order_status_tool():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orders/ord_100":
            return httpx.Response(
                200,
                json={
                    "order_id": "ord_100",
                    "status": "paid",
                    "shipment_id": "ship_100",
                },
            )
        if request.url.path == "/shipments/ship_100":
            return httpx.Response(
                200,
                json={
                    "shipment_id": "ship_100",
                    "status": "in_transit",
                    "estimated_delivery": "2026-05-22",
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mock-api")
    request = MessageRequest(
        message_id="msg_1",
        source="internal_test",
        message_type="text",
        text="Where is order ord_100?",
        created_at="2026-05-19T10:00:00Z",
    )

    response = handle_message(request, mock_api_url="http://mock-api", http_client=client)

    assert response.intent == "order_status"
    assert response.requires_human is False
    assert response.tool_calls[0].tool_name == "get_order_status"
    assert response.tool_calls[0].status == "succeeded"
    assert "ord_100" in response.answer
    assert "in_transit" in response.answer


def test_handle_message_requests_order_id_when_missing():
    request = MessageRequest(
        message_id="msg_missing",
        source="internal_test",
        message_type="text",
        text="Where is my order?",
        created_at="2026-05-19T10:00:00Z",
    )

    response = handle_message(request, mock_api_url="http://mock-api")

    assert response.intent == "order_status"
    assert response.requires_human is False
    assert response.tool_calls == []
    assert response.answer == "Please provide the order ID so I can check the latest status."
