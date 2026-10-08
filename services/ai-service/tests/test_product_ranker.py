"""Unit coverage for C4 ranking policy and input boundary failures."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

import pytest

from app.routers.AImodel.agent_trace import (
    AgentTraceContext,
    AgentTraceEventType,
    AgentTraceStatus,
)
from app.routers.AImodel.candidate_service import (
    CandidateReference,
    CandidateSet,
    CandidateSetStatus,
    CandidateSource,
    FilteredCandidate,
)
from app.routers.AImodel.feature_normalizer import (
    FeatureNormalizationStatus,
    FeatureNormalizer,
    FeatureProfileRegistry,
    load_feature_profiles,
)
from app.routers.AImodel.product_models import (
    DeliveryCapability,
    FactSource,
    FactStatus,
    Freshness,
    ProductFact,
    ProductSnapshotItem,
    ProductSpecifications,
)
from app.routers.AImodel.ranking import (
    ProductRanker,
    RankingInputError,
    RankingPolicy,
    RankingPolicyError,
    ScoreComponentStatus,
    load_ranking_policy,
)
from app.routers.AImodel.shopping_goal import ShoppingGoal


NOW = datetime(2026, 9, 21, 12, tzinfo=UTC)
SOURCE = FactSource(
    provider="mock-api",
    endpoint="/products/snapshots",
    source_version="ranking-unit-v1",
    captured_at=NOW,
)
REGISTRY = load_feature_profiles()
NORMALIZER = FeatureNormalizer(REGISTRY)


def fact(value: object) -> ProductFact:
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


def item(item_id: str = "sku-1") -> ProductSnapshotItem:
    return ProductSnapshotItem(
        item_id=item_id,
        name=fact("Talon Phone"),
        category=fact("electronics"),
        brand=fact("Talon"),
        current_price=fact(Decimal("699")),
        currency=fact("CNY"),
        stock=fact(10),
        specifications=fact(
            ProductSpecifications.from_mapping(
                {
                    "memory": "16 GB",
                    "storage": "256 GB",
                    "wireless_charging": "yes",
                }
            )
        ),
        rating=fact(Decimal("4.8")),
        review_count=fact(500),
        delivery=fact(
            DeliveryCapability(
                shipping_available=True,
                pickup_available=False,
                delivery_available=True,
            )
        ),
    )


def candidate_set(product: ProductSnapshotItem) -> CandidateSet:
    return CandidateSet(
        status=CandidateSetStatus.READY,
        snapshot_id="snapshot-unit",
        recalled=(
            CandidateReference(
                item_id=product.item_id,
                sources=(CandidateSource.SEARCH,),
            ),
        ),
        eligible=(
            FilteredCandidate(
                item_id=product.item_id,
                sources=(CandidateSource.SEARCH,),
                snapshot_id="snapshot-unit",
            ),
        ),
    )


def policy_payload() -> dict:
    return load_ranking_policy(REGISTRY).model_dump(mode="json")


def test_default_policy_loads_and_scores_with_bounded_components() -> None:
    product = item()
    result = ProductRanker(load_ranking_policy(REGISTRY), REGISTRY).rank(
        candidate_set=candidate_set(product),
        products={product.item_id: product},
        normalized_features={product.item_id: NORMALIZER.normalize(product)},
        goal=ShoppingGoal(),
    )

    ranked = result.ranked[0]
    assert result.policy_version == "c4-baseline-v1"
    assert Decimal(0) <= ranked.total_score <= Decimal(1)
    assert (
        sum(
            component.contribution
            for component in ranked.components
            if component.status is ScoreComponentStatus.USED
        )
        == ranked.total_score
    )
    quantum = Decimal("0.000001")
    for component in ranked.components:
        if component.status is ScoreComponentStatus.USED:
            assert component.contribution == (
                component.raw_score * component.effective_weight
            ).quantize(quantum, rounding=ROUND_DOWN)
    assert "memory_gb" in ranked.used_features


def test_maximum_length_product_name_remains_a_valid_model_identity() -> None:
    base = item()
    product = base.model_copy(
        update={
            "name": base.name.model_copy(update={"value": "x" * 512}),
        }
    )

    result = ProductRanker(load_ranking_policy(REGISTRY), REGISTRY).rank(
        candidate_set=candidate_set(product),
        products={product.item_id: product},
        normalized_features={product.item_id: NORMALIZER.normalize(product)},
        goal=ShoppingGoal(),
    )

    assert result.ranked[0].model_key == "x" * 512


def test_declared_preference_value_uses_product_unit_normalization() -> None:
    normalized = NORMALIZER.normalize_value(
        profile_id="electronics",
        feature_key="memory",
        raw_value="16384 MB",
    )

    assert normalized.key == "memory_gb"
    assert normalized.status is FeatureNormalizationStatus.KNOWN
    assert normalized.value == Decimal(16)
    assert normalized.unit == "GB"


def test_profile_missing_semantics_are_exposed_in_distinct_states() -> None:
    base = item()
    product = base.model_copy(
        update={
            "specifications": fact(
                ProductSpecifications.from_mapping({"storage": "256 GB"})
            )
        }
    )
    result = ProductRanker(load_ranking_policy(REGISTRY), REGISTRY).rank(
        candidate_set=candidate_set(product),
        products={product.item_id: product},
        normalized_features={product.item_id: NORMALIZER.normalize(product)},
        goal=ShoppingGoal(),
    )
    ranked = result.ranked[0]
    components = {component.code: component for component in ranked.components}

    assert components["feature.memory_gb"].status is ScoreComponentStatus.MISSING
    assert (
        components["feature.wireless_charging"].status
        is ScoreComponentStatus.NOT_COMPARABLE
    )
    assert "memory_gb" in ranked.missing_features
    assert "wireless_charging" in ranked.not_comparable_features


def test_ignore_score_missing_feature_does_not_lower_evidence_completeness() -> None:
    registry = FeatureProfileRegistry.from_mapping(
        {
            "profiles": {
                "fallback": {"features": {}},
                "ignore_test": {
                    "category_aliases": ["ignore-test"],
                    "features": {
                        "optional_value": {
                            "display_name": "Optional",
                            "data_type": "number",
                            "larger_is_better": True,
                            "missing_policy": "ignore_score",
                            "ranking": True,
                        },
                        "stable_value": {
                            "display_name": "Stable",
                            "data_type": "number",
                            "larger_is_better": True,
                            "missing_policy": "lower_confidence",
                            "ranking": True,
                        },
                    },
                },
            }
        }
    )
    normalizer = FeatureNormalizer(registry)
    payload = policy_payload()
    payload["policy_version"] = "ignore-test-v1"
    payload["component_weights"] = {
        key: "1" if key == "category_features" else "0"
        for key in payload["component_weights"]
    }
    payload["category_feature_weights"] = {
        "ignore_test": {"optional_value": "0.5", "stable_value": "0.5"}
    }
    policy = RankingPolicy.from_mapping(payload, profile_registry=registry)

    def custom_product(item_id: str, specifications: dict[str, str]):
        base = item(item_id)
        return base.model_copy(
            update={
                "category": fact("ignore-test"),
                "specifications": fact(
                    ProductSpecifications.from_mapping(specifications)
                ),
            }
        )

    complete = custom_product(
        "complete", {"optional_value": "10", "stable_value": "10"}
    )
    optional_missing = custom_product("optional-missing", {"stable_value": "10"})
    products = {item.item_id: item for item in (complete, optional_missing)}
    result = ProductRanker(policy, registry).rank(
        candidate_set=candidate_set(complete).model_copy(
            update={
                "recalled": (
                    CandidateReference(
                        item_id=complete.item_id,
                        sources=(CandidateSource.SEARCH,),
                    ),
                    CandidateReference(
                        item_id=optional_missing.item_id,
                        sources=(CandidateSource.SEARCH,),
                    ),
                ),
                "eligible": (
                    FilteredCandidate(
                        item_id=complete.item_id,
                        sources=(CandidateSource.SEARCH,),
                        snapshot_id="snapshot-unit",
                    ),
                    FilteredCandidate(
                        item_id=optional_missing.item_id,
                        sources=(CandidateSource.SEARCH,),
                        snapshot_id="snapshot-unit",
                    ),
                ),
            }
        ),
        products=products,
        normalized_features={
            item_id: normalizer.normalize(product)
            for item_id, product in products.items()
        },
        goal=ShoppingGoal(),
    )
    ranked = {candidate.item_id: candidate for candidate in result.ranked}

    assert ranked["complete"].evidence_completeness == Decimal(1)
    assert ranked["optional-missing"].evidence_completeness == Decimal(1)
    assert ranked["optional-missing"].ignored_features == ("optional_value",)


@pytest.mark.parametrize(
    "weights",
    [
        {
            "soft_preference": "0",
            "price_position": "0",
            "rating_confidence": "0",
            "review_volume": "0",
            "delivery_match": "0",
            "category_features": "0.9",
        },
        {
            "soft_preference": "0",
            "price_position": "0",
            "rating_confidence": "0",
            "review_volume": "0",
            "delivery_match": "0",
            "category_features": "1",
            "invented": "0",
        },
    ],
)
def test_policy_rejects_invalid_component_weight_contract(weights: dict) -> None:
    payload = policy_payload()
    payload["component_weights"] = weights

    with pytest.raises(RankingPolicyError, match="component_weights"):
        RankingPolicy.from_mapping(payload, profile_registry=REGISTRY)


@pytest.mark.parametrize("feature", ["not_a_feature", "color"])
def test_policy_rejects_unknown_or_non_rankable_features(feature: str) -> None:
    payload = policy_payload()
    payload["category_feature_weights"]["fallback"] = {feature: "1"}

    with pytest.raises(RankingPolicyError, match="category_feature_weights"):
        RankingPolicy.from_mapping(payload, profile_registry=REGISTRY)


def test_missing_snapshot_item_is_rejected_and_trace_closes_as_error() -> None:
    product = item()
    trace = AgentTraceContext.start(user_query="推荐手机")

    with pytest.raises(RankingInputError, match="missing its product snapshot"):
        ProductRanker(load_ranking_policy(REGISTRY), REGISTRY).rank(
            candidate_set=candidate_set(product),
            products={},
            normalized_features={product.item_id: NORMALIZER.normalize(product)},
            goal=ShoppingGoal(),
            trace_context=trace,
        )

    rank_event = next(
        event for event in trace.events if event.event_type is AgentTraceEventType.RANK
    )
    assert rank_event.status is AgentTraceStatus.ERROR
    assert rank_event.summary["policy_version"] == "c4-baseline-v1"


def test_mismatched_normalized_identity_is_rejected() -> None:
    expected = item("expected")
    other = item("other")

    with pytest.raises(RankingInputError, match="mismatched item identity"):
        ProductRanker(load_ranking_policy(REGISTRY), REGISTRY).rank(
            candidate_set=candidate_set(expected),
            products={expected.item_id: expected},
            normalized_features={expected.item_id: NORMALIZER.normalize(other)},
            goal=ShoppingGoal(),
        )


def test_no_candidate_result_is_empty_and_versioned() -> None:
    result = ProductRanker(load_ranking_policy(REGISTRY), REGISTRY).rank(
        candidate_set=CandidateSet(status=CandidateSetStatus.NO_CANDIDATE),
        products={},
        normalized_features={},
        goal=ShoppingGoal(),
    )

    assert result.ranked == ()
    assert result.policy_version == "c4-baseline-v1"
    assert result.diversity_applied is False
    assert result.all_low_confidence is False
