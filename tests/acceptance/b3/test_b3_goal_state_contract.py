"""Frozen acceptance contract for B3 deterministic shopping-goal merging."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "ai-service"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.routers.AImodel.goal_extractor import (  # noqa: E402
    DeltaAction,
    GoalDelta,
    GoalExtractionTrace,
    GoalRemoveMutation,
    GoalValueMutation,
)
from app.routers.AImodel.goal_state import (  # noqa: E402
    CandidateStatus,
    GoalConflictCode,
    GoalMergeRejected,
    MergeEventOutcome,
    merge_goal_delta,
)
from app.routers.AImodel.shopping_goal import (  # noqa: E402
    Constraint,
    DecisionStage,
    Exclusion,
    GoalEvidence,
    GoalField,
    GoalSourceType,
    InvalidGoalTransition,
    Preference,
    ShoppingGoal,
)


NOW = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
TRACE = GoalExtractionTrace(duration_ms=0)


def evidence(
    *,
    turn: int,
    quote: str,
    source_type: GoalSourceType = GoalSourceType.USER_TURN,
    confidence: float = 1,
) -> GoalEvidence:
    return GoalEvidence(
        source_type=source_type,
        source_turn=turn,
        quote=quote,
        confidence=confidence,
        created_at=NOW + timedelta(minutes=turn),
        updated_at=NOW + timedelta(minutes=turn),
    )


def constraint(
    field: GoalField,
    value: object,
    *,
    turn: int = 1,
    quote: str = "用户原话",
    source_type: GoalSourceType = GoalSourceType.USER_TURN,
    attribute: str | None = None,
) -> Constraint:
    return Constraint(
        field=field,
        value=value,
        attribute=attribute,
        evidence=evidence(
            turn=turn,
            quote=quote,
            source_type=source_type,
        ),
    )


def mutation(
    item: Constraint | Preference | Exclusion,
    *,
    action: DeltaAction = DeltaAction.ADD,
) -> GoalValueMutation:
    return GoalValueMutation(
        action=action,
        item=item,
        source_span=item.evidence.quote or "model inference",
    )


def delta(turn: int, *operations: object) -> GoalDelta:
    return GoalDelta(
        source_turn=turn,
        operations=operations,
        trace=TRACE,
    )


def value_for(goal: ShoppingGoal, field: GoalField) -> object:
    return next(item.value for item in goal.hard_constraints if item.field is field)


def test_merge_is_pure_deterministic_and_idempotent() -> None:
    incoming = delta(
        1,
        mutation(constraint(GoalField.CATEGORY, "手机", quote="手机")),
    )

    first = merge_goal_delta(ShoppingGoal(), incoming, reference_time=NOW)
    replay = merge_goal_delta(ShoppingGoal(), incoming, reference_time=NOW)
    repeated = merge_goal_delta(first.goal, incoming, reference_time=NOW)

    assert first == replay
    assert first.goal.decision_stage is DecisionStage.SEARCHING
    assert first.goal.revision == 1
    assert repeated.goal == first.goal
    assert len(repeated.goal.hard_constraints) == 1


def test_explicit_replacement_keeps_before_and_after_lineage_in_audit_event() -> None:
    old = constraint(GoalField.BUDGET_MAX, Decimal("5000"), quote="五千以内")
    current = ShoppingGoal(hard_constraints=(old,))
    replacement = constraint(
        GoalField.BUDGET_MAX,
        Decimal("3000"),
        turn=2,
        quote="改成三千以内",
    )

    result = merge_goal_delta(
        current,
        delta(2, mutation(replacement, action=DeltaAction.REPLACE)),
        reference_time=NOW,
    )

    assert value_for(result.goal, GoalField.BUDGET_MAX) == Decimal("3000")
    assert result.events[0].before == (old,)
    assert result.events[0].after == (replacement,)
    assert result.events[0].source_turn == 2
    assert result.events[0].outcome is MergeEventOutcome.APPLIED


def test_current_user_turn_beats_page_context_and_history() -> None:
    historical = constraint(GoalField.CATEGORY, "家电", quote="之前看家电")
    page = constraint(
        GoalField.CATEGORY,
        "耳机",
        turn=2,
        quote="耳机列表",
        source_type=GoalSourceType.PAGE_CONTEXT,
    )
    user = constraint(
        GoalField.CATEGORY,
        "手机",
        turn=2,
        quote="我要手机",
    )

    result = merge_goal_delta(
        ShoppingGoal(hard_constraints=(historical,)),
        delta(
            2,
            mutation(page, action=DeltaAction.ADD),
            mutation(user, action=DeltaAction.REPLACE),
        ),
        reference_time=NOW,
    )

    assert value_for(result.goal, GoalField.CATEGORY) == "手机"
    assert (
        result.goal.hard_constraints[0].evidence.source_type is GoalSourceType.USER_TURN
    )


def test_soft_preference_never_overwrites_hard_constraint() -> None:
    hard_brand = constraint(GoalField.BRAND, "Huawei", quote="只要华为")
    soft_brand = Preference(
        field=GoalField.BRAND,
        value="Xiaomi",
        evidence=evidence(turn=2, quote="小米也行", confidence=0.7),
    )

    result = merge_goal_delta(
        ShoppingGoal(hard_constraints=(hard_brand,)),
        delta(2, mutation(soft_brand)),
        reference_time=NOW,
    )

    assert result.goal.hard_constraints == (hard_brand,)
    assert result.goal.preferences == ()
    assert result.events[0].outcome is MergeEventOutcome.IGNORED_LOWER_PRIORITY


@pytest.mark.parametrize(
    ("operations", "code", "field"),
    [
        (
            (
                mutation(constraint(GoalField.BUDGET_MIN, 5000, quote="至少五千")),
                mutation(constraint(GoalField.BUDGET_MAX, 3000, quote="最多三千")),
            ),
            GoalConflictCode.BUDGET_RANGE_REVERSED,
            GoalField.BUDGET_MAX,
        ),
        (
            (
                mutation(constraint(GoalField.BRAND, "Apple", quote="只要苹果")),
                mutation(
                    Exclusion(
                        field=GoalField.BRAND,
                        value="Apple",
                        evidence=evidence(turn=1, quote="不要苹果"),
                    )
                ),
            ),
            GoalConflictCode.BRAND_INCLUDED_AND_EXCLUDED,
            GoalField.BRAND,
        ),
        (
            (
                mutation(
                    constraint(
                        GoalField.SPECIFICATION,
                        "16GB",
                        quote="内存要 16GB",
                        attribute="内存",
                    )
                ),
                mutation(
                    Exclusion(
                        field=GoalField.SPECIFICATION,
                        value="16GB",
                        attribute="内存",
                        evidence=evidence(turn=1, quote="不要 16GB"),
                    )
                ),
            ),
            GoalConflictCode.SPECIFICATION_MUTUALLY_EXCLUSIVE,
            GoalField.SPECIFICATION,
        ),
    ],
)
def test_logical_conflicts_are_stable_and_never_silently_choose_a_side(
    operations: tuple[GoalValueMutation, ...],
    code: GoalConflictCode,
    field: GoalField,
) -> None:
    result = merge_goal_delta(
        ShoppingGoal(),
        delta(1, *operations),
        reference_time=NOW,
    )

    assert result.goal.decision_stage is DecisionStage.CLARIFYING
    assert result.goal.hard_constraints == ()
    assert result.goal.exclusions == ()
    assert len(result.conflicts) == 1
    assert result.conflicts[0].code is code
    assert result.conflicts[0].field is field
    assert len(result.conflicts[0].values) == 2
    assert len(result.conflicts[0].sources) == 2
    assert result.conflicts[0].clarification_topic


def test_past_delivery_deadline_is_a_blocking_conflict() -> None:
    deadline = constraint(
        GoalField.DELIVERY_DEADLINE,
        NOW - timedelta(minutes=1),
        quote="十点前送到",
    )
    result = merge_goal_delta(
        ShoppingGoal(),
        delta(1, mutation(deadline)),
        reference_time=NOW,
    )

    assert result.conflicts[0].code is GoalConflictCode.DELIVERY_DEADLINE_IN_PAST
    assert result.goal.hard_constraints == ()
    assert result.goal.decision_stage is DecisionStage.CLARIFYING


def test_remove_only_affects_the_selected_field_and_kinds() -> None:
    category = constraint(GoalField.CATEGORY, "手机", quote="买手机")
    brand = constraint(GoalField.BRAND, "Huawei", quote="只看华为")
    maximum = constraint(GoalField.BUDGET_MAX, 5000, quote="五千以内")
    remove = GoalRemoveMutation(
        field=GoalField.BRAND,
        target_kinds=("hard",),
        evidence=evidence(turn=2, quote="品牌不限"),
    )

    result = merge_goal_delta(
        ShoppingGoal(hard_constraints=(category, brand, maximum)),
        delta(2, remove),
        reference_time=NOW,
    )

    assert {item.field for item in result.goal.hard_constraints} == {
        GoalField.CATEGORY,
        GoalField.BUDGET_MAX,
    }
    assert result.events[0].before == (brand,)
    assert result.events[0].after == ()


@pytest.mark.parametrize(
    ("candidate_status", "expected_stage"),
    [
        (CandidateStatus.NOT_SEARCHED, DecisionStage.SEARCHING),
        (CandidateStatus.EMPTY, DecisionStage.CLARIFYING),
        (CandidateStatus.AVAILABLE, DecisionStage.COMPARING),
    ],
)
def test_required_slots_conflicts_and_candidate_status_drive_stage(
    candidate_status: CandidateStatus,
    expected_stage: DecisionStage,
) -> None:
    category = constraint(GoalField.CATEGORY, "手机", quote="买手机")
    current_stage = (
        DecisionStage.SEARCHING
        if candidate_status is not CandidateStatus.NOT_SEARCHED
        else DecisionStage.DISCOVERING
    )
    current = ShoppingGoal(
        decision_stage=current_stage,
        hard_constraints=(category,)
        if current_stage is DecisionStage.SEARCHING
        else (),
    )
    incoming = (
        delta(1)
        if current_stage is DecisionStage.SEARCHING
        else delta(1, mutation(category))
    )

    result = merge_goal_delta(
        current,
        incoming,
        candidate_status=candidate_status,
        reference_time=NOW,
    )

    assert result.goal.decision_stage is expected_stage


def test_missing_required_category_remains_discovering() -> None:
    result = merge_goal_delta(
        ShoppingGoal(),
        delta(1, mutation(constraint(GoalField.BUDGET_MAX, 5000))),
        reference_time=NOW,
    )
    assert result.goal.decision_stage is DecisionStage.DISCOVERING


def test_selected_candidate_cannot_skip_directly_from_discovering_to_decided() -> None:
    category = constraint(GoalField.CATEGORY, "手机", quote="买手机")
    with pytest.raises(InvalidGoalTransition, match="discovering to decided"):
        merge_goal_delta(
            ShoppingGoal(),
            delta(1, mutation(category)),
            candidate_status=CandidateStatus.SELECTED,
            reference_time=NOW,
        )


def test_forged_hard_constraint_without_evidence_is_explicitly_rejected() -> None:
    forged = Constraint.model_construct(
        field=GoalField.CATEGORY,
        value="手机",
        attribute=None,
        evidence=None,
    )
    operation = GoalValueMutation.model_construct(
        action=DeltaAction.ADD,
        item=forged,
        source_span="手机",
    )
    forged_delta = GoalDelta.model_construct(
        schema_version="v1",
        source_turn=1,
        operations=(operation,),
        trace=TRACE,
    )

    with pytest.raises(GoalMergeRejected, match="hard constraint requires evidence"):
        merge_goal_delta(ShoppingGoal(), forged_delta, reference_time=NOW)


def test_merge_rule_document_covers_priority_conflicts_and_stage_policy() -> None:
    document = (ROOT / "docs" / "shopping_goal_merge_rules.md").read_text(
        encoding="utf-8"
    )
    assert all(
        heading in document
        for heading in (
            "## 来源优先级",
            "## 合并操作",
            "## 冲突代码",
            "## 阶段迁移",
            "## 审计事件",
        )
    )
