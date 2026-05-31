# feishu-adapter

`feishu-adapter` 负责采购飞书多维表格读模型同步。其他 workflow 不应直接调用飞书开放平台创建或更新采购表，应该通过这里的受控接口。

## 采购补货请求表

- `POST /procurement/replenishment-requests-table/provision`
  - 用途：创建或复用采购补货请求表。
  - 默认表名：`Procurement Replenishment Requests`。
  - 唯一键：`Request ID`。

- `POST /procurement/replenishment-requests-table/sync`
  - 用途：把数据库中的补货申请同步到飞书补货请求表。
  - 数据源：`mock-api /procurement/replenishment-requests/table-rows`。
  - 常用入参：`status`、`request_id`、`limit`。

## 采购单表

- `POST /procurement/purchase-orders-table/provision`
  - 用途：创建或复用采购单表。
  - 默认表名：`Procurement Purchase Orders`。
  - 唯一键：`Purchase Order ID`。

- `POST /procurement/purchase-orders-table/sync`
  - 用途：把数据库中的采购单同步到飞书采购单表。
  - 数据源：`mock-api /procurement/purchase-orders/table-rows`。
  - 常用入参：`request_id`、`purchase_order_id`、`warehouse_sync_status`、`limit`。

## 配置约定

- 采购表复用同一个飞书 Base / app 凭据。
- 如果未配置采购 table id，后端可以自动建表。
- 采购单表优先读取 `FEISHU_PROCUREMENT_PURCHASE_ORDER_TABLE_ID`、`FEISHU_PROCUREMENT_PURCHASE_ORDER_TABLE_VIEW_ID`、`FEISHU_PROCUREMENT_PURCHASE_ORDER_TABLE_URL`；旧 draft 环境变量仍作为兼容 fallback。
- 表同步是数据库到飞书的单向读模型同步；当前不支持从飞书表编辑回写数据库。
