"""Frozen acceptance contract for C1 immutable product fact snapshots."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "ai-service"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.routers.AImodel.agent_trace import AgentTraceContext  # noqa: E402
from app.routers.AImodel.product_models import (  # noqa: E402
    FactSource,
    FactStatus,
    Freshness,
    FreshnessState,
    ProductFact,
)
from app.routers.AImodel.product_snapshot import (  # noqa: E402
    ProductSnapshotClient,
    ProductSnapshotSealed,
    SnapshotFreshnessRules,
)


CASES = json.loads(
    (Path(__file__).with_name("product_snapshot_cases.json")).read_text(
        encoding="utf-8"
    )
)
NOW = datetime.fromisoformat(CASES["captured_at"].replace("Z", "+00:00"))


def build_client(
    payload: dict,
) -> tuple[ProductSnapshotClient, list[dict[str, object]]]:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        assert request.method == "POST"
        assert request.url.path == "/products/snapshots"
        return httpx.Response(200, json=payload)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    return (
        ProductSnapshotClient(
            "http://mock-api",
            http_client=http_client,
            clock=lambda: NOW,
            id_factory=lambda: "snapshot-turn-1",
            freshness_rules=SnapshotFreshnessRules.model_validate(
                CASES["freshness_rules"]
            ),
        ),
        requests,
    )


def test_product_fact_requires_explicit_unknown_and_timezone_aware_freshness() -> (
    None
):
    source = FactSource(
        provider="mock-api",
        endpoint="/products/snapshots",
        source_version=CASES["source_version"],
        captured_at=NOW,
    )
    freshness = Freshness.from_observation(
        observed_at=NOW - timedelta(seconds=301),
        captured_at=NOW,
        max_age_seconds=300,
    )
    fact = ProductFact[Decimal](
        status=FactStatus.KNOWN,
        value=Decimal("4999.00"),
        source=source,
        freshness=freshness,
    )

    assert fact.freshness.state is FreshnessState.STALE
    assert fact.value == Decimal("4999.00")
    with pytest.raises(ValidationError):
        ProductFact[Decimal](
            status=FactStatus.UNKNOWN,
            value=Decimal("0"),
            source=source,
            freshness=Freshness.unknown(),
        )
    with pytest.raises(ValidationError):
        Freshness.from_observation(
            observed_at=datetime(2026, 9, 21, 12, 0),
            captured_at=NOW,
            max_age_seconds=300,
        )


def test_batch_capture_is_ordered_partial_and_server_authoritative() -> None:
    client, requests = build_client(CASES["api_response"])
    trace = AgentTraceContext.start(user_query="比较三款手机", conversation_id=7)

    snapshot = client.capture_for_turn(
        turn_id=trace.trace_id,
        item_ids=CASES["request_item_ids"],
        trace_context=trace,
    )

    assert snapshot.snapshot_id == "snapshot-turn-1"
    assert snapshot.source_version == CASES["source_version"]
    assert snapshot.captured_at == NOW
    assert snapshot.requested_item_ids == ("sku-b", "missing", "sku-a")
    assert [entry.item_id for entry in snapshot.entries] == [
        "sku-b",
        "missing",
        "sku-a",
    ]
    assert snapshot.entries[1].error.code == "item_not_found"
    assert snapshot.items_by_id["sku-a"].current_price.value == Decimal("4999.00")
    assert snapshot.items_by_id["sku-b"].stock.value == 0
    assert snapshot.items_by_id["sku-b"].rating.status is FactStatus.UNKNOWN
    assert snapshot.items_by_id["sku-b"].rating.value is None
    assert snapshot.items_by_id["sku-b"].current_price.freshness.state is FreshnessState.STALE
    assert snapshot.items_by_id["sku-b"].stock.freshness.state is FreshnessState.FRESH
    assert snapshot.items_by_id["sku-b"].delivery.freshness.state is FreshnessState.STALE
    assert requests == [{"item_ids": ["sku-b", "missing", "sku-a"]}]

    snapshot_event = next(event for event in trace.events if event.stage == "product_snapshot")
    assert snapshot_event.related_ids["snapshot_id"] == snapshot.snapshot_id
    assert snapshot_event.summary["source_version"] == snapshot.source_version
    assert snapshot_event.summary["captured_at"] == CASES["captured_at"]


def test_turn_snapshot_is_sealed_and_reuses_the_same_authoritative_object() -> None:
    client, requests = build_client(CASES["api_response"])

    first = client.capture_for_turn(
        turn_id="turn-1",
        item_ids=CASES["request_item_ids"],
    )
    replay = client.capture_for_turn(
        turn_id="turn-1",
        item_ids=["sku-a", "sku-b", "missing"],
    )

    assert replay is first
    assert len(requests) == 1
    with pytest.raises(ProductSnapshotSealed):
        client.capture_for_turn(
            turn_id="turn-1",
            item_ids=["sku-a", "new-sku"],
        )


def test_api_response_order_does_not_change_facts_by_item_id() -> None:
    client_a, _ = build_client(CASES["api_response"])
    reversed_payload = {
        **CASES["api_response"],
        "items": list(reversed(CASES["api_response"]["items"])),
    }
    client_b, _ = build_client(reversed_payload)

    first = client_a.capture_for_turn(
        turn_id="turn-a",
        item_ids=CASES["request_item_ids"],
    )
    second = client_b.capture_for_turn(
        turn_id="turn-b",
        item_ids=CASES["request_item_ids"],
    )

    first_items = {
        item_id: item.model_dump(mode="json")
        for item_id, item in first.items_by_id.items()
    }
    second_items = {
        item_id: item.model_dump(mode="json")
        for item_id, item in second.items_by_id.items()
    }
    assert first_items == second_items
