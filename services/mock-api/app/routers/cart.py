from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.routers.warehouse.state import get_warehouse_repository
from app.store import load_json

router = APIRouter()

CART_ITEMS: list[dict[str, Any]] = []
DEFAULT_USER_IDS = {1, 2}


class AddCartItemRequest(BaseModel):
    user_id: int
    item_id: str
    item_name: str
    price: float
    quantity: int = 1


def error_response(status_code: int, error: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "error": error, "message": message},
    )


def cart_item_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "user_id": int(row["user_id"]),
        "item_id": str(row["item_id"]),
        "item_name": str(row["item_name"]),
        "price": float(row["price"]),
        "quantity": int(row["quantity"]),
    }


def user_exists(user_id: int) -> bool:
    repository = get_warehouse_repository()
    if repository:
        return repository.user_exists(user_id)
    return user_id in DEFAULT_USER_IDS


def fixture_item(item_id: str) -> dict[str, Any] | None:
    return next((item for item in load_json("items.json") if item["item_id"] == item_id), None)


def add_cart_item_in_memory(payload: AddCartItemRequest) -> dict[str, Any] | None:
    item = fixture_item(payload.item_id)
    if not item:
        return None
    existing = next(
        (row for row in CART_ITEMS if row["user_id"] == payload.user_id and row["item_id"] == payload.item_id),
        None,
    )
    if existing:
        existing["quantity"] = int(existing["quantity"]) + payload.quantity
        existing["item_name"] = item["item_name"]
        existing["price"] = float(item["price"])
        return existing
    row = {
        "id": (max((int(item["id"]) for item in CART_ITEMS), default=0) + 1),
        "user_id": payload.user_id,
        "item_id": payload.item_id,
        "item_name": item["item_name"],
        "price": float(item["price"]),
        "quantity": payload.quantity,
    }
    CART_ITEMS.append(row)
    return row


@router.post("/cart")
def add_cart_item(payload: AddCartItemRequest):
    if payload.quantity <= 0:
        return error_response(400, "invalid_cart_item", "quantity must be greater than 0")
    if payload.price < 0:
        return error_response(400, "invalid_cart_item", "price must be greater than or equal to 0")
    if not user_exists(payload.user_id):
        return error_response(404, "user_not_found", "user does not exist")

    repository = get_warehouse_repository()
    if repository:
        try:
            row = repository.upsert_cart_item(
                user_id=payload.user_id,
                item_id=payload.item_id,
                quantity=payload.quantity,
            )
        except ValueError:
            return error_response(404, "item_not_found", "item does not exist")
    else:
        row = add_cart_item_in_memory(payload)
        if not row:
            return error_response(404, "item_not_found", "item does not exist")
    return {"ok": True, "item": cart_item_response(row)}


@router.get("/cart")
def list_cart_items(user_id: int | None = None):
    if user_id is None:
        return error_response(400, "missing_user_id", "user_id is required")
    if not user_exists(user_id):
        return error_response(404, "user_not_found", "user does not exist")

    repository = get_warehouse_repository()
    if repository:
        rows = repository.list_cart_items(user_id)
    else:
        rows = [row for row in CART_ITEMS if row["user_id"] == user_id]
    items = [cart_item_response(row) for row in rows]
    return {"ok": True, "user_id": user_id, "count": len(items), "items": items}


@router.delete("/cart")
def delete_cart_item(user_id: int | None = None, item_id: str | None = None):
    if user_id is None:
        return error_response(400, "missing_user_id", "user_id is required")
    if not item_id:
        return error_response(400, "invalid_cart_item", "item_id is required")
    if not user_exists(user_id):
        return error_response(404, "user_not_found", "user does not exist")

    repository = get_warehouse_repository()
    if repository:
        removed = repository.delete_cart_item(user_id=user_id, item_id=item_id)
    else:
        before_count = len(CART_ITEMS)
        CART_ITEMS[:] = [row for row in CART_ITEMS if not (row["user_id"] == user_id and row["item_id"] == item_id)]
        removed = len(CART_ITEMS) != before_count
    if not removed:
        return error_response(404, "cart_item_not_found", "cart item does not exist")
    return {"ok": True, "removed": True, "user_id": user_id, "item_id": item_id}
