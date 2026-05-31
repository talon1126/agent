# 其他 workflow 可能会用到的契约

- 采购 agent 如果确认采购单到货，必须通过 `POST /procurement/purchase-orders/confirm-arrival-batch` 把采购单标记为 `arrived_unsynced`，不要直接写 `inventory_batches`、`inventory_location_balances` 或飞书库存表。
- 物流 agent 如果只需要判断是否能出库，可以调用 `POST /warehouse/fulfillment/check`，不要自行推断批次库存；如果查询配送状态，应调用 `POST /delivery/status/lookup` 读取订单物流字段。
- 创建订单时即扣减 `inventory_location_balances` 并写入 `order_items`；付款只进入 `待发货`；发货和到货只更新为 `已发货`、`已到货`；取消、退款、退货或超时释放按 `order_items` 原扣减明细加回库存，并进入 `已退款`、`已退货` 或 `已取消`。
- 客服 agent 如果需要解释“为什么不能发货”，可以引用 `warehouse/fulfillment/check` 的 `blockers` 和 `next_action`，但不要承诺退款或补偿。
- 运营 agent 如果要做库存风险汇总，可以调用 `POST /warehouse/inventory/search`，按 `risk_level`、`expiry_risk`、`warehouse_id`、`category` 聚合。
- 任何 agent 需要飞书库存表、视图或同步状态，都应调用 `feishu-adapter` 的 `/warehouse/inventory-table/*` 接口，不要直接访问飞书开放平台。
- 任何 agent 需要飞书库存余额表，应调用 `feishu-adapter` 的 `/warehouse/inventory-balances-table/*` 接口；余额表按 `item_id + warehouse_id + location_code` 展示，不按批次展开。
- 飞书库存表是读模型，不是库存源数据；库存源数据始终是 `mock-api` 暴露的仓储事实接口和背后的业务表。
- Warehouse 扫描未同步采购单时，应使用 `BATCH-YYYYMMDD` 作为入库批次号，同一天到达的采购入库共享同一批次号，并在成功写入库存余额后把 `warehouse_sync_status` 更新为 `synced`。
