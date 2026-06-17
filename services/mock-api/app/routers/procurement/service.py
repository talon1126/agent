from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from app.routers.warehouse.inventory import enrich_batch_row, load_batch_inventory_rows
from app.store import load_json
from app.warehouse_store import WarehouseRepository

from .schemas import ReplenishmentApproveRequest, ReplenishmentRequestCreate
from .state import PURCHASE_ORDERS, REPLENISHMENT_REQUESTS


REPLENISHMENT_STATUS_UNAPPROVED = "未审批"
REPLENISHMENT_STATUS_APPROVED = "已审批"

_LEGACY_REPLENISHMENT_STATUS_MAP = {
    "pending_procurement_review": REPLENISHMENT_STATUS_UNAPPROVED,
    "rejected": REPLENISHMENT_STATUS_UNAPPROVED,
    "purchase_order_created": REPLENISHMENT_STATUS_APPROVED,
    "purchase_order_draft_created": REPLENISHMENT_STATUS_APPROVED,
}


def normalize_replenishment_status(status: str | None) -> str | None:
    if status is None:
        return None
    stripped = str(status).strip()
    return _LEGACY_REPLENISHMENT_STATUS_MAP.get(stripped, stripped)


PROCUREMENT_REPLENISHMENT_REQUEST_TABLE_SCHEMA = [
    {"name": "Request ID", "type": "text"},
    {"name": "Status", "type": "single_select", "options": [
        {"name": REPLENISHMENT_STATUS_UNAPPROVED, "color": 24},
        {"name": REPLENISHMENT_STATUS_APPROVED, "color": 28},
    ]},
    {"name": "Source", "type": "text"},
    {"name": "Warehouse", "type": "text"},
    {"name": "Warehouse ID", "type": "text"},
    {"name": "Location", "type": "text"},
    {"name": "Category", "type": "text"},
    {"name": "Item Name", "type": "text"},
    {"name": "Current Quantity", "type": "number"},
    {"name": "Reorder Threshold", "type": "number"},
    {"name": "Suggested Quantity", "type": "number"},
    {"name": "Reason", "type": "text"},
    {"name": "Created By", "type": "text"},
    {"name": "Created At", "type": "text"},
    {"name": "Updated At", "type": "text"},
    {"name": "Last Synced At", "type": "text"},
    {"name": "Sync Status", "type": "single_select", "options": [
        {"name": "synced", "color": 28},
        {"name": "pending", "color": 24},
        {"name": "failed", "color": 17},
    ]},
    {"name": "Source Version", "type": "text"},
]

PROCUREMENT_PURCHASE_ORDER_TABLE_SCHEMA = [
    {"name": "Purchase Order ID", "type": "text"},
    {"name": "Request ID", "type": "text"},
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
    {"name": "Last Synced At", "type": "text"},
    {"name": "Sync Status", "type": "single_select", "options": [
        {"name": "synced", "color": 28},
        {"name": "pending", "color": 24},
        {"name": "failed", "color": 17},
    ]},
    {"name": "Source Version", "type": "text"},
]


def next_replenishment_request_id(repository: WarehouseRepository | None = None) -> str:
    existing_count = (
        repository.count_replenishment_requests()
        if repository
        else len(REPLENISHMENT_REQUESTS)
    )
    return f"REQ-{existing_count + 1001}"


def next_purchase_order_id(repository: WarehouseRepository | None = None) -> str:
    existing_count = (
        repository.count_purchase_orders()
        if repository
        else len(PURCHASE_ORDERS)
    )
    return f"PO-{existing_count + 5001}"


def build_replenishment_request(
    payload: ReplenishmentRequestCreate,
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
        raise HTTPException(status_code=404, detail="inventory item not found for replenishment")

    current_quantity = sum(int(row["quantity_available"]) for row in rows)
    reorder_threshold = max(int(row["reorder_threshold"]) for row in rows)
    suggested_quantity = max((reorder_threshold * 2) - current_quantity, reorder_threshold)
    first = rows[0]
    return {
        "request_id": next_replenishment_request_id(repository),
        "source": payload.source,
        "status": REPLENISHMENT_STATUS_UNAPPROVED,
        "warehouse_id": payload.warehouse_id,
        "warehouse_name": first["warehouse_name"],
        "location_code": payload.location_code,
        "item_id": payload.item_id,
        "item_name": first["item_name"],
        "category_id": first["category_id"],
        "category_name": first["category_name"],
        "current_quantity": current_quantity,
        "reorder_threshold": reorder_threshold,
        "suggested_quantity": suggested_quantity,
        "reason": payload.reason,
        "created_by": payload.created_by,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }


def find_replenishment_request(
    request_id: str,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any] | None:
    if repository:
        return repository.get_replenishment_request(request_id)
    return next(
        (request for request in REPLENISHMENT_REQUESTS if request["request_id"] == request_id),
        None,
    )


def update_replenishment_request_status(
    request_id: str,
    *,
    status: str,
    reason: str | None = None,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any] | None:
    updated_at = datetime.now(UTC).isoformat()
    if repository:
        return repository.update_replenishment_request(
            request_id,
            status=status,
            reason=reason,
            updated_at=updated_at,
        )
    request = find_replenishment_request(request_id)
    if not request:
        return None
    request["status"] = status
    if reason is not None:
        request["reason"] = reason
    request["updated_at"] = updated_at
    return request


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
    request_id: str | None = None,
    *,
    warehouse_sync_status: str | None = None,
    purchase_order_id: str | None = None,
    payment_status: str | None = None,
    repository: WarehouseRepository | None = None,
) -> list[dict[str, Any]]:
    if repository:
        return repository.list_purchase_orders(
            request_id=request_id,
            warehouse_sync_status=warehouse_sync_status,
            purchase_order_id=purchase_order_id,
            payment_status=payment_status,
        )
    return [
        order
        for order in PURCHASE_ORDERS
        if (not request_id or order["request_id"] == request_id)
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
    request: dict[str, Any],
    supplier: dict[str, Any],
    payload: ReplenishmentApproveRequest,
    repository: WarehouseRepository | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    order = {
        "purchase_order_id": next_purchase_order_id(repository),
        "request_id": request["request_id"],
        "supplier_id": supplier["supplier_id"],
        "supplier_name": supplier["supplier_name"],
        "item_id": request["item_id"],
        "warehouse_id": request["warehouse_id"],
        "warehouse_name": request["warehouse_name"],
        "location_code": request.get("location_code") or "",
        "quantity": int(request["suggested_quantity"]),
        "unit_price": int(supplier["unit_price"]),
        "currency": supplier["currency"],
        "estimated_total_price": int(request["suggested_quantity"]) * int(supplier["unit_price"]),
        "lead_time_days": int(supplier["lead_time_days"]),
        "estimated_arrival_date": estimated_arrival_date_for_request(request, supplier),
        "payment_status": "unpaid",
        "warehouse_sync_status": "pending_arrival",
        "arrived_at": "",
        "created_by": payload.created_by,
        "created_at": now,
        "updated_at": now,
    }
    if repository:
        return repository.create_purchase_order(order)
    PURCHASE_ORDERS.append(order)
    return order


def approve_replenishment_request_data(
    request: dict[str, Any],
    payload: ReplenishmentApproveRequest,
    repository: WarehouseRepository | None = None,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    orders = list_purchase_orders(request["request_id"], repository=repository)
    order = orders[0] if orders else None
    created = False
    if not order:
        supplier = find_default_supplier(request["item_id"], repository)
        if not supplier:
            raise ValueError("default_supplier_not_found")
        order = create_purchase_order(request, supplier, payload, repository)
        created = True

    updated = update_replenishment_request_status(
        request["request_id"],
        status=REPLENISHMENT_STATUS_APPROVED,
        repository=repository,
    )
    return updated or request, order, created


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
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    order = get_purchase_order_by_id(purchase_order_id, repository)
    if not order:
        return None, None, "purchase_order_not_found"
    request = find_replenishment_request(order["request_id"], repository)
    if not request:
        return order, None, "replenishment_request_not_found"

    action = "reused" if order.get("warehouse_sync_status") in {"arrived_unsynced", "synced"} else "updated"
    arrived_at = order.get("arrived_at") or datetime.now(UTC).isoformat()
    updated_order = update_purchase_order_warehouse_sync_status(
        purchase_order_id,
        warehouse_sync_status="arrived_unsynced",
        arrived_at=arrived_at,
        repository=repository,
    )
    return updated_order or order, request, action


def procurement_replenishment_request_table_fields(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "Request ID": request["request_id"],
        "Status": request["status"],
        "Source": request["source"],
        "Warehouse": request["warehouse_name"],
        "Warehouse ID": request["warehouse_id"],
        "Location": request.get("location_code") or "",
        "Category": request["category_name"],
        "Item Name": request["item_name"],
        "Current Quantity": int(request["current_quantity"]),
        "Reorder Threshold": int(request["reorder_threshold"]),
        "Suggested Quantity": int(request["suggested_quantity"]),
        "Reason": request["reason"],
        "Created By": request["created_by"],
        "Created At": request["created_at"],
        "Updated At": request["updated_at"],
        "Last Synced At": datetime.now(UTC).isoformat(),
        "Sync Status": "synced",
        "Source Version": f"mock-api:{request['request_id']}:{request['updated_at']}",
    }


def procurement_purchase_order_table_fields(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "Purchase Order ID": order["purchase_order_id"],
        "Request ID": order["request_id"],
        "Payment Status": order["payment_status"],
        "Warehouse Sync Status": order["warehouse_sync_status"],
        "Arrived At": order.get("arrived_at") or "",
        "Supplier Name": order["supplier_name"],
        "Warehouse": order["warehouse_name"],
        "Warehouse ID": order["warehouse_id"],
        "Location": order.get("location_code") or "",
        "Quantity": int(order["quantity"]),
        "Unit Price": int(order["unit_price"]),
        "Currency": order["currency"],
        "Estimated Total Price": int(order["estimated_total_price"]),
        "Lead Time Days": int(order["lead_time_days"]),
        "Estimated Arrival Date": order["estimated_arrival_date"],
        "Created By": order["created_by"],
        "Created At": order["created_at"],
        "Updated At": order["updated_at"],
        "Last Synced At": datetime.now(UTC).isoformat(),
        "Sync Status": "synced",
        "Source Version": f"mock-api:{order['purchase_order_id']}:{order['updated_at']}",
    }
