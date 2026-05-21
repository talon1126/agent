# Warehouse Agent 设计

日期：2026-05-21

## 目标

把 `Warehouse Agent` 从简单库存查询专员，升级成更贴近真实电商仓储运营的业务 agent。

第一版仍然是本地 demo，不接真实 WMS，但要覆盖仓库和库存员工实际会处理的问题：库存可用性、库位、履约风险、收货/上架异常、拣货/打包延迟、破损、库存差异和盘点跟进。

## 调研摘要

仓库和库存岗位不只是查数量。常见问题包括：

- 收货错误、漏扫、未记录调拨、拣货错误、破损、退货未入账导致的库存不准
- 缺货、超卖、库存积压、滞销和补货信号
- 仓库空间和库位问题，例如系统有库存但现场找不到货
- 收货、上架、补货、拣货、打包、暂存、出库和退货流程
- 循环盘点和差异调查
- 可用库存不足、未关闭异常、拣货/打包瓶颈导致的履约延迟

本设计参考了库存管理常见问题、仓储管理挑战、拣货最佳实践，以及库存文员职责中关于 receiving、issuing、storing、shipping、inventorying 和 discrepancy handling 的描述。

## 当前系统上下文

当前项目已有：

- `n8n/workflows/chat-parent-son-agent.json` 中的 `Warehouse Agent`
- 调用 `GET /inventory/{sku}` 的 `inventory_status_tool`
- `fixtures/data/inventory.json`，包含 `available`、`pending_orders`、`reorder_threshold`
- `mock-api`，适合作为企业系统模拟边界
- 相邻的 `Procurement Agent`、`Customer Support Agent`、`Operations Agent`

本设计扩展仓储域，不替换现有 Parent/Son n8n 架构。

## 职责范围

`Warehouse Agent` 第一版负责四类问题。

### 1. 库存可用性

示例：

- “sku_bag_1 还有多少库存？”
- “这个 SKU 会不会缺货？”
- “可用库存能不能覆盖待发订单？”

必须基于库存字段回答，不能猜。

输出应包含：

- SKU
- 可用库存
- 已预留库存
- 待处理订单
- 补货阈值
- 风险等级
- 行动建议

### 2. 库位和仓库状态

示例：

- “这个 SKU 在哪个库位？”
- “哪个仓库还有货？”
- “系统有库存但仓库找不到货，怎么办？”

输出应包含：

- warehouse ID/name
- zone
- bin/location
- 各库位数量
- 库位状态，例如 available、reserved、damaged、hold、pending_putaway
- 库位数据是否可靠

### 3. 履约和发货风险

示例：

- “这个订单能不能今天发？”
- “sku_bag_1 会不会影响发货？”
- “哪些 SKU 有履约风险？”

输出应包含：

- can_ship
- blocker 列表
- 库存覆盖计算
- 下一步动作建议
- 是否需要转 Customer Support 或 Procurement

### 4. 仓储异常

示例：

- “这个 SKU 有异常吗？”
- “库存差异怎么处理？”
- “今天仓储异常有哪些？”

第一版异常类型：

- `stock_mismatch`
- `damaged_goods`
- `missing_location`
- `pending_putaway`
- `picking_delay`

输出应包含：

- exception ID
- SKU
- 类型
- 严重程度
- 状态
- 库位
- 建议动作

## Agent 边界

`Warehouse Agent` 只负责仓库内部事实和履约风险。

- 面向客户的退款、投诉、政策问题转 `Customer Support Agent`。
- 采购单、供应商、补货责任、交期问题转 `Procurement Agent`。
- 日报、跨部门异常总结、指标汇总转 `Operations Agent`。
- 如果问题从仓储开始但需要采购，Warehouse Agent 可以说“库存存在补货风险，建议交给 Procurement Agent 处理采购动作”，但不自己创建采购单。

## 工具设计

### `warehouse_inventory_tool`

替代现在较窄的 `inventory_status_tool`。

后端接口：

`GET /warehouse/inventory/{sku}`

返回：

```json
{
  "ok": true,
  "sku": "sku_bag_1",
  "available": 5,
  "reserved": 3,
  "pending_orders": 9,
  "reorder_threshold": 15,
  "locations": [
    {
      "warehouse_id": "wh_hk_1",
      "zone": "A",
      "bin": "A-01-03",
      "quantity": 5,
      "status": "available"
    }
  ],
  "risk_level": "high",
  "recommendation": "库存不足以覆盖待处理订单，建议通知采购并关注发货风险。"
}
```

### `warehouse_exception_tool`

后端接口：

`POST /warehouse/exceptions/search`

输入：

```json
{
  "sku": "sku_bag_1",
  "status": "open"
}
```

返回异常记录，包括类型、严重程度、库位、状态和建议动作。

### `warehouse_fulfillment_tool`

后端接口：

`POST /warehouse/fulfillment/check`

输入可以包含 `sku` 或 `order_id`。

返回：

- 是否可发货
- blocker，例如 insufficient_stock、open_exception、missing_location、pending_putaway
- 下一步动作，例如 release_to_pick、cycle_count_required、notify_procurement、manual_review

## Mock 数据

新增或扩展 fixtures：

- `fixtures/data/inventory.json`
- `fixtures/data/warehouse_locations.json`
- `fixtures/data/warehouse_exceptions.json`

保持数据小而可检查。

最小场景：

- `sku_bottle_1`：健康库存，低风险
- `sku_bag_1`：低库存，有库存差异或拣货风险
- `sku_lamp_1`：待上架或破损风险

## n8n Workflow 改动

更新 `Warehouse Agent` prompt：

- 提到全部三个仓储工具
- SKU、库位、异常、履约问题必须调用工具
- 禁止编造仓储数据
- 回复使用适合飞书的简洁中文

更新 Parent 路由：

- inventory、SKU、warehouse、stock、picking、packing、putaway、fulfillment、cycle count、location、damaged goods 等问题路由到 `Warehouse Agent`
- replenishment 归属、purchase action 路由到 `Procurement Agent`
- 客户投诉、退款和客服回复路由到 `Customer Support Agent`

更新工具节点：

- 把 `inventory_status_tool` 改为 `warehouse_inventory_tool`
- 新增 `warehouse_exception_tool`
- 新增 `warehouse_fulfillment_tool`

## 测试

Mock API 测试：

- `GET /warehouse/inventory/sku_bag_1` 返回库位和高风险
- `POST /warehouse/exceptions/search` 返回 `sku_bag_1` 的 open 异常
- `POST /warehouse/fulfillment/check` 在库存或异常阻塞时返回 cannot ship
- 健康 SKU 返回 can ship

Workflow 测试：

- `Warehouse Agent` prompt 包含全部仓储工具
- 旧 `inventory_status_tool` 不再存在
- 新工具节点连接到 `Warehouse Agent`
- Parent prompt 能把仓储词路由到 `Warehouse Agent`
- 采购词仍路由到 `Procurement Agent`

Smoke tests：

- `sku_bag_1 还有多少库存`
- `sku_bag_1 在哪个库位`
- `sku_bag_1 有仓储异常吗`
- `sku_bag_1 今天能发货吗`

这些 smoke tests 可能触发 Qwen，所以只在你确认可以消耗额度后执行。

## 非目标

- 暂不接真实 WMS。
- 暂不做创建拣货任务、移动库存、关闭异常、提交库存调整等写操作。
- 不实现条码/RFID。
- 不做路径规划或波次拣货这类高级优化。
- 不让 Warehouse Agent 自主执行采购动作。

## 推荐第一版实现

按“仓储 readiness 升级”来做：

1. 新增仓储 fixture 和 mock-api endpoint。
2. 用 `warehouse_inventory_tool` 替换 `inventory_status_tool`。
3. 新增异常和履约工具。
4. 更新 prompt 和测试。
5. 结构测试通过后再导入/发布 n8n。
