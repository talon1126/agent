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

ORDER_STATUS_PENDING_FULFILLMENT_REVIEW = "pending_fulfillment_review"
ORDER_STATUS_UNPAID = "unpaid"
ORDER_STATUS_PENDING_SHIPMENT = "pending_shipment"
ORDER_STATUS_SHIPPED = "shipped"
ORDER_STATUS_ARRIVED = "arrived"
ORDER_STATUS_REFUNDED = "refunded"
ORDER_STATUS_RETURNED = "returned"
ORDER_STATUS_CANCELED = "canceled"

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
    Column("image", Text, nullable=False, default=""),
)

item_reviews = Table(
    "item_reviews",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("item_id", String(64), nullable=False, index=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("rating", Integer, nullable=False),
    Column("title", String(120), nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

inventory_location_balances = Table(
    "inventory_location_balances",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("warehouse_id", String, nullable=False, index=True),
    Column("location_code", String, nullable=False, index=True),
    Column("item_id", String, nullable=False, index=True),
    Column("production_date", String, nullable=False),
    Column("expiry_date", String, nullable=False),
    Column("quantity_on_hand", Integer, nullable=False, default=0),
    Column("reorder_threshold", Integer, nullable=False, default=0),
    Column("storage_status", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
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

item_rank_events = Table(
    "item_rank_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("item_id", String(64), nullable=False, index=True),
    Column("category_id", String(64), nullable=False, index=True),
    Column("event_type", String(32), nullable=False, index=True),
    Column("event_weight", Numeric(12, 2), nullable=False, default=0),
    Column("user_id", Integer, nullable=True, index=True),
    Column("occurred_at", String, nullable=False),
    Column("created_at", String, nullable=False),
)

category_rank_snapshots = Table(
    "category_rank_snapshots",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("category_id", String(64), nullable=False, index=True),
    Column("rank_type", String(32), nullable=False, index=True),
    Column("window_type", String(32), nullable=False, index=True),
    Column("rank", Integer, nullable=False),
    Column("item_id", String(64), nullable=False, index=True),
    Column("score", Numeric(12, 2), nullable=False, default=0),
    Column("generated_at", String, nullable=False),
    UniqueConstraint(
        "category_id",
        "rank_type",
        "window_type",
        "item_id",
        name="uq_category_rank_snapshot_item",
    ),
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
    Column("approval_status", String, nullable=False, index=True, default="pending"),
    Column("source", String, nullable=False, default="warehouse"),
    Column("supplier_id", String, nullable=False, index=True),
    Column("supplier_name", String, nullable=False),
    Column("item_id", String, nullable=False, index=True),
    Column("item_name", String, nullable=False, default=""),
    Column("category_id", String, nullable=False, index=True, default=""),
    Column("category_name", String, nullable=False, default=""),
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
    Column("reason", String, nullable=False, default=""),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("updated_by", String, nullable=False, default=""),
)

# Backward-compatible Python symbol for older imports. The physical table is
# intentionally renamed to purchase_orders.
purchase_order_drafts = purchase_orders

WAREHOUSE_TABLE_COMMENTS = {
    "warehouses": "仓库主数据表，保存企业仓库的编号、名称、城市和启用状态。",
    "storage_locations": "具体库位表，保存仓库内 A1、B1、C1 等可存储位置及容量属性。",
    "categories": "商品分类表，保存纸品、乳制品、饮料等业务分类和存储要求。",
    "items": "商品主数据表，保存每个商品的名称、品牌、规格、单位、条码和图片地址。",
    "item_reviews": "商品评论表，保存用户对商品的星级评分、标题和正文。",
    "inventory_location_balances": "库位库存余额表，保存当前可售库存；采购入库、发仓扣减和退回通过库存流水追踪。",
    "delivery_providers": "物流供应商表，保存顺丰、京东、圆通等承运商基础信息供订单和 Delivery Agent 使用。",
    "users": "TalonMart 用户表，保存购物车 v1 使用的测试用户资料。",
    "delivery_addresses": "TalonMart 配送地址表，保存购物车结算创建订单时使用的用户收货地址。",
    "cart_items": "购物车明细表，按用户和商品保存加入购物车时的商品快照价格与数量。",
    "flash_sales": "秒杀活动表，一条活动绑定一个秒杀商品和独立营销库存配额。",
    "flash_sale_claims": "秒杀抢购结果表，记录用户抢购结果和关联订单，保证一人一单。",
    "item_rank_events": "商品排行榜事件事实表，记录浏览、加购、购买、收藏、评论等可聚合行为。",
    "category_rank_snapshots": "分类排行榜快照表，保存各分类下的商品排名、分数和生成时间。",
    "procurement_suppliers": "采购供应商表，保存 mock 供应商、交期、价格和可靠性。",
    "purchase_orders": "采购单表，保存低库存采购需求、审批状态、供应商、支付状态和仓库同步状态。",
    "orders": "订单主表，保存下单、发仓确认、付款、发货、到货、退款和退货状态，并保留物流供应商与快递员联系方式供 Delivery Agent 查询。",
    "order_items": "订单明细表，保存待发仓确认商品和确认后扣减命中的仓库、库位和数量。",
    "inventory_movements": "库存流水表，记录发仓确认、退款、退货和未付款超时释放对库位库存余额的影响。",
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
        "image": "商品主图 URL，可指向本地演示图片或后续 OSS 图片地址。",
    },
    "item_reviews": {
        "id": "评论自增整数主键。",
        "item_id": "评论所属商品编号。",
        "user_id": "评论用户 ID。",
        "rating": "星级评分，范围 1 到 5。",
        "title": "评论标题。",
        "content": "评论正文。",
        "created_at": "评论创建时间。",
        "updated_at": "评论更新时间。",
    },
    "inventory_location_balances": {
        "id": "库位余额自增整数主键。",
        "warehouse_id": "余额所在仓库编号。",
        "location_code": "余额所在具体库位编号。",
        "item_id": "余额对应商品编号。",
        "production_date": "当前余额的最早生产日期，用于 FEFO 和风险展示。",
        "expiry_date": "当前余额的最早保质期到期日期，用于 FEFO 和风险展示。",
        "quantity_on_hand": "当前可售库存余额；员工确认发仓扣减，取消或退货时加回。",
        "reorder_threshold": "补货预警阈值。",
        "storage_status": "余额库存状态，例如 available、quality_hold。",
        "created_at": "余额行创建时间。",
        "updated_at": "余额行更新时间。",
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
        "approval_status": "采购审批状态，例如 pending、approved 或 rejected。",
        "source": "采购需求来源，例如 warehouse。",
        "supplier_id": "采购单选用的供应商编号。",
        "supplier_name": "采购单选用的供应商名称。",
        "item_id": "采购商品编号。",
        "item_name": "采购商品名称。",
        "category_id": "采购商品分类编号。",
        "category_name": "采购商品分类名称。",
        "warehouse_id": "采购商品预计入库仓库编号。",
        "warehouse_name": "采购商品预计入库仓库名称。",
        "location_code": "采购商品预计入库库位。",
        "quantity": "采购数量。",
        "unit_price": "采购单价，按 currency 表示。",
        "currency": "价格币种，例如 CNY。",
        "estimated_total_price": "预计采购总价。",
        "lead_time_days": "预计交期天数。",
        "estimated_arrival_date": "预计到达日期，格式为 YYYY-MM-DD。",
        "payment_status": "支付状态，例如 unpaid、paid。",
        "warehouse_sync_status": "仓库同步状态，例如 pending_arrival、arrived_unsynced、synced。",
        "arrived_at": "采购单实际确认到仓时间。",
        "reason": "创建或驳回采购单的业务原因。",
        "created_by": "创建采购单的用户或系统身份。",
        "created_at": "采购单创建时间。",
        "updated_at": "采购单更新时间。",
        "updated_by": "最近更新采购单的用户或系统身份。",
    },
    "orders": {
        "id": "订单自增整数主键。",
        "order_id": "订单业务编号，例如 ORD-CODEX-9001。",
        "customer_id": "客户编号。",
        "status": "订单状态：pending_fulfillment_review、unpaid、pending_shipment、shipped、arrived、refunded、returned、canceled。",
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
        "expires_at": "unpaid 订单自动取消截止时间。",
        "release_reason": "订单取消原因，例如 unpaid_timeout。",
    },
    "order_items": {
        "id": "订单明细自增整数主键。",
        "order_id": "关联订单业务编号。",
        "customer_id": "客户编号。",
        "status": "明细状态，例如 pending_fulfillment_review、unpaid、pending_shipment、refunded、returned。",
        "item_id": "明细商品编号。",
        "warehouse_id": "扣减或加回库存所在仓库编号。",
        "location_code": "扣减或加回库存所在库位。",
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
    "item_rank_events": {
        "id": "排行榜事件自增整数主键。",
        "item_id": "发生排行榜行为的商品编号。",
        "category_id": "事件发生时商品所属分类编号。",
        "event_type": "事件类型，例如 view、add_to_cart、purchase、favorite、review。",
        "event_weight": "事件参与排行计算的权重。",
        "user_id": "触发事件的用户 ID；系统种子事件可为空。",
        "occurred_at": "业务事件发生时间。",
        "created_at": "事件写入时间。",
    },
    "category_rank_snapshots": {
        "id": "排行榜快照自增整数主键。",
        "category_id": "榜单所属商品分类编号。",
        "rank_type": "榜单类型，例如 hot。",
        "window_type": "统计时间窗口，例如 all_time。",
        "rank": "商品在当前榜单中的排名，从 1 开始。",
        "item_id": "榜单商品编号。",
        "score": "聚合后的排序分数。",
        "generated_at": "快照生成时间。",
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
    metadata.create_all(
        engine,
        tables=[
            warehouses,
            storage_locations,
            categories,
            items,
            item_reviews,
            inventory_location_balances,
            procurement_suppliers,
            users,
            delivery_addresses,
            cart_items,
            flash_sales,
            flash_sale_claims,
            item_rank_events,
            category_rank_snapshots,
            purchase_orders,
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
    with engine.begin() as connection:
        if inspector.has_table("inventory_batches"):
            connection.execute(text("DROP TABLE inventory_batches"))
        if inspector.has_table("warehouse_inventory_sync_jobs"):
            connection.execute(text("DROP TABLE warehouse_inventory_sync_jobs"))
        if inspector.has_table(inventory_location_balances.name):
            balance_columns = {column["name"] for column in inspector.get_columns(inventory_location_balances.name)}
            if "batch_no" in balance_columns:
                connection.execute(text("ALTER TABLE inventory_location_balances DROP COLUMN batch_no"))
        if inspector.has_table(order_items.name):
            item_columns = {column["name"] for column in inspector.get_columns(order_items.name)}
            if "batch_no" in item_columns:
                connection.execute(text("ALTER TABLE order_items DROP COLUMN batch_no"))
    inspector = inspect(engine)
    if not inspector.has_table(purchase_orders.name):
        return
    existing_columns = {
        column["name"]
        for column in inspector.get_columns(purchase_orders.name)
    }
    missing_column_sql = {
        "approval_status": "ALTER TABLE purchase_orders ADD COLUMN approval_status VARCHAR NOT NULL DEFAULT 'pending'",
        "source": "ALTER TABLE purchase_orders ADD COLUMN source VARCHAR NOT NULL DEFAULT 'warehouse'",
        "item_name": "ALTER TABLE purchase_orders ADD COLUMN item_name VARCHAR NOT NULL DEFAULT ''",
        "category_id": "ALTER TABLE purchase_orders ADD COLUMN category_id VARCHAR NOT NULL DEFAULT ''",
        "category_name": "ALTER TABLE purchase_orders ADD COLUMN category_name VARCHAR NOT NULL DEFAULT ''",
        "estimated_arrival_date": "ALTER TABLE purchase_orders ADD COLUMN estimated_arrival_date VARCHAR NOT NULL DEFAULT ''",
        "warehouse_id": "ALTER TABLE purchase_orders ADD COLUMN warehouse_id VARCHAR NOT NULL DEFAULT ''",
        "warehouse_name": "ALTER TABLE purchase_orders ADD COLUMN warehouse_name VARCHAR NOT NULL DEFAULT ''",
        "location_code": "ALTER TABLE purchase_orders ADD COLUMN location_code VARCHAR",
        "payment_status": "ALTER TABLE purchase_orders ADD COLUMN payment_status VARCHAR NOT NULL DEFAULT 'unpaid'",
        "warehouse_sync_status": "ALTER TABLE purchase_orders ADD COLUMN warehouse_sync_status VARCHAR NOT NULL DEFAULT 'pending_arrival'",
        "arrived_at": "ALTER TABLE purchase_orders ADD COLUMN arrived_at VARCHAR NOT NULL DEFAULT ''",
        "reason": "ALTER TABLE purchase_orders ADD COLUMN reason VARCHAR NOT NULL DEFAULT ''",
        "updated_by": "ALTER TABLE purchase_orders ADD COLUMN updated_by VARCHAR NOT NULL DEFAULT ''",
    }
    with engine.begin() as connection:
        for column_name, statement in missing_column_sql.items():
            if column_name not in existing_columns:
                connection.execute(text(statement))
        for column_name in ("current_quantity", "reorder_threshold", "suggested_quantity"):
            if column_name in existing_columns:
                connection.execute(text(f"ALTER TABLE purchase_orders DROP COLUMN {column_name}"))
        if "request_id" in existing_columns:
            connection.execute(text("ALTER TABLE purchase_orders DROP COLUMN request_id"))
        if inspector.has_table("replenishment_requests"):
            connection.execute(text("DROP TABLE replenishment_requests"))
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
                "release_reason": "ALTER TABLE orders ADD COLUMN release_reason VARCHAR NOT NULL DEFAULT ''",
            }
            for column_name, statement in order_missing_column_sql.items():
                if column_name not in order_columns:
                    connection.execute(text(statement))
            if "released_at" in order_columns:
                connection.execute(text("ALTER TABLE orders DROP COLUMN released_at"))
            if "requested_items_json" in order_columns:
                connection.execute(text("ALTER TABLE orders DROP COLUMN requested_items_json"))
            connection.execute(text("UPDATE orders SET status = 'unpaid' WHERE status IN ('created', '未付款')"))
            connection.execute(text("UPDATE orders SET status = 'pending_shipment' WHERE status IN ('paid', '待发货')"))
            connection.execute(text("UPDATE orders SET status = 'shipped' WHERE status = '已发货'"))
            connection.execute(text("UPDATE orders SET status = 'arrived' WHERE status = '已到货'"))
            connection.execute(text("UPDATE orders SET status = 'refunded' WHERE status IN ('cancelled', '已退款')"))
            connection.execute(text("UPDATE orders SET status = 'returned' WHERE status = '已退货'"))
            connection.execute(text("UPDATE orders SET status = 'canceled' WHERE status = '已取消'"))
        if inspector.has_table(order_items.name):
            connection.execute(text("UPDATE order_items SET status = 'unpaid' WHERE status = '未付款'"))
            connection.execute(text("UPDATE order_items SET status = 'pending_shipment' WHERE status IN ('paid', '待发货')"))
            connection.execute(text("UPDATE order_items SET status = 'shipped' WHERE status = '已发货'"))
            connection.execute(text("UPDATE order_items SET status = 'arrived' WHERE status = '已到货'"))
            connection.execute(text("UPDATE order_items SET status = 'refunded' WHERE status IN ('cancelled', '已退款')"))
            connection.execute(text("UPDATE order_items SET status = 'returned' WHERE status = '已退货'"))
            connection.execute(text("UPDATE order_items SET status = 'canceled' WHERE status = '已取消'"))
        if inspector.has_table(items.name):
            item_columns = {column["name"] for column in inspector.get_columns(items.name)}
            if "shelf_life_days" not in item_columns:
                connection.execute(text("ALTER TABLE items ADD COLUMN shelf_life_days INTEGER NOT NULL DEFAULT 365"))
            if "price" not in item_columns:
                connection.execute(text("ALTER TABLE items ADD COLUMN price NUMERIC(12, 2) NOT NULL DEFAULT 0"))
            if "search_text" not in item_columns:
                connection.execute(text("ALTER TABLE items ADD COLUMN search_text TEXT NOT NULL DEFAULT ''"))
            if "image" not in item_columns:
                connection.execute(text("ALTER TABLE items ADD COLUMN image TEXT NOT NULL DEFAULT ''"))
            connection.execute(
                text(
                    "UPDATE items "
                    "SET image = 'https://static.talonmart.local/products/' || item_id || '.jpg' "
                    "WHERE image = ''"
                )
            )
            connection.execute(
                text(
                    "UPDATE items "
                    "SET search_text = trim(item_id || ' ' || item_name || ' ' || brand || ' ' || spec) "
                    "WHERE search_text = ''"
                )
            )
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


def load_inventory_location_balance_fixture_rows(fixture_dir: Path) -> list[dict[str, Any]]:
    now = "2026-05-24T00:00:00+00:00"
    rows = load_fixture_rows(fixture_dir, "inventory_location_balances.json")
    return [
        {
            "warehouse_id": row["warehouse_id"],
            "location_code": row["location_code"],
            "item_id": row["item_id"],
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


def default_item_image_url(row: dict[str, Any]) -> str:
    """Return a deterministic product image URL for seeded catalog rows.

    Args:
        row: Item fixture row. The function reads `item_id` so the generated
            URL is stable across reseeds and can later be replaced by an OSS URL
            without changing table contracts.

    Returns:
        A URL string suitable for frontend image tags and Feishu text/url fields.
    """

    item_id = str(row.get("item_id") or "item")
    return f"https://static.talonmart.local/products/{item_id}.jpg"


def load_item_fixture_rows(fixture_dir: Path) -> list[dict[str, Any]]:
    rows = load_fixture_rows(fixture_dir, "items.json")
    return [
        {
            **row,
            "image": str(row.get("image") or default_item_image_url(row)),
            "search_text": item_search_text(row),
        }
        for row in rows
    ]


def default_item_rank_event_rows(item_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create deterministic ranking facts for the local demo catalog.

    The ranking feature is backed by durable event facts, not hardcoded frontend
    cards. Seed events give fresh local databases a useful leaderboard while
    preserving the same rebuild path that production-like events use later.

    Args:
        item_rows: Item fixture rows already normalized for insertion.

    Returns:
        One synthetic popularity event per item. Scores intentionally vary by
        stable item order so every category can produce a deterministic ranking.
    """
    timestamp = datetime.now(UTC).isoformat()
    total = len(item_rows)
    return [
        {
            "item_id": str(row["item_id"]),
            "category_id": str(row["category_id"]),
            "event_type": "seed_popularity",
            "event_weight": total - index,
            "user_id": None,
            "occurred_at": timestamp,
            "created_at": timestamp,
        }
        for index, row in enumerate(item_rows)
    ]


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


def default_item_review_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "item_id": "item_milk_pure",
            "user_id": 1,
            "rating": 4,
            "title": "Reliable daily milk",
            "content": "Fresh taste and enough stock for the weekly grocery run.",
            "created_at": "2026-05-30T09:30:00+08:00",
            "updated_at": "2026-05-30T09:30:00+08:00",
        },
        {
            "id": 2,
            "item_id": "item_milk_pure",
            "user_id": 2,
            "rating": 5,
            "title": "Family pack is convenient",
            "content": "The 1L multipack is easy to store and works well for breakfast.",
            "created_at": "2026-06-01T10:00:00+08:00",
            "updated_at": "2026-06-01T10:00:00+08:00",
        },
        {
            "id": 3,
            "item_id": "item_cola_zero",
            "user_id": 1,
            "rating": 4,
            "title": "Good party drink",
            "content": "Crisp taste, fair price, and the pack size is useful for gatherings.",
            "created_at": "2026-05-31T16:20:00+08:00",
            "updated_at": "2026-05-31T16:20:00+08:00",
        },
        {
            "id": 4,
            "item_id": "item_vinda_tissue",
            "user_id": 2,
            "rating": 5,
            "title": "Soft and easy to restock",
            "content": "Good household tissue option when the pantry stock is running low.",
            "created_at": "2026-06-02T11:15:00+08:00",
            "updated_at": "2026-06-02T11:15:00+08:00",
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
    """Build the pg_search-backed keyword search statement for storefront search.

    Args:
        None.

    Returns:
        A SQLAlchemy text statement that ranks products by pg_search score and can
        optionally constrain results to a department category.
    """
    return text(
        """
        SELECT
            items.item_id,
            items.item_name,
            items.brand,
            items.spec,
            items.price,
            items.image,
            items.category_id,
            categories.category_name,
            pdb.score(items.item_id) AS score
        FROM items
        JOIN categories ON categories.category_id = items.category_id
        WHERE search_text &&& :query
          AND (CAST(:category_id AS TEXT) IS NULL OR items.category_id = CAST(:category_id AS TEXT))
        ORDER BY score DESC, item_id
        LIMIT :limit
        """
    )


def build_item_category_search_sql():
    """Build the deterministic category listing statement for Departments browsing.

    Args:
        None.

    Returns:
        A SQLAlchemy text statement that returns all products in one category in
        stable item-id order. The constant score keeps the response shape aligned
        with keyword search without implying a ranked relevance score.
    """
    return text(
        """
        SELECT
            items.item_id,
            items.item_name,
            items.brand,
            items.spec,
            items.price,
            items.image,
            items.category_id,
            categories.category_name,
            1.0 AS score
        FROM items
        JOIN categories ON categories.category_id = items.category_id
        WHERE items.category_id = :category_id
        ORDER BY items.item_id
        LIMIT :limit
        """
    )


def build_item_catalog_listing_sql():
    """Build the deterministic catalog listing statement for operations sync.

    Returns:
        A SQLAlchemy text statement that lists catalog products with display
        category names when no keyword or category filter is provided. This path
        avoids running pg_search with an empty query during Feishu read-model
        synchronization.
    """

    return text(
        """
        SELECT
            items.item_id,
            items.item_name,
            items.brand,
            items.spec,
            items.price,
            items.image,
            items.category_id,
            categories.category_name,
            1.0 AS score
        FROM items
        JOIN categories ON categories.category_id = items.category_id
        ORDER BY items.category_id, items.item_id
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


def rebuild_category_rankings_for_connection(
    connection: Any,
    *,
    category_id: str | None = None,
    rank_type: str = "hot",
    window_type: str = "all_time",
    limit: int = 100,
    generated_at: str,
) -> list[dict[str, Any]]:
    """Aggregate ranking events into durable category snapshot rows.

    Args:
        connection: SQLAlchemy connection that owns the surrounding transaction.
        category_id: Optional category to rebuild. When omitted, all categories
            with ranking events are rebuilt.
        rank_type: Logical ranking name, currently `hot`.
        window_type: Time window label, currently `all_time`.
        limit: Maximum number of items written per category.
        generated_at: Timestamp stored on snapshot rows for traceability.

    Returns:
        Snapshot rows joined with current item display fields in the same shape
        returned by `WarehouseRepository.get_category_ranking`.
    """
    categories_to_rebuild: list[str]
    if category_id:
        categories_to_rebuild = [category_id]
    else:
        categories_to_rebuild = [
            str(row["category_id"])
            for row in connection.execute(
                select(item_rank_events.c.category_id).distinct().order_by(item_rank_events.c.category_id)
            ).mappings()
        ]

    rebuilt_rows: list[dict[str, Any]] = []
    capped_limit = max(1, min(int(limit or 100), 500))
    for current_category_id in categories_to_rebuild:
        connection.execute(
            category_rank_snapshots.delete()
            .where(category_rank_snapshots.c.category_id == current_category_id)
            .where(category_rank_snapshots.c.rank_type == rank_type)
            .where(category_rank_snapshots.c.window_type == window_type)
        )
        scored_rows = (
            connection.execute(
                select(
                    item_rank_events.c.item_id,
                    item_rank_events.c.category_id,
                    func.sum(item_rank_events.c.event_weight).label("score"),
                )
                .where(item_rank_events.c.category_id == current_category_id)
                .group_by(item_rank_events.c.item_id, item_rank_events.c.category_id)
                .order_by(func.sum(item_rank_events.c.event_weight).desc(), item_rank_events.c.item_id)
                .limit(capped_limit)
            )
            .mappings()
            .all()
        )
        snapshot_rows = [
            {
                "category_id": str(row["category_id"]),
                "rank_type": rank_type,
                "window_type": window_type,
                "rank": index,
                "item_id": str(row["item_id"]),
                "score": float(row["score"] or 0),
                "generated_at": generated_at,
            }
            for index, row in enumerate(scored_rows, start=1)
        ]
        if snapshot_rows:
            connection.execute(category_rank_snapshots.insert(), snapshot_rows)
            rebuilt_rows.extend(snapshot_rows)

    return hydrate_category_rank_snapshot_rows(connection, rebuilt_rows)


def hydrate_category_rank_snapshot_rows(connection: Any, snapshot_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach current item and category display fields to ranking snapshots."""
    if not snapshot_rows:
        return []
    item_ids = [str(row["item_id"]) for row in snapshot_rows]
    item_rows = (
        connection.execute(
            select(
                items.c.item_id,
                items.c.item_name,
                items.c.brand,
                items.c.spec,
                items.c.category_id,
                categories.c.category_name,
                items.c.price,
            )
            .select_from(items.outerjoin(categories, items.c.category_id == categories.c.category_id))
            .where(items.c.item_id.in_(item_ids))
        )
        .mappings()
        .all()
    )
    items_by_id = {str(row["item_id"]): dict(row) for row in item_rows}
    hydrated: list[dict[str, Any]] = []
    for row in snapshot_rows:
        item = items_by_id.get(str(row["item_id"]))
        if not item:
            continue
        hydrated.append(
            {
                "rank": int(row["rank"]),
                "item_id": str(row["item_id"]),
                "item_name": str(item["item_name"]),
                "brand": str(item["brand"]),
                "spec": str(item["spec"]),
                "category_id": str(row["category_id"]),
                "category_name": str(item.get("category_name") or row["category_id"]),
                "price": float(item["price"]),
                "score": float(row["score"]),
                "rank_type": str(row["rank_type"]),
                "window_type": str(row["window_type"]),
                "generated_at": str(row["generated_at"]),
            }
        )
    return hydrated


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
        connection.execute(category_rank_snapshots.delete())
        connection.execute(item_rank_events.delete())
        connection.execute(item_reviews.delete())
        connection.execute(delivery_addresses.delete())
        connection.execute(inventory_location_balances.delete())
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
        item_rows = load_item_fixture_rows(fixture_dir)
        connection.execute(items.insert(), item_rows)
        connection.execute(item_rank_events.insert(), default_item_rank_event_rows(item_rows))
        connection.execute(item_reviews.insert(), default_item_review_rows())
        if engine.dialect.name == "postgresql":
            connection.execute(
                text(
                    "SELECT setval("
                    "pg_get_serial_sequence('item_reviews', 'id'), "
                    "COALESCE((SELECT MAX(id) FROM item_reviews), 1), "
                    "true"
                    ")"
                )
            )
        connection.execute(users.insert(), default_user_rows())
        connection.execute(delivery_addresses.insert(), default_delivery_address_rows())
        connection.execute(flash_sales.insert(), default_flash_sale_rows())
        connection.execute(
            delivery_providers.insert(),
            load_fixture_rows(fixture_dir, "delivery_providers.json"),
        )
        connection.execute(
            inventory_location_balances.insert(),
            load_inventory_location_balance_fixture_rows(fixture_dir),
        )
        connection.execute(
            procurement_suppliers.insert(),
            load_fixture_rows(fixture_dir, "procurement_suppliers.json"),
        )
        rebuild_category_rankings_for_connection(
            connection,
            rank_type="hot",
            window_type="all_time",
            limit=100,
            generated_at=datetime.now(UTC).isoformat(),
        )
    ensure_item_pg_search_index(engine)


class WarehouseRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def list_inventory_balances(
        self,
        *,
        item_id: str | None = None,
        warehouse_id: str | None = None,
        location_code: str | None = None,
        category_id: str | None = None,
    ) -> list[dict[str, Any]]:
        statement = (
            select(
                inventory_location_balances.c.id.label("balance_id"),
                inventory_location_balances.c.warehouse_id,
                inventory_location_balances.c.location_code,
                inventory_location_balances.c.item_id,
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
        statement = statement.order_by(
            inventory_location_balances.c.warehouse_id,
            inventory_location_balances.c.location_code,
            items.c.item_name,
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
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [dict(row) for row in rows]

    def search_items(
        self,
        query: str | None = None,
        *,
        category_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return storefront products for keyword search or department browsing.

        Args:
            query: Optional storefront keyword. When present, pg_search BM25 ranks
                matching rows by `search_text`.
            category_id: Optional normalized category id used by the Departments
                guide. It filters keyword searches and drives deterministic
                category-only listings.
            limit: Maximum number of product rows to return.

        Returns:
            Product rows with numeric prices. Inventory balances are joined by the
            router because search results and balance snapshots have different
            fallback sources.
        """
        normalized_query = (query or "").strip()
        if category_id and not normalized_query:
            statement = build_item_category_search_sql()
            params = {"category_id": category_id, "limit": limit}
        elif not category_id and not normalized_query:
            statement = build_item_catalog_listing_sql()
            params = {"limit": limit}
        else:
            statement = build_item_search_sql()
            params = {
                "category_id": category_id,
                "query": normalized_query,
                "limit": limit,
            }
        with self.engine.connect() as connection:
            rows = connection.execute(statement, params).mappings().all()
        return [{**dict(row), "price": float(row["price"])} for row in rows]

    def record_item_rank_event(
        self,
        *,
        item_id: str,
        event_type: str,
        user_id: int | None = None,
        occurred_at: str,
    ) -> dict[str, Any]:
        """Persist one item behavior fact for later leaderboard rebuilds.

        Args:
            item_id: Catalog item that received the behavior.
            event_type: Behavior type. Unknown values are accepted with weight
                `1` so new UI events do not break ingestion.
            user_id: Optional local user id associated with the behavior.
            occurred_at: Business timestamp supplied by the caller.

        Returns:
            The stored event row.

        Raises:
            ValueError: If `item_id` does not exist in the catalog.
        """
        event_weights = {
            "view": 1,
            "review": 2,
            "add_to_cart": 3,
            "favorite": 4,
            "purchase": 5,
            "seed_popularity": 1,
        }
        with self.engine.begin() as connection:
            item = (
                connection.execute(
                    select(items.c.item_id, items.c.category_id).where(items.c.item_id == item_id)
                )
                .mappings()
                .one_or_none()
            )
            if not item:
                raise ValueError("item_not_found")
            values = {
                "item_id": str(item["item_id"]),
                "category_id": str(item["category_id"]),
                "event_type": event_type,
                "event_weight": event_weights.get(event_type, 1),
                "user_id": user_id,
                "occurred_at": occurred_at,
                "created_at": datetime.now(UTC).isoformat(),
            }
            result = connection.execute(item_rank_events.insert().values(**values))
            row = (
                connection.execute(
                    select(item_rank_events).where(item_rank_events.c.id == result.inserted_primary_key[0])
                )
                .mappings()
                .one()
            )
        return self._format_item_rank_event(row)

    def rebuild_category_rankings(
        self,
        *,
        category_id: str | None = None,
        rank_type: str = "hot",
        window_type: str = "all_time",
        limit: int = 100,
        generated_at: str | None = None,
    ) -> list[dict[str, Any]]:
        """Rebuild ranking snapshots from durable item rank events."""
        with self.engine.begin() as connection:
            return rebuild_category_rankings_for_connection(
                connection,
                category_id=category_id,
                rank_type=rank_type,
                window_type=window_type,
                limit=limit,
                generated_at=generated_at or datetime.now(UTC).isoformat(),
            )

    def get_category_ranking(
        self,
        *,
        category_id: str,
        rank_type: str = "hot",
        window_type: str = "all_time",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Read a category leaderboard from PostgreSQL snapshots."""
        capped_limit = max(1, min(int(limit or 20), 100))
        with self.engine.connect() as connection:
            snapshot_rows = (
                connection.execute(
                    select(category_rank_snapshots)
                    .where(category_rank_snapshots.c.category_id == category_id)
                    .where(category_rank_snapshots.c.rank_type == rank_type)
                    .where(category_rank_snapshots.c.window_type == window_type)
                    .order_by(category_rank_snapshots.c.rank, category_rank_snapshots.c.item_id)
                    .limit(capped_limit)
                )
                .mappings()
                .all()
            )
            return hydrate_category_rank_snapshot_rows(connection, [dict(row) for row in snapshot_rows])

    def get_ranked_items_by_ids(
        self,
        item_ids: list[str],
        *,
        rank_type: str = "hot",
        scores: dict[str, float],
        window_type: str = "all_time",
    ) -> list[dict[str, Any]]:
        """Hydrate Redis ZSET item ids while preserving cached order."""
        if not item_ids:
            return []
        with self.engine.connect() as connection:
            item_rows = (
                connection.execute(
                    select(
                        items.c.item_id,
                        items.c.item_name,
                        items.c.brand,
                        items.c.spec,
                        items.c.category_id,
                        categories.c.category_name,
                        items.c.price,
                    )
                    .select_from(items.outerjoin(categories, items.c.category_id == categories.c.category_id))
                    .where(items.c.item_id.in_(item_ids))
                )
                .mappings()
                .all()
            )
        items_by_id = {str(row["item_id"]): dict(row) for row in item_rows}
        rows: list[dict[str, Any]] = []
        for index, item_id in enumerate(item_ids, start=1):
            item = items_by_id.get(item_id)
            if not item:
                continue
            rows.append(
                {
                    "rank": index,
                    "item_id": item_id,
                    "item_name": str(item["item_name"]),
                    "brand": str(item["brand"]),
                    "spec": str(item["spec"]),
                    "category_id": str(item["category_id"]),
                    "category_name": str(item.get("category_name") or item["category_id"]),
                    "price": float(item["price"]),
                    "score": float(scores.get(item_id, 0)),
                    "rank_type": rank_type,
                    "window_type": window_type,
                    "generated_at": "",
                }
            )
        return rows

    def list_home_hot_rankings(
        self,
        *,
        rank_type: str = "hot",
        window_type: str = "all_time",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return the strongest ranking rows across all categories for the homepage."""
        capped_limit = max(1, min(int(limit or 20), 100))
        with self.engine.connect() as connection:
            snapshot_rows = (
                connection.execute(
                    select(category_rank_snapshots)
                    .where(category_rank_snapshots.c.rank_type == rank_type)
                    .where(category_rank_snapshots.c.window_type == window_type)
                    .order_by(category_rank_snapshots.c.score.desc(), category_rank_snapshots.c.item_id)
                    .limit(capped_limit)
                )
                .mappings()
                .all()
            )
            hydrated = hydrate_category_rank_snapshot_rows(connection, [dict(row) for row in snapshot_rows])
        return hydrated

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
                items.c.image,
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

    def list_item_reviews(self, item_id: str, *, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        statement = (
            select(item_reviews)
            .where(item_reviews.c.item_id == item_id)
            .order_by(item_reviews.c.created_at.desc(), item_reviews.c.id.desc())
            .limit(limit)
            .offset(offset)
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [self._format_item_review(row) for row in rows]

    def item_review_summary(self, item_id: str) -> dict[str, Any]:
        statement = select(
            func.avg(item_reviews.c.rating).label("average_rating"),
            func.count(item_reviews.c.id).label("review_count"),
        ).where(item_reviews.c.item_id == item_id)
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        review_count = int(row["review_count"] or 0) if row else 0
        average_rating = round(float(row["average_rating"] or 0), 1) if review_count else 0
        return {"average_rating": average_rating, "review_count": review_count}

    def create_item_review(
        self,
        item_id: str,
        payload: dict[str, Any],
        *,
        created_at: str,
    ) -> dict[str, Any]:
        values = {
            "item_id": item_id,
            "user_id": int(payload["user_id"]),
            "rating": int(payload["rating"]),
            "title": str(payload["title"]).strip(),
            "content": str(payload["content"]).strip(),
            "created_at": created_at,
            "updated_at": created_at,
        }
        with self.engine.begin() as connection:
            result = connection.execute(item_reviews.insert().values(**values))
            row = connection.execute(
                select(item_reviews).where(item_reviews.c.id == result.inserted_primary_key[0])
            ).mappings().first()
        return self._format_item_review(row)

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
        statement = (
            select(flash_sales, items.c.price.label("item_price"))
            .select_from(flash_sales.outerjoin(items, flash_sales.c.item_id == items.c.item_id))
            .order_by(flash_sales.c.id)
        )
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

    def list_flash_sale_claims(
        self,
        *,
        flash_sale_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return flash sale claim rows for Feishu read-model synchronization.

        Args:
            flash_sale_id: Optional flash sale id filter.
            status: Optional claim status filter.
            limit: Maximum number of claim rows returned to callers.

        Returns:
            Claim dictionaries ordered by id so scheduled syncs are stable.
        """

        capped_limit = max(1, min(int(limit or 100), 500))
        statement = select(flash_sale_claims).order_by(flash_sale_claims.c.id).limit(capped_limit)
        if flash_sale_id is not None:
            statement = statement.where(flash_sale_claims.c.flash_sale_id == int(flash_sale_id))
        if status:
            statement = statement.where(flash_sale_claims.c.status == status)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [dict(row) for row in rows]

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
        if item.get("item_price") is not None:
            item["item_price"] = float(item["item_price"])
        return item

    @staticmethod
    def _format_item_review(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["id"] = int(item["id"])
        item["user_id"] = int(item["user_id"])
        item["rating"] = int(item["rating"])
        return item

    @staticmethod
    def _format_item_rank_event(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["id"] = int(item["id"])
        item["event_weight"] = float(item["event_weight"])
        if item.get("user_id") is not None:
            item["user_id"] = int(item["user_id"])
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
                inventory_location_balances.c.production_date,
                inventory_location_balances.c.expiry_date,
                inventory_location_balances.c.quantity_on_hand,
                inventory_location_balances.c.reorder_threshold,
                inventory_location_balances.c.storage_status,
                inventory_location_balances.c.created_at,
                inventory_location_balances.c.updated_at,
            )
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
        payload.setdefault("approval_status", "pending")
        payload.setdefault("source", "warehouse")
        payload.setdefault("item_name", "")
        payload.setdefault("category_id", "")
        payload.setdefault("category_name", "")
        payload.pop("current_quantity", None)
        payload.pop("reorder_threshold", None)
        payload.pop("suggested_quantity", None)
        payload.setdefault("arrived_at", "")
        payload.setdefault("reason", "")
        payload.setdefault("updated_by", "")
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
        payload.pop("request_id", None)
        payload.setdefault("warehouse_id", "")
        payload.setdefault("warehouse_name", "")
        payload.setdefault("location_code", "")
        payload.setdefault("approval_status", "pending")
        payload.setdefault("source", "warehouse")
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

    def update_purchase_order_approval_status(
        self,
        purchase_order_id: str,
        *,
        approval_status: str,
        updated_by: str,
        updated_at: str,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        values: dict[str, Any] = {
            "approval_status": approval_status,
            "updated_by": updated_by,
            "updated_at": updated_at,
        }
        if reason is not None:
            values["reason"] = reason
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
        approval_status: str | None = None,
        warehouse_sync_status: str | None = None,
        purchase_order_id: str | None = None,
        payment_status: str | None = None,
    ) -> list[dict[str, Any]]:
        statement = select(purchase_orders)
        if approval_status:
            statement = statement.where(purchase_orders.c.approval_status == approval_status)
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

    def list_purchase_order_drafts(self) -> list[dict[str, Any]]:
        return self.list_purchase_orders()

    def sync_arrived_purchase_orders(
        self,
        *,
        limit: int,
        processed_by: str,
        processed_at: str,
        purchase_order_id: str | None = None,
    ) -> list[dict[str, Any]]:
        statement = (
            select(purchase_orders)
            .where(purchase_orders.c.payment_status == "paid")
            .where(purchase_orders.c.warehouse_sync_status == "arrived_unsynced")
            .order_by(purchase_orders.c.arrived_at, purchase_orders.c.purchase_order_id)
            .limit(limit)
        )
        if purchase_order_id:
            statement = statement.where(purchase_orders.c.purchase_order_id == purchase_order_id)
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
                expiry_day = production_day.toordinal() + int(item_row["shelf_life_days"])
                expiry_date = date.fromordinal(expiry_day).isoformat()
                reorder_threshold = _deterministic_reorder_threshold(
                    order["purchase_order_id"],
                    order["item_id"],
                    order["warehouse_id"],
                )
                quantity = int(order["quantity"])
                existing_balance = (
                    connection.execute(
                        select(inventory_location_balances)
                        .where(inventory_location_balances.c.warehouse_id == order["warehouse_id"])
                        .where(inventory_location_balances.c.location_code == location_code)
                        .where(inventory_location_balances.c.item_id == order["item_id"])
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
                            production_date=production_day.isoformat(),
                            expiry_date=expiry_date,
                            updated_at=processed_at,
                        )
                    )
                else:
                    connection.execute(
                        inventory_location_balances.insert().values(
                            warehouse_id=order["warehouse_id"],
                            location_code=location_code,
                            item_id=order["item_id"],
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
                        "item_id": order["item_id"],
                        "warehouse_id": order["warehouse_id"],
                        "warehouse_name": order["warehouse_name"],
                        "location_code": location_code,
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

    def sync_purchase_order_inventory(
        self,
        *,
        purchase_order_id: str,
        processed_by: str,
        processed_at: str,
    ) -> dict[str, Any] | None:
        synced_items = self.sync_arrived_purchase_orders(
            limit=1,
            processed_by=processed_by,
            processed_at=processed_at,
            purchase_order_id=purchase_order_id,
        )
        return synced_items[0] if synced_items else None

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

    def _delivery_provider(self, provider_id: str) -> dict[str, Any]:
        """Return the active delivery provider selected during fulfillment review.

        Fulfillment confirmation is the point where warehouse staff can choose
        the carrier. The repository resolves that carrier from the same
        `delivery_providers` table used by Delivery Agent, so order facts stay
        consistent across warehouse and delivery workflows.
        """
        normalized_provider_id = provider_id.strip() or "sf"
        providers = self.list_delivery_providers()
        provider = next((item for item in providers if item["provider_id"] == normalized_provider_id), None)
        if not provider:
            raise ValueError(f"delivery_provider_not_found:{normalized_provider_id}")
        return provider

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
            pending_items = self._pending_order_item_values(values, item_requests, updated_at)
            if pending_items:
                connection.execute(order_items.insert(), pending_items)
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
        if order["status"] == ORDER_STATUS_PENDING_FULFILLMENT_REVIEW:
            return details
        if order["status"] != ORDER_STATUS_UNPAID:
            raise ValueError(f"order_cannot_pay_from_{order['status']}")
        with self.engine.begin() as connection:
            connection.execute(
                order_items.update()
                .where(order_items.c.order_id == order_id)
                .where(order_items.c.status == ORDER_STATUS_UNPAID)
                .values(status=ORDER_STATUS_PENDING_FULFILLMENT_REVIEW, updated_at=updated_at)
            )
            connection.execute(
                orders.update()
                .where(orders.c.order_id == order_id)
                .values(
                    status=ORDER_STATUS_PENDING_FULFILLMENT_REVIEW,
                    updated_at=updated_at,
                    paid_at=updated_at,
                )
            )
        return self.get_order(order_id) or details

    def confirm_order_fulfillment(
        self,
        order_id: str,
        *,
        warehouse_id: str,
        delivery_provider_id: str | None = None,
        courier_phone: str = "",
        tracking_no: str = "",
        updated_by: str,
        updated_at: str,
    ) -> dict[str, Any]:
        details = self.get_order(order_id)
        if not details:
            raise ValueError("order_not_found")
        order = details["order"]
        if order["status"] == ORDER_STATUS_PENDING_SHIPMENT:
            return details
        if order["status"] != ORDER_STATUS_PENDING_FULFILLMENT_REVIEW:
            raise ValueError(f"order_cannot_confirm_fulfillment_from_{order['status']}")
        requested_items = [
            {
                "item_id": item["item_id"],
                "warehouse_id": warehouse_id or item["warehouse_id"] or order["selected_warehouse_id"],
                "location_code": item.get("location_code") or "",
                "quantity": int(item["quantity"]),
            }
            for item in details["items"]
            if item["status"] == ORDER_STATUS_PENDING_FULFILLMENT_REVIEW
        ]
        if not requested_items:
            raise ValueError("order_has_no_pending_fulfillment_items")
        selected_warehouse_id = str(warehouse_id or requested_items[0]["warehouse_id"] or order["selected_warehouse_id"])
        selected_warehouse_name = self._warehouse_name(selected_warehouse_id)
        delivery_provider = self._delivery_provider(delivery_provider_id or str(order.get("delivery_provider_id") or "sf"))
        selected_tracking_no = tracking_no.strip() or str(order.get("tracking_no") or "")
        if not selected_tracking_no:
            selected_tracking_no = f"{delivery_provider['tracking_prefix']}{order_id.replace('-', '')}"
        with self.engine.begin() as connection:
            allocated_items = self._allocate_order_items(
                connection,
                {**order, "selected_warehouse_id": selected_warehouse_id},
                requested_items,
                updated_at,
            )
            connection.execute(order_items.delete().where(order_items.c.order_id == order_id))
            for item in allocated_items:
                connection.execute(
                    inventory_location_balances.update()
                    .where(inventory_location_balances.c.item_id == item["item_id"])
                    .where(inventory_location_balances.c.warehouse_id == item["warehouse_id"])
                    .where(inventory_location_balances.c.location_code == item["location_code"])
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
                        movement_type="order_fulfillment_confirmed",
                        created_by=updated_by,
                        created_at=updated_at,
                        direction=-1,
                    ),
                )
            connection.execute(
                orders.update()
                .where(orders.c.order_id == order_id)
                .values(
                    status=ORDER_STATUS_PENDING_SHIPMENT,
                    delivery_provider_id=delivery_provider["provider_id"],
                    delivery_provider_name=delivery_provider["name"],
                    courier_phone=courier_phone.strip() or str(order.get("courier_phone") or ""),
                    tracking_no=selected_tracking_no,
                    selected_warehouse_id=selected_warehouse_id,
                    selected_warehouse_name=selected_warehouse_name,
                    updated_at=updated_at,
                )
            )
        return self.get_order(order_id) or details

    @staticmethod
    def _pending_order_item_values(
        order: dict[str, Any],
        item_requests: list[dict[str, Any]],
        updated_at: str,
    ) -> list[dict[str, Any]]:
        return [
            {
                "order_id": order["order_id"],
                "customer_id": order["customer_id"],
                "status": order.get("status") or ORDER_STATUS_UNPAID,
                "item_id": request["item_id"],
                "warehouse_id": request.get("warehouse_id") or order.get("selected_warehouse_id") or "",
                "location_code": request.get("location_code") or "",
                "quantity": int(request["quantity"]),
                "created_at": updated_at,
                "updated_at": updated_at,
            }
            for request in item_requests
        ]

    def _warehouse_name(self, warehouse_id: str) -> str:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(warehouses.c.warehouse_name).where(warehouses.c.warehouse_id == warehouse_id)
                )
                .mappings()
                .one_or_none()
            )
        return str(row["warehouse_name"]) if row else warehouse_id

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
                    inventory_location_balances.c.location_code,
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
                        "status": ORDER_STATUS_PENDING_SHIPMENT,
                        "item_id": row["item_id"],
                        "warehouse_id": row["warehouse_id"],
                        "location_code": row["location_code"],
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

    def list_order_fulfillment_candidates(self, order_id: str) -> dict[str, Any]:
        details = self.get_order(order_id)
        if not details:
            raise ValueError("order_not_found")
        order = details["order"]
        requested_quantities: dict[str, int] = {}
        for item in details["items"]:
            requested_quantities[str(item["item_id"])] = requested_quantities.get(str(item["item_id"]), 0) + int(item["quantity"])
        with self.engine.connect() as connection:
            warehouse_rows = (
                connection.execute(
                    select(warehouses)
                    .where(warehouses.c.status == "active")
                    .order_by(warehouses.c.warehouse_id)
                )
                .mappings()
                .all()
            )
            candidates: list[dict[str, Any]] = []
            for warehouse in warehouse_rows:
                shortages: list[dict[str, Any]] = []
                total_available = 0
                for item_id, quantity in requested_quantities.items():
                    available = int(
                        connection.execute(
                            select(func.coalesce(func.sum(inventory_location_balances.c.quantity_on_hand), 0))
                            .where(inventory_location_balances.c.item_id == item_id)
                            .where(inventory_location_balances.c.warehouse_id == warehouse["warehouse_id"])
                            .where(inventory_location_balances.c.storage_status == "available")
                        ).scalar_one()
                    )
                    total_available += available
                    if available < quantity:
                        shortages.append(
                            {
                                "error": "insufficient_available_stock",
                                "item_id": item_id,
                                "warehouse_id": warehouse["warehouse_id"],
                                "requested_quantity": quantity,
                                "available_quantity": available,
                                "shortage_quantity": quantity - available,
                            }
                        )
                candidates.append(
                    {
                        "warehouse_id": warehouse["warehouse_id"],
                        "warehouse_name": warehouse["warehouse_name"],
                        "city": warehouse["city"],
                        "can_fulfill": not shortages,
                        "total_available": total_available,
                        "shortage": shortages[0] if shortages else {},
                        "recommended": warehouse["warehouse_id"] == order.get("selected_warehouse_id"),
                    }
                )
        candidates.sort(key=lambda item: (not item["recommended"], not item["can_fulfill"], item["warehouse_id"]))
        recommended = next((item for item in candidates if item["recommended"]), candidates[0] if candidates else {})
        return {
            "order_id": order_id,
            "recommended_warehouse_id": recommended.get("warehouse_id", ""),
            "candidates": candidates,
        }

    def update_order_status(self, order_id: str, *, status: str, updated_by: str, updated_at: str) -> dict[str, Any]:
        timestamp_columns = {
            ORDER_STATUS_SHIPPED: "shipped_at",
            ORDER_STATUS_ARRIVED: "arrived_at",
            ORDER_STATUS_REFUNDED: "cancelled_at",
            ORDER_STATUS_RETURNED: "returned_at",
            ORDER_STATUS_CANCELED: "cancelled_at",
        }
        details = self.get_order(order_id)
        if not details:
            raise ValueError("order_not_found")
        current_status = details["order"]["status"]
        if status in {ORDER_STATUS_REFUNDED, ORDER_STATUS_RETURNED, ORDER_STATUS_CANCELED} and current_status in {
            ORDER_STATUS_PENDING_SHIPMENT,
            ORDER_STATUS_SHIPPED,
            ORDER_STATUS_ARRIVED,
        }:
            self._restore_order_items(
                order_id,
                status=status,
                movement_type={
                    ORDER_STATUS_REFUNDED: "order_refunded",
                    ORDER_STATUS_RETURNED: "order_returned",
                    ORDER_STATUS_CANCELED: "order_timeout_released",
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
        return self.update_order_status(order_id, status=ORDER_STATUS_REFUNDED, updated_by=updated_by, updated_at=updated_at)

    def return_order(self, order_id: str, *, updated_by: str, updated_at: str) -> dict[str, Any]:
        return self.update_order_status(order_id, status=ORDER_STATUS_RETURNED, updated_by=updated_by, updated_at=updated_at)

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
            if item["status"] in {
                ORDER_STATUS_UNPAID,
                ORDER_STATUS_PENDING_SHIPMENT,
                ORDER_STATUS_SHIPPED,
                ORDER_STATUS_ARRIVED,
            }
        ]
        with self.engine.begin() as connection:
            for item in restorable:
                connection.execute(
                    inventory_location_balances.update()
                    .where(inventory_location_balances.c.item_id == item["item_id"])
                    .where(inventory_location_balances.c.warehouse_id == item["warehouse_id"])
                    .where(inventory_location_balances.c.location_code == item["location_code"])
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
                    .where(orders.c.status == ORDER_STATUS_UNPAID)
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
            self.update_order_status(order_id, status=ORDER_STATUS_CANCELED, updated_by=processed_by, updated_at=now)
            with self.engine.begin() as connection:
                connection.execute(
                    orders.update()
                    .where(orders.c.order_id == order_id)
                    .values(release_reason="unpaid_timeout")
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
