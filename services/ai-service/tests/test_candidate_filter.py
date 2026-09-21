"""Unit coverage for C2 candidate recall and hard-filter invariants."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.routers.AImodel.candidate_service import (
    CandidateService,
    CandidatePolicy,
    ExclusionAction,
    RequiredSpecification,
    apply_hard_filters,
)
from app.routers.AImodel.product_snapshot import ProductSnapshotTransportError
from app.routers.AImodel.product_models import (
    DeliveryCapability,
    FactSource,
    FactStatus,
    Freshness,
    ProductFact,
    ProductSnapshot,
    ProductSnapshotEntry,
    ProductSnapshotItem,
    ProductSpecifications,
)
from app.routers.AImodel.shopping_goal import ShoppingGoal


NOW = datetime(2026, 9, 21, 12, tzinfo=UTC)
SOURCE = FactSource(
    provider="mock-api",
    endpoint="/products/snapshots",
    source_version="test-v1",
    captured_at=NOW,
)


def fact(value: object, *, freshness: Freshness | None = None) -> ProductFact:
    return ProductFact(
        status=FactStatus.KNOWN,
        value=value,
        source=SOURCE,
        freshness=freshness
        or Freshness.from_observation(
            observed_at=NOW,
            captured_at=NOW,
            max_age_seconds=60,
        ),
    )


def product(
    item_id: str = "sku-1",
    *,
    stock: int = 1,
    specifications: dict[str, str] | None = None,
) -> ProductSnapshotItem:
    return ProductSnapshotItem(
        item_id=item_id,
        name=fact("Phone"),
        category=fact("phone"),
        brand=fact("Acme"),
        current_price=fact(Decimal("499")),
        currency=fact("CNY"),
        stock=fact(stock),
        specifications=fact(
            ProductSpecifications.from_mapping(specifications or {"memory": "16GB"})
        ),
        rating=fact(Decimal("4.5")),
        review_count=fact(10),
        delivery=fact(
            DeliveryCapability(
                shipping_available=True,
                pickup_available=False,
                delivery_available=True,
                estimated_delivery_at=NOW,
            )
        ),
    )


def snapshot(*products: ProductSnapshotItem) -> ProductSnapshot:
    entries = tuple(
        ProductSnapshotEntry(item_id=item.item_id, status="ok", item=item)
        for item in products
    )
    return ProductSnapshot(
        snapshot_id="snapshot-1",
        turn_id="turn-1",
        source_version="test-v1",
        captured_at=NOW,
        requested_item_ids=tuple(item.item_id for item in products),
        entries=entries,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_candidates", 0),
        ("max_candidates", 101),
        ("max_search_query_length", 0),
        ("minimum_stock", -1),
        ("unknown_fact_action", ExclusionAction.RELAX_CONSTRAINT),
        ("stale_fact_action", ExclusionAction.CLARIFY),
    ],
)
def test_candidate_policy_rejects_unsafe_bounds_or_actions(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        CandidatePolicy.model_validate({field: value})


def test_default_policy_requires_one_available_unit() -> None:
    result = apply_hard_filters(
        ShoppingGoal(),
        snapshot(product(stock=0)),
    )

    assert result.eligible == ()
    assert result.excluded[0].reasons[0].code == "insufficient_stock"


def test_minimum_stock_zero_disables_implicit_stock_requirement() -> None:
    result = apply_hard_filters(
        ShoppingGoal(),
        snapshot(product(stock=0)),
        policy=CandidatePolicy(minimum_stock=0),
    )

    assert [item.item_id for item in result.eligible] == ["sku-1"]


def test_configured_specification_is_a_hard_filter() -> None:
    result = apply_hard_filters(
        ShoppingGoal(),
        snapshot(product(specifications={"memory": "8GB"})),
        policy=CandidatePolicy(
            required_specifications=(
                RequiredSpecification(attribute="memory", value="16GB"),
            )
        ),
    )

    assert result.eligible == ()
    assert result.excluded[0].reasons[0].code == "specification_mismatch"


def test_known_value_with_unknown_freshness_does_not_pass() -> None:
    value = product()
    payload = value.model_dump()
    payload["stock"] = fact(1, freshness=Freshness.unknown("timestamp_missing"))
    candidate = ProductSnapshotItem.model_validate(payload)

    result = apply_hard_filters(ShoppingGoal(), snapshot(candidate))

    assert result.eligible == ()
    assert result.excluded[0].reasons[0].code == "stock_freshness_unknown"
    assert result.excluded[0].reasons[0].action is ExclusionAction.REFRESH_FACT


def test_required_specification_attributes_are_unique_and_normalized() -> None:
    with pytest.raises(ValidationError):
        RequiredSpecification(attribute="Memory", value="16GB")
    with pytest.raises(ValidationError):
        CandidatePolicy(
            required_specifications=(
                RequiredSpecification(attribute="memory", value="16GB"),
                RequiredSpecification(attribute="memory", value="32GB"),
            )
        )


def test_goal_specification_cannot_override_configured_requirement() -> None:
    from app.routers.AImodel.shopping_goal import (
        Constraint,
        GoalEvidence,
        GoalField,
        GoalSourceType,
    )

    goal = ShoppingGoal(
        hard_constraints=(
            Constraint(
                field=GoalField.SPECIFICATION,
                attribute="memory",
                value="8GB",
                evidence=GoalEvidence(
                    source_type=GoalSourceType.USER_TURN,
                    source_turn=1,
                    quote="8GB",
                    confidence=1,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            ),
        )
    )
    result = apply_hard_filters(
        goal,
        snapshot(product(specifications={"memory": "8GB"})),
        policy=CandidatePolicy(
            required_specifications=(
                RequiredSpecification(attribute="memory", value="16GB"),
            )
        ),
    )

    assert result.eligible == ()
    assert [reason.code for reason in result.excluded[0].reasons] == [
        "specification_mismatch"
    ]


def test_total_snapshot_failure_is_recorded_per_recalled_item() -> None:
    class FailingSnapshotClient:
        def capture_for_turn(self, **_kwargs):
            raise ProductSnapshotTransportError("offline")

    service = CandidateService(
        "http://mock-api",
        snapshot_client=FailingSnapshotClient(),
        policy=CandidatePolicy(minimum_stock=0),
    )

    result = service.build_candidate_set(
        turn_id="turn-1",
        goal=ShoppingGoal(),
        explicit_item_ids=("sku-1", "sku-2"),
    )

    assert result.eligible == ()
    assert [failure.item_id for failure in result.failures] == ["sku-1", "sku-2"]
    assert {failure.code for failure in result.failures} == {"snapshot_unavailable"}
