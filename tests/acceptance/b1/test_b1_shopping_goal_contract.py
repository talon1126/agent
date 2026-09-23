"""Frozen acceptance contract for B1 shopping-goal domain models."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[3]
AI_SERVICE_ROOT = ROOT / "services" / "ai-service"
sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.routers.AImodel.shopping_goal import (  # noqa: E402
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


NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _evidence(
    *,
    source_type: GoalSourceType = GoalSourceType.USER_TURN,
    source_turn: int | None = 1,
    quote: str | None = "预算不超过 5000 元",
    confidence: float = 1.0,
) -> GoalEvidence:
    return GoalEvidence(
        source_type=source_type,
        source_turn=source_turn,
        quote=quote,
        confidence=confidence,
        created_at=NOW,
        updated_at=NOW,
    )


def _constraint(
    field: GoalField,
    value: object,
    *,
    attribute: str | None = None,
) -> Constraint:
    return Constraint(
        field=field,
        value=value,
        attribute=attribute,
        evidence=_evidence(),
    )


def test_empty_goal_has_stable_defaults() -> None:
    goal = ShoppingGoal()

    assert goal.schema_version == "v1"
    assert goal.decision_stage is DecisionStage.DISCOVERING
    assert goal.hard_constraints == ()
    assert goal.preferences == ()
    assert goal.exclusions == ()
    assert goal.open_slots == ()


def test_complete_goal_supports_every_required_field_family() -> None:
    goal = ShoppingGoal(
        hard_constraints=(
            _constraint(GoalField.CATEGORY, "冰箱"),
            _constraint(GoalField.USAGE_SCENARIO, "三口之家"),
            _constraint(GoalField.BUDGET_MIN, 3000),
            _constraint(GoalField.BUDGET_MAX, 5000),
            _constraint(GoalField.BRAND, "海尔"),
            _constraint(GoalField.SPECIFICATION, "500L", attribute="capacity"),
            _constraint(
                GoalField.DELIVERY_DEADLINE,
                datetime(2026, 9, 25, 18, 0, tzinfo=UTC),
            ),
            _constraint(GoalField.QUANTITY, 1),
        ),
        preferences=(
            Preference(
                field=GoalField.FREEFORM_PREFERENCE,
                value="低噪音",
                evidence=_evidence(confidence=0.8),
            ),
        ),
        exclusions=(
            Exclusion(
                field=GoalField.BRAND,
                value="某品牌",
                evidence=_evidence(quote="不要某品牌"),
            ),
        ),
        open_slots=(
            OpenSlot(
                field=GoalField.SPECIFICATION,
                attribute="door_style",
                question="偏好双开门还是法式多门？",
                evidence=_evidence(
                    source_type=GoalSourceType.SYSTEM_DEFAULT,
                    source_turn=None,
                    quote=None,
                    confidence=1,
                ),
            ),
        ),
        decision_stage=DecisionStage.CLARIFYING,
    )

    assert len(goal.hard_constraints) == 8
    assert goal.preferences[0].kind == "soft"
    assert goal.exclusions[0].kind == "exclude"
    assert goal.open_slots[0].kind == "unknown"


@pytest.mark.parametrize(
    ("model", "kind"),
    [
        (Constraint, "soft"),
        (Preference, "hard"),
        (Exclusion, "unknown"),
        (OpenSlot, "exclude"),
    ],
)
def test_goal_kinds_cannot_be_mixed(model: type, kind: str) -> None:
    payload = {
        "kind": kind,
        "field": GoalField.CATEGORY,
        "evidence": _evidence(),
    }
    if model is not OpenSlot:
        payload["value"] = "冰箱"
    else:
        payload["question"] = "需要什么品类？"

    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_confidence_must_be_between_zero_and_one(confidence: float) -> None:
    with pytest.raises(ValidationError):
        _evidence(confidence=confidence)


def test_user_evidence_requires_turn_and_original_quote() -> None:
    with pytest.raises(ValidationError):
        _evidence(source_turn=None)
    with pytest.raises(ValidationError):
        _evidence(quote=None)


def test_page_context_is_a_valid_hard_constraint_source() -> None:
    evidence = _evidence(
        source_type=GoalSourceType.PAGE_CONTEXT,
        quote="当前商品：海尔冰箱",
    )
    constraint = Constraint(
        field=GoalField.BRAND,
        value="海尔",
        evidence=evidence,
    )

    assert constraint.evidence.source_type is GoalSourceType.PAGE_CONTEXT


def test_model_inference_cannot_claim_a_hard_constraint() -> None:
    evidence = _evidence(source_type=GoalSourceType.MODEL_INFERENCE)

    with pytest.raises(ValidationError, match="hard constraint"):
        Constraint(field=GoalField.BRAND, value="海尔", evidence=evidence)


def test_evidence_update_cannot_predate_creation() -> None:
    with pytest.raises(ValidationError):
        GoalEvidence(
            source_type=GoalSourceType.USER_TURN,
            source_turn=1,
            quote="预算 5000",
            confidence=1,
            created_at=NOW,
            updated_at=NOW - timedelta(seconds=1),
        )


@pytest.mark.parametrize("field", [GoalField.BUDGET_MIN, GoalField.BUDGET_MAX])
def test_budget_values_cannot_be_negative(field: GoalField) -> None:
    with pytest.raises(ValidationError, match="budget"):
        _constraint(field, -1)


def test_budget_minimum_cannot_exceed_maximum() -> None:
    with pytest.raises(ValidationError, match="budget_min"):
        ShoppingGoal(
            hard_constraints=(
                _constraint(GoalField.BUDGET_MIN, 6000),
                _constraint(GoalField.BUDGET_MAX, 5000),
            )
        )


@pytest.mark.parametrize("quantity", [0, 1000, True, 1.5])
def test_quantity_has_strict_integer_boundaries(quantity: object) -> None:
    with pytest.raises(ValidationError, match="quantity"):
        _constraint(GoalField.QUANTITY, quantity)


def test_delivery_deadline_requires_timezone() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        _constraint(GoalField.DELIVERY_DEADLINE, datetime(2026, 9, 25, 18, 0))


def test_text_value_cannot_be_blank() -> None:
    with pytest.raises(ValidationError, match="blank"):
        _constraint(GoalField.CATEGORY, "   ")


def test_specification_requires_an_attribute_name() -> None:
    with pytest.raises(ValidationError, match="attribute"):
        _constraint(GoalField.SPECIFICATION, "500L")


def test_brand_include_and_exclude_cannot_overlap_case_insensitively() -> None:
    with pytest.raises(ValidationError, match="included and excluded"):
        ShoppingGoal(
            hard_constraints=(_constraint(GoalField.BRAND, "SONY"),),
            exclusions=(
                Exclusion(
                    field=GoalField.BRAND,
                    value="sony",
                    evidence=_evidence(quote="不要 sony"),
                ),
            ),
        )


def test_duplicate_semantic_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        ShoppingGoal(
            hard_constraints=(
                _constraint(GoalField.CATEGORY, "冰箱"),
                _constraint(GoalField.CATEGORY, "洗衣机"),
            )
        )


def test_json_serialization_round_trip_is_lossless() -> None:
    goal = ShoppingGoal(
        hard_constraints=(
            _constraint(GoalField.CATEGORY, "冰箱"),
            _constraint(GoalField.BUDGET_MAX, 5000),
        ),
        decision_stage=DecisionStage.SEARCHING,
    )

    restored = ShoppingGoal.model_validate_json(goal.model_dump_json())

    assert restored == goal


def test_missing_schema_version_uses_current_shape_legacy_strategy() -> None:
    payload = ShoppingGoal(
        hard_constraints=(_constraint(GoalField.CATEGORY, "冰箱"),)
    ).model_dump(mode="json")
    payload.pop("schema_version")

    restored = ShoppingGoal.from_payload(payload)

    assert restored.schema_version == "v1"
    assert restored.hard_constraints[0].value == "冰箱"


def test_legacy_collection_aliases_are_upgraded() -> None:
    legacy = {
        "constraints": [
            {
                "field": "category",
                "value": "冰箱",
                "evidence": _evidence().model_dump(mode="json"),
            }
        ],
        "soft_preferences": [],
        "excluded": [],
        "unknown": [],
        "stage": "discovering",
    }

    restored = ShoppingGoal.from_payload(legacy)

    assert restored.hard_constraints[0].kind == "hard"
    assert restored.hard_constraints[0].field is GoalField.CATEGORY


def test_unknown_schema_version_is_rejected() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        ShoppingGoal.from_payload({"schema_version": "v99"})


def test_decision_stage_dictionary_is_closed() -> None:
    assert {stage.value for stage in DecisionStage} == {
        "discovering",
        "clarifying",
        "searching",
        "comparing",
        "decided",
    }


def test_discovering_cannot_jump_directly_to_decided() -> None:
    with pytest.raises(InvalidGoalTransition):
        ShoppingGoal().transition_to(DecisionStage.DECIDED)


def test_normal_forward_transition_is_allowed() -> None:
    searching = ShoppingGoal().transition_to(DecisionStage.SEARCHING)

    assert searching.decision_stage is DecisionStage.SEARCHING


def test_return_to_clarification_requires_a_reason() -> None:
    searching = ShoppingGoal(decision_stage=DecisionStage.SEARCHING)

    with pytest.raises(InvalidGoalTransition, match="clarification_reason"):
        searching.transition_to(DecisionStage.CLARIFYING)

    clarified = searching.transition_to(
        DecisionStage.CLARIFYING,
        clarification_reason="预算上下限冲突",
    )
    assert clarified.decision_stage is DecisionStage.CLARIFYING


def test_models_reject_undeclared_fields() -> None:
    payload = json.loads(ShoppingGoal().model_dump_json())
    payload["free_text_constraints"] = ["预算五千"]

    with pytest.raises(ValidationError):
        ShoppingGoal.model_validate(payload)
