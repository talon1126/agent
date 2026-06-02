# mock-api

`mock-api` 是仓储事实数据和采购交接数据的后端模拟系统。其他 workflow 如果需要仓储数据，应优先调用这些接口，不要直接访问 Postgres。

## 库存与履约接口

- `GET /warehouse/inventory/{item_id}`
  - 用途：查询某个商品在多个仓库、库位、批次上的库存汇总和明细。
  - 返回重点：`total_quantity_on_hand`、`total_quantity_reserved`、`total_quantity_available`、`risk_level`、`recommendation`、`batches`。

- `POST /warehouse/inventory/search`
  - 用途：按条件搜索批次库存。
  - 常用过滤：`item_id`、`warehouse_id`、`location_code`、`category` / `category_id`、`batch_no`、`expiry_risk`、`risk_level`、`limit`。
  - 适合运营 agent 做库存风险汇总，或采购 agent 查补货上下文。

- `POST /warehouse/inventory/table-rows`
  - 用途：返回适合写入飞书库存表的行数据。
  - 返回结构：`items[].batch_key`、`items[].item_id`、`items[].batch_no`、`items[].fields`。
  - 这是 `feishu-adapter` 同步飞书库存表的主要读模型接口。

- `GET /warehouse/inventory/table-schema`
  - 用途：返回仓储库存飞书表字段定义。
  - 返回 `schema_id=warehouse_batch_inventory` 和字段列表。

- `POST /warehouse/exceptions/search`
  - 用途：查询某商品的高风险批次、临期、过期、质检冻结等仓储异常。
  - 入参重点：`item_id`，可选 `expiry_risk`。

- `POST /warehouse/fulfillment/check`
  - 用途：判断某商品是否可以发货。
  - 返回重点：`can_ship`、`blockers`、`available`、`reserved`、`batches`、`next_action`。
  - 物流 agent 或客服 agent 只需要判断能否出库时，可以用这个接口。

## 库位库存余额与订单库存接口

- `GET /warehouse/stock/balances`
  - 用途：按 `item_id + warehouse_id + location_code` 聚合返回仓库库位级库存余额。
  - 返回重点：`quantity_on_hand`、`quantity_available`、`batch_count`、`earliest_expiry_date`。
  - 物流 agent 需要判断某件商品在某仓库/库位还有多少可用库存时，应优先使用这个接口。

- `GET /warehouse/stock/balances/table-schema`
  - 用途：返回飞书库存余额表字段定义。
  - 字段约定：一行一个 `item_id + warehouse_id + location_code`，不包含 `batch_no`；状态字段使用 `single_select`，时间字段使用 `date`。

- `POST /warehouse/stock/balances/table-rows`
  - 用途：分页返回适合写入飞书库存余额表的行数据。
  - 常用入参：`item_id`、`warehouse_id`、`location_code`、`cursor`、`limit`。
  - 分页约束：`limit` 最大 500；返回 `next_cursor` 供下一页继续同步。
  - 余额规则：库存余额行不会因为数量为 0 被删除，余额为 0 时仍返回该行并标记 `Balance Status=zero_stock`。

- `POST /warehouse/orders`
  - 用途：创建订单主单，支持多行商品。
  - 入参重点：`order_id`、`customer_id`、`shipping_address`、`delivery_provider_id`、`courier_phone`、`tracking_no`、`items[].item_id`、`items[].quantity`。
  - 地址格式：`xx省xx市`；`shipping_address` 不能为空，空地址返回 `400 shipping_address_required`；Warehouse 按整单同仓策略优先选择距离用户地址最近且能满足整单库存的仓库。
  - 创建后状态为 `未付款`，立即写入 `order_items` 并扣减 `inventory_location_balances`；物流供应商字段写入订单主表，供 Delivery Agent 查询。

- `POST /warehouse/orders/{order_id}/pay`
  - 用途：订单付款后只更新订单和明细状态，不再次扣减库存。
  - 状态流转：`未付款` -> `待发货`。

- `POST /warehouse/orders/{order_id}/ship`
  - 用途：订单发货，只更新订单状态为 `已发货`，不再次扣减库存。

- `POST /warehouse/orders/{order_id}/arrive`
  - 用途：订单到货，只更新订单状态为 `已到货`，不再次扣减库存。

- `POST /warehouse/orders/{order_id}/cancel`
  - 用途：订单取消或发货前退款，状态更新为 `已退款`，按 `order_items` 原批次加回 `inventory_location_balances`。

- `POST /warehouse/orders/{order_id}/return`
  - 用途：已到货后退货，状态更新为 `已退货`，按 `order_items` 原批次加回 `inventory_location_balances`。

- `POST /warehouse/orders/release-expired`
  - 用途：释放未付款超时订单占用的库存，由 n8n 定时 workflow 调用。
  - 处理规则：扫描 `status=未付款`、`expires_at < now`、`released_at` 为空的订单，按 `order_items` 加回库存，订单状态更新为 `已取消`，写入 `release_reason=unpaid_timeout`。
  - 定时 workflow 文件：`n8n/workflows/warehouse-order-timeout-release.json`，每 5 分钟调用一次。

## 秒杀接口

- `GET /flash-sales/{flash_sale_id}`
  - 用途：读取秒杀活动详情和 Redis 中的剩余营销库存。
  - 返回重点：`item_id`、`sale_price`、`stock_limit`、`stock_remaining`、`status`、`starts_at`、`ends_at`。
  - 约束：活动必须已写入 `flash_sales`，并且执行过激活初始化 Redis 库存；否则返回 `flash_sale_not_initialized`。

- `POST /flash-sales/{flash_sale_id}/activate`
  - 用途：把活动状态更新为 `active`，并把 `flash_sales.stock_limit` 初始化到 Redis。
  - Redis 键：`flash_sale:{id}:stock` 保存剩余配额，`flash_sale:{id}:users` 保存已抢购用户。
  - 注意：重复激活会重置 Redis 剩余配额和已抢购用户集合，只应用于活动开始前或测试环境。

- `POST /flash-sales/{flash_sale_id}/purchase`
  - 用途：用户参与秒杀，成功后立即复用 `/warehouse/orders` 创建 `未付款` 订单。
  - 入参重点：`user_id`、`shipping_address`，可选 `delivery_provider_id`。
  - 处理规则：先用 Redis Lua 原子扣减营销库存和写入用户集合，再写 `flash_sale_claims`，最后创建仓储订单并扣减真实库存。
  - 补偿规则：如果 Redis 扣减成功但仓储订单创建失败，会回补 Redis 库存、移除用户集合，并把抢购结果标记为 `failed`。
  - 重复规则：同一 `flash_sale_id + user_id` 只允许成功一次，重复请求返回 `already_claimed` 或已存在订单结果。

- `POST /warehouse/purchase-orders/sync-arrivals`
  - 用途：Warehouse 扫描采购单中 `payment_status=paid` 且 `warehouse_sync_status=arrived_unsynced` 的记录，同步到库存事实。
  - 写入规则：`batch_no` 使用 `BATCH-YYYYMMDD`，日期来自采购单 `arrived_at`；`expiry_date` 按商品主数据 `items.shelf_life_days` 计算；`storage_status=available`；`reorder_threshold` 在合理范围内生成。
  - 库位规则：同一 `item_id + warehouse_id` 只保留一个 `location_code`；已有余额时复用已有库位，没有则用采购单库位，再没有则用仓库第一个库位。
  - 影响：写入 `inventory_batches`，更新或新增 `inventory_location_balances`，成功后把采购单 `warehouse_sync_status` 标记为 `synced`。

## 补货和采购交接接口

- `POST /procurement/replenishment-requests`
  - 用途：仓储发现低库存后创建补货申请。
  - 默认状态：`未审批`。
  - 采购 agent 后续负责审批、驳回原因记录和创建采购单。

- `GET /procurement/replenishment-requests?status=未审批`
  - 用途：采购 agent 查询待审核补货申请。

- `POST /procurement/purchase-orders/confirm-arrival-batch`
  - 用途：采购人员批量确认采购单到仓。
  - 影响：只把 `purchase_orders.warehouse_sync_status` 更新为 `arrived_unsynced`，不直接创建库存批次或 Warehouse sync job。
  - 返回重点：`confirmed_items`、`errors`、`next_action`。

## 库存同步任务接口

- `GET /warehouse/inventory-sync-jobs?status=pending`
  - 用途：仓储 agent 拉取旧链路遗留的待处理库存同步任务。
  - 任务来源：历史采购到货确认产生的 `warehouse_inventory_sync_requested`。

- `POST /warehouse/inventory-sync-jobs/{job_id}/complete`
  - 用途：飞书库存表同步成功后标记任务完成。
  - 入参重点：`processed_by`、`result`。

- `POST /warehouse/inventory-sync-jobs/{job_id}/fail`
  - 用途：飞书库存表同步失败后标记任务失败。
  - 入参重点：`processed_by`、`error`。
