from typing import Any, Literal

from pydantic import BaseModel, Field

EventType = Literal["refund_request", "logistics_delay", "bad_review", "low_stock"]
Priority = Literal["low", "medium", "high"]


class EventContext(BaseModel):
    event_id: str
    event_type: EventType
    source: str
    customer: dict[str, Any] | None = None
    order: dict[str, Any] | None = None
    inventory: dict[str, Any] | None = None
    shipment: dict[str, Any] | None = None
    message: str
    created_at: str


class DecisionOutput(BaseModel):
    event_id: str
    category: EventType
    priority: Priority
    recommended_action: str
    requires_approval: bool
    confidence: float = Field(ge=0, le=1)
    explanation: str
    draft_response: str
    internal_task_summary: str
    policy_references: list[str]
