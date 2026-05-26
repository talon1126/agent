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
    inventory_batches,
    items,
    procurement_suppliers,
    purchase_order_drafts,
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
    procurement_suppliers,
    purchase_order_drafts,
    warehouse_inventory_sync_jobs,
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
        supplier_count = connection.execute(text("select count(*) from procurement_suppliers")).scalar_one()
        draft_count = connection.execute(text("select count(*) from purchase_order_drafts")).scalar_one()
        sync_job_count = connection.execute(text("select count(*) from warehouse_inventory_sync_jobs")).scalar_one()

    assert warehouse_count == 2
    assert location_count == 6
    assert category_count == 5
    assert item_count == 8
    assert batch_count == 10
    assert replenishment_count == 0
    assert supplier_count == 7
    assert draft_count == 0
    assert sync_job_count == 0


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
    assert rows[0]["batch_no"] == "BATCH-20260501"


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
            "status": "pending_procurement_review",
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
    listed = repository.list_replenishment_requests(status="pending_procurement_review")

    assert created["request_id"] == "REQ-2001"
    assert listed == [created]


def test_warehouse_repository_reads_suppliers_and_persists_purchase_order_drafts(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)

    supplier = repository.get_default_supplier("item_vinda_tissue")
    created = repository.create_purchase_order_draft(
        {
            "po_draft_id": "POD-5001",
            "request_id": "REQ-2001",
            "supplier_id": supplier["supplier_id"],
            "supplier_name": supplier["supplier_name"],
            "item_id": "item_vinda_tissue",
            "quantity": 104,
            "unit_price": supplier["unit_price"],
            "currency": supplier["currency"],
            "estimated_total_price": 832,
            "lead_time_days": supplier["lead_time_days"],
            "estimated_arrival_date": "2026-05-27",
            "status": "draft",
            "created_by": "procurement:user-001",
            "created_at": "2026-05-24T21:00:00+08:00",
            "updated_at": "2026-05-24T21:00:00+08:00",
        }
    )
    listed = repository.list_purchase_order_drafts(request_id="REQ-2001")

    assert supplier["supplier_name"] == "深圳纸品供应商"
    assert created["po_draft_id"] == "POD-5001"
    assert listed == [created]


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
