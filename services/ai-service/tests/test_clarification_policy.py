"""Unit tests for the B4 deterministic clarification policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.routers.AImodel.clarification import (
    ClarificationDecision,
    ClarificationHistory,
    ClarificationOptionSeed,
    ClarificationReason,
    evaluate_clarification_fixture,
    load_clarification_policy,
    select_clarification,
)
from app.routers.AImodel.goal_state import (
    CandidateStatus,
    GoalConflict,
    GoalConflictCode,
)
from app.routers.AImodel.shopping_goal import (
    Constraint,
    Exclusion,
    GoalEvidence,
    GoalField,
    GoalSourceType,
    OpenSlot,
    Preference,
    ShoppingGoal,
)


ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def evidence(quote: str, *, turn: int = 1) -> GoalEvidence:
    return GoalEvidence(
        source_type=GoalSourceType.USER_TURN,
        source_turn=turn,
        quote=quote,
        confidence=1,
        created_at=NOW,
        updated_at=NOW,
    )


def system_evidence() -> GoalEvidence:
    return GoalEvidence(
        source_type=GoalSourceType.SYSTEM_DEFAULT,
        confidence=0,
        created_at=NOW,
        updated_at=NOW,
    )


def hard(
    field: GoalField,
    value: object,
    *,
    attribute: str | None = None,
    turn: int = 1,
) -> Constraint:
    return Constraint(
        field=field,
        value=value,
        attribute=attribute,
        evidence=evidence("用户原话", turn=turn),
    )


def open_slot(
    field: GoalField,
    question: str,
    *,
    attribute: str | None = None,
) -> OpenSlot:
    return OpenSlot(
        field=field,
        attribute=attribute,
        question=question,
        evidence=system_evidence(),
    )


def conflict(
    code: GoalConflictCode,
    *items: Constraint | Exclusion,
    field: GoalField,
    attribute: str | None = None,
) -> GoalConflict:
    return GoalConflict(
        code=code,
        field=field,
        attribute=attribute,
        values=items,
        sources=tuple(item.evidence for item in items),
        clarification_topic="需要确认",
    )


def category_goal(*slots: OpenSlot) -> ShoppingGoal:
    return ShoppingGoal(
        hard_constraints=(hard(GoalField.CATEGORY, "electronics"),),
        open_slots=slots,
    )


def test_policy_configuration_is_complete_and_bounded() -> None:
    config = load_clarification_policy()
    assert config.policy_version == "b4-v1"
    assert config.min_options == 2
    assert config.max_options == 5
    assert set(config.conflict_priority) == set(GoalConflictCode)


def test_multiple_conflicts_use_stable_configured_priority() -> None:
    minimum = hard(GoalField.BUDGET_MIN, 5000)
    maximum = hard(GoalField.BUDGET_MAX, 3000)
    included = hard(GoalField.BRAND, "Xiaomi")
    excluded = Exclusion(
        field=GoalField.BRAND,
        value="Xiaomi",
        evidence=evidence("不要小米"),
    )
    brand = conflict(
        GoalConflictCode.BRAND_INCLUDED_AND_EXCLUDED,
        included,
        excluded,
        field=GoalField.BRAND,
    )
    budget = conflict(
        GoalConflictCode.BUDGET_RANGE_REVERSED,
        minimum,
        maximum,
        field=GoalField.BUDGET_MAX,
    )

    left = select_clarification(ShoppingGoal(), conflicts=(brand, budget))
    right = select_clarification(ShoppingGoal(), conflicts=(budget, brand))

    assert left == right
    assert left.slot_key == "budget_max"
    assert left.reason is ClarificationReason.BLOCKING_CONFLICT


def test_conflict_options_are_evidence_backed_and_include_skip() -> None:
    included = hard(GoalField.BRAND, "Xiaomi")
    excluded = Exclusion(
        field=GoalField.BRAND,
        value="Xiaomi",
        evidence=evidence("不要小米"),
    )
    decision = select_clarification(
        category_goal(),
        conflicts=(
            conflict(
                GoalConflictCode.BRAND_INCLUDED_AND_EXCLUDED,
                included,
                excluded,
                field=GoalField.BRAND,
            ),
        ),
    )

    assert decision.payload is not None
    values = [option.value for option in decision.payload.options]
    assert values == [
        "resolve:brand:hard:Xiaomi",
        "resolve:brand:exclude:Xiaomi",
        "skip:brand",
    ]


def test_recent_window_only_suppresses_last_configured_questions() -> None:
    goal = category_goal(open_slot(GoalField.BUDGET_MAX, "预算上限？"))
    history = ClarificationHistory(
        recent_slot_keys=(
            "budget_max",
            "usage_scenario",
            "quantity",
            "delivery_deadline",
        )
    )

    decision = select_clarification(goal, history=history)

    assert decision.should_ask is True
    assert decision.slot_key == "budget_max"


def test_suppression_tries_next_best_topic_before_proceeding() -> None:
    goal = category_goal(
        open_slot(GoalField.BUDGET_MAX, "预算上限？"),
        open_slot(GoalField.USAGE_SCENARIO, "使用场景？"),
    )
    history = ClarificationHistory(skipped_slot_keys=("budget_max",))

    decision = select_clarification(goal, history=history)

    assert decision.slot_key == "usage_scenario"


def test_answered_slot_is_suppressed_but_a_new_conflict_can_reask() -> None:
    goal = category_goal(open_slot(GoalField.BUDGET_MAX, "预算上限？"))
    history = ClarificationHistory(answered_slot_keys=("budget_max",))

    suppressed = select_clarification(
        goal,
        history=history,
        candidate_status=CandidateStatus.AVAILABLE,
    )
    minimum = hard(GoalField.BUDGET_MIN, 5000)
    maximum = hard(GoalField.BUDGET_MAX, 3000)
    conflicted = select_clarification(
        goal,
        history=history,
        conflicts=(
            conflict(
                GoalConflictCode.BUDGET_RANGE_REVERSED,
                minimum,
                maximum,
                field=GoalField.BUDGET_MAX,
            ),
        ),
    )

    assert suppressed.should_ask is False
    assert suppressed.critical_unknowns == ("budget_max",)
    assert conflicted.should_ask is True
    assert conflicted.slot_key == "budget_max"


def test_skipped_conflict_fingerprint_suppresses_only_the_same_conflict() -> None:
    original = conflict(
        GoalConflictCode.BUDGET_RANGE_REVERSED,
        hard(GoalField.BUDGET_MIN, 5000),
        hard(GoalField.BUDGET_MAX, 3000),
        field=GoalField.BUDGET_MAX,
    )
    first = select_clarification(category_goal(), conflicts=(original,))
    assert first.conflict_fingerprint is not None
    replay_reordered = select_clarification(
        category_goal(),
        conflicts=(
            GoalConflict(
                code=original.code,
                field=original.field,
                values=tuple(reversed(original.values)),
                sources=tuple(reversed(original.sources)),
                clarification_topic=original.clarification_topic,
            ),
        ),
    )
    assert replay_reordered.conflict_fingerprint == first.conflict_fingerprint
    history = ClarificationHistory(
        skipped_slot_keys=("budget_max",),
        skipped_conflict_fingerprints=(first.conflict_fingerprint,),
    )

    suppressed = select_clarification(
        category_goal(),
        conflicts=(original,),
        history=history,
        candidate_status=CandidateStatus.AVAILABLE,
    )
    changed = conflict(
        GoalConflictCode.BUDGET_RANGE_REVERSED,
        hard(GoalField.BUDGET_MIN, 5000, turn=2),
        hard(GoalField.BUDGET_MAX, 2500, turn=2),
        field=GoalField.BUDGET_MAX,
    )
    reasked = select_clarification(
        category_goal(),
        conflicts=(changed,),
        history=history,
    )

    assert suppressed.should_ask is False
    assert suppressed.may_proceed is False
    assert suppressed.recommend_with_uncertainty is False
    assert suppressed.reason is ClarificationReason.UNRESOLVED_BLOCKING_CONFLICT
    assert suppressed.critical_unknowns == ("budget_max",)
    assert reasked.should_ask is True
    assert reasked.conflict_fingerprint != first.conflict_fingerprint


def test_specification_slot_key_matches_b1_casefold_semantics() -> None:
    first = select_clarification(
        category_goal(open_slot(GoalField.SPECIFICATION, "内存？", attribute="RAM"))
    )
    assert first.slot_key == "specification:ram"

    suppressed = select_clarification(
        category_goal(open_slot(GoalField.SPECIFICATION, "内存？", attribute="ram")),
        history=ClarificationHistory(skipped_slot_keys=("specification:ram",)),
        candidate_status=CandidateStatus.AVAILABLE,
    )
    assert suppressed.should_ask is False
    assert suppressed.critical_unknowns == ("specification:ram",)


def test_long_valid_upstream_values_still_project_to_a4() -> None:
    attribute = "A" * 120
    slot_decision = select_clarification(
        category_goal(open_slot(GoalField.SPECIFICATION, "规格？", attribute=attribute))
    )
    assert slot_decision.slot_key is not None
    assert len(slot_decision.slot_key) <= 128

    brand = "B" * 512
    included = hard(GoalField.BRAND, brand)
    excluded = Exclusion(
        field=GoalField.BRAND,
        value=brand,
        evidence=evidence("不要这个品牌"),
    )
    conflict_decision = select_clarification(
        category_goal(),
        conflicts=(
            conflict(
                GoalConflictCode.BRAND_INCLUDED_AND_EXCLUDED,
                included,
                excluded,
                field=GoalField.BRAND,
            ),
        ),
    )
    assert conflict_decision.payload is not None
    assert all(
        len(option.label) <= 512 and len(option.value) <= 512
        for option in conflict_decision.payload.options
    )
    assert all(
        "sha256:" in option.value
        for option in conflict_decision.payload.options
        if option.value.startswith("resolve:")
    )


def test_suppressed_unknowns_are_stably_capped_to_output_schema() -> None:
    attributes = tuple(f"spec-{index:02d}" for index in range(17))
    goal = category_goal(
        *(
            open_slot(GoalField.SPECIFICATION, "规格？", attribute=attribute)
            for attribute in attributes
        )
    )
    skipped = tuple(f"specification:{attribute}" for attribute in attributes)

    decision = select_clarification(
        goal,
        history=ClarificationHistory(skipped_slot_keys=skipped),
        candidate_status=CandidateStatus.AVAILABLE,
    )

    assert decision.should_ask is False
    assert decision.critical_unknowns == skipped[:16]


def test_suppressed_unknowns_are_stable_and_allow_uncertain_ranking() -> None:
    goal = category_goal(
        open_slot(GoalField.BUDGET_MAX, "预算上限？"),
        open_slot(GoalField.USAGE_SCENARIO, "使用场景？"),
    )
    history = ClarificationHistory(skipped_slot_keys=("budget_max", "usage_scenario"))

    decision = select_clarification(
        goal,
        history=history,
        candidate_status=CandidateStatus.AVAILABLE,
    )

    assert decision.should_ask is False
    assert decision.may_proceed is True
    assert decision.recommend_with_uncertainty is True
    assert decision.critical_unknowns == ("budget_max", "usage_scenario")


def test_empty_candidates_without_an_explicit_questionable_gap_use_fallback() -> None:
    decision = select_clarification(
        category_goal(),
        candidate_status=CandidateStatus.EMPTY,
    )
    assert decision.should_ask is False
    assert decision.may_proceed is False
    assert decision.reason is ClarificationReason.NO_CANDIDATES


def test_category_exclusion_is_not_a_positive_category_answer() -> None:
    excluded = Exclusion(
        field=GoalField.CATEGORY,
        value="electronics",
        evidence=evidence("不要电子产品"),
    )
    decision = select_clarification(ShoppingGoal(exclusions=(excluded,)))
    assert decision.slot_key == "category"


def test_soft_positive_category_is_enough_to_start_search() -> None:
    preferred = Preference(
        field=GoalField.CATEGORY,
        value="electronics",
        evidence=evidence("倾向电子产品"),
    )
    decision = select_clarification(
        ShoppingGoal(preferences=(preferred,)),
        candidate_status=CandidateStatus.NOT_SEARCHED,
    )
    assert decision.should_ask is False
    assert decision.may_proceed is True


def test_recipient_alias_comes_from_versioned_keywords() -> None:
    goal = category_goal(
        open_slot(
            GoalField.FREEFORM_PREFERENCE,
            "这是送人的礼物，收礼人喜欢什么？",
        )
    )
    decision = select_clarification(goal)
    assert decision.slot_key == "recipient_preference"


def test_known_spec_values_are_deduplicated_and_capped_with_skip() -> None:
    goal = category_goal(open_slot(GoalField.SPECIFICATION, "容量？", attribute="容量"))
    options = tuple(
        ClarificationOptionSeed(label=f"{value}L", value=f"{value}L")
        for value in (3, 4, 5, 6, 7, 7)
    )

    decision = select_clarification(
        goal,
        known_attribute_options={"容量": options},
    )

    assert decision.payload is not None
    assert len(decision.payload.options) == 5
    assert decision.payload.options[-1].value == "skip:specification:容量"
    assert len({option.value for option in decision.payload.options}) == 5


@pytest.mark.parametrize(
    "rendered",
    ["", "budget_max", "slot_key=budget_max", "x" * 513],
)
def test_invalid_rendered_question_uses_fixed_fallback(rendered: str) -> None:
    goal = category_goal(open_slot(GoalField.BUDGET_MAX, "预算？"))
    decision = select_clarification(
        goal,
        question_renderer=lambda _slot: rendered,
    )
    assert decision.payload is not None
    assert decision.payload.answer == "你的预算上限是多少？"


def test_valid_rendered_question_may_customize_text_but_not_options() -> None:
    goal = category_goal(open_slot(GoalField.BUDGET_MAX, "预算？"))
    default = select_clarification(goal)
    rendered = select_clarification(
        goal,
        question_renderer=lambda _slot: "这次准备花多少钱？",
    )

    assert rendered.payload is not None
    assert default.payload is not None
    assert rendered.payload.answer == "这次准备花多少钱？"
    assert rendered.payload.options == default.payload.options


def test_decision_model_rejects_multiple_shape_contradictions() -> None:
    with pytest.raises(ValidationError, match="cannot carry a question"):
        ClarificationDecision(
            policy_version="b4-v1",
            should_ask=False,
            may_proceed=True,
            reason=ClarificationReason.NO_CLARIFICATION_NEEDED,
            slot_key="budget_max",
        )


def test_a2_linked_evaluation_report_is_exact() -> None:
    report = evaluate_clarification_fixture(
        ROOT / "fixtures" / "evals" / "clarification_policy_cases.json"
    )
    assert report.case_count == 40
    assert report.true_positive == 7
    assert report.false_positive == 0
    assert report.false_negative == 0
    assert report.slot_accuracy == 1


def test_delivery_deadline_conflict_offers_input_and_skip() -> None:
    deadline = hard(
        GoalField.DELIVERY_DEADLINE,
        NOW - timedelta(hours=1),
    )
    decision = select_clarification(
        category_goal(),
        conflicts=(
            conflict(
                GoalConflictCode.DELIVERY_DEADLINE_IN_PAST,
                deadline,
                field=GoalField.DELIVERY_DEADLINE,
            ),
        ),
    )
    assert decision.payload is not None
    assert [option.value for option in decision.payload.options] == [
        "input:delivery_deadline",
        "skip:delivery_deadline",
    ]
