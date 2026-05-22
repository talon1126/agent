# 仓储库存飞书表格创建和同步

仓储库存表格创建会在一个已有的飞书多维表格 app/base 里创建固定 schema 的数据表。仓储库存表格同步则用于在仓储用户明确要求“同步、导出、发布、飞书表格、看板”时，把某个 SKU 的库存快照发布到这张表里。

## 设计

飞书表格是 read model，不是库存主数据源。

```text
mock-api / 未来仓储系统
        -> feishu-adapter /warehouse/inventory-table/provision
        -> feishu-adapter /warehouse/inventory-table/sync
        -> 飞书表格 upsert
        -> Warehouse Agent 回复
```

库存事实仍然保留在 `mock-api` 或未来真实仓储系统中。飞书表格只提供给员工协作查看，展示风险和建议。

## 创建行为

Warehouse Agent 有一个工具叫 `warehouse_inventory_table_provision_tool`。只有用户明确要求创建、初始化、配置或 provision 仓储库存飞书表格时，才应该调用这个工具。

创建 endpoint 会：

- 要求配置 `FEISHU_INVENTORY_TABLE_APP_ID`、`FEISHU_INVENTORY_TABLE_APP_SECRET` 和 `FEISHU_INVENTORY_TABLE_APP_TOKEN`。
- 在配置好的飞书多维表格 app/base 中创建一张数据表。
- 创建下方列出的固定库存快照字段，其中 `Risk Level` 和 `Sync Status` 是带颜色的单选字段。
- 如果已经配置 `FEISHU_INVENTORY_TABLE_ID`，直接返回 `action=existing`，不会重复调用飞书建表。
- 如果飞书返回 `TableNameDuplicated`，会按同名表复用，并补齐缺失字段。这样可以从“表已创建但字段创建失败”的半成品状态恢复。
- 如果已有 `Risk Level` 和 `Sync Status` 是旧的文本字段，只要飞书返回了字段 ID，后端会把它们升级为带颜色的单选字段。
- 配置 `FEISHU_RUN_LOG_URL` 后写入 run log。
- 不创建新的飞书 app/base，也不创建库存主数据源。

## 同步行为

Warehouse Agent 有一个工具叫 `warehouse_inventory_table_sync_tool`。只有用户明确要求同步、导出、发布、飞书表格或看板时，才应该调用这个工具。

同步 endpoint 会：

- 从 `mock-api` 获取 `GET /warehouse/inventory/{sku}`。
- 如果没有配置 `FEISHU_INVENTORY_TABLE_ID`，会自动创建或复用库存表。
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
Risk Level        # 单选：low=绿色，medium=黄色，high=红色，unknown=灰色
Open Exception Count
Recommendation
Last Synced At
Sync Status       # 单选：synced=绿色，pending=黄色，failed=红色
Source Version
```

`Source Version` 是轻量幂等标识，例如 `mock-api:sku_bag_1:wh_hk_1`。

如果旧表里的 `Risk Level` 或 `Sync Status` 现在仍然是白色普通文本，部署这个版本后重新调用创建 endpoint。adapter 会检查已有字段，并把这两个字段更新成带颜色的单选字段。

## 必要环境变量

```env
FEISHU_INVENTORY_TABLE_APP_ID=
FEISHU_INVENTORY_TABLE_APP_SECRET=
FEISHU_INVENTORY_TABLE_APP_TOKEN=
FEISHU_INVENTORY_TABLE_ID=
FEISHU_INVENTORY_TABLE_VIEW_ID=
FEISHU_INVENTORY_TABLE_URL=
```

`FEISHU_INVENTORY_TABLE_ID` 是可选项。如果配置了它，创建和同步都会使用这张表。如果为空，后端会按 `Warehouse Inventory Snapshot` 这个表名查找，找到则复用，找不到则创建。adapter 会在当前进程内记住解析出的 `table_id`，所以同步不再必须手动编辑 `.env`。

如果是长期 demo，仍然建议把返回的 `table_id` 填回 `.env`，这样容器重启后不用再按表名查找。

`FEISHU_INVENTORY_TABLE_VIEW_ID` 和 `FEISHU_INVENTORY_TABLE_URL` 是可选项。URL 只用于在回复中返回一个方便打开的链接。

## 手动 Smoke Test

创建表格：

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/provision `
  -ContentType "application/json" `
  -Body '{"table_name":"Warehouse Inventory Snapshot"}' | ConvertTo-Json -Depth 10
```

同步库存：

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/sync `
  -ContentType "application/json" `
  -Body '{"sku":"sku_bag_1"}' | ConvertTo-Json -Depth 10
```

预期行为：

- 缺少创建配置时返回 `ok=false` 和 `missing_feishu_inventory_table_provision_config`。
- 已经配置 `FEISHU_INVENTORY_TABLE_ID` 时返回 `ok=true` 和 `action=existing`。
- 创建成功时返回 `ok=true`、`action=created` 或 `action=existing`、`table_id` 和固定字段列表。
- 缺少同步配置时返回 `ok=false` 和 `missing_feishu_inventory_table_config`。
- 同步配置正确时会自动创建或复用表格，然后返回 `ok=true`、`action=created` 或 `action=updated`，以及 `record_id`。
