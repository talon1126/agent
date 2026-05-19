# 电商售后 Multi-agent Workflow 设计说明

日期：2026-05-19

## 目标

构建一个以作品集为优先级的 AI workflow 项目，用来展示真实企业自动化能力、n8n 编排能力、AI service 开发能力、Docker 部署能力和 AI 运维能力。

系统处理电商售后与库存协同事件。它应该像一个真实的企业内部业务流程，而不是一个简单的 prompt 演示。

主要受众：

- 评估 AI automation、AI operations 或 AI application engineering 能力的招聘方和 hiring manager。
- 想理解 n8n、mock 企业系统和模型服务如何协作的后续维护者。

## 已确认方向

第一个项目是电商售后与库存协同系统。

已确认选择：

- 项目目标：优先服务作品集和求职展示。
- 编排工具：以 n8n 为主。
- 企业系统集成：先使用 mock 系统，并明确预留真实 SaaS API 替换点。
- 交付方式：第一阶段优先 Docker 本地 demo，后续补充在线部署文档。
- AI 范围：综合型 MVP，覆盖路由、知识库建议、客服回复草稿、人工审批、日志和失败重放。
- 架构路线：n8n 编排 + 独立 AI service。

## 推荐架构

n8n 负责工作流编排、分支路由、重试、集成调用和审批流转。

AI service 负责面向模型的能力：

- 售后事件分类。
- 基于政策的处理建议。
- 结构化输出校验。
- 决策解释。
- 客服回复草稿生成。
- 基于本地政策和商品规则的检索。

mock 企业 API 用来模拟：

- 订单和客户历史。
- 库存和 SKU 风险。
- 物流和配送状态。
- 客服工单和内部通知。

这个边界能让项目比纯 n8n workflow 更像工程项目，同时仍然把 n8n 作为可见的自动化编排层。

## 系统组件

### n8n

n8n 是主工作流引擎。

职责：

- 通过 Webhook trigger 接收售后事件。
- 从 mock API 拉取相关业务上下文。
- 将事件路由到退款、物流、差评或库存 workflow。
- 调用 AI service 完成分类、政策推理和草稿生成。
- 处理重试和失败路径。
- 为高风险动作创建审批请求。
- 将运行元数据和决策摘要写入存储。

### AI Service

AI service 应该实现为一个小型 API 服务。第一版建议使用 FastAPI。

职责：

- 接收 n8n 传入的标准化事件 payload。
- 返回严格的 JSON 响应。
- 查询本地政策和商品知识。
- 生成建议动作、解释、置信度和回复草稿。
- 在返回给 n8n 前校验模型输出。
- 提供健康检查和诊断接口，方便部署检查。

### Mock 企业 API

mock API 提供确定性的本地企业上下文。

职责：

- 提供订单、客户、物流、库存和客服数据。
- 支持脚本化的成功和失败场景。
- 让 demo 不依赖外部 SaaS 账号即可运行。
- 明确展示未来如何替换为 Shopify、Zendesk、Slack、ERP、物流平台或表格系统。

### 存储

第一版可以通过 Docker Compose 使用 SQLite 或 Postgres。

需要存储的数据：

- 传入事件 payload。
- AI 决策和解释。
- 审批状态。
- workflow 运行历史。
- 失败记录和 replay 元数据。

SQLite 对第一版本地 demo 更简单。若实现成本可控，Postgres 对作品集更有说服力。

## Agent 职责

系统应使用小而可审计的 AI 能力模块，而不是模糊的“自主 agent”。

### Triage Agent

输入：

- 事件 payload。
- 客户和订单上下文。

输出：

- 事件类别。
- 优先级。
- 业务影响。
- 目标团队。
- 路由原因。

### Policy Agent

输入：

- 事件类别。
- 订单、客户和商品上下文。
- 相关政策文档或规则。

输出：

- 允许的动作。
- 不允许的动作。
- 是否需要审批。
- 政策引用或政策理由摘要。

### Inventory Risk Agent

输入：

- SKU。
- 当前库存。
- 待处理订单。
- 补货阈值。

输出：

- 风险等级。
- 建议的补货或替代动作。
- 目标内部团队。
- 解释。

### Response Draft Agent

输入：

- 事件上下文。
- 政策判断结果。
- 推荐动作。
- 客户等级和语气约束。

输出：

- 面向客户的回复草稿。
- 内部任务摘要。
- 必要时生成升级说明。

## Workflow 路径

MVP 应包含四条主要 workflow 路径。

### 退款请求

流程：

1. 接收退款请求。
2. 获取订单和客户数据。
3. 判断请求类型和优先级。
4. 检查退款政策。
5. 生成处理建议和客服回复草稿。
6. 对高金额、VIP、政策边界或低置信度场景要求人工审批。
7. 创建客服任务并写入审计记录。

### 物流延迟

流程：

1. 接收配送延迟事件或客户投诉。
2. 获取物流和 tracking 数据。
3. 判断严重程度。
4. 检查物流补偿政策。
5. 起草道歉回复和补偿建议。
6. 创建客服任务或审批请求。

### 差评处理

流程：

1. 接收公开差评或客户投诉。
2. 获取客户和订单上下文。
3. 判断品牌风险和紧急程度。
4. 检查政策和挽回选项。
5. 起草回复和内部升级摘要。
6. 对 VIP、公开渠道或高风险场景要求人工审批。

### 缺货风险

流程：

1. 由定时库存检查或 webhook 触发。
2. 获取 SKU、销售速度、待处理订单和库存阈值。
3. 判断断货风险。
4. 推荐补货、替代商品或运营提醒。
5. 通知采购或运营团队。

## 人工审批规则

系统必须在高风险建议被最终采纳前暂停并进入人工审核。

以下情况需要审批：

- 退款金额超过配置阈值。
- 客户是 VIP 或企业级客户。
- AI 置信度低于阈值。
- 回复是公开可见且影响品牌风险。
- 政策判断结果不明确。
- 推荐动作涉及财务补偿。

审批记录必须包含：

- 事件 ID。
- AI 推荐。
- 解释。
- 审批人决策。
- 最终动作。
- 时间戳。

## 数据模型

### Event Payload

字段：

- `event_id`
- `event_type`
- `source`
- `customer`
- `order`
- `sku`
- `shipment`
- `message`
- `created_at`

支持的 `event_type`：

- `refund_request`
- `logistics_delay`
- `bad_review`
- `low_stock`

### AI Decision Output

字段：

- `event_id`
- `category`
- `priority`
- `recommended_action`
- `requires_approval`
- `confidence`
- `explanation`
- `draft_response`
- `internal_task_summary`
- `policy_references`

AI service 必须在返回给 n8n 前校验这个结构。

## Mock API 范围

### Orders API

接口：

- `GET /orders/{id}`
- `GET /customers/{id}`
- `POST /refund-cases`

### Inventory API

接口：

- `GET /inventory/{sku}`
- `GET /substitutes/{sku}`
- `POST /reorder-alerts`

### Logistics API

接口：

- `GET /shipments/{id}`
- `GET /tracking/{id}`
- `POST /delivery-cases`

### Support API

接口：

- `POST /tickets`
- `POST /approval-requests`
- `POST /internal-notifications`

## Demo 事件

仓库应包含 6-8 个脚本化 demo 事件。

必须覆盖：

- 普通退款请求。
- 需要审批的高金额退款。
- 带补偿建议的物流延迟。
- 需要品牌风险审批的公开差评。
- 触发采购提醒的低库存风险。
- 需要人工复核的低置信度 AI 决策。
- mock API 失败，并进入 retry 和 dead-letter 路径。
- 失败事件 replay。

这些事件也应该作为自动化测试 fixture。

## AI Ops 要求

项目应清楚展示运维思维。

必备功能：

- 运行日志，包含 event ID、workflow ID、延迟、模型名称、估算 token 用量、结果和错误状态。
- 针对临时 mock API 故障的 retry 路径。
- 针对不可恢复 workflow 失败的 dead-letter 路径。
- 失败事件 replay 路径。
- AI 输出 schema validation。
- 置信度阈值 guardrail。
- 针对退款、VIP 客户和公开回复的高风险动作 guardrail。
- 审批审计记录。

后续可选功能：

- 简单运行历史 dashboard。
- 成本和延迟汇总。
- 云部署指南。
- 通过 Slack、Discord 或 email 集成告警。

## 交付计划

### 第一阶段：本地 Docker Demo

第一个完整版本应通过 Docker Compose 运行。

服务：

- n8n。
- AI service。
- Mock API service。
- Storage service。

预期本地 demo：

1. 启动服务。
2. 导入 n8n workflow。
3. 发送脚本化 demo 事件。
4. 观察 workflow 路由和 AI 决策。
5. 审批一个高风险案例。
6. 查看运行日志并 replay 一个失败案例。

### 第二阶段：作品集打磨

交付物：

- 英文 README。
- 架构图。
- n8n workflow 截图。
- demo 事件示例。
- API contract 示例。
- 本地安装指南。
- 测试指南。
- demo 视频脚本。

### 第三阶段：可选在线部署

本地 demo 稳定后，再补充小型云主机或平台服务的部署指南。

第一版作品集不应依赖公开在线部署才有价值。

## 测试策略

测试应聚焦能证明系统可靠性的行为。

AI service 测试：

- 输出 schema validation。
- 基于 fixture 数据的事件分类行为。
- 审批阈值行为。
- 高风险动作 guardrail。
- 政策检索 fallback 行为。

Mock API 测试：

- 预期 fixture 响应。
- 失败模拟接口。

Workflow 验证：

- n8n workflow export 可以导入。
- 脚本化事件进入预期 workflow 路径。
- 失败路径创建 retry 或 dead-letter 记录。
- 审批路径记录预期审计字段。

## 仓库结构

计划结构：

```text
.
|-- docker-compose.yml
|-- README.md
|-- docs/
|   |-- architecture.md
|   |-- demo-script.md
|   `-- superpowers/specs/
|-- n8n/
|   `-- workflows/
|-- services/
|   |-- ai-service/
|   `-- mock-api/
|-- fixtures/
|   |-- events/
|   `-- policies/
`-- tests/
```

## Implementation Plan 阶段待定事项

以下决策可以在 implementation plan 阶段确定：

- mock API service 使用 FastAPI 还是 Node。
- 第一版存储使用 SQLite 还是 Postgres。
- 是否提供本地 LLM fallback，或要求 OpenAI-compatible API key。
- 审批只通过 mock API 模拟，还是同时使用 n8n forms。

默认建议：

- AI service 使用 FastAPI。
- mock API 默认也使用 FastAPI，除非后续有明确理由拆技术栈。
- 如果 Docker Compose 配置足够简单，使用 Postgres；否则第一版先用 SQLite。
- 通过环境变量配置 OpenAI-compatible API key，并为测试提供 deterministic fake mode。

