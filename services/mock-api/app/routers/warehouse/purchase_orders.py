import json
import os
import urllib.error
import urllib.request
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter

from app.routers.procurement.service import list_purchase_orders
from app.store import load_json
from app.warehouse_store import _date_from_iso, _deterministic_reorder_threshold

from .schemas import WarehousePurchaseOrderArrivalNotifyRequest, WarehousePurchaseOrderArrivalSyncRequest
from .state import RECEIVED_INVENTORY_BATCHES, get_warehouse_repository

router = APIRouter()
PURCHASE_ARRIVAL_NOTIFY_TIMEOUT_SECONDS = 5


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
        "next_action": "已将已支付且未同步的采购到仓单写入库存批次表和库存余额表。",
    }


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
) -> list[dict[str, Any]]:
    orders = list_purchase_orders(
        warehouse_sync_status="arrived_unsynced",
        payment_status="paid",
    )[:limit]
    synced_items: list[dict[str, Any]] = []
    for order in orders:
        item = item_by_id(str(order["item_id"]))
        production_day = _date_from_iso(str(order.get("arrived_at") or processed_at))
        batch_no = f"BATCH-{production_day:%Y%m%d}"
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
        upsert_received_inventory_batch(
            {
                "warehouse_id": order["warehouse_id"],
                "location_code": location_code,
                "item_id": order["item_id"],
                "batch_no": batch_no,
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
                "request_id": order["request_id"],
                "item_id": order["item_id"],
                "warehouse_id": order["warehouse_id"],
                "warehouse_name": order["warehouse_name"],
                "location_code": location_code,
                "batch_no": batch_no,
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


def item_by_id(item_id: str) -> dict[str, Any]:
    return next(item for item in load_json("items.json") if item["item_id"] == item_id)


def resolve_purchase_order_location(order: dict[str, Any]) -> str:
    existing_locations = sorted(
        {
            row["location_code"]
            for row in [*load_json("inventory_batches.json"), *RECEIVED_INVENTORY_BATCHES]
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


def upsert_received_inventory_batch(batch: dict[str, Any]) -> None:
    existing = next(
        (
            item
            for item in RECEIVED_INVENTORY_BATCHES
            if item["warehouse_id"] == batch["warehouse_id"]
            and item["location_code"] == batch["location_code"]
            and item["item_id"] == batch["item_id"]
            and item["batch_no"] == batch["batch_no"]
        ),
        None,
    )
    if existing:
        existing["quantity_on_hand"] = int(existing["quantity_on_hand"]) + int(batch["quantity_on_hand"])
        existing["reorder_threshold"] = batch["reorder_threshold"]
        existing["storage_status"] = "available"
        return
    RECEIVED_INVENTORY_BATCHES.append(batch)
