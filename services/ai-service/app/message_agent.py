import re

from app.message_schemas import MessageRequest

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
