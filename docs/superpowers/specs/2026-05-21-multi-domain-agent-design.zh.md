# 多业务域 Agent Workflow 设计

日期：2026-05-21

## 目标

把当前“飞书 -> n8n -> Parent/Son Agent”的工作流，从单一售后专员扩展成更贴近企业内部业务的多 Agent 演示。

第一版要可运行，但范围要控制住：

- 飞书协议处理继续放在 `feishu-adapter`。
- 编排、可视化布局、Parent/Son 分发继续放在 n8n。
- 确定性业务工具继续放在 `ai-service` 或 `mock-api`。
- 先从 n8n live workflow 导出版本开始改，保护你已经手动调整过的画布间距。
- Agent 节点名和工具名统一使用英文。

## 当前基线

当前 live workflow 已经从 n8n workflow ID `wechat-qwen-agent-template` 导出到：

`n8n/workflows/chat-parent-son-agent.live-2026-05-21.json`

n8n 当前有两个同名 `Wechat Gateway to Qwen Agent` workflow，所以后续操作必须按 workflow ID `wechat-qwen-agent-template`，不能只看显示名称。

## Agent 集合

### Parent Agent

职责：只做路由分发。

Parent Agent 判断应该调用哪个专业 agent，不直接回答需要内部工具的业务问题。

路由目标：

- `Customer Support Agent`
- `Warehouse Agent`
- `Procurement Agent`
- `Operations Agent`
- 不保留 `Weather Agent`；天气能力从当前企业业务 workflow 中移除
- 简单测试或 echo 工具只在用户明确要求测试链路时调用

### Customer Support Agent

这个 agent 替代旧的 `after_sales_agent` 角色。

职责：

- 查询订单状态
- 回答客服问题时查询物流状态
- 处理退款、退货、换货、补偿和投诉
- 检索售后政策，并返回元数据引用
- 处理“这个订单”“上一单”这类短期上下文指代

状态：

- n8n Postgres Chat Memory 使用 `customer_support:` 命名空间。
- `ai-service` 的 `session_state` 继续保存 `last_order_id` 这类可跨重启保留的后端短期状态。
- `user_profile` 保留给精简用户事实和未来摘要，不存完整聊天记录。

### Warehouse Agent

这是第一批要完整接入的新增专业 agent。

职责：

- 按 SKU 查询库存
- 查询发货或履约状态
- 汇总仓储异常
- 给客服或运营提供可执行的简短回复

第一版工具：

- `inventory_status_tool`：调用后端 API，返回 SKU 可用库存、待处理订单、补货阈值。
- `shipment_status_tool`：如果实现成本低，可以复用已有订单/物流数据。

第一版只接 `mock-api`，不接真实 ERP 或 WMS。

### Procurement Agent

第一版先做占位专业 agent。

职责：

- 识别补货、供应商、采购单、交期类问题
- 返回结构化 mock 结果
- 在没有真实采购系统时明确说明未接入

第一版工具：

- `procurement_mock_tool`

### Operations Agent

第一版先做占位专业 agent。

职责：

- 汇总运营异常
- 生成每日或每周运营摘要
- 聚合客服、仓储、采购的跨域信号
- 返回结构化 mock 输出，为后续报表或 dashboard 做准备

第一版工具：

- `operations_mock_tool`

## Workflow 形态

入口链路不变：

飞书 -> `feishu-adapter` -> n8n `/webhook/chat-agent-inbound` -> fast path check -> Parent Agent -> specialist agent -> tool/API -> formatted webhook reply -> 飞书

Customer Support 的明确订单/退款问题仍然优先走 Parent 前面的 fast path。内部可以暂时继续使用现有 `/after-sales/fast-path` endpoint，但 n8n 的用户可见节点名逐步改成 `Customer Support`。endpoint 重命名可以后续做兼容迁移。

n8n 画布应按专业 agent 分 lane 排布：

- Parent 和入口处理靠左或靠上
- Customer Support lane
- Warehouse lane
- Procurement lane
- Operations lane
- 共享模型、memory、tool 节点尽量靠近所属 agent

## Prompt 规则

Parent prompt：

- 对用户回复使用中文。
- 内部工具和 agent 名使用英文。
- 客服、订单、退款、退货、换货、投诉、政策类问题路由到 `Customer Support Agent`。
- 库存、仓库、履约、现货、发货、拣货、打包、物流作业类问题路由到 `Warehouse Agent`。
- 供应商、采购、补货、交期、采购单类问题路由到 `Procurement Agent`。
- 日报、指标、异常汇总、运营总结、跨域分析类问题路由到 `Operations Agent`。
- 如果任务跨多个业务域，第一版先调用最具体的一个 agent 并汇总结果；多 agent 串联后续再加。

专业 agent prompt：

- 不编造内部系统数据。
- 需要数据时必须先调用对应工具。
- 返回适合飞书展示的简洁中文。
- 使用政策或知识库内容时必须带 source metadata。
- 如果后端只是 mock 或尚未接真实系统，要明确说明。

## 数据和 API 设计

第一版保持简单：

- `Warehouse Agent` 优先复用现有 `fixtures/data/inventory.json`。
- 尽量复用已有订单和物流 fixture。
- 只有现有 endpoint 不够时才新增窄接口。
- 采购和运营工具保持确定性、可检查。

候选后端接口：

- `GET /inventory/{sku}` 或复用现有库存查询接口
- `GET /shipments/{shipment_id}`
- `POST /procurement/mock`
- `POST /operations/summary/mock`

## 测试

导入 workflow 前必须有这些测试：

- workflow JSON 包含所有英文 agent 名
- Parent Agent prompt 能路由到正确 agent 名
- `Customer Support Agent` 保留政策检索和订单状态工具
- `Warehouse Agent` 至少有一个后端 API 工具
- `Procurement Agent` 和 `Operations Agent` 作为占位 agent 存在，并有 mock tool
- memory namespace 不应在多个 agent 之间无区分混用
- workflow 连接保持“先 Parent Agent，再 specialist agent”的路径

运行时 smoke tests：

- 客服：`帮我查一下订单 ord_100`
- 仓储：`sku_bag_1 还有多少库存`
- 采购：`sku_bag_1 需要补货吗`
- 运营：`帮我总结今天的运营异常`

## 非目标

- 本阶段不接真实 ERP、WMS、OMS 或采购系统。
- 本阶段不做递归式 multi-agent chaining。
- 不做大规模 UI 重设计，只保护你已有布局，并新增清晰 lane。
- 不迁移 Feishu adapter 行为。
- 本阶段不保留天气专员。

## 实现建议

分两小步实现：

1. 先把 `Customer Support Agent` 重命名并加固，再新增一个真正调用 mock-api 的 `Warehouse Agent`。
2. 再新增 `Procurement Agent` 和 `Operations Agent`，先作为可路由的确定性 mock 占位。

这样既能保持演示有效，又不会一次性修改太大的 n8n workflow。
