from pathlib import Path

from sqlalchemy import create_engine, text

from app.store import FIXTURE_DIR
from app.warehouse_store import WarehouseRepository, init_warehouse_schema, seed_warehouse_fixtures


def test_seed_warehouse_fixtures_populates_postgres_shape_tables(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")

    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)

    with engine.connect() as connection:
        inventory_count = connection.execute(text("select count(*) from warehouse_inventory")).scalar_one()
        location_count = connection.execute(text("select count(*) from warehouse_locations")).scalar_one()
        exception_count = connection.execute(text("select count(*) from warehouse_exceptions")).scalar_one()

    assert inventory_count == 8
    assert location_count == 12
    assert exception_count == 8


def test_warehouse_repository_reads_inventory_locations_and_exceptions(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'warehouse.db'}")
    init_warehouse_schema(engine)
    seed_warehouse_fixtures(engine, FIXTURE_DIR)
    repository = WarehouseRepository(engine)

    inventory = repository.get_inventory("sku_bag_1")
    locations = repository.list_locations_for_sku("sku_bag_1")
    open_exceptions = repository.list_exceptions_for_sku("sku_bag_1", status="open")

    assert inventory == {
        "sku": "sku_bag_1",
        "available": 5,
        "reserved": 3,
        "pending_orders": 9,
        "reorder_threshold": 15,
    }
    assert [item["bin"] for item in locations] == ["A-01-03", "Q-02-01"]
    assert open_exceptions[0]["exception_id"] == "wh_exc_100"
