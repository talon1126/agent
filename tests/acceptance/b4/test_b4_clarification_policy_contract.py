"""Frozen acceptance contract for B4 deterministic clarification policy."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "ai-service"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.routers.AImodel.clarification import (  # noqa: E402
    ClarificationHistory,
    ClarificationOptionSeed,
    ClarificationReason,
    evaluate_clarification_fixture,
    select_clarification,
)
from app.routers.AImodel.goal_state import (  # noqa: E402
    CandidateStatus,
    GoalConflict,
    GoalConflictCode,
)
from app.routers.AImodel.schemas import (  # noqa: E402
    AiModelClarificationPayload,
)
from app.routers.AImodel.shopping_goal import (  # noqa: E402
    Constraint,
    Exclusion,
    GoalEvidence,
    GoalField,
    GoalSourceType,
    OpenSlot,
    ShoppingGoal,
)


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
FIXTURE_PATH = ROOT / "fixtures" / "evals" / "clarification_policy_cases.json"
A2_FIXTURE_PATH = ROOT / "fixtures" / "evals" / "shopping_agent_scenarios.json"


def evidence(quote: str = "用户原话", *, turn: int = 1) -> GoalEvidence:
    return GoalEvidence(
        source_type=GoalSourceType.USER_TURN,
        source_turn=turn,
        quote=quote,
        confidence=1,
        created_at=NOW,
        updated_at=NOW,
    )


def hard(
    field: GoalField,
    value: object,
    *,
    attribute: str | None = None,
    quote: str = "用户原话",
) -> Constraint:
    return Constraint(
        field=field,
        value=value,
        attribute=attribute,
        evidence=evidence(quote),
    )


def slot(
    field: GoalField,
    question: str,
    *,
    attribute: str | None = None,
) -> OpenSlot:
    return OpenSlot(
        field=field,
        attribute=attribute,
        question=question,
        evidence=GoalEvidence(
            source_type=GoalSourceType.SYSTEM_DEFAULT,
            confidence=0,
            created_at=NOW,
            updated_at=NOW,
        ),
    )


def budget_conflict() -> GoalConflict:
    minimum = hard(GoalField.BUDGET_MIN, 5000, quote="至少五千")
    maximum = hard(GoalField.BUDGET_MAX, 3000, quote="最多三千")
    return GoalConflict(
        code=GoalConflictCode.BUDGET_RANGE_REVERSED,
        field=GoalField.BUDGET_MAX,
        values=(minimum, maximum),
        sources=(minimum.evidence, maximum.evidence),
        clarification_topic="预算范围",
    )


def brand_conflict() -> GoalConflict:
    included = hard(GoalField.BRAND, "Xiaomi", quote="只要小米")
    excluded = Exclusion(
        field=GoalField.BRAND,
        value="Xiaomi",
        evidence=evidence("不要小米"),
    )
    return GoalConflict(
        code=GoalConflictCode.BRAND_INCLUDED_AND_EXCLUDED,
        field=GoalField.BRAND,
        values=(included, excluded),
        sources=(included.evidence, excluded.evidence),
        clarification_topic="品牌取舍",
    )


def test_blocking_conflict_wins_over_missing_category_and_other_slots() -> None:
    goal = ShoppingGoal(
        open_slots=(
            slot(GoalField.CATEGORY, "想买哪一类商品？"),
            slot(GoalField.USAGE_SCENARIO, "主要在什么场景使用？"),
        )
    )

    decision = select_clarification(goal, conflicts=(brand_conflict(),))

    assert decision.should_ask is True
    assert decision.may_proceed is False
    assert decision.slot_key == "brand"
    assert decision.reason is ClarificationReason.BLOCKING_CONFLICT
    assert isinstance(decision.payload, AiModelClarificationPayload)


def test_missing_category_wins_over_high_impact_optional_slots() -> None:
    goal = ShoppingGoal(
        open_slots=(
            slot(GoalField.BUDGET_MAX, "预算上限是多少？"),
            slot(
                GoalField.SPECIFICATION,
                "需要多大容量？",
                attribute="容量",
            ),
        )
    )

    decision = select_clarification(goal)

    assert decision.slot_key == "category"
    assert decision.reason is ClarificationReason.MISSING_CATEGORY


def test_topic_and_option_values_are_stable_and_a4_compatible() -> None:
    goal = ShoppingGoal(
        hard_constraints=(hard(GoalField.CATEGORY, "electronics"),),
        open_slots=(slot(GoalField.BUDGET_MAX, "预算上限是多少？"),),
    )

    first = select_clarification(goal)
    replay = select_clarification(goal)

    assert first == replay
    assert first.slot_key == "budget_max"
    assert first.payload is not None
    assert 2 <= len(first.payload.options) <= 5
    assert len({item.option_id for item in first.payload.options}) == len(
        first.payload.options
    )
    assert len({item.value for item in first.payload.options}) == len(
        first.payload.options
    )
    assert any(item.value == "skip:budget_max" for item in first.payload.options)
    assert all(len(item.label) <= 512 for item in first.payload.options)


def test_skipped_and_recent_slots_do_not_loop_but_new_conflict_can_reask() -> None:
    goal = ShoppingGoal(
        hard_constraints=(hard(GoalField.CATEGORY, "electronics"),),
        open_slots=(slot(GoalField.BUDGET_MAX, "预算上限是多少？"),),
    )
    history = ClarificationHistory(
        skipped_slot_keys=("budget_max",),
        recent_slot_keys=("budget_max",),
    )

    suppressed = select_clarification(
        goal,
        history=history,
        candidate_status=CandidateStatus.AVAILABLE,
    )
    conflict = select_clarification(
        goal,
        conflicts=(budget_conflict(),),
        history=history,
        candidate_status=CandidateStatus.AVAILABLE,
    )

    assert suppressed.should_ask is False
    assert suppressed.may_proceed is True
    assert suppressed.recommend_with_uncertainty is True
    assert suppressed.critical_unknowns == ("budget_max",)
    assert conflict.should_ask is True
    assert conflict.slot_key == "budget_max"


def test_sortable_candidates_without_blocking_gap_are_not_questioned() -> None:
    goal = ShoppingGoal(
        hard_constraints=(
            hard(GoalField.CATEGORY, "electronics"),
            hard(GoalField.BUDGET_MAX, 5000),
        )
    )

    decision = select_clarification(
        goal,
        candidate_status=CandidateStatus.AVAILABLE,
    )

    assert decision.should_ask is False
    assert decision.may_proceed is True
    assert decision.recommend_with_uncertainty is False
    assert decision.payload is None
    assert decision.slot_key is None


def test_only_one_highest_value_slot_is_returned() -> None:
    goal = ShoppingGoal(
        hard_constraints=(hard(GoalField.CATEGORY, "electronics"),),
        open_slots=(
            slot(GoalField.FREEFORM_PREFERENCE, "还有其他偏好吗？"),
            slot(GoalField.USAGE_SCENARIO, "主要在什么场景使用？"),
            slot(GoalField.DELIVERY_DEADLINE, "最晚什么时候送到？"),
        ),
    )

    decision = select_clarification(goal)

    assert decision.slot_key == "delivery_deadline"
    assert decision.payload is not None
    assert decision.payload.response_type == "clarification"


def test_spec_options_come_only_from_known_attributes_plus_control_values() -> None:
    goal = ShoppingGoal(
        hard_constraints=(hard(GoalField.CATEGORY, "electronics"),),
        open_slots=(slot(GoalField.SPECIFICATION, "容量要多大？", attribute="容量"),),
    )
    known = {
        "容量": (
            ClarificationOptionSeed(label="4L", value="4L"),
            ClarificationOptionSeed(label="6.5L", value="6.5L"),
        )
    }

    decision = select_clarification(goal, known_attribute_options=known)

    assert decision.payload is not None
    assert {option.value for option in decision.payload.options} == {
        "specification:容量:4L",
        "specification:容量:6.5L",
        "skip:specification:容量",
    }
    assert all("8L" not in option.label for option in decision.payload.options)


def test_spec_without_known_values_offers_input_and_skip_not_invented_specs() -> None:
    goal = ShoppingGoal(
        hard_constraints=(hard(GoalField.CATEGORY, "electronics"),),
        open_slots=(slot(GoalField.SPECIFICATION, "容量要多大？", attribute="容量"),),
    )

    decision = select_clarification(goal)

    assert decision.payload is not None
    assert [option.value for option in decision.payload.options] == [
        "input:specification:容量",
        "skip:specification:容量",
    ]


def test_question_renderer_failure_uses_safe_template_without_internal_key() -> None:
    goal = ShoppingGoal(
        hard_constraints=(hard(GoalField.CATEGORY, "electronics"),),
        open_slots=(slot(GoalField.BUDGET_MAX, "预算上限是多少？"),),
    )

    def fail_renderer(_slot_key: str) -> NoReturn:
        raise RuntimeError("provider leaked budget_max token")

    decision = select_clarification(goal, question_renderer=fail_renderer)

    assert decision.payload is not None
    assert decision.payload.answer == "你的预算上限是多少？"
    assert "budget_max" not in decision.payload.answer
    assert "provider" not in decision.model_dump_json()


def test_a2_fixture_is_complete_and_metrics_are_machine_comparable() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    a2_fixture = json.loads(A2_FIXTURE_PATH.read_text(encoding="utf-8"))
    expected_by_id = {
        item["scenario_id"]: item["expected_response_type"] == "clarification"
        for item in a2_fixture["scenarios"]
    }
    actual_by_id = {
        item["source_scenario_id"]: item["expected_should_ask"]
        for item in fixture["cases"]
    }

    assert fixture["metadata"]["source_scenario_count"] == 40
    assert actual_by_id == expected_by_id
    assert sum(actual_by_id.values()) == 7

    report = evaluate_clarification_fixture(FIXTURE_PATH)
    assert report.case_count == 40
    assert report.expected_ask_count == 7
    assert report.expected_no_ask_count == 33
    assert report.necessary_clarification_recall == 1
    assert report.meaningless_question_rate == 0
    assert report.slot_accuracy == 1


def test_policy_configuration_is_versioned_and_documents_priority_fields() -> None:
    config_path = (
        SERVICE_ROOT / "app" / "routers" / "AImodel" / "clarification_policy.yaml"
    )
    config = config_path.read_text(encoding="utf-8")
    assert all(
        key in config
        for key in (
            "schema_version:",
            "policy_version:",
            "conflict_priority:",
            "slot_priority:",
            "recent_question_window:",
            "fallback_questions:",
        )
    )
