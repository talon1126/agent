from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.routers.AImodel.agent_trace import AgentTraceContext
from app.routers.AImodel.plan_models import (
    AgentPlan,
    AgentPlanValidator,
    PlanStep,
    StepType,
    load_plan_policy,
)
from app.routers.AImodel.tool_policy import (
    ActionPreviewToolInput,
    AgentToolCall,
    AgentToolName,
    CartWriteToolInput,
    OrderLookupToolInput,
    ProductReviewsToolInput,
    ProductSearchToolInput,
    ProductSnapshotToolInput,
    RagLookupToolInput,
    StepPolicyGate,
    ToolAccessMode,
    ToolAuthorizationCode,
    ToolAuthorizationContext,
    WebSearchToolInput,
)


def _root(name: str, source: str, value_type: str) -> dict[str, str]:
    return {"name": name, "source": source, "value_type": value_type}


def _plan_for(step_type: str) -> tuple[AgentPlan, PlanStep]:
    contracts: dict[str, dict[str, Any]] = {
        "product_search": {
            "inputs": [_root("goal", "goal", "shopping_goal")],
            "output_type": "candidate_refs",
            "risk_level": "medium",
            "allowed_tools": ["product_search"],
        },
        "snapshot": {
            "inputs": [_root("page", "context", "page_context")],
            "output_type": "product_snapshot",
            "risk_level": "medium",
            "allowed_tools": ["product_snapshot"],
        },
        "rag_lookup": {
            "inputs": [_root("question", "request", "user_query")],
            "output_type": "knowledge_result",
            "risk_level": "medium",
            "allowed_tools": ["rag_lookup"],
        },
        "compose": {
            "inputs": [_root("question", "request", "user_query")],
            "output_type": "response_draft",
            "risk_level": "low",
            "allowed_tools": [],
        },
    }
    contract = contracts[step_type]
    plan = AgentPlanValidator(load_plan_policy()).validate(
        {
            "schema_version": "1.0",
            "plan_id": f"unit-d3-{step_type}",
            "steps": [
                {
                    "step_id": f"s01_{step_type}",
                    "step_type": step_type,
                    "dependencies": [],
                    "timeout_ms": 2_000,
                    **contract,
                }
            ],
            "stop_reasons": ["completed", "tool_denied"],
        }
    )
    return plan, plan.steps[0]


def _context(*, trace: AgentTraceContext | None = None) -> ToolAuthorizationContext:
    return ToolAuthorizationContext(
        user_id=11,
        conversation_id=22,
        candidate_item_ids=("sku-1",),
        trace_context=trace,
    )


def _call(tool_name: str, **arguments: Any) -> AgentToolCall:
    return AgentToolCall(
        tool_name=tool_name,
        arguments={"user_id": 11, "conversation_id": 22, **arguments},
    )


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (ProductSearchToolInput, {"query": "phone"}),
        (ProductSnapshotToolInput, {"item_ids": ["sku-1"]}),
        (ProductReviewsToolInput, {"item_ids": ["sku-1"]}),
        (RagLookupToolInput, {"query": "memory", "collections": ["guides"]}),
        (WebSearchToolInput, {"query": "release date"}),
        (OrderLookupToolInput, {"order_id": "order-1"}),
        (ActionPreviewToolInput, {"item_id": "sku-1", "quantity": 1}),
        (
            CartWriteToolInput,
            {
                "item_id": "sku-1",
                "quantity": 1,
                "confirmation_token": "signed-confirmation",
            },
        ),
    ],
)
def test_every_registered_tool_has_a_strict_typed_input(
    model: Any, payload: dict[str, Any]
) -> None:
    scoped_payload = {"user_id": 11, "conversation_id": 22, **payload}

    parsed = model.model_validate(scoped_payload)

    assert parsed.user_id == 11
    assert model.model_json_schema()["additionalProperties"] is False
    with pytest.raises(ValidationError):
        model.model_validate({**scoped_payload, "policy_override": "allow"})


@pytest.mark.parametrize("field", ["user_id", "conversation_id"])
def test_scope_ids_do_not_coerce_strings(field: str) -> None:
    payload: dict[str, Any] = {
        "user_id": 11,
        "conversation_id": 22,
        "query": "phone",
    }
    payload[field] = str(payload[field])

    with pytest.raises(ValidationError):
        ProductSearchToolInput.model_validate(payload)


def test_server_step_mapping_wins_over_a_forged_plan_tool_list() -> None:
    plan, search_step = _plan_for("product_search")
    forged_step = search_step.model_copy(update={"step_type": StepType.COMPOSE})
    forged_plan = plan.model_copy(update={"steps": (forged_step,)})

    decision = StepPolicyGate().authorize(
        forged_plan,
        forged_step,
        _call("product_search", query="phone"),
        _context(),
    )

    assert decision.code is ToolAuthorizationCode.STEP_TOOL_NOT_ALLOWED


def test_step_object_must_match_the_step_frozen_in_the_plan() -> None:
    plan, step = _plan_for("product_search")
    altered_step = step.model_copy(update={"timeout_ms": step.timeout_ms + 1})

    decision = StepPolicyGate().authorize(
        plan,
        altered_step,
        _call("product_search", query="phone"),
        _context(),
    )

    assert decision.code is ToolAuthorizationCode.STEP_MISMATCH


@pytest.mark.parametrize("tool_name", ["web_search", "order_lookup", "cart_write"])
def test_registered_but_unplanned_capabilities_are_default_denied(
    tool_name: str,
) -> None:
    plan, step = _plan_for("compose")

    decision = StepPolicyGate().authorize(
        plan,
        step,
        _call(tool_name),
        _context(),
    )

    assert decision.code is ToolAuthorizationCode.STEP_TOOL_NOT_ALLOWED


def test_rag_denial_never_invokes_confirmation_verifier() -> None:
    verifier_inputs: list[CartWriteToolInput] = []

    def verifier(
        tool_input: CartWriteToolInput,
        _context: ToolAuthorizationContext,
    ) -> bool:
        verifier_inputs.append(tool_input)
        return True

    plan, step = _plan_for("rag_lookup")
    decision = StepPolicyGate(confirmation_verifier=verifier).authorize(
        plan,
        step,
        _call(
            "cart_write",
            item_id="sku-1",
            quantity=1,
            confirmation_token="forged-confirmation",
        ),
        _context(),
    )

    assert decision.code is ToolAuthorizationCode.STEP_TOOL_NOT_ALLOWED
    assert verifier_inputs == []


@pytest.mark.parametrize(
    "url",
    [
        "https://127.1/items/sku-1",
        "https://2130706433/items/sku-1",
        "https://0177.0.0.1/items/sku-1",
        "https://0x7f.0.0.1/items/sku-1",
        "https://%31%32%37.0.0.1/items/sku-1",
        "https://[::1]/items/sku-1",
        "https://user:password@shop.example/items/sku-1",
        "https://shop.example:8443/items/sku-1",
    ],
)
def test_url_guard_rejects_alternate_internal_and_credential_forms(url: str) -> None:
    plan, step = _plan_for("snapshot")

    decision = StepPolicyGate().authorize(
        plan,
        step,
        _call("product_snapshot", product_urls=[url]),
        _context(),
    )

    assert decision.code is ToolAuthorizationCode.UNSAFE_URL


def test_each_product_url_requires_its_own_candidate_item_provenance() -> None:
    plan, step = _plan_for("snapshot")

    decision = StepPolicyGate().authorize(
        plan,
        step,
        _call(
            "product_snapshot",
            item_ids=["sku-1"],
            product_urls=["https://shop.example/no-product-id"],
        ),
        _context(),
    )

    assert decision.code is ToolAuthorizationCode.ITEM_PROVENANCE_REQUIRED


def test_encoded_path_traversal_cannot_reuse_a_candidate_item_id() -> None:
    plan, step = _plan_for("snapshot")

    decision = StepPolicyGate().authorize(
        plan,
        step,
        _call(
            "product_snapshot",
            product_urls=["https://shop.example/items/sku-1%2F..%2Fadmin"],
        ),
        _context(),
    )

    assert decision.code is ToolAuthorizationCode.ITEM_PROVENANCE_REQUIRED


def test_persisted_trace_keeps_public_code_and_redacts_argument_values() -> None:
    trace = AgentTraceContext.start(user_query="do not persist this text")
    plan, step = _plan_for("product_search")

    decision = StepPolicyGate().authorize(
        plan,
        step,
        AgentToolCall(
            tool_name="product_search",
            arguments={
                "user_id": 11,
                "conversation_id": 22,
                "query": "private-query-value",
                "authorization": "private-sensitive-header-value",
                "sensitive-text-used-as-a-field-name": "ignored",
            },
        ),
        _context(trace=trace),
    )

    record = trace.events[-1].to_record()
    serialized = json.dumps(record, default=str)
    assert decision.code is ToolAuthorizationCode.INVALID_ARGUMENTS
    assert record["summary"]["authorization_code"] == "invalid_arguments"
    assert "private-query-value" not in serialized
    assert "private-secret-value" not in serialized
    assert "sensitive-text-used-as-a-field-name" not in serialized


def test_unknown_tool_has_a_stable_code_without_internal_registry_details() -> None:
    plan, step = _plan_for("product_search")

    decision = StepPolicyGate().authorize(
        plan,
        step,
        _call("shell_command", command="printenv"),
        _context(),
    )

    assert decision.code is ToolAuthorizationCode.UNKNOWN_TOOL
    assert decision.access_mode is None
    assert "_TOOL_POLICIES" not in json.dumps(decision.model_dump(mode="json"))


def test_cart_write_remains_high_risk_and_default_denied_until_e4() -> None:
    gate = StepPolicyGate()

    assert gate.access_mode_for(AgentToolName.CART_WRITE) is ToolAccessMode.WRITE
    assert (
        gate.access_mode_for(AgentToolName.ACTION_PREVIEW)
        is ToolAccessMode.WRITE_PREVIEW
    )
