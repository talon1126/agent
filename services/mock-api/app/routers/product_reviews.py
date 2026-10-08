from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.routers.warehouse.state import get_warehouse_repository

router = APIRouter()


class ItemReviewCreate(BaseModel):
    user_id: int
    rating: int
    title: str
    content: str


class ItemReviewBatchRequest(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=5)
    limit: int = Field(default=100, ge=1, le=100)

    @field_validator("item_ids")
    @classmethod
    def validate_item_ids(cls, value: list[str]) -> list[str]:
        normalized = [item_id.strip() for item_id in value]
        if any(not item_id for item_id in normalized):
            raise ValueError("item IDs cannot be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("item IDs must be unique")
        return normalized


def error_response(status_code: int, error: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "error": error, "message": message},
    )


def validate_review_payload(payload: ItemReviewCreate) -> str | None:
    if payload.rating < 1 or payload.rating > 5:
        return "Rating must be between 1 and 5."
    if not payload.title.strip():
        return "Title is required."
    if len(payload.title.strip()) > 120:
        return "Title must be at most 120 characters."
    if not payload.content.strip():
        return "Content is required."
    if len(payload.content.strip()) > 2000:
        return "Content must be at most 2000 characters."
    return None


def item_review_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "item_id": str(row["item_id"]),
        "user_id": int(row["user_id"]),
        "rating": int(row["rating"]),
        "title": str(row["title"]),
        "content": str(row["content"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


@router.post("/items/reviews/batch", response_model=None)
def batch_item_reviews(payload: ItemReviewBatchRequest):
    """Return one ordered, source-versioned review read for Agent comparison."""

    repository = get_warehouse_repository()
    if not repository:
        return error_response(
            503,
            "item_review_backend_unavailable",
            "Postgres backend is required.",
        )

    captured_at = datetime.now(UTC).isoformat()
    items: list[dict[str, Any]] = []
    for item_id in payload.item_ids:
        if not repository.get_item_detail(item_id):
            items.append(
                {
                    "item_id": item_id,
                    "status": "error",
                    "error": {
                        "code": "item_not_found",
                        "message": "Item not found.",
                    },
                }
            )
            continue
        reviews = [
            item_review_response(row)
            for row in repository.list_item_reviews(
                item_id,
                limit=payload.limit,
                offset=0,
            )
        ]
        items.append(
            {
                "item_id": item_id,
                "status": "ok",
                "summary": repository.item_review_summary(item_id),
                "reviews": reviews,
            }
        )
    return {
        "ok": True,
        "source_version": "mock-api-item-reviews-v1",
        "captured_at": captured_at,
        "items": items,
    }


@router.get("/items/{item_id}/reviews", response_model=None)
def list_item_reviews(
    item_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    repository = get_warehouse_repository()
    if not repository:
        return error_response(
            503, "item_review_backend_unavailable", "Postgres backend is required."
        )
    if not repository.get_item_detail(item_id):
        return error_response(404, "item_not_found", "Item not found.")

    reviews = [
        item_review_response(row)
        for row in repository.list_item_reviews(item_id, limit=limit, offset=offset)
    ]
    return {
        "ok": True,
        "item_id": item_id,
        "count": len(reviews),
        "summary": repository.item_review_summary(item_id),
        "reviews": reviews,
    }


@router.post("/items/{item_id}/reviews", response_model=None)
def create_item_review(item_id: str, payload: ItemReviewCreate):
    repository = get_warehouse_repository()
    if not repository:
        return error_response(
            503, "item_review_backend_unavailable", "Postgres backend is required."
        )
    if not repository.get_item_detail(item_id):
        return error_response(404, "item_not_found", "Item not found.")

    invalid_message = validate_review_payload(payload)
    if invalid_message:
        return error_response(400, "invalid_review", invalid_message)

    # 中文注释：评论创建时间由后端统一生成，避免前端伪造排序时间。
    created = repository.create_item_review(
        item_id,
        payload.model_dump(),
        created_at=datetime.now(UTC).isoformat(),
    )
    return {"ok": True, "review": item_review_response(created)}
