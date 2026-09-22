"""Deterministic grounding verification and one-shot response repair."""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .agent_trace import (
    AgentTraceContext,
    AgentTraceEventType,
    AgentTraceStatus,
)
from .candidate_service import (
    CandidatePolicy,
    CandidateSet,
    apply_hard_filters,
)
from .comparison import (
    ComparisonCell,
    ComparisonCellStatus,
    ComparisonMatrix,
    EvidenceRef,
    EvidenceSourceType,
)
from .product_models import (
    FactStatus,
    FreshnessState,
    ProductFact,
    ProductSnapshot,
    ProductSnapshotItem,
    ProductSpecifications,
)
from .ranking import RankingResult
from .schemas import (
    AiModelComparisonPayload,
    AiModelFallbackPayload,
    AiModelRecommendationPayload,
    AiModelResponsePayload,
)
from .shopping_goal import ShoppingGoal


VerifierText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]
ClaimValue: TypeAlias = str | bool | int | Decimal | datetime


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClaimType(StrEnum):
    """Closed claim vocabulary accepted from the response composer."""

    CATEGORY = "category"
    BRAND = "brand"
    PRICE = "price"
    STOCK = "stock"
    DELIVERY = "delivery"
    RATING = "rating"
    REVIEW_COUNT = "review_count"
    SPECIFICATION = "specification"
    RANK = "rank"
    COMPARISON_CELL = "comparison_cell"
    PROMOTION = "promotion"
    SALES_VOLUME = "sales_volume"
    ABSOLUTE = "absolute"


class VerificationErrorCode(StrEnum):
    SNAPSHOT_LINEAGE_MISMATCH = "snapshot_lineage_mismatch"
    PRODUCT_NOT_IN_CANDIDATES = "product_not_in_candidates"
    PRODUCT_FACT_MISSING = "product_fact_missing"
    PRODUCT_NAME_MISMATCH = "product_name_mismatch"
    HARD_FILTER_VIOLATION = "hard_filter_violation"
    RANKING_RESULT_MISSING = "ranking_result_missing"
    EVIDENCE_DUPLICATE = "evidence_duplicate"
    EVIDENCE_NOT_FOUND = "evidence_not_found"
    EVIDENCE_ITEM_MISMATCH = "evidence_item_mismatch"
    EVIDENCE_METADATA_MISMATCH = "evidence_metadata_mismatch"
    FACT_UNAVAILABLE = "fact_unavailable"
    CATEGORY_MISMATCH = "category_mismatch"
    BRAND_MISMATCH = "brand_mismatch"
    PRICE_MISMATCH = "price_mismatch"
    STOCK_MISMATCH = "stock_mismatch"
    DELIVERY_MISMATCH = "delivery_mismatch"
    RATING_MISMATCH = "rating_mismatch"
    REVIEW_COUNT_MISMATCH = "review_count_mismatch"
    SPECIFICATION_MISMATCH = "specification_mismatch"
    RANK_MISMATCH = "rank_mismatch"
    COMPARISON_MATRIX_REQUIRED = "comparison_matrix_required"
    COMPARISON_CELL_MISMATCH = "comparison_cell_mismatch"
    UNVERIFIED_PROMOTION = "unverified_promotion"
    UNVERIFIED_SALES = "unverified_sales"
    ABSOLUTE_CLAIM_FORBIDDEN = "absolute_claim_forbidden"
    UNDECLARED_MATERIAL_CLAIM = "undeclared_material_claim"
    REPAIR_FAILED = "repair_failed"


class GroundingDecision(StrEnum):
    VERIFIED = "verified"
    REPAIRED = "repaired"
    FALLBACK = "fallback"


class ResponseClaim(_StrictModel):
    """One machine-verifiable material assertion from the composer."""

    claim_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
    )
    claim_type: ClaimType
    item_id: VerifierText
    field: VerifierText | None = None
    value: ClaimValue
    evidence_ids: tuple[VerifierText, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_shape(self) -> ResponseClaim:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("claim evidence IDs must be unique")
        field_required = {
            ClaimType.DELIVERY,
            ClaimType.SPECIFICATION,
            ClaimType.COMPARISON_CELL,
        }
        if self.claim_type in field_required and self.field is None:
            raise ValueError(f"{self.claim_type.value} claim requires field")
        return self


class GroundedResponseDraft(_StrictModel):
    """A4 response payload plus all material factual claims it renders."""

    schema_version: str = Field(default="v1", pattern=r"^v1$")
    payload: AiModelResponsePayload
    claims: tuple[ResponseClaim, ...] = Field(default_factory=tuple, max_length=128)

    @model_validator(mode="after")
    def validate_claim_ids(self) -> GroundedResponseDraft:
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim IDs must be unique")
        return self


class ClaimCheck(_StrictModel):
    """One deterministic rule result without prose or hidden reasoning."""

    check_id: VerifierText
    passed: bool
    code: VerificationErrorCode | None = None
    claim_id: VerifierText | None = None
    item_id: VerifierText | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> ClaimCheck:
        if self.passed == (self.code is not None):
            raise ValueError("failed checks require a code; passed checks forbid one")
        return self


class VerificationResult(_StrictModel):
    """Stable verification summary consumed by repair and tracing."""

    schema_version: str = Field(default="v1", pattern=r"^v1$")
    valid: bool
    checks: tuple[ClaimCheck, ...]
    error_codes: tuple[VerificationErrorCode, ...]
    verified_claim_ids: tuple[VerifierText, ...]

    @model_validator(mode="after")
    def validate_summary(self) -> VerificationResult:
        failed_codes = _unique(
            check.code for check in self.checks if check.code is not None
        )
        if self.error_codes != failed_codes:
            raise ValueError("error codes must match failed checks in stable order")
        if self.valid != (not self.error_codes):
            raise ValueError("valid flag must match error codes")
        return self


class RepairRequest(_StrictModel):
    """Bounded feedback supplied to the composer exactly once."""

    draft: GroundedResponseDraft
    error_codes: tuple[VerificationErrorCode, ...] = Field(min_length=1)
    failed_claim_ids: tuple[VerifierText, ...]


class GroundingOutcome(_StrictModel):
    """Final payload and proof of whether repair or fallback was used."""

    decision: GroundingDecision
    repair_count: int = Field(ge=0, le=1)
    final_payload: AiModelResponsePayload
    verification: VerificationResult

    @model_validator(mode="after")
    def validate_decision(self) -> GroundingOutcome:
        if self.decision is GroundingDecision.VERIFIED:
            if self.repair_count != 0 or not self.verification.valid:
                raise ValueError("verified outcome requires initial success")
        elif self.decision is GroundingDecision.REPAIRED:
            if self.repair_count != 1 or not self.verification.valid:
                raise ValueError("repaired outcome requires one successful repair")
        elif self.repair_count != 1 or self.verification.valid:
            raise ValueError("fallback requires one failed repair")
        return self


ResponseComposer = Callable[
    [RepairRequest],
    Awaitable[GroundedResponseDraft],
]


_PROMOTION_TERMS = ("优惠", "折扣", "满减", "券后", "到手价")
_SALES_TERMS = ("销量", "已售", "热销")
_ABSOLUTE_TERMS = ("绝对", "百分之百", "100%", "保证")
_STOCK_TERMS = ("现货", "库存充足", "库存还有")
_DELIVERY_TERMS = ("今日达", "明日达", "当天送达", "保证送达")
_PRICE_PATTERN = re.compile(r"(?:[¥￥]\s*\d|\d+(?:\.\d+)?\s*元)")


class GroundingVerifier:
    """Verify response facts against one sealed turn snapshot."""

    def __init__(self, candidate_policy: CandidatePolicy | None = None) -> None:
        self._candidate_policy = candidate_policy or CandidatePolicy()

    def verify(
        self,
        *,
        draft: GroundedResponseDraft,
        goal: ShoppingGoal,
        snapshot: ProductSnapshot,
        candidate_set: CandidateSet,
        ranking: RankingResult,
        evidence: Sequence[EvidenceRef],
        comparison: ComparisonMatrix | None,
    ) -> VerificationResult:
        """Return the same ordered checks for the same structured inputs."""

        checks: list[ClaimCheck] = []
        products = snapshot.items_by_id
        recalled_ids = {item.item_id for item in candidate_set.recalled}
        eligible_ids = {item.item_id for item in candidate_set.eligible}
        excluded_ids = {item.item_id for item in candidate_set.excluded}
        ranked_ids = {item.item_id for item in ranking.ranked}
        lineage_matches = (
            candidate_set.snapshot_id == snapshot.snapshot_id
            and ranking.snapshot_id == snapshot.snapshot_id
            and (comparison is None or comparison.snapshot_id == snapshot.snapshot_id)
        )
        _append_check(
            checks,
            "snapshot_lineage",
            lineage_matches,
            VerificationErrorCode.SNAPSHOT_LINEAGE_MISMATCH,
        )

        rechecked_eligible = self._rechecked_eligible(goal, snapshot, candidate_set)
        evidence_by_id, duplicate_evidence_ids = _evidence_index(evidence)
        _append_check(
            checks,
            "evidence_unique",
            not duplicate_evidence_ids,
            VerificationErrorCode.EVIDENCE_DUPLICATE,
        )
        for item in evidence:
            if item.source_type in {
                EvidenceSourceType.PRODUCT_FACT,
                EvidenceSourceType.RANKING_SCORE,
            }:
                _append_check(
                    checks,
                    f"evidence_lineage:{item.evidence_id}",
                    item.snapshot_id == snapshot.snapshot_id,
                    VerificationErrorCode.SNAPSHOT_LINEAGE_MISMATCH,
                    item_id=item.item_id,
                )

        for product in draft.payload.recommended_products():
            item_id = str(product.item_id)
            _append_check(
                checks,
                f"product_candidate:{item_id}",
                item_id in recalled_ids,
                VerificationErrorCode.PRODUCT_NOT_IN_CANDIDATES,
                item_id=item_id,
            )
            snapshot_item = products.get(item_id)
            _append_check(
                checks,
                f"product_snapshot:{item_id}",
                snapshot_item is not None,
                VerificationErrorCode.PRODUCT_FACT_MISSING,
                item_id=item_id,
            )
            passes_filter = (
                item_id in eligible_ids
                and item_id not in excluded_ids
                and item_id in rechecked_eligible
            )
            _append_check(
                checks,
                f"product_filter:{item_id}",
                passes_filter,
                VerificationErrorCode.HARD_FILTER_VIOLATION,
                item_id=item_id,
            )
            if isinstance(draft.payload, AiModelRecommendationPayload):
                _append_check(
                    checks,
                    f"product_ranking:{item_id}",
                    item_id in ranked_ids,
                    VerificationErrorCode.RANKING_RESULT_MISSING,
                    item_id=item_id,
                )
            if snapshot_item is not None:
                _append_check(
                    checks,
                    f"product_name:{item_id}",
                    _fact_matches(snapshot_item.name, product.item_name),
                    VerificationErrorCode.PRODUCT_NAME_MISMATCH,
                    item_id=item_id,
                )

        self._check_payload_evidence(
            draft,
            evidence_by_id,
            checks,
        )
        self._check_recommendation_evidence(
            draft,
            evidence_by_id,
            checks,
        )
        self._check_comparison_payload(draft, comparison, checks)

        failed_claim_ids: set[str] = set()
        for claim in draft.claims:
            before = len(checks)
            _append_check(
                checks,
                f"claim_candidate:{claim.claim_id}",
                claim.item_id in recalled_ids,
                VerificationErrorCode.PRODUCT_NOT_IN_CANDIDATES,
                claim_id=claim.claim_id,
                item_id=claim.item_id,
            )
            _append_check(
                checks,
                f"claim_filter:{claim.claim_id}",
                claim.item_id in eligible_ids
                and claim.item_id not in excluded_ids
                and claim.item_id in rechecked_eligible,
                VerificationErrorCode.HARD_FILTER_VIOLATION,
                claim_id=claim.claim_id,
                item_id=claim.item_id,
            )
            self._check_claim_evidence(
                claim,
                evidence_by_id,
                duplicate_evidence_ids,
                snapshot.snapshot_id,
                checks,
            )
            value_error = self._claim_value_error(
                claim,
                products=products,
                ranking=ranking,
                comparison=comparison,
            )
            _append_check(
                checks,
                f"claim_value:{claim.claim_id}",
                value_error is None,
                value_error,
                claim_id=claim.claim_id,
                item_id=claim.item_id,
            )
            if any(not check.passed for check in checks[before:]):
                failed_claim_ids.add(claim.claim_id)

        self._check_unstructured_material_claims(draft, checks)
        verified_claim_ids = tuple(
            claim.claim_id
            for claim in draft.claims
            if claim.claim_id not in failed_claim_ids
            and not any(
                not check.passed and check.claim_id == claim.claim_id
                for check in checks
            )
        )
        error_codes = _unique(check.code for check in checks if check.code is not None)
        return VerificationResult(
            valid=not error_codes,
            checks=tuple(checks),
            error_codes=error_codes,
            verified_claim_ids=verified_claim_ids,
        )

    async def verify_with_repair(
        self,
        *,
        draft: GroundedResponseDraft,
        goal: ShoppingGoal,
        snapshot: ProductSnapshot,
        candidate_set: CandidateSet,
        ranking: RankingResult,
        evidence: Sequence[EvidenceRef],
        comparison: ComparisonMatrix | None,
        composer: ResponseComposer,
        trace_context: AgentTraceContext | None = None,
    ) -> GroundingOutcome:
        """Verify, ask for one repair at most, then return a safe fallback."""

        initial = self.verify(
            draft=draft,
            goal=goal,
            snapshot=snapshot,
            candidate_set=candidate_set,
            ranking=ranking,
            evidence=evidence,
            comparison=comparison,
        )
        if initial.valid:
            outcome = GroundingOutcome(
                decision=GroundingDecision.VERIFIED,
                repair_count=0,
                final_payload=draft.payload,
                verification=initial,
            )
            self._record_trace(trace_context, outcome)
            return outcome

        request = RepairRequest(
            draft=draft,
            error_codes=initial.error_codes,
            failed_claim_ids=_failed_claim_ids(initial),
        )
        repaired_draft = draft
        try:
            candidate = composer(request)
            if not inspect.isawaitable(candidate):
                raise TypeError("response composer must be async")
            repaired_draft = await candidate
            if not isinstance(repaired_draft, GroundedResponseDraft):
                raise TypeError("response composer returned an invalid draft")
            final_verification = self.verify(
                draft=repaired_draft,
                goal=goal,
                snapshot=snapshot,
                candidate_set=candidate_set,
                ranking=ranking,
                evidence=evidence,
                comparison=comparison,
            )
        except Exception:
            final_verification = _append_result_error(
                initial,
                VerificationErrorCode.REPAIR_FAILED,
            )

        if final_verification.valid:
            outcome = GroundingOutcome(
                decision=GroundingDecision.REPAIRED,
                repair_count=1,
                final_payload=repaired_draft.payload,
                verification=final_verification,
            )
        else:
            outcome = GroundingOutcome(
                decision=GroundingDecision.FALLBACK,
                repair_count=1,
                final_payload=self._fallback(
                    repaired_draft,
                    final_verification,
                    snapshot,
                    evidence,
                ),
                verification=final_verification,
            )
        self._record_trace(trace_context, outcome)
        return outcome

    def _rechecked_eligible(
        self,
        goal: ShoppingGoal,
        snapshot: ProductSnapshot,
        candidate_set: CandidateSet,
    ) -> set[str]:
        if not candidate_set.recalled:
            return set()
        result = apply_hard_filters(
            goal,
            snapshot,
            policy=self._candidate_policy,
            candidates=candidate_set.recalled,
        )
        return {item.item_id for item in result.eligible}

    @staticmethod
    def _check_payload_evidence(
        draft: GroundedResponseDraft,
        evidence_by_id: Mapping[str, EvidenceRef],
        checks: list[ClaimCheck],
    ) -> None:
        for item in draft.payload.evidence:
            domain = evidence_by_id.get(str(item.evidence_id))
            _append_check(
                checks,
                f"payload_evidence:{item.evidence_id}",
                domain is not None,
                VerificationErrorCode.EVIDENCE_NOT_FOUND,
            )
            if domain is None:
                continue
            expected_source_type = (
                "policy"
                if domain.source_type is EvidenceSourceType.RANKING_SCORE
                else domain.source_type.value
            )
            metadata_matches = (
                item.source_type == expected_source_type
                and item.source_id == domain.source_id
            )
            _append_check(
                checks,
                f"payload_evidence_metadata:{item.evidence_id}",
                metadata_matches,
                VerificationErrorCode.EVIDENCE_METADATA_MISMATCH,
                item_id=domain.item_id,
            )

    @staticmethod
    def _check_recommendation_evidence(
        draft: GroundedResponseDraft,
        evidence_by_id: Mapping[str, EvidenceRef],
        checks: list[ClaimCheck],
    ) -> None:
        if not isinstance(draft.payload, AiModelRecommendationPayload):
            return
        for recommendation in draft.payload.recommendations:
            item_id = str(recommendation.item_id)
            _append_check(
                checks,
                f"recommendation_evidence_required:{item_id}",
                bool(recommendation.evidence_ids),
                VerificationErrorCode.EVIDENCE_NOT_FOUND,
                item_id=item_id,
            )
            for evidence_id in recommendation.evidence_ids:
                domain = evidence_by_id.get(str(evidence_id))
                _append_check(
                    checks,
                    f"recommendation_evidence:{item_id}:{evidence_id}",
                    domain is not None,
                    VerificationErrorCode.EVIDENCE_NOT_FOUND,
                    item_id=item_id,
                )
                if domain is not None:
                    _append_check(
                        checks,
                        f"recommendation_evidence_item:{item_id}:{evidence_id}",
                        domain.item_id == item_id,
                        VerificationErrorCode.EVIDENCE_ITEM_MISMATCH,
                        item_id=item_id,
                    )

    @staticmethod
    def _check_comparison_payload(
        draft: GroundedResponseDraft,
        comparison: ComparisonMatrix | None,
        checks: list[ClaimCheck],
    ) -> None:
        payload = draft.payload
        if not isinstance(payload, AiModelComparisonPayload):
            return
        _append_check(
            checks,
            "comparison_matrix_present",
            comparison is not None,
            VerificationErrorCode.COMPARISON_MATRIX_REQUIRED,
        )
        if comparison is None:
            return
        expected_columns = [(row.feature_key, row.label) for row in comparison.rows]
        actual_columns = [(str(item.key), str(item.label)) for item in payload.columns]
        _append_check(
            checks,
            "comparison_columns",
            actual_columns == expected_columns,
            VerificationErrorCode.COMPARISON_CELL_MISMATCH,
        )
        expected_rows = {
            column.item_id: {
                row.feature_key: _comparison_cell_text(row.cells[index])
                for row in comparison.rows
            }
            for index, column in enumerate(comparison.columns)
        }
        actual_rows = {
            str(row.item_id): {str(key): value for key, value in row.cells.items()}
            for row in payload.rows
        }
        _append_check(
            checks,
            "comparison_cells",
            actual_rows == expected_rows,
            VerificationErrorCode.COMPARISON_CELL_MISMATCH,
        )

    @staticmethod
    def _check_claim_evidence(
        claim: ResponseClaim,
        evidence_by_id: Mapping[str, EvidenceRef],
        duplicate_evidence_ids: set[str],
        snapshot_id: str,
        checks: list[ClaimCheck],
    ) -> None:
        for evidence_id in claim.evidence_ids:
            _append_check(
                checks,
                f"claim_evidence_unique:{claim.claim_id}:{evidence_id}",
                evidence_id not in duplicate_evidence_ids,
                VerificationErrorCode.EVIDENCE_DUPLICATE,
                claim_id=claim.claim_id,
                item_id=claim.item_id,
            )
            domain = evidence_by_id.get(evidence_id)
            _append_check(
                checks,
                f"claim_evidence:{claim.claim_id}:{evidence_id}",
                domain is not None,
                VerificationErrorCode.EVIDENCE_NOT_FOUND,
                claim_id=claim.claim_id,
                item_id=claim.item_id,
            )
            if domain is not None:
                _append_check(
                    checks,
                    f"claim_evidence_item:{claim.claim_id}:{evidence_id}",
                    domain.item_id == claim.item_id,
                    VerificationErrorCode.EVIDENCE_ITEM_MISMATCH,
                    claim_id=claim.claim_id,
                    item_id=claim.item_id,
                )
                if domain.source_type in {
                    EvidenceSourceType.PRODUCT_FACT,
                    EvidenceSourceType.RANKING_SCORE,
                }:
                    _append_check(
                        checks,
                        f"claim_evidence_lineage:{claim.claim_id}:{evidence_id}",
                        domain.snapshot_id == snapshot_id,
                        VerificationErrorCode.SNAPSHOT_LINEAGE_MISMATCH,
                        claim_id=claim.claim_id,
                        item_id=claim.item_id,
                    )
                _append_check(
                    checks,
                    f"claim_evidence_source:{claim.claim_id}:{evidence_id}",
                    _claim_evidence_matches(claim, domain),
                    VerificationErrorCode.EVIDENCE_METADATA_MISMATCH,
                    claim_id=claim.claim_id,
                    item_id=claim.item_id,
                )

    @staticmethod
    def _claim_value_error(
        claim: ResponseClaim,
        *,
        products: Mapping[str, ProductSnapshotItem],
        ranking: RankingResult,
        comparison: ComparisonMatrix | None,
    ) -> VerificationErrorCode | None:
        if claim.claim_type is ClaimType.PROMOTION:
            return VerificationErrorCode.UNVERIFIED_PROMOTION
        if claim.claim_type is ClaimType.SALES_VOLUME:
            return VerificationErrorCode.UNVERIFIED_SALES
        if claim.claim_type is ClaimType.ABSOLUTE:
            return VerificationErrorCode.ABSOLUTE_CLAIM_FORBIDDEN
        product = products.get(claim.item_id)
        if product is None:
            return VerificationErrorCode.PRODUCT_FACT_MISSING
        if claim.claim_type is ClaimType.CATEGORY:
            return _fact_error(
                product.category,
                claim.value,
                VerificationErrorCode.CATEGORY_MISMATCH,
            )
        if claim.claim_type is ClaimType.BRAND:
            return _fact_error(
                product.brand,
                claim.value,
                VerificationErrorCode.BRAND_MISMATCH,
            )
        if claim.claim_type is ClaimType.PRICE:
            return _fact_error(
                product.current_price,
                claim.value,
                VerificationErrorCode.PRICE_MISMATCH,
            )
        if claim.claim_type is ClaimType.STOCK:
            return _fact_error(
                product.stock,
                claim.value,
                VerificationErrorCode.STOCK_MISMATCH,
            )
        if claim.claim_type is ClaimType.RATING:
            return _fact_error(
                product.rating,
                claim.value,
                VerificationErrorCode.RATING_MISMATCH,
            )
        if claim.claim_type is ClaimType.REVIEW_COUNT:
            return _fact_error(
                product.review_count,
                claim.value,
                VerificationErrorCode.REVIEW_COUNT_MISMATCH,
            )
        if claim.claim_type is ClaimType.DELIVERY:
            if not _fact_usable(product.delivery):
                return VerificationErrorCode.FACT_UNAVAILABLE
            delivery = product.delivery.value
            if delivery is None or not hasattr(delivery, str(claim.field)):
                return VerificationErrorCode.DELIVERY_MISMATCH
            return (
                None
                if _values_equal(getattr(delivery, str(claim.field)), claim.value)
                else VerificationErrorCode.DELIVERY_MISMATCH
            )
        if claim.claim_type is ClaimType.SPECIFICATION:
            if not _fact_usable(product.specifications):
                return VerificationErrorCode.FACT_UNAVAILABLE
            specifications = product.specifications.value
            if not isinstance(specifications, ProductSpecifications):
                return VerificationErrorCode.FACT_UNAVAILABLE
            values = {item.key: item.value for item in specifications.values}
            return (
                None
                if claim.field in values
                and _values_equal(values[str(claim.field)], claim.value)
                else VerificationErrorCode.SPECIFICATION_MISMATCH
            )
        if claim.claim_type is ClaimType.RANK:
            ranked = next(
                (item for item in ranking.ranked if item.item_id == claim.item_id),
                None,
            )
            return (
                None
                if ranked is not None and _values_equal(ranked.rank, claim.value)
                else VerificationErrorCode.RANK_MISMATCH
            )
        if comparison is None:
            return VerificationErrorCode.COMPARISON_MATRIX_REQUIRED
        cell = _comparison_cell(comparison, claim.item_id, str(claim.field))
        if cell is None or cell.status is not ComparisonCellStatus.KNOWN:
            return VerificationErrorCode.COMPARISON_CELL_MISMATCH
        if not _values_equal(cell.display_value, claim.value):
            return VerificationErrorCode.COMPARISON_CELL_MISMATCH
        if cell.evidence_id not in claim.evidence_ids:
            return VerificationErrorCode.COMPARISON_CELL_MISMATCH
        return None

    @staticmethod
    def _check_unstructured_material_claims(
        draft: GroundedResponseDraft,
        checks: list[ClaimCheck],
    ) -> None:
        text_parts = [draft.payload.answer]
        if isinstance(draft.payload, AiModelRecommendationPayload):
            text_parts.extend(item.reason for item in draft.payload.recommendations)
        text = " ".join(text_parts)
        claim_types = {claim.claim_type for claim in draft.claims}
        for name, present, code in (
            (
                "promotion_text",
                any(term in text for term in _PROMOTION_TERMS),
                VerificationErrorCode.UNVERIFIED_PROMOTION,
            ),
            (
                "sales_text",
                any(term in text for term in _SALES_TERMS),
                VerificationErrorCode.UNVERIFIED_SALES,
            ),
            (
                "absolute_text",
                any(term in text for term in _ABSOLUTE_TERMS),
                VerificationErrorCode.ABSOLUTE_CLAIM_FORBIDDEN,
            ),
        ):
            _append_check(checks, name, not present, code)
        material_without_claim = (
            (
                _PRICE_PATTERN.search(text) is not None
                and ClaimType.PRICE not in claim_types
            )
            or (
                any(term in text for term in _STOCK_TERMS)
                and ClaimType.STOCK not in claim_types
            )
            or (
                any(term in text for term in _DELIVERY_TERMS)
                and ClaimType.DELIVERY not in claim_types
            )
        )
        _append_check(
            checks,
            "material_claim_declaration",
            not material_without_claim,
            VerificationErrorCode.UNDECLARED_MATERIAL_CLAIM,
        )

    @staticmethod
    def _fallback(
        draft: GroundedResponseDraft,
        result: VerificationResult,
        snapshot: ProductSnapshot,
        evidence: Sequence[EvidenceRef],
    ) -> AiModelFallbackPayload:
        verified = set(result.verified_claim_ids)
        safe_claims = [claim for claim in draft.claims if claim.claim_id in verified]
        facts = [
            _render_verified_claim(claim, snapshot.items_by_id)
            for claim in safe_claims[:3]
        ]
        facts = [fact for fact in facts if fact is not None]
        answer = (
            f"部分结果未通过事实校验。已验证：{'；'.join(facts)}。"
            "建议刷新商品信息后重试。"
            if facts
            else "本轮商品信息无法完成可靠核验，建议刷新商品信息后重试。"
        )
        safe_evidence_ids = {
            evidence_id for claim in safe_claims for evidence_id in claim.evidence_ids
        }
        evidence_by_id, _ = _evidence_index(evidence)
        payload_evidence = [
            evidence_by_id[evidence_id].to_payload()
            for evidence_id in safe_evidence_ids
            if evidence_id in evidence_by_id
        ]
        payload_evidence.sort(key=lambda item: str(item.evidence_id))
        return AiModelFallbackPayload(
            answer=answer,
            reason_code="grounding_verification_failed",
            evidence=payload_evidence,
        )

    @staticmethod
    def _record_trace(
        trace: AgentTraceContext | None,
        outcome: GroundingOutcome,
    ) -> None:
        if trace is None:
            return
        event = trace.begin_event(
            AgentTraceEventType.VERIFY,
            stage="grounding",
            summary={
                "check_count": len(outcome.verification.checks),
                "failed_check_count": sum(
                    not check.passed for check in outcome.verification.checks
                ),
                "error_codes": [
                    code.value for code in outcome.verification.error_codes
                ],
                "verified_claim_count": len(outcome.verification.verified_claim_ids),
                "repair_count": outcome.repair_count,
                "decision": outcome.decision.value,
            },
        )
        event.finish(
            AgentTraceStatus.ERROR
            if outcome.decision is GroundingDecision.FALLBACK
            else AgentTraceStatus.SUCCESS,
            error=(
                "grounding_verification_failed"
                if outcome.decision is GroundingDecision.FALLBACK
                else None
            ),
        )


def _append_check(
    checks: list[ClaimCheck],
    check_id: str,
    passed: bool,
    code: VerificationErrorCode | None,
    *,
    claim_id: str | None = None,
    item_id: str | None = None,
) -> None:
    checks.append(
        ClaimCheck(
            check_id=check_id,
            passed=passed,
            code=None if passed else code,
            claim_id=claim_id,
            item_id=item_id,
        )
    )


def _evidence_index(
    evidence: Sequence[EvidenceRef],
) -> tuple[dict[str, EvidenceRef], set[str]]:
    indexed: dict[str, EvidenceRef] = {}
    duplicate_ids: set[str] = set()
    for item in evidence:
        if item.evidence_id in indexed:
            duplicate_ids.add(item.evidence_id)
        else:
            indexed[item.evidence_id] = item
    return indexed, duplicate_ids


def _fact_usable(fact: ProductFact[Any]) -> bool:
    return (
        fact.status is FactStatus.KNOWN
        and fact.freshness.state is FreshnessState.FRESH
        and fact.value is not None
    )


def _claim_evidence_matches(claim: ResponseClaim, evidence: EvidenceRef) -> bool:
    if claim.claim_type is ClaimType.RANK:
        return evidence.source_type is EvidenceSourceType.RANKING_SCORE
    if claim.claim_type in {
        ClaimType.PROMOTION,
        ClaimType.SALES_VOLUME,
        ClaimType.ABSOLUTE,
    }:
        return True
    if evidence.source_type is not EvidenceSourceType.PRODUCT_FACT:
        return False
    field_by_type = {
        ClaimType.CATEGORY: "category",
        ClaimType.BRAND: "brand",
        ClaimType.PRICE: "current_price",
        ClaimType.STOCK: "stock",
        ClaimType.DELIVERY: "delivery",
        ClaimType.RATING: "rating",
        ClaimType.REVIEW_COUNT: "review_count",
        ClaimType.SPECIFICATION: f"specifications.{claim.field}",
        ClaimType.COMPARISON_CELL: str(claim.field),
    }
    expected = field_by_type[claim.claim_type]
    source_id = evidence.source_id
    return source_id == f"{claim.item_id}.{expected}" or source_id.endswith(
        f".{expected}"
    )


def _fact_matches(fact: ProductFact[Any], value: object) -> bool:
    return _fact_usable(fact) and _values_equal(fact.value, value)


def _fact_error(
    fact: ProductFact[Any],
    value: object,
    mismatch: VerificationErrorCode,
) -> VerificationErrorCode | None:
    if not _fact_usable(fact):
        return VerificationErrorCode.FACT_UNAVAILABLE
    return None if _values_equal(fact.value, value) else mismatch


def _values_equal(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (Decimal, int)) and isinstance(right, (Decimal, int)):
        try:
            return Decimal(str(left)) == Decimal(str(right))
        except InvalidOperation:
            return False
    if isinstance(left, datetime) and isinstance(right, datetime):
        return left == right
    return str(left).strip() == str(right).strip()


def _comparison_cell(
    matrix: ComparisonMatrix,
    item_id: str,
    feature_key: str,
) -> ComparisonCell | None:
    for row in matrix.rows:
        if row.feature_key != feature_key:
            continue
        return next((cell for cell in row.cells if cell.item_id == item_id), None)
    return None


def _comparison_cell_text(cell: ComparisonCell) -> str:
    if cell.status is ComparisonCellStatus.KNOWN:
        return str(cell.display_value)
    if cell.status is ComparisonCellStatus.NOT_APPLICABLE:
        return "不适用"
    if cell.status is ComparisonCellStatus.ERROR:
        return "数据异常"
    return "未知"


def _render_verified_claim(
    claim: ResponseClaim,
    products: Mapping[str, ProductSnapshotItem],
) -> str | None:
    product = products.get(claim.item_id)
    if product is None or not _fact_usable(product.name):
        return None
    name = str(product.name.value)
    value = (
        claim.value.isoformat() if isinstance(claim.value, datetime) else claim.value
    )
    if claim.claim_type is ClaimType.PRICE:
        currency = product.currency.value if _fact_usable(product.currency) else ""
        return f"{name} 当前价格为 {value} {currency}".strip()
    if claim.claim_type is ClaimType.STOCK:
        return f"{name} 当前库存为 {value}"
    if claim.claim_type is ClaimType.CATEGORY:
        return f"{name} 的品类为 {value}"
    if claim.claim_type is ClaimType.BRAND:
        return f"{name} 的品牌为 {value}"
    if claim.claim_type is ClaimType.DELIVERY:
        return f"{name} 配送字段 {claim.field} 为 {value}"
    if claim.claim_type is ClaimType.RATING:
        return f"{name} 当前评分为 {value}"
    if claim.claim_type is ClaimType.REVIEW_COUNT:
        return f"{name} 当前评论数为 {value}"
    if claim.claim_type is ClaimType.SPECIFICATION:
        return f"{name} 的 {claim.field} 为 {value}"
    if claim.claim_type is ClaimType.RANK:
        return f"{name} 当前排序为第 {value} 位"
    if claim.claim_type is ClaimType.COMPARISON_CELL:
        return f"{name} 的比较字段 {claim.field} 为 {value}"
    return None


def _failed_claim_ids(result: VerificationResult) -> tuple[str, ...]:
    return _unique(
        check.claim_id
        for check in result.checks
        if not check.passed and check.claim_id is not None
    )


def _append_result_error(
    result: VerificationResult,
    code: VerificationErrorCode,
) -> VerificationResult:
    checks = (
        *result.checks,
        ClaimCheck(
            check_id="repair_execution",
            passed=False,
            code=code,
        ),
    )
    return VerificationResult(
        valid=False,
        checks=checks,
        error_codes=_unique((*result.error_codes, code)),
        verified_claim_ids=result.verified_claim_ids,
    )


def _unique(values: Iterable[Any]) -> tuple[Any, ...]:
    ordered: list[Any] = []
    seen: set[Any] = set()
    for value in values:
        if value not in seen:
            ordered.append(value)
            seen.add(value)
    return tuple(ordered)


__all__ = [
    "ClaimCheck",
    "ClaimType",
    "GroundedResponseDraft",
    "GroundingDecision",
    "GroundingOutcome",
    "GroundingVerifier",
    "RepairRequest",
    "ResponseClaim",
    "VerificationErrorCode",
    "VerificationResult",
]
