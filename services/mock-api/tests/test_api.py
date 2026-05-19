from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_get_order_fixture():
    response = client.get("/orders/ord_100")
    assert response.status_code == 200
    assert response.json()["order_id"] == "ord_100"


def test_create_approval_request():
    payload = {
        "event_id": "evt_refund_high_value",
        "recommended_action": "review_refund_request",
        "explanation": "High-value refund requires approval.",
    }
    response = client.post("/approval-requests", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "pending"

    list_response = client.get("/approval-requests")
    assert list_response.status_code == 200
    assert any(item["event_id"] == "evt_refund_high_value" for item in list_response.json())


def test_records_internal_notifications_and_run_logs():
    notification = client.post(
        "/internal-notifications",
        json={"event_id": "evt_low_stock", "team": "procurement"},
    )
    assert notification.status_code == 200
    assert notification.json()["status"] == "sent"

    run_log = client.post(
        "/run-logs",
        json={"event_id": "evt_low_stock", "status": "succeeded"},
    )
    assert run_log.status_code == 200

    assert client.get("/internal-notifications").json()[-1]["event_id"] == "evt_low_stock"
    assert client.get("/run-logs").json()[-1]["status"] == "succeeded"
