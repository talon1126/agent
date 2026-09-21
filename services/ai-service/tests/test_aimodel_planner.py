from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.routers.AImodel.agent_trace import AgentTraceContext
from app.routers.AImodel.clarification import (
    ClarificationDecision,
    ClarificationReason,
)
from app.routers.AImodel.intent_router import AImodelIntentRoute
from app.routers.AImodel.plan_models import (
    AgentPlan,
    AgentPlanValidator,
    load_plan_policy,
)
from app.routers.AImodel.planner import (
    HierarchicalPlanner,
    PlannerFallbackReason,
    PlannerModelRequest,
    PlanningSource,
    PlanningTaskType,
    evaluate_planner_fixture_file,
)
from app.routers.AImodel.schemas import AiModelPageContext
from app.routers.AImodel.shopping_goal import (
    Constraint,
    DecisionStage,
    GoalEvidence,
    GoalField,
    GoalSourceType,
    ShoppingGoal,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = ROOT / "tests" / "acceptance" / "d2" / "planner_cases.json"
NOW = datetime(2026, 9, 21, 16, 0, tzinfo=UTC)


def _route(action: str, intent: str) -> AImodelIntentRoute:
    return AImodelIntentRoute(
        action=action,
        collection="shopping_guides" if action == "rag" else None,
        collections=("shopping_guides",) if action == "rag" else (),
        domain="support",
        category="presale",
        intent=intent,
        confidence=0.9,
        reason="unit-test",
    )


def _goal(*, quote: str = "need a laptop") -> ShoppingGoal:
    return ShoppingGoal(
        decision_stage=DecisionStage.SEARCHING,
        hard_constraints=(
            Constraint(
                field=GoalField.CATEGORY,
                value="laptop",
                evidence=GoalEvidence(
                    source_type=GoalSourceType.USER_TURN,
                    source_turn=1,
                    quote=quote,
                    confidence=1,
                    created_at=NOW,
                    updated_at=NOW,
                ),
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


def _planner(backend: object | None = None) -> HierarchicalPlanner:
    return HierarchicalPlanner(
        AgentPlanValidator(load_plan_policy()),
        model_backend=backend,
    )


def _draft(plan: AgentPlan) -> dict[str, object]:
    payload = plan.model_dump(mode="json")
    payload.pop("policy_version")
    payload.pop("budget")
    payload.pop("topological_step_ids")
    return payload


class _Backend:
    def __init__(self, *outputs: object) -> None:
        self.outputs = outputs
        self.requests: list[PlannerModelRequest] = []

    def generate_plan(self, request: PlannerModelRequest) -> object:
        self.requests.append(request)
        output = self.outputs[len(self.requests) - 1]
        if isinstance(output, Exception):
            raise output
        return output


@pytest.mark.parametrize(
    ("action", "intent", "page", "expected_task", "expected_steps"),
    [
        ("direct", "greeting", None, "direct", ["compose"]),
        ("rag", "operation_guide", None, "knowledge", ["rag_lookup", "compose"]),
        (
            "product_api",
            "product_detail",
            AiModelPageContext(page_type="product", current_item_id="sku-1"),
            "product_detail",
            ["snapshot", "compose"],
        ),
        (
            "product_api",
            "catalog_search",
            None,
            "product_search",
            ["product_search", "snapshot", "filter", "compose"],
        ),
        (
            "rag",
            "buying_recommendation",
            None,
            "recommend",
            ["product_search", "snapshot", "filter", "rank", "compose"],
        ),
        (
            "rag",
            "comparison",
            None,
            "compare",
            [
                "product_search",
                "snapshot",
                "review_fetch",
                "filter",
                "rank",
                "compare",
                "compose",
            ],
        ),
    ],
)
def test_templates_are_minimal_and_d1_valid(
    action: str,
    intent: str,
    page: AiModelPageContext | None,
    expected_task: str,
    expected_steps: list[str],
) -> None:
    result = _planner().plan(
        intent_route=_route(action, intent),
        shopping_goal=_goal(),
        clarification=_proceed(),
        page_context=page,
    )

    assert result.task_type == expected_task
    assert [step.step_type.value for step in result.plan.steps] == expected_steps
    assert result.source is PlanningSource.TEMPLATE


def test_explicit_detail_without_current_item_fails_closed() -> None:
    result = _planner().plan(
        intent_route=_route("product_api", "product_detail"),
        shopping_goal=_goal(),
        clarification=_proceed(),
    )

    assert result.task_type is PlanningTaskType.DIRECT
    assert result.source is PlanningSource.FALLBACK
    assert result.fallback_reason is PlannerFallbackReason.MISSING_PAGE_ITEM
    assert all(not step.allowed_tools for step in result.plan.steps)


def test_valid_complex_model_plan_is_accepted_under_server_budget() -> None:
    template = _planner().plan(
        intent_route=_route("rag", "buying_recommendation"),
        shopping_goal=_goal(),
        clarification=_proceed(),
    )
    backend = _Backend(_draft(template.plan))

    result = _planner(backend).plan(
        intent_route=_route("rag", "buying_recommendation"),
        shopping_goal=_goal(),
        clarification=_proceed(),
        requested_budget={"max_steps": 8, "max_candidates": 12},
    )

    assert result.source is PlanningSource.MODEL
    assert result.model_attempts == 1
    assert result.plan.budget.max_steps == 8
    assert result.plan.budget.max_candidates == 12


def test_backend_failure_is_retried_once_then_uses_template() -> None:
    backend = _Backend(RuntimeError("offline"), RuntimeError("still offline"))

    result = _planner(backend).plan(
        intent_route=_route("rag", "comparison"),
        shopping_goal=_goal(),
        clarification=_proceed(),
    )

    assert len(backend.requests) == 2
    assert result.model_attempts == 2
    assert result.source is PlanningSource.FALLBACK
    assert result.fallback_reason is PlannerFallbackReason.MODEL_UNAVAILABLE


def test_model_backend_is_never_used_for_fast_path() -> None:
    backend = _Backend(RuntimeError("must not run"))

    result = _planner(backend).plan(
        intent_route=_route("rag", "shipping_policy"),
        shopping_goal=_goal(),
        clarification=_proceed(),
    )

    assert backend.requests == []
    assert result.task_type is PlanningTaskType.KNOWLEDGE


def test_plan_id_ignores_goal_evidence_quote_but_tracks_normalized_goal() -> None:
    first = _planner().plan(
        intent_route=_route("product_api", "catalog_search"),
        shopping_goal=_goal(quote="first private quote"),
        clarification=_proceed(),
    )
    second = _planner().plan(
        intent_route=_route("product_api", "catalog_search"),
        shopping_goal=_goal(quote="second private quote"),
        clarification=_proceed(),
    )

    assert first.plan.plan_id == second.plan.plan_id


def test_fallback_trace_records_reason_without_backend_exception() -> None:
    trace = AgentTraceContext.start(user_query="private trace query")
    backend = _Backend(RuntimeError("secret backend detail"), RuntimeError("again"))

    result = _planner(backend).plan(
        intent_route=_route("rag", "comparison"),
        shopping_goal=_goal(),
        clarification=_proceed(),
        trace_context=trace,
    )

    event = trace.events[-1]
    encoded = json.dumps(event.to_record(), default=str)
    assert event.summary["fallback_reason"] == "model_unavailable"
    assert event.summary["plan_id"] == result.plan.plan_id
    assert "secret backend detail" not in encoded
    assert "private trace query" not in encoded


def test_fixture_report_covers_every_task_and_turn_kind() -> None:
    report = evaluate_planner_fixture_file(FIXTURE_PATH, _planner())

    assert report.case_count == 34
    assert set(report.task_counts) == {item.value for item in PlanningTaskType}
    assert report.turn_kind_counts == {"error": 4, "multi": 15, "single": 15}
    assert report.task_mismatch_case_ids == ()
    assert report.shape_mismatch_case_ids == ()


def test_fixture_reader_rejects_too_small_suite(tmp_path: Path) -> None:
    path = tmp_path / "small.json"
    path.write_text(
        json.dumps({"schema_version": 1, "cases": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid planner fixture"):
        evaluate_planner_fixture_file(path, _planner())
