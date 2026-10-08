"""Frozen acceptance contract for C4 deterministic explainable ranking."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "ai-service"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.routers.AImodel.agent_trace import (  # noqa: E402
    AgentTraceContext,
    AgentTraceEventType,
)
from app.routers.AImodel.candidate_service import (  # noqa: E402
    CandidateReference,
    CandidateSet,
    CandidateSetStatus,
    CandidateSource,
    ExclusionAction,
    ExclusionReason,
    FilteredCandidate,
)
from app.routers.AImodel.feature_normalizer import (  # noqa: E402
    FeatureMissingPolicy,
    FeatureNormalizer,
    FeatureProfileRegistry,
    load_feature_profiles,
)
from app.routers.AImodel.product_models import (  # noqa: E402
    DeliveryCapability,
    FactSource,
    FactStatus,
    Freshness,
    ProductFact,
    ProductSnapshotItem,
    ProductSpecifications,
)
from app.routers.AImodel.ranking import (  # noqa: E402
    RankingInputError,
    RankingPolicy,
    ScoreComponentStatus,
    ProductRanker,
    load_ranking_policy,
)
from app.routers.AImodel.shopping_goal import (  # noqa: E402
    GoalEvidence,
    GoalField,
    GoalSourceType,
    Preference,
    ShoppingGoal,
)


NOW = datetime(2026, 9, 21, 12, tzinfo=UTC)
SOURCE = FactSource(
    provider="mock-api",
    endpoint="/products/snapshots",
    source_version="ranking-contract-v1",
    captured_at=NOW,
)
REGISTRY = load_feature_profiles()
NORMALIZER = FeatureNormalizer(REGISTRY)


def known(value: object) -> ProductFact:
    return ProductFact(
        status=FactStatus.KNOWN,
        value=value,
        source=SOURCE,
        freshness=Freshness.from_observation(
            observed_at=NOW,
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


def product(
    item_id: str,
    *,
    name: str | None = None,
    category: str = "electronics",
    brand: str = "Talon",
    price: str = "500",
    rating: str | None = "4.5",
    review_count: int | None = 100,
    delivery_days: int | None = 1,
    specifications: dict[str, str] | None = None,
) -> ProductSnapshotItem:
    delivery = (
        DeliveryCapability(
            shipping_available=True,
            pickup_available=False,
            delivery_available=True,
            estimated_delivery_at=NOW + timedelta(days=delivery_days),
        )
        if delivery_days is not None
        else None
    )
    return ProductSnapshotItem(
        item_id=item_id,
        name=known(name or item_id),
        category=known(category),
        brand=known(brand),
        current_price=known(Decimal(price)),
        currency=known("CNY"),
        stock=known(10),
        specifications=(
            known(ProductSpecifications.from_mapping(specifications))
            if specifications is not None
            else unknown()
        ),
        rating=known(Decimal(rating)) if rating is not None else unknown(),
        review_count=known(review_count) if review_count is not None else unknown(),
        delivery=known(delivery) if delivery is not None else unknown(),
    )


def candidate_set(
    items: list[ProductSnapshotItem],
    *,
    excluded: tuple[FilteredCandidate, ...] = (),
) -> CandidateSet:
    eligible = tuple(
        FilteredCandidate(
            item_id=item.item_id,
            sources=(CandidateSource.SEARCH,),
            snapshot_id="snapshot-c4",
        )
        for item in items
    )
    recalled_ids = [item.item_id for item in items]
    recalled_ids.extend(item.item_id for item in excluded)
    return CandidateSet(
        status=CandidateSetStatus.READY,
        snapshot_id="snapshot-c4",
        recalled=tuple(
            CandidateReference(
                item_id=item_id,
                sources=(CandidateSource.SEARCH,),
            )
            for item_id in recalled_ids
        ),
        eligible=eligible,
        excluded=excluded,
    )


def rank(
    items: list[ProductSnapshotItem],
    *,
    goal: ShoppingGoal | None = None,
    policy: RankingPolicy | None = None,
    trace: AgentTraceContext | None = None,
    excluded: tuple[FilteredCandidate, ...] = (),
):
    normalized = {item.item_id: NORMALIZER.normalize(item) for item in items}
    return ProductRanker(
        policy or load_ranking_policy(REGISTRY),
        REGISTRY,
    ).rank(
        candidate_set=candidate_set(items, excluded=excluded),
        products={item.item_id: item for item in items},
        normalized_features=normalized,
        goal=goal or ShoppingGoal(),
        trace_context=trace,
    )


def policy_variant(
    *,
    version: str,
    component: str,
    feature_weights: dict[str, str] | None = None,
) -> RankingPolicy:
    payload = load_ranking_policy(REGISTRY).model_dump(mode="json")
    payload["policy_version"] = version
    payload["component_weights"] = {
        key: "1" if key == component else "0" for key in payload["component_weights"]
    }
    if feature_weights is not None:
        payload["category_feature_weights"]["electronics"] = feature_weights
    return RankingPolicy.from_mapping(payload, profile_registry=REGISTRY)


def brand_preference(value: str) -> Preference:
    return Preference(
        field=GoalField.BRAND,
        value=value,
        evidence=GoalEvidence(
            source_type=GoalSourceType.USER_TURN,
            source_turn=1,
            quote=f"偏好 {value}",
            confidence=1,
            created_at=NOW,
            updated_at=NOW,
        ),
    )


def specification_preference(attribute: str, value: str) -> Preference:
    return Preference(
        field=GoalField.SPECIFICATION,
        attribute=attribute,
        value=value,
        evidence=GoalEvidence(
            source_type=GoalSourceType.USER_TURN,
            source_turn=1,
            quote=f"偏好 {attribute} {value}",
            confidence=1,
            created_at=NOW,
            updated_at=NOW,
        ),
    )


def test_fixed_input_is_stable_and_every_total_is_recomputable() -> None:
    items = [
        product(
            "phone-b",
            brand="Alpha",
            price="450",
            rating="4.2",
            review_count=200,
            specifications={"memory": "16 GB", "storage": "256 GB"},
        ),
        product(
            "phone-a",
            brand="Alpha",
            price="500",
            rating="4.7",
            review_count=100,
            specifications={"memory": "8 GB", "storage": "128 GB"},
        ),
        product(
            "phone-c",
            brand="Beta",
            price="550",
            rating="4.8",
            review_count=500,
            specifications={"memory": "12 GB", "storage": "512 GB"},
        ),
    ]

    runs = [rank(items) for _ in range(100)]
    orders = [tuple(item.item_id for item in result.ranked) for result in runs]

    assert len(set(orders)) == 1
    for candidate in runs[0].ranked:
        contributions = [
            component.contribution
            for component in candidate.components
            if component.status is ScoreComponentStatus.USED
        ]
        assert all(value is not None for value in contributions)
        assert sum(contributions, Decimal(0)) == candidate.total_score
        assert candidate.explanation_codes


def test_hard_filter_failures_are_never_ranked_and_tainted_eligible_is_rejected() -> (
    None
):
    kept = product("kept")
    reason = ExclusionReason(
        code="brand_excluded",
        field="brand",
        expected="not blocked",
        actual="blocked",
        action=ExclusionAction.EXCLUDE,
    )
    blocked = FilteredCandidate(
        item_id="blocked",
        sources=(CandidateSource.SEARCH,),
        snapshot_id="snapshot-c4",
        reasons=(reason,),
    )

    result = rank([kept], excluded=(blocked,))
    assert [item.item_id for item in result.ranked] == ["kept"]

    tainted = CandidateSet.model_construct(
        status=CandidateSetStatus.READY,
        snapshot_id="snapshot-c4",
        recalled=(
            CandidateReference(item_id="kept", sources=(CandidateSource.SEARCH,)),
        ),
        eligible=(
            FilteredCandidate.model_construct(
                item_id="kept",
                sources=(CandidateSource.SEARCH,),
                snapshot_id="snapshot-c4",
                reasons=(reason,),
            ),
        ),
        excluded=(),
        failures=(),
        reason_counts=(),
        suggestions=(),
        search_query=None,
    )
    with pytest.raises(RankingInputError, match="hard filter"):
        ProductRanker(load_ranking_policy(REGISTRY), REGISTRY).rank(
            candidate_set=tainted,
            products={kept.item_id: kept},
            normalized_features={kept.item_id: NORMALIZER.normalize(kept)},
            goal=ShoppingGoal(),
        )


def test_missing_feature_is_explicit_and_lowers_confidence_instead_of_becoming_zero() -> (
    None
):
    complete = product(
        "complete",
        specifications={"memory": "16 GB", "storage": "256 GB"},
    )
    incomplete = product(
        "incomplete",
        specifications={"storage": "256 GB"},
    )

    result = rank([incomplete, complete])
    by_id = {item.item_id: item for item in result.ranked}

    assert "memory_gb" in by_id["incomplete"].missing_features
    assert (
        by_id["complete"].evidence_completeness
        > by_id["incomplete"].evidence_completeness
    )
    missing_components = [
        component
        for component in by_id["incomplete"].components
        if component.status is ScoreComponentStatus.MISSING
    ]
    assert missing_components
    assert all(component.raw_score is None for component in missing_components)
    assert all(component.contribution is None for component in missing_components)


def test_profile_missing_policies_remain_distinct_in_ranking_output() -> None:
    registry = FeatureProfileRegistry.from_mapping(
        {
            "profiles": {
                "fallback": {"features": {}},
                "policy_test": {
                    "category_aliases": ["policy-test"],
                    "features": {
                        "ignored_value": {
                            "display_name": "Ignored",
                            "data_type": "number",
                            "larger_is_better": True,
                            "missing_policy": "ignore_score",
                            "ranking": True,
                        },
                        "confidence_value": {
                            "display_name": "Confidence",
                            "data_type": "number",
                            "larger_is_better": True,
                            "missing_policy": "lower_confidence",
                            "ranking": True,
                        },
                        "comparable_flag": {
                            "display_name": "Comparable",
                            "data_type": "boolean",
                            "missing_policy": "not_comparable",
                            "ranking": True,
                        },
                    },
                },
            }
        }
    )
    payload = load_ranking_policy(REGISTRY).model_dump(mode="json")
    payload["policy_version"] = "missing-policy-v1"
    payload["component_weights"] = {
        key: "1" if key == "category_features" else "0"
        for key in payload["component_weights"]
    }
    payload["category_feature_weights"] = {
        "policy_test": {
            "ignored_value": "0.3",
            "confidence_value": "0.3",
            "comparable_flag": "0.4",
        }
    }
    policy = RankingPolicy.from_mapping(payload, profile_registry=registry)
    item = product("policy-item", category="policy-test", specifications=None)
    normalized = FeatureNormalizer(registry).normalize(item)
    missing_policies = {
        feature.key: feature.missing_policy for feature in normalized.features
    }

    assert missing_policies == {
        "ignored_value": FeatureMissingPolicy.IGNORE_SCORE,
        "confidence_value": FeatureMissingPolicy.LOWER_CONFIDENCE,
        "comparable_flag": FeatureMissingPolicy.NOT_COMPARABLE,
    }

    result = ProductRanker(policy, registry).rank(
        candidate_set=candidate_set([item]),
        products={item.item_id: item},
        normalized_features={item.item_id: normalized},
        goal=ShoppingGoal(),
    )
    candidate = result.ranked[0]
    components = {component.code: component for component in candidate.components}

    assert components["feature.ignored_value"].status is ScoreComponentStatus.IGNORED
    assert components["feature.confidence_value"].status is ScoreComponentStatus.MISSING
    assert (
        components["feature.comparable_flag"].status
        is ScoreComponentStatus.NOT_COMPARABLE
    )
    assert candidate.ignored_features == ("ignored_value",)
    assert candidate.not_comparable_features == ("comparable_flag",)
    assert "confidence_value" in candidate.missing_features
    assert "ignored_value" not in candidate.missing_features
    assert "comparable_flag" not in candidate.missing_features


def test_tie_break_is_score_then_completeness_then_item_id() -> None:
    items = [
        product(
            "tie-b",
            specifications={"memory": "8 GB", "storage": "128 GB"},
        ),
        product(
            "tie-a",
            specifications={"memory": "8 GB", "storage": "128 GB"},
        ),
    ]

    result = rank(items)

    assert [item.item_id for item in result.ranked] == ["tie-a", "tie-b"]
    assert result.ranked[0].total_score == result.ranked[1].total_score
    assert (
        result.ranked[0].evidence_completeness == result.ranked[1].evidence_completeness
    )


def test_top_n_diversity_suppresses_brand_concentration_without_dropping_items() -> (
    None
):
    items = [
        product("alpha-1", name="Model One", brand="Alpha", rating="5"),
        product("alpha-2", name="Model Two", brand="Alpha", rating="4.9"),
        product("beta-1", name="Model Three", brand="Beta", rating="3"),
    ]
    policy = policy_variant(version="rating-diverse-v1", component="rating_confidence")

    result = rank(items, policy=policy)

    assert [item.brand_key for item in result.ranked[:2]] == ["alpha", "beta"]
    assert {item.item_id for item in result.ranked} == {
        "alpha-1",
        "alpha-2",
        "beta-1",
    }
    assert result.diversity_applied is True
    assert result.ranked[1].diversity_adjusted is True


def test_candidate_shortage_returns_every_candidate_without_padding() -> None:
    result = rank([product("only-one")])

    assert [item.item_id for item in result.ranked] == ["only-one"]
    assert result.ranked[0].rank == 1


def test_all_low_confidence_is_explicit() -> None:
    sparse = [
        product(
            "sparse-a",
            rating=None,
            review_count=None,
            delivery_days=None,
            specifications=None,
        ),
        product(
            "sparse-b",
            rating=None,
            review_count=None,
            delivery_days=None,
            specifications=None,
        ),
    ]

    result = rank(sparse)

    assert result.all_low_confidence is True
    assert all(
        candidate.evidence_completeness < result.low_confidence_threshold
        for candidate in result.ranked
    )


def test_policy_only_change_updates_version_and_expected_order() -> None:
    cheap = product(
        "cheap",
        price="100",
        specifications={"memory": "4 GB"},
    )
    powerful = product(
        "powerful",
        price="1000",
        specifications={"memory": "32 GB"},
    )
    price_policy = policy_variant(version="price-v1", component="price_position")
    memory_policy = policy_variant(
        version="memory-v2",
        component="category_features",
        feature_weights={"memory_gb": "1"},
    )

    by_price = rank([powerful, cheap], policy=price_policy)
    by_memory = rank([powerful, cheap], policy=memory_policy)

    assert by_price.policy_version == "price-v1"
    assert by_memory.policy_version == "memory-v2"
    assert by_price.ranked[0].item_id == "cheap"
    assert by_memory.ranked[0].item_id == "powerful"


def test_soft_brand_preference_is_scored_and_trace_contains_policy_version() -> None:
    trace = AgentTraceContext.start(user_query="偏好 Beta")
    goal = ShoppingGoal(preferences=(brand_preference("Beta"),))
    result = rank(
        [product("alpha", brand="Alpha"), product("beta", brand="Beta")],
        goal=goal,
        trace=trace,
    )

    assert result.ranked[0].item_id == "beta"
    rank_events = [
        event for event in trace.events if event.event_type is AgentTraceEventType.RANK
    ]
    assert len(rank_events) == 1
    assert rank_events[0].summary["policy_version"] == result.policy_version
    assert rank_events[0].status.value == "success"


def test_soft_specification_preference_uses_c3_unit_normalization() -> None:
    goal = ShoppingGoal(preferences=(specification_preference("memory", "16384 MB"),))
    policy = policy_variant(version="soft-spec-v1", component="soft_preference")
    result = rank(
        [
            product("a-8gb", specifications={"memory": "8 GB"}),
            product("z-16gb", specifications={"memory": "16 GB"}),
        ],
        goal=goal,
        policy=policy,
    )
    by_id = {candidate.item_id: candidate for candidate in result.ranked}
    soft_scores = {
        item_id: next(
            component.raw_score
            for component in candidate.components
            if component.code == "soft_preference"
        )
        for item_id, candidate in by_id.items()
    }

    assert result.ranked[0].item_id == "z-16gb"
    assert soft_scores == {"a-8gb": Decimal(0), "z-16gb": Decimal(1)}
