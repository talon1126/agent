"""Versioned domain models for an explicit, evidence-backed shopping goal."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    model_validator,
)


MAX_GOAL_ITEMS = 64
MAX_GOAL_TEXT_LENGTH = 512
MAX_EVIDENCE_QUOTE_LENGTH = 512
MAX_QUANTITY = 999
MAX_BUDGET = Decimal("1000000000")

GoalText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_GOAL_TEXT_LENGTH,
    ),
]
EvidenceQuote = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_EVIDENCE_QUOTE_LENGTH,
    ),
]


class GoalField(StrEnum):
    """Closed vocabulary understood by later filtering and clarification steps."""

    CATEGORY = "category"
    USAGE_SCENARIO = "usage_scenario"
    BUDGET_MIN = "budget_min"
    BUDGET_MAX = "budget_max"
    BRAND = "brand"
    SPECIFICATION = "specification"
    DELIVERY_DEADLINE = "delivery_deadline"
    QUANTITY = "quantity"
    FREEFORM_PREFERENCE = "freeform_preference"


class GoalSourceType(StrEnum):
    """Origin of a goal assertion or unanswered slot."""

    USER_TURN = "user_turn"
    PAGE_CONTEXT = "page_context"
    MODEL_INFERENCE = "model_inference"
    SYSTEM_DEFAULT = "system_default"


class DecisionStage(StrEnum):
    """Shopping decision lifecycle controlled by the domain model."""

    DISCOVERING = "discovering"
    CLARIFYING = "clarifying"
    SEARCHING = "searching"
    COMPARING = "comparing"
    DECIDED = "decided"


class InvalidGoalTransition(ValueError):
    """Raised when a caller attempts an unsupported decision-stage transition."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


class GoalEvidence(BaseModel):
    """Trace one goal field to its source without retaining an entire prompt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: GoalSourceType
    source_turn: Annotated[StrictInt, Field(ge=1)] | None = None
    quote: EvidenceQuote | None = None
    confidence: float = Field(ge=0, le=1)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        if not _is_timezone_aware(self.created_at) or not _is_timezone_aware(
            self.updated_at
        ):
            raise ValueError("evidence timestamps must include a timezone")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot predate created_at")
        if self.source_type is GoalSourceType.SYSTEM_DEFAULT:
            if self.source_turn is not None or self.quote is not None:
                raise ValueError(
                    "system_default evidence cannot claim a turn or original quote"
                )
        elif self.source_turn is None:
            raise ValueError("non-system evidence requires source_turn")
        if (
            self.source_type
            in {
                GoalSourceType.USER_TURN,
                GoalSourceType.PAGE_CONTEXT,
            }
            and self.quote is None
        ):
            raise ValueError("user and page evidence require an original quote")
        return self


class _GoalValue(BaseModel):
    """Shared value validation; public subclasses fix the semantic kind."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: GoalField
    value: Any
    attribute: GoalText | None = None
    evidence: GoalEvidence

    @model_validator(mode="before")
    @classmethod
    def normalize_typed_value(cls, raw: Any) -> Any:
        if not isinstance(raw, Mapping):
            return raw
        data = dict(raw)
        field = data.get("field")
        value = data.get("value")
        if field in {
            GoalField.BUDGET_MIN,
            GoalField.BUDGET_MAX,
            "budget_min",
            "budget_max",
        }:
            if isinstance(value, bool):
                return data
            try:
                data["value"] = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                return data
        elif field in {GoalField.DELIVERY_DEADLINE, "delivery_deadline"} and isinstance(
            value, str
        ):
            try:
                data["value"] = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return data
        return data

    @model_validator(mode="after")
    def validate_field_value(self) -> Self:
        if self.field in {GoalField.BUDGET_MIN, GoalField.BUDGET_MAX}:
            if isinstance(self.value, bool) or not isinstance(self.value, Decimal):
                raise ValueError("budget must be a finite decimal number")
            if not self.value.is_finite() or not 0 <= self.value <= MAX_BUDGET:
                raise ValueError(f"budget must be between 0 and {MAX_BUDGET}")
        elif self.field is GoalField.QUANTITY:
            if (
                isinstance(self.value, bool)
                or not isinstance(self.value, int)
                or not 1 <= self.value <= MAX_QUANTITY
            ):
                raise ValueError(
                    f"quantity must be an integer from 1 to {MAX_QUANTITY}"
                )
        elif self.field is GoalField.DELIVERY_DEADLINE:
            if not isinstance(self.value, datetime) or not _is_timezone_aware(
                self.value
            ):
                raise ValueError("delivery deadline must be a timezone-aware datetime")
        elif not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("goal text value cannot be blank")
        elif len(self.value.strip()) > MAX_GOAL_TEXT_LENGTH:
            raise ValueError("goal text value is too long")

        if self.field is GoalField.SPECIFICATION and self.attribute is None:
            raise ValueError("specification requires an attribute")
        if self.field is not GoalField.SPECIFICATION and self.attribute is not None:
            raise ValueError("attribute is only valid for specification fields")
        return self

    @property
    def semantic_key(self) -> tuple[GoalField, str | None]:
        attribute = self.attribute.casefold() if self.attribute else None
        return self.field, attribute


class Constraint(_GoalValue):
    """A user- or page-backed condition that candidates must satisfy."""

    kind: Literal["hard"] = "hard"

    @model_validator(mode="after")
    def validate_hard_source(self) -> Self:
        if self.evidence.source_type not in {
            GoalSourceType.USER_TURN,
            GoalSourceType.PAGE_CONTEXT,
        }:
            raise ValueError(
                "hard constraint requires user_turn or page_context evidence"
            )
        if self.field is GoalField.FREEFORM_PREFERENCE:
            raise ValueError("freeform_preference cannot be a hard constraint")
        return self


class Preference(_GoalValue):
    """A condition that ranking may trade off against stronger preferences."""

    kind: Literal["soft"] = "soft"


class Exclusion(_GoalValue):
    """An explicit value that must be removed from candidate consideration."""

    kind: Literal["exclude"] = "exclude"

    @model_validator(mode="after")
    def validate_exclusion_source(self) -> Self:
        if self.evidence.source_type not in {
            GoalSourceType.USER_TURN,
            GoalSourceType.PAGE_CONTEXT,
        }:
            raise ValueError("exclusion requires user_turn or page_context evidence")
        if self.field in {
            GoalField.BUDGET_MIN,
            GoalField.BUDGET_MAX,
            GoalField.DELIVERY_DEADLINE,
            GoalField.QUANTITY,
        }:
            raise ValueError(
                f"{self.field.value} cannot be represented as an exclusion"
            )
        return self


class OpenSlot(BaseModel):
    """A controlled shopping field that still requires an answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["unknown"] = "unknown"
    field: GoalField
    attribute: GoalText | None = None
    question: GoalText
    evidence: GoalEvidence

    @model_validator(mode="after")
    def validate_attribute(self) -> Self:
        if self.field is GoalField.SPECIFICATION and self.attribute is None:
            raise ValueError("specification open slot requires an attribute")
        if self.field is not GoalField.SPECIFICATION and self.attribute is not None:
            raise ValueError("attribute is only valid for specification fields")
        return self

    @property
    def semantic_key(self) -> tuple[GoalField, str | None]:
        attribute = self.attribute.casefold() if self.attribute else None
        return self.field, attribute


_ALLOWED_TRANSITIONS: dict[DecisionStage, frozenset[DecisionStage]] = {
    DecisionStage.DISCOVERING: frozenset(
        {DecisionStage.CLARIFYING, DecisionStage.SEARCHING}
    ),
    DecisionStage.CLARIFYING: frozenset(
        {DecisionStage.DISCOVERING, DecisionStage.SEARCHING}
    ),
    DecisionStage.SEARCHING: frozenset(
        {DecisionStage.CLARIFYING, DecisionStage.COMPARING}
    ),
    DecisionStage.COMPARING: frozenset(
        {
            DecisionStage.CLARIFYING,
            DecisionStage.SEARCHING,
            DecisionStage.DECIDED,
        }
    ),
    DecisionStage.DECIDED: frozenset({DecisionStage.CLARIFYING}),
}


class ShoppingGoal(BaseModel):
    """One immutable snapshot of the user's explicit shopping objective."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"
    revision: int = Field(default=0, ge=0)
    decision_stage: DecisionStage = DecisionStage.DISCOVERING
    stage_reason: GoalText | None = None
    hard_constraints: tuple[Constraint, ...] = Field(
        default_factory=tuple,
        max_length=MAX_GOAL_ITEMS,
    )
    preferences: tuple[Preference, ...] = Field(
        default_factory=tuple,
        max_length=MAX_GOAL_ITEMS,
    )
    exclusions: tuple[Exclusion, ...] = Field(
        default_factory=tuple,
        max_length=MAX_GOAL_ITEMS,
    )
    open_slots: tuple[OpenSlot, ...] = Field(
        default_factory=tuple,
        max_length=MAX_GOAL_ITEMS,
    )

    @model_validator(mode="after")
    def validate_goal_invariants(self) -> Self:
        for collection_name in (
            "hard_constraints",
            "preferences",
            "exclusions",
            "open_slots",
        ):
            items = getattr(self, collection_name)
            keys = [item.semantic_key for item in items]
            if len(keys) != len(set(keys)):
                raise ValueError(f"duplicate semantic field in {collection_name}")

        minimum = self._hard_budget(GoalField.BUDGET_MIN)
        maximum = self._hard_budget(GoalField.BUDGET_MAX)
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("budget_min cannot exceed budget_max")

        included_brands = {
            str(item.value).strip().casefold()
            for item in self.hard_constraints
            if item.field is GoalField.BRAND
        }
        excluded_brands = {
            str(item.value).strip().casefold()
            for item in self.exclusions
            if item.field is GoalField.BRAND
        }
        if included_brands & excluded_brands:
            raise ValueError("a brand cannot be both included and excluded")

        known_keys = {
            item.semantic_key
            for collection in (
                self.hard_constraints,
                self.preferences,
                self.exclusions,
            )
            for item in collection
        }
        unresolved_known = known_keys & {item.semantic_key for item in self.open_slots}
        if unresolved_known:
            raise ValueError("an answered goal field cannot remain an open slot")
        return self

    def _hard_budget(self, field: GoalField) -> Decimal | None:
        for item in self.hard_constraints:
            if item.field is field:
                return item.value
        return None

    def transition_to(
        self,
        target: DecisionStage,
        *,
        clarification_reason: str | None = None,
    ) -> ShoppingGoal:
        """Return the next immutable state after validating lifecycle movement."""

        if target is self.decision_stage:
            return self
        if target not in _ALLOWED_TRANSITIONS[self.decision_stage]:
            raise InvalidGoalTransition(
                f"cannot transition from {self.decision_stage.value} to {target.value}"
            )
        normalized_reason = (
            clarification_reason.strip() if clarification_reason is not None else None
        )
        if target is DecisionStage.CLARIFYING and not normalized_reason:
            raise InvalidGoalTransition(
                "transition to clarifying requires clarification_reason"
            )
        return self.model_copy(
            update={
                "decision_stage": target,
                "stage_reason": normalized_reason,
                "revision": self.revision + 1,
            }
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ShoppingGoal:
        """Read v1 or deterministically upgrade the documented unversioned shape."""

        data = deepcopy(dict(payload))
        version = data.get("schema_version")
        if version not in {None, "v1"}:
            raise ValueError(f"unsupported shopping goal schema_version: {version}")
        if version is None:
            aliases = {
                "constraints": "hard_constraints",
                "soft_preferences": "preferences",
                "excluded": "exclusions",
                "unknown": "open_slots",
                "stage": "decision_stage",
            }
            for legacy_name, current_name in aliases.items():
                if legacy_name in data:
                    if current_name in data:
                        raise ValueError(
                            f"legacy field {legacy_name} conflicts with {current_name}"
                        )
                    data[current_name] = data.pop(legacy_name)
            for collection_name, kind in (
                ("hard_constraints", "hard"),
                ("preferences", "soft"),
                ("exclusions", "exclude"),
                ("open_slots", "unknown"),
            ):
                for item in data.get(collection_name, ()):
                    if isinstance(item, dict):
                        item.setdefault("kind", kind)
            data["schema_version"] = "v1"
        return cls.model_validate(data)
