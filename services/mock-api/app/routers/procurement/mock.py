from fastapi import APIRouter

from app.routers.warehouse.inventory import enrich_batch_row, load_batch_inventory_rows

router = APIRouter()


@router.post("/procurement/mock")
def procurement_mock(payload: dict) -> dict:
    item_id = str(payload.get("item_id") or payload.get("sku") or "").strip()
    rows = [enrich_batch_row(row) for row in load_batch_inventory_rows(item_id=item_id)] if item_id else []
    if not rows:
        return {
            "ok": False,
            "system": "mock-procurement",
            "item_id": item_id,
            "recommendation": "request_valid_item",
            "message": "未找到商品，需要提供有效 item_id。",
        }

    available = sum(int(row["quantity_available"]) for row in rows)
    reorder_threshold = max(int(row["reorder_threshold"]) for row in rows)
    should_replenish = any(row["quantity_available"] < row["reorder_threshold"] for row in rows)
    return {
        "ok": True,
        "system": "mock-procurement",
        "item_id": item_id,
        "available": available,
        "reorder_threshold": reorder_threshold,
        "recommendation": "create_purchase_request" if should_replenish else "no_action",
        "message": "库存低于阈值，建议创建采购申请。" if should_replenish else "当前库存无需补货。",
    }
