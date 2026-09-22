"""Integration coverage for the production D-stage Agent main chain."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from app.routers.AImodel.agent_runtime import ShoppingAgentRuntime
from app.routers.AImodel.agent_trace import AgentTraceContext, AgentTraceEventType
from app.routers.AImodel.memory import NoopAiModelMemoryStore
from app.routers.AImodel.schemas import AiModelChatRequest
from app.routers.AImodel.service import stream_chat_events
from app.routers.AImodel.shopping_goal import ShoppingGoal


def _product_facts(item_id: str, *, price: str, rating: str) -> dict:
    observed_at = datetime.now(UTC).isoformat()
    return {
        "item_id": item_id,
        "status": "ok",
        "facts": {
            "name": f"测试手机 {item_id}",
            "category": "electronics",
            "brand": f"Brand-{item_id}",
            "current_price": price,
            "currency": "CNY",
            "stock": 20,
            "specifications": {
                "memory": "8GB",
                "storage": "128GB",
                "wireless_charging": "true",
                "model": item_id,
            },
            "rating": rating,
            "review_count": 300,
            "delivery": {
                "shipping_available": True,
                "pickup_available": False,
                "delivery_available": True,
            },
            "observed_at": {
                "catalog": observed_at,
                "price": observed_at,
                "stock": observed_at,
                "rating": observed_at,
                "delivery": observed_at,
            },
        },
    }


def _mock_product_client(calls: list[str]) -> httpx.Client:
    products = {
        "sku-1": _product_facts("sku-1", price="2999", rating="4.8"),
        "sku-2": _product_facts("sku-2", price="2599", rating="4.6"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/search":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "items": [
                        {"item_id": "sku-1", "item_name": "测试手机 sku-1"},
                        {"item_id": "sku-2", "item_name": "测试手机 sku-2"},
                    ],
                },
            )
        if request.url.path == "/products/snapshots":
            requested = json.loads(request.content)["item_ids"]
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "source_version": "agent-runtime-test-v1",
                    "captured_at": datetime.now(UTC).isoformat(),
                    "items": [products[item_id] for item_id in requested],
                },
            )
        return httpx.Response(404, json={"ok": False})

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://mock-api",
    )


def _parse_event(raw_event: str) -> tuple[str, dict]:
    lines = raw_event.strip().splitlines()
    return (
        lines[0].removeprefix("event: "),
        json.loads(lines[1].removeprefix("data: ")),
    )


class _FakeRagClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def query_knowledge_hub(self, **arguments: object) -> dict:
        self.calls.append(dict(arguments))
        return {
            "ok": True,
            "trace_id": "rag-runtime-trace",
            "content": "退货前请保持商品和附件完整，并按订单页面指引申请。",
            "citations": [],
            "images": [],
            "is_empty": False,
        }


def test_recommendation_runs_plan_policy_executor_ranker_and_verifier() -> None:
    calls: list[str] = []
    client = _mock_product_client(calls)
    trace = AgentTraceContext.start(user_query="推荐一款手机")

    result = ShoppingAgentRuntime().run(
        AiModelChatRequest(user_id=7, message="推荐一款手机"),
        conversation_id=11,
        goal=ShoppingGoal(),
        clarification=None,
        mock_api_url="http://mock-api",
        http_client=client,
        trace_context=trace,
    )

    assert result is not None
    assert result.planning.task_type == "recommend"
    assert result.execution.status == "success"
    assert result.payload.response_type == "recommendation"
    assert calls == ["/search", "/products/snapshots"]
    assert all(item.evidence_ids for item in result.payload.recommendations)
    event_types = [event.event_type for event in trace.events]
    assert AgentTraceEventType.PLAN in event_types
    assert AgentTraceEventType.TOOL_CALL in event_types
    assert AgentTraceEventType.STEP in event_types
    assert AgentTraceEventType.VERIFY in event_types
    assert [event.summary["authorization_code"] for event in trace.events if event.stage == "step_policy"] == [
        "allowed",
        "allowed",
    ]


def test_knowledge_route_runs_authorized_rag_plan() -> None:
    rag_client = _FakeRagClient()
    trace = AgentTraceContext.start(user_query="商品怎么申请退货")

    result = ShoppingAgentRuntime(rag_client=rag_client).run(
        AiModelChatRequest(user_id=7, message="商品怎么申请退货"),
        conversation_id=11,
        goal=ShoppingGoal(),
        clarification=None,
        mock_api_url="http://mock-api",
        trace_context=trace,
    )

    assert result is not None
    assert result.planning.task_type == "knowledge"
    assert result.execution.status == "success"
    assert result.payload.response_type == "answer"
    assert "订单页面" in result.payload.answer
    assert len(rag_client.calls) == 1
    authorization = [
        event for event in trace.events if event.stage == "step_policy"
    ]
    assert [(event.tool_name, event.summary["authorization_code"]) for event in authorization] == [
        ("rag_lookup", "allowed")
    ]


def test_default_sse_uses_verified_main_chain_and_persists_structured_result(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    calls: list[str] = []
    client = _mock_product_client(calls)
    memory_store = NoopAiModelMemoryStore()

    events = [
        _parse_event(raw)
        for raw in stream_chat_events(
            AiModelChatRequest(user_id=7, message="推荐一款手机"),
            mock_api_url="http://mock-api",
            http_client=client,
            memory_store=memory_store,
        )
    ]
    done = next(payload for event, payload in events if event == "done")

    assert done["response_type"] == "recommendation"
    assert done["payload"]["recommendations"]
    assert events[-1][0] == "done"
    assert calls == ["/search", "/products/snapshots"]
    stored = memory_store.list_messages(1, user_id=7)
    assert stored[-1].structured_response["response_type"] == "recommendation"
