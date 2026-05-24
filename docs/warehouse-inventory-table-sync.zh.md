# 仓储库存飞书表格创建和同步

仓储库存表格创建会在一个已有的飞书多维表格 app/base 里创建固定 schema 的数据表。仓储库存表格同步用于在仓储用户明确要求“同步、导出、发布、飞书表格、看板”时，发布批次 + 库位库存快照。

飞书表格是 read model，不是库存主数据源。

## 设计

```text
mock-api / 未来仓储系统
        -> feishu-adapter /warehouse/inventory-table/provision
        -> feishu-adapter /warehouse/inventory-table/sync
        -> feishu-adapter /warehouse/inventory-table/sync/filter
        -> feishu-adapter /warehouse/inventory-table/schema
        -> feishu-adapter /warehouse/inventory-table/views/from-template
        -> 飞书表格 upsert 或飞书视图创建
        -> Warehouse workflow 回复
```

库存事实仍然保留在 `mock-api` 或未来真实仓储系统中。飞书只提供给员工协作查看，展示风险、临期状态、库位和建议。

## 数据模型

当前仓储库存模型是批次 + 库位模型：

- `warehouses`：仓库身份，例如深圳仓、香港仓、新加坡仓
- `storage_locations`：具体库位，例如 A1、B1、C1
- `categories`：业务分类，例如纸品、乳制品、饮料、日化、办公耗材
- `items`：商品主数据，包括品牌、规格和单位
- `inventory_batches`：按商品、仓库、库位和批次记录数量、预留、生产日期、过期日期、风险等级和建议

飞书同步的幂等记录身份是：

```text
Warehouse ID + Location + Item ID + Batch No
```

## 创建行为

Warehouse Agent 有一个工具叫 `warehouse_inventory_table_provision_tool`。只有用户明确要求创建、初始化、配置或 provision 仓储库存飞书表格时，才应该调用这个工具。

创建 endpoint 会：

- 要求配置 `FEISHU_INVENTORY_TABLE_APP_ID`、`FEISHU_INVENTORY_TABLE_APP_SECRET` 和 `FEISHU_INVENTORY_TABLE_APP_TOKEN`。
- 在配置好的飞书多维表格 app/base 中创建一张数据表。
- 创建固定的批次 + 库位字段，其中 `Risk Level`、`Expiry Risk`、`Storage Status` 和 `Sync Status` 是带颜色的单选字段。
- 如果已经配置 `FEISHU_INVENTORY_TABLE_ID`，直接返回 `action=existing`，不会重复调用飞书建表。
- 如果飞书返回 `TableNameDuplicated`，会按同名表复用，并补齐缺失字段。
- 配置 `FEISHU_RUN_LOG_URL` 后写入 run log。
- 不创建新的飞书 app/base，也不创建库存主数据源。

## 同步行为

同步有两条路径：

- `/warehouse/inventory-table/sync` 同步一个具体 `item_id`，例如 `item_vinda_tissue`。
- `/warehouse/inventory-table/sync/filter` 按仓库、库位、分类、风险等级、临期状态或批次号同步一批记录。

同步 endpoint 会：

- 从 `mock-api /warehouse/inventory/table-rows` 获取行。
- 如果没有配置 `FEISHU_INVENTORY_TABLE_ID`，会自动创建或复用库存表。
- 构造批次 + 库位字段的飞书行。
- 按 `Warehouse ID + Location + Item ID + Batch No` 查找飞书表格已有记录。
- 如果记录存在则更新，不存在则创建。
- 如果表格配置缺失，返回安全降级错误。

## 视图创建行为

自然语言创建视图应使用：

- `POST /warehouse/inventory-table/views/from-template`：用于员工自然语言请求。
- `POST /warehouse/inventory-table/views/create`：只用于受控 JSON 计划。

示例用户请求：

```text
@Warehouse 帮我建一个香港仓乳制品临期库存视图
```

预期模板计划：

```json
{
  "template_id": "expiring_inventory_view",
  "view_name": "香港仓乳制品临期库存",
  "visible_fields": ["Warehouse", "Location", "Category", "Item Name", "Batch No", "Expiry Date", "Days To Expiry", "Expiry Risk", "Quantity Available", "Recommendation"],
  "filters": [
    {"field": "Warehouse ID", "operator": "is", "value": "wh_hk_1"},
    {"field": "Category ID", "operator": "is", "value": "dairy"},
    {"field": "Expiry Risk", "operator": "is", "value": "expiring_soon"}
  ],
  "sorts": [{"field": "Days To Expiry", "order": "asc"}]
}
```

## 表格字段

在飞书表格中创建这些字段：

```text
Warehouse
Warehouse ID
Location
Category
Category ID
Item ID
Item Name
Brand
Spec
Unit
Batch No
Quantity On Hand
Quantity Available
Quantity Reserved
Reorder Threshold
Production Date
Expiry Date
Days To Expiry
Expiry Risk
Risk Level
Storage Status
Recommendation
Last Synced At
Sync Status
Source Version
```

`Source Version` 是轻量幂等标识，例如 `mock-api:wh_sz_1:A1:item_vinda_tissue:BATCH-20260501`。

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

## 手动 Smoke Test

创建表格：

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/provision `
  -ContentType "application/json" `
  -Body '{"table_name":"Warehouse Inventory Snapshot"}' | ConvertTo-Json -Depth 10
```

同步单个商品：

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/sync `
  -ContentType "application/json" `
  -Body '{"item_id":"item_vinda_tissue"}' | ConvertTo-Json -Depth 10
```

按仓库/分类/库位范围同步：

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/sync/filter `
  -ContentType "application/json" `
  -Body '{"warehouse_id":"wh_sz_1","location_code":"A1","category":"dairy","expiry_risk":"expiring_soon","limit":50}' | ConvertTo-Json -Depth 10
```

基于模板创建或复用视图：

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/views/from-template `
  -ContentType "application/json" `
  -Body '{"message":"帮我建一个深圳仓A1库位库存视图"}' | ConvertTo-Json -Depth 10
```

预期行为：

- 缺少创建配置时返回 `ok=false` 和 `missing_feishu_inventory_table_provision_config`。
- 已经配置 `FEISHU_INVENTORY_TABLE_ID` 时返回 `ok=true` 和 `action=existing`。
- 创建成功时返回 `ok=true`、`action=created` 或 `action=existing`、`table_id` 和固定字段列表。
- 同步配置正确时返回 `ok=true`、`synced_count`，以及每条记录的 `item_id`、`batch_no`、`warehouse_id`、`location_code`、`risk_level`、`action` 和 `record_id`。
- 读取 schema 会返回 `ok=true`、`table_id`、`fields` 和 `views`。
- 模板创建视图会返回 `ok=true`、`action=created` 或 `action=existing`、`view_id` 和 `validated_plan`。
- 如果字段不存在，会返回 `ok=false`、`invalid_inventory_view_plan` 和 `missing_fields`，并且不会调用飞书创建视图。
