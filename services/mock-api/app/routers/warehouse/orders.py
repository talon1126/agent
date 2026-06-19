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
from pydantic import BaseModel

from app.routers.pagination import page_items
from app.routers.delivery.state import DELIVERY_PROVIDERS, get_delivery_provider
from app.store import load_json
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

ORDER_FULFILLMENT_TABLE_SCHEMA = [
    {"name": "Order ID", "type": "text"},
    {
        "name": "Status",
        "type": "single_select",
        "options": [
            {"name": ORDER_STATUS_UNPAID, "color": 24},
            {"name": ORDER_STATUS_PENDING_FULFILLMENT_REVIEW, "color": 17},
            {"name": ORDER_STATUS_PENDING_SHIPMENT, "color": 28},
            {"name": ORDER_STATUS_SHIPPED, "color": 21},
            {"name": ORDER_STATUS_ARRIVED, "color": 30},
            {"name": ORDER_STATUS_REFUNDED, "color": 19},
            {"name": ORDER_STATUS_RETURNED, "color": 18},
            {"name": ORDER_STATUS_CANCELED, "color": 20},
        ],
    },
    {"name": "Customer", "type": "text"},
    {"name": "Warehouse", "type": "text"},
    {"name": "Delivery Provider", "type": "text"},
    {"name": "Tracking No", "type": "text"},
    {"name": "Shipping City", "type": "text"},
    {"name": "Item Summary", "type": "text"},
    {"name": "Total Quantity", "type": "number"},
    {"name": "Candidate Warehouses", "type": "text"},
    {"name": "Created At", "type": "datetime"},
    {"name": "Paid At", "type": "datetime"},
    {"name": "Updated At", "type": "datetime"},
    {"name": "Source Version", "type": "text"},
]

ORDER_ITEMS_TABLE_SCHEMA = [
    {"name": "Order Item ID", "type": "text"},
    {"name": "Order ID", "type": "text"},
    {
        "name": "Status",
        "type": "single_select",
        "options": [
            {"name": ORDER_STATUS_UNPAID, "color": 24},
            {"name": ORDER_STATUS_PENDING_FULFILLMENT_REVIEW, "color": 17},
            {"name": ORDER_STATUS_PENDING_SHIPMENT, "color": 28},
            {"name": ORDER_STATUS_SHIPPED, "color": 21},
            {"name": ORDER_STATUS_ARRIVED, "color": 30},
            {"name": ORDER_STATUS_REFUNDED, "color": 19},
            {"name": ORDER_STATUS_RETURNED, "color": 18},
            {"name": ORDER_STATUS_CANCELED, "color": 20},
        ],
    },
    {"name": "Customer", "type": "text"},
    {"name": "Item ID", "type": "text"},
    {"name": "Warehouse", "type": "text"},
    {"name": "Location", "type": "text"},
    {"name": "Quantity", "type": "number"},
    {"name": "Created At", "type": "datetime"},
    {"name": "Updated At", "type": "datetime"},
    {"name": "Source Version", "type": "text"},
]


class OrderFulfillmentTableRowsRequest(BaseModel):
    """Request contract for the Order Fulfillment Feishu read model.

    Args:
        order_id: Optional order business identifier used for a single-row
            refresh after a specific order changes.
        status: Optional order lifecycle status filter used by page-level or
            scheduled sync jobs.
        limit: Maximum number of order rows returned to feishu-adapter.
    """

    order_id: str | None = None
    status: str | None = None
    limit: int = 100
    offset: int = 0


class OrderItemsTableRowsRequest(BaseModel):
    """Request contract for the Order Items Feishu read model.

    Args:
        order_id: Optional order id filter for a single order detail refresh.
        status: Optional order item lifecycle status filter.
        limit: Maximum number of order item rows returned to feishu-adapter.
    """

    order_id: str | None = None
    status: str | None = None
    limit: int = 100
    offset: int = 0


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
        key=lambda row: (row["expiry_date"], row["production_date"], row["location_code"]),
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
        "delivery_providers": [provider for provider in DELIVERY_PROVIDERS if provider.get("status") == "active"],
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


def summarize_order_items_for_table(items: list[dict[str, Any]]) -> tuple[str, int]:
    """Build the compact item summary used by the Feishu fulfillment table.

    Args:
        items: Order item rows from PostgreSQL or the in-memory fallback store.

    Returns:
        A tuple containing a readable summary such as `item_a x 2, item_b x 1`
        and the total quantity across all order lines.
    """

    quantities: dict[str, int] = defaultdict(int)
    for item in items:
        item_id = str(item.get("item_id") or "").strip()
        if not item_id:
            continue
        quantities[item_id] += int(item.get("quantity") or 0)
    summary = ", ".join(f"{item_id} x {quantity}" for item_id, quantity in quantities.items())
    return summary, sum(quantities.values())


def candidate_warehouses_for_table(order: dict[str, Any], items: list[dict[str, Any]]) -> str:
    """Return a readable fulfillment-candidate summary for Feishu employees.

    Args:
        order: Order header row containing the preferred warehouse.
        items: Order item rows used to calculate whether each warehouse can
            fulfill the order.

    Returns:
        Comma-separated warehouse labels with an `available` or `blocked`
        indicator. An empty string is returned when candidate calculation cannot
        be completed from the current fixture/repository state.
    """

    try:
        review = list_fulfillment_candidates_for_items(
            items,
            preferred_warehouse_id=str(order.get("selected_warehouse_id") or ""),
        )
    except HTTPException:
        return ""
    labels = []
    for candidate in review.get("candidates", []):
        status = "available" if candidate.get("can_fulfill") else "blocked"
        labels.append(f"{candidate.get('warehouse_name') or candidate.get('warehouse_id')}({status})")
    return ", ".join(labels)


def order_fulfillment_table_fields(order: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert one order and its lines into Feishu table fields.

    Args:
        order: Order header row from PostgreSQL or fallback fixtures.
        items: Order item rows attached to the order.

    Returns:
        Field dictionary matching `ORDER_FULFILLMENT_TABLE_SCHEMA`. Only
        employee-facing business values are emitted; internal order item row ids
        are intentionally omitted from this read model.
    """

    item_summary, total_quantity = summarize_order_items_for_table(items)
    updated_at = str(order.get("updated_at") or "")
    return {
        "Order ID": order["order_id"],
        "Status": order["status"],
        "Customer": order.get("customer_id") or "",
        "Warehouse": order.get("selected_warehouse_name") or warehouse_name_by_id(str(order.get("selected_warehouse_id") or "")),
        "Delivery Provider": order.get("delivery_provider_name") or "",
        "Tracking No": order.get("tracking_no") or "",
        "Shipping City": order.get("shipping_city") or "",
        "Item Summary": item_summary,
        "Total Quantity": total_quantity,
        "Candidate Warehouses": candidate_warehouses_for_table(order, items),
        "Created At": order.get("created_at") or "",
        "Paid At": order.get("paid_at") or "",
        "Updated At": updated_at,
        "Source Version": f"mock-api:{order['order_id']}:{updated_at}",
    }


def load_order_fulfillment_table_rows(
    *,
    order_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], bool, int | None]:
    """Load order fulfillment rows for the Feishu read-model sync endpoint.

    Args:
        order_id: Optional exact order business id filter.
        status: Optional order lifecycle status filter.
        limit: Maximum number of rows in one response page.
        offset: Zero-based row offset for multi-page Feishu table sync.

    Returns:
        Page rows, whether another page exists, and the next offset.
    """

    repository = get_warehouse_repository()
    normalized_order_id = (order_id or "").strip()
    normalized_status = (status or "").strip()

    source_orders = repository.list_orders() if repository else list(WAREHOUSE_ORDERS)
    rows: list[dict[str, Any]] = []
    for order in source_orders:
        if normalized_order_id and str(order.get("order_id") or "") != normalized_order_id:
            continue
        if normalized_status and str(order.get("status") or "") != normalized_status:
            continue
        if repository:
            details = repository.get_order(str(order["order_id"]))
            items = list((details or {}).get("items") or [])
        else:
            items = fallback_order_items(str(order["order_id"]))
        rows.append(
            {
                "order_id": order["order_id"],
                "fields": order_fulfillment_table_fields(order, items),
            }
        )
    return page_items(rows, limit=limit, offset=offset)


def order_item_business_id(item: dict[str, Any]) -> str:
    """Build a stable employee-facing order line identifier.

    Args:
        item: Order item row from PostgreSQL or fallback fixtures.

    Returns:
        A deterministic id composed from business fields. The database
        autoincrement `id` is deliberately not exposed in Feishu.
    """

    return ":".join(
        [
            str(item.get("order_id") or ""),
            str(item.get("item_id") or ""),
            str(item.get("location_code") or ""),
        ]
    )


def order_item_table_fields(item: dict[str, Any]) -> dict[str, Any]:
    """Convert one order line into Feishu table fields.

    Args:
        item: Order item row containing order, item, warehouse, location, quantity,
            and timestamp facts.

    Returns:
        Field dictionary matching `ORDER_ITEMS_TABLE_SCHEMA` without exposing
        the physical row id.
    """

    business_id = order_item_business_id(item)
    updated_at = str(item.get("updated_at") or "")
    return {
        "Order Item ID": business_id,
        "Order ID": item.get("order_id") or "",
        "Status": item.get("status") or "",
        "Customer": item.get("customer_id") or "",
        "Item ID": item.get("item_id") or "",
        "Warehouse": warehouse_name_by_id(str(item.get("warehouse_id") or "")),
        "Location": item.get("location_code") or "",
        "Quantity": int(item.get("quantity") or 0),
        "Created At": item.get("created_at") or "",
        "Updated At": updated_at,
        "Source Version": f"mock-api:{business_id}:{updated_at}",
    }


def load_order_items_table_rows(
    *,
    order_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], bool, int | None]:
    """Load order item rows for the Feishu read-model sync endpoint."""

    repository = get_warehouse_repository()
    normalized_order_id = (order_id or "").strip()
    normalized_status = (status or "").strip()

    if repository and normalized_order_id:
        details = repository.get_order(normalized_order_id)
        source_items = list((details or {}).get("items") or [])
    elif repository:
        source_items = []
        for order in repository.list_orders():
            details = repository.get_order(str(order["order_id"]))
            source_items.extend(list((details or {}).get("items") or []))
    else:
        source_items = list(WAREHOUSE_ORDER_ITEMS)

    rows: list[dict[str, Any]] = []
    for item in source_items:
        if normalized_order_id and str(item.get("order_id") or "") != normalized_order_id:
            continue
        if normalized_status and str(item.get("status") or "") != normalized_status:
            continue
        business_id = order_item_business_id(item)
        rows.append({"order_item_id": business_id, "fields": order_item_table_fields(item)})
    return page_items(rows, limit=limit, offset=offset)


@router.get("/warehouse/orders/fulfillment/table-schema")
def get_order_fulfillment_table_schema() -> dict[str, Any]:
    """Return the Feishu table schema for the Order Fulfillment read model."""

    return {
        "ok": True,
        "schema_id": "order_fulfillment",
        "source": "mock-api",
        "fields": ORDER_FULFILLMENT_TABLE_SCHEMA,
    }


@router.post("/warehouse/orders/fulfillment/table-rows")
def get_order_fulfillment_table_rows(payload: OrderFulfillmentTableRowsRequest) -> dict[str, Any]:
    """Return order fulfillment rows consumed by feishu-adapter table sync."""

    rows, has_more, next_offset = load_order_fulfillment_table_rows(
        order_id=payload.order_id,
        status=payload.status,
        limit=payload.limit,
        offset=payload.offset,
    )
    return {
        "ok": True,
        "schema_id": "order_fulfillment",
        "count": len(rows),
        "has_more": has_more,
        "next_offset": next_offset,
        "items": rows,
    }


@router.get("/warehouse/orders/items/table-schema")
def get_order_items_table_schema() -> dict[str, Any]:
    """Return the Feishu table schema for the Order Items read model."""

    return {
        "ok": True,
        "schema_id": "order_items",
        "source": "mock-api",
        "fields": ORDER_ITEMS_TABLE_SCHEMA,
    }


@router.post("/warehouse/orders/items/table-rows")
def get_order_items_table_rows(payload: OrderItemsTableRowsRequest) -> dict[str, Any]:
    """Return order item rows consumed by feishu-adapter table sync."""

    rows, has_more, next_offset = load_order_items_table_rows(
        order_id=payload.order_id,
        status=payload.status,
        limit=payload.limit,
        offset=payload.offset,
    )
    return {
        "ok": True,
        "schema_id": "order_items",
        "count": len(rows),
        "has_more": has_more,
        "next_offset": next_offset,
        "items": rows,
    }



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
        "status": ORDER_STATUS_UNPAID,
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
        "release_reason": "",
    }
    if repository:
        try:
            result = repository.create_order(order)
            fulfillment_review = repository.list_order_fulfillment_candidates(order_id)
            notification = {"configured": bool(os.getenv("FEISHU_FULFILLMENT_REVIEW_NOTIFY_URL", "").strip()), "status": "skipped"}
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
                "status": ORDER_STATUS_UNPAID,
                "item_id": requested["item_id"],
                "warehouse_id": requested["warehouse_id"],
                "location_code": requested.get("location_code") or "",
                "quantity": int(requested["quantity"]),
                "created_at": now,
                "updated_at": now,
            }
        )
    WAREHOUSE_ORDER_ITEMS.extend(created_items)
    WAREHOUSE_ORDERS.append(order)
    response = warehouse_order_response(order, fallback_order_items(order_id))
    response["notification"] = {
        "configured": bool(os.getenv("FEISHU_FULFILLMENT_REVIEW_NOTIFY_URL", "").strip()),
        "status": "skipped",
    }
    return response


def confirm_fallback_order_fulfillment(
    order_id: str,
    *,
    warehouse_id: str,
    delivery_provider_id: str | None = None,
    courier_phone: str = "",
    tracking_no: str = "",
    updated_by: str,
    updated_at: str,
) -> dict[str, Any]:
    order = get_warehouse_order_or_404(order_id)
    if order["status"] == ORDER_STATUS_PENDING_SHIPMENT:
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
                    "status": ORDER_STATUS_PENDING_SHIPMENT,
                    "item_id": row["item_id"],
                    "warehouse_id": row["warehouse_id"],
                    "location_code": row["location_code"],
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
    delivery_provider = get_delivery_provider(delivery_provider_id or str(order.get("delivery_provider_id") or "sf"))
    selected_tracking_no = tracking_no.strip() or str(order.get("tracking_no") or "")
    if not selected_tracking_no:
        selected_tracking_no = f"{delivery_provider['tracking_prefix']}{order_id.replace('-', '')}"
    order["status"] = ORDER_STATUS_PENDING_SHIPMENT
    order["delivery_provider_id"] = delivery_provider["provider_id"]
    order["delivery_provider_name"] = delivery_provider["name"]
    order["courier_phone"] = courier_phone.strip() or str(order.get("courier_phone") or "")
    order["tracking_no"] = selected_tracking_no
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
                    delivery_provider_id=payload.delivery_provider_id,
                    courier_phone=payload.courier_phone,
                    tracking_no=payload.tracking_no,
                    updated_by=payload.updated_by,
                    updated_at=now,
                ),
            }
        except ValueError as error:
            raise order_http_error(error) from error

    return confirm_fallback_order_fulfillment(
        order_id,
        warehouse_id=payload.warehouse_id,
        delivery_provider_id=payload.delivery_provider_id,
        courier_phone=payload.courier_phone,
        tracking_no=payload.tracking_no,
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
            result = repository.pay_order(
                order_id,
                updated_by=payload.updated_by,
                updated_at=datetime.now(UTC).isoformat(),
            )
            fulfillment_review = repository.list_order_fulfillment_candidates(order_id)
            notification = send_fulfillment_review_notification(
                order=result["order"],
                items=result["items"],
                fulfillment_review=fulfillment_review,
            )
            return {"ok": True, **result, "fulfillment_review": fulfillment_review, "notification": notification}
        except ValueError as error:
            raise order_http_error(error) from error

    order = get_warehouse_order_or_404(order_id)
    if order["status"] == ORDER_STATUS_PENDING_FULFILLMENT_REVIEW:
        return warehouse_order_response(order, fallback_order_items(order_id))
    if order["status"] != ORDER_STATUS_UNPAID:
        raise HTTPException(status_code=409, detail=f"order_cannot_pay_from_{order['status']}")
    now = datetime.now(UTC).isoformat()
    for item in fallback_order_items(order_id):
        if item["status"] == ORDER_STATUS_UNPAID:
            item["status"] = ORDER_STATUS_PENDING_FULFILLMENT_REVIEW
            item["updated_at"] = now
    order["status"] = ORDER_STATUS_PENDING_FULFILLMENT_REVIEW
    order["updated_at"] = now
    order["paid_at"] = now
    response = warehouse_order_response(order, fallback_order_items(order_id))
    response["notification"] = send_fulfillment_review_notification(
        order=response["order"],
        items=response["items"],
        fulfillment_review=response["fulfillment_review"],
    )
    return response


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
        and item["status"] in {ORDER_STATUS_PENDING_SHIPMENT, ORDER_STATUS_SHIPPED, ORDER_STATUS_ARRIVED}
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
        if order["status"] != ORDER_STATUS_UNPAID:
            continue
        if str(order.get("expires_at") or "") >= now:
            continue
        update_fallback_order_status(order["order_id"], ORDER_STATUS_CANCELED)
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
    payload = normalize_order_tool_payload(payload)
    action = str(payload.get("action") or "").strip().lower()
    if action in {"create", "create_order"}:
        return create_warehouse_order(WarehouseOrderCreate(**payload))
    if action in {"create_and_pay", "paid"}:
        created = create_warehouse_order(WarehouseOrderCreate(**payload))
        order_id = created["order"]["order_id"]
        return pay_warehouse_order(
            order_id,
            WarehouseOrderStatusUpdateRequest(updated_by=str(payload.get("updated_by") or "warehouse-agent")),
        )
    order_id = str(payload.get("order_id") or "").strip()
    if not order_id:
        raise HTTPException(status_code=400, detail="missing_order_id")
    if order_id.lower().startswith("ord-"):
        order_id = order_id.upper()
    update = WarehouseOrderStatusUpdateRequest(updated_by=str(payload.get("updated_by") or "warehouse-agent"))
    if action in {"pay", "付款"}:
        return pay_warehouse_order(order_id, update)
    if action in {"confirm_fulfillment", "confirm_warehouse", "确认发仓"}:
        return confirm_order_fulfillment(
            order_id,
            WarehouseOrderFulfillmentConfirmRequest(
                warehouse_id=str(payload.get("warehouse_id") or ""),
                delivery_provider_id=str(payload.get("delivery_provider_id") or "") or None,
                courier_phone=str(payload.get("courier_phone") or ""),
                tracking_no=str(payload.get("tracking_no") or ""),
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


def normalize_order_tool_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a warehouse order tool payload with nested Agent input merged.

    Feishu and n8n sometimes pass an LLM tool call as {"input": "{...json...}"}
    instead of flattening each argument at the top level. The warehouse order
    tool is the stable API boundary, so it normalizes that shape before action
    routing and preserves explicitly provided top-level fields when both shapes
    are present.
    """
    normalized = dict(payload)
    embedded_input = payload.get("input")
    if isinstance(embedded_input, str) and embedded_input.strip().startswith("{"):
        try:
            embedded_payload = json.loads(embedded_input)
        except json.JSONDecodeError:
            embedded_payload = {}
        if isinstance(embedded_payload, dict):
            for key, value in embedded_payload.items():
                if normalized.get(key) in (None, ""):
                    normalized[key] = value

    if not normalized.get("delivery_provider_id"):
        for alias in ("carrier", "deliveryProviderId", "provider_id"):
            alias_value = normalized.get(alias)
            if alias_value not in (None, ""):
                normalized["delivery_provider_id"] = str(alias_value).strip().lower()
                break
    return normalized

