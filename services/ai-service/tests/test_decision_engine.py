from app.decision_engine import decide
from app.schemas import EventContext


def test_high_value_refund_requires_approval():
    event = EventContext(
        event_id="evt_refund_high_value",
        event_type="refund_request",
        source="support_inbox",
        customer={"customer_id": "cus_200", "tier": "vip"},
        order={"order_id": "ord_200", "value": 240, "sku": "sku_bag_1"},
        inventory={"sku": "sku_bag_1", "available": 5, "pending_orders": 9, "reorder_threshold": 15},
        shipment={"shipment_id": "ship_200", "status": "delivered", "delay_days": 0},
        message="The premium bag arrived with a broken zipper. I want a full refund.",
        created_at="2026-05-19T05:00:00Z",
    )
    decision = decide(event)
    assert decision.category == "refund_request"
    assert decision.priority == "high"
    assert decision.requires_approval is True
    assert decision.recommended_action == "review_refund_request"
