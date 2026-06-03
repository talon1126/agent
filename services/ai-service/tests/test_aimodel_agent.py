import importlib

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.routers.AImodel.schemas import AiModelChatRequest, AiModelChatResponse
from app.routers.AImodel.memory import AiModelMemoryMessage, AiModelUserMemory, NoopAiModelMemoryStore
from app.routers.AImodel.service import (
    _build_langchain_messages,
    _extract_answer,
    _extract_stream_token,
    handle_chat,
    stream_chat_events,
)
from app.routers.AImodel.tools import (
    build_product_url,
    fetch_product_detail_from_link,
    parse_item_id_from_link,
    search_products,
)

aimodel_router = importlib.import_module("app.routers.AImodel.router")


def test_parse_item_id_from_frontend_product_link() -> None:
    assert parse_item_id_from_link("https://shop.example.com/items/item_milk_pure") == "item_milk_pure"
    assert parse_item_id_from_link("/items/item_vinda_tissue?from=chat") == "item_vinda_tissue"


def test_build_product_url_uses_frontend_base_url(monkeypatch) -> None:
    monkeypatch.setenv("FRONTEND_BASE_URL", "https://shop.example.com/")

    assert build_product_url("item_milk_pure") == "https://shop.example.com/items/item_milk_pure"


def test_build_product_url_falls_back_to_relative_link(monkeypatch) -> None:
    monkeypatch.delenv("FRONTEND_BASE_URL", raising=False)

    assert build_product_url("item_milk_pure") == "/items/item_milk_pure"


def test_fetch_product_detail_from_link_calls_mock_api_product_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/ip/item_milk_pure":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "item": {
                        "item_id": "item_milk_pure",
                        "item_name": "纯牛奶",
                        "price": 12.5,
                    },
                },
            )
        return httpx.Response(404, json={"ok": False, "error": "item_not_found"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mock-api")

    result = fetch_product_detail_from_link(
        "https://shop.example.com/items/item_milk_pure",
        mock_api_url="http://mock-api",
        http_client=client,
    )

    assert result.ok is True
    assert result.item_id == "item_milk_pure"
    assert result.data["item"]["item_name"] == "纯牛奶"
    assert [request.url.path for request in requests] == ["/ip/item_milk_pure"]


def test_search_products_calls_mock_api_search_and_adds_product_links(monkeypatch) -> None:
    monkeypatch.setenv("FRONTEND_BASE_URL", "https://shop.example.com")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/search":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "items": [
                        {
                            "item_id": "item_toy_cube",
                            "item_name": "减压魔方",
                            "price": 8.9,
                        }
                    ],
                },
            )
        return httpx.Response(404, json={"ok": False})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mock-api")

    result = search_products("解压玩具", mock_api_url="http://mock-api", http_client=client)

    assert result.ok is True
    assert requests[0].url.path == "/search"
    assert requests[0].url.params["q"] == "解压玩具"
    assert result.data["items"][0]["url"] == "https://shop.example.com/items/item_toy_cube"


def test_handle_chat_returns_503_when_dashscope_api_key_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    response = TestClient(app).post(
        "/AImodel/chat",
        json={"user_id": 1, "message": "有推荐的解压玩具吗", "links": []},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "AImodel is not configured. Provide DASHSCOPE_API_KEY."


def test_chat_endpoint_streams_sse_from_existing_route(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    memory_store = NoopAiModelMemoryStore()

    def fake_streaming_agent_runner(request: AiModelChatRequest, tool_results: list) -> list[str]:
        assert request.message == "有推荐的解压玩具吗"
        assert tool_results == []
        return ["推荐", "减压魔方。"]

    events = list(
        stream_chat_events(
            AiModelChatRequest(user_id=1, conversation_id=None, message="有推荐的解压玩具吗", links=[]),
            mock_api_url="http://mock-api",
            streaming_agent_runner=fake_streaming_agent_runner,
            memory_store=memory_store,
        )
    )

    assert events[0].startswith("event: status\n")
    assert 'data: {"content": "正在理解问题"}' in events[0]
    assert any('event: delta\ndata: {"content": "推荐"}' in event for event in events)
    assert any('event: delta\ndata: {"content": "减压魔方。"}' in event for event in events)
    assert events[-1].startswith("event: done\n")
    assert '"conversation_id": 1' in events[-1]
    assert '"answer": "推荐减压魔方。"' in events[-1]
    assert "tool_results" not in events[-1]
    stored_messages = memory_store.load_recent_messages(1, limit=5)
    assert [message.role for message in stored_messages] == ["user", "assistant"]
    assert [message.content for message in stored_messages] == ["有推荐的解压玩具吗", "推荐减压魔方。"]


def test_stream_chat_filters_tool_json_from_model_answer(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    leaked_tool_json = (
        '{"tool": "search_products", "ok": true, "input": "无线耳机", '
        '"item_id": null, "data": {"ok": true, "items": []}, "error": null}'
    )

    def fake_streaming_agent_runner(request: AiModelChatRequest, tool_results: list) -> list[str]:
        return [
            "我先查询商品。\n\n",
            leaked_tool_json[:45],
            leaked_tool_json[45:],
            "目前没有找到无线耳机商品。\n- 可以关注续航\n- 可以关注佩戴舒适度",
        ]

    events = list(
        stream_chat_events(
            AiModelChatRequest(user_id=1, conversation_id=1, message="如何挑选高性价比的无线耳机?", links=[]),
            mock_api_url="http://mock-api",
            streaming_agent_runner=fake_streaming_agent_runner,
            memory_store=NoopAiModelMemoryStore(),
        )
    )
    response_text = "".join(events)

    assert '"tool": "search_products"' not in response_text
    assert '\\"tool\\": \\"search_products\\"' not in response_text
    assert '"data": {"ok": true' not in response_text
    assert '\\"data\\": {\\"ok\\": true' not in response_text
    assert "目前没有找到无线耳机商品" in response_text


def test_extract_stream_token_reads_ai_message_chunks() -> None:
    class FakeAiChunk:
        content = "推荐"
        type = "AIMessageChunk"

    assert _extract_stream_token((FakeAiChunk(), {"langgraph_node": "model"})) == "推荐"
    assert _extract_stream_token({"messages": []}) == ""


def test_langchain_messages_use_human_message() -> None:
    messages = _build_langchain_messages(
        AiModelChatRequest(
            user_id=1,
            message="帮我对比这两个商品",
            links=["https://shop.example.com/items/item_milk_pure"],
        )
    )

    assert len(messages) == 1
    assert messages[0].type == "human"
    assert "用户问题：帮我对比这两个商品" in messages[0].content


def test_langchain_messages_include_recent_history_and_user_memories() -> None:
    messages = _build_langchain_messages(
        AiModelChatRequest(
            user_id=1,
            message="那我刚才喜欢什么?",
            links=[],
        ),
        history=[
            AiModelMemoryMessage(role="user", content="我喜欢小米"),
            AiModelMemoryMessage(role="assistant", content="我记住了你喜欢小米。"),
        ],
        user_memories=[
            AiModelUserMemory(
                memory_type="brand_preference",
                memory_value="小米",
                evidence="用户表达了对小米的品牌偏好。",
                confidence=0.8,
            )
        ],
    )

    assert [message.type for message in messages] == ["human", "human", "ai", "human"]
    assert "用户长期偏好" in messages[0].content
    assert "品牌偏好：小米" in messages[0].content
    assert messages[1].content == "我喜欢小米"
    assert messages[2].content == "我记住了你喜欢小米。"
    assert "用户问题：那我刚才喜欢什么?" in messages[3].content


def test_extract_answer_ignores_human_message_stream_updates() -> None:
    messages = _build_langchain_messages(AiModelChatRequest(user_id=1, message="你好", links=[]))

    assert _extract_answer({"messages": messages}) == ""


def test_handle_chat_uses_injected_agent_runner_without_real_dashscope(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

    def fake_agent_runner(request: AiModelChatRequest, tool_results: list) -> str:
        assert request.message == "帮我对比这两个商品"
        assert tool_results[0].item_id == "item_milk_pure"
        return "纯牛奶更适合早餐场景。"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ip/item_milk_pure":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "item": {
                        "item_id": "item_milk_pure",
                        "item_name": "纯牛奶",
                        "price": 12.5,
                    },
                },
            )
        return httpx.Response(404, json={"ok": False})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mock-api")

    response = handle_chat(
        AiModelChatRequest(
            user_id=1,
            conversation_id=1,
            message="帮我对比这两个商品",
            links=["https://shop.example.com/items/item_milk_pure"],
        ),
        mock_api_url="http://mock-api",
        http_client=client,
        agent_runner=fake_agent_runner,
    )

    assert isinstance(response, AiModelChatResponse)
    assert response.conversation_id == 1
    assert response.answer == "纯牛奶更适合早餐场景。"
    assert response.recommended_links[0].item_id == "item_milk_pure"


def test_conversation_routes_list_conversations_and_messages(monkeypatch) -> None:
    store = NoopAiModelMemoryStore()
    conversation_id = store.ensure_conversation(None, user_id=1, first_message="我喜欢小米")
    store.append_user_message(conversation_id, user_id=1, content="我喜欢小米", links=[])
    store.append_assistant_message(conversation_id, user_id=1, content="已记住。", recommended_links=[])
    monkeypatch.setattr(aimodel_router, "get_aimodel_memory_store", lambda: store)

    client = TestClient(app)
    conversations_response = client.get("/AImodel/conversations", params={"user_id": 1})
    messages_response = client.get(f"/AImodel/conversations/{conversation_id}/messages", params={"user_id": 1})

    assert conversations_response.status_code == 200
    assert conversations_response.json() == [
        {
            "id": conversation_id,
            "title": "我喜欢小米",
            "created_at": None,
            "updated_at": None,
        }
    ]
    assert messages_response.status_code == 200
    assert messages_response.json() == [
        {
            "id": 1,
            "role": "user",
            "content": "我喜欢小米",
            "links": [],
            "recommended_links": [],
            "created_at": None,
        },
        {
            "id": 2,
            "role": "assistant",
            "content": "已记住。",
            "links": [],
            "recommended_links": [],
            "created_at": None,
        },
    ]
