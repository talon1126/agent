from app.message_agent import extract_order_id, infer_intent, normalize_message_text
from app.message_schemas import MessageRequest


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
