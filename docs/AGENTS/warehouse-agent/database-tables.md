# 业务数据库表

仓储相关表由 `mock-api` 的仓储仓库层管理。有 `DATABASE_URL` 时走 Postgres；没有数据库时使用内存/fixture fallback。

- `warehouses`
  - 仓库主数据。
  - 关键字段：`warehouse_id`、`warehouse_name`、`city`、`region`、`status`。

- `storage_locations`
  - 仓库库位数据。
  - 关键字段：`location_id`、`warehouse_id`、`location_code`、`zone`、`temperature_zone`、`capacity_units`。

- `categories`
  - 商品分类。
  - 关键字段：`category_id`、`category_name`、`storage_requirement`。

- `items`
  - 商品主数据。
  - 关键字段：`item_id`、`category_id`、`item_name`、`brand`、`spec`、`price`、`unit`、`barcode`、`shelf_life_days`、`search_text`。
  - `price` 是前台商品展示和购物车写入的可信价格来源；`search_text` 用于 `pg_search` BM25 检索。

- `inventory_batches`
  - 批次入库事实表，用于进货记录和商品批次溯源，不再被订单扣减。
  - 关键字段：`warehouse_id`、`location_code`、`item_id`、`batch_no`、`production_date`、`expiry_date`、`quantity_on_hand`、`quantity_reserved`、`reorder_threshold`、`storage_status`。

- `inventory_location_balances`
  - 批次级库位库存余额表，订单创建扣减，取消、退款、退货或超时释放加回。
  - 关键字段：`id`、`warehouse_id`、`location_code`、`item_id`、`batch_no`、`quantity_on_hand`、`reorder_threshold`、`storage_status`。
  - 初始化来源：按 `inventory_batches.quantity_on_hand` 建立余额，忽略旧模型的 `quantity_reserved`。
  - 当前约束：同一 `item_id + warehouse_id` 后续采购入库复用同一个 `location_code`，避免同仓同商品分散到多个库位。

- `flash_sales`
  - 秒杀活动表，一条活动绑定一个商品，`stock_limit` 是独立营销秒杀库存配额，不等同于真实仓储库存。
  - 关键字段：`id`、`item_id`、`sale_price`、`stock_limit`、`status`、`starts_at`、`ends_at`、`created_at`、`updated_at`。
  - 主要状态：`draft`、`active`、`ended`、`disabled`。
  - 运行规则：活动激活后会把 `stock_limit` 初始化到 Redis；抢购成功后仍会复用仓储订单能力扣减 `inventory_location_balances`。

- `flash_sale_claims`
  - 秒杀抢购结果表，记录用户参与结果和关联订单，数据库唯一约束保证同一活动一人一单。
  - 关键字段：`id`、`flash_sale_id`、`user_id`、`item_id`、`order_id`、`status`、`created_at`、`updated_at`。
  - 主要状态：`pending`、`ordered`、`failed`、`cancelled`。
  - 补偿规则：Redis 抢购成功但订单创建失败时，结果会标记为 `failed`，同时回补 Redis 营销库存和用户集合。

- `orders`
  - 订单主表，记录下单、付款、发货、到货、取消和退货状态。
  - 关键字段：`id`、`order_id`、`customer_id`、`status`、`delivery_provider_id`、`delivery_provider_name`、`courier_phone`、`tracking_no`、`shipping_address`、`shipping_province`、`shipping_city`、`selected_warehouse_id`、`selected_warehouse_name`、`paid_at`、`shipped_at`、`arrived_at`、`cancelled_at`、`returned_at`、`expires_at`、`released_at`、`release_reason`。
  - 已删除字段：`requested_items_json`；订单商品明细和库存扣减事实以 `order_items` 为准。
  - 主要状态：`未付款`、`待发货`、`已发货`、`已到货`、`已退款`、`已退货`、`已取消`。
  - 业务边界：Warehouse 负责库存扣减和订单状态流转；Delivery 只读取订单上的物流供应商、快递员电话和物流单号，不负责库存分配。

- `order_items`
  - 订单明细表，创建订单时即记录扣减命中的批次库存，用于取消、退款、退货或超时释放时按原批次加回。
  - 关键字段：`id`、`order_id`、`customer_id`、`status`、`item_id`、`warehouse_id`、`location_code`、`batch_no`、`quantity`。

- `inventory_movements`
  - 库存流水表，记录订单创建扣减和退款、退货、未付款超时释放的库存变化。
  - 关键字段：`movement_id`、`order_id`、`movement_type`、`item_id`、`warehouse_id`、`location_code`、`quantity_delta`、`created_by`、`created_at`。
  - 不记录 `batch_no`、`before_quantity` 或 `after_quantity`。

- `delivery_providers`
  - 物流供应商表，记录可分配到订单的承运商主数据。
  - 关键字段：`provider_id`、`provider_name`、`service_hotline`、`tracking_prefix`、`status`。
  - 当前内置供应商：顺丰（`sf`）、京东（`jd`）、圆通（`yto`）。

- `replenishment_requests`
  - 仓储发起、采购处理的补货申请。
  - 关键字段：`request_id`、`status`、`warehouse_id`、`location_code`、`item_id`、`current_quantity`、`reorder_threshold`、`suggested_quantity`、`reason`、`created_by`。
  - 主要状态：`未审批`、`已审批`。
  - 驳回补货申请时仍保持 `未审批`，拒绝原因写入 `reason`。

- `purchase_orders`
  - 采购单表，由采购 agent 负责，但到货后会影响仓储库存同步。
  - 关键字段：`purchase_order_id`、`request_id`、`supplier_id`、`item_id`、`warehouse_id`、`location_code`、`quantity`、`payment_status`、`warehouse_sync_status`、`estimated_arrival_date`、`arrived_at`。
  - 到仓后 `warehouse_sync_status` 会进入 `arrived_unsynced` 并写入 `arrived_at`；Warehouse 只同步已支付采购单，成功后改为 `synced`。

- `warehouse_inventory_sync_jobs`
  - 旧链路仓储库存同步任务表。
  - 关键字段：`job_id`、`event`、`po_draft_id`、`request_id`、`item_id`、`warehouse_id`、`location_code`、`batch_no`、`quantity`、`status`、`processed_by`、`processed_at`、`result_json`、`error`。
  - 历史任务会保留 `pending`、`completed`、`failed` 状态；新采购到仓链路不再由采购直接创建该任务。
