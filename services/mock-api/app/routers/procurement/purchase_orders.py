from typing import Any

from fastapi import APIRouter, HTTPException

from app.routers.pagination import page_items
from app.routers.warehouse.state import get_warehouse_repository

from .schemas import (
    PurchaseOrderApproveBatchRequest,
    PurchaseOrderApproveRequest,
    PurchaseOrderConfirmArrivalBatchRequest,
    PurchaseOrderCreateRequest,
    PurchaseOrderRejectRequest,
    PurchaseOrderTableRowsRequest,
)
from .service import (
    APPROVAL_STATUS_PENDING,
    PROCUREMENT_PURCHASE_ORDER_TABLE_SCHEMA,
    approve_purchase_order_data,
    build_purchase_order,
    confirm_purchase_order_arrival,
    create_purchase_order,
    list_purchase_orders,
    procurement_purchase_order_table_fields,
    update_purchase_order_approval_status,
)

router = APIRouter()


@router.get("/procurement/purchase-orders")
def list_procurement_purchase_orders(
    approval_status: str | None = None,
    warehouse_sync_status: str | None = None,
    payment_status: str | None = None,
    purchase_order_id: str | None = None,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    items = list_purchase_orders(
        approval_status=approval_status,
        warehouse_sync_status=warehouse_sync_status,
        payment_status=payment_status,
        purchase_order_id=purchase_order_id,
        repository=repository,
    )
    return {"ok": True, "count": len(items), "items": items}


@router.post("/procurement/purchase-orders")
def create_procurement_purchase_order(payload: PurchaseOrderCreateRequest) -> dict[str, Any]:
    repository = get_warehouse_repository()
    try:
        order = build_purchase_order(payload, repository)
    except ValueError as error:
        if str(error) == "default_supplier_not_found":
            raise HTTPException(status_code=400, detail="default supplier not found for item") from error
        raise
    created = create_purchase_order(order, repository)
    return {"ok": True, "purchase_order": created}


@router.post("/procurement/purchase-orders/{purchase_order_id}/approve")
def approve_procurement_purchase_order(
    purchase_order_id: str,
    payload: PurchaseOrderApproveRequest,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    updated = approve_purchase_order_data(purchase_order_id, payload, repository)
    if not updated:
        raise HTTPException(status_code=404, detail="purchase order not found")
    return {"ok": True, "purchase_order": updated}


@router.post("/procurement/purchase-orders/{purchase_order_id}/reject")
def reject_procurement_purchase_order(
    purchase_order_id: str,
    payload: PurchaseOrderRejectRequest,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    updated = update_purchase_order_approval_status(
        purchase_order_id,
        approval_status="rejected",
        updated_by=payload.updated_by,
        reason=payload.reason,
        repository=repository,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="purchase order not found")
    return {"ok": True, "purchase_order": updated}


@router.post("/procurement/purchase-orders/approve-batch")
def approve_procurement_purchase_orders_batch(
    payload: PurchaseOrderApproveBatchRequest,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    target_status = payload.approval_status or APPROVAL_STATUS_PENDING
    pending_orders = list_purchase_orders(
        approval_status=target_status,
        repository=repository,
    )
    approved: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    approve_payload = PurchaseOrderApproveRequest(created_by=payload.created_by)
    for order in pending_orders:
        updated = approve_purchase_order_data(
            order["purchase_order_id"],
            approve_payload,
            repository,
        )
        if not updated:
            errors.append(
                {
                    "purchase_order_id": order["purchase_order_id"],
                    "error": "purchase_order_not_found",
                }
            )
            continue
        approved.append(updated)
    return {
        "ok": True,
        "approval_status": target_status,
        "processed_count": len(pending_orders),
        "approved_count": len(approved),
        "skipped_count": len(errors),
        "approved_purchase_orders": approved,
        "errors": errors,
    }


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
        order, action = confirm_purchase_order_arrival(
            purchase_order_id,
            repository=repository,
        )
        if not order:
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
        "next_action": "Warehouse 后续查询 warehouse_sync_status=arrived_unsynced 的采购单并同步到库存余额和库存流水。",
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
        approval_status=payload.approval_status,
        warehouse_sync_status=payload.warehouse_sync_status,
        payment_status=payload.payment_status,
        purchase_order_id=payload.purchase_order_id,
        repository=repository,
    )
    page, has_more, next_offset = page_items(items, limit=payload.limit, offset=payload.offset)
    return {
        "ok": True,
        "schema_id": "procurement_purchase_orders",
        "count": len(page),
        "has_more": has_more,
        "next_offset": next_offset,
        "items": [
            {
                "purchase_order_id": order["purchase_order_id"],
                "fields": procurement_purchase_order_table_fields(order),
            }
            for order in page
        ],
    }
