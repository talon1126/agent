"""Category ranking HTTP routes for TalonMart storefront pages.

This module owns the public mock-api contract for F8 leaderboards. PostgreSQL
remains the durable source for ranking facts and snapshots, while Redis is only
used as a rebuildable ZSET cache for category-level hot lists.
"""

from typing import Any
import os

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

try:
    import redis
except ImportError:  # pragma: no cover - runtime dependency guard
    redis = None

from app.routers.warehouse.state import get_warehouse_repository

router = APIRouter()

CATEGORY_RANKING_REDIS_CLIENT: Any = None
CATEGORY_RANKING_CACHE_TTL_SECONDS = 300


def error_response(status_code: int, error: str, message: str) -> JSONResponse:
    """Return the stable error envelope used by TalonMart mock-api routes."""
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "error": error, "message": message},
    )


def category_ranking_cache_key(category_id: str, rank_type: str, window_type: str) -> str:
    """Build the documented Redis ZSET cache key for one category leaderboard."""
    return f"rank:category:{category_id}:{rank_type}:{window_type}"


def normalize_cached_member(value: Any) -> str:
    """Normalize Redis member values across decode_responses modes."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def get_category_ranking_redis():
    """Return a lazily created Redis client when ranking caching is configured."""
    global CATEGORY_RANKING_REDIS_CLIENT
    if CATEGORY_RANKING_REDIS_CLIENT is not None:
        return CATEGORY_RANKING_REDIS_CLIENT
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url or redis is None:
        return None
    CATEGORY_RANKING_REDIS_CLIENT = redis.Redis.from_url(redis_url, decode_responses=True)
    return CATEGORY_RANKING_REDIS_CLIENT


def read_category_ranking_from_cache(
    redis_client: Any,
    repository: Any,
    *,
    category_id: str,
    rank_type: str,
    window_type: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Hydrate a category leaderboard from Redis item ids when the cache is warm."""
    cached_rows = redis_client.zrevrange(
        category_ranking_cache_key(category_id, rank_type, window_type),
        0,
        limit - 1,
        withscores=True,
    )
    if not cached_rows:
        return []
    item_ids: list[str] = []
    scores: dict[str, float] = {}
    for item_id, score in cached_rows:
        normalized_item_id = normalize_cached_member(item_id)
        item_ids.append(normalized_item_id)
        scores[normalized_item_id] = float(score)
    return repository.get_ranked_items_by_ids(
        item_ids,
        rank_type=rank_type,
        scores=scores,
        window_type=window_type,
    )


def write_category_ranking_cache(
    redis_client: Any,
    *,
    category_id: str,
    rank_type: str,
    window_type: str,
    items: list[dict[str, Any]],
) -> None:
    """Write PostgreSQL snapshot rows into the rebuildable Redis ZSET cache."""
    if not items:
        return
    key = category_ranking_cache_key(category_id, rank_type, window_type)
    redis_client.delete(key)
    redis_client.zadd(key, {str(item["item_id"]): float(item["score"]) for item in items})
    redis_client.expire(key, CATEGORY_RANKING_CACHE_TTL_SECONDS)


@router.get("/rankings/categories/{category_id}", response_model=None)
def get_category_ranking(
    category_id: str,
    rank_type: str = "hot",
    window_type: str = "all_time",
    limit: int = Query(default=10, ge=1, le=100),
):
    """Return one category leaderboard for category pages and product tags."""
    repository = get_warehouse_repository()
    if not repository:
        return error_response(503, "ranking_backend_unavailable", "Postgres backend is required")

    redis_client = get_category_ranking_redis()
    items: list[dict[str, Any]] = []
    if redis_client is not None:
        try:
            items = read_category_ranking_from_cache(
                redis_client,
                repository,
                category_id=category_id,
                rank_type=rank_type,
                window_type=window_type,
                limit=limit,
            )
        except Exception:
            items = []

    if not items:
        items = repository.get_category_ranking(
            category_id=category_id,
            rank_type=rank_type,
            window_type=window_type,
            limit=limit,
        )
        if redis_client is not None:
            try:
                write_category_ranking_cache(
                    redis_client,
                    category_id=category_id,
                    rank_type=rank_type,
                    window_type=window_type,
                    items=items,
                )
            except Exception:
                pass

    return {
        "ok": True,
        "category_id": category_id,
        "rank_type": rank_type,
        "window_type": window_type,
        "count": len(items),
        "items": items,
    }


@router.get("/rankings/home/hot", response_model=None)
def get_home_hot_rankings(
    rank_type: str = "hot",
    window_type: str = "all_time",
    limit: int = Query(default=10, ge=1, le=100),
):
    """Return cross-category hot products for the homepage recommendation rail."""
    repository = get_warehouse_repository()
    if not repository:
        return error_response(503, "ranking_backend_unavailable", "Postgres backend is required")
    items = repository.list_home_hot_rankings(
        rank_type=rank_type,
        window_type=window_type,
        limit=limit,
    )
    return {
        "ok": True,
        "rank_type": rank_type,
        "window_type": window_type,
        "count": len(items),
        "items": items,
    }
