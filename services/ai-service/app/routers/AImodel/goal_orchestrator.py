"""Runtime orchestration for deterministic, persistent shopping-goal updates."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .clarification import ClarificationDecision, select_clarification
from .goal_extractor import GoalDelta, ModelExtractionRequest, extract_goal_delta
from .goal_repository import (
    InMemoryShoppingGoalRepository,
    PostgresShoppingGoalRepository,
    ShoppingGoalAlreadyExists,
    ShoppingGoalEventRecord,
    ShoppingGoalRecord,
    ShoppingGoalRepository,
    ShoppingGoalRevisionConflict,
    sync_reusable_goal_memories,
)
from .goal_state import CandidateStatus, GoalConflict, merge_goal_delta
from .memory import AiModelMemoryStore, get_aimodel_memory_store
from .schemas import AiModelPageContext
from .shopping_goal import ShoppingGoal


class ShoppingGoalTurnResult(BaseModel):
    """Result of one deterministic goal transition before Agent execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record: ShoppingGoalRecord
    delta: GoalDelta
    conflicts: tuple[GoalConflict, ...] = ()
    committed_events: tuple[ShoppingGoalEventRecord, ...] = ()
    clarification: ClarificationDecision
    revision_retries: int = Field(default=0, ge=0, le=2)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _next_source_turn(record: ShoppingGoalRecord | None) -> int:
    if record is None:
        return 1
    evidence_items = (
        *record.goal.hard_constraints,
        *record.goal.preferences,
        *record.goal.exclusions,
        *record.goal.open_slots,
    )
    return (
        max((item.evidence.source_turn or 0 for item in evidence_items), default=0) + 1
    )


class ShoppingGoalOrchestrator:
    """Load, extract, merge, atomically persist, and clarify one user turn."""

    def __init__(
        self,
        repository: ShoppingGoalRepository,
        memory_store: AiModelMemoryStore,
        *,
        clock: Callable[[], datetime] = _utc_now,
        model_extractor: Callable[[ModelExtractionRequest], object] | None = None,
        max_revision_retries: int = 1,
    ) -> None:
        if max_revision_retries < 0 or max_revision_retries > 2:
            raise ValueError("max_revision_retries must be between 0 and 2")
        self.repository = repository
        self.memory_store = memory_store
        self.clock = clock
        self.model_extractor = model_extractor
        self.max_revision_retries = max_revision_retries
        self.repository.initialize()

    def process_turn(
        self,
        *,
        conversation_id: int,
        user_id: int,
        text: str,
        page_context: AiModelPageContext | None = None,
        candidate_status: CandidateStatus = CandidateStatus.UNCHANGED,
        reference_time: datetime | None = None,
    ) -> ShoppingGoalTurnResult | None:
        """Apply one shopping-related turn; non-shopping turns remain passthrough."""

        observed_at = reference_time or self.clock()
        current = self.repository.load(conversation_id, user_id=user_id)
        delta = extract_goal_delta(
            text,
            source_turn=_next_source_turn(current),
            page_context=page_context,
            model_extractor=self.model_extractor,
            reference_time=observed_at,
        )
        if not delta.operations:
            return None

        retries = 0
        while True:
            base_goal = current.goal if current is not None else ShoppingGoal()
            merged = merge_goal_delta(
                base_goal,
                delta,
                candidate_status=candidate_status,
                reference_time=observed_at,
            )
            try:
                committed = self.repository.commit_transition(
                    conversation_id,
                    user_id=user_id,
                    expected_revision=current.revision if current is not None else None,
                    goal=merged.goal,
                    events=merged.events,
                )
                break
            except (ShoppingGoalAlreadyExists, ShoppingGoalRevisionConflict):
                if retries >= self.max_revision_retries:
                    raise
                current = self.repository.load(conversation_id, user_id=user_id)
                retries += 1

        clarification = select_clarification(
            committed.record.goal,
            conflicts=merged.conflicts,
            candidate_status=candidate_status,
        )
        sync_reusable_goal_memories(
            self.memory_store,
            user_id=user_id,
            goal_id=committed.record.goal_id,
            goal=committed.record.goal,
            now=observed_at,
            current_turn_text=text,
        )
        return ShoppingGoalTurnResult(
            record=committed.record,
            delta=delta,
            conflicts=merged.conflicts,
            committed_events=committed.events,
            clarification=clarification,
            revision_retries=retries,
        )


def shopping_goal_prompt_context(record: ShoppingGoalRecord) -> str:
    """Serialize only normalized goal facts, never source quotes or raw prompts."""

    goal = record.goal

    def public_item(item: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "field": item.field.value,
            "value": item.value,
        }
        if item.attribute is not None:
            payload["attribute"] = item.attribute
        return payload

    payload = {
        "schema_version": goal.schema_version,
        "goal_id": record.goal_id,
        "revision": record.revision,
        "decision_stage": goal.decision_stage.value,
        "hard_constraints": [public_item(item) for item in goal.hard_constraints],
        "preferences": [public_item(item) for item in goal.preferences],
        "exclusions": [public_item(item) for item in goal.exclusions],
        "open_slots": [
            {
                "field": item.field.value,
                **({"attribute": item.attribute} if item.attribute else {}),
            }
            for item in goal.open_slots
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


@lru_cache(maxsize=1)
def get_shopping_goal_orchestrator() -> ShoppingGoalOrchestrator:
    """Return the process-level orchestrator sharing the conversation store."""

    memory_store = get_aimodel_memory_store()
    database_url = os.getenv("DATABASE_URL", "").strip()
    repository: ShoppingGoalRepository
    if database_url:
        repository = PostgresShoppingGoalRepository(database_url)
    else:
        repository = InMemoryShoppingGoalRepository(
            conversation_owner=memory_store.get_conversation_owner
        )
    return ShoppingGoalOrchestrator(repository, memory_store)
