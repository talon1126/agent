from typing import Any
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.routers.warehouse.state import get_warehouse_repository

router = APIRouter()

CATEGORY_FEATURES = {
    "dairy": {
        "features": ["Chilled dairy staple for everyday meals", "Great for breakfast, coffee, and cooking"],
        "ingredients": "Milk and dairy cultures where applicable.",
    },
    "beverage": {
        "features": ["Ready-to-drink refreshment", "Convenient pack for home, office, and parties"],
        "ingredients": "Water and beverage ingredients by product type.",
    },
    "paper": {
        "features": ["Reliable household and office paper supply", "Easy to store and replenish"],
        "ingredients": "Paper fiber materials.",
    },
    "household": {
        "features": ["Everyday household cleaning supply", "Suitable for regular home use"],
        "ingredients": "Household cleaning ingredients by product type.",
    },
    "office": {
        "features": ["Office-ready supply for daily work", "Designed for repeated everyday use"],
        "ingredients": "Office supply materials.",
    },
}


def item_rating_from_reviews(repository: Any, item_id: str) -> dict[str, Any] | None:
    """Build a storefront rating from persisted product reviews.

    Args:
        repository: Warehouse repository that exposes `item_review_summary`.
        item_id: Product id whose customer reviews should be summarized.

    Returns:
        A `{score, count}` rating when the item has reviews, otherwise `None`
        so callers do not display fabricated stars.
    """
    summary = repository.item_review_summary(item_id)
    review_count = int(summary.get("review_count") or 0)
    if review_count <= 0:
        return None
    return {
        "score": round(float(summary.get("average_rating") or 0), 1),
        "count": review_count,
    }


def product_image_url(item_id: str, item_name: str) -> str:
    label = quote(item_name or item_id)
    return f"https://placehold.co/900x900/ffffff/111827?text={label}"


def build_product_detail(
    item: dict[str, Any],
    rating: dict[str, Any] | None = None,
) -> dict[str, Any]:
    category_id = str(item["category_id"])
    category_defaults = CATEGORY_FEATURES.get(
        category_id,
        {
            "features": ["Selected TalonMart item for everyday use", "Available through TalonMart fulfillment"],
            "ingredients": "See product packaging for full ingredient or material details.",
        },
    )
    item_name = str(item["item_name"])
    spec = str(item["spec"])
    unit = str(item.get("unit") or "")
    barcode = str(item.get("barcode") or "")
    image_url = str(item.get("image") or "").strip() or product_image_url(str(item["item_id"]), item_name)
    return {
        "item_id": str(item["item_id"]),
        "item_name": item_name,
        "brand": str(item["brand"]),
        "spec": spec,
        "category_id": category_id,
        "price": float(item["price"]),
        "currency": "USD",
        "images": [
            {
                "url": image_url,
                "alt": f"{item_name} main product image",
                "sort_order": 1,
            }
        ],
        "rating": rating,
        "badges": ["TalonMart pick"],
        "features": category_defaults["features"],
        "ingredients": category_defaults["ingredients"],
        "description": f"{item_name} from {item['brand']} in {spec}, selected for TalonMart customers.",
        "details": [
            {"label": "Brand", "value": str(item["brand"])},
            {"label": "Specification", "value": spec},
            {"label": "Category", "value": category_id},
            {"label": "Unit", "value": unit},
            {"label": "Barcode", "value": barcode},
        ],
        "fulfillment": {
            "shipping_available": True,
            "pickup_available": True,
            "delivery_available": True,
            "pickup_message": "As soon as today",
            "delivery_message": "As soon as tomorrow",
        },
    }


@router.get("/ip/{item_id}")
def get_product_detail(item_id: str):
    repository = get_warehouse_repository()
    if not repository:
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "error": "product_detail_backend_unavailable",
                "message": "Postgres backend is required for product detail.",
            },
        )
    item = repository.get_item_detail(item_id)
    if not item:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": "item_not_found", "message": "Item not found."},
        )
    rating = item_rating_from_reviews(repository, item_id)
    return {"ok": True, "item": build_product_detail(item, rating)}
