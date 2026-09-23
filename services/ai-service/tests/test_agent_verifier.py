"""Unit coverage for D5 deterministic grounding and bounded recovery."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.routers.AImodel.agent_trace import AgentTraceContext
from app.routers.AImodel.candidate_service import (
    CandidateReference,
    CandidateSet,
    CandidateSetStatus,
    CandidateSource,
    ExclusionReason,
    FilteredCandidate,
)
from app.routers.AImodel.comparison import (
    ComparisonCell,
    ComparisonCellStatus,
    ComparisonColumn,
    ComparisonMatrix,
    ComparisonRow,
    EvidenceRef,
    EvidenceSourceType,
)
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
)
from app.routers.AImodel.ranking import RankedCandidate, RankingResult
from app.routers.AImodel.schemas import (
    AiModelAnswerPayload,
    AiModelEvidenceReference,
    AiModelProductRef,
    AiModelRecommendationPayload,
    AiModelRecommendationReason,
)
from app.routers.AImodel.shopping_goal import ShoppingGoal
from app.routers.AImodel.verifier import (
    ClaimType,
    GroundedResponseDraft,
    GroundingDecision,
    GroundingVerifier,
    ResponseClaim,
    VerificationErrorCode,
)


ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 9, 22, 9, tzinfo=UTC)
SOURCE = FactSource(
    provider="mock-api",
    endpoint="/products/snapshots",
    source_version="d5-unit-v1",
    captured_at=NOW,
)


def _fact(value: object, *, freshness: Freshness | None = None) -> ProductFact:
    return ProductFact(
        status=FactStatus.KNOWN,
        value=value,
        source=SOURCE,
        freshness=freshness
        or Freshness.from_observation(
            observed_at=NOW,
            captured_at=NOW,
            max_age_seconds=300,
        ),
    )


def _product(item_id: str, *, price: str = "499") -> ProductSnapshotItem:
    return ProductSnapshotItem(
        item_id=item_id,
        name=_fact(f"Phone {item_id}"),
        category=_fact("phone"),
        brand=_fact("Talon"),
        current_price=_fact(Decimal(price)),
        currency=_fact("CNY"),
        stock=_fact(10),
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


def _snapshot(*, first: ProductSnapshotItem | None = None) -> ProductSnapshot:
    products = (first or _product("sku-1"), _product("sku-2"))
    return ProductSnapshot(
        snapshot_id="snapshot-unit",
        turn_id="turn-unit",
        source_version=SOURCE.source_version,
        captured_at=NOW,
        requested_item_ids=tuple(item.item_id for item in products),
        entries=tuple(
            ProductSnapshotEntry(item_id=item.item_id, status="ok", item=item)
            for item in products
        ),
    )


def _candidates(*, exclude_second: bool = False) -> CandidateSet:
    recalled = tuple(
        CandidateReference(item_id=item_id, sources=(CandidateSource.SEARCH,))
        for item_id in ("sku-1", "sku-2")
    )
    eligible_ids = ("sku-1",) if exclude_second else ("sku-1", "sku-2")
    excluded = (
        (
            FilteredCandidate(
                item_id="sku-2",
                sources=(CandidateSource.SEARCH,),
                snapshot_id="snapshot-unit",
                reasons=(
                    ExclusionReason(
                        code="brand_excluded",
                        field="brand",
                        expected="not Talon",
                        actual="Talon",
                    ),
                ),
            ),
        )
        if exclude_second
        else ()
    )
    return CandidateSet(
        status=CandidateSetStatus.READY,
        snapshot_id="snapshot-unit",
        recalled=recalled,
        eligible=tuple(
            FilteredCandidate(
                item_id=item_id,
                sources=(CandidateSource.SEARCH,),
                snapshot_id="snapshot-unit",
            )
            for item_id in eligible_ids
        ),
        excluded=excluded,
    )


def _ranking() -> RankingResult:
    return RankingResult(
        policy_version="ranking-unit-v1",
        snapshot_id="snapshot-unit",
        ranked=tuple(
            RankedCandidate(
                item_id=item_id,
                rank=index,
                base_rank=index,
                total_score=Decimal(score),
                evidence_completeness=Decimal("1"),
                components=(),
                used_features=(),
                missing_features=(),
                ignored_features=(),
                not_comparable_features=(),
                explanation_codes=("score",),
                brand_key="talon",
                model_key=item_id,
            )
            for index, (item_id, score) in enumerate(
                (("sku-1", "0.9"), ("sku-2", "0.8")), 1
            )
        ),
        diversity_applied=False,
        all_low_confidence=False,
        low_confidence_threshold=Decimal("0.5"),
    )


def _evidence(
    field: str,
    *,
    item_id: str = "sku-1",
    snapshot_id: str = "snapshot-unit",
    source_id: str | None = None,
    evidence_id: str | None = None,
) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=evidence_id or f"fact:{snapshot_id}:{item_id}:{field}",
        source_type=EvidenceSourceType.PRODUCT_FACT,
        source_id=source_id or f"{item_id}.{field}",
        item_id=item_id,
        snapshot_id=snapshot_id,
        title=f"{item_id} {field}",
    )


def _claim(
    claim_type: ClaimType,
    value: object,
    evidence: EvidenceRef,
    *,
    item_id: str = "sku-1",
    field: str | None = None,
) -> ResponseClaim:
    return ResponseClaim(
        claim_id=f"claim-{claim_type.value.replace('_', '-')}-{item_id}",
        claim_type=claim_type,
        item_id=item_id,
        field=field,
        value=value,
        evidence_ids=(evidence.evidence_id,),
    )


def _draft(
    *claims: ResponseClaim,
    evidence: tuple[EvidenceRef, ...] = (),
    answer: str = "基于本轮事实给出建议。",
) -> GroundedResponseDraft:
    return GroundedResponseDraft(
        payload=AiModelRecommendationPayload(
            answer=answer,
            candidates=[AiModelProductRef(item_id="sku-1", item_name="Phone sku-1")],
            recommendations=[
                AiModelRecommendationReason(
                    item_id="sku-1",
                    reason="符合当前需求",
                    evidence_ids=[item.evidence_id for item in evidence],
                )
            ],
            evidence=[item.to_payload() for item in evidence],
        ),
        claims=claims,
    )


def _verify(
    draft: GroundedResponseDraft,
    *,
    evidence: tuple[EvidenceRef, ...] = (),
    snapshot: ProductSnapshot | None = None,
    candidates: CandidateSet | None = None,
    comparison: ComparisonMatrix | None = None,
):
    return GroundingVerifier().verify(
        draft=draft,
        goal=ShoppingGoal(),
        snapshot=snapshot or _snapshot(),
        candidate_set=candidates or _candidates(),
        ranking=_ranking(),
        evidence=evidence,
        comparison=comparison,
    )


def test_stale_fact_and_old_evidence_lineage_fail_closed() -> None:
    stale = Freshness.from_observation(
        observed_at=NOW - timedelta(minutes=10),
        captured_at=NOW,
        max_age_seconds=60,
    )
    product = _product("sku-1").model_copy(
        update={"current_price": _fact(Decimal("499"), freshness=stale)}
    )
    old_evidence = _evidence("current_price", snapshot_id="snapshot-old")
    result = _verify(
        _draft(
            _claim(ClaimType.PRICE, Decimal("499"), old_evidence),
            evidence=(old_evidence,),
        ),
        evidence=(old_evidence,),
        snapshot=_snapshot(first=product),
    )

    assert VerificationErrorCode.FACT_UNAVAILABLE in result.error_codes
    assert VerificationErrorCode.SNAPSHOT_LINEAGE_MISMATCH in result.error_codes
    assert "claim-price-sku-1" not in result.verified_claim_ids


def test_claim_evidence_must_reference_the_correct_fact_field() -> None:
    forged = _evidence("stock", source_id="sku-1.stock")
    result = _verify(
        _draft(
            _claim(ClaimType.PRICE, Decimal("499"), forged),
            evidence=(forged,),
        ),
        evidence=(forged,),
    )

    assert VerificationErrorCode.EVIDENCE_METADATA_MISMATCH in result.error_codes


def test_payload_evidence_metadata_cannot_be_forged() -> None:
    domain = _evidence("current_price")
    payload = AiModelRecommendationPayload(
        answer="基于本轮事实给出建议。",
        candidates=[AiModelProductRef(item_id="sku-1", item_name="Phone sku-1")],
        recommendations=[
            AiModelRecommendationReason(
                item_id="sku-1",
                reason="符合当前需求",
                evidence_ids=[domain.evidence_id],
            )
        ],
        evidence=[
            AiModelEvidenceReference(
                evidence_id=domain.evidence_id,
                source_type="product_fact",
                source_id="sku-1.stock",
            )
        ],
    )

    result = _verify(
        GroundedResponseDraft(payload=payload),
        evidence=(domain,),
    )

    assert VerificationErrorCode.EVIDENCE_METADATA_MISMATCH in result.error_codes


@pytest.mark.parametrize(
    ("answer", "expected_code"),
    [
        ("保证这是最好的选择。", VerificationErrorCode.ABSOLUTE_CLAIM_FORBIDDEN),
        ("当前有满减优惠。", VerificationErrorCode.UNVERIFIED_PROMOTION),
        ("这款销量第一。", VerificationErrorCode.UNVERIFIED_SALES),
        ("价格是 499 元。", VerificationErrorCode.UNDECLARED_MATERIAL_CLAIM),
        ("库存充足。", VerificationErrorCode.UNDECLARED_MATERIAL_CLAIM),
        ("支持明日达。", VerificationErrorCode.UNDECLARED_MATERIAL_CLAIM),
    ],
)
def test_unstructured_high_risk_text_is_detected(
    answer: str,
    expected_code: VerificationErrorCode,
) -> None:
    result = _verify(_draft(answer=answer))

    assert expected_code in result.error_codes


def _matrix() -> ComparisonMatrix:
    refs = tuple(
        _evidence(
            "memory",
            item_id=item_id,
            source_id=f"{item_id}.specifications.memory",
            evidence_id=f"matrix:{item_id}:memory",
        )
        for item_id in ("sku-1", "sku-2")
    )
    return ComparisonMatrix(
        snapshot_id="snapshot-unit",
        ranking_policy_version="ranking-unit-v1",
        comparable=True,
        columns=(
            ComparisonColumn(item_id="sku-1", rank=1, item_name="Phone sku-1"),
            ComparisonColumn(item_id="sku-2", rank=2, item_name="Phone sku-2"),
        ),
        rows=(
            ComparisonRow(
                feature_key="memory",
                label="Memory",
                cells=tuple(
                    ComparisonCell(
                        item_id=item_id,
                        status=ComparisonCellStatus.KNOWN,
                        normalized_value="16 GB",
                        display_value="16 GB",
                        source_version=SOURCE.source_version,
                        freshness=FreshnessState.FRESH,
                        evidence_id=reference.evidence_id,
                    )
                    for item_id, reference in zip(("sku-1", "sku-2"), refs)
                ),
            ),
        ),
        evidence=refs,
    )


def test_a4_comparison_payload_cannot_change_a_matrix_cell() -> None:
    matrix = _matrix()
    products = {
        item_id: AiModelProductRef(item_id=item_id, item_name=f"Phone {item_id}")
        for item_id in ("sku-1", "sku-2")
    }
    payload = matrix.to_payload(answer="结构化比较", products=products)
    raw = payload.model_dump(mode="json")
    raw["rows"][0]["cells"]["memory"] = "32 GB"
    tampered = type(payload).model_validate(raw)

    result = _verify(
        GroundedResponseDraft(payload=tampered),
        evidence=matrix.evidence,
        comparison=matrix,
    )

    assert VerificationErrorCode.COMPARISON_CELL_MISMATCH in result.error_codes


def test_duplicate_evidence_ids_are_rejected() -> None:
    evidence = _evidence("current_price")
    result = _verify(
        _draft(
            _claim(ClaimType.PRICE, Decimal("499"), evidence),
            evidence=(evidence,),
        ),
        evidence=(evidence, evidence),
    )

    assert VerificationErrorCode.EVIDENCE_DUPLICATE in result.error_codes
    assert "claim-price-sku-1" not in result.verified_claim_ids


def test_claim_on_filtered_product_fails_even_when_payload_does_not_recommend_it() -> (
    None
):
    stock = _evidence("stock", item_id="sku-2")
    draft = GroundedResponseDraft(
        payload=AiModelAnswerPayload(answer="这里只保留通用建议。"),
        claims=(_claim(ClaimType.STOCK, 10, stock, item_id="sku-2"),),
    )

    result = _verify(
        draft,
        evidence=(stock,),
        candidates=_candidates(exclude_second=True),
    )

    assert VerificationErrorCode.HARD_FILTER_VIOLATION in result.error_codes


def test_composer_exception_is_called_once_and_never_leaks_to_fallback_or_trace() -> (
    None
):
    async def scenario() -> None:
        price = _evidence("current_price")
        invalid = _draft(
            _claim(ClaimType.PRICE, Decimal("1"), price),
            evidence=(price,),
        )
        calls = 0

        async def composer(_request):
            nonlocal calls
            calls += 1
            raise RuntimeError("private-composer-stack-detail")

        trace = AgentTraceContext.start(user_query="recommend a phone")
        outcome = await GroundingVerifier().verify_with_repair(
            draft=invalid,
            goal=ShoppingGoal(),
            snapshot=_snapshot(),
            candidate_set=_candidates(),
            ranking=_ranking(),
            evidence=(price,),
            comparison=None,
            composer=composer,
            trace_context=trace,
        )

        serialized = json.dumps(
            {
                "payload": outcome.final_payload.model_dump(mode="json"),
                "events": [event.to_record() for event in trace.events],
            },
            ensure_ascii=False,
            default=str,
        )
        assert calls == 1
        assert outcome.decision is GroundingDecision.FALLBACK
        assert VerificationErrorCode.REPAIR_FAILED in outcome.verification.error_codes
        assert "private-composer-stack-detail" not in serialized

    asyncio.run(scenario())


def test_all_a2_hard_constraint_scenarios_reject_a_simulated_violation() -> None:
    document = json.loads(
        (ROOT / "fixtures/evals/shopping_agent_scenarios.json").read_text(
            encoding="utf-8"
        )
    )
    scenarios = [
        item for item in document["scenarios"] if item["category"] == "hard_constraint"
    ]
    assert len(scenarios) >= 7
    undetected: list[str] = []
    for scenario in scenarios:
        target = scenario["hard_assertions"][0]["target"]
        if "price" in target:
            evidence = _evidence("current_price")
            claim = _claim(ClaimType.PRICE, Decimal("999999"), evidence)
            candidates = _candidates()
        elif "brand" in target:
            evidence = _evidence("brand")
            claim = _claim(ClaimType.BRAND, "forged-brand", evidence)
            candidates = _candidates()
        elif "spec" in target:
            evidence = _evidence("memory", source_id="sku-1.specifications.memory")
            claim = _claim(
                ClaimType.SPECIFICATION,
                "forged-spec",
                evidence,
                field="memory",
            )
            candidates = _candidates()
        elif "delivery" in target:
            evidence = _evidence("delivery")
            claim = _claim(
                ClaimType.DELIVERY,
                NOW + timedelta(days=30),
                evidence,
                field="estimated_delivery_at",
            )
            candidates = _candidates()
        else:
            evidence = _evidence("stock", item_id="sku-2")
            claim = _claim(ClaimType.STOCK, 10, evidence, item_id="sku-2")
            candidates = _candidates(exclude_second=True)
        result = _verify(
            GroundedResponseDraft(
                payload=AiModelAnswerPayload(answer="通用建议"),
                claims=(claim,),
            ),
            evidence=(evidence,),
            candidates=candidates,
        )
        if result.valid:
            undetected.append(scenario["scenario_id"])

    assert undetected == []
