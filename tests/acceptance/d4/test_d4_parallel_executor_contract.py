"""Frozen acceptance contract for D4 bounded parallel execution."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "ai-service"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.routers.AImodel.plan_models import (  # noqa: E402
    AgentPlan,
    AgentPlanValidator,
    PlanValueType,
    load_plan_policy,
)
from app.routers.AImodel.tool_executor import (  # noqa: E402
    BoundedParallelToolExecutor,
    ExecutionCancellation,
    ExecutionValue,
    ExecutorPolicy,
    PlanExecutionStatus,
    RetryableStepError,
    StepExecutionContext,
    StepExecutionStatus,
    StepHandler,
    StepInvocation,
)
from app.routers.AImodel.tool_policy import AgentToolCall  # noqa: E402


def _root(name: str, source: str, value_type: str) -> dict[str, str]:
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


def _snapshot_step(step_id: str) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "step_type": "snapshot",
        "dependencies": [],
        "inputs": [_root("page", "context", "page_context")],
        "output_type": "product_snapshot",
        "risk_level": "medium",
        "allowed_tools": ["product_snapshot"],
        "timeout_ms": 1_000,
    }


def _rag_step(step_id: str) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "step_type": "rag_lookup",
        "dependencies": [],
        "inputs": [_root("question", "request", "user_query")],
        "output_type": "knowledge_result",
        "risk_level": "medium",
        "allowed_tools": ["rag_lookup"],
        "timeout_ms": 1_000,
    }


def _compose_step(step_id: str, dependencies: list[str]) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "step_type": "compose",
        "dependencies": [
            _dependency(dependency, "product_snapshot") for dependency in dependencies
        ],
        "inputs": [
            _step_input(f"product_{index}", dependency, "product_snapshot")
            for index, dependency in enumerate(dependencies, 1)
        ],
        "output_type": "response_draft",
        "risk_level": "low",
        "allowed_tools": [],
        "timeout_ms": 1_000,
    }


def _plan(*steps: dict[str, Any], plan_id: str = "d4-acceptance") -> AgentPlan:
    return AgentPlanValidator(load_plan_policy()).validate(
        {
            "schema_version": "1.0",
            "plan_id": plan_id,
            "steps": list(steps),
            "stop_reasons": [
                "completed",
                "tool_denied",
                "timeout",
                "cancelled",
                "safe_fallback",
            ],
        },
        requested_budget={
            "max_concurrency": 4,
            "step_timeout_ms": 1_000,
            "total_timeout_ms": 5_000,
            "max_retries": 1,
        },
    )


def _context(
    *, cancellation: ExecutionCancellation | None = None
) -> StepExecutionContext:
    return StepExecutionContext(
        user_id=7,
        conversation_id=70,
        request_inputs={
            "question": ExecutionValue(
                value_type=PlanValueType.USER_QUERY,
                value="how to choose",
            )
        },
        context_inputs={
            "page": ExecutionValue(
                value_type=PlanValueType.PAGE_CONTEXT,
                value={"surface": "product"},
            )
        },
        candidate_item_ids=("sku-1", "sku-2", "sku-3", "sku-4"),
        cancellation=cancellation or ExecutionCancellation(),
    )


def _snapshot_call(item_id: str) -> Callable[[StepInvocation], AgentToolCall]:
    def factory(_invocation: StepInvocation) -> AgentToolCall:
        return AgentToolCall(
            tool_name="product_snapshot",
            arguments={
                "user_id": 7,
                "conversation_id": 70,
                "item_ids": [item_id],
            },
        )

    return factory


def _rag_call(_invocation: StepInvocation) -> AgentToolCall:
    return AgentToolCall(
        tool_name="rag_lookup",
        arguments={
            "user_id": 7,
            "conversation_id": 70,
            "query": "how to choose",
            "collections": ["shopping_guides", "policies"],
        },
    )


def _policy(
    *,
    max_concurrency: int = 4,
    step_timeout_ms: int = 1_000,
    total_timeout_ms: int = 5_000,
    max_retries: int = 1,
    retry_backoff_ms: int = 1,
) -> ExecutorPolicy:
    return ExecutorPolicy(
        schema_version="1.0",
        policy_version="d4-acceptance",
        max_concurrency=max_concurrency,
        step_timeout_ms=step_timeout_ms,
        total_timeout_ms=total_timeout_ms,
        max_retries=max_retries,
        retry_backoff_ms=retry_backoff_ms,
    )


def test_independent_steps_run_concurrently_and_dependency_waits() -> None:
    async def scenario() -> None:
        plan = _plan(
            _snapshot_step("s01_left"),
            _snapshot_step("s02_right"),
            _compose_step("s03_compose", ["s01_left", "s02_right"]),
            plan_id="d4-concurrent-dependency",
        )
        both_started = asyncio.Event()
        release = asyncio.Event()
        started: list[str] = []
        finished: list[str] = []

        async def snapshot(invocation: StepInvocation) -> ExecutionValue:
            started.append(invocation.step.step_id)
            if len(started) == 2:
                both_started.set()
            await release.wait()
            finished.append(invocation.step.step_id)
            return ExecutionValue(
                value_type=PlanValueType.PRODUCT_SNAPSHOT,
                value={"step_id": invocation.step.step_id},
            )

        async def compose(invocation: StepInvocation) -> ExecutionValue:
            assert set(finished) == {"s01_left", "s02_right"}
            assert list(invocation.inputs) == ["product_1", "product_2"]
            return ExecutionValue(
                value_type=PlanValueType.RESPONSE_DRAFT,
                value={"answer": "done"},
            )

        handlers = {
            "s01_left": StepHandler(
                invoke=snapshot,
                tool_call_factory=_snapshot_call("sku-1"),
                idempotent=True,
            ),
            "s02_right": StepHandler(
                invoke=snapshot,
                tool_call_factory=_snapshot_call("sku-2"),
                idempotent=True,
            ),
            "s03_compose": StepHandler(invoke=compose),
        }
        execution = asyncio.create_task(
            BoundedParallelToolExecutor(_policy(max_concurrency=2)).execute(
                plan,
                _context(),
                handlers,
            )
        )
        await asyncio.wait_for(both_started.wait(), timeout=1)
        assert started == ["s01_left", "s02_right"]
        release.set()
        result = await asyncio.wait_for(execution, timeout=1)

        assert result.status is PlanExecutionStatus.SUCCESS
        assert [item.status for item in result.steps] == [
            StepExecutionStatus.SUCCESS,
            StepExecutionStatus.SUCCESS,
            StepExecutionStatus.SUCCESS,
        ]

    asyncio.run(scenario())


def test_max_concurrency_is_never_exceeded() -> None:
    async def scenario() -> None:
        steps = [_snapshot_step(f"s0{index}_snapshot") for index in range(1, 5)]
        plan = _plan(*steps, plan_id="d4-concurrency-cap")
        release = asyncio.Event()
        first_wave = asyncio.Event()
        active = 0
        maximum = 0
        started: list[str] = []

        async def snapshot(invocation: StepInvocation) -> ExecutionValue:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            started.append(invocation.step.step_id)
            if len(started) == 2:
                first_wave.set()
            try:
                await release.wait()
                return ExecutionValue(
                    value_type=PlanValueType.PRODUCT_SNAPSHOT,
                    value={"step_id": invocation.step.step_id},
                )
            finally:
                active -= 1

        handlers = {
            step["step_id"]: StepHandler(
                invoke=snapshot,
                tool_call_factory=_snapshot_call(f"sku-{index}"),
                idempotent=True,
            )
            for index, step in enumerate(steps, 1)
        }
        execution = asyncio.create_task(
            BoundedParallelToolExecutor(_policy(max_concurrency=2)).execute(
                plan,
                _context(),
                handlers,
            )
        )
        await asyncio.wait_for(first_wave.wait(), timeout=1)
        assert started == ["s01_snapshot", "s02_snapshot"]
        assert maximum == 2
        release.set()
        result = await asyncio.wait_for(execution, timeout=1)

        assert result.status is PlanExecutionStatus.SUCCESS
        assert maximum == 2
        assert len(started) == 4

    asyncio.run(scenario())


def test_parallel_results_are_merged_in_plan_order() -> None:
    async def scenario() -> None:
        plan = _plan(
            _snapshot_step("s01_slow"),
            _snapshot_step("s02_fast"),
            plan_id="d4-stable-order",
        )
        slow_release = asyncio.Event()
        fast_finished = asyncio.Event()

        async def slow(_invocation: StepInvocation) -> ExecutionValue:
            await slow_release.wait()
            return ExecutionValue(
                value_type=PlanValueType.PRODUCT_SNAPSHOT,
                value={"item_id": "sku-1"},
            )

        async def fast(_invocation: StepInvocation) -> ExecutionValue:
            fast_finished.set()
            return ExecutionValue(
                value_type=PlanValueType.PRODUCT_SNAPSHOT,
                value={"item_id": "sku-2"},
            )

        execution = asyncio.create_task(
            BoundedParallelToolExecutor(_policy(max_concurrency=2)).execute(
                plan,
                _context(),
                {
                    "s01_slow": StepHandler(
                        invoke=slow,
                        tool_call_factory=_snapshot_call("sku-1"),
                        idempotent=True,
                    ),
                    "s02_fast": StepHandler(
                        invoke=fast,
                        tool_call_factory=_snapshot_call("sku-2"),
                        idempotent=True,
                    ),
                },
            )
        )
        await asyncio.wait_for(fast_finished.wait(), timeout=1)
        slow_release.set()
        result = await asyncio.wait_for(execution, timeout=1)

        assert [item.step_id for item in result.steps] == ["s01_slow", "s02_fast"]
        assert [item.output.value["item_id"] for item in result.steps] == [
            "sku-1",
            "sku-2",
        ]

    asyncio.run(scenario())


def test_partial_failure_preserves_success_and_explicitly_degrades_downstream() -> None:
    async def scenario() -> None:
        plan = _plan(
            _snapshot_step("s01_good"),
            _snapshot_step("s02_failed"),
            _compose_step("s03_compose", ["s01_good", "s02_failed"]),
            plan_id="d4-partial-failure",
        )

        async def good(_invocation: StepInvocation) -> ExecutionValue:
            return ExecutionValue(
                value_type=PlanValueType.PRODUCT_SNAPSHOT,
                value={"item_id": "sku-1"},
            )

        async def failed(_invocation: StepInvocation) -> ExecutionValue:
            raise RuntimeError("private upstream detail")

        async def compose(invocation: StepInvocation) -> ExecutionValue:
            assert list(invocation.inputs) == ["product_1"]
            assert len(invocation.dependency_failures) == 1
            failure = invocation.dependency_failures[0]
            assert failure.step_id == "s02_failed"
            assert failure.status is StepExecutionStatus.FAILED
            assert failure.error_code == "step_execution_failed"
            return ExecutionValue(
                value_type=PlanValueType.RESPONSE_DRAFT,
                value={"answer": "partial result", "items": ["sku-1"]},
            )

        result = await BoundedParallelToolExecutor(_policy()).execute(
            plan,
            _context(),
            {
                "s01_good": StepHandler(
                    invoke=good,
                    tool_call_factory=_snapshot_call("sku-1"),
                    idempotent=True,
                ),
                "s02_failed": StepHandler(
                    invoke=failed,
                    tool_call_factory=_snapshot_call("sku-2"),
                    idempotent=True,
                ),
                "s03_compose": StepHandler(
                    invoke=compose,
                    allow_degraded_dependencies=True,
                ),
            },
        )

        assert result.status is PlanExecutionStatus.PARTIAL_FAILED
        assert [item.status for item in result.steps] == [
            StepExecutionStatus.SUCCESS,
            StepExecutionStatus.FAILED,
            StepExecutionStatus.SUCCESS,
        ]
        assert result.steps[0].output.value == {"item_id": "sku-1"}
        assert "private upstream detail" not in str(result.model_dump())

    asyncio.run(scenario())


def test_step_timeout_cancels_handler_without_residual_tasks() -> None:
    async def scenario() -> None:
        plan = _plan(_snapshot_step("s01_timeout"), plan_id="d4-step-timeout")
        never = asyncio.Event()
        finalized = asyncio.Event()

        async def blocked(_invocation: StepInvocation) -> ExecutionValue:
            try:
                await never.wait()
            finally:
                finalized.set()
            raise AssertionError("unreachable")

        result = await BoundedParallelToolExecutor(
            _policy(step_timeout_ms=50, total_timeout_ms=500)
        ).execute(
            plan,
            _context(),
            {
                "s01_timeout": StepHandler(
                    invoke=blocked,
                    tool_call_factory=_snapshot_call("sku-1"),
                    idempotent=True,
                )
            },
        )

        assert result.steps[0].status is StepExecutionStatus.TIMED_OUT
        assert result.steps[0].error_code == "step_timeout"
        assert finalized.is_set()
        assert [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ] == []

    asyncio.run(scenario())


def test_total_deadline_prevents_new_steps_from_starting() -> None:
    async def scenario() -> None:
        plan = _plan(
            _snapshot_step("s01_running"),
            _snapshot_step("s02_waiting"),
            _snapshot_step("s03_waiting"),
            plan_id="d4-total-timeout",
        )
        never = asyncio.Event()
        started: list[str] = []
        finalized = asyncio.Event()

        async def blocked(invocation: StepInvocation) -> ExecutionValue:
            started.append(invocation.step.step_id)
            try:
                await never.wait()
            finally:
                finalized.set()
            raise AssertionError("unreachable")

        handlers = {
            step_id: StepHandler(
                invoke=blocked,
                tool_call_factory=_snapshot_call(f"sku-{index}"),
                idempotent=True,
            )
            for index, step_id in enumerate(
                ("s01_running", "s02_waiting", "s03_waiting"), 1
            )
        }
        result = await BoundedParallelToolExecutor(
            _policy(
                max_concurrency=1,
                step_timeout_ms=1_000,
                total_timeout_ms=50,
            )
        ).execute(plan, _context(), handlers)

        assert result.status is PlanExecutionStatus.TIMED_OUT
        assert started == ["s01_running"]
        assert [item.status for item in result.steps] == [
            StepExecutionStatus.TIMED_OUT,
            StepExecutionStatus.SKIPPED,
            StepExecutionStatus.SKIPPED,
        ]
        assert finalized.is_set()

    asyncio.run(scenario())


def test_client_cancellation_cancels_running_work_and_leaves_no_tasks() -> None:
    async def scenario() -> None:
        cancellation = ExecutionCancellation()
        plan = _plan(_snapshot_step("s01_cancel"), plan_id="d4-cancel")
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
                plan,
                _context(cancellation=cancellation),
                {
                    "s01_cancel": StepHandler(
                        invoke=blocked,
                        tool_call_factory=_snapshot_call("sku-1"),
                        idempotent=True,
                    )
                },
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        cancellation.cancel()
        result = await asyncio.wait_for(execution, timeout=1)

        assert result.status is PlanExecutionStatus.CANCELLED
        assert result.steps[0].status is StepExecutionStatus.CANCELLED
        assert finalized.is_set()
        assert [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ] == []

    asyncio.run(scenario())


def test_retryable_idempotent_read_is_retried_once() -> None:
    async def scenario() -> None:
        plan = _plan(_snapshot_step("s01_retry"), plan_id="d4-safe-retry")
        calls = 0

        async def flaky(_invocation: StepInvocation) -> ExecutionValue:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RetryableStepError("upstream_unavailable")
            return ExecutionValue(
                value_type=PlanValueType.PRODUCT_SNAPSHOT,
                value={"item_id": "sku-1"},
            )

        result = await BoundedParallelToolExecutor(
            _policy(max_retries=1, retry_backoff_ms=1)
        ).execute(
            plan,
            _context(),
            {
                "s01_retry": StepHandler(
                    invoke=flaky,
                    tool_call_factory=_snapshot_call("sku-1"),
                    idempotent=True,
                )
            },
        )

        assert calls == 2
        assert result.steps[0].status is StepExecutionStatus.SUCCESS
        assert result.steps[0].attempt_count == 2

    asyncio.run(scenario())


def test_repeated_rag_steps_share_one_tool_invocation() -> None:
    async def scenario() -> None:
        plan = _plan(
            _rag_step("s01_rag"),
            _rag_step("s02_rag_repeat"),
            plan_id="d4-rag-single-flight",
        )
        calls = 0

        async def rag(_invocation: StepInvocation) -> ExecutionValue:
            nonlocal calls
            calls += 1
            return ExecutionValue(
                value_type=PlanValueType.KNOWLEDGE_RESULT,
                value={"trace_id": "rag-1", "collections": 2},
            )

        handler = StepHandler(
            invoke=rag,
            tool_call_factory=_rag_call,
            idempotent=True,
        )
        result = await BoundedParallelToolExecutor(_policy()).execute(
            plan,
            _context(),
            {"s01_rag": handler, "s02_rag_repeat": handler},
        )

        assert calls == 1
        assert [item.status for item in result.steps] == [
            StepExecutionStatus.SUCCESS,
            StepExecutionStatus.SUCCESS,
        ]
        assert result.steps[0].output == result.steps[1].output

    asyncio.run(scenario())
