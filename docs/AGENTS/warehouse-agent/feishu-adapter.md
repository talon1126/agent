# feishu-adapter

`feishu-adapter` 负责飞书协议、多维表格和仓储意图快路径。其他 workflow 不应直接调用飞书开放平台，应该通过这里的受控接口。

## 仓储意图路由

- `POST /warehouse/intents/route`
  - 用途：把自然语言仓储请求路由到库存查询、表格同步、视图创建或 agent fallback。
  - 常见输出：`executor`、`slots`、`clarification_required`、`clarification_question`。

## 飞书库存表

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

## 飞书库存余额表

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
