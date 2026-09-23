"""Frozen end-to-end acceptance contract for the B5 goal-state runtime."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "ai-service"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.routers.AImodel.goal_orchestrator import (  # noqa: E402
    ShoppingGoalOrchestrator,
    shopping_goal_prompt_context,
)
from app.routers.AImodel.goal_repository import (  # noqa: E402
    InMemoryShoppingGoalRepository,
)
from app.routers.AImodel.memory import NoopAiModelMemoryStore  # noqa: E402
from app.routers.AImodel.schemas import AiModelChatRequest  # noqa: E402
from app.routers.AImodel.service import stream_chat_events  # noqa: E402
from app.routers.AImodel.shopping_goal import GoalField  # noqa: E402


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def build_runtime() -> tuple[
    NoopAiModelMemoryStore,
    InMemoryShoppingGoalRepository,
    ShoppingGoalOrchestrator,
    int,
]:
    memory_store = NoopAiModelMemoryStore(clock=lambda: NOW)
    conversation_id = memory_store.ensure_conversation(
        None,
        user_id=9,
        first_message="想买手机",
    )
    repository = InMemoryShoppingGoalRepository(
        clock=lambda: NOW,
        conversation_owner=memory_store.get_conversation_owner,
    )
    orchestrator = ShoppingGoalOrchestrator(
        repository,
        memory_store,
        clock=lambda: NOW,
    )
    return memory_store, repository, orchestrator, conversation_id


def _field_value(goal, field: GoalField):
    for collection in (goal.hard_constraints, goal.preferences, goal.exclusions):
        for item in collection:
            if item.field is field:
                return item.value
    return None


def _parse_sse(raw_event: str) -> tuple[str, dict]:
    lines = raw_event.strip().splitlines()
    return (
        lines[0].removeprefix("event: "),
        json.loads(lines[1].removeprefix("data: ")),
    )


def test_orchestrator_loads_merges_and_persists_multiturn_goal() -> None:
    memory_store, repository, orchestrator, conversation_id = build_runtime()

    first = orchestrator.process_turn(
        conversation_id=conversation_id,
        user_id=9,
        text="想买手机，预算不超过5000元，不要苹果",
        reference_time=NOW,
    )
    second = orchestrator.process_turn(
        conversation_id=conversation_id,
        user_id=9,
        text="预算改成6000元，长期喜欢华为",
        reference_time=NOW,
    )

    assert first is not None and second is not None
    assert second.record.goal_id == first.record.goal_id
    assert second.record.revision > first.record.revision
    assert _field_value(second.record.goal, GoalField.CATEGORY) == "electronics"
    assert _field_value(second.record.goal, GoalField.BUDGET_MAX) == Decimal("6000")
    assert _field_value(second.record.goal, GoalField.BRAND) == "Huawei"
    assert second.committed_events
    assert repository.load(conversation_id, user_id=9) == second.record
    assert [item.memory_value for item in memory_store.load_user_memories(9)] == [
        "Huawei"
    ]

    prompt_context = shopping_goal_prompt_context(second.record)
    assert "goal_id" in prompt_context
    assert "hard_constraints" in prompt_context
    assert "长期喜欢华为" not in prompt_context


def test_orchestrator_skips_non_shopping_turn_without_existing_goal() -> None:
    _memory_store, repository, orchestrator, conversation_id = build_runtime()

    result = orchestrator.process_turn(
        conversation_id=conversation_id,
        user_id=9,
        text="今天天气怎么样",
        reference_time=NOW,
    )

    assert result is None
    assert repository.load(conversation_id, user_id=9) is None


def test_stream_returns_structured_clarification_without_calling_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_store, _repository, orchestrator, conversation_id = build_runtime()
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

    def unexpected_runner(*_args, **_kwargs):
        raise AssertionError("clarification must bypass the LLM runner")

    events = [
        _parse_sse(raw)
        for raw in stream_chat_events(
            AiModelChatRequest(
                user_id=9,
                conversation_id=conversation_id,
                message="预算不超过5000元",
            ),
            mock_api_url="http://mock-api",
            streaming_agent_runner=unexpected_runner,
            memory_store=memory_store,
            goal_orchestrator=orchestrator,
        )
    ]

    done = next(payload for event, payload in events if event == "done")
    assert done["response_type"] == "clarification"
    assert done["payload"]["response_type"] == "clarification"
    assert len(done["payload"]["options"]) >= 2
    assert len(memory_store.list_messages(conversation_id, user_id=9)) == 2


def test_conversation_owner_cannot_be_reassigned_by_another_user() -> None:
    memory_store, _repository, _orchestrator, conversation_id = build_runtime()

    with pytest.raises(PermissionError, match="conversation access denied"):
        memory_store.ensure_conversation(
            conversation_id,
            user_id=10,
            first_message="接管会话",
        )
    assert memory_store.get_conversation_owner(conversation_id) == 9
