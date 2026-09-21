from __future__ import annotations

import json

import pytest

from app.routers.AImodel.agent_trace import AgentTraceContext, AgentTraceEventType
from app.routers.AImodel.plan_models import (
    AgentPlanValidator,
    PlanValidationError,
    load_plan_policy,
)


def _rag_draft() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "plan_id": "unit-rag-plan",
        "steps": [
            {
                "step_id": "s01_rag",
                "step_type": "rag_lookup",
                "dependencies": [],
                "inputs": [
                    {
                        "name": "question",
                        "source": "request",
                        "value_type": "user_query",
                    }
                ],
                "output_type": "knowledge_result",
                "risk_level": "medium",
                "allowed_tools": ["rag_lookup"],
                "timeout_ms": 2_000,
            },
            {
                "step_id": "s02_compose",
                "step_type": "compose",
                "dependencies": [
                    {
                        "step_id": "s01_rag",
                        "output_type": "knowledge_result",
                    }
                ],
                "inputs": [
                    {
                        "name": "knowledge",
                        "source": "step",
                        "step_id": "s01_rag",
                        "value_type": "knowledge_result",
                    }
                ],
                "output_type": "response_draft",
                "risk_level": "low",
                "allowed_tools": [],
                "timeout_ms": 2_000,
            },
        ],
        "stop_reasons": ["completed", "safe_fallback"],
    }


@pytest.fixture
def validator() -> AgentPlanValidator:
    return AgentPlanValidator(load_plan_policy())


def test_validator_injects_server_policy_and_effective_budget(
    validator: AgentPlanValidator,
) -> None:
    plan = validator.validate(
        _rag_draft(),
        requested_budget={
            "max_steps": 4,
            "max_candidates": 10,
            "max_concurrency": 1,
        },
    )

    assert plan.policy_version == "d1-plan-policy-v1"
    assert plan.budget.max_steps == 4
    assert plan.budget.max_candidates == 10
    assert plan.budget.max_concurrency == 1
    assert plan.budget.step_timeout_ms == validator.policy.defaults.step_timeout_ms
    assert plan.topological_step_ids == ("s01_rag", "s02_compose")


def test_plan_draft_cannot_supply_server_owned_fields(
    validator: AgentPlanValidator,
) -> None:
    draft = _rag_draft()
    draft["budget"] = {"max_steps": 999}
    draft["policy_version"] = "client-owned"

    with pytest.raises(PlanValidationError) as error:
        validator.validate(draft)

    assert error.value.code == "invalid_schema"


def test_direct_compose_plan_does_not_require_a_synthetic_tool_step(
    validator: AgentPlanValidator,
) -> None:
    plan = validator.validate(
        {
            "schema_version": "1.0",
            "plan_id": "unit-direct-plan",
            "steps": [
                {
                    "step_id": "s01_compose",
                    "step_type": "compose",
                    "dependencies": [],
                    "inputs": [
                        {
                            "name": "question",
                            "source": "request",
                            "value_type": "user_query",
                        }
                    ],
                    "output_type": "response_draft",
                    "risk_level": "low",
                    "allowed_tools": [],
                    "timeout_ms": 2_000,
                }
            ],
            "stop_reasons": ["completed"],
        }
    )

    assert plan.topological_step_ids == ("s01_compose",)
    assert plan.steps[0].allowed_tools == ()


def test_root_input_source_is_type_checked(
    validator: AgentPlanValidator,
) -> None:
    draft = _rag_draft()
    draft["steps"][0]["inputs"][0].update(
        {"source": "context", "value_type": "user_query"}
    )

    with pytest.raises(PlanValidationError) as error:
        validator.validate(draft)

    assert error.value.code == "input_source_mismatch"


def test_step_input_must_be_a_declared_dependency(
    validator: AgentPlanValidator,
) -> None:
    draft = _rag_draft()
    draft["steps"][1]["dependencies"] = []

    with pytest.raises(PlanValidationError) as error:
        validator.validate(draft)

    assert error.value.code == "undeclared_input_dependency"


def test_model_call_count_is_checked_against_effective_budget(
    validator: AgentPlanValidator,
) -> None:
    with pytest.raises(PlanValidationError) as error:
        validator.validate(
            _rag_draft(),
            requested_budget={"max_model_calls": 0},
        )

    assert error.value.code == "step_budget_exceeded"


def test_validation_failure_emits_only_a_stable_trace_code(
    validator: AgentPlanValidator,
) -> None:
    trace = AgentTraceContext.start(user_query="private invalid planning payload")
    draft = _rag_draft()
    draft["steps"][0]["allowed_tools"] = ["action_preview"]

    with pytest.raises(PlanValidationError):
        validator.validate(draft, trace_context=trace)

    event = trace.events[-1]
    assert event.event_type is AgentTraceEventType.PLAN
    assert event.status == "error"
    assert event.summary == {
        "policy_version": validator.policy.policy_version,
        "validation_code": "step_contract_violation",
    }
    assert "private invalid planning payload" not in json.dumps(
        event.to_record(), default=str
    )


def test_restore_rejects_noncanonical_topology(
    validator: AgentPlanValidator,
) -> None:
    plan = validator.validate(_rag_draft())
    payload = plan.model_dump(mode="json")
    payload["topological_step_ids"] = list(reversed(payload["topological_step_ids"]))

    with pytest.raises(PlanValidationError) as error:
        validator.restore(json.dumps(payload))

    assert error.value.code == "topology_mismatch"
