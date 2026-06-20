import json
import logging
import os
import urllib.error
import urllib.request
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter

from app.routers.procurement.service import list_purchase_orders
from app.store import load_json
from app.warehouse_store import _date_from_iso, _deterministic_reorder_threshold

from .schemas import (
    WarehousePurchaseOrderArrivalNotifyRequest,
    WarehousePurchaseOrderArrivalSyncRequest,
    WarehousePurchaseOrderInventorySyncRequest,
)
from .state import RECEIVED_INVENTORY_BALANCES, get_warehouse_repository

router = APIRouter()
logger = logging.getLogger(__name__)
PURCHASE_ARRIVAL_NOTIFY_TIMEOUT_SECONDS = 5
PURCHASE_ORDER_TABLE_SYNC_TIMEOUT_SECONDS = 5


@router.post("/warehouse/purchase-orders/sync-arrivals")
def sync_arrived_purchase_orders(
    payload: WarehousePurchaseOrderArrivalSyncRequest,
) -> dict[str, Any]:
    limit = max(min(int(payload.limit or 50), 500), 1)
    processed_at = datetime.now(UTC).isoformat()
    repository = get_warehouse_repository()
    if repository:
        synced_items = repository.sync_arrived_purchase_orders(
            limit=limit,
            processed_by=payload.processed_by,
            processed_at=processed_at,
        )
    else:
        synced_items = sync_arrived_purchase_orders_fallback(
            limit=limit,
            processed_by=payload.processed_by,
            processed_at=processed_at,
        )

    return {
        "ok": True,
        "processed_count": len(synced_items),
        "synced_count": len(synced_items),
        "skipped_count": 0,
        "synced_items": synced_items,
        "next_action": "已将已支付且未同步的采购到仓单写入库存余额表。",
    }


@router.post("/warehouse/purchase-orders/{purchase_order_id}/sync-inventory")
def sync_purchase_order_inventory(
    purchase_order_id: str,
    payload: WarehousePurchaseOrderInventorySyncRequest,
) -> dict[str, Any]:
    resolved_purchase_order_id = resolve_purchase_order_id(
        path_purchase_order_id=purchase_order_id,
        payload_purchase_order_id=payload.purchase_order_id,
    )
    processed_at = datetime.now(UTC).isoformat()
    processed_by = payload.operator_id.strip() or payload.processed_by
    repository = get_warehouse_repository()
    if repository:
        synced_item = repository.sync_purchase_order_inventory(
            purchase_order_id=resolved_purchase_order_id,
            processed_by=processed_by,
            processed_at=processed_at,
        )
    else:
        synced_item = sync_purchase_order_inventory_fallback(
            purchase_order_id=resolved_purchase_order_id,
            processed_by=processed_by,
            processed_at=processed_at,
        )

    if not synced_item:
        logger.warning(
            "purchase order inventory sync skipped path_purchase_order_id=%s "
            "payload_purchase_order_id=%s resolved_purchase_order_id=%s trigger_source=%s",
            purchase_order_id,
            payload.purchase_order_id,
            resolved_purchase_order_id,
            payload.trigger_source,
        )
        return {
            "ok": False,
            "status": "skipped",
            "purchase_order_id": resolved_purchase_order_id,
            "path_purchase_order_id": purchase_order_id,
            "payload_purchase_order_id": payload.purchase_order_id,
            "updated_balance_count": 0,
            "movement_count": 0,
            "trigger_source": payload.trigger_source,
            "error": "purchase_order_not_eligible_for_inventory_sync",
        }

    table_sync = post_purchase_order_table_sync(purchase_order_id=resolved_purchase_order_id)
    balance_table_sync = post_inventory_balance_table_sync(synced_item=synced_item)

    return {
        "ok": True,
        "status": "synced",
        "purchase_order_id": resolved_purchase_order_id,
        "purchase_order": synced_item,
        "updated_balance_count": 1,
        "movement_count": 0,
        "trigger_source": payload.trigger_source,
        "processed_by": processed_by,
        "processed_at": processed_at,
        "table_sync": table_sync,
        "balance_table_sync": balance_table_sync,
    }

def resolve_purchase_order_id(*, path_purchase_order_id: str, payload_purchase_order_id: str) -> str:
    """Resolve the purchase order ID from a Feishu button callback.

    Feishu bitable buttons may send a literal template placeholder in the URL
    when the field token is not configured correctly. Accepting the ID from the
    JSON body gives the automation a stable fallback while preserving the normal
    path-parameter contract for direct API callers.
    """
    body_value = payload_purchase_order_id.strip()
    path_value = path_purchase_order_id.strip()
    if body_value and (not path_value or "{{" in path_value or "}}" in path_value):
        return body_value
    return path_value


def post_inventory_balance_table_sync(*, synced_item: dict[str, Any]) -> dict[str, Any]:
    """Refresh the Feishu inventory balance row affected by a purchase order sync."""
    sync_url = os.getenv("FEISHU_WAREHOUSE_INVENTORY_BALANCE_SYNC_URL", "").strip()
    if not sync_url:
        return {"configured": False, "status": "skipped"}
    payload = {
        "item_id": str(synced_item.get("item_id") or "").strip(),
        "warehouse_id": str(synced_item.get("warehouse_id") or "").strip(),
        "limit": 500,
    }
    request = urllib.request.Request(
        sync_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=PURCHASE_ORDER_TABLE_SYNC_TIMEOUT_SECONDS) as response:
            raw_body = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {"configured": True, "status": "failed", "error": str(error), "request": payload}
    try:
        body = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        body = {"raw": raw_body}
    return {"configured": True, "status": "sent", "request": payload, "response": body}

def post_purchase_order_table_sync(*, purchase_order_id: str) -> dict[str, Any]:
    sync_url = os.getenv("FEISHU_PROCUREMENT_PURCHASE_ORDER_SYNC_URL", "").strip()
    if not sync_url:
        return {"configured": False, "status": "skipped"}
    payload = {"purchase_order_id": purchase_order_id, "limit": 1}
    request = urllib.request.Request(
        sync_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=PURCHASE_ORDER_TABLE_SYNC_TIMEOUT_SECONDS) as response:
            raw_body = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {"configured": True, "status": "failed", "error": str(error)}
    try:
        body = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        body = {"raw": raw_body}
    return {"configured": True, "status": "sent", "response": body}
@router.post("/warehouse/purchase-orders/today-arrivals")
def list_today_purchase_order_arrivals(
    payload: WarehousePurchaseOrderArrivalNotifyRequest,
) -> dict[str, Any]:
    target_date = normalize_target_date(payload.target_date)
    limit = max(min(int(payload.limit or 50), 500), 1)
    arrivals = today_purchase_order_arrivals(target_date=target_date, limit=limit)
    return {
        "ok": True,
        "target_date": target_date,
        "count": len(arrivals),
        "items": arrivals,
    }


@router.post("/warehouse/purchase-orders/arrival-notifications/send")
def send_purchase_arrival_notification(
    payload: WarehousePurchaseOrderArrivalNotifyRequest,
) -> dict[str, Any]:
    target_date = normalize_target_date(payload.target_date)
    arrivals = today_purchase_order_arrivals(
        target_date=target_date,
        limit=max(min(int(payload.limit or 50), 500), 1),
    )
    notification = post_purchase_arrival_notification(
        chat_id=payload.chat_id,
        target_date=target_date,
        items=arrivals,
    )
    return {
        "ok": notification["status"] != "failed",
        "target_date": target_date,
        "count": len(arrivals),
        "items": arrivals,
        "notification": notification,
        "next_action": "员工确认全部或指定采购单后，调用 /procurement/purchase-orders/confirm-arrival-batch。",
    }


def normalize_target_date(value: str | None) -> str:
    raw_value = str(value or "").strip()
    if raw_value:
        return raw_value
    return datetime.now(UTC).date().isoformat()


def today_purchase_order_arrivals(*, target_date: str, limit: int) -> list[dict[str, Any]]:
    repository = get_warehouse_repository()
    orders = list_purchase_orders(
        warehouse_sync_status="pending_arrival",
        payment_status="paid",
        repository=repository,
    )
    arrivals = [
        order
        for order in orders
        if str(order.get("estimated_arrival_date") or "").strip() == target_date
    ]
    return arrivals[:limit]


def post_purchase_arrival_notification(
    *,
    chat_id: str,
    target_date: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    notify_url = os.getenv("FEISHU_PURCHASE_ARRIVAL_NOTIFY_URL", "").strip()
    if not notify_url:
        return {"configured": False, "status": "skipped"}
    resolved_chat_id = (
        chat_id.strip()
        or os.getenv("FEISHU_PURCHASE_ARRIVAL_NOTIFY_CHAT_ID", "").strip()
        or os.getenv("FEISHU_FULFILLMENT_REVIEW_CHAT_ID", "").strip()
    )
    payload = {
        "chat_id": resolved_chat_id,
        "target_date": target_date,
        "items": items,
    }
    request = urllib.request.Request(
        notify_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=PURCHASE_ARRIVAL_NOTIFY_TIMEOUT_SECONDS) as response:
            raw_body = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {"configured": True, "status": "failed", "error": str(error)}
    try:
        body = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        body = {"raw": raw_body}
    return {"configured": True, "status": "sent", "response": body}


def sync_arrived_purchase_orders_fallback(
    *,
    limit: int,
    processed_by: str,
    processed_at: str,
    purchase_order_id: str | None = None,
) -> list[dict[str, Any]]:
    orders = list_purchase_orders(
        warehouse_sync_status="arrived_unsynced",
        payment_status="paid",
        purchase_order_id=purchase_order_id,
    )[:limit]
    synced_items: list[dict[str, Any]] = []
    for order in orders:
        item = item_by_id(str(order["item_id"]))
        production_day = _date_from_iso(str(order.get("arrived_at") or processed_at))
        expiry_date = date.fromordinal(
            production_day.toordinal() + int(item.get("shelf_life_days") or 365)
        ).isoformat()
        location_code = resolve_purchase_order_location(order)
        reorder_threshold = _deterministic_reorder_threshold(
            str(order["purchase_order_id"]),
            str(order["item_id"]),
            str(order["warehouse_id"]),
        )
        quantity = int(order["quantity"])
        upsert_received_inventory_balance(
            {
                "warehouse_id": order["warehouse_id"],
                "location_code": location_code,
                "item_id": order["item_id"],
                "production_date": production_day.isoformat(),
                "expiry_date": expiry_date,
                "quantity_on_hand": quantity,
                "quantity_reserved": 0,
                "reorder_threshold": reorder_threshold,
                "storage_status": "available",
            }
        )
        order["location_code"] = location_code
        order["warehouse_sync_status"] = "synced"
        order["updated_at"] = processed_at
        synced_items.append(
            {
                "purchase_order_id": order["purchase_order_id"],
                "item_id": order["item_id"],
                "warehouse_id": order["warehouse_id"],
                "warehouse_name": order["warehouse_name"],
                "location_code": location_code,
                "production_date": production_day.isoformat(),
                "expiry_date": expiry_date,
                "quantity": quantity,
                "reorder_threshold": reorder_threshold,
                "storage_status": "available",
                "payment_status": "paid",
                "warehouse_sync_status": "synced",
                "processed_by": processed_by,
                "processed_at": processed_at,
            }
        )
    return synced_items


def sync_purchase_order_inventory_fallback(
    *,
    purchase_order_id: str,
    processed_by: str,
    processed_at: str,
) -> dict[str, Any] | None:
    synced_items = sync_arrived_purchase_orders_fallback(
        limit=1,
        processed_by=processed_by,
        processed_at=processed_at,
        purchase_order_id=purchase_order_id,
    )
    return synced_items[0] if synced_items else None


def item_by_id(item_id: str) -> dict[str, Any]:
    return next(item for item in load_json("items.json") if item["item_id"] == item_id)


def resolve_purchase_order_location(order: dict[str, Any]) -> str:
    existing_locations = sorted(
        {
            row["location_code"]
            for row in [*load_json("inventory_location_balances.json"), *RECEIVED_INVENTORY_BALANCES]
            if row["item_id"] == order["item_id"] and row["warehouse_id"] == order["warehouse_id"]
        }
    )
    if existing_locations:
        return existing_locations[0]
    requested_location = str(order.get("location_code") or "").strip()
    if requested_location:
        return requested_location
    return next(
        row["location_code"]
        for row in sorted(load_json("storage_locations.json"), key=lambda item: item["location_code"])
        if row["warehouse_id"] == order["warehouse_id"]
    )


def upsert_received_inventory_balance(batch: dict[str, Any]) -> None:
    existing = next(
        (
            item
            for item in RECEIVED_INVENTORY_BALANCES
            if item["warehouse_id"] == batch["warehouse_id"]
            and item["location_code"] == batch["location_code"]
            and item["item_id"] == batch["item_id"]
        ),
        None,
    )
    if existing:
        existing["quantity_on_hand"] = int(existing["quantity_on_hand"]) + int(batch["quantity_on_hand"])
        existing["reorder_threshold"] = batch["reorder_threshold"]
        existing["storage_status"] = "available"
        return
    RECEIVED_INVENTORY_BALANCES.append(batch)
