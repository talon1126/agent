from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from app.routers.warehouse.inventory import enrich_batch_row, load_batch_inventory_rows
from app.store import load_json
from app.warehouse_store import WarehouseRepository

from .schemas import PurchaseOrderApproveRequest, PurchaseOrderCreateRequest
from .state import PURCHASE_ORDERS


APPROVAL_STATUS_PENDING = "pending"
APPROVAL_STATUS_APPROVED = "approved"
APPROVAL_STATUS_REJECTED = "rejected"

PROCUREMENT_PURCHASE_ORDER_TABLE_SCHEMA = [
    {"name": "Purchase Order ID", "type": "text"},
    {"name": "Approval Status", "type": "single_select", "options": [
        {"name": APPROVAL_STATUS_PENDING, "color": 24},
        {"name": APPROVAL_STATUS_APPROVED, "color": 28},
        {"name": APPROVAL_STATUS_REJECTED, "color": 17},
    ]},
    {"name": "Payment Status", "type": "single_select", "options": [
        {"name": "unpaid", "color": 24},
        {"name": "paid", "color": 28},
    ]},
    {"name": "Warehouse Sync Status", "type": "single_select", "options": [
        {"name": "pending_arrival", "color": 24},
        {"name": "arrived_unsynced", "color": 17},
        {"name": "synced", "color": 28},
    ]},
    {"name": "Supplier Name", "type": "text"},
    {"name": "Warehouse", "type": "text"},
    {"name": "Warehouse ID", "type": "text"},
    {"name": "Location", "type": "text"},
    {"name": "Reason", "type": "text"},
    {"name": "Sync Inventory", "type": "text"},
    {"name": "Quantity", "type": "number"},
    {"name": "Unit Price", "type": "number"},
    {"name": "Currency", "type": "text"},
    {"name": "Estimated Total Price", "type": "number"},
    {"name": "Lead Time Days", "type": "number"},
    {"name": "Estimated Arrival Date", "type": "text"},
    {"name": "Arrived At", "type": "text"},
    {"name": "Created By", "type": "text"},
    {"name": "Created At", "type": "text"},
    {"name": "Updated At", "type": "text"},
]


def next_purchase_order_id(repository: WarehouseRepository | None = None) -> str:
    existing_count = (
        repository.count_purchase_orders()
        if repository
        else len(PURCHASE_ORDERS)
    )
    return f"PO-{existing_count + 5001}"


def build_purchase_order(
    payload: PurchaseOrderCreateRequest,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any]:
    rows = [
        enrich_batch_row(row)
        for row in load_batch_inventory_rows(
            item_id=payload.item_id,
            warehouse_id=payload.warehouse_id,
            location_code=payload.location_code,
        )
    ]
    if not rows:
        raise HTTPException(status_code=404, detail="inventory item not found for purchase order")
    supplier = find_default_supplier(payload.item_id, repository)
    if not supplier:
        raise ValueError("default_supplier_not_found")

    current_quantity = sum(int(row["quantity_available"]) for row in rows)
    reorder_threshold = max(int(row["reorder_threshold"]) for row in rows)
    purchase_quantity = max((reorder_threshold * 2) - current_quantity, reorder_threshold)
    first = rows[0]
    now = datetime.now(UTC).isoformat()
    request_like = {"created_at": now}
    return {
        "purchase_order_id": next_purchase_order_id(repository),
        "approval_status": APPROVAL_STATUS_PENDING,
        "source": payload.source,
        "supplier_id": supplier["supplier_id"],
        "supplier_name": supplier["supplier_name"],
        "warehouse_id": payload.warehouse_id,
        "warehouse_name": first["warehouse_name"],
        "location_code": payload.location_code or "",
        "item_id": payload.item_id,
        "item_name": first["item_name"],
        "category_id": first["category_id"],
        "category_name": first["category_name"],
        "quantity": purchase_quantity,
        "unit_price": int(supplier["unit_price"]),
        "currency": supplier["currency"],
        "estimated_total_price": purchase_quantity * int(supplier["unit_price"]),
        "lead_time_days": int(supplier["lead_time_days"]),
        "estimated_arrival_date": estimated_arrival_date_for_request(request_like, supplier),
        "payment_status": "unpaid",
        "warehouse_sync_status": "pending_arrival",
        "arrived_at": "",
        "reason": payload.reason,
        "created_by": payload.created_by,
        "created_at": now,
        "updated_at": now,
    }


def find_default_supplier(
    item_id: str,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any] | None:
    if repository:
        return repository.get_default_supplier(item_id)
    return next(
        (supplier for supplier in load_json("procurement_suppliers.json") if supplier["item_id"] == item_id),
        None,
    )


def list_purchase_orders(
    *,
    approval_status: str | None = None,
    warehouse_sync_status: str | None = None,
    purchase_order_id: str | None = None,
    payment_status: str | None = None,
    repository: WarehouseRepository | None = None,
) -> list[dict[str, Any]]:
    if repository:
        return repository.list_purchase_orders(
            approval_status=approval_status,
            warehouse_sync_status=warehouse_sync_status,
            purchase_order_id=purchase_order_id,
            payment_status=payment_status,
        )
    return [
        order
        for order in PURCHASE_ORDERS
        if (not approval_status or order.get("approval_status") == approval_status)
        and (not warehouse_sync_status or order["warehouse_sync_status"] == warehouse_sync_status)
        and (not purchase_order_id or order["purchase_order_id"] == purchase_order_id)
        and (not payment_status or order["payment_status"] == payment_status)
    ]


def estimated_arrival_date_for_request(request: dict[str, Any], supplier: dict[str, Any]) -> str:
    created_at = str(request.get("created_at") or "")
    try:
        request_date = datetime.fromisoformat(created_at).date()
    except ValueError:
        request_date = datetime.now(UTC).date()
    return (request_date + timedelta(days=int(supplier["lead_time_days"]))).isoformat()


def create_purchase_order(
    order: dict[str, Any],
    repository: WarehouseRepository | None = None,
) -> dict[str, Any]:
    if repository:
        return repository.create_purchase_order(order)
    PURCHASE_ORDERS.append(order)
    return order


def update_purchase_order_approval_status(
    purchase_order_id: str,
    *,
    approval_status: str,
    updated_by: str,
    reason: str | None = None,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any] | None:
    updated_at = datetime.now(UTC).isoformat()
    if repository:
        return repository.update_purchase_order_approval_status(
            purchase_order_id,
            approval_status=approval_status,
            updated_by=updated_by,
            reason=reason,
            updated_at=updated_at,
        )
    order = get_purchase_order_by_id(purchase_order_id)
    if not order:
        return None
    order["approval_status"] = approval_status
    if reason is not None:
        order["reason"] = reason
    order["updated_by"] = updated_by
    order["updated_at"] = updated_at
    return order


def approve_purchase_order_data(
    purchase_order_id: str,
    payload: PurchaseOrderApproveRequest,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any] | None:
    return update_purchase_order_approval_status(
        purchase_order_id,
        approval_status=APPROVAL_STATUS_APPROVED,
        updated_by=payload.created_by,
        repository=repository,
    )


def get_purchase_order_by_id(
    purchase_order_id: str,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any] | None:
    if repository:
        return repository.get_purchase_order(purchase_order_id)
    return next(
        (order for order in PURCHASE_ORDERS if order["purchase_order_id"] == purchase_order_id),
        None,
    )


def update_purchase_order_warehouse_sync_status(
    purchase_order_id: str,
    *,
    warehouse_sync_status: str,
    arrived_at: str | None = None,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any] | None:
    updated_at = datetime.now(UTC).isoformat()
    if repository:
        return repository.update_purchase_order_warehouse_sync_status(
            purchase_order_id,
            warehouse_sync_status=warehouse_sync_status,
            updated_at=updated_at,
            arrived_at=arrived_at,
        )
    order = get_purchase_order_by_id(purchase_order_id)
    if not order:
        return None
    order["warehouse_sync_status"] = warehouse_sync_status
    if arrived_at is not None:
        order["arrived_at"] = arrived_at
    order["updated_at"] = updated_at
    return order


def confirm_purchase_order_arrival(
    purchase_order_id: str,
    *,
    repository: WarehouseRepository | None = None,
) -> tuple[dict[str, Any] | None, str]:
    order = get_purchase_order_by_id(purchase_order_id, repository)
    if not order:
        return None, "purchase_order_not_found"

    action = "reused" if order.get("warehouse_sync_status") in {"arrived_unsynced", "synced"} else "updated"
    arrived_at = order.get("arrived_at") or datetime.now(UTC).isoformat()
    updated_order = update_purchase_order_warehouse_sync_status(
        purchase_order_id,
        warehouse_sync_status="arrived_unsynced",
        arrived_at=arrived_at,
        repository=repository,
    )
    return updated_order or order, action


def procurement_purchase_order_table_fields(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "Purchase Order ID": order["purchase_order_id"],
        "Approval Status": order["approval_status"],
        "Payment Status": order["payment_status"],
        "Warehouse Sync Status": order["warehouse_sync_status"],
        "Arrived At": order.get("arrived_at") or "",
        "Supplier Name": order["supplier_name"],
        "Warehouse": order["warehouse_name"],
        "Warehouse ID": order["warehouse_id"],
        "Location": order.get("location_code") or "",
        "Reason": order.get("reason") or "",
        "Sync Inventory": "同步库存" if order.get("warehouse_sync_status") == "arrived_unsynced" else "",
        "Quantity": int(order["quantity"]),
        "Unit Price": int(order["unit_price"]),
        "Currency": order["currency"],
        "Estimated Total Price": int(order["estimated_total_price"]),
        "Lead Time Days": int(order["lead_time_days"]),
        "Estimated Arrival Date": order["estimated_arrival_date"],
        "Created By": order["created_by"],
        "Created At": order["created_at"],
        "Updated At": order["updated_at"],
    }
