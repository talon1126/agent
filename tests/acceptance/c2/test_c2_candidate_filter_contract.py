"""Frozen acceptance contract for C2 deterministic recall and hard filtering."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "ai-service"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.routers.AImodel.candidate_service import (  # noqa: E402
    CandidatePolicy,
    CandidateService,
    CandidateSetStatus,
    CandidateSource,
    ExclusionAction,
    apply_hard_filters,
)
from app.routers.AImodel.product_models import (  # noqa: E402
    DeliveryCapability,
    FactSource,
    FactStatus,
    Freshness,
    ProductFact,
    ProductSnapshot,
    ProductSnapshotEntry,
    ProductSnapshotError,
    ProductSnapshotItem,
    ProductSpecifications,
)
from app.routers.AImodel.shopping_goal import (  # noqa: E402
    Constraint,
    Exclusion,
    GoalEvidence,
    GoalField,
    GoalSourceType,
    ShoppingGoal,
)


CASES = json.loads(
    (Path(__file__).with_name("candidate_filter_cases.json")).read_text(
        encoding="utf-8"
    )
)
NOW = datetime.fromisoformat(CASES["captured_at"].replace("Z", "+00:00"))
DEADLINE = datetime.fromisoformat(CASES["delivery_deadline"].replace("Z", "+00:00"))
SOURCE = FactSource(
    provider="mock-api",
    endpoint="/products/snapshots",
    source_version="mock-api-product-snapshot-v1:test",
    captured_at=NOW,
)


def evidence(quote: str = "硬条件") -> GoalEvidence:
    return GoalEvidence(
        source_type=GoalSourceType.USER_TURN,
        source_turn=1,
        quote=quote,
        confidence=1,
        created_at=NOW,
        updated_at=NOW,
    )


def constraint(
    field: GoalField,
    value: object,
    *,
    attribute: str | None = None,
) -> Constraint:
    return Constraint(
        field=field,
        value=value,
        attribute=attribute,
        evidence=evidence(),
    )


def exclusion(field: GoalField, value: object) -> Exclusion:
    return Exclusion(field=field, value=value, evidence=evidence("排除"))


def known(value: object, *, stale: bool = False) -> ProductFact:
    observed_at = NOW - timedelta(days=2) if stale else NOW
    return ProductFact(
        status=FactStatus.KNOWN,
        value=value,
        source=SOURCE,
        freshness=Freshness.from_observation(
            observed_at=observed_at,
            captured_at=NOW,
            max_age_seconds=300,
        ),
    )


def unknown() -> ProductFact:
    return ProductFact(
        status=FactStatus.UNKNOWN,
        value=None,
        source=SOURCE,
        freshness=Freshness.unknown("fixture_missing"),
    )


def item(
    item_id: str,
    *,
    price: Decimal | None = Decimal("500"),
    brand: str | None = "Acme",
    category: str | None = "phone",
    stock: int | None = 2,
    delivery_at: datetime | None = DEADLINE,
    specifications: dict[str, str] | None = None,
    stale_price: bool = False,
) -> ProductSnapshotItem:
    specifications = (
        {"memory": "16GB", "storage": "512GB"}
        if specifications is None
        else specifications
    )
    delivery = (
        DeliveryCapability(
            shipping_available=True,
            pickup_available=False,
            delivery_available=True,
            estimated_delivery_at=delivery_at,
        )
        if delivery_at is not None
        else None
    )
    return ProductSnapshotItem(
        item_id=item_id,
        name=known(item_id),
        category=known(category) if category is not None else unknown(),
        brand=known(brand) if brand is not None else unknown(),
        current_price=(
            known(price, stale=stale_price) if price is not None else unknown()
        ),
        currency=known("CNY"),
        stock=known(stock) if stock is not None else unknown(),
        specifications=(
            known(ProductSpecifications.from_mapping(specifications))
            if specifications
            else unknown()
        ),
        rating=known(Decimal("4.8")),
        review_count=known(100),
        delivery=known(delivery) if delivery is not None else unknown(),
    )


def snapshot(
    *items: ProductSnapshotItem, errors: tuple[str, ...] = ()
) -> ProductSnapshot:
    entries = [
        ProductSnapshotEntry(item_id=value.item_id, status="ok", item=value)
        for value in items
    ]
    entries.extend(
        ProductSnapshotEntry(
            item_id=item_id,
            status="error",
            error=ProductSnapshotError(code="item_not_found", message="missing"),
        )
        for item_id in errors
    )
    return ProductSnapshot(
        snapshot_id="snapshot-c2",
        turn_id="turn-c2",
        source_version=SOURCE.source_version,
        captured_at=NOW,
        requested_item_ids=tuple(entry.item_id for entry in entries),
        entries=tuple(entries),
    )


def policy(**updates: object) -> CandidatePolicy:
    return CandidatePolicy.model_validate({**CASES["policy"], **updates})


def reason_codes(result) -> list[str]:
    return [
        reason.code for candidate in result.excluded for reason in candidate.reasons
    ]


@pytest.mark.parametrize(
    ("goal", "candidate", "kept", "reason_code"),
    [
        (
            ShoppingGoal(hard_constraints=(constraint(GoalField.BUDGET_MIN, 500),)),
            item("budget-min-equal", price=Decimal("500")),
            True,
            None,
        ),
        (
            ShoppingGoal(hard_constraints=(constraint(GoalField.BUDGET_MIN, 500),)),
            item("budget-min-out", price=Decimal("499.99")),
            False,
            "budget_below_minimum",
        ),
        (
            ShoppingGoal(hard_constraints=(constraint(GoalField.BUDGET_MAX, 500),)),
            item("budget-max-equal", price=Decimal("500")),
            True,
            None,
        ),
        (
            ShoppingGoal(hard_constraints=(constraint(GoalField.BUDGET_MAX, 500),)),
            item("budget-max-out", price=Decimal("500.01")),
            False,
            "budget_above_maximum",
        ),
        (
            ShoppingGoal(hard_constraints=(constraint(GoalField.BRAND, "ACME"),)),
            item("brand-equal", brand="Acme"),
            True,
            None,
        ),
        (
            ShoppingGoal(hard_constraints=(constraint(GoalField.BRAND, "Acme"),)),
            item("brand-out", brand="Other"),
            False,
            "brand_not_included",
        ),
        (
            ShoppingGoal(exclusions=(exclusion(GoalField.BRAND, "Acme"),)),
            item("brand-excluded", brand="ACME"),
            False,
            "brand_excluded",
        ),
        (
            ShoppingGoal(exclusions=(exclusion(GoalField.BRAND, "Acme"),)),
            item("brand-not-excluded", brand="Other"),
            True,
            None,
        ),
        (
            ShoppingGoal(hard_constraints=(constraint(GoalField.CATEGORY, "PHONE"),)),
            item("category-equal", category="phone"),
            True,
            None,
        ),
        (
            ShoppingGoal(hard_constraints=(constraint(GoalField.CATEGORY, "phone"),)),
            item("category-out", category="tablet"),
            False,
            "category_mismatch",
        ),
        (
            ShoppingGoal(hard_constraints=(constraint(GoalField.QUANTITY, 2),)),
            item("stock-equal", stock=2),
            True,
            None,
        ),
        (
            ShoppingGoal(hard_constraints=(constraint(GoalField.QUANTITY, 2),)),
            item("stock-out", stock=1),
            False,
            "insufficient_stock",
        ),
        (
            ShoppingGoal(
                hard_constraints=(constraint(GoalField.DELIVERY_DEADLINE, DEADLINE),)
            ),
            item("delivery-equal", delivery_at=DEADLINE),
            True,
            None,
        ),
        (
            ShoppingGoal(
                hard_constraints=(constraint(GoalField.DELIVERY_DEADLINE, DEADLINE),)
            ),
            item("delivery-out", delivery_at=DEADLINE + timedelta(seconds=1)),
            False,
            "delivery_after_deadline",
        ),
        (
            ShoppingGoal(
                hard_constraints=(
                    constraint(
                        GoalField.SPECIFICATION,
                        "16gb",
                        attribute="Memory",
                    ),
                )
            ),
            item("spec-equal", specifications={"memory": "16GB"}),
            True,
            None,
        ),
        (
            ShoppingGoal(
                hard_constraints=(
                    constraint(
                        GoalField.SPECIFICATION,
                        "16GB",
                        attribute="memory",
                    ),
                )
            ),
            item("spec-out", specifications={"memory": "8GB"}),
            False,
            "specification_mismatch",
        ),
    ],
)
def test_hard_filter_boundaries_are_inclusive_and_deterministic(
    goal: ShoppingGoal,
    candidate: ProductSnapshotItem,
    kept: bool,
    reason_code: str | None,
) -> None:
    first = apply_hard_filters(goal, snapshot(candidate), policy=policy())
    second = apply_hard_filters(goal, snapshot(candidate), policy=policy())

    assert first == second
    assert [entry.item_id for entry in first.eligible] == (
        [candidate.item_id] if kept else []
    )
    assert reason_codes(first) == ([] if reason_code is None else [reason_code])
    if reason_code is not None:
        reason = first.excluded[0].reasons[0]
        assert reason.field
        assert reason.expected is not None
        assert reason.actual is not None


@pytest.mark.parametrize(
    ("field", "candidate", "expected_code"),
    [
        (GoalField.BUDGET_MAX, item("unknown-price", price=None), "price_unknown"),
        (GoalField.BRAND, item("unknown-brand", brand=None), "brand_unknown"),
        (
            GoalField.CATEGORY,
            item("unknown-category", category=None),
            "category_unknown",
        ),
        (GoalField.QUANTITY, item("unknown-stock", stock=None), "stock_unknown"),
        (
            GoalField.DELIVERY_DEADLINE,
            item("unknown-delivery", delivery_at=None),
            "delivery_unknown",
        ),
        (
            GoalField.SPECIFICATION,
            item("unknown-spec", specifications={}),
            "specification_unknown",
        ),
    ],
)
def test_unknown_hard_facts_never_pass_and_request_clarification(
    field: GoalField,
    candidate: ProductSnapshotItem,
    expected_code: str,
) -> None:
    value: object = {
        GoalField.BUDGET_MAX: 500,
        GoalField.BRAND: "Acme",
        GoalField.CATEGORY: "phone",
        GoalField.QUANTITY: 1,
        GoalField.DELIVERY_DEADLINE: DEADLINE,
        GoalField.SPECIFICATION: "16GB",
    }[field]
    goal = ShoppingGoal(
        hard_constraints=(
            constraint(
                field,
                value,
                attribute="memory" if field is GoalField.SPECIFICATION else None,
            ),
        )
    )

    result = apply_hard_filters(goal, snapshot(candidate), policy=policy())

    assert result.eligible == ()
    assert reason_codes(result) == [expected_code]
    assert result.excluded[0].reasons[0].action is ExclusionAction.CLARIFY


def test_stale_hard_fact_is_not_treated_as_current() -> None:
    goal = ShoppingGoal(hard_constraints=(constraint(GoalField.BUDGET_MAX, 500),))
    result = apply_hard_filters(
        goal,
        snapshot(item("stale-price", stale_price=True)),
        policy=policy(),
    )

    assert result.eligible == ()
    assert reason_codes(result) == ["price_stale"]
    assert result.excluded[0].reasons[0].action is ExclusionAction.REFRESH_FACT


class FakeSnapshotClient:
    def __init__(self, *, failed_item_ids: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.failed_item_ids = failed_item_ids

    def capture_for_turn(self, *, turn_id: str, item_ids: list[str]):
        self.calls.append((turn_id, tuple(item_ids)))
        ok_items = [
            item(
                item_id,
                price=Decimal("800")
                if item_id == "page-over-budget"
                else Decimal("400"),
                category="tablet" if item_id == "wrong-category" else "phone",
            )
            for item_id in item_ids
            if item_id not in self.failed_item_ids
        ]
        return snapshot(*ok_items, errors=self.failed_item_ids)


def test_candidate_service_bounds_query_merges_sources_and_filters_every_source() -> (
    None
):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "items": [
                    {"item_id": "page-over-budget"},
                    {"item_id": "search-a"},
                    {"item_id": "search-b"},
                    {"item_id": "search-over-limit"},
                ],
            },
        )

    snapshots = FakeSnapshotClient()
    service = CandidateService(
        "http://mock-api",
        snapshot_client=snapshots,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        policy=policy(),
    )
    goal = ShoppingGoal(hard_constraints=(constraint(GoalField.BUDGET_MAX, 500),))

    result = service.build_candidate_set(
        turn_id="turn-c2",
        goal=goal,
        keywords=("wireless", "x" * 100),
        page_search_query="headphones for travel",
        page_candidate_ids=("page-over-budget",),
        explicit_item_ids=("explicit",),
    )

    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/search"
    assert (
        len(requests[0].url.params["q"]) <= CASES["policy"]["max_search_query_length"]
    )
    assert [entry.item_id for entry in result.recalled] == [
        "explicit",
        "page-over-budget",
        "search-a",
        "search-b",
    ]
    assert result.recalled[1].sources == (
        CandidateSource.PAGE,
        CandidateSource.SEARCH,
    )
    assert snapshots.calls == [
        (
            "turn-c2",
            ("explicit", "page-over-budget", "search-a", "search-b"),
        )
    ]
    assert [entry.item_id for entry in result.eligible] == [
        "explicit",
        "search-a",
        "search-b",
    ]
    assert [entry.item_id for entry in result.excluded] == ["page-over-budget"]
    assert result.excluded[0].reasons[0].code == "budget_above_maximum"


def test_no_candidate_separates_retrieval_failures_and_suggests_without_relaxing() -> (
    None
):
    snapshots = FakeSnapshotClient(failed_item_ids=("missing",))
    service = CandidateService(
        "http://mock-api",
        snapshot_client=snapshots,
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(503, json={"ok": False})
            )
        ),
        policy=policy(max_candidates=5),
    )
    goal = ShoppingGoal(hard_constraints=(constraint(GoalField.CATEGORY, "phone"),))

    result = service.build_candidate_set(
        turn_id="turn-c2",
        goal=goal,
        page_candidate_ids=("wrong-category", "missing"),
    )

    assert result.status is CandidateSetStatus.NO_CANDIDATE
    assert result.eligible == ()
    assert [entry.item_id for entry in result.excluded] == ["wrong-category"]
    assert result.excluded[0].reasons[0].code == "category_mismatch"
    assert [(failure.stage, failure.item_id) for failure in result.failures] == [
        ("search", None),
        ("snapshot", "missing"),
    ]
    assert result.reason_counts[0].code == "category_mismatch"
    assert result.reason_counts[0].count == 1
    assert result.suggestions[0].action is ExclusionAction.RELAX_CONSTRAINT
    assert result.suggestions[0].field == "category"
    assert goal.hard_constraints[0].value == "phone"
