from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import DELIVERY_CASES, RECEIVED_INVENTORY_BATCHES, WAREHOUSE_INVENTORY_SYNC_JOBS, app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_received_inventory_batches():
    DELIVERY_CASES.clear()
    RECEIVED_INVENTORY_BATCHES.clear()
    WAREHOUSE_INVENTORY_SYNC_JOBS.clear()
    yield
    DELIVERY_CASES.clear()
    RECEIVED_INVENTORY_BATCHES.clear()
    WAREHOUSE_INVENTORY_SYNC_JOBS.clear()


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


def create_replenishment_request_for_test(
    item_id: str = "item_vinda_tissue",
    location_code: str = "A1",
) -> dict:
    response = client.post(
        "/procurement/replenishment-requests",
        json={
            "source": "warehouse",
            "warehouse_id": "wh_sz_1",
            "location_code": location_code,
            "item_id": item_id,
            "reason": "available_quantity_below_reorder_threshold",
            "created_by": "warehouse:user-001",
        },
    )
    assert response.status_code == 200
    return response.json()["request"]


def test_approve_replenishment_request_creates_purchase_order_draft():
    request = create_replenishment_request_for_test()
    expected_arrival_date = (
        datetime.fromisoformat(request["created_at"]).date() + timedelta(days=3)
    ).isoformat()

    response = client.post(
        f"/procurement/replenishment-requests/{request['request_id']}/approve",
        json={"created_by": "procurement:user-001"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["request"]["request_id"] == request["request_id"]
    assert body["request"]["status"] == "purchase_order_draft_created"
    assert body["draft"]["po_draft_id"].startswith("POD-")
    assert body["draft"]["request_id"] == request["request_id"]
    assert body["draft"]["supplier_id"] == "supplier_paper_sz"
    assert body["draft"]["supplier_name"] == "深圳纸品供应商"
    assert body["draft"]["item_id"] == "item_vinda_tissue"
    assert body["draft"]["quantity"] == request["suggested_quantity"]
    assert body["draft"]["unit_price"] == 8
    assert body["draft"]["currency"] == "CNY"
    assert body["draft"]["estimated_total_price"] == request["suggested_quantity"] * 8
    assert body["draft"]["lead_time_days"] == 3
    assert body["draft"]["estimated_arrival_date"] == expected_arrival_date
    assert body["draft"]["status"] == "draft"

    drafts_response = client.get(
        f"/procurement/purchase-order-drafts?request_id={request['request_id']}"
    )
    assert drafts_response.status_code == 200
    drafts = drafts_response.json()
    assert drafts["ok"] is True
    assert drafts["count"] == 1
    assert drafts["items"][0]["po_draft_id"] == body["draft"]["po_draft_id"]


def test_approve_replenishment_request_reuses_existing_purchase_order_draft():
    request = create_replenishment_request_for_test()

    first = client.post(
        f"/procurement/replenishment-requests/{request['request_id']}/approve",
        json={"created_by": "procurement:user-001"},
    )
    second = client.post(
        f"/procurement/replenishment-requests/{request['request_id']}/approve",
        json={"created_by": "procurement:user-001"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["draft"]["po_draft_id"] == first.json()["draft"]["po_draft_id"]
    assert second.json()["draft"]["estimated_arrival_date"] == first.json()["draft"]["estimated_arrival_date"]
    drafts = client.get(
        f"/procurement/purchase-order-drafts?request_id={request['request_id']}"
    ).json()
    assert drafts["count"] == 1


def test_reject_replenishment_request_updates_status_without_draft():
    request = create_replenishment_request_for_test()

    response = client.post(
        f"/procurement/replenishment-requests/{request['request_id']}/reject",
        json={"reason": "供应商暂不稳定，先人工复核。", "updated_by": "procurement:user-001"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["request"]["request_id"] == request["request_id"]
    assert body["request"]["status"] == "rejected"
    assert body["request"]["reason"] == "供应商暂不稳定，先人工复核。"

    drafts = client.get(
        f"/procurement/purchase-order-drafts?request_id={request['request_id']}"
    ).json()
    assert drafts["count"] == 0


def test_replenishment_request_decision_returns_404_for_unknown_request():
    response = client.post(
        "/procurement/replenishment-requests/REQ-DOES-NOT-EXIST/approve",
        json={"created_by": "procurement:user-001"},
    )

    assert response.status_code == 404


def test_approve_replenishment_request_requires_default_supplier():
    request = create_replenishment_request_for_test(
        item_id="item_office_pen",
        location_code="B1",
    )

    response = client.post(
        f"/procurement/replenishment-requests/{request['request_id']}/approve",
        json={"created_by": "procurement:user-001"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "default supplier not found for item"


def test_approve_replenishment_request_batch_processes_pending_requests_and_skips_missing_suppliers():
    first = create_replenishment_request_for_test(item_id="item_vinda_tissue", location_code="A1")
    second = create_replenishment_request_for_test(item_id="item_milk_pure", location_code="C1")
    missing_supplier = create_replenishment_request_for_test(item_id="item_office_pen", location_code="B1")

    response = client.post(
        "/procurement/replenishment-requests/approve-batch",
        json={"created_by": "procurement:user-001"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["approved_count"] >= 2
    assert body["skipped_count"] >= 1
    approved_request_ids = {
        item["request_id"] for item in body["created_or_reused_drafts"]
    }
    assert {first["request_id"], second["request_id"]}.issubset(approved_request_ids)
    assert any(
        error["request_id"] == missing_supplier["request_id"]
        and error["error"] == "default_supplier_not_found"
        for error in body["errors"]
    )

    refreshed = client.get("/procurement/replenishment-requests").json()["items"]
    statuses = {item["request_id"]: item["status"] for item in refreshed}
    assert statuses[first["request_id"]] == "purchase_order_draft_created"
    assert statuses[second["request_id"]] == "purchase_order_draft_created"
    assert statuses[missing_supplier["request_id"]] == "pending_procurement_review"


def test_confirm_purchase_order_draft_arrival_batch_updates_inventory_and_is_idempotent():
    first_request = create_replenishment_request_for_test(
        item_id="item_vinda_tissue",
        location_code="A1",
    )
    second_request = create_replenishment_request_for_test(
        item_id="item_milk_pure",
        location_code="C1",
    )
    first_draft = client.post(
        f"/procurement/replenishment-requests/{first_request['request_id']}/approve",
        json={"created_by": "procurement:user-001"},
    ).json()["draft"]
    second_draft = client.post(
        f"/procurement/replenishment-requests/{second_request['request_id']}/approve",
        json={"created_by": "procurement:user-001"},
    ).json()["draft"]

    response = client.post(
        "/procurement/purchase-order-drafts/confirm-arrival-batch",
        json={
            "po_draft_ids": [
                first_draft["po_draft_id"],
                second_draft["po_draft_id"],
            ],
            "received_by": "warehouse:user-001",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["processed_count"] == 2
    assert body["confirmed_count"] == 2
    assert body["skipped_count"] == 0
    assert {item["po_draft_id"] for item in body["confirmed_items"]} == {
        first_draft["po_draft_id"],
        second_draft["po_draft_id"],
    }
    assert {
        item["item_id"] for item in body["warehouse_inventory_sync_requests"]
    } == {"item_vinda_tissue", "item_milk_pure"}
    assert all(
        item["next_action"] == "notify_warehouse_to_sync_inventory_table"
        for item in body["warehouse_inventory_sync_requests"]
    )

    refreshed_drafts = client.get("/procurement/purchase-order-drafts").json()["items"]
    draft_statuses = {item["po_draft_id"]: item["status"] for item in refreshed_drafts}
    assert draft_statuses[first_draft["po_draft_id"]] == "received_at_warehouse"
    assert draft_statuses[second_draft["po_draft_id"]] == "received_at_warehouse"

    first_inventory = client.get("/warehouse/inventory/item_vinda_tissue").json()
    second_inventory = client.get("/warehouse/inventory/item_milk_pure").json()
    first_receipt_batch = next(
        item
        for item in first_inventory["batches"]
        if item["batch_no"] == f"RCV-{first_draft['po_draft_id']}"
    )
    second_receipt_batch = next(
        item
        for item in second_inventory["batches"]
        if item["batch_no"] == f"RCV-{second_draft['po_draft_id']}"
    )
    assert first_receipt_batch["quantity_on_hand"] == first_draft["quantity"]
    assert isinstance(first_receipt_batch["batch_id"], int)
    assert first_receipt_batch["warehouse_id"] == first_request["warehouse_id"]
    assert first_receipt_batch["location_code"] == first_request["location_code"]
    assert second_receipt_batch["quantity_on_hand"] == second_draft["quantity"]
    assert isinstance(second_receipt_batch["batch_id"], int)
    assert second_receipt_batch["warehouse_id"] == second_request["warehouse_id"]
    assert second_receipt_batch["location_code"] == second_request["location_code"]

    repeat = client.post(
        "/procurement/purchase-order-drafts/confirm-arrival-batch",
        json={"po_draft_ids": [first_draft["po_draft_id"]], "received_by": "warehouse:user-001"},
    )

    assert repeat.status_code == 200
    repeat_body = repeat.json()
    assert repeat_body["confirmed_items"][0]["action"] == "reused"
    receipt_batches = [
        item
        for item in client.get("/warehouse/inventory/item_vinda_tissue").json()["batches"]
        if item["batch_no"] == f"RCV-{first_draft['po_draft_id']}"
    ]
    assert len(receipt_batches) == 1


def test_warehouse_inventory_sync_jobs_are_created_and_can_be_completed_or_failed():
    request = create_replenishment_request_for_test(
        item_id="item_vinda_tissue",
        location_code="A1",
    )
    draft = client.post(
        f"/procurement/replenishment-requests/{request['request_id']}/approve",
        json={"created_by": "procurement:user-001"},
    ).json()["draft"]

    client.post(
        "/procurement/purchase-order-drafts/confirm-arrival-batch",
        json={"po_draft_ids": [draft["po_draft_id"]], "received_by": "warehouse:user-001"},
    )

    pending = client.get("/warehouse/inventory-sync-jobs?status=pending")
    assert pending.status_code == 200
    pending_body = pending.json()
    assert pending_body["ok"] is True
    assert pending_body["count"] >= 1
    job = next(
        item
        for item in pending_body["items"]
        if item["po_draft_id"] == draft["po_draft_id"]
    )
    assert job["job_id"] == f"WSJ-{draft['po_draft_id']}"
    assert job["event"] == "warehouse_inventory_sync_requested"
    assert job["status"] == "pending"
    assert job["item_id"] == "item_vinda_tissue"
    assert job["warehouse_id"] == request["warehouse_id"]
    assert job["location_code"] == request["location_code"]

    failed_sync_complete = client.post(
        f"/warehouse/inventory-sync-jobs/{job['job_id']}/complete",
        json={
            "processed_by": "warehouse-agent",
            "result": {
                "ok": False,
                "error": "feishu_inventory_table_sync_failed",
                "message": "missing warehouse inventory table config",
            },
        },
    )
    assert failed_sync_complete.status_code == 400
    assert failed_sync_complete.json()["detail"] == "warehouse inventory sync result is not successful"
    still_pending = client.get("/warehouse/inventory-sync-jobs?status=pending").json()
    assert any(item["job_id"] == job["job_id"] for item in still_pending["items"])

    complete = client.post(
        f"/warehouse/inventory-sync-jobs/{job['job_id']}/complete",
        json={"processed_by": "warehouse-agent", "result": {"ok": True, "synced_count": 1}},
    )
    assert complete.status_code == 200
    assert complete.json()["job"]["status"] == "completed"
    assert complete.json()["job"]["result"]["synced_count"] == 1

    failed = client.post(
        "/warehouse/inventory-sync-jobs/WSJ-DOES-NOT-EXIST/fail",
        json={"processed_by": "warehouse-agent", "error": "not found"},
    )
    assert failed.status_code == 404


def test_procurement_table_schema_and_rows_are_feishu_ready():
    request = create_replenishment_request_for_test()
    approve = client.post(
        f"/procurement/replenishment-requests/{request['request_id']}/approve",
        json={"created_by": "procurement:user-001"},
    ).json()

    request_schema = client.get("/procurement/replenishment-requests/table-schema").json()
    draft_schema = client.get("/procurement/purchase-order-drafts/table-schema").json()
    request_rows = client.post(
        "/procurement/replenishment-requests/table-rows",
        json={"status": "purchase_order_draft_created"},
    ).json()
    draft_rows = client.post(
        "/procurement/purchase-order-drafts/table-rows",
        json={"request_id": request["request_id"]},
    ).json()

    assert request_schema["ok"] is True
    assert request_schema["schema_id"] == "procurement_replenishment_requests"
    assert "Request ID" in [field["name"] for field in request_schema["fields"]]
    assert draft_schema["ok"] is True
    assert draft_schema["schema_id"] == "procurement_purchase_order_drafts"
    assert "Estimated Arrival Date" in [field["name"] for field in draft_schema["fields"]]

    assert request_rows["ok"] is True
    request_fields = next(
        item["fields"]
        for item in request_rows["items"]
        if item["request_id"] == request["request_id"]
    )
    assert request_fields["Request ID"] == request["request_id"]
    assert request_fields["Status"] == "purchase_order_draft_created"
    assert request_fields["Item Name"] == "维达纸巾"

    assert draft_rows["ok"] is True
    assert draft_rows["count"] == 1
    draft_fields = draft_rows["items"][0]["fields"]
    assert draft_fields["PO Draft ID"] == approve["draft"]["po_draft_id"]
    assert draft_fields["Request ID"] == request["request_id"]
    assert draft_fields["Estimated Arrival Date"] == approve["draft"]["estimated_arrival_date"]


def test_operations_summary_mock_returns_cross_domain_summary():
    response = client.post("/operations/summary/mock", json={"query": "帮我总结今天的运营异常"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["system"] == "mock-operations"
    assert body["summary"]
    assert any(item["domain"] == "warehouse" for item in body["incidents"])


def test_delivery_status_lookup_accepts_order_id_and_returns_shipment_context():
    response = client.post("/delivery/status/lookup", json={"order_id": "ord_300"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["system"] == "mock-delivery"
    assert body["order"]["order_id"] == "ord_300"
    assert body["shipment"]["shipment_id"] == "ship_300"
    assert body["shipment"]["status"] == "delayed"
    assert body["risk_level"] == "high"
    assert "延迟" in body["recommendation"]


def test_delivery_exceptions_search_returns_delayed_shipments():
    response = client.post("/delivery/exceptions/search", json={"status": "delayed"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["system"] == "mock-delivery"
    assert body["count"] == 1
    assert body["items"][0]["shipment_id"] == "ship_300"
    assert body["items"][0]["order_id"] == "ord_300"
    assert body["items"][0]["exception_type"] == "delivery_delay"


def test_delivery_case_create_records_follow_up_case():
    response = client.post(
        "/delivery/cases",
        json={
            "shipment_id": "ship_300",
            "case_type": "delivery_delay",
            "reason": "客户催促，超过预计时效",
            "created_by": "delivery-agent",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["case"]["case_id"].startswith("DCASE-")
    assert body["case"]["shipment_id"] == "ship_300"
    assert body["case"]["status"] == "open"
    assert DELIVERY_CASES[0]["case_id"] == body["case"]["case_id"]


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
