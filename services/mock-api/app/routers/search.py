from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.routers.product_details import item_rating_from_reviews
from app.routers.warehouse.inventory import load_batch_inventory_rows
from app.routers.warehouse.state import get_warehouse_repository

router = APIRouter()


class SearchBackendUnavailable(RuntimeError):
    pass


SUPPORTED_DEPARTMENT_CATEGORIES = {
    "grocery": "grocery",
    "clothing-shoes-accessories": "clothing_shoes_accessories",
    "baby-kids": "baby_kids",
    "electronics": "electronics",
}


def normalize_category_slug(category: str | None) -> str | None:
    """Normalize a storefront department slug into the backend category id.

    Args:
        category: Raw query parameter from `/search?category=...`.

    Returns:
        The canonical category id used by the `items.category_id` column, or
        `None` when no category was supplied.
    """
    normalized = (category or "").strip().lower()
    if not normalized:
        return None
    return SUPPORTED_DEPARTMENT_CATEGORIES.get(normalized, normalized)


def load_search_items(query: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
    """Load product rows from the warehouse repository search backend.

    Args:
        query: Optional keyword used by the standard search page.
        category: Optional normalized category id used by Departments browsing.

    Returns:
        Product rows that match the keyword and/or category filter.

    Raises:
        SearchBackendUnavailable: When the PostgreSQL-backed repository is not
        available, because storefront search depends on pg_search.
    """
    repository = get_warehouse_repository()
    if repository:
        return repository.search_items(query, category_id=category)
    raise SearchBackendUnavailable("Postgres pg_search backend is required for /search")


def load_search_balance_rows() -> list[dict[str, Any]]:
    repository = get_warehouse_repository()
    if repository:
        return repository.list_inventory_balance_snapshots()
    return load_batch_inventory_rows()


def load_item_rating(item_id: str) -> dict[str, Any] | None:
    """Load customer-review rating for a product search card.

    Args:
        item_id: Product id returned by the search backend.

    Returns:
        The review-backed rating, or `None` when the item has no reviews.
    """
    repository = get_warehouse_repository()
    if not repository:
        return None
    return item_rating_from_reviews(repository, item_id)


def balance_id(row: dict[str, Any], fallback_id: int) -> int:
    raw_id = row.get("id", row.get("batch_id", fallback_id))
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return fallback_id


def product_search_items(query: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
    """Build search response products with inventory balance snapshots.

    Args:
        query: Optional keyword from the search page.
        category: Optional normalized category id from the Departments route.

    Returns:
        Product dictionaries enriched with `balances` for the frontend cards.
        Products without balance rows remain visible with an empty balance list so
        category browsing can still show newly seeded catalog items.
    """
    products: dict[str, dict[str, Any]] = {}
    for row in load_search_items(query, category):
        item_id = str(row["item_id"])
        products[item_id] = {
            "item_id": item_id,
            "item_name": row["item_name"],
            "brand": row["brand"],
            "spec": row["spec"],
            "category_id": row["category_id"],
            "price": float(row["price"]),
            "rating": load_item_rating(item_id),
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
def search_products(q: str | None = None, category: str | None = None):
    query = (q or "").strip()
    category_id = normalize_category_slug(category)
    if not query and not category_id:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "missing_query", "message": "q is required"},
        )

    try:
        items = product_search_items(query or None, category_id)
    except SearchBackendUnavailable as error:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "search_backend_unavailable", "message": str(error)},
        )
    response = {"ok": True, "query": query, "count": len(items), "items": items}
    if category_id:
        response["category"] = category_id
    return response
