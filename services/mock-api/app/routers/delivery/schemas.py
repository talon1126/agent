from pydantic import BaseModel


class DeliveryStatusLookupRequest(BaseModel):
    order_id: str | None = None
    query: str | None = None
    text: str | None = None
    input: str | None = None


class DeliveryExceptionSearchRequest(BaseModel):
    status: str | None = None
    provider_id: str | None = None
    limit: int = 50


class DeliveryCaseCreateRequest(BaseModel):
    order_id: str
    case_type: str = "delivery_follow_up"
    reason: str = "delivery follow-up requested"
    created_by: str = "delivery-agent"
