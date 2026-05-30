from fastapi import APIRouter, HTTPException

from .schemas import (
    DeliveryCaseCreateRequest,
    DeliveryExceptionSearchRequest,
    DeliveryStatusLookupRequest,
)
from .service import (
    build_delivery_status,
    create_delivery_case_record,
    extract_order_id,
    get_delivery_order,
    list_delivery_providers as list_delivery_provider_rows,
    list_delivery_orders,
)

router = APIRouter()


@router.get("/delivery/providers")
def list_delivery_providers() -> dict:
    return {"ok": True, "items": list_delivery_provider_rows()}


@router.post("/delivery/status/lookup")
def delivery_status_lookup(payload: DeliveryStatusLookupRequest) -> dict:
    order_id = payload.order_id or extract_order_id(payload.query, payload.text, payload.input)
    if not order_id:
        raise HTTPException(status_code=400, detail="order_id is required")
    return build_delivery_status(get_delivery_order(order_id))


@router.post("/delivery/exceptions/search")
def delivery_exceptions_search(payload: DeliveryExceptionSearchRequest) -> dict:
    items = list_delivery_orders(
        status=payload.status,
        provider_id=payload.provider_id,
        limit=payload.limit,
    )
    return {
        "ok": True,
        "system": "mock-api-delivery",
        "count": len(items),
        "items": items,
    }


@router.post("/delivery/cases")
def create_delivery_case(payload: DeliveryCaseCreateRequest) -> dict:
    case = create_delivery_case_record(payload)
    return {
        "ok": True,
        "system": "mock-api-delivery",
        "case": case,
        "recommendation": "物流 case 已创建，请跟进物流供应商并同步相关部门。",
    }
