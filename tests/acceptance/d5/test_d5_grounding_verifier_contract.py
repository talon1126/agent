"""Frozen acceptance contract for D5 deterministic grounding verification."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "ai-service"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.routers.AImodel.agent_trace import AgentTraceContext  # noqa: E402
from app.routers.AImodel.candidate_service import (
    CandidateReference,
    CandidateSet,
    CandidateSetStatus,
    CandidateSource,
    ExclusionReason,
    FilteredCandidate,
)  # noqa: E402
from app.routers.AImodel.comparison import (
    ComparisonCell,
    ComparisonCellStatus,
    ComparisonColumn,
    ComparisonMatrix,
    ComparisonRow,
    EvidenceRef,
    EvidenceSourceType,
)  # noqa: E402
from app.routers.AImodel.product_models import (
    DeliveryCapability,
    FactSource,
    FactStatus,
    Freshness,
    FreshnessState,
    ProductFact,
    ProductSnapshot,
    ProductSnapshotEntry,
    ProductSnapshotItem,
    ProductSpecifications,
)  # noqa: E402
from app.routers.AImodel.ranking import RankedCandidate, RankingResult  # noqa: E402
from app.routers.AImodel.schemas import (
    AiModelEvidenceReference,
    AiModelProductRef,
    AiModelRecommendationPayload,
    AiModelRecommendationReason,
)  # noqa: E402
from app.routers.AImodel.shopping_goal import (
    Constraint,
    GoalEvidence,
    GoalField,
    GoalSourceType,
    ShoppingGoal,
)  # noqa: E402
from app.routers.AImodel.verifier import (
    ClaimType,
    GroundedResponseDraft,
    GroundingDecision,
    GroundingVerifier,
    ResponseClaim,
    VerificationErrorCode,
)  # noqa: E402


NOW = datetime(2026, 9, 22, 8, tzinfo=UTC)
SOURCE = FactSource(
    provider="mock-api",
    endpoint="/products/snapshots",
    source_version="d5-acceptance-v1",
    captured_at=NOW,
)


def _fact(value: object) -> ProductFact:
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


def _product(
    item_id: str,
    *,
    name: str,
    price: str,
    stock: int,
    brand: str = "Talon",
) -> ProductSnapshotItem:
    return ProductSnapshotItem(
        item_id=item_id,
        name=_fact(name),
        category=_fact("phone"),
        brand=_fact(brand),
        current_price=_fact(Decimal(price)),
        currency=_fact("CNY"),
        stock=_fact(stock),
        specifications=_fact(ProductSpecifications.from_mapping({"memory": "16 GB"})),
        rating=_fact(Decimal("4.8")),
        review_count=_fact(100),
        delivery=_fact(
            DeliveryCapability(
                shipping_available=True,
                pickup_available=False,
                delivery_available=True,
                estimated_delivery_at=NOW,
            )
        ),
    )


PRODUCT_A = _product("sku-1", name="Phone One", price="499", stock=10)
PRODUCT_B = _product("sku-2", name="Phone Two", price="699", stock=0)


def _snapshot() -> ProductSnapshot:
    return ProductSnapshot(
        snapshot_id="snapshot-d5",
        turn_id="turn-d5",
        source_version=SOURCE.source_version,
        captured_at=NOW,
        requested_item_ids=("sku-1", "sku-2"),
        entries=(
            ProductSnapshotEntry(item_id="sku-1", status="ok", item=PRODUCT_A),
            ProductSnapshotEntry(item_id="sku-2", status="ok", item=PRODUCT_B),
        ),
    )


def _candidate_set(*, exclude_second: bool = True) -> CandidateSet:
    recalled = tuple(
        CandidateReference(item_id=item_id, sources=(CandidateSource.SEARCH,))
        for item_id in ("sku-1", "sku-2")
    )
    eligible_ids = ("sku-1",) if exclude_second else ("sku-1", "sku-2")
    eligible = tuple(
        FilteredCandidate(
            item_id=item_id,
            sources=(CandidateSource.SEARCH,),
            snapshot_id="snapshot-d5",
        )
        for item_id in eligible_ids
    )
    excluded = (
        (
            FilteredCandidate(
                item_id="sku-2",
                sources=(CandidateSource.SEARCH,),
                snapshot_id="snapshot-d5",
                reasons=(
                    ExclusionReason(
                        code="budget_max_exceeded",
                        field="budget_max",
                        expected=Decimal("500"),
                        actual=Decimal("699"),
                    ),
                ),
            ),
        )
        if exclude_second
        else ()
    )
    return CandidateSet(
        status=CandidateSetStatus.READY,
        snapshot_id="snapshot-d5",
        recalled=recalled,
        eligible=eligible,
        excluded=excluded,
    )


def _ranked(item_id: str, rank: int, score: str) -> RankedCandidate:
    return RankedCandidate(
        item_id=item_id,
        rank=rank,
        base_rank=rank,
        total_score=Decimal(score),
        evidence_completeness=Decimal("1"),
        components=(),
        used_features=(),
        missing_features=(),
        ignored_features=(),
        not_comparable_features=(),
        explanation_codes=("deterministic_score",),
        brand_key="talon",
        model_key=item_id,
    )


def _ranking(*, include_second: bool = False) -> RankingResult:
    ranked = [_ranked("sku-1", 1, "0.9")]
    if include_second:
        ranked.append(_ranked("sku-2", 2, "0.7"))
    return RankingResult(
        policy_version="ranking-d5-v1",
        snapshot_id="snapshot-d5",
        ranked=tuple(ranked),
        diversity_applied=False,
        all_low_confidence=False,
        low_confidence_threshold=Decimal("0.5"),
    )


def _goal() -> ShoppingGoal:
    return ShoppingGoal(
        hard_constraints=(
            Constraint(
                field=GoalField.BUDGET_MAX,
                value=Decimal("500"),
                evidence=GoalEvidence(
                    source_type=GoalSourceType.USER_TURN,
                    source_turn=1,
                    quote="预算不超过 500",
                    confidence=1,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            ),
        )
    )


def _evidence(
    field: str,
    *,
    item_id: str = "sku-1",
    evidence_id: str | None = None,
) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=evidence_id or f"fact:snapshot-d5:{item_id}:{field}",
        source_type=EvidenceSourceType.PRODUCT_FACT,
        source_id=f"{item_id}.{field}",
        item_id=item_id,
        snapshot_id="snapshot-d5",
        title=f"{item_id} {field}",
    )


def _draft(
    *claims: ResponseClaim,
    item_id: str = "sku-1",
    item_name: str = "Phone One",
    answer: str = "根据本轮商品事实推荐。",
    evidence: tuple[EvidenceRef, ...] = (),
) -> GroundedResponseDraft:
    payload_evidence = [
        AiModelEvidenceReference(
            evidence_id=item.evidence_id,
            source_type="product_fact",
            source_id=item.source_id,
            title=item.title,
        )
        for item in evidence
    ]
    return GroundedResponseDraft(
        payload=AiModelRecommendationPayload(
            answer=answer,
            candidates=[AiModelProductRef(item_id=item_id, item_name=item_name)],
            recommendations=[
                AiModelRecommendationReason(
                    item_id=item_id,
                    reason="符合当前需求",
                    evidence_ids=[item.evidence_id for item in evidence],
                )
            ],
            evidence=payload_evidence,
        ),
        claims=claims,
    )


def _verify(
    draft: GroundedResponseDraft,
    *,
    evidence: tuple[EvidenceRef, ...],
    candidate_set: CandidateSet | None = None,
    ranking: RankingResult | None = None,
    comparison: ComparisonMatrix | None = None,
):
    return GroundingVerifier().verify(
        draft=draft,
        goal=_goal(),
        snapshot=_snapshot(),
        candidate_set=candidate_set or _candidate_set(),
        ranking=ranking or _ranking(),
        evidence=evidence,
        comparison=comparison,
    )


def test_valid_structured_claim_is_deterministic_and_passes() -> None:
    price_evidence = _evidence("current_price")
    draft = _draft(
        ResponseClaim(
            claim_id="claim-price",
            claim_type=ClaimType.PRICE,
            item_id="sku-1",
            value=Decimal("499"),
            evidence_ids=(price_evidence.evidence_id,),
        ),
        evidence=(price_evidence,),
    )

    first = _verify(draft, evidence=(price_evidence,))
    second = _verify(draft, evidence=(price_evidence,))

    assert first.valid is True
    assert first == second
    assert first.error_codes == ()
    assert first.verified_claim_ids == ("claim-price",)


@pytest.mark.parametrize(
    ("item_id", "candidate_set", "expected_code"),
    [
        (
            "sku-missing",
            _candidate_set(),
            VerificationErrorCode.PRODUCT_NOT_IN_CANDIDATES,
        ),
        (
            "sku-2",
            _candidate_set(),
            VerificationErrorCode.HARD_FILTER_VIOLATION,
        ),
    ],
)
def test_unknown_and_filtered_recommendations_are_rejected(
    item_id: str,
    candidate_set: CandidateSet,
    expected_code: VerificationErrorCode,
) -> None:
    result = _verify(
        _draft(item_id=item_id, item_name="Unknown"),
        evidence=(),
        candidate_set=candidate_set,
    )

    assert result.valid is False
    assert expected_code in result.error_codes


@pytest.mark.parametrize(
    ("claim_type", "field", "value", "expected_code"),
    [
        (
            ClaimType.PRICE,
            "current_price",
            Decimal("399"),
            VerificationErrorCode.PRICE_MISMATCH,
        ),
        (
            ClaimType.STOCK,
            "stock",
            999,
            VerificationErrorCode.STOCK_MISMATCH,
        ),
    ],
)
def test_stale_price_and_invented_stock_are_rejected(
    claim_type: ClaimType,
    field: str,
    value: object,
    expected_code: VerificationErrorCode,
) -> None:
    fact_evidence = _evidence(field)
    result = _verify(
        _draft(
            ResponseClaim(
                claim_id=f"claim-{field}",
                claim_type=claim_type,
                item_id="sku-1",
                value=value,
                evidence_ids=(fact_evidence.evidence_id,),
            ),
            evidence=(fact_evidence,),
        ),
        evidence=(fact_evidence,),
    )

    assert result.valid is False
    assert expected_code in result.error_codes


def test_unregistered_and_cross_item_evidence_are_rejected() -> None:
    other_item_evidence = _evidence(
        "current_price",
        item_id="sku-2",
        evidence_id="evidence-other-item",
    )
    missing = ResponseClaim(
        claim_id="claim-missing-evidence",
        claim_type=ClaimType.PRICE,
        item_id="sku-1",
        value=Decimal("499"),
        evidence_ids=("evidence-not-registered",),
    )
    wrong_item = ResponseClaim(
        claim_id="claim-wrong-item",
        claim_type=ClaimType.PRICE,
        item_id="sku-1",
        value=Decimal("499"),
        evidence_ids=(other_item_evidence.evidence_id,),
    )

    result = _verify(
        _draft(missing, wrong_item, evidence=(other_item_evidence,)),
        evidence=(other_item_evidence,),
    )

    assert VerificationErrorCode.EVIDENCE_NOT_FOUND in result.error_codes
    assert VerificationErrorCode.EVIDENCE_ITEM_MISMATCH in result.error_codes


def _comparison() -> ComparisonMatrix:
    evidence_a = _evidence("memory", evidence_id="matrix-a-memory")
    evidence_b = _evidence("memory", item_id="sku-2", evidence_id="matrix-b-memory")
    return ComparisonMatrix(
        snapshot_id="snapshot-d5",
        ranking_policy_version="ranking-d5-v1",
        comparable=True,
        columns=(
            ComparisonColumn(item_id="sku-1", rank=1, item_name="Phone One"),
            ComparisonColumn(item_id="sku-2", rank=2, item_name="Phone Two"),
        ),
        rows=(
            ComparisonRow(
                feature_key="memory",
                label="Memory",
                cells=(
                    ComparisonCell(
                        item_id="sku-1",
                        status=ComparisonCellStatus.KNOWN,
                        normalized_value="16 GB",
                        display_value="16 GB",
                        source_version=SOURCE.source_version,
                        freshness=FreshnessState.FRESH,
                        evidence_id=evidence_a.evidence_id,
                    ),
                    ComparisonCell(
                        item_id="sku-2",
                        status=ComparisonCellStatus.KNOWN,
                        normalized_value="16 GB",
                        display_value="16 GB",
                        source_version=SOURCE.source_version,
                        freshness=FreshnessState.FRESH,
                        evidence_id=evidence_b.evidence_id,
                    ),
                ),
            ),
        ),
        evidence=(evidence_a, evidence_b),
    )


def test_comparison_claim_must_equal_the_matrix_cell() -> None:
    matrix = _comparison()
    matrix_evidence = matrix.evidence[0]
    result = _verify(
        _draft(
            ResponseClaim(
                claim_id="claim-memory",
                claim_type=ClaimType.COMPARISON_CELL,
                item_id="sku-1",
                field="memory",
                value="32 GB",
                evidence_ids=(matrix_evidence.evidence_id,),
            ),
            evidence=(matrix_evidence,),
        ),
        evidence=matrix.evidence,
        candidate_set=_candidate_set(exclude_second=False),
        ranking=_ranking(include_second=True),
        comparison=matrix,
    )

    assert result.valid is False
    assert VerificationErrorCode.COMPARISON_CELL_MISMATCH in result.error_codes


@pytest.mark.parametrize(
    ("claim_type", "expected_code"),
    [
        (ClaimType.PROMOTION, VerificationErrorCode.UNVERIFIED_PROMOTION),
        (ClaimType.SALES_VOLUME, VerificationErrorCode.UNVERIFIED_SALES),
        (ClaimType.ABSOLUTE, VerificationErrorCode.ABSOLUTE_CLAIM_FORBIDDEN),
    ],
)
def test_unsupported_commercial_claims_are_never_accepted(
    claim_type: ClaimType,
    expected_code: VerificationErrorCode,
) -> None:
    price_evidence = _evidence("current_price")
    result = _verify(
        _draft(
            ResponseClaim(
                claim_id=f"claim-{claim_type.value}",
                claim_type=claim_type,
                item_id="sku-1",
                value="guaranteed",
                evidence_ids=(price_evidence.evidence_id,),
            ),
            evidence=(price_evidence,),
        ),
        evidence=(price_evidence,),
    )

    assert result.valid is False
    assert expected_code in result.error_codes


def test_verifier_recomputes_hard_constraints_instead_of_trusting_filter_flag() -> None:
    price_evidence = _evidence("current_price", item_id="sku-2")
    result = _verify(
        _draft(
            ResponseClaim(
                claim_id="claim-over-budget",
                claim_type=ClaimType.PRICE,
                item_id="sku-2",
                value=Decimal("699"),
                evidence_ids=(price_evidence.evidence_id,),
            ),
            item_id="sku-2",
            item_name="Phone Two",
            evidence=(price_evidence,),
        ),
        evidence=(price_evidence,),
        candidate_set=_candidate_set(exclude_second=False),
        ranking=_ranking(include_second=True),
    )

    assert result.valid is False
    assert VerificationErrorCode.HARD_FILTER_VIOLATION in result.error_codes


def test_one_repair_can_recover_but_is_never_called_recursively() -> None:
    async def scenario() -> None:
        price_evidence = _evidence("current_price")
        initial = _draft(
            ResponseClaim(
                claim_id="claim-price",
                claim_type=ClaimType.PRICE,
                item_id="sku-1",
                value=Decimal("399"),
                evidence_ids=(price_evidence.evidence_id,),
            ),
            evidence=(price_evidence,),
        )
        repaired = initial.model_copy(
            update={
                "claims": (
                    initial.claims[0].model_copy(update={"value": Decimal("499")}),
                )
            }
        )
        calls = 0

        async def composer(request):
            nonlocal calls
            calls += 1
            assert VerificationErrorCode.PRICE_MISMATCH in request.error_codes
            return repaired

        outcome = await GroundingVerifier().verify_with_repair(
            draft=initial,
            goal=_goal(),
            snapshot=_snapshot(),
            candidate_set=_candidate_set(),
            ranking=_ranking(),
            evidence=(price_evidence,),
            comparison=None,
            composer=composer,
        )

        assert calls == 1
        assert outcome.decision is GroundingDecision.REPAIRED
        assert outcome.repair_count == 1
        assert outcome.verification.valid is True
        assert outcome.final_payload.response_type == "recommendation"

    asyncio.run(scenario())


def test_second_failure_returns_safe_partial_fallback_and_safe_trace() -> None:
    async def scenario() -> None:
        price_evidence = _evidence("current_price")
        stock_evidence = _evidence("stock")
        invalid = _draft(
            ResponseClaim(
                claim_id="claim-price",
                claim_type=ClaimType.PRICE,
                item_id="sku-1",
                value=Decimal("399"),
                evidence_ids=(price_evidence.evidence_id,),
            ),
            ResponseClaim(
                claim_id="claim-stock",
                claim_type=ClaimType.STOCK,
                item_id="sku-1",
                value=10,
                evidence_ids=(stock_evidence.evidence_id,),
            ),
            answer="private prose must not enter trace",
            evidence=(price_evidence, stock_evidence),
        )
        calls = 0

        async def composer(_request):
            nonlocal calls
            calls += 1
            return invalid

        trace = AgentTraceContext.start(user_query="shopping request")
        outcome = await GroundingVerifier().verify_with_repair(
            draft=invalid,
            goal=_goal(),
            snapshot=_snapshot(),
            candidate_set=_candidate_set(),
            ranking=_ranking(),
            evidence=(price_evidence, stock_evidence),
            comparison=None,
            composer=composer,
            trace_context=trace,
        )

        assert calls == 1
        assert outcome.decision is GroundingDecision.FALLBACK
        assert outcome.repair_count == 1
        assert outcome.final_payload.response_type == "fallback"
        assert outcome.final_payload.reason_code == "grounding_verification_failed"
        assert "已验证" in outcome.final_payload.answer
        assert [item.evidence_id for item in outcome.final_payload.evidence] == [
            stock_evidence.evidence_id
        ]
        verify_events = [event for event in trace.events if event.stage == "grounding"]
        assert len(verify_events) == 1
        assert verify_events[0].summary["repair_count"] == 1
        assert verify_events[0].summary["decision"] == "fallback"
        assert "private prose" not in json.dumps(
            verify_events[0].to_record(), ensure_ascii=False, default=str
        )

    asyncio.run(scenario())
