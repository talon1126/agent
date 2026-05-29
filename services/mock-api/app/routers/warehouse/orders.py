import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from app.warehouse_store import WarehouseRepository

from .inventory import (
    aggregate_location_balances,
    enrich_batch_row,
    inventory_batch_key,
    load_batch_inventory_rows,
)
from .schemas import WarehouseOrderCreate, WarehouseOrderStatusUpdateRequest
from .state import (
    WAREHOUSE_BATCH_QUANTITY_OVERRIDES,
    WAREHOUSE_ORDERS,
    WAREHOUSE_ORDER_ITEMS,
    get_warehouse_repository,
)

router = APIRouter()


def available_order_batches(item_id: str, warehouse_id: str, location_code: str | None = None) -> list[dict[str, Any]]:
    rows = [enrich_batch_row(row) for row in load_batch_inventory_rows(item_id=item_id, warehouse_id=warehouse_id)]
    return sorted(
        [
            row
            for row in rows
            if row["storage_status"] == "available"
            and row["expiry_risk"] != "expired"
            and int(row["quantity_available"]) > 0
            and (not location_code or row["location_code"].casefold() == location_code.casefold())
        ],
        key=lambda row: (row["expiry_date"], row["production_date"], row["batch_no"]),
    )


def insufficient_stock_detail(
    *,
    requested_quantity: int,
    available_quantity: int,
    item_id: str,
    warehouse_id: str,
    balances: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "error": "insufficient_available_stock",
        "item_id": item_id,
        "warehouse_id": warehouse_id,
        "requested_quantity": requested_quantity,
        "available_quantity": available_quantity,
        "shortage_quantity": max(requested_quantity - available_quantity, 0),
        "available_locations": (balances or {}).get("locations", []),
    }


def allocate_order_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    item_id = str(item["item_id"]).strip()
    warehouse_id = str(item["warehouse_id"]).strip()
    location_code = str(item.get("location_code") or "").strip() or None
    quantity = int(item["quantity"])
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be positive")
    balances = aggregate_location_balances(item_id=item_id, warehouse_id=warehouse_id)
    total_available = int(balances["total_quantity_available"])
    if total_available < quantity:
        raise HTTPException(
            status_code=409,
            detail=insufficient_stock_detail(
                requested_quantity=quantity,
                available_quantity=total_available,
                item_id=item_id,
                warehouse_id=warehouse_id,
                balances=balances,
            ),
        )
    candidate_batches = available_order_batches(item_id, warehouse_id, location_code)

    remaining = quantity
    allocations: list[dict[str, Any]] = []
    for row in candidate_batches:
        if remaining <= 0:
            break
        allocated = min(int(row["quantity_available"]), remaining)
        if allocated <= 0:
            continue
        allocations.append({**row, "allocated_quantity": allocated})
        remaining -= allocated
    if remaining:
        raise HTTPException(
            status_code=409,
            detail=insufficient_stock_detail(
                requested_quantity=quantity,
                available_quantity=quantity - remaining,
                item_id=item_id,
                warehouse_id=warehouse_id,
                balances=balances,
            ),
        )
    return allocations


def next_warehouse_order_id(repository: WarehouseRepository | None = None) -> str:
    existing_count = repository.count_orders() if repository else len(WAREHOUSE_ORDERS)
    return f"ORD-CODEX-{existing_count + 1001}"


def set_inventory_balance_quantity(
    row: dict[str, Any],
    *,
    quantity_on_hand: int,
) -> None:
    WAREHOUSE_BATCH_QUANTITY_OVERRIDES[inventory_batch_key(row)] = {
        "quantity_on_hand": quantity_on_hand,
        "quantity_reserved": 0,
    }


def find_current_inventory_balance(line: dict[str, Any]) -> dict[str, Any]:
    rows = load_batch_inventory_rows(
        item_id=line["item_id"],
        warehouse_id=line["warehouse_id"],
        location_code=line["location_code"],
        batch_no=line["batch_no"],
    )
    if not rows:
        raise HTTPException(status_code=404, detail="inventory balance not found")
    return rows[0]


def warehouse_order_response(order: dict[str, Any], items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"ok": True, "order": order, "items": items or []}


def get_warehouse_order_or_404(order_id: str) -> dict[str, Any]:
    order = next((item for item in WAREHOUSE_ORDERS if item["order_id"] == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="order_not_found")
    return order


def fallback_order_items(order_id: str) -> list[dict[str, Any]]:
    return [item for item in WAREHOUSE_ORDER_ITEMS if item["order_id"] == order_id]



@router.post("/warehouse/orders")
def create_warehouse_order(payload: WarehouseOrderCreate) -> dict[str, Any]:
    repository = get_warehouse_repository()
    now = datetime.now(UTC).isoformat()
    order_id = (payload.order_id or "").strip() or next_warehouse_order_id(repository)
    requested_items = [
        {
            "item_id": item.item_id.strip(),
            "warehouse_id": item.warehouse_id.strip(),
            "location_code": (item.location_code or "").strip(),
            "quantity": int(item.quantity),
        }
        for item in payload.items
    ]
    order = {
        "order_id": order_id,
        "customer_id": payload.customer_id.strip(),
        "status": "created",
        "requested_items": requested_items,
        "created_by": payload.created_by,
        "created_at": now,
        "updated_at": now,
        "paid_at": "",
        "shipped_at": "",
        "arrived_at": "",
        "cancelled_at": "",
        "returned_at": "",
    }
    if repository:
        return repository.create_order(order)

    WAREHOUSE_ORDERS.append(order)
    return warehouse_order_response(order)


@router.get("/warehouse/orders/{order_id}")
def get_warehouse_order(order_id: str) -> dict[str, Any]:
    repository = get_warehouse_repository()
    if repository:
        details = repository.get_order(order_id)
        if not details:
            raise HTTPException(status_code=404, detail="order_not_found")
        return {"ok": True, **details}
    order = get_warehouse_order_or_404(order_id)
    return warehouse_order_response(order, fallback_order_items(order_id))


@router.post("/warehouse/orders/{order_id}/pay")
def pay_warehouse_order(
    order_id: str,
    payload: WarehouseOrderStatusUpdateRequest,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    if repository:
        try:
            return {"ok": True, **repository.pay_order(order_id, updated_by=payload.updated_by, updated_at=datetime.now(UTC).isoformat())}
        except ValueError as error:
            raise order_http_error(error) from error

    order = get_warehouse_order_or_404(order_id)
    if order["status"] == "paid":
        return warehouse_order_response(order, fallback_order_items(order_id))
    if order["status"] != "created":
        raise HTTPException(status_code=409, detail=f"order_cannot_pay_from_{order['status']}")
    now = datetime.now(UTC).isoformat()
    created_items: list[dict[str, Any]] = []
    for requested in order["requested_items"]:
        for row in allocate_order_item(requested):
            quantity = int(row["allocated_quantity"])
            balance = find_current_inventory_balance(row)
            set_inventory_balance_quantity(balance, quantity_on_hand=int(balance["quantity_on_hand"]) - quantity)
            created_items.append(
                {
                    "id": len(WAREHOUSE_ORDER_ITEMS) + len(created_items) + 1,
                    "order_id": order_id,
                    "customer_id": order["customer_id"],
                    "status": "paid",
                    "item_id": row["item_id"],
                    "warehouse_id": row["warehouse_id"],
                    "location_code": row["location_code"],
                    "batch_no": row["batch_no"],
                    "quantity": quantity,
                    "created_at": now,
                    "updated_at": now,
                }
            )
    WAREHOUSE_ORDER_ITEMS.extend(created_items)
    order["status"] = "paid"
    order["updated_at"] = now
    order["paid_at"] = now
    return warehouse_order_response(order, fallback_order_items(order_id))


def order_http_error(error: ValueError) -> HTTPException:
    message = str(error)
    try:
        return HTTPException(status_code=409, detail=json.loads(message))
    except json.JSONDecodeError:
        if message == "order_not_found":
            return HTTPException(status_code=404, detail=message)
        return HTTPException(status_code=409, detail=message)


def restore_fallback_order_items(order: dict[str, Any], status: str, now: str) -> None:
    for line in [item for item in WAREHOUSE_ORDER_ITEMS if item["order_id"] == order["order_id"] and item["status"] == "paid"]:
        balance = find_current_inventory_balance(line)
        set_inventory_balance_quantity(
            balance,
            quantity_on_hand=int(balance["quantity_on_hand"]) + int(line["quantity"]),
        )
        line["status"] = status
        line["updated_at"] = now


def update_fallback_order_status(order_id: str, status: str) -> dict[str, Any]:
    order = get_warehouse_order_or_404(order_id)
    now = datetime.now(UTC).isoformat()
    if status in {"cancelled", "returned"} and order["status"] in {"paid", "shipped", "arrived"}:
        restore_fallback_order_items(order, status, now)
    order["status"] = status
    order["updated_at"] = now
    timestamp_field = {
        "shipped": "shipped_at",
        "arrived": "arrived_at",
        "cancelled": "cancelled_at",
        "returned": "returned_at",
    }.get(status)
    if timestamp_field:
        order[timestamp_field] = now
    return warehouse_order_response(order, fallback_order_items(order_id))


@router.post("/warehouse/orders/{order_id}/ship")
def ship_warehouse_order(order_id: str, payload: WarehouseOrderStatusUpdateRequest) -> dict[str, Any]:
    repository = get_warehouse_repository()
    if repository:
        try:
            return {
                "ok": True,
                **repository.update_order_status(
                    order_id,
                    status="shipped",
                    updated_by=payload.updated_by,
                    updated_at=datetime.now(UTC).isoformat(),
                ),
            }
        except ValueError as error:
            raise order_http_error(error) from error
    return update_fallback_order_status(order_id, "shipped")


@router.post("/warehouse/orders/{order_id}/arrive")
def arrive_warehouse_order(order_id: str, payload: WarehouseOrderStatusUpdateRequest) -> dict[str, Any]:
    repository = get_warehouse_repository()
    if repository:
        try:
            return {
                "ok": True,
                **repository.update_order_status(
                    order_id,
                    status="arrived",
                    updated_by=payload.updated_by,
                    updated_at=datetime.now(UTC).isoformat(),
                ),
            }
        except ValueError as error:
            raise order_http_error(error) from error
    return update_fallback_order_status(order_id, "arrived")


@router.post("/warehouse/orders/{order_id}/cancel")
def cancel_warehouse_order(order_id: str, payload: WarehouseOrderStatusUpdateRequest) -> dict[str, Any]:
    repository = get_warehouse_repository()
    if repository:
        try:
            return {"ok": True, **repository.cancel_order(order_id, updated_by=payload.updated_by, updated_at=datetime.now(UTC).isoformat())}
        except ValueError as error:
            raise order_http_error(error) from error
    return update_fallback_order_status(order_id, "cancelled")


@router.post("/warehouse/orders/{order_id}/return")
def return_warehouse_order(order_id: str, payload: WarehouseOrderStatusUpdateRequest) -> dict[str, Any]:
    repository = get_warehouse_repository()
    if repository:
        try:
            return {"ok": True, **repository.return_order(order_id, updated_by=payload.updated_by, updated_at=datetime.now(UTC).isoformat())}
        except ValueError as error:
            raise order_http_error(error) from error
    return update_fallback_order_status(order_id, "returned")


@router.post("/warehouse/order-tool")
def warehouse_order_tool(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip().lower()
    if action in {"create", "create_order"}:
        return create_warehouse_order(WarehouseOrderCreate(**payload))
    if action in {"create_and_pay", "paid"}:
        created = create_warehouse_order(WarehouseOrderCreate(**payload))
        return pay_warehouse_order(created["order"]["order_id"], WarehouseOrderStatusUpdateRequest(updated_by=str(payload.get("updated_by") or "warehouse-agent")))
    order_id = str(payload.get("order_id") or "").strip()
    if not order_id:
        raise HTTPException(status_code=400, detail="missing_order_id")
    update = WarehouseOrderStatusUpdateRequest(updated_by=str(payload.get("updated_by") or "warehouse-agent"))
    if action in {"pay", "付款"}:
        return pay_warehouse_order(order_id, update)
    if action in {"ship", "发货"}:
        return ship_warehouse_order(order_id, update)
    if action in {"arrive", "到货", "delivered"}:
        return arrive_warehouse_order(order_id, update)
    if action in {"cancel", "取消", "refund"}:
        return cancel_warehouse_order(order_id, update)
    if action in {"return", "退货"}:
        return return_warehouse_order(order_id, update)
    raise HTTPException(status_code=400, detail="unsupported_order_action")

