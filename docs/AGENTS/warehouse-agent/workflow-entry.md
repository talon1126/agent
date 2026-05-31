# workflow 入口

- Feishu 仓储机器人消息进入 `feishu-adapter`，再转发到 n8n `Warehouse Workflow`。
- n8n webhook：`POST /webhook/warehouse-inbound`。
- workflow 文件：`n8n/workflows/warehouse-workflow.json`。
- 定时刷新 workflow 文件：`n8n/workflows/warehouse-inventory-balances-refresh.json`，每 10 分钟调用一次飞书库存余额表同步。
- 当前主要工具：
  - `warehouse_inventory_tool`：查询商品批次库存。
  - `warehouse_exception_tool`：查询高风险、临期、过期、质检冻结等仓储异常。
  - `warehouse_fulfillment_tool`：判断商品是否可发货，返回履约阻塞原因。
  - `warehouse_replenishment_request_tool`：低库存时创建补货申请，交给采购 agent。
  - `warehouse_inventory_table_provision_tool`：显式创建或初始化飞书库存表。
  - `warehouse_inventory_table_sync_tool`：按商品、仓库、库位、分类、批次或风险范围同步库存快照到飞书。
  - `warehouse_table_schema_tool`：创建飞书库存视图前读取真实字段和视图。
  - `warehouse_view_create_tool`：按受控 JSON 创建或复用飞书库存视图。
  - `warehouse_purchase_order_arrival_sync_tool`：扫描已支付且到仓未同步的采购单，写入库存批次表和库存余额表。
  - `warehouse_inventory_sync_jobs_tool`：处理旧链路遗留的待同步库存任务；新采购到仓链路以 `purchase_orders.warehouse_sync_status=arrived_unsynced` 为交接点。
