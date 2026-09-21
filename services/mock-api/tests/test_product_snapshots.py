"""Contract tests for the C1 read-only batch product snapshot endpoint."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.main import app
from app.routers import product_snapshots as snapshot_router
from app.store import FIXTURE_DIR
from app.warehouse_store import (
    WarehouseRepository,
    init_warehouse_schema,
    seed_warehouse_fixtures,
)


client = TestClient(app)


def test_batch_endpoint_preserves_order_deduplicates_and_reports_partial_errors(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def fake_load(_repository: object, item_ids: list[str]):
        calls.append(item_ids)
        return [
            {
                "item_id": "sku-b",
                "item_name": "Beta",
                "brand": "Brand B",
                "spec": "128GB",
                "category_id": "electronics",
                "price": 99.0,
                "stock": None,
                "stock_observed_at": None,
                "rating": None,
                "review_count": None,
                "rating_observed_at": None,
            },
            {
                "item_id": "sku-a",
                "item_name": "Alpha",
                "brand": "Brand A",
                "spec": "256GB",
                "category_id": "electronics",
                "price": 199.0,
                "stock": 5,
                "stock_observed_at": "2026-09-21T12:00:00Z",
                "rating": 4.8,
                "review_count": 20,
                "rating_observed_at": "2026-09-21T11:00:00Z",
            },
        ]

    monkeypatch.setattr(snapshot_router, "get_warehouse_repository", lambda: object())
    monkeypatch.setattr(snapshot_router, "load_product_snapshot_rows", fake_load)

    response = client.post(
        "/products/snapshots",
        json={"item_ids": ["sku-a", "missing", "sku-b", "sku-a"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["source_version"].startswith("mock-api-product-snapshot-v1:")
    assert [item["item_id"] for item in body["items"]] == [
        "sku-a",
        "missing",
        "sku-b",
    ]
    assert body["items"][1]["error"]["code"] == "item_not_found"
    assert body["items"][2]["facts"]["stock"] is None
    assert body["items"][2]["facts"]["rating"] is None
    captured_at = datetime.fromisoformat(body["captured_at"])
    estimated_delivery_at = datetime.fromisoformat(
        body["items"][0]["facts"]["delivery"]["estimated_delivery_at"]
    )
    assert estimated_delivery_at - captured_at == timedelta(days=1)
    assert calls == [["sku-a", "missing", "sku-b"]]


def test_batch_endpoint_rejects_client_supplied_business_facts() -> None:
    response = client.post(
        "/products/snapshots",
        json={"item_ids": ["sku-a"], "price": 0, "stock": 999},
    )

    assert response.status_code == 422


def test_batch_endpoint_fails_closed_without_authoritative_backend(monkeypatch) -> None:
    monkeypatch.setattr(snapshot_router, "get_warehouse_repository", lambda: None)

    response = client.post("/products/snapshots", json={"item_ids": ["sku-a"]})

    assert response.status_code == 503
    assert response.json()["error"] == "product_snapshot_backend_unavailable"


def test_set_based_loader_reads_catalog_stock_and_rating_in_one_result(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)

    rows = snapshot_router.load_product_snapshot_rows(
        repository,
        ["item_milk_pure", "item_wireless_earbuds", "missing"],
    )
    rows_by_id = {row["item_id"]: row for row in rows}

    assert set(rows_by_id) == {"item_milk_pure", "item_wireless_earbuds"}
    assert rows_by_id["item_milk_pure"]["stock"] is not None
    assert rows_by_id["item_milk_pure"]["rating"] is not None
    assert rows_by_id["item_milk_pure"]["review_count"] > 0
