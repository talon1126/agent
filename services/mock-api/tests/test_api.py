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
    RECEIVED_INVENTORY_BALANCES,
    WAREHOUSE_BATCH_QUANTITY_OVERRIDES,
    WAREHOUSE_INVENTORY_MOVEMENTS,
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
from app.routers.warehouse import purchase_orders as warehouse_purchase_orders_router
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

    def list_flash_sale_claims(
        self,
        *,
        flash_sale_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
    ):
        rows = [dict(claim) for claim in self.claims.values()]
        if flash_sale_id is not None:
            rows = [claim for claim in rows if int(claim["flash_sale_id"]) == flash_sale_id]
        if status:
            rows = [claim for claim in rows if claim["status"] == status]
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


class FakeProductOperationsRepository(FakeRankingRepository):
    """Repository double for the Feishu Product Operations read model.

    H8 exposes a business-facing table contract that combines catalog facts,
    customer review summaries, Flash Deals, and leaderboard signals. The fake
    keeps those inputs in memory so the route test can verify the response
    shape without requiring PostgreSQL or Redis.
    """

    def search_items(self, query: str | None = None, *, category_id: str | None = None):
        assert query is None
        rows = [
            {
                "item_id": "item_wireless_earbuds",
                "item_name": "Wireless Earbuds",
                "brand": "Talon Audio",
                "spec": "Bluetooth 5.3",
                "category_id": "electronics",
                "category_name": "Electronics",
                "price": 59.99,
                "image": "https://oss.example.com/products/item_wireless_earbuds.jpg",
            }
        ]
        return [row for row in rows if category_id is None or row["category_id"] == category_id]

    def item_review_summary(self, item_id: str):
        return {"average_rating": 4.8, "review_count": 128}

    def list_flash_sales(self, *, status: str | None = None, limit: int = 20):
        return [
            {
                "id": 1,
                "item_id": "item_wireless_earbuds",
                "item_price": 59.99,
                "sale_price": 49.99,
                "stock_limit": 50,
                "status": "active",
                "starts_at": "2026-06-17T00:00:00+08:00",
                "ends_at": "2026-06-30T00:00:00+08:00",
                "created_at": "2026-06-17T00:00:00+08:00",
                "updated_at": "2026-06-17T00:00:00+08:00",
            }
        ][:limit]


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


def purchase_order_fixture(**overrides: Any) -> dict[str, Any]:
    """Build a minimal purchase order row for warehouse/procurement route tests.

    The mock API keeps procurement fallback data in memory, so tests can append
    explicit rows without going through the full purchase order approval flow
    when the behavior under test only depends on purchase order status fields.
    """
    order = {
        "purchase_order_id": "PO-CODEX-ARRIVAL-1",
        "approval_status": "approved",
        "supplier_id": "sup_vinda",
        "supplier_name": "Vinda Supplier",
        "item_id": "item_vinda_tissue",
        "warehouse_id": "wh_sz_1",
        "warehouse_name": "深圳仓",
        "location_code": "A1",
        "quantity": 20,
        "unit_price": 10,
        "currency": "CNY",
        "estimated_total_price": 200,
        "lead_time_days": 1,
        "estimated_arrival_date": "2026-06-17",
        "payment_status": "paid",
        "warehouse_sync_status": "pending_arrival",
        "arrived_at": "",
        "created_by": "procurement-agent",
        "created_at": "2026-06-16T10:00:00+00:00",
        "updated_at": "2026-06-16T10:00:00+00:00",
    }
    order.update(overrides)
    return order


@pytest.fixture(autouse=True)
def clear_received_inventory_batches():
    DELIVERY_CASES.clear()
    RECEIVED_INVENTORY_BALANCES.clear()
    WAREHOUSE_BATCH_QUANTITY_OVERRIDES.clear()
    WAREHOUSE_INVENTORY_MOVEMENTS.clear()
    WAREHOUSE_ORDERS.clear()
    WAREHOUSE_ORDER_ITEMS.clear()
    PURCHASE_ORDERS.clear()
    CART_ITEMS.clear()
    yield
    DELIVERY_CASES.clear()
    RECEIVED_INVENTORY_BALANCES.clear()
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
            "image": "",
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


def test_order_fulfillment_table_schema_and_rows_expose_business_fields():
    """Protect the H8 Order Fulfillment read model contract for Feishu tables.

    The endpoint is consumed by feishu-adapter table sync, so it must return
    human-readable field names and row fields without leaking order item row ids
    or other implementation-only persistence details.
    """

    create_response = client.post(
        "/warehouse/orders",
        json={
            "order_id": "ORD-CODEX-TABLE-1",
            "customer_id": "cus_100",
            "shipping_address": "广东省深圳市南山区",
            "items": [{"item_id": "item_vinda_tissue", "quantity": 2}],
            "delivery_provider_id": "sf",
        },
    )
    assert create_response.status_code == 200
    pay_response = client.post("/warehouse/orders/ORD-CODEX-TABLE-1/pay", json={"updated_by": "customer"})
    assert pay_response.status_code == 200

    schema_response = client.get("/warehouse/orders/fulfillment/table-schema")
    rows_response = client.post(
        "/warehouse/orders/fulfillment/table-rows",
        json={"status": "pending_fulfillment_review", "limit": 10},
    )

    assert schema_response.status_code == 200
    schema = schema_response.json()
    assert schema["ok"] is True
    assert schema["schema_id"] == "order_fulfillment"
    field_names = [field["name"] for field in schema["fields"]]
    assert field_names[:4] == ["Order ID", "Status", "Customer", "Warehouse"]
    assert "Order Item ID" not in field_names

    assert rows_response.status_code == 200
    body = rows_response.json()
    assert body["ok"] is True
    assert body["schema_id"] == "order_fulfillment"
    assert body["count"] == 1
    row = body["items"][0]
    assert row["order_id"] == "ORD-CODEX-TABLE-1"
    assert row["fields"]["Order ID"] == "ORD-CODEX-TABLE-1"
    assert row["fields"]["Status"] == "pending_fulfillment_review"
    assert row["fields"]["Item Summary"] == "item_vinda_tissue x 2"
    assert "id" not in row["fields"]


def test_order_items_table_schema_and_rows_expose_line_fields():
    """Protect the H8 Order Items read model used by Feishu detail tables."""

    create_response = client.post(
        "/warehouse/orders",
        json={
            "order_id": "ORD-CODEX-LINES-1",
            "customer_id": "cus_100",
            "shipping_address": "广东省深圳市南山区",
            "items": [{"item_id": "item_vinda_tissue", "quantity": 2}],
            "delivery_provider_id": "sf",
        },
    )
    assert create_response.status_code == 200

    schema_response = client.get("/warehouse/orders/items/table-schema")
    rows_response = client.post(
        "/warehouse/orders/items/table-rows",
        json={"order_id": "ORD-CODEX-LINES-1", "limit": 10},
    )

    assert schema_response.status_code == 200
    schema = schema_response.json()
    assert schema["ok"] is True
    assert schema["schema_id"] == "order_items"
    field_names = [field["name"] for field in schema["fields"]]
    assert field_names[:4] == ["Order Item ID", "Order ID", "Status", "Customer"]
    assert "id" not in field_names

    assert rows_response.status_code == 200
    body = rows_response.json()
    assert body["ok"] is True
    assert body["schema_id"] == "order_items"
    assert body["count"] == 1
    row = body["items"][0]
    assert row["order_item_id"] == "ORD-CODEX-LINES-1:item_vinda_tissue:"
    assert row["fields"]["Order Item ID"] == "ORD-CODEX-LINES-1:item_vinda_tissue:"
    assert row["fields"]["Order ID"] == "ORD-CODEX-LINES-1"
    assert row["fields"]["Item ID"] == "item_vinda_tissue"
    assert row["fields"]["Quantity"] == 2
    assert "id" not in row["fields"]


def test_order_items_table_rows_support_offset_pagination():
    """Protect the shared H1 pagination contract for the H8 Order Items table."""

    create_response = client.post(
        "/warehouse/orders",
        json={
            "order_id": "ORD-CODEX-LINES-PAGED",
            "customer_id": "cus_100",
            "shipping_address": "广东省深圳市南山区",
            "items": [
                {"item_id": "item_vinda_tissue", "quantity": 1},
                {"item_id": "item_milk_pure", "quantity": 1},
                {"item_id": "item_cola_zero", "quantity": 1},
            ],
            "delivery_provider_id": "sf",
        },
    )
    assert create_response.status_code == 200

    response = client.post(
        "/warehouse/orders/items/table-rows",
        json={"order_id": "ORD-CODEX-LINES-PAGED", "limit": 2, "offset": 2},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["count"] == 1
    assert body["has_more"] is False
    assert body["next_offset"] is None
    assert body["items"][0]["fields"]["Order ID"] == "ORD-CODEX-LINES-PAGED"
    assert body["items"][0]["fields"]["Item ID"] == "item_cola_zero"


def test_product_operations_table_schema_and_rows_merge_catalog_deal_and_ranking(monkeypatch):
    """Protect the H8 Product Operations read model contract for Feishu tables.

    Product Operations powers a future Feishu app page, so the route should
    present operational product signals in one row per item while keeping the
    table fields readable for non-developer employees.
    """

    repository = FakeProductOperationsRepository()
    monkeypatch.setattr(search_router, "get_warehouse_repository", lambda: repository)
    monkeypatch.setattr(category_rankings_router, "get_warehouse_repository", lambda: repository)
    monkeypatch.setattr(category_rankings_router, "get_category_ranking_redis", lambda: None)

    schema_response = client.get("/products/operations/table-schema")
    rows_response = client.post(
        "/products/operations/table-rows",
        json={"category_id": "electronics", "limit": 10},
    )

    assert schema_response.status_code == 200
    schema = schema_response.json()
    assert schema["ok"] is True
    assert schema["schema_id"] == "product_operations"
    field_names = [field["name"] for field in schema["fields"]]
    assert "Item ID" in field_names
    assert "Image" in field_names
    assert "Category ID" not in field_names
    assert "Database ID" not in field_names

    assert rows_response.status_code == 200
    body = rows_response.json()
    assert body["ok"] is True
    assert body["schema_id"] == "product_operations"
    assert body["count"] == 1
    row = body["items"][0]
    assert row["item_id"] == "item_wireless_earbuds"
    assert row["fields"]["Item ID"] == "item_wireless_earbuds"
    assert row["fields"]["Image"] == "https://oss.example.com/products/item_wireless_earbuds.jpg"
    assert row["fields"]["Category"] == "Electronics"
    assert row["fields"]["Rating"] == 4.8
    assert row["fields"]["Review Count"] == 128
    assert row["fields"]["Flash Deal Status"] == "active"
    assert row["fields"]["Flash Sale Price"] == 49.99
    assert row["fields"]["Ranking Score"] == 91.0


def test_items_table_schema_and_rows_expose_catalog_fields(monkeypatch):
    """Protect the H9 Items read model used by Feishu product pages."""

    repository = FakeProductOperationsRepository()
    monkeypatch.setattr(search_router, "get_warehouse_repository", lambda: repository)

    schema_response = client.get("/items/table-schema")
    rows_response = client.post("/items/table-rows", json={"category_id": "electronics", "limit": 10})

    assert schema_response.status_code == 200
    schema = schema_response.json()
    assert schema["ok"] is True
    assert schema["schema_id"] == "items"
    field_names = [field["name"] for field in schema["fields"]]
    field_types = {field["name"]: field["type"] for field in schema["fields"]}
    assert field_names[:5] == ["Item ID", "Product Image", "Image URL", "Item Name", "Brand"]
    assert field_types["Product Image"] == "image"
    assert field_types["Image URL"] == "text"
    assert "Image" not in field_names
    assert "Category ID" not in field_names

    assert rows_response.status_code == 200
    body = rows_response.json()
    assert body["ok"] is True
    assert body["schema_id"] == "items"
    row = body["items"][0]
    assert row["item_id"] == "item_wireless_earbuds"
    assert row["fields"]["Image URL"] == "https://oss.example.com/products/item_wireless_earbuds.jpg"
    assert "Product Image" not in row["fields"]
    assert "Image" not in row["fields"]
    assert row["fields"]["Category"] == "Electronics"
    assert row["fields"]["Rating"] == 4.8


def test_items_table_rows_support_offset_pagination(monkeypatch):
    """Protect the shared H1 pagination contract for the H9 Items read model.

    Feishu table sync jobs must be able to read more than the first page of
    catalog rows. This test uses a repository double with three rows, requests
    the second page, and verifies that the endpoint returns the standard
    `has_more` and `next_offset` envelope consumed by feishu-adapter.
    """

    class PaginatedItemsRepository(FakeProductOperationsRepository):
        def search_items(self, query: str | None = None, *, category_id: str | None = None):
            assert query is None
            return [
                {
                    "item_id": f"item_page_{index}",
                    "item_name": f"Paged Item {index}",
                    "brand": "Talon",
                    "spec": "Demo",
                    "category_id": "electronics",
                    "category_name": "Electronics",
                    "price": 10 + index,
                    "image": f"https://oss.example.com/products/item_page_{index}.jpg",
                }
                for index in range(1, 4)
            ]

    monkeypatch.setattr(search_router, "get_warehouse_repository", lambda: PaginatedItemsRepository())

    response = client.post("/items/table-rows", json={"category_id": "electronics", "limit": 2, "offset": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["count"] == 1
    assert body["has_more"] is False
    assert body["next_offset"] is None
    assert [item["item_id"] for item in body["items"]] == ["item_page_3"]


def test_flash_sales_table_schema_and_rows_expose_activity_fields(monkeypatch):
    """Protect the H10 Flash Sales read model used by Feishu operations."""

    repository = FakeFlashSaleRepository()
    redis_client = FakeFlashSaleRedis(stocks={1: 1})
    monkeypatch.setattr(flash_sales_router, "get_warehouse_repository", lambda: repository)
    monkeypatch.setattr(flash_sales_router, "get_flash_sale_redis", lambda: redis_client)

    schema_response = client.get("/flash-sales/table-schema")
    rows_response = client.post("/flash-sales/table-rows", json={"status": "active", "limit": 10})

    assert schema_response.status_code == 200
    assert schema_response.json()["schema_id"] == "flash_sales"
    assert rows_response.status_code == 200
    body = rows_response.json()
    assert body["ok"] is True
    row = body["items"][0]
    assert row["flash_sale_id"] == "1"
    assert row["fields"]["Flash Sale ID"] == "1"
    assert row["fields"]["Item ID"] == "item_milk_pure"
    assert row["fields"]["Original Price"] == 18.4
    assert row["fields"]["Stock Remaining"] == 1


def test_flash_sales_table_rows_support_offset_pagination(monkeypatch):
    """Protect the shared H1 pagination contract for H10 Flash Sales rows."""

    sales = [active_flash_sale(id=index, item_id=f"item_flash_{index}") for index in range(1, 4)]
    repository = FakeFlashSaleRepository(sales=sales)
    monkeypatch.setattr(flash_sales_router, "get_warehouse_repository", lambda: repository)
    monkeypatch.setattr(flash_sales_router, "get_flash_sale_redis", lambda: FakeFlashSaleRedis(stocks={}))

    response = client.post("/flash-sales/table-rows", json={"status": "active", "limit": 2, "offset": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["count"] == 1
    assert body["has_more"] is False
    assert body["next_offset"] is None
    assert [item["flash_sale_id"] for item in body["items"]] == ["3"]


def test_flash_sale_claims_table_schema_and_rows_expose_result_fields(monkeypatch):
    """Protect the H10 Flash Sale Claims read model used by Feishu operations."""

    repository = FakeFlashSaleRepository()
    repository.claims[(1, 100)] = {
        "id": 7,
        "flash_sale_id": 1,
        "user_id": 100,
        "item_id": "item_milk_pure",
        "order_id": "ORD-FLASH-1",
        "status": "ordered",
        "created_at": "2026-06-18T10:00:00+00:00",
        "updated_at": "2026-06-18T10:01:00+00:00",
    }
    monkeypatch.setattr(flash_sales_router, "get_warehouse_repository", lambda: repository)

    schema_response = client.get("/flash-sales/claims/table-schema")
    rows_response = client.post("/flash-sales/claims/table-rows", json={"status": "ordered", "limit": 10})

    assert schema_response.status_code == 200
    assert schema_response.json()["schema_id"] == "flash_sale_claims"
    assert rows_response.status_code == 200
    body = rows_response.json()
    assert body["ok"] is True
    row = body["items"][0]
    assert row["claim_id"] == "7"
    assert row["fields"]["Claim ID"] == "7"
    assert row["fields"]["Flash Sale ID"] == "1"
    assert row["fields"]["Order ID"] == "ORD-FLASH-1"
    assert "id" not in row["fields"]


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
            "image": "",
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


def test_procurement_mock_recommends_replenishment_for_low_balance_stock():
    response = client.post("/procurement/mock", json={"item_id": "item_cola_zero"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item_id"] == "item_cola_zero"
    assert body["recommendation"] == "create_purchase_request"
    assert body["system"] == "mock-procurement"


def test_legacy_replenishment_request_routes_are_removed():
    """Ensure procurement demand no longer exposes the standalone request API."""

    payload = {
        "source": "warehouse",
        "warehouse_id": "wh_sz_1",
        "location_code": "A1",
        "item_id": "item_vinda_tissue",
        "reason": "available_quantity_below_reorder_threshold",
        "created_by": "warehouse:user-001",
    }

    assert client.post("/procurement/replenishment-requests", json=payload).status_code == 404
    assert client.get("/procurement/replenishment-requests?status=pending").status_code == 404
    assert client.get("/procurement/replenishment-requests/table-schema").status_code == 404


def create_purchase_order_for_test(
    item_id: str = "item_vinda_tissue",
    location_code: str = "A1",
) -> dict:
    """Create a pending purchase order through the current procurement demand API."""

    response = client.post(
        "/procurement/purchase-orders",
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
    return response.json()["purchase_order"]


def test_create_and_list_purchase_orders_from_warehouse_signal():
    order = create_purchase_order_for_test()

    assert order["purchase_order_id"].startswith("PO-")
    assert order["approval_status"] == "pending"
    assert order["source"] == "warehouse"
    assert order["warehouse_id"] == "wh_sz_1"
    assert order["location_code"] == "A1"
    assert order["item_id"] == "item_vinda_tissue"
    assert "current_quantity" not in order
    assert "reorder_threshold" not in order
    assert "suggested_quantity" not in order
    assert order["supplier_id"] == "supplier_paper_sz"
    assert order["supplier_name"] == "深圳纸品供应商"
    assert order["quantity"] == 100
    assert order["payment_status"] == "unpaid"
    assert order["warehouse_sync_status"] == "pending_arrival"
    assert "request_id" not in order

    list_response = client.get("/procurement/purchase-orders?approval_status=pending")

    assert list_response.status_code == 200
    listed = list_response.json()
    assert listed["ok"] is True
    assert any(
        item["purchase_order_id"] == order["purchase_order_id"]
        and item["approval_status"] == "pending"
        for item in listed["items"]
    )


def test_approve_purchase_order_updates_approval_status():
    order = create_purchase_order_for_test()
    expected_arrival_date = (
        datetime.fromisoformat(order["created_at"]).date() + timedelta(days=3)
    ).isoformat()

    response = client.post(
        f"/procurement/purchase-orders/{order['purchase_order_id']}/approve",
        json={"created_by": "procurement:user-001"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["purchase_order"]["purchase_order_id"] == order["purchase_order_id"]
    assert body["purchase_order"]["approval_status"] == "approved"
    assert body["purchase_order"]["supplier_id"] == "supplier_paper_sz"
    assert body["purchase_order"]["supplier_name"] == "深圳纸品供应商"
    assert body["purchase_order"]["item_id"] == "item_vinda_tissue"
    assert body["purchase_order"]["warehouse_id"] == order["warehouse_id"]
    assert body["purchase_order"]["warehouse_name"] == order["warehouse_name"]
    assert body["purchase_order"]["location_code"] == order["location_code"]
    assert body["purchase_order"]["quantity"] == order["quantity"]
    assert body["purchase_order"]["unit_price"] == 8
    assert body["purchase_order"]["currency"] == "CNY"
    assert body["purchase_order"]["estimated_total_price"] == order["quantity"] * 8
    assert body["purchase_order"]["lead_time_days"] == 3
    assert body["purchase_order"]["estimated_arrival_date"] == expected_arrival_date
    assert body["purchase_order"]["payment_status"] == "unpaid"
    assert body["purchase_order"]["warehouse_sync_status"] == "pending_arrival"
    assert "request_id" not in body["purchase_order"]

    orders_response = client.get(
        f"/procurement/purchase-orders?purchase_order_id={order['purchase_order_id']}"
    )
    assert orders_response.status_code == 200
    orders = orders_response.json()
    assert orders["ok"] is True
    assert orders["count"] == 1
    assert orders["items"][0]["purchase_order_id"] == body["purchase_order"]["purchase_order_id"]
    assert orders["items"][0]["approval_status"] == "approved"


def test_approve_purchase_order_is_idempotent():
    order = create_purchase_order_for_test()
    approve_url = f"/procurement/purchase-orders/{order['purchase_order_id']}/approve"

    first = client.post(approve_url, json={"created_by": "procurement:user-001"})
    second = client.post(approve_url, json={"created_by": "procurement:user-001"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["purchase_order"]["purchase_order_id"] == first.json()["purchase_order"]["purchase_order_id"]
    assert second.json()["purchase_order"]["estimated_arrival_date"] == first.json()["purchase_order"]["estimated_arrival_date"]
    orders = client.get(
        f"/procurement/purchase-orders?purchase_order_id={order['purchase_order_id']}"
    ).json()
    assert orders["count"] == 1


def test_reject_purchase_order_updates_approval_status_without_approving():
    order = create_purchase_order_for_test()

    response = client.post(
        f"/procurement/purchase-orders/{order['purchase_order_id']}/reject",
        json={"reason": "供应商暂不稳定，先人工复核。", "updated_by": "procurement:user-001"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["purchase_order"]["purchase_order_id"] == order["purchase_order_id"]
    assert body["purchase_order"]["approval_status"] == "rejected"
    assert body["purchase_order"]["reason"] == "供应商暂不稳定，先人工复核。"

    orders = client.get(
        f"/procurement/purchase-orders?purchase_order_id={order['purchase_order_id']}"
    ).json()
    assert orders["count"] == 1
    assert orders["items"][0]["approval_status"] == "rejected"


def test_purchase_order_decision_returns_404_for_unknown_order():
    response = client.post(
        "/procurement/purchase-orders/PO-DOES-NOT-EXIST/approve",
        json={"created_by": "procurement:user-001"},
    )

    assert response.status_code == 404


def test_create_purchase_order_requires_default_supplier():
    response = client.post(
        "/procurement/purchase-orders",
        json={
            "source": "warehouse",
            "warehouse_id": "wh_sz_1",
            "location_code": "B1",
            "item_id": "item_office_pen",
            "reason": "available_quantity_below_reorder_threshold",
            "created_by": "warehouse:user-001",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "default supplier not found for item"


def test_approve_purchase_order_batch_processes_pending_orders():
    first = create_purchase_order_for_test(item_id="item_vinda_tissue", location_code="A1")
    second = create_purchase_order_for_test(item_id="item_milk_pure", location_code="C1")
    rejected = create_purchase_order_for_test(item_id="item_vinda_tissue", location_code="A1")
    client.post(
        f"/procurement/purchase-orders/{rejected['purchase_order_id']}/reject",
        json={"reason": "manual hold", "updated_by": "procurement:user-001"},
    )

    response = client.post(
        "/procurement/purchase-orders/approve-batch",
        json={"created_by": "procurement:user-001"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["approved_count"] == 2
    assert body["skipped_count"] == 0
    approved_order_ids = {
        item["purchase_order_id"] for item in body["approved_purchase_orders"]
    }
    assert approved_order_ids == {first["purchase_order_id"], second["purchase_order_id"]}

    refreshed = client.get("/procurement/purchase-orders").json()["items"]
    statuses = {item["purchase_order_id"]: item["approval_status"] for item in refreshed}
    assert statuses[first["purchase_order_id"]] == "approved"
    assert statuses[second["purchase_order_id"]] == "approved"
    assert statuses[rejected["purchase_order_id"]] == "rejected"


def test_confirm_purchase_order_arrival_batch_marks_unsynced_without_inventory_mutation():
    first_order = create_purchase_order_for_test(
        item_id="item_vinda_tissue",
        location_code="A1",
    )
    second_order = create_purchase_order_for_test(
        item_id="item_milk_pure",
        location_code="C1",
    )
    first_order = client.post(
        f"/procurement/purchase-orders/{first_order['purchase_order_id']}/approve",
        json={"created_by": "procurement:user-001"},
    ).json()["purchase_order"]
    second_order = client.post(
        f"/procurement/purchase-orders/{second_order['purchase_order_id']}/approve",
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

    first_inventory = client.get(
        "/warehouse/stock/balances",
        params={"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1"},
    ).json()
    second_inventory = client.get(
        "/warehouse/stock/balances",
        params={"item_id": "item_milk_pure", "warehouse_id": "wh_sz_1"},
    ).json()
    assert first_inventory["total_quantity_on_hand"] == 136
    assert second_inventory["total_quantity_on_hand"] == 140
    assert WAREHOUSE_INVENTORY_MOVEMENTS == []

    removed_jobs_route = client.get("/warehouse/inventory-sync-jobs?status=pending")
    assert removed_jobs_route.status_code == 404

    repeat = client.post(
        "/procurement/purchase-orders/confirm-arrival-batch",
        json={"purchase_order_ids": [first_order["purchase_order_id"]], "received_by": "warehouse:user-001"},
    )

    assert repeat.status_code == 200
    repeat_body = repeat.json()
    assert repeat_body["confirmed_items"][0]["action"] == "reused"


def test_warehouse_syncs_paid_arrived_purchase_orders_to_inventory_balances():
    pending_order = create_purchase_order_for_test(
        item_id="item_vinda_tissue",
        location_code="A1",
    )
    order = client.post(
        f"/procurement/purchase-orders/{pending_order['purchase_order_id']}/approve",
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
    assert arrival["confirmed_items"][0]["arrived_at"]

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
    assert "batch_no" not in synced
    assert synced["location_code"] == "A1"
    assert synced["warehouse_sync_status"] == "synced"
    assert body["next_action"] == "已将已支付且未同步的采购到仓单写入库存余额表。"


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
    assert not any(
        item["order_id"] == order["purchase_order_id"] for item in WAREHOUSE_INVENTORY_MOVEMENTS
    )

def test_purchase_order_sync_inventory_endpoint_updates_one_order_without_movement(monkeypatch):
    table_sync_requests = []

    class FakeTableSyncResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps({"ok": True, "synced_count": 1}).encode("utf-8")

    def fake_urlopen(request, timeout):
        table_sync_requests.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "body": json.loads(request.data.decode("utf-8")),
                "timeout": timeout,
            }
        )
        return FakeTableSyncResponse()

    monkeypatch.setenv(
        "FEISHU_PROCUREMENT_PURCHASE_ORDER_SYNC_URL",
        "http://feishu-adapter/procurement/purchase-orders-table/sync",
    )
    monkeypatch.setenv(
        "FEISHU_WAREHOUSE_INVENTORY_BALANCE_SYNC_URL",
        "http://feishu-adapter/warehouse/inventory-balances-table/sync",
    )
    monkeypatch.setattr(warehouse_purchase_orders_router.urllib.request, "urlopen", fake_urlopen)
    pending_order = create_purchase_order_for_test(
        item_id="item_vinda_tissue",
        location_code="A1",
    )
    order = client.post(
        f"/procurement/purchase-orders/{pending_order['purchase_order_id']}/approve",
        json={"created_by": "procurement:user-001"},
    ).json()["purchase_order"]
    for item in PURCHASE_ORDERS:
        if item["purchase_order_id"] == order["purchase_order_id"]:
            item["payment_status"] = "paid"
            item["location_code"] = "B1"

    client.post(
        "/procurement/purchase-orders/confirm-arrival-batch",
        json={"purchase_order_ids": [order["purchase_order_id"]], "received_by": "warehouse:user-001"},
    )

    response = client.post(
        f"/warehouse/purchase-orders/{order['purchase_order_id']}/sync-inventory",
        json={"processed_by": "feishu:user-001", "trigger_source": "feishu_bitable_button"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status"] == "synced"
    assert body["updated_balance_count"] == 1
    assert body["purchase_order"]["purchase_order_id"] == order["purchase_order_id"]
    assert body["purchase_order"]["warehouse_sync_status"] == "synced"
    assert body["movement_count"] == 0
    assert body["table_sync"]["status"] == "sent"
    assert body["balance_table_sync"]["status"] == "sent"
    assert table_sync_requests == [
        {
            "url": "http://feishu-adapter/procurement/purchase-orders-table/sync",
            "method": "POST",
            "body": {"purchase_order_id": order["purchase_order_id"], "limit": 1},
            "timeout": 5,
        },
        {
            "url": "http://feishu-adapter/warehouse/inventory-balances-table/sync",
            "method": "POST",
            "body": {
                "item_id": order["item_id"],
                "warehouse_id": order["warehouse_id"],
                "limit": 500,
            },
            "timeout": 5,
        },
    ]
    assert not any(
        item["order_id"] == order["purchase_order_id"] for item in WAREHOUSE_INVENTORY_MOVEMENTS
    )


def test_purchase_order_sync_inventory_endpoint_accepts_feishu_body_purchase_order_id(monkeypatch):
    table_sync_requests = []

    class FakeTableSyncResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps({"ok": True, "synced_count": 1}).encode("utf-8")

    def fake_urlopen(request, timeout):
        table_sync_requests.append(json.loads(request.data.decode("utf-8")))
        return FakeTableSyncResponse()

    monkeypatch.setenv(
        "FEISHU_PROCUREMENT_PURCHASE_ORDER_SYNC_URL",
        "http://feishu-adapter/procurement/purchase-orders-table/sync",
    )
    monkeypatch.setenv(
        "FEISHU_WAREHOUSE_INVENTORY_BALANCE_SYNC_URL",
        "http://feishu-adapter/warehouse/inventory-balances-table/sync",
    )
    monkeypatch.setattr(warehouse_purchase_orders_router.urllib.request, "urlopen", fake_urlopen)
    pending_order = create_purchase_order_for_test(
        item_id="item_vinda_tissue",
        location_code="A1",
    )
    order = client.post(
        f"/procurement/purchase-orders/{pending_order['purchase_order_id']}/approve",
        json={"created_by": "procurement:user-001"},
    ).json()["purchase_order"]
    for item in PURCHASE_ORDERS:
        if item["purchase_order_id"] == order["purchase_order_id"]:
            item["payment_status"] = "paid"
            item["location_code"] = "B1"

    client.post(
        "/procurement/purchase-orders/confirm-arrival-batch",
        json={"purchase_order_ids": [order["purchase_order_id"]], "received_by": "warehouse:user-001"},
    )

    response = client.post(
        "/warehouse/purchase-orders/%7B%7BPurchase%20Order%20ID%7D%7D/sync-inventory",
        json={
            "purchase_order_id": order["purchase_order_id"],
            "processed_by": "feishu:user-001",
            "trigger_source": "feishu_bitable_button",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status"] == "synced"
    assert body["purchase_order"]["purchase_order_id"] == order["purchase_order_id"]
    assert body["purchase_order"]["warehouse_sync_status"] == "synced"
    assert table_sync_requests == [
        {"purchase_order_id": order["purchase_order_id"], "limit": 1},
        {
            "item_id": order["item_id"],
            "warehouse_id": order["warehouse_id"],
            "limit": 500,
        },
    ]


def test_warehouse_lists_today_paid_purchase_order_arrivals_only():
    PURCHASE_ORDERS.extend(
        [
            purchase_order_fixture(purchase_order_id="PO-TODAY-PAID"),
            purchase_order_fixture(
                purchase_order_id="PO-TODAY-UNPAID",
                payment_status="unpaid",
            ),
            purchase_order_fixture(
                purchase_order_id="PO-TOMORROW-PAID",
                estimated_arrival_date="2026-06-18",
            ),
        ]
    )

    response = client.post(
        "/warehouse/purchase-orders/today-arrivals",
        json={"target_date": "2026-06-17"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["target_date"] == "2026-06-17"
    assert [item["purchase_order_id"] for item in body["items"]] == ["PO-TODAY-PAID"]


def test_warehouse_purchase_arrival_notification_posts_without_changing_purchase_orders(monkeypatch):
    calls: list[dict[str, Any]] = []
    PURCHASE_ORDERS.append(purchase_order_fixture(purchase_order_id="PO-TODAY-NOTIFY"))

    class FakeUrlopenResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"ok": true, "message_id": "om_purchase_arrival"}'

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
        "FEISHU_PURCHASE_ARRIVAL_NOTIFY_URL",
        "http://feishu-adapter.local/warehouse/purchase-arrival-review/send",
    )
    monkeypatch.setenv("FEISHU_PURCHASE_ARRIVAL_NOTIFY_CHAT_ID", "oc_warehouse_ops")
    monkeypatch.setattr("app.routers.warehouse.purchase_orders.urllib.request.urlopen", fake_urlopen)

    response = client.post(
        "/warehouse/purchase-orders/arrival-notifications/send",
        json={"target_date": "2026-06-17"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["notification"]["status"] == "sent"
    assert calls[0]["url"] == "http://feishu-adapter.local/warehouse/purchase-arrival-review/send"
    assert calls[0]["payload"]["chat_id"] == "oc_warehouse_ops"
    assert calls[0]["payload"]["items"][0]["purchase_order_id"] == "PO-TODAY-NOTIFY"
    assert PURCHASE_ORDERS[0]["warehouse_sync_status"] == "pending_arrival"


def test_warehouse_purchase_arrival_notification_falls_back_to_fulfillment_review_chat(monkeypatch):
    calls: list[dict[str, Any]] = []
    PURCHASE_ORDERS.append(purchase_order_fixture(purchase_order_id="PO-TODAY-FALLBACK-CHAT"))

    class FakeUrlopenResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"ok": true, "message_id": "om_purchase_arrival"}'

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
        "FEISHU_PURCHASE_ARRIVAL_NOTIFY_URL",
        "http://feishu-adapter.local/warehouse/purchase-arrival-review/send",
    )
    monkeypatch.delenv("FEISHU_PURCHASE_ARRIVAL_NOTIFY_CHAT_ID", raising=False)
    monkeypatch.setenv("FEISHU_FULFILLMENT_REVIEW_CHAT_ID", "oc_warehouse_ops")
    monkeypatch.setattr("app.routers.warehouse.purchase_orders.urllib.request.urlopen", fake_urlopen)

    response = client.post(
        "/warehouse/purchase-orders/arrival-notifications/send",
        json={"target_date": "2026-06-17"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert calls[0]["payload"]["chat_id"] == "oc_warehouse_ops"


def test_purchase_orders_can_be_filtered_by_arrived_unsynced_status():
    pending_order = create_purchase_order_for_test(
        item_id="item_vinda_tissue",
        location_code="A1",
    )
    order = client.post(
        f"/procurement/purchase-orders/{pending_order['purchase_order_id']}/approve",
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
    pending_order = create_purchase_order_for_test()
    approve = client.post(
        f"/procurement/purchase-orders/{pending_order['purchase_order_id']}/approve",
        json={"created_by": "procurement:user-001"},
    ).json()

    request_schema_response = client.get("/procurement/replenishment-requests/table-schema")
    order_schema = client.get("/procurement/purchase-orders/table-schema").json()
    order_rows = client.post(
        "/procurement/purchase-orders/table-rows",
        json={"purchase_order_id": pending_order["purchase_order_id"]},
    ).json()

    assert request_schema_response.status_code == 404
    assert order_schema["ok"] is True
    assert order_schema["schema_id"] == "procurement_purchase_orders"
    order_field_names = [field["name"] for field in order_schema["fields"]]
    assert "Purchase Order ID" in order_field_names
    assert "Approval Status" in order_field_names
    assert "Request ID" not in order_field_names
    assert "Supplier ID" not in order_field_names
    assert "Item ID" not in order_field_names
    assert "Warehouse ID" in order_field_names
    assert "Location" in order_field_names
    assert "Reason" in order_field_names
    sync_inventory_field = next(field for field in order_schema["fields"] if field["name"] == "Sync Inventory")
    assert sync_inventory_field["type"] == "button"
    assert "Payment Status" in order_field_names
    assert "Warehouse Sync Status" in order_field_names
    assert "Estimated Arrival Date" in order_field_names
    assert "Arrived At" in order_field_names
    assert "Last Synced At" not in order_field_names
    assert "Sync Status" not in order_field_names
    assert "Source Version" not in order_field_names

    assert order_rows["ok"] is True
    assert order_rows["count"] == 1
    order_fields = order_rows["items"][0]["fields"]
    assert order_fields["Purchase Order ID"] == approve["purchase_order"]["purchase_order_id"]
    assert order_fields["Approval Status"] == "approved"
    assert "Request ID" not in order_fields
    assert "Supplier ID" not in order_fields
    assert "Item ID" not in order_fields
    assert order_fields["Warehouse ID"] == pending_order["warehouse_id"]
    assert order_fields["Location"] == pending_order["location_code"]
    assert order_fields["Reason"] == pending_order["reason"]
    assert "Sync Inventory" not in order_fields
    assert order_fields["Payment Status"] == "unpaid"
    assert order_fields["Warehouse Sync Status"] == "pending_arrival"
    assert order_fields["Estimated Arrival Date"] == approve["purchase_order"]["estimated_arrival_date"]
    assert order_fields["Arrived At"] == ""
    assert "Last Synced At" not in order_fields
    assert "Sync Status" not in order_fields
    assert "Source Version" not in order_fields


def test_procurement_purchase_order_table_rows_support_offset_pagination():
    """Protect the shared H1 pagination contract for H3 procurement tables."""

    for _index in range(3):
        create_purchase_order_for_test(
            item_id="item_vinda_tissue",
            location_code="A1",
        )

    first_response = client.post(
        "/procurement/purchase-orders/table-rows",
        json={"approval_status": "pending", "limit": 2, "offset": 0},
    )
    response = client.post(
        "/procurement/purchase-orders/table-rows",
        json={"approval_status": "pending", "limit": 2, "offset": 2},
    )

    assert first_response.status_code == 200
    assert response.status_code == 200
    first_body = first_response.json()
    body = response.json()
    assert first_body["ok"] is True
    assert body["ok"] is True
    assert body["count"] >= 1
    assert isinstance(body["has_more"], bool)
    if body["has_more"]:
        assert body["next_offset"] == 4
    else:
        assert body["next_offset"] is None
    first_ids = {item["purchase_order_id"] for item in first_body["items"]}
    second_ids = {item["purchase_order_id"] for item in body["items"]}
    assert first_ids.isdisjoint(second_ids)


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
    assert created_order["status"] == "unpaid"
    assert created_order["delivery_provider_id"] == "sf"
    assert created_order["delivery_provider_name"] == "顺丰"
    assert created_order["courier_phone"] == "13800000001"
    assert created_order["shipping_province"] == "广东省"
    assert created_order["shipping_city"] == "深圳市"
    assert created_order["selected_warehouse_id"] == "wh_sz_1"
    assert create.json()["items"][0]["status"] == "unpaid"

    paid = client.post(
        "/warehouse/orders/ORD-DELIVERY-1001/pay",
        json={"updated_by": "customer"},
    ).json()
    assert paid["order"]["status"] == "pending_fulfillment_review"
    assert paid["items"][0]["status"] == "pending_fulfillment_review"

    confirmed = client.post(
        "/warehouse/orders/ORD-DELIVERY-1001/fulfillment/confirm",
        json={"warehouse_id": "wh_sz_1", "delivery_provider_id": "yto", "updated_by": "warehouse-agent"},
    ).json()
    assert confirmed["order"]["status"] == "pending_shipment"
    assert confirmed["order"]["delivery_provider_id"] == "yto"
    assert confirmed["items"][0]["status"] == "pending_shipment"

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
    assert body["risk_level"] == "medium"
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
    assert not any(item["name"] == "Batch No" for item in body["fields"])
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
    assert first["batch_key"] == "wh_sz_1:A1:item_vinda_tissue"
    assert set(first["fields"]).issuperset(
        {
            "Warehouse",
            "Location",
            "Category",
            "Item Name",
            "Brand",
            "Spec",
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
    assert "Category ID" not in first["fields"]
    assert "Item ID" not in first["fields"]


def test_warehouse_inventory_table_rows_support_offset_pagination():
    """Protect the shared H1 pagination contract for H2 inventory snapshots."""

    first_page = client.post(
        "/warehouse/inventory/table-rows",
        json={"warehouse_id": "wh_sz_1", "limit": 1},
    )
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert first_body["ok"] is True
    assert first_body["count"] == 1
    assert first_body["has_more"] is True
    assert first_body["next_offset"] == 1

    second_page = client.post(
        "/warehouse/inventory/table-rows",
        json={"warehouse_id": "wh_sz_1", "limit": 1, "offset": 1},
    )
    assert second_page.status_code == 200
    second_body = second_page.json()
    assert second_body["ok"] is True
    assert second_body["count"] == 1
    assert second_body["items"][0]["batch_key"] != first_body["items"][0]["batch_key"]


def test_warehouse_stock_balance_table_schema_uses_select_statuses_and_date_fields():
    response = client.get("/warehouse/stock/balances/table-schema")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["schema_id"] == "warehouse_inventory_balances"
    fields_by_name = {item["name"]: item for item in body["fields"]}
    assert list(fields_by_name) == [
        "id",
        "warehouse_id",
        "location_code",
        "item_id",
        "production_date",
        "expiry_date",
        "quantity_on_hand",
        "reorder_threshold",
        "storage_status",
        "created_at",
        "updated_at",
    ]
    assert fields_by_name["id"]["source"] == "inventory_location_balances.id"
    assert fields_by_name["quantity_on_hand"]["type"] == "number"
    assert fields_by_name["storage_status"]["type"] == "single_select"
    assert fields_by_name["production_date"]["type"] == "date"
    assert fields_by_name["expiry_date"]["type"] == "date"
    assert fields_by_name["created_at"]["type"] == "date"
    assert fields_by_name["updated_at"]["type"] == "date"


def test_warehouse_stock_balance_table_rows_match_inventory_location_balance_columns():
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
    assert first["balance_id"] == first["fields"]["id"]
    assert first["balance_id"].isdigit() or first["balance_id"].startswith("fallback:")
    assert set(first["fields"]) == {
        "id",
        "warehouse_id",
        "location_code",
        "item_id",
        "production_date",
        "expiry_date",
        "quantity_on_hand",
        "reorder_threshold",
        "storage_status",
        "created_at",
        "updated_at",
    }
    assert first["fields"]["warehouse_id"] == "wh_sz_1"
    assert first["fields"]["item_id"]
    assert first["fields"]["quantity_on_hand"] >= 0
    assert first["fields"]["storage_status"] in {"available", "quality_hold"}
    assert first["fields"]["created_at"]
    assert first["fields"]["updated_at"]


def test_warehouse_stock_balance_table_rows_keep_batch_level_balance_rows():
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
        if item["fields"]["item_id"] == "item_vinda_tissue"
        and item["warehouse_id"] == "wh_sz_1"
        and item["location_code"] == "A1"
    ]
    assert len(vinda) >= 1
    assert sum(item["fields"]["quantity_on_hand"] for item in vinda) >= 136


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
        json={"item_id": "item_cola_zero"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item_id"] == "item_cola_zero"
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


def test_warehouse_order_fulfillment_confirmation_deducts_location_balances_and_records_movements():
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
    assert created["order"]["status"] == "unpaid"
    assert created["order"]["selected_warehouse_id"] == "wh_sz_1"
    assert all(line["status"] == "unpaid" for line in created["items"])
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

    paid_response = client.post("/warehouse/orders/ORD-CODEX-9001/pay", json={"updated_by": "customer"})
    assert paid_response.status_code == 200
    paid_before_review = paid_response.json()
    assert paid_before_review["order"]["status"] == "pending_fulfillment_review"
    assert all(line["status"] == "pending_fulfillment_review" for line in paid_before_review["items"])

    confirmed_response = client.post(
        "/warehouse/orders/ORD-CODEX-9001/fulfillment/confirm",
        json={"warehouse_id": "wh_sz_1", "delivery_provider_id": "jd", "updated_by": "warehouse-agent"},
    )
    assert confirmed_response.status_code == 200
    confirmed = confirmed_response.json()
    assert confirmed["order"]["status"] == "pending_shipment"
    assert confirmed["order"]["delivery_provider_id"] == "jd"
    assert confirmed["order"]["delivery_provider_name"] == "京东"
    vinda_lines = [line for line in confirmed["items"] if line["item_id"] == "item_vinda_tissue"]
    assert [line["location_code"] for line in vinda_lines] == ["A1"]
    assert all("batch_no" not in line for line in vinda_lines)
    assert [line["quantity"] for line in vinda_lines] == [20]
    balances = client.get(
        "/warehouse/stock/balances",
        params={"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1"},
    ).json()
    assert balances["total_quantity_on_hand"] == 116
    assert balances["total_quantity_available"] == 116

    inventory = client.get("/warehouse/inventory/item_vinda_tissue").json()
    assert inventory["total_quantity_on_hand"] == 116
    assert sum(int(batch["quantity_on_hand"]) for batch in inventory["batches"]) == 116

    assert all(line["status"] == "pending_shipment" for line in confirmed["items"])
    movements = [item for item in WAREHOUSE_INVENTORY_MOVEMENTS if item["order_id"] == "ORD-CODEX-9001"]
    assert any(
        item["item_id"] == "item_vinda_tissue"
        and item["location_code"] == "A1"
        and item["quantity_delta"] == -20
        for item in movements
    )


def test_warehouse_order_tool_normalizes_lowercase_order_id_for_fulfillment_confirmation():
    create_response = client.post(
        "/warehouse/orders",
        json={
            "order_id": "ORD-CODEX-LOWERCASE",
            "customer_id": "cus_100",
            "shipping_address": "广东省深圳市",
            "items": [{"item_id": "item_vinda_tissue", "quantity": 1}],
            "delivery_provider_id": "sf",
        },
    )
    assert create_response.status_code == 200

    paid_response = client.post("/warehouse/orders/ORD-CODEX-LOWERCASE/pay", json={"updated_by": "customer"})
    assert paid_response.status_code == 200

    confirmed_response = client.post(
        "/warehouse/order-tool",
        json={
            "action": "confirm_fulfillment",
            "order_id": "ord-codex-lowercase",
            "warehouse_id": "wh_sz_1",
            "delivery_provider_id": "jd",
            "updated_by": "warehouse-agent",
        },
    )

    assert confirmed_response.status_code == 200
    confirmed = confirmed_response.json()
    assert confirmed["order"]["order_id"] == "ORD-CODEX-LOWERCASE"
    assert confirmed["order"]["status"] == "pending_shipment"
    assert confirmed["order"]["delivery_provider_id"] == "jd"


def test_warehouse_order_tool_merges_nested_json_input_for_fulfillment_confirmation():
    create_response = client.post(
        "/warehouse/orders",
        json={
            "order_id": "ORD-CODEX-NESTED",
            "customer_id": "cus_100",
            "shipping_address": "广东省深圳市",
            "items": [{"item_id": "item_vinda_tissue", "quantity": 1}],
            "delivery_provider_id": "sf",
        },
    )
    assert create_response.status_code == 200

    paid_response = client.post("/warehouse/orders/ORD-CODEX-NESTED/pay", json={"updated_by": "customer"})
    assert paid_response.status_code == 200

    confirmed_response = client.post(
        "/warehouse/order-tool",
        json={
            "input": json.dumps(
                {
                    "order_id": "ord-codex-nested",
                    "warehouse_id": "wh_sz_1",
                    "action": "confirm_fulfillment",
                    "carrier": "jd",
                }
            )
        },
    )

    assert confirmed_response.status_code == 200
    confirmed = confirmed_response.json()
    assert confirmed["order"]["order_id"] == "ORD-CODEX-NESTED"
    assert confirmed["order"]["status"] == "pending_shipment"
    assert confirmed["order"]["delivery_provider_id"] == "jd"
    assert confirmed["order"]["delivery_provider_name"] == "京东"


def test_warehouse_order_payment_posts_fulfillment_review_notification(monkeypatch):
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

    create = client.post(
        "/warehouse/orders",
        json={
            "order_id": "ORD-CODEX-NOTIFY",
            "customer_id": "cus_100",
            "shipping_address": "广东省深圳市",
            "items": [{"item_id": "item_vinda_tissue", "quantity": 2}],
        },
    )

    assert create.status_code == 200
    assert create.json()["notification"]["status"] == "skipped"
    assert calls == []

    response = client.post("/warehouse/orders/ORD-CODEX-NOTIFY/pay", json={"updated_by": "customer"})

    assert response.status_code == 200
    body = response.json()
    assert body["order"]["status"] == "pending_fulfillment_review"
    assert body["notification"]["status"] == "sent"
    assert calls[0]["url"] == "http://feishu-adapter.local/warehouse/order-fulfillment-review/send"
    assert calls[0]["payload"]["chat_id"] == "oc_warehouse_ops"
    assert calls[0]["payload"]["order"]["order_id"] == "ORD-CODEX-NOTIFY"
    assert calls[0]["payload"]["candidates"][0]["warehouse_id"] == "wh_sz_1"
    assert {item["provider_id"] for item in calls[0]["payload"]["delivery_providers"]} >= {"sf", "jd", "yto"}


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
    client.post("/warehouse/orders/ORD-CODEX-9002/pay", json={"updated_by": "customer"})
    client.post(
        "/warehouse/orders/ORD-CODEX-9002/fulfillment/confirm",
        json={"warehouse_id": "wh_sz_1", "updated_by": "warehouse-agent"},
    )

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
    client.post("/warehouse/orders/ORD-CODEX-9003/pay", json={"updated_by": "customer"})
    client.post(
        "/warehouse/orders/ORD-CODEX-9003/fulfillment/confirm",
        json={"warehouse_id": "wh_sz_1", "updated_by": "warehouse-agent"},
    )
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
    assert create.json()["order"]["status"] == "unpaid"
    balances = client.get(
        "/warehouse/stock/balances",
        params={"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1"},
    ).json()
    assert balances["total_quantity_available"] == 136

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
    balances = client.get(
        "/warehouse/stock/balances",
        params={"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1"},
    ).json()
    assert balances["total_quantity_available"] == 136
