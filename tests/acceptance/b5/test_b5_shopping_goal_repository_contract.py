"""Frozen acceptance contract for B5 shopping-goal persistence."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "ai-service"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.routers.AImodel.goal_extractor import DeltaAction  # noqa: E402
from app.routers.AImodel.goal_repository import (  # noqa: E402
    InMemoryShoppingGoalRepository,
    PostgresShoppingGoalRepository,
    ShoppingGoalAccessDenied,
    ShoppingGoalRevisionConflict,
    project_reusable_user_memories,
)
from app.routers.AImodel.goal_state import (  # noqa: E402
    GoalChangeEvent,
    MergeEventOutcome,
)
from app.routers.AImodel.memory import (  # noqa: E402
    POSTGRES_AIMODEL_MEMORY_SCHEMA_SQL,
    extract_user_memories_from_text,
)
from app.routers.AImodel.shopping_goal import (  # noqa: E402
    Constraint,
    GoalEvidence,
    GoalField,
    GoalSourceType,
    Preference,
    ShoppingGoal,
)


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
FIXTURE = json.loads(
    (ROOT / "fixtures" / "evals" / "shopping_goal_repository_cases.json").read_text(
        encoding="utf-8"
    )
)


def evidence(quote: str, *, updated_at: datetime = NOW) -> GoalEvidence:
    return GoalEvidence(
        source_type=GoalSourceType.USER_TURN,
        source_turn=1,
        quote=quote,
        confidence=1,
        created_at=updated_at,
        updated_at=updated_at,
    )


def goal(revision: int = 0, *, brand: str = "小米") -> ShoppingGoal:
    return ShoppingGoal(
        revision=revision,
        hard_constraints=(
            Constraint(
                field=GoalField.CATEGORY,
                value="手机",
                evidence=evidence("想买手机"),
            ),
            Constraint(
                field=GoalField.BUDGET_MAX,
                value=5000,
                evidence=evidence("预算五千"),
            ),
        ),
        preferences=(
            Preference(
                field=GoalField.BRAND,
                value=brand,
                evidence=evidence(f"长期偏好{brand}"),
            ),
        ),
    )


def change_event() -> GoalChangeEvent:
    brand = Preference(
        field=GoalField.BRAND,
        value="华为",
        evidence=evidence("改成华为"),
    )
    return GoalChangeEvent(
        action=DeltaAction.REPLACE,
        field=GoalField.BRAND,
        source_turn=2,
        after=(brand,),
        outcome=MergeEventOutcome.APPLIED,
    )


class _FakeCursor:
    def __init__(self, database: _FakePostgresDatabase) -> None:
        self.database = database
        self.row: tuple[Any, ...] | None = None

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str, params: tuple[Any, ...] | None = None) -> None:
        sql = " ".join(statement.split()).lower()
        params = params or ()
        self.row = None
        if sql.startswith(("create ", "alter ")):
            self.database.ddl_runs += 1
            return
        if "insert into shopping_goal_state" in sql:
            goal_id, conversation_id, user_id, version, revision, payload, now = params
            payload = getattr(payload, "obj", payload)
            if conversation_id in self.database.goals:
                raise RuntimeError("duplicate conversation goal")
            self.database.goals[conversation_id] = {
                "goal_id": goal_id,
                "conversation_id": conversation_id,
                "user_id": user_id,
                "schema_version": version,
                "revision": revision,
                "payload": payload,
                "created_at": now,
                "updated_at": now,
            }
            self.row = self.database.goal_row(conversation_id)
            return
        if "select goal_id" in sql and "from shopping_goal_state" in sql:
            self.row = self.database.goal_row(params[0])
            return
        if "update shopping_goal_state" in sql:
            version, revision, payload, now, conversation_id, user_id, expected = params
            current = self.database.goals.get(conversation_id)
            if (
                current is not None
                and current["user_id"] == user_id
                and current["revision"] == expected
            ):
                current.update(
                    schema_version=version,
                    revision=revision,
                    payload=getattr(payload, "obj", payload),
                    updated_at=now,
                )
                self.row = self.database.goal_row(conversation_id)
            return
        if "insert into shopping_goal_event" in sql:
            event_id, goal_id, conversation_id, user_id, revision, payload, now = params
            self.database.events.append(
                {
                    "event_id": event_id,
                    "goal_id": goal_id,
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "revision": revision,
                    "payload": getattr(payload, "obj", payload),
                    "created_at": now,
                }
            )
            self.row = (
                event_id,
                goal_id,
                conversation_id,
                user_id,
                revision,
                getattr(payload, "obj", payload),
                now,
            )
            return
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.row


class _FakeConnection:
    def __init__(self, database: _FakePostgresDatabase) -> None:
        self.database = database

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.database)

    def commit(self) -> None:
        self.database.commits += 1


class _FakePostgresDatabase:
    def __init__(self) -> None:
        self.goals: dict[int, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.ddl_runs = 0
        self.commits = 0

    def connect(self) -> _FakeConnection:
        return _FakeConnection(self)

    def goal_row(self, conversation_id: int) -> tuple[Any, ...] | None:
        current = self.goals.get(conversation_id)
        if current is None:
            return None
        return (
            current["goal_id"],
            current["conversation_id"],
            current["user_id"],
            current["schema_version"],
            current["revision"],
            current["payload"],
            current["created_at"],
            current["updated_at"],
        )


def repository_factories() -> tuple[tuple[str, Any], ...]:
    database = _FakePostgresDatabase()

    def conversation_owner(conversation_id: int) -> int | None:
        return 9 if conversation_id in {71, 72} else None

    return (
        (
            "memory",
            lambda: InMemoryShoppingGoalRepository(
                clock=lambda: NOW,
                conversation_owner=conversation_owner,
            ),
        ),
        (
            "postgres",
            lambda: PostgresShoppingGoalRepository(
                "postgresql://test",
                connection_factory=database.connect,
                clock=lambda: NOW,
                conversation_owner=conversation_owner,
            ),
        ),
    )


@pytest.mark.parametrize("backend,factory", repository_factories())
def test_repository_contract_restores_state_and_rejects_stale_writes(
    backend: str, factory: Any
) -> None:
    repository = factory()
    created = repository.create(conversation_id=71, user_id=9, goal=goal())
    restored = (
        factory().load(71, user_id=9)
        if backend == "postgres"
        else repository.load(71, user_id=9)
    )

    assert restored == created
    first_update = goal(revision=1, brand="华为")
    saved = repository.compare_and_save(
        71,
        user_id=9,
        expected_revision=0,
        goal=first_update,
    )
    assert saved.revision == 1
    with pytest.raises(ShoppingGoalRevisionConflict) as caught:
        repository.compare_and_save(
            71,
            user_id=9,
            expected_revision=0,
            goal=goal(revision=1, brand="苹果"),
        )
    assert caught.value.expected_revision == 0
    assert caught.value.actual_revision == 1
    assert repository.load(71, user_id=9).goal == first_update


@pytest.mark.parametrize("_backend,factory", repository_factories())
def test_repository_contract_enforces_user_ownership_and_appends_events(
    _backend: str, factory: Any
) -> None:
    repository = factory()
    repository.create(conversation_id=72, user_id=9, goal=goal())

    with pytest.raises(ShoppingGoalAccessDenied):
        repository.load(72, user_id=10)
    with pytest.raises(ShoppingGoalAccessDenied):
        repository.compare_and_save(
            72,
            user_id=10,
            expected_revision=0,
            goal=goal(revision=1),
        )
    stored_event = repository.append_event(
        72,
        user_id=9,
        revision=0,
        event=change_event(),
    )
    assert stored_event.conversation_id == 72
    assert stored_event.user_id == 9
    assert stored_event.event == change_event()


@pytest.mark.parametrize("_backend,factory", repository_factories())
def test_repository_contract_rejects_create_without_matching_conversation_owner(
    _backend: str, factory: Any
) -> None:
    repository = factory()

    with pytest.raises(ShoppingGoalAccessDenied):
        repository.create(conversation_id=71, user_id=10, goal=goal())


def test_long_term_memory_projection_is_explicit_evidenced_and_expiring() -> None:
    current = goal()
    memories = project_reusable_user_memories(
        current,
        goal_id="goal-1",
        now=NOW,
    )

    assert [(item.memory_type, item.memory_value) for item in memories] == [
        ("brand_preference", "小米")
    ]
    assert memories[0].evidence == "长期偏好小米"
    assert memories[0].expires_at == NOW + timedelta(days=180)
    assert all("budget" not in item.memory_type for item in memories)
    assert extract_user_memories_from_text("这次预算有限，最多五千", user_id=9) == []
    for statement in FIXTURE["long_term_memory"]["rejected_brand_statements"]:
        assert extract_user_memories_from_text(statement, user_id=9, now=NOW) == []

    expired_brand = Preference(
        field=GoalField.BRAND,
        value="索尼",
        evidence=evidence("以前偏好索尼", updated_at=NOW - timedelta(days=181)),
    )
    expired = ShoppingGoal(preferences=(expired_brand,))
    assert project_reusable_user_memories(expired, goal_id="goal-2", now=NOW) == ()


def test_schema_is_idempotent_and_never_truncates_existing_memory_data() -> None:
    schema = "\n".join(POSTGRES_AIMODEL_MEMORY_SCHEMA_SQL).upper()
    assert "TRUNCATE" not in schema
    assert "CREATE TABLE IF NOT EXISTS SHOPPING_GOAL_STATE" in schema
    assert "CREATE TABLE IF NOT EXISTS SHOPPING_GOAL_EVENT" in schema
    assert "CONVERSATION_ID INTEGER NOT NULL UNIQUE" in schema
    assert "REVISION INTEGER NOT NULL" in schema
    assert "PAYLOAD JSONB NOT NULL" in schema
    assert "ADD COLUMN IF NOT EXISTS EXPIRES_AT" in schema
    assert FIXTURE["long_term_memory"]["ttl_days"] == 180


def test_postgres_initialize_is_process_idempotent() -> None:
    database = _FakePostgresDatabase()
    repository = PostgresShoppingGoalRepository(
        "postgresql://test",
        connection_factory=database.connect,
        clock=lambda: NOW,
    )
    repository.initialize()
    first_ddl_runs = database.ddl_runs
    repository.initialize()
    assert database.ddl_runs == first_ddl_runs
    assert database.commits == 1
