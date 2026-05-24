# Agent 上下文摘要

后续在这个仓库工作时，先读这个文件。它故意保持很短，避免每次小任务都重新扫描完整项目。

## 项目根目录

- 用户视角根目录：`D:\Project\agent`
- 当前实现 worktree：`D:\Project\agent\.worktrees\after-sales-implementation`
- 当前工作分支：`after-sales-implementation`
- 远程仓库：`https://github.com/talon1126/agent.git`

## 运行结构

这是一个 Docker-first 的内部电商运营 Copilot 项目。

- `n8n` 负责 workflow 编排、部门 webhook 路由，以及服务之间的调用。
- `feishu-adapter` 负责飞书/Lark 协议处理。它支持 `FEISHU_BOTS_JSON` 多机器人网关模式，默认使用长连接模式，归一化收到的消息，把每个 bot 转发到对应部门 n8n webhook，按 `bot_name + message_id` 去重，并把回复发回飞书。
- `ai-service` 负责后端 AI 逻辑，要求可以脱离 n8n 单独测试。当前包含确定性决策和消息处理入口。
- `mock-api` 模拟企业内部系统：订单、客户、物流、库存、仓储作业、审批、工单、内部通知、运行日志、dead letter 和 replay。
- `feishu-adapter` 可以把结构化消息 run log 写到 `FEISHU_RUN_LOG_URL`；Docker 默认目标是 `mock-api /run-logs`。
- `postgres` 是运维存储目标。配置 `DATABASE_URL` 后，`mock-api` 现在会从 fixtures 创建并 seed 仓储“批次 + 库位”模型（`warehouses`、`storage_locations`、`categories`、`items`、`inventory_batches`）；部分动作记录仍保留在 mock endpoint 内存中。
- 配置 `DATABASE_URL` 后，`ai-service` 会在 Postgres 中创建 `session_state` 和 `user_profile`。fast path 会把 `last_order_id` 存到 `session_state`，如果有 `sender_id`，也会同步到 `user_profile.profile`。
- 当前推荐聊天架构是一个 Feishu Gateway Adapter 加多个独立部门 workflow：`Customer Support Workflow`、`Warehouse Workflow`、`Procurement Workflow` 和 `Operations Workflow`。
- `chat-parent-son-agent.json` 仍作为历史兼容文件保留，但主内部聊天链路应使用部门 workflow，而不是 Parent -> son 分发。

## 关键入口

- 飞书部门聊天链路：部门机器人 -> `feishu-adapter` -> 部门 n8n webhook -> 部门 Agent -> tool/API -> 飞书回复。
- 飞书网关诊断：`feishu-adapter` 的 `GET /health/details` 会展示 bot 配置、listener 数量、已处理消息数量和 run-log 状态，但不暴露 secret。
- 仓储库存表格创建、同步和视图工具：`feishu-adapter` 的 `POST /warehouse/inventory-table/provision` 会在已有飞书多维表格 app/base 里创建或复用固定 schema 的数据表；`POST /warehouse/inventory-table/sync` 会在需要时自动建表，并基于 `mock-api /warehouse/inventory/{item_id}` 发布单个商品的单向快照；`POST /warehouse/inventory-table/sync/filter` 可以按仓库、库位、分类、商品或临期风险同步批次记录；`GET /warehouse/inventory-table/schema` 和 `POST /warehouse/inventory-table/views/from-template` 让 Warehouse Agent 可以基于自然语言模板和校验后的字段计划创建受控飞书 grid 视图。
- 仓储视图模板构建器：员工可以用“高风险库存”“缺货预警”等自然语言创建视图。`feishu-adapter` 会把消息映射为 `template + slots`，校验 schema，再调用受控视图创建接口。
- fast path 链路：飞书 -> `feishu-adapter` -> `n8n /webhook/chat-agent-inbound` -> `ai-service /after-sales/fast-path` -> 飞书回复。如果 fast path 拒绝处理，workflow 会回退到 Parent Agent。
- 部门 workflow 导出：`n8n/workflows/customer-support-workflow.json`、`n8n/workflows/warehouse-workflow.json`、`n8n/workflows/procurement-workflow.json` 和 `n8n/workflows/operations-workflow.json`
- Parent/son workflow 导出：`n8n/workflows/chat-parent-son-agent.json` 是历史兼容文件。
- Message-agent workflow 导出：`n8n/workflows/message-agent.json`
- Event workflow 导出：`n8n/workflows/ecommerce-after-sales.json`
- AI 消息入口：`services/ai-service/app/main.py` 中的 `POST /message/handle`
- AI 决策入口：`services/ai-service/app/main.py` 中的 `POST /decide`
- 订单状态工具代码：`services/ai-service/app/order_status_tool.py`
- n8n 客服工具：`n8n/workflows/customer-support-workflow.json` 中的 `order_status_tool` 和 `policy_search_tool`
- n8n 仓储工具：`n8n/workflows/warehouse-workflow.json` 中的 `warehouse_inventory_tool`、`warehouse_exception_tool`、`warehouse_fulfillment_tool`、`warehouse_inventory_table_provision_tool`、`warehouse_inventory_table_sync_tool`、`warehouse_table_schema_tool` 和 `warehouse_view_create_tool`
- n8n 采购和运营工具：分别位于自己的部门 workflow 中的 `procurement_mock_tool` 和 `operations_mock_tool`
- n8n memory 节点：`Parent Postgres Chat Memory` 和 `Customer Support Postgres Chat Memory`
- Parent 和 son memory 可以共用同一张物理表，但 `sessionKey` 必须分别加命名空间（`parent:` 和 `customer_support:`），避免跨 Agent 上下文污染。
- n8n 政策 RAG 工具：`n8n/workflows/chat-parent-son-agent.json` 中的 `policy_search_tool`
- 政策检索 API：`services/mock-api/app/main.py` 中的 `POST /policies/search`
- 政策 RAG eval 用例：`fixtures/evals/policy_rag_eval.json`

## ai-service 结构

- `services/ai-service/app/main.py`：FastAPI app 和 HTTP endpoint。
- `services/ai-service/app/message_agent.py`：确定性消息意图处理、订单号提取、音频转写文本处理、订单状态工具调用。
- `services/ai-service/app/order_status_tool.py`：调用 `mock-api /orders/{order_id}` 和 `/shipments/{shipment_id}`，返回结构化摘要。
- `services/ai-service/app/decision_engine.py`：售后事件的确定性决策规则。
- `services/ai-service/app/schemas.py`：事件决策 request/response schema。
- `services/ai-service/app/message_schemas.py`：message-agent request/response schema。
- `services/ai-service/app/transcription.py`：音频转写 adapter 边界，包含 mock 和 Qwen-ready 模式。

## mock-api 结构

- `services/mock-api/app/main.py`：FastAPI mock 企业 API。
- `services/mock-api/app/store.py`：fixture 加载 helper。
- `services/mock-api/app/warehouse_store.py`：Postgres-first 仓储 repository，负责创建仓储“批次 + 库位”库存模型、写入中文表/字段注释，并从 fixtures seed 数据。
- `fixtures/data/orders.json`：订单 fixture 数据。
- `fixtures/data/customers.json`：客户 fixture 数据。
- `fixtures/data/shipments.json`：物流 fixture 数据。
- `fixtures/data/inventory.json`：旧的库存 fixture，仍供非仓储 mock endpoint 使用。
- `fixtures/data/warehouses.json`：仓库主数据，例如深圳仓和香港仓。
- `fixtures/data/storage_locations.json`：仓库库位数据，例如 A1、B1、C1 等具体存储位置。
- `fixtures/data/categories.json`：商品分类数据，例如纸品、乳制品、饮料和家清。
- `fixtures/data/items.json`：商品主数据，例如 `item_vinda_tissue` 等可售商品。
- `fixtures/data/inventory_batches.json`：批次级库存事实，按仓库、库位、商品、批次号、数量、保质期和存储状态记录。
- `fixtures/policies/after_sales_policy.md` 和 `fixtures/policies/after_sales_policy.zh.md`：当前售后政策文档，包含 `REFUND-001` 等稳定条款 ID。

## 文档结构

- `README.md` 和 `README.zh.md`：顶层使用说明和 workflow 导入说明。
- `docs/architecture.md` 和 `docs/architecture.zh.md`：服务边界和架构说明。
- `docs/local-runbook.md` 和 `docs/local-runbook.zh.md`：本地 Docker、n8n、飞书验证步骤。
- `docs/n8n-workflow-contract.md` 和 `docs/n8n-workflow-contract.zh.md`：workflow payload 契约。
- `docs/superpowers/specs/`：设计 spec。英文和中文版本一起维护。
- `docs/superpowers/plans/`：实现 plan。英文和中文版本一起维护。

## 当前设计约束

- 飞书协议处理保留在 `feishu-adapter`。
- 编排保留在 n8n。
- 模型相关逻辑和确定性可测试行为保留在 `ai-service`。
- 企业 API 模拟保留在 `mock-api`。
- 仓储事实数据必须通过 `mock-api` / 未来的 warehouse-service API 暴露。不要让 n8n 或 `feishu-adapter` 直接读取仓储 PostgreSQL 表。
- 使用 n8n Postgres Chat Memory 处理“这个订单”这类对话指代。
- 使用 `session_state` 保存需要跨 `ai-service` 重启保留的短期后端状态，例如 fast path 的 `last_order_id`。
- 使用 `user_profile` 保存长期用户级事实、未来摘要和偏好；保持精简，不要把完整聊天记录放进去。
- 主内部聊天链路使用部门飞书机器人和部门 workflow；除非为了兼容旧链路，不要把新业务功能继续加到 Parent/Son 图里。
- fast path 只有在同一 session 已经记住 `last_order_id` 时，才可以处理“我怎么退款”这类没有显式订单引用的追问；否则必须拒绝处理并回退到 Parent Agent。
- 使用 `policy_search_tool` 和 `/policies/search` 处理需要 `source_file`、`section`、`clause_id` 元数据的公司政策回答。
- Warehouse Agent 负责库存可用性、仓库库位、批次库存、临期风险、仓储异常和履约风险问题。
- 使用 `warehouse_inventory_tool` 和 `/warehouse/inventory/{item_id}` 查询商品库存、已预留数量、仓库、库位、分类、批次、保质期、存储状态和风险。
- 使用 `warehouse_exception_tool` 和 `/warehouse/exceptions/search` 处理库存差异、待上架、破损、找不到库位和拣货延迟问题。
- 使用 `warehouse_fulfillment_tool` 和 `/warehouse/fulfillment/check` 判断能否发货、履约阻塞原因和下一步仓储动作。
- 只有用户明确要求创建、初始化或配置飞书库存表时，才使用 `warehouse_inventory_table_provision_tool`。它只会在已有多维表格 app/base 里创建或复用数据表，并为风险和状态创建带颜色的单选字段，不会创建 base，也不会创建库存主数据源。
- 只有用户明确要求同步、导出、发布或展示飞书表格快照时，才使用 `warehouse_inventory_table_sync_tool`。它可以按 `item_id` 同步，也可以按仓库、库位、分类、临期风险等过滤条件同步。如果没有配置 table id，后端可以先自动创建或复用表格再同步。飞书表格是 read model，不是库存主数据源。
- 创建飞书库存视图前，必须先使用 `warehouse_table_schema_tool`。它返回真实字段名、字段类型、单选项颜色和已有视图。
- 只有 schema discovery 之后，才使用 `warehouse_view_create_tool`。它的输入必须是包含 `view_name`、`visible_fields`、`filters` 和 `sorts` 的 JSON；后端会在调用飞书前拒绝不存在的字段。MVP 只创建或复用 grid 视图并返回校验后的计划，不允许 Agent 直接执行飞书 CLI/API 命令。
- `Procurement Agent` 和 `Operations Agent` 在自己的部门 workflow 中使用确定性 mock endpoint。
- 不要提交 `.env`，不要打印 secrets。
- 每新增一份英文 Markdown 文档，都要同时新增中文 `.zh.md` 版本。
- 优先完成 Docker-first 本地验证，再考虑云端部署。

## 常用验证

从 `D:\Project\agent\.worktrees\after-sales-implementation` 执行：

```powershell
pytest services\ai-service\tests -v
pytest services\mock-api\tests -v
pytest services\feishu-adapter\tests -v
pytest tests\test_department_workflows.py -v
pytest tests\test_chat_parent_son_workflow.py -v
docker compose config --quiet
docker compose ps
```

常用 smoke 路径：

- n8n 部门 chat webhook：`http://localhost:5678/webhook/customer-support-inbound`、`http://localhost:5678/webhook/warehouse-inbound`、`http://localhost:5678/webhook/procurement-inbound` 和 `http://localhost:5678/webhook/operations-inbound`
- ai-service 本地端口：`http://localhost:8001`
- mock-api 本地端口：`http://localhost:8002`
- feishu-adapter 本地端口：`http://localhost:8010`
- feishu-adapter 诊断端点：`http://localhost:8010/health/details`
- 仓储库存表格创建：`http://localhost:8010/warehouse/inventory-table/provision`
- 仓储库存表格同步：`http://localhost:8010/warehouse/inventory-table/sync`
- 仓储库存表格 schema：`http://localhost:8010/warehouse/inventory-table/schema`
- 仓储库存视图创建：`http://localhost:8010/warehouse/inventory-table/views/create`

`ord_100` 的预期订单 smoke 片段：`Order ord_100 is delivered. Shipment status is delivered.`
