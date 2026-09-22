from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.routers.AImodel.agent_trace import AgentTraceContext
from app.routers.AImodel.plan_models import (
    AgentPlan,
    AgentPlanValidator,
    PlanValueType,
    load_plan_policy,
)
from app.routers.AImodel.tool_executor import (
    BoundedParallelToolExecutor,
    ExecutionValue,
    ExecutorPolicy,
    RetryableStepError,
    StepExecutionContext,
    StepExecutionStatus,
    StepHandler,
    StepInvocation,
    load_executor_policy,
)
from app.routers.AImodel.tool_policy import (
    AgentToolCall,
    ToolAccessMode,
)


def _root(name: str, source: str, value_type: str) -> dict[str, str]:
    return {"name": name, "source": source, "value_type": value_type}


def _snapshot_plan(*step_ids: str) -> AgentPlan:
    return AgentPlanValidator(load_plan_policy()).validate(
        {
            "schema_version": "1.0",
            "plan_id": "d4-unit-plan",
            "steps": [
                {
                    "step_id": step_id,
                    "step_type": "snapshot",
                    "dependencies": [],
                    "inputs": [_root("page", "context", "page_context")],
                    "output_type": "product_snapshot",
                    "risk_level": "medium",
                    "allowed_tools": ["product_snapshot"],
                    "timeout_ms": 1_000,
                }
                for step_id in step_ids
            ],
            "stop_reasons": ["completed", "tool_denied", "safe_fallback"],
        },
        requested_budget={
            "max_concurrency": 4,
            "step_timeout_ms": 1_000,
            "total_timeout_ms": 5_000,
            "max_retries": 2,
        },
    )


def _context(*, trace: AgentTraceContext | None = None) -> StepExecutionContext:
    return StepExecutionContext(
        user_id=11,
        conversation_id=22,
        context_inputs={
            "page": ExecutionValue(
                value_type=PlanValueType.PAGE_CONTEXT,
                value={"surface": "product"},
            )
        },
        candidate_item_ids=("sku-1", "sku-2"),
        trace_context=trace,
    )


def _snapshot_call(
    item_id: str,
) -> Any:
    def factory(_invocation: StepInvocation) -> AgentToolCall:
        return AgentToolCall(
            tool_name="product_snapshot",
            arguments={
                "user_id": 11,
                "conversation_id": 22,
                "item_ids": [item_id],
            },
        )

    return factory


def _policy(**overrides: int) -> ExecutorPolicy:
    values: dict[str, Any] = {
        "schema_version": "1.0",
        "policy_version": "d4-unit-policy",
        "max_concurrency": 4,
        "step_timeout_ms": 1_000,
        "total_timeout_ms": 5_000,
        "max_retries": 2,
        "retry_backoff_ms": 0,
    }
    values.update(overrides)
    return ExecutorPolicy.model_validate(values)


async def _snapshot(_invocation: StepInvocation) -> ExecutionValue:
    return ExecutionValue(
        value_type=PlanValueType.PRODUCT_SNAPSHOT,
        value={"item_id": "sku-1"},
    )


def test_versioned_server_policy_loads_bounded_defaults() -> None:
    policy = load_executor_policy()

    assert policy.policy_version == "d4-executor-policy-v1"
    assert policy.max_concurrency == 4
    assert policy.step_timeout_ms == 10_000
    assert policy.total_timeout_ms == 60_000
    assert policy.max_retries == 1
    assert policy.retry_backoff_ms == 50


def test_denied_tool_call_never_reaches_handler() -> None:
    async def scenario() -> None:
        calls = 0

        async def handler(invocation: StepInvocation) -> ExecutionValue:
            nonlocal calls
            calls += 1
            return await _snapshot(invocation)

        def outside_candidate(_invocation: StepInvocation) -> AgentToolCall:
            return AgentToolCall(
                tool_name="product_snapshot",
                arguments={
                    "user_id": 11,
                    "conversation_id": 22,
                    "item_ids": ["sku-outside-scope"],
                },
            )

        result = await BoundedParallelToolExecutor(_policy()).execute(
            _snapshot_plan("s01_denied"),
            _context(),
            {
                "s01_denied": StepHandler(
                    invoke=handler,
                    tool_call_factory=outside_candidate,
                    idempotent=True,
                )
            },
        )

        assert calls == 0
        assert result.steps[0].status is StepExecutionStatus.FAILED
        assert result.steps[0].error_code == "item_not_in_candidate_set"
        assert result.steps[0].attempt_count == 0

    asyncio.run(scenario())


def test_non_idempotent_read_is_not_retried() -> None:
    async def scenario() -> None:
        calls = 0

        async def transient(_invocation: StepInvocation) -> ExecutionValue:
            nonlocal calls
            calls += 1
            raise RetryableStepError("upstream_unavailable")

        result = await BoundedParallelToolExecutor(_policy(max_retries=2)).execute(
            _snapshot_plan("s01_non_idempotent"),
            _context(),
            {
                "s01_non_idempotent": StepHandler(
                    invoke=transient,
                    tool_call_factory=_snapshot_call("sku-1"),
                    idempotent=False,
                )
            },
        )

        assert calls == 1
        assert result.steps[0].error_code == "upstream_unavailable"
        assert result.steps[0].attempt_count == 1

    asyncio.run(scenario())


def test_each_retry_is_reauthorized_immediately_before_tool_io() -> None:
    async def scenario() -> None:
        trace = AgentTraceContext.start(user_query="compare products")
        calls = 0

        async def transient(invocation: StepInvocation) -> ExecutionValue:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RetryableStepError("upstream_unavailable")
            return await _snapshot(invocation)

        result = await BoundedParallelToolExecutor(_policy(max_retries=1)).execute(
            _snapshot_plan("s01_retry"),
            _context(trace=trace),
            {
                "s01_retry": StepHandler(
                    invoke=transient,
                    tool_call_factory=_snapshot_call("sku-1"),
                    idempotent=True,
                )
            },
        )

        authorization_events = [
            event
            for event in trace.events
            if event.stage == "step_policy" and event.tool_name == "product_snapshot"
        ]
        assert calls == 2
        assert result.steps[0].status is StepExecutionStatus.SUCCESS
        assert len(authorization_events) == 2

    asyncio.run(scenario())


class _ExclusiveWriteGate:
    def access_mode_for(self, _tool_name: str) -> ToolAccessMode:
        return ToolAccessMode.WRITE

    def enforce(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def test_side_effect_steps_are_never_executed_concurrently() -> None:
    async def scenario() -> None:
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        active = 0
        maximum = 0
        started: list[str] = []

        async def write(invocation: StepInvocation) -> ExecutionValue:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            started.append(invocation.step.step_id)
            try:
                if len(started) == 1:
                    first_started.set()
                    await release_first.wait()
                return await _snapshot(invocation)
            finally:
                active -= 1

        plan = _snapshot_plan("s01_write", "s02_write")
        execution = asyncio.create_task(
            BoundedParallelToolExecutor(
                _policy(max_concurrency=4),
                policy_gate=_ExclusiveWriteGate(),  # type: ignore[arg-type]
            ).execute(
                plan,
                _context(),
                {
                    step.step_id: StepHandler(
                        invoke=write,
                        tool_call_factory=_snapshot_call(f"sku-{index}"),
                    )
                    for index, step in enumerate(plan.steps, 1)
                },
            )
        )
        await asyncio.wait_for(first_started.wait(), timeout=1)
        await asyncio.sleep(0)
        assert started == ["s01_write"]
        release_first.set()
        result = await asyncio.wait_for(execution, timeout=1)

        assert result.status == "success"
        assert started == ["s01_write", "s02_write"]
        assert maximum == 1

    asyncio.run(scenario())


def test_output_type_error_is_stable_and_not_retried() -> None:
    async def scenario() -> None:
        calls = 0

        async def wrong_type(_invocation: StepInvocation) -> ExecutionValue:
            nonlocal calls
            calls += 1
            return ExecutionValue(
                value_type=PlanValueType.RESPONSE_DRAFT,
                value={"answer": "not a snapshot"},
            )

        result = await BoundedParallelToolExecutor(_policy(max_retries=2)).execute(
            _snapshot_plan("s01_wrong_type"),
            _context(),
            {
                "s01_wrong_type": StepHandler(
                    invoke=wrong_type,
                    tool_call_factory=_snapshot_call("sku-1"),
                    idempotent=True,
                )
            },
        )

        assert calls == 1
        assert result.steps[0].status is StepExecutionStatus.FAILED
        assert result.steps[0].error_code == "output_type_mismatch"
        assert result.steps[0].attempt_count == 1

    asyncio.run(scenario())


def test_outer_coroutine_cancellation_cleans_up_running_steps() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        finalized = asyncio.Event()
        never = asyncio.Event()

        async def blocked(_invocation: StepInvocation) -> ExecutionValue:
            started.set()
            try:
                await never.wait()
            finally:
                finalized.set()
            raise AssertionError("unreachable")

        execution = asyncio.create_task(
            BoundedParallelToolExecutor(_policy()).execute(
                _snapshot_plan("s01_cancelled"),
                _context(),
                {
                    "s01_cancelled": StepHandler(
                        invoke=blocked,
                        tool_call_factory=_snapshot_call("sku-1"),
                        idempotent=True,
                    )
                },
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        execution.cancel()

        with pytest.raises(asyncio.CancelledError):
            await execution

        assert finalized.is_set()
        assert [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and not task.done()
            and task.get_name().startswith("agent-")
        ] == []

    asyncio.run(scenario())


def test_execution_trace_exposes_only_stable_terminal_metadata() -> None:
    async def scenario() -> None:
        trace = AgentTraceContext.start(user_query="compare products")

        async def failed(_invocation: StepInvocation) -> ExecutionValue:
            raise RuntimeError("private-upstream-secret")

        result = await BoundedParallelToolExecutor(_policy()).execute(
            _snapshot_plan("s01_failed"),
            _context(trace=trace),
            {
                "s01_failed": StepHandler(
                    invoke=failed,
                    tool_call_factory=_snapshot_call("sku-1"),
                    idempotent=True,
                )
            },
        )

        serialized = json.dumps(
            [event.to_record() for event in trace.events],
            default=str,
        )
        step_event = trace.events[-1]
        assert result.steps[0].error_code == "step_execution_failed"
        assert step_event.stage == "tool_executor"
        assert step_event.summary["error_code"] == "step_execution_failed"
        assert "private-upstream-secret" not in serialized

    asyncio.run(scenario())
