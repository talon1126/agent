# 业务数据库表

采购相关表由 `mock-api` 的仓储仓库层统一管理。有 `DATABASE_URL` 时走 Postgres；没有数据库时使用内存/fixture fallback。

- `replenishment_requests`
  - 补货申请表，由 Warehouse 创建、Procurement 审核。
  - 关键字段：`request_id`、`source`、`status`、`warehouse_id`、`warehouse_name`、`location_code`、`item_id`、`item_name`、`category_id`、`category_name`、`current_quantity`、`reorder_threshold`、`suggested_quantity`、`reason`、`created_by`、`created_at`、`updated_at`。
  - 主要状态：`未审批`、`已审批`。
  - 驳回不会产生第三种状态；拒绝原因写入 `reason`。

- `procurement_suppliers`
  - mock 采购供应商表，按 `item_id` 匹配默认供应商。
  - 关键字段：`supplier_id`、`supplier_name`、`item_id`、`unit_price`、`currency`、`lead_time_days`、`reliability_score`。
  - 当前策略：v1 每个商品只选一个默认供应商，不做多供应商比价。

- `purchase_orders`
  - 采购单表，由批准补货申请生成。
  - 关键字段：`purchase_order_id`、`request_id`、`supplier_id`、`supplier_name`、`item_id`、`warehouse_id`、`warehouse_name`、`location_code`、`quantity`、`unit_price`、`currency`、`estimated_total_price`、`lead_time_days`、`estimated_arrival_date`、`payment_status`、`warehouse_sync_status`、`created_by`、`created_at`、`updated_at`。
  - 支付状态：`unpaid`、`paid`。
  - 仓库同步状态：`pending_arrival`、`arrived_unsynced`、`synced`。
