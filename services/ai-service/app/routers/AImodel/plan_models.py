"""Typed, server-bounded planning contract for the shopping Agent.

This module validates static plans only. It deliberately does not choose tools,
execute steps, or persist runtime progress.
"""

from __future__ import annotations

import heapq
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.routers.AImodel.agent_trace import (
    AgentTraceContext,
    AgentTraceEvent,
    AgentTraceEventType,
    AgentTraceStatus,
)

PLAN_SCHEMA_VERSION = "1.0"
DEFAULT_PLAN_POLICY_PATH = Path(__file__).with_name("plan_policy.yaml")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StepType(StrEnum):
    """Closed set of static capabilities implemented by the Agent."""

    CLARIFY = "clarify"
    PRODUCT_SEARCH = "product_search"
    SNAPSHOT = "snapshot"
    REVIEW_FETCH = "review_fetch"
    RAG_LOOKUP = "rag_lookup"
    FILTER = "filter"
    RANK = "rank"
    COMPARE = "compare"
    COMPOSE = "compose"
    ACTION_PREVIEW = "action_preview"


class PlanTool(StrEnum):
    """Logical tool grants; D3 will bind them to concrete server tools."""

    PRODUCT_SEARCH = "product_search"
    PRODUCT_SNAPSHOT = "product_snapshot"
    PRODUCT_REVIEWS = "product_reviews"
    RAG_LOOKUP = "rag_lookup"
    ACTION_PREVIEW = "action_preview"


class RiskLevel(StrEnum):
    """Risk class used by the static step contract."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InputSource(StrEnum):
    """Origin of a typed step input."""

    REQUEST = "request"
    GOAL = "goal"
    CONTEXT = "context"
    STEP = "step"


class PlanValueType(StrEnum):
    """Closed data types exchanged by plan steps."""

    USER_QUERY = "user_query"
    SHOPPING_GOAL = "shopping_goal"
    PAGE_CONTEXT = "page_context"
    USER_CONTEXT = "user_context"
    CLARIFICATION = "clarification"
    CANDIDATE_REFS = "candidate_refs"
    PRODUCT_SNAPSHOT = "product_snapshot"
    REVIEW_COLLECTION = "review_collection"
    KNOWLEDGE_RESULT = "knowledge_result"
    CANDIDATE_SET = "candidate_set"
    RANKING_RESULT = "ranking_result"
    COMPARISON_MATRIX = "comparison_matrix"
    RESPONSE_DRAFT = "response_draft"
    ACTION_PREVIEW = "action_preview"


class StopReason(StrEnum):
    """Closed terminal reasons available to a future plan executor."""

    COMPLETED = "completed"
    NEEDS_CLARIFICATION = "needs_clarification"
    NO_CANDIDATE = "no_candidate"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TOOL_DENIED = "tool_denied"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    VALIDATION_FAILED = "validation_failed"
    SAFE_FALLBACK = "safe_fallback"


class StepDependency(_StrictModel):
    """Declare a predecessor and the output type expected from it."""

    step_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    output_type: PlanValueType


class PlanInputReference(_StrictModel):
    """Point to a typed server input or a predecessor output."""

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    source: InputSource
    value_type: PlanValueType
    step_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
    )

    @model_validator(mode="after")
    def validate_source_reference(self) -> PlanInputReference:
        if self.source is InputSource.STEP and self.step_id is None:
            raise ValueError("step inputs require step_id")
        if self.source is not InputSource.STEP and self.step_id is not None:
            raise ValueError("server inputs cannot declare step_id")
        return self


class PlanStep(_StrictModel):
    """One statically typed node in an Agent plan DAG."""

    step_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    step_type: StepType
    dependencies: tuple[StepDependency, ...] = ()
    inputs: tuple[PlanInputReference, ...] = Field(min_length=1)
    output_type: PlanValueType
    risk_level: RiskLevel
    allowed_tools: tuple[PlanTool, ...] = ()
    timeout_ms: int = Field(ge=100, le=60_000)


class ExecutionBudget(_StrictModel):
    """Effective limits owned by the server and embedded in a validated plan."""

    max_steps: int = Field(ge=1, le=100)
    max_candidates: int = Field(ge=1, le=1_000)
    max_concurrency: int = Field(ge=1, le=100)
    step_timeout_ms: int = Field(ge=100, le=60_000)
    total_timeout_ms: int = Field(ge=100, le=600_000)
    max_model_calls: int = Field(ge=0, le=100)
    max_retries: int = Field(ge=0, le=20)

    @model_validator(mode="after")
    def validate_timeout_order(self) -> ExecutionBudget:
        if self.total_timeout_ms < self.step_timeout_ms:
            raise ValueError("total_timeout_ms must cover one step timeout")
        return self


class ExecutionBudgetRequest(_StrictModel):
    """Optional reductions or bounded requests supplied outside the plan draft."""

    max_steps: int | None = Field(default=None, ge=1)
    max_candidates: int | None = Field(default=None, ge=1)
    max_concurrency: int | None = Field(default=None, ge=1)
    step_timeout_ms: int | None = Field(default=None, ge=100)
    total_timeout_ms: int | None = Field(default=None, ge=100)
    max_model_calls: int | None = Field(default=None, ge=0)
    max_retries: int | None = Field(default=None, ge=0)


class PlanPolicy(_StrictModel):
    """Versioned server defaults and hard upper bounds."""

    schema_version: Literal["1.0"]
    policy_version: str = Field(min_length=1, max_length=64)
    defaults: ExecutionBudget
    limits: ExecutionBudget

    @model_validator(mode="after")
    def validate_defaults_within_limits(self) -> PlanPolicy:
        for field_name in ExecutionBudget.model_fields:
            if getattr(self.defaults, field_name) > getattr(self.limits, field_name):
                raise ValueError(f"default {field_name} exceeds the hard limit")
        return self


class AgentPlanDraft(_StrictModel):
    """Model-authored portion of a plan, excluding all server-owned limits."""

    schema_version: Literal["1.0"]
    plan_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    steps: tuple[PlanStep, ...] = Field(min_length=1)
    stop_reasons: tuple[StopReason, ...] = Field(min_length=1)


class AgentPlan(AgentPlanDraft):
    """Validated, policy-bound plan that can be serialized for recovery."""

    policy_version: str = Field(min_length=1, max_length=64)
    budget: ExecutionBudget
    topological_step_ids: tuple[str, ...] = Field(min_length=1)


class PlanValidationError(ValueError):
    """Expose a stable failure code without returning unsafe draft content."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class _StepContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    output_type: PlanValueType
    risk_level: RiskLevel
    allowed_tools: frozenset[PlanTool]
    required_input_types: frozenset[PlanValueType]
    required_any_input_types: frozenset[PlanValueType] = frozenset()
    allowed_input_types: frozenset[PlanValueType]


_CONTRACTS: dict[StepType, _StepContract] = {
    StepType.CLARIFY: _StepContract(
        output_type=PlanValueType.CLARIFICATION,
        risk_level=RiskLevel.LOW,
        allowed_tools=frozenset(),
        required_input_types=frozenset({PlanValueType.SHOPPING_GOAL}),
        allowed_input_types=frozenset(
            {PlanValueType.SHOPPING_GOAL, PlanValueType.USER_QUERY}
        ),
    ),
    StepType.PRODUCT_SEARCH: _StepContract(
        output_type=PlanValueType.CANDIDATE_REFS,
        risk_level=RiskLevel.MEDIUM,
        allowed_tools=frozenset({PlanTool.PRODUCT_SEARCH}),
        required_input_types=frozenset({PlanValueType.SHOPPING_GOAL}),
        allowed_input_types=frozenset(
            {
                PlanValueType.SHOPPING_GOAL,
                PlanValueType.USER_QUERY,
                PlanValueType.PAGE_CONTEXT,
            }
        ),
    ),
    StepType.SNAPSHOT: _StepContract(
        output_type=PlanValueType.PRODUCT_SNAPSHOT,
        risk_level=RiskLevel.MEDIUM,
        allowed_tools=frozenset({PlanTool.PRODUCT_SNAPSHOT}),
        required_input_types=frozenset(),
        required_any_input_types=frozenset(
            {PlanValueType.CANDIDATE_REFS, PlanValueType.PAGE_CONTEXT}
        ),
        allowed_input_types=frozenset(
            {PlanValueType.CANDIDATE_REFS, PlanValueType.PAGE_CONTEXT}
        ),
    ),
    StepType.REVIEW_FETCH: _StepContract(
        output_type=PlanValueType.REVIEW_COLLECTION,
        risk_level=RiskLevel.MEDIUM,
        allowed_tools=frozenset({PlanTool.PRODUCT_REVIEWS}),
        required_input_types=frozenset({PlanValueType.CANDIDATE_REFS}),
        allowed_input_types=frozenset(
            {PlanValueType.CANDIDATE_REFS, PlanValueType.RANKING_RESULT}
        ),
    ),
    StepType.RAG_LOOKUP: _StepContract(
        output_type=PlanValueType.KNOWLEDGE_RESULT,
        risk_level=RiskLevel.MEDIUM,
        allowed_tools=frozenset({PlanTool.RAG_LOOKUP}),
        required_input_types=frozenset({PlanValueType.USER_QUERY}),
        allowed_input_types=frozenset(
            {
                PlanValueType.USER_QUERY,
                PlanValueType.SHOPPING_GOAL,
                PlanValueType.PAGE_CONTEXT,
            }
        ),
    ),
    StepType.FILTER: _StepContract(
        output_type=PlanValueType.CANDIDATE_SET,
        risk_level=RiskLevel.LOW,
        allowed_tools=frozenset(),
        required_input_types=frozenset(
            {PlanValueType.PRODUCT_SNAPSHOT, PlanValueType.SHOPPING_GOAL}
        ),
        allowed_input_types=frozenset(
            {
                PlanValueType.PRODUCT_SNAPSHOT,
                PlanValueType.SHOPPING_GOAL,
                PlanValueType.USER_CONTEXT,
            }
        ),
    ),
    StepType.RANK: _StepContract(
        output_type=PlanValueType.RANKING_RESULT,
        risk_level=RiskLevel.LOW,
        allowed_tools=frozenset(),
        required_input_types=frozenset(
            {
                PlanValueType.CANDIDATE_SET,
                PlanValueType.PRODUCT_SNAPSHOT,
                PlanValueType.SHOPPING_GOAL,
            }
        ),
        allowed_input_types=frozenset(
            {
                PlanValueType.CANDIDATE_SET,
                PlanValueType.PRODUCT_SNAPSHOT,
                PlanValueType.SHOPPING_GOAL,
                PlanValueType.USER_CONTEXT,
            }
        ),
    ),
    StepType.COMPARE: _StepContract(
        output_type=PlanValueType.COMPARISON_MATRIX,
        risk_level=RiskLevel.LOW,
        allowed_tools=frozenset(),
        required_input_types=frozenset(
            {PlanValueType.PRODUCT_SNAPSHOT, PlanValueType.RANKING_RESULT}
        ),
        allowed_input_types=frozenset(
            {
                PlanValueType.PRODUCT_SNAPSHOT,
                PlanValueType.RANKING_RESULT,
                PlanValueType.REVIEW_COLLECTION,
                PlanValueType.SHOPPING_GOAL,
            }
        ),
    ),
    StepType.COMPOSE: _StepContract(
        output_type=PlanValueType.RESPONSE_DRAFT,
        risk_level=RiskLevel.LOW,
        allowed_tools=frozenset(),
        required_input_types=frozenset(),
        allowed_input_types=frozenset(
            {
                PlanValueType.CLARIFICATION,
                PlanValueType.KNOWLEDGE_RESULT,
                PlanValueType.PRODUCT_SNAPSHOT,
                PlanValueType.REVIEW_COLLECTION,
                PlanValueType.CANDIDATE_SET,
                PlanValueType.RANKING_RESULT,
                PlanValueType.COMPARISON_MATRIX,
                PlanValueType.RESPONSE_DRAFT,
                PlanValueType.ACTION_PREVIEW,
                PlanValueType.USER_QUERY,
                PlanValueType.SHOPPING_GOAL,
                PlanValueType.PAGE_CONTEXT,
            }
        ),
    ),
    StepType.ACTION_PREVIEW: _StepContract(
        output_type=PlanValueType.ACTION_PREVIEW,
        risk_level=RiskLevel.HIGH,
        allowed_tools=frozenset({PlanTool.ACTION_PREVIEW}),
        required_input_types=frozenset({PlanValueType.PRODUCT_SNAPSHOT}),
        allowed_input_types=frozenset(
            {
                PlanValueType.PRODUCT_SNAPSHOT,
                PlanValueType.RANKING_RESULT,
                PlanValueType.PAGE_CONTEXT,
            }
        ),
    ),
}

_ROOT_INPUT_TYPES: dict[InputSource, frozenset[PlanValueType]] = {
    InputSource.REQUEST: frozenset({PlanValueType.USER_QUERY}),
    InputSource.GOAL: frozenset({PlanValueType.SHOPPING_GOAL}),
    InputSource.CONTEXT: frozenset(
        {PlanValueType.PAGE_CONTEXT, PlanValueType.USER_CONTEXT}
    ),
}
_MODEL_STEP_TYPES = frozenset({StepType.CLARIFY, StepType.COMPOSE})


class AgentPlanValidator:
    """Build and restore plans under one immutable server policy."""

    def __init__(self, policy: PlanPolicy) -> None:
        self.policy = policy

    def validate(
        self,
        draft: AgentPlanDraft | Mapping[str, Any],
        *,
        requested_budget: ExecutionBudgetRequest | Mapping[str, Any] | None = None,
        trace_context: AgentTraceContext | None = None,
    ) -> AgentPlan:
        """Validate a draft and attach the effective server-owned budget."""

        event = self._start_trace(trace_context)
        try:
            parsed_draft = self._parse_draft(draft)
            budget = self._resolve_budget(requested_budget)
            topology = self._validate_steps(parsed_draft.steps, budget)
            plan = AgentPlan(
                **parsed_draft.model_dump(),
                policy_version=self.policy.policy_version,
                budget=budget,
                topological_step_ids=topology,
            )
        except PlanValidationError as error:
            self._finish_trace_error(event, error)
            raise
        self._finish_trace_success(event, plan)
        return plan

    def restore(
        self,
        serialized_plan: str | bytes | bytearray,
        *,
        trace_context: AgentTraceContext | None = None,
    ) -> AgentPlan:
        """Recover a plan while rechecking its policy, budget, DAG, and contracts."""

        event = self._start_trace(trace_context)
        try:
            try:
                raw = json.loads(serialized_plan)
                plan = AgentPlan.model_validate(raw)
            except (
                json.JSONDecodeError,
                UnicodeDecodeError,
                ValidationError,
                TypeError,
            ):
                raise PlanValidationError(
                    "invalid_serialized_plan",
                    "serialized plan does not match the closed schema",
                ) from None
            if plan.policy_version != self.policy.policy_version:
                raise PlanValidationError(
                    "policy_version_mismatch",
                    "serialized plan belongs to another policy version",
                )
            self._ensure_budget_within_limits(plan.budget)
            topology = self._validate_steps(plan.steps, plan.budget)
            if topology != plan.topological_step_ids:
                raise PlanValidationError(
                    "topology_mismatch",
                    "serialized topological order is not canonical",
                )
        except PlanValidationError as error:
            self._finish_trace_error(event, error)
            raise
        self._finish_trace_success(event, plan)
        return plan

    @staticmethod
    def _parse_draft(
        draft: AgentPlanDraft | Mapping[str, Any],
    ) -> AgentPlanDraft:
        try:
            return AgentPlanDraft.model_validate(draft)
        except ValidationError:
            raise PlanValidationError(
                "invalid_schema",
                "plan draft does not match the closed schema",
            ) from None

    def _resolve_budget(
        self,
        requested_budget: ExecutionBudgetRequest | Mapping[str, Any] | None,
    ) -> ExecutionBudget:
        try:
            request = ExecutionBudgetRequest.model_validate(requested_budget or {})
        except ValidationError:
            raise PlanValidationError(
                "invalid_budget_request",
                "budget request does not match the closed schema",
            ) from None
        requested = request.model_dump(exclude_none=True)
        for field_name, requested_value in requested.items():
            if requested_value > getattr(self.policy.limits, field_name):
                raise PlanValidationError(
                    "budget_exceeds_limit",
                    f"requested {field_name} exceeds the server hard limit",
                )
        effective = self.policy.defaults.model_dump()
        effective.update(requested)
        try:
            budget = ExecutionBudget.model_validate(effective)
        except ValidationError:
            raise PlanValidationError(
                "invalid_budget_request",
                "effective budget violates server invariants",
            ) from None
        self._ensure_budget_within_limits(budget)
        return budget

    def _ensure_budget_within_limits(self, budget: ExecutionBudget) -> None:
        for field_name in ExecutionBudget.model_fields:
            if getattr(budget, field_name) > getattr(self.policy.limits, field_name):
                raise PlanValidationError(
                    "budget_exceeds_limit",
                    f"effective {field_name} exceeds the server hard limit",
                )

    def _validate_steps(
        self,
        steps: Sequence[PlanStep],
        budget: ExecutionBudget,
    ) -> tuple[str, ...]:
        if len(steps) > budget.max_steps:
            raise PlanValidationError(
                "budget_exceeds_limit",
                "plan contains more steps than the effective budget",
            )
        step_ids = [step.step_id for step in steps]
        duplicates = sorted(
            step_id for step_id, count in Counter(step_ids).items() if count > 1
        )
        if duplicates:
            raise PlanValidationError(
                "duplicate_step_id",
                "plan step IDs must be unique",
            )
        by_id = {step.step_id: step for step in steps}
        for step in steps:
            for dependency in step.dependencies:
                if dependency.step_id not in by_id:
                    raise PlanValidationError(
                        "missing_dependency",
                        f"step {step.step_id} references a missing dependency",
                    )
        topology = _topological_order(steps)
        if len(topology) != len(steps):
            raise PlanValidationError(
                "dependency_cycle",
                "plan dependencies must form a DAG",
            )
        model_calls = sum(step.step_type in _MODEL_STEP_TYPES for step in steps)
        if model_calls > budget.max_model_calls:
            raise PlanValidationError(
                "step_budget_exceeded",
                "plan contains more model-call steps than the effective budget",
            )
        for step in steps:
            if step.timeout_ms > budget.step_timeout_ms:
                raise PlanValidationError(
                    "step_budget_exceeded",
                    f"step {step.step_id} exceeds the effective timeout",
                )
            self._validate_data_flow(step, by_id)
            self._validate_step_contract(step)
        return topology

    @staticmethod
    def _validate_data_flow(
        step: PlanStep,
        by_id: Mapping[str, PlanStep],
    ) -> None:
        dependency_ids = [dependency.step_id for dependency in step.dependencies]
        if len(set(dependency_ids)) != len(dependency_ids):
            raise PlanValidationError(
                "duplicate_dependency",
                f"step {step.step_id} repeats a dependency",
            )
        input_names = [input_ref.name for input_ref in step.inputs]
        if len(set(input_names)) != len(input_names):
            raise PlanValidationError(
                "duplicate_input_name",
                f"step {step.step_id} repeats an input name",
            )
        dependency_outputs = {
            dependency.step_id: dependency.output_type
            for dependency in step.dependencies
        }
        step_input_ids: set[str] = set()
        for input_ref in step.inputs:
            if input_ref.source is InputSource.STEP:
                source_id = input_ref.step_id
                if source_id not in by_id:
                    raise PlanValidationError(
                        "missing_dependency",
                        f"step {step.step_id} references a missing step input",
                    )
                if source_id not in dependency_outputs:
                    raise PlanValidationError(
                        "undeclared_input_dependency",
                        f"step {step.step_id} input is not a declared dependency",
                    )
                source_output = by_id[source_id].output_type
                if (
                    dependency_outputs[source_id] is not source_output
                    or input_ref.value_type is not source_output
                ):
                    raise PlanValidationError(
                        "output_type_mismatch",
                        f"step {step.step_id} expects the wrong predecessor output",
                    )
                step_input_ids.add(source_id)
                continue
            allowed_root_types = _ROOT_INPUT_TYPES[input_ref.source]
            if input_ref.value_type not in allowed_root_types:
                raise PlanValidationError(
                    "input_source_mismatch",
                    f"step {step.step_id} uses an invalid server input type",
                )
        if step_input_ids != set(dependency_ids):
            raise PlanValidationError(
                "unused_dependency",
                f"step {step.step_id} dependencies must be consumed as inputs",
            )

    @staticmethod
    def _validate_step_contract(step: PlanStep) -> None:
        contract = _CONTRACTS[step.step_type]
        input_types = frozenset(input_ref.value_type for input_ref in step.inputs)
        tool_set = frozenset(step.allowed_tools)
        if len(tool_set) != len(step.allowed_tools):
            raise PlanValidationError(
                "step_contract_violation",
                f"step {step.step_id} repeats an allowed tool",
            )
        if (
            step.output_type is not contract.output_type
            or step.risk_level is not contract.risk_level
            or tool_set != contract.allowed_tools
            or not contract.required_input_types.issubset(input_types)
            or (
                contract.required_any_input_types
                and not contract.required_any_input_types.intersection(input_types)
            )
            or not input_types.issubset(contract.allowed_input_types)
        ):
            raise PlanValidationError(
                "step_contract_violation",
                f"step {step.step_id} violates its server capability contract",
            )
        if (
            step.step_type is not StepType.ACTION_PREVIEW
            and PlanTool.ACTION_PREVIEW in tool_set
        ):
            raise PlanValidationError(
                "step_contract_violation",
                "write-preview capability is restricted to action_preview steps",
            )

    def _start_trace(
        self,
        trace_context: AgentTraceContext | None,
    ) -> AgentTraceEvent | None:
        if trace_context is None:
            return None
        return trace_context.begin_event(
            AgentTraceEventType.PLAN,
            stage="plan_validation",
            summary={"policy_version": self.policy.policy_version},
        )

    @staticmethod
    def _finish_trace_success(
        event: AgentTraceEvent | None,
        plan: AgentPlan,
    ) -> None:
        if event is None:
            return
        event.finish(
            AgentTraceStatus.SUCCESS,
            summary={
                "plan_id": plan.plan_id,
                "policy_version": plan.policy_version,
                "step_count": len(plan.steps),
                "step_types": [step.step_type.value for step in plan.steps],
                "allowed_tools": [
                    tool.value for step in plan.steps for tool in step.allowed_tools
                ],
                "topological_step_ids": list(plan.topological_step_ids),
                "stop_reasons": [reason.value for reason in plan.stop_reasons],
                "budget": plan.budget.model_dump(mode="json"),
            },
        )

    @staticmethod
    def _finish_trace_error(
        event: AgentTraceEvent | None,
        error: PlanValidationError,
    ) -> None:
        if event is None:
            return
        event.finish(
            AgentTraceStatus.ERROR,
            summary={"validation_code": error.code},
            error=error.code,
        )


def _topological_order(steps: Sequence[PlanStep]) -> tuple[str, ...]:
    """Return a canonical lexicographic topological order."""

    outgoing: dict[str, list[str]] = {step.step_id: [] for step in steps}
    indegree: dict[str, int] = {step.step_id: 0 for step in steps}
    for step in steps:
        unique_dependencies = {dependency.step_id for dependency in step.dependencies}
        indegree[step.step_id] = len(unique_dependencies)
        for dependency_id in unique_dependencies:
            outgoing[dependency_id].append(step.step_id)
    ready = [step_id for step_id, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    ordered: list[str] = []
    while ready:
        step_id = heapq.heappop(ready)
        ordered.append(step_id)
        for dependent_id in sorted(outgoing[step_id]):
            indegree[dependent_id] -= 1
            if indegree[dependent_id] == 0:
                heapq.heappush(ready, dependent_id)
    return tuple(ordered)


def load_plan_policy(path: str | Path | None = None) -> PlanPolicy:
    """Load and strictly validate the versioned server planning policy."""

    policy_path = Path(path) if path is not None else DEFAULT_PLAN_POLICY_PATH
    try:
        payload = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        return PlanPolicy.model_validate(payload)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise RuntimeError(f"invalid Agent plan policy: {policy_path}") from error


__all__ = [
    "AgentPlan",
    "AgentPlanDraft",
    "AgentPlanValidator",
    "ExecutionBudget",
    "ExecutionBudgetRequest",
    "InputSource",
    "PlanInputReference",
    "PlanPolicy",
    "PlanStep",
    "PlanTool",
    "PlanValidationError",
    "PlanValueType",
    "RiskLevel",
    "StepDependency",
    "StepType",
    "StopReason",
    "load_plan_policy",
]
