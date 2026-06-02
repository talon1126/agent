from typing import Any

from fastapi import APIRouter

from app.routers.cart import error_response, user_exists
from app.routers.warehouse.state import get_warehouse_repository

router = APIRouter()

DEFAULT_DELIVERY_ADDRESSES: list[dict[str, Any]] = [
    {
        "id": 1,
        "user_id": 1,
        "receiver_name": "Talon 测试用户",
        "phone_number": "13800000001",
        "address": "广东省深圳市南山区示例路 100 号",
        "is_default": 1,
    },
    {
        "id": 2,
        "user_id": 2,
        "receiver_name": "Talon 测试用户二",
        "phone_number": "13800000002",
        "address": "广东省深圳市福田区示例路 200 号",
        "is_default": 1,
    },
]


def delivery_address_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "user_id": int(row["user_id"]),
        "receiver_name": str(row["receiver_name"]),
        "phone_number": str(row["phone_number"]),
        "address": str(row["address"]),
        "is_default": int(row["is_default"]),
    }


@router.get("/delivery_addresses")
def list_delivery_addresses(user_id: int | None = None):
    if user_id is None:
        return error_response(400, "missing_user_id", "user_id is required")
    if not user_exists(user_id):
        return error_response(404, "user_not_found", "user does not exist")

    repository = get_warehouse_repository()
    if repository:
        rows = repository.list_delivery_addresses(user_id)
    else:
        rows = [row for row in DEFAULT_DELIVERY_ADDRESSES if int(row["user_id"]) == user_id]
    items = [delivery_address_response(row) for row in rows]
    return {"ok": True, "user_id": user_id, "count": len(items), "items": items}
