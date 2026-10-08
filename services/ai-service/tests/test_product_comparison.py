"""Unit coverage for C5 product comparison and review insight boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from app.routers.AImodel.candidate_service import (
    CandidateReference,
    CandidateSet,
    CandidateSetStatus,
    CandidateSource,
    FilteredCandidate,
)
from app.routers.AImodel.comparison import (
    ComparisonConfigError,
    ComparisonInputError,
    EvidenceSourceType,
    ProductComparisonService,
    ReviewBatchClient,
    ReviewCollection,
    ReviewInsightStatus,
    ReviewRecord,
    load_comparison_policy,
)
from app.routers.AImodel.feature_normalizer import (
    FeatureNormalizer,
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
from app.routers.AImodel.ranking import ProductRanker, load_ranking_policy
from app.routers.AImodel.schemas import AiModelProductRef
from app.routers.AImodel.shopping_goal import ShoppingGoal


NOW = datetime(2026, 9, 21, 12, tzinfo=UTC)
SOURCE = FactSource(
    provider="mock-api",
    endpoint="/products/snapshots",
    source_version="comparison-unit-v1",
    captured_at=NOW,
)
REGISTRY = load_feature_profiles()
NORMALIZER = FeatureNormalizer(REGISTRY)
SERVICE = ProductComparisonService(REGISTRY, load_comparison_policy())


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


def item(item_id: str) -> ProductSnapshotItem:
    return ProductSnapshotItem(
        item_id=item_id,
        name=fact(f"Phone {item_id}"),
        category=fact("electronics"),
        brand=fact("Talon"),
        current_price=fact(Decimal("699")),
        currency=fact("CNY"),
        stock=fact(10),
        specifications=fact(
            ProductSpecifications.from_mapping({"memory": "16 GB", "storage": "256 GB"})
        ),
        rating=fact(Decimal("4.8")),
        review_count=fact(100),
        delivery=fact(
            DeliveryCapability(
                shipping_available=True,
                pickup_available=False,
                delivery_available=True,
            )
        ),
    )


def rank(items: list[ProductSnapshotItem]):
    candidates = CandidateSet(
        status=CandidateSetStatus.READY,
        snapshot_id="snapshot-unit",
        recalled=tuple(
            CandidateReference(
                item_id=product.item_id,
                sources=(CandidateSource.SEARCH,),
            )
            for product in items
        ),
        eligible=tuple(
            FilteredCandidate(
                item_id=product.item_id,
                sources=(CandidateSource.SEARCH,),
                snapshot_id="snapshot-unit",
            )
            for product in items
        ),
    )
    normalized = {product.item_id: NORMALIZER.normalize(product) for product in items}
    ranking = ProductRanker(load_ranking_policy(REGISTRY), REGISTRY).rank(
        candidate_set=candidates,
        products={product.item_id: product for product in items},
        normalized_features=normalized,
        goal=ShoppingGoal(),
    )
    return ranking, normalized


def record(review_id: str, *, item_id: str = "phone-a") -> ReviewRecord:
    return ReviewRecord(
        review_id=review_id,
        item_id=item_id,
        rating=5,
        title="Battery",
        content="battery life is good",
        created_at=NOW,
        updated_at=NOW,
    )


def test_policy_loader_rejects_invalid_threshold_relationship(tmp_path) -> None:
    policy_path = tmp_path / "comparison.yaml"
    policy_path.write_text(
        """
schema_version: v1
policy_version: invalid
minimum_review_sample: 2
minimum_topic_mentions: 3
majority_ratio: '0.6'
controversy_minimum_each: 1
topics:
  battery:
    label: Battery
    keywords: [battery]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ComparisonConfigError):
        load_comparison_policy(policy_path)


def test_matrix_rejects_too_few_candidates_and_missing_snapshot() -> None:
    products = [item("phone-a"), item("phone-b")]
    ranking, normalized = rank(products)

    with pytest.raises(ComparisonInputError, match="2-5"):
        SERVICE.build_matrix(
            ranking=ranking,
            products={product.item_id: product for product in products},
            normalized_features=normalized,
            selected_item_ids=["phone-a"],
        )

    with pytest.raises(ComparisonInputError, match="snapshot"):
        SERVICE.build_matrix(
            ranking=ranking.model_copy(update={"snapshot_id": None}),
            products={product.item_id: product for product in products},
            normalized_features=normalized,
            selected_item_ids=[product.item_id for product in products],
        )


def test_matrix_transposes_to_closed_a4_payload() -> None:
    products = [item("phone-a"), item("phone-b")]
    ranking, normalized = rank(products)
    matrix = SERVICE.build_matrix(
        ranking=ranking,
        products={product.item_id: product for product in products},
        normalized_features=normalized,
        selected_item_ids=[product.item_id for product in products],
    )
    refs = {
        product.item_id: AiModelProductRef(
            item_id=product.item_id,
            item_name=str(product.name.value),
        )
        for product in products
    }

    payload = matrix.to_payload(answer="结构化比较", products=refs)

    assert [str(product.item_id) for product in payload.products] == [
        column.item_id for column in matrix.columns
    ]
    assert {column.key for column in payload.columns} == {
        row.feature_key for row in matrix.rows
    }
    assert all(
        set(row.cells) == {column.key for column in payload.columns}
        for row in payload.rows
    )
    assert {evidence.source_type for evidence in payload.evidence} == {
        "product_fact",
        "policy",
    }


def test_review_collection_rejects_mismatched_or_unreported_reviews() -> None:
    with pytest.raises(ValidationError, match="match collection"):
        ReviewCollection(
            item_id="phone-a",
            reported_review_count=1,
            average_rating=Decimal(5),
            reviews=(record("r-1", item_id="phone-b"),),
            source_version="reviews-v1",
            captured_at=NOW,
        )

    with pytest.raises(ValidationError, match="smaller than batch"):
        ReviewCollection(
            item_id="phone-a",
            reported_review_count=0,
            average_rating=Decimal(5),
            reviews=(record("r-1"),),
            source_version="reviews-v1",
            captured_at=NOW,
        )


def test_empty_review_collection_is_not_summarized() -> None:
    report = SERVICE.summarize_reviews(
        ReviewCollection(
            item_id="phone-a",
            reported_review_count=0,
            average_rating=Decimal(0),
            reviews=(),
            source_version="reviews-v1",
            captured_at=NOW,
        )
    )

    assert report.status is ReviewInsightStatus.NO_REVIEWS
    assert report.insights == ()


def test_review_batch_client_preserves_successful_item_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/items/reviews/batch"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "source_version": "mock-api-item-reviews-v1",
                "captured_at": NOW.isoformat(),
                "items": [
                    {
                        "item_id": "phone-b",
                        "status": "ok",
                        "summary": {"average_rating": 5, "review_count": 1},
                        "reviews": [
                            {
                                "id": 9,
                                "item_id": "phone-b",
                                "rating": 5,
                                "title": "Battery",
                                "content": "battery is good",
                                "created_at": NOW.isoformat(),
                                "updated_at": NOW.isoformat(),
                            }
                        ],
                    },
                    {
                        "item_id": "missing",
                        "status": "error",
                        "error": {"code": "item_not_found"},
                    },
                    {
                        "item_id": "phone-a",
                        "status": "ok",
                        "summary": {"average_rating": 0, "review_count": 0},
                        "reviews": [],
                    },
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    collections = ReviewBatchClient("http://mock-api", client).fetch(
        ["phone-b", "missing", "phone-a"]
    )

    assert tuple(item.item_id for item in collections) == ("phone-b", "phone-a")
    assert collections[0].reviews[0].review_id == "9"
    assert collections[0].source_version == "mock-api-item-reviews-v1"


def test_review_batch_client_rejects_duplicate_ids_and_http_failure() -> None:
    client = ReviewBatchClient(
        "http://mock-api",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(503, json={"ok": False})
            )
        ),
    )

    with pytest.raises(ComparisonInputError, match="unique"):
        client.fetch(["phone-a", "phone-a"])
    with pytest.raises(ComparisonInputError, match="failed"):
        client.fetch(["phone-a"])


def test_score_breakdown_is_exposed_as_ranking_evidence() -> None:
    products = [item("phone-a"), item("phone-b")]
    ranking, normalized = rank(products)
    matrix = SERVICE.build_matrix(
        ranking=ranking,
        products={product.item_id: product for product in products},
        normalized_features=normalized,
        selected_item_ids=[product.item_id for product in products],
    )

    ranking_evidence = [
        evidence
        for evidence in matrix.evidence
        if evidence.source_type is EvidenceSourceType.RANKING_SCORE
    ]
    assert len(ranking_evidence) == sum(
        len(candidate.components) for candidate in ranking.ranked
    )
    assert all(evidence.snapshot_id == "snapshot-unit" for evidence in ranking_evidence)
