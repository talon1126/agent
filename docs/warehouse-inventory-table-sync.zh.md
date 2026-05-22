# 仓储库存飞书表格同步

仓储库存表格同步用于在仓储用户明确要求“同步、导出、发布、飞书表格、看板”时，把某个 SKU 的库存快照发布到飞书表格。

## 设计

飞书表格是 read model，不是库存主数据源。

```text
mock-api / 未来仓储系统
        -> feishu-adapter /warehouse/inventory-table/sync
        -> 飞书表格 upsert
        -> Warehouse Agent 回复
```

库存事实仍然保留在 `mock-api` 或未来真实仓储系统中。飞书表格只提供给员工协作查看，展示风险和建议。

## 同步行为

Warehouse Agent 有一个工具叫 `warehouse_inventory_table_sync_tool`。只有用户明确要求同步、导出、发布、飞书表格或看板时，才应该调用这个工具。

同步 endpoint 会：

- 从 `mock-api` 获取 `GET /warehouse/inventory/{sku}`。
- 构造标准表格行。
- 按 `SKU + Warehouse` 查找飞书表格已有记录。
- 如果记录存在则更新，不存在则创建。
- 配置 `FEISHU_RUN_LOG_URL` 后写入 run log。
- 如果表格配置缺失，返回安全降级错误。

## 表格字段

在飞书表格中创建这些字段：

```text
SKU
Product Name
Warehouse
Available
Reserved
Pending Orders
Risk Level
Open Exception Count
Recommendation
Last Synced At
Sync Status
Source Version
```

`Source Version` 是轻量幂等标识，例如 `mock-api:sku_bag_1:wh_hk_1`。

## 必要环境变量

```env
FEISHU_INVENTORY_TABLE_APP_ID=
FEISHU_INVENTORY_TABLE_APP_SECRET=
FEISHU_INVENTORY_TABLE_APP_TOKEN=
FEISHU_INVENTORY_TABLE_ID=
FEISHU_INVENTORY_TABLE_VIEW_ID=
FEISHU_INVENTORY_TABLE_URL=
```

`FEISHU_INVENTORY_TABLE_VIEW_ID` 和 `FEISHU_INVENTORY_TABLE_URL` 是可选项。URL 只用于在回复中返回一个方便打开的链接。

## 手动 Smoke Test

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/sync `
  -ContentType "application/json" `
  -Body '{"sku":"sku_bag_1"}' | ConvertTo-Json -Depth 10
```

预期行为：

- 缺少配置时返回 `ok=false` 和 `missing_feishu_inventory_table_config`。
- 配置正确时返回 `ok=true`、`action=created` 或 `action=updated`，以及 `record_id`。
