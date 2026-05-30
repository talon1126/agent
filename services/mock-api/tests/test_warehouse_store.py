from pathlib import Path

from sqlalchemy import create_engine, text

from app.store import FIXTURE_DIR
from app.warehouse_store import (
    WAREHOUSE_COLUMN_COMMENTS,
    WAREHOUSE_TABLE_COMMENTS,
    WarehouseRepository,
    _quote_literal,
    categories,
    init_warehouse_schema,
    inventory_location_balances,
    inventory_batches,
    items,
    delivery_providers,
    order_items,
    orders,
    procurement_suppliers,
    purchase_orders,
    replenishment_requests,
    seed_warehouse_fixtures,
    storage_locations,
    warehouse_inventory_sync_jobs,
    warehouses,
)


WAREHOUSE_TABLES = [
    warehouses,
    storage_locations,
    categories,
    items,
    inventory_batches,
    replenishment_requests,
    delivery_providers,
    procurement_suppliers,
    purchase_orders,
    warehouse_inventory_sync_jobs,
    inventory_location_balances,
    orders,
    order_items,
]


def test_seed_warehouse_fixtures_populates_postgres_shape_tables(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")

    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)

    with engine.connect() as connection:
        warehouse_count = connection.execute(text("select count(*) from warehouses")).scalar_one()
        location_count = connection.execute(text("select count(*) from storage_locations")).scalar_one()
        category_count = connection.execute(text("select count(*) from categories")).scalar_one()
        item_count = connection.execute(text("select count(*) from items")).scalar_one()
        batch_count = connection.execute(text("select count(*) from inventory_batches")).scalar_one()
        replenishment_count = connection.execute(text("select count(*) from replenishment_requests")).scalar_one()
        delivery_provider_count = connection.execute(text("select count(*) from delivery_providers")).scalar_one()
        supplier_count = connection.execute(text("select count(*) from procurement_suppliers")).scalar_one()
        purchase_order_count = connection.execute(text("select count(*) from purchase_orders")).scalar_one()
        sync_job_count = connection.execute(text("select count(*) from warehouse_inventory_sync_jobs")).scalar_one()
        balance_count = connection.execute(text("select count(*) from inventory_location_balances")).scalar_one()
        order_count = connection.execute(text("select count(*) from orders")).scalar_one()
        order_item_count = connection.execute(text("select count(*) from order_items")).scalar_one()

    assert warehouse_count == 2
    assert location_count == 6
    assert category_count == 5
    assert item_count == 8
    assert batch_count == 10
    assert replenishment_count == 0
    assert delivery_provider_count == 3
    assert supplier_count == 7
    assert purchase_order_count == 0
    assert sync_job_count == 0
    assert balance_count == 10
    assert order_count == 0
    assert order_item_count == 0


def test_warehouse_tables_and_columns_have_chinese_comments() -> None:
    table_names = {table.name for table in WAREHOUSE_TABLES}

    assert set(WAREHOUSE_TABLE_COMMENTS) == table_names
    assert set(WAREHOUSE_COLUMN_COMMENTS) == table_names
    for table in WAREHOUSE_TABLES:
        assert WAREHOUSE_TABLE_COMMENTS[table.name]
        assert set(WAREHOUSE_COLUMN_COMMENTS[table.name]) == {
            column.name for column in table.columns
        }
        assert all(WAREHOUSE_COLUMN_COMMENTS[table.name].values())


def test_postgres_comment_literal_escapes_single_quotes() -> None:
    assert _quote_literal("员工's 库存说明") == "'员工''s 库存说明'"


def test_warehouse_repository_reads_batch_inventory_rows(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)

    rows = repository.list_inventory_batches(
        warehouse_id="wh_sz_1",
        category_id="paper",
    )

    assert rows
    assert rows[0]["warehouse_id"] == "wh_sz_1"
    assert rows[0]["warehouse_name"] == "深圳仓"
    assert rows[0]["location_code"] == "A1"
    assert rows[0]["category_id"] == "paper"
    assert rows[0]["category_name"] == "纸品"
    assert rows[0]["item_id"] == "item_vinda_tissue"
    assert rows[0]["item_name"] == "维达纸巾"
    assert rows[0]["batch_no"] == "BATCH-20260401"


def test_inventory_batch_ids_are_autoincrementing_integers(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)

    with engine.connect() as connection:
        seeded_batch_id = connection.execute(
            text("select batch_id from inventory_batches order by batch_id limit 1")
        ).scalar_one()
        max_seeded_batch_id = connection.execute(
            text("select max(batch_id) from inventory_batches")
        ).scalar_one()

    created = repository.create_inventory_batch(
        {
            "warehouse_id": "wh_sz_1",
            "location_code": "A1",
            "item_id": "item_vinda_tissue",
            "batch_no": "RCV-POD-TEST-1",
            "production_date": "2026-05-26",
            "expiry_date": "2028-05-25",
            "quantity_on_hand": 20,
            "quantity_reserved": 0,
            "reorder_threshold": 100,
            "storage_status": "available",
        }
    )

    assert isinstance(seeded_batch_id, int)
    assert created["batch_id"] == max_seeded_batch_id + 1
    assert isinstance(created["batch_id"], int)


def test_warehouse_repository_persists_replenishment_requests(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    repository = WarehouseRepository(engine)

    created = repository.create_replenishment_request(
        {
            "request_id": "REQ-2001",
            "source": "warehouse",
            "status": "未审批",
            "warehouse_id": "wh_sz_1",
            "warehouse_name": "深圳仓",
            "location_code": "A1",
            "item_id": "item_vinda_tissue",
            "item_name": "维达纸巾",
            "category_id": "paper",
            "category_name": "纸品",
            "current_quantity": 96,
            "reorder_threshold": 100,
            "suggested_quantity": 104,
            "reason": "available_quantity_below_reorder_threshold",
            "created_by": "warehouse:user-001",
            "created_at": "2026-05-24T21:00:00+08:00",
            "updated_at": "2026-05-24T21:00:00+08:00",
        }
    )
    listed = repository.list_replenishment_requests(status="未审批")

    assert created["request_id"] == "REQ-2001"
    assert listed == [created]


def test_warehouse_repository_reads_suppliers_and_persists_purchase_orders(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)

    supplier = repository.get_default_supplier("item_vinda_tissue")
    created = repository.create_purchase_order(
        {
            "purchase_order_id": "PO-5001",
            "request_id": "REQ-2001",
            "supplier_id": supplier["supplier_id"],
            "supplier_name": supplier["supplier_name"],
            "item_id": "item_vinda_tissue",
            "warehouse_id": "wh_sz_1",
            "warehouse_name": "深圳仓",
            "location_code": "A1",
            "quantity": 104,
            "unit_price": supplier["unit_price"],
            "currency": supplier["currency"],
            "estimated_total_price": 832,
            "lead_time_days": supplier["lead_time_days"],
            "estimated_arrival_date": "2026-05-27",
            "payment_status": "unpaid",
            "warehouse_sync_status": "pending_arrival",
            "created_by": "procurement:user-001",
            "created_at": "2026-05-24T21:00:00+08:00",
            "updated_at": "2026-05-24T21:00:00+08:00",
        }
    )
    listed = repository.list_purchase_orders(request_id="REQ-2001")

    assert supplier["supplier_name"] == "深圳纸品供应商"
    assert created["purchase_order_id"] == "PO-5001"
    assert created["payment_status"] == "unpaid"
    assert created["warehouse_sync_status"] == "pending_arrival"
    assert listed == [created]


def test_warehouse_repository_syncs_paid_arrived_purchase_orders_to_inventory_balances(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)
    supplier = repository.get_default_supplier("item_vinda_tissue")
    repository.create_purchase_order(
        {
            "purchase_order_id": "PO-6001",
            "request_id": "REQ-3001",
            "supplier_id": supplier["supplier_id"],
            "supplier_name": supplier["supplier_name"],
            "item_id": "item_vinda_tissue",
            "warehouse_id": "wh_sz_1",
            "warehouse_name": "深圳仓",
            "location_code": "B1",
            "quantity": 104,
            "unit_price": supplier["unit_price"],
            "currency": supplier["currency"],
            "estimated_total_price": 832,
            "lead_time_days": supplier["lead_time_days"],
            "estimated_arrival_date": "2026-05-29",
            "payment_status": "paid",
            "warehouse_sync_status": "arrived_unsynced",
            "arrived_at": "2026-05-29T10:00:00+00:00",
            "created_by": "procurement:user-001",
            "created_at": "2026-05-29T09:00:00+00:00",
            "updated_at": "2026-05-29T10:00:00+00:00",
        }
    )

    synced = repository.sync_arrived_purchase_orders(
        limit=10,
        processed_by="warehouse-agent",
        processed_at="2026-05-29T10:01:00+00:00",
    )

    assert len(synced) == 1
    assert synced[0]["batch_no"] == "BATCH-20260529"
    assert synced[0]["location_code"] == "A1"
    assert synced[0]["expiry_date"] == "2027-05-29"
    assert 20 <= synced[0]["reorder_threshold"] <= 120
    order = repository.get_purchase_order("PO-6001")
    assert order["warehouse_sync_status"] == "synced"
    balances = repository.list_location_balances(item_id="item_vinda_tissue", warehouse_id="wh_sz_1")
    assert {item["location_code"] for item in balances} == {"A1"}
    assert sum(item["quantity_on_hand"] for item in balances) == 240


def test_warehouse_repository_persists_inventory_sync_jobs(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    repository = WarehouseRepository(engine)

    created = repository.upsert_warehouse_inventory_sync_job(
        {
            "job_id": "WSJ-POD-5001",
            "team": "warehouse",
            "event": "warehouse_inventory_sync_requested",
            "po_draft_id": "POD-5001",
            "request_id": "REQ-2001",
            "item_id": "item_vinda_tissue",
            "warehouse_id": "wh_sz_1",
            "warehouse_name": "深圳仓",
            "location_code": "A1",
            "batch_no": "RCV-POD-5001",
            "quantity": 104,
            "next_action": "notify_warehouse_to_sync_inventory_table",
            "suggested_message": "@warehouse 同步 item_vinda_tissue 库存到飞书",
            "created_by": "warehouse:user-001",
            "created_at": "2026-05-26T10:00:00+00:00",
            "updated_at": "2026-05-26T10:00:00+00:00",
        }
    )
    pending = repository.list_warehouse_inventory_sync_jobs(status="pending")

    assert created["job_id"] == "WSJ-POD-5001"
    assert created["status"] == "pending"
    assert pending == [created]

    completed = repository.update_warehouse_inventory_sync_job(
        "WSJ-POD-5001",
        status="completed",
        processed_by="warehouse-agent",
        processed_at="2026-05-26T10:05:00+00:00",
        updated_at="2026-05-26T10:05:00+00:00",
        result={"synced_count": 1},
    )

    assert completed
    assert completed["status"] == "completed"
    assert completed["processed_by"] == "warehouse-agent"
    assert completed["result"] == {"synced_count": 1}
    assert repository.list_warehouse_inventory_sync_jobs(status="pending") == []
    assert repository.list_warehouse_inventory_sync_jobs(status="completed") == [completed]


def test_warehouse_repository_persists_order_lifecycle_against_location_balances(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)

    created = repository.create_order(
        {
            "order_id": "ORD-CODEX-DB-1",
            "customer_id": "cus_100",
            "status": "未付款",
            "delivery_provider_id": "sf",
            "delivery_provider_name": "顺丰",
            "courier_phone": "13800000001",
            "tracking_no": "SF1001",
            "requested_items": [
                {"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1", "quantity": 20}
            ],
            "created_by": "delivery-agent",
            "created_at": "2026-05-28T10:00:00+00:00",
            "updated_at": "2026-05-28T10:00:00+00:00",
            "paid_at": "",
            "shipped_at": "",
            "arrived_at": "",
            "cancelled_at": "",
            "returned_at": "",
        }
    )

    assert created["order"]["id"] == 1
    assert created["order"]["status"] == "未付款"
    assert created["order"]["delivery_provider_name"] == "顺丰"

    paid = repository.pay_order(
        "ORD-CODEX-DB-1",
        updated_by="warehouse-agent",
        updated_at="2026-05-28T10:01:00+00:00",
    )

    assert paid["order"]["status"] == "待发货"
    assert [item["batch_no"] for item in paid["items"]] == ["BATCH-20260401", "BATCH-20260501"]
    assert [item["quantity"] for item in paid["items"]] == [16, 4]
    balances = repository.list_location_balances(item_id="item_vinda_tissue", warehouse_id="wh_sz_1")
    assert sum(item["quantity_on_hand"] for item in balances) == 116

    batch = repository.get_inventory_batch_by_batch_no("BATCH-20260401")
    assert batch["quantity_on_hand"] == 16

    returned = repository.return_order(
        "ORD-CODEX-DB-1",
        updated_by="warehouse-agent",
        updated_at="2026-05-28T10:10:00+00:00",
    )

    assert returned["order"]["status"] == "已退货"
    balances = repository.list_location_balances(item_id="item_vinda_tissue", warehouse_id="wh_sz_1")
    assert sum(item["quantity_on_hand"] for item in balances) == 136
