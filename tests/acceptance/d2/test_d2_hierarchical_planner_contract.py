"""Frozen acceptance contract for D2 hierarchical planning."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "ai-service"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.routers.AImodel.agent_trace import (  # noqa: E402
    AgentTraceContext,
    AgentTraceEventType,
)
from app.routers.AImodel.clarification import (  # noqa: E402
    ClarificationDecision,
    ClarificationReason,
)
from app.routers.AImodel.intent_router import AImodelIntentRoute  # noqa: E402
from app.routers.AImodel.plan_models import (  # noqa: E402
    AgentPlan,
    AgentPlanValidator,
    load_plan_policy,
)
from app.routers.AImodel.planner import (  # noqa: E402
    HierarchicalPlanner,
    PlannerFallbackReason,
    PlannerModelRequest,
    PlanningSource,
    PlanningTaskType,
    evaluate_planner_fixture_file,
)
from app.routers.AImodel.schemas import (  # noqa: E402
    AiModelClarificationOption,
    AiModelClarificationPayload,
    AiModelPageContext,
)
from app.routers.AImodel.shopping_goal import (  # noqa: E402
    Constraint,
    DecisionStage,
    GoalEvidence,
    GoalField,
    GoalSourceType,
    OpenSlot,
    ShoppingGoal,
)

NOW = datetime(2026, 9, 21, 16, 0, tzinfo=UTC)
FIXTURE_PATH = Path(__file__).with_name("planner_cases.json")


def _route(
    *,
    action: str,
    intent: str,
    category: str = "presale",
    domain: str = "support",
) -> AImodelIntentRoute:
    return AImodelIntentRoute(
        action=action,
        collection="shopping_guides" if action == "rag" else None,
        collections=("shopping_guides",) if action == "rag" else (),
        domain=domain,
        category=category,
        intent=intent,
        confidence=0.9,
        reason="acceptance route reason must not enter plan trace",
        matched_rule="acceptance",
    )


def _evidence(
    quote: str = "private-goal-quote",
    *,
    source_type: GoalSourceType = GoalSourceType.USER_TURN,
) -> GoalEvidence:
    return GoalEvidence(
        source_type=source_type,
        source_turn=None if source_type is GoalSourceType.SYSTEM_DEFAULT else 1,
        quote=None if source_type is GoalSourceType.SYSTEM_DEFAULT else quote,
        confidence=0 if source_type is GoalSourceType.SYSTEM_DEFAULT else 1,
        created_at=NOW,
        updated_at=NOW,
    )


def _goal(
    *,
    stage: DecisionStage = DecisionStage.SEARCHING,
    open_category: bool = False,
) -> ShoppingGoal:
    if open_category:
        return ShoppingGoal(
            decision_stage=DecisionStage.CLARIFYING,
            stage_reason="required_slot_missing:category",
            open_slots=(
                OpenSlot(
                    field=GoalField.CATEGORY,
                    question="想买什么品类？",
                    evidence=_evidence(source_type=GoalSourceType.SYSTEM_DEFAULT),
                ),
            ),
        )
    return ShoppingGoal(
        decision_stage=stage,
        hard_constraints=(
            Constraint(
                field=GoalField.CATEGORY,
                value="laptop",
                evidence=_evidence(),
            ),
        ),
    )


def _proceed() -> ClarificationDecision:
    return ClarificationDecision(
        policy_version="b4-v1",
        should_ask=False,
        may_proceed=True,
        reason=ClarificationReason.NO_CLARIFICATION_NEEDED,
    )


def _ask() -> ClarificationDecision:
    return ClarificationDecision(
        policy_version="b4-v1",
        should_ask=True,
        may_proceed=False,
        reason=ClarificationReason.MISSING_CATEGORY,
        slot_key="category",
        payload=AiModelClarificationPayload(
            answer="想买什么品类？",
            options=[
                AiModelClarificationOption(
                    option_id="category-laptop",
                    label="笔记本电脑",
                    value="laptop",
                )
            ],
        ),
    )


def _blocked() -> ClarificationDecision:
    return ClarificationDecision(
        policy_version="b4-v1",
        should_ask=False,
        may_proceed=False,
        reason=ClarificationReason.NO_CANDIDATES,
    )


def _planner(model_backend: Any | None = None) -> HierarchicalPlanner:
    return HierarchicalPlanner(
        AgentPlanValidator(load_plan_policy()),
        model_backend=model_backend,
    )


def _draft_from_plan(plan: AgentPlan) -> dict[str, Any]:
    payload = plan.model_dump(mode="json")
    for server_field in ("policy_version", "budget", "topological_step_ids"):
        payload.pop(server_field)
    return payload


class _SequenceBackend:
    def __init__(self, *outputs: object) -> None:
        self.outputs = list(outputs)
        self.requests: list[PlannerModelRequest] = []

    def generate_plan(self, request: PlannerModelRequest) -> object:
        self.requests.append(request)
        output = self.outputs[len(self.requests) - 1]
        if isinstance(output, Exception):
            raise output
        return output


@pytest.mark.parametrize(
    ("route", "goal", "clarification", "page", "expected"),
    [
        (
            _route(action="direct", intent="greeting"),
            _goal(),
            _proceed(),
            None,
            "direct",
        ),
        (
            _route(action="rag", intent="parameter_consulting"),
            _goal(),
            _proceed(),
            None,
            "knowledge",
        ),
        (
            _route(action="product_api", intent="product_detail"),
            _goal(),
            _proceed(),
            AiModelPageContext(page_type="product", current_item_id="sku-1"),
            "product_detail",
        ),
        (
            _route(action="product_api", intent="catalog_search"),
            _goal(),
            _proceed(),
            AiModelPageContext(page_type="search", search_query="laptop"),
            "product_search",
        ),
        (
            _route(action="rag", intent="comparison"),
            _goal(),
            _proceed(),
            None,
            "compare",
        ),
        (
            _route(action="rag", intent="buying_recommendation"),
            _goal(),
            _proceed(),
            None,
            "recommend",
        ),
        (
            _route(action="product_api", intent="add_to_cart"),
            _goal(),
            _proceed(),
            AiModelPageContext(page_type="product", current_item_id="sku-1"),
            "action_preview",
        ),
        (
            _route(action="rag", intent="comparison"),
            _goal(open_category=True),
            _ask(),
            None,
            "clarify",
        ),
    ],
)
def test_classifier_covers_all_planning_task_types(
    route: AImodelIntentRoute,
    goal: ShoppingGoal,
    clarification: ClarificationDecision,
    page: AiModelPageContext | None,
    expected: str,
) -> None:
    result = _planner().plan(
        intent_route=route,
        shopping_goal=goal,
        clarification=clarification,
        page_context=page,
    )

    assert result.task_type == expected


def test_missing_critical_slot_produces_only_clarification() -> None:
    result = _planner().plan(
        intent_route=_route(action="rag", intent="buying_recommendation"),
        shopping_goal=_goal(open_category=True),
        clarification=_ask(),
    )

    assert result.task_type is PlanningTaskType.CLARIFY
    assert result.source is PlanningSource.TEMPLATE
    assert [step.step_type.value for step in result.plan.steps] == ["clarify"]
    assert result.plan.stop_reasons[0].value == "needs_clarification"


def test_detail_and_knowledge_fast_paths_contain_no_unrelated_steps() -> None:
    detail = _planner().plan(
        intent_route=_route(action="product_api", intent="product_detail"),
        shopping_goal=_goal(),
        clarification=_proceed(),
        page_context=AiModelPageContext(
            page_type="product",
            current_item_id="sku-1",
        ),
    )
    knowledge = _planner().plan(
        intent_route=_route(action="rag", intent="operation_guide"),
        shopping_goal=_goal(),
        clarification=_proceed(),
    )

    assert [step.step_type.value for step in detail.plan.steps] == [
        "snapshot",
        "compose",
    ]
    assert detail.plan.steps[0].inputs[0].source.value == "context"
    assert [step.step_type.value for step in knowledge.plan.steps] == [
        "rag_lookup",
        "compose",
    ]


def test_compare_plan_exposes_parallel_reads_and_ordered_decision_steps() -> None:
    result = _planner().plan(
        intent_route=_route(action="rag", intent="comparison"),
        shopping_goal=_goal(stage=DecisionStage.COMPARING),
        clarification=_proceed(),
    )
    by_type = {step.step_type.value: step for step in result.plan.steps}

    assert [step.step_type.value for step in result.plan.steps] == [
        "product_search",
        "snapshot",
        "review_fetch",
        "filter",
        "rank",
        "compare",
        "compose",
    ]
    assert {item.step_id for item in by_type["snapshot"].dependencies} == {
        by_type["product_search"].step_id
    }
    assert {item.step_id for item in by_type["review_fetch"].dependencies} == {
        by_type["product_search"].step_id
    }
    assert by_type["filter"].step_id in {
        item.step_id for item in by_type["rank"].dependencies
    }
    assert by_type["rank"].step_id in {
        item.step_id for item in by_type["compare"].dependencies
    }


def test_model_plan_gets_one_repair_then_controlled_fallback() -> None:
    backend = _SequenceBackend(
        {"schema_version": "1.0", "plan_id": "bad-first", "steps": []},
        {
            "schema_version": "1.0",
            "plan_id": "bad-second",
            "steps": [
                {
                    "step_id": "s01_shell",
                    "step_type": "shell_command",
                    "inputs": [],
                    "output_type": "response_draft",
                    "risk_level": "low",
                    "allowed_tools": ["all_tools"],
                    "timeout_ms": 1000,
                }
            ],
            "stop_reasons": ["completed"],
        },
    )
    result = _planner(backend).plan(
        intent_route=_route(action="rag", intent="comparison"),
        shopping_goal=_goal(stage=DecisionStage.COMPARING),
        clarification=_proceed(),
    )

    assert len(backend.requests) == 2
    assert backend.requests[0].attempt == 1
    assert backend.requests[1].attempt == 2
    assert backend.requests[1].previous_error_code
    assert result.source is PlanningSource.FALLBACK
    assert result.fallback_reason is PlannerFallbackReason.MODEL_INVALID
    assert "all_tools" not in {
        tool.value for step in result.plan.steps for tool in step.allowed_tools
    }


def test_schema_valid_but_wrong_task_shape_is_repaired_once() -> None:
    template = _planner().plan(
        intent_route=_route(action="rag", intent="comparison"),
        shopping_goal=_goal(stage=DecisionStage.COMPARING),
        clarification=_proceed(),
    )
    wrong_shape = _draft_from_plan(
        _planner()
        .plan(
            intent_route=_route(action="direct", intent="greeting"),
            shopping_goal=_goal(),
            clarification=_proceed(),
        )
        .plan
    )
    backend = _SequenceBackend(wrong_shape, _draft_from_plan(template.plan))

    result = _planner(backend).plan(
        intent_route=_route(action="rag", intent="comparison"),
        shopping_goal=_goal(stage=DecisionStage.COMPARING),
        clarification=_proceed(),
    )

    assert result.source is PlanningSource.MODEL
    assert result.model_attempts == 2
    assert backend.requests[1].previous_error_code == "task_shape_mismatch"


def test_fast_paths_never_call_model_and_templates_are_deterministic() -> None:
    backend = _SequenceBackend(RuntimeError("must not be called"))
    planner = _planner(backend)
    arguments = {
        "intent_route": _route(action="rag", intent="operation_guide"),
        "shopping_goal": _goal(),
        "clarification": _proceed(),
    }

    left = planner.plan(**arguments)
    right = planner.plan(**arguments)

    assert backend.requests == []
    assert left.plan == right.plan
    assert left.source is PlanningSource.TEMPLATE


def test_model_context_uses_normalized_goal_without_allowing_goal_override() -> None:
    invalid_override = _draft_from_plan(
        _planner()
        .plan(
            intent_route=_route(action="direct", intent="greeting"),
            shopping_goal=_goal(),
            clarification=_proceed(),
        )
        .plan
    )
    invalid_override["goal_override"] = {"category": "phone"}
    backend = _SequenceBackend(invalid_override, invalid_override)

    result = _planner(backend).plan(
        intent_route=_route(action="rag", intent="buying_recommendation"),
        shopping_goal=_goal(),
        clarification=_proceed(),
    )

    request_json = backend.requests[0].model_dump_json()
    assert "laptop" in request_json
    assert "private-goal-quote" not in request_json
    assert result.source is PlanningSource.FALLBACK
    assert result.fallback_reason is PlannerFallbackReason.MODEL_INVALID


def test_blocked_or_unsupported_inputs_use_tool_free_fallback() -> None:
    blocked = _planner().plan(
        intent_route=_route(action="rag", intent="buying_recommendation"),
        shopping_goal=_goal(),
        clarification=_blocked(),
    )
    unsupported = _planner().plan(
        intent_route=_route(action="order_api", intent="order_status"),
        shopping_goal=_goal(),
        clarification=_proceed(),
    )

    assert blocked.fallback_reason is PlannerFallbackReason.GOAL_BLOCKED
    assert unsupported.fallback_reason is PlannerFallbackReason.UNSUPPORTED_ROUTE
    for result in (blocked, unsupported):
        assert result.source is PlanningSource.FALLBACK
        assert all(not step.allowed_tools for step in result.plan.steps)


def test_planner_trace_is_complete_and_does_not_copy_raw_reasoning() -> None:
    trace = AgentTraceContext.start(
        user_query="private user query and hidden reasoning",
        conversation_id=42,
    )
    result = _planner().plan(
        intent_route=_route(action="rag", intent="comparison"),
        shopping_goal=_goal(stage=DecisionStage.COMPARING),
        clarification=_proceed(),
        trace_context=trace,
    )

    event = trace.events[-1]
    encoded = json.dumps(event.to_record(), ensure_ascii=False, default=str)
    assert event.event_type is AgentTraceEventType.PLAN
    assert event.stage == "hierarchical_planner"
    assert event.summary["task_type"] == "compare"
    assert event.summary["source"] == "template"
    assert event.summary["step_count"] == len(result.plan.steps)
    assert event.summary["budget"] == result.plan.budget.model_dump(mode="json")
    assert "private user query" not in encoded
    assert "private-goal-quote" not in encoded
    assert "acceptance route reason" not in encoded


def test_fixture_suite_reports_classification_shape_and_fallback_metrics() -> None:
    report = evaluate_planner_fixture_file(FIXTURE_PATH, _planner())

    assert report.case_count >= 30
    assert report.task_accuracy == 1
    assert report.plan_shape_accuracy == 1
    assert report.average_step_count > 0
    assert 0 <= report.fallback_rate < 0.2
