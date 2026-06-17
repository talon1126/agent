import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.main import (
    CART_ITEMS,
    DELIVERY_CASES,
    PURCHASE_ORDERS,
    RECEIVED_INVENTORY_BATCHES,
    WAREHOUSE_BATCH_QUANTITY_OVERRIDES,
    WAREHOUSE_INVENTORY_MOVEMENTS,
    WAREHOUSE_INVENTORY_SYNC_JOBS,
    WAREHOUSE_ORDER_ITEMS,
    WAREHOUSE_ORDERS,
    app,
)
from app.routers import search as search_router
from app.routers import category_rankings as category_rankings_router
from app.routers import flash_sales as flash_sales_router
from app.routers import product_details as product_details_router
from app.routers import product_reviews as product_reviews_router
from app.routers.warehouse import orders as warehouse_orders_router
from app.routers.warehouse.inventory import aggregate_stock_balance_snapshot_rows
from app.store import FIXTURE_DIR
from app.warehouse_store import (
    WarehouseRepository,
    build_item_search_sql,
    init_warehouse_schema,
    seed_warehouse_fixtures,
)

client = TestClient(app)


class FakeFlashSaleRedis:
    def __init__(
        self,
        stock: int = 1,
        claimed_users: set[str] | None = None,
        stocks: dict[int, int] | None = None,
    ):
        self.stock = stock
        self.stocks = dict(stocks or {})
        self.claimed_users = set(claimed_users or set())
        self.compensated: list[str] = []

    def get(self, key: str):
        if key.endswith(":stock"):
            flash_sale_id = int(key.split(":")[1])
            if flash_sale_id in self.stocks:
                return str(self.stocks[flash_sale_id])
            return str(self.stock)
        return None

    def set(self, key: str, value: int):
        if key.endswith(":stock"):
            flash_sale_id = int(key.split(":")[1])
            self.stocks[flash_sale_id] = int(value)
            self.stock = int(value)

    def delete(self, key: str):
        if key.endswith(":users"):
            self.claimed_users.clear()

    def eval(self, script: str, numkeys: int, stock_key: str, users_key: str, user_id: str):
        if user_id in self.claimed_users:
            return "already_claimed"
        if self.stock <= 0:
            return "sold_out"
        self.stock -= 1
        self.claimed_users.add(user_id)
        return "ok"

    def incr(self, key: str):
        if key.endswith(":stock"):
            self.stock += 1

    def srem(self, key: str, user_id: str):
        self.compensated.append(str(user_id))
        self.claimed_users.discard(str(user_id))


class FakeFlashSaleRepository:
    def __init__(self, sale: dict | None = None, sales: list[dict] | None = None):
        self.sale = sale or (sales or [active_flash_sale()])[0]
        self.sales = list(sales or [self.sale])
        self.claims: dict[tuple[int, int], dict] = {}
        self.failed_claims: list[dict] = []

    def get_flash_sale(self, flash_sale_id: int):
        for sale in self.sales:
            if flash_sale_id == int(sale["id"]):
                return dict(sale)
        return None

    def list_flash_sales(self, *, status: str | None = None, limit: int = 20):
        rows = [dict(sale) for sale in self.sales if status is None or sale["status"] == status]
        return rows[:limit]

    def get_flash_sale_claim(self, *, flash_sale_id: int, user_id: int):
        claim = self.claims.get((flash_sale_id, user_id))
        return dict(claim) if claim else None

    def create_flash_sale_claim_pending(self, *, flash_sale_id: int, user_id: int, item_id: str, created_at: str):
        claim = {
            "id": len(self.claims) + 1,
            "flash_sale_id": flash_sale_id,
            "user_id": user_id,
            "item_id": item_id,
            "order_id": "",
            "status": "pending",
            "created_at": created_at,
            "updated_at": created_at,
        }
        self.claims[(flash_sale_id, user_id)] = claim
        return dict(claim)

    def mark_flash_sale_claim_ordered(self, claim_id: int, *, order_id: str, updated_at: str):
        for claim in self.claims.values():
            if int(claim["id"]) == claim_id:
                claim["order_id"] = order_id
                claim["status"] = "ordered"
                claim["updated_at"] = updated_at
                return dict(claim)
        return None

    def mark_flash_sale_claim_failed(self, claim_id: int, *, updated_at: str):
        for claim in self.claims.values():
            if int(claim["id"]) == claim_id:
                claim["status"] = "failed"
                claim["updated_at"] = updated_at
                self.failed_claims.append(dict(claim))
                return dict(claim)
        return None


class FakeProductDetailRepository:
    def __init__(self, item: dict | None = None):
        self.item = item
        self.review_summaries: dict[str, dict[str, float | int]] = {}

    def get_item_detail(self, item_id: str):
        if self.item and self.item["item_id"] == item_id:
            return dict(self.item)
        return None

    def item_review_summary(self, item_id: str):
        return self.review_summaries.get(item_id, {"average_rating": 0, "review_count": 0})


class FakeRankingRepository:
    """In-memory repository double for category ranking HTTP contracts.

    The router combines Redis cache reads with PostgreSQL-backed snapshots. This
    fake exposes the same read methods needed by the router so API tests can
    verify fallback behavior without depending on a real database or Redis.
    """

    def __init__(self):
        self.snapshot_rows = [
            {
                "rank": 2,
                "item_id": "item_wireless_earbuds",
                "item_name": "Wireless Earbuds",
                "brand": "Talon Audio",
                "spec": "Bluetooth 5.3",
                "category_id": "electronics",
                "category_name": "Electronics",
                "price": 59.99,
                "score": 91.0,
                "rank_type": "hot",
                "window_type": "all_time",
                "generated_at": "2026-06-17T10:00:00+08:00",
            },
            {
                "rank": 2,
                "item_id": "item_smart_tv_43",
                "item_name": "43 inch Smart TV",
                "brand": "Talon Vision",
                "spec": "4K UHD",
                "category_id": "electronics",
                "category_name": "Electronics",
                "price": 279.99,
                "score": 73.0,
                "rank_type": "hot",
                "window_type": "all_time",
                "generated_at": "2026-06-17T10:00:00+08:00",
            },
        ]

    def get_category_ranking(self, *, category_id: str, rank_type: str, window_type: str, limit: int):
        return [
            dict(row)
            for row in self.snapshot_rows
            if row["category_id"] == category_id
            and row["rank_type"] == rank_type
            and row["window_type"] == window_type
        ][:limit]

    def get_ranked_items_by_ids(
        self,
        item_ids: list[str],
        *,
        rank_type: str = "hot",
        scores: dict[str, float],
        window_type: str = "all_time",
    ):
        rows_by_id = {row["item_id"]: row for row in self.snapshot_rows}
        hydrated = []
        for index, item_id in enumerate(item_ids, start=1):
            row = dict(rows_by_id[item_id])
            row["rank"] = index
            row["rank_type"] = rank_type
            row["score"] = scores[item_id]
            row["window_type"] = window_type
            hydrated.append(row)
        return hydrated

    def list_home_hot_rankings(self, *, rank_type: str, window_type: str, limit: int):
        return sorted(self.snapshot_rows, key=lambda row: row["score"], reverse=True)[:limit]


class FakeRankingRedis:
    """Minimal Redis ZSET double used by ranking router tests."""

    def __init__(self, cached_items: list[tuple[str, float]] | None = None):
        self.cached_items = cached_items or []
        self.writes: dict[str, dict[str, float]] = {}

    def zrevrange(self, key: str, start: int, end: int, withscores: bool = False):
        sliced = self.cached_items[start : end + 1]
        if withscores:
            return sliced
        return [item_id for item_id, _score in sliced]

    def zadd(self, key: str, mapping: dict[str, float]):
        self.writes[key] = dict(mapping)

    def expire(self, key: str, seconds: int):
        return True

    def delete(self, key: str):
        self.cached_items = []


def active_flash_sale(**overrides):
    sale = {
        "id": 1,
        "item_id": "item_milk_pure",
        "item_price": 18.4,
        "sale_price": 9.9,
        "stock_limit": 2,
        "status": "active",
        "starts_at": "2026-06-01T00:00:00+00:00",
        "ends_at": "2099-06-03T00:00:00+00:00",
        "created_at": "2026-06-01T00:00:00+00:00",
        "updated_at": "2026-06-01T00:00:00+00:00",
    }
    sale.update(overrides)
    return sale


@pytest.fixture(autouse=True)
def clear_received_inventory_batches():
    DELIVERY_CASES.clear()
    RECEIVED_INVENTORY_BATCHES.clear()
    WAREHOUSE_INVENTORY_SYNC_JOBS.clear()
    WAREHOUSE_BATCH_QUANTITY_OVERRIDES.clear()
    WAREHOUSE_INVENTORY_MOVEMENTS.clear()
    WAREHOUSE_ORDERS.clear()
    WAREHOUSE_ORDER_ITEMS.clear()
    PURCHASE_ORDERS.clear()
    CART_ITEMS.clear()
    yield
    DELIVERY_CASES.clear()
    RECEIVED_INVENTORY_BATCHES.clear()
    WAREHOUSE_INVENTORY_SYNC_JOBS.clear()
    WAREHOUSE_BATCH_QUANTITY_OVERRIDES.clear()
    WAREHOUSE_INVENTORY_MOVEMENTS.clear()
    WAREHOUSE_ORDERS.clear()
    WAREHOUSE_ORDER_ITEMS.clear()
    PURCHASE_ORDERS.clear()
    CART_ITEMS.clear()


def test_get_order_fixture():
    response = client.get("/orders/ord_100")
    assert response.status_code == 200
    assert response.json()["order_id"] == "ord_100"


def test_search_policy_returns_refund_clause_metadata():
    response = client.post("/policies/search", json={"query": "ord_100 这个订单怎么退款"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["query"] == "ord_100 这个订单怎么退款"
    assert body["matches"]

    first_match = body["matches"][0]
    assert first_match["source_file"] == "fixtures/policies/after_sales_policy.zh.md"
    assert first_match["document_title"] == "售后政策"
    assert first_match["section"] == "退款"
    assert first_match["clause_id"].startswith("REFUND-")
    assert first_match["clause_title"]
    assert first_match["text"]


def test_product_search_returns_item_with_inventory_balances(monkeypatch):
    monkeypatch.setattr(
        search_router,
        "load_item_rating",
        lambda item_id: {"score": 4.5, "count": 2} if item_id == "item_milk_pure" else None,
    )
    monkeypatch.setattr(
        search_router,
        "load_search_items",
        lambda query=None, category=None: [
            {
                "item_id": "item_milk_pure",
                "item_name": "纯牛奶",
                "brand": "蒙牛",
                "spec": "250ml*24盒",
                "category_id": "dairy",
                "price": 18.40,
            }
        ],
    )
    monkeypatch.setattr(
        search_router,
        "load_search_balance_rows",
        lambda: [
            {
                "id": 3,
                "warehouse_id": "wh_hk_1",
                "item_id": "item_milk_pure",
                "quantity_on_hand": 64,
                "storage_status": "available",
            },
            {
                "id": 4,
                "warehouse_id": "wh_sz_1",
                "item_id": "item_milk_pure",
                "quantity_on_hand": 140,
                "storage_status": "available",
            }
        ],
    )

    response = client.get("/search", params={"q": "milk"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["query"] == "milk"
    assert body["count"] == 1
    assert body["items"] == [
        {
            "item_id": "item_milk_pure",
            "item_name": "纯牛奶",
            "brand": "蒙牛",
            "spec": "250ml*24盒",
            "category_id": "dairy",
            "price": 18.40,
            "rating": {"score": 4.5, "count": 2},
            "balances": [
                {
                    "id": 3,
                    "warehouse_id": "wh_hk_1",
                    "item_id": "item_milk_pure",
                    "quantity_on_hand": 64,
                    "storage_status": "available",
                },
                {
                    "id": 4,
                    "warehouse_id": "wh_sz_1",
                    "item_id": "item_milk_pure",
                    "quantity_on_hand": 140,
                    "storage_status": "available",
                },
            ],
        }
    ]


def test_product_search_does_not_match_category_id(monkeypatch):
    monkeypatch.setattr(search_router, "load_search_items", lambda query=None, category=None: [])
    monkeypatch.setattr(search_router, "load_search_balance_rows", lambda: [])

    response = client.get("/search", params={"q": "dairy"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["query"] == "dairy"
    assert body["count"] == 0
    assert body["items"] == []


def test_product_search_returns_items_by_category_without_query(monkeypatch):
    monkeypatch.setattr(
        search_router,
        "load_item_rating",
        lambda item_id: {"score": 4.7, "count": 3}
        if item_id == "item_wireless_earbuds"
        else None,
    )

    def fake_load_search_items(query=None, category=None):
        assert query is None
        assert category == "electronics"
        return [
            {
                "item_id": "item_wireless_earbuds",
                "item_name": "Wireless Earbuds",
                "brand": "Talon Audio",
                "spec": "Bluetooth 5.3 noise cancelling",
                "category_id": "electronics",
                "price": 59.99,
            }
        ]

    monkeypatch.setattr(search_router, "load_search_items", fake_load_search_items)
    monkeypatch.setattr(
        search_router,
        "load_search_balance_rows",
        lambda: [
            {
                "id": 9,
                "warehouse_id": "wh_hk_1",
                "item_id": "item_wireless_earbuds",
                "quantity_on_hand": 42,
                "storage_status": "available",
            }
        ],
    )

    response = client.get("/search", params={"category": "electronics"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["query"] == ""
    assert body["category"] == "electronics"
    assert body["count"] == 1
    assert body["items"][0]["item_id"] == "item_wireless_earbuds"
    assert body["items"][0]["category_id"] == "electronics"
    assert body["items"][0]["rating"] == {"score": 4.7, "count": 3}
    assert body["items"][0]["balances"][0]["quantity_on_hand"] == 42


def test_category_ranking_endpoint_falls_back_to_postgres_snapshot(monkeypatch):
    repository = FakeRankingRepository()
    monkeypatch.setattr(category_rankings_router, "get_warehouse_repository", lambda: repository)
    monkeypatch.setattr(category_rankings_router, "get_category_ranking_redis", lambda: None)

    response = client.get("/rankings/categories/electronics", params={"limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["category_id"] == "electronics"
    assert body["count"] == 2
    assert [item["item_id"] for item in body["items"]] == [
        "item_wireless_earbuds",
        "item_smart_tv_43",
    ]


def test_category_ranking_endpoint_uses_redis_zset_when_available(monkeypatch):
    repository = FakeRankingRepository()
    redis_client = FakeRankingRedis(
        [
            ("item_smart_tv_43", 101.0),
            ("item_wireless_earbuds", 96.0),
        ]
    )
    monkeypatch.setattr(category_rankings_router, "get_warehouse_repository", lambda: repository)
    monkeypatch.setattr(category_rankings_router, "get_category_ranking_redis", lambda: redis_client)

    response = client.get("/rankings/categories/electronics", params={"limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert [item["item_id"] for item in body["items"]] == [
        "item_smart_tv_43",
        "item_wireless_earbuds",
    ]
    assert body["items"][0]["score"] == 101.0


def test_home_hot_ranking_endpoint_returns_cross_category_items(monkeypatch):
    """Homepage rankings must preserve the item's category snapshot rank.

    The home rail is a cross-category projection sorted by score, but the
    displayed label still means "#N in category". Re-numbering the cross-category
    result would make a second-ranked Electronics item appear as "#1 in
    Electronics", which is misleading for users and inconsistent with product
    detail rank tags.
    """
    repository = FakeRankingRepository()
    monkeypatch.setattr(category_rankings_router, "get_warehouse_repository", lambda: repository)
    monkeypatch.setattr(category_rankings_router, "get_category_ranking_redis", lambda: None)

    response = client.get("/rankings/home/hot", params={"limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["count"] == 2
    assert body["items"][0]["rank"] == 2
    assert body["items"][0]["item_id"] == "item_wireless_earbuds"


def test_product_keyword_search_sql_keeps_optional_category_filter():
    statement = str(build_item_search_sql())

    assert "CAST(:category_id AS TEXT) IS NULL" in statement
    assert "category_id = CAST(:category_id AS TEXT)" in statement


def test_product_search_requires_query():
    response = client.get("/search")

    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "error": "missing_query",
        "message": "q is required",
    }


def test_product_search_requires_postgres_search_backend(monkeypatch):
    monkeypatch.setattr(search_router, "get_warehouse_repository", lambda: None)

    response = client.get("/search", params={"q": "milk"})

    assert response.status_code == 503
    assert response.json() == {
        "ok": False,
        "error": "search_backend_unavailable",
        "message": "Postgres pg_search backend is required for /search",
    }


def test_product_search_returns_matching_item_without_balances(monkeypatch):
    monkeypatch.setattr(search_router, "load_item_rating", lambda item_id: None)
    monkeypatch.setattr(
        search_router,
        "load_search_items",
        lambda query=None, category=None: [
            {
                "item_id": "item_no_stock",
                "item_name": "无库存测试商品",
                "brand": "Talon",
                "spec": "1件",
                "category_id": "paper",
                "price": 9.90,
            }
        ],
    )
    monkeypatch.setattr(search_router, "load_search_balance_rows", lambda: [])

    assert search_router.product_search_items("无库存") == [
        {
            "item_id": "item_no_stock",
            "item_name": "无库存测试商品",
            "brand": "Talon",
            "spec": "1件",
            "category_id": "paper",
            "price": 9.90,
            "rating": None,
            "balances": [],
        }
    ]


def test_product_detail_returns_enriched_item_from_repository(monkeypatch):
    repository = FakeProductDetailRepository(
        {
            "item_id": "item_milk_pure",
            "item_name": "纯牛奶",
            "brand": "Talon Value",
            "spec": "1L x 6",
            "category_id": "dairy",
            "price": 18.4,
            "unit": "box",
            "barcode": "690000000001",
        }
    )
    repository.review_summaries["item_milk_pure"] = {"average_rating": 4.5, "review_count": 2}
    monkeypatch.setattr(product_details_router, "get_warehouse_repository", lambda: repository)

    response = client.get("/ip/item_milk_pure")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item"]["item_id"] == "item_milk_pure"
    assert body["item"]["item_name"] == "纯牛奶"
    assert body["item"]["brand"] == "Talon Value"
    assert body["item"]["spec"] == "1L x 6"
    assert body["item"]["category_id"] == "dairy"
    assert body["item"]["price"] == 18.4
    assert body["item"]["currency"] == "USD"
    assert body["item"]["images"][0]["alt"] == "纯牛奶 main product image"
    assert body["item"]["rating"] == {"score": 4.5, "count": 2}
    assert body["item"]["features"]
    assert body["item"]["ingredients"]
    assert body["item"]["description"]
    assert {"label": "Specification", "value": "1L x 6"} in body["item"]["details"]
    assert body["item"]["fulfillment"]["delivery_available"] is True


def test_product_detail_returns_404_for_missing_item(monkeypatch):
    repository = FakeProductDetailRepository()
    monkeypatch.setattr(product_details_router, "get_warehouse_repository", lambda: repository)

    response = client.get("/ip/item_missing")

    assert response.status_code == 404
    assert response.json() == {
        "ok": False,
        "error": "item_not_found",
        "message": "Item not found.",
    }


def test_cart_add_uses_item_price_and_accumulates_quantity():
    first = client.post(
        "/cart",
        json={
            "user_id": 1,
            "item_id": "item_milk_pure",
            "item_name": "纯牛奶",
            "price": 999.99,
            "quantity": 1,
        },
    )
    second = client.post(
        "/cart",
        json={
            "user_id": 1,
            "item_id": "item_milk_pure",
            "item_name": "纯牛奶",
            "price": 0,
            "quantity": 2,
        },
    )

    assert first.status_code == 200
    assert first.json()["item"]["price"] == 18.4
    assert second.status_code == 200
    assert second.json()["item"] == {
        "id": 1,
        "user_id": 1,
        "item_id": "item_milk_pure",
        "item_name": "纯牛奶",
        "price": 18.4,
        "quantity": 3,
    }


def test_cart_list_filters_by_user_id():
    CART_ITEMS.append(
        {
            "id": 1,
            "user_id": 1,
            "item_id": "item_milk_pure",
            "item_name": "纯牛奶",
            "price": 18.4,
            "quantity": 2,
        }
    )
    CART_ITEMS.append(
        {
            "id": 2,
            "user_id": 2,
            "item_id": "item_cola_zero",
            "item_name": "零度可乐",
            "price": 36.9,
            "quantity": 1,
        }
    )

    response = client.get("/cart", params={"user_id": 1})

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "user_id": 1,
        "count": 1,
        "items": [
            {
                "id": 1,
                "user_id": 1,
                "item_id": "item_milk_pure",
                "item_name": "纯牛奶",
                "price": 18.4,
                "quantity": 2,
            }
        ],
    }


def test_cart_delete_requires_matching_user_and_item():
    CART_ITEMS.append(
        {
            "id": 1,
            "user_id": 1,
            "item_id": "item_milk_pure",
            "item_name": "纯牛奶",
            "price": 18.4,
            "quantity": 2,
        }
    )

    missing = client.delete("/cart", params={"user_id": 2, "item_id": "item_milk_pure"})
    removed = client.delete("/cart", params={"user_id": 1, "item_id": "item_milk_pure"})

    assert missing.status_code == 404
    assert missing.json() == {
        "ok": False,
        "error": "cart_item_not_found",
        "message": "cart item does not exist",
    }
    assert removed.status_code == 200
    assert removed.json() == {
        "ok": True,
        "removed": True,
        "user_id": 1,
        "item_id": "item_milk_pure",
    }


def test_cart_rejects_unknown_user_and_invalid_quantity():
    unknown_user = client.post(
        "/cart",
        json={
            "user_id": 999,
            "item_id": "item_milk_pure",
            "item_name": "纯牛奶",
            "price": 18.4,
            "quantity": 1,
        },
    )
    invalid_quantity = client.post(
        "/cart",
        json={
            "user_id": 1,
            "item_id": "item_milk_pure",
            "item_name": "纯牛奶",
            "price": 18.4,
            "quantity": 0,
        },
    )

    assert unknown_user.status_code == 404
    assert unknown_user.json()["error"] == "user_not_found"
    assert invalid_quantity.status_code == 400
    assert invalid_quantity.json() == {
        "ok": False,
        "error": "invalid_cart_item",
        "message": "quantity must be greater than 0",
    }


def test_delivery_addresses_list_returns_default_address_for_user():
    response = client.get("/delivery_addresses", params={"user_id": 1})

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "user_id": 1,
        "count": 1,
        "items": [
            {
                "id": 1,
                "user_id": 1,
                "receiver_name": "Talon 测试用户",
                "phone_number": "13800000001",
                "address": "广东省深圳市南山区示例路 100 号",
                "is_default": 1,
            }
        ],
    }


def test_delivery_addresses_rejects_missing_and_unknown_user():
    missing = client.get("/delivery_addresses")
    unknown = client.get("/delivery_addresses", params={"user_id": 999})

    assert missing.status_code == 400
    assert missing.json() == {
        "ok": False,
        "error": "missing_user_id",
        "message": "user_id is required",
    }
    assert unknown.status_code == 404
    assert unknown.json()["error"] == "user_not_found"


def test_item_reviews_list_returns_reviews_and_summary(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)
    monkeypatch.setattr(product_reviews_router, "get_warehouse_repository", lambda: repository)

    response = client.get("/items/item_milk_pure/reviews")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item_id"] == "item_milk_pure"
    assert body["count"] == 2
    assert body["summary"] == {"average_rating": 4.5, "review_count": 2}
    assert body["reviews"][0] == {
        "id": 2,
        "item_id": "item_milk_pure",
        "user_id": 2,
        "rating": 5,
        "title": "Family pack is convenient",
        "content": "The 1L multipack is easy to store and works well for breakfast.",
        "created_at": "2026-06-01T10:00:00+08:00",
        "updated_at": "2026-06-01T10:00:00+08:00",
    }


def test_item_reviews_create_and_reject_invalid_payload(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)
    monkeypatch.setattr(product_reviews_router, "get_warehouse_repository", lambda: repository)

    created = client.post(
        "/items/item_milk_pure/reviews",
        json={
            "user_id": 1,
            "rating": 5,
            "title": "Good value",
            "content": "Fresh taste and good price for a family pack.",
        },
    )
    invalid = client.post(
        "/items/item_milk_pure/reviews",
        json={
            "user_id": 1,
            "rating": 6,
            "title": "Invalid",
            "content": "Rating is too high.",
        },
    )
    missing_item = client.get("/items/item_missing/reviews")

    assert created.status_code == 200
    assert created.json()["review"] == {
        "id": 5,
        "item_id": "item_milk_pure",
        "user_id": 1,
        "rating": 5,
        "title": "Good value",
        "content": "Fresh taste and good price for a family pack.",
        "created_at": created.json()["review"]["created_at"],
        "updated_at": created.json()["review"]["updated_at"],
    }
    assert invalid.status_code == 400
    assert invalid.json() == {
        "ok": False,
        "error": "invalid_review",
        "message": "Rating must be between 1 and 5.",
    }
    assert missing_item.status_code == 404
    assert missing_item.json()["error"] == "item_not_found"


def test_flash_sale_detail_returns_redis_remaining_stock(monkeypatch):
    repository = FakeFlashSaleRepository(active_flash_sale())
    redis_client = FakeFlashSaleRedis(stock=2)
    monkeypatch.setattr(flash_sales_router, "get_warehouse_repository", lambda: repository)
    monkeypatch.setattr(flash_sales_router, "get_flash_sale_redis", lambda: redis_client)

    response = client.get("/flash-sales/1")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "flash_sale": {
            "id": 1,
            "item_id": "item_milk_pure",
            "item_price": 18.4,
            "sale_price": 9.9,
            "stock_limit": 2,
            "stock_remaining": 2,
            "status": "active",
            "starts_at": "2026-06-01T00:00:00+00:00",
            "ends_at": "2099-06-03T00:00:00+00:00",
        },
    }


def test_flash_sale_list_returns_multiple_sales_with_redis_stock(monkeypatch):
    repository = FakeFlashSaleRepository(
        sales=[
            active_flash_sale(id=1, item_id="item_milk_pure", stock_limit=2),
            active_flash_sale(id=2, item_id="item_cola_zero", item_price=24.9, stock_limit=3),
            active_flash_sale(id=3, item_id="item_vinda_tissue", status="draft", stock_limit=4),
        ]
    )
    redis_client = FakeFlashSaleRedis(stocks={1: 1, 2: 3})
    monkeypatch.setattr(flash_sales_router, "get_warehouse_repository", lambda: repository)
    monkeypatch.setattr(flash_sales_router, "get_flash_sale_redis", lambda: redis_client)

    response = client.get("/flash-sales?status=active&limit=10")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "count": 2,
        "flash_sales": [
            {
                "id": 1,
                "item_id": "item_milk_pure",
                "item_price": 18.4,
                "sale_price": 9.9,
                "stock_limit": 2,
                "stock_remaining": 1,
                "status": "active",
                "starts_at": "2026-06-01T00:00:00+00:00",
                "ends_at": "2099-06-03T00:00:00+00:00",
            },
            {
                "id": 2,
                "item_id": "item_cola_zero",
                "item_price": 24.9,
                "sale_price": 9.9,
                "stock_limit": 3,
                "stock_remaining": 3,
                "status": "active",
                "starts_at": "2026-06-01T00:00:00+00:00",
                "ends_at": "2099-06-03T00:00:00+00:00",
            },
        ],
    }


def test_initialize_active_flash_sales_resets_active_stock(monkeypatch):
    repository = FakeFlashSaleRepository(
        sales=[
            active_flash_sale(id=1, item_id="item_milk_pure", stock_limit=2),
            active_flash_sale(id=2, item_id="item_cola_zero", stock_limit=3),
            active_flash_sale(id=3, item_id="item_vinda_tissue", status="draft", stock_limit=4),
        ]
    )
    redis_client = FakeFlashSaleRedis(stock=0, claimed_users={"1"})
    monkeypatch.setattr(flash_sales_router, "get_warehouse_repository", lambda: repository)
    monkeypatch.setattr(flash_sales_router, "get_flash_sale_redis", lambda: redis_client)

    result = flash_sales_router.initialize_active_flash_sales()

    assert result == {"initialized": 2}
    assert redis_client.stocks == {1: 2, 2: 3}
    assert redis_client.claimed_users == set()


def test_flash_sale_purchase_creates_fulfillment_review_order_and_records_claim(monkeypatch):
    repository = FakeFlashSaleRepository(active_flash_sale())
    redis_client = FakeFlashSaleRedis(stock=1)
    monkeypatch.setattr(flash_sales_router, "get_warehouse_repository", lambda: repository)
    monkeypatch.setattr(flash_sales_router, "get_flash_sale_redis", lambda: redis_client)

    response = client.post(
        "/flash-sales/1/purchase",
        json={"user_id": 1, "shipping_address": "广东省深圳市南山区示例路 100 号"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["order"]["status"] == "pending_fulfillment_review"
    assert body["order"]["customer_id"] == "1"
    assert body["items"][0]["item_id"] == "item_milk_pure"
    assert body["claim"]["status"] == "ordered"
    assert body["claim"]["order_id"] == body["order"]["order_id"]
    assert redis_client.stock == 0
    assert "1" in redis_client.claimed_users


def test_flash_sale_purchase_rejects_duplicate_user(monkeypatch):
    repository = FakeFlashSaleRepository(active_flash_sale())
    redis_client = FakeFlashSaleRedis(stock=1, claimed_users={"1"})
    monkeypatch.setattr(flash_sales_router, "get_warehouse_repository", lambda: repository)
    monkeypatch.setattr(flash_sales_router, "get_flash_sale_redis", lambda: redis_client)

    response = client.post(
        "/flash-sales/1/purchase",
        json={"user_id": 1, "shipping_address": "广东省深圳市南山区示例路 100 号"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "ok": False,
        "error": "purchase_limit_reached",
        "message": "已达到购买上限",
    }


def test_flash_sale_purchase_rejects_existing_ordered_claim(monkeypatch):
    repository = FakeFlashSaleRepository(active_flash_sale())
    repository.claims[(1, 1)] = {
        "id": 1,
        "flash_sale_id": 1,
        "user_id": 1,
        "item_id": "item_milk_pure",
        "status": "ordered",
        "order_id": "ORD-CODEX-FLASH-1",
        "error": None,
        "created_at": "2026-06-01T00:00:00+00:00",
        "updated_at": "2026-06-01T00:00:00+00:00",
    }
    redis_client = FakeFlashSaleRedis(stock=1)
    monkeypatch.setattr(flash_sales_router, "get_warehouse_repository", lambda: repository)
    monkeypatch.setattr(flash_sales_router, "get_flash_sale_redis", lambda: redis_client)

    response = client.post(
        "/flash-sales/1/purchase",
        json={"user_id": 1, "shipping_address": "广东省深圳市南山区示例路 100 号"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "ok": False,
        "error": "purchase_limit_reached",
        "message": "已达到购买上限",
    }
    assert redis_client.stock == 1


def test_flash_sale_purchase_compensates_redis_when_order_creation_fails(monkeypatch):
    repository = FakeFlashSaleRepository(active_flash_sale(item_id="item_vinda_tissue"))
    redis_client = FakeFlashSaleRedis(stock=1)
    def fail_order_creation(payload):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "insufficient_available_stock",
                "item_id": payload.items[0].item_id,
                "warehouse_id": "wh_sz_1",
            },
        )
    monkeypatch.setattr(flash_sales_router, "get_warehouse_repository", lambda: repository)
    monkeypatch.setattr(flash_sales_router, "get_flash_sale_redis", lambda: redis_client)
    monkeypatch.setattr(flash_sales_router, "create_warehouse_order", fail_order_creation)

    response = client.post(
        "/flash-sales/1/purchase",
        json={"user_id": 1, "shipping_address": "广东省深圳市南山区示例路 100 号"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "insufficient_available_stock"
    assert redis_client.stock == 1
    assert "1" not in redis_client.claimed_users
    assert repository.failed_claims[0]["status"] == "failed"


def test_create_approval_request():
    payload = {
        "event_id": "evt_refund_high_value",
        "recommended_action": "review_refund_request",
        "explanation": "High-value refund requires approval.",
    }
    response = client.post("/approval-requests", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "pending"

    list_response = client.get("/approval-requests")
    assert list_response.status_code == 200
    assert any(item["event_id"] == "evt_refund_high_value" for item in list_response.json())


def test_records_internal_notifications_and_run_logs():
    notification = client.post(
        "/internal-notifications",
        json={"event_id": "evt_low_stock", "team": "procurement"},
    )
    assert notification.status_code == 200
    assert notification.json()["status"] == "sent"

    run_log = client.post(
        "/run-logs",
        json={"event_id": "evt_low_stock", "status": "succeeded"},
    )
    assert run_log.status_code == 200

    assert client.get("/internal-notifications").json()[-1]["event_id"] == "evt_low_stock"
    assert client.get("/run-logs").json()[-1]["status"] == "succeeded"


def test_procurement_mock_recommends_replenishment_for_low_batch_stock():
    response = client.post("/procurement/mock", json={"item_id": "item_vinda_tissue"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item_id"] == "item_vinda_tissue"
    assert body["recommendation"] == "create_purchase_request"
    assert body["system"] == "mock-procurement"


def test_create_and_list_replenishment_requests_from_warehouse_signal():
    create_response = client.post(
        "/procurement/replenishment-requests",
        json={
            "source": "warehouse",
            "warehouse_id": "wh_sz_1",
            "location_code": "A1",
            "item_id": "item_vinda_tissue",
            "reason": "available_quantity_below_reorder_threshold",
            "created_by": "warehouse:user-001",
        },
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["ok"] is True
    assert created["request"]["request_id"].startswith("REQ-")
    assert created["request"]["status"] == "未审批"
    assert created["request"]["source"] == "warehouse"
    assert created["request"]["warehouse_id"] == "wh_sz_1"
    assert created["request"]["location_code"] == "A1"
    assert created["request"]["item_id"] == "item_vinda_tissue"
    assert created["request"]["current_quantity"] == 136
    assert created["request"]["reorder_threshold"] == 100
    assert created["request"]["suggested_quantity"] == 100
    assert created["request"]["item_name"] == "维达纸巾"

    list_response = client.get("/procurement/replenishment-requests?status=未审批")

    assert list_response.status_code == 200
    listed = list_response.json()
    assert listed["ok"] is True
    assert any(
        item["request_id"] == created["request"]["request_id"]
        and item["status"] == "未审批"
        for item in listed["items"]
    )


def create_replenishment_request_for_test(
    item_id: str = "item_vinda_tissue",
    location_code: str = "A1",
) -> dict:
    response = client.post(
        "/procurement/replenishment-requests",
        json={
            "source": "warehouse",
            "warehouse_id": "wh_sz_1",
            "location_code": location_code,
            "item_id": item_id,
            "reason": "available_quantity_below_reorder_threshold",
            "created_by": "warehouse:user-001",
        },
    )
    assert response.status_code == 200
    return response.json()["request"]


def test_approve_replenishment_request_creates_purchase_order():
    request = create_replenishment_request_for_test()
    expected_arrival_date = (
        datetime.fromisoformat(request["created_at"]).date() + timedelta(days=3)
    ).isoformat()

    response = client.post(
        f"/procurement/replenishment-requests/{request['request_id']}/approve",
        json={"created_by": "procurement:user-001"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["request"]["request_id"] == request["request_id"]
    assert body["request"]["status"] == "已审批"
    assert body["purchase_order"]["purchase_order_id"].startswith("PO-")
    assert body["purchase_order"]["request_id"] == request["request_id"]
    assert body["purchase_order"]["supplier_id"] == "supplier_paper_sz"
    assert body["purchase_order"]["supplier_name"] == "深圳纸品供应商"
    assert body["purchase_order"]["item_id"] == "item_vinda_tissue"
    assert body["purchase_order"]["warehouse_id"] == request["warehouse_id"]
    assert body["purchase_order"]["warehouse_name"] == request["warehouse_name"]
    assert body["purchase_order"]["location_code"] == request["location_code"]
    assert body["purchase_order"]["quantity"] == request["suggested_quantity"]
    assert body["purchase_order"]["unit_price"] == 8
    assert body["purchase_order"]["currency"] == "CNY"
    assert body["purchase_order"]["estimated_total_price"] == request["suggested_quantity"] * 8
    assert body["purchase_order"]["lead_time_days"] == 3
    assert body["purchase_order"]["estimated_arrival_date"] == expected_arrival_date
    assert body["purchase_order"]["payment_status"] == "unpaid"
    assert body["purchase_order"]["warehouse_sync_status"] == "pending_arrival"

    orders_response = client.get(
        f"/procurement/purchase-orders?request_id={request['request_id']}"
    )
    assert orders_response.status_code == 200
    orders = orders_response.json()
    assert orders["ok"] is True
    assert orders["count"] == 1
    assert orders["items"][0]["purchase_order_id"] == body["purchase_order"]["purchase_order_id"]


def test_approve_replenishment_request_reuses_existing_purchase_order():
    request = create_replenishment_request_for_test()

    first = client.post(
        f"/procurement/replenishment-requests/{request['request_id']}/approve",
        json={"created_by": "procurement:user-001"},
    )
    second = client.post(
        f"/procurement/replenishment-requests/{request['request_id']}/approve",
        json={"created_by": "procurement:user-001"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["purchase_order"]["purchase_order_id"] == first.json()["purchase_order"]["purchase_order_id"]
    assert second.json()["purchase_order"]["estimated_arrival_date"] == first.json()["purchase_order"]["estimated_arrival_date"]
    orders = client.get(
        f"/procurement/purchase-orders?request_id={request['request_id']}"
    ).json()
    assert orders["count"] == 1


def test_reject_replenishment_request_keeps_unapproved_status_without_purchase_order():
    request = create_replenishment_request_for_test()

    response = client.post(
        f"/procurement/replenishment-requests/{request['request_id']}/reject",
        json={"reason": "供应商暂不稳定，先人工复核。", "updated_by": "procurement:user-001"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["request"]["request_id"] == request["request_id"]
    assert body["request"]["status"] == "未审批"
    assert body["request"]["reason"] == "供应商暂不稳定，先人工复核。"

    orders = client.get(
        f"/procurement/purchase-orders?request_id={request['request_id']}"
    ).json()
    assert orders["count"] == 0


def test_replenishment_request_decision_returns_404_for_unknown_request():
    response = client.post(
        "/procurement/replenishment-requests/REQ-DOES-NOT-EXIST/approve",
        json={"created_by": "procurement:user-001"},
    )

    assert response.status_code == 404


def test_approve_replenishment_request_requires_default_supplier():
    request = create_replenishment_request_for_test(
        item_id="item_office_pen",
        location_code="B1",
    )

    response = client.post(
        f"/procurement/replenishment-requests/{request['request_id']}/approve",
        json={"created_by": "procurement:user-001"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "default supplier not found for item"


def test_approve_replenishment_request_batch_processes_pending_requests_and_skips_missing_suppliers():
    first = create_replenishment_request_for_test(item_id="item_vinda_tissue", location_code="A1")
    second = create_replenishment_request_for_test(item_id="item_milk_pure", location_code="C1")
    missing_supplier = create_replenishment_request_for_test(item_id="item_office_pen", location_code="B1")

    response = client.post(
        "/procurement/replenishment-requests/approve-batch",
        json={"created_by": "procurement:user-001"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["approved_count"] >= 2
    assert body["skipped_count"] >= 1
    approved_request_ids = {
        item["request_id"] for item in body["created_or_reused_orders"]
    }
    assert {first["request_id"], second["request_id"]}.issubset(approved_request_ids)
    assert any(
        error["request_id"] == missing_supplier["request_id"]
        and error["error"] == "default_supplier_not_found"
        for error in body["errors"]
    )

    refreshed = client.get("/procurement/replenishment-requests").json()["items"]
    statuses = {item["request_id"]: item["status"] for item in refreshed}
    assert statuses[first["request_id"]] == "已审批"
    assert statuses[second["request_id"]] == "已审批"
    assert statuses[missing_supplier["request_id"]] == "未审批"


def test_confirm_purchase_order_arrival_batch_marks_unsynced_without_inventory_sync_job():
    first_request = create_replenishment_request_for_test(
        item_id="item_vinda_tissue",
        location_code="A1",
    )
    second_request = create_replenishment_request_for_test(
        item_id="item_milk_pure",
        location_code="C1",
    )
    first_order = client.post(
        f"/procurement/replenishment-requests/{first_request['request_id']}/approve",
        json={"created_by": "procurement:user-001"},
    ).json()["purchase_order"]
    second_order = client.post(
        f"/procurement/replenishment-requests/{second_request['request_id']}/approve",
        json={"created_by": "procurement:user-001"},
    ).json()["purchase_order"]

    response = client.post(
        "/procurement/purchase-orders/confirm-arrival-batch",
        json={
            "purchase_order_ids": [
                first_order["purchase_order_id"],
                second_order["purchase_order_id"],
            ],
            "received_by": "warehouse:user-001",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["processed_count"] == 2
    assert body["confirmed_count"] == 2
    assert body["skipped_count"] == 0
    assert {item["purchase_order_id"] for item in body["confirmed_items"]} == {
        first_order["purchase_order_id"],
        second_order["purchase_order_id"],
    }
    assert "warehouse_inventory_sync_jobs" not in body
    assert "warehouse_inventory_sync_requests" not in body
    assert all(item["warehouse_sync_status"] == "arrived_unsynced" for item in body["confirmed_items"])

    refreshed_orders = client.get("/procurement/purchase-orders").json()["items"]
    sync_statuses = {item["purchase_order_id"]: item["warehouse_sync_status"] for item in refreshed_orders}
    assert sync_statuses[first_order["purchase_order_id"]] == "arrived_unsynced"
    assert sync_statuses[second_order["purchase_order_id"]] == "arrived_unsynced"

    first_inventory = client.get("/warehouse/inventory/item_vinda_tissue").json()
    second_inventory = client.get("/warehouse/inventory/item_milk_pure").json()
    assert all(
        item["batch_no"] != f"RCV-{first_order['purchase_order_id']}"
        for item in first_inventory["batches"]
    )
    assert all(
        item["batch_no"] != f"RCV-{second_order['purchase_order_id']}"
        for item in second_inventory["batches"]
    )

    pending_jobs = client.get("/warehouse/inventory-sync-jobs?status=pending").json()
    assert all(
        item.get("purchase_order_id") not in {first_order["purchase_order_id"], second_order["purchase_order_id"]}
        for item in pending_jobs.get("items", [])
    )

    repeat = client.post(
        "/procurement/purchase-orders/confirm-arrival-batch",
        json={"purchase_order_ids": [first_order["purchase_order_id"]], "received_by": "warehouse:user-001"},
    )

    assert repeat.status_code == 200
    repeat_body = repeat.json()
    assert repeat_body["confirmed_items"][0]["action"] == "reused"


def test_warehouse_syncs_paid_arrived_purchase_orders_to_inventory_balances():
    request = create_replenishment_request_for_test(
        item_id="item_vinda_tissue",
        location_code="A1",
    )
    order = client.post(
        f"/procurement/replenishment-requests/{request['request_id']}/approve",
        json={"created_by": "procurement:user-001"},
    ).json()["purchase_order"]
    for item in PURCHASE_ORDERS:
        if item["purchase_order_id"] == order["purchase_order_id"]:
            item["payment_status"] = "paid"
            item["location_code"] = "B1"

    arrival = client.post(
        "/procurement/purchase-orders/confirm-arrival-batch",
        json={"purchase_order_ids": [order["purchase_order_id"]], "received_by": "warehouse:user-001"},
    ).json()
    arrived_at = arrival["confirmed_items"][0]["arrived_at"]
    batch_no = f"BATCH-{datetime.fromisoformat(arrived_at).strftime('%Y%m%d')}"

    response = client.post(
        "/warehouse/purchase-orders/sync-arrivals",
        json={"processed_by": "warehouse-agent"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["processed_count"] == 1
    assert body["synced_count"] == 1
    synced = body["synced_items"][0]
    assert synced["purchase_order_id"] == order["purchase_order_id"]
    assert synced["batch_no"] == batch_no
    assert synced["location_code"] == "A1"
    assert synced["warehouse_sync_status"] == "synced"

    inventory = client.get("/warehouse/inventory/item_vinda_tissue").json()
    created_batch = next(item for item in inventory["batches"] if item["batch_no"] == batch_no)
    production_date = datetime.fromisoformat(arrived_at).date()
    assert created_batch["location_code"] == "A1"
    assert created_batch["quantity_on_hand"] == order["quantity"]
    assert created_batch["production_date"] == production_date.isoformat()
    assert created_batch["expiry_date"] == (production_date + timedelta(days=365)).isoformat()
    assert created_batch["storage_status"] == "available"
    assert 20 <= int(created_batch["reorder_threshold"]) <= 120

    balances = client.get(
        "/warehouse/stock/balances",
        params={"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1"},
    ).json()
    assert {item["location_code"] for item in balances["locations"]} == {"A1"}
    assert balances["total_quantity_on_hand"] == 136 + order["quantity"]

    refreshed = client.get(
        "/procurement/purchase-orders",
        params={"purchase_order_id": order["purchase_order_id"]},
    ).json()["items"][0]
    assert refreshed["warehouse_sync_status"] == "synced"


def test_purchase_orders_can_be_filtered_by_arrived_unsynced_status():
    request = create_replenishment_request_for_test(
        item_id="item_vinda_tissue",
        location_code="A1",
    )
    order = client.post(
        f"/procurement/replenishment-requests/{request['request_id']}/approve",
        json={"created_by": "procurement:user-001"},
    ).json()["purchase_order"]

    client.post(
        "/procurement/purchase-orders/confirm-arrival-batch",
        json={"purchase_order_ids": [order["purchase_order_id"]], "received_by": "warehouse:user-001"},
    )

    response = client.get(
        "/procurement/purchase-orders",
        params={"warehouse_sync_status": "arrived_unsynced"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert any(item["purchase_order_id"] == order["purchase_order_id"] for item in body["items"])


def test_procurement_table_schema_and_rows_are_feishu_ready():
    request = create_replenishment_request_for_test()
    approve = client.post(
        f"/procurement/replenishment-requests/{request['request_id']}/approve",
        json={"created_by": "procurement:user-001"},
    ).json()

    request_schema = client.get("/procurement/replenishment-requests/table-schema").json()
    order_schema = client.get("/procurement/purchase-orders/table-schema").json()
    request_rows = client.post(
        "/procurement/replenishment-requests/table-rows",
        json={"status": "已审批"},
    ).json()
    order_rows = client.post(
        "/procurement/purchase-orders/table-rows",
        json={"request_id": request["request_id"]},
    ).json()

    assert request_schema["ok"] is True
    assert request_schema["schema_id"] == "procurement_replenishment_requests"
    request_field_names = [field["name"] for field in request_schema["fields"]]
    assert "Request ID" in request_field_names
    assert "Category ID" not in request_field_names
    assert "Item ID" not in request_field_names
    assert order_schema["ok"] is True
    assert order_schema["schema_id"] == "procurement_purchase_orders"
    order_field_names = [field["name"] for field in order_schema["fields"]]
    assert "Purchase Order ID" in order_field_names
    assert "Supplier ID" not in order_field_names
    assert "Item ID" not in order_field_names
    assert "Warehouse ID" in order_field_names
    assert "Location" in order_field_names
    assert "Payment Status" in order_field_names
    assert "Warehouse Sync Status" in order_field_names
    assert "Estimated Arrival Date" in order_field_names
    assert "Arrived At" in order_field_names

    assert request_rows["ok"] is True
    request_fields = next(
        item["fields"]
        for item in request_rows["items"]
        if item["request_id"] == request["request_id"]
    )
    assert request_fields["Request ID"] == request["request_id"]
    assert request_fields["Status"] == "已审批"
    assert request_fields["Item Name"] == "维达纸巾"
    assert "Category ID" not in request_fields
    assert "Item ID" not in request_fields

    assert order_rows["ok"] is True
    assert order_rows["count"] == 1
    order_fields = order_rows["items"][0]["fields"]
    assert order_fields["Purchase Order ID"] == approve["purchase_order"]["purchase_order_id"]
    assert order_fields["Request ID"] == request["request_id"]
    assert "Supplier ID" not in order_fields
    assert "Item ID" not in order_fields
    assert order_fields["Warehouse ID"] == request["warehouse_id"]
    assert order_fields["Location"] == request["location_code"]
    assert order_fields["Payment Status"] == "unpaid"
    assert order_fields["Warehouse Sync Status"] == "pending_arrival"
    assert order_fields["Estimated Arrival Date"] == approve["purchase_order"]["estimated_arrival_date"]
    assert order_fields["Arrived At"] == ""


def test_operations_summary_mock_returns_cross_domain_summary():
    response = client.post("/operations/summary/mock", json={"query": "帮我总结今天的运营异常"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["system"] == "mock-operations"
    assert body["summary"]
    assert any(item["domain"] == "warehouse" for item in body["incidents"])


def test_delivery_providers_list_contains_default_domestic_carriers():
    response = client.get("/delivery/providers")

    assert response.status_code == 200
    body = response.json()
    provider_names = {item["name"] for item in body["items"]}
    assert {"顺丰", "京东", "圆通"}.issubset(provider_names)


def test_warehouse_order_uses_english_statuses_and_delivery_provider_fields():
    create = client.post(
        "/warehouse/orders",
        json={
            "order_id": "ORD-DELIVERY-1001",
            "customer_id": "cus_100",
            "delivery_provider_id": "sf",
            "courier_phone": "13800000001",
            "shipping_address": "广东省深圳市",
            "items": [
                {
                    "item_id": "item_vinda_tissue",
                    "quantity": 2,
                }
            ],
            "created_by": "delivery-agent",
        },
    )

    assert create.status_code == 200
    created_order = create.json()["order"]
    assert created_order["status"] == "pending_fulfillment_review"
    assert created_order["delivery_provider_id"] == "sf"
    assert created_order["delivery_provider_name"] == "顺丰"
    assert created_order["courier_phone"] == "13800000001"
    assert created_order["shipping_province"] == "广东省"
    assert created_order["shipping_city"] == "深圳市"
    assert created_order["selected_warehouse_id"] == "wh_sz_1"
    assert create.json()["items"][0]["status"] == "pending_fulfillment_review"

    confirmed = client.post(
        "/warehouse/orders/ORD-DELIVERY-1001/fulfillment/confirm",
        json={"warehouse_id": "wh_sz_1", "updated_by": "warehouse-agent"},
    ).json()
    assert confirmed["order"]["status"] == "unpaid"
    assert confirmed["items"][0]["status"] == "unpaid"

    paid = client.post(
        "/warehouse/orders/ORD-DELIVERY-1001/pay",
        json={"updated_by": "warehouse-agent"},
    ).json()
    assert paid["order"]["status"] == "pending_shipment"

    shipped = client.post(
        "/warehouse/orders/ORD-DELIVERY-1001/ship",
        json={"updated_by": "warehouse-agent"},
    ).json()
    assert shipped["order"]["status"] == "shipped"

    arrived = client.post(
        "/warehouse/orders/ORD-DELIVERY-1001/arrive",
        json={"updated_by": "warehouse-agent"},
    ).json()
    assert arrived["order"]["status"] == "arrived"


def test_delivery_status_lookup_uses_warehouse_order_and_delivery_provider_table():
    client.post(
        "/warehouse/orders",
        json={
            "order_id": "ORD-DELIVERY-1002",
            "customer_id": "cus_100",
            "delivery_provider_id": "jd",
            "courier_phone": "13800000002",
            "shipping_address": "广东省深圳市",
            "items": [
                {
                    "item_id": "item_vinda_tissue",
                    "warehouse_id": "wh_sz_1",
                    "quantity": 1,
                }
            ],
            "created_by": "warehouse-agent",
        },
    )
    client.post(
        "/warehouse/orders/ORD-DELIVERY-1002/fulfillment/confirm",
        json={"warehouse_id": "wh_sz_1", "updated_by": "warehouse-agent"},
    )
    client.post(
        "/warehouse/orders/ORD-DELIVERY-1002/pay",
        json={"updated_by": "warehouse-agent"},
    )
    client.post(
        "/warehouse/orders/ORD-DELIVERY-1002/ship",
        json={"updated_by": "warehouse-agent"},
    )

    response = client.post(
        "/delivery/status/lookup",
        json={"order_id": "ORD-DELIVERY-1002"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["system"] == "mock-api-delivery"
    assert body["order"]["order_id"] == "ORD-DELIVERY-1002"
    assert body["order"]["status"] == "shipped"
    assert body["delivery"]["provider_id"] == "jd"
    assert body["delivery"]["provider_name"] == "京东"
    assert body["delivery"]["courier_phone"] == "13800000002"
    assert body["risk_level"] == "medium"


def test_delivery_exceptions_search_returns_shipped_orders_from_warehouse_order_table():
    client.post(
        "/warehouse/orders",
        json={
            "order_id": "ORD-DELIVERY-1003",
            "customer_id": "cus_100",
            "delivery_provider_id": "yto",
            "courier_phone": "13800000003",
            "shipping_address": "广东省深圳市",
            "items": [
                {
                    "item_id": "item_vinda_tissue",
                    "warehouse_id": "wh_sz_1",
                    "quantity": 1,
                }
            ],
            "created_by": "warehouse-agent",
        },
    )
    client.post(
        "/warehouse/orders/ORD-DELIVERY-1003/fulfillment/confirm",
        json={"warehouse_id": "wh_sz_1", "updated_by": "warehouse-agent"},
    )
    client.post("/warehouse/orders/ORD-DELIVERY-1003/pay", json={"updated_by": "warehouse-agent"})
    client.post("/warehouse/orders/ORD-DELIVERY-1003/ship", json={"updated_by": "warehouse-agent"})

    response = client.post("/delivery/exceptions/search", json={"status": "shipped"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["system"] == "mock-api-delivery"
    assert body["count"] == 1
    assert body["items"][0]["order_id"] == "ORD-DELIVERY-1003"
    assert body["items"][0]["delivery_provider_name"] == "圆通"


def test_delivery_case_create_records_follow_up_case_for_warehouse_order():
    client.post(
        "/warehouse/orders",
        json={
            "order_id": "ORD-DELIVERY-1004",
            "customer_id": "cus_100",
            "delivery_provider_id": "sf",
            "courier_phone": "13800000004",
            "shipping_address": "广东省深圳市",
            "items": [
                {
                    "item_id": "item_vinda_tissue",
                    "warehouse_id": "wh_sz_1",
                    "quantity": 1,
                }
            ],
            "created_by": "warehouse-agent",
        },
    )
    response = client.post(
        "/delivery/cases",
        json={
            "order_id": "ORD-DELIVERY-1004",
            "case_type": "delivery_delay",
            "reason": "客户催促，超过预计时效",
            "created_by": "delivery-agent",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["case"]["case_id"].startswith("DCASE-")
    assert body["case"]["order_id"] == "ORD-DELIVERY-1004"
    assert body["case"]["delivery_provider_name"] == "顺丰"
    assert body["case"]["status"] == "open"
    assert DELIVERY_CASES[0]["case_id"] == body["case"]["case_id"]


def test_warehouse_inventory_returns_batches_locations_and_risk():
    response = client.get("/warehouse/inventory/item_vinda_tissue")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item_id"] == "item_vinda_tissue"
    assert body["item_name"] == "维达纸巾"
    assert body["category_name"] == "纸品"
    assert body["total_quantity_available"] == 136
    assert body["risk_level"] == "high"
    assert body["batches"][0]["warehouse_id"] == "wh_sz_1"
    assert body["batches"][0]["location_code"] == "A1"
    assert body["recommendation"]


def test_warehouse_inventory_returns_perishable_batch_fixture():
    response = client.get("/warehouse/inventory/item_milk_pure")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item_id"] == "item_milk_pure"
    assert body["category_name"] == "乳制品"
    assert body["risk_level"] == "high"
    assert body["batches"][0]["expiry_risk"] == "expiring_soon"


def test_warehouse_inventory_search_filters_by_warehouse_category_and_expiry_risk():
    response = client.post(
        "/warehouse/inventory/search",
        json={"warehouse_id": "wh_hk_1", "category": "dairy", "expiry_risk": "expiring_soon"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["schema_id"] == "warehouse_batch_inventory"
    assert body["count"] >= 1
    assert {item["item_id"] for item in body["items"]}.issuperset({"item_milk_pure"})
    assert {item["warehouse_id"] for item in body["items"]} == {"wh_hk_1"}
    assert {item["category_id"] for item in body["items"]} == {"dairy"}
    assert {item["expiry_risk"] for item in body["items"]} == {"expiring_soon"}


def test_warehouse_inventory_table_schema_exposes_business_fields():
    response = client.get("/warehouse/inventory/table-schema")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["schema_id"] == "warehouse_batch_inventory"
    assert body["source"] == "mock-api"
    assert body["fields"][0] == {
        "name": "Warehouse",
        "source": "warehouses.name",
        "type": "text",
        "comment": "仓库展示名称，例如深圳仓、香港仓。",
    }
    assert any(item["name"] == "Location" for item in body["fields"])
    assert any(item["name"] == "Category" for item in body["fields"])
    assert any(item["name"] == "Batch No" for item in body["fields"])
    assert any(item["name"] == "Expiry Date" for item in body["fields"])
    risk_field = next(item for item in body["fields"] if item["name"] == "Risk Level")
    assert risk_field["type"] == "single_select"
    assert risk_field["options"] == [
        {"name": "low", "color": 28},
        {"name": "medium", "color": 24},
        {"name": "high", "color": 17},
        {"name": "unknown", "color": 0},
    ]


def test_warehouse_inventory_table_rows_return_batch_location_feishu_ready_fields():
    response = client.post(
        "/warehouse/inventory/table-rows",
        json={"warehouse_id": "wh_sz_1", "category": "paper", "limit": 5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["schema_id"] == "warehouse_batch_inventory"
    assert body["count"] >= 1
    first = body["items"][0]
    assert first["batch_key"].startswith("wh_sz_1:A1:item_vinda_tissue:")
    assert set(first["fields"]).issuperset(
        {
            "Warehouse",
            "Location",
            "Category",
            "Item ID",
            "Item Name",
            "Brand",
            "Spec",
            "Batch No",
            "Quantity On Hand",
            "Quantity Available",
            "Quantity Reserved",
            "Expiry Date",
            "Risk Level",
            "Recommendation",
            "Last Synced At",
            "Sync Status",
            "Source Version",
        }
    )
    assert first["fields"]["Warehouse"] == "深圳仓"
    assert first["fields"]["Location"] == "A1"
    assert first["fields"]["Category"] == "纸品"
    assert first["fields"]["Item Name"] == "维达纸巾"


def test_warehouse_stock_balance_table_schema_uses_select_statuses_and_date_fields():
    response = client.get("/warehouse/stock/balances/table-schema")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["schema_id"] == "warehouse_inventory_balances"
    fields_by_name = {item["name"]: item for item in body["fields"]}
    assert "Batch No" not in fields_by_name
    assert "Category ID" not in fields_by_name
    assert "Item ID" not in fields_by_name
    assert fields_by_name["Balance ID"]["type"] == "text"
    assert fields_by_name["Quantity On Hand"]["type"] == "number"
    assert fields_by_name["Storage Status"]["type"] == "single_select"
    assert fields_by_name["Risk Level"]["type"] == "single_select"
    assert fields_by_name["Balance Status"]["type"] == "single_select"
    assert fields_by_name["Sync Status"]["type"] == "single_select"
    assert fields_by_name["Created At"]["type"] == "date"
    assert fields_by_name["Updated At"]["type"] == "date"


def test_warehouse_stock_balance_table_rows_page_by_cursor_without_batch_no():
    response = client.post(
        "/warehouse/stock/balances/table-rows",
        json={"warehouse_id": "wh_sz_1", "limit": 2},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["schema_id"] == "warehouse_inventory_balances"
    assert body["count"] == 2
    assert body["next_cursor"]
    first = body["items"][0]
    assert first["balance_id"] == first["fields"]["Balance ID"]
    assert first["balance_id"].isdigit() or first["balance_id"].startswith("fallback:")
    assert "Batch No" not in first["fields"]
    assert "Category ID" not in first["fields"]
    assert "Item ID" not in first["fields"]
    assert first["fields"]["Warehouse"] == "深圳仓"
    assert first["fields"]["Warehouse ID"] == "wh_sz_1"
    assert first["fields"]["Quantity On Hand"] >= 0
    assert first["fields"]["Quantity Available"] == first["fields"]["Quantity On Hand"]
    assert first["fields"]["Storage Status"] in {"available", "quality_hold"}
    assert first["fields"]["Risk Level"] in {"low", "medium", "high", "unknown"}
    assert first["fields"]["Balance Status"] in {"available", "low_stock", "zero_stock", "quality_hold"}
    assert first["fields"]["Sync Status"] == "synced"
    assert first["fields"]["Created At"]
    assert first["fields"]["Updated At"]


def test_warehouse_stock_balance_table_rows_are_unique_by_item_warehouse_location():
    response = client.post(
        "/warehouse/stock/balances/table-rows",
        json={"limit": 500},
    )

    assert response.status_code == 200
    body = response.json()
    balance_ids = [item["balance_id"] for item in body["items"]]
    assert len(balance_ids) == len(set(balance_ids))
    vinda = [
        item
        for item in body["items"]
        if item["fields"]["Item Name"] == "维达纸巾"
        and item["warehouse_id"] == "wh_sz_1"
        and item["location_code"] == "A1"
    ]
    assert len(vinda) == 1
    assert vinda[0]["fields"]["Quantity On Hand"] >= 136


def test_aggregate_stock_balance_snapshot_rows_merges_duplicate_balance_keys():
    rows = [
        {
            "item_id": "item_vinda_tissue",
            "warehouse_id": "wh_sz_1",
            "warehouse_name": "深圳仓",
            "location_code": "A1",
            "category_id": "paper",
            "category_name": "纸品",
            "item_name": "维达纸巾",
            "brand": "维达",
            "spec": "3层抽纸 24包",
            "unit": "包",
            "quantity_on_hand": 16,
            "reorder_threshold": 100,
            "storage_status": "available",
            "created_at": "2026-05-24T00:00:00+00:00",
            "updated_at": "2026-05-24T00:00:00+00:00",
        },
        {
            "item_id": "item_vinda_tissue",
            "warehouse_id": "wh_sz_1",
            "warehouse_name": "深圳仓",
            "location_code": "A1",
            "category_id": "paper",
            "category_name": "纸品",
            "item_name": "维达纸巾",
            "brand": "维达",
            "spec": "3层抽纸 24包",
            "unit": "包",
            "quantity_on_hand": 120,
            "reorder_threshold": 80,
            "storage_status": "available",
            "created_at": "2026-05-23T00:00:00+00:00",
            "updated_at": "2026-05-25T00:00:00+00:00",
        },
    ]

    result = aggregate_stock_balance_snapshot_rows(rows)

    assert len(result) == 1
    assert result[0]["quantity_on_hand"] == 136
    assert result[0]["reorder_threshold"] == 100
    assert result[0]["created_at"] == "2026-05-23T00:00:00+00:00"
    assert result[0]["updated_at"] == "2026-05-25T00:00:00+00:00"


def test_warehouse_exception_search_returns_expiring_batch_risks():
    response = client.post(
        "/warehouse/exceptions/search",
        json={"item_id": "item_milk_pure", "expiry_risk": "expiring_soon"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["matches"]
    assert body["matches"][0]["item_id"] == "item_milk_pure"
    assert body["matches"][0]["expiry_risk"] == "expiring_soon"


def test_warehouse_fulfillment_check_blocks_low_stock_sku():
    response = client.post(
        "/warehouse/fulfillment/check",
        json={"item_id": "item_vinda_tissue"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item_id"] == "item_vinda_tissue"
    assert body["can_ship"] is False
    assert "insufficient_available_stock" in body["blockers"]
    assert body["next_action"] == "notify_procurement"


def test_warehouse_fulfillment_check_allows_healthy_sku():
    response = client.post(
        "/warehouse/fulfillment/check",
        json={"item_id": "item_office_pen"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item_id"] == "item_office_pen"
    assert body["can_ship"] is True
    assert body["blockers"] == []
    assert body["next_action"] == "release_to_pick"


def test_warehouse_stock_balances_group_item_by_location():
    response = client.get(
        "/warehouse/stock/balances",
        params={"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item_id"] == "item_vinda_tissue"
    assert body["warehouse_id"] == "wh_sz_1"
    assert body["total_quantity_on_hand"] == 136
    assert body["total_quantity_available"] == 136
    locations = {item["location_code"]: item for item in body["locations"]}
    assert set(locations) == {"A1"}
    assert locations["A1"]["quantity_available"] == 136
    assert locations["A1"]["earliest_expiry_date"] == "2027-04-01"


def test_warehouse_order_fulfillment_confirmation_deducts_location_balances_and_preserves_batch_facts():
    before = client.get(
        "/warehouse/stock/balances",
        params={"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1"},
    ).json()
    assert before["total_quantity_available"] == 136

    response = client.post(
        "/warehouse/orders",
        json={
            "order_id": "ORD-CODEX-9001",
            "customer_id": "cus_100",
            "shipping_address": "广东省深圳市",
            "created_by": "warehouse-agent",
            "items": [
                {"item_id": "item_vinda_tissue", "quantity": 20},
                {"item_id": "item_cola_zero", "quantity": 5},
            ],
        },
    )
    assert response.status_code == 200
    created = response.json()
    assert created["order"]["status"] == "pending_fulfillment_review"
    assert created["order"]["selected_warehouse_id"] == "wh_sz_1"
    assert all(line["status"] == "pending_fulfillment_review" for line in created["items"])
    balances = client.get(
        "/warehouse/stock/balances",
        params={"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1"},
    ).json()
    assert balances["total_quantity_on_hand"] == 136
    assert balances["total_quantity_available"] == 136

    candidates = client.get("/warehouse/orders/ORD-CODEX-9001/fulfillment-candidates")
    assert candidates.status_code == 200
    candidate_body = candidates.json()
    assert candidate_body["recommended_warehouse_id"] == "wh_sz_1"
    assert candidate_body["candidates"][0]["can_fulfill"] is True

    confirmed_response = client.post(
        "/warehouse/orders/ORD-CODEX-9001/fulfillment/confirm",
        json={"warehouse_id": "wh_sz_1", "updated_by": "warehouse-agent"},
    )
    assert confirmed_response.status_code == 200
    confirmed = confirmed_response.json()
    assert confirmed["order"]["status"] == "unpaid"
    assert [line["location_code"] for line in confirmed["items"] if line["item_id"] == "item_vinda_tissue"] == ["A1", "A1"]
    assert [line["batch_no"] for line in confirmed["items"] if line["item_id"] == "item_vinda_tissue"] == [
        "BATCH-20260401",
        "BATCH-20260501",
    ]
    assert [line["quantity"] for line in confirmed["items"] if line["item_id"] == "item_vinda_tissue"] == [16, 4]
    balances = client.get(
        "/warehouse/stock/balances",
        params={"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1"},
    ).json()
    assert balances["total_quantity_on_hand"] == 116
    assert balances["total_quantity_available"] == 116

    inventory = client.get("/warehouse/inventory/item_vinda_tissue").json()
    assert inventory["total_quantity_on_hand"] == 116
    assert sum(int(batch["quantity_on_hand"]) for batch in inventory["batches"]) == 116

    response = client.post("/warehouse/orders/ORD-CODEX-9001/pay", json={"updated_by": "warehouse-agent"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["order"]["status"] == "pending_shipment"
    assert all(line["status"] == "pending_shipment" for line in body["items"])


def test_warehouse_order_creation_posts_fulfillment_review_notification(monkeypatch):
    calls: list[dict[str, Any]] = []

    class FakeUrlopenResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"ok": true, "message_id": "om_fulfillment_review"}'

    def fake_urlopen(request, timeout):
        calls.append(
            {
                "url": request.full_url,
                "timeout": timeout,
                "payload": json.loads(request.data.decode("utf-8")),
            }
        )
        return FakeUrlopenResponse()

    monkeypatch.setenv(
        "FEISHU_FULFILLMENT_REVIEW_NOTIFY_URL",
        "http://feishu-adapter.local/warehouse/order-fulfillment-review/send",
    )
    monkeypatch.setenv("FEISHU_FULFILLMENT_REVIEW_CHAT_ID", "oc_warehouse_ops")
    monkeypatch.setattr(warehouse_orders_router.urllib.request, "urlopen", fake_urlopen)

    response = client.post(
        "/warehouse/orders",
        json={
            "order_id": "ORD-CODEX-NOTIFY",
            "customer_id": "cus_100",
            "shipping_address": "广东省深圳市",
            "items": [{"item_id": "item_vinda_tissue", "quantity": 2}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["notification"]["status"] == "sent"
    assert calls[0]["url"] == "http://feishu-adapter.local/warehouse/order-fulfillment-review/send"
    assert calls[0]["payload"]["chat_id"] == "oc_warehouse_ops"
    assert calls[0]["payload"]["order"]["order_id"] == "ORD-CODEX-NOTIFY"
    assert calls[0]["payload"]["candidates"][0]["warehouse_id"] == "wh_sz_1"


def test_warehouse_order_rejects_empty_shipping_address():
    response = client.post(
        "/warehouse/orders",
        json={
            "order_id": "ORD-CODEX-EMPTY-ADDRESS",
            "customer_id": "1",
            "shipping_address": "   ",
            "items": [{"item_id": "item_milk_pure", "warehouse_id": "wh_sz_1", "quantity": 1}],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "shipping_address_required"


def test_warehouse_order_cancel_adds_paid_stock_back_to_original_batches():
    client.post(
        "/warehouse/orders",
        json={
            "order_id": "ORD-CODEX-9002",
            "customer_id": "cus_100",
            "shipping_address": "广东省深圳市",
            "items": [{"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1", "quantity": 20}],
        },
    )
    client.post(
        "/warehouse/orders/ORD-CODEX-9002/fulfillment/confirm",
        json={"warehouse_id": "wh_sz_1", "updated_by": "warehouse-agent"},
    )
    client.post("/warehouse/orders/ORD-CODEX-9002/pay", json={"updated_by": "warehouse-agent"})

    response = client.post("/warehouse/orders/ORD-CODEX-9002/cancel", json={"updated_by": "warehouse-agent"})

    assert response.status_code == 200
    body = response.json()
    assert body["order"]["status"] == "refunded"
    balances = client.get(
        "/warehouse/stock/balances",
        params={"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1"},
    ).json()
    assert balances["total_quantity_available"] == 136

    second_response = client.post("/warehouse/orders/ORD-CODEX-9002/cancel", json={"updated_by": "warehouse-agent"})
    assert second_response.status_code == 200
    balances = client.get(
        "/warehouse/stock/balances",
        params={"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1"},
    ).json()
    assert balances["total_quantity_available"] == 136


def test_warehouse_order_return_after_arrival_adds_stock_back_to_original_batches():
    client.post(
        "/warehouse/orders",
        json={
            "order_id": "ORD-CODEX-9003",
            "customer_id": "cus_100",
            "shipping_address": "广东省深圳市",
            "items": [{"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1", "quantity": 20}],
        },
    )
    client.post(
        "/warehouse/orders/ORD-CODEX-9003/fulfillment/confirm",
        json={"warehouse_id": "wh_sz_1", "updated_by": "warehouse-agent"},
    )
    client.post("/warehouse/orders/ORD-CODEX-9003/pay", json={"updated_by": "warehouse-agent"})
    client.post("/warehouse/orders/ORD-CODEX-9003/ship", json={"updated_by": "delivery-agent"})
    client.post("/warehouse/orders/ORD-CODEX-9003/arrive", json={"updated_by": "delivery-agent"})

    response = client.post("/warehouse/orders/ORD-CODEX-9003/return", json={"updated_by": "warehouse-agent"})

    assert response.status_code == 200
    body = response.json()
    assert body["order"]["status"] == "returned"
    balances = client.get(
        "/warehouse/stock/balances",
        params={"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1"},
    ).json()
    assert balances["total_quantity_available"] == 136


def test_warehouse_order_pay_rejects_insufficient_stock():
    response = client.post(
        "/warehouse/orders",
        json={
            "order_id": "ORD-CODEX-9004",
            "customer_id": "cus_100",
            "shipping_address": "广东省深圳市",
            "items": [{"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1", "quantity": 200}],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "insufficient_available_stock"


def test_warehouse_release_expired_orders_cancels_unpaid_and_restores_stock():
    create = client.post(
        "/warehouse/orders",
        json={
            "order_id": "ORD-CODEX-9005",
            "customer_id": "cus_100",
            "shipping_address": "广东省深圳市",
            "items": [{"item_id": "item_vinda_tissue", "quantity": 20}],
        },
    )
    assert create.status_code == 200
    assert create.json()["order"]["status"] == "pending_fulfillment_review"
    balances = client.get(
        "/warehouse/stock/balances",
        params={"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1"},
    ).json()
    assert balances["total_quantity_available"] == 136

    client.post(
        "/warehouse/orders/ORD-CODEX-9005/fulfillment/confirm",
        json={"warehouse_id": "wh_sz_1", "updated_by": "warehouse-agent"},
    )
    balances = client.get(
        "/warehouse/stock/balances",
        params={"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1"},
    ).json()
    assert balances["total_quantity_available"] == 116

    response = client.post(
        "/warehouse/orders/release-expired",
        json={"processed_by": "warehouse-timeout-job", "now": "2099-01-01T00:00:00+00:00"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["released_count"] == 1
    assert body["released_orders"][0]["order_id"] == "ORD-CODEX-9005"
    assert body["released_orders"][0]["status"] == "canceled"
    balances = client.get(
        "/warehouse/stock/balances",
        params={"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1"},
    ).json()
    assert balances["total_quantity_available"] == 136
