# mock-api

`mock-api` 是采购事实数据的后端模拟系统。其他 workflow 如果需要采购状态，应优先调用这些接口，不要直接访问 Postgres。

## 路由实现结构

- 采购 HTTP 路由已从 `services/mock-api/app/main.py` 拆到 `services/mock-api/app/routers/procurement/`，由 `main.py` 通过 `procurement_router` 统一注册。
- `mock.py`：基础 mock 采购建议接口。
- `requests.py`：补货申请创建、查询、批准、驳回、批量批准，以及补货申请飞书表 schema/rows。
- `purchase_orders.py`：采购单查询、到仓确认，以及采购单飞书表 schema/rows。
- `service.py`：采购确定性业务逻辑，例如默认供应商匹配、采购单生成、预计到达日期、状态流转和飞书表字段映射。
- `schemas.py`：采购请求模型；`state.py`：内存 fallback 状态。
- `/warehouse/*` 仍归仓储 router；采购 router 不直接创建库存批次、不创建 Warehouse sync job。

## 采购建议接口

- `POST /procurement/mock`
  - 用途：根据 `item_id` 和文本查询返回基础采购建议。
  - 当前定位：mock 建议，不代表真实 ERP 报价或正式采购决策。

## 补货申请接口

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

## 采购单接口

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

## 采购飞书表数据源接口

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
