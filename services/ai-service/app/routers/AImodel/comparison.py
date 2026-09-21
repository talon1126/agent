"""Evidence-closed product comparison and deterministic review insights."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

import httpx
import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from .feature_normalizer import (
    FeatureProfileRegistry,
    NormalizedProductFeatures,
    NormalizedValue,
)
from .product_models import FreshnessState, ProductSnapshotItem
from .ranking import RankingResult, ScoreComponent
from .schemas import (
    AiModelComparisonColumn,
    AiModelComparisonPayload,
    AiModelComparisonRow,
    AiModelEvidenceReference,
    AiModelProductRef,
    AiModelRecommendationReason,
)


DEFAULT_COMPARISON_POLICY_PATH = Path(__file__).with_name("comparison_policy.yaml")
ComparisonText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]
_INSTRUCTION_PATTERNS = (
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions?", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"(?:call|invoke|use)\s+(?:a\s+)?tool", re.IGNORECASE),
    re.compile(r"delete[_\s-]?cart", re.IGNORECASE),
    re.compile(r"忽略(?:以上|之前|前面).{0,12}(?:指令|提示)", re.IGNORECASE),
    re.compile(r"(?:调用|使用).{0,12}(?:工具|函数)", re.IGNORECASE),
)


class ComparisonConfigError(ValueError):
    """Comparison policy cannot be loaded safely."""


class ComparisonInputError(ValueError):
    """Comparison input violates the C1-C4 handoff contract."""


class ComparisonCellStatus(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    ERROR = "error"
    NOT_APPLICABLE = "not_applicable"


class EvidenceSourceType(StrEnum):
    PRODUCT_FACT = "product_fact"
    REVIEW = "review"
    RANKING_SCORE = "ranking_score"


class InsightKind(StrEnum):
    PRO = "pro"
    CON = "con"
    CONTROVERSY = "controversy"


class InsightPrevalence(StrEnum):
    MAJORITY = "majority"
    MINORITY = "minority"
    CONTROVERSY = "controversy"


class ReviewInsightStatus(StrEnum):
    READY = "ready"
    LOW_SAMPLE = "low_sample"
    NO_REVIEWS = "no_reviews"


class ReviewTopicPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: ComparisonText
    keywords: tuple[ComparisonText, ...] = Field(min_length=1)

    @field_validator("keywords")
    @classmethod
    def unique_keywords(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = [keyword.casefold() for keyword in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("topic keywords must be unique")
        return value


class ComparisonPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"
    policy_version: ComparisonText
    minimum_review_sample: int = Field(ge=2, le=10_000)
    minimum_topic_mentions: int = Field(ge=1, le=10_000)
    majority_ratio: Decimal = Field(gt=Decimal("0.5"), le=1)
    controversy_minimum_each: int = Field(ge=1, le=10_000)
    topics: dict[ComparisonText, ReviewTopicPolicy] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        if self.minimum_topic_mentions > self.minimum_review_sample:
            raise ValueError("minimum topic mentions cannot exceed sample threshold")
        return self


def load_comparison_policy(
    path: str | Path = DEFAULT_COMPARISON_POLICY_PATH,
) -> ComparisonPolicy:
    policy_path = Path(path)
    try:
        payload = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        return ComparisonPolicy.model_validate(payload)
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
        raise ComparisonConfigError(
            f"cannot load comparison policy: {policy_path}"
        ) from exc


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: ComparisonText
    source_type: EvidenceSourceType
    source_id: ComparisonText
    item_id: ComparisonText
    snapshot_id: ComparisonText | None = None
    review_ids: tuple[ComparisonText, ...] = ()
    title: ComparisonText | None = None

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        if self.source_type is EvidenceSourceType.PRODUCT_FACT:
            if self.snapshot_id is None or self.review_ids:
                raise ValueError("product facts require only snapshot lineage")
        elif self.source_type is EvidenceSourceType.REVIEW:
            if self.snapshot_id is not None or len(self.review_ids) != 1:
                raise ValueError("review evidence requires exactly one review ID")
            if self.source_id != self.review_ids[0]:
                raise ValueError("review source ID must equal its review ID")
        elif self.snapshot_id is None or self.review_ids:
            raise ValueError("ranking evidence requires only snapshot lineage")
        return self

    def to_payload(self) -> AiModelEvidenceReference:
        return AiModelEvidenceReference(
            evidence_id=self.evidence_id,
            source_type=(
                "policy"
                if self.source_type is EvidenceSourceType.RANKING_SCORE
                else self.source_type.value
            ),
            source_id=self.source_id,
            title=self.title,
        )


class ComparisonColumn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: ComparisonText
    rank: int = Field(ge=1, le=100)
    item_name: ComparisonText


class ComparisonCell(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: ComparisonText
    status: ComparisonCellStatus
    normalized_value: NormalizedValue | None = None
    display_value: ComparisonText | None = None
    unit: ComparisonText | None = None
    source_version: ComparisonText | None = None
    freshness: FreshnessState | None = None
    evidence_id: ComparisonText | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.status is ComparisonCellStatus.KNOWN:
            if self.normalized_value is None or self.display_value is None:
                raise ValueError("known comparison cells require values")
        elif self.normalized_value is not None or self.display_value is not None:
            raise ValueError("non-known comparison cells cannot carry values")
        if self.status is ComparisonCellStatus.NOT_APPLICABLE:
            if any(
                value is not None
                for value in (
                    self.source_version,
                    self.freshness,
                    self.evidence_id,
                )
            ):
                raise ValueError("not-applicable cells cannot claim fact lineage")
        elif any(
            value is None
            for value in (self.source_version, self.freshness, self.evidence_id)
        ):
            raise ValueError("fact cells require source, freshness, and evidence")
        return self


class ComparisonRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feature_key: ComparisonText
    label: ComparisonText
    cells: tuple[ComparisonCell, ...] = Field(min_length=2, max_length=5)


class ComparisonMatrix(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"
    snapshot_id: ComparisonText
    ranking_policy_version: ComparisonText
    comparable: bool
    incompatibility_code: ComparisonText | None = None
    columns: tuple[ComparisonColumn, ...] = Field(min_length=2, max_length=5)
    rows: tuple[ComparisonRow, ...] = Field(min_length=1, max_length=64)
    evidence: tuple[EvidenceRef, ...]

    @model_validator(mode="after")
    def validate_matrix(self) -> Self:
        item_ids = tuple(column.item_id for column in self.columns)
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("comparison columns must be unique")
        if self.comparable == (self.incompatibility_code is not None):
            raise ValueError("incompatibility code must match comparability")
        row_keys = [row.feature_key for row in self.rows]
        if len(row_keys) != len(set(row_keys)):
            raise ValueError("comparison rows must be unique")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence IDs must be unique")
        known_evidence = {item.evidence_id: item for item in self.evidence}
        for reference in self.evidence:
            if reference.item_id not in item_ids:
                raise ValueError("comparison evidence item must be a matrix column")
            if (
                reference.source_type
                in {
                    EvidenceSourceType.PRODUCT_FACT,
                    EvidenceSourceType.RANKING_SCORE,
                }
                and reference.snapshot_id != self.snapshot_id
            ):
                raise ValueError("comparison evidence must use the matrix snapshot")
        for row in self.rows:
            if tuple(cell.item_id for cell in row.cells) != item_ids:
                raise ValueError("comparison cells must follow column order")
            cell_evidence_ids = {
                cell.evidence_id for cell in row.cells if cell.evidence_id is not None
            }
            if not cell_evidence_ids <= set(known_evidence):
                raise ValueError("comparison cell evidence must resolve")
            for cell in row.cells:
                if cell.evidence_id is None:
                    continue
                reference = known_evidence[cell.evidence_id]
                if (
                    reference.source_type is not EvidenceSourceType.PRODUCT_FACT
                    or reference.item_id != cell.item_id
                ):
                    raise ValueError("comparison cells require matching fact evidence")
        return self

    def to_payload(
        self,
        *,
        answer: str,
        products: Mapping[str, AiModelProductRef],
    ) -> AiModelComparisonPayload:
        ordered_products = [products[column.item_id] for column in self.columns]
        return AiModelComparisonPayload(
            answer=answer,
            products=ordered_products,
            columns=[
                AiModelComparisonColumn(key=row.feature_key, label=row.label)
                for row in self.rows
            ],
            rows=[
                AiModelComparisonRow(
                    item_id=column.item_id,
                    cells={
                        row.feature_key: _payload_cell(row.cells[index])
                        for row in self.rows
                    },
                )
                for index, column in enumerate(self.columns)
            ],
            evidence=[item.to_payload() for item in self.evidence],
        )


class ReviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: ComparisonText
    item_id: ComparisonText
    rating: int = Field(ge=1, le=5)
    title: ComparisonText
    content: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
    ]
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("review timestamps must include a timezone")
        return value


class ReviewCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: ComparisonText
    reported_review_count: int = Field(ge=0)
    average_rating: Decimal = Field(ge=0, le=5)
    reviews: tuple[ReviewRecord, ...] = Field(max_length=100)
    source_version: ComparisonText
    captured_at: datetime

    @field_validator("captured_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("review capture time must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_reviews(self) -> Self:
        review_ids = [review.review_id for review in self.reviews]
        if len(review_ids) != len(set(review_ids)):
            raise ValueError("review IDs must be unique")
        if any(review.item_id != self.item_id for review in self.reviews):
            raise ValueError("review item IDs must match collection item")
        if self.reported_review_count < len(self.reviews):
            raise ValueError("reported review count cannot be smaller than batch")
        return self


class SanitizedReviewContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    security_flags: tuple[Literal["review_prompt_injection"], ...] = ()


def sanitize_review_content(content: str) -> SanitizedReviewContent:
    safe_lines: list[str] = []
    flagged = False
    for line in content.splitlines():
        if any(pattern.search(line) for pattern in _INSTRUCTION_PATTERNS):
            flagged = True
            continue
        if stripped := line.strip():
            safe_lines.append(stripped)
    return SanitizedReviewContent(
        text="\n".join(safe_lines),
        security_flags=("review_prompt_injection",) if flagged else (),
    )


class ReviewInsight(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    insight_id: ComparisonText
    item_id: ComparisonText
    topic_code: ComparisonText
    label: ComparisonText
    kind: InsightKind
    prevalence: InsightPrevalence
    summary: ComparisonText
    positive_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    review_ids: tuple[ComparisonText, ...] = Field(min_length=1)
    evidence_ids: tuple[ComparisonText, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.kind is InsightKind.PRO and self.positive_count < 1:
            raise ValueError("pro insight requires positive reviews")
        if self.kind is InsightKind.CON and self.negative_count < 1:
            raise ValueError("con insight requires negative reviews")
        if self.kind is InsightKind.CONTROVERSY and (
            self.positive_count < 1 or self.negative_count < 1
        ):
            raise ValueError("controversy requires both review directions")
        if len(self.review_ids) != len(set(self.review_ids)):
            raise ValueError("insight review IDs must be unique")
        if len(self.review_ids) != len(self.evidence_ids):
            raise ValueError("each review ID requires one evidence ID")
        return self


class ReviewInsightReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"
    policy_version: ComparisonText
    item_id: ComparisonText
    status: ReviewInsightStatus
    sample_count: int = Field(ge=0)
    reported_review_count: int = Field(ge=0)
    minimum_sample_count: int = Field(ge=1)
    insights: tuple[ReviewInsight, ...]
    evidence: tuple[EvidenceRef, ...]
    security_flags: tuple[Literal["review_prompt_injection"], ...] = ()

    @model_validator(mode="after")
    def validate_evidence_closure(self) -> Self:
        insight_ids = [insight.insight_id for insight in self.insights]
        if len(insight_ids) != len(set(insight_ids)):
            raise ValueError("review insight IDs must be unique")
        if any(insight.item_id != self.item_id for insight in self.insights):
            raise ValueError("review insight item must match its report")
        if any(
            evidence.source_type is not EvidenceSourceType.REVIEW
            or evidence.item_id != self.item_id
            for evidence in self.evidence
        ):
            raise ValueError("review report evidence must belong to its item")
        review_ids = {
            review_id for evidence in self.evidence for review_id in evidence.review_ids
        }
        evidence_ids = {evidence.evidence_id for evidence in self.evidence}
        for insight in self.insights:
            if not set(insight.review_ids) <= review_ids:
                raise ValueError("insight review IDs must resolve")
            if not set(insight.evidence_ids) <= evidence_ids:
                raise ValueError("insight evidence IDs must resolve")
        if self.status is not ReviewInsightStatus.READY and self.insights:
            raise ValueError("non-ready review report cannot contain insights")
        return self


class RecommendationReason(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: ComparisonText
    reason: ComparisonText
    evidence_ids: tuple[ComparisonText, ...] = Field(min_length=1)

    def to_payload(self) -> AiModelRecommendationReason:
        return AiModelRecommendationReason(
            item_id=self.item_id,
            reason=self.reason,
            evidence_ids=list(self.evidence_ids),
        )


class ReviewBatchClient:
    """Read one stable, ordered review batch from mock-api."""

    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=10)

    def fetch(
        self,
        item_ids: Sequence[str],
        *,
        limit: int = 100,
    ) -> tuple[ReviewCollection, ...]:
        if not 1 <= len(item_ids) <= 5 or len(item_ids) != len(set(item_ids)):
            raise ComparisonInputError("review batch requires 1-5 unique item IDs")
        response = self._client.post(
            f"{self._base_url}/items/reviews/batch",
            json={"item_ids": list(item_ids), "limit": limit},
        )
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ComparisonInputError("review batch request failed") from exc
        if payload.get("ok") is not True:
            raise ComparisonInputError("review batch response was not successful")
        captured_at = payload.get("captured_at")
        source_version = payload.get("source_version")
        collections: list[ReviewCollection] = []
        for item in payload.get("items", []):
            if item.get("status") != "ok":
                continue
            summary = item["summary"]
            collections.append(
                ReviewCollection(
                    item_id=item["item_id"],
                    reported_review_count=summary["review_count"],
                    average_rating=summary["average_rating"],
                    reviews=tuple(
                        ReviewRecord(
                            review_id=str(review["id"]),
                            item_id=review["item_id"],
                            rating=review["rating"],
                            title=review["title"],
                            content=review["content"],
                            created_at=review["created_at"],
                            updated_at=review["updated_at"],
                        )
                        for review in item["reviews"]
                    ),
                    source_version=source_version,
                    captured_at=captured_at,
                )
            )
        return tuple(collections)


class ProductComparisonService:
    def __init__(
        self,
        profile_registry: FeatureProfileRegistry,
        policy: ComparisonPolicy,
    ) -> None:
        self._profiles = profile_registry
        self._policy = policy

    def build_matrix(
        self,
        *,
        ranking: RankingResult,
        products: Mapping[str, ProductSnapshotItem],
        normalized_features: Mapping[str, NormalizedProductFeatures],
        selected_item_ids: Sequence[str],
    ) -> ComparisonMatrix:
        ordered_ids = self._validate_matrix_inputs(
            ranking,
            products,
            normalized_features,
            selected_item_ids,
        )
        profiles = {normalized_features[item_id].profile_id for item_id in ordered_ids}
        comparable = len(profiles) == 1
        feature_rows = self._feature_rows(profiles)
        evidence: list[EvidenceRef] = self._ranking_evidence(ranking, ordered_ids)
        rows: list[ComparisonRow] = []

        for profile_id, feature_key, label in feature_rows:
            cells: list[ComparisonCell] = []
            for item_id in ordered_ids:
                if (
                    not comparable
                    or normalized_features[item_id].profile_id != profile_id
                ):
                    cells.append(
                        ComparisonCell(
                            item_id=item_id,
                            status=ComparisonCellStatus.NOT_APPLICABLE,
                        )
                    )
                    continue
                feature = next(
                    item
                    for item in normalized_features[item_id].features
                    if item.key == feature_key
                )
                item = products[item_id]
                evidence_ref = self._fact_evidence(
                    ranking.snapshot_id,
                    item,
                    feature_key,
                    label,
                )
                evidence.append(evidence_ref)
                status = ComparisonCellStatus(feature.status.value)
                cells.append(
                    ComparisonCell(
                        item_id=item_id,
                        status=status,
                        normalized_value=feature.value,
                        display_value=(
                            _display_value(feature.value, feature.unit)
                            if feature.value is not None
                            else None
                        ),
                        unit=feature.unit,
                        source_version=item.specifications.source.source_version,
                        freshness=item.specifications.freshness.state,
                        evidence_id=evidence_ref.evidence_id,
                    )
                )
            rows.append(
                ComparisonRow(
                    feature_key=feature_key,
                    label=label,
                    cells=tuple(cells),
                )
            )

        ranking_by_id = {item.item_id: item for item in ranking.ranked}
        return ComparisonMatrix(
            snapshot_id=ranking.snapshot_id,
            ranking_policy_version=ranking.policy_version,
            comparable=comparable,
            incompatibility_code=None if comparable else "cross_profile_comparison",
            columns=tuple(
                ComparisonColumn(
                    item_id=item_id,
                    rank=ranking_by_id[item_id].rank,
                    item_name=str(products[item_id].name.value),
                )
                for item_id in ordered_ids
            ),
            rows=tuple(rows),
            evidence=tuple(evidence),
        )

    def summarize_reviews(
        self,
        collection: ReviewCollection,
    ) -> ReviewInsightReport:
        sample_count = len(collection.reviews)
        if sample_count < self._policy.minimum_review_sample:
            return ReviewInsightReport(
                policy_version=self._policy.policy_version,
                item_id=collection.item_id,
                status=(
                    ReviewInsightStatus.NO_REVIEWS
                    if sample_count == 0
                    else ReviewInsightStatus.LOW_SAMPLE
                ),
                sample_count=sample_count,
                reported_review_count=collection.reported_review_count,
                minimum_sample_count=self._policy.minimum_review_sample,
                insights=(),
                evidence=(),
            )

        sanitized: list[
            tuple[ReviewRecord, SanitizedReviewContent, SanitizedReviewContent]
        ] = [
            (
                review,
                sanitize_review_content(review.title),
                sanitize_review_content(review.content),
            )
            for review in collection.reviews
        ]
        insights: list[ReviewInsight] = []
        referenced_reviews: dict[str, ReviewRecord] = {}
        for topic_code in sorted(self._policy.topics):
            topic = self._policy.topics[topic_code]
            positive: list[ReviewRecord] = []
            negative: list[ReviewRecord] = []
            for review, title, content in sanitized:
                haystack = f"{title.text}\n{content.text}".casefold()
                if not any(
                    keyword.casefold() in haystack for keyword in topic.keywords
                ):
                    continue
                if review.rating >= 4:
                    positive.append(review)
                elif review.rating <= 2:
                    negative.append(review)
            insight = self._topic_insight(
                collection.item_id,
                topic_code,
                topic.label,
                sample_count,
                positive,
                negative,
            )
            if insight is not None:
                insights.append(insight)
                for review in (*positive, *negative):
                    if review.review_id in insight.review_ids:
                        referenced_reviews[review.review_id] = review

        evidence = tuple(
            EvidenceRef(
                evidence_id=_review_evidence_id(review.review_id),
                source_type=EvidenceSourceType.REVIEW,
                source_id=review.review_id,
                item_id=review.item_id,
                review_ids=(review.review_id,),
                title=f"用户评论 {review.review_id}",
            )
            for review in sorted(
                referenced_reviews.values(), key=lambda item: item.review_id
            )
        )
        flags = (
            ("review_prompt_injection",)
            if any(
                title.security_flags or content.security_flags
                for _, title, content in sanitized
            )
            else ()
        )
        return ReviewInsightReport(
            policy_version=self._policy.policy_version,
            item_id=collection.item_id,
            status=ReviewInsightStatus.READY,
            sample_count=sample_count,
            reported_review_count=collection.reported_review_count,
            minimum_sample_count=self._policy.minimum_review_sample,
            insights=tuple(insights),
            evidence=evidence,
            security_flags=flags,
        )

    def build_recommendation_reason(
        self,
        *,
        item_id: str,
        reason: str,
        evidence_ids: Sequence[str],
        matrix: ComparisonMatrix,
        review_reports: Mapping[str, ReviewInsightReport],
    ) -> RecommendationReason:
        matrix_item_ids = {column.item_id for column in matrix.columns}
        if item_id not in matrix_item_ids:
            raise ComparisonInputError("recommendation item is not in the matrix")
        if not evidence_ids or len(evidence_ids) != len(set(evidence_ids)):
            raise ComparisonInputError("recommendation evidence must be unique")
        allowed = {item.evidence_id for item in matrix.evidence}
        for report_item_id, report in review_reports.items():
            if (
                report_item_id != report.item_id
                or report.item_id not in matrix_item_ids
            ):
                raise ComparisonInputError(
                    "review report identity must resolve to a matrix item"
                )
            allowed.update(item.evidence_id for item in report.evidence)
        if not set(evidence_ids) <= allowed:
            raise ComparisonInputError(
                "recommendation references unregistered evidence"
            )
        return RecommendationReason(
            item_id=item_id,
            reason=reason,
            evidence_ids=tuple(evidence_ids),
        )

    def _validate_matrix_inputs(
        self,
        ranking: RankingResult,
        products: Mapping[str, ProductSnapshotItem],
        normalized_features: Mapping[str, NormalizedProductFeatures],
        selected_item_ids: Sequence[str],
    ) -> tuple[str, ...]:
        if ranking.snapshot_id is None:
            raise ComparisonInputError("comparison requires a snapshot ID")
        if not 2 <= len(selected_item_ids) <= 5:
            raise ComparisonInputError("comparison requires 2-5 candidates")
        if len(selected_item_ids) != len(set(selected_item_ids)):
            raise ComparisonInputError("selected item IDs must be unique")
        selected = set(selected_item_ids)
        ranked_ids = [item.item_id for item in ranking.ranked]
        if not selected <= set(ranked_ids):
            raise ComparisonInputError("selected item is not in ranking result")
        ordered_ids = tuple(item_id for item_id in ranked_ids if item_id in selected)
        for item_id in ordered_ids:
            product = products.get(item_id)
            normalized = normalized_features.get(item_id)
            if product is None or normalized is None:
                raise ComparisonInputError("comparison candidate data is incomplete")
            if product.item_id != item_id or normalized.item_id != item_id:
                raise ComparisonInputError("comparison candidate identity mismatch")
        return ordered_ids

    def _feature_rows(
        self,
        profiles: set[str],
    ) -> tuple[tuple[str, str, str], ...]:
        rows: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str]] = set()
        for profile_id in sorted(profiles):
            profile = self._profiles.resolve(profile_id)
            for feature_key, definition in profile.features.items():
                identity = (profile_id, feature_key)
                if definition.display and identity not in seen:
                    rows.append((profile_id, feature_key, definition.display_name))
                    seen.add(identity)
        return tuple(rows)

    @staticmethod
    def _fact_evidence(
        snapshot_id: str,
        item: ProductSnapshotItem,
        feature_key: str,
        label: str,
    ) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=f"fact:{snapshot_id}:{item.item_id}:{feature_key}",
            source_type=EvidenceSourceType.PRODUCT_FACT,
            source_id=f"{item.item_id}.specifications.{feature_key}",
            item_id=item.item_id,
            snapshot_id=snapshot_id,
            title=f"{item.name.value} {label}",
        )

    @staticmethod
    def _ranking_evidence(
        ranking: RankingResult,
        ordered_ids: tuple[str, ...],
    ) -> list[EvidenceRef]:
        allowed = set(ordered_ids)
        return [
            _score_evidence(ranking, candidate.item_id, component)
            for candidate in ranking.ranked
            if candidate.item_id in allowed
            for component in candidate.components
        ]

    def _topic_insight(
        self,
        item_id: str,
        topic_code: str,
        label: str,
        sample_count: int,
        positive: list[ReviewRecord],
        negative: list[ReviewRecord],
    ) -> ReviewInsight | None:
        total = len(positive) + len(negative)
        if total < self._policy.minimum_topic_mentions:
            return None
        if (
            len(positive) >= self._policy.controversy_minimum_each
            and len(negative) >= self._policy.controversy_minimum_each
        ):
            kind = InsightKind.CONTROVERSY
            prevalence = InsightPrevalence.CONTROVERSY
            selected = [*positive, *negative]
            summary = f"关于{label}的评论存在明显分歧"
        else:
            kind = (
                InsightKind.PRO if len(positive) >= len(negative) else InsightKind.CON
            )
            selected = positive if kind is InsightKind.PRO else negative
            prevalence = (
                InsightPrevalence.MAJORITY
                if Decimal(len(selected)) / Decimal(sample_count)
                >= self._policy.majority_ratio
                else InsightPrevalence.MINORITY
            )
            prefix = (
                "多数评论" if prevalence is InsightPrevalence.MAJORITY else "少量评论"
            )
            verb = "认可" if kind is InsightKind.PRO else "不满"
            summary = f"{prefix}{verb}{label}表现"
        selected = sorted(selected, key=lambda item: item.review_id)
        review_ids = tuple(review.review_id for review in selected)
        return ReviewInsight(
            insight_id=f"insight:{item_id}:{topic_code}:{kind.value}",
            item_id=item_id,
            topic_code=topic_code,
            label=label,
            kind=kind,
            prevalence=prevalence,
            summary=summary,
            positive_count=len(positive),
            negative_count=len(negative),
            review_ids=review_ids,
            evidence_ids=tuple(_review_evidence_id(value) for value in review_ids),
        )


def _display_value(value: NormalizedValue, unit: str | None) -> str:
    if isinstance(value, bool):
        rendered = "是" if value else "否"
    elif isinstance(value, Decimal):
        rendered = format(value, "f")
    else:
        rendered = value
    return f"{rendered} {unit}" if unit else rendered


def _payload_cell(cell: ComparisonCell) -> str:
    if cell.status is ComparisonCellStatus.KNOWN:
        return str(cell.display_value)
    if cell.status is ComparisonCellStatus.NOT_APPLICABLE:
        return "不适用"
    if cell.status is ComparisonCellStatus.ERROR:
        return "数据异常"
    return "未知"


def _review_evidence_id(review_id: str) -> str:
    return f"review:{review_id}"


def _score_evidence(
    ranking: RankingResult,
    item_id: str,
    component: ScoreComponent,
) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=(f"rank:{ranking.policy_version}:{item_id}:{component.code}"),
        source_type=EvidenceSourceType.RANKING_SCORE,
        source_id=f"{ranking.policy_version}.{component.code}",
        item_id=item_id,
        snapshot_id=ranking.snapshot_id,
        title=f"排序分解 {component.code}",
    )
