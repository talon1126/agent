# Warehouse View Template Builder 设计

日期：2026-05-23

## 目标

让普通仓储员工用业务语言创建飞书多维表格视图，而不是学习字段名、JSON、筛选条件或排序语法。

理想使用方式是：

```text
@Warehouse 帮我建一个高风险库存视图
```

系统应该自动把这句话解析成受控模板，读取后端仓储数据/表结构，创建或复用飞书视图，然后返回 `view_id`、字段、筛选、排序等简洁结果。

## 问题

当前仓储视图创建链路已经能创建飞书视图，但仍然偏技术化，例如：

```text
创建一个视图，只显示 SKU、Warehouse、Available、Risk Level、Recommendation，过滤 Risk Level=high，并按 Available 升序排序
```

这不是普通员工应该学习的表达方式。内部员工不应该知道精确字段名、英文字段、API payload 或过滤语法。之前让 Agent 自由生成这些细节，已经导致重复调用工具和 `422` 参数错误。

## 推荐方案

使用模板驱动的 View Builder：

```text
员工消息
-> n8n warehouse workflow
-> 检测建视图模板请求
-> feishu-adapter 模板匹配
-> template + slots
-> schema 校验
-> 复用现有 /warehouse/inventory-table/views/create
-> 飞书回复
```

后续 Agent 可以辅助判断模糊语言，但 Agent 不能直接操作飞书 API，也不能自由生成最终字段和筛选 payload。

## 组件设计

### 1. 视图模板目录

在 `services/feishu-adapter/app/view_templates/` 增加小型模板目录。

每个模板定义：

- `template_id`
- `display_name`
- 面向员工的 `aliases`
- 来源 `table_name`
- 默认 `visible_fields`
- 默认 `filters`
- 默认 `sorts`
- 可变 `slots`

示例：

```json
{
  "template_id": "inventory_risk_view",
  "display_name": "库存风险视图",
  "aliases": ["高风险库存", "风险库存", "需要优先处理的库存"],
  "table_name": "Warehouse Inventory Snapshot",
  "visible_fields": ["SKU", "Warehouse", "Available", "Risk Level", "Recommendation"],
  "slots": {
    "risk_level": ["high", "medium", "low"],
    "warehouse": "optional",
    "available_lt": "optional"
  },
  "defaults": {
    "risk_level": "high"
  },
  "sorts": [{"field": "Available", "order": "asc"}]
}
```

### 2. 模板匹配器

第一版在 `feishu-adapter` 中做确定性 matcher。

它负责：

- 归一化中英文文本
- 匹配 `高风险库存`、`风险库存`、`缺货预警`、`库位异常` 等 alias
- 提取简单 slots，例如风险等级、仓库名/仓库代码、SKU、库存阈值
- 置信度低时返回 `matched=false`

这样不需要写大量脆弱的 if。大部分业务变化放在模板 alias 和 slot 定义里。

### 3. 视图渲染器

渲染器把：

```json
{
  "template_id": "inventory_risk_view",
  "slots": {
    "risk_level": "high",
    "warehouse": "wh_hk_1"
  }
}
```

转换成现有受控创建视图请求：

```json
{
  "table_name": "Warehouse Inventory Snapshot",
  "view_name": "香港仓高风险库存",
  "visible_fields": ["SKU", "Warehouse", "Available", "Risk Level", "Recommendation"],
  "filters": [
    {"field": "Risk Level", "operator": "is", "value": "high"},
    {"field": "Warehouse", "operator": "is", "value": "wh_hk_1"}
  ],
  "sorts": [{"field": "Available", "order": "asc"}]
}
```

调用飞书前，渲染器必须基于当前库存表 schema 校验所有字段。

### 4. API

新增：

```text
GET /warehouse/inventory-table/view-templates
POST /warehouse/inventory-table/views/from-template
```

`POST /views/from-template` 请求：

```json
{
  "message": "帮我建一个香港仓高风险库存视图",
  "view_name": "香港仓高风险库存"
}
```

返回：

```json
{
  "ok": true,
  "matched": true,
  "template_id": "inventory_risk_view",
  "slots": {
    "risk_level": "high",
    "warehouse": "wh_hk_1"
  },
  "view_id": "vew_xxx",
  "action": "created",
  "validated_plan": {
    "visible_fields": ["SKU", "Warehouse", "Available", "Risk Level", "Recommendation"],
    "filters": [
      {"field": "Risk Level", "operator": "is", "value": "high"}
    ],
    "sorts": [{"field": "Available", "order": "asc"}]
  }
}
```

如果没有匹配模板，返回：

```json
{
  "ok": false,
  "matched": false,
  "error": "unknown_view_template",
  "message": "未匹配到视图模板。可尝试：高风险库存、缺货预警、仓储异常。"
}
```

### 5. n8n Workflow 改造

更新 `Warehouse Workflow`：

```text
Normalize Inbound Message
-> Detect Warehouse View Template Request
-> Create Warehouse View From Template
-> Format Warehouse View Template Reply
```

未命中模板或非建视图请求继续进入 `Warehouse Agent`。

n8n Code node 只做宽泛意图检测，例如 `创建/生成/做一个 + 视图/看板/表格`，不解析字段、筛选或排序。

## 初始模板

第一版只做五个模板：

- `inventory_risk_view`：库存风险视图，支持 `risk_level`、`warehouse`、`available_lt`
- `low_stock_view`：缺货/低库存预警视图
- `warehouse_exception_view`：仓储异常视图
- `replenishment_candidate_view`：补货候选视图
- `fulfillment_block_view`：履约阻塞视图

这足够支撑可信 demo，同时不会变成模板爆炸。

## 面向员工的文档

增加双语使用文档，例如：

```text
你可以这样说：
- 帮我建一个高风险库存视图
- 创建一个香港仓缺货预警视图
- 生成今天的仓储异常看板

你不需要提供字段名或筛选条件。
```

文档只描述员工可以怎么说，不暴露实现细节。

## 错误处理

- 未匹配模板：返回可选模板建议。
- 模板歧义：用一句话要求员工确认。
- 未知仓库别名：要求员工从已知仓库中选择。
- schema 校验发现未知字段：调用飞书前失败。
- 飞书 API 失败：返回简洁错误，并在 run log 中保留调试细节。

## 测试计划

增加测试：

- 模板目录加载
- 中英文 alias 匹配
- 风险等级、仓库、SKU、阈值 slot 提取
- `template + slots` 渲染成受控 view plan
- 调用飞书前拒绝未知字段
- `POST /views/from-template` 成功和未知模板响应
- n8n workflow 结构：模板 fast path 存在，未命中请求才进入 `Warehouse Agent`

## 成功标准

员工发送：

```text
帮我建一个香港仓高风险库存视图
```

系统可以成功返回飞书视图结果，员工不需要知道字段、筛选语法或 API payload。

核心规则保持不变：自然语言只负责选择模板和填写 slots，后端负责 schema 校验和最终飞书 payload 生成。
