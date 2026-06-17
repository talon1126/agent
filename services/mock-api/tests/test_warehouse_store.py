from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.store import FIXTURE_DIR
from app.warehouse_store import (
    WAREHOUSE_COLUMN_COMMENTS,
    WAREHOUSE_TABLE_COMMENTS,
    WarehouseRepository,
    build_item_pg_search_index_sql,
    build_item_search_sql,
    _quote_literal,
    cart_items,
    category_rank_snapshots,
    categories,
    delivery_addresses,
    flash_sale_claims,
    flash_sales,
    init_warehouse_schema,
    inventory_movements,
    inventory_location_balances,
    inventory_batches,
    item_rank_events,
    item_reviews,
    items,
    delivery_providers,
    order_items,
    orders,
    procurement_suppliers,
    purchase_orders,
    replenishment_requests,
    seed_warehouse_fixtures,
    storage_locations,
    users,
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
    users,
    delivery_addresses,
    cart_items,
    flash_sales,
    flash_sale_claims,
    item_rank_events,
    category_rank_snapshots,
    item_reviews,
    procurement_suppliers,
    purchase_orders,
    warehouse_inventory_sync_jobs,
    inventory_location_balances,
    inventory_movements,
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
        movement_count = connection.execute(text("select count(*) from inventory_movements")).scalar_one()
        order_count = connection.execute(text("select count(*) from orders")).scalar_one()
        order_item_count = connection.execute(text("select count(*) from order_items")).scalar_one()
        user_count = connection.execute(text("select count(*) from users")).scalar_one()
        delivery_address_count = connection.execute(text("select count(*) from delivery_addresses")).scalar_one()
        flash_sale_count = connection.execute(text("select count(*) from flash_sales")).scalar_one()
        active_flash_sale_count = connection.execute(
            text("select count(*) from flash_sales where status = 'active'")
        ).scalar_one()
        flash_sale_claim_count = connection.execute(text("select count(*) from flash_sale_claims")).scalar_one()
        rank_event_count = connection.execute(text("select count(*) from item_rank_events")).scalar_one()
        rank_snapshot_count = connection.execute(text("select count(*) from category_rank_snapshots")).scalar_one()
        item_review_count = connection.execute(text("select count(*) from item_reviews")).scalar_one()
        default_address = connection.execute(
            text("select address from delivery_addresses where user_id = 1 and is_default = 1")
        ).scalar_one()
        cart_item_count = connection.execute(text("select count(*) from cart_items")).scalar_one()
        milk_price = connection.execute(text("select price from items where item_id = 'item_milk_pure'")).scalar_one()

    assert warehouse_count == 2
    assert location_count == 6
    assert category_count == 9
    assert item_count == 16
    assert batch_count == 10
    assert replenishment_count == 0
    assert delivery_provider_count == 3
    assert supplier_count == 7
    assert purchase_order_count == 0
    assert sync_job_count == 0
    assert balance_count == 10
    assert movement_count == 0
    assert order_count == 0
    assert order_item_count == 0
    assert user_count == 2
    assert delivery_address_count == 2
    assert flash_sale_count == 8
    assert active_flash_sale_count == 7
    assert flash_sale_claim_count == 0
    assert rank_event_count >= 16
    assert rank_snapshot_count >= 9
    assert item_review_count == 4
    assert default_address == "广东省深圳市南山区示例路 100 号"
    assert cart_item_count == 0
    assert float(milk_price) == 18.4


def test_warehouse_repository_lists_and_creates_item_reviews(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)

    reviews = repository.list_item_reviews("item_milk_pure", limit=20, offset=0)
    summary = repository.item_review_summary("item_milk_pure")
    created = repository.create_item_review(
        "item_milk_pure",
        {
            "user_id": 1,
            "rating": 5,
            "title": "Good value",
            "content": "Fresh taste and good price for a family pack.",
        },
        created_at="2026-06-03T10:00:00+08:00",
    )

    assert len(reviews) == 2
    assert reviews[0]["item_id"] == "item_milk_pure"
    assert reviews[0]["rating"] == 5
    assert summary == {"average_rating": 4.5, "review_count": 2}
    assert created == {
        "id": 5,
        "item_id": "item_milk_pure",
        "user_id": 1,
        "rating": 5,
        "title": "Good value",
        "content": "Fresh taste and good price for a family pack.",
        "created_at": "2026-06-03T10:00:00+08:00",
        "updated_at": "2026-06-03T10:00:00+08:00",
    }
    assert repository.item_review_summary("item_milk_pure") == {
        "average_rating": 4.7,
        "review_count": 3,
    }


def test_warehouse_repository_lists_delivery_addresses(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)

    addresses = repository.list_delivery_addresses(1)

    assert addresses == [
        {
            "id": 1,
            "user_id": 1,
            "receiver_name": "Talon 测试用户",
            "phone_number": "13800000001",
            "address": "广东省深圳市南山区示例路 100 号",
            "is_default": 1,
        }
    ]


def test_warehouse_repository_persists_flash_sales_and_claims(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    repository = WarehouseRepository(engine)

    sale = repository.create_flash_sale(
        {
            "item_id": "item_milk_pure",
            "sale_price": 9.9,
            "stock_limit": 2,
            "status": "active",
            "starts_at": "2026-06-02T00:00:00+08:00",
            "ends_at": "2026-06-03T00:00:00+08:00",
            "created_at": "2026-06-02T00:00:00+08:00",
            "updated_at": "2026-06-02T00:00:00+08:00",
        }
    )
    claim = repository.create_flash_sale_claim_pending(
        flash_sale_id=sale["id"],
        user_id=1,
        item_id="item_milk_pure",
        created_at="2026-06-02T01:00:00+08:00",
    )
    ordered = repository.mark_flash_sale_claim_ordered(
        claim["id"],
        order_id="ORD-CODEX-FLASH-1",
        updated_at="2026-06-02T01:00:01+08:00",
    )

    assert sale["id"] == 1
    assert float(sale["sale_price"]) == 9.9
    assert claim["status"] == "pending"
    assert ordered["status"] == "ordered"
    assert ordered["order_id"] == "ORD-CODEX-FLASH-1"
    assert repository.get_flash_sale_claim(flash_sale_id=1, user_id=1)["order_id"] == "ORD-CODEX-FLASH-1"

    with pytest.raises(IntegrityError):
        repository.create_flash_sale_claim_pending(
            flash_sale_id=sale["id"],
            user_id=1,
            item_id="item_milk_pure",
            created_at="2026-06-02T01:00:02+08:00",
        )


def test_warehouse_repository_lists_flash_sales_by_status(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    repository = WarehouseRepository(engine)

    repository.create_flash_sale(
        {
            "item_id": "item_milk_pure",
            "sale_price": 9.9,
            "stock_limit": 5,
            "status": "active",
            "starts_at": "2026-06-01T00:00:00+00:00",
            "ends_at": "2099-06-03T00:00:00+00:00",
            "created_at": "2026-06-01T00:00:00+00:00",
            "updated_at": "2026-06-01T00:00:00+00:00",
        }
    )
    repository.create_flash_sale(
        {
            "item_id": "item_cola_zero",
            "sale_price": 4.9,
            "stock_limit": 8,
            "status": "draft",
            "starts_at": "2026-06-04T00:00:00+00:00",
            "ends_at": "2099-06-03T00:00:00+00:00",
            "created_at": "2026-06-01T00:00:00+00:00",
            "updated_at": "2026-06-01T00:00:00+00:00",
        }
    )

    sales = repository.list_flash_sales(status="active", limit=10)

    assert len(sales) == 1
    assert sales[0]["item_id"] == "item_milk_pure"
    assert sales[0]["status"] == "active"


def test_warehouse_repository_rebuilds_category_rankings_from_rank_events(tmp_path: Path) -> None:
    """Protect the F8 ranking contract from drifting back to static frontend data.

    The repository owns durable ranking facts and snapshots. Rebuilding should
    aggregate item events into deterministic category rankings, then expose rows
    with item display fields so routers and frontend pages do not need separate
    item lookups for the common read path.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)

    repository.record_item_rank_event(
        item_id="item_wireless_earbuds",
        event_type="purchase",
        user_id=1,
        occurred_at="2026-06-17T10:00:00+08:00",
    )
    repository.record_item_rank_event(
        item_id="item_smart_tv_43",
        event_type="view",
        user_id=1,
        occurred_at="2026-06-17T10:01:00+08:00",
    )

    rebuilt = repository.rebuild_category_rankings(
        category_id="electronics",
        rank_type="hot",
        window_type="all_time",
        limit=10,
        generated_at="2026-06-17T10:05:00+08:00",
    )
    ranking = repository.get_category_ranking(
        category_id="electronics",
        rank_type="hot",
        window_type="all_time",
        limit=10,
    )

    assert rebuilt[0]["item_id"] == "item_wireless_earbuds"
    assert rebuilt[0]["rank"] == 1
    assert rebuilt[0]["score"] > rebuilt[1]["score"]
    assert ranking == rebuilt
    assert ranking[0]["item_name"]
    assert isinstance(ranking[0]["price"], float)


def test_warehouse_repository_gets_ranked_items_by_cached_ids(tmp_path: Path) -> None:
    """Ensure Redis ZSET cache hits can hydrate display cards from PostgreSQL.

    Redis only stores `item_id` scores. The repository must preserve the cached
    item order while joining current item facts and category metadata.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)

    rows = repository.get_ranked_items_by_ids(
        ["item_smart_tv_43", "item_wireless_earbuds"],
        scores={"item_smart_tv_43": 42.0, "item_wireless_earbuds": 39.0},
    )

    assert [row["item_id"] for row in rows] == ["item_smart_tv_43", "item_wireless_earbuds"]
    assert rows[0]["rank"] == 1
    assert rows[0]["score"] == 42.0
    assert rows[0]["category_id"] == "electronics"


def test_warehouse_repository_home_hot_preserves_category_snapshot_rank(tmp_path: Path) -> None:
    """Keep homepage hot labels aligned with the category leaderboard rank.

    The home rail sorts products from every category by score, but each label is
    still rendered as "#N in Category". The repository must therefore return the
    rank stored in `category_rank_snapshots` instead of re-numbering the
    cross-category result set.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                update category_rank_snapshots
                set rank = 2, score = 999
                where item_id = 'item_wireless_earbuds'
                  and category_id = 'electronics'
                  and rank_type = 'hot'
                  and window_type = 'all_time'
                """
            )
        )

    rows = repository.list_home_hot_rankings(rank_type="hot", window_type="all_time", limit=1)

    assert rows[0]["item_id"] == "item_wireless_earbuds"
    assert rows[0]["rank"] == 2


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


def test_item_search_sql_uses_pg_search_bm25_without_like() -> None:
    statement = str(build_item_search_sql())

    assert "search_text &&&" in statement
    assert "pdb.score" in statement
    assert "LIKE" not in statement.upper()


def test_warehouse_repository_searches_items_by_category_without_keyword(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)

    rows = repository.search_items(category_id="electronics")

    assert [item["item_id"] for item in rows] == [
        "item_smart_tv_43",
        "item_wireless_earbuds",
    ]
    assert {item["category_id"] for item in rows} == {"electronics"}
    assert all(isinstance(item["price"], float) for item in rows)


def test_item_pg_search_index_uses_chinese_compatible_tokenizer() -> None:
    statement = build_item_pg_search_index_sql()

    assert "USING bm25" in statement
    assert "search_text::pdb.chinese_compatible" in statement
    assert "key_field='item_id'" in statement


def test_warehouse_repository_gets_item_detail_from_items_table(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)

    item = repository.get_item_detail("item_milk_pure")

    assert item["item_id"] == "item_milk_pure"
    assert item["item_name"] == "纯牛奶"
    assert item["brand"]
    assert item["spec"]
    assert item["category_id"] == "dairy"
    assert isinstance(item["price"], float)
    assert item["unit"]
    assert item["barcode"]


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
            "status": "unpaid",
            "delivery_provider_id": "sf",
            "delivery_provider_name": "顺丰",
            "courier_phone": "13800000001",
            "tracking_no": "SF1001",
            "shipping_address": "广东省深圳市",
            "shipping_province": "广东省",
            "shipping_city": "深圳市",
            "selected_warehouse_id": "wh_sz_1",
            "selected_warehouse_name": "深圳仓",
            "expires_at": "2026-05-28T10:30:00+00:00",
            "release_reason": "",
            "items": [
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
    assert created["order"]["status"] == "unpaid"
    assert created["order"]["delivery_provider_name"] == "顺丰"
    assert "released_at" not in created["order"]
    assert "requested_items" not in created["order"]
    assert [item["status"] for item in created["items"]] == ["unpaid"]
    balances = repository.list_location_balances(item_id="item_vinda_tissue", warehouse_id="wh_sz_1")
    assert sum(item["quantity_on_hand"] for item in balances) == 136

    candidates = repository.list_order_fulfillment_candidates("ORD-CODEX-DB-1")

    assert candidates["recommended_warehouse_id"] == "wh_sz_1"
    assert candidates["candidates"][0]["can_fulfill"] is True

    paid_before_review = repository.pay_order(
        "ORD-CODEX-DB-1",
        updated_by="customer",
        updated_at="2026-05-28T10:00:20+00:00",
    )

    assert paid_before_review["order"]["status"] == "pending_fulfillment_review"
    assert [item["status"] for item in paid_before_review["items"]] == ["pending_fulfillment_review"]

    confirmed = repository.confirm_order_fulfillment(
        "ORD-CODEX-DB-1",
        warehouse_id="wh_sz_1",
        delivery_provider_id="jd",
        updated_by="warehouse-agent",
        updated_at="2026-05-28T10:00:30+00:00",
    )

    assert confirmed["order"]["status"] == "pending_shipment"
    assert confirmed["order"]["delivery_provider_id"] == "jd"
    assert confirmed["order"]["delivery_provider_name"] == "京东"
    assert [item["status"] for item in confirmed["items"]] == ["pending_shipment", "pending_shipment"]
    balances = repository.list_location_balances(item_id="item_vinda_tissue", warehouse_id="wh_sz_1")
    assert sum(item["quantity_on_hand"] for item in balances) == 116

    assert [item["batch_no"] for item in confirmed["items"]] == ["BATCH-20260401", "BATCH-20260501"]
    assert [item["quantity"] for item in confirmed["items"]] == [16, 4]
    assert all(item["status"] == "pending_shipment" for item in confirmed["items"])
    balances = repository.list_location_balances(item_id="item_vinda_tissue", warehouse_id="wh_sz_1")
    assert sum(item["quantity_on_hand"] for item in balances) == 116

    batch = repository.get_inventory_batch_by_batch_no("BATCH-20260401")
    assert batch["quantity_on_hand"] == 16

    returned = repository.return_order(
        "ORD-CODEX-DB-1",
        updated_by="warehouse-agent",
        updated_at="2026-05-28T10:10:00+00:00",
    )

    assert returned["order"]["status"] == "returned"
    balances = repository.list_location_balances(item_id="item_vinda_tissue", warehouse_id="wh_sz_1")
    assert sum(item["quantity_on_hand"] for item in balances) == 136

    movements = repository.list_inventory_movements(order_id="ORD-CODEX-DB-1")
    assert [item["movement_type"] for item in movements] == ["order_fulfillment_confirmed", "order_returned"]
    assert [item["quantity_delta"] for item in movements] == [-20, 20]
    assert "batch_no" not in movements[0]


def test_warehouse_repository_releases_expired_unpaid_orders_once(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)
    repository.create_order(
        {
            "order_id": "ORD-CODEX-DB-EXPIRED",
            "customer_id": "cus_100",
            "status": "unpaid",
            "delivery_provider_id": "sf",
            "delivery_provider_name": "顺丰",
            "courier_phone": "",
            "tracking_no": "SFEXPIRED",
            "shipping_address": "广东省深圳市",
            "shipping_province": "广东省",
            "shipping_city": "深圳市",
            "selected_warehouse_id": "wh_sz_1",
            "selected_warehouse_name": "深圳仓",
            "expires_at": "2026-05-28T10:30:00+00:00",
            "release_reason": "",
            "items": [
                {"item_id": "item_vinda_tissue", "warehouse_id": "wh_sz_1", "quantity": 20}
            ],
            "created_by": "warehouse-agent",
            "created_at": "2026-05-28T10:00:00+00:00",
            "updated_at": "2026-05-28T10:00:00+00:00",
            "paid_at": "",
            "shipped_at": "",
            "arrived_at": "",
            "cancelled_at": "",
            "returned_at": "",
        }
    )
    released = repository.release_expired_orders(
        processed_by="warehouse-timeout-job",
        now="2026-05-28T10:31:00+00:00",
    )
    released_again = repository.release_expired_orders(
        processed_by="warehouse-timeout-job",
        now="2026-05-28T10:32:00+00:00",
    )

    assert [item["order_id"] for item in released] == ["ORD-CODEX-DB-EXPIRED"]
    assert released[0]["status"] == "canceled"
    assert released_again == []
    balances = repository.list_location_balances(item_id="item_vinda_tissue", warehouse_id="wh_sz_1")
    assert sum(item["quantity_on_hand"] for item in balances) == 136
    movements = repository.list_inventory_movements(order_id="ORD-CODEX-DB-EXPIRED")
    assert movements == []
