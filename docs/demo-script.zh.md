# Demo 脚本

这份脚本适合用于作品集面试或录屏，时长控制在 5-7 分钟。项目定位是 **Internal Ecommerce Operations Copilot**，也就是面向内部员工的电商运营助手，不是外部客户客服机器人。

## 1. 开场说明

“这个项目是一个 Docker-first 的内部电商运营 Copilot。员工通过不同部门的飞书机器人提问。一个 Feishu Gateway Adapter 负责归一化消息，并把每个 bot 路由到自己的 n8n workflow。n8n 负责编排部门流程、调用 AI 和后端工具，再把结果回复到飞书。后端服务都是普通 FastAPI 服务，所以 AI 逻辑和企业 API 可以脱离 n8n 单独测试。”

展示：

- `AGENTS.md`
- `docker-compose.yml`
- `n8n/workflows/customer-support-workflow.json`
- `services/feishu-adapter/app/main.py`
- `services/mock-api/app/main.py`

## 2. 启动和检查服务

```powershell
docker compose up --build -d
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health
Invoke-RestMethod http://localhost:8010/health/details | ConvertTo-Json -Depth 10
```

讲解：

“adapter 的详细健康检查会展示 bot 名称、webhook 配置、listener 数量和 run log 状态，但不会暴露 app secret。对于多 bot Agent 系统来说，这类运行可见性是上线前必须具备的。”

## 3. 客服 Demo

给 Customer Support bot 发送：

```text
帮我查询订单 ord_100
这个订单怎么退款？
```

预期行为：

- 第一条消息查询订单状态。
- 第二条追问可以通过 memory/session state 理解“这个订单”。
- 退款回答应该引用政策元数据，例如 `fixtures/policies/after_sales_policy.zh.md`、`section=退款` 和 `REFUND-001` 这类 `clause_id`。

讲解：

“这里展示的是短期对话记忆和可审计的政策 RAG。Agent 不能编造退款规则，必须指出公司文档来源和条款。”

## 4. 仓储 Demo

给 Warehouse bot 发送，或在共享群里 @ 它：

```text
@Warehouse 查询 sku_bag_1 的库存、库位和履约风险
@Warehouse 把 sku_bag_1 的库存快照同步到飞书表格
```

预期行为：

- 只有 Warehouse workflow 执行。
- 仓储工具返回库存、库位、未关闭异常和风险等级。
- 明确同步请求会调用 `warehouse_inventory_table_sync_tool`，返回 `created` 或 `updated`。
- 其他部门 workflow 不执行。

讲解：

“这个项目遇到过真实的多 bot 问题：多个机器人在同一个飞书群里时，一条消息可能被所有 bot 收到。现在 gateway 会通过 mention 和 bot open_id 过滤，避免一条群消息触发所有 workflow。对于库存可见性，Agent 可以把库存发布成单向飞书表格快照。这个表格是 read model，不是库存主数据源。”

## 5. 采购 Demo

给 Procurement bot 发送：

```text
SKU sku_bag_1 是否需要补货？给出采购建议
```

预期行为：

- Procurement workflow 调用采购工具。
- 回复根据库存和待履约订单判断是否需要创建采购申请。

讲解：

“采购和仓储是分开的。仓储负责库存和履约风险，采购负责补货建议。这种边界更接近真实企业部门职责。”

## 6. 运营 Demo

给 Operations bot 发送：

```text
生成今天的运营日报，包含订单、库存、采购和异常摘要
```

预期行为：

- Operations workflow 调用运营摘要工具。
- 回复跨部门摘要和下一步动作。

讲解：

“运营是跨部门汇总，但仍然有自己的 bot 和 workflow。我没有继续做一个全局 Parent Agent，因为企业内部部门通常需要清晰的权限、归属和审计边界。”

## 7. AI Ops 证据

展示：

```powershell
Invoke-RestMethod http://localhost:8002/run-logs | ConvertTo-Json -Depth 10
Invoke-RestMethod http://localhost:8002/dead-letter | ConvertTo-Json -Depth 10
```

讲解：

“每条飞书消息都可以写入结构化 run log，包括 message id、bot name、workflow URL、耗时、状态、错误和 workflow 返回的 tool calls。这样项目不是一个聊天玩具，而是一个能被排查和运营的系统。”

## 8. 收尾

“重点不是我用了 n8n 或 Qwen，而是这个架构：协议网关、workflow 编排、可测试的 AI service、可替换的企业 API、memory 边界、RAG 引用、run log、失败恢复和 CI。这才是企业里可以运行和排查的 Agent workflow。”
