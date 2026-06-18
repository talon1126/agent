import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

try:
    import redis
except ImportError:  # pragma: no cover - runtime dependency guard
    redis = None

from app.routers.warehouse.orders import create_warehouse_order, pay_warehouse_order
from app.routers.warehouse.schemas import WarehouseOrderCreate, WarehouseOrderStatusUpdateRequest
from app.routers.warehouse.state import get_warehouse_repository

router = APIRouter()

FLASH_SALE_CLAIM_LUA = """
local stock = tonumber(redis.call('GET', KEYS[1]) or '-1')
if stock < 0 then
  return 'not_initialized'
end
if redis.call('SISMEMBER', KEYS[2], ARGV[1]) == 1 then
  return 'already_claimed'
end
if stock <= 0 then
  return 'sold_out'
end
redis.call('DECR', KEYS[1])
redis.call('SADD', KEYS[2], ARGV[1])
return 'ok'
"""

FLASH_SALE_REDIS_CLIENT: Any = None
PURCHASE_LIMIT_ERROR = "purchase_limit_reached"
PURCHASE_LIMIT_MESSAGE = "已达到购买上限"


class FlashSalePurchaseRequest(BaseModel):
    user_id: int
    shipping_address: str
    delivery_provider_id: str = "sf"


class FlashSalesTableRowsRequest(BaseModel):
    """Filter payload used by the Feishu Flash Sales table sync endpoint.

    The request is intentionally small because the Feishu read model only needs
    a bounded activity list. `status` lets n8n refresh one operational state,
    while `limit` protects mock-api and feishu-adapter from accidentally
    transferring an unbounded result set during scheduled syncs.
    """

    status: str | None = None
    limit: int = 100


class FlashSaleClaimsTableRowsRequest(BaseModel):
    """Filter payload used by the Feishu Flash Sale Claims sync endpoint.

    Claims are the result rows for flash-sale participation. The optional
    `flash_sale_id` and `status` filters support focused operator views, and
    `limit` is capped again inside the route before the repository is called.
    """

    flash_sale_id: int | None = None
    status: str | None = None
    limit: int = 100


FLASH_SALES_TABLE_SCHEMA = [
    {"name": "Flash Sale ID", "type": "text"},
    {"name": "Item ID", "type": "text"},
    {"name": "Sale Price", "type": "number"},
    {"name": "Original Price", "type": "number"},
    {"name": "Stock Limit", "type": "number"},
    {"name": "Stock Remaining", "type": "number"},
    {
        "name": "Status",
        "type": "single_select",
        "options": [
            {"name": "draft", "color": 21},
            {"name": "active", "color": 28},
            {"name": "paused", "color": 19},
            {"name": "ended", "color": 20},
        ],
    },
    {"name": "Starts At", "type": "datetime"},
    {"name": "Ends At", "type": "datetime"},
    {"name": "Source Version", "type": "text"},
]

FLASH_SALE_CLAIMS_TABLE_SCHEMA = [
    {"name": "Claim ID", "type": "text"},
    {"name": "Flash Sale ID", "type": "text"},
    {"name": "User ID", "type": "number"},
    {"name": "Item ID", "type": "text"},
    {"name": "Order ID", "type": "text"},
    {
        "name": "Status",
        "type": "single_select",
        "options": [
            {"name": "pending", "color": 24},
            {"name": "ordered", "color": 28},
            {"name": "failed", "color": 17},
        ],
    },
    {"name": "Created At", "type": "datetime"},
    {"name": "Updated At", "type": "datetime"},
    {"name": "Source Version", "type": "text"},
]

def error_response(status_code: int, error: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "error": error, "message": message},
    )


def purchase_limit_response() -> JSONResponse:
    return error_response(409, PURCHASE_LIMIT_ERROR, PURCHASE_LIMIT_MESSAGE)


def flash_sale_stock_key(flash_sale_id: int) -> str:
    return f"flash_sale:{flash_sale_id}:stock"


def flash_sale_users_key(flash_sale_id: int) -> str:
    return f"flash_sale:{flash_sale_id}:users"


def get_flash_sale_redis():
    global FLASH_SALE_REDIS_CLIENT
    if FLASH_SALE_REDIS_CLIENT is not None:
        return FLASH_SALE_REDIS_CLIENT
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url or redis is None:
        return None
    FLASH_SALE_REDIS_CLIENT = redis.Redis.from_url(redis_url, decode_responses=True)
    return FLASH_SALE_REDIS_CLIENT


def parse_timestamp(value: str) -> datetime:
    normalized = str(value or "").replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def format_flash_sale(sale: dict[str, Any], stock_remaining: int | None) -> dict[str, Any]:
    return {
        "id": int(sale["id"]),
        "item_id": str(sale["item_id"]),
        "item_price": float(sale["item_price"]) if sale.get("item_price") is not None else None,
        "sale_price": float(sale["sale_price"]),
        "stock_limit": int(sale["stock_limit"]),
        "stock_remaining": stock_remaining,
        "status": str(sale["status"]),
        "starts_at": str(sale["starts_at"]),
        "ends_at": str(sale["ends_at"]),
    }


def flash_sale_table_fields(sale: dict[str, Any], stock_remaining: int | None) -> dict[str, Any]:
    """Convert one flash sale row into employee-facing Feishu fields.

    Args:
        sale: PostgreSQL flash-sale row containing item, price, quota, status,
            and activity window fields.
        stock_remaining: Redis-backed live quota value. `None` means Redis is
            unavailable or the sale has not been initialized, so the Feishu
            cell is left blank instead of inventing a value.

    Returns:
        A field map using business-readable names that match the Flash Sales
        Bitable schema. The Source Version field gives the upsert layer a
        simple change marker without exposing internal database row versions.
    """

    flash_sale_id = str(sale["id"])
    return {
        "Flash Sale ID": flash_sale_id,
        "Item ID": str(sale["item_id"]),
        "Sale Price": float(sale["sale_price"]),
        "Original Price": float(sale["item_price"]) if sale.get("item_price") is not None else "",
        "Stock Limit": int(sale["stock_limit"]),
        "Stock Remaining": int(stock_remaining) if stock_remaining is not None else "",
        "Status": str(sale["status"]),
        "Starts At": str(sale["starts_at"]),
        "Ends At": str(sale["ends_at"]),
        "Source Version": f"mock-api:flash-sale:{flash_sale_id}:{sale.get('updated_at') or sale.get('status')}",
    }


def flash_sale_claim_table_fields(claim: dict[str, Any]) -> dict[str, Any]:
    """Convert one flash-sale claim row into Feishu result-table fields.

    Args:
        claim: PostgreSQL claim row with user, item, order, status, and
            timestamp fields.

    Returns:
        A field map for the Flash Sale Claims table. The public Claim ID is the
        sync identity, while related ids are kept as business references for
        operators who need to reconcile claims with orders.
    """

    claim_id = str(claim["id"])
    status = str(claim.get("status") or "")
    return {
        "Claim ID": claim_id,
        "Flash Sale ID": str(claim.get("flash_sale_id") or ""),
        "User ID": int(claim.get("user_id") or 0),
        "Item ID": str(claim.get("item_id") or ""),
        "Order ID": str(claim.get("order_id") or ""),
        "Status": status,
        "Created At": str(claim.get("created_at") or ""),
        "Updated At": str(claim.get("updated_at") or ""),
        "Source Version": f"mock-api:flash-sale-claim:{claim_id}:{status}",
    }


def normalize_redis_result(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def flash_sale_is_active(sale: dict[str, Any], now: datetime) -> bool:
    return (
        str(sale["status"]) == "active"
        and parse_timestamp(str(sale["starts_at"])) <= now
        and now <= parse_timestamp(str(sale["ends_at"]))
    )


def compensate_flash_sale_claim(redis_client: Any, flash_sale_id: int, user_id: int) -> None:
    redis_client.incr(flash_sale_stock_key(flash_sale_id))
    redis_client.srem(flash_sale_users_key(flash_sale_id), str(user_id))


def claim_flash_sale_slot(redis_client: Any, flash_sale_id: int, user_id: int) -> str:
    result = redis_client.eval(
        FLASH_SALE_CLAIM_LUA,
        2,
        flash_sale_stock_key(flash_sale_id),
        flash_sale_users_key(flash_sale_id),
        str(user_id),
    )
    return normalize_redis_result(result)


def initialize_active_flash_sales() -> dict[str, int]:
    repository = get_warehouse_repository()
    if not repository:
        return {"initialized": 0}
    redis_client = get_flash_sale_redis()
    if not redis_client:
        return {"initialized": 0}

    initialized = 0
    for sale in repository.list_flash_sales(status="active", limit=100):
        flash_sale_id = int(sale["id"])
        # Demo database startup resets flash-sale claims, so Redis participation
        # sets must be reset at the same time to keep the local demo coherent.
        redis_client.set(flash_sale_stock_key(flash_sale_id), int(sale["stock_limit"]))
        redis_client.delete(flash_sale_users_key(flash_sale_id))
        initialized += 1
    return {"initialized": initialized}


@router.get("/flash-sales", response_model=None)
def list_flash_sales(
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
):
    repository = get_warehouse_repository()
    if not repository:
        return error_response(503, "flash_sale_backend_unavailable", "Postgres backend is required")
    redis_client = get_flash_sale_redis()
    if not redis_client:
        return error_response(503, "flash_sale_backend_unavailable", "Redis backend is required")

    sales = repository.list_flash_sales(status=status, limit=limit)
    formatted_sales = []
    for sale in sales:
        raw_stock = redis_client.get(flash_sale_stock_key(int(sale["id"])))
        stock_remaining = int(raw_stock) if raw_stock is not None else None
        formatted_sales.append(format_flash_sale(sale, stock_remaining))

    return {
        "ok": True,
        "count": len(formatted_sales),
        "flash_sales": formatted_sales,
    }


@router.get("/flash-sales/table-schema")
def get_flash_sales_table_schema() -> dict[str, Any]:
    """Return the schema contract consumed by feishu-adapter.

    Returns:
        A stable schema id and field list for the Flash Sales Bitable. The
        adapter uses this response to create missing fields and to keep field
        names consistent across scheduled sync runs.
    """

    return {
        "ok": True,
        "schema_id": "flash_sales",
        "source": "mock-api",
        "fields": FLASH_SALES_TABLE_SCHEMA,
    }


@router.post("/flash-sales/table-rows", response_model=None)
def get_flash_sales_table_rows(payload: FlashSalesTableRowsRequest) -> Any:
    """Return Flash Sales rows for the scheduled Feishu table sync.

    Args:
        payload: Optional status filter and requested row limit from n8n or a
            manual sync call.

    Returns:
        On success, a read-model payload containing row count and Feishu field
        maps. On backend failure, a JSONResponse with the existing mock-api
        error envelope is returned so the adapter can log a clear sync failure.

    Side Effects:
        Reads PostgreSQL flash-sale facts and, when Redis is available, reads
        live stock counters. It does not mutate sale state.
    """

    repository = get_warehouse_repository()
    if not repository:
        return error_response(503, "flash_sale_backend_unavailable", "Postgres backend is required")
    redis_client = get_flash_sale_redis()
    sales = repository.list_flash_sales(status=payload.status, limit=max(min(int(payload.limit or 100), 500), 1))
    rows = []
    for sale in sales:
        stock_remaining = None
        if redis_client:
            raw_stock = redis_client.get(flash_sale_stock_key(int(sale["id"])))
            stock_remaining = int(raw_stock) if raw_stock is not None else None
        flash_sale_id = str(sale["id"])
        rows.append({"flash_sale_id": flash_sale_id, "fields": flash_sale_table_fields(sale, stock_remaining)})
    return {"ok": True, "schema_id": "flash_sales", "source": "mock-api", "count": len(rows), "items": rows}


@router.get("/flash-sales/claims/table-schema")
def get_flash_sale_claims_table_schema() -> dict[str, Any]:
    """Return the schema contract for Flash Sale Claims.

    Returns:
        A stable schema id and field list for the claim-result table used by
        the Feishu business application.
    """

    return {
        "ok": True,
        "schema_id": "flash_sale_claims",
        "source": "mock-api",
        "fields": FLASH_SALE_CLAIMS_TABLE_SCHEMA,
    }


@router.post("/flash-sales/claims/table-rows", response_model=None)
def get_flash_sale_claims_table_rows(payload: FlashSaleClaimsTableRowsRequest) -> Any:
    """Return Flash Sale Claims rows for the scheduled Feishu table sync.

    Args:
        payload: Optional flash-sale id, optional claim status, and requested
            row limit.

    Returns:
        A read-model payload containing claim identities and Feishu field maps,
        or the standard mock-api JSON error envelope when the repository cannot
        provide claim rows.
    """

    repository = get_warehouse_repository()
    if not repository or not hasattr(repository, "list_flash_sale_claims"):
        return error_response(503, "flash_sale_claim_backend_unavailable", "Postgres backend is required")
    claims = repository.list_flash_sale_claims(
        flash_sale_id=payload.flash_sale_id,
        status=payload.status,
        limit=max(min(int(payload.limit or 100), 500), 1),
    )
    rows = [
        {"claim_id": str(claim["id"]), "fields": flash_sale_claim_table_fields(claim)}
        for claim in claims
    ]
    return {"ok": True, "schema_id": "flash_sale_claims", "source": "mock-api", "count": len(rows), "items": rows}


@router.get("/flash-sales/{flash_sale_id}", response_model=None)
def get_flash_sale(flash_sale_id: int):
    repository = get_warehouse_repository()
    if not repository:
        return error_response(503, "flash_sale_backend_unavailable", "Postgres backend is required")
    redis_client = get_flash_sale_redis()
    if not redis_client:
        return error_response(503, "flash_sale_backend_unavailable", "Redis backend is required")
    sale = repository.get_flash_sale(flash_sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="flash_sale_not_found")
    raw_stock = redis_client.get(flash_sale_stock_key(flash_sale_id))
    if raw_stock is None:
        return error_response(503, "flash_sale_not_initialized", "flash sale redis stock is not initialized")
    return {"ok": True, "flash_sale": format_flash_sale(sale, int(raw_stock))}


@router.post("/flash-sales/{flash_sale_id}/activate", response_model=None)
def activate_flash_sale(flash_sale_id: int):
    repository = get_warehouse_repository()
    if not repository:
        return error_response(503, "flash_sale_backend_unavailable", "Postgres backend is required")
    redis_client = get_flash_sale_redis()
    if not redis_client:
        return error_response(503, "flash_sale_backend_unavailable", "Redis backend is required")
    sale = repository.get_flash_sale(flash_sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="flash_sale_not_found")
    redis_client.set(flash_sale_stock_key(flash_sale_id), int(sale["stock_limit"]))
    redis_client.delete(flash_sale_users_key(flash_sale_id))
    updated_at = datetime.now(UTC).isoformat()
    updated_sale = repository.update_flash_sale_status(flash_sale_id, status="active", updated_at=updated_at) or sale
    return {
        "ok": True,
        "flash_sale": format_flash_sale(updated_sale, int(updated_sale["stock_limit"])),
    }


@router.post("/flash-sales/{flash_sale_id}/purchase", response_model=None)
def purchase_flash_sale(
    flash_sale_id: int,
    payload: FlashSalePurchaseRequest,
):
    repository = get_warehouse_repository()
    if not repository:
        return error_response(503, "flash_sale_backend_unavailable", "Postgres backend is required")
    redis_client = get_flash_sale_redis()
    if not redis_client:
        return error_response(503, "flash_sale_backend_unavailable", "Redis backend is required")
    if not payload.shipping_address.strip():
        return error_response(400, "shipping_address_required", "shipping_address is required")

    sale = repository.get_flash_sale(flash_sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="flash_sale_not_found")
    now = datetime.now(UTC)
    if not flash_sale_is_active(sale, now):
        return error_response(409, "flash_sale_not_active", "flash sale is not active")

    existing_claim = repository.get_flash_sale_claim(
        flash_sale_id=flash_sale_id,
        user_id=payload.user_id,
    )
    if existing_claim and existing_claim["status"] in {"ordered", "pending"}:
        return purchase_limit_response()

    claim_result = claim_flash_sale_slot(redis_client, flash_sale_id, payload.user_id)
    if claim_result == "already_claimed":
        return purchase_limit_response()
    if claim_result == "sold_out":
        return error_response(409, "sold_out", "flash sale stock is sold out")
    if claim_result == "not_initialized":
        return error_response(503, "flash_sale_not_initialized", "flash sale redis stock is not initialized")
    if claim_result != "ok":
        return error_response(503, "flash_sale_backend_unavailable", "flash sale redis claim failed")

    timestamp = now.isoformat()
    try:
        claim = repository.create_flash_sale_claim_pending(
            flash_sale_id=flash_sale_id,
            user_id=payload.user_id,
            item_id=str(sale["item_id"]),
            created_at=timestamp,
        )
    except IntegrityError:
        compensate_flash_sale_claim(redis_client, flash_sale_id, payload.user_id)
        return purchase_limit_response()

    try:
        created = create_warehouse_order(
            WarehouseOrderCreate(
                customer_id=str(payload.user_id),
                delivery_provider_id=payload.delivery_provider_id,
                shipping_address=payload.shipping_address,
                items=[{"item_id": str(sale["item_id"]), "quantity": 1}],
                created_by="flash-sale",
            )
        )
    except HTTPException as error:
        compensate_flash_sale_claim(redis_client, flash_sale_id, payload.user_id)
        repository.mark_flash_sale_claim_failed(claim["id"], updated_at=datetime.now(UTC).isoformat())
        raise error

    paid_order = pay_warehouse_order(
        created["order"]["order_id"],
        WarehouseOrderStatusUpdateRequest(updated_by="flash-sale"),
    )
    ordered_claim = repository.mark_flash_sale_claim_ordered(
        claim["id"],
        order_id=paid_order["order"]["order_id"],
        updated_at=datetime.now(UTC).isoformat(),
    )
    return {"ok": True, "claim": ordered_claim, **paid_order}
