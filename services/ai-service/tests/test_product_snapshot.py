"""Unit tests for C1 immutable, turn-scoped product snapshots."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from app.routers.AImodel.product_models import (
    FactStatus,
    ProductSnapshot,
    ProductSpecifications,
)
from app.routers.AImodel.product_snapshot import (
    ProductSnapshotClient,
    ProductSnapshotTransportError,
)


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def response_payload(*, items: list[dict] | None = None) -> dict:
    return {
        "ok": True,
        "source_version": "mock-api-product-snapshot-v1:test",
        "captured_at": NOW.isoformat(),
        "items": items
        if items is not None
        else [
            {
                "item_id": "sku-1",
                "status": "ok",
                "facts": {
                    "name": "Phone",
                    "category": "electronics",
                    "brand": "Brand",
                    "current_price": "99.00",
                    "currency": "CNY",
                    "stock": None,
                    "specifications": {"storage": "128GB", "memory": "8GB"},
                    "rating": None,
                    "review_count": None,
                    "delivery": {
                        "shipping_available": True,
                        "pickup_available": False,
                        "delivery_available": True,
                    },
                    "observed_at": {
                        "catalog": NOW.isoformat(),
                        "price": NOW.isoformat(),
                        "stock": None,
                        "rating": None,
                        "delivery": NOW.isoformat(),
                    },
                },
            }
        ],
    }


def build_client(
    payload: dict, calls: list[int] | None = None
) -> ProductSnapshotClient:
    def handler(_request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(1)
        return httpx.Response(200, json=payload)

    return ProductSnapshotClient(
        "http://mock-api",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: NOW,
        id_factory=lambda: "snapshot-1",
    )


def test_snapshot_round_trip_preserves_unknowns_and_sorted_specifications() -> None:
    snapshot = build_client(response_payload()).capture_for_turn(
        turn_id="turn-1",
        item_ids=["sku-1"],
    )
    restored = ProductSnapshot.model_validate(snapshot.model_dump(mode="json"))
    item = restored.items_by_id["sku-1"]

    assert restored == snapshot
    assert item.stock.status is FactStatus.UNKNOWN
    assert item.stock.value is None
    assert [spec.key for spec in item.specifications.value.values] == [
        "memory",
        "storage",
    ]
    with pytest.raises(ValidationError):
        item.specifications.value.values = ()


def test_missing_batch_item_becomes_explicit_per_item_error() -> None:
    snapshot = build_client(response_payload(items=[])).capture_for_turn(
        turn_id="turn-1",
        item_ids=["missing"],
    )

    assert snapshot.entries[0].status == "error"
    assert snapshot.entries[0].error.code == "missing_result"


def test_snapshot_can_only_be_consumed_by_its_registered_id() -> None:
    client = build_client(response_payload())
    snapshot = client.capture_for_turn(turn_id="turn-1", item_ids=["sku-1"])

    assert client.get_snapshot(snapshot.snapshot_id) is snapshot
    with pytest.raises(KeyError):
        client.get_snapshot("missing-snapshot")


def test_concurrent_same_turn_capture_uses_one_batch_request() -> None:
    calls: list[int] = []
    calls_lock = threading.Lock()

    def handler(_request: httpx.Request) -> httpx.Response:
        with calls_lock:
            calls.append(1)
        return httpx.Response(200, json=response_payload())

    client = ProductSnapshotClient(
        "http://mock-api",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: NOW,
        id_factory=lambda: "snapshot-1",
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        snapshots = list(
            executor.map(
                lambda _: client.capture_for_turn(
                    turn_id="turn-1",
                    item_ids=["sku-1"],
                ),
                range(4),
            )
        )

    assert all(snapshot is snapshots[0] for snapshot in snapshots)
    assert len(calls) == 1


def test_http_or_schema_failure_never_returns_partial_unvalidated_snapshot() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"ok": True, "items": []})
    )
    client = ProductSnapshotClient(
        "http://mock-api",
        http_client=httpx.Client(transport=transport),
        clock=lambda: NOW,
    )

    with pytest.raises(ProductSnapshotTransportError):
        client.capture_for_turn(turn_id="turn-1", item_ids=["sku-1"])


def test_product_specifications_reject_duplicate_or_unsorted_keys() -> None:
    with pytest.raises(ValidationError):
        ProductSpecifications.model_validate(
            {
                "values": [
                    {"key": "storage", "value": "128GB"},
                    {"key": "memory", "value": "8GB"},
                ]
            }
        )
