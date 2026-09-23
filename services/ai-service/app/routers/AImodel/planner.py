"""Hierarchical, bounded plan selection for AImodel shopping turns.

The planner classifies one normalized request and returns a D1 ``AgentPlan``.
It never executes a tool or generates a user-facing answer.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.routers.AImodel.agent_trace import (
    AgentTraceContext,
    AgentTraceEventType,
    AgentTraceStatus,
)
from app.routers.AImodel.clarification import (
    ClarificationDecision,
    ClarificationReason,
)
from app.routers.AImodel.intent_router import AImodelIntentRoute
from app.routers.AImodel.plan_models import (
    AgentPlan,
    AgentPlanValidator,
    ExecutionBudgetRequest,
    PlanValidationError,
    StepType,
    StopReason,
)
from app.routers.AImodel.schemas import (
    AiModelClarificationOption,
    AiModelClarificationPayload,
    AiModelPageContext,
)
from app.routers.AImodel.shopping_goal import (
    Constraint,
    DecisionStage,
    GoalEvidence,
    GoalField,
    GoalSourceType,
    OpenSlot,
    ShoppingGoal,
)

PLANNER_SCHEMA_VERSION = "1.0"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanningTaskType(StrEnum):
    """Closed task classes understood by the D2 planner."""

    DIRECT = "direct"
    CLARIFY = "clarify"
    KNOWLEDGE = "knowledge"
    PRODUCT_DETAIL = "product_detail"
    PRODUCT_SEARCH = "product_search"
    COMPARE = "compare"
    RECOMMEND = "recommend"
    ACTION_PREVIEW = "action_preview"


class PlanningSource(StrEnum):
    """How the accepted plan was produced."""

    TEMPLATE = "template"
    MODEL = "model"
    FALLBACK = "fallback"


class PlannerFallbackReason(StrEnum):
    """Stable reasons for choosing a bounded deterministic fallback."""

    GOAL_BLOCKED = "goal_blocked"
    UNSUPPORTED_ROUTE = "unsupported_route"
    MISSING_PAGE_ITEM = "missing_page_item"
    MODEL_INVALID = "model_invalid"
    MODEL_UNAVAILABLE = "model_unavailable"


class PlannerRouteView(_StrictModel):
    """Small route projection safe to send to a planning backend."""

    action: str = Field(min_length=1, max_length=64)
    domain: str | None = Field(default=None, max_length=64)
    category: str | None = Field(default=None, max_length=64)
    intent: str | None = Field(default=None, max_length=64)


class PlannerGoalFact(_StrictModel):
    """Normalized goal fact without evidence quotes or prompts."""

    field: GoalField
    attribute: str | None = Field(default=None, max_length=512)
    value: str = Field(min_length=1, max_length=512)


class PlannerGoalView(_StrictModel):
    """Evidence-free projection used only to select a plan shape."""

    revision: int = Field(ge=0)
    decision_stage: DecisionStage
    hard_constraints: tuple[PlannerGoalFact, ...] = ()
    preferences: tuple[PlannerGoalFact, ...] = ()
    exclusions: tuple[PlannerGoalFact, ...] = ()
    open_slots: tuple[str, ...] = Field(default=(), max_length=64)


class PlannerPageView(_StrictModel):
    """Bounded page-context summary without item IDs or routes."""

    page_type: str = Field(min_length=1, max_length=32)
    has_current_item: bool
    candidate_count: int = Field(ge=0, le=20)


class PlannerModelRequest(_StrictModel):
    """One structured request to an optional complex-plan backend."""

    schema_version: Literal["1.0"] = PLANNER_SCHEMA_VERSION
    task_type: PlanningTaskType
    route: PlannerRouteView
    goal: PlannerGoalView
    page_context: PlannerPageView | None = None
    attempt: int = Field(ge=1, le=2)
    previous_error_code: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_attempt_state(self) -> PlannerModelRequest:
        if self.attempt == 1 and self.previous_error_code is not None:
            raise ValueError("first attempt cannot have a previous error")
        if self.attempt == 2 and self.previous_error_code is None:
            raise ValueError("repair attempt requires a previous error code")
        return self


class PlannerModelBackend(Protocol):
    """Generate a D1 draft for a complex compare or recommend request."""

    def generate_plan(self, request: PlannerModelRequest) -> object:
        """Return an untrusted D1 draft; the server validates it before use."""


class PlanningResult(_StrictModel):
    """Final classification and validated plan returned by D2."""

    task_type: PlanningTaskType
    source: PlanningSource
    plan: AgentPlan
    model_attempts: int = Field(default=0, ge=0, le=2)
    fallback_reason: PlannerFallbackReason | None = None

    @model_validator(mode="after")
    def validate_source_state(self) -> PlanningResult:
        if self.source is PlanningSource.FALLBACK:
            if self.fallback_reason is None:
                raise ValueError("fallback source requires a reason")
        elif self.fallback_reason is not None:
            raise ValueError("non-fallback source cannot have a fallback reason")
        if self.source is PlanningSource.MODEL and self.model_attempts == 0:
            raise ValueError("model source requires at least one model attempt")
        if self.source is PlanningSource.TEMPLATE and self.model_attempts != 0:
            raise ValueError("template source cannot have model attempts")
        return self


class PlannerEvaluationReport(_StrictModel):
    """Deterministic aggregate over the versioned planning fixtures."""

    fixture_schema_version: int = Field(ge=1)
    case_count: int = Field(ge=30)
    correct_task_count: int = Field(ge=0)
    correct_shape_count: int = Field(ge=0)
    fallback_count: int = Field(ge=0)
    task_accuracy: float = Field(ge=0, le=1)
    plan_shape_accuracy: float = Field(ge=0, le=1)
    average_step_count: float = Field(gt=0)
    fallback_rate: float = Field(ge=0, le=1)
    task_counts: dict[str, int]
    turn_kind_counts: dict[str, int]
    task_mismatch_case_ids: tuple[str, ...] = ()
    shape_mismatch_case_ids: tuple[str, ...] = ()


class _FixtureRoute(_StrictModel):
    action: str = Field(min_length=1, max_length=64)
    intent: str = Field(min_length=1, max_length=64)
    domain: str = Field(default="support", min_length=1, max_length=64)
    category: str = Field(default="presale", min_length=1, max_length=64)


class _PlannerFixtureCase(_StrictModel):
    case_id: str = Field(min_length=1, max_length=128)
    turn_kind: Literal["single", "multi", "error"]
    route: _FixtureRoute
    page_context: AiModelPageContext | None = None
    goal_mode: Literal[
        "empty",
        "category",
        "open_category",
        "searching",
        "comparing",
    ]
    clarification_mode: Literal["proceed", "ask", "blocked"]
    expected_task_type: PlanningTaskType
    expected_step_types: tuple[StepType, ...] = Field(min_length=1)
    expected_source: PlanningSource


class _PlannerFixtureSuite(_StrictModel):
    schema_version: int = Field(ge=1)
    cases: tuple[_PlannerFixtureCase, ...] = Field(min_length=30)


class _ModelPlanRejected(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_ACTION_INTENTS = frozenset(
    {"action_preview", "add_to_cart", "cart_preview", "cart_add"}
)
_COMPARE_INTENTS = frozenset(
    {"compare", "comparison", "product_compare", "product_comparison"}
)
_RECOMMEND_INTENTS = frozenset(
    {
        "buying_recommendation",
        "recommend",
        "recommendation",
        "shopping_recommendation",
    }
)
_DETAIL_INTENTS = frozenset(
    {
        "detail",
        "parameter_consulting",
        "product_detail",
        "product_info",
        "specification",
        "stock",
    }
)
_COMPLEX_TASKS = frozenset({PlanningTaskType.COMPARE, PlanningTaskType.RECOMMEND})
_KNOWLEDGE_STEP_TIMEOUT_MS = 10_000


class HierarchicalPlanner:
    """Choose a fast-path template or a strictly validated complex plan."""

    def __init__(
        self,
        validator: AgentPlanValidator,
        *,
        model_backend: PlannerModelBackend | None = None,
    ) -> None:
        self.validator = validator
        self.model_backend = model_backend

    def plan(
        self,
        *,
        intent_route: AImodelIntentRoute,
        shopping_goal: ShoppingGoal,
        clarification: ClarificationDecision,
        page_context: AiModelPageContext | None = None,
        requested_budget: ExecutionBudgetRequest | Mapping[str, Any] | None = None,
        trace_context: AgentTraceContext | None = None,
    ) -> PlanningResult:
        """Return one bounded D1 plan without executing it."""

        task_type, unsupported = self._classify(
            intent_route,
            shopping_goal,
            clarification,
            page_context,
        )
        source = PlanningSource.TEMPLATE
        fallback_reason: PlannerFallbackReason | None = None
        model_attempts = 0

        if clarification.should_ask:
            task_type = PlanningTaskType.CLARIFY
        elif not clarification.may_proceed:
            task_type = PlanningTaskType.DIRECT
            source = PlanningSource.FALLBACK
            fallback_reason = PlannerFallbackReason.GOAL_BLOCKED
        elif unsupported:
            task_type = PlanningTaskType.DIRECT
            source = PlanningSource.FALLBACK
            fallback_reason = PlannerFallbackReason.UNSUPPORTED_ROUTE
        elif task_type in {
            PlanningTaskType.PRODUCT_DETAIL,
            PlanningTaskType.ACTION_PREVIEW,
        } and (page_context is None or page_context.current_item_id is None):
            task_type = PlanningTaskType.DIRECT
            source = PlanningSource.FALLBACK
            fallback_reason = PlannerFallbackReason.MISSING_PAGE_ITEM

        if task_type in _COMPLEX_TASKS and self.model_backend is not None:
            model_plan, model_attempts, model_fallback = self._try_model_plan(
                task_type=task_type,
                intent_route=intent_route,
                shopping_goal=shopping_goal,
                page_context=page_context,
                requested_budget=requested_budget,
                trace_context=trace_context,
            )
            if model_plan is not None:
                result = PlanningResult(
                    task_type=task_type,
                    source=PlanningSource.MODEL,
                    plan=model_plan,
                    model_attempts=model_attempts,
                )
                self._record_trace(trace_context, result)
                return result
            source = PlanningSource.FALLBACK
            fallback_reason = model_fallback

        plan = self._template_plan(
            task_type=task_type,
            intent_route=intent_route,
            shopping_goal=shopping_goal,
            clarification=clarification,
            page_context=page_context,
            requested_budget=requested_budget,
            trace_context=trace_context,
        )
        result = PlanningResult(
            task_type=task_type,
            source=source,
            plan=plan,
            model_attempts=model_attempts,
            fallback_reason=fallback_reason,
        )
        self._record_trace(trace_context, result)
        return result

    @staticmethod
    def _classify(
        intent_route: AImodelIntentRoute,
        shopping_goal: ShoppingGoal,
        clarification: ClarificationDecision,
        page_context: AiModelPageContext | None,
    ) -> tuple[PlanningTaskType, bool]:
        if clarification.should_ask:
            return PlanningTaskType.CLARIFY, False
        action = intent_route.action.strip().casefold()
        intent = (intent_route.intent or "").strip().casefold()
        if action in {"direct", "refuse"}:
            return PlanningTaskType.DIRECT, False
        if intent in _ACTION_INTENTS:
            return PlanningTaskType.ACTION_PREVIEW, False
        if intent in _COMPARE_INTENTS:
            return PlanningTaskType.COMPARE, False
        if intent in _RECOMMEND_INTENTS:
            return PlanningTaskType.RECOMMEND, False
        if (
            intent in _DETAIL_INTENTS
            and page_context is not None
            and page_context.current_item_id is not None
        ):
            return PlanningTaskType.PRODUCT_DETAIL, False
        if action == "product_api":
            if shopping_goal.decision_stage is DecisionStage.COMPARING:
                return PlanningTaskType.COMPARE, False
            if intent in _DETAIL_INTENTS or (
                page_context is not None and page_context.current_item_id is not None
            ):
                return PlanningTaskType.PRODUCT_DETAIL, False
            return PlanningTaskType.PRODUCT_SEARCH, False
        if action == "rag":
            return PlanningTaskType.KNOWLEDGE, False
        return PlanningTaskType.DIRECT, True

    def _try_model_plan(
        self,
        *,
        task_type: PlanningTaskType,
        intent_route: AImodelIntentRoute,
        shopping_goal: ShoppingGoal,
        page_context: AiModelPageContext | None,
        requested_budget: ExecutionBudgetRequest | Mapping[str, Any] | None,
        trace_context: AgentTraceContext | None,
    ) -> tuple[AgentPlan | None, int, PlannerFallbackReason]:
        previous_error_code: str | None = None
        backend_failed_only = True
        for attempt in (1, 2):
            request = PlannerModelRequest(
                task_type=task_type,
                route=_route_view(intent_route),
                goal=_goal_view(shopping_goal),
                page_context=_page_view(page_context),
                attempt=attempt,
                previous_error_code=previous_error_code,
            )
            try:
                raw_plan = self.model_backend.generate_plan(request)
            except Exception:
                previous_error_code = "model_backend_error"
                continue
            backend_failed_only = False
            try:
                plan = self.validator.validate(
                    raw_plan,
                    requested_budget=requested_budget,
                    trace_context=trace_context,
                )
                _validate_task_shape(task_type, plan)
            except PlanValidationError as error:
                previous_error_code = error.code
                continue
            except _ModelPlanRejected as error:
                previous_error_code = error.code
                continue
            return plan, attempt, PlannerFallbackReason.MODEL_INVALID
        fallback = (
            PlannerFallbackReason.MODEL_UNAVAILABLE
            if backend_failed_only
            else PlannerFallbackReason.MODEL_INVALID
        )
        return None, 2, fallback

    def _template_plan(
        self,
        *,
        task_type: PlanningTaskType,
        intent_route: AImodelIntentRoute,
        shopping_goal: ShoppingGoal,
        clarification: ClarificationDecision,
        page_context: AiModelPageContext | None,
        requested_budget: ExecutionBudgetRequest | Mapping[str, Any] | None,
        trace_context: AgentTraceContext | None,
    ) -> AgentPlan:
        plan_id = _stable_plan_id(
            task_type,
            intent_route,
            shopping_goal,
            clarification,
            page_context,
        )
        effective_budget = requested_budget
        knowledge_timeout_ms = 2_000
        if task_type is PlanningTaskType.KNOWLEDGE:
            knowledge_timeout_ms = _KNOWLEDGE_STEP_TIMEOUT_MS
            if requested_budget is None:
                effective_budget = ExecutionBudgetRequest(
                    step_timeout_ms=_KNOWLEDGE_STEP_TIMEOUT_MS
                )
            elif isinstance(requested_budget, ExecutionBudgetRequest):
                if requested_budget.step_timeout_ms is not None:
                    knowledge_timeout_ms = requested_budget.step_timeout_ms
                else:
                    effective_budget = requested_budget.model_copy(
                        update={"step_timeout_ms": _KNOWLEDGE_STEP_TIMEOUT_MS}
                    )
            elif isinstance(requested_budget, Mapping):
                requested_timeout = requested_budget.get("step_timeout_ms")
                if isinstance(requested_timeout, int):
                    knowledge_timeout_ms = requested_timeout
                elif requested_timeout is None:
                    effective_budget = {
                        **requested_budget,
                        "step_timeout_ms": _KNOWLEDGE_STEP_TIMEOUT_MS,
                    }

        return self.validator.validate(
            _template_draft(
                task_type,
                plan_id,
                knowledge_timeout_ms=knowledge_timeout_ms,
            ),
            requested_budget=effective_budget,
            trace_context=trace_context,
        )

    @staticmethod
    def _record_trace(
        trace_context: AgentTraceContext | None,
        result: PlanningResult,
    ) -> None:
        if trace_context is None:
            return
        event = trace_context.begin_event(
            AgentTraceEventType.PLAN,
            stage="hierarchical_planner",
            summary={
                "task_type": result.task_type.value,
                "source": result.source.value,
                "model_attempts": result.model_attempts,
                "fallback_reason": (
                    result.fallback_reason.value if result.fallback_reason else None
                ),
                "plan_id": result.plan.plan_id,
                "step_count": len(result.plan.steps),
                "step_types": [step.step_type.value for step in result.plan.steps],
                "topological_step_ids": list(result.plan.topological_step_ids),
                "budget": result.plan.budget.model_dump(mode="json"),
            },
        )
        event.finish(AgentTraceStatus.SUCCESS)


def _route_view(route: AImodelIntentRoute) -> PlannerRouteView:
    return PlannerRouteView(
        action=route.action,
        domain=route.domain,
        category=route.category,
        intent=route.intent,
    )


def _fact_view(item: Any) -> PlannerGoalFact:
    value = item.value
    if isinstance(value, str):
        normalized = value.strip()
    else:
        normalized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    return PlannerGoalFact(
        field=item.field,
        attribute=item.attribute,
        value=normalized,
    )


def _goal_view(goal: ShoppingGoal) -> PlannerGoalView:
    return PlannerGoalView(
        revision=goal.revision,
        decision_stage=goal.decision_stage,
        hard_constraints=tuple(_fact_view(item) for item in goal.hard_constraints),
        preferences=tuple(_fact_view(item) for item in goal.preferences),
        exclusions=tuple(_fact_view(item) for item in goal.exclusions),
        open_slots=tuple(
            f"{item.field.value}:{item.attribute}"
            if item.attribute
            else item.field.value
            for item in goal.open_slots
        ),
    )


def _page_view(page_context: AiModelPageContext | None) -> PlannerPageView | None:
    if page_context is None:
        return None
    return PlannerPageView(
        page_type=page_context.page_type,
        has_current_item=page_context.current_item_id is not None,
        candidate_count=len(page_context.candidate_refs),
    )


def _stable_plan_id(
    task_type: PlanningTaskType,
    route: AImodelIntentRoute,
    goal: ShoppingGoal,
    clarification: ClarificationDecision,
    page_context: AiModelPageContext | None,
) -> str:
    payload = {
        "task_type": task_type.value,
        "route": _route_view(route).model_dump(mode="json"),
        "goal": _goal_view(goal).model_dump(mode="json"),
        "clarification": {
            "should_ask": clarification.should_ask,
            "may_proceed": clarification.may_proceed,
            "reason": clarification.reason.value,
            "slot_key": clarification.slot_key,
        },
        "page_context": (
            _page_view(page_context).model_dump(mode="json")
            if page_context is not None
            else None
        ),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return f"planner-{task_type.value}-{digest}"


def _root_input(name: str, source: str, value_type: str) -> dict[str, str]:
    return {"name": name, "source": source, "value_type": value_type}


def _step_input(name: str, step_id: str, value_type: str) -> dict[str, str]:
    return {
        "name": name,
        "source": "step",
        "step_id": step_id,
        "value_type": value_type,
    }


def _dependency(step_id: str, output_type: str) -> dict[str, str]:
    return {"step_id": step_id, "output_type": output_type}


def _step(
    step_id: str,
    step_type: str,
    *,
    inputs: Sequence[dict[str, str]],
    output_type: str,
    dependencies: Sequence[dict[str, str]] = (),
    risk_level: str = "low",
    allowed_tools: Sequence[str] = (),
    timeout_ms: int = 2_000,
) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "step_type": step_type,
        "dependencies": list(dependencies),
        "inputs": list(inputs),
        "output_type": output_type,
        "risk_level": risk_level,
        "allowed_tools": list(allowed_tools),
        "timeout_ms": timeout_ms,
    }


def _search_step() -> dict[str, Any]:
    return _step(
        "s01_search",
        "product_search",
        inputs=[_root_input("shopping_goal", "goal", "shopping_goal")],
        output_type="candidate_refs",
        risk_level="medium",
        allowed_tools=["product_search"],
    )


def _snapshot_after_search() -> dict[str, Any]:
    return _step(
        "s02_snapshot",
        "snapshot",
        dependencies=[_dependency("s01_search", "candidate_refs")],
        inputs=[_step_input("candidate_refs", "s01_search", "candidate_refs")],
        output_type="product_snapshot",
        risk_level="medium",
        allowed_tools=["product_snapshot"],
    )


def _filter_step() -> dict[str, Any]:
    return _step(
        "s04_filter",
        "filter",
        dependencies=[_dependency("s02_snapshot", "product_snapshot")],
        inputs=[
            _step_input("products", "s02_snapshot", "product_snapshot"),
            _root_input("shopping_goal", "goal", "shopping_goal"),
        ],
        output_type="candidate_set",
    )


def _rank_step() -> dict[str, Any]:
    return _step(
        "s05_rank",
        "rank",
        dependencies=[
            _dependency("s02_snapshot", "product_snapshot"),
            _dependency("s04_filter", "candidate_set"),
        ],
        inputs=[
            _step_input("products", "s02_snapshot", "product_snapshot"),
            _step_input("candidates", "s04_filter", "candidate_set"),
            _root_input("shopping_goal", "goal", "shopping_goal"),
        ],
        output_type="ranking_result",
    )


def _template_draft(
    task_type: PlanningTaskType,
    plan_id: str,
    *,
    knowledge_timeout_ms: int = _KNOWLEDGE_STEP_TIMEOUT_MS,
) -> dict[str, Any]:
    if task_type is PlanningTaskType.DIRECT:
        steps = [
            _step(
                "s01_compose",
                "compose",
                inputs=[_root_input("question", "request", "user_query")],
                output_type="response_draft",
            )
        ]
        stop_reasons = ["completed", "safe_fallback"]
    elif task_type is PlanningTaskType.CLARIFY:
        steps = [
            _step(
                "s01_clarify",
                "clarify",
                inputs=[_root_input("shopping_goal", "goal", "shopping_goal")],
                output_type="clarification",
            )
        ]
        stop_reasons = ["needs_clarification", "safe_fallback"]
    elif task_type is PlanningTaskType.KNOWLEDGE:
        steps = [
            _step(
                "s01_rag",
                "rag_lookup",
                inputs=[_root_input("question", "request", "user_query")],
                output_type="knowledge_result",
                risk_level="medium",
                allowed_tools=["rag_lookup"],
                timeout_ms=knowledge_timeout_ms,
            ),
            _step(
                "s02_compose",
                "compose",
                dependencies=[_dependency("s01_rag", "knowledge_result")],
                inputs=[_step_input("knowledge", "s01_rag", "knowledge_result")],
                output_type="response_draft",
            ),
        ]
        stop_reasons = ["completed", "tool_denied", "timeout", "safe_fallback"]
    elif task_type is PlanningTaskType.PRODUCT_DETAIL:
        steps = [
            _step(
                "s01_snapshot",
                "snapshot",
                inputs=[_root_input("page_context", "context", "page_context")],
                output_type="product_snapshot",
                risk_level="medium",
                allowed_tools=["product_snapshot"],
            ),
            _step(
                "s02_compose",
                "compose",
                dependencies=[_dependency("s01_snapshot", "product_snapshot")],
                inputs=[_step_input("product", "s01_snapshot", "product_snapshot")],
                output_type="response_draft",
            ),
        ]
        stop_reasons = ["completed", "tool_denied", "timeout", "safe_fallback"]
    elif task_type is PlanningTaskType.PRODUCT_SEARCH:
        steps = [
            _search_step(),
            _snapshot_after_search(),
            _filter_step(),
            _step(
                "s05_compose",
                "compose",
                dependencies=[
                    _dependency("s02_snapshot", "product_snapshot"),
                    _dependency("s04_filter", "candidate_set"),
                ],
                inputs=[
                    _step_input("products", "s02_snapshot", "product_snapshot"),
                    _step_input("candidates", "s04_filter", "candidate_set"),
                ],
                output_type="response_draft",
            ),
        ]
        stop_reasons = [
            "completed",
            "no_candidate",
            "tool_denied",
            "timeout",
            "safe_fallback",
        ]
    elif task_type is PlanningTaskType.RECOMMEND:
        steps = [
            _search_step(),
            _snapshot_after_search(),
            _filter_step(),
            _rank_step(),
            _step(
                "s06_compose",
                "compose",
                dependencies=[_dependency("s05_rank", "ranking_result")],
                inputs=[_step_input("ranking", "s05_rank", "ranking_result")],
                output_type="response_draft",
            ),
        ]
        stop_reasons = [
            "completed",
            "no_candidate",
            "budget_exhausted",
            "tool_denied",
            "timeout",
            "safe_fallback",
        ]
    elif task_type is PlanningTaskType.COMPARE:
        steps = [
            _search_step(),
            _snapshot_after_search(),
            _step(
                "s03_reviews",
                "review_fetch",
                dependencies=[_dependency("s01_search", "candidate_refs")],
                inputs=[_step_input("candidate_refs", "s01_search", "candidate_refs")],
                output_type="review_collection",
                risk_level="medium",
                allowed_tools=["product_reviews"],
            ),
            _filter_step(),
            _rank_step(),
            _step(
                "s06_compare",
                "compare",
                dependencies=[
                    _dependency("s02_snapshot", "product_snapshot"),
                    _dependency("s03_reviews", "review_collection"),
                    _dependency("s05_rank", "ranking_result"),
                ],
                inputs=[
                    _step_input("products", "s02_snapshot", "product_snapshot"),
                    _step_input("reviews", "s03_reviews", "review_collection"),
                    _step_input("ranking", "s05_rank", "ranking_result"),
                ],
                output_type="comparison_matrix",
            ),
            _step(
                "s07_compose",
                "compose",
                dependencies=[_dependency("s06_compare", "comparison_matrix")],
                inputs=[_step_input("comparison", "s06_compare", "comparison_matrix")],
                output_type="response_draft",
            ),
        ]
        stop_reasons = [
            "completed",
            "no_candidate",
            "budget_exhausted",
            "tool_denied",
            "timeout",
            "safe_fallback",
        ]
    elif task_type is PlanningTaskType.ACTION_PREVIEW:
        steps = [
            _step(
                "s01_snapshot",
                "snapshot",
                inputs=[_root_input("page_context", "context", "page_context")],
                output_type="product_snapshot",
                risk_level="medium",
                allowed_tools=["product_snapshot"],
            ),
            _step(
                "s02_action_preview",
                "action_preview",
                dependencies=[_dependency("s01_snapshot", "product_snapshot")],
                inputs=[_step_input("product", "s01_snapshot", "product_snapshot")],
                output_type="action_preview",
                risk_level="high",
                allowed_tools=["action_preview"],
            ),
            _step(
                "s03_compose",
                "compose",
                dependencies=[_dependency("s02_action_preview", "action_preview")],
                inputs=[_step_input("preview", "s02_action_preview", "action_preview")],
                output_type="response_draft",
            ),
        ]
        stop_reasons = ["completed", "tool_denied", "timeout", "safe_fallback"]
    else:
        raise ValueError(f"unsupported planning task type: {task_type}")
    return {
        "schema_version": "1.0",
        "plan_id": plan_id,
        "steps": steps,
        "stop_reasons": stop_reasons,
    }


def _validate_task_shape(task_type: PlanningTaskType, plan: AgentPlan) -> None:
    if task_type not in _COMPLEX_TASKS:
        raise _ModelPlanRejected("model_not_allowed_for_task")
    by_type: dict[StepType, list[Any]] = {}
    for step in plan.steps:
        by_type.setdefault(step.step_type, []).append(step)
    required = {
        StepType.PRODUCT_SEARCH,
        StepType.SNAPSHOT,
        StepType.FILTER,
        StepType.RANK,
        StepType.COMPOSE,
    }
    allowed = set(required)
    if task_type is PlanningTaskType.COMPARE:
        required.update({StepType.REVIEW_FETCH, StepType.COMPARE})
        allowed.update({StepType.REVIEW_FETCH, StepType.COMPARE})
    else:
        allowed.add(StepType.REVIEW_FETCH)
    actual = set(by_type)
    if not required.issubset(actual) or not actual.issubset(allowed):
        raise _ModelPlanRejected("task_shape_mismatch")
    if any(len(steps) != 1 for steps in by_type.values()):
        raise _ModelPlanRejected("task_shape_mismatch")
    required_stop_reasons = {
        StopReason.NO_CANDIDATE,
        StopReason.BUDGET_EXHAUSTED,
        StopReason.TOOL_DENIED,
        StopReason.TIMEOUT,
        StopReason.SAFE_FALLBACK,
    }
    if not required_stop_reasons.issubset(set(plan.stop_reasons)):
        raise _ModelPlanRejected("task_shape_mismatch")

    def dependency_types(step_type: StepType) -> set[StepType]:
        step = by_type[step_type][0]
        by_id = {item.step_id: item.step_type for item in plan.steps}
        return {by_id[item.step_id] for item in step.dependencies}

    if dependency_types(StepType.SNAPSHOT) != {StepType.PRODUCT_SEARCH}:
        raise _ModelPlanRejected("task_shape_mismatch")
    if StepType.REVIEW_FETCH in actual and dependency_types(StepType.REVIEW_FETCH) != {
        StepType.PRODUCT_SEARCH
    }:
        raise _ModelPlanRejected("task_shape_mismatch")
    if dependency_types(StepType.FILTER) != {StepType.SNAPSHOT}:
        raise _ModelPlanRejected("task_shape_mismatch")
    rank_dependencies = dependency_types(StepType.RANK)
    if rank_dependencies != {StepType.SNAPSHOT, StepType.FILTER}:
        raise _ModelPlanRejected("task_shape_mismatch")
    if task_type is PlanningTaskType.COMPARE:
        compare_dependencies = dependency_types(StepType.COMPARE)
        if compare_dependencies != {
            StepType.SNAPSHOT,
            StepType.REVIEW_FETCH,
            StepType.RANK,
        }:
            raise _ModelPlanRejected("task_shape_mismatch")
        if dependency_types(StepType.COMPOSE) != {StepType.COMPARE}:
            raise _ModelPlanRejected("task_shape_mismatch")
    else:
        expected_compose_dependencies = {StepType.RANK}
        if StepType.REVIEW_FETCH in actual:
            expected_compose_dependencies.add(StepType.REVIEW_FETCH)
        if dependency_types(StepType.COMPOSE) != expected_compose_dependencies:
            raise _ModelPlanRejected("task_shape_mismatch")


_FIXTURE_TIME = datetime(2026, 9, 21, 16, 0, tzinfo=UTC)


def _fixture_evidence(*, system: bool = False) -> GoalEvidence:
    return GoalEvidence(
        source_type=(
            GoalSourceType.SYSTEM_DEFAULT if system else GoalSourceType.USER_TURN
        ),
        source_turn=None if system else 1,
        quote=None if system else "fixture category",
        confidence=0 if system else 1,
        created_at=_FIXTURE_TIME,
        updated_at=_FIXTURE_TIME,
    )


def _fixture_goal(mode: str) -> ShoppingGoal:
    if mode == "empty":
        return ShoppingGoal()
    if mode == "open_category":
        return ShoppingGoal(
            decision_stage=DecisionStage.CLARIFYING,
            stage_reason="required_slot_missing:category",
            open_slots=(
                OpenSlot(
                    field=GoalField.CATEGORY,
                    question="category required",
                    evidence=_fixture_evidence(system=True),
                ),
            ),
        )
    stage = {
        "category": DecisionStage.SEARCHING,
        "searching": DecisionStage.SEARCHING,
        "comparing": DecisionStage.COMPARING,
    }[mode]
    return ShoppingGoal(
        decision_stage=stage,
        hard_constraints=(
            Constraint(
                field=GoalField.CATEGORY,
                value="laptop",
                evidence=_fixture_evidence(),
            ),
        ),
    )


def _fixture_clarification(mode: str) -> ClarificationDecision:
    if mode == "ask":
        return ClarificationDecision(
            policy_version="b4-v1",
            should_ask=True,
            may_proceed=False,
            reason=ClarificationReason.MISSING_CATEGORY,
            slot_key="category",
            payload=AiModelClarificationPayload(
                answer="category required",
                options=[
                    AiModelClarificationOption(
                        option_id="category-laptop",
                        label="laptop",
                        value="laptop",
                    )
                ],
            ),
        )
    if mode == "blocked":
        return ClarificationDecision(
            policy_version="b4-v1",
            should_ask=False,
            may_proceed=False,
            reason=ClarificationReason.NO_CANDIDATES,
        )
    return ClarificationDecision(
        policy_version="b4-v1",
        should_ask=False,
        may_proceed=True,
        reason=ClarificationReason.NO_CLARIFICATION_NEEDED,
    )


def evaluate_planner_fixture_file(
    path: str | Path,
    planner: HierarchicalPlanner,
) -> PlannerEvaluationReport:
    """Evaluate classification, shape, step count, and fallback rate."""

    fixture_path = Path(path)
    try:
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        suite = _PlannerFixtureSuite.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise ValueError(f"invalid planner fixture: {fixture_path}") from error

    correct_task = 0
    correct_shape = 0
    fallback_count = 0
    total_steps = 0
    task_counts: Counter[str] = Counter()
    turn_kind_counts: Counter[str] = Counter()
    task_mismatches: list[str] = []
    shape_mismatches: list[str] = []
    for case in suite.cases:
        result = planner.plan(
            intent_route=AImodelIntentRoute(
                action=case.route.action,
                collection=("shopping_guides" if case.route.action == "rag" else None),
                collections=(
                    ("shopping_guides",) if case.route.action == "rag" else ()
                ),
                domain=case.route.domain,
                category=case.route.category,
                intent=case.route.intent,
                confidence=1,
                reason="planner_fixture",
            ),
            shopping_goal=_fixture_goal(case.goal_mode),
            clarification=_fixture_clarification(case.clarification_mode),
            page_context=case.page_context,
        )
        task_counts[result.task_type.value] += 1
        turn_kind_counts[case.turn_kind] += 1
        total_steps += len(result.plan.steps)
        fallback_count += result.source is PlanningSource.FALLBACK
        if result.task_type is case.expected_task_type:
            correct_task += 1
        else:
            task_mismatches.append(case.case_id)
        actual_steps = tuple(step.step_type for step in result.plan.steps)
        if (
            actual_steps == case.expected_step_types
            and result.source is case.expected_source
        ):
            correct_shape += 1
        else:
            shape_mismatches.append(case.case_id)
    count = len(suite.cases)
    return PlannerEvaluationReport(
        fixture_schema_version=suite.schema_version,
        case_count=count,
        correct_task_count=correct_task,
        correct_shape_count=correct_shape,
        fallback_count=fallback_count,
        task_accuracy=correct_task / count,
        plan_shape_accuracy=correct_shape / count,
        average_step_count=total_steps / count,
        fallback_rate=fallback_count / count,
        task_counts=dict(sorted(task_counts.items())),
        turn_kind_counts=dict(sorted(turn_kind_counts.items())),
        task_mismatch_case_ids=tuple(task_mismatches),
        shape_mismatch_case_ids=tuple(shape_mismatches),
    )


__all__ = [
    "HierarchicalPlanner",
    "PlannerEvaluationReport",
    "PlannerFallbackReason",
    "PlannerGoalFact",
    "PlannerGoalView",
    "PlannerModelBackend",
    "PlannerModelRequest",
    "PlannerPageView",
    "PlannerRouteView",
    "PlanningResult",
    "PlanningSource",
    "PlanningTaskType",
    "evaluate_planner_fixture_file",
]
