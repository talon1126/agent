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
