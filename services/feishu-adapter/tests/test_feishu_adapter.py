import json

import httpx
from fastapi.testclient import TestClient

from app.feishu_events import FeishuMessage, normalize_feishu_event
from app.main import create_app


def test_url_verification_returns_challenge() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/feishu/events",
        json={
            "type": "url_verification",
            "token": "verify-token",
            "challenge": "challenge-code",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-code"}


def test_normalize_text_event_extracts_chat_message_fields() -> None:
    payload = {
        "schema": "2.0",
        "header": {
            "event_id": "evt_001",
            "event_type": "im.message.receive_v1",
            "token": "verify-token",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_sender"}},
            "message": {
                "message_id": "om_001",
                "chat_id": "oc_chat",
                "message_type": "text",
                "content": '{"text":"帮我查一下订单 ord_100"}',
            },
        },
    }

    message = normalize_feishu_event(payload)

    assert message == FeishuMessage(
        platform="feishu",
        message_type="text",
        sender_id="ou_sender",
        chat_id="oc_chat",
        message_id="om_001",
        text="帮我查一下订单 ord_100",
        audio_url="",
        media_id="",
        raw_payload=payload,
    )


def test_event_callback_forwards_normalized_message_to_n8n() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "reply": "Order ord_100 is delivered.",
            },
        )

    transport = httpx.MockTransport(handler)
    app = create_app(
        http_client=httpx.Client(transport=transport),
        n8n_webhook_url="http://n8n.local/webhook/chat-agent-inbound",
    )
    client = TestClient(app)

    response = client.post(
        "/feishu/events",
        json={
            "schema": "2.0",
            "header": {
                "event_id": "evt_001",
                "event_type": "im.message.receive_v1",
                "token": "verify-token",
            },
            "event": {
                "sender": {"sender_id": {"open_id": "ou_sender"}},
                "message": {
                    "message_id": "om_001",
                    "chat_id": "oc_chat",
                    "message_type": "text",
                    "content": '{"text":"帮我查一下订单 ord_100"}',
                },
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "platform": "feishu",
        "message_id": "om_001",
        "accepted": True,
    }
    assert len(requests) == 1
    assert requests[0].url == "http://n8n.local/webhook/chat-agent-inbound"
    assert requests[0].headers["content-type"] == "application/json"
    assert json.loads(requests[0].content) == {
        "platform": "feishu",
        "message_type": "text",
        "sender_id": "ou_sender",
        "chat_id": "oc_chat",
        "message_id": "om_001",
        "text": "帮我查一下订单 ord_100",
        "audio_url": "",
        "media_id": "",
    }


def test_event_callback_replies_to_feishu_when_credentials_are_configured() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "http://n8n.local/webhook/chat-agent-inbound":
            return httpx.Response(200, json={"reply": "Order ord_100 is delivered."})
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/im/v1/messages/om_001/reply":
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "reply_001"}})
        return httpx.Response(404, json={"error": "unexpected url"})

    transport = httpx.MockTransport(handler)
    app = create_app(
        http_client=httpx.Client(transport=transport),
        n8n_webhook_url="http://n8n.local/webhook/chat-agent-inbound",
        feishu_app_id="cli_test",
        feishu_app_secret="secret_test",
    )
    client = TestClient(app)

    response = client.post(
        "/feishu/events",
        json={
            "schema": "2.0",
            "header": {
                "event_id": "evt_001",
                "event_type": "im.message.receive_v1",
                "token": "verify-token",
            },
            "event": {
                "sender": {"sender_id": {"open_id": "ou_sender"}},
                "message": {
                    "message_id": "om_001",
                    "chat_id": "oc_chat",
                    "message_type": "text",
                    "content": '{"text":"帮我查一下订单 ord_100"}',
                },
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "platform": "feishu",
        "message_id": "om_001",
        "accepted": True,
    }
    token_request = requests[1]
    assert json.loads(token_request.content) == {
        "app_id": "cli_test",
        "app_secret": "secret_test",
    }
    reply_request = requests[2]
    assert reply_request.headers["authorization"] == "Bearer tenant-token"
    assert json.loads(reply_request.content) == {
        "msg_type": "text",
        "content": '{"text": "Order ord_100 is delivered."}',
    }


def test_event_callback_still_acknowledges_when_feishu_reply_fails() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "http://n8n.local/webhook/chat-agent-inbound":
            return httpx.Response(200, json={"reply": "Order ord_100 is delivered."})
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/im/v1/messages/om_001/reply":
            return httpx.Response(400, json={"code": 230001, "msg": "invalid message id"})
        return httpx.Response(404, json={"error": "unexpected url"})

    transport = httpx.MockTransport(handler)
    app = create_app(
        http_client=httpx.Client(transport=transport),
        n8n_webhook_url="http://n8n.local/webhook/chat-agent-inbound",
        feishu_app_id="cli_test",
        feishu_app_secret="secret_test",
    )
    client = TestClient(app)

    response = client.post(
        "/feishu/events",
        json={
            "schema": "2.0",
            "header": {
                "event_id": "evt_001",
                "event_type": "im.message.receive_v1",
                "token": "verify-token",
            },
            "event": {
                "sender": {"sender_id": {"open_id": "ou_sender"}},
                "message": {
                    "message_id": "om_001",
                    "chat_id": "oc_chat",
                    "message_type": "text",
                    "content": '{"text":"帮我查一下订单 ord_100"}',
                },
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "platform": "feishu",
        "message_id": "om_001",
        "accepted": True,
    }
    assert str(requests[0].url) == "http://n8n.local/webhook/chat-agent-inbound"


def test_long_connection_event_forwards_message_to_n8n() -> None:
    requests: list[httpx.Request] = []
    captured_listener: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "http://n8n.local/webhook/chat-agent-inbound":
            return httpx.Response(200, json={"reply": "Order ord_100 is delivered."})
        return httpx.Response(404, json={"error": "unexpected url"})

    def fake_long_connection_starter(**kwargs: object) -> object:
        captured_listener.update(kwargs)
        return object()

    transport = httpx.MockTransport(handler)
    app = create_app(
        http_client=httpx.Client(transport=transport),
        n8n_webhook_url="http://n8n.local/webhook/chat-agent-inbound",
        feishu_app_id="cli_test",
        feishu_app_secret="secret_test",
        feishu_event_mode="long_connection",
        long_connection_starter=fake_long_connection_starter,
    )

    with TestClient(app):
        assert captured_listener["app_id"] == "cli_test"
        assert captured_listener["app_secret"] == "secret_test"
        on_event = captured_listener["on_event"]
        assert callable(on_event)
        on_event(
            {
                "schema": "2.0",
                "header": {
                    "event_id": "evt_ws_001",
                    "event_type": "im.message.receive_v1",
                },
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_sender"}},
                    "message": {
                        "message_id": "om_ws_001",
                        "chat_id": "oc_chat",
                        "message_type": "text",
                        "content": '{"text":"帮我查一下订单 ord_100"}',
                    },
                },
            }
        )
        on_event(
            {
                "schema": "2.0",
                "header": {
                    "event_id": "evt_ws_001_retry",
                    "event_type": "im.message.receive_v1",
                },
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_sender"}},
                    "message": {
                        "message_id": "om_ws_001",
                        "chat_id": "oc_chat",
                        "message_type": "text",
                        "content": '{"text":"帮我查一下订单 ord_100"}',
                    },
                },
            }
        )

    n8n_requests = [
        request for request in requests if str(request.url) == "http://n8n.local/webhook/chat-agent-inbound"
    ]
    assert len(n8n_requests) == 1
    assert json.loads(n8n_requests[0].content) == {
        "platform": "feishu",
        "message_type": "text",
        "sender_id": "ou_sender",
        "chat_id": "oc_chat",
        "message_id": "om_ws_001",
        "text": "帮我查一下订单 ord_100",
        "audio_url": "",
        "media_id": "",
    }
