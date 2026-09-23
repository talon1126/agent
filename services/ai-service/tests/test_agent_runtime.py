"""Integration coverage for the production D-stage Agent main chain."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx

from app.routers.AImodel.agent_runtime import ShoppingAgentRuntime
from app.routers.AImodel.agent_trace import AgentTraceContext, AgentTraceEventType
from app.routers.AImodel.memory import NoopAiModelMemoryStore
from app.routers.AImodel.schemas import AiModelChatRequest
from app.routers.AImodel.service import stream_chat_events
from app.routers.AImodel.shopping_goal import (
    Constraint,
    DecisionStage,
    GoalEvidence,
    GoalField,
    GoalSourceType,
    ShoppingGoal,
)


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


def _category_goal(value: str, quote: str) -> ShoppingGoal:
    observed_at = datetime.now(UTC)
    return ShoppingGoal(
        decision_stage=DecisionStage.SEARCHING,
        hard_constraints=(
            Constraint(
                field=GoalField.CATEGORY,
                value=value,
                evidence=GoalEvidence(
                    source_type=GoalSourceType.USER_TURN,
                    source_turn=1,
                    quote=quote,
                    confidence=1,
                    created_at=observed_at,
                    updated_at=observed_at,
                ),
            ),
        )
    )


def test_product_search_uses_goal_terms_instead_of_full_user_utterance() -> None:
    search_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            search_queries.append(request.url.params["q"])
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "items": [
                        {"item_id": "item_milk_pure", "item_name": "纯牛奶"}
                    ],
                },
            )
        if request.url.path == "/products/snapshots":
            observed_at = datetime.now(UTC).isoformat()
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "source_version": "agent-runtime-test-v1",
                    "captured_at": observed_at,
                    "items": [
                        {
                            "item_id": "item_milk_pure",
                            "status": "ok",
                            "facts": {
                                "name": "纯牛奶",
                                "category": "dairy",
                                "brand": "Farm",
                                "current_price": "12.9",
                                "currency": "CNY",
                                "stock": 10,
                                "specifications": {"volume": "1L"},
                                "rating": "4.8",
                                "review_count": 100,
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
                    ],
                },
            )
        return httpx.Response(404, json={"ok": False})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://mock-api",
    )
    result = ShoppingAgentRuntime().run(
        AiModelChatRequest(user_id=7, message="请帮我推荐牛奶，预算 20 元以内"),
        conversation_id=12,
        goal=_category_goal("dairy", "牛奶"),
        clarification=None,
        mock_api_url="http://mock-api",
        http_client=client,
    )

    assert result is not None
    assert result.payload.response_type == "recommendation"
    assert search_queries == ["牛奶"]
    assert len(result.evaluation_contexts) == 1
    context = result.evaluation_contexts[0]
    assert context.source_type == "product_snapshot"
    assert context.source_id.endswith(":item_milk_pure")
    assert json.loads(context.content)["facts"]["current_price"]["value"] == "12.9"


def test_candidate_filter_trace_explains_every_exclusion() -> None:
    trace = AgentTraceContext.start(user_query="推荐一款手机")
    calls: list[str] = []

    result = ShoppingAgentRuntime().run(
        AiModelChatRequest(user_id=7, message="推荐一款手机"),
        conversation_id=13,
        goal=_category_goal("dairy", "牛奶"),
        clarification=None,
        mock_api_url="http://mock-api",
        http_client=_mock_product_client(calls),
        trace_context=trace,
    )

    assert result is not None
    filter_event = next(event for event in trace.events if event.stage == "candidate_filter")
    assert filter_event.summary["eligible_count"] == 0
    assert filter_event.summary["excluded_count"] == 2
    assert filter_event.summary["reason_counts"] == [
        {"code": "category_mismatch", "field": "category", "count": 2}
    ]
    assert filter_event.summary["suggestions"] == [
        {
            "action": "relax_constraint",
            "field": "category",
            "reason_code": "category_mismatch",
            "affected_count": 2,
        }
    ]


def test_category_answer_after_clarification_recovers_recommendation_task() -> None:
    calls: list[str] = []
    client = _mock_product_client(calls)

    result = ShoppingAgentRuntime(rag_client=_FakeRagClient()).run(
        AiModelChatRequest(user_id=7, message="我想买电水壶"),
        conversation_id=14,
        goal=_category_goal("electronics", "电水壶"),
        clarification=None,
        mock_api_url="http://mock-api",
        http_client=client,
    )

    assert result is not None
    assert result.planning.task_type == "recommend"
    assert result.payload.response_type == "recommendation"
    assert calls == ["/search", "/products/snapshots"]


def test_search_falls_back_to_canonical_category_after_alias_miss() -> None:
    search_params: list[dict[str, str]] = []
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/search":
            search_params.append(dict(request.url.params))
            if request.url.params.get("q"):
                return httpx.Response(200, json={"ok": True, "items": []})
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "items": [{"item_id": "sku-1", "item_name": "Electric Kettle"}],
                },
            )
        if request.url.path == "/products/snapshots":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "source_version": "category-fallback-v1",
                    "captured_at": datetime.now(UTC).isoformat(),
                    "items": [_product_facts("sku-1", price="199", rating="4.8")],
                },
            )
        return httpx.Response(404, json={"ok": False})

    result = ShoppingAgentRuntime().run(
        AiModelChatRequest(user_id=7, message="我想买电水壶"),
        conversation_id=16,
        goal=_category_goal("electronics", "电水壶"),
        clarification=None,
        mock_api_url="http://mock-api",
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://mock-api",
        ),
    )

    assert result is not None
    assert result.payload.response_type == "recommendation"
    assert search_params == [
        {"q": "电水壶", "category": "electronics"},
        {"category": "electronics"},
    ]
    assert calls == ["/search", "/search", "/products/snapshots"]


def test_stale_required_fact_is_refreshed_once_before_filtering() -> None:
    snapshot_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal snapshot_calls
        if request.url.path == "/search":
            return httpx.Response(
                200,
                json={"ok": True, "items": [{"item_id": "sku-1"}]},
            )
        if request.url.path == "/products/snapshots":
            snapshot_calls += 1
            captured_at = datetime.now(UTC)
            facts = _product_facts("sku-1", price="199", rating="4.8")
            observed_at = (
                captured_at - timedelta(days=1)
                if snapshot_calls == 1
                else captured_at
            ).isoformat()
            facts["facts"]["observed_at"]["stock"] = observed_at
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "source_version": f"refresh-test-v{snapshot_calls}",
                    "captured_at": captured_at.isoformat(),
                    "items": [facts],
                },
            )
        return httpx.Response(404, json={"ok": False})

    trace = AgentTraceContext.start(user_query="推荐手机")
    result = ShoppingAgentRuntime().run(
        AiModelChatRequest(user_id=7, message="推荐手机"),
        conversation_id=15,
        goal=_category_goal("electronics", "手机"),
        clarification=None,
        mock_api_url="http://mock-api",
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://mock-api",
        ),
        trace_context=trace,
    )

    assert result is not None
    assert result.payload.response_type == "recommendation"
    assert snapshot_calls == 2
    refresh_event = next(event for event in trace.events if event.stage == "fact_refresh")
    assert refresh_event.summary["attempt"] == 1
    assert refresh_event.summary["remaining_reason_codes"] == []


def test_missing_fact_after_refresh_is_not_reported_as_no_candidate() -> None:
    snapshot_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal snapshot_calls
        if request.url.path == "/search":
            return httpx.Response(
                200,
                json={"ok": True, "items": [{"item_id": "sku-1"}]},
            )
        if request.url.path == "/products/snapshots":
            snapshot_calls += 1
            captured_at = datetime.now(UTC).isoformat()
            facts = _product_facts("sku-1", price="199", rating="4.8")
            facts["facts"]["stock"] = None
            facts["facts"]["observed_at"]["stock"] = None
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "source_version": f"missing-fact-v{snapshot_calls}",
                    "captured_at": captured_at,
                    "items": [facts],
                },
            )
        return httpx.Response(404, json={"ok": False})

    result = ShoppingAgentRuntime().run(
        AiModelChatRequest(user_id=7, message="推荐手机"),
        conversation_id=17,
        goal=_category_goal("electronics", "手机"),
        clarification=None,
        mock_api_url="http://mock-api",
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://mock-api",
        ),
    )

    assert result is not None
    assert result.payload.response_type == "fallback"
    assert result.payload.reason_code == "required_fact_unavailable"
    assert snapshot_calls == 2


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
