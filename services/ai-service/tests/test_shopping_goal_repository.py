"""Unit tests for B5 shopping-goal persistence and memory isolation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.routers.AImodel.goal_extractor import DeltaAction
from app.routers.AImodel.goal_repository import (
    InMemoryShoppingGoalRepository,
    PostgresShoppingGoalRepository,
    ShoppingGoalAccessDenied,
    ShoppingGoalNotFound,
    ShoppingGoalRepository,
    ShoppingGoalRepositoryError,
    ShoppingGoalRevisionConflict,
    project_reusable_user_memories,
    sync_reusable_goal_memories,
)
from app.routers.AImodel.goal_state import GoalChangeEvent, MergeEventOutcome
from app.routers.AImodel.memory import NoopAiModelMemoryStore
from app.routers.AImodel.shopping_goal import (
    Constraint,
    GoalEvidence,
    GoalField,
    GoalSourceType,
    Preference,
    ShoppingGoal,
)


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def evidence(
    quote: str | None,
    *,
    source_type: GoalSourceType = GoalSourceType.USER_TURN,
    updated_at: datetime = NOW,
) -> GoalEvidence:
    return GoalEvidence(
        source_type=source_type,
        source_turn=1,
        quote=quote,
        confidence=0.9,
        created_at=updated_at,
        updated_at=updated_at,
    )


def shopping_goal(revision: int = 0, *, brand: str = "小米") -> ShoppingGoal:
    return ShoppingGoal(
        revision=revision,
        hard_constraints=(
            Constraint(
                field=GoalField.CATEGORY,
                value="手机",
                evidence=evidence("买手机"),
            ),
        ),
        preferences=(
            Preference(
                field=GoalField.BRAND,
                value=brand,
                evidence=evidence(f"长期喜欢{brand}"),
            ),
        ),
    )


def event() -> GoalChangeEvent:
    return GoalChangeEvent(
        action=DeltaAction.REPLACE,
        field=GoalField.BRAND,
        source_turn=2,
        outcome=MergeEventOutcome.APPLIED,
    )


def test_in_memory_repository_satisfies_protocol_and_returns_none_when_absent() -> None:
    repository = InMemoryShoppingGoalRepository(clock=lambda: NOW)
    assert isinstance(repository, ShoppingGoalRepository)
    assert repository.load(1, user_id=1) is None


def test_create_can_validate_external_conversation_ownership() -> None:
    owners = {1: 10}
    repository = InMemoryShoppingGoalRepository(
        clock=lambda: NOW,
        conversation_owner=owners.get,
    )

    with pytest.raises(ShoppingGoalAccessDenied):
        repository.create(conversation_id=1, user_id=11, goal=shopping_goal())
    with pytest.raises(ShoppingGoalNotFound):
        repository.create(conversation_id=2, user_id=10, goal=shopping_goal())


def test_in_memory_create_fails_closed_without_conversation_owner() -> None:
    repository = InMemoryShoppingGoalRepository(clock=lambda: NOW)

    with pytest.raises(ShoppingGoalRepositoryError, match="owner resolver"):
        repository.create(conversation_id=1, user_id=10, goal=shopping_goal())


def test_compare_and_save_requires_exact_next_revision() -> None:
    repository = InMemoryShoppingGoalRepository(
        clock=lambda: NOW,
        conversation_owner=lambda conversation_id: 10 if conversation_id == 1 else None,
    )
    repository.create(conversation_id=1, user_id=10, goal=shopping_goal())

    with pytest.raises(ValueError, match=r"expected_revision \+ 1"):
        repository.compare_and_save(
            1,
            user_id=10,
            expected_revision=0,
            goal=shopping_goal(revision=2),
        )
    repository.compare_and_save(
        1,
        user_id=10,
        expected_revision=0,
        goal=shopping_goal(revision=1, brand="华为"),
    )
    with pytest.raises(ShoppingGoalRevisionConflict):
        repository.append_event(
            1,
            user_id=10,
            revision=0,
            event=event(),
        )


def test_commit_transition_persists_snapshot_and_events_under_one_revision() -> None:
    repository = InMemoryShoppingGoalRepository(
        clock=lambda: NOW,
        conversation_owner=lambda conversation_id: 10 if conversation_id == 1 else None,
    )

    initial = repository.commit_transition(
        1,
        user_id=10,
        expected_revision=None,
        goal=shopping_goal(revision=1),
        events=(event(),),
    )
    updated = repository.commit_transition(
        1,
        user_id=10,
        expected_revision=1,
        goal=shopping_goal(revision=2, brand="华为"),
        events=(event(),),
    )

    assert initial.events[0].revision == initial.record.revision == 1
    assert updated.events[0].revision == updated.record.revision == 2
    assert updated.record.goal_id == initial.record.goal_id
    assert repository.load(1, user_id=10) == updated.record


def test_commit_transition_conflict_leaves_snapshot_and_events_unchanged() -> None:
    repository = InMemoryShoppingGoalRepository(
        clock=lambda: NOW,
        conversation_owner=lambda conversation_id: 10 if conversation_id == 1 else None,
    )
    committed = repository.commit_transition(
        1,
        user_id=10,
        expected_revision=None,
        goal=shopping_goal(revision=1),
        events=(event(),),
    )

    with pytest.raises(ShoppingGoalRevisionConflict):
        repository.commit_transition(
            1,
            user_id=10,
            expected_revision=0,
            goal=shopping_goal(revision=1, brand="华为"),
            events=(event(),),
        )

    assert repository.load(1, user_id=10) == committed.record
    assert len(repository._events) == 1


def test_projection_rejects_inferred_expired_and_non_brand_preferences() -> None:
    inferred = Preference(
        field=GoalField.BRAND,
        value="索尼",
        evidence=evidence(None, source_type=GoalSourceType.MODEL_INFERENCE),
    )
    old_brand = Preference(
        field=GoalField.BRAND,
        value="苹果",
        evidence=evidence("以前喜欢苹果", updated_at=NOW - timedelta(days=181)),
    )
    scenario = Preference(
        field=GoalField.USAGE_SCENARIO,
        value="本次出差",
        evidence=evidence("这次出差用"),
    )
    temporary_brand = Preference(
        field=GoalField.BRAND,
        value="华为",
        evidence=evidence("这次想买华为"),
    )

    assert (
        project_reusable_user_memories(
            ShoppingGoal(preferences=(inferred, scenario)),
            goal_id="goal-1",
            now=NOW,
        )
        == ()
    )
    assert (
        project_reusable_user_memories(
            ShoppingGoal(preferences=(temporary_brand,)),
            goal_id="goal-temporary",
            now=NOW,
        )
        == ()
    )
    assert (
        project_reusable_user_memories(
            ShoppingGoal(preferences=(old_brand,)),
            goal_id="goal-2",
            now=NOW,
        )
        == ()
    )


@pytest.mark.parametrize(
    "statement,brand",
    [
        ("我不喜欢小米", "小米"),
        ("我一直不喜欢华为", "华为"),
        ("以前喜欢苹果，现在不喜欢了", "苹果"),
    ],
)
def test_projection_rejects_negated_or_withdrawn_brand_preferences(
    statement: str, brand: str
) -> None:
    preference = Preference(
        field=GoalField.BRAND,
        value=brand,
        evidence=evidence(statement),
    )

    assert (
        project_reusable_user_memories(
            ShoppingGoal(preferences=(preference,)),
            goal_id="goal-negative",
            now=NOW,
        )
        == ()
    )


def test_sync_reusable_memory_preserves_lineage_and_expires() -> None:
    memory_store = NoopAiModelMemoryStore(clock=lambda: NOW)
    projected = sync_reusable_goal_memories(
        memory_store,
        user_id=10,
        goal_id="goal-1",
        goal=shopping_goal(),
        now=NOW,
    )

    assert memory_store.load_user_memories(10) == list(projected)
    assert projected[0].source_goal_id == "goal-1"
    assert projected[0].expires_at == NOW + timedelta(days=180)


def test_projection_matches_normalized_brand_to_current_turn_evidence() -> None:
    normalized_goal = shopping_goal(brand="Huawei")

    projected = project_reusable_user_memories(
        normalized_goal,
        goal_id="goal-normalized",
        now=NOW,
        current_turn_text="预算改成6000元，长期喜欢华为",
    )

    assert [memory.memory_value for memory in projected] == ["Huawei"]
    assert projected[0].evidence == "预算改成6000元，长期喜欢华为"


def test_noop_memory_hides_expired_long_term_entries() -> None:
    memory_store = NoopAiModelMemoryStore(clock=lambda: NOW)
    memory_store.upsert_user_memory(
        10,
        memory_type="brand_preference",
        memory_value="过期品牌",
        evidence="以前喜欢",
        confidence=0.9,
        expires_at=NOW - timedelta(seconds=1),
        source_goal_id="goal-old",
    )
    assert memory_store.load_user_memories(10) == []


def test_postgres_repository_normalizes_driver_url() -> None:
    repository = PostgresShoppingGoalRepository(
        "postgresql+psycopg://agent:agent@db/agent_ops"
    )
    assert repository.database_url == "postgresql://agent:agent@db/agent_ops"
