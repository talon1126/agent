import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.routers.AImodel.schemas import AiModelChatRequest, AiModelChatResponse
from app.routers.AImodel.service import _build_langchain_messages, _extract_answer, handle_chat, stream_chat_events
from app.routers.AImodel.tools import (
    build_product_url,
    fetch_product_detail_from_link,
    parse_item_id_from_link,
    search_products,
)


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
        json={"message": "有推荐的解压玩具吗", "links": []},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "AImodel is not configured. Provide DASHSCOPE_API_KEY."


def test_chat_endpoint_streams_sse_from_existing_route(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

    def fake_streaming_agent_runner(request: AiModelChatRequest, tool_results: list) -> list[str]:
        assert request.message == "有推荐的解压玩具吗"
        assert tool_results == []
        return ["推荐", "减压魔方。"]

    events = list(
        stream_chat_events(
            AiModelChatRequest(conversation_id="conv_1", message="有推荐的解压玩具吗", links=[]),
            mock_api_url="http://mock-api",
            streaming_agent_runner=fake_streaming_agent_runner,
        )
    )

    assert events[0].startswith("event: status\n")
    assert 'data: {"content": "正在理解问题"}' in events[0]
    assert any('event: delta\ndata: {"content": "推荐"}' in event for event in events)
    assert any('event: delta\ndata: {"content": "减压魔方。"}' in event for event in events)
    assert events[-1].startswith("event: done\n")
    assert '"answer": "推荐减压魔方。"' in events[-1]


def test_langchain_messages_use_human_message() -> None:
    messages = _build_langchain_messages(
        AiModelChatRequest(
            message="帮我对比这两个商品",
            links=["https://shop.example.com/items/item_milk_pure"],
        )
    )

    assert len(messages) == 1
    assert messages[0].type == "human"
    assert "用户问题：帮我对比这两个商品" in messages[0].content


def test_extract_answer_ignores_human_message_stream_updates() -> None:
    messages = _build_langchain_messages(AiModelChatRequest(message="你好", links=[]))

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
            conversation_id="conv_1",
            message="帮我对比这两个商品",
            links=["https://shop.example.com/items/item_milk_pure"],
        ),
        mock_api_url="http://mock-api",
        http_client=client,
        agent_runner=fake_agent_runner,
    )

    assert isinstance(response, AiModelChatResponse)
    assert response.conversation_id == "conv_1"
    assert response.answer == "纯牛奶更适合早餐场景。"
    assert response.recommended_links[0].item_id == "item_milk_pure"
