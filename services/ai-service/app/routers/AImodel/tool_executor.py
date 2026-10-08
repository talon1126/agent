"""Bounded asynchronous execution for validated shopping Agent plans.

The executor schedules a finite D1 DAG, re-authorizes D3 tool calls immediately
before I/O, and returns typed terminal results. Tool business logic remains in
registered async handlers.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.routers.AImodel.agent_trace import (
    AgentTraceContext,
    AgentTraceEventType,
    AgentTraceStatus,
)
from app.routers.AImodel.plan_models import (
    AgentPlan,
    InputSource,
    PlanStep,
    PlanValueType,
    StopReason,
)
from app.routers.AImodel.tool_policy import (
    AgentToolCall,
    AgentToolName,
    StepPolicyDenied,
    StepPolicyGate,
    ToolAccessMode,
    ToolAuthorizationContext,
)

EXECUTOR_SCHEMA_VERSION = "1.0"
DEFAULT_EXECUTOR_POLICY_PATH = Path(__file__).with_name("tool_executor_policy.yaml")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)


class StepExecutionStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class PlanExecutionStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ExecutionValue(_StrictModel):
    """One runtime value with the D1 type preserved beside its payload."""

    schema_version: Literal["1.0"] = EXECUTOR_SCHEMA_VERSION
    value_type: PlanValueType
    value: Any


class DependencyFailure(_StrictModel):
    """Safe terminal dependency summary supplied to degraded handlers."""

    step_id: str = Field(min_length=1, max_length=64)
    status: StepExecutionStatus
    error_code: str = Field(min_length=1, max_length=64)


class StepInvocation(_StrictModel):
    """Typed inputs visible to one trusted server-side step handler."""

    schema_version: Literal["1.0"] = EXECUTOR_SCHEMA_VERSION
    plan_id: str = Field(min_length=1, max_length=128)
    step: PlanStep
    inputs: dict[str, ExecutionValue] = Field(default_factory=dict)
    dependency_failures: tuple[DependencyFailure, ...] = ()


class StepExecutionResult(_StrictModel):
    """Terminal result for one step; no exception text crosses this boundary."""

    schema_version: Literal["1.0"] = EXECUTOR_SCHEMA_VERSION
    step_id: str = Field(min_length=1, max_length=64)
    step_type: str = Field(min_length=1, max_length=64)
    status: StepExecutionStatus
    output: ExecutionValue | None = None
    error_code: str | None = Field(default=None, min_length=1, max_length=64)
    attempt_count: int = Field(ge=0, le=100)
    started_at: datetime
    completed_at: datetime
    duration_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> StepExecutionResult:
        if self.status is StepExecutionStatus.SUCCESS:
            if self.output is None or self.error_code is not None:
                raise ValueError("successful steps require output and no error code")
        elif self.output is not None or self.error_code is None:
            raise ValueError("non-success steps require an error code and no output")
        return self


class PlanExecutionResult(_StrictModel):
    """Plan-level result whose step order always matches ``AgentPlan.steps``."""

    schema_version: Literal["1.0"] = EXECUTOR_SCHEMA_VERSION
    plan_id: str = Field(min_length=1, max_length=128)
    status: PlanExecutionStatus
    stop_reason: StopReason
    steps: tuple[StepExecutionResult, ...]
    started_at: datetime
    completed_at: datetime
    duration_ms: float = Field(ge=0)


class ExecutorPolicy(_StrictModel):
    """Versioned runtime caps intersected with the validated D1 plan budget."""

    schema_version: Literal["1.0"]
    policy_version: str = Field(min_length=1, max_length=64)
    max_concurrency: int = Field(ge=1, le=32, strict=True)
    step_timeout_ms: int = Field(ge=10, le=60_000, strict=True)
    total_timeout_ms: int = Field(ge=10, le=600_000, strict=True)
    max_retries: int = Field(ge=0, le=3, strict=True)
    retry_backoff_ms: int = Field(ge=0, le=5_000, strict=True)


class ExecutionCancellation:
    """Cooperative turn cancellation signal owned by the API/SSE caller."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        while not self._event.is_set():
            await asyncio.sleep(0.05)


class StepExecutionContext(_StrictModel):
    """Trusted root values and request scope shared by one plan execution."""

    user_id: int = Field(gt=0, strict=True)
    conversation_id: int = Field(gt=0, strict=True)
    request_inputs: dict[str, ExecutionValue] = Field(default_factory=dict)
    goal_inputs: dict[str, ExecutionValue] = Field(default_factory=dict)
    context_inputs: dict[str, ExecutionValue] = Field(default_factory=dict)
    candidate_item_ids: tuple[str, ...] = Field(default=(), max_length=100)
    cancellation: ExecutionCancellation = Field(default_factory=ExecutionCancellation)
    trace_context: AgentTraceContext | None = Field(default=None, exclude=True)

    def authorization_context(self) -> ToolAuthorizationContext:
        return ToolAuthorizationContext(
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            candidate_item_ids=self.candidate_item_ids,
            trace_context=self.trace_context,
        )


class StepExecutionError(RuntimeError):
    """Stable handler failure without leaking the underlying exception text."""

    retryable = False

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RetryableStepError(StepExecutionError):
    """Transient error eligible for bounded retries on idempotent read tools."""

    retryable = True


AsyncStepCallable = Callable[[StepInvocation], Awaitable[ExecutionValue]]
ToolCallFactory = Callable[[StepInvocation], AgentToolCall]


@dataclass(frozen=True, slots=True)
class StepHandler:
    """Trusted executable binding for a plan step or step type."""

    invoke: AsyncStepCallable
    tool_call_factory: ToolCallFactory | None = None
    idempotent: bool = False
    allow_degraded_dependencies: bool = False


@dataclass(frozen=True, slots=True)
class _PreparedStep:
    step: PlanStep
    handler: StepHandler
    invocation: StepInvocation
    tool_call: AgentToolCall | None
    access_mode: ToolAccessMode | None
    authorization_context: ToolAuthorizationContext
    scheduled_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    scheduled_clock: float = field(default_factory=time.perf_counter)

    @property
    def is_exclusive_write(self) -> bool:
        return self.access_mode is ToolAccessMode.WRITE


@dataclass(slots=True)
class _ExecutionState:
    rag_task: asyncio.Task[ExecutionValue] | None = None


def load_executor_policy(
    path: str | Path = DEFAULT_EXECUTOR_POLICY_PATH,
) -> ExecutorPolicy:
    """Load the server-owned runtime policy from YAML."""

    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return ExecutorPolicy.model_validate(payload)


class BoundedParallelToolExecutor:
    """Execute one finite plan with bounded concurrency and fail-closed calls."""

    def __init__(
        self,
        policy: ExecutorPolicy | None = None,
        *,
        policy_gate: StepPolicyGate | None = None,
    ) -> None:
        self.policy = policy or load_executor_policy()
        self._policy_gate = policy_gate or StepPolicyGate()

    async def execute(
        self,
        plan: AgentPlan,
        context: StepExecutionContext,
        handlers: Mapping[str, StepHandler],
    ) -> PlanExecutionResult:
        """Run ready steps, stop on cancellation/deadline, and order results."""

        started_at = datetime.now(UTC)
        started_clock = time.perf_counter()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._total_timeout_ms(plan) / 1_000
        concurrency = min(self.policy.max_concurrency, plan.budget.max_concurrency)
        results: dict[str, StepExecutionResult] = {}
        pending = {step.step_id for step in plan.steps}
        running: dict[asyncio.Task[StepExecutionResult], _PreparedStep] = {}
        state = _ExecutionState()
        forced_status: PlanExecutionStatus | None = None

        try:
            while pending or running:
                self._collect_completed(running, results)
                if context.cancellation.is_cancelled:
                    forced_status = PlanExecutionStatus.CANCELLED
                    await self._stop_running(
                        running,
                        results,
                        status=StepExecutionStatus.CANCELLED,
                        error_code="client_cancelled",
                    )
                    self._finish_pending(
                        plan,
                        pending,
                        results,
                        error_code="client_cancelled",
                    )
                    break
                if loop.time() >= deadline:
                    forced_status = PlanExecutionStatus.TIMED_OUT
                    await self._stop_running(
                        running,
                        results,
                        status=StepExecutionStatus.TIMED_OUT,
                        error_code="total_deadline_exceeded",
                    )
                    self._finish_pending(
                        plan,
                        pending,
                        results,
                        error_code="total_deadline_exceeded",
                    )
                    break

                made_progress = self._schedule_ready(
                    plan=plan,
                    context=context,
                    handlers=handlers,
                    results=results,
                    pending=pending,
                    running=running,
                    state=state,
                    concurrency=concurrency,
                )
                if not pending and not running:
                    break
                if running:
                    await self._wait_for_progress(
                        tuple(running),
                        context.cancellation,
                        max(deadline - loop.time(), 0),
                    )
                    continue
                if pending and not made_progress:
                    self._finish_pending(
                        plan,
                        pending,
                        results,
                        error_code="dependency_unresolved",
                    )
                    break
        except asyncio.CancelledError:
            await self._stop_running(
                running,
                results,
                status=StepExecutionStatus.CANCELLED,
                error_code="execution_cancelled",
            )
            raise
        finally:
            await self._cancel_shared_rag(state)

        ordered = tuple(results[step.step_id] for step in plan.steps)
        status, stop_reason = _plan_outcome(ordered, forced_status)
        completed_at = datetime.now(UTC)
        result = PlanExecutionResult(
            plan_id=plan.plan_id,
            status=status,
            stop_reason=stop_reason,
            steps=ordered,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=max((time.perf_counter() - started_clock) * 1_000, 0),
        )
        self._record_trace(plan, context, result)
        return result

    def _schedule_ready(
        self,
        *,
        plan: AgentPlan,
        context: StepExecutionContext,
        handlers: Mapping[str, StepHandler],
        results: dict[str, StepExecutionResult],
        pending: set[str],
        running: dict[asyncio.Task[StepExecutionResult], _PreparedStep],
        state: _ExecutionState,
        concurrency: int,
    ) -> bool:
        made_progress = False
        running_write = any(item.is_exclusive_write for item in running.values())
        if running_write:
            return False
        for step in plan.steps:
            if step.step_id not in pending:
                continue
            dependency_ids = tuple(item.step_id for item in step.dependencies)
            if not all(item in results for item in dependency_ids):
                continue
            handler = handlers.get(step.step_id) or handlers.get(step.step_type.value)
            if handler is None:
                results[step.step_id] = _instant_result(
                    step,
                    StepExecutionStatus.FAILED,
                    "handler_not_registered",
                )
                pending.remove(step.step_id)
                made_progress = True
                continue
            prepared_or_result = self._prepare_step(
                plan,
                step,
                handler,
                context,
                results,
            )
            if isinstance(prepared_or_result, StepExecutionResult):
                results[step.step_id] = prepared_or_result
                pending.remove(step.step_id)
                made_progress = True
                continue
            prepared = prepared_or_result
            if prepared.is_exclusive_write and running:
                break
            if len(running) >= concurrency:
                break
            task = asyncio.create_task(
                self._run_prepared(plan, context, prepared, state),
                name=f"agent-step:{step.step_id}",
            )
            running[task] = prepared
            pending.remove(step.step_id)
            made_progress = True
            if prepared.is_exclusive_write:
                break
        return made_progress

    def _prepare_step(
        self,
        plan: AgentPlan,
        step: PlanStep,
        handler: StepHandler,
        context: StepExecutionContext,
        results: Mapping[str, StepExecutionResult],
    ) -> _PreparedStep | StepExecutionResult:
        failures = tuple(
            DependencyFailure(
                step_id=dependency.step_id,
                status=results[dependency.step_id].status,
                error_code=results[dependency.step_id].error_code
                or "dependency_failed",
            )
            for dependency in step.dependencies
            if results[dependency.step_id].status is not StepExecutionStatus.SUCCESS
        )
        if failures and not handler.allow_degraded_dependencies:
            return _instant_result(
                step,
                StepExecutionStatus.SKIPPED,
                "dependency_not_successful",
            )

        inputs: dict[str, ExecutionValue] = {}
        for input_ref in step.inputs:
            value: ExecutionValue | None
            if input_ref.source is InputSource.STEP:
                dependency = results[input_ref.step_id or ""]
                if dependency.status is not StepExecutionStatus.SUCCESS:
                    continue
                value = dependency.output
            else:
                root = _root_inputs(context, input_ref.source)
                value = root.get(input_ref.name)
            if value is None:
                return _instant_result(
                    step,
                    StepExecutionStatus.FAILED,
                    "input_missing",
                )
            if value.value_type is not input_ref.value_type:
                return _instant_result(
                    step,
                    StepExecutionStatus.FAILED,
                    "input_type_mismatch",
                )
            inputs[input_ref.name] = value

        invocation = StepInvocation(
            plan_id=plan.plan_id,
            step=step,
            inputs=inputs,
            dependency_failures=failures,
        )
        tool_call: AgentToolCall | None = None
        access_mode: ToolAccessMode | None = None
        if handler.tool_call_factory is not None:
            try:
                tool_call = handler.tool_call_factory(invocation)
                if not isinstance(tool_call, AgentToolCall):
                    raise TypeError
            except Exception:
                return _instant_result(
                    step,
                    StepExecutionStatus.FAILED,
                    "invalid_tool_call",
                )
            try:
                access_mode = self._policy_gate.access_mode_for(tool_call.tool_name)
            except (KeyError, ValueError):
                access_mode = ToolAccessMode.WRITE
        return _PreparedStep(
            step=step,
            handler=handler,
            invocation=invocation,
            tool_call=tool_call,
            access_mode=access_mode,
            authorization_context=_authorization_context_for_inputs(context, inputs),
        )

    async def _run_prepared(
        self,
        plan: AgentPlan,
        context: StepExecutionContext,
        prepared: _PreparedStep,
        state: _ExecutionState,
    ) -> StepExecutionResult:
        started_at = datetime.now(UTC)
        started_clock = time.perf_counter()
        attempts = 0
        retry_limit = min(self.policy.max_retries, plan.budget.max_retries)
        can_retry = (
            prepared.tool_call is not None
            and prepared.access_mode is ToolAccessMode.READ
            and prepared.handler.idempotent
            and prepared.tool_call.tool_name != AgentToolName.RAG_LOOKUP.value
        )

        async def invoke_with_retries() -> ExecutionValue:
            nonlocal attempts
            while True:
                if prepared.tool_call is not None:
                    self._policy_gate.enforce(
                        plan,
                        prepared.step,
                        prepared.tool_call,
                        prepared.authorization_context,
                    )
                attempts += 1
                try:
                    output = await self._invoke_once(prepared, state)
                    if not isinstance(output, ExecutionValue):
                        raise StepExecutionError("invalid_handler_result")
                    if output.value_type is not prepared.step.output_type:
                        raise StepExecutionError("output_type_mismatch")
                    return output
                except RetryableStepError:
                    if not can_retry or attempts > retry_limit:
                        raise
                    delay = self.policy.retry_backoff_ms * (2 ** (attempts - 1))
                    if delay:
                        await asyncio.sleep(delay / 1_000)

        timeout_ms = min(
            prepared.step.timeout_ms,
            plan.budget.step_timeout_ms,
            self.policy.step_timeout_ms,
        )
        try:
            async with asyncio.timeout(timeout_ms / 1_000):
                output = await invoke_with_retries()
        except TimeoutError:
            return _result(
                prepared.step,
                StepExecutionStatus.TIMED_OUT,
                error_code="step_timeout",
                attempts=max(attempts, 1),
                started_at=started_at,
                started_clock=started_clock,
            )
        except asyncio.CancelledError:
            raise
        except StepPolicyDenied as error:
            return _result(
                prepared.step,
                StepExecutionStatus.FAILED,
                error_code=error.decision.code.value,
                attempts=attempts,
                started_at=started_at,
                started_clock=started_clock,
            )
        except StepExecutionError as error:
            return _result(
                prepared.step,
                StepExecutionStatus.FAILED,
                error_code=error.code,
                attempts=attempts,
                started_at=started_at,
                started_clock=started_clock,
            )
        except Exception:
            return _result(
                prepared.step,
                StepExecutionStatus.FAILED,
                error_code="step_execution_failed",
                attempts=attempts,
                started_at=started_at,
                started_clock=started_clock,
            )
        return _result(
            prepared.step,
            StepExecutionStatus.SUCCESS,
            output=output,
            attempts=attempts,
            started_at=started_at,
            started_clock=started_clock,
        )

    async def _invoke_once(
        self,
        prepared: _PreparedStep,
        state: _ExecutionState,
    ) -> ExecutionValue:
        if (
            prepared.tool_call is not None
            and prepared.tool_call.tool_name == AgentToolName.RAG_LOOKUP.value
        ):
            if state.rag_task is None:
                state.rag_task = asyncio.create_task(
                    _invoke_handler(prepared.handler, prepared.invocation),
                    name="agent-rag-single-flight",
                )
            return await asyncio.shield(state.rag_task)
        return await _invoke_handler(prepared.handler, prepared.invocation)

    @staticmethod
    def _collect_completed(
        running: dict[asyncio.Task[StepExecutionResult], _PreparedStep],
        results: dict[str, StepExecutionResult],
    ) -> None:
        for task in tuple(running):
            if not task.done():
                continue
            prepared = running.pop(task)
            if task.cancelled():
                results[prepared.step.step_id] = _external_stop_result(
                    prepared,
                    StepExecutionStatus.CANCELLED,
                    "execution_cancelled",
                )
                continue
            try:
                results[prepared.step.step_id] = task.result()
            except Exception:
                results[prepared.step.step_id] = _external_stop_result(
                    prepared,
                    StepExecutionStatus.FAILED,
                    "executor_internal_error",
                )

    @staticmethod
    async def _stop_running(
        running: dict[asyncio.Task[StepExecutionResult], _PreparedStep],
        results: dict[str, StepExecutionResult],
        *,
        status: StepExecutionStatus,
        error_code: str,
    ) -> None:
        tasks = tuple(running)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for task in tasks:
            prepared = running.pop(task)
            if not task.cancelled() and task.exception() is None:
                results[prepared.step.step_id] = task.result()
            else:
                results[prepared.step.step_id] = _external_stop_result(
                    prepared,
                    status,
                    error_code,
                )

    @staticmethod
    def _finish_pending(
        plan: AgentPlan,
        pending: set[str],
        results: dict[str, StepExecutionResult],
        *,
        error_code: str,
    ) -> None:
        for step in plan.steps:
            if step.step_id in pending:
                results[step.step_id] = _instant_result(
                    step,
                    StepExecutionStatus.SKIPPED,
                    error_code,
                )
                pending.remove(step.step_id)

    @staticmethod
    async def _wait_for_progress(
        running: tuple[asyncio.Task[StepExecutionResult], ...],
        cancellation: ExecutionCancellation,
        timeout_seconds: float,
    ) -> None:
        watcher = asyncio.create_task(
            cancellation.wait(),
            name="agent-cancellation-watch",
        )
        try:
            await asyncio.wait(
                (*running, watcher),
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not watcher.done():
                watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    @staticmethod
    async def _cancel_shared_rag(state: _ExecutionState) -> None:
        if state.rag_task is None:
            return
        if not state.rag_task.done():
            state.rag_task.cancel()
        await asyncio.gather(state.rag_task, return_exceptions=True)

    def _total_timeout_ms(self, plan: AgentPlan) -> int:
        return min(self.policy.total_timeout_ms, plan.budget.total_timeout_ms)

    @staticmethod
    def _record_trace(
        plan: AgentPlan,
        context: StepExecutionContext,
        result: PlanExecutionResult,
    ) -> None:
        trace = context.trace_context
        if trace is None:
            return
        for step in result.steps:
            event = trace.begin_event(
                AgentTraceEventType.STEP,
                stage="tool_executor",
                summary={
                    "status": step.status.value,
                    "attempt_count": step.attempt_count,
                    "error_code": step.error_code,
                    "duration_ms": round(step.duration_ms, 3),
                },
                related_ids={"plan_id": plan.plan_id, "step_id": step.step_id},
            )
            event.started_at = step.started_at
            trace_status = AgentTraceStatus.SUCCESS
            if step.status is StepExecutionStatus.SKIPPED:
                trace_status = AgentTraceStatus.SKIPPED
            elif step.status is not StepExecutionStatus.SUCCESS:
                trace_status = AgentTraceStatus.ERROR
            event.finish(
                trace_status,
                error=step.error_code
                if trace_status is AgentTraceStatus.ERROR
                else None,
                duration_ms=step.duration_ms,
            )


async def _invoke_handler(
    handler: StepHandler,
    invocation: StepInvocation,
) -> ExecutionValue:
    result = handler.invoke(invocation)
    if not inspect.isawaitable(result):
        raise StepExecutionError("handler_must_be_async")
    return await result


def _root_inputs(
    context: StepExecutionContext,
    source: InputSource,
) -> Mapping[str, ExecutionValue]:
    if source is InputSource.REQUEST:
        return context.request_inputs
    if source is InputSource.GOAL:
        return context.goal_inputs
    if source is InputSource.CONTEXT:
        return context.context_inputs
    return {}


def _authorization_context_for_inputs(
    context: StepExecutionContext,
    inputs: Mapping[str, ExecutionValue],
) -> ToolAuthorizationContext:
    """Add candidate provenance created by an upstream search step."""

    candidate_ids = list(context.candidate_item_ids)
    seen = set(candidate_ids)
    for value in inputs.values():
        if value.value_type is not PlanValueType.CANDIDATE_REFS:
            continue
        candidates = value.value
        if not isinstance(candidates, (list, tuple)):
            continue
        for candidate in candidates:
            item_id = getattr(candidate, "item_id", None)
            normalized = str(item_id).strip() if item_id is not None else ""
            if normalized and normalized not in seen:
                candidate_ids.append(normalized)
                seen.add(normalized)
    return ToolAuthorizationContext(
        user_id=context.user_id,
        conversation_id=context.conversation_id,
        candidate_item_ids=tuple(candidate_ids[:100]),
        trace_context=context.trace_context,
    )


def _instant_result(
    step: PlanStep,
    status: StepExecutionStatus,
    error_code: str,
) -> StepExecutionResult:
    now = datetime.now(UTC)
    return StepExecutionResult(
        step_id=step.step_id,
        step_type=step.step_type.value,
        status=status,
        error_code=error_code,
        attempt_count=0,
        started_at=now,
        completed_at=now,
        duration_ms=0,
    )


def _result(
    step: PlanStep,
    status: StepExecutionStatus,
    *,
    attempts: int,
    started_at: datetime,
    started_clock: float,
    output: ExecutionValue | None = None,
    error_code: str | None = None,
) -> StepExecutionResult:
    return StepExecutionResult(
        step_id=step.step_id,
        step_type=step.step_type.value,
        status=status,
        output=output,
        error_code=error_code,
        attempt_count=attempts,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        duration_ms=max((time.perf_counter() - started_clock) * 1_000, 0),
    )


def _external_stop_result(
    prepared: _PreparedStep,
    status: StepExecutionStatus,
    error_code: str,
) -> StepExecutionResult:
    return _result(
        prepared.step,
        status,
        error_code=error_code,
        attempts=1,
        started_at=prepared.scheduled_at,
        started_clock=prepared.scheduled_clock,
    )


def _plan_outcome(
    steps: tuple[StepExecutionResult, ...],
    forced_status: PlanExecutionStatus | None,
) -> tuple[PlanExecutionStatus, StopReason]:
    if forced_status is PlanExecutionStatus.CANCELLED:
        return forced_status, StopReason.CANCELLED
    if forced_status is PlanExecutionStatus.TIMED_OUT:
        return forced_status, StopReason.TIMEOUT
    statuses = {step.status for step in steps}
    if statuses == {StepExecutionStatus.SUCCESS}:
        return PlanExecutionStatus.SUCCESS, StopReason.COMPLETED
    if StepExecutionStatus.SUCCESS in statuses:
        return PlanExecutionStatus.PARTIAL_FAILED, StopReason.SAFE_FALLBACK
    if StepExecutionStatus.TIMED_OUT in statuses:
        return PlanExecutionStatus.TIMED_OUT, StopReason.TIMEOUT
    if StepExecutionStatus.CANCELLED in statuses:
        return PlanExecutionStatus.CANCELLED, StopReason.CANCELLED
    return PlanExecutionStatus.FAILED, StopReason.SAFE_FALLBACK
