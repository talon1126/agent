# 仓储视图模板构建器

仓储员工可以用自然业务语言创建飞书库存视图，不需要知道字段名、筛选语法、排序规则或 API payload。

当前仓储模型是批次 + 库位库存。视图可以按仓库、库位、分类、临期状态、风险等级或可用库存数量筛选。

## 自然语言示例

```text
@Warehouse 帮我建一个深圳仓纸品库存视图
@Warehouse 帮我建一个香港仓乳制品临期库存视图
@Warehouse 帮我建一个深圳仓A1库位库存视图
@Warehouse 帮我建一个库存低于20的牛奶预警视图
@Warehouse 帮我建一个高风险批次视图
```

## 运行链路

```text
飞书消息
-> feishu-adapter
-> n8n Warehouse Workflow
-> Warehouse Intent Router
-> Create Warehouse View From Template
-> POST /warehouse/inventory-table/views/from-template
-> 创建或复用飞书多维表格视图
-> Format Warehouse View Template Reply
```

如果后端没有匹配到受支持模板，workflow 会恢复原始消息，并把请求交给 `Warehouse Agent` 兜底处理。

## 当前模板

| Template ID | 业务用途 | 示例 |
| --- | --- | --- |
| `category_inventory_view` | 按分类查看库存 | 深圳仓纸品库存 |
| `low_stock_view` | 缺货/低库存预警 | 库存低于20的牛奶预警 |
| `expiring_inventory_view` | 临期或过期批次 | 香港仓乳制品临期库存 |
| `location_inventory_view` | 按库位查看库存 | 深圳仓A1库位库存 |
| `batch_risk_view` | 高风险库存批次 | 高风险批次视图 |
| `replenishment_candidate_view` | 补货候选 | 补货候选视图 |

## 受控字段

模板使用批次 + 库位 read model 中的字段：

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

后端会在调用飞书前校验所有可见字段、筛选字段和排序字段。

## Smoke Test

只测试模板识别，不写入飞书：

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/intents/route `
  -ContentType "application/json" `
  -Body '{"message":"帮我建一个深圳仓纸品库存视图"}' | ConvertTo-Json -Depth 10
```

创建或复用飞书视图：

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/warehouse/inventory-table/views/from-template `
  -ContentType "application/json" `
  -Body '{"message":"帮我建一个香港仓乳制品临期库存视图"}' | ConvertTo-Json -Depth 10
```

预期结果：

- `matched=true`
- `template_id` 属于上面的当前模板之一
- `validated_plan.visible_fields` 包含批次 + 库位字段
- `validated_plan.filters` 包含业务筛选，例如 `Warehouse ID`、`Category ID`、`Location`、`Expiry Risk`、`Risk Level` 或 `Quantity Available`
- 未知模板返回 `ok=false`、`matched=false` 和建议模板
