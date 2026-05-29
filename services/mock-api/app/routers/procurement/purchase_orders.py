from typing import Any

from fastapi import APIRouter, HTTPException

from app.routers.warehouse.state import get_warehouse_repository

from .schemas import PurchaseOrderConfirmArrivalBatchRequest, PurchaseOrderTableRowsRequest
from .service import (
    PROCUREMENT_PURCHASE_ORDER_TABLE_SCHEMA,
    confirm_purchase_order_arrival,
    list_purchase_orders,
    procurement_purchase_order_table_fields,
)

router = APIRouter()


@router.get("/procurement/purchase-orders")
def list_procurement_purchase_orders(
    request_id: str | None = None,
    warehouse_sync_status: str | None = None,
    payment_status: str | None = None,
    purchase_order_id: str | None = None,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    items = list_purchase_orders(
        request_id,
        warehouse_sync_status=warehouse_sync_status,
        payment_status=payment_status,
        purchase_order_id=purchase_order_id,
        repository=repository,
    )
    return {"ok": True, "count": len(items), "items": items}


@router.post("/procurement/purchase-orders/confirm-arrival-batch")
def confirm_purchase_order_arrival_batch(
    payload: PurchaseOrderConfirmArrivalBatchRequest,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    purchase_order_ids = []
    for value in payload.purchase_order_ids:
        normalized = str(value).strip().upper()
        if normalized and normalized not in purchase_order_ids:
            purchase_order_ids.append(normalized)
    if not purchase_order_ids:
        raise HTTPException(status_code=400, detail="purchase_order_ids required")

    confirmed_items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for purchase_order_id in purchase_order_ids:
        order, request, action = confirm_purchase_order_arrival(
            purchase_order_id,
            repository=repository,
        )
        if not order or not request:
            errors.append(
                {
                    "purchase_order_id": purchase_order_id,
                    "error": action,
                    "message": action.replace("_", " "),
                }
            )
            continue

        confirmed_items.append(
            {
                "purchase_order_id": order["purchase_order_id"],
                "request_id": order["request_id"],
                "payment_status": order["payment_status"],
                "warehouse_sync_status": order["warehouse_sync_status"],
                "arrived_at": order.get("arrived_at") or "",
                "item_id": order["item_id"],
                "warehouse_id": order["warehouse_id"],
                "warehouse_name": order["warehouse_name"],
                "location_code": order.get("location_code") or "",
                "quantity": int(order["quantity"]),
                "action": action,
            }
        )

    return {
        "ok": True,
        "processed_count": len(purchase_order_ids),
        "confirmed_count": len(confirmed_items),
        "skipped_count": len(errors),
        "confirmed_items": confirmed_items,
        "errors": errors,
        "next_action": "Warehouse 后续查询 warehouse_sync_status=arrived_unsynced 的采购单并同步到库存批次。",
    }


@router.get("/procurement/purchase-orders/table-schema")
def get_procurement_purchase_order_table_schema() -> dict[str, Any]:
    return {
        "ok": True,
        "schema_id": "procurement_purchase_orders",
        "source": "mock-api",
        "fields": PROCUREMENT_PURCHASE_ORDER_TABLE_SCHEMA,
    }


@router.post("/procurement/purchase-orders/table-rows")
def get_procurement_purchase_order_table_rows(
    payload: PurchaseOrderTableRowsRequest,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    items = list_purchase_orders(
        payload.request_id,
        warehouse_sync_status=payload.warehouse_sync_status,
        payment_status=payload.payment_status,
        purchase_order_id=payload.purchase_order_id,
        repository=repository,
    )
    limit = max(min(int(payload.limit or 100), 500), 1)
    items = items[:limit]
    return {
        "ok": True,
        "schema_id": "procurement_purchase_orders",
        "count": len(items),
        "items": [
            {
                "purchase_order_id": order["purchase_order_id"],
                "request_id": order["request_id"],
                "fields": procurement_purchase_order_table_fields(order),
            }
            for order in items
        ],
    }
