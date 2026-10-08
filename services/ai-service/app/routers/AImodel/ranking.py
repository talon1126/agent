"""Deterministic, configuration-driven ranking for hard-filtered products."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import ROUND_DOWN, Decimal, localcontext
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self

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

from .agent_trace import (
    AgentTraceContext,
    AgentTraceEvent,
    AgentTraceEventType,
    AgentTraceStatus,
)
from .candidate_service import CandidateSet, CandidateSetStatus
from .feature_normalizer import (
    FeatureDataType,
    FeatureMissingPolicy,
    FeatureNormalizationStatus,
    FeatureNormalizer,
    FeatureProfileRegistry,
    NormalizedFeature,
    NormalizedProductFeatures,
)
from .product_models import FactStatus, ProductSnapshotItem
from .shopping_goal import GoalField, Preference, ShoppingGoal


DEFAULT_RANKING_POLICY_PATH = Path(__file__).with_name("ranking_policy.yaml")
RankingText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
RankingIdentity = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]
_COMPONENT_KEYS = frozenset(
    {
        "soft_preference",
        "price_position",
        "rating_confidence",
        "review_volume",
        "delivery_match",
        "category_features",
    }
)
_EXPECTED_TIE_BREAK = (
    "total_score_desc",
    "evidence_completeness_desc",
    "item_id_asc",
)


class RankingPolicyError(ValueError):
    """The ranking policy is invalid and cannot be applied safely."""


class RankingInputError(ValueError):
    """Ranking input violates the C2/C3 handoff contract."""


class ScoreComponentStatus(StrEnum):
    USED = "used"
    MISSING = "missing"
    IGNORED = "ignored"
    NOT_COMPARABLE = "not_comparable"


class MissingFeatureStrategy(StrEnum):
    RENORMALIZE_AVAILABLE = "renormalize_available"


class DiversityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    top_n: int = Field(default=5, ge=1, le=100)
    max_per_brand: int = Field(default=1, ge=1, le=100)
    max_per_model: int = Field(default=1, ge=1, le=100)


class NormalizationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    price: Literal["cohort_min_max_lower_better"]
    rating: Literal["bounded_0_5_with_review_confidence"]
    review_volume: Literal["log_saturation"]
    delivery: Literal["deadline_or_availability"]
    category_number: Literal["cohort_min_max"]
    review_saturation: int = Field(ge=1, le=10_000_000)


class RankingPolicy(BaseModel):
    """Closed ranking policy whose feature references are checked against C3."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"
    policy_version: RankingText
    score_precision: int = Field(default=6, ge=3, le=12)
    component_weights: dict[RankingText, Decimal]
    normalization: NormalizationPolicy
    missing_feature_strategy: MissingFeatureStrategy
    tie_break: tuple[RankingText, ...]
    diversity: DiversityPolicy
    low_confidence_threshold: Decimal = Field(ge=0, le=1)
    category_feature_weights: dict[RankingText, dict[RankingText, Decimal]]

    @field_validator("component_weights")
    @classmethod
    def validate_component_weights(
        cls, value: dict[str, Decimal]
    ) -> dict[str, Decimal]:
        if set(value) != _COMPONENT_KEYS:
            missing = sorted(_COMPONENT_KEYS - set(value))
            unknown = sorted(set(value) - _COMPONENT_KEYS)
            raise ValueError(
                f"component weights require exact keys; missing={missing}, unknown={unknown}"
            )
        if any(weight < 0 or weight > 1 for weight in value.values()):
            raise ValueError("component weights must be between 0 and 1")
        if sum(value.values(), Decimal(0)) != Decimal(1):
            raise ValueError("component weights must sum to 1")
        return value

    @field_validator("tie_break")
    @classmethod
    def validate_tie_break(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != _EXPECTED_TIE_BREAK:
            raise ValueError(
                "tie_break must be total_score_desc, evidence_completeness_desc, "
                "item_id_asc"
            )
        return value

    @field_validator("category_feature_weights")
    @classmethod
    def validate_category_weight_ranges(
        cls, value: dict[str, dict[str, Decimal]]
    ) -> dict[str, dict[str, Decimal]]:
        for profile_id, weights in value.items():
            if not weights:
                raise ValueError(f"profile {profile_id} requires at least one feature")
            if any(weight <= 0 or weight > 1 for weight in weights.values()):
                raise ValueError(
                    f"profile {profile_id} feature weights must be greater than 0 and at most 1"
                )
            if sum(weights.values(), Decimal(0)) != Decimal(1):
                raise ValueError(f"profile {profile_id} feature weights must sum to 1")
        return value

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        profile_registry: FeatureProfileRegistry,
    ) -> RankingPolicy:
        try:
            policy = cls.model_validate(value)
        except ValidationError as exc:
            first = exc.errors(include_url=False)[0]
            path = ".".join(str(part) for part in first.get("loc", ())) or "config"
            raise RankingPolicyError(
                f"{path}: {first.get('msg', 'invalid value')}"
            ) from exc
        policy._validate_profile_references(profile_registry)
        return policy

    def _validate_profile_references(
        self, profile_registry: FeatureProfileRegistry
    ) -> None:
        known_profiles = set(profile_registry.profile_ids)
        for profile_id, weights in self.category_feature_weights.items():
            if profile_id not in known_profiles:
                raise RankingPolicyError(
                    f"category_feature_weights.{profile_id}: unknown feature profile"
                )
            profile = profile_registry.resolve(profile_id)
            for feature_key in weights:
                definition = profile.features.get(feature_key)
                path = f"category_feature_weights.{profile_id}.{feature_key}"
                if definition is None:
                    raise RankingPolicyError(f"{path}: unknown feature")
                if not definition.ranking:
                    raise RankingPolicyError(f"{path}: feature is not rankable")
                if (
                    definition.data_type is FeatureDataType.NUMBER
                    and definition.larger_is_better is None
                ):
                    raise RankingPolicyError(
                        f"{path}: numeric feature requires a ranking direction"
                    )
                if definition.data_type not in {
                    FeatureDataType.NUMBER,
                    FeatureDataType.BOOLEAN,
                }:
                    raise RankingPolicyError(
                        f"{path}: only numeric and boolean features are rankable"
                    )


class ScoreComponent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: RankingText
    status: ScoreComponentStatus
    configured_weight: Decimal = Field(ge=0, le=1)
    effective_weight: Decimal | None = Field(default=None, ge=0, le=1)
    raw_score: Decimal | None = Field(default=None, ge=0, le=1)
    contribution: Decimal | None = Field(default=None, ge=0, le=1)
    used_features: tuple[RankingText, ...] = ()
    missing_features: tuple[RankingText, ...] = ()
    ignored_features: tuple[RankingText, ...] = ()
    not_comparable_features: tuple[RankingText, ...] = ()
    explanation_code: RankingText

    @model_validator(mode="after")
    def validate_status_shape(self) -> Self:
        if self.status is ScoreComponentStatus.USED:
            if (
                self.raw_score is None
                or self.effective_weight is None
                or self.contribution is None
            ):
                raise ValueError(
                    "used component requires score, weight, and contribution"
                )
        elif any(
            value is not None
            for value in (self.raw_score, self.effective_weight, self.contribution)
        ):
            raise ValueError("unused component cannot carry score or contribution")
        feature_sets = (
            self.used_features,
            self.missing_features,
            self.ignored_features,
            self.not_comparable_features,
        )
        flattened = [feature for values in feature_sets for feature in values]
        if len(flattened) != len(set(flattened)):
            raise ValueError("component feature classifications cannot overlap")
        return self


class RankedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: RankingText
    rank: int = Field(ge=1, le=100)
    base_rank: int = Field(ge=1, le=100)
    total_score: Decimal = Field(ge=0, le=1)
    evidence_completeness: Decimal = Field(ge=0, le=1)
    components: tuple[ScoreComponent, ...]
    used_features: tuple[RankingText, ...]
    missing_features: tuple[RankingText, ...]
    ignored_features: tuple[RankingText, ...]
    not_comparable_features: tuple[RankingText, ...]
    explanation_codes: tuple[RankingText, ...]
    brand_key: RankingIdentity
    model_key: RankingIdentity
    diversity_adjusted: bool = False


class RankingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"
    policy_version: RankingText
    snapshot_id: str | None
    ranked: tuple[RankedCandidate, ...]
    diversity_applied: bool
    all_low_confidence: bool
    low_confidence_threshold: Decimal = Field(ge=0, le=1)


class _PendingComponent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    configured_weight: Decimal
    raw_score: Decimal | None
    used_features: tuple[str, ...] = ()
    missing_features: tuple[str, ...] = ()
    ignored_features: tuple[str, ...] = ()
    not_comparable_features: tuple[str, ...] = ()
    missing_status: ScoreComponentStatus = ScoreComponentStatus.MISSING
    explanation_code: str

    @model_validator(mode="after")
    def validate_pending_status(self) -> Self:
        if self.missing_status is ScoreComponentStatus.USED:
            raise ValueError("pending missing status cannot be used")
        return self


class _ScoredCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str
    total_score: Decimal
    evidence_completeness: Decimal
    components: tuple[ScoreComponent, ...]
    used_features: tuple[str, ...]
    missing_features: tuple[str, ...]
    ignored_features: tuple[str, ...]
    not_comparable_features: tuple[str, ...]
    explanation_codes: tuple[str, ...]
    brand_key: str
    model_key: str


def load_ranking_policy(
    profile_registry: FeatureProfileRegistry,
    path: str | Path = DEFAULT_RANKING_POLICY_PATH,
) -> RankingPolicy:
    policy_path = Path(path)
    try:
        payload = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RankingPolicyError(f"cannot load ranking policy: {policy_path}") from exc
    if not isinstance(payload, Mapping):
        raise RankingPolicyError("ranking policy root must be a mapping")
    return RankingPolicy.from_mapping(payload, profile_registry=profile_registry)


def _identity(value: object) -> str:
    return " ".join(str(value).strip().split()).casefold()


def _known_value(item: ProductSnapshotItem, field_name: str) -> Any | None:
    fact = getattr(item, field_name)
    return fact.value if fact.status is FactStatus.KNOWN else None


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    return None


def _clamp(value: Decimal) -> Decimal:
    return min(Decimal(1), max(Decimal(0), value))


def _cohort_score(
    value: Decimal,
    minimum: Decimal,
    maximum: Decimal,
    *,
    larger_is_better: bool,
) -> Decimal:
    if minimum == maximum:
        return Decimal("0.5")
    score = _ratio(value - minimum, maximum - minimum)
    return _clamp(score if larger_is_better else Decimal(1) - score)


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 28
        return numerator / denominator


class ProductRanker:
    """Rank only C2-eligible candidates with deterministic, auditable scoring."""

    def __init__(
        self,
        policy: RankingPolicy,
        profile_registry: FeatureProfileRegistry,
    ) -> None:
        policy._validate_profile_references(profile_registry)
        self._policy = policy
        self._profiles = profile_registry
        self._feature_normalizer = FeatureNormalizer(profile_registry)
        self._quantum = Decimal(1).scaleb(-policy.score_precision)

    def rank(
        self,
        *,
        candidate_set: CandidateSet,
        products: Mapping[str, ProductSnapshotItem],
        normalized_features: Mapping[str, NormalizedProductFeatures],
        goal: ShoppingGoal,
        trace_context: AgentTraceContext | None = None,
    ) -> RankingResult:
        event = self._begin_trace(trace_context, candidate_set)
        try:
            eligible_ids = self._validate_inputs(
                candidate_set, products, normalized_features
            )
            if not eligible_ids:
                result = RankingResult(
                    policy_version=self._policy.policy_version,
                    snapshot_id=candidate_set.snapshot_id,
                    ranked=(),
                    diversity_applied=False,
                    all_low_confidence=False,
                    low_confidence_threshold=self._policy.low_confidence_threshold,
                )
                self._finish_trace(event, result)
                return result

            ranges = self._cohort_ranges(eligible_ids, products, normalized_features)
            scored = [
                self._score_candidate(
                    item_id,
                    products[item_id],
                    normalized_features[item_id],
                    goal,
                    ranges,
                )
                for item_id in eligible_ids
            ]
            base_order = sorted(
                scored,
                key=lambda item: (
                    -item.total_score,
                    -item.evidence_completeness,
                    item.item_id,
                ),
            )
            diversified = self._diversify(base_order)
            base_ranks = {
                candidate.item_id: index
                for index, candidate in enumerate(base_order, start=1)
            }
            ranked = tuple(
                RankedCandidate(
                    **candidate.model_dump(),
                    rank=index,
                    base_rank=base_ranks[candidate.item_id],
                    diversity_adjusted=base_ranks[candidate.item_id] != index,
                )
                for index, candidate in enumerate(diversified, start=1)
            )
            result = RankingResult(
                policy_version=self._policy.policy_version,
                snapshot_id=candidate_set.snapshot_id,
                ranked=ranked,
                diversity_applied=any(item.diversity_adjusted for item in ranked),
                all_low_confidence=bool(ranked)
                and all(
                    item.evidence_completeness < self._policy.low_confidence_threshold
                    for item in ranked
                ),
                low_confidence_threshold=self._policy.low_confidence_threshold,
            )
        except Exception as exc:
            if event is not None:
                event.finish(AgentTraceStatus.ERROR, error=str(exc))
            raise
        self._finish_trace(event, result)
        return result

    def _validate_inputs(
        self,
        candidate_set: CandidateSet,
        products: Mapping[str, ProductSnapshotItem],
        normalized_features: Mapping[str, NormalizedProductFeatures],
    ) -> tuple[str, ...]:
        if any(candidate.reasons for candidate in candidate_set.eligible):
            raise RankingInputError(
                "eligible candidate contains a hard filter exclusion reason"
            )
        eligible_ids = tuple(candidate.item_id for candidate in candidate_set.eligible)
        if len(eligible_ids) != len(set(eligible_ids)):
            raise RankingInputError("eligible candidate IDs must be unique")
        if candidate_set.status is CandidateSetStatus.NO_CANDIDATE:
            if eligible_ids:
                raise RankingInputError(
                    "no_candidate set cannot contain eligible items"
                )
            return ()
        if candidate_set.status is not CandidateSetStatus.READY or not eligible_ids:
            raise RankingInputError("ready candidate set requires eligible items")
        if candidate_set.snapshot_id is None:
            raise RankingInputError("eligible candidates require a snapshot ID")

        for candidate in candidate_set.eligible:
            if candidate.snapshot_id != candidate_set.snapshot_id:
                raise RankingInputError(
                    f"candidate {candidate.item_id} has a mismatched snapshot ID"
                )
            product = products.get(candidate.item_id)
            normalized = normalized_features.get(candidate.item_id)
            if product is None:
                raise RankingInputError(
                    f"candidate {candidate.item_id} is missing its product snapshot"
                )
            if normalized is None:
                raise RankingInputError(
                    f"candidate {candidate.item_id} is missing normalized features"
                )
            if (
                product.item_id != candidate.item_id
                or normalized.item_id != candidate.item_id
            ):
                raise RankingInputError(
                    f"candidate {candidate.item_id} has mismatched item identity"
                )
            category = _known_value(product, "category")
            expected_profile = self._profiles.resolve(
                str(category) if category is not None else None
            )
            if normalized.profile_id != expected_profile.profile_id:
                raise RankingInputError(
                    f"candidate {candidate.item_id} has mismatched feature profile"
                )
            normalized_by_key = {
                feature.key: feature for feature in normalized.features
            }
            if set(normalized_by_key) != set(expected_profile.features):
                raise RankingInputError(
                    f"candidate {candidate.item_id} has an incomplete feature profile"
                )
            for feature_key, definition in expected_profile.features.items():
                feature = normalized_by_key[feature_key]
                if (
                    feature.data_type is not definition.data_type
                    or feature.missing_policy is not definition.missing_policy
                    or feature.larger_is_better is not definition.larger_is_better
                    or feature.ranking is not definition.ranking
                ):
                    raise RankingInputError(
                        f"candidate {candidate.item_id} has inconsistent feature metadata"
                    )
        return eligible_ids

    def _cohort_ranges(
        self,
        item_ids: Sequence[str],
        products: Mapping[str, ProductSnapshotItem],
        normalized_features: Mapping[str, NormalizedProductFeatures],
    ) -> dict[str, tuple[Decimal, Decimal]]:
        collected: dict[str, list[Decimal]] = {"price": []}
        for item_id in item_ids:
            price = _decimal(_known_value(products[item_id], "current_price"))
            if price is not None:
                collected["price"].append(price)
            normalized = normalized_features[item_id]
            configured = self._policy.category_feature_weights.get(
                normalized.profile_id, {}
            )
            for feature in normalized.features:
                if feature.key not in configured:
                    continue
                numeric = _decimal(feature.value)
                if (
                    feature.status is FeatureNormalizationStatus.KNOWN
                    and numeric is not None
                ):
                    collected.setdefault(
                        f"{normalized.profile_id}.{feature.key}", []
                    ).append(numeric)
        return {
            key: (min(values), max(values))
            for key, values in collected.items()
            if values
        }

    def _score_candidate(
        self,
        item_id: str,
        product: ProductSnapshotItem,
        normalized: NormalizedProductFeatures,
        goal: ShoppingGoal,
        ranges: Mapping[str, tuple[Decimal, Decimal]],
    ) -> _ScoredCandidate:
        components = [
            self._soft_preference_component(product, normalized, goal),
            self._price_component(product, ranges),
            self._rating_component(product),
            self._review_component(product),
            self._delivery_component(product, goal),
        ]
        components.extend(self._category_components(normalized, ranges))
        used_weight = sum(
            (
                component.configured_weight
                for component in components
                if component.raw_score is not None
            ),
            Decimal(0),
        )
        ignored_weight = sum(
            (
                component.configured_weight
                for component in components
                if component.raw_score is None
                and component.missing_status is ScoreComponentStatus.IGNORED
            ),
            Decimal(0),
        )
        finalized = tuple(
            self._finalize_component(component, used_weight) for component in components
        )
        contributions = [
            component.contribution
            for component in finalized
            if component.contribution is not None
        ]
        total = sum(contributions, Decimal(0))
        confidence_denominator = Decimal(1) - ignored_weight
        completeness = (
            self._round(_ratio(used_weight, confidence_denominator))
            if confidence_denominator > 0
            else Decimal(1)
        )
        used_features = tuple(
            sorted(
                {
                    feature
                    for component in finalized
                    for feature in component.used_features
                }
            )
        )
        missing_features = tuple(
            sorted(
                {
                    feature
                    for component in finalized
                    for feature in component.missing_features
                }
                - set(used_features)
            )
        )
        ignored_features = tuple(
            sorted(
                {
                    feature
                    for component in finalized
                    for feature in component.ignored_features
                }
            )
        )
        not_comparable_features = tuple(
            sorted(
                {
                    feature
                    for component in finalized
                    for feature in component.not_comparable_features
                }
            )
        )
        return _ScoredCandidate(
            item_id=item_id,
            total_score=total,
            evidence_completeness=completeness,
            components=finalized,
            used_features=used_features,
            missing_features=missing_features,
            ignored_features=ignored_features,
            not_comparable_features=not_comparable_features,
            explanation_codes=tuple(
                component.explanation_code
                for component in finalized
                if component.status
                in {
                    ScoreComponentStatus.USED,
                    ScoreComponentStatus.IGNORED,
                    ScoreComponentStatus.NOT_COMPARABLE,
                }
            ),
            brand_key=self._identity_key(product, "brand", f"unknown-brand:{item_id}"),
            model_key=self._identity_key(product, "name", f"unknown-model:{item_id}"),
        )

    def _soft_preference_component(
        self,
        product: ProductSnapshotItem,
        normalized: NormalizedProductFeatures,
        goal: ShoppingGoal,
    ) -> _PendingComponent:
        scores: list[Decimal] = []
        used: list[str] = []
        missing: list[str] = []
        feature_map = {feature.key: feature for feature in normalized.features}
        for preference in goal.preferences:
            score = self._preference_score(preference, product, feature_map)
            key = self._preference_key(preference, normalized.profile_id)
            if score is None:
                missing.append(key)
            else:
                scores.append(score)
                used.append(key)
        raw = sum(scores, Decimal(0)) / len(scores) if scores else None
        return self._pending(
            "soft_preference",
            raw,
            used,
            missing or ([] if goal.preferences else ["preference"]),
            "rank.soft_preference_match",
        )

    def _preference_key(self, preference: Preference, profile_id: str) -> str:
        if preference.field is not GoalField.SPECIFICATION:
            return f"preference.{preference.field.value}"
        feature_key = (
            self._profiles.feature_key(profile_id, preference.attribute)
            if preference.attribute
            else None
        )
        suffix = f".{feature_key}" if feature_key is not None else ""
        return f"preference.specification{suffix}"

    def _preference_score(
        self,
        preference: Preference,
        product: ProductSnapshotItem,
        feature_map: Mapping[str, NormalizedFeature],
    ) -> Decimal | None:
        if preference.field is GoalField.BRAND:
            brand = _known_value(product, "brand")
            return (
                Decimal(int(_identity(brand) == _identity(preference.value)))
                if brand is not None
                else None
            )
        if preference.field is GoalField.CATEGORY:
            category = _known_value(product, "category")
            return (
                Decimal(int(_identity(category) == _identity(preference.value)))
                if category is not None
                else None
            )
        if preference.field is GoalField.SPECIFICATION and preference.attribute:
            profile_id = self._profiles.resolve(normalized_category(product)).profile_id
            feature_key = self._profiles.feature_key(profile_id, preference.attribute)
            feature = feature_map.get(feature_key or "")
            if (
                feature_key is None
                or feature is None
                or feature.status is not FeatureNormalizationStatus.KNOWN
            ):
                return None
            expected = self._feature_normalizer.normalize_value(
                profile_id=profile_id,
                feature_key=feature_key,
                raw_value=str(preference.value),
            )
            if expected.status is not FeatureNormalizationStatus.KNOWN:
                return None
            if isinstance(feature.value, str) and isinstance(expected.value, str):
                matches = _identity(feature.value) == _identity(expected.value)
            else:
                matches = feature.value == expected.value
            return Decimal(int(matches))
        if preference.field in {GoalField.BUDGET_MIN, GoalField.BUDGET_MAX}:
            price = _decimal(_known_value(product, "current_price"))
            target = _decimal(preference.value)
            if price is None or target is None:
                return None
            if preference.field is GoalField.BUDGET_MAX:
                return Decimal(1) if price <= target else _clamp(_ratio(target, price))
            return Decimal(1) if price >= target else _clamp(_ratio(price, target))
        return None

    def _price_component(
        self,
        product: ProductSnapshotItem,
        ranges: Mapping[str, tuple[Decimal, Decimal]],
    ) -> _PendingComponent:
        price = _decimal(_known_value(product, "current_price"))
        price_range = ranges.get("price")
        raw = (
            _cohort_score(price, *price_range, larger_is_better=False)
            if price is not None and price_range is not None
            else None
        )
        return self._pending(
            "price_position",
            raw,
            ["current_price"] if raw is not None else [],
            ["current_price"] if raw is None else [],
            "rank.price_position",
        )

    def _rating_component(self, product: ProductSnapshotItem) -> _PendingComponent:
        rating = _decimal(_known_value(product, "rating"))
        reviews = _decimal(_known_value(product, "review_count"))
        raw: Decimal | None = None
        if rating is not None and reviews is not None and reviews >= 0:
            with localcontext() as context:
                context.prec = 28
                confidence = min(
                    Decimal(1),
                    reviews / Decimal(self._policy.normalization.review_saturation),
                ).sqrt()
                raw = _clamp(rating / Decimal(5)) * confidence
        missing = [
            field
            for field, value in (("rating", rating), ("review_count", reviews))
            if value is None
        ]
        return self._pending(
            "rating_confidence",
            raw,
            ["rating", "review_count"] if raw is not None else [],
            missing,
            "rank.rating_confidence",
        )

    def _review_component(self, product: ProductSnapshotItem) -> _PendingComponent:
        reviews = _decimal(_known_value(product, "review_count"))
        raw = None
        if reviews is not None and reviews >= 0:
            with localcontext() as context:
                context.prec = 28
                saturation = Decimal(self._policy.normalization.review_saturation)
                raw = min(Decimal(1), (reviews + 1).ln() / (saturation + 1).ln())
        return self._pending(
            "review_volume",
            raw,
            ["review_count"] if raw is not None else [],
            ["review_count"] if raw is None else [],
            "rank.review_volume",
        )

    def _delivery_component(
        self, product: ProductSnapshotItem, goal: ShoppingGoal
    ) -> _PendingComponent:
        delivery = _known_value(product, "delivery")
        if delivery is None:
            raw = None
        else:
            deadline = next(
                (
                    preference.value
                    for preference in goal.preferences
                    if preference.field is GoalField.DELIVERY_DEADLINE
                    and isinstance(preference.value, datetime)
                ),
                None,
            )
            if deadline is not None:
                estimate = delivery.estimated_delivery_at
                raw = (
                    Decimal(int(estimate <= deadline)) if estimate is not None else None
                )
            else:
                raw = Decimal(
                    int(
                        delivery.delivery_available
                        or delivery.shipping_available
                        or delivery.pickup_available
                    )
                )
        return self._pending(
            "delivery_match",
            raw,
            ["delivery"] if raw is not None else [],
            ["delivery"] if raw is None else [],
            "rank.delivery_match",
        )

    def _category_components(
        self,
        normalized: NormalizedProductFeatures,
        ranges: Mapping[str, tuple[Decimal, Decimal]],
    ) -> list[_PendingComponent]:
        configured = self._policy.category_feature_weights.get(
            normalized.profile_id, {}
        )
        by_key = {feature.key: feature for feature in normalized.features}
        category_weight = self._policy.component_weights["category_features"]
        components: list[_PendingComponent] = []
        for feature_key, relative_weight in sorted(configured.items()):
            feature = by_key.get(feature_key)
            raw = self._category_feature_score(normalized.profile_id, feature, ranges)
            missing_status = self._missing_component_status(feature)
            missing_features = (
                (feature_key,)
                if raw is None and missing_status is ScoreComponentStatus.MISSING
                else ()
            )
            ignored_features = (
                (feature_key,)
                if raw is None and missing_status is ScoreComponentStatus.IGNORED
                else ()
            )
            not_comparable_features = (
                (feature_key,)
                if raw is None and missing_status is ScoreComponentStatus.NOT_COMPARABLE
                else ()
            )
            components.append(
                _PendingComponent(
                    code=f"feature.{feature_key}",
                    configured_weight=category_weight * relative_weight,
                    raw_score=raw,
                    used_features=(feature_key,) if raw is not None else (),
                    missing_features=missing_features,
                    ignored_features=ignored_features,
                    not_comparable_features=not_comparable_features,
                    missing_status=missing_status,
                    explanation_code=(
                        f"rank.feature.{feature_key}"
                        if raw is not None
                        else f"rank.feature.{feature_key}.{missing_status.value}"
                    ),
                )
            )
        if not configured:
            components.append(
                _PendingComponent(
                    code="category_features",
                    configured_weight=category_weight,
                    raw_score=None,
                    missing_features=("category_features",),
                    explanation_code="rank.category_features_unconfigured",
                )
            )
        return components

    @staticmethod
    def _missing_component_status(
        feature: NormalizedFeature | None,
    ) -> ScoreComponentStatus:
        if feature is None:
            return ScoreComponentStatus.MISSING
        if feature.missing_policy is FeatureMissingPolicy.IGNORE_SCORE:
            return ScoreComponentStatus.IGNORED
        if feature.missing_policy is FeatureMissingPolicy.NOT_COMPARABLE:
            return ScoreComponentStatus.NOT_COMPARABLE
        return ScoreComponentStatus.MISSING

    def _category_feature_score(
        self,
        profile_id: str,
        feature: NormalizedFeature | None,
        ranges: Mapping[str, tuple[Decimal, Decimal]],
    ) -> Decimal | None:
        if feature is None or feature.status is not FeatureNormalizationStatus.KNOWN:
            return None
        if feature.data_type is FeatureDataType.BOOLEAN:
            return Decimal(int(feature.value is True))
        numeric = _decimal(feature.value)
        feature_range = ranges.get(f"{profile_id}.{feature.key}")
        definition = self._profiles.resolve(profile_id).features[feature.key]
        if (
            numeric is None
            or feature_range is None
            or definition.larger_is_better is None
        ):
            return None
        return _cohort_score(
            numeric,
            *feature_range,
            larger_is_better=definition.larger_is_better,
        )

    def _pending(
        self,
        code: str,
        raw_score: Decimal | None,
        used_features: Sequence[str],
        missing_features: Sequence[str],
        explanation_code: str,
    ) -> _PendingComponent:
        return _PendingComponent(
            code=code,
            configured_weight=self._policy.component_weights[code],
            raw_score=raw_score,
            used_features=tuple(used_features),
            missing_features=tuple(missing_features),
            explanation_code=explanation_code,
        )

    def _finalize_component(
        self, component: _PendingComponent, used_weight: Decimal
    ) -> ScoreComponent:
        if component.raw_score is None or used_weight == 0:
            return ScoreComponent(
                code=component.code,
                status=component.missing_status,
                configured_weight=component.configured_weight,
                used_features=component.used_features,
                missing_features=component.missing_features,
                ignored_features=component.ignored_features,
                not_comparable_features=component.not_comparable_features,
                explanation_code=component.explanation_code,
            )
        effective = self._round(_ratio(component.configured_weight, used_weight))
        raw_score = self._round(component.raw_score)
        return ScoreComponent(
            code=component.code,
            status=ScoreComponentStatus.USED,
            configured_weight=component.configured_weight,
            effective_weight=effective,
            raw_score=raw_score,
            contribution=self._round(raw_score * effective),
            used_features=component.used_features,
            missing_features=component.missing_features,
            ignored_features=component.ignored_features,
            not_comparable_features=component.not_comparable_features,
            explanation_code=component.explanation_code,
        )

    def _diversify(
        self, base_order: Sequence[_ScoredCandidate]
    ) -> list[_ScoredCandidate]:
        policy = self._policy.diversity
        remaining = list(base_order)
        selected: list[_ScoredCandidate] = []
        brand_counts: dict[str, int] = {}
        model_counts: dict[str, int] = {}
        target = min(policy.top_n, len(remaining))
        while remaining and len(selected) < target:
            chosen_index = next(
                (
                    index
                    for index, candidate in enumerate(remaining)
                    if brand_counts.get(candidate.brand_key, 0) < policy.max_per_brand
                    and model_counts.get(candidate.model_key, 0) < policy.max_per_model
                ),
                0,
            )
            chosen = remaining.pop(chosen_index)
            selected.append(chosen)
            brand_counts[chosen.brand_key] = brand_counts.get(chosen.brand_key, 0) + 1
            model_counts[chosen.model_key] = model_counts.get(chosen.model_key, 0) + 1
        selected.extend(remaining)
        return selected

    def _round(self, value: Decimal) -> Decimal:
        return value.quantize(self._quantum, rounding=ROUND_DOWN)

    @staticmethod
    def _identity_key(
        product: ProductSnapshotItem, field_name: str, fallback: str
    ) -> str:
        value = _known_value(product, field_name)
        return _identity(value) if value is not None else fallback

    def _begin_trace(
        self,
        trace_context: AgentTraceContext | None,
        candidate_set: CandidateSet,
    ) -> AgentTraceEvent | None:
        if trace_context is None:
            return None
        return trace_context.begin_event(
            AgentTraceEventType.RANK,
            summary={
                "policy_version": self._policy.policy_version,
                "eligible_count": len(candidate_set.eligible),
            },
            related_ids={"snapshot_id": candidate_set.snapshot_id or "none"},
        )

    def _finish_trace(
        self, event: AgentTraceEvent | None, result: RankingResult
    ) -> None:
        if event is None:
            return
        event.finish(
            AgentTraceStatus.SUCCESS,
            summary={
                "policy_version": result.policy_version,
                "ranked_count": len(result.ranked),
                "all_low_confidence": result.all_low_confidence,
                "top_item_ids": [item.item_id for item in result.ranked[:5]],
            },
        )


def normalized_category(product: ProductSnapshotItem) -> str | None:
    value = _known_value(product, "category")
    return str(value) if value is not None else None
