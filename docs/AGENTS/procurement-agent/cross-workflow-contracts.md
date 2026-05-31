# 其他 workflow 可能会用到的契约

- Warehouse Agent 创建补货申请时调用 `POST /procurement/replenishment-requests`，不要直接创建采购单。
- Procurement Agent 批准或驳回补货申请后，状态只在 `replenishment_requests` 上流转，不直接改库存。
- Procurement Agent 确认 `PO-*` 到仓时必须调用 `POST /procurement/purchase-orders/confirm-arrival-batch`，只更新采购单仓库同步状态，不直接写 `inventory_batches` 或飞书库存表。
- Warehouse Agent 如果要同步采购到仓库存，应读取采购单中 `warehouse_sync_status=arrived_unsynced` 的记录，并使用采购单上的 `item_id`、`warehouse_id`、`location_code`、`quantity` 作为同步依据。
- Operations Agent 如果需要采购计划或采购单状态，应读取 `GET /procurement/replenishment-requests` 或 `GET /procurement/purchase-orders`，不要从飞书采购表反推源数据。
- Customer Support Agent 如果需要解释缺货补货进度，只能引用采购申请或采购单状态，不要承诺具体到货时间之外的正式履约承诺。
- 飞书采购表是读模型，不是采购源数据；采购源数据始终是 `mock-api` 暴露的采购接口和背后的业务表。
