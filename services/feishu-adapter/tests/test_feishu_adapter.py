import json
import logging

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


def test_health_details_reports_gateway_configuration_without_secrets() -> None:
    bots_json = json.dumps(
        [
            {
                "name": "customer_support",
                "app_id": "cli_customer",
                "app_secret": "secret_customer",
                "bot_open_id": "ou_customer_bot",
                "n8n_webhook_url": "http://n8n.local/webhook/customer-support-inbound",
            },
            {
                "name": "warehouse",
                "app_id": "cli_warehouse",
                "app_secret": "secret_warehouse",
                "n8n_webhook_url": "http://n8n.local/webhook/warehouse-inbound",
            },
        ]
    )
    app = create_app(
        feishu_bots_json=bots_json,
        feishu_event_mode="long_connection",
        run_log_url="http://mock-api.local/run-logs",
        long_connection_starter=lambda **kwargs: object(),
    )
    with TestClient(app) as client:
        response = client.get("/health/details")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["event_mode"] == "long_connection"
    assert body["bot_count"] == 2
    assert body["listener_count"] == 2
    assert body["run_log_enabled"] is True
    assert body["processed_message_count"] == 0
    assert body["bots"] == [
        {
            "name": "customer_support",
            "n8n_webhook_url": "http://n8n.local/webhook/customer-support-inbound",
            "n8n_webhook_status": "configured",
            "has_app_id": True,
            "has_app_secret": True,
            "has_bot_open_id": True,
        },
        {
            "name": "warehouse",
            "n8n_webhook_url": "http://n8n.local/webhook/warehouse-inbound",
            "n8n_webhook_status": "configured",
            "has_app_id": True,
            "has_app_secret": True,
            "has_bot_open_id": False,
        },
    ]
    assert "secret_customer" not in json.dumps(body)


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


def test_event_callback_logs_n8n_and_total_latency(caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"reply": "Order ord_100 is delivered."})

    transport = httpx.MockTransport(handler)
    app = create_app(
        http_client=httpx.Client(transport=transport),
        n8n_webhook_url="http://n8n.local/webhook/chat-agent-inbound",
    )
    client = TestClient(app)

    with caplog.at_level(logging.INFO, logger="feishu_adapter"):
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
    assert "n8n_ms=" in caplog.text
    assert "total_ms=" in caplog.text


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


def test_event_callback_writes_structured_run_log() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "http://n8n.local/webhook/customer-support-inbound":
            return httpx.Response(
                200,
                json={
                    "reply": "Order ord_100 is delivered.",
                    "tool_trace": [
                        {
                            "tool": "order_status_tool",
                            "input": {"order_id": "ord_100"},
                            "output": {"status": "delivered"},
                        }
                    ],
                },
            )
        if str(request.url) == "http://mock-api.local/run-logs":
            return httpx.Response(200, json={"ok": True})
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/im/v1/messages/om_001/reply":
            return httpx.Response(200, json={"code": 0})
        return httpx.Response(404, json={"error": "unexpected url"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        feishu_bots_json=json.dumps(
            [
                {
                    "name": "customer_support",
                    "app_id": "cli_customer",
                    "app_secret": "secret_customer",
                    "n8n_webhook_url": "http://n8n.local/webhook/customer-support-inbound",
                }
            ]
        ),
        feishu_event_mode="long_connection",
        run_log_url="http://mock-api.local/run-logs",
        long_connection_starter=lambda **kwargs: object(),
    )

    with TestClient(app):
        app.state.feishu_long_connection_clients
        on_event = app.state.feishu_long_connection_clients
        assert on_event is not None

    response = TestClient(app).post(
        "/feishu/events",
        json={
            "schema": "2.0",
            "header": {"event_id": "evt_001", "event_type": "im.message.receive_v1"},
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
    run_log_requests = [request for request in requests if str(request.url) == "http://mock-api.local/run-logs"]
    assert len(run_log_requests) == 1
    run_log = json.loads(run_log_requests[0].content)
    assert run_log["event_id"] == "om_001"
    assert run_log["message_id"] == "om_001"
    assert run_log["bot_name"] == "customer_support"
    assert run_log["workflow"] == "http://n8n.local/webhook/customer-support-inbound"
    assert run_log["status"] == "succeeded"
    assert run_log["has_reply"] is True
    assert run_log["tool_calls"][0]["tool"] == "order_status_tool"
    assert run_log["latency_ms"] >= 0


def test_inventory_table_sync_returns_not_configured_without_table_settings() -> None:
    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
    )
    client = TestClient(app)

    response = client.post("/warehouse/inventory-table/sync", json={"sku": "sku_bag_1"})

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "configured": False,
        "error": "missing_feishu_inventory_table_config",
        "message": "Feishu inventory table sync is not configured.",
    }


def test_inventory_table_sync_creates_snapshot_record() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "http://mock-api.local/warehouse/inventory/sku_bag_1":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "sku": "sku_bag_1",
                    "product_name": "Canvas Bag",
                    "available": 5,
                    "reserved": 3,
                    "pending_orders": 9,
                    "risk_level": "high",
                    "recommendation": "库存或异常存在履约风险，建议仓库复核并通知采购。",
                    "locations": [
                        {
                            "warehouse_id": "wh_hk_1",
                            "location_code": "A-01-01",
                            "quantity": 5,
                            "status": "available",
                        }
                    ],
                    "open_exceptions": [{"exception_id": "wh_exc_1"}],
                },
            )
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url).startswith(
            "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/records?"
        ):
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/records":
            return httpx.Response(200, json={"code": 0, "data": {"record": {"record_id": "rec_new"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        feishu_api_base_url="https://open.feishu.cn",
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        inventory_table_id="tbl_inventory",
        inventory_table_url="https://example.feishu.cn/base/app_token?table=tbl_inventory",
        mock_api_url="http://mock-api.local",
    )
    client = TestClient(app)

    response = client.post("/warehouse/inventory-table/sync", json={"sku": "sku_bag_1"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["action"] == "created"
    assert body["record_id"] == "rec_new"
    assert body["table_url"] == "https://example.feishu.cn/base/app_token?table=tbl_inventory"
    create_request = requests[-1]
    assert json.loads(create_request.content)["fields"] == {
        "SKU": "sku_bag_1",
        "Product Name": "Canvas Bag",
        "Warehouse": "wh_hk_1",
        "Available": 5,
        "Reserved": 3,
        "Pending Orders": 9,
        "Risk Level": "high",
        "Open Exception Count": 1,
        "Recommendation": "库存或异常存在履约风险，建议仓库复核并通知采购。",
        "Last Synced At": body["last_synced_at"],
        "Sync Status": "synced",
        "Source Version": "mock-api:sku_bag_1:wh_hk_1",
    }


def test_inventory_table_sync_updates_existing_snapshot_record() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "http://mock-api.local/warehouse/inventory/sku_bag_1":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "sku": "sku_bag_1",
                    "available": 5,
                    "reserved": 3,
                    "pending_orders": 9,
                    "risk_level": "high",
                    "recommendation": "review stock",
                    "locations": [{"warehouse_id": "wh_hk_1"}],
                    "open_exceptions": [],
                },
            )
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url).startswith(
            "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/records?"
        ):
            return httpx.Response(200, json={"code": 0, "data": {"items": [{"record_id": "rec_existing"}]}})
        if str(request.url) == (
            "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/records/rec_existing"
        ):
            return httpx.Response(200, json={"code": 0, "data": {"record": {"record_id": "rec_existing"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        inventory_table_id="tbl_inventory",
        mock_api_url="http://mock-api.local",
    )
    client = TestClient(app)

    response = client.post("/warehouse/inventory-table/sync", json={"sku": "sku_bag_1"})

    assert response.status_code == 200
    assert response.json()["action"] == "updated"
    assert requests[-1].method == "PUT"


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


def test_multi_bot_long_connection_routes_each_bot_to_own_webhook_and_credentials() -> None:
    requests: list[httpx.Request] = []
    captured_listeners: list[dict[str, object]] = []
    bots_json = json.dumps(
        [
            {
                "name": "customer_support",
                "app_id": "cli_customer",
                "app_secret": "secret_customer",
                "n8n_webhook_url": "http://n8n.local/webhook/customer-support-inbound",
            },
            {
                "name": "warehouse",
                "app_id": "cli_warehouse",
                "app_secret": "secret_warehouse",
                "n8n_webhook_url": "http://n8n.local/webhook/warehouse-inbound",
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "http://n8n.local/webhook/customer-support-inbound":
            return httpx.Response(200, json={"reply": "customer reply"})
        if str(request.url) == "http://n8n.local/webhook/warehouse-inbound":
            return httpx.Response(200, json={"reply": "warehouse reply"})
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            content = json.loads(request.content)
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": f"token-{content['app_id']}"},
            )
        if str(request.url).startswith("https://open.feishu.cn/open-apis/im/v1/messages/"):
            return httpx.Response(200, json={"code": 0})
        return httpx.Response(404, json={"error": "unexpected url"})

    def fake_long_connection_starter(**kwargs: object) -> object:
        captured_listeners.append(kwargs)
        return object()

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        feishu_bots_json=bots_json,
        feishu_event_mode="long_connection",
        long_connection_starter=fake_long_connection_starter,
    )

    with TestClient(app):
        assert [listener["app_id"] for listener in captured_listeners] == [
            "cli_customer",
            "cli_warehouse",
        ]
        for listener in captured_listeners:
            on_event = listener["on_event"]
            assert callable(on_event)
            on_event(
                {
                    "schema": "2.0",
                    "header": {"event_id": f"evt_{listener['app_id']}"},
                    "event": {
                        "sender": {"sender_id": {"open_id": "ou_sender"}},
                        "message": {
                            "message_id": "om_shared",
                            "chat_id": "oc_chat",
                            "message_type": "text",
                            "content": '{"text":"test message"}',
                        },
                    },
                }
            )

    n8n_urls = [
        str(request.url)
        for request in requests
        if str(request.url).startswith("http://n8n.local/webhook/")
    ]
    assert n8n_urls == [
        "http://n8n.local/webhook/customer-support-inbound",
        "http://n8n.local/webhook/warehouse-inbound",
    ]

    token_requests = [
        json.loads(request.content)
        for request in requests
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    ]
    assert token_requests == [
        {"app_id": "cli_customer", "app_secret": "secret_customer"},
        {"app_id": "cli_warehouse", "app_secret": "secret_warehouse"},
    ]

    reply_requests = [
        request
        for request in requests
        if str(request.url).startswith("https://open.feishu.cn/open-apis/im/v1/messages/")
    ]
    assert [request.headers["authorization"] for request in reply_requests] == [
        "Bearer token-cli_customer",
        "Bearer token-cli_warehouse",
    ]
    assert [json.loads(request.content)["content"] for request in reply_requests] == [
        '{"text": "customer reply"}',
        '{"text": "warehouse reply"}',
    ]


def test_multi_bot_deduplication_is_scoped_by_bot_name() -> None:
    requests: list[httpx.Request] = []
    captured_listeners: list[dict[str, object]] = []
    bots_json = json.dumps(
        [
            {
                "name": "customer_support",
                "app_id": "cli_customer",
                "app_secret": "secret_customer",
                "n8n_webhook_url": "http://n8n.local/webhook/customer-support-inbound",
            },
            {
                "name": "warehouse",
                "app_id": "cli_warehouse",
                "app_secret": "secret_warehouse",
                "n8n_webhook_url": "http://n8n.local/webhook/warehouse-inbound",
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"reply": ""})

    def fake_long_connection_starter(**kwargs: object) -> object:
        captured_listeners.append(kwargs)
        return object()

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        feishu_bots_json=bots_json,
        feishu_event_mode="long_connection",
        long_connection_starter=fake_long_connection_starter,
    )
    payload = {
        "schema": "2.0",
        "header": {"event_id": "evt_same"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_sender"}},
            "message": {
                "message_id": "om_same",
                "chat_id": "oc_chat",
                "message_type": "text",
                "content": '{"text":"same id"}',
            },
        },
    }

    with TestClient(app):
        captured_listeners[0]["on_event"](payload)
        captured_listeners[0]["on_event"](payload)
        captured_listeners[1]["on_event"](payload)

    n8n_urls = [
        str(request.url)
        for request in requests
        if str(request.url).startswith("http://n8n.local/webhook/")
    ]
    assert n8n_urls == [
        "http://n8n.local/webhook/customer-support-inbound",
        "http://n8n.local/webhook/warehouse-inbound",
    ]


def test_multi_bot_group_message_routes_only_to_mentioned_bot() -> None:
    requests: list[httpx.Request] = []
    captured_listeners: list[dict[str, object]] = []
    bots_json = json.dumps(
        [
            {
                "name": "customer_support",
                "app_id": "cli_customer",
                "app_secret": "secret_customer",
                "bot_open_id": "ou_customer_bot",
                "n8n_webhook_url": "http://n8n.local/webhook/customer-support-inbound",
            },
            {
                "name": "warehouse",
                "app_id": "cli_warehouse",
                "app_secret": "secret_warehouse",
                "bot_open_id": "ou_warehouse_bot",
                "n8n_webhook_url": "http://n8n.local/webhook/warehouse-inbound",
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"reply": ""})

    def fake_long_connection_starter(**kwargs: object) -> object:
        captured_listeners.append(kwargs)
        return object()

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        feishu_bots_json=bots_json,
        feishu_event_mode="long_connection",
        long_connection_starter=fake_long_connection_starter,
    )
    payload = {
        "schema": "2.0",
        "header": {"event_id": "evt_group_mention"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_sender"}},
            "message": {
                "message_id": "om_group_mention",
                "chat_id": "oc_group",
                "chat_type": "group",
                "message_type": "text",
                "content": '{"text":"@Warehouse 查询 sku_100 库存"}',
                "mentions": [
                    {
                        "id": {"open_id": "ou_warehouse_bot"},
                        "name": "Warehouse",
                    }
                ],
            },
        },
    }

    with TestClient(app):
        for listener in captured_listeners:
            listener["on_event"](payload)

    n8n_urls = [
        str(request.url)
        for request in requests
        if str(request.url).startswith("http://n8n.local/webhook/")
    ]
    assert n8n_urls == ["http://n8n.local/webhook/warehouse-inbound"]


def test_multi_bot_group_message_without_mention_is_ignored() -> None:
    requests: list[httpx.Request] = []
    captured_listeners: list[dict[str, object]] = []
    bots_json = json.dumps(
        [
            {
                "name": "customer_support",
                "app_id": "cli_customer",
                "app_secret": "secret_customer",
                "bot_open_id": "ou_customer_bot",
                "n8n_webhook_url": "http://n8n.local/webhook/customer-support-inbound",
            },
            {
                "name": "warehouse",
                "app_id": "cli_warehouse",
                "app_secret": "secret_warehouse",
                "bot_open_id": "ou_warehouse_bot",
                "n8n_webhook_url": "http://n8n.local/webhook/warehouse-inbound",
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"reply": ""})

    def fake_long_connection_starter(**kwargs: object) -> object:
        captured_listeners.append(kwargs)
        return object()

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        feishu_bots_json=bots_json,
        feishu_event_mode="long_connection",
        long_connection_starter=fake_long_connection_starter,
    )
    payload = {
        "schema": "2.0",
        "header": {"event_id": "evt_group_no_mention"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_sender"}},
            "message": {
                "message_id": "om_group_no_mention",
                "chat_id": "oc_group",
                "chat_type": "group",
                "message_type": "text",
                "content": '{"text":"查询 sku_100 库存"}',
            },
        },
    }

    with TestClient(app):
        for listener in captured_listeners:
            listener["on_event"](payload)

    n8n_urls = [
        str(request.url)
        for request in requests
        if str(request.url).startswith("http://n8n.local/webhook/")
    ]
    assert n8n_urls == []
