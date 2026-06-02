import logging
import os
import json
import hashlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    inspect,
    select,
    text,
)
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
    Column("price", Numeric(12, 2), nullable=False, default=0),
    Column("search_text", Text, nullable=False, default=""),
    Column("unit", String, nullable=False),
    Column("barcode", String, nullable=False),
    Column("shelf_life_days", Integer, nullable=False, default=365),
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

inventory_location_balances = Table(
    "inventory_location_balances",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("warehouse_id", String, nullable=False, index=True),
    Column("location_code", String, nullable=False, index=True),
    Column("item_id", String, nullable=False, index=True),
    Column("batch_no", String, nullable=False, index=True),
    Column("production_date", String, nullable=False),
    Column("expiry_date", String, nullable=False),
    Column("quantity_on_hand", Integer, nullable=False, default=0),
    Column("reorder_threshold", Integer, nullable=False, default=0),
    Column("storage_status", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
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

orders = Table(
    "orders",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("order_id", String, nullable=False, unique=True, index=True),
    Column("customer_id", String, nullable=False, index=True),
    Column("status", String, nullable=False, index=True),
    Column("delivery_provider_id", String, nullable=False, default="sf"),
    Column("delivery_provider_name", String, nullable=False, default="顺丰"),
    Column("courier_phone", String, nullable=False, default=""),
    Column("tracking_no", String, nullable=False, default=""),
    Column("shipping_address", String, nullable=False, default=""),
    Column("shipping_province", String, nullable=False, default=""),
    Column("shipping_city", String, nullable=False, default=""),
    Column("selected_warehouse_id", String, nullable=False, default=""),
    Column("selected_warehouse_name", String, nullable=False, default=""),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("paid_at", String, nullable=False, default=""),
    Column("shipped_at", String, nullable=False, default=""),
    Column("arrived_at", String, nullable=False, default=""),
    Column("cancelled_at", String, nullable=False, default=""),
    Column("returned_at", String, nullable=False, default=""),
    Column("expires_at", String, nullable=False, default=""),
    Column("released_at", String, nullable=False, default=""),
    Column("release_reason", String, nullable=False, default=""),
)

order_items = Table(
    "order_items",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("order_id", String, nullable=False, index=True),
    Column("customer_id", String, nullable=False, index=True),
    Column("status", String, nullable=False, index=True),
    Column("item_id", String, nullable=False, index=True),
    Column("warehouse_id", String, nullable=False, index=True),
    Column("location_code", String, nullable=False, index=True),
    Column("batch_no", String, nullable=False, index=True),
    Column("quantity", Integer, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

inventory_movements = Table(
    "inventory_movements",
    metadata,
    Column("movement_id", String, primary_key=True),
    Column("order_id", String, nullable=False, index=True),
    Column("movement_type", String, nullable=False, index=True),
    Column("item_id", String, nullable=False, index=True),
    Column("warehouse_id", String, nullable=False, index=True),
    Column("location_code", String, nullable=False, index=True),
    Column("quantity_delta", Integer, nullable=False),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
)

delivery_providers = Table(
    "delivery_providers",
    metadata,
    Column("provider_id", String, primary_key=True),
    Column("provider_name", String, nullable=False),
    Column("service_hotline", String, nullable=False),
    Column("tracking_prefix", String, nullable=False),
    Column("status", String, nullable=False),
)

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("phone_number", String(32), nullable=False, default=""),
    Column("email", String(255), nullable=False, default=""),
    Column("username", String(100), nullable=False, default=""),
    Column("password", String(20), nullable=False, default=""),
)

delivery_addresses = Table(
    "delivery_addresses",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("receiver_name", String(100), nullable=False),
    Column("phone_number", String(32), nullable=False),
    Column("address", String(500), nullable=False),
    Column("is_default", Integer, nullable=False, default=0),
)

cart_items = Table(
    "cart_items",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("item_id", String(64), nullable=False, index=True),
    Column("item_name", String(255), nullable=False),
    Column("user_id", Integer, nullable=False, index=True),
    Column("price", Numeric(12, 2), nullable=False),
    Column("quantity", Integer, nullable=False, default=1),
)

flash_sales = Table(
    "flash_sales",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("item_id", String(64), nullable=False, index=True),
    Column("sale_price", Numeric(12, 2), nullable=False),
    Column("stock_limit", Integer, nullable=False),
    Column("status", String(32), nullable=False, index=True),
    Column("starts_at", String, nullable=False),
    Column("ends_at", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

flash_sale_claims = Table(
    "flash_sale_claims",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("flash_sale_id", Integer, nullable=False, index=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("item_id", String(64), nullable=False, index=True),
    Column("order_id", String, nullable=False, default=""),
    Column("status", String(32), nullable=False, index=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("flash_sale_id", "user_id", name="uq_flash_sale_claim_user"),
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

purchase_orders = Table(
    "purchase_orders",
    metadata,
    Column("purchase_order_id", String, primary_key=True),
    Column("request_id", String, nullable=False, index=True),
    Column("supplier_id", String, nullable=False, index=True),
    Column("supplier_name", String, nullable=False),
    Column("item_id", String, nullable=False, index=True),
    Column("warehouse_id", String, nullable=False, index=True),
    Column("warehouse_name", String, nullable=False),
    Column("location_code", String, nullable=True, index=True),
    Column("quantity", Integer, nullable=False),
    Column("unit_price", Integer, nullable=False),
    Column("currency", String, nullable=False),
    Column("estimated_total_price", Integer, nullable=False),
    Column("lead_time_days", Integer, nullable=False),
    Column("estimated_arrival_date", String, nullable=False),
    Column("payment_status", String, nullable=False, index=True),
    Column("warehouse_sync_status", String, nullable=False, index=True),
    Column("arrived_at", String, nullable=False, default=""),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

# Backward-compatible Python symbol for older imports. The physical table is
# intentionally renamed to purchase_orders.
purchase_order_drafts = purchase_orders

WAREHOUSE_TABLE_COMMENTS = {
    "warehouses": "仓库主数据表，保存企业仓库的编号、名称、城市和启用状态。",
    "storage_locations": "具体库位表，保存仓库内 A1、B1、C1 等可存储位置及容量属性。",
    "categories": "商品分类表，保存纸品、乳制品、饮料等业务分类和存储要求。",
    "items": "商品主数据表，保存每个商品的名称、品牌、规格、单位和条码。",
    "inventory_batches": "批次库存事实表，按仓库、库位、商品和批次保存库存数量与保质期。",
    "inventory_location_balances": "批次级库位库存余额表，保存订单创建扣减和退回后的当前可售库存。",
    "replenishment_requests": "补货申请表，保存仓储发现低库存后交给采购审核的结构化需求。",
    "delivery_providers": "物流供应商表，保存顺丰、京东、圆通等承运商基础信息供订单和 Delivery Agent 使用。",
    "users": "TalonMart 用户表，保存购物车 v1 使用的测试用户资料。",
    "delivery_addresses": "TalonMart 配送地址表，保存购物车结算创建订单时使用的用户收货地址。",
    "cart_items": "购物车明细表，按用户和商品保存加入购物车时的商品快照价格与数量。",
    "flash_sales": "秒杀活动表，一条活动绑定一个秒杀商品和独立营销库存配额。",
    "flash_sale_claims": "秒杀抢购结果表，记录用户抢购结果和关联订单，保证一人一单。",
    "procurement_suppliers": "采购供应商表，保存 mock 供应商、交期、价格和可靠性。",
    "purchase_orders": "采购单表，保存采购审核补货申请后生成的采购单、支付状态和仓库同步状态。",
    "warehouse_inventory_sync_jobs": "仓储库存同步任务表，保存采购到仓后需要 Warehouse Agent 同步飞书库存视图的待处理任务。",
    "orders": "订单主表，保存下单、付款、发货、到货、退款和退货状态，并保留物流供应商与快递员联系方式供 Delivery Agent 查询。",
    "order_items": "订单明细表，保存订单扣减命中的商品、仓库、库位、批次和数量。",
    "inventory_movements": "库存流水表，记录订单创建、退款、退货和未付款超时释放对库位库存余额的影响。",
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
        "price": "商品销售价格，用于前台搜索结果和购物车加入时的可信价格来源。",
        "search_text": "商品搜索文本，由商品编号、名称、品牌和规格合并，用于 pg_search BM25 检索。",
        "unit": "库存计量单位。",
        "barcode": "商品条码。",
        "shelf_life_days": "商品实际保质期天数，用于采购到仓同步时计算批次过期日期。",
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
    "inventory_location_balances": {
        "id": "批次级库位余额自增整数主键。",
        "warehouse_id": "余额所在仓库编号。",
        "location_code": "余额所在具体库位编号。",
        "item_id": "余额对应商品编号。",
        "batch_no": "余额对应批次号，用于商品溯源。",
        "production_date": "批次生产日期。",
        "expiry_date": "批次保质期到期日期。",
        "quantity_on_hand": "当前可售库存余额；订单创建扣减，取消、退货或超时释放加回。",
        "reorder_threshold": "补货预警阈值。",
        "storage_status": "余额库存状态，例如 available、quality_hold。",
        "created_at": "余额行创建时间。",
        "updated_at": "余额行更新时间。",
    },
    "replenishment_requests": {
        "request_id": "补货申请编号，例如 REQ-1001。",
        "source": "申请来源，例如 warehouse。",
        "status": "申请状态，只允许未审批或已审批。",
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
    "purchase_orders": {
        "purchase_order_id": "采购单编号，例如 PO-5001。",
        "request_id": "关联的补货申请编号。",
        "supplier_id": "采购单选用的供应商编号。",
        "supplier_name": "采购单选用的供应商名称。",
        "item_id": "采购商品编号。",
        "warehouse_id": "采购商品预计入库仓库编号。",
        "warehouse_name": "采购商品预计入库仓库名称。",
        "location_code": "采购商品预计入库库位。",
        "quantity": "建议采购数量。",
        "unit_price": "采购单价，按 currency 表示。",
        "currency": "价格币种，例如 CNY。",
        "estimated_total_price": "预计采购总价。",
        "lead_time_days": "预计交期天数。",
        "estimated_arrival_date": "预计到达日期，格式为 YYYY-MM-DD。",
        "payment_status": "支付状态，例如 unpaid、paid。",
        "warehouse_sync_status": "仓库同步状态，例如 pending_arrival、arrived_unsynced、synced。",
        "arrived_at": "采购单实际确认到仓时间。",
        "created_by": "创建采购单的用户或系统身份。",
        "created_at": "采购单创建时间。",
        "updated_at": "采购单更新时间。",
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
    "orders": {
        "id": "订单自增整数主键。",
        "order_id": "订单业务编号，例如 ORD-CODEX-9001。",
        "customer_id": "客户编号。",
        "status": "订单状态：未付款、待发货、已发货、已到货、已退款、已退货、已取消。",
        "delivery_provider_id": "物流供应商编号，例如 sf、jd、yto。",
        "delivery_provider_name": "物流供应商展示名称，例如顺丰、京东、圆通。",
        "courier_phone": "快递员联系电话，由 Delivery Agent 查询和跟进。",
        "tracking_no": "物流单号或跟踪号。",
        "shipping_address": "用户收货地址，统一格式为 xx省xx市。",
        "shipping_province": "从收货地址解析出的省份。",
        "shipping_city": "从收货地址解析出的城市。",
        "selected_warehouse_id": "整单同仓发货时选中的仓库编号。",
        "selected_warehouse_name": "整单同仓发货时选中的仓库名称。",
        "created_by": "创建订单的用户或系统身份。",
        "created_at": "订单创建时间。",
        "updated_at": "订单更新时间。",
        "paid_at": "付款时间。",
        "shipped_at": "发货时间。",
        "arrived_at": "到货时间。",
        "cancelled_at": "取消时间。",
        "returned_at": "退货入库时间。",
        "expires_at": "未付款订单库存占用释放截止时间。",
        "released_at": "未付款超时释放库存的处理时间。",
        "release_reason": "库存释放原因，例如 unpaid_timeout。",
    },
    "order_items": {
        "id": "订单明细自增整数主键。",
        "order_id": "关联订单业务编号。",
        "customer_id": "客户编号。",
        "status": "明细状态，例如待发货、已退款、已退货。",
        "item_id": "明细商品编号。",
        "warehouse_id": "扣减或加回库存所在仓库编号。",
        "location_code": "扣减或加回库存所在库位。",
        "batch_no": "扣减或加回库存对应批次号。",
        "quantity": "明细数量。",
        "created_at": "明细创建时间。",
        "updated_at": "明细更新时间。",
    },
    "delivery_providers": {
        "provider_id": "物流供应商编号，例如 sf、jd、yto。",
        "provider_name": "物流供应商展示名称，例如顺丰、京东、圆通。",
        "service_hotline": "物流供应商客服电话。",
        "tracking_prefix": "生成 mock 物流单号时使用的前缀。",
        "status": "供应商启用状态，例如 active。",
    },
    "users": {
        "id": "用户自增整数主键。",
        "phone_number": "用户手机号。",
        "email": "用户邮箱。",
        "username": "用户名。",
        "password": "密码字段；当前仅用于 mock，不应用于正式明文存储。",
    },
    "delivery_addresses": {
        "id": "配送地址自增整数主键。",
        "user_id": "地址所属用户 ID，逻辑关联 users.id。",
        "receiver_name": "收货人姓名。",
        "phone_number": "收货人手机号。",
        "address": "收货地址，购物车结算创建订单时不能为空。",
        "is_default": "是否默认地址；1 表示默认，0 表示非默认。",
    },
    "cart_items": {
        "id": "购物车明细自增整数主键。",
        "item_id": "购物车商品编号。",
        "item_name": "加入购物车时的商品名称。",
        "user_id": "购物车所属用户 ID。",
        "price": "加入购物车时采用的后端商品价格。",
        "quantity": "购物车商品数量。",
    },
    "flash_sales": {
        "id": "秒杀活动自增整数主键。",
        "item_id": "秒杀商品编号，一条活动只绑定一个商品。",
        "sale_price": "秒杀展示价格。",
        "stock_limit": "独立营销秒杀库存配额，不等同于真实仓储库存。",
        "status": "活动状态：draft、active、ended 或 disabled。",
        "starts_at": "活动开始时间。",
        "ends_at": "活动结束时间。",
        "created_at": "活动创建时间。",
        "updated_at": "活动更新时间。",
    },
    "flash_sale_claims": {
        "id": "秒杀结果自增整数主键。",
        "flash_sale_id": "关联秒杀活动 ID。",
        "user_id": "抢购用户 ID。",
        "item_id": "抢购商品编号。",
        "order_id": "秒杀成功后关联的仓储订单编号。",
        "status": "抢购结果状态：pending、ordered、failed 或 cancelled。",
        "created_at": "结果创建时间。",
        "updated_at": "结果更新时间。",
    },
    "inventory_movements": {
        "movement_id": "库存流水编号。",
        "order_id": "关联订单编号。",
        "movement_type": "流水类型，例如 order_created、order_refunded、order_returned、order_timeout_released。",
        "item_id": "发生库存变化的商品编号。",
        "warehouse_id": "发生库存变化的仓库编号。",
        "location_code": "发生库存变化的库位。",
        "quantity_delta": "库存变化数量；扣减为负数，加回为正数。",
        "created_by": "创建流水的用户、agent 或定时任务身份。",
        "created_at": "流水创建时间。",
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
            inventory_location_balances,
            replenishment_requests,
            procurement_suppliers,
            users,
            delivery_addresses,
            cart_items,
            flash_sales,
            flash_sale_claims,
            purchase_orders,
            warehouse_inventory_sync_jobs,
            orders,
            order_items,
            inventory_movements,
            delivery_providers,
        ],
    )
    ensure_warehouse_schema_columns(engine)
    apply_warehouse_comments(engine)


def ensure_warehouse_schema_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    if not inspector.has_table(purchase_orders.name):
        return
    existing_columns = {
        column["name"]
        for column in inspector.get_columns(purchase_orders.name)
    }
    missing_column_sql = {
        "estimated_arrival_date": "ALTER TABLE purchase_orders ADD COLUMN estimated_arrival_date VARCHAR NOT NULL DEFAULT ''",
        "warehouse_id": "ALTER TABLE purchase_orders ADD COLUMN warehouse_id VARCHAR NOT NULL DEFAULT ''",
        "warehouse_name": "ALTER TABLE purchase_orders ADD COLUMN warehouse_name VARCHAR NOT NULL DEFAULT ''",
        "location_code": "ALTER TABLE purchase_orders ADD COLUMN location_code VARCHAR",
        "payment_status": "ALTER TABLE purchase_orders ADD COLUMN payment_status VARCHAR NOT NULL DEFAULT 'unpaid'",
        "warehouse_sync_status": "ALTER TABLE purchase_orders ADD COLUMN warehouse_sync_status VARCHAR NOT NULL DEFAULT 'pending_arrival'",
        "arrived_at": "ALTER TABLE purchase_orders ADD COLUMN arrived_at VARCHAR NOT NULL DEFAULT ''",
    }
    with engine.begin() as connection:
        for column_name, statement in missing_column_sql.items():
            if column_name not in existing_columns:
                connection.execute(text(statement))
        if inspector.has_table(orders.name):
            order_columns = {column["name"] for column in inspector.get_columns(orders.name)}
            order_missing_column_sql = {
                "delivery_provider_id": "ALTER TABLE orders ADD COLUMN delivery_provider_id VARCHAR NOT NULL DEFAULT 'sf'",
                "delivery_provider_name": "ALTER TABLE orders ADD COLUMN delivery_provider_name VARCHAR NOT NULL DEFAULT '顺丰'",
                "courier_phone": "ALTER TABLE orders ADD COLUMN courier_phone VARCHAR NOT NULL DEFAULT ''",
                "tracking_no": "ALTER TABLE orders ADD COLUMN tracking_no VARCHAR NOT NULL DEFAULT ''",
                "shipping_address": "ALTER TABLE orders ADD COLUMN shipping_address VARCHAR NOT NULL DEFAULT ''",
                "shipping_province": "ALTER TABLE orders ADD COLUMN shipping_province VARCHAR NOT NULL DEFAULT ''",
                "shipping_city": "ALTER TABLE orders ADD COLUMN shipping_city VARCHAR NOT NULL DEFAULT ''",
                "selected_warehouse_id": "ALTER TABLE orders ADD COLUMN selected_warehouse_id VARCHAR NOT NULL DEFAULT ''",
                "selected_warehouse_name": "ALTER TABLE orders ADD COLUMN selected_warehouse_name VARCHAR NOT NULL DEFAULT ''",
                "expires_at": "ALTER TABLE orders ADD COLUMN expires_at VARCHAR NOT NULL DEFAULT ''",
                "released_at": "ALTER TABLE orders ADD COLUMN released_at VARCHAR NOT NULL DEFAULT ''",
                "release_reason": "ALTER TABLE orders ADD COLUMN release_reason VARCHAR NOT NULL DEFAULT ''",
            }
            for column_name, statement in order_missing_column_sql.items():
                if column_name not in order_columns:
                    connection.execute(text(statement))
            if "requested_items_json" in order_columns:
                connection.execute(text("ALTER TABLE orders DROP COLUMN requested_items_json"))
            connection.execute(text("UPDATE orders SET status = '未付款' WHERE status = 'created'"))
            connection.execute(text("UPDATE orders SET status = '待发货' WHERE status = 'paid'"))
            connection.execute(text("UPDATE orders SET status = '已发货' WHERE status = 'shipped'"))
            connection.execute(text("UPDATE orders SET status = '已到货' WHERE status = 'arrived'"))
            connection.execute(text("UPDATE orders SET status = '已退款' WHERE status = 'cancelled'"))
            connection.execute(text("UPDATE orders SET status = '已退货' WHERE status = 'returned'"))
        if inspector.has_table(order_items.name):
            connection.execute(text("UPDATE order_items SET status = '待发货' WHERE status = 'paid'"))
            connection.execute(text("UPDATE order_items SET status = '已退款' WHERE status = 'cancelled'"))
            connection.execute(text("UPDATE order_items SET status = '已退货' WHERE status = 'returned'"))
        if inspector.has_table(items.name):
            item_columns = {column["name"] for column in inspector.get_columns(items.name)}
            if "shelf_life_days" not in item_columns:
                connection.execute(text("ALTER TABLE items ADD COLUMN shelf_life_days INTEGER NOT NULL DEFAULT 365"))
            if "price" not in item_columns:
                connection.execute(text("ALTER TABLE items ADD COLUMN price NUMERIC(12, 2) NOT NULL DEFAULT 0"))
            if "search_text" not in item_columns:
                connection.execute(text("ALTER TABLE items ADD COLUMN search_text TEXT NOT NULL DEFAULT ''"))
            connection.execute(
                text(
                    "UPDATE items "
                    "SET search_text = trim(item_id || ' ' || item_name || ' ' || brand || ' ' || spec) "
                    "WHERE search_text = ''"
                )
            )
        if inspector.has_table(replenishment_requests.name):
            connection.execute(
                text(
                    "UPDATE replenishment_requests "
                    "SET status = '未审批' "
                    "WHERE status IN ('pending_procurement_review', 'rejected')"
                )
            )
            connection.execute(
                text(
                    "UPDATE replenishment_requests "
                    "SET status = '已审批' "
                    "WHERE status IN ('purchase_order_created', 'purchase_order_draft_created')"
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


def inventory_location_balance_rows_from_batches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = "2026-05-24T00:00:00+00:00"
    return [
        {
            "warehouse_id": row["warehouse_id"],
            "location_code": row["location_code"],
            "item_id": row["item_id"],
            "batch_no": row["batch_no"],
            "production_date": row["production_date"],
            "expiry_date": row["expiry_date"],
            "quantity_on_hand": int(row["quantity_on_hand"]),
            "reorder_threshold": int(row["reorder_threshold"]),
            "storage_status": row["storage_status"],
            "created_at": now,
            "updated_at": now,
        }
        for row in rows
    ]


def _date_from_iso(value: str) -> date:
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return date.today()


def _deterministic_reorder_threshold(*parts: str) -> int:
    digest = hashlib.sha256(":".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return 20 + (int(digest[:8], 16) % 101)


def item_search_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(field) or "").strip()
        for field in ("item_id", "item_name", "brand", "spec")
        if str(row.get(field) or "").strip()
    )


def load_item_fixture_rows(fixture_dir: Path) -> list[dict[str, Any]]:
    rows = load_fixture_rows(fixture_dir, "items.json")
    return [{**row, "search_text": item_search_text(row)} for row in rows]


def default_user_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "phone_number": "13800000001",
            "email": "user1@talonmart.local",
            "username": "talon_user_1",
            "password": "demo123",
        },
        {
            "id": 2,
            "phone_number": "13800000002",
            "email": "user2@talonmart.local",
            "username": "talon_user_2",
            "password": "demo123",
        },
    ]


def default_delivery_address_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "user_id": 1,
            "receiver_name": "Talon 测试用户",
            "phone_number": "13800000001",
            "address": "广东省深圳市南山区示例路 100 号",
            "is_default": 1,
        },
        {
            "id": 2,
            "user_id": 2,
            "receiver_name": "Talon 测试用户二",
            "phone_number": "13800000002",
            "address": "广东省深圳市福田区示例路 200 号",
            "is_default": 1,
        },
    ]


def default_flash_sale_rows() -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    active_starts_at = (now - timedelta(days=1)).isoformat()
    active_ends_at = (now + timedelta(days=7)).isoformat()
    draft_starts_at = (now + timedelta(days=1)).isoformat()
    draft_ends_at = (now + timedelta(days=8)).isoformat()
    timestamp = now.isoformat()
    # 秒杀活动属于本地演示基准数据，mock-api 重启或重建后会重新生成。
    return [
        {
            "item_id": "item_milk_pure",
            "sale_price": "12.90",
            "stock_limit": 30,
            "status": "active",
            "starts_at": active_starts_at,
            "ends_at": active_ends_at,
            "created_at": timestamp,
            "updated_at": timestamp,
        },
        {
            "item_id": "item_cola_zero",
            "sale_price": "19.90",
            "stock_limit": 40,
            "status": "active",
            "starts_at": active_starts_at,
            "ends_at": active_ends_at,
            "created_at": timestamp,
            "updated_at": timestamp,
        },
        {
            "item_id": "item_vinda_tissue",
            "sale_price": "18.80",
            "stock_limit": 25,
            "status": "active",
            "starts_at": active_starts_at,
            "ends_at": active_ends_at,
            "created_at": timestamp,
            "updated_at": timestamp,
        },
        {
            "item_id": "item_yogurt_plain",
            "sale_price": "15.90",
            "stock_limit": 20,
            "status": "active",
            "starts_at": active_starts_at,
            "ends_at": active_ends_at,
            "created_at": timestamp,
            "updated_at": timestamp,
        },
        {
            "item_id": "item_water_spring",
            "sale_price": "16.90",
            "stock_limit": 35,
            "status": "active",
            "starts_at": active_starts_at,
            "ends_at": active_ends_at,
            "created_at": timestamp,
            "updated_at": timestamp,
        },
        {
            "item_id": "item_detergent",
            "sale_price": "39.90",
            "stock_limit": 10,
            "status": "active",
            "starts_at": active_starts_at,
            "ends_at": active_ends_at,
            "created_at": timestamp,
            "updated_at": timestamp,
        },
        {
            "item_id": "item_office_pen",
            "sale_price": "6.90",
            "stock_limit": 50,
            "status": "active",
            "starts_at": active_starts_at,
            "ends_at": active_ends_at,
            "created_at": timestamp,
            "updated_at": timestamp,
        },
        {
            "item_id": "item_copy_paper",
            "sale_price": "18.90",
            "stock_limit": 15,
            "status": "draft",
            "starts_at": draft_starts_at,
            "ends_at": draft_ends_at,
            "created_at": timestamp,
            "updated_at": timestamp,
        },
    ]


def build_item_pg_search_index_sql() -> str:
    return (
        "CREATE INDEX items_search_idx ON items "
        "USING bm25 (item_id, (search_text::pdb.chinese_compatible)) "
        "WITH (key_field='item_id')"
    )


def build_item_search_sql():
    return text(
        """
        SELECT
            item_id,
            item_name,
            brand,
            spec,
            price,
            category_id,
            pdb.score(item_id) AS score
        FROM items
        WHERE search_text &&& :query
        ORDER BY score DESC, item_id
        LIMIT :limit
        """
    )


def ensure_item_pg_search_index(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_search"))
        connection.execute(text("DROP INDEX IF EXISTS items_search_idx"))
        connection.execute(text(build_item_pg_search_index_sql()))


def seed_warehouse_fixtures(engine: Engine, fixture_dir: Path) -> None:
    init_warehouse_schema(engine)
    with engine.begin() as connection:
        # Demo data is fixture-owned, so restart/reseed should converge the DB to fixtures.
        connection.execute(order_items.delete())
        connection.execute(orders.delete())
        connection.execute(inventory_movements.delete())
        connection.execute(delivery_providers.delete())
        connection.execute(cart_items.delete())
        connection.execute(flash_sale_claims.delete())
        connection.execute(flash_sales.delete())
        connection.execute(delivery_addresses.delete())
        connection.execute(inventory_location_balances.delete())
        connection.execute(inventory_batches.delete())
        connection.execute(procurement_suppliers.delete())
        connection.execute(items.delete())
        connection.execute(users.delete())
        connection.execute(categories.delete())
        connection.execute(storage_locations.delete())
        connection.execute(warehouses.delete())
        connection.execute(warehouses.insert(), load_fixture_rows(fixture_dir, "warehouses.json"))
        connection.execute(
            storage_locations.insert(),
            load_fixture_rows(fixture_dir, "storage_locations.json"),
        )
        connection.execute(categories.insert(), load_fixture_rows(fixture_dir, "categories.json"))
        connection.execute(items.insert(), load_item_fixture_rows(fixture_dir))
        connection.execute(users.insert(), default_user_rows())
        connection.execute(delivery_addresses.insert(), default_delivery_address_rows())
        connection.execute(flash_sales.insert(), default_flash_sale_rows())
        connection.execute(
            delivery_providers.insert(),
            load_fixture_rows(fixture_dir, "delivery_providers.json"),
        )
        batch_rows = load_inventory_batch_fixture_rows(fixture_dir)
        connection.execute(inventory_batches.insert(), batch_rows)
        connection.execute(
            inventory_location_balances.insert(),
            inventory_location_balance_rows_from_batches(batch_rows),
        )
        connection.execute(
            procurement_suppliers.insert(),
            load_fixture_rows(fixture_dir, "procurement_suppliers.json"),
        )
    ensure_item_pg_search_index(engine)


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
                inventory_location_balances.c.id.label("batch_id"),
                inventory_location_balances.c.warehouse_id,
                inventory_location_balances.c.location_code,
                inventory_location_balances.c.item_id,
                inventory_location_balances.c.batch_no,
                inventory_location_balances.c.production_date,
                inventory_location_balances.c.expiry_date,
                inventory_location_balances.c.quantity_on_hand,
                text("0 AS quantity_reserved"),
                inventory_location_balances.c.reorder_threshold,
                inventory_location_balances.c.storage_status,
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
                items.c.shelf_life_days,
            )
            .join(warehouses, warehouses.c.warehouse_id == inventory_location_balances.c.warehouse_id)
            .join(
                storage_locations,
                (storage_locations.c.warehouse_id == inventory_location_balances.c.warehouse_id)
                & (storage_locations.c.location_code == inventory_location_balances.c.location_code),
            )
            .join(items, items.c.item_id == inventory_location_balances.c.item_id)
            .join(categories, categories.c.category_id == items.c.category_id)
        )
        if item_id:
            statement = statement.where(inventory_location_balances.c.item_id == item_id)
        if warehouse_id:
            statement = statement.where(inventory_location_balances.c.warehouse_id == warehouse_id)
        if location_code:
            statement = statement.where(inventory_location_balances.c.location_code == location_code)
        if category_id:
            statement = statement.where(categories.c.category_id == category_id)
        if batch_no:
            statement = statement.where(inventory_location_balances.c.batch_no == batch_no)
        statement = statement.order_by(
            inventory_location_balances.c.warehouse_id,
            inventory_location_balances.c.location_code,
            items.c.item_name,
            inventory_location_balances.c.batch_no,
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [dict(row) for row in rows]

    def list_location_balances(
        self,
        *,
        item_id: str | None = None,
        warehouse_id: str | None = None,
        location_code: str | None = None,
    ) -> list[dict[str, Any]]:
        statement = select(inventory_location_balances)
        if item_id:
            statement = statement.where(inventory_location_balances.c.item_id == item_id)
        if warehouse_id:
            statement = statement.where(inventory_location_balances.c.warehouse_id == warehouse_id)
        if location_code:
            statement = statement.where(inventory_location_balances.c.location_code == location_code)
        statement = statement.order_by(
            inventory_location_balances.c.warehouse_id,
            inventory_location_balances.c.location_code,
            inventory_location_balances.c.expiry_date,
            inventory_location_balances.c.batch_no,
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [dict(row) for row in rows]

    def search_items(self, query: str, *, limit: int = 50) -> list[dict[str, Any]]:
        statement = build_item_search_sql()
        with self.engine.connect() as connection:
            rows = connection.execute(statement, {"query": query.strip(), "limit": limit}).mappings().all()
        return [{**dict(row), "price": float(row["price"])} for row in rows]

    def user_exists(self, user_id: int) -> bool:
        with self.engine.connect() as connection:
            row = connection.execute(select(users.c.id).where(users.c.id == user_id)).first()
        return row is not None

    def list_delivery_addresses(self, user_id: int) -> list[dict[str, Any]]:
        statement = (
            select(delivery_addresses)
            .where(delivery_addresses.c.user_id == user_id)
            .order_by(delivery_addresses.c.is_default.desc(), delivery_addresses.c.id)
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [dict(row) for row in rows]

    def get_item_for_cart(self, item_id: str) -> dict[str, Any] | None:
        statement = select(items.c.item_id, items.c.item_name, items.c.price).where(items.c.item_id == item_id)
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        if not row:
            return None
        return {**dict(row), "price": float(row["price"])}

    def get_item_detail(self, item_id: str) -> dict[str, Any] | None:
        statement = (
            select(
                items.c.item_id,
                items.c.item_name,
                items.c.brand,
                items.c.spec,
                items.c.category_id,
                items.c.price,
                items.c.unit,
                items.c.barcode,
            )
            .where(items.c.item_id == item_id)
        )
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        if not row:
            return None
        return {**dict(row), "price": float(row["price"])}

    def list_cart_items(self, user_id: int) -> list[dict[str, Any]]:
        statement = select(cart_items).where(cart_items.c.user_id == user_id).order_by(cart_items.c.id)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [{**dict(row), "price": float(row["price"])} for row in rows]

    def upsert_cart_item(self, *, user_id: int, item_id: str, quantity: int) -> dict[str, Any]:
        item = self.get_item_for_cart(item_id)
        if not item:
            raise ValueError("item_not_found")
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(cart_items)
                .where(cart_items.c.user_id == user_id)
                .where(cart_items.c.item_id == item_id)
            ).mappings().first()
            if existing:
                new_quantity = int(existing["quantity"]) + quantity
                connection.execute(
                    cart_items.update()
                    .where(cart_items.c.id == existing["id"])
                    .values(quantity=new_quantity, item_name=item["item_name"], price=item["price"])
                )
                row = connection.execute(select(cart_items).where(cart_items.c.id == existing["id"])).mappings().one()
            else:
                result = connection.execute(
                    cart_items.insert().values(
                        user_id=user_id,
                        item_id=item_id,
                        item_name=item["item_name"],
                        price=item["price"],
                        quantity=quantity,
                    )
                )
                row = connection.execute(
                    select(cart_items).where(cart_items.c.id == result.inserted_primary_key[0])
                ).mappings().one()
        return {**dict(row), "price": float(row["price"])}

    def delete_cart_item(self, *, user_id: int, item_id: str) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(
                cart_items.delete()
                .where(cart_items.c.user_id == user_id)
                .where(cart_items.c.item_id == item_id)
            )
        return bool(result.rowcount)

    def create_flash_sale(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.engine.begin() as connection:
            result = connection.execute(flash_sales.insert().values(**payload))
            row = (
                connection.execute(
                    select(flash_sales).where(flash_sales.c.id == result.inserted_primary_key[0])
                )
                .mappings()
                .one()
            )
        return self._format_flash_sale(row)

    def get_flash_sale(self, flash_sale_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(flash_sales).where(flash_sales.c.id == flash_sale_id))
                .mappings()
                .one_or_none()
            )
        return self._format_flash_sale(row) if row else None

    def list_flash_sales(self, *, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        capped_limit = max(1, min(limit, 100))
        statement = select(flash_sales).order_by(flash_sales.c.id)
        if status:
            statement = statement.where(flash_sales.c.status == status)
        statement = statement.limit(capped_limit)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [self._format_flash_sale(row) for row in rows]

    def update_flash_sale_status(self, flash_sale_id: int, *, status: str, updated_at: str) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            connection.execute(
                flash_sales.update()
                .where(flash_sales.c.id == flash_sale_id)
                .values(status=status, updated_at=updated_at)
            )
            row = (
                connection.execute(select(flash_sales).where(flash_sales.c.id == flash_sale_id))
                .mappings()
                .one_or_none()
            )
        return self._format_flash_sale(row) if row else None

    def create_flash_sale_claim_pending(
        self,
        *,
        flash_sale_id: int,
        user_id: int,
        item_id: str,
        created_at: str,
    ) -> dict[str, Any]:
        values = {
            "flash_sale_id": flash_sale_id,
            "user_id": user_id,
            "item_id": item_id,
            "order_id": "",
            "status": "pending",
            "created_at": created_at,
            "updated_at": created_at,
        }
        with self.engine.begin() as connection:
            result = connection.execute(flash_sale_claims.insert().values(**values))
            row = (
                connection.execute(
                    select(flash_sale_claims).where(flash_sale_claims.c.id == result.inserted_primary_key[0])
                )
                .mappings()
                .one()
            )
        return dict(row)

    def get_flash_sale_claim(self, *, flash_sale_id: int, user_id: int) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(flash_sale_claims)
                    .where(flash_sale_claims.c.flash_sale_id == flash_sale_id)
                    .where(flash_sale_claims.c.user_id == user_id)
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row else None

    def mark_flash_sale_claim_ordered(
        self,
        claim_id: int,
        *,
        order_id: str,
        updated_at: str,
    ) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            connection.execute(
                flash_sale_claims.update()
                .where(flash_sale_claims.c.id == claim_id)
                .values(status="ordered", order_id=order_id, updated_at=updated_at)
            )
            row = (
                connection.execute(select(flash_sale_claims).where(flash_sale_claims.c.id == claim_id))
                .mappings()
                .one_or_none()
            )
        return dict(row) if row else None

    def mark_flash_sale_claim_failed(
        self,
        claim_id: int,
        *,
        updated_at: str,
    ) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            connection.execute(
                flash_sale_claims.update()
                .where(flash_sale_claims.c.id == claim_id)
                .values(status="failed", updated_at=updated_at)
            )
            row = (
                connection.execute(select(flash_sale_claims).where(flash_sale_claims.c.id == claim_id))
                .mappings()
                .one_or_none()
            )
        return dict(row) if row else None

    @staticmethod
    def _format_flash_sale(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["sale_price"] = float(item["sale_price"])
        return item

    def list_inventory_balance_snapshots(
        self,
        *,
        item_id: str | None = None,
        warehouse_id: str | None = None,
        location_code: str | None = None,
    ) -> list[dict[str, Any]]:
        statement = (
            select(
                inventory_location_balances.c.id,
                inventory_location_balances.c.warehouse_id,
                inventory_location_balances.c.location_code,
                inventory_location_balances.c.item_id,
                inventory_location_balances.c.quantity_on_hand,
                inventory_location_balances.c.reorder_threshold,
                inventory_location_balances.c.storage_status,
                inventory_location_balances.c.created_at,
                inventory_location_balances.c.updated_at,
                warehouses.c.warehouse_name,
                categories.c.category_id,
                categories.c.category_name,
                items.c.item_name,
                items.c.brand,
                items.c.spec,
                items.c.unit,
            )
            .join(warehouses, warehouses.c.warehouse_id == inventory_location_balances.c.warehouse_id)
            .join(items, items.c.item_id == inventory_location_balances.c.item_id)
            .join(categories, categories.c.category_id == items.c.category_id)
        )
        if item_id:
            statement = statement.where(inventory_location_balances.c.item_id == item_id)
        if warehouse_id:
            statement = statement.where(inventory_location_balances.c.warehouse_id == warehouse_id)
        if location_code:
            statement = statement.where(inventory_location_balances.c.location_code == location_code)
        statement = statement.order_by(
            inventory_location_balances.c.warehouse_id,
            inventory_location_balances.c.location_code,
            inventory_location_balances.c.item_id,
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
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(inventory_batches).where(inventory_batches.c.batch_no == batch_no)
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row else None

    def create_inventory_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = {key: value for key, value in payload.items() if key != "batch_id"}
        now = values.get("created_at") or "2026-05-24T00:00:00+00:00"
        with self.engine.begin() as connection:
            connection.execute(inventory_batches.insert().values(**values))
            connection.execute(
                inventory_location_balances.insert().values(
                    warehouse_id=values["warehouse_id"],
                    location_code=values["location_code"],
                    item_id=values["item_id"],
                    batch_no=values["batch_no"],
                    production_date=values["production_date"],
                    expiry_date=values["expiry_date"],
                    quantity_on_hand=int(values["quantity_on_hand"]),
                    reorder_threshold=int(values["reorder_threshold"]),
                    storage_status=values["storage_status"],
                    created_at=now,
                    updated_at=now,
                )
            )
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

    def count_purchase_orders(self) -> int:
        with self.engine.connect() as connection:
            return int(connection.execute(select(func.count()).select_from(purchase_orders)).scalar_one())

    def count_purchase_order_drafts(self) -> int:
        return self.count_purchase_orders()

    def create_purchase_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = {**payload}
        payload.setdefault("arrived_at", "")
        with self.engine.begin() as connection:
            connection.execute(purchase_orders.insert().values(**payload))
            row = (
                connection.execute(
                    select(purchase_orders).where(
                        purchase_orders.c.purchase_order_id == payload["purchase_order_id"]
                    )
                )
                .mappings()
                .one()
            )
        return dict(row)

    def create_purchase_order_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "po_draft_id" in payload and "purchase_order_id" not in payload:
            payload = {**payload, "purchase_order_id": payload["po_draft_id"]}
            payload.pop("po_draft_id", None)
        payload.setdefault("warehouse_id", "")
        payload.setdefault("warehouse_name", "")
        payload.setdefault("location_code", "")
        payload.setdefault("payment_status", "unpaid")
        payload.setdefault("warehouse_sync_status", payload.pop("status", "pending_arrival"))
        payload.setdefault("arrived_at", "")
        return self.create_purchase_order(payload)

    def get_purchase_order(self, purchase_order_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(purchase_orders).where(
                        purchase_orders.c.purchase_order_id == purchase_order_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        return dict(row) if row else None

    def get_purchase_order_draft(self, po_draft_id: str) -> dict[str, Any] | None:
        return self.get_purchase_order(po_draft_id)

    def update_purchase_order_warehouse_sync_status(
        self,
        purchase_order_id: str,
        *,
        warehouse_sync_status: str,
        updated_at: str,
        arrived_at: str | None = None,
    ) -> dict[str, Any] | None:
        values = {"warehouse_sync_status": warehouse_sync_status, "updated_at": updated_at}
        if arrived_at is not None:
            values["arrived_at"] = arrived_at
        with self.engine.begin() as connection:
            connection.execute(
                purchase_orders.update()
                .where(purchase_orders.c.purchase_order_id == purchase_order_id)
                .values(**values)
            )
            row = (
                connection.execute(
                    select(purchase_orders).where(
                        purchase_orders.c.purchase_order_id == purchase_order_id
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
        return self.update_purchase_order_warehouse_sync_status(
            po_draft_id,
            warehouse_sync_status=status,
            updated_at=updated_at,
        )

    def list_purchase_orders(
        self,
        *,
        request_id: str | None = None,
        warehouse_sync_status: str | None = None,
        purchase_order_id: str | None = None,
        payment_status: str | None = None,
    ) -> list[dict[str, Any]]:
        statement = select(purchase_orders)
        if request_id:
            statement = statement.where(purchase_orders.c.request_id == request_id)
        if warehouse_sync_status:
            statement = statement.where(purchase_orders.c.warehouse_sync_status == warehouse_sync_status)
        if purchase_order_id:
            statement = statement.where(purchase_orders.c.purchase_order_id == purchase_order_id)
        if payment_status:
            statement = statement.where(purchase_orders.c.payment_status == payment_status)
        statement = statement.order_by(purchase_orders.c.created_at, purchase_orders.c.purchase_order_id)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [dict(row) for row in rows]

    def list_purchase_order_drafts(self, *, request_id: str | None = None) -> list[dict[str, Any]]:
        return self.list_purchase_orders(request_id=request_id)

    def sync_arrived_purchase_orders(
        self,
        *,
        limit: int,
        processed_by: str,
        processed_at: str,
    ) -> list[dict[str, Any]]:
        statement = (
            select(purchase_orders)
            .where(purchase_orders.c.payment_status == "paid")
            .where(purchase_orders.c.warehouse_sync_status == "arrived_unsynced")
            .order_by(purchase_orders.c.arrived_at, purchase_orders.c.purchase_order_id)
            .limit(limit)
        )
        synced_items: list[dict[str, Any]] = []
        with self.engine.begin() as connection:
            rows = connection.execute(statement).mappings().all()
            for row in rows:
                order = dict(row)
                item_row = connection.execute(
                    select(items).where(items.c.item_id == order["item_id"])
                ).mappings().one_or_none()
                if not item_row:
                    continue
                location_code = self._resolve_purchase_order_location(connection, order)
                arrived_at = order.get("arrived_at") or processed_at
                production_day = _date_from_iso(arrived_at)
                batch_no = f"BATCH-{production_day:%Y%m%d}"
                expiry_day = production_day.toordinal() + int(item_row["shelf_life_days"])
                expiry_date = date.fromordinal(expiry_day).isoformat()
                reorder_threshold = _deterministic_reorder_threshold(
                    order["purchase_order_id"],
                    order["item_id"],
                    order["warehouse_id"],
                )
                quantity = int(order["quantity"])
                connection.execute(
                    inventory_batches.insert().values(
                        warehouse_id=order["warehouse_id"],
                        location_code=location_code,
                        item_id=order["item_id"],
                        batch_no=batch_no,
                        production_date=production_day.isoformat(),
                        expiry_date=expiry_date,
                        quantity_on_hand=quantity,
                        quantity_reserved=0,
                        reorder_threshold=reorder_threshold,
                        storage_status="available",
                    )
                )
                existing_balance = (
                    connection.execute(
                        select(inventory_location_balances)
                        .where(inventory_location_balances.c.warehouse_id == order["warehouse_id"])
                        .where(inventory_location_balances.c.location_code == location_code)
                        .where(inventory_location_balances.c.item_id == order["item_id"])
                        .where(inventory_location_balances.c.batch_no == batch_no)
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing_balance:
                    connection.execute(
                        inventory_location_balances.update()
                        .where(inventory_location_balances.c.id == existing_balance["id"])
                        .values(
                            quantity_on_hand=inventory_location_balances.c.quantity_on_hand + quantity,
                            reorder_threshold=reorder_threshold,
                            storage_status="available",
                            updated_at=processed_at,
                        )
                    )
                else:
                    connection.execute(
                        inventory_location_balances.insert().values(
                            warehouse_id=order["warehouse_id"],
                            location_code=location_code,
                            item_id=order["item_id"],
                            batch_no=batch_no,
                            production_date=production_day.isoformat(),
                            expiry_date=expiry_date,
                            quantity_on_hand=quantity,
                            reorder_threshold=reorder_threshold,
                            storage_status="available",
                            created_at=processed_at,
                            updated_at=processed_at,
                        )
                    )
                connection.execute(
                    purchase_orders.update()
                    .where(purchase_orders.c.purchase_order_id == order["purchase_order_id"])
                    .values(
                        location_code=location_code,
                        warehouse_sync_status="synced",
                        updated_at=processed_at,
                    )
                )
                synced_items.append(
                    {
                        "purchase_order_id": order["purchase_order_id"],
                        "request_id": order["request_id"],
                        "item_id": order["item_id"],
                        "warehouse_id": order["warehouse_id"],
                        "warehouse_name": order["warehouse_name"],
                        "location_code": location_code,
                        "batch_no": batch_no,
                        "production_date": production_day.isoformat(),
                        "expiry_date": expiry_date,
                        "quantity": quantity,
                        "reorder_threshold": reorder_threshold,
                        "storage_status": "available",
                        "payment_status": "paid",
                        "warehouse_sync_status": "synced",
                        "processed_by": processed_by,
                        "processed_at": processed_at,
                    }
                )
        return synced_items

    @staticmethod
    def _resolve_purchase_order_location(connection: Any, order: dict[str, Any]) -> str:
        existing_location = (
            connection.execute(
                select(inventory_location_balances.c.location_code)
                .where(inventory_location_balances.c.item_id == order["item_id"])
                .where(inventory_location_balances.c.warehouse_id == order["warehouse_id"])
                .order_by(inventory_location_balances.c.location_code)
            )
            .scalars()
            .first()
        )
        if existing_location:
            return str(existing_location)
        requested_location = str(order.get("location_code") or "").strip()
        if requested_location:
            return requested_location
        first_location = (
            connection.execute(
                select(storage_locations.c.location_code)
                .where(storage_locations.c.warehouse_id == order["warehouse_id"])
                .order_by(storage_locations.c.location_code)
            )
            .scalars()
            .first()
        )
        if not first_location:
            raise ValueError("warehouse_location_not_found")
        return str(first_location)

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

    def list_delivery_providers(self) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(delivery_providers).order_by(delivery_providers.c.provider_id)
                )
                .mappings()
                .all()
            )
        return [
            {
                "provider_id": row["provider_id"],
                "name": row["provider_name"],
                "service_hotline": row["service_hotline"],
                "tracking_prefix": row["tracking_prefix"],
                "status": row["status"],
            }
            for row in rows
        ]

    def count_orders(self) -> int:
        with self.engine.connect() as connection:
            return int(connection.execute(select(func.count()).select_from(orders)).scalar_one())

    def list_orders(self) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(orders).order_by(orders.c.id)).mappings().all()
        return [dict(row) for row in rows]

    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        item_requests = [dict(item) for item in payload["items"]]
        values = {**payload}
        values.pop("items", None)
        updated_at = str(values["created_at"])
        with self.engine.begin() as connection:
            connection.execute(orders.insert().values(**values))
            allocated_items = self._allocate_order_items(connection, values, item_requests, updated_at)
            for item in allocated_items:
                connection.execute(
                    inventory_location_balances.update()
                    .where(inventory_location_balances.c.item_id == item["item_id"])
                    .where(inventory_location_balances.c.warehouse_id == item["warehouse_id"])
                    .where(inventory_location_balances.c.location_code == item["location_code"])
                    .where(inventory_location_balances.c.batch_no == item["batch_no"])
                    .values(
                        quantity_on_hand=inventory_location_balances.c.quantity_on_hand - int(item["quantity"]),
                        updated_at=updated_at,
                    )
                )
            if allocated_items:
                connection.execute(order_items.insert(), allocated_items)
                connection.execute(
                    inventory_movements.insert(),
                    self._inventory_movement_values(
                        allocated_items,
                        movement_type="order_created",
                        created_by=str(values["created_by"]),
                        created_at=updated_at,
                        direction=-1,
                    ),
                )
        return self.get_order(str(payload["order_id"])) or {"order": values, "items": []}

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            order_row = (
                connection.execute(select(orders).where(orders.c.order_id == order_id))
                .mappings()
                .one_or_none()
            )
            if not order_row:
                return None
            item_rows = (
                connection.execute(
                    select(order_items)
                    .where(order_items.c.order_id == order_id)
                    .order_by(order_items.c.id)
                )
                .mappings()
                .all()
            )
        order = dict(order_row)
        return {"order": order, "items": [dict(row) for row in item_rows]}

    def pay_order(self, order_id: str, *, updated_by: str, updated_at: str) -> dict[str, Any]:
        details = self.get_order(order_id)
        if not details:
            raise ValueError("order_not_found")
        order = details["order"]
        if order["status"] == "待发货":
            return details
        if order["status"] != "未付款":
            raise ValueError(f"order_cannot_pay_from_{order['status']}")
        with self.engine.begin() as connection:
            connection.execute(
                order_items.update()
                .where(order_items.c.order_id == order_id)
                .where(order_items.c.status == "未付款")
                .values(status="待发货", updated_at=updated_at)
            )
            connection.execute(
                orders.update()
                .where(orders.c.order_id == order_id)
                .values(status="待发货", updated_at=updated_at, paid_at=updated_at)
            )
        return self.get_order(order_id) or details

    def _allocate_order_items(
        self,
        connection: Any,
        order: dict[str, Any],
        item_requests: list[dict[str, Any]],
        updated_at: str,
    ) -> list[dict[str, Any]]:
        allocated_items: list[dict[str, Any]] = []
        for request in item_requests:
            remaining = int(request["quantity"])
            statement = (
                select(inventory_location_balances)
                .where(inventory_location_balances.c.item_id == request["item_id"])
                .where(inventory_location_balances.c.warehouse_id == request["warehouse_id"])
                .where(inventory_location_balances.c.storage_status == "available")
                .where(inventory_location_balances.c.quantity_on_hand > 0)
                .order_by(
                    inventory_location_balances.c.expiry_date,
                    inventory_location_balances.c.production_date,
                    inventory_location_balances.c.batch_no,
                )
            ).with_for_update()
            rows = connection.execute(statement).mappings().all()
            for row in rows:
                if remaining <= 0:
                    break
                quantity = min(int(row["quantity_on_hand"]), remaining)
                allocated_items.append(
                    {
                        "order_id": order["order_id"],
                        "customer_id": order["customer_id"],
                        "status": "未付款",
                        "item_id": row["item_id"],
                        "warehouse_id": row["warehouse_id"],
                        "location_code": row["location_code"],
                        "batch_no": row["batch_no"],
                        "quantity": quantity,
                        "created_at": updated_at,
                        "updated_at": updated_at,
                    }
                )
                remaining -= quantity
            if remaining > 0:
                available = int(request["quantity"]) - remaining
                raise ValueError(
                    json.dumps(
                        {
                            "error": "insufficient_available_stock",
                            "item_id": request["item_id"],
                            "warehouse_id": request["warehouse_id"],
                            "requested_quantity": int(request["quantity"]),
                            "available_quantity": available,
                            "shortage_quantity": remaining,
                        },
                        ensure_ascii=False,
                    )
                )
        return allocated_items

    def update_order_status(self, order_id: str, *, status: str, updated_by: str, updated_at: str) -> dict[str, Any]:
        timestamp_columns = {
            "已发货": "shipped_at",
            "已到货": "arrived_at",
            "已退款": "cancelled_at",
            "已退货": "returned_at",
            "已取消": "cancelled_at",
        }
        details = self.get_order(order_id)
        if not details:
            raise ValueError("order_not_found")
        current_status = details["order"]["status"]
        if status in {"已退款", "已退货", "已取消"} and current_status in {"未付款", "待发货", "已发货", "已到货"}:
            self._restore_order_items(
                order_id,
                status=status,
                movement_type={
                    "已退款": "order_refunded",
                    "已退货": "order_returned",
                    "已取消": "order_timeout_released",
                }[status],
                created_by=updated_by,
                updated_at=updated_at,
            )
        values = {"status": status, "updated_at": updated_at}
        if status in timestamp_columns:
            values[timestamp_columns[status]] = updated_at
        with self.engine.begin() as connection:
            connection.execute(orders.update().where(orders.c.order_id == order_id).values(**values))
        return self.get_order(order_id) or details

    def cancel_order(self, order_id: str, *, updated_by: str, updated_at: str) -> dict[str, Any]:
        return self.update_order_status(order_id, status="已退款", updated_by=updated_by, updated_at=updated_at)

    def return_order(self, order_id: str, *, updated_by: str, updated_at: str) -> dict[str, Any]:
        return self.update_order_status(order_id, status="已退货", updated_by=updated_by, updated_at=updated_at)

    def _restore_order_items(
        self,
        order_id: str,
        *,
        status: str,
        movement_type: str,
        created_by: str,
        updated_at: str,
    ) -> None:
        details = self.get_order(order_id)
        if not details:
            return
        restorable = [
            item
            for item in details["items"]
            if item["status"] in {"未付款", "待发货", "已发货", "已到货"}
        ]
        with self.engine.begin() as connection:
            for item in restorable:
                connection.execute(
                    inventory_location_balances.update()
                    .where(inventory_location_balances.c.item_id == item["item_id"])
                    .where(inventory_location_balances.c.warehouse_id == item["warehouse_id"])
                    .where(inventory_location_balances.c.location_code == item["location_code"])
                    .where(inventory_location_balances.c.batch_no == item["batch_no"])
                    .values(
                        quantity_on_hand=inventory_location_balances.c.quantity_on_hand + int(item["quantity"]),
                        updated_at=updated_at,
                    )
                )
                connection.execute(
                    order_items.update()
                    .where(order_items.c.id == item["id"])
                    .values(status=status, updated_at=updated_at)
                )
            if restorable:
                connection.execute(
                    inventory_movements.insert(),
                    self._inventory_movement_values(
                        restorable,
                        movement_type=movement_type,
                        created_by=created_by,
                        created_at=updated_at,
                        direction=1,
                    ),
                )

    def release_expired_orders(self, *, processed_by: str, now: str, limit: int = 100) -> list[dict[str, Any]]:
        normalized_limit = max(min(int(limit or 100), 500), 1)
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(orders)
                    .where(orders.c.status == "未付款")
                    .where(orders.c.released_at == "")
                    .where(orders.c.expires_at != "")
                    .where(orders.c.expires_at < now)
                    .order_by(orders.c.expires_at, orders.c.id)
                    .limit(normalized_limit)
                )
                .mappings()
                .all()
            )
        released: list[dict[str, Any]] = []
        for row in rows:
            order_id = str(row["order_id"])
            self.update_order_status(order_id, status="已取消", updated_by=processed_by, updated_at=now)
            with self.engine.begin() as connection:
                connection.execute(
                    orders.update()
                    .where(orders.c.order_id == order_id)
                    .values(released_at=now, release_reason="unpaid_timeout")
                )
            details = self.get_order(order_id)
            if details:
                released.append(details["order"])
        return released

    def list_inventory_movements(self, *, order_id: str | None = None) -> list[dict[str, Any]]:
        statement = select(inventory_movements)
        if order_id:
            statement = statement.where(inventory_movements.c.order_id == order_id)
        statement = statement.order_by(inventory_movements.c.created_at, inventory_movements.c.movement_id)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [dict(row) for row in rows]

    @staticmethod
    def _inventory_movement_values(
        items: list[dict[str, Any]],
        *,
        movement_type: str,
        created_by: str,
        created_at: str,
        direction: int,
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str, str], int] = {}
        for item in items:
            key = (
                str(item["order_id"]),
                str(item["item_id"]),
                str(item["warehouse_id"]),
                str(item["location_code"]),
            )
            grouped[key] = grouped.get(key, 0) + int(item["quantity"])
        return [
            {
                "movement_id": f"IM-{created_at}-{index + 1}".replace(":", "").replace("+", "").replace("-", ""),
                "order_id": order_id,
                "movement_type": movement_type,
                "item_id": item_id,
                "warehouse_id": warehouse_id,
                "location_code": location_code,
                "quantity_delta": quantity * direction,
                "created_by": created_by,
                "created_at": created_at,
            }
            for index, ((order_id, item_id, warehouse_id, location_code), quantity) in enumerate(grouped.items())
        ]


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
