import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, select, text
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

WAREHOUSE_TABLE_COMMENTS = {
    "warehouse_inventory": "仓储库存主表，按 SKU 保存可用库存、预留库存、待履约订单和补货阈值。",
    "warehouse_locations": "仓储库位明细表，记录 SKU 在不同仓库、区域、库位中的数量和状态。",
    "warehouse_exceptions": "仓储异常表，记录库存差异、断货、质检冻结等履约风险事件。",
}

WAREHOUSE_COLUMN_COMMENTS = {
    "warehouse_inventory": {
        "sku": "商品 SKU，库存记录的唯一业务标识。",
        "available": "当前可用库存数量。",
        "reserved": "已被订单或作业预留的库存数量。",
        "pending_orders": "等待履约的订单数量。",
        "reorder_threshold": "补货预警阈值，低于该值时建议采购或调拨。",
    },
    "warehouse_locations": {
        "id": "库位明细自增主键。",
        "sku": "商品 SKU，对应仓储库存主表。",
        "warehouse_id": "仓库编号，例如 wh_hk_1、wh_sz_1、wh_sg_1。",
        "warehouse_name": "仓库展示名称。",
        "zone": "仓库区域编号。",
        "bin": "具体库位编号。",
        "quantity": "该库位上的库存数量。",
        "status": "库位库存状态，例如 available、reserved、stockout、quality_hold。",
    },
    "warehouse_exceptions": {
        "exception_id": "仓储异常唯一编号。",
        "sku": "发生异常的商品 SKU。",
        "type": "异常类型，例如 stockout、quality_hold、stock_mismatch。",
        "severity": "异常严重程度，取值 high、medium、low。",
        "status": "异常处理状态，例如 open、closed。",
        "warehouse_id": "异常发生仓库编号。",
        "zone": "异常发生仓库区域。",
        "bin": "异常发生具体库位。",
        "message": "异常说明，供 Agent 和员工理解当前问题。",
        "recommended_action": "建议处理动作，例如 notify_procurement、quality_review。",
    },
}


def init_warehouse_schema(engine: Engine) -> None:
    metadata.create_all(
        engine,
        tables=[warehouse_inventory, warehouse_locations, warehouse_exceptions],
    )
    apply_warehouse_comments(engine)


def apply_warehouse_comments(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        for table_name, comment in WAREHOUSE_TABLE_COMMENTS.items():
            connection.execute(
                text(
                    f"COMMENT ON TABLE {_quote_identifier(table_name)} "
                    f"IS {_quote_literal(comment)}"
                ),
            )
        for table_name, column_comments in WAREHOUSE_COLUMN_COMMENTS.items():
            for column_name, comment in column_comments.items():
                connection.execute(
                    text(
                        "COMMENT ON COLUMN "
                        f"{_quote_identifier(table_name)}.{_quote_identifier(column_name)} "
                        f"IS {_quote_literal(comment)}"
                    ),
                )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


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
