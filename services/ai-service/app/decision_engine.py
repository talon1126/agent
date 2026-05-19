from app.schemas import DecisionOutput, EventContext


def decide(event: EventContext) -> DecisionOutput:
    if event.event_type == "refund_request":
        value = float((event.order or {}).get("value", 0))
        tier = (event.customer or {}).get("tier", "standard")
        requires_approval = value >= 100 or tier == "vip"
        return DecisionOutput(
            event_id=event.event_id,
            category="refund_request",
            priority="high" if requires_approval else "medium",
            recommended_action="review_refund_request" if requires_approval else "offer_refund",
            requires_approval=requires_approval,
            confidence=0.86,
            explanation="Refund policy requires approval for high-value orders or VIP customers.",
            draft_response="Thanks for sharing the issue. We are reviewing the order and will follow up shortly.",
            internal_task_summary=f"Review refund request for order {(event.order or {}).get('order_id', 'unknown')}.",
            policy_references=["Refunds: high-value and VIP compensation require approval"],
        )
    if event.event_type == "logistics_delay":
        return DecisionOutput(
            event_id=event.event_id,
            category="logistics_delay",
            priority="high",
            recommended_action="offer_shipping_credit",
            requires_approval=(event.customer or {}).get("tier") == "vip",
            confidence=0.84,
            explanation="Shipping credit is recommended when delivery delay exceeds 5 days.",
            draft_response="We are sorry for the delivery delay. We checked the shipment and will help resolve this promptly.",
            internal_task_summary="Create logistics follow-up case and notify support.",
            policy_references=["Logistics: delays above 5 days can receive shipping credit"],
        )
    if event.event_type == "bad_review":
        return DecisionOutput(
            event_id=event.event_id,
            category="bad_review",
            priority="high",
            recommended_action="draft_recovery_reply",
            requires_approval=True,
            confidence=0.81,
            explanation="Public bad reviews require brand-risk approval before final response.",
            draft_response="We are sorry about your experience and would like to make this right.",
            internal_task_summary="Review public response draft before publishing.",
            policy_references=["Reviews: public bad reviews require approval"],
        )
    inventory = event.inventory or {}
    available = int(inventory.get("available", 0))
    pending = int(inventory.get("pending_orders", 0))
    threshold = int(inventory.get("reorder_threshold", 0))
    high_risk = available - pending < 0 or available < threshold
    return DecisionOutput(
        event_id=event.event_id,
        category="low_stock",
        priority="high" if high_risk else "medium",
        recommended_action="create_reorder_alert" if high_risk else "monitor_inventory",
        requires_approval=False,
        confidence=0.9,
        explanation="Available stock is below threshold or cannot cover pending orders.",
        draft_response="",
        internal_task_summary=f"Create procurement alert for SKU {inventory.get('sku', 'unknown')}.",
        policy_references=["Inventory: stock below reorder threshold should trigger procurement alert"],
    )
