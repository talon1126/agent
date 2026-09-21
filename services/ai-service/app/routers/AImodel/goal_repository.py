"""Persistent shopping-goal repositories with optimistic concurrency control."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .goal_state import GoalChangeEvent
from .memory import (
    POSTGRES_AIMODEL_MEMORY_SCHEMA_SQL,
    AiModelMemoryStore,
    AiModelUserMemory,
    is_explicit_reusable_brand_preference,
)
from .shopping_goal import GoalField, GoalSourceType, ShoppingGoal


GoalId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
LONG_TERM_MEMORY_TTL = timedelta(days=180)


class ShoppingGoalRepositoryError(RuntimeError):
    """Base error for persistence failures callers may handle deterministically."""


class ShoppingGoalNotFound(ShoppingGoalRepositoryError):
    """Raised when a requested conversation has no shopping goal."""


class ShoppingGoalAlreadyExists(ShoppingGoalRepositoryError):
    """Raised when create is replayed for an existing conversation."""


class ShoppingGoalAccessDenied(ShoppingGoalRepositoryError):
    """Raised without goal details when a conversation belongs to another user."""


class ShoppingGoalRevisionConflict(ShoppingGoalRepositoryError):
    """Expose only revision metadata so the caller can reload and merge."""

    def __init__(self, *, expected_revision: int, actual_revision: int) -> None:
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(
            "shopping goal revision conflict: "
            f"expected {expected_revision}, actual {actual_revision}"
        )


class ShoppingGoalRecord(BaseModel):
    """One persisted, user-owned shopping-goal snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goal_id: GoalId
    conversation_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    schema_version: GoalId
    revision: int = Field(ge=0)
    goal: ShoppingGoal
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_snapshot(self) -> ShoppingGoalRecord:
        if self.schema_version != self.goal.schema_version:
            raise ValueError("record and goal schema versions must match")
        if self.revision != self.goal.revision:
            raise ValueError("record and goal revisions must match")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot predate created_at")
        return self


class ShoppingGoalEventRecord(BaseModel):
    """Append-only audit record linked to one stored goal revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: GoalId
    goal_id: GoalId
    conversation_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    revision: int = Field(ge=0)
    event: GoalChangeEvent
    created_at: datetime


@runtime_checkable
class ShoppingGoalRepository(Protocol):
    def initialize(self) -> None: ...

    def load(
        self,
        conversation_id: int,
        *,
        user_id: int,
    ) -> ShoppingGoalRecord | None: ...

    def create(
        self,
        *,
        conversation_id: int,
        user_id: int,
        goal: ShoppingGoal,
    ) -> ShoppingGoalRecord: ...

    def compare_and_save(
        self,
        conversation_id: int,
        *,
        user_id: int,
        expected_revision: int,
        goal: ShoppingGoal,
    ) -> ShoppingGoalRecord: ...

    def append_event(
        self,
        conversation_id: int,
        *,
        user_id: int,
        revision: int,
        event: GoalChangeEvent,
    ) -> ShoppingGoalEventRecord: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_identity(conversation_id: int, user_id: int) -> None:
    if conversation_id <= 0:
        raise ValueError("conversation_id must be positive")
    if user_id <= 0:
        raise ValueError("user_id must be positive")


def _validate_next_revision(goal: ShoppingGoal, expected_revision: int) -> None:
    if expected_revision < 0:
        raise ValueError("expected_revision cannot be negative")
    if goal.revision != expected_revision + 1:
        raise ValueError("saved goal revision must equal expected_revision + 1")


class InMemoryShoppingGoalRepository:
    """Thread-safe repository with the same externally visible CAS semantics."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = _utc_now,
        conversation_owner: Callable[[int], int | None] | None = None,
    ) -> None:
        self._clock = clock
        self._conversation_owner = conversation_owner
        self._lock = threading.RLock()
        self._records: dict[int, ShoppingGoalRecord] = {}
        self._events: list[ShoppingGoalEventRecord] = []

    def initialize(self) -> None:
        return None

    def _validate_create_owner(self, conversation_id: int, user_id: int) -> None:
        if self._conversation_owner is None:
            raise ShoppingGoalRepositoryError(
                "conversation owner resolver is required for goal creation"
            )
        owner = self._conversation_owner(conversation_id)
        if owner is None:
            raise ShoppingGoalNotFound("conversation does not exist")
        if owner != user_id:
            raise ShoppingGoalAccessDenied("shopping goal access denied")

    def _owned_record(self, conversation_id: int, user_id: int) -> ShoppingGoalRecord:
        record = self._records.get(conversation_id)
        if record is None:
            raise ShoppingGoalNotFound("shopping goal does not exist")
        if record.user_id != user_id:
            raise ShoppingGoalAccessDenied("shopping goal access denied")
        return record

    def load(
        self,
        conversation_id: int,
        *,
        user_id: int,
    ) -> ShoppingGoalRecord | None:
        _validate_identity(conversation_id, user_id)
        with self._lock:
            record = self._records.get(conversation_id)
            if record is None:
                return None
            if record.user_id != user_id:
                raise ShoppingGoalAccessDenied("shopping goal access denied")
            return record.model_copy(deep=True)

    def create(
        self,
        *,
        conversation_id: int,
        user_id: int,
        goal: ShoppingGoal,
    ) -> ShoppingGoalRecord:
        _validate_identity(conversation_id, user_id)
        validated_goal = ShoppingGoal.model_validate(goal.model_dump(mode="python"))
        with self._lock:
            self._validate_create_owner(conversation_id, user_id)
            if conversation_id in self._records:
                raise ShoppingGoalAlreadyExists("shopping goal already exists")
            now = self._clock()
            record = ShoppingGoalRecord(
                goal_id=str(uuid4()),
                conversation_id=conversation_id,
                user_id=user_id,
                schema_version=validated_goal.schema_version,
                revision=validated_goal.revision,
                goal=validated_goal,
                created_at=now,
                updated_at=now,
            )
            self._records[conversation_id] = record
            return record.model_copy(deep=True)

    def compare_and_save(
        self,
        conversation_id: int,
        *,
        user_id: int,
        expected_revision: int,
        goal: ShoppingGoal,
    ) -> ShoppingGoalRecord:
        _validate_identity(conversation_id, user_id)
        validated_goal = ShoppingGoal.model_validate(goal.model_dump(mode="python"))
        _validate_next_revision(validated_goal, expected_revision)
        with self._lock:
            current = self._owned_record(conversation_id, user_id)
            if current.revision != expected_revision:
                raise ShoppingGoalRevisionConflict(
                    expected_revision=expected_revision,
                    actual_revision=current.revision,
                )
            updated = current.model_copy(
                update={
                    "schema_version": validated_goal.schema_version,
                    "revision": validated_goal.revision,
                    "goal": validated_goal,
                    "updated_at": self._clock(),
                },
                deep=True,
            )
            self._records[conversation_id] = updated
            return updated.model_copy(deep=True)

    def append_event(
        self,
        conversation_id: int,
        *,
        user_id: int,
        revision: int,
        event: GoalChangeEvent,
    ) -> ShoppingGoalEventRecord:
        _validate_identity(conversation_id, user_id)
        validated_event = GoalChangeEvent.model_validate(
            event.model_dump(mode="python")
        )
        with self._lock:
            current = self._owned_record(conversation_id, user_id)
            if current.revision != revision:
                raise ShoppingGoalRevisionConflict(
                    expected_revision=revision,
                    actual_revision=current.revision,
                )
            stored = ShoppingGoalEventRecord(
                event_id=str(uuid4()),
                goal_id=current.goal_id,
                conversation_id=conversation_id,
                user_id=user_id,
                revision=revision,
                event=validated_event,
                created_at=self._clock(),
            )
            self._events.append(stored)
            return stored.model_copy(deep=True)


class PostgresShoppingGoalRepository:
    """PostgreSQL repository using atomic revision predicates for every update."""

    def __init__(
        self,
        database_url: str,
        *,
        connection_factory: Callable[[], Any] | None = None,
        clock: Callable[[], datetime] = _utc_now,
        conversation_owner: Callable[[int], int | None] | None = None,
    ) -> None:
        self.database_url = database_url.replace(
            "postgresql+psycopg://", "postgresql://"
        )
        self._connection_factory = connection_factory
        self._clock = clock
        self._conversation_owner = conversation_owner
        self._init_lock = threading.Lock()
        self._initialized = False

    def _connect(self) -> Any:
        if self._connection_factory is not None:
            return self._connection_factory()
        import psycopg

        return psycopg.connect(self.database_url)

    @staticmethod
    def _jsonb(payload: dict[str, Any]) -> Any:
        from psycopg.types.json import Jsonb

        return Jsonb(payload)

    def initialize(self) -> None:
        with self._init_lock:
            if self._initialized:
                return
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    for statement in POSTGRES_AIMODEL_MEMORY_SCHEMA_SQL:
                        cursor.execute(statement)
                connection.commit()
            self._initialized = True

    def _validate_conversation_owner(self, conversation_id: int, user_id: int) -> None:
        if self._conversation_owner is not None:
            owner = self._conversation_owner(conversation_id)
        else:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT user_id FROM conversation WHERE id = %s",
                        (conversation_id,),
                    )
                    row = cursor.fetchone()
            owner = int(row[0]) if row is not None else None
        if owner is None:
            raise ShoppingGoalNotFound("conversation does not exist")
        if owner != user_id:
            raise ShoppingGoalAccessDenied("shopping goal access denied")

    @staticmethod
    def _record_from_row(row: tuple[Any, ...]) -> ShoppingGoalRecord:
        payload = getattr(row[5], "obj", row[5])
        goal = ShoppingGoal.from_payload(payload)
        return ShoppingGoalRecord(
            goal_id=str(row[0]),
            conversation_id=int(row[1]),
            user_id=int(row[2]),
            schema_version=str(row[3]),
            revision=int(row[4]),
            goal=goal,
            created_at=row[6],
            updated_at=row[7],
        )

    def _select_row(self, conversation_id: int) -> tuple[Any, ...] | None:
        self.initialize()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT goal_id, conversation_id, user_id, schema_version,
                           revision, payload, created_at, updated_at
                    FROM shopping_goal_state
                    WHERE conversation_id = %s
                    """,
                    (conversation_id,),
                )
                return cursor.fetchone()

    def load(
        self,
        conversation_id: int,
        *,
        user_id: int,
    ) -> ShoppingGoalRecord | None:
        _validate_identity(conversation_id, user_id)
        row = self._select_row(conversation_id)
        if row is None:
            return None
        record = self._record_from_row(row)
        if record.user_id != user_id:
            raise ShoppingGoalAccessDenied("shopping goal access denied")
        return record

    def create(
        self,
        *,
        conversation_id: int,
        user_id: int,
        goal: ShoppingGoal,
    ) -> ShoppingGoalRecord:
        _validate_identity(conversation_id, user_id)
        validated_goal = ShoppingGoal.model_validate(goal.model_dump(mode="python"))
        self.initialize()
        self._validate_conversation_owner(conversation_id, user_id)
        goal_id = str(uuid4())
        now = self._clock()
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO shopping_goal_state (
                            goal_id, conversation_id, user_id, schema_version,
                            revision, payload, created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING goal_id, conversation_id, user_id, schema_version,
                                  revision, payload, created_at, updated_at
                        """,
                        (
                            goal_id,
                            conversation_id,
                            user_id,
                            validated_goal.schema_version,
                            validated_goal.revision,
                            self._jsonb(validated_goal.model_dump(mode="json")),
                            now,
                        ),
                    )
                    row = cursor.fetchone()
                connection.commit()
        except Exception as exc:
            existing_row = self._select_row(conversation_id)
            if existing_row is not None:
                existing = self._record_from_row(existing_row)
                if existing.user_id != user_id:
                    raise ShoppingGoalAccessDenied(
                        "shopping goal access denied"
                    ) from exc
                raise ShoppingGoalAlreadyExists("shopping goal already exists") from exc
            raise
        if row is None:
            raise ShoppingGoalRepositoryError("create did not return a shopping goal")
        return self._record_from_row(row)

    def compare_and_save(
        self,
        conversation_id: int,
        *,
        user_id: int,
        expected_revision: int,
        goal: ShoppingGoal,
    ) -> ShoppingGoalRecord:
        _validate_identity(conversation_id, user_id)
        validated_goal = ShoppingGoal.model_validate(goal.model_dump(mode="python"))
        _validate_next_revision(validated_goal, expected_revision)
        current = self.load(conversation_id, user_id=user_id)
        if current is None:
            raise ShoppingGoalNotFound("shopping goal does not exist")
        if current.revision != expected_revision:
            raise ShoppingGoalRevisionConflict(
                expected_revision=expected_revision,
                actual_revision=current.revision,
            )
        now = self._clock()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE shopping_goal_state
                    SET schema_version = %s, revision = %s, payload = %s,
                        updated_at = %s
                    WHERE conversation_id = %s AND user_id = %s AND revision = %s
                    RETURNING goal_id, conversation_id, user_id, schema_version,
                              revision, payload, created_at, updated_at
                    """,
                    (
                        validated_goal.schema_version,
                        validated_goal.revision,
                        self._jsonb(validated_goal.model_dump(mode="json")),
                        now,
                        conversation_id,
                        user_id,
                        expected_revision,
                    ),
                )
                row = cursor.fetchone()
            connection.commit()
        if row is not None:
            return self._record_from_row(row)
        latest = self.load(conversation_id, user_id=user_id)
        if latest is None:
            raise ShoppingGoalNotFound("shopping goal does not exist")
        raise ShoppingGoalRevisionConflict(
            expected_revision=expected_revision,
            actual_revision=latest.revision,
        )

    def append_event(
        self,
        conversation_id: int,
        *,
        user_id: int,
        revision: int,
        event: GoalChangeEvent,
    ) -> ShoppingGoalEventRecord:
        _validate_identity(conversation_id, user_id)
        validated_event = GoalChangeEvent.model_validate(
            event.model_dump(mode="python")
        )
        self.initialize()
        event_id = str(uuid4())
        now = self._clock()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT goal_id, conversation_id, user_id, schema_version,
                           revision, payload, created_at, updated_at
                    FROM shopping_goal_state
                    WHERE conversation_id = %s
                    FOR UPDATE
                    """,
                    (conversation_id,),
                )
                current_row = cursor.fetchone()
                if current_row is None:
                    raise ShoppingGoalNotFound("shopping goal does not exist")
                current = self._record_from_row(current_row)
                if current.user_id != user_id:
                    raise ShoppingGoalAccessDenied("shopping goal access denied")
                if current.revision != revision:
                    raise ShoppingGoalRevisionConflict(
                        expected_revision=revision,
                        actual_revision=current.revision,
                    )
                cursor.execute(
                    """
                    INSERT INTO shopping_goal_event (
                        event_id, goal_id, conversation_id, user_id,
                        revision, payload, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING event_id, goal_id, conversation_id, user_id,
                              revision, payload, created_at
                    """,
                    (
                        event_id,
                        current.goal_id,
                        conversation_id,
                        user_id,
                        revision,
                        self._jsonb(validated_event.model_dump(mode="json")),
                        now,
                    ),
                )
                row = cursor.fetchone()
            connection.commit()
        if row is None:
            raise ShoppingGoalRepositoryError("append did not return a goal event")
        payload = getattr(row[5], "obj", row[5])
        return ShoppingGoalEventRecord(
            event_id=str(row[0]),
            goal_id=str(row[1]),
            conversation_id=int(row[2]),
            user_id=int(row[3]),
            revision=int(row[4]),
            event=GoalChangeEvent.model_validate(payload),
            created_at=row[6],
        )


def project_reusable_user_memories(
    goal: ShoppingGoal,
    *,
    goal_id: str,
    now: datetime | None = None,
) -> tuple[AiModelUserMemory, ...]:
    """Project only explicit, reusable, unexpired goal facts into user memory."""

    reference_time = now or _utc_now()
    memories: list[AiModelUserMemory] = []
    for preference in goal.preferences:
        evidence = preference.evidence
        if preference.field is not GoalField.BRAND:
            continue
        if evidence.source_type is not GoalSourceType.USER_TURN or not evidence.quote:
            continue
        if not is_explicit_reusable_brand_preference(
            evidence.quote, str(preference.value)
        ):
            continue
        expires_at = evidence.updated_at + LONG_TERM_MEMORY_TTL
        if expires_at <= reference_time:
            continue
        memories.append(
            AiModelUserMemory(
                memory_type="brand_preference",
                memory_value=str(preference.value).strip(),
                evidence=evidence.quote,
                confidence=evidence.confidence,
                expires_at=expires_at,
                source_goal_id=goal_id,
            )
        )
    return tuple(memories)


def sync_reusable_goal_memories(
    memory_store: AiModelMemoryStore,
    *,
    user_id: int,
    goal_id: str,
    goal: ShoppingGoal,
    now: datetime | None = None,
) -> tuple[AiModelUserMemory, ...]:
    """Persist the conservative long-term projection through the existing store."""

    memories = project_reusable_user_memories(goal, goal_id=goal_id, now=now)
    for memory in memories:
        memory_store.upsert_user_memory(
            user_id,
            memory_type=memory.memory_type,
            memory_value=memory.memory_value,
            evidence=memory.evidence,
            confidence=memory.confidence,
            expires_at=memory.expires_at,
            source_goal_id=memory.source_goal_id,
        )
    return memories
