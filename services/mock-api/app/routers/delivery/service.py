import re
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from app.routers.warehouse.state import WAREHOUSE_ORDERS, get_warehouse_repository

from .state import DELIVERY_CASES, DELIVERY_PROVIDERS, get_delivery_provider


def extract_order_id(*values: str | None) -> str | None:
    for value in values:
        if not value:
            continue
        match = re.search(r"\b(?:ORD-[0-9A-Za-z_-]+|ord_[0-9A-Za-z_]+)\b", str(value), re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def get_delivery_order(order_id: str) -> dict[str, Any]:
    repository = get_warehouse_repository()
    if repository:
        details = repository.get_order(order_id)
        if details:
            return details["order"]
    order = next((item for item in WAREHOUSE_ORDERS if item["order_id"] == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="delivery_order_not_found")
    return order


def delivery_details_for_order(order: dict[str, Any]) -> dict[str, Any]:
    provider = get_delivery_provider(str(order.get("delivery_provider_id") or ""))
    tracking_no = str(order.get("tracking_no") or "").strip()
    if not tracking_no:
        tracking_no = f"{provider['tracking_prefix']}{str(order['order_id']).replace('-', '')}"
    return {
        "provider_id": provider["provider_id"],
        "provider_name": provider["name"],
        "service_hotline": provider["service_hotline"],
        "courier_phone": order.get("courier_phone") or "",
        "tracking_no": tracking_no,
    }


def delivery_risk_level(order: dict[str, Any]) -> str:
    status = str(order.get("status") or "")
    if status == "已发货":
        return "medium"
    if status in {"已退款", "已退货"}:
        return "high"
    return "low"


def delivery_recommendation(order: dict[str, Any]) -> str:
    status = str(order.get("status") or "")
    if status == "未付款":
        return "订单未付款，物流暂不处理。"
    if status == "待发货":
        return "订单待发货，Warehouse 负责出库发货。"
    if status == "已发货":
        return "订单已发货，Delivery 负责跟进承运商配送进度。"
    if status == "已到货":
        return "订单已到货，物流流程完成。"
    if status in {"已退款", "已退货"}:
        return "订单已进入退款或退货状态，物流流程只保留配送记录。"
    return "订单状态未知，请人工复核。"


def build_delivery_status(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "system": "mock-api-delivery",
        "order": order,
        "delivery": delivery_details_for_order(order),
        "risk_level": delivery_risk_level(order),
        "recommendation": delivery_recommendation(order),
    }


def list_delivery_providers() -> list[dict[str, Any]]:
    repository = get_warehouse_repository()
    if repository:
        items = repository.list_delivery_providers()
        if items:
            return items
    return DELIVERY_PROVIDERS


def list_delivery_orders(status: str | None = None, provider_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    repository = get_warehouse_repository()
    if repository:
        # Delivery only reads the warehouse-owned order model. Warehouse remains
        # the owner of inventory allocation and status transitions.
        rows = repository.list_orders()
    else:
        rows = list(WAREHOUSE_ORDERS)
    normalized_limit = max(min(int(limit or 50), 500), 1)
    items: list[dict[str, Any]] = []
    for order in rows:
        if status and order.get("status") != status:
            continue
        delivery = delivery_details_for_order(order)
        if provider_id and delivery["provider_id"] != provider_id:
            continue
        items.append(
            {
                "order_id": order["order_id"],
                "status": order.get("status"),
                "delivery_provider_id": delivery["provider_id"],
                "delivery_provider_name": delivery["provider_name"],
                "courier_phone": delivery["courier_phone"],
                "tracking_no": delivery["tracking_no"],
                "risk_level": delivery_risk_level(order),
                "recommendation": delivery_recommendation(order),
            }
        )
    return items[:normalized_limit]


def create_delivery_case_record(payload: Any) -> dict[str, Any]:
    order = get_delivery_order(payload.order_id)
    delivery = delivery_details_for_order(order)
    now = datetime.now(UTC).isoformat()
    case = {
        "case_id": f"DCASE-{len(DELIVERY_CASES) + 1:04d}",
        "case_type": payload.case_type,
        "status": "open",
        "order_id": order["order_id"],
        "delivery_provider_id": delivery["provider_id"],
        "delivery_provider_name": delivery["provider_name"],
        "courier_phone": delivery["courier_phone"],
        "tracking_no": delivery["tracking_no"],
        "reason": payload.reason,
        "risk_level": delivery_risk_level(order),
        "created_by": payload.created_by,
        "created_at": now,
        "updated_at": now,
    }
    DELIVERY_CASES.append(case)
    return case
