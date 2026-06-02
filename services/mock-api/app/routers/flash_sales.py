import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

try:
    import redis
except ImportError:  # pragma: no cover - runtime dependency guard
    redis = None

from app.routers.warehouse.orders import create_warehouse_order
from app.routers.warehouse.schemas import WarehouseOrderCreate
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


class FlashSalePurchaseRequest(BaseModel):
    user_id: int
    shipping_address: str
    delivery_provider_id: str = "sf"


def error_response(status_code: int, error: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "error": error, "message": message},
    )


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


def format_flash_sale(sale: dict[str, Any], stock_remaining: int) -> dict[str, Any]:
    return {
        "id": int(sale["id"]),
        "item_id": str(sale["item_id"]),
        "sale_price": float(sale["sale_price"]),
        "stock_limit": int(sale["stock_limit"]),
        "stock_remaining": stock_remaining,
        "status": str(sale["status"]),
        "starts_at": str(sale["starts_at"]),
        "ends_at": str(sale["ends_at"]),
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
    if existing_claim and existing_claim["status"] == "ordered":
        return {
            "ok": True,
            "claim": existing_claim,
            "order": {"order_id": existing_claim["order_id"], "status": "未付款"},
            "items": [],
        }
    if existing_claim and existing_claim["status"] == "pending":
        return error_response(409, "already_claimed", "user already claimed this flash sale")

    claim_result = claim_flash_sale_slot(redis_client, flash_sale_id, payload.user_id)
    if claim_result == "already_claimed":
        return error_response(409, "already_claimed", "user already claimed this flash sale")
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
        return error_response(409, "already_claimed", "user already claimed this flash sale")

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

    ordered_claim = repository.mark_flash_sale_claim_ordered(
        claim["id"],
        order_id=created["order"]["order_id"],
        updated_at=datetime.now(UTC).isoformat(),
    )
    return {"ok": True, "claim": ordered_claim, **created}
