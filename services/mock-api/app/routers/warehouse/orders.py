import json
import os
import re
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import timedelta
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from app.store import load_json
from app.routers.delivery.state import get_delivery_provider
from app.warehouse_store import WarehouseRepository

from .inventory import (
    aggregate_location_balances,
    enrich_batch_row,
    inventory_batch_key,
    load_batch_inventory_rows,
)
from .schemas import (
    WarehouseOrderCreate,
    WarehouseOrderFulfillmentConfirmRequest,
    WarehouseOrderReleaseExpiredRequest,
    WarehouseOrderStatusUpdateRequest,
)
from .state import (
    WAREHOUSE_BATCH_QUANTITY_OVERRIDES,
    WAREHOUSE_INVENTORY_MOVEMENTS,
    WAREHOUSE_ORDERS,
    WAREHOUSE_ORDER_ITEMS,
    get_warehouse_repository,
)

router = APIRouter()
FULFILLMENT_REVIEW_NOTIFY_TIMEOUT_SECONDS = 5

ORDER_STATUS_PENDING_FULFILLMENT_REVIEW = "pending_fulfillment_review"
ORDER_STATUS_UNPAID = "unpaid"
ORDER_STATUS_PENDING_SHIPMENT = "pending_shipment"
ORDER_STATUS_SHIPPED = "shipped"
ORDER_STATUS_ARRIVED = "arrived"
ORDER_STATUS_REFUNDED = "refunded"
ORDER_STATUS_RETURNED = "returned"
ORDER_STATUS_CANCELED = "canceled"


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


def parse_shipping_address(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    province = ""
    city = ""
    province_match = re.search(r"([^省]+省)", text)
    city_match = re.search(r"([^省市]+市)", text)
    if province_match:
        province = province_match.group(1)
    if city_match:
        city = city_match.group(1)
    return province, city


def normalize_city(value: str) -> str:
    return str(value or "").strip().removesuffix("市")


def active_warehouses() -> list[dict[str, Any]]:
    return [item for item in load_json("warehouses.json") if item.get("status") == "active"]


def aggregate_requested_quantities(items: list[dict[str, Any]]) -> dict[str, int]:
    quantities: dict[str, int] = defaultdict(int)
    for item in items:
        quantity = int(item["quantity"])
        if quantity <= 0:
            raise HTTPException(status_code=400, detail="quantity must be positive")
        quantities[str(item["item_id"])] += quantity
    return dict(quantities)


def warehouse_can_fulfill_all(items: list[dict[str, Any]], warehouse_id: str) -> tuple[bool, dict[str, Any] | None]:
    for item_id, quantity in aggregate_requested_quantities(items).items():
        try:
            balances = aggregate_location_balances(item_id=item_id, warehouse_id=warehouse_id)
        except HTTPException as error:
            if error.status_code != 404:
                raise
            balances = {"total_quantity_available": 0, "locations": []}
        available = int(balances["total_quantity_available"])
        if available < quantity:
            return False, insufficient_stock_detail(
                requested_quantity=quantity,
                available_quantity=available,
                item_id=item_id,
                warehouse_id=warehouse_id,
                balances=balances,
            )
    return True, None


def warehouse_name_by_id(warehouse_id: str) -> str:
    warehouse = next((item for item in active_warehouses() if item["warehouse_id"] == warehouse_id), None)
    return str((warehouse or {}).get("warehouse_name") or warehouse_id)


def list_fulfillment_candidates_for_items(
    items: list[dict[str, Any]],
    *,
    preferred_warehouse_id: str = "",
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for warehouse in active_warehouses():
        can_fulfill, shortage = warehouse_can_fulfill_all(items, warehouse["warehouse_id"])
        total_available = 0
        for item_id in aggregate_requested_quantities(items):
            try:
                balances = aggregate_location_balances(item_id=item_id, warehouse_id=warehouse["warehouse_id"])
            except HTTPException as error:
                if error.status_code != 404:
                    raise
                balances = {"total_quantity_available": 0}
            total_available += int(balances["total_quantity_available"])
        candidates.append(
            {
                "warehouse_id": warehouse["warehouse_id"],
                "warehouse_name": warehouse["warehouse_name"],
                "city": warehouse.get("city", ""),
                "can_fulfill": can_fulfill,
                "total_available": total_available,
                "shortage": shortage or {},
                "recommended": warehouse["warehouse_id"] == preferred_warehouse_id,
            }
        )
    candidates.sort(key=lambda item: (not item["recommended"], not item["can_fulfill"], item["warehouse_id"]))
    recommended = next((item for item in candidates if item["recommended"]), candidates[0] if candidates else {})
    return {
        "recommended_warehouse_id": recommended.get("warehouse_id", ""),
        "candidates": candidates,
    }


def choose_single_warehouse(items: list[dict[str, Any]], shipping_city: str) -> dict[str, Any]:
    explicit_ids = {str(item.get("warehouse_id") or "").strip() for item in items if str(item.get("warehouse_id") or "").strip()}
    warehouses = active_warehouses()
    if len(explicit_ids) > 1:
        raise HTTPException(status_code=400, detail="order_items_must_use_single_warehouse")
    if explicit_ids:
        warehouses = [warehouse for warehouse in warehouses if warehouse["warehouse_id"] in explicit_ids]
    city_key = normalize_city(shipping_city)
    city_matches = [
        warehouse for warehouse in warehouses if city_key and normalize_city(warehouse.get("city", "")) == city_key
    ]
    candidates = city_matches + [warehouse for warehouse in warehouses if warehouse not in city_matches]
    first_shortage: dict[str, Any] | None = None
    for warehouse in candidates:
        can_fulfill, shortage = warehouse_can_fulfill_all(items, warehouse["warehouse_id"])
        if can_fulfill:
            return warehouse
        if not first_shortage:
            first_shortage = shortage
    raise HTTPException(status_code=409, detail=first_shortage or {"error": "no_active_warehouse_can_fulfill_order"})


def movement_rows_from_order_items(
    items: list[dict[str, Any]],
    *,
    movement_type: str,
    created_by: str,
    created_at: str,
    direction: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], int] = defaultdict(int)
    for item in items:
        key = (
            item["order_id"],
            item["item_id"],
            item["warehouse_id"],
            item["location_code"],
        )
        grouped[key] += int(item["quantity"])
    return [
        {
            "movement_id": f"IM-{len(WAREHOUSE_INVENTORY_MOVEMENTS) + index + 1:06d}",
            "order_id": order_id,
            "movement_type": movement_type,
            "item_id": item_id,
            "warehouse_id": warehouse_id,
            "location_code": location_code,
            "quantity_delta": quantity * direction,
            "created_by": created_by,
            "created_at": created_at,
        }
        for index, ((order_id, item_id, warehouse_id, location_code), quantity) in enumerate(grouped.items())
    ]


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
    fulfillment_review = list_fulfillment_candidates_for_items(
        items or order.get("items") or [],
        preferred_warehouse_id=str(order.get("selected_warehouse_id") or ""),
    )
    return {"ok": True, "order": order, "items": items or [], "fulfillment_review": fulfillment_review}


def send_fulfillment_review_notification(
    *,
    order: dict[str, Any],
    items: list[dict[str, Any]],
    fulfillment_review: dict[str, Any],
) -> dict[str, Any]:
    notify_url = os.getenv("FEISHU_FULFILLMENT_REVIEW_NOTIFY_URL", "").strip()
    if not notify_url:
        return {"configured": False, "status": "skipped"}
    payload = {
        "chat_id": os.getenv("FEISHU_FULFILLMENT_REVIEW_CHAT_ID", "").strip(),
        "order": order,
        "items": items,
        "candidates": fulfillment_review.get("candidates", []),
    }
    request = urllib.request.Request(
        notify_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=FULFILLMENT_REVIEW_NOTIFY_TIMEOUT_SECONDS) as response:
            raw_body = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {"configured": True, "status": "failed", "error": str(error)}
    try:
        body = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        body = {"raw": raw_body}
    return {"configured": True, "status": "sent", "response": body}


def get_warehouse_order_or_404(order_id: str) -> dict[str, Any]:
    order = next((item for item in WAREHOUSE_ORDERS if item["order_id"] == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="order_not_found")
    return order


def fallback_order_items(order_id: str) -> list[dict[str, Any]]:
    return [item for item in WAREHOUSE_ORDER_ITEMS if item["order_id"] == order_id]


def pending_fallback_order_items(order_id: str) -> list[dict[str, Any]]:
    return [
        item
        for item in fallback_order_items(order_id)
        if item["status"] == ORDER_STATUS_PENDING_FULFILLMENT_REVIEW
    ]



@router.post("/warehouse/orders")
def create_warehouse_order(payload: WarehouseOrderCreate) -> dict[str, Any]:
    repository = get_warehouse_repository()
    now = datetime.now(UTC).isoformat()
    order_id = (payload.order_id or "").strip() or next_warehouse_order_id(repository)
    delivery_provider = get_delivery_provider(payload.delivery_provider_id)
    shipping_address = payload.shipping_address.strip()
    if not shipping_address:
        raise HTTPException(status_code=400, detail="shipping_address_required")
    shipping_province, shipping_city = parse_shipping_address(shipping_address)
    requested_items = [
        {
            "item_id": item.item_id.strip(),
            "warehouse_id": (item.warehouse_id or "").strip(),
            "location_code": (item.location_code or "").strip(),
            "quantity": int(item.quantity),
        }
        for item in payload.items
    ]
    selected_warehouse = choose_single_warehouse(requested_items, shipping_city)
    for item in requested_items:
        item["warehouse_id"] = selected_warehouse["warehouse_id"]
    expires_at = (datetime.fromisoformat(now) + timedelta(minutes=30)).isoformat()
    order = {
        "order_id": order_id,
        "customer_id": payload.customer_id.strip(),
        "status": ORDER_STATUS_PENDING_FULFILLMENT_REVIEW,
        # Warehouse owns inventory allocation and order lifecycle transitions.
        # Delivery fields live on the order so Delivery Agent can read provider
        # context without owning stock, picking, or shipment transition logic.
        "delivery_provider_id": delivery_provider["provider_id"],
        "delivery_provider_name": delivery_provider["name"],
        "courier_phone": payload.courier_phone.strip(),
        "tracking_no": payload.tracking_no.strip() or f"{delivery_provider['tracking_prefix']}{order_id.replace('-', '')}",
        "shipping_address": shipping_address,
        "shipping_province": shipping_province,
        "shipping_city": shipping_city,
        "selected_warehouse_id": selected_warehouse["warehouse_id"],
        "selected_warehouse_name": selected_warehouse["warehouse_name"],
        "items": requested_items,
        "created_by": payload.created_by,
        "created_at": now,
        "updated_at": now,
        "paid_at": "",
        "shipped_at": "",
        "arrived_at": "",
        "cancelled_at": "",
        "returned_at": "",
        "expires_at": expires_at,
        "released_at": "",
        "release_reason": "",
    }
    if repository:
        try:
            result = repository.create_order(order)
            fulfillment_review = repository.list_order_fulfillment_candidates(order_id)
            notification = send_fulfillment_review_notification(
                order=result["order"],
                items=result["items"],
                fulfillment_review=fulfillment_review,
            )
            return {"ok": True, **result, "fulfillment_review": fulfillment_review, "notification": notification}
        except ValueError as error:
            raise order_http_error(error) from error

    created_items: list[dict[str, Any]] = []
    for requested in requested_items:
        created_items.append(
            {
                "id": len(WAREHOUSE_ORDER_ITEMS) + len(created_items) + 1,
                "order_id": order_id,
                "customer_id": order["customer_id"],
                "status": ORDER_STATUS_PENDING_FULFILLMENT_REVIEW,
                "item_id": requested["item_id"],
                "warehouse_id": requested["warehouse_id"],
                "location_code": requested.get("location_code") or "",
                "batch_no": "",
                "quantity": int(requested["quantity"]),
                "created_at": now,
                "updated_at": now,
            }
        )
    WAREHOUSE_ORDER_ITEMS.extend(created_items)
    WAREHOUSE_ORDERS.append(order)
    response = warehouse_order_response(order, fallback_order_items(order_id))
    response["notification"] = send_fulfillment_review_notification(
        order=response["order"],
        items=response["items"],
        fulfillment_review=response["fulfillment_review"],
    )
    return response


def confirm_fallback_order_fulfillment(
    order_id: str,
    *,
    warehouse_id: str,
    updated_by: str,
    updated_at: str,
) -> dict[str, Any]:
    order = get_warehouse_order_or_404(order_id)
    if order["status"] == ORDER_STATUS_UNPAID:
        return warehouse_order_response(order, fallback_order_items(order_id))
    if order["status"] != ORDER_STATUS_PENDING_FULFILLMENT_REVIEW:
        raise HTTPException(status_code=409, detail=f"order_cannot_confirm_fulfillment_from_{order['status']}")

    requested_items = pending_fallback_order_items(order_id)
    if not requested_items:
        raise HTTPException(status_code=409, detail="order_has_no_pending_fulfillment_items")

    selected_warehouse_id = warehouse_id.strip() or str(order.get("selected_warehouse_id") or "")
    allocated_items: list[dict[str, Any]] = []
    for requested in requested_items:
        request = {
            "item_id": requested["item_id"],
            "warehouse_id": selected_warehouse_id,
            "location_code": requested.get("location_code") or "",
            "quantity": int(requested["quantity"]),
        }
        for row in allocate_order_item(request):
            quantity = int(row["allocated_quantity"])
            balance = find_current_inventory_balance(row)
            set_inventory_balance_quantity(balance, quantity_on_hand=int(balance["quantity_on_hand"]) - quantity)
            allocated_items.append(
                {
                    "id": len(WAREHOUSE_ORDER_ITEMS) + len(allocated_items) + 1,
                    "order_id": order_id,
                    "customer_id": order["customer_id"],
                    "status": ORDER_STATUS_UNPAID,
                    "item_id": row["item_id"],
                    "warehouse_id": row["warehouse_id"],
                    "location_code": row["location_code"],
                    "batch_no": row["batch_no"],
                    "quantity": quantity,
                    "created_at": requested["created_at"],
                    "updated_at": updated_at,
                }
            )

    WAREHOUSE_ORDER_ITEMS[:] = [item for item in WAREHOUSE_ORDER_ITEMS if item["order_id"] != order_id]
    WAREHOUSE_ORDER_ITEMS.extend(allocated_items)
    WAREHOUSE_INVENTORY_MOVEMENTS.extend(
        movement_rows_from_order_items(
            allocated_items,
            movement_type="order_fulfillment_confirmed",
            created_by=updated_by,
            created_at=updated_at,
            direction=-1,
        )
    )
    order["status"] = ORDER_STATUS_UNPAID
    order["selected_warehouse_id"] = selected_warehouse_id
    order["selected_warehouse_name"] = warehouse_name_by_id(selected_warehouse_id)
    order["updated_at"] = updated_at
    return warehouse_order_response(order, fallback_order_items(order_id))


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


@router.get("/warehouse/orders/{order_id}/fulfillment-candidates")
def get_warehouse_order_fulfillment_candidates(order_id: str) -> dict[str, Any]:
    repository = get_warehouse_repository()
    if repository:
        try:
            return {"ok": True, **repository.list_order_fulfillment_candidates(order_id)}
        except ValueError as error:
            raise order_http_error(error) from error

    order = get_warehouse_order_or_404(order_id)
    items = fallback_order_items(order_id)
    return {
        "ok": True,
        "order_id": order_id,
        **list_fulfillment_candidates_for_items(
            items,
            preferred_warehouse_id=str(order.get("selected_warehouse_id") or ""),
        ),
    }


@router.post("/warehouse/orders/{order_id}/fulfillment/confirm")
def confirm_order_fulfillment(
    order_id: str,
    payload: WarehouseOrderFulfillmentConfirmRequest,
) -> dict[str, Any]:
    repository = get_warehouse_repository()
    now = datetime.now(UTC).isoformat()
    if repository:
        try:
            return {
                "ok": True,
                **repository.confirm_order_fulfillment(
                    order_id,
                    warehouse_id=payload.warehouse_id,
                    updated_by=payload.updated_by,
                    updated_at=now,
                ),
            }
        except ValueError as error:
            raise order_http_error(error) from error

    return confirm_fallback_order_fulfillment(
        order_id,
        warehouse_id=payload.warehouse_id,
        updated_by=payload.updated_by,
        updated_at=now,
    )


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
    if order["status"] == ORDER_STATUS_PENDING_SHIPMENT:
        return warehouse_order_response(order, fallback_order_items(order_id))
    if order["status"] != ORDER_STATUS_UNPAID:
        raise HTTPException(status_code=409, detail=f"order_cannot_pay_from_{order['status']}")
    now = datetime.now(UTC).isoformat()
    for item in fallback_order_items(order_id):
        if item["status"] == ORDER_STATUS_UNPAID:
            item["status"] = ORDER_STATUS_PENDING_SHIPMENT
            item["updated_at"] = now
    order["status"] = ORDER_STATUS_PENDING_SHIPMENT
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
    for line in [
        item
        for item in WAREHOUSE_ORDER_ITEMS
        if item["order_id"] == order["order_id"]
        and item["status"] in {ORDER_STATUS_UNPAID, ORDER_STATUS_PENDING_SHIPMENT, ORDER_STATUS_SHIPPED, ORDER_STATUS_ARRIVED}
    ]:
        balance = find_current_inventory_balance(line)
        set_inventory_balance_quantity(
            balance,
            quantity_on_hand=int(balance["quantity_on_hand"]) + int(line["quantity"]),
        )
        line["status"] = status
        line["updated_at"] = now
    WAREHOUSE_INVENTORY_MOVEMENTS.extend(
        movement_rows_from_order_items(
            [
                item
                for item in WAREHOUSE_ORDER_ITEMS
                if item["order_id"] == order["order_id"] and item["status"] == status
            ],
            movement_type={
                ORDER_STATUS_REFUNDED: "order_refunded",
                ORDER_STATUS_RETURNED: "order_returned",
                ORDER_STATUS_CANCELED: "order_timeout_released",
            }.get(status, "order_restored"),
            created_by="warehouse-agent",
            created_at=now,
            direction=1,
        )
    )


def update_fallback_order_status(order_id: str, status: str) -> dict[str, Any]:
    order = get_warehouse_order_or_404(order_id)
    now = datetime.now(UTC).isoformat()
    if status in {ORDER_STATUS_REFUNDED, ORDER_STATUS_RETURNED, ORDER_STATUS_CANCELED} and order["status"] in {
        ORDER_STATUS_UNPAID,
        ORDER_STATUS_PENDING_SHIPMENT,
        ORDER_STATUS_SHIPPED,
        ORDER_STATUS_ARRIVED,
    }:
        restore_fallback_order_items(order, status, now)
    order["status"] = status
    order["updated_at"] = now
    timestamp_field = {
        ORDER_STATUS_SHIPPED: "shipped_at",
        ORDER_STATUS_ARRIVED: "arrived_at",
        ORDER_STATUS_REFUNDED: "cancelled_at",
        ORDER_STATUS_RETURNED: "returned_at",
        ORDER_STATUS_CANCELED: "cancelled_at",
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
                    status=ORDER_STATUS_SHIPPED,
                    updated_by=payload.updated_by,
                    updated_at=datetime.now(UTC).isoformat(),
                ),
            }
        except ValueError as error:
            raise order_http_error(error) from error
    return update_fallback_order_status(order_id, ORDER_STATUS_SHIPPED)


@router.post("/warehouse/orders/{order_id}/arrive")
def arrive_warehouse_order(order_id: str, payload: WarehouseOrderStatusUpdateRequest) -> dict[str, Any]:
    repository = get_warehouse_repository()
    if repository:
        try:
            return {
                "ok": True,
                **repository.update_order_status(
                    order_id,
                    status=ORDER_STATUS_ARRIVED,
                    updated_by=payload.updated_by,
                    updated_at=datetime.now(UTC).isoformat(),
                ),
            }
        except ValueError as error:
            raise order_http_error(error) from error
    return update_fallback_order_status(order_id, ORDER_STATUS_ARRIVED)


@router.post("/warehouse/orders/{order_id}/cancel")
def cancel_warehouse_order(order_id: str, payload: WarehouseOrderStatusUpdateRequest) -> dict[str, Any]:
    repository = get_warehouse_repository()
    if repository:
        try:
            return {"ok": True, **repository.cancel_order(order_id, updated_by=payload.updated_by, updated_at=datetime.now(UTC).isoformat())}
        except ValueError as error:
            raise order_http_error(error) from error
    return update_fallback_order_status(order_id, ORDER_STATUS_REFUNDED)


@router.post("/warehouse/orders/release-expired")
def release_expired_warehouse_orders(payload: WarehouseOrderReleaseExpiredRequest) -> dict[str, Any]:
    repository = get_warehouse_repository()
    now = payload.now or datetime.now(UTC).isoformat()
    if repository:
        released_orders = repository.release_expired_orders(
            processed_by=payload.processed_by,
            now=now,
            limit=payload.limit,
        )
        return {
            "ok": True,
            "released_count": len(released_orders),
            "released_orders": released_orders,
            "processed_at": now,
        }

    released: list[dict[str, Any]] = []
    for order in WAREHOUSE_ORDERS:
        if len(released) >= max(min(int(payload.limit or 100), 500), 1):
            break
        if order["status"] != ORDER_STATUS_UNPAID or order.get("released_at"):
            continue
        if str(order.get("expires_at") or "") >= now:
            continue
        update_fallback_order_status(order["order_id"], ORDER_STATUS_CANCELED)
        order["released_at"] = now
        order["release_reason"] = "unpaid_timeout"
        released.append(order)
    return {"ok": True, "released_count": len(released), "released_orders": released, "processed_at": now}


@router.post("/warehouse/orders/{order_id}/return")
def return_warehouse_order(order_id: str, payload: WarehouseOrderStatusUpdateRequest) -> dict[str, Any]:
    repository = get_warehouse_repository()
    if repository:
        try:
            return {"ok": True, **repository.return_order(order_id, updated_by=payload.updated_by, updated_at=datetime.now(UTC).isoformat())}
        except ValueError as error:
            raise order_http_error(error) from error
    return update_fallback_order_status(order_id, ORDER_STATUS_RETURNED)


@router.post("/warehouse/order-tool")
def warehouse_order_tool(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip().lower()
    if action in {"create", "create_order"}:
        return create_warehouse_order(WarehouseOrderCreate(**payload))
    if action in {"create_and_pay", "paid"}:
        created = create_warehouse_order(WarehouseOrderCreate(**payload))
        order_id = created["order"]["order_id"]
        confirm_order_fulfillment(
            order_id,
            WarehouseOrderFulfillmentConfirmRequest(
                warehouse_id=str(created["order"].get("selected_warehouse_id") or ""),
                updated_by=str(payload.get("updated_by") or "warehouse-agent"),
            ),
        )
        return pay_warehouse_order(
            order_id,
            WarehouseOrderStatusUpdateRequest(updated_by=str(payload.get("updated_by") or "warehouse-agent")),
        )
    order_id = str(payload.get("order_id") or "").strip()
    if not order_id:
        raise HTTPException(status_code=400, detail="missing_order_id")
    update = WarehouseOrderStatusUpdateRequest(updated_by=str(payload.get("updated_by") or "warehouse-agent"))
    if action in {"pay", "付款"}:
        return pay_warehouse_order(order_id, update)
    if action in {"confirm_fulfillment", "confirm_warehouse", "确认发仓"}:
        return confirm_order_fulfillment(
            order_id,
            WarehouseOrderFulfillmentConfirmRequest(
                warehouse_id=str(payload.get("warehouse_id") or ""),
                updated_by=update.updated_by,
            ),
        )
    if action in {"ship", "发货"}:
        return ship_warehouse_order(order_id, update)
    if action in {"arrive", "到货", "delivered"}:
        return arrive_warehouse_order(order_id, update)
    if action in {"cancel", "取消", "refund"}:
        return cancel_warehouse_order(order_id, update)
    if action in {"return", "退货"}:
        return return_warehouse_order(order_id, update)
    raise HTTPException(status_code=400, detail="unsupported_order_action")

