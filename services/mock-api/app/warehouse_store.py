import logging
import os
import json
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine, func, inspect, select, text
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
    Column("batch_id", Integer, primary_key=True, autoincrement=True),
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

warehouse_inventory_sync_jobs = Table(
    "warehouse_inventory_sync_jobs",
    metadata,
    Column("job_id", String, primary_key=True),
    Column("team", String, nullable=False),
    Column("event", String, nullable=False, index=True),
    Column("po_draft_id", String, nullable=False, index=True),
    Column("request_id", String, nullable=False, index=True),
    Column("item_id", String, nullable=False, index=True),
    Column("warehouse_id", String, nullable=False, index=True),
    Column("warehouse_name", String, nullable=False),
    Column("location_code", String, nullable=True, index=True),
    Column("batch_no", String, nullable=False, index=True),
    Column("quantity", Integer, nullable=False),
    Column("next_action", String, nullable=False),
    Column("suggested_message", String, nullable=False),
    Column("status", String, nullable=False, index=True),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("processed_by", String, nullable=False, default=""),
    Column("processed_at", String, nullable=False, default=""),
    Column("result_json", Text, nullable=False, default="{}"),
    Column("error", Text, nullable=True),
)

procurement_suppliers = Table(
    "procurement_suppliers",
    metadata,
    Column("supplier_id", String, primary_key=True),
    Column("supplier_name", String, nullable=False),
    Column("item_id", String, nullable=False, index=True),
    Column("lead_time_days", Integer, nullable=False),
    Column("unit_price", Integer, nullable=False),
    Column("currency", String, nullable=False),
    Column("reliability_score", Integer, nullable=False),
)

purchase_order_drafts = Table(
    "purchase_order_drafts",
    metadata,
    Column("po_draft_id", String, primary_key=True),
    Column("request_id", String, nullable=False, index=True),
    Column("supplier_id", String, nullable=False, index=True),
    Column("supplier_name", String, nullable=False),
    Column("item_id", String, nullable=False, index=True),
    Column("quantity", Integer, nullable=False),
    Column("unit_price", Integer, nullable=False),
    Column("currency", String, nullable=False),
    Column("estimated_total_price", Integer, nullable=False),
    Column("lead_time_days", Integer, nullable=False),
    Column("estimated_arrival_date", String, nullable=False),
    Column("status", String, nullable=False, index=True),
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
    "procurement_suppliers": "采购供应商表，保存 mock 供应商、交期、价格和可靠性。",
    "purchase_order_drafts": "采购单草稿表，保存采购审核补货申请后生成的草稿单及到仓状态。",
    "warehouse_inventory_sync_jobs": "仓储库存同步任务表，保存采购到仓后需要 Warehouse Agent 同步飞书库存视图的待处理任务。",
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
        "batch_id": "库存批次自增整数主键。",
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
    "procurement_suppliers": {
        "supplier_id": "供应商编号。",
        "supplier_name": "供应商展示名称。",
        "item_id": "该供应商默认供应的商品编号。",
        "lead_time_days": "预计交期天数。",
        "unit_price": "采购单价，按 currency 表示。",
        "currency": "价格币种，例如 CNY。",
        "reliability_score": "供应商可靠性评分，0 到 100。",
    },
    "purchase_order_drafts": {
        "po_draft_id": "采购单草稿编号，例如 POD-5001。",
        "request_id": "关联的补货申请编号。",
        "supplier_id": "草稿选用的供应商编号。",
        "supplier_name": "草稿选用的供应商名称。",
        "item_id": "采购商品编号。",
        "quantity": "建议采购数量。",
        "unit_price": "采购单价，按 currency 表示。",
        "currency": "价格币种，例如 CNY。",
        "estimated_total_price": "预计采购总价。",
        "lead_time_days": "预计交期天数。",
        "estimated_arrival_date": "预计到达日期，格式为 YYYY-MM-DD。",
        "status": "草稿状态，例如 draft、received_at_warehouse。",
        "created_by": "创建草稿的用户或系统身份。",
        "created_at": "草稿创建时间。",
        "updated_at": "草稿更新时间。",
    },
    "warehouse_inventory_sync_jobs": {
        "job_id": "库存同步任务编号，例如 WSJ-POD-5001。",
        "team": "消费任务的团队，例如 warehouse。",
        "event": "任务事件类型，例如 warehouse_inventory_sync_requested。",
        "po_draft_id": "触发任务的采购单草稿编号。",
        "request_id": "关联的补货申请编号。",
        "item_id": "需要同步到飞书库存视图的商品编号。",
        "warehouse_id": "需要同步的仓库编号。",
        "warehouse_name": "需要同步的仓库名称。",
        "location_code": "需要同步的具体库位。",
        "batch_no": "到仓入库批次号。",
        "quantity": "本次到仓数量。",
        "next_action": "Warehouse Agent 下一步动作。",
        "suggested_message": "建议发送给 Warehouse Agent 的同步消息。",
        "status": "任务状态，例如 pending、completed、failed。",
        "created_by": "创建任务的用户或系统身份。",
        "created_at": "任务创建时间。",
        "updated_at": "任务更新时间。",
        "processed_by": "处理任务的用户或系统身份。",
        "processed_at": "任务处理时间。",
        "result_json": "任务处理结果 JSON 字符串。",
        "error": "任务失败原因，可为空。",
    },
}


def init_warehouse_schema(engine: Engine) -> None:
    ensure_inventory_batch_id_integer(engine)
    metadata.create_all(
        engine,
        tables=[
            warehouses,
            storage_locations,
            categories,
            items,
            inventory_batches,
            replenishment_requests,
            procurement_suppliers,
            purchase_order_drafts,
            warehouse_inventory_sync_jobs,
        ],
    )
    ensure_warehouse_schema_columns(engine)
    apply_warehouse_comments(engine)


def ensure_warehouse_schema_columns(engine: Engine) -> None:
    existing_columns = {
        column["name"]
        for column in inspect(engine).get_columns(purchase_order_drafts.name)
    }
    if "estimated_arrival_date" in existing_columns:
        return
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE purchase_order_drafts "
                "ADD COLUMN estimated_arrival_date VARCHAR NOT NULL DEFAULT ''"
            )
        )


def ensure_inventory_batch_id_integer(engine: Engine) -> None:
    inspector = inspect(engine)
    if not inspector.has_table(inventory_batches.name):
        return
    batch_id_column = next(
        (column for column in inspector.get_columns(inventory_batches.name) if column["name"] == "batch_id"),
        None,
    )
    if not batch_id_column:
        return
    column_type = str(batch_id_column["type"]).upper()
    if "INT" in column_type:
        return
    with engine.begin() as connection:
        connection.execute(text(f"DROP TABLE {_quote_identifier(inventory_batches.name)}"))


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


def load_inventory_batch_fixture_rows(fixture_dir: Path) -> list[dict[str, Any]]:
    rows = load_fixture_rows(fixture_dir, "inventory_batches.json")
    return [
        {key: value for key, value in row.items() if key != "batch_id"}
        for row in rows
    ]


def seed_warehouse_fixtures(engine: Engine, fixture_dir: Path) -> None:
    init_warehouse_schema(engine)
    with engine.begin() as connection:
        # Demo data is fixture-owned, so restart/reseed should converge the DB to fixtures.
        connection.execute(inventory_batches.delete())
        connection.execute(procurement_suppliers.delete())
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
            load_inventory_batch_fixture_rows(fixture_dir),
        )
        connection.execute(
            procurement_suppliers.insert(),
            load_fixture_rows(fixture_dir, "procurement_suppliers.json"),
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

    def get_replenishment_request(self, request_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(replenishment_requests).where(
                        replenishment_requests.c.request_id == request_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row else None

    def update_replenishment_request(
        self,
        request_id: str,
        *,
        status: str,
        updated_at: str,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        values: dict[str, Any] = {"status": status, "updated_at": updated_at}
        if reason is not None:
            values["reason"] = reason
        with self.engine.begin() as connection:
            connection.execute(
                replenishment_requests.update()
                .where(replenishment_requests.c.request_id == request_id)
                .values(**values)
            )
            row = (
                connection.execute(
                    select(replenishment_requests).where(
                        replenishment_requests.c.request_id == request_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row else None

    def list_replenishment_requests(self, *, status: str | None = None) -> list[dict[str, Any]]:
        statement = select(replenishment_requests)
        if status:
            statement = statement.where(replenishment_requests.c.status == status)
        statement = statement.order_by(replenishment_requests.c.created_at, replenishment_requests.c.request_id)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [dict(row) for row in rows]

    def get_inventory_batch_by_batch_no(self, batch_no: str) -> dict[str, Any] | None:
        rows = self.list_inventory_batches(batch_no=batch_no)
        return rows[0] if rows else None

    def create_inventory_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = {key: value for key, value in payload.items() if key != "batch_id"}
        with self.engine.begin() as connection:
            connection.execute(inventory_batches.insert().values(**values))
        batch = self.get_inventory_batch_by_batch_no(str(values["batch_no"]))
        return batch or values

    def get_default_supplier(self, item_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(procurement_suppliers).where(
                        procurement_suppliers.c.item_id == item_id
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    def count_purchase_order_drafts(self) -> int:
        with self.engine.connect() as connection:
            return int(connection.execute(select(func.count()).select_from(purchase_order_drafts)).scalar_one())

    def create_purchase_order_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.engine.begin() as connection:
            connection.execute(purchase_order_drafts.insert().values(**payload))
            row = (
                connection.execute(
                    select(purchase_order_drafts).where(
                        purchase_order_drafts.c.po_draft_id == payload["po_draft_id"]
                    )
                )
                .mappings()
                .one()
            )
        return dict(row)

    def get_purchase_order_draft(self, po_draft_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(purchase_order_drafts).where(
                        purchase_order_drafts.c.po_draft_id == po_draft_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row else None

    def update_purchase_order_draft_status(
        self,
        po_draft_id: str,
        *,
        status: str,
        updated_at: str,
    ) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            connection.execute(
                purchase_order_drafts.update()
                .where(purchase_order_drafts.c.po_draft_id == po_draft_id)
                .values(status=status, updated_at=updated_at)
            )
            row = (
                connection.execute(
                    select(purchase_order_drafts).where(
                        purchase_order_drafts.c.po_draft_id == po_draft_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row else None

    def list_purchase_order_drafts(self, *, request_id: str | None = None) -> list[dict[str, Any]]:
        statement = select(purchase_order_drafts)
        if request_id:
            statement = statement.where(purchase_order_drafts.c.request_id == request_id)
        statement = statement.order_by(purchase_order_drafts.c.created_at, purchase_order_drafts.c.po_draft_id)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [dict(row) for row in rows]

    def upsert_warehouse_inventory_sync_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = self._warehouse_inventory_sync_job_values(payload)
        with self.engine.begin() as connection:
            existing = (
                connection.execute(
                    select(warehouse_inventory_sync_jobs).where(
                        warehouse_inventory_sync_jobs.c.job_id == values["job_id"]
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing:
                status = existing["status"] if existing["status"] in {"pending", "processing"} else "pending"
                update_values = {
                    key: value
                    for key, value in values.items()
                    if key not in {"job_id", "created_by", "created_at"}
                }
                update_values["status"] = status
                update_values["processed_by"] = "" if status == "pending" else existing["processed_by"]
                update_values["processed_at"] = "" if status == "pending" else existing["processed_at"]
                update_values["result_json"] = "{}" if status == "pending" else existing["result_json"]
                update_values["error"] = None
                connection.execute(
                    warehouse_inventory_sync_jobs.update()
                    .where(warehouse_inventory_sync_jobs.c.job_id == values["job_id"])
                    .values(**update_values)
                )
            else:
                connection.execute(warehouse_inventory_sync_jobs.insert().values(**values))
            row = (
                connection.execute(
                    select(warehouse_inventory_sync_jobs).where(
                        warehouse_inventory_sync_jobs.c.job_id == values["job_id"]
                    )
                )
                .mappings()
                .one()
            )
        return self._format_warehouse_inventory_sync_job(row)

    def list_warehouse_inventory_sync_jobs(self, *, status: str | None = None) -> list[dict[str, Any]]:
        statement = select(warehouse_inventory_sync_jobs)
        if status:
            statement = statement.where(warehouse_inventory_sync_jobs.c.status == status)
        statement = statement.order_by(
            warehouse_inventory_sync_jobs.c.created_at,
            warehouse_inventory_sync_jobs.c.job_id,
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [self._format_warehouse_inventory_sync_job(row) for row in rows]

    def update_warehouse_inventory_sync_job(
        self,
        job_id: str,
        *,
        status: str,
        processed_by: str,
        processed_at: str,
        updated_at: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            connection.execute(
                warehouse_inventory_sync_jobs.update()
                .where(warehouse_inventory_sync_jobs.c.job_id == job_id)
                .values(
                    status=status,
                    processed_by=processed_by,
                    processed_at=processed_at,
                    updated_at=updated_at,
                    result_json=json.dumps(result or {}, ensure_ascii=False),
                    error=error,
                )
            )
            row = (
                connection.execute(
                    select(warehouse_inventory_sync_jobs).where(
                        warehouse_inventory_sync_jobs.c.job_id == job_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        return self._format_warehouse_inventory_sync_job(row) if row else None

    @staticmethod
    def _warehouse_inventory_sync_job_values(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_id": payload["job_id"],
            "team": payload["team"],
            "event": payload["event"],
            "po_draft_id": payload["po_draft_id"],
            "request_id": payload["request_id"],
            "item_id": payload["item_id"],
            "warehouse_id": payload["warehouse_id"],
            "warehouse_name": payload["warehouse_name"],
            "location_code": payload.get("location_code") or "",
            "batch_no": payload["batch_no"],
            "quantity": int(payload["quantity"]),
            "next_action": payload["next_action"],
            "suggested_message": payload["suggested_message"],
            "status": payload.get("status") or "pending",
            "created_by": payload["created_by"],
            "created_at": payload["created_at"],
            "updated_at": payload["updated_at"],
            "processed_by": payload.get("processed_by") or "",
            "processed_at": payload.get("processed_at") or "",
            "result_json": json.dumps(payload.get("result") or {}, ensure_ascii=False),
            "error": payload.get("error"),
        }

    @staticmethod
    def _format_warehouse_inventory_sync_job(row: Any) -> dict[str, Any]:
        item = dict(row)
        raw_result = item.pop("result_json", "{}") or "{}"
        try:
            item["result"] = json.loads(raw_result)
        except json.JSONDecodeError:
            item["result"] = {}
        return item


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
