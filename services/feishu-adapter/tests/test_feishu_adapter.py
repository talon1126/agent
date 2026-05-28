import json
import logging

import httpx
from fastapi.testclient import TestClient

from app.feishu_events import FeishuMessage, normalize_feishu_event
from app.main import INVENTORY_TABLE_FIELD_SPECS, create_app


def batch_inventory_table_rows_response() -> dict:
    return {
        "ok": True,
        "schema_id": "warehouse_batch_inventory",
        "count": 1,
        "items": [
            {
                "batch_key": "wh_sz_1:A1:item_vinda_tissue:BATCH-20260501",
                "item_id": "item_vinda_tissue",
                "batch_no": "BATCH-20260501",
                "fields": {
                    "Warehouse": "深圳仓",
                    "Warehouse ID": "wh_sz_1",
                    "Location": "A1",
                    "Category": "纸品",
                    "Category ID": "paper",
                    "Item ID": "item_vinda_tissue",
                    "Item Name": "维达纸巾",
                    "Brand": "维达",
                    "Spec": "3层抽纸 24包",
                    "Unit": "包",
                    "Batch No": "BATCH-20260501",
                    "Quantity On Hand": 120,
                    "Quantity Available": 96,
                    "Quantity Reserved": 24,
                    "Risk Level": "high",
                    "Last Synced At": "2026-05-24T00:00:00+00:00",
                    "Sync Status": "synced",
                    "Source Version": "mock-api:wh_sz_1:A1:item_vinda_tissue:BATCH-20260501",
                },
            }
        ],
    }


def batch_inventory_table_rows_response_for(batch_no: str, item_id: str = "item_vinda_tissue") -> dict:
    payload = batch_inventory_table_rows_response()
    row = payload["items"][0]
    row["batch_key"] = f"wh_sz_1:A1:{item_id}:{batch_no}"
    row["item_id"] = item_id
    row["batch_no"] = batch_no
    row["fields"]["Item ID"] = item_id
    row["fields"]["Batch No"] = batch_no
    row["fields"]["Source Version"] = f"mock-api:wh_sz_1:A1:{item_id}:{batch_no}"
    return payload


def procurement_replenishment_table_rows_response() -> dict:
    return {
        "ok": True,
        "schema_id": "procurement_replenishment_requests",
        "count": 1,
        "items": [
            {
                "request_id": "REQ-1001",
                "fields": {
                    "Request ID": "REQ-1001",
                    "Status": "pending_procurement_review",
                    "Warehouse": "深圳仓",
                    "Warehouse ID": "wh_sz_1",
                    "Location": "A1",
                    "Item ID": "item_vinda_tissue",
                    "Item Name": "维达纸巾",
                    "Suggested Quantity": 104,
                    "Last Synced At": "2026-05-26T00:00:00+00:00",
                    "Sync Status": "synced",
                    "Source Version": "mock-api:REQ-1001",
                },
            }
        ],
    }


def procurement_purchase_order_draft_table_rows_response() -> dict:
    return {
        "ok": True,
        "schema_id": "procurement_purchase_order_drafts",
        "count": 1,
        "items": [
            {
                "po_draft_id": "POD-5001",
                "request_id": "REQ-1001",
                "fields": {
                    "PO Draft ID": "POD-5001",
                    "Request ID": "REQ-1001",
                    "Status": "draft",
                    "Supplier ID": "supplier_paper_sz",
                    "Supplier Name": "深圳纸品供应商",
                    "Item ID": "item_vinda_tissue",
                    "Quantity": 104,
                    "Unit Price": 8,
                    "Currency": "CNY",
                    "Estimated Total Price": 832,
                    "Lead Time Days": 3,
                    "Estimated Arrival Date": "2026-05-29",
                    "Last Synced At": "2026-05-26T00:00:00+00:00",
                    "Sync Status": "synced",
                    "Source Version": "mock-api:POD-5001",
                },
            }
        ],
    }


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

    response = client.post("/warehouse/inventory-table/sync", json={"item_id": "item_vinda_tissue"})

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "configured": False,
        "error": "missing_feishu_inventory_table_config",
        "message": "Feishu inventory table sync is not configured.",
    }


def test_procurement_table_sync_returns_not_configured_without_table_settings() -> None:
    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
    )
    client = TestClient(app)

    request_response = client.post("/procurement/replenishment-requests-table/sync", json={})
    draft_response = client.post("/procurement/purchase-order-drafts-table/sync", json={})

    assert request_response.status_code == 200
    assert request_response.json() == {
        "ok": False,
        "configured": False,
        "error": "missing_feishu_procurement_table_config",
        "message": "Feishu procurement table sync is not configured.",
    }
    assert draft_response.status_code == 200
    assert draft_response.json() == {
        "ok": False,
        "configured": False,
        "error": "missing_feishu_procurement_table_config",
        "message": "Feishu procurement table sync is not configured.",
    }


def test_inventory_table_provision_returns_not_configured_without_app_token() -> None:
    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
    )
    client = TestClient(app)

    response = client.post("/warehouse/inventory-table/provision", json={})

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "configured": False,
        "error": "missing_feishu_inventory_table_provision_config",
        "message": "Feishu inventory table provisioning requires app credentials and app token.",
    }


def test_inventory_table_provision_returns_existing_table_when_table_id_is_configured() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_existing/fields":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {"field_id": "fld_sku", "field_name": "SKU", "type": 1, "property": None},
                            {"field_id": "fld_product", "field_name": "Product Name", "type": 1},
                            {"field_id": "fld_warehouse", "field_name": "Warehouse", "type": 1},
                            {"field_id": "fld_available", "field_name": "Available", "type": 2},
                            {"field_id": "fld_reserved", "field_name": "Reserved", "type": 2},
                            {"field_id": "fld_pending", "field_name": "Pending Orders", "type": 2},
                            {
                                "field_id": "fld_risk",
                                "field_name": "Risk Level",
                                "type": 3,
                                "property": {
                                    "options": [
                                        {"name": "low", "color": 28},
                                        {"name": "medium", "color": 24},
                                        {"name": "high", "color": 17},
                                        {"name": "unknown", "color": 0},
                                    ]
                                },
                            },
                            {"field_id": "fld_exc", "field_name": "Open Exception Count", "type": 2},
                            {"field_id": "fld_rec", "field_name": "Recommendation", "type": 1},
                            {"field_id": "fld_sync_at", "field_name": "Last Synced At", "type": 1},
                            {
                                "field_id": "fld_status",
                                "field_name": "Sync Status",
                                "type": 3,
                                "property": {
                                    "options": [
                                        {"name": "synced", "color": 28},
                                        {"name": "pending", "color": 24},
                                        {"name": "failed", "color": 17},
                                    ]
                                },
                            },
                            {"field_id": "fld_source", "field_name": "Source Version", "type": 1},
                        ]
                    },
                },
            )
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        inventory_table_id="tbl_existing",
        inventory_table_view_id="vew_existing",
        inventory_table_url="https://example.feishu.cn/base/app_token?table=tbl_existing",
    )
    client = TestClient(app)

    response = client.post("/warehouse/inventory-table/provision", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["configured"] is True
    assert body["action"] == "existing"
    assert body["table_id"] == "tbl_existing"
    assert body["view_id"] == "vew_existing"
    assert body["table_url"] == "https://example.feishu.cn/base/app_token?table=tbl_existing"
    assert "Item ID" in body["fields"]
    assert "Batch No" in body["fields"]
    assert "Risk Level" in body["fields"]
    assert str(requests[0].url).endswith("/warehouse/inventory/table-schema")


def test_inventory_table_provision_creates_inventory_table_with_fixed_schema() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "table_id": "tbl_inventory",
                        "default_view_id": "vew_inventory",
                    },
                },
            )
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields":
            return httpx.Response(200, json={"code": 0, "data": {"field": {"field_id": "fld_created"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        feishu_api_base_url="https://open.feishu.cn",
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
    )
    client = TestClient(app)

    response = client.post(
        "/warehouse/inventory-table/provision",
        json={"table_name": "Warehouse Inventory Snapshot"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["configured"] is True
    assert body["action"] == "created"
    assert body["table_id"] == "tbl_inventory"
    assert body["view_id"] == "vew_inventory"
    assert body["table_url"] == ""
    create_request = next(
        request
        for request in requests
        if request.method == "POST"
        and str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables"
    )
    assert create_request.headers["authorization"] == "Bearer tenant-token"
    create_body = json.loads(create_request.content)
    assert create_body["table"]["name"] == "Warehouse Inventory Snapshot"
    field_requests = [
        request
        for request in requests
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields"
    ]
    assert len(field_requests) == len(INVENTORY_TABLE_FIELD_SPECS)
    field_names = [json.loads(request.content)["field_name"] for request in field_requests]
    assert field_names == [field["field_name"] for field in INVENTORY_TABLE_FIELD_SPECS]
    assert "SKU" not in field_names
    assert {"Warehouse ID", "Location", "Item ID", "Batch No", "Quantity Available"}.issubset(field_names)
    field_bodies = [json.loads(request.content) for request in field_requests]
    risk_field = next(field for field in field_bodies if field["field_name"] == "Risk Level")
    sync_field = next(field for field in field_bodies if field["field_name"] == "Sync Status")
    assert risk_field == {
        "field_name": "Risk Level",
        "type": 3,
        "property": {
            "options": [
                {"name": "low", "color": 28},
                {"name": "medium", "color": 24},
                {"name": "high", "color": 17},
                {"name": "unknown", "color": 0},
            ]
        },
    }
    assert sync_field == {
        "field_name": "Sync Status",
        "type": 3,
        "property": {
            "options": [
                {"name": "synced", "color": 28},
                {"name": "pending", "color": 24},
                {"name": "failed", "color": 17},
            ]
        },
    }
    assert body["fields"] == [field["field_name"] for field in INVENTORY_TABLE_FIELD_SPECS]


def test_inventory_table_provision_uses_backend_schema_when_available() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "http://mock-api.local/warehouse/inventory/table-schema":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "schema_id": "warehouse_inventory_snapshot",
                    "fields": [
                        {
                            "name": "SKU",
                            "type": "text",
                            "source": "warehouse_inventory.sku",
                            "comment": "商品 SKU",
                        },
                        {
                            "name": "Risk Level",
                            "type": "single_select",
                            "source": "computed.risk_level",
                            "comment": "风险等级",
                            "options": [{"name": "high", "color": 17}],
                        },
                        {
                            "name": "Backend Only Metric",
                            "type": "number",
                            "source": "computed.backend_only_metric",
                            "comment": "后端新增指标",
                        },
                    ],
                },
            )
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"table_id": "tbl_inventory", "default_view_id": "vew_inventory"}},
            )
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields":
            return httpx.Response(200, json={"code": 0, "data": {"field": {"field_id": "fld_created"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        mock_api_url="http://mock-api.local",
    )
    client = TestClient(app)

    response = client.post("/warehouse/inventory-table/provision", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["fields"] == ["SKU", "Risk Level", "Backend Only Metric"]
    field_requests = [
        json.loads(request.content)
        for request in requests
        if request.method == "POST"
        and str(request.url).endswith("/tables/tbl_inventory/fields")
    ]
    assert field_requests == [
        {"field_name": "SKU", "type": 1},
        {
            "field_name": "Risk Level",
            "type": 3,
            "property": {"options": [{"name": "high", "color": 17}]},
        },
        {"field_name": "Backend Only Metric", "type": 2},
    ]


def test_procurement_replenishment_table_provision_creates_table_from_backend_schema() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url == "http://mock-api.local/procurement/replenishment-requests/table-schema":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "schema_id": "procurement_replenishment_requests",
                    "fields": [
                        {"name": "Request ID", "type": "text"},
                        {
                            "name": "Status",
                            "type": "single_select",
                            "options": [{"name": "pending_procurement_review", "color": 24}],
                        },
                        {"name": "Suggested Quantity", "type": "number"},
                    ],
                },
            )
        if url == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"table_id": "tbl_replenishment", "default_view_id": "vew_replenishment"}},
            )
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_replenishment/fields":
            return httpx.Response(200, json={"code": 0, "data": {"field": {"field_id": "fld_created"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        feishu_api_base_url="https://open.feishu.cn",
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        mock_api_url="http://mock-api.local",
    )
    client = TestClient(app)

    response = client.post("/procurement/replenishment-requests-table/provision", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["action"] == "created"
    assert body["table_id"] == "tbl_replenishment"
    assert body["table_name"] == "Procurement Replenishment Requests"
    field_requests = [
        json.loads(request.content)
        for request in requests
        if request.method == "POST" and str(request.url).endswith("/tables/tbl_replenishment/fields")
    ]
    assert field_requests == [
        {"field_name": "Request ID", "type": 1},
        {
            "field_name": "Status",
            "type": 3,
            "property": {"options": [{"name": "pending_procurement_review", "color": 24}]},
        },
        {"field_name": "Suggested Quantity", "type": 2},
    ]


def test_procurement_purchase_order_draft_table_provision_reuses_configured_table() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url == "http://mock-api.local/procurement/purchase-order-drafts/table-schema":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "fields": [
                        {"name": "PO Draft ID", "type": "text"},
                        {"name": "Estimated Arrival Date", "type": "text"},
                    ],
                },
            )
        if url == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_po/fields":
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        procurement_purchase_order_draft_table_id="tbl_po",
        procurement_purchase_order_draft_table_view_id="vew_po",
        procurement_purchase_order_draft_table_url="https://example.feishu.cn/base/app_token?table=tbl_po",
        mock_api_url="http://mock-api.local",
    )
    client = TestClient(app)

    response = client.post("/procurement/purchase-order-drafts-table/provision", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["action"] == "existing"
    assert body["table_id"] == "tbl_po"
    assert body["view_id"] == "vew_po"
    assert body["table_url"] == "https://example.feishu.cn/base/app_token?table=tbl_po"
    assert any(
        str(request.url).endswith("/procurement/purchase-order-drafts/table-schema")
        for request in requests
    )


def test_inventory_table_provision_reuses_duplicate_table_and_creates_missing_fields() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables":
            if request.method == "POST":
                return httpx.Response(200, json={"code": 1254013, "msg": "TableNameDuplicated"})
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "table_id": "tbl_existing",
                                "name": "Warehouse Inventory Snapshot",
                            }
                        ]
                    },
                },
            )
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_existing/fields":
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={"code": 0, "data": {"items": [{"field_name": "SKU", "type": 1}]}},
                )
            return httpx.Response(200, json={"code": 0, "data": {"field": {"field_id": "fld_created"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        feishu_api_base_url="https://open.feishu.cn",
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
    )
    client = TestClient(app)

    response = client.post("/warehouse/inventory-table/provision", json={})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["action"] == "existing"
    assert response.json()["table_id"] == "tbl_existing"
    field_create_requests = [
        request
        for request in requests
        if request.method == "POST"
        and str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_existing/fields"
    ]
    assert len(field_create_requests) == len(INVENTORY_TABLE_FIELD_SPECS)
    assert "SKU" not in [json.loads(request.content)["field_name"] for request in field_create_requests]


def test_inventory_table_provision_updates_existing_text_fields_to_colored_single_selects() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_existing/fields":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {"field_id": "fld_sku", "field_name": "SKU", "type": 1},
                            {"field_id": "fld_risk", "field_name": "Risk Level", "type": 1},
                            {"field_id": "fld_status", "field_name": "Sync Status", "type": 1},
                        ]
                    },
                },
            )
        if url in {
            "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_existing/fields/fld_risk",
            "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_existing/fields/fld_status",
        }:
            return httpx.Response(200, json={"code": 0, "data": {"field": {"field_id": url.rsplit('/', 1)[-1]}}})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_existing/fields":
            return httpx.Response(200, json={"code": 0, "data": {"field": {"field_id": "fld_created"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        feishu_api_base_url="https://open.feishu.cn",
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        inventory_table_id="tbl_existing",
    )
    client = TestClient(app)

    response = client.post("/warehouse/inventory-table/provision", json={})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    update_requests = [request for request in requests if request.method == "PUT"]
    assert [str(request.url) for request in update_requests] == [
        "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_existing/fields/fld_risk",
        "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_existing/fields/fld_status",
    ]
    assert json.loads(update_requests[0].content) == {
        "field_name": "Risk Level",
        "type": 3,
        "property": {
            "options": [
                {"name": "low", "color": 28},
                {"name": "medium", "color": 24},
                {"name": "high", "color": 17},
                {"name": "unknown", "color": 0},
            ]
        },
    }
    assert json.loads(update_requests[1].content) == {
        "field_name": "Sync Status",
        "type": 3,
        "property": {
            "options": [
                {"name": "synced", "color": 28},
                {"name": "pending", "color": 24},
                {"name": "failed", "color": 17},
            ]
        },
    }


def test_inventory_table_provision_treats_field_data_not_change_as_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_existing/fields":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {"field_id": "fld_risk", "field_name": "Risk Level", "type": 1},
                        ]
                    },
                },
            )
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_existing/fields/fld_risk":
            return httpx.Response(200, json={"code": 1254606, "msg": "DataNotChange"})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_existing/fields":
            return httpx.Response(200, json={"code": 0, "data": {"field": {"field_id": "fld_created"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        feishu_api_base_url="https://open.feishu.cn",
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        inventory_table_id="tbl_existing",
    )
    client = TestClient(app)

    response = client.post("/warehouse/inventory-table/provision", json={})

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_inventory_table_sync_auto_provisions_table_when_table_id_is_missing() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url == "http://mock-api.local/warehouse/inventory/table-rows":
            return httpx.Response(200, json=batch_inventory_table_rows_response())
        if url == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables":
            if request.method == "GET":
                return httpx.Response(200, json={"code": 0, "data": {"items": []}})
            return httpx.Response(
                200,
                json={"code": 0, "data": {"table_id": "tbl_auto", "default_view_id": "vew_auto"}},
            )
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_auto/fields":
            return httpx.Response(200, json={"code": 0, "data": {"field": {"field_id": "fld_created"}}})
        if url.startswith("https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_auto/records?"):
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_auto/records":
            return httpx.Response(200, json={"code": 0, "data": {"record": {"record_id": "rec_auto"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        feishu_api_base_url="https://open.feishu.cn",
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        mock_api_url="http://mock-api.local",
    )
    client = TestClient(app)

    response = client.post("/warehouse/inventory-table/sync", json={"item_id": "item_vinda_tissue"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["action"] == "created"
    assert body["table_id"] == "tbl_auto"
    assert body["record_id"] == "rec_auto"


def test_inventory_table_sync_filter_recreates_table_when_configured_table_was_deleted() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url == "http://mock-api.local/warehouse/inventory/table-rows":
            return httpx.Response(200, json=batch_inventory_table_rows_response())
        if url == "http://mock-api.local/warehouse/inventory/table-schema":
            return httpx.Response(200, json={"ok": False, "fields": []})
        if url == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_deleted/fields":
            return httpx.Response(200, json={"code": 1254041, "msg": "TableIdNotFound"})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables":
            if request.method == "GET":
                return httpx.Response(200, json={"code": 0, "data": {"items": []}})
            return httpx.Response(
                200,
                json={"code": 0, "data": {"table_id": "tbl_recreated", "default_view_id": "vew_recreated"}},
            )
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_recreated/fields":
            return httpx.Response(200, json={"code": 0, "data": {"field": {"field_id": "fld_created"}}})
        if url.startswith("https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_recreated/records?"):
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_recreated/records":
            return httpx.Response(200, json={"code": 0, "data": {"record": {"record_id": "rec_recreated"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        feishu_api_base_url="https://open.feishu.cn",
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        inventory_table_id="tbl_deleted",
        mock_api_url="http://mock-api.local",
    )
    client = TestClient(app)

    response = client.post(
        "/warehouse/inventory-table/sync/filter",
        json={
            "item_id": "item_vinda_tissue",
            "warehouse_id": "wh_sz_1",
            "location_code": "A1",
            "batch_no": "BATCH-20260501",
            "limit": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["table_id"] == "tbl_recreated"
    assert body["synced_count"] == 1
    assert body["items"][0]["action"] == "created"
    assert body["items"][0]["record_id"] == "rec_recreated"
    assert any("/tables/tbl_deleted/fields" in str(request.url) for request in requests)
    assert any(request.method == "POST" and str(request.url).endswith("/tables") for request in requests)


def test_inventory_table_sync_jobs_batches_feishu_setup_and_writes() -> None:
    requests: list[httpx.Request] = []
    fields_payload = {
        "code": 0,
        "data": {
            "items": [
                {
                    "field_id": f"fld_{index}",
                    "field_name": field["field_name"],
                    "type": field["type"],
                    "property": field.get("property", {}),
                }
                for index, field in enumerate(INVENTORY_TABLE_FIELD_SPECS)
            ]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url == "http://mock-api.local/warehouse/inventory/table-rows":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json=batch_inventory_table_rows_response_for(
                    body["batch_no"],
                    body["item_id"],
                ),
            )
        if url == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "name": "库存表",
                                "table_id": "tbl_inventory",
                                "default_view_id": "vew_inventory",
                            }
                        ]
                    },
                },
            )
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields":
            return httpx.Response(200, json=fields_payload)
        if url.startswith("https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/records?"):
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/records/batch_create":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "records": [
                            {"record_id": "rec_batch_1"},
                            {"record_id": "rec_batch_2"},
                        ]
                    },
                },
            )
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        feishu_api_base_url="https://open.feishu.cn",
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        mock_api_url="http://mock-api.local",
    )
    client = TestClient(app)

    response = client.post(
        "/warehouse/inventory-table/sync/jobs",
        json={
            "jobs": [
                {
                    "job_id": "WSJ-POD-6001",
                    "item_id": "item_vinda_tissue",
                    "warehouse_id": "wh_sz_1",
                    "location_code": "A1",
                    "batch_no": "RCV-POD-6001",
                },
                {
                    "job_id": "WSJ-POD-6002",
                    "item_id": "item_vinda_tissue",
                    "warehouse_id": "wh_sz_1",
                    "location_code": "A1",
                    "batch_no": "RCV-POD-6002",
                },
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["processed_count"] == 2
    assert body["completed_count"] == 2
    assert body["failed_count"] == 0
    assert [item["job_id"] for item in body["completed"]] == ["WSJ-POD-6001", "WSJ-POD-6002"]
    assert [item["sync"]["items"][0]["record_id"] for item in body["completed"]] == [
        "rec_batch_1",
        "rec_batch_2",
    ]
    assert sum(str(request.url).endswith("/tenant_access_token/internal") for request in requests) == 1
    assert sum(str(request.url).endswith("/tables/tbl_inventory/fields") for request in requests) == 2
    assert sum("/tables/tbl_inventory/records?" in str(request.url) for request in requests) == 1
    create_requests = [
        request
        for request in requests
        if str(request.url).endswith("/tables/tbl_inventory/records/batch_create")
    ]
    assert len(create_requests) == 1
    assert len(json.loads(create_requests[0].content)["records"]) == 2


def test_inventory_table_sync_jobs_chunks_existing_record_lookup_filter() -> None:
    requests: list[httpx.Request] = []
    fields_payload = {
        "code": 0,
        "data": {
            "items": [
                {
                    "field_id": f"fld_{index}",
                    "field_name": field["field_name"],
                    "type": field["type"],
                    "property": field.get("property", {}),
                }
                for index, field in enumerate(INVENTORY_TABLE_FIELD_SPECS)
            ]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url == "http://mock-api.local/warehouse/inventory/table-rows":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json=batch_inventory_table_rows_response_for(
                    body["batch_no"],
                    body["item_id"],
                ),
            )
        if url == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "name": "库存表",
                                "table_id": "tbl_inventory",
                                "default_view_id": "vew_inventory",
                            }
                        ]
                    },
                },
            )
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields":
            return httpx.Response(200, json=fields_payload)
        if url.startswith("https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/records?"):
            filter_value = str(request.url.params.get("filter", ""))
            if len(filter_value) > 1000:
                return httpx.Response(
                    200,
                    json={
                        "code": 1254107,
                        "msg": "FilterLengthExceedLimit",
                        "data": {},
                    },
                )
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/records/batch_create":
            records = json.loads(request.content)["records"]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "records": [
                            {"record_id": f"rec_batch_{index + 1}"}
                            for index, _record in enumerate(records)
                        ]
                    },
                },
            )
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        feishu_api_base_url="https://open.feishu.cn",
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        mock_api_url="http://mock-api.local",
    )
    client = TestClient(app)

    response = client.post(
        "/warehouse/inventory-table/sync/jobs",
        json={
            "jobs": [
                {
                    "job_id": f"WSJ-POD-{7000 + index}",
                    "item_id": "item_vinda_tissue",
                    "warehouse_id": "wh_sz_1",
                    "location_code": "A1",
                    "batch_no": f"RCV-POD-{7000 + index}",
                }
                for index in range(20)
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["processed_count"] == 20
    assert body["completed_count"] == 20
    assert body["failed_count"] == 0
    lookup_requests = [
        request
        for request in requests
        if request.method == "GET" and "/tables/tbl_inventory/records?" in str(request.url)
    ]
    assert len(lookup_requests) > 1
    assert all(len(str(request.url.params.get("filter", ""))) <= 1000 for request in lookup_requests)
    create_requests = [
        request
        for request in requests
        if str(request.url).endswith("/tables/tbl_inventory/records/batch_create")
    ]
    assert len(create_requests) == 1
    assert len(json.loads(create_requests[0].content)["records"]) == 20


def test_inventory_table_sync_creates_snapshot_record() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "http://mock-api.local/warehouse/inventory/table-rows":
            return httpx.Response(200, json=batch_inventory_table_rows_response())
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields":
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
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

    response = client.post("/warehouse/inventory-table/sync", json={"item_id": "item_vinda_tissue"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["action"] == "created"
    assert body["record_id"] == "rec_new"
    assert body["table_url"] == "https://example.feishu.cn/base/app_token?table=tbl_inventory"
    create_request = requests[-1]
    assert json.loads(create_request.content)["fields"] == {
        "Warehouse": "深圳仓",
        "Warehouse ID": "wh_sz_1",
        "Location": "A1",
        "Category": "纸品",
        "Category ID": "paper",
        "Item ID": "item_vinda_tissue",
        "Item Name": "维达纸巾",
        "Brand": "维达",
        "Spec": "3层抽纸 24包",
        "Unit": "包",
        "Batch No": "BATCH-20260501",
        "Quantity On Hand": 120,
        "Quantity Available": 96,
        "Quantity Reserved": 24,
        "Risk Level": "high",
        "Last Synced At": "2026-05-24T00:00:00+00:00",
        "Sync Status": "synced",
        "Source Version": "mock-api:wh_sz_1:A1:item_vinda_tissue:BATCH-20260501",
    }
    assert body["item_id"] == "item_vinda_tissue"
    assert body["batch_key"] == "wh_sz_1:A1:item_vinda_tissue:BATCH-20260501"
    assert body["warehouse_id"] == "wh_sz_1"
    assert body["location_code"] == "A1"
    assert body["batch_no"] == "BATCH-20260501"


def test_inventory_table_sync_updates_existing_snapshot_record() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "http://mock-api.local/warehouse/inventory/table-rows":
            return httpx.Response(200, json=batch_inventory_table_rows_response())
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields":
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
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

    response = client.post("/warehouse/inventory-table/sync", json={"item_id": "item_vinda_tissue"})

    assert response.status_code == 200
    assert response.json()["action"] == "updated"
    assert requests[-1].method == "PUT"


def test_procurement_replenishment_request_table_sync_upserts_by_request_id() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url == "http://mock-api.local/procurement/replenishment-requests/table-schema":
            return httpx.Response(
                200,
                json={"ok": True, "fields": [{"name": "Request ID", "type": "text"}]},
            )
        if url == "http://mock-api.local/procurement/replenishment-requests/table-rows":
            return httpx.Response(200, json=procurement_replenishment_table_rows_response())
        if url == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_req/fields":
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if url.startswith("https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_req/records?"):
            return httpx.Response(200, json={"code": 0, "data": {"items": [{"record_id": "rec_req"}]}})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_req/records/rec_req":
            return httpx.Response(200, json={"code": 0, "data": {"record": {"record_id": "rec_req"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        procurement_replenishment_request_table_id="tbl_req",
        mock_api_url="http://mock-api.local",
    )
    client = TestClient(app)

    response = client.post("/procurement/replenishment-requests-table/sync", json={"status": "pending_procurement_review"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["synced_count"] == 1
    assert body["items"] == [
        {
            "request_id": "REQ-1001",
            "status": "pending_procurement_review",
            "action": "updated",
            "record_id": "rec_req",
            "source_version": "mock-api:REQ-1001",
        }
    ]
    lookup_request = next(
        request for request in requests if request.method == "GET" and "/records?" in str(request.url)
    )
    assert 'CurrentValue.[Request ID]="REQ-1001"' in str(lookup_request.url.params["filter"])
    assert requests[-1].method == "PUT"


def test_procurement_purchase_order_draft_table_sync_upserts_by_po_draft_id() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url == "http://mock-api.local/procurement/purchase-order-drafts/table-schema":
            return httpx.Response(
                200,
                json={"ok": True, "fields": [{"name": "PO Draft ID", "type": "text"}]},
            )
        if url == "http://mock-api.local/procurement/purchase-order-drafts/table-rows":
            return httpx.Response(200, json=procurement_purchase_order_draft_table_rows_response())
        if url == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_po/fields":
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if url.startswith("https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_po/records?"):
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_po/records":
            return httpx.Response(200, json={"code": 0, "data": {"record": {"record_id": "rec_po"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        procurement_purchase_order_draft_table_id="tbl_po",
        mock_api_url="http://mock-api.local",
    )
    client = TestClient(app)

    response = client.post("/procurement/purchase-order-drafts-table/sync", json={"request_id": "REQ-1001"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["synced_count"] == 1
    assert body["items"] == [
        {
            "po_draft_id": "POD-5001",
            "request_id": "REQ-1001",
            "status": "draft",
            "action": "created",
            "record_id": "rec_po",
            "source_version": "mock-api:POD-5001",
        }
    ]
    lookup_request = next(
        request for request in requests if request.method == "GET" and "/records?" in str(request.url)
    )
    assert 'CurrentValue.[PO Draft ID]="POD-5001"' in str(lookup_request.url.params["filter"])
    create_request = next(
        request for request in requests if request.method == "POST" and str(request.url).endswith("/records")
    )
    assert json.loads(create_request.content)["fields"]["Estimated Arrival Date"] == "2026-05-29"


def test_inventory_table_sync_filter_updates_matching_inventory_records() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "http://mock-api.local/warehouse/inventory/table-rows":
            second_response = batch_inventory_table_rows_response()
            second_item = {
                **second_response["items"][0],
                "batch_key": "wh_sz_1:B1:item_vinda_tissue:BATCH-20260401",
                "batch_no": "BATCH-20260401",
                "fields": {
                    **second_response["items"][0]["fields"],
                    "Location": "B1",
                    "Batch No": "BATCH-20260401",
                    "Quantity On Hand": 16,
                    "Quantity Available": 12,
                    "Quantity Reserved": 4,
                    "Source Version": "mock-api:wh_sz_1:B1:item_vinda_tissue:BATCH-20260401",
                },
            }
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "schema_id": "warehouse_batch_inventory",
                    "count": 2,
                    "items": [batch_inventory_table_rows_response()["items"][0], second_item],
                },
            )
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields":
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if str(request.url).startswith(
            "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/records?"
        ):
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/records":
            return httpx.Response(200, json={"code": 0, "data": {"record": {"record_id": "rec_new"}}})
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

    response = client.post(
        "/warehouse/inventory-table/sync/filter",
        json={"warehouse_id": "wh_sz_1", "category": "paper", "risk_level": "high"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["synced_count"] == 2
    assert body["warehouse_id"] == "wh_sz_1"
    assert body["category"] == "paper"
    assert body["risk_level"] == "high"
    assert [item["batch_no"] for item in body["items"]] == ["BATCH-20260501", "BATCH-20260401"]
    create_requests = [
        request
        for request in requests
        if request.method == "POST" and str(request.url).endswith("/records")
    ]
    assert len(create_requests) == 2


def test_inventory_table_sync_filter_batch_creates_records_with_single_select_option_ids() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url == "http://mock-api.local/warehouse/inventory/table-rows":
            second_response = batch_inventory_table_rows_response()
            first_item = {
                **second_response["items"][0],
                "fields": {
                    **second_response["items"][0]["fields"],
                    "Expiry Risk": "normal",
                    "Risk Level": "high",
                    "Storage Status": "available",
                    "Sync Status": "synced",
                },
            }
            second_item = {
                **second_response["items"][0],
                "batch_key": "wh_sz_1:B1:item_vinda_tissue:BATCH-20260401",
                "batch_no": "BATCH-20260401",
                "fields": {
                    **second_response["items"][0]["fields"],
                    "Location": "B1",
                    "Batch No": "BATCH-20260401",
                    "Expiry Risk": "expiring_soon",
                    "Risk Level": "medium",
                    "Storage Status": "quality_hold",
                    "Sync Status": "synced",
                    "Source Version": "mock-api:wh_sz_1:B1:item_vinda_tissue:BATCH-20260401",
                },
            }
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "schema_id": "warehouse_batch_inventory",
                    "count": 2,
                    "items": [first_item, second_item],
                },
            )
        if url == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables":
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields":
            if request.method == "POST":
                return httpx.Response(200, json={"code": 0, "data": {"field": {"field_id": "fld_created"}}})
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "field_name": "Expiry Risk",
                                "type": 3,
                                "property": {
                                    "options": [
                                        {"id": "opt_exp_normal", "name": "normal", "color": 28},
                                        {"id": "opt_exp_soon", "name": "expiring_soon", "color": 24},
                                    ]
                                },
                            },
                            {
                                "field_name": "Risk Level",
                                "type": 3,
                                "property": {
                                    "options": [
                                        {"id": "opt_risk_high", "name": "high", "color": 17},
                                        {"id": "opt_risk_medium", "name": "medium", "color": 24},
                                    ]
                                },
                            },
                            {
                                "field_name": "Storage Status",
                                "type": 3,
                                "property": {
                                    "options": [
                                        {"id": "opt_storage_available", "name": "available", "color": 28},
                                        {"id": "opt_storage_hold", "name": "quality_hold", "color": 17},
                                    ]
                                },
                            },
                            {
                                "field_name": "Sync Status",
                                "type": 3,
                                "property": {
                                    "options": [
                                        {"id": "opt_sync_synced", "name": "synced", "color": 28},
                                    ]
                                },
                            },
                        ]
                    },
                },
            )
        if url.startswith("https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/records?"):
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/records/batch_create":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "records": [
                            {"record_id": "rec_first"},
                            {"record_id": "rec_second"},
                        ]
                    },
                },
            )
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

    response = client.post(
        "/warehouse/inventory-table/sync/filter",
        json={"warehouse_id": "wh_sz_1", "category": "paper", "limit": 2},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["synced_count"] == 2
    batch_create_request = next(
        request for request in requests if request.method == "POST" and str(request.url).endswith("/records/batch_create")
    )
    records = json.loads(batch_create_request.content)["records"]
    assert [record["fields"]["Risk Level"] for record in records] == ["high", "medium"]
    assert [record["fields"]["Expiry Risk"] for record in records] == ["normal", "expiring_soon"]
    assert [record["fields"]["Storage Status"] for record in records] == [
        "available",
        "quality_hold",
    ]
    assert [record["fields"]["Sync Status"] for record in records] == ["synced", "synced"]
    assert body["items"][0]["record_id"] == "rec_first"
    assert body["items"][1]["record_id"] == "rec_second"


def test_inventory_table_sync_filter_prefers_exact_table_over_configured_conflict_table() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url == "http://mock-api.local/warehouse/inventory/table-rows":
            return httpx.Response(200, json=batch_inventory_table_rows_response())
        if url == "http://mock-api.local/warehouse/inventory/table-schema":
            return httpx.Response(200, json={"ok": False, "fields": []})
        if url == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "table_id": "tbl_conflict",
                                "name": "Warehouse Inventory Snapshot_conflict_tblAAA",
                            },
                            {
                                "table_id": "tbl_canonical",
                                "name": "Warehouse Inventory Snapshot",
                            },
                        ]
                    },
                },
            )
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_canonical/fields":
            if request.method == "POST":
                return httpx.Response(200, json={"code": 0, "data": {"field": {"field_id": "fld_created"}}})
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if url.startswith("https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_canonical/records?"):
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_canonical/records/batch_create":
            return httpx.Response(200, json={"code": 0, "data": {"records": [{"record_id": "rec_canonical"}]}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        inventory_table_id="tbl_conflict",
        mock_api_url="http://mock-api.local",
    )
    client = TestClient(app)

    response = client.post(
        "/warehouse/inventory-table/sync/filter",
        json={
            "item_id": "item_vinda_tissue",
            "warehouse_id": "wh_sz_1",
            "location_code": "A1",
            "batch_no": "BATCH-20260501",
            "limit": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["table_id"] == "tbl_canonical"
    assert body["items"][0]["record_id"] == "rec_canonical"
    assert not any("/tables/tbl_conflict/" in str(request.url) for request in requests)
    assert not any(request.method == "POST" and str(request.url).endswith("/tables") for request in requests)


def test_inventory_table_sync_filter_prefers_existing_chinese_inventory_table_alias() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url == "http://mock-api.local/warehouse/inventory/table-rows":
            return httpx.Response(200, json=batch_inventory_table_rows_response())
        if url == "http://mock-api.local/warehouse/inventory/table-schema":
            return httpx.Response(200, json={"ok": False, "fields": []})
        if url == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "table_id": "tbl_chinese",
                                "name": "库存表",
                            },
                            {
                                "table_id": "tbl_english",
                                "name": "Warehouse Inventory Snapshot",
                            },
                        ]
                    },
                },
            )
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_chinese/fields":
            if request.method == "POST":
                return httpx.Response(200, json={"code": 0, "data": {"field": {"field_id": "fld_created"}}})
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if url.startswith("https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_chinese/records?"):
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if url == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_chinese/records/batch_create":
            return httpx.Response(200, json={"code": 0, "data": {"records": [{"record_id": "rec_chinese"}]}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        mock_api_url="http://mock-api.local",
    )
    client = TestClient(app)

    response = client.post(
        "/warehouse/inventory-table/sync/filter",
        json={
            "item_id": "item_vinda_tissue",
            "warehouse_id": "wh_sz_1",
            "location_code": "A1",
            "batch_no": "BATCH-20260501",
            "limit": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["table_id"] == "tbl_chinese"
    assert body["items"][0]["record_id"] == "rec_chinese"
    assert not any("/tables/tbl_english/" in str(request.url) for request in requests)
    assert not any(request.method == "POST" and str(request.url).endswith("/tables") for request in requests)


def test_inventory_table_sync_filter_uses_backend_table_rows() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "http://mock-api.local/warehouse/inventory/table-rows":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "schema_id": "warehouse_batch_inventory",
                    "count": 1,
                    "items": [
                        {
                            "batch_key": "wh_sz_1:A1:item_vinda_tissue:BATCH-20260501",
                            "item_id": "item_vinda_tissue",
                            "batch_no": "BATCH-20260501",
                            "fields": {
                                "Warehouse": "深圳仓",
                                "Warehouse ID": "wh_sz_1",
                                "Location": "A1",
                                "Category": "纸品",
                                "Item ID": "item_vinda_tissue",
                                "Item Name": "维达纸巾",
                                "Batch No": "BATCH-20260501",
                                "Quantity Available": 96,
                                "Risk Level": "high",
                                "Backend Only Metric": 99,
                                "Last Synced At": "2026-05-24T00:00:00+00:00",
                                "Source Version": "mock-api:wh_sz_1:A1:item_vinda_tissue:BATCH-20260501",
                            },
                        }
                    ],
                },
            )
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields":
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if str(request.url).startswith(
            "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/records?"
        ):
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/records":
            return httpx.Response(200, json={"code": 0, "data": {"record": {"record_id": "rec_new"}}})
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

    response = client.post(
        "/warehouse/inventory-table/sync/filter",
        json={"warehouse_id": "wh_sz_1", "risk_level": "high"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["synced_count"] == 1
    table_row_requests = [
        request for request in requests if str(request.url) == "http://mock-api.local/warehouse/inventory/table-rows"
    ]
    assert len(table_row_requests) == 1
    assert json.loads(table_row_requests[0].content) == {
        "item_id": None,
        "sku": None,
        "warehouse_id": "wh_sz_1",
        "location_code": None,
        "category": None,
        "category_id": None,
        "batch_no": None,
        "expiry_risk": None,
        "risk_level": "high",
        "limit": 50,
    }
    lookup_request = next(
        request
        for request in requests
        if request.method == "GET" and str(request.url).startswith(
            "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/records?"
        )
    )
    decoded_query = str(lookup_request.url.params["filter"])
    assert 'CurrentValue.[Warehouse ID]="wh_sz_1"' in decoded_query
    assert 'CurrentValue.[Location]="A1"' in decoded_query
    assert 'CurrentValue.[Item ID]="item_vinda_tissue"' in decoded_query
    assert 'CurrentValue.[Batch No]="BATCH-20260501"' in decoded_query
    create_request = next(
        request
        for request in requests
        if request.method == "POST" and str(request.url).endswith("/records")
    )
    assert json.loads(create_request.content)["fields"]["Backend Only Metric"] == 99
    assert body["items"] == [
        {
            "batch_key": "wh_sz_1:A1:item_vinda_tissue:BATCH-20260501",
            "item_id": "item_vinda_tissue",
            "warehouse_id": "wh_sz_1",
            "location_code": "A1",
            "batch_no": "BATCH-20260501",
            "risk_level": "high",
            "action": "created",
            "record_id": "rec_new",
            "source_version": "mock-api:wh_sz_1:A1:item_vinda_tissue:BATCH-20260501",
        }
    ]


def test_inventory_table_schema_returns_fields_and_views() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {"field_id": "fld_sku", "field_name": "SKU", "type": 1, "property": None},
                            {
                                "field_id": "fld_risk",
                                "field_name": "Risk Level",
                                "type": 3,
                                "property": {
                                    "options": [
                                        {"name": "low", "color": 28},
                                        {"name": "medium", "color": 24},
                                        {"name": "high", "color": 17},
                                    ]
                                },
                            },
                        ]
                    },
                },
            )
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/views":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "view_id": "vew_existing",
                                "view_name": "Grid",
                                "view_type": "grid",
                            }
                        ]
                    },
                },
            )
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        inventory_table_id="tbl_inventory",
    )
    client = TestClient(app)

    response = client.get("/warehouse/inventory-table/schema")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "ok": True,
        "configured": True,
        "table_id": "tbl_inventory",
        "table_name": "Warehouse Inventory Snapshot",
        "table_url": "",
        "fields": [
            {
                "field_id": "fld_sku",
                "field_name": "SKU",
                "type": 1,
                "kind": "text",
                "options": [],
            },
            {
                "field_id": "fld_risk",
                "field_name": "Risk Level",
                "type": 3,
                "kind": "single_select",
                "options": [
                    {"name": "low", "color": 28},
                    {"name": "medium", "color": 24},
                    {"name": "high", "color": 17},
                ],
            },
        ],
        "views": [
            {
                "view_id": "vew_existing",
                "view_name": "Grid",
                "view_type": "grid",
            }
        ],
    }
    assert [request.method for request in requests] == ["POST", "GET", "GET", "GET"]


def test_inventory_table_view_create_validates_fields_and_creates_view() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {"field_id": "fld_sku", "field_name": "SKU", "type": 1},
                            {"field_id": "fld_wh", "field_name": "Warehouse", "type": 1},
                            {"field_id": "fld_available", "field_name": "Available", "type": 2},
                            {"field_id": "fld_risk", "field_name": "Risk Level", "type": 3},
                            {"field_id": "fld_rec", "field_name": "Recommendation", "type": 1},
                        ]
                    },
                },
            )
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/views":
            if request.method == "GET":
                return httpx.Response(200, json={"code": 0, "data": {"items": []}})
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"view": {"view_id": "vew_high_risk"}},
                },
            )
        if str(request.url) == (
            "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token"
            "/tables/tbl_inventory/views/vew_high_risk"
        ):
            return httpx.Response(200, json={"code": 0, "data": {"view": {"view_id": "vew_high_risk"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        inventory_table_id="tbl_inventory",
    )
    client = TestClient(app)

    response = client.post(
        "/warehouse/inventory-table/views/create",
        json={
            "view_name": "High Risk Inventory",
            "visible_fields": ["SKU", "Warehouse", "Available", "Risk Level", "Recommendation"],
            "filters": [{"field": "Risk Level", "operator": "is", "value": "high"}],
            "sorts": [{"field": "Available", "order": "asc"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["action"] == "created"
    assert body["view_id"] == "vew_high_risk"
    assert body["table_id"] == "tbl_inventory"
    assert body["validated_plan"] == {
        "view_name": "High Risk Inventory",
        "view_type": "grid",
        "visible_fields": ["SKU", "Warehouse", "Available", "Risk Level", "Recommendation"],
        "filters": [{"field": "Risk Level", "operator": "is", "value": "high"}],
        "sorts": [{"field": "Available", "order": "asc"}],
    }
    create_requests = [
        request
        for request in requests
        if request.method == "POST" and str(request.url).endswith("/tables/tbl_inventory/views")
    ]
    assert len(create_requests) == 1
    assert json.loads(create_requests[0].content) == {
        "view_name": "High Risk Inventory",
        "view_type": "grid",
    }
    patch_requests = [
        request
        for request in requests
        if request.method == "PATCH"
        and str(request.url).endswith("/tables/tbl_inventory/views/vew_high_risk")
    ]
    assert len(patch_requests) == 1
    assert json.loads(patch_requests[0].content) == {
        "view_name": "High Risk Inventory",
        "property": {
            "filter_info": {
                "conditions": [
                    {
                        "field_id": "fld_risk",
                        "operator": "is",
                        "value": "[\"high\"]",
                    }
                ],
                "conjunction": "and",
            },
            "hidden_fields": [],
        },
    }


def test_inventory_table_view_create_rejects_unknown_fields() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"items": [{"field_id": "fld_sku", "field_name": "SKU", "type": 1}]}},
            )
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/views":
            return httpx.Response(200, json={"code": 0, "data": {"items": []}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        inventory_table_id="tbl_inventory",
    )
    client = TestClient(app)

    response = client.post(
        "/warehouse/inventory-table/views/create",
        json={
            "view_name": "Bad View",
            "visible_fields": ["SKU", "Bad Field"],
            "filters": [{"field": "Missing Filter", "operator": "is", "value": "x"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "invalid_inventory_view_plan"
    assert body["missing_fields"] == ["Bad Field", "Missing Filter"]
    assert not [
        request
        for request in requests
        if request.method == "POST" and str(request.url).endswith("/tables/tbl_inventory/views")
    ]


def test_inventory_table_view_create_returns_existing_when_view_name_exists() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"items": [{"field_id": "fld_sku", "field_name": "SKU", "type": 1}]}},
            )
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/views":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "view_id": "vew_existing",
                                "view_name": "High Risk Inventory",
                                "view_type": "grid",
                            }
                        ]
                    },
                },
            )
        if str(request.url) == (
            "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token"
            "/tables/tbl_inventory/views/vew_existing"
        ):
            return httpx.Response(200, json={"code": 0, "data": {"view": {"view_id": "vew_existing"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        inventory_table_id="tbl_inventory",
    )
    client = TestClient(app)

    response = client.post(
        "/warehouse/inventory-table/views/create",
        json={"view_name": "High Risk Inventory", "visible_fields": ["SKU"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["action"] == "existing"
    assert body["view_id"] == "vew_existing"
    patch_requests = [
        request
        for request in requests
        if request.method == "PATCH"
        and str(request.url).endswith("/tables/tbl_inventory/views/vew_existing")
    ]
    assert len(patch_requests) == 1
    assert not [
        request
        for request in requests
        if request.method == "POST" and str(request.url).endswith("/tables/tbl_inventory/views")
    ]


def test_inventory_table_view_create_accepts_llm_shaped_filter_and_sort_payloads() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {"field_id": "fld_sku", "field_name": "SKU", "type": 1},
                            {"field_id": "fld_wh", "field_name": "Warehouse", "type": 1},
                            {"field_id": "fld_available", "field_name": "Available", "type": 2},
                            {"field_id": "fld_risk", "field_name": "Risk Level", "type": 3},
                            {"field_id": "fld_rec", "field_name": "Recommendation", "type": 1},
                        ]
                    },
                },
            )
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/views":
            if request.method == "GET":
                return httpx.Response(200, json={"code": 0, "data": {"items": []}})
            return httpx.Response(200, json={"code": 0, "data": {"view_id": "vew_high_risk"}})
        if str(request.url) == (
            "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token"
            "/tables/tbl_inventory/views/vew_high_risk"
        ):
            return httpx.Response(200, json={"code": 0, "data": {"view": {"view_id": "vew_high_risk"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        inventory_table_id="tbl_inventory",
    )
    client = TestClient(app)

    response = client.post(
        "/warehouse/inventory-table/views/create",
        json={
            "view_name": "High Risk Inventory",
            "visible_fields": "SKU, Warehouse, Available, Risk Level, Recommendation",
            "filters": [{"field": "Risk Level", "value": "high"}],
            "sorts": [{"field": "Available", "direction": "asc"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["action"] == "created"
    assert body["validated_plan"] == {
        "view_name": "High Risk Inventory",
        "view_type": "grid",
        "visible_fields": ["SKU", "Warehouse", "Available", "Risk Level", "Recommendation"],
        "filters": [{"field": "Risk Level", "operator": "is", "value": "high"}],
        "sorts": [{"field": "Available", "order": "asc"}],
    }


def test_inventory_table_view_templates_endpoint_lists_employee_templates() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.get("/warehouse/inventory-table/view-templates")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert any(item["template_id"] == "batch_risk_view" for item in body["templates"])
    assert any(item["template_id"] == "location_inventory_view" for item in body["templates"])


def test_warehouse_intent_router_endpoint_routes_update_table_view_to_sync() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/warehouse/intents/route",
        json={"message": "帮我更新一下香港仓库存表格视图"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "matched"
    assert body["intent"] == "sync_inventory_table"
    assert body["executor"] == "warehouse_inventory_table_sync"
    assert body["slots"]["warehouse"] == "wh_hk_1"
    assert body["clarification_question"] is None


def test_inventory_table_view_from_template_creates_controlled_view() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {"field_id": "fld_wh", "field_name": "Warehouse", "type": 1},
                            {"field_id": "fld_wh_id", "field_name": "Warehouse ID", "type": 1},
                            {"field_id": "fld_location", "field_name": "Location", "type": 1},
                            {"field_id": "fld_category", "field_name": "Category", "type": 1},
                            {"field_id": "fld_item", "field_name": "Item Name", "type": 1},
                            {"field_id": "fld_batch", "field_name": "Batch No", "type": 1},
                            {"field_id": "fld_available", "field_name": "Quantity Available", "type": 2},
                            {"field_id": "fld_expiry", "field_name": "Expiry Date", "type": 5},
                            {
                                "field_id": "fld_risk",
                                "field_name": "Risk Level",
                                "type": 3,
                                "property": {
                                    "options": [
                                        {"id": "opt_low", "name": "low", "color": 28},
                                        {"id": "opt_high", "name": "high", "color": 17},
                                    ]
                                },
                            },
                            {"field_id": "fld_rec", "field_name": "Recommendation", "type": 1},
                        ]
                    },
                },
            )
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/views":
            if request.method == "GET":
                return httpx.Response(200, json={"code": 0, "data": {"items": []}})
            return httpx.Response(200, json={"code": 0, "data": {"view_id": "vew_hk_high_risk"}})
        if str(request.url) == (
            "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token"
            "/tables/tbl_inventory/views/vew_hk_high_risk"
        ):
            return httpx.Response(200, json={"code": 0, "data": {"view": {"view_id": "vew_hk_high_risk"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        inventory_table_id="tbl_inventory",
    )
    client = TestClient(app)

    response = client.post(
        "/warehouse/inventory-table/views/from-template",
        json={"message": "帮我建一个香港仓高风险库存视图"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["matched"] is True
    assert body["template_id"] == "batch_risk_view"
    assert body["slots"]["risk_level"] == "high"
    assert body["slots"]["warehouse"] == "wh_hk_1"
    assert body["validated_plan"]["view_name"] == "香港仓高风险批次"
    create_requests = [
        request
        for request in requests
        if request.method == "POST" and str(request.url).endswith("/tables/tbl_inventory/views")
    ]
    assert len(create_requests) == 1
    assert json.loads(create_requests[0].content) == {
        "view_name": "香港仓高风险批次",
        "view_type": "grid",
    }
    patch_requests = [
        request
        for request in requests
        if request.method == "PATCH"
        and str(request.url).endswith("/tables/tbl_inventory/views/vew_hk_high_risk")
    ]
    assert len(patch_requests) == 1
    assert json.loads(patch_requests[0].content) == {
        "view_name": "香港仓高风险批次",
        "property": {
            "filter_info": {
                "conditions": [
                    {"field_id": "fld_risk", "operator": "is", "value": "[\"opt_high\"]"},
                    {"field_id": "fld_wh_id", "operator": "is", "value": "[\"wh_hk_1\"]"},
                ],
                "conjunction": "and",
            },
            "hidden_fields": ["fld_wh_id"],
        },
    }


def test_inventory_table_view_from_template_maps_threshold_operator_to_feishu_operator() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {"field_id": "fld_wh", "field_name": "Warehouse", "type": 1},
                            {"field_id": "fld_wh_id", "field_name": "Warehouse ID", "type": 1},
                            {"field_id": "fld_location", "field_name": "Location", "type": 1},
                            {"field_id": "fld_category", "field_name": "Category", "type": 1},
                            {"field_id": "fld_item", "field_name": "Item Name", "type": 1},
                            {"field_id": "fld_batch", "field_name": "Batch No", "type": 1},
                            {"field_id": "fld_available", "field_name": "Quantity Available", "type": 2},
                            {"field_id": "fld_threshold", "field_name": "Reorder Threshold", "type": 2},
                            {"field_id": "fld_rec", "field_name": "Recommendation", "type": 1},
                        ]
                    },
                },
            )
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/views":
            if request.method == "GET":
                return httpx.Response(200, json={"code": 0, "data": {"items": []}})
            return httpx.Response(200, json={"code": 0, "data": {"view_id": "vew_low_stock"}})
        if str(request.url) == (
            "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token"
            "/tables/tbl_inventory/views/vew_low_stock"
        ):
            return httpx.Response(200, json={"code": 0, "data": {"view": {"view_id": "vew_low_stock"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        inventory_table_id="tbl_inventory",
    )
    client = TestClient(app)

    response = client.post(
        "/warehouse/inventory-table/views/from-template",
        json={"message": "帮我建一个深圳仓缺货预警视图"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["template_id"] == "low_stock_view"
    patch_request = next(
        request
        for request in requests
        if request.method == "PATCH"
        and str(request.url).endswith("/tables/tbl_inventory/views/vew_low_stock")
    )
    assert json.loads(patch_request.content)["property"]["filter_info"]["conditions"] == [
        {"field_id": "fld_wh_id", "operator": "is", "value": "[\"wh_sz_1\"]"},
        {"field_id": "fld_available", "operator": "isLess", "value": "[10]"},
    ]


def test_inventory_table_view_from_template_does_not_hide_primary_field() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/fields":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "field_id": "fld_primary",
                                "field_name": "默认主字段",
                                "type": 1,
                                "is_primary": True,
                            },
                            {"field_id": "fld_wh", "field_name": "Warehouse", "type": 1},
                            {"field_id": "fld_wh_id", "field_name": "Warehouse ID", "type": 1},
                            {"field_id": "fld_location", "field_name": "Location", "type": 1},
                            {"field_id": "fld_category", "field_name": "Category", "type": 1},
                            {"field_id": "fld_item", "field_name": "Item Name", "type": 1},
                            {"field_id": "fld_batch", "field_name": "Batch No", "type": 1},
                            {"field_id": "fld_available", "field_name": "Quantity Available", "type": 2},
                            {"field_id": "fld_threshold", "field_name": "Reorder Threshold", "type": 2},
                            {"field_id": "fld_rec", "field_name": "Recommendation", "type": 1},
                            {"field_id": "fld_status", "field_name": "Sync Status", "type": 3},
                        ]
                    },
                },
            )
        if str(request.url) == "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token/tables/tbl_inventory/views":
            if request.method == "GET":
                return httpx.Response(200, json={"code": 0, "data": {"items": []}})
            return httpx.Response(200, json={"code": 0, "data": {"view_id": "vew_low_stock"}})
        if str(request.url) == (
            "https://open.feishu.cn/open-apis/bitable/v1/apps/app_token"
            "/tables/tbl_inventory/views/vew_low_stock"
        ):
            return httpx.Response(200, json={"code": 0, "data": {"view": {"view_id": "vew_low_stock"}}})
        return httpx.Response(404, json={"error": f"unexpected url {request.url}"})

    app = create_app(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        inventory_table_app_id="cli_table",
        inventory_table_app_secret="secret_table",
        inventory_table_app_token="app_token",
        inventory_table_id="tbl_inventory",
    )
    client = TestClient(app)

    response = client.post(
        "/warehouse/inventory-table/views/from-template",
        json={"message": "帮我建一个深圳仓缺货预警视图"},
    )

    assert response.status_code == 200
    patch_request = next(
        request
        for request in requests
        if request.method == "PATCH"
        and str(request.url).endswith("/tables/tbl_inventory/views/vew_low_stock")
    )
    hidden_fields = json.loads(patch_request.content)["property"]["hidden_fields"]
    assert "fld_primary" not in hidden_fields
    assert "fld_status" in hidden_fields


def test_inventory_table_view_from_template_returns_suggestions_for_unknown_template() -> None:
    app = create_app()
    client = TestClient(app)

    response = client.post(
        "/warehouse/inventory-table/views/from-template",
        json={"message": "帮我建一个财务利润视图"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["matched"] is False
    assert body["error"] == "unknown_view_template"
    assert "高风险库存" in body["message"]


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


def test_multi_bot_group_message_routes_when_mention_id_is_string() -> None:
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
                "name": "delivery",
                "app_id": "cli_delivery",
                "app_secret": "secret_delivery",
                "bot_open_id": "ou_delivery_bot",
                "n8n_webhook_url": "http://n8n.local/webhook/delivery-inbound",
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
        "header": {"event_id": "evt_group_mention_string_id"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_sender"}},
            "message": {
                "message_id": "om_group_mention_string_id",
                "chat_id": "oc_group",
                "chat_type": "group",
                "message_type": "text",
                "content": '{"text":"@Delivery 查询 ord_300 物流"}',
                "mentions": [
                    {
                        "id": "ou_delivery_bot",
                        "key": "@_user_1",
                        "name": "Delivery",
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
    assert n8n_urls == ["http://n8n.local/webhook/delivery-inbound"]


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
