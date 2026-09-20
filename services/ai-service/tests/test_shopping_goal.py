"""Unit tests for the B1 shopping-goal domain model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.routers.AImodel.shopping_goal import (
    Constraint,
    DecisionStage,
    Exclusion,
    GoalEvidence,
    GoalField,
    GoalSourceType,
    InvalidGoalTransition,
    OpenSlot,
    Preference,
    ShoppingGoal,
)


NOW = datetime(2026, 9, 20, 13, 0, tzinfo=UTC)


def evidence(
    source_type: GoalSourceType = GoalSourceType.USER_TURN,
    *,
    quote: str | None = "用户原话",
    turn: int | None = 1,
    confidence: float = 1,
) -> GoalEvidence:
    return GoalEvidence(
        source_type=source_type,
        source_turn=turn,
        quote=quote,
        confidence=confidence,
        created_at=NOW,
        updated_at=NOW,
    )


def constraint(
    field: GoalField, value: object, *, attribute: str | None = None
) -> Constraint:
    return Constraint(
        field=field,
        value=value,
        attribute=attribute,
        evidence=evidence(),
    )


def test_empty_goal_is_immutable_and_versioned() -> None:
    goal = ShoppingGoal()
    assert goal.schema_version == "v1"
    assert goal.revision == 0
    with pytest.raises(ValidationError):
        goal.revision = 2  # type: ignore[misc]


def test_hard_soft_exclude_and_unknown_are_separate_models() -> None:
    hard = constraint(GoalField.CATEGORY, "手机")
    soft = Preference(
        field=GoalField.FREEFORM_PREFERENCE,
        value="拍照好",
        evidence=evidence(confidence=0.7),
    )
    excluded = Exclusion(
        field=GoalField.BRAND,
        value="Apple",
        evidence=evidence(quote="不要 Apple"),
    )
    unknown = OpenSlot(
        field=GoalField.BUDGET_MAX,
        question="最高预算是多少？",
        evidence=evidence(
            GoalSourceType.SYSTEM_DEFAULT,
            quote=None,
            turn=None,
        ),
    )

    assert (hard.kind, soft.kind, excluded.kind, unknown.kind) == (
        "hard",
        "soft",
        "exclude",
        "unknown",
    )


@pytest.mark.parametrize("value", [-1, Decimal("NaN"), Decimal("Infinity")])
def test_budget_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValidationError, match="budget"):
        constraint(GoalField.BUDGET_MAX, value)


def test_budget_is_normalized_for_stable_round_trip() -> None:
    item = constraint(GoalField.BUDGET_MAX, "5999.50")
    assert item.value == Decimal("5999.50")
    restored = Constraint.model_validate_json(item.model_dump_json())
    assert restored == item


@pytest.mark.parametrize("value", [0, 1000, True, 1.5, "2"])
def test_quantity_is_a_bounded_strict_integer(value: object) -> None:
    with pytest.raises(ValidationError, match="quantity"):
        constraint(GoalField.QUANTITY, value)


def test_delivery_deadline_round_trips_as_aware_datetime() -> None:
    deadline = datetime(2026, 9, 22, 18, 0, tzinfo=UTC)
    item = constraint(GoalField.DELIVERY_DEADLINE, deadline)
    restored = Constraint.model_validate_json(item.model_dump_json())
    assert restored.value == deadline


def test_naive_evidence_and_deadline_timestamps_are_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        GoalEvidence(
            source_type=GoalSourceType.USER_TURN,
            source_turn=1,
            quote="明天送到",
            confidence=1,
            created_at=datetime(2026, 9, 20),
            updated_at=NOW,
        )
    with pytest.raises(ValidationError, match="timezone"):
        constraint(GoalField.DELIVERY_DEADLINE, datetime(2026, 9, 22))


def test_updated_at_cannot_precede_created_at() -> None:
    with pytest.raises(ValidationError, match="predate"):
        GoalEvidence(
            source_type=GoalSourceType.USER_TURN,
            source_turn=1,
            quote="用户原话",
            confidence=1,
            created_at=NOW,
            updated_at=NOW - timedelta(seconds=1),
        )


def test_system_default_cannot_forge_user_lineage() -> None:
    with pytest.raises(ValidationError, match="cannot claim"):
        evidence(GoalSourceType.SYSTEM_DEFAULT)


@pytest.mark.parametrize(
    "source_type",
    [GoalSourceType.MODEL_INFERENCE, GoalSourceType.SYSTEM_DEFAULT],
)
def test_hard_constraint_requires_authoritative_evidence(
    source_type: GoalSourceType,
) -> None:
    kwargs = (
        {"quote": None, "turn": None}
        if source_type is GoalSourceType.SYSTEM_DEFAULT
        else {}
    )
    with pytest.raises(ValidationError, match="hard constraint"):
        Constraint(
            field=GoalField.BRAND,
            value="华为",
            evidence=evidence(source_type, **kwargs),
        )


def test_specification_identity_includes_attribute() -> None:
    goal = ShoppingGoal(
        hard_constraints=(
            constraint(GoalField.SPECIFICATION, "16GB", attribute="memory"),
            constraint(GoalField.SPECIFICATION, "512GB", attribute="storage"),
        )
    )
    assert len(goal.hard_constraints) == 2


def test_duplicate_specification_attribute_is_rejected_case_insensitively() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        ShoppingGoal(
            hard_constraints=(
                constraint(GoalField.SPECIFICATION, "16GB", attribute="Memory"),
                constraint(GoalField.SPECIFICATION, "32GB", attribute="memory"),
            )
        )


def test_open_slot_cannot_duplicate_a_known_field() -> None:
    with pytest.raises(ValidationError, match="open slot"):
        ShoppingGoal(
            hard_constraints=(constraint(GoalField.CATEGORY, "手机"),),
            open_slots=(
                OpenSlot(
                    field=GoalField.CATEGORY,
                    question="需要什么品类？",
                    evidence=evidence(
                        GoalSourceType.SYSTEM_DEFAULT,
                        quote=None,
                        turn=None,
                    ),
                ),
            ),
        )


def test_brand_conflict_is_case_insensitive() -> None:
    with pytest.raises(ValidationError, match="included and excluded"):
        ShoppingGoal(
            hard_constraints=(constraint(GoalField.BRAND, "Sony"),),
            exclusions=(
                Exclusion(
                    field=GoalField.BRAND,
                    value="SONY",
                    evidence=evidence(quote="不要 SONY"),
                ),
            ),
        )


def test_hard_budget_range_is_ordered() -> None:
    with pytest.raises(ValidationError, match="budget_min"):
        ShoppingGoal(
            hard_constraints=(
                constraint(GoalField.BUDGET_MIN, 9000),
                constraint(GoalField.BUDGET_MAX, 5000),
            )
        )


def test_full_goal_json_round_trip_is_lossless() -> None:
    goal = ShoppingGoal(
        revision=3,
        decision_stage=DecisionStage.COMPARING,
        hard_constraints=(
            constraint(GoalField.CATEGORY, "手机"),
            constraint(GoalField.BUDGET_MAX, 6000),
        ),
        preferences=(
            Preference(
                field=GoalField.FREEFORM_PREFERENCE,
                value="拍照好",
                evidence=evidence(confidence=0.8),
            ),
        ),
    )
    assert ShoppingGoal.model_validate_json(goal.model_dump_json()) == goal


def test_unversioned_current_shape_upgrades_to_v1() -> None:
    payload = ShoppingGoal().model_dump(mode="json")
    payload.pop("schema_version")
    assert ShoppingGoal.from_payload(payload).schema_version == "v1"


def test_documented_legacy_aliases_upgrade_to_typed_collections() -> None:
    goal = ShoppingGoal.from_payload(
        {
            "constraints": [
                {
                    "field": "category",
                    "value": "手机",
                    "evidence": evidence().model_dump(mode="json"),
                }
            ],
            "stage": "searching",
        }
    )
    assert goal.hard_constraints[0].kind == "hard"
    assert goal.decision_stage is DecisionStage.SEARCHING


def test_legacy_and_current_names_cannot_be_mixed() -> None:
    with pytest.raises(ValueError, match="conflicts"):
        ShoppingGoal.from_payload({"constraints": [], "hard_constraints": []})


@pytest.mark.parametrize("version", ["v0", "v2", 1])
def test_unknown_explicit_schema_version_is_rejected(version: object) -> None:
    with pytest.raises(ValueError, match="schema_version"):
        ShoppingGoal.from_payload({"schema_version": version})


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (DecisionStage.DISCOVERING, DecisionStage.DECIDED),
        (DecisionStage.SEARCHING, DecisionStage.DECIDED),
        (DecisionStage.DECIDED, DecisionStage.SEARCHING),
    ],
)
def test_invalid_stage_jumps_are_rejected(
    start: DecisionStage, target: DecisionStage
) -> None:
    with pytest.raises(InvalidGoalTransition):
        ShoppingGoal(decision_stage=start).transition_to(target)


def test_forward_stage_transition_increments_revision() -> None:
    result = ShoppingGoal().transition_to(DecisionStage.SEARCHING)
    assert result.decision_stage is DecisionStage.SEARCHING
    assert result.revision == 1


def test_return_to_clarifying_requires_and_records_reason() -> None:
    goal = ShoppingGoal(decision_stage=DecisionStage.COMPARING)
    with pytest.raises(InvalidGoalTransition, match="clarification_reason"):
        goal.transition_to(DecisionStage.CLARIFYING)
    result = goal.transition_to(
        DecisionStage.CLARIFYING,
        clarification_reason="候选均超出硬预算",
    )
    assert result.stage_reason == "候选均超出硬预算"


def test_extra_fields_are_forbidden_at_every_level() -> None:
    with pytest.raises(ValidationError):
        ShoppingGoal.model_validate({"raw_constraints": ["预算 5000"]})
    with pytest.raises(ValidationError):
        GoalEvidence.model_validate(
            {
                **evidence().model_dump(mode="json"),
                "full_prompt": "should not be retained",
            }
        )
