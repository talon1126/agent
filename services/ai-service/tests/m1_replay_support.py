"""Frozen catalog transport shared by the M1 replay and focused regressions."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[3]
ITEMS = {
    item["item_id"]: item
    for item in json.loads((ROOT / "fixtures/data/items.json").read_text("utf-8"))
}
INVENTORY = json.loads(
    (ROOT / "fixtures/data/inventory_location_balances.json").read_text("utf-8")
)
STOCK_BY_ITEM: dict[str, int] = {}
for balance in INVENTORY:
    if balance["storage_status"] == "available":
        item_id = balance["item_id"]
        STOCK_BY_ITEM[item_id] = (
            STOCK_BY_ITEM.get(item_id, 0) + balance["quantity_on_hand"]
        )


def _snapshot_item(
    item: dict[str, Any], observed_at: datetime, *, stock: int | None = 20,
    currency: str = "CNY",
) -> dict[str, Any]:
    timestamp = observed_at.isoformat()
    return {
        "item_id": item["item_id"],
        "status": "ok",
        "facts": {
            "name": item["item_name"],
            "category": item["category_id"],
            "brand": item["brand"],
            "current_price": str(item["price"]),
            "currency": currency,
            "stock": stock,
            "specifications": {"summary": item["spec"]},
            "rating": "4.5",
            "review_count": 0,
            "delivery": {
                "shipping_available": True,
                "pickup_available": True,
                "delivery_available": True,
                "estimated_delivery_at": (observed_at + timedelta(days=1)).isoformat(),
            },
            "observed_at": {
                "catalog": timestamp,
                "price": timestamp,
                "stock": timestamp if stock is not None else None,
                "rating": timestamp,
                "delivery": timestamp,
            },
        },
    }


def product_client(
    calls: list[str],
    *,
    reviews_by_item: dict[str, list[dict[str, Any]]] | None = None,
    use_fixture_inventory: bool = False,
    search_item_ids: set[str] | None = None,
    snapshot_currency: str = "CNY",
    failure_paths: set[str] | None = None,
    search_results_by_query: dict[str, list[str]] | None = None,
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path in (failure_paths or set()):
            return httpx.Response(503, json={"ok": False, "error": "fixture_unavailable"})
        observed_at = datetime.now(UTC)
        if request.url.path == "/search":
            query = request.url.params.get("q", "")
            matched_ids = (
                set(search_results_by_query[query])
                if search_results_by_query is not None
                and query in search_results_by_query
                else search_item_ids
            )
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "items": [
                        {
                            "item_id": item["item_id"],
                            "item_name": item["item_name"],
                        }
                        for item in ITEMS.values()
                        if matched_ids is None or item["item_id"] in matched_ids
                    ],
                },
            )
        if request.url.path == "/products/snapshots":
            requested = json.loads(request.content)["item_ids"]
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "source_version": "m1-replay-v2",
                    "captured_at": observed_at.isoformat(),
                    "items": [
                        _snapshot_item(
                            ITEMS[item_id],
                            observed_at,
                            stock=(
                                STOCK_BY_ITEM.get(item_id)
                                if use_fixture_inventory
                                else 20
                            ),
                            currency=snapshot_currency,
                        )
                        for item_id in requested
                    ],
                },
            )
        if request.url.path == "/items/reviews/batch":
            requested = json.loads(request.content)["item_ids"]
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "source_version": "m1-replay-v2",
                    "captured_at": observed_at.isoformat(),
                    "items": [
                        {
                            "item_id": item_id,
                            "status": "ok",
                            "summary": {
                                "average_rating": (
                                    sum(
                                        review["rating"]
                                        for review in (reviews_by_item or {}).get(
                                            item_id, []
                                        )
                                    )
                                    / len((reviews_by_item or {}).get(item_id, []))
                                    if (reviews_by_item or {}).get(item_id)
                                    else 0
                                ),
                                "review_count": len(
                                    (reviews_by_item or {}).get(item_id, [])
                                ),
                            },
                            "reviews": (reviews_by_item or {}).get(item_id, []),
                        }
                        for item_id in requested
                    ],
                },
            )
        return httpx.Response(404, json={"ok": False})

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://mock-api",
    )
