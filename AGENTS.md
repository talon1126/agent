# 仓储agent

本文档用于和其他 workflow / agent 协作时快速说明仓储 agent 的业务边界、可复用接口、数据表和交接契约。后续维护时，仓储 agent 只维护本章节；其他 workflow 可以按同级大标题追加自己的章节。

## 业务边界

仓储 agent 负责库存、仓库、库位、批次、临期风险、仓储异常、履约风险、补货申请发起，以及采购到货后的库存同步任务处理。

仓储 agent 不负责供应商选择、采购单审批、退款/赔付、物流承运商决策。需要采购时只生成补货申请或消费采购到货同步任务，后续采购动作由采购 agent 处理。

## workflow 入口

- Feishu 仓储机器人消息进入 `feishu-adapter`，再转发到 n8n `Warehouse Workflow`。
- n8n webhook：`POST /webhook/warehouse-inbound`。
- workflow 文件：`n8n/workflows/warehouse-workflow.json`。
- 当前主要工具：
  - `warehouse_inventory_tool`：查询商品批次库存。
  - `warehouse_exception_tool`：查询高风险、临期、过期、质检冻结等仓储异常。
  - `warehouse_fulfillment_tool`：判断商品是否可发货，返回履约阻塞原因。
  - `warehouse_replenishment_request_tool`：低库存时创建补货申请，交给采购 agent。
  - `warehouse_inventory_table_provision_tool`：显式创建或初始化飞书库存表。
  - `warehouse_inventory_table_sync_tool`：按商品、仓库、库位、分类、批次或风险范围同步库存快照到飞书。
  - `warehouse_table_schema_tool`：创建飞书库存视图前读取真实字段和视图。
  - `warehouse_view_create_tool`：按受控 JSON 创建或复用飞书库存视图。
  - `warehouse_inventory_sync_jobs_tool`：处理采购到货后产生的待同步库存任务。

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
  - 用途：按 `item_id + warehouse_id + location_code + batch_no` 聚合返回仓库库位级库存余额。
  - 返回重点：`quantity_on_hand`、`quantity_available`、`batch_count`、`earliest_expiry_date`。
  - 物流 agent 需要判断某件商品在某仓库/库位还有多少可用库存时，应优先使用这个接口。

- `POST /warehouse/orders`
  - 用途：创建订单主单，支持多行商品。
  - 入参重点：`order_id`、`customer_id`、`items[].item_id`、`items[].warehouse_id`、`items[].quantity`。
  - 创建后状态为 `created`，不会扣减库存。

- `POST /warehouse/orders/{order_id}/pay`
  - 用途：订单付款后按 FEFO 从 `inventory_location_balances` 扣减库存，并在 `order_items` 记录命中的 `warehouse_id + location_code + batch_no + quantity`。

- `POST /warehouse/orders/{order_id}/ship`
  - 用途：订单发货，只更新订单状态为 `shipped`，不再次扣减库存。

- `POST /warehouse/orders/{order_id}/arrive`
  - 用途：订单到货，只更新订单状态为 `arrived`，不再次扣减库存。

- `POST /warehouse/orders/{order_id}/cancel`
  - 用途：订单取消或发货前退款，按 `order_items` 原批次加回 `inventory_location_balances`。

- `POST /warehouse/orders/{order_id}/return`
  - 用途：已到货后退货，按 `order_items` 原批次加回 `inventory_location_balances`。

### 补货和采购交接接口

- `POST /procurement/replenishment-requests`
  - 用途：仓储发现低库存后创建补货申请。
  - 默认状态：`pending_procurement_review`。
  - 采购 agent 后续负责审批、拒绝和创建采购单草稿。

- `GET /procurement/replenishment-requests?status=pending_procurement_review`
  - 用途：采购 agent 查询待审核补货申请。

- `POST /procurement/purchase-order-drafts/confirm-arrival-batch`
  - 用途：采购人员批量确认采购单到仓。
  - 影响：创建 `RCV-POD-*` 入库批次，并创建 `warehouse_inventory_sync_jobs` 待处理任务。
  - 返回重点：`confirmed_items`、`warehouse_inventory_sync_requests`、`warehouse_inventory_sync_jobs`、`next_action`。

### 库存同步任务接口

- `GET /warehouse/inventory-sync-jobs?status=pending`
  - 用途：仓储 agent 拉取待处理库存同步任务。
  - 任务来源：采购到货确认后产生的 `warehouse_inventory_sync_requested`。

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
  - 用途：批量处理仓储库存同步任务。
  - 入参重点：`jobs`、`limit_per_job`、`table_name`。
  - 当前实现按 job 的 `item_id + warehouse_id + location_code + batch_no` 精确取行，再批量 upsert 到飞书表。
  - 这是采购到货后增量同步的主路径。

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
  - 关键字段：`item_id`、`category_id`、`item_name`、`brand`、`spec`、`unit`、`barcode`。

- `inventory_batches`
  - 批次入库事实表，用于进货记录和商品批次溯源，不再被订单扣减。
  - 关键字段：`warehouse_id`、`location_code`、`item_id`、`batch_no`、`production_date`、`expiry_date`、`quantity_on_hand`、`quantity_reserved`、`reorder_threshold`、`storage_status`。

- `inventory_location_balances`
  - 批次级库位库存余额表，订单付款扣减、取消或退货加回。
  - 关键字段：`id`、`warehouse_id`、`location_code`、`item_id`、`batch_no`、`quantity_on_hand`、`reorder_threshold`、`storage_status`。
  - 初始化来源：按 `inventory_batches.quantity_on_hand` 建立余额，忽略旧模型的 `quantity_reserved`。

- `orders`
  - 订单主表，记录下单、付款、发货、到货、取消和退货状态。
  - 关键字段：`id`、`order_id`、`customer_id`、`status`、`requested_items_json`、`paid_at`、`shipped_at`、`arrived_at`、`cancelled_at`、`returned_at`。

- `order_items`
  - 订单明细表，记录付款扣减命中的批次库存，用于取消和退货时按原批次加回。
  - 关键字段：`id`、`order_id`、`customer_id`、`status`、`item_id`、`warehouse_id`、`location_code`、`batch_no`、`quantity`。

- `replenishment_requests`
  - 仓储发起、采购处理的补货申请。
  - 关键字段：`request_id`、`status`、`warehouse_id`、`location_code`、`item_id`、`current_quantity`、`reorder_threshold`、`suggested_quantity`、`reason`、`created_by`。
  - 主要状态：`pending_procurement_review`、`purchase_order_draft_created`、`rejected`。

- `purchase_order_drafts`
  - 采购单草稿，由采购 agent 负责，但到货后会影响仓储库存。
  - 关键字段：`po_draft_id`、`request_id`、`supplier_id`、`item_id`、`quantity`、`status`、`estimated_arrival_date`。
  - 到货后状态会进入 `received_at_warehouse`。

- `warehouse_inventory_sync_jobs`
  - 仓储库存同步任务表。
  - 关键字段：`job_id`、`event`、`po_draft_id`、`request_id`、`item_id`、`warehouse_id`、`location_code`、`batch_no`、`quantity`、`status`、`processed_by`、`processed_at`、`result_json`、`error`。
  - 采购确认到货后新增 `pending` 任务；仓储处理完成后改为 `completed` 或 `failed`。

## 其他 workflow 可能会用到的契约

- 采购 agent 如果确认采购单到货，必须通过 `POST /procurement/purchase-order-drafts/confirm-arrival-batch` 产生仓储同步任务，不要直接写飞书库存表。
- 物流 agent 如果只需要判断是否能出库，可以调用 `POST /warehouse/fulfillment/check`，不要自行推断批次库存。
- 订单付款后必须调用 `POST /warehouse/orders/{order_id}/pay` 扣减 `inventory_location_balances`；发货和到货只更新订单状态；取消或退货调用 `/cancel` 或 `/return` 按原批次加回库存。
- 客服 agent 如果需要解释“为什么不能发货”，可以引用 `warehouse/fulfillment/check` 的 `blockers` 和 `next_action`，但不要承诺退款或补偿。
- 运营 agent 如果要做库存风险汇总，可以调用 `POST /warehouse/inventory/search`，按 `risk_level`、`expiry_risk`、`warehouse_id`、`category` 聚合。
- 任何 agent 需要飞书库存表、视图或同步状态，都应调用 `feishu-adapter` 的 `/warehouse/inventory-table/*` 接口，不要直接访问飞书开放平台。
- 飞书库存表是读模型，不是库存源数据；库存源数据始终是 `mock-api` 暴露的仓储事实接口和背后的业务表。

## 常见业务对象

- 商品 ID 示例：`item_vinda_tissue`、`item_milk_pure`、`item_cola_zero`、`item_copy_paper`。
- 仓库 ID 示例：`wh_sz_1`、`wh_hk_1`、`wh_sg_1`。
- 库位示例：`A1`、`B1`、`C1`。
- 批次号示例：`RCV-POD-*` 表示采购到货生成的入库批次。
- 同步任务 ID 示例：`WSJ-POD-*`。
- 补货申请 ID 示例：`REQ-*`。
- 采购单草稿 ID 示例：`POD-*`。

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
  - `procurement_approve_replenishment_batch_tool`：批量批准全部 pending 补货申请，生成或复用采购单，并刷新两张采购飞书表。
  - `procurement_confirm_purchase_order_arrival_tool`：确认一个或多个 `PO-*` 到仓，把采购单标记为 `arrived_unsynced`，并刷新采购单表。
  - `procurement_replenishment_request_tool`：查询 Warehouse 创建的 `pending_procurement_review` 补货申请。
  - `procurement_approve_replenishment_tool`：批准单个 `REQ-*` 补货申请，生成或复用采购单。
  - `procurement_reject_replenishment_tool`：驳回单个 `REQ-*` 补货申请，并记录拒绝原因。
  - `procurement_mock_tool`：按 `item_id` 生成基础 mock 采购建议。

## ai-service

当前采购业务没有在 `ai-service` 暴露专用 HTTP 接口。采购的确定性业务动作主要通过 n8n agent 调用 `mock-api` 和 `feishu-adapter` 完成。

如果后续要把采购决策下沉到 `ai-service`，需要先明确是否新增可测试的采购决策层，例如供应商选择策略、比价规则、采购审批策略。不要让 `ai-service` 直接读写采购 Postgres 表，采购事实仍应通过 `mock-api` 或未来采购服务暴露。

## mock-api

`mock-api` 是采购事实数据的后端模拟系统。其他 workflow 如果需要采购状态，应优先调用这些接口，不要直接访问 Postgres。

### 采购建议接口

- `POST /procurement/mock`
  - 用途：根据 `item_id` 和文本查询返回基础采购建议。
  - 当前定位：mock 建议，不代表真实 ERP 报价或正式采购决策。

### 补货申请接口

- `POST /procurement/replenishment-requests`
  - 用途：创建补货申请。
  - 主要调用方：Warehouse Agent 在确认低库存后调用。
  - 默认状态：`pending_procurement_review`。
  - 入参重点：`source`、`warehouse_id`、`location_code`、`item_id`、`reason`、`created_by`。

- `GET /procurement/replenishment-requests?status=pending_procurement_review`
  - 用途：查询补货申请，可按状态过滤。
  - 主要调用方：Procurement Agent、采购飞书表同步。

- `POST /procurement/replenishment-requests/{request_id}/approve`
  - 用途：批准单个 `REQ-*` 补货申请。
  - 影响：申请状态更新为 `purchase_order_created`，创建或复用 `PO-*` 采购单。
  - 幂等规则：重复批准同一个 `REQ-*` 必须复用已有采购单，不重复创建。
  - 采购单会继承补货申请的 `warehouse_id`、`warehouse_name`、`location_code`。
  - 异常：商品没有默认供应商时返回明确错误。

- `POST /procurement/replenishment-requests/{request_id}/reject`
  - 用途：驳回单个 `REQ-*` 补货申请。
  - 影响：申请状态更新为 `rejected`，记录拒绝原因。
  - 不会创建采购单。

- `POST /procurement/replenishment-requests/approve-batch`
  - 用途：批量批准补货申请。
  - 默认范围：全部 `pending_procurement_review`。
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
  - 主要状态：`pending_procurement_review`、`purchase_order_created`、`rejected`。

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
