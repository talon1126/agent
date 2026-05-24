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
    seed_warehouse_fixtures,
    storage_locations,
    warehouses,
)


WAREHOUSE_TABLES = [warehouses, storage_locations, categories, items, inventory_batches]


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

    assert warehouse_count == 2
    assert location_count == 6
    assert category_count == 5
    assert item_count == 8
    assert batch_count == 10


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
