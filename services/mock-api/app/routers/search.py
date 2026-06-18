from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.routers.product_details import item_rating_from_reviews
from app.routers.warehouse.inventory import load_batch_inventory_rows
from app.routers.warehouse.state import get_warehouse_repository

router = APIRouter()

PRODUCT_OPERATIONS_TABLE_SCHEMA = [
    {"name": "Item ID", "type": "text"},
    {"name": "Image", "type": "text"},
    {"name": "Item Name", "type": "text"},
    {"name": "Brand", "type": "text"},
    {"name": "Category", "type": "text"},
    {"name": "Price", "type": "number"},
    {"name": "Rating", "type": "number"},
    {"name": "Review Count", "type": "number"},
    {
        "name": "Flash Deal Status",
        "type": "single_select",
        "options": [
            {"name": "none", "color": 24},
            {"name": "active", "color": 28},
            {"name": "draft", "color": 21},
            {"name": "paused", "color": 19},
            {"name": "ended", "color": 20},
        ],
    },
    {"name": "Flash Sale Price", "type": "number"},
    {"name": "Ranking Label", "type": "text"},
    {"name": "Ranking Score", "type": "number"},
    {"name": "Source Version", "type": "text"},
]

ITEMS_TABLE_SCHEMA = [
    {"name": "Item ID", "type": "text"},
    {"name": "Image", "type": "text"},
    {"name": "Item Name", "type": "text"},
    {"name": "Brand", "type": "text"},
    {"name": "Category", "type": "text"},
    {"name": "Spec", "type": "text"},
    {"name": "Price", "type": "number"},
    {"name": "Rating", "type": "number"},
    {"name": "Review Count", "type": "number"},
    {"name": "Source Version", "type": "text"},
]


class ProductOperationsTableRowsRequest(BaseModel):
    """Request contract for the Product Operations Feishu read model.

    Args:
        category_id: Optional backend category id used by Feishu page filters.
        limit: Maximum number of product rows returned to feishu-adapter.
    """

    category_id: str | None = None
    limit: int = 100


class ItemsTableRowsRequest(BaseModel):
    """Request contract for the standalone Items Feishu read model.

    Args:
        category_id: Optional backend category id used by Feishu page filters.
        limit: Maximum number of catalog rows returned to feishu-adapter.
    """

    category_id: str | None = None
    limit: int = 100


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
            "image": str(row.get("image") or ""),
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


def active_flash_sales_by_item(repository: Any) -> dict[str, dict[str, Any]]:
    """Return active Flash Deal rows keyed by item id.

    Args:
        repository: Warehouse repository or test double exposing
            `list_flash_sales`.

    Returns:
        A dictionary keyed by `item_id`. Missing repository capabilities degrade
        to an empty map so Product Operations can still sync catalog rows.
    """

    if not hasattr(repository, "list_flash_sales"):
        return {}
    try:
        rows = repository.list_flash_sales(status="active", limit=100)
    except Exception:
        return {}
    return {str(row.get("item_id")): row for row in rows if row.get("item_id")}


def ranking_rows_by_item(repository: Any, *, category_id: str | None, limit: int) -> dict[str, dict[str, Any]]:
    """Return leaderboard rows keyed by item id for Product Operations.

    Args:
        repository: Warehouse repository or test double exposing ranking reads.
        category_id: Optional category filter from the Feishu table request.
        limit: Maximum number of ranking rows to inspect.

    Returns:
        Ranking rows keyed by `item_id`. Ranking is optional enrichment, so any
        unavailable ranking backend returns an empty map.
    """

    try:
        if category_id and hasattr(repository, "get_category_ranking"):
            rows = repository.get_category_ranking(
                category_id=category_id,
                rank_type="hot",
                window_type="all_time",
                limit=limit,
            )
        elif hasattr(repository, "list_home_hot_rankings"):
            rows = repository.list_home_hot_rankings(rank_type="hot", window_type="all_time", limit=limit)
        else:
            rows = []
    except Exception:
        rows = []
    return {str(row.get("item_id")): row for row in rows if row.get("item_id")}


def product_operations_table_fields(
    product: dict[str, Any],
    *,
    rating: dict[str, Any],
    flash_sale: dict[str, Any] | None,
    ranking: dict[str, Any] | None,
) -> dict[str, Any]:
    """Convert one product into Feishu Product Operations table fields.

    Args:
        product: Catalog product row from PostgreSQL search.
        rating: Review summary for the product.
        flash_sale: Optional active Flash Deal row for the product.
        ranking: Optional leaderboard row for the product.

    Returns:
        Field dictionary matching `PRODUCT_OPERATIONS_TABLE_SCHEMA`. Internal
        database row ids and category ids are deliberately omitted from fields;
        Feishu pages display human-readable business columns only.
    """

    category_name = product.get("category_name") or product.get("category_id") or ""
    ranking_label = ""
    if ranking:
        ranking_label = f"#{ranking.get('rank')} in {ranking.get('category_name') or category_name}"
    flash_status = str((flash_sale or {}).get("status") or "none")
    source_parts = [str(product["item_id"])]
    if flash_sale:
        source_parts.append(str(flash_sale.get("updated_at") or flash_sale.get("id") or "flash"))
    if ranking:
        source_parts.append(str(ranking.get("generated_at") or ranking.get("score") or "rank"))
    return {
        "Item ID": product["item_id"],
        "Image": str(product.get("image") or ""),
        "Item Name": product["item_name"],
        "Brand": product.get("brand") or "",
        "Category": category_name,
        "Price": float(product.get("price") or 0),
        "Rating": float(rating.get("average_rating") or rating.get("score") or 0),
        "Review Count": int(rating.get("review_count") or rating.get("count") or 0),
        "Flash Deal Status": flash_status,
        "Flash Sale Price": float(flash_sale["sale_price"]) if flash_sale else "",
        "Ranking Label": ranking_label,
        "Ranking Score": float(ranking.get("score") or 0) if ranking else "",
        "Source Version": "mock-api:" + ":".join(source_parts),
    }


def items_table_fields(product: dict[str, Any], *, rating: dict[str, Any]) -> dict[str, Any]:
    """Convert one catalog product into the standalone Feishu Items table.

    Args:
        product: Catalog product row from PostgreSQL search.
        rating: Review summary for the product.

    Returns:
        Employee-facing field map with image, display category, price, and
        rating fields. Internal category ids remain out of the Feishu table.
    """

    category_name = product.get("category_name") or product.get("category_id") or ""
    return {
        "Item ID": product["item_id"],
        "Image": str(product.get("image") or ""),
        "Item Name": product["item_name"],
        "Brand": product.get("brand") or "",
        "Category": category_name,
        "Spec": product.get("spec") or "",
        "Price": float(product.get("price") or 0),
        "Rating": float(rating.get("average_rating") or rating.get("score") or 0),
        "Review Count": int(rating.get("review_count") or rating.get("count") or 0),
        "Source Version": f"mock-api:{product['item_id']}",
    }


def load_items_table_rows(*, category_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Load standalone Items rows for the Feishu read-model sync endpoint.

    Args:
        category_id: Optional backend category id used by Feishu page filters.
        limit: Requested row limit. The function caps this value before reading
            PostgreSQL so scheduled n8n runs cannot request an unbounded export.

    Returns:
        Rows with stable `item_id` identities and Feishu field maps. The field
        map keeps category display names and image URLs, but does not expose the
        internal category id in the employee-facing table.

    Raises:
        SearchBackendUnavailable: When the PostgreSQL-backed catalog repository
        is unavailable. Items table sync must use real catalog facts.
    """

    repository = get_warehouse_repository()
    if not repository:
        raise SearchBackendUnavailable("Postgres backend is required for Items table rows")
    normalized_category = (category_id or "").strip() or None
    capped_limit = max(min(int(limit or 100), 500), 1)
    products = repository.search_items(None, category_id=normalized_category)[:capped_limit]
    rows = []
    for product in products:
        item_id = str(product["item_id"])
        rating = repository.item_review_summary(item_id) if hasattr(repository, "item_review_summary") else {}
        rows.append({"item_id": item_id, "fields": items_table_fields(product, rating=rating)})
    return rows


def load_product_operations_table_rows(
    *,
    category_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Load Product Operations rows for the Feishu read-model sync endpoint.

    Args:
        category_id: Optional backend category id filter.
        limit: Maximum number of rows to return after catalog lookup.

    Returns:
        A list of row dictionaries containing item ids and Feishu field maps.

    Raises:
        SearchBackendUnavailable: When PostgreSQL-backed catalog search is not
        available. Product Operations must bind real catalog facts.
    """

    repository = get_warehouse_repository()
    if not repository:
        raise SearchBackendUnavailable("Postgres backend is required for Product Operations table rows")
    normalized_category = (category_id or "").strip() or None
    capped_limit = max(min(int(limit or 100), 500), 1)
    products = repository.search_items(None, category_id=normalized_category)[:capped_limit]
    flash_sales = active_flash_sales_by_item(repository)
    rankings = ranking_rows_by_item(repository, category_id=normalized_category, limit=capped_limit)
    rows = []
    for product in products:
        item_id = str(product["item_id"])
        rating = repository.item_review_summary(item_id) if hasattr(repository, "item_review_summary") else {}
        rows.append(
            {
                "item_id": item_id,
                "fields": product_operations_table_fields(
                    product,
                    rating=rating,
                    flash_sale=flash_sales.get(item_id),
                    ranking=rankings.get(item_id),
                ),
            }
        )
    return rows


@router.get("/products/operations/table-schema")
def get_product_operations_table_schema() -> dict[str, Any]:
    """Return the Feishu table schema for the Product Operations read model."""

    return {
        "ok": True,
        "schema_id": "product_operations",
        "source": "mock-api",
        "fields": PRODUCT_OPERATIONS_TABLE_SCHEMA,
    }


@router.get("/items/table-schema")
def get_items_table_schema() -> dict[str, Any]:
    """Return the Feishu schema contract for the standalone Items table.

    Returns:
        A stable schema id and field list used by feishu-adapter to create or
        repair the Items Bitable before row upsert.
    """

    return {
        "ok": True,
        "schema_id": "items",
        "source": "mock-api",
        "fields": ITEMS_TABLE_SCHEMA,
    }


@router.post("/items/table-rows")
def get_items_table_rows(payload: ItemsTableRowsRequest):
    """Return standalone Items rows consumed by feishu-adapter table sync.

    Args:
        payload: Optional category filter and requested limit from n8n or a
            manual sync trigger.

    Returns:
        A standard mock-api read-model payload on success. Repository failures
        are converted to the existing JSON error envelope so feishu-adapter can
        report a deterministic sync error.
    """

    try:
        rows = load_items_table_rows(category_id=payload.category_id, limit=payload.limit)
    except SearchBackendUnavailable as error:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "items_backend_unavailable", "message": str(error)},
        )
    return {
        "ok": True,
        "schema_id": "items",
        "source": "mock-api",
        "count": len(rows),
        "items": rows,
    }


@router.post("/products/operations/table-rows")
def get_product_operations_table_rows(payload: ProductOperationsTableRowsRequest):
    """Return Product Operations rows consumed by feishu-adapter table sync."""

    try:
        rows = load_product_operations_table_rows(category_id=payload.category_id, limit=payload.limit)
    except SearchBackendUnavailable as error:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "product_operations_backend_unavailable", "message": str(error)},
        )
    return {
        "ok": True,
        "schema_id": "product_operations",
        "count": len(rows),
        "items": rows,
    }


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
