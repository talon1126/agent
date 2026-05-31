from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.routers.warehouse.inventory import load_batch_inventory_rows
from app.routers.warehouse.state import get_warehouse_repository
from app.store import load_json

router = APIRouter()


SEARCH_FIELDS = ("item_id", "item_name", "brand", "spec")


def load_search_items(query: str | None = None) -> list[dict[str, Any]]:
    normalized_query = (query or "").casefold()
    repository = get_warehouse_repository()
    if repository:
        return repository.search_items(normalized_query)
    rows = load_json("items.json")
    if not normalized_query:
        return rows
    return [
        row
        for row in rows
        if any(normalized_query in str(row.get(field) or "").casefold() for field in SEARCH_FIELDS)
    ]


def load_search_balance_rows() -> list[dict[str, Any]]:
    repository = get_warehouse_repository()
    if repository:
        return repository.list_inventory_balance_snapshots()
    return load_batch_inventory_rows()


def matches_product_query(row: dict[str, Any], query: str) -> bool:
    normalized_query = query.casefold()
    return any(normalized_query in str(row.get(field) or "").casefold() for field in SEARCH_FIELDS)


def balance_id(row: dict[str, Any], fallback_id: int) -> int:
    raw_id = row.get("id", row.get("batch_id", fallback_id))
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return fallback_id


def product_search_items(query: str) -> list[dict[str, Any]]:
    products: dict[str, dict[str, Any]] = {}
    for row in load_search_items(query):
        item_id = str(row["item_id"])
        products[item_id] = {
            "item_id": item_id,
            "item_name": row["item_name"],
            "brand": row["brand"],
            "spec": row["spec"],
            "category_id": row["category_id"],
            "balances": [],
        }

    for index, row in enumerate(load_search_balance_rows(), start=1):
        item_id = str(row["item_id"])
        product = products.get(item_id)
        if not product:
            continue
        product["balances"].append(
            {
                "id": balance_id(row, index),
                "warehouse_id": row["warehouse_id"],
                "item_id": item_id,
                "quantity_on_hand": int(row["quantity_on_hand"]),
                "storage_status": row["storage_status"],
            }
        )
    return list(products.values())


@router.get("/search")
def search_products(q: str | None = None):
    query = (q or "").strip()
    if not query:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "missing_query", "message": "q is required"},
        )

    items = product_search_items(query)
    return {"ok": True, "query": query, "count": len(items), "items": items}
