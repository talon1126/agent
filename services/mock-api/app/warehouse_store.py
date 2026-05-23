import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, select
from sqlalchemy.engine import Engine

logger = logging.getLogger("mock_api.warehouse_store")

metadata = MetaData()

warehouse_inventory = Table(
    "warehouse_inventory",
    metadata,
    Column("sku", String, primary_key=True),
    Column("available", Integer, nullable=False, default=0),
    Column("reserved", Integer, nullable=False, default=0),
    Column("pending_orders", Integer, nullable=False, default=0),
    Column("reorder_threshold", Integer, nullable=False, default=0),
)

warehouse_locations = Table(
    "warehouse_locations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("sku", String, nullable=False, index=True),
    Column("warehouse_id", String, nullable=False),
    Column("warehouse_name", String, nullable=False),
    Column("zone", String, nullable=False),
    Column("bin", String, nullable=False),
    Column("quantity", Integer, nullable=False, default=0),
    Column("status", String, nullable=False),
)

warehouse_exceptions = Table(
    "warehouse_exceptions",
    metadata,
    Column("exception_id", String, primary_key=True),
    Column("sku", String, nullable=False, index=True),
    Column("type", String, nullable=False),
    Column("severity", String, nullable=False),
    Column("status", String, nullable=False),
    Column("warehouse_id", String, nullable=False),
    Column("zone", String, nullable=False),
    Column("bin", String, nullable=False),
    Column("message", String, nullable=False),
    Column("recommended_action", String, nullable=False),
)


def init_warehouse_schema(engine: Engine) -> None:
    metadata.create_all(
        engine,
        tables=[warehouse_inventory, warehouse_locations, warehouse_exceptions],
    )


def load_fixture_rows(fixture_dir: Path, name: str) -> list[dict[str, Any]]:
    import json

    with (fixture_dir / "data" / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def seed_warehouse_fixtures(engine: Engine, fixture_dir: Path) -> None:
    init_warehouse_schema(engine)
    with engine.begin() as connection:
        # Demo data is fixture-owned, so restart/reseed should converge the DB to fixtures.
        connection.execute(warehouse_exceptions.delete())
        connection.execute(warehouse_locations.delete())
        connection.execute(warehouse_inventory.delete())
        connection.execute(warehouse_inventory.insert(), load_fixture_rows(fixture_dir, "inventory.json"))
        connection.execute(
            warehouse_locations.insert(),
            load_fixture_rows(fixture_dir, "warehouse_locations.json"),
        )
        connection.execute(
            warehouse_exceptions.insert(),
            load_fixture_rows(fixture_dir, "warehouse_exceptions.json"),
        )


class WarehouseRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def get_inventory(self, sku: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(warehouse_inventory).where(warehouse_inventory.c.sku == sku)
            ).mappings().first()
        return dict(row) if row else None

    def list_locations_for_sku(self, sku: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(warehouse_locations)
                .where(warehouse_locations.c.sku == sku)
                .order_by(warehouse_locations.c.id)
            ).mappings().all()
        return [
            {key: value for key, value in dict(row).items() if key != "id"}
            for row in rows
        ]

    def list_exceptions_for_sku(self, sku: str, status: str | None = None) -> list[dict[str, Any]]:
        statement = select(warehouse_exceptions).where(warehouse_exceptions.c.sku == sku)
        if status:
            statement = statement.where(warehouse_exceptions.c.status == status)
        statement = statement.order_by(warehouse_exceptions.c.exception_id)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [dict(row) for row in rows]


def create_warehouse_repository_from_env(fixture_dir: Path) -> WarehouseRepository | None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return None
    try:
        engine = create_engine(database_url, pool_pre_ping=True)
        seed_warehouse_fixtures(engine, fixture_dir)
        return WarehouseRepository(engine)
    except Exception as error:  # pragma: no cover - runtime safety fallback
        logger.warning("warehouse postgres unavailable, falling back to fixtures: %s", error)
        return None
