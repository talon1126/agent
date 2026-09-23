from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.kayn_target import KaynAgentTarget
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

    async def fake_evaluation(request, **_kwargs):
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
                {"input": "推荐手机", "user_id": 7},
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
    assert len(result["traceId"]) == 32
    assert "ai.agent.turn" in names
    assert "kayn.endpoint.talonmart-shopping-agent" in names
    endpoint_span = next(
        span for span in spans if span.name == "kayn.endpoint.talonmart-shopping-agent"
    )
    agent_span = next(span for span in spans if span.name == "ai.agent.turn")
    response_span = next(span for span in spans if ".response." in span.name)
    assert agent_span.parent.span_id == endpoint_span.context.span_id
    assert response_span.parent.span_id == agent_span.context.span_id
