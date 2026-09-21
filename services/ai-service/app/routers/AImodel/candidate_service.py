"""Deterministic candidate recall followed by non-negotiable hard filtering."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol, Self

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from .agent_trace import AgentTraceContext
from .product_models import (
    FactStatus,
    FreshnessState,
    ProductFact,
    ProductSnapshot,
    ProductSnapshotItem,
)
from .product_snapshot import ProductSnapshotErrorBase
from .shopping_goal import GoalField, ShoppingGoal


CandidateText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]
CandidateId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
ReasonValue = str | int | bool | Decimal | datetime | None


class CandidateSource(StrEnum):
    EXPLICIT = "explicit"
    PAGE = "page"
    SEARCH = "search"


_SOURCE_ORDER = {
    CandidateSource.EXPLICIT: 0,
    CandidateSource.PAGE: 1,
    CandidateSource.SEARCH: 2,
}


class CandidateSetStatus(StrEnum):
    READY = "ready"
    NO_CANDIDATE = "no_candidate"


class ExclusionAction(StrEnum):
    EXCLUDE = "exclude"
    CLARIFY = "clarify"
    REFRESH_FACT = "refresh_fact"
    RELAX_CONSTRAINT = "relax_constraint"


class RequiredSpecification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attribute: CandidateText
    value: CandidateText

    @model_validator(mode="after")
    def normalize_identity(self) -> Self:
        if self.attribute != self.attribute.casefold():
            raise ValueError("required specification attribute must be casefolded")
        return self


class CandidatePolicy(BaseModel):
    """Bounded recall and conservative unknown/stale handling."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_search_query_length: int = Field(default=120, ge=1, le=200)
    max_candidates: int = Field(default=50, ge=1, le=100)
    minimum_stock: int = Field(default=1, ge=0, le=999)
    unknown_fact_action: ExclusionAction = ExclusionAction.CLARIFY
    stale_fact_action: ExclusionAction = ExclusionAction.REFRESH_FACT
    required_specifications: tuple[RequiredSpecification, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )
    max_suggestions: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def validate_actions_and_specifications(self) -> Self:
        if self.unknown_fact_action not in {
            ExclusionAction.EXCLUDE,
            ExclusionAction.CLARIFY,
        }:
            raise ValueError("unknown fact action must be exclude or clarify")
        if self.stale_fact_action not in {
            ExclusionAction.EXCLUDE,
            ExclusionAction.REFRESH_FACT,
        }:
            raise ValueError("stale fact action must be exclude or refresh_fact")
        attributes = [item.attribute for item in self.required_specifications]
        if len(attributes) != len(set(attributes)):
            raise ValueError("required specification attributes must be unique")
        return self


class CandidateReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: CandidateId
    sources: tuple[CandidateSource, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_sources(self) -> Self:
        if len(self.sources) != len(set(self.sources)):
            raise ValueError("candidate sources must be unique")
        if list(self.sources) != sorted(
            self.sources, key=lambda value: _SOURCE_ORDER[value]
        ):
            raise ValueError("candidate sources must follow stable priority")
        return self


class ExclusionReason(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: CandidateText
    field: CandidateText
    expected: ReasonValue
    actual: ReasonValue
    action: ExclusionAction = ExclusionAction.EXCLUDE


class FilteredCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: CandidateId
    sources: tuple[CandidateSource, ...] = Field(min_length=1, max_length=3)
    snapshot_id: CandidateId
    reasons: tuple[ExclusionReason, ...] = Field(default_factory=tuple, max_length=64)


class CandidateFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: Literal["search", "snapshot"]
    item_id: CandidateId | None = None
    code: CandidateText
    message: CandidateText

    @model_validator(mode="after")
    def validate_item_scope(self) -> Self:
        if self.stage == "snapshot" and self.item_id is None:
            raise ValueError("snapshot failure requires item_id")
        if self.stage == "search" and self.item_id is not None:
            raise ValueError("search failure cannot claim one item_id")
        return self


class ExclusionCount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: CandidateText
    field: CandidateText
    count: int = Field(gt=0)


class CandidateSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: ExclusionAction
    field: CandidateText
    reason_code: CandidateText
    affected_count: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        if self.action is ExclusionAction.EXCLUDE:
            raise ValueError("a suggestion must propose a next action")
        return self


class CandidateSet(BaseModel):
    """Immutable result separating eligible, excluded, and unreadable candidates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"
    status: CandidateSetStatus
    snapshot_id: CandidateId | None = None
    search_query: str | None = Field(default=None, max_length=200)
    recalled: tuple[CandidateReference, ...] = Field(
        default_factory=tuple, max_length=100
    )
    eligible: tuple[FilteredCandidate, ...] = Field(
        default_factory=tuple, max_length=100
    )
    excluded: tuple[FilteredCandidate, ...] = Field(
        default_factory=tuple, max_length=100
    )
    failures: tuple[CandidateFailure, ...] = Field(
        default_factory=tuple, max_length=101
    )
    reason_counts: tuple[ExclusionCount, ...] = Field(
        default_factory=tuple, max_length=64
    )
    suggestions: tuple[CandidateSuggestion, ...] = Field(
        default_factory=tuple, max_length=10
    )

    @model_validator(mode="after")
    def validate_partition(self) -> Self:
        recalled_ids = [item.item_id for item in self.recalled]
        if len(recalled_ids) != len(set(recalled_ids)):
            raise ValueError("recalled candidates must be unique")
        eligible_ids = [item.item_id for item in self.eligible]
        excluded_ids = [item.item_id for item in self.excluded]
        if set(eligible_ids) & set(excluded_ids):
            raise ValueError("candidate cannot be both eligible and excluded")
        if not set(eligible_ids + excluded_ids).issubset(recalled_ids):
            raise ValueError("filtered candidates must originate from recall")
        if any(item.reasons for item in self.eligible):
            raise ValueError("eligible candidate cannot contain exclusion reasons")
        if any(not item.reasons for item in self.excluded):
            raise ValueError("excluded candidate requires at least one reason")
        if self.status is CandidateSetStatus.READY and not self.eligible:
            raise ValueError("ready candidate set requires an eligible candidate")
        if self.status is CandidateSetStatus.NO_CANDIDATE and self.eligible:
            raise ValueError("no_candidate result cannot contain eligible candidates")
        if (self.eligible or self.excluded) and self.snapshot_id is None:
            raise ValueError("filtered candidates require snapshot_id")
        return self


class _SnapshotClient(Protocol):
    def capture_for_turn(
        self,
        *,
        turn_id: str,
        item_ids: list[str],
        trace_context: AgentTraceContext | None = None,
    ) -> ProductSnapshot: ...


def _normalized_text(value: object) -> str:
    return " ".join(str(value).strip().split()).casefold()


def _display_value(value: object) -> ReasonValue:
    if isinstance(value, (str, int, bool, Decimal, datetime)) or value is None:
        return value
    return str(value)


def _reason(
    code: str,
    field: str,
    expected: object,
    actual: object,
    *,
    action: ExclusionAction = ExclusionAction.EXCLUDE,
) -> ExclusionReason:
    return ExclusionReason(
        code=code,
        field=field,
        expected=_display_value(expected),
        actual=_display_value(actual),
        action=action,
    )


def _unusable_fact_reason(
    fact: ProductFact[Any],
    *,
    field: str,
    expected: object,
    policy: CandidatePolicy,
) -> ExclusionReason | None:
    if fact.status is FactStatus.UNKNOWN:
        return _reason(
            f"{field}_unknown",
            field,
            expected,
            "unknown",
            action=policy.unknown_fact_action,
        )
    if fact.freshness.state is FreshnessState.STALE:
        return _reason(
            f"{field}_stale",
            field,
            expected,
            fact.value,
            action=policy.stale_fact_action,
        )
    if fact.freshness.state is FreshnessState.UNKNOWN:
        return _reason(
            f"{field}_freshness_unknown",
            field,
            expected,
            fact.value,
            action=policy.stale_fact_action,
        )
    return None


def _constraint_value(goal: ShoppingGoal, field: GoalField) -> object | None:
    return next(
        (item.value for item in goal.hard_constraints if item.field is field),
        None,
    )


def _excluded_value(goal: ShoppingGoal, field: GoalField) -> object | None:
    return next(
        (item.value for item in goal.exclusions if item.field is field),
        None,
    )


def _append_fact_problem(
    reasons: list[ExclusionReason],
    fact: ProductFact[Any],
    *,
    field: str,
    expected: object,
    policy: CandidatePolicy,
) -> bool:
    problem = _unusable_fact_reason(
        fact,
        field=field,
        expected=expected,
        policy=policy,
    )
    if problem is None:
        return False
    reasons.append(problem)
    return True


def _price_reasons(
    goal: ShoppingGoal,
    item: ProductSnapshotItem,
    policy: CandidatePolicy,
) -> list[ExclusionReason]:
    minimum = _constraint_value(goal, GoalField.BUDGET_MIN)
    maximum = _constraint_value(goal, GoalField.BUDGET_MAX)
    if minimum is None and maximum is None:
        return []
    expected = minimum if maximum is None else maximum
    reasons: list[ExclusionReason] = []
    if _append_fact_problem(
        reasons,
        item.current_price,
        field="price",
        expected=expected,
        policy=policy,
    ):
        return reasons
    price = item.current_price.value
    assert isinstance(price, Decimal)
    if minimum is not None and price < minimum:
        reasons.append(_reason("budget_below_minimum", "budget_min", minimum, price))
    if maximum is not None and price > maximum:
        reasons.append(_reason("budget_above_maximum", "budget_max", maximum, price))
    return reasons


def _brand_reasons(
    goal: ShoppingGoal,
    item: ProductSnapshotItem,
    policy: CandidatePolicy,
) -> list[ExclusionReason]:
    included = _constraint_value(goal, GoalField.BRAND)
    excluded = _excluded_value(goal, GoalField.BRAND)
    if included is None and excluded is None:
        return []
    expected = included if included is not None else f"not {excluded}"
    reasons: list[ExclusionReason] = []
    if _append_fact_problem(
        reasons,
        item.brand,
        field="brand",
        expected=expected,
        policy=policy,
    ):
        return reasons
    actual = str(item.brand.value)
    if included is not None and _normalized_text(actual) != _normalized_text(included):
        reasons.append(_reason("brand_not_included", "brand", included, actual))
    if excluded is not None and _normalized_text(actual) == _normalized_text(excluded):
        reasons.append(_reason("brand_excluded", "brand", f"not {excluded}", actual))
    return reasons


def _category_reasons(
    goal: ShoppingGoal,
    item: ProductSnapshotItem,
    policy: CandidatePolicy,
) -> list[ExclusionReason]:
    expected = _constraint_value(goal, GoalField.CATEGORY)
    if expected is None:
        return []
    reasons: list[ExclusionReason] = []
    if _append_fact_problem(
        reasons,
        item.category,
        field="category",
        expected=expected,
        policy=policy,
    ):
        return reasons
    actual = str(item.category.value)
    if _normalized_text(actual) != _normalized_text(expected):
        reasons.append(_reason("category_mismatch", "category", expected, actual))
    return reasons


def _stock_reasons(
    goal: ShoppingGoal,
    item: ProductSnapshotItem,
    policy: CandidatePolicy,
) -> list[ExclusionReason]:
    quantity = _constraint_value(goal, GoalField.QUANTITY)
    required = int(quantity) if quantity is not None else policy.minimum_stock
    if required == 0:
        return []
    reasons: list[ExclusionReason] = []
    if _append_fact_problem(
        reasons,
        item.stock,
        field="stock",
        expected=required,
        policy=policy,
    ):
        return reasons
    actual = item.stock.value
    assert isinstance(actual, int)
    if actual < required:
        reasons.append(_reason("insufficient_stock", "stock", required, actual))
    return reasons


def _delivery_reasons(
    goal: ShoppingGoal,
    item: ProductSnapshotItem,
    policy: CandidatePolicy,
) -> list[ExclusionReason]:
    deadline = _constraint_value(goal, GoalField.DELIVERY_DEADLINE)
    if deadline is None:
        return []
    reasons: list[ExclusionReason] = []
    if _append_fact_problem(
        reasons,
        item.delivery,
        field="delivery",
        expected=deadline,
        policy=policy,
    ):
        return reasons
    delivery = item.delivery.value
    assert delivery is not None
    if not delivery.delivery_available:
        reasons.append(_reason("delivery_unavailable", "delivery", deadline, False))
    elif delivery.estimated_delivery_at is None:
        reasons.append(
            _reason(
                "delivery_unknown",
                "delivery",
                deadline,
                "unknown",
                action=policy.unknown_fact_action,
            )
        )
    elif delivery.estimated_delivery_at > deadline:
        reasons.append(
            _reason(
                "delivery_after_deadline",
                "delivery_deadline",
                deadline,
                delivery.estimated_delivery_at,
            )
        )
    return reasons


def _required_specifications(
    goal: ShoppingGoal,
    policy: CandidatePolicy,
) -> tuple[tuple[str, str], ...]:
    configured = [
        (item.attribute.casefold(), item.value)
        for item in policy.required_specifications
    ]
    goal_values = [
        (item.attribute.casefold(), str(item.value))
        for item in goal.hard_constraints
        if item.field is GoalField.SPECIFICATION and item.attribute is not None
    ]
    combined = {
        (attribute, _normalized_text(value)): (attribute, value)
        for attribute, value in (*configured, *goal_values)
    }
    return tuple(
        value for _, value in sorted(combined.items(), key=lambda entry: entry[0])
    )


def _specification_reasons(
    goal: ShoppingGoal,
    item: ProductSnapshotItem,
    policy: CandidatePolicy,
) -> list[ExclusionReason]:
    required = _required_specifications(goal, policy)
    if not required:
        return []
    expected_summary = ", ".join(f"{key}={value}" for key, value in required)
    reasons: list[ExclusionReason] = []
    if _append_fact_problem(
        reasons,
        item.specifications,
        field="specification",
        expected=expected_summary,
        policy=policy,
    ):
        return reasons
    specifications = item.specifications.value
    assert specifications is not None
    actual = {value.key.casefold(): value.value for value in specifications.values}
    for attribute, expected in required:
        actual_value = actual.get(attribute)
        if actual_value is None:
            reasons.append(
                _reason(
                    "specification_unknown",
                    f"specification.{attribute}",
                    expected,
                    "unknown",
                    action=policy.unknown_fact_action,
                )
            )
        elif _normalized_text(actual_value) != _normalized_text(expected):
            reasons.append(
                _reason(
                    "specification_mismatch",
                    f"specification.{attribute}",
                    expected,
                    actual_value,
                )
            )
    return reasons


def _candidate_reasons(
    goal: ShoppingGoal,
    item: ProductSnapshotItem,
    policy: CandidatePolicy,
) -> tuple[ExclusionReason, ...]:
    reasons = [
        *_price_reasons(goal, item, policy),
        *_brand_reasons(goal, item, policy),
        *_category_reasons(goal, item, policy),
        *_stock_reasons(goal, item, policy),
        *_delivery_reasons(goal, item, policy),
        *_specification_reasons(goal, item, policy),
    ]
    return tuple(reasons)


def _summaries(
    excluded: Sequence[FilteredCandidate],
    *,
    policy: CandidatePolicy,
) -> tuple[tuple[ExclusionCount, ...], tuple[CandidateSuggestion, ...]]:
    reasons = [reason for candidate in excluded for reason in candidate.reasons]
    counts = Counter((reason.code, reason.field) for reason in reasons)
    ordered = sorted(counts.items(), key=lambda entry: (-entry[1], *entry[0]))
    reason_counts = tuple(
        ExclusionCount(code=code, field=field, count=count)
        for (code, field), count in ordered
    )
    action_by_reason = {
        (reason.code, reason.field): reason.action for reason in reasons
    }
    suggestions: list[CandidateSuggestion] = []
    for (code, field), count in ordered[: policy.max_suggestions]:
        reason_action = action_by_reason[(code, field)]
        action = (
            ExclusionAction.RELAX_CONSTRAINT
            if reason_action is ExclusionAction.EXCLUDE
            else reason_action
        )
        suggestions.append(
            CandidateSuggestion(
                action=action,
                field=field.split(".", 1)[0],
                reason_code=code,
                affected_count=count,
            )
        )
    return reason_counts, tuple(suggestions)


def apply_hard_filters(
    goal: ShoppingGoal,
    snapshots: ProductSnapshot,
    *,
    policy: CandidatePolicy | None = None,
    candidates: Sequence[CandidateReference] | None = None,
    failures: Sequence[CandidateFailure] = (),
    search_query: str | None = None,
) -> CandidateSet:
    """Apply every hard rule without I/O, scoring, or implicit relaxation."""

    active_policy = policy or CandidatePolicy()
    recalled = (
        tuple(candidates)
        if candidates is not None
        else tuple(
            CandidateReference(item_id=entry.item_id, sources=(CandidateSource.SEARCH,))
            for entry in snapshots.entries
        )
    )
    entries_by_id = {entry.item_id: entry for entry in snapshots.entries}
    eligible: list[FilteredCandidate] = []
    excluded: list[FilteredCandidate] = []
    all_failures = list(failures)
    for candidate in recalled:
        entry = entries_by_id.get(candidate.item_id)
        if entry is None:
            all_failures.append(
                CandidateFailure(
                    stage="snapshot",
                    item_id=candidate.item_id,
                    code="missing_snapshot_result",
                    message="Snapshot omitted recalled candidate.",
                )
            )
            continue
        if entry.item is None:
            error = entry.error
            all_failures.append(
                CandidateFailure(
                    stage="snapshot",
                    item_id=candidate.item_id,
                    code=error.code if error is not None else "snapshot_error",
                    message=(
                        error.message
                        if error is not None
                        else "Snapshot candidate could not be read."
                    ),
                )
            )
            continue
        reasons = _candidate_reasons(goal, entry.item, active_policy)
        filtered = FilteredCandidate(
            item_id=candidate.item_id,
            sources=candidate.sources,
            snapshot_id=snapshots.snapshot_id,
            reasons=reasons,
        )
        (excluded if reasons else eligible).append(filtered)
    reason_counts, suggestions = _summaries(excluded, policy=active_policy)
    if eligible:
        suggestions = ()
    return CandidateSet(
        status=(
            CandidateSetStatus.READY if eligible else CandidateSetStatus.NO_CANDIDATE
        ),
        snapshot_id=snapshots.snapshot_id,
        search_query=search_query,
        recalled=recalled,
        eligible=tuple(eligible),
        excluded=tuple(excluded),
        failures=tuple(all_failures),
        reason_counts=reason_counts,
        suggestions=suggestions,
    )


def _normalized_item_ids(values: Iterable[object]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item_id = str(raw).strip()
        if not item_id or len(item_id) > 128:
            continue
        if item_id not in seen:
            normalized.append(item_id)
            seen.add(item_id)
    return tuple(normalized)


class CandidateService:
    """Recall bounded candidates, snapshot once, then invoke the pure filter."""

    def __init__(
        self,
        base_url: str,
        *,
        snapshot_client: _SnapshotClient,
        http_client: httpx.Client | None = None,
        policy: CandidatePolicy | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = base_url.rstrip("/")
        self.snapshot_client = snapshot_client
        self.http_client = http_client
        self.policy = policy or CandidatePolicy()
        self.timeout_seconds = timeout_seconds

    def build_candidate_set(
        self,
        *,
        turn_id: str,
        goal: ShoppingGoal,
        keywords: Iterable[str] = (),
        page_search_query: str | None = None,
        page_candidate_ids: Iterable[object] = (),
        explicit_item_ids: Iterable[object] = (),
        trace_context: AgentTraceContext | None = None,
    ) -> CandidateSet:
        query, category = self._search_terms(
            goal,
            keywords=keywords,
            page_search_query=page_search_query,
        )
        search_ids, search_failure = self._search(query=query, category=category)
        failures = [search_failure] if search_failure is not None else []
        recalled = self._merge_recalled(
            explicit_item_ids=explicit_item_ids,
            page_candidate_ids=page_candidate_ids,
            search_item_ids=search_ids,
        )
        if not recalled:
            return CandidateSet(
                status=CandidateSetStatus.NO_CANDIDATE,
                search_query=query or None,
                recalled=(),
                failures=tuple(failures),
            )
        snapshot_kwargs: dict[str, Any] = {
            "turn_id": str(turn_id).strip(),
            "item_ids": [candidate.item_id for candidate in recalled],
        }
        if trace_context is not None:
            snapshot_kwargs["trace_context"] = trace_context
        try:
            snapshots = self.snapshot_client.capture_for_turn(**snapshot_kwargs)
        except ProductSnapshotErrorBase as error:
            failures.extend(
                CandidateFailure(
                    stage="snapshot",
                    item_id=candidate.item_id,
                    code="snapshot_unavailable",
                    message=f"Product snapshot unavailable: {type(error).__name__}.",
                )
                for candidate in recalled
            )
            return CandidateSet(
                status=CandidateSetStatus.NO_CANDIDATE,
                search_query=query or None,
                recalled=recalled,
                failures=tuple(failures),
            )
        return apply_hard_filters(
            goal,
            snapshots,
            policy=self.policy,
            candidates=recalled,
            failures=failures,
            search_query=query or None,
        )

    def _search_terms(
        self,
        goal: ShoppingGoal,
        *,
        keywords: Iterable[str],
        page_search_query: str | None,
    ) -> tuple[str, str | None]:
        category_value = _constraint_value(goal, GoalField.CATEGORY)
        category = str(category_value).strip() if category_value is not None else None
        raw_parts: list[object] = []
        if page_search_query:
            raw_parts.append(page_search_query)
        raw_parts.extend(keywords)
        raw_parts.extend(
            item.value
            for collection in (goal.hard_constraints, goal.preferences)
            for item in collection
            if item.field
            in {GoalField.BRAND, GoalField.USAGE_SCENARIO, GoalField.SPECIFICATION}
        )
        parts: list[str] = []
        seen: set[str] = set()
        for raw in raw_parts:
            part = " ".join(str(raw).strip().split())
            folded = part.casefold()
            if part and folded not in seen:
                parts.append(part)
                seen.add(folded)
        query = " ".join(parts)[: self.policy.max_search_query_length].rstrip()
        return query, category

    def _search(
        self,
        *,
        query: str,
        category: str | None,
    ) -> tuple[tuple[str, ...], CandidateFailure | None]:
        if not query and not category:
            return (), None
        owns_client = self.http_client is None
        client = self.http_client or httpx.Client(timeout=self.timeout_seconds)
        params: dict[str, str] = {}
        if query:
            params["q"] = query
        if category:
            params["category"] = category
        try:
            response = client.get(
                f"{self.base_url}/search",
                params=params,
                timeout=self.timeout_seconds,
            )
            if response.status_code != 200:
                return (), CandidateFailure(
                    stage="search",
                    code=f"mock_api_status_{response.status_code}",
                    message="Product search request failed.",
                )
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                raise ValueError("search response must be an ok object")
            items = payload.get("items")
            if not isinstance(items, list):
                raise ValueError("search response items must be a list")
            item_ids = _normalized_item_ids(
                item.get("item_id")
                for item in items
                if isinstance(item, dict) and item.get("item_id") is not None
            )
            return item_ids, None
        except (httpx.HTTPError, ValueError) as error:
            return (), CandidateFailure(
                stage="search",
                code="invalid_search_response",
                message=f"Product search unavailable: {type(error).__name__}.",
            )
        finally:
            if owns_client:
                client.close()

    def _merge_recalled(
        self,
        *,
        explicit_item_ids: Iterable[object],
        page_candidate_ids: Iterable[object],
        search_item_ids: Iterable[object],
    ) -> tuple[CandidateReference, ...]:
        ordered_ids: list[str] = []
        sources_by_id: dict[str, set[CandidateSource]] = {}
        for source, values in (
            (CandidateSource.EXPLICIT, explicit_item_ids),
            (CandidateSource.PAGE, page_candidate_ids),
            (CandidateSource.SEARCH, search_item_ids),
        ):
            for item_id in _normalized_item_ids(values):
                if item_id in sources_by_id:
                    sources_by_id[item_id].add(source)
                    continue
                if len(ordered_ids) >= self.policy.max_candidates:
                    continue
                ordered_ids.append(item_id)
                sources_by_id[item_id] = {source}
        return tuple(
            CandidateReference(
                item_id=item_id,
                sources=tuple(
                    sorted(
                        sources_by_id[item_id], key=lambda value: _SOURCE_ORDER[value]
                    )
                ),
            )
            for item_id in ordered_ids
        )
