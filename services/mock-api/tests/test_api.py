from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_get_order_fixture():
    response = client.get("/orders/ord_100")
    assert response.status_code == 200
    assert response.json()["order_id"] == "ord_100"


def test_search_policy_returns_refund_clause_metadata():
    response = client.post("/policies/search", json={"query": "ord_100 这个订单怎么退款"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["query"] == "ord_100 这个订单怎么退款"
    assert body["matches"]

    first_match = body["matches"][0]
    assert first_match["source_file"] == "fixtures/policies/after_sales_policy.zh.md"
    assert first_match["document_title"] == "售后政策"
    assert first_match["section"] == "退款"
    assert first_match["clause_id"].startswith("REFUND-")
    assert first_match["clause_title"]
    assert first_match["text"]


def test_create_approval_request():
    payload = {
        "event_id": "evt_refund_high_value",
        "recommended_action": "review_refund_request",
        "explanation": "High-value refund requires approval.",
    }
    response = client.post("/approval-requests", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "pending"

    list_response = client.get("/approval-requests")
    assert list_response.status_code == 200
    assert any(item["event_id"] == "evt_refund_high_value" for item in list_response.json())


def test_records_internal_notifications_and_run_logs():
    notification = client.post(
        "/internal-notifications",
        json={"event_id": "evt_low_stock", "team": "procurement"},
    )
    assert notification.status_code == 200
    assert notification.json()["status"] == "sent"

    run_log = client.post(
        "/run-logs",
        json={"event_id": "evt_low_stock", "status": "succeeded"},
    )
    assert run_log.status_code == 200

    assert client.get("/internal-notifications").json()[-1]["event_id"] == "evt_low_stock"
    assert client.get("/run-logs").json()[-1]["status"] == "succeeded"


def test_procurement_mock_recommends_replenishment_for_low_batch_stock():
    response = client.post("/procurement/mock", json={"item_id": "item_vinda_tissue"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item_id"] == "item_vinda_tissue"
    assert body["recommendation"] == "create_purchase_request"
    assert body["system"] == "mock-procurement"


def test_create_and_list_replenishment_requests_from_warehouse_signal():
    create_response = client.post(
        "/procurement/replenishment-requests",
        json={
            "source": "warehouse",
            "warehouse_id": "wh_sz_1",
            "location_code": "A1",
            "item_id": "item_vinda_tissue",
            "reason": "available_quantity_below_reorder_threshold",
            "created_by": "warehouse:user-001",
        },
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["ok"] is True
    assert created["request"]["request_id"].startswith("REQ-")
    assert created["request"]["status"] == "pending_procurement_review"
    assert created["request"]["source"] == "warehouse"
    assert created["request"]["warehouse_id"] == "wh_sz_1"
    assert created["request"]["location_code"] == "A1"
    assert created["request"]["item_id"] == "item_vinda_tissue"
    assert created["request"]["current_quantity"] == 96
    assert created["request"]["reorder_threshold"] == 100
    assert created["request"]["suggested_quantity"] == 104
    assert created["request"]["item_name"] == "维达纸巾"

    list_response = client.get("/procurement/replenishment-requests?status=pending_procurement_review")

    assert list_response.status_code == 200
    listed = list_response.json()
    assert listed["ok"] is True
    assert any(
        item["request_id"] == created["request"]["request_id"]
        and item["status"] == "pending_procurement_review"
        for item in listed["items"]
    )


def test_operations_summary_mock_returns_cross_domain_summary():
    response = client.post("/operations/summary/mock", json={"query": "帮我总结今天的运营异常"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["system"] == "mock-operations"
    assert body["summary"]
    assert any(item["domain"] == "warehouse" for item in body["incidents"])


def test_warehouse_inventory_returns_batches_locations_and_risk():
    response = client.get("/warehouse/inventory/item_vinda_tissue")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item_id"] == "item_vinda_tissue"
    assert body["item_name"] == "维达纸巾"
    assert body["category_name"] == "纸品"
    assert body["total_quantity_available"] == 108
    assert body["risk_level"] == "high"
    assert body["batches"][0]["warehouse_id"] == "wh_sz_1"
    assert body["batches"][0]["location_code"] == "A1"
    assert body["recommendation"]


def test_warehouse_inventory_returns_perishable_batch_fixture():
    response = client.get("/warehouse/inventory/item_milk_pure")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item_id"] == "item_milk_pure"
    assert body["category_name"] == "乳制品"
    assert body["risk_level"] == "high"
    assert body["batches"][0]["expiry_risk"] == "expiring_soon"


def test_warehouse_inventory_search_filters_by_warehouse_category_and_expiry_risk():
    response = client.post(
        "/warehouse/inventory/search",
        json={"warehouse_id": "wh_hk_1", "category": "dairy", "expiry_risk": "expiring_soon"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["schema_id"] == "warehouse_batch_inventory"
    assert body["count"] >= 1
    assert {item["item_id"] for item in body["items"]}.issuperset({"item_milk_pure"})
    assert {item["warehouse_id"] for item in body["items"]} == {"wh_hk_1"}
    assert {item["category_id"] for item in body["items"]} == {"dairy"}
    assert {item["expiry_risk"] for item in body["items"]} == {"expiring_soon"}


def test_warehouse_inventory_table_schema_exposes_business_fields():
    response = client.get("/warehouse/inventory/table-schema")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["schema_id"] == "warehouse_batch_inventory"
    assert body["source"] == "mock-api"
    assert body["fields"][0] == {
        "name": "Warehouse",
        "source": "warehouses.name",
        "type": "text",
        "comment": "仓库展示名称，例如深圳仓、香港仓。",
    }
    assert any(item["name"] == "Location" for item in body["fields"])
    assert any(item["name"] == "Category" for item in body["fields"])
    assert any(item["name"] == "Batch No" for item in body["fields"])
    assert any(item["name"] == "Expiry Date" for item in body["fields"])
    risk_field = next(item for item in body["fields"] if item["name"] == "Risk Level")
    assert risk_field["type"] == "single_select"
    assert risk_field["options"] == [
        {"name": "low", "color": 28},
        {"name": "medium", "color": 24},
        {"name": "high", "color": 17},
        {"name": "unknown", "color": 0},
    ]


def test_warehouse_inventory_table_rows_return_batch_location_feishu_ready_fields():
    response = client.post(
        "/warehouse/inventory/table-rows",
        json={"warehouse_id": "wh_sz_1", "category": "paper", "limit": 5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["schema_id"] == "warehouse_batch_inventory"
    assert body["count"] >= 1
    first = body["items"][0]
    assert first["batch_key"].startswith("wh_sz_1:A1:item_vinda_tissue:")
    assert set(first["fields"]).issuperset(
        {
            "Warehouse",
            "Location",
            "Category",
            "Item ID",
            "Item Name",
            "Brand",
            "Spec",
            "Batch No",
            "Quantity On Hand",
            "Quantity Available",
            "Quantity Reserved",
            "Expiry Date",
            "Risk Level",
            "Recommendation",
            "Last Synced At",
            "Sync Status",
            "Source Version",
        }
    )
    assert first["fields"]["Warehouse"] == "深圳仓"
    assert first["fields"]["Location"] == "A1"
    assert first["fields"]["Category"] == "纸品"
    assert first["fields"]["Item Name"] == "维达纸巾"


def test_warehouse_exception_search_returns_expiring_batch_risks():
    response = client.post(
        "/warehouse/exceptions/search",
        json={"item_id": "item_milk_pure", "expiry_risk": "expiring_soon"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["matches"]
    assert body["matches"][0]["item_id"] == "item_milk_pure"
    assert body["matches"][0]["expiry_risk"] == "expiring_soon"


def test_warehouse_fulfillment_check_blocks_low_stock_sku():
    response = client.post(
        "/warehouse/fulfillment/check",
        json={"item_id": "item_vinda_tissue"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item_id"] == "item_vinda_tissue"
    assert body["can_ship"] is False
    assert "insufficient_available_stock" in body["blockers"]
    assert body["next_action"] == "notify_procurement"


def test_warehouse_fulfillment_check_allows_healthy_sku():
    response = client.post(
        "/warehouse/fulfillment/check",
        json={"item_id": "item_office_pen"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item_id"] == "item_office_pen"
    assert body["can_ship"] is True
    assert body["blockers"] == []
    assert body["next_action"] == "release_to_pick"
