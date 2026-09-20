"""Unit coverage for B3 goal-state merge, conflict, and transition rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.routers.AImodel.goal_extractor import (
    DeltaAction,
    GoalDelta,
    GoalExtractionTrace,
    GoalRemoveMutation,
    GoalValueMutation,
)
from app.routers.AImodel.goal_state import (
    CandidateStatus,
    GoalConflictCode,
    GoalMergeRejected,
    MergeEventOutcome,
    merge_goal_delta,
)
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


NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
TRACE = GoalExtractionTrace(duration_ms=0)


def evidence(
    turn: int,
    quote: str | None = "用户原话",
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


def hard(
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
        evidence=evidence(turn, quote, source_type),
    )


def value_operation(
    item: Constraint | Preference | Exclusion | OpenSlot,
    action: DeltaAction = DeltaAction.ADD,
) -> GoalValueMutation:
    return GoalValueMutation(
        action=action,
        item=item,
        source_span=item.evidence.quote or "inference",
    )


def change(turn: int, *operations: object) -> GoalDelta:
    return GoalDelta(source_turn=turn, operations=operations, trace=TRACE)


def test_page_context_replaces_history_but_not_same_turn_user_input() -> None:
    historical = hard(GoalField.CATEGORY, "家电", quote="之前看家电")
    page = hard(
        GoalField.CATEGORY,
        "耳机",
        turn=2,
        quote="耳机列表",
        source_type=GoalSourceType.PAGE_CONTEXT,
    )
    user = hard(GoalField.CATEGORY, "手机", turn=2, quote="我要手机")

    page_result = merge_goal_delta(
        ShoppingGoal(hard_constraints=(historical,)),
        change(2, value_operation(page)),
        reference_time=NOW,
    )
    user_result = merge_goal_delta(
        ShoppingGoal(hard_constraints=(historical,)),
        change(
            2,
            value_operation(user, DeltaAction.REPLACE),
            value_operation(page),
        ),
        reference_time=NOW,
    )

    assert page_result.goal.hard_constraints[0] == page
    assert user_result.goal.hard_constraints[0] == user
    assert user_result.events[-1].outcome is MergeEventOutcome.IGNORED_LOWER_PRIORITY


@pytest.mark.parametrize("remove_first", [True, False])
def test_user_remove_beats_page_context_independent_of_operation_order(
    remove_first: bool,
) -> None:
    historical = hard(GoalField.CATEGORY, "家电", quote="之前看家电")
    page = hard(
        GoalField.CATEGORY,
        "耳机",
        turn=2,
        quote="耳机列表",
        source_type=GoalSourceType.PAGE_CONTEXT,
    )
    remove = GoalRemoveMutation(
        field=GoalField.CATEGORY,
        target_kinds=("hard",),
        evidence=evidence(2, "不看这个品类了"),
    )
    page_add = value_operation(page)
    operations = (remove, page_add) if remove_first else (page_add, remove)

    result = merge_goal_delta(
        ShoppingGoal(hard_constraints=(historical,)),
        change(2, *operations),
        reference_time=NOW,
    )

    assert result.goal.hard_constraints == ()
    assert result.goal.decision_stage is DecisionStage.DISCOVERING


@pytest.mark.parametrize("confirm_first", [True, False])
def test_user_confirm_beats_page_add_independent_of_operation_order(
    confirm_first: bool,
) -> None:
    historical = hard(GoalField.CATEGORY, "手机", quote="之前买手机")
    page = hard(
        GoalField.CATEGORY,
        "耳机",
        turn=2,
        quote="耳机列表",
        source_type=GoalSourceType.PAGE_CONTEXT,
    )
    confirmed = hard(
        GoalField.CATEGORY,
        "手机",
        turn=2,
        quote="确认还是手机",
    )
    page_add = value_operation(page)
    user_confirm = value_operation(confirmed, DeltaAction.CONFIRM)
    operations = (user_confirm, page_add) if confirm_first else (page_add, user_confirm)

    result = merge_goal_delta(
        ShoppingGoal(hard_constraints=(historical,)),
        change(2, *operations),
        reference_time=NOW,
    )

    assert result.goal.hard_constraints == (confirmed,)
    assert result.goal.decision_stage is DecisionStage.SEARCHING
    assert result.conflicts == ()
    outcomes = {event.action: event.outcome for event in result.events}
    assert outcomes == {
        DeltaAction.ADD: MergeEventOutcome.IGNORED_LOWER_PRIORITY,
        DeltaAction.CONFIRM: MergeEventOutcome.APPLIED,
    }


def test_different_source_priority_permutations_return_the_same_result() -> None:
    historical = hard(GoalField.CATEGORY, "手机", quote="之前买手机")
    page = hard(
        GoalField.CATEGORY,
        "耳机",
        turn=2,
        quote="耳机列表",
        source_type=GoalSourceType.PAGE_CONTEXT,
    )
    confirmed = hard(
        GoalField.CATEGORY,
        "手机",
        turn=2,
        quote="确认还是手机",
    )
    page_add = value_operation(page)
    user_confirm = value_operation(confirmed, DeltaAction.CONFIRM)
    current = ShoppingGoal(hard_constraints=(historical,))

    page_first = merge_goal_delta(
        current,
        change(2, page_add, user_confirm),
        reference_time=NOW,
    )
    user_first = merge_goal_delta(
        current,
        change(2, user_confirm, page_add),
        reference_time=NOW,
    )

    assert page_first == user_first


def test_long_term_preference_only_fills_an_unoccupied_slot() -> None:
    remembered = Preference(
        field=GoalField.BRAND,
        value="Huawei",
        evidence=evidence(1, "长期喜欢华为", confidence=0.8),
    )
    current = Preference(
        field=GoalField.BRAND,
        value="Xiaomi",
        evidence=evidence(2, "这次倾向小米", confidence=0.9),
    )

    empty_result = merge_goal_delta(
        ShoppingGoal(),
        change(2),
        long_term_preferences=(remembered,),
        reference_time=NOW,
    )
    occupied_result = merge_goal_delta(
        ShoppingGoal(preferences=(current,)),
        change(2),
        long_term_preferences=(remembered,),
        reference_time=NOW,
    )

    assert empty_result.goal.preferences == (remembered,)
    assert occupied_result.goal.preferences == (current,)


def test_confirm_refreshes_lineage_once_and_then_is_idempotent() -> None:
    old = hard(GoalField.BUDGET_MAX, 5000, quote="五千以内")
    confirmed = hard(
        GoalField.BUDGET_MAX,
        5000,
        turn=2,
        quote="还是五千以内",
    )
    incoming = change(2, value_operation(confirmed, DeltaAction.CONFIRM))

    first = merge_goal_delta(
        ShoppingGoal(hard_constraints=(old,)), incoming, reference_time=NOW
    )
    replay = merge_goal_delta(first.goal, incoming, reference_time=NOW)

    assert first.goal.hard_constraints == (confirmed,)
    assert first.events[0].before == (old,)
    assert replay.goal == first.goal
    assert replay.events[0].outcome is MergeEventOutcome.NO_CHANGE


def test_mismatched_confirmation_is_exposed_without_mutating_state() -> None:
    old = hard(GoalField.BUDGET_MAX, 5000, quote="五千以内")
    mismatch = hard(
        GoalField.BUDGET_MAX,
        3000,
        turn=2,
        quote="还是三千以内",
    )

    result = merge_goal_delta(
        ShoppingGoal(hard_constraints=(old,)),
        change(2, value_operation(mismatch, DeltaAction.CONFIRM)),
        reference_time=NOW,
    )

    assert result.goal.hard_constraints == (old,)
    assert result.goal.decision_stage is DecisionStage.CLARIFYING
    assert result.conflicts[0].code is GoalConflictCode.CONFIRMATION_MISMATCH
    assert {item.value for item in result.conflicts[0].values} == {
        Decimal("5000"),
        Decimal("3000"),
    }


def test_known_value_resolves_matching_open_slot() -> None:
    slot = OpenSlot(
        field=GoalField.CATEGORY,
        question="想买什么品类？",
        evidence=GoalEvidence(
            source_type=GoalSourceType.SYSTEM_DEFAULT,
            source_turn=None,
            quote=None,
            confidence=0,
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    category = hard(GoalField.CATEGORY, "手机", turn=2, quote="买手机")

    result = merge_goal_delta(
        ShoppingGoal(open_slots=(slot,)),
        change(2, value_operation(category)),
        reference_time=NOW,
    )

    assert result.goal.open_slots == ()
    assert result.goal.hard_constraints == (category,)
    assert result.goal.decision_stage is DecisionStage.SEARCHING


def test_open_required_slot_enters_clarifying() -> None:
    slot = OpenSlot(
        field=GoalField.CATEGORY,
        question="想买什么品类？",
        evidence=evidence(
            1,
            quote=None,
            source_type=GoalSourceType.MODEL_INFERENCE,
            confidence=0.5,
        ),
    )
    operation = value_operation(slot)
    result = merge_goal_delta(ShoppingGoal(), change(1, operation), reference_time=NOW)

    assert result.goal.decision_stage is DecisionStage.CLARIFYING
    assert result.goal.stage_reason == "required_slot_open:category"


def test_open_slot_confirmation_is_deterministic_instead_of_crashing() -> None:
    current_slot = OpenSlot(
        field=GoalField.CATEGORY,
        question="想买什么品类？",
        evidence=GoalEvidence(
            source_type=GoalSourceType.SYSTEM_DEFAULT,
            confidence=0,
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    confirmed_slot = OpenSlot(
        field=GoalField.CATEGORY,
        question="想买什么品类？",
        evidence=evidence(
            2,
            quote=None,
            source_type=GoalSourceType.MODEL_INFERENCE,
            confidence=0.6,
        ),
    )

    result = merge_goal_delta(
        ShoppingGoal(open_slots=(current_slot,)),
        change(2, value_operation(confirmed_slot, DeltaAction.CONFIRM)),
        reference_time=NOW,
    )

    assert result.goal.open_slots == (current_slot,)
    assert result.events[0].outcome is MergeEventOutcome.IGNORED_LOWER_PRIORITY


def test_remove_targets_attribute_and_kind_without_touching_other_specs() -> None:
    memory = hard(
        GoalField.SPECIFICATION,
        "16GB",
        attribute="内存",
        quote="内存 16GB",
    )
    storage = hard(
        GoalField.SPECIFICATION,
        "512GB",
        attribute="存储",
        quote="存储 512GB",
    )
    soft_memory = Preference(
        field=GoalField.SPECIFICATION,
        value="越大越好",
        attribute="内存",
        evidence=evidence(1, "内存越大越好", confidence=0.7),
    )
    remove = GoalRemoveMutation(
        field=GoalField.SPECIFICATION,
        attribute="内存",
        target_kinds=("soft",),
        evidence=evidence(2, "撤销内存偏好"),
    )

    result = merge_goal_delta(
        ShoppingGoal(
            hard_constraints=(memory, storage),
            preferences=(soft_memory,),
        ),
        change(2, remove),
        reference_time=NOW,
    )

    assert result.goal.hard_constraints == (memory, storage)
    assert result.goal.preferences == ()


def test_conflicting_incoming_value_rolls_back_only_affected_fields() -> None:
    category = hard(GoalField.CATEGORY, "手机", quote="买手机")
    old_maximum = hard(GoalField.BUDGET_MAX, 6000, quote="六千以内")
    new_minimum = hard(GoalField.BUDGET_MIN, 7000, turn=2, quote="至少七千")
    quantity = hard(GoalField.QUANTITY, 2, turn=2, quote="买两件")

    result = merge_goal_delta(
        ShoppingGoal(hard_constraints=(category, old_maximum)),
        change(2, value_operation(new_minimum), value_operation(quantity)),
        reference_time=NOW,
    )

    assert result.conflicts[0].code is GoalConflictCode.BUDGET_RANGE_REVERSED
    assert {item.field for item in result.goal.hard_constraints} == {
        GoalField.CATEGORY,
        GoalField.BUDGET_MAX,
        GoalField.QUANTITY,
    }
    assert all(
        item.field is not GoalField.BUDGET_MIN for item in result.goal.hard_constraints
    )


def test_conflict_rollback_preserves_original_field_order() -> None:
    old_maximum = hard(GoalField.BUDGET_MAX, 6000, quote="六千以内")
    category = hard(GoalField.CATEGORY, "手机", quote="买手机")
    new_minimum = hard(GoalField.BUDGET_MIN, 7000, turn=2, quote="至少七千")
    current = ShoppingGoal(hard_constraints=(old_maximum, category))

    result = merge_goal_delta(
        current,
        change(2, value_operation(new_minimum)),
        reference_time=NOW,
    )

    assert result.goal.hard_constraints == current.hard_constraints


def test_complete_discovering_snapshot_advances_without_a_new_delta() -> None:
    category = hard(GoalField.CATEGORY, "手机", quote="买手机")
    result = merge_goal_delta(
        ShoppingGoal(hard_constraints=(category,)),
        change(2),
        reference_time=NOW,
    )

    assert result.goal.decision_stage is DecisionStage.SEARCHING


def test_delivery_deadline_equal_to_reference_time_is_not_past() -> None:
    category = hard(GoalField.CATEGORY, "鲜花", quote="买鲜花")
    deadline = hard(
        GoalField.DELIVERY_DEADLINE,
        NOW,
        quote="九点送到",
    )
    result = merge_goal_delta(
        ShoppingGoal(),
        change(1, value_operation(category), value_operation(deadline)),
        reference_time=NOW,
    )
    assert result.conflicts == ()


def test_confirmation_compares_aware_datetimes_as_absolute_instants() -> None:
    local_deadline = datetime.fromisoformat("2026-09-22T10:00:00+08:00")
    utc_deadline = datetime.fromisoformat("2026-09-22T02:00:00+00:00")
    old = hard(
        GoalField.DELIVERY_DEADLINE,
        local_deadline,
        quote="十点前送到",
    )
    confirmed = hard(
        GoalField.DELIVERY_DEADLINE,
        utc_deadline,
        turn=2,
        quote="确认这个时间",
    )

    result = merge_goal_delta(
        ShoppingGoal(hard_constraints=(old,)),
        change(2, value_operation(confirmed, DeltaAction.CONFIRM)),
        reference_time=NOW,
    )

    assert result.conflicts == ()
    assert result.goal.hard_constraints == (confirmed,)


def test_category_exclusion_does_not_fill_required_positive_category() -> None:
    excluded = Exclusion(
        field=GoalField.CATEGORY,
        value="家电",
        evidence=evidence(1, "不要家电"),
    )
    result = merge_goal_delta(
        ShoppingGoal(),
        change(1, value_operation(excluded)),
        reference_time=NOW,
    )

    assert result.goal.decision_stage is DecisionStage.DISCOVERING


def test_reference_time_must_be_timezone_aware() -> None:
    with pytest.raises(GoalMergeRejected, match="timezone"):
        merge_goal_delta(
            ShoppingGoal(),
            change(1),
            reference_time=datetime(2026, 9, 21, 9, 0),
        )


def test_material_change_invalidates_candidates_but_confirmation_does_not() -> None:
    category = hard(GoalField.CATEGORY, "手机", quote="买手机")
    current = ShoppingGoal(
        decision_stage=DecisionStage.COMPARING,
        hard_constraints=(category,),
    )
    brand = hard(GoalField.BRAND, "Huawei", turn=2, quote="只看华为")
    changed = merge_goal_delta(
        current,
        change(2, value_operation(brand)),
        reference_time=NOW,
    )
    unchanged = merge_goal_delta(current, change(2), reference_time=NOW)

    assert changed.goal.decision_stage is DecisionStage.SEARCHING
    assert unchanged.goal.decision_stage is DecisionStage.COMPARING


def test_candidate_stage_advances_one_legal_step_at_a_time() -> None:
    category = hard(GoalField.CATEGORY, "手机", quote="买手机")
    searching = ShoppingGoal(
        decision_stage=DecisionStage.SEARCHING,
        hard_constraints=(category,),
    )
    comparing = merge_goal_delta(
        searching,
        change(2),
        candidate_status=CandidateStatus.AVAILABLE,
        reference_time=NOW,
    ).goal
    decided = merge_goal_delta(
        comparing,
        change(3),
        candidate_status=CandidateStatus.SELECTED,
        reference_time=NOW,
    ).goal

    assert comparing.decision_stage is DecisionStage.COMPARING
    assert decided.decision_stage is DecisionStage.DECIDED
    with pytest.raises(InvalidGoalTransition):
        merge_goal_delta(
            searching,
            change(2),
            candidate_status=CandidateStatus.SELECTED,
            reference_time=NOW,
        )


def test_goal_and_delta_inputs_are_not_mutated() -> None:
    current = ShoppingGoal()
    category = hard(GoalField.CATEGORY, "手机", quote="买手机")
    incoming = change(1, value_operation(category))
    current_before = current.model_dump_json()
    delta_before = incoming.model_dump_json()

    merge_goal_delta(current, incoming, reference_time=NOW)

    assert current.model_dump_json() == current_before
    assert incoming.model_dump_json() == delta_before
