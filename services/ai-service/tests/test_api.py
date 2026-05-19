from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_message_handle_missing_order_id():
    response = client.post(
        "/message/handle",
        json={
            "message_id": "msg_missing",
            "source": "internal_test",
            "message_type": "text",
            "text": "Where is my order?",
            "created_at": "2026-05-19T10:00:00Z",
        },
    )
    assert response.status_code == 200
    assert response.json()["intent"] == "order_status"
    assert response.json()["answer"] == "Please provide the order ID so I can check the latest status."


def test_message_handle_audio_without_text_uses_mock_transcription():
    response = client.post(
        "/message/handle",
        json={
            "message_id": "msg_audio",
            "source": "internal_test",
            "message_type": "audio",
            "audio_base64": "bW9jayBhdWRpbw==",
            "mime_type": "audio/mpeg",
            "created_at": "2026-05-19T10:00:00Z",
        },
    )
    assert response.status_code == 200
    assert response.json()["normalized_text"] == "Please check order ord_100"
