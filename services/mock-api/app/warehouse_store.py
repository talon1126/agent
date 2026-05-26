import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, func, select, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("mock_api.warehouse_store")

metadata = MetaData()

warehouses = Table(
    "warehouses",
    metadata,
    Column("warehouse_id", String, primary_key=True),
    Column("warehouse_name", String, nullable=False),
    Column("city", String, nullable=False),
    Column("region", String, nullable=False),
    Column("status", String, nullable=False),
)

storage_locations = Table(
    "storage_locations",
    metadata,
    Column("location_id", String, primary_key=True),
    Column("warehouse_id", String, nullable=False, index=True),
    Column("location_code", String, nullable=False, index=True),
    Column("zone", String, nullable=False),
    Column("temperature_zone", String, nullable=False),
    Column("capacity_units", Integer, nullable=False, default=0),
)

categories = Table(
    "categories",
    metadata,
    Column("category_id", String, primary_key=True),
    Column("category_name", String, nullable=False),
    Column("storage_requirement", String, nullable=False),
)

items = Table(
    "items",
    metadata,
    Column("item_id", String, primary_key=True),
    Column("category_id", String, nullable=False, index=True),
    Column("item_name", String, nullable=False),
    Column("brand", String, nullable=False),
    Column("spec", String, nullable=False),
    Column("unit", String, nullable=False),
    Column("barcode", String, nullable=False),
)

inventory_batches = Table(
    "inventory_batches",
    metadata,
    Column("batch_id", String, primary_key=True),
    Column("warehouse_id", String, nullable=False, index=True),
    Column("location_code", String, nullable=False, index=True),
    Column("item_id", String, nullable=False, index=True),
    Column("batch_no", String, nullable=False, index=True),
    Column("production_date", String, nullable=False),
    Column("expiry_date", String, nullable=False),
    Column("quantity_on_hand", Integer, nullable=False, default=0),
    Column("quantity_reserved", Integer, nullable=False, default=0),
    Column("reorder_threshold", Integer, nullable=False, default=0),
    Column("storage_status", String, nullable=False),
)

replenishment_requests = Table(
    "replenishment_requests",
    metadata,
    Column("request_id", String, primary_key=True),
    Column("source", String, nullable=False),
    Column("status", String, nullable=False, index=True),
    Column("warehouse_id", String, nullable=False, index=True),
    Column("warehouse_name", String, nullable=False),
    Column("location_code", String, nullable=True, index=True),
    Column("item_id", String, nullable=False, index=True),
    Column("item_name", String, nullable=False),
    Column("category_id", String, nullable=False, index=True),
    Column("category_name", String, nullable=False),
    Column("current_quantity", Integer, nullable=False),
    Column("reorder_threshold", Integer, nullable=False),
    Column("suggested_quantity", Integer, nullable=False),
    Column("reason", String, nullable=False),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

WAREHOUSE_TABLE_COMMENTS = {
    "warehouses": "仓库主数据表，保存企业仓库的编号、名称、城市和启用状态。",
    "storage_locations": "具体库位表，保存仓库内 A1、B1、C1 等可存储位置及容量属性。",
    "categories": "商品分类表，保存纸品、乳制品、饮料等业务分类和存储要求。",
    "items": "商品主数据表，保存每个商品的名称、品牌、规格、单位和条码。",
    "inventory_batches": "批次库存事实表，按仓库、库位、商品和批次保存库存数量与保质期。",
    "replenishment_requests": "补货申请表，保存仓储发现低库存后交给采购审核的结构化需求。",
}

WAREHOUSE_COLUMN_COMMENTS = {
    "warehouses": {
        "warehouse_id": "仓库编号，例如 wh_sz_1、wh_hk_1。",
        "warehouse_name": "仓库展示名称，例如深圳仓、香港仓。",
        "city": "仓库所在城市。",
        "region": "仓库所属区域。",
        "status": "仓库启用状态，例如 active。",
    },
    "storage_locations": {
        "location_id": "库位唯一编号。",
        "warehouse_id": "库位所属仓库编号。",
        "location_code": "员工可识别的具体库位编号，例如 A1、B1、C1。",
        "zone": "库位所属仓库区域。",
        "temperature_zone": "库位温区，例如 ambient、chilled。",
        "capacity_units": "库位最大容量，按商品单位折算。",
    },
    "categories": {
        "category_id": "商品分类编号，例如 paper、dairy。",
        "category_name": "商品分类展示名称，例如纸品、乳制品。",
        "storage_requirement": "该分类默认存储要求，例如常温或冷藏。",
    },
    "items": {
        "item_id": "商品主数据编号。",
        "category_id": "商品所属分类编号。",
        "item_name": "商品名称，例如维达纸巾、纯牛奶。",
        "brand": "商品品牌。",
        "spec": "商品规格。",
        "unit": "库存计量单位。",
        "barcode": "商品条码。",
    },
    "inventory_batches": {
        "batch_id": "库存批次唯一编号。",
        "warehouse_id": "批次库存所在仓库编号。",
        "location_code": "批次库存所在具体库位编号。",
        "item_id": "批次库存对应商品编号。",
        "batch_no": "业务批次号，用于追踪入库批次。",
        "production_date": "生产日期。",
        "expiry_date": "保质期到期日期。",
        "quantity_on_hand": "账面库存数量。",
        "quantity_reserved": "已被订单或作业预留的库存数量。",
        "reorder_threshold": "补货预警阈值。",
        "storage_status": "库存存储状态，例如 available、quality_hold。",
    },
    "replenishment_requests": {
        "request_id": "补货申请编号，例如 REQ-1001。",
        "source": "申请来源，例如 warehouse。",
        "status": "申请状态，例如 pending_procurement_review。",
        "warehouse_id": "触发补货申请的仓库编号。",
        "warehouse_name": "触发补货申请的仓库名称。",
        "location_code": "触发补货申请的具体库位，可为空。",
        "item_id": "需要补货的商品编号。",
        "item_name": "需要补货的商品名称。",
        "category_id": "商品分类编号。",
        "category_name": "商品分类名称。",
        "current_quantity": "当前可用库存数量。",
        "reorder_threshold": "补货预警阈值。",
        "suggested_quantity": "系统建议补货数量。",
        "reason": "生成补货申请的业务原因。",
        "created_by": "创建申请的用户或系统身份。",
        "created_at": "申请创建时间。",
        "updated_at": "申请更新时间。",
    },
}


def init_warehouse_schema(engine: Engine) -> None:
    metadata.create_all(
        engine,
        tables=[
            warehouses,
            storage_locations,
            categories,
            items,
            inventory_batches,
            replenishment_requests,
        ],
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
        connection.execute(inventory_batches.delete())
        connection.execute(items.delete())
        connection.execute(categories.delete())
        connection.execute(storage_locations.delete())
        connection.execute(warehouses.delete())
        connection.execute(warehouses.insert(), load_fixture_rows(fixture_dir, "warehouses.json"))
        connection.execute(
            storage_locations.insert(),
            load_fixture_rows(fixture_dir, "storage_locations.json"),
        )
        connection.execute(categories.insert(), load_fixture_rows(fixture_dir, "categories.json"))
        connection.execute(items.insert(), load_fixture_rows(fixture_dir, "items.json"))
        connection.execute(
            inventory_batches.insert(),
            load_fixture_rows(fixture_dir, "inventory_batches.json"),
        )


class WarehouseRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def list_inventory_batches(
        self,
        *,
        item_id: str | None = None,
        warehouse_id: str | None = None,
        location_code: str | None = None,
        category_id: str | None = None,
        batch_no: str | None = None,
    ) -> list[dict[str, Any]]:
        statement = (
            select(
                inventory_batches,
                warehouses.c.warehouse_name,
                warehouses.c.city,
                storage_locations.c.zone,
                storage_locations.c.temperature_zone,
                categories.c.category_id,
                categories.c.category_name,
                categories.c.storage_requirement,
                items.c.item_name,
                items.c.brand,
                items.c.spec,
                items.c.unit,
                items.c.barcode,
            )
            .join(warehouses, warehouses.c.warehouse_id == inventory_batches.c.warehouse_id)
            .join(
                storage_locations,
                (storage_locations.c.warehouse_id == inventory_batches.c.warehouse_id)
                & (storage_locations.c.location_code == inventory_batches.c.location_code),
            )
            .join(items, items.c.item_id == inventory_batches.c.item_id)
            .join(categories, categories.c.category_id == items.c.category_id)
        )
        if item_id:
            statement = statement.where(inventory_batches.c.item_id == item_id)
        if warehouse_id:
            statement = statement.where(inventory_batches.c.warehouse_id == warehouse_id)
        if location_code:
            statement = statement.where(inventory_batches.c.location_code == location_code)
        if category_id:
            statement = statement.where(categories.c.category_id == category_id)
        if batch_no:
            statement = statement.where(inventory_batches.c.batch_no == batch_no)
        statement = statement.order_by(
            inventory_batches.c.warehouse_id,
            inventory_batches.c.location_code,
            items.c.item_name,
            inventory_batches.c.batch_no,
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [dict(row) for row in rows]

    def count_replenishment_requests(self) -> int:
        with self.engine.connect() as connection:
            return int(connection.execute(select(func.count()).select_from(replenishment_requests)).scalar_one())

    def create_replenishment_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.engine.begin() as connection:
            connection.execute(replenishment_requests.insert().values(**payload))
            row = (
                connection.execute(
                    select(replenishment_requests).where(
                        replenishment_requests.c.request_id == payload["request_id"]
                    )
                )
                .mappings()
                .one()
            )
        return dict(row)

    def list_replenishment_requests(self, *, status: str | None = None) -> list[dict[str, Any]]:
        statement = select(replenishment_requests)
        if status:
            statement = statement.where(replenishment_requests.c.status == status)
        statement = statement.order_by(replenishment_requests.c.created_at, replenishment_requests.c.request_id)
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
