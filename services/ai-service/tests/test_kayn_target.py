from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

import pytest

from app.kayn_target import (
    KaynAgentTarget,
    _connector_max_message_bytes,
    evaluate_talonmart_contract,
)
from app.kayn_runtime_fingerprint import evaluated_files, implementation_fingerprint
from app.routers.AImodel.agent_trace import AgentTraceContext
from app.routers.AImodel.evaluation import AiModelEvaluationResult
from app.routers.AImodel.service import AiModelExecutionCapture


def test_kayn_target_builds_v2_request_and_returns_normalized_result() -> None:
    observed: dict[str, Any] = {}

    async def fake_evaluation(request, **kwargs):
        observed["request"] = request
        observed["kwargs"] = kwargs
        return (
            AiModelEvaluationResult(
                output="推荐结果",
                context=["商品事实"],
                metadata={"response_type": "recommendation"},
                toolCalls=[{"name": "product_search", "status": "succeeded"}],
            ),
            AiModelExecutionCapture(),
        )

    target = KaynAgentTarget(
        object(),
        mock_api_url="http://mock-api",
        evaluation_runner=fake_evaluation,
        callback_factory=lambda: "kayn-callback",
    )
    result = asyncio.run(
        target.invoke(
            "推荐手机",
            user_id=7,
            page_context={"page_type": "search", "search_query": "手机"},
        )
    )

    request = observed["request"]
    assert request.request_version == "v2"
    assert request.user_id == 7
    assert request.page_context.search_query == "手机"
    assert observed["kwargs"]["langchain_callbacks"] == ["kayn-callback"]
    assert result.output == "推荐结果"
    assert result.context == ["商品事实"]
    assert result.toolCalls[0]["name"] == "product_search"


def test_kayn_target_decodes_versioned_evaluation_context_envelope() -> None:
    observed: dict[str, Any] = {}

    async def fake_evaluation(request, **_kwargs):
        observed["request"] = request
        return (
            AiModelEvaluationResult(output="详情结果", metadata={}),
            AiModelExecutionCapture(),
        )

    target = KaynAgentTarget(
        object(),
        mock_api_url="http://mock-api",
        evaluation_runner=fake_evaluation,
    )
    envelope = (
        '{"_talonmart_evaluation_version":1,"message":"这款适合 40 平吗？",'
        '"links":["https://item.example/1"],"page_context":'
        '{"page_type":"product","current_item_id":"item_air_purifier",'
        '"candidate_refs":[{"item_id":"item_air_purifier"}]}}'
    )

    asyncio.run(
        target.invoke(
            envelope,
            user_id=9,
            conversation_id=11,
            request={"target_context": {}},
        )
    )

    request = observed["request"]
    assert request.message == "这款适合 40 平吗？"
    assert request.links == ["https://item.example/1"]
    assert request.request_version == "v2"
    assert request.page_context.current_item_id == "item_air_purifier"
    assert request.page_context.candidate_refs[0].item_id == "item_air_purifier"


def test_kayn_connector_message_limit_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KAYN_CONNECTOR_MAX_MESSAGE_BYTES", raising=False)
    assert _connector_max_message_bytes() == 16 * 1024 * 1024

    monkeypatch.setenv("KAYN_CONNECTOR_MAX_MESSAGE_BYTES", "1048575")
    with pytest.raises(RuntimeError, match="between 1048576 and 16777216"):
        _connector_max_message_bytes()


def test_real_kayn_sdk_contract_exports_result_and_nested_agent_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAYN_AGENT_ENDPOINT_NAME", "talonmart-shopping-agent")
    kayn_sdk = pytest.importorskip("kayn_sdk")
    exporter_module = pytest.importorskip(
        "opentelemetry.sdk.trace.export.in_memory_span_exporter"
    )
    from app.kayn_target import register_kayn_agent_target

    exporter = exporter_module.InMemorySpanExporter()
    client = kayn_sdk.KaynClient(
        api_token="kayn-test-token",
        base_url="https://kayn.example",
        project_id="11111111-1111-1111-1111-111111111111",
        environment="local",
        exporter=exporter,
    )

    observed_requests = []

    async def fake_evaluation(request, **_kwargs):
        observed_requests.append(request)
        trace = AgentTraceContext.start(
            user_query=request.message,
            conversation_id=request.conversation_id,
        )
        trace.complete(message_id=1, query_trace_ids=["query-sdk-1"])
        return (
            AiModelEvaluationResult(
                output="SDK 推荐结果",
                context=["最终上下文"],
                metadata={"response_type": "recommendation"},
                toolCalls=[{"name": "product_search", "status": "succeeded"}],
            ),
            AiModelExecutionCapture(trace_context=trace),
        )

    try:
        _target, _registered = register_kayn_agent_target(
            client,
            mock_api_url="http://mock-api",
            evaluation_runner=fake_evaluation,
        )
        result = asyncio.run(
            kayn_sdk.EndpointExecutor(client).execute(
                "talonmart-shopping-agent",
                {
                    "input": "推荐手机",
                    "conversation_id": "11111111-1111-4111-8111-111111111111",
                    "conversation_id_int": 1610661890,
                    "history": [],
                    "messages": [{"role": "user", "content": "推荐手机"}],
                },
                context={
                    "x-kayn-conversation-id": ("11111111-1111-4111-8111-111111111111")
                },
                require_async=True,
            )
        )
        contract_result = asyncio.run(
            kayn_sdk.SdkMetricExecutor(client).execute(
                "talonmart_contract_guard",
                "1.0.0",
                {
                    "input": "推荐手机",
                    "output": "SDK 推荐结果",
                    "expected_output": {"required_tools": ["product_search"]},
                    "tool_calls": [{"name": "product_search", "status": "succeeded"}],
                },
                require_async=True,
            )
        )
        client.force_flush()
        spans = exporter.get_finished_spans()
        names = [span.name for span in spans]
    finally:
        client.close()

    assert result["status"] == "success"
    assert result["output"] == "SDK 推荐结果"
    assert result["context"] == ["最终上下文"]
    assert result["toolCalls"][0]["name"] == "product_search"
    assert contract_result["status"] == "success"
    assert contract_result["output"]["evidence"][-1] == {
        "type": "runtime_fingerprint",
        "sha256": implementation_fingerprint(
            Path(__file__).resolve().parents[3],
            evaluated_files(Path(__file__).resolve().parents[3]),
        ),
    }
    assert len(result["traceId"]) == 32
    assert observed_requests[0].user_id == 1610661890
    assert observed_requests[0].conversation_id == 1610661890
    assert "ai.agent.turn" in names
    assert "kayn.endpoint.talonmart-shopping-agent" in names
    endpoint_span = next(
        span for span in spans if span.name == "kayn.endpoint.talonmart-shopping-agent"
    )
    agent_span = next(span for span in spans if span.name == "ai.agent.turn")
    response_span = next(span for span in spans if ".response." in span.name)
    assert endpoint_span.attributes["kayn.conversation.id"] == (
        "11111111-1111-4111-8111-111111111111"
    )
    assert agent_span.parent.span_id == endpoint_span.context.span_id
    assert response_span.parent.span_id == agent_span.context.span_id


def test_real_kayn_sdk_maps_multi_turn_and_isolates_scenarios(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAYN_AGENT_ENDPOINT_NAME", "talonmart-shopping-agent")
    kayn_sdk = pytest.importorskip("kayn_sdk")
    from app.kayn_target import register_kayn_agent_target

    client = kayn_sdk.KaynClient(
        api_token="kayn-test-token",
        base_url="https://kayn.example",
        project_id="11111111-1111-1111-1111-111111111111",
        environment="local",
    )
    observed: list[tuple[str, int, int | None, str | None]] = []

    async def fake_evaluation(request, **_kwargs):
        observed.append(
            (
                request.message,
                request.user_id,
                request.conversation_id,
                request.page_context.current_item_id if request.page_context else None,
            )
        )
        return (
            AiModelEvaluationResult(
                output=f"回答：{request.message}",
                metadata={"response_type": "answer"},
            ),
            AiModelExecutionCapture(),
        )

    try:
        register_kayn_agent_target(
            client,
            mock_api_url="http://mock-api",
            evaluation_runner=fake_evaluation,
        )
        executor = kayn_sdk.EndpointExecutor(client)

        async def execute_turn(
            message: str,
            *,
            external_id: str,
            internal_id: int,
            history: list[dict[str, str]],
        ) -> dict[str, Any]:
            return await executor.execute(
                "talonmart-shopping-agent",
                {
                    "input": message,
                    "conversation_id": external_id,
                    "conversation_id_int": internal_id,
                    "target_context": {
                        "page_type": "product",
                        "current_item_id": "ord_300",
                    },
                    "history": history,
                    "messages": [*history, {"role": "user", "content": message}],
                },
                context={"x-kayn-conversation-id": external_id},
                require_async=True,
            )

        async def scenario() -> list[dict[str, Any]]:
            first = await execute_turn(
                "推荐手机",
                external_id="conversation-a",
                internal_id=101,
                history=[],
            )
            second = await execute_turn(
                "预算三千",
                external_id="conversation-a",
                internal_id=101,
                history=[
                    {"role": "user", "content": "推荐手机"},
                    {"role": "assistant", "content": "请告诉我预算"},
                ],
            )
            isolated = await execute_turn(
                "推荐耳机",
                external_id="conversation-b",
                internal_id=202,
                history=[],
            )
            return [first, second, isolated]

        results = asyncio.run(scenario())
    finally:
        client.close()

    assert [result["status"] for result in results] == ["success"] * 3
    assert observed == [
        ("推荐手机", 101, 101, "ord_300"),
        ("预算三千", 101, 101, "ord_300"),
        ("推荐耳机", 202, 202, "ord_300"),
    ]


def test_kayn_m1_alias_keeps_primary_endpoint_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kayn_sdk = pytest.importorskip("kayn_sdk")
    from app.kayn_target import register_kayn_agent_target

    monkeypatch.setenv("KAYN_AGENT_ENDPOINT_NAME", "talonmart-shopping-agent")
    monkeypatch.setenv("KAYN_M1_AGENT_ENDPOINT_NAME", "talonmart-shopping-agent-m1")
    client = kayn_sdk.KaynClient(
        api_token="kayn-test-token",
        base_url="https://kayn.example",
        project_id="11111111-1111-1111-1111-111111111111",
        environment="local",
    )

    async def fake_evaluation(request, **_kwargs):
        return (
            AiModelEvaluationResult(output=request.message, metadata={}),
            AiModelExecutionCapture(),
        )

    try:
        register_kayn_agent_target(client, evaluation_runner=fake_evaluation)
        executor = kayn_sdk.EndpointExecutor(client)
        results = [
            asyncio.run(executor.execute(name, {"input": "比较商品"}))
            for name in (
                "talonmart-shopping-agent",
                "talonmart-shopping-agent-m1",
            )
        ]
    finally:
        client.close()

    assert [result["status"] for result in results] == ["success", "success"]
    assert [result["output"] for result in results] == ["比较商品", "比较商品"]


def test_talonmart_contract_guard_rejects_missing_and_forbidden_tools() -> None:
    result = asyncio.run(
        evaluate_talonmart_contract(
            input="查询物流",
            output="请稍后重试",
            expected_output={
                "required_tools": ["product_search", "order_lookup"],
                "forbidden_tools": ["refund_order"],
            },
            tool_calls=[
                {"name": "search_products", "status": "succeeded"},
                {"name": "refund_order", "status": "succeeded"},
            ],
            conversation=[],
        )
    )

    assert result["passed"] is False
    assert result["score"] == 0
    assert result["evidence"] == [
        {
            "type": "tool_contract",
            "missingRequired": ["order_lookup"],
            "forbiddenUsed": ["refund_order"],
        }
    ]


def test_kayn_contract_metric_attests_runtime_fingerprint() -> None:
    result = asyncio.run(
        evaluate_talonmart_contract(
            input="比较商品",
            output="比较结果",
            runtime_fingerprint="a" * 64,
        )
    )

    assert result["evidence"][-1] == {
        "type": "runtime_fingerprint",
        "sha256": "a" * 64,
    }


def test_kayn_runtime_fingerprint_covers_agent_sources(tmp_path) -> None:
    source = tmp_path / "agent.py"
    source.write_text("old", encoding="utf-8")
    before = implementation_fingerprint(tmp_path, ("agent.py",))
    source.write_text("new", encoding="utf-8")

    assert implementation_fingerprint(tmp_path, ("agent.py",)) != before
    assert "services/ai-service/app/routers/AImodel/verifier.py" in evaluated_files(
        Path(__file__).resolve().parents[3]
    )


def test_kayn_eval_manifest_requires_each_runtime_fingerprint() -> None:
    pytest.importorskip("kayn_sdk")
    script = Path(__file__).resolve().parents[3] / "scripts/run_kayn_abcd_evaluation.py"
    spec = importlib.util.spec_from_file_location("run_kayn_abcd_evaluation", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    evidence = [{"type": "runtime_fingerprint", "sha256": "a" * 64}]
    single = {
        "details": [
            {
                "evidence": {
                    "metricResults": [
                        {
                            "metricKey": "talonmart_contract_guard",
                            "evidence": evidence,
                        }
                    ]
                }
            }
        ]
    }
    multi = {
        "details": [
            {
                "evidence": {
                    "metrics": {
                        "talonmart_contract_guard": {
                            "outcomes": [{"evidence": evidence}]
                        }
                    }
                }
            }
        ]
    }

    assert module.target_runtime_fingerprint(single, multi) == "a" * 64
    multi["details"][0]["evidence"]["metrics"]["talonmart_contract_guard"]["outcomes"][0]["evidence"] = []
    assert module.target_runtime_fingerprint(single, multi) is None
