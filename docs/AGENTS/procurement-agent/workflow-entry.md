# workflow 入口

- Feishu 采购机器人消息进入 `feishu-adapter`，再转发到 n8n `Procurement Workflow`。
- n8n webhook：`POST /webhook/procurement-inbound`。
- workflow 文件：`n8n/workflows/procurement-workflow.json`。
- 当前主要工具：
  - `procurement_sync_replenishment_requests_tool`：把数据库补货申请同步到飞书采购补货请求表。
  - `procurement_sync_purchase_orders_tool`：把采购单同步到飞书采购单表。
  - `procurement_approve_replenishment_batch_tool`：批量批准全部 `未审批` 补货申请，生成或复用采购单，并刷新两张采购飞书表。
  - `procurement_confirm_purchase_order_arrival_tool`：确认一个或多个 `PO-*` 到仓，把采购单标记为 `arrived_unsynced`，并刷新采购单表。
  - `procurement_replenishment_request_tool`：查询 Warehouse 创建的 `未审批` 补货申请。
  - `procurement_approve_replenishment_tool`：批准单个 `REQ-*` 补货申请，生成或复用采购单。
  - `procurement_reject_replenishment_tool`：驳回单个 `REQ-*` 补货申请，并记录拒绝原因。
  - `procurement_mock_tool`：按 `item_id` 生成基础 mock 采购建议。
