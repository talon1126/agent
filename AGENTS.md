# 仓储agent

本文档用于和其他 workflow / agent 协作时快速说明仓储 agent 的业务边界、可复用接口、数据表和交接契约。后续维护时，仓储 agent 只维护本章节；其他 workflow 可以按同级大标题追加自己的章节。

## 业务边界

仓储 agent 负责库存、仓库、库位、批次、临期风险、仓储异常、履约风险、补货申请发起，以及采购到货后的仓库库存同步处理。

仓储 agent 不负责供应商选择、采购单审批、退款/赔付、物流承运商决策。需要采购时只生成补货申请；采购单到仓后，仓储后续消费 `purchase_orders.warehouse_sync_status=arrived_unsynced` 的采购单并同步库存事实。

## workflow 入口

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

## ai-service

当前仓储业务没有在 `ai-service` 暴露专用 HTTP 接口。仓储的业务判断主要走 n8n agent + `mock-api` 工具接口，飞书表格能力走 `feishu-adapter`。

`ai-service` 现有能力主要是客服/售后通用能力，例如：

- `POST /message/handle`：消息处理入口。
- `POST /decide`：售后事件确定性决策。
- `POST /after-sales/fast-path`：售后快路径。

如果其他 agent 需要把仓储逻辑下沉到 `ai-service`，需要先明确是否要新增“模型可测试的仓储决策层”，不要绕过 `mock-api` 直接读仓储数据库。

## mock-api

`mock-api` 是仓储事实数据和采购交接数据的后端模拟系统。其他 workflow 如果需要仓储数据，应优先调用这些接口，不要直接访问 Postgres。

### 库存与履约接口

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

### 库位库存余额与订单库存接口

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
  - 地址格式：`xx省xx市`；Warehouse 按整单同仓策略优先选择距离用户地址最近且能满足整单库存的仓库。
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

- `POST /warehouse/purchase-orders/sync-arrivals`
  - 用途：Warehouse 扫描采购单中 `payment_status=paid` 且 `warehouse_sync_status=arrived_unsynced` 的记录，同步到库存事实。
  - 写入规则：`batch_no` 使用 `BATCH-YYYYMMDD`，日期来自采购单 `arrived_at`；`expiry_date` 按商品主数据 `items.shelf_life_days` 计算；`storage_status=available`；`reorder_threshold` 在合理范围内生成。
  - 库位规则：同一 `item_id + warehouse_id` 只保留一个 `location_code`；已有余额时复用已有库位，没有则用采购单库位，再没有则用仓库第一个库位。
  - 影响：写入 `inventory_batches`，更新或新增 `inventory_location_balances`，成功后把采购单 `warehouse_sync_status` 标记为 `synced`。

### 补货和采购交接接口

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

### 库存同步任务接口

- `GET /warehouse/inventory-sync-jobs?status=pending`
  - 用途：仓储 agent 拉取旧链路遗留的待处理库存同步任务。
  - 任务来源：历史采购到货确认产生的 `warehouse_inventory_sync_requested`。

- `POST /warehouse/inventory-sync-jobs/{job_id}/complete`
  - 用途：飞书库存表同步成功后标记任务完成。
  - 入参重点：`processed_by`、`result`。

- `POST /warehouse/inventory-sync-jobs/{job_id}/fail`
  - 用途：飞书库存表同步失败后标记任务失败。
  - 入参重点：`processed_by`、`error`。

## feishu-adapter

`feishu-adapter` 负责飞书协议、多维表格和仓储意图快路径。其他 workflow 不应直接调用飞书开放平台，应该通过这里的受控接口。

### 仓储意图路由

- `POST /warehouse/intents/route`
  - 用途：把自然语言仓储请求路由到库存查询、表格同步、视图创建或 agent fallback。
  - 常见输出：`executor`、`slots`、`clarification_required`、`clarification_question`。

### 飞书库存表

- `POST /warehouse/inventory-table/provision`
  - 用途：创建或复用固定 schema 的飞书库存表。
  - 默认表名：`Warehouse Inventory Snapshot`。
  - 只在用户明确要求创建、初始化、配置飞书库存表时调用。

- `POST /warehouse/inventory-table/sync`
  - 用途：同步单个商品/批次库存快照。
  - 常用入参：`item_id`、`warehouse_id`、`location_code`、`batch_no`。
  - 表不存在时会通过受控 provisioning 自动创建或复用目标表。

- `POST /warehouse/inventory-table/sync/filter`
  - 用途：按过滤条件批量同步库存快照。
  - 常用入参：`item_id`、`warehouse_id`、`location_code`、`category` / `category_id`、`batch_no`、`expiry_risk`、`risk_level`、`limit`。
  - 适合人工要求“同步香港仓高风险库存”这类范围同步。

- `POST /warehouse/inventory-table/sync/jobs`
  - 用途：批量处理旧链路仓储库存同步任务。
  - 入参重点：`jobs`、`limit_per_job`、`table_name`。
  - 当前实现按 job 的 `item_id + warehouse_id + location_code + batch_no` 精确取行，再批量 upsert 到飞书表。
  - 当前采购到仓主路径已转为 Warehouse 读取 `purchase_orders` 并先写库存事实；飞书库存表仍通过库存读模型同步展示。

- `GET /warehouse/inventory-table/schema`
  - 用途：读取真实飞书库存表字段、字段类型、选项颜色和已有视图。
  - 创建视图前必须先调用。

- `GET /warehouse/inventory-table/view-templates`
  - 用途：列出仓储内置视图模板，例如高风险、临期、低库存、补货候选等。

- `POST /warehouse/inventory-table/views/create`
  - 用途：按受控 JSON 创建或复用飞书库存视图。
  - 入参重点：`view_name`、`visible_fields`、`filters`、`sorts`。
  - 后端会校验字段是否真实存在，避免 agent 编造飞书字段。

- `POST /warehouse/inventory-table/views/from-template`
  - 用途：把自然语言视图请求匹配到模板并创建视图。
  - 适合“帮我建一个香港仓高风险库存视图”这类请求。

### 飞书库存余额表

- `POST /warehouse/inventory-balances-table/provision`
  - 用途：创建或复用固定 schema 的飞书库存余额表。
  - 默认表名：`Warehouse Inventory Balances`。
  - 表结构：一行一个 `item_id + warehouse_id + location_code` 聚合余额；所有状态字段为单选，所有时间字段为日期。
  - 默认视图：库存余额总览、低库存余额、可售库存。

- `POST /warehouse/inventory-balances-table/sync`
  - 用途：把 `inventory_location_balances` 的余额读模型同步到飞书库存余额表。
  - 数据源：`mock-api /warehouse/stock/balances/table-rows`。
  - 常用入参：`item_id`、`warehouse_id`、`location_code`、`limit`、`max_pages`。
  - 同步策略：每页最多 500 条，按 `Balance Key=item_id:warehouse_id:location_code` upsert；表不存在时自动创建，表存在时复用并补齐字段和视图。
  - 定时刷新：`warehouse-inventory-balances-refresh.json` 每 10 分钟调用该接口，默认 `limit=500`、`max_pages=500`。
  - 注意：同步不会删除飞书中的余额行；源数据余额为 0 时应同步为 0 行展示。

## 业务数据库表

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
  - 关键字段：`item_id`、`category_id`、`item_name`、`brand`、`spec`、`unit`、`barcode`、`shelf_life_days`。

- `inventory_batches`
  - 批次入库事实表，用于进货记录和商品批次溯源，不再被订单扣减。
  - 关键字段：`warehouse_id`、`location_code`、`item_id`、`batch_no`、`production_date`、`expiry_date`、`quantity_on_hand`、`quantity_reserved`、`reorder_threshold`、`storage_status`。

- `inventory_location_balances`
  - 批次级库位库存余额表，订单创建扣减，取消、退款、退货或超时释放加回。
  - 关键字段：`id`、`warehouse_id`、`location_code`、`item_id`、`batch_no`、`quantity_on_hand`、`reorder_threshold`、`storage_status`。
  - 初始化来源：按 `inventory_batches.quantity_on_hand` 建立余额，忽略旧模型的 `quantity_reserved`。
  - 当前约束：同一 `item_id + warehouse_id` 后续采购入库复用同一个 `location_code`，避免同仓同商品分散到多个库位。

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

## 其他 workflow 可能会用到的契约

- 采购 agent 如果确认采购单到货，必须通过 `POST /procurement/purchase-orders/confirm-arrival-batch` 把采购单标记为 `arrived_unsynced`，不要直接写 `inventory_batches`、`inventory_location_balances` 或飞书库存表。
- 物流 agent 如果只需要判断是否能出库，可以调用 `POST /warehouse/fulfillment/check`，不要自行推断批次库存；如果查询配送状态，应调用 `POST /delivery/status/lookup` 读取订单物流字段。
- 创建订单时即扣减 `inventory_location_balances` 并写入 `order_items`；付款只进入 `待发货`；发货和到货只更新为 `已发货`、`已到货`；取消、退款、退货或超时释放按 `order_items` 原扣减明细加回库存，并进入 `已退款`、`已退货` 或 `已取消`。
- 客服 agent 如果需要解释“为什么不能发货”，可以引用 `warehouse/fulfillment/check` 的 `blockers` 和 `next_action`，但不要承诺退款或补偿。
- 运营 agent 如果要做库存风险汇总，可以调用 `POST /warehouse/inventory/search`，按 `risk_level`、`expiry_risk`、`warehouse_id`、`category` 聚合。
- 任何 agent 需要飞书库存表、视图或同步状态，都应调用 `feishu-adapter` 的 `/warehouse/inventory-table/*` 接口，不要直接访问飞书开放平台。
- 任何 agent 需要飞书库存余额表，应调用 `feishu-adapter` 的 `/warehouse/inventory-balances-table/*` 接口；余额表按 `item_id + warehouse_id + location_code` 展示，不按批次展开。
- 飞书库存表是读模型，不是库存源数据；库存源数据始终是 `mock-api` 暴露的仓储事实接口和背后的业务表。
- Warehouse 扫描未同步采购单时，应使用 `BATCH-YYYYMMDD` 作为入库批次号，同一天到达的采购入库共享同一批次号，并在成功写入库存余额后把 `warehouse_sync_status` 更新为 `synced`。

## 常见业务对象

- 商品 ID 示例：`item_vinda_tissue`、`item_milk_pure`、`item_cola_zero`、`item_copy_paper`。
- 仓库 ID 示例：`wh_sz_1`、`wh_hk_1`、`wh_sg_1`。
- 库位示例：`A1`、`B1`、`C1`。
- 批次号示例：`BATCH-20260529` 表示 2026-05-29 到仓生成的入库批次。
- 同步任务 ID 示例：`WSJ-POD-*`。
- 补货申请 ID 示例：`REQ-*`。
- 采购单 ID 示例：`PO-*`。

## 维护原则

- 仓储 agent 后续只维护本章节。
- 新增仓储接口时，同步更新 `mock-api`、`feishu-adapter` 或 n8n 工具名对应说明。
- 新增跨 workflow 交接时，优先写清楚“谁创建、谁消费、状态怎么变、失败怎么处理”。
- 不清楚的业务归属或技术细节，先和用户确认后再改文档或代码。

# 采购agent

本文档用于和其他 workflow / agent 协作时快速说明采购 agent 的业务边界、可复用接口、数据表和交接契约。后续维护时，采购 agent 只维护本章节；其他 workflow 的内容不要在采购章节里改。

## 业务边界

采购 agent 负责仓储补货申请的采购审核、默认供应商匹配、采购单生成、采购飞书视图同步、采购单到仓确认，以及到仓后把采购单标记为等待仓储同步。

采购 agent 不负责直接修改库存事实、不负责同步仓储库存飞书视图、不负责创建或完成 Warehouse sync job、不负责客服退款/赔付、不负责物流承运商或派送决策。采购单到仓后，采购只维护 `purchase_orders.warehouse_sync_status=arrived_unsynced`，Warehouse 后续自行检查未同步采购单并同步库存批次。

当前采购系统是 `mock-procurement`，用于内部流程验证和 demo；尚未接入真实 ERP 或正式采购下单系统。

## workflow 入口

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

## ai-service

当前采购业务没有在 `ai-service` 暴露专用 HTTP 接口。采购的确定性业务动作主要通过 n8n agent 调用 `mock-api` 和 `feishu-adapter` 完成。

如果后续要把采购决策下沉到 `ai-service`，需要先明确是否新增可测试的采购决策层，例如供应商选择策略、比价规则、采购审批策略。不要让 `ai-service` 直接读写采购 Postgres 表，采购事实仍应通过 `mock-api` 或未来采购服务暴露。

## mock-api

`mock-api` 是采购事实数据的后端模拟系统。其他 workflow 如果需要采购状态，应优先调用这些接口，不要直接访问 Postgres。

### 路由实现结构

- 采购 HTTP 路由已从 `services/mock-api/app/main.py` 拆到 `services/mock-api/app/routers/procurement/`，由 `main.py` 通过 `procurement_router` 统一注册。
- `mock.py`：基础 mock 采购建议接口。
- `requests.py`：补货申请创建、查询、批准、驳回、批量批准，以及补货申请飞书表 schema/rows。
- `purchase_orders.py`：采购单查询、到仓确认，以及采购单飞书表 schema/rows。
- `service.py`：采购确定性业务逻辑，例如默认供应商匹配、采购单生成、预计到达日期、状态流转和飞书表字段映射。
- `schemas.py`：采购请求模型；`state.py`：内存 fallback 状态。
- `/warehouse/*` 仍归仓储 router；采购 router 不直接创建库存批次、不创建 Warehouse sync job。

### 采购建议接口

- `POST /procurement/mock`
  - 用途：根据 `item_id` 和文本查询返回基础采购建议。
  - 当前定位：mock 建议，不代表真实 ERP 报价或正式采购决策。

### 补货申请接口

- `POST /procurement/replenishment-requests`
  - 用途：创建补货申请。
  - 主要调用方：Warehouse Agent 在确认低库存后调用。
  - 默认状态：`未审批`。
  - 入参重点：`source`、`warehouse_id`、`location_code`、`item_id`、`reason`、`created_by`。

- `GET /procurement/replenishment-requests?status=未审批`
  - 用途：查询补货申请，可按状态过滤。
  - 主要调用方：Procurement Agent、采购飞书表同步。

- `POST /procurement/replenishment-requests/{request_id}/approve`
  - 用途：批准单个 `REQ-*` 补货申请。
  - 影响：申请状态更新为 `已审批`，创建或复用 `PO-*` 采购单。
  - 幂等规则：重复批准同一个 `REQ-*` 必须复用已有采购单，不重复创建。
  - 采购单会继承补货申请的 `warehouse_id`、`warehouse_name`、`location_code`。
  - 异常：商品没有默认供应商时返回明确错误。

- `POST /procurement/replenishment-requests/{request_id}/reject`
  - 用途：驳回单个 `REQ-*` 补货申请。
  - 影响：申请状态保持 `未审批`，拒绝原因写入 `reason`。
  - 不会创建采购单。

- `POST /procurement/replenishment-requests/approve-batch`
  - 用途：批量批准补货申请。
  - 默认范围：全部 `未审批`。
  - 返回重点：`processed_count`、`approved_count`、`skipped_count`、`created_or_reused_orders`、`errors`。
  - 异常策略：某个商品没有默认供应商时跳过该申请并写入 `errors`，不中断整批任务。

### 采购单接口

- `GET /procurement/purchase-orders?request_id=REQ-1001`
  - 用途：查询采购单，可按 `request_id`、`purchase_order_id`、`warehouse_sync_status` 过滤。
  - 返回重点：`purchase_order_id`、`request_id`、`supplier_id`、`supplier_name`、`item_id`、`warehouse_id`、`warehouse_name`、`location_code`、`quantity`、`unit_price`、`estimated_total_price`、`lead_time_days`、`estimated_arrival_date`、`payment_status`、`warehouse_sync_status`。

- `POST /procurement/purchase-orders/confirm-arrival-batch`
  - 用途：批量确认一个或多个 `PO-*` 已到仓。
  - 入参重点：`purchase_order_ids`、`received_by`。
  - 影响：`purchase_orders.warehouse_sync_status` 更新为 `arrived_unsynced`。
  - 不会创建库存批次，不会创建 Warehouse sync job，不会直接同步仓储库存飞书表。
  - 返回重点：`confirmed_items`、`errors`、`next_action`。
  - 后续动作：Warehouse Agent 后续查询 `warehouse_sync_status=arrived_unsynced` 的采购单并同步到库存批次。

### 采购飞书表数据源接口

- `GET /procurement/replenishment-requests/table-schema`
  - 用途：返回采购补货请求飞书表字段定义。
  - schema：`procurement_replenishment_requests`。

- `POST /procurement/replenishment-requests/table-rows`
  - 用途：返回补货请求飞书表行数据。
  - 常用过滤：`status`、`request_id`、`limit`。
  - 唯一键字段：`Request ID`。

- `GET /procurement/purchase-orders/table-schema`
  - 用途：返回采购单飞书表字段定义。
  - schema：`procurement_purchase_orders`。

- `POST /procurement/purchase-orders/table-rows`
  - 用途：返回采购单飞书表行数据。
  - 常用过滤：`request_id`、`purchase_order_id`、`warehouse_sync_status`、`limit`。
  - 唯一键字段：`Purchase Order ID`。

## feishu-adapter

`feishu-adapter` 负责采购飞书多维表格读模型同步。其他 workflow 不应直接调用飞书开放平台创建或更新采购表，应该通过这里的受控接口。

### 采购补货请求表

- `POST /procurement/replenishment-requests-table/provision`
  - 用途：创建或复用采购补货请求表。
  - 默认表名：`Procurement Replenishment Requests`。
  - 唯一键：`Request ID`。

- `POST /procurement/replenishment-requests-table/sync`
  - 用途：把数据库中的补货申请同步到飞书补货请求表。
  - 数据源：`mock-api /procurement/replenishment-requests/table-rows`。
  - 常用入参：`status`、`request_id`、`limit`。

### 采购单表

- `POST /procurement/purchase-orders-table/provision`
  - 用途：创建或复用采购单表。
  - 默认表名：`Procurement Purchase Orders`。
  - 唯一键：`Purchase Order ID`。

- `POST /procurement/purchase-orders-table/sync`
  - 用途：把数据库中的采购单同步到飞书采购单表。
  - 数据源：`mock-api /procurement/purchase-orders/table-rows`。
  - 常用入参：`request_id`、`purchase_order_id`、`warehouse_sync_status`、`limit`。

### 配置约定

- 采购表复用同一个飞书 Base / app 凭据。
- 如果未配置采购 table id，后端可以自动建表。
- 采购单表优先读取 `FEISHU_PROCUREMENT_PURCHASE_ORDER_TABLE_ID`、`FEISHU_PROCUREMENT_PURCHASE_ORDER_TABLE_VIEW_ID`、`FEISHU_PROCUREMENT_PURCHASE_ORDER_TABLE_URL`；旧 draft 环境变量仍作为兼容 fallback。
- 表同步是数据库到飞书的单向读模型同步；当前不支持从飞书表编辑回写数据库。

## 业务数据库表

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

## 其他 workflow 可能会用到的契约

- Warehouse Agent 创建补货申请时调用 `POST /procurement/replenishment-requests`，不要直接创建采购单。
- Procurement Agent 批准或驳回补货申请后，状态只在 `replenishment_requests` 上流转，不直接改库存。
- Procurement Agent 确认 `PO-*` 到仓时必须调用 `POST /procurement/purchase-orders/confirm-arrival-batch`，只更新采购单仓库同步状态，不直接写 `inventory_batches` 或飞书库存表。
- Warehouse Agent 如果要同步采购到仓库存，应读取采购单中 `warehouse_sync_status=arrived_unsynced` 的记录，并使用采购单上的 `item_id`、`warehouse_id`、`location_code`、`quantity` 作为同步依据。
- Operations Agent 如果需要采购计划或采购单状态，应读取 `GET /procurement/replenishment-requests` 或 `GET /procurement/purchase-orders`，不要从飞书采购表反推源数据。
- Customer Support Agent 如果需要解释缺货补货进度，只能引用采购申请或采购单状态，不要承诺具体到货时间之外的正式履约承诺。
- 飞书采购表是读模型，不是采购源数据；采购源数据始终是 `mock-api` 暴露的采购接口和背后的业务表。

## 常见业务对象

- 补货申请 ID 示例：`REQ-1001`。
- 采购单 ID 示例：`PO-5001`。
- 商品 ID 示例：`item_vinda_tissue`、`item_milk_pure`、`item_cola_zero`、`item_copy_paper`。
- 仓库 ID 示例：`wh_sz_1`、`wh_hk_1`。
- 库位示例：`A1`、`B1`、`C1`。

## 维护原则

- 采购 agent 后续只维护本章节。
- 新增采购接口时，同步更新 `mock-api`、`feishu-adapter` 或 n8n 工具名对应说明。
- 新增跨 workflow 交接时，优先写清楚“谁创建、谁消费、状态怎么变、失败怎么处理”。
- 不清楚的业务归属或技术细节，先和用户确认后再改文档或代码。

# 物流agent

本文档用于和其他 workflow / agent 协作时快速说明物流 agent 的业务边界、可复用接口、数据表和交接契约。后续维护时，物流 agent 只维护本章节；其他 workflow 的内容不要在物流章节里改。

## 业务边界

物流 agent 负责读取订单上的物流供应商、快递员电话、物流单号和配送状态，查询已发货或已到货订单列表，并在用户明确要求时创建物流跟进 case。

物流 agent 不负责库存扣减、出库拣货、订单付款、退款赔付或退货入库。订单状态和库存事实仍由 Warehouse 维护；物流只消费 `orders` 和 `delivery_providers` 的读模型字段。

## workflow 入口

- Feishu 物流机器人消息进入 `feishu-adapter`，再转发到 n8n `Delivery Workflow`。
- n8n webhook：`POST /webhook/delivery-inbound`。
- workflow 文件：`n8n/workflows/delivery-workflow.json`。
- 当前主要工具：
  - `delivery_status_tool`：按 `ord_` 订单号查询数据库订单的物流状态、供应商、快递员电话和处理建议。
  - `delivery_exception_tool`：按订单状态或物流供应商查询物流订单列表，常用于已发货未到货或某承运商配送排查。
  - `delivery_case_tool`：为指定订单创建物流跟进 case。

## mock-api

物流路由已拆到 `services/mock-api/app/routers/delivery/`，由 `main.py` 通过 `delivery_router` 统一注册。物流路由只读取 Warehouse 拥有的订单模型，不直接修改库存或订单状态。

- `GET /delivery/providers`
  - 用途：列出当前可用物流供应商。
  - 默认供应商：顺丰（`sf`）、京东（`jd`）、圆通（`yto`）。

- `POST /delivery/status/lookup`
  - 用途：按 `order_id` 查询订单物流状态。
  - 返回重点：`order.status`、`delivery.provider_id`、`delivery.provider_name`、`delivery.courier_phone`、`delivery.tracking_no`、`risk_level`、`recommendation`。
  - 只接受订单号，不再依赖历史 `ship_` 运单 demo。

- `POST /delivery/exceptions/search`
  - 用途：按订单状态或供应商查询物流列表。
  - 常用入参：`status`、`provider_id`、`limit`。
  - 状态枚举：`未付款`、`待发货`、`已发货`、`已到货`、`已退款`、`已退货`、`已取消`。

- `POST /delivery/cases`
  - 用途：为真实订单创建物流跟进 case。
  - 入参重点：`order_id`、`case_type`、`reason`、`created_by`。

## 业务数据库表

- `delivery_providers`
  - 物流供应商表。
  - 关键字段：`provider_id`、`provider_name`、`service_hotline`、`tracking_prefix`、`status`。
  - 当前内置供应商：顺丰、京东、圆通。

- `orders`
  - 物流只读取订单主表上的物流字段，不拥有该表。
  - 物流相关字段：`delivery_provider_id`、`delivery_provider_name`、`courier_phone`、`tracking_no`、`status`。
  - 订单状态：`未付款`、`待发货`、`已发货`、`已到货`、`已退款`、`已退货`、`已取消`。

## 其他 workflow 可能会用到的契约

- Warehouse 创建订单时可以指定 `delivery_provider_id`、`courier_phone` 和 `tracking_no`；未指定供应商时默认顺丰。
- Warehouse 付款、发货、到货、退款、退货接口负责订单状态流转；Delivery 不直接调用库存扣减逻辑。
- Delivery 查询物流时只使用 `POST /delivery/status/lookup` 或 `POST /delivery/exceptions/search`，不要读取历史 `fixtures/data/orders.json` 或 `fixtures/data/shipments.json`。
- 需要判断是否能出库时，应交给 Warehouse 的 `POST /warehouse/fulfillment/check`，不要由 Delivery 自行判断库存。

## 维护原则

- 物流 agent 后续只维护本章节。
- 新增物流接口时，同步更新 `mock-api`、n8n 工具名和本章节说明。
- 新增物流供应商字段或订单状态时，优先写清楚 Warehouse 与 Delivery 的读写边界。
- 不清楚的业务归属或技术细节，先和用户确认后再改文档或代码。

# 前端

本文档用于记录 TalonMart 前端项目的长期协作约定。后续维护前端时，先查看本章节和相关前端文档，再改代码或接口契约。

## 项目定位

TalonMart 是一个非商用、用于部署到公网展示的面试项目。目标是做一个参考 Walmart 信息结构的消费者零售前台，但保持独立品牌，不使用 Walmart 商标、文案或第一方素材。

前端面向消费者展示商品浏览、搜索、促销区、购物车、下单和订单状态。前端不暴露仓储内部字段，例如批次号、库位、临期风险、补货申请、采购单或飞书同步状态。

## 技术栈

- 框架：Vue 3。
- 构建工具：Vite。
- 语言：TypeScript。
- 路由：Vue Router。
- 状态管理：Pinia。
- HTTP 请求：Axios。
- 样式：Tailwind CSS。
- 图标：lucide-vue-next。
- 当前不使用 Nuxt 3、SSR 或全栈框架；后端继续作为独立 API 服务。

前端项目路径：`apps/talonmart-web`。

## 设计系统

当前设计系统文档：`docs/frontend/talonmart-design-system.md`。

关键约定：

- 品牌名：TalonMart。
- 视觉方向：密集、清晰、可信的大型零售电商界面。
- 主色：深海军蓝 + 青色，促销用琥珀色，折扣和错误用红色，成功和可售状态用绿色。
- 首版以桌面 Web 为主；移动端只做基础响应式兜底，不单独设计移动导航模型。
- 不做营销落地页优先，首页应直接呈现可用的零售购物体验。

## 后端接口对接

前端通过 Axios 调用后端 API，不直接访问 Postgres、n8n 或飞书表。

当前商品列表 / 搜索接口文档：`docs/development/search-api-integration.zh.md`。

当前搜索接口：

- `GET /search?q=milk`
- `q` 通过 Postgres `pg_search` / BM25 匹配 `items.search_text`，来源包括 `items.item_id`、`items.item_name`、`items.brand`、`items.spec`。
- `q` 不匹配 `category_id`。
- 返回结果按 `item_id` 聚合。
- `item_id` 类型保持字符串，例如 `item_milk_pure`，不要为了前端展示改成 number。
- 库存余额明细放在 `items[].balances[]`。
- 前端不接收 `total_quantity_on_hand`；总库存由前端从 `balances[].quantity_on_hand` 求和。

注意：当前 `q=milk` 可以通过 `item_id=item_milk_pure` 命中中文商品；后续如果需要“牛乳”等自然语言同义词，应设计别名或同义词表，不要在接口里临时硬编码翻译规则。

## 部署约定

前端是 Vite 静态应用，构建产物为 `apps/talonmart-web/dist`。

当前部署选择：

- 面试展示优先使用 Netlify 托管静态前端。
- 仓库根目录的 `netlify.toml` 指定 `base=apps/talonmart-web`、`command=pnpm build`、`publish=dist`。
- 后端 API 单独部署，前端通过 Netlify 环境变量配置 API Base URL，例如 `VITE_API_BASE_URL`。
- Vue Router 使用 history 模式时，Netlify 需要把 `/*` rewrite 到 `/index.html`，避免刷新子路由 404。
- 只有在需要展示完整工程部署能力时，再增加 Docker + Nginx 托管前端静态产物。

前端不要求一开始放进 Docker。若后续使用 Docker，应避免把运行时业务逻辑放进前端容器；前端容器只负责服务静态文件。

## 维护原则

- 前端章节后续只维护 TalonMart 前端相关约定，不写仓储、采购或物流内部实现细节。
- 新增或调整前端依赖、接口契约、部署方式时，同步更新本章节和对应开发文档。
- 不清楚的产品交互、接口字段、部署目标或技术取舍，先和用户确认后再改文档或代码。
- Walmart 只能作为信息结构和交互密度参考，不复制品牌资产、页面文案或受保护内容。
