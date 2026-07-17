<!-- synced-from: DEV_SPEC.md -->
<!-- reference: 任务计划与状态 -->

## 6. 项目排期

状态标记：`[ ]` 未开始，`[~]` 进行中，`[✔]` 已完成。

### 6.1 阶段总览表

| 阶段 | 阶段标题 | 目标 | 状态 |
| --- | --- | --- | --- |
| 阶段 A | Project Foundation | 建立本地运行、共享服务、测试入口和基础规范 | [✔] |
| 阶段 B | Warehouse Workflow | 完成仓储库存、履约、采购需求和仓储主链路 | [✔] |
| 阶段 C | Procurement Workflow | 完成采购单审批、采购单查询和采购主链路 | [✔] |
| 阶段 D | Delivery Workflow | 完成物流查询、异常和 case 闭环 | [✔] |
| 阶段 E | Operations Workflow | 完成跨领域只读摘要和运营建议闭环 | [✔] |
| 阶段 F | 电商项目 | 完成 TalonMart 商品、Departments 导购、购物车、秒杀、排行榜和前端体验 | [✔] |
| 阶段 G | AImodel | 完成前端 AI 聊天、商品工具、会话记忆、AImodel Intent Router、受控联网搜索和 RAG MCP 集成 | [✔] |
| 阶段 H | 飞书应用与协作后台 | 完成 feishu-adapter、多维表格 read model、主动通知和飞书应用搭建 | [~] |
| 阶段 I | Quality And Delivery | 完成全量质量门禁、演示脚本和部署检查 | [~] |
| 阶段 J | RPA Data Operations | 建立通用影刀网页导出 CSV 与 pandas processor 能力，并完成京东商品首个端到端实现 | [ ] |

### 6.2 交付里程碑

| 阶段 | 项目当前位置 | 可用功能 | 验证方式 | 下一阶段入口 | 完成日期 |
| --- | --- | --- | --- | --- | --- |
| 阶段 A | 基础服务可运行 | Docker Compose、fixtures、Python/Node 测试入口 | `docker compose -p after-sales-implementation config --quiet` | Warehouse Workflow |  |
| 阶段 B | 仓储主链路可演示 | 库存查询、履约风险、待审批采购单创建 | `uv run --project services/mock-api pytest services\mock-api\tests\test_warehouse_store.py -q` | Procurement Workflow |  |
| 阶段 C | 采购主链路可演示 | 采购单审批、采购单驳回、采购单查询 | `uv run --project services/mock-api pytest services\mock-api\tests\test_procurement_router_structure.py -q` | Delivery Workflow |  |
| 阶段 D | 物流主链路可演示 | 物流状态、异常查询、case 创建 | `uv run --project services/mock-api pytest services\mock-api\tests\test_delivery_router_structure.py -q` | Operations Workflow |  |
| 阶段 E | 运营只读汇总可用 | 异常摘要、风险汇总、后续动作建议 | `uv run --project services/mock-api pytest tests\test_department_workflows.py -q` | 电商项目 |  |
| 阶段 F | 电商项目可用 | 商品、Departments 导购、详情、购物车、秒杀、排行榜、AI 模式 | `pnpm --dir apps/talonmart-web test:unit` | AImodel | 2026-06-17 |
| 阶段 G | AImodel 持续增强 | 流式聊天、工具调用、会话记忆、AImodel Intent Router、受控联网搜索、RAG MCP | `uv run --project services/ai-service pytest services\ai-service\tests -q` | 飞书应用与协作后台 | 2026-06-29 |
| 阶段 H | 飞书协作后台可演进 | 飞书机器人、表格同步、主动通知、运营驾驶舱首页、业务操作页、订单明细、商品、秒杀 read model 分页同步 | `uv run --project services/feishu-adapter pytest services\feishu-adapter\tests -q` | Quality And Delivery |  |
| 阶段 I | 质量门禁持续完善 | 全量验证、演示检查、部署说明 | 全量测试矩阵 | RPA Data Operations |  |
| 阶段 J | 通用网页数据文件流水线可扩展 | 影刀通用模板、pandas processor 框架、京东商品原始/标准/失败 CSV、批次清单和扩展指南 | `uv run --project services/data-ops pytest services\data-ops\tests -q` | 新站点实现或数据入库设计 |  |

### 6.3 任务跟踪表

#### 阶段 A：Project Foundation

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| A1 | 建立服务目录和本地编排 | [✔] |  | apps、services、n8n、fixtures、tests |
| A2 | 配置 Docker Compose | [✔] |  | postgres、redis、ai-service、mock-api、feishu-adapter、n8n |
| A3 | 建立 fixtures 和环境变量模板 | [✔] |  | `.env.example`、fixtures/data、fixtures/events |
| A4 | 建立 uv 驱动的 Python 测试入口 | [✔] |  | Python 服务使用 `uv run --project` |
| A5 | 建立根项目 workflow 测试 | [✔] |  | `tests/test_department_workflows.py` |

#### 阶段 B：Warehouse Workflow

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| B1 | 建立仓储数据模型和 repository | [✔] |  | 仓库、库位、库存余额 |
| B2 | 实现库存查询与异常查询 API | [✔] |  | warehouse inventory |
| B3 | 实现履约风险和订单确认后库存扣减 | [✔] |  | FEFO、整单同仓、员工确认扣减 |
| B4 | 实现待审批采购单创建 | [✔] |  | purchase_orders.approval_status |
| B5 | 实现 Warehouse n8n Workflow | [✔] |  | warehouse-workflow.json |
| B6 | 实现仓储测试与回归门禁 | [✔] |  | warehouse tests |

#### 阶段 C：Procurement Workflow

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| C1 | 建立采购单数据模型和状态规则 | [✔] |  | purchase_orders、approval_status |
| C2 | 实现采购单查询、批准和驳回 | [✔] |  | purchase_orders.py |
| C3 | 实现采购单批量审批 | [✔] |  | service.py |
| C4 | 实现 Procurement n8n Workflow | [✔] |  | procurement-workflow.json |
| C5 | 实现采购测试与回归门禁 | [✔] |  | procurement router tests |

#### 阶段 D：Delivery Workflow

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| D1 | 建立物流供应商与订单物流字段 | [✔] |  | delivery_providers、orders |
| D2 | 实现物流状态查询 API | [✔] |  | delivery status lookup |
| D3 | 实现物流异常搜索 API | [✔] |  | delivery exceptions |
| D4 | 实现物流跟进 case API | [✔] |  | delivery cases |
| D5 | 实现 Delivery n8n Workflow | [✔] |  | delivery-workflow.json |
| D6 | 实现物流测试与回归门禁 | [✔] |  | delivery router tests |

#### 阶段 E：Operations Workflow

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| E1 | 定义运营只读边界 | [✔] |  | 不直接修改库存/采购/物流事实 |
| E2 | 实现运营摘要 mock API | [✔] |  | `/operations/summary/mock` |
| E3 | 实现 Operations n8n Workflow | [✔] |  | operations-workflow.json |
| E4 | 实现跨领域异常摘要输出 | [✔] |  | incident summary |
| E5 | 实现运营 workflow 测试 | [✔] |  | department workflow tests |

#### 阶段 F：电商项目

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| F1 | 实现首页、搜索与 Departments 导购 | [✔] | 2026-06-14 | HomeView、SearchView、DepartmentCategoryView |
| F2 | 实现商品详情与评论 | [✔] |  | ProductDetailView |
| F3 | 实现购物车页面 | [✔] |  | CartView |
| F4 | 实现秒杀前端接口 | [✔] |  | flashSaleApi |
| F5 | 实现 AI 模式浮动入口和聊天面板 | [✔] |  | AiModeSidebar、AiModeChatPanel |
| F6 | 实现前端 API client 和类型 | [✔] |  | services、types |
| F7 | 实现前端单元/E2E 测试 | [✔] |  | Vitest、Playwright |
| F8 | 实现分类排行榜和热门商品展示 | [✔] | 2026-06-17 | PostgreSQL facts、Redis ZSET、HomeView、DepartmentCategoryView、ProductDetailView |

#### 阶段 G：AImodel

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| G1 | 建立 AImodel Router 和 schemas | [✔] |  | router.py、schemas.py |
| G2 | 实现 conversation/message/user_memory | [✔] |  | memory.py |
| G3 | 实现商品搜索和详情工具 | [✔] |  | tools.py |
| G4 | 实现 RAG MCP 客户端 | [✔] |  | PersistentMcpRagKnowledgeClient |
| G5 | 实现 LangChain Agent 编排 | [✔] |  | service.py |
| G6 | 实现 SSE 流式响应和输出清洗 | [✔] |  | hide tool result |
| G7 | 实现 message_query_trace 关联 | [✔] |  | trace id mapping |
| G8 | 实现 Tavily 联网搜索工具 | [✔] | 2026-06-23 | web search tool、TAVILY_API_KEY |
| G9 | 实现 AImodel 测试与回归门禁 | [✔] |  | ai-service tests |
| G10 | 实现 AImodel Intent Router | [✔] | 2026-06-29 | 树状意图配置、action/collection 路由 |
| G11 | 实现 LangChain Middleware Agent Trace | [✔] | 2026-06-29 | intent/tool/message trace |

#### 阶段 H：飞书应用与协作后台

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| H1 | 建立 feishu-adapter 基础能力 | [✔] | 2026-06-18 | 长连接、多机器人、事件解析、n8n 转发、回复、run log、table_id-first 表定位、table_id 持久记忆、分页同步和图片上传基础能力 |
| H2 | 实现库存余额和库存流水飞书表同步 | [✔] |  | Inventory Balances / Movements |
| H3 | 实现采购到仓库存同步和采购单飞书表同步 | [✔] |  | arrived_unsynced -> synced、Purchase Orders |
| H4 | 实现订单发仓确认通知 | [✔] | 2026-06-17 | 支付后发仓确认、候选发仓、物流选择、员工确认后扣减 |
| H5 | 实现采购到货入库确认通知 | [✔] | 2026-06-17 | 今日到货采购单、飞书通知、员工入库确认 |
| H6 | 设计飞书应用信息架构和首页草图 | [✔] | 2026-06-17 | 运营驾驶舱 + 业务操作台 |
| H7 | 搭建飞书应用首页运营驾驶舱 | [✔] | 2026-06-18 | 指标卡、图表、排行榜、待办列表、快捷按钮 |
| H8 | 实现订单与订单明细飞书表同步 | [✔] | 2026-06-18 | Order Fulfillment / Order Items read model |
| H9 | 实现商品飞书表同步 | [✔] | 2026-06-18 | Items read model、商品图片 URL 转飞书真实图片 |
| H10 | 实现秒杀活动和秒杀结果飞书表同步 | [✔] | 2026-06-18 | Flash Sales / Flash Sale Claims read model |
| H11 | 搭建飞书应用业务操作页 | [✔] | 2026-06-18 | 订单、库存、采购、商品运营页面 |
| H12 | 实现飞书应用联调与验收门禁 | [ ] |  | Chrome 验证、表格数据校验、页面联调验证 |
| H13 | 实现飞书表全量对账策略 | [ ] |  | 源端删除、失效标记、table_id 审计 |

#### 阶段 I：Quality And Delivery

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| I1 | 统一全量验证命令 | [~] |  | uv、pnpm、docker compose |
| I2 | 强化 run log 与错误回放 | [✔] |  | run-logs、dead-letter、replay |
| I3 | 强化 workflow 结构测试 | [✔] |  | tests/test_department_workflows.py |
| I4 | 强化文档一致性测试 | [~] |  | tests/test_current_docs.py |
| I5 | 增加本地一键验收脚本 | [ ] |  | scripts/verify_local.ps1 |
| I6 | 增加演示前健康检查脚本 | [ ] |  | scripts/demo_check.ps1 |
| I7 | 强化 Docker 启动说明 | [ ] |  | README / compose |

#### 阶段 J：RPA Data Operations

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| J1 | 定义通用网页 CSV 交付与处理扩展契约 | [ ] |  | dataset_type、RPA hooks、processor contract、目录 |
| J2 | 建立影刀通用网页导出 CSV 模板 | [ ] |  | 输入循环、页面等待、适配子流程、原始 CSV |
| J3 | 实现影刀京东商品采集适配器 | [ ] |  | URL 清单、商品页字段、状态与失败行 |
| J4 | 建立 pandas 通用 CSV 处理框架 | [ ] |  | core、contracts、validation、processor registry |
| J5 | 实现通用批次、归档与失败重放 | [ ] |  | manifest、archive、failed、replay |
| J6 | 实现京东商品 pandas processor | [ ] |  | 字段标准化、重复识别、标准/失败 CSV |
| J7 | 打通京东商品端到端文件链路 | [ ] |  | jd_product 原始 CSV 到标准化结果 |
| J8 | 实现扩展指南与阶段质量门禁 | [ ] |  | 新站点模板、自动测试、影刀人工验收 |

### 6.4 总体进度表

| 阶段 | 总任务数 | 已完成 | 进度 |
| --- | ---: | ---: | --- |
| 阶段 A | 5 | 5 | 100% |
| 阶段 B | 6 | 6 | 100% |
| 阶段 C | 5 | 5 | 100% |
| 阶段 D | 6 | 6 | 100% |
| 阶段 E | 5 | 5 | 100% |
| 阶段 F | 8 | 8 | 100% |
| 阶段 G | 11 | 11 | 100% |
| 阶段 H | 13 | 11 | 85% |
| 阶段 I | 7 | 2 | 29% |
| 阶段 J | 8 | 0 | 0% |
| **总计** | **74** | **59** | **80%** |

### 6.5 阶段实施明细

#### 阶段 A：Project Foundation

##### A1：建立服务目录和本地编排

目标：建立根项目目录、服务边界和本地运行入口。

修改文件：

- `docker-compose.yml`
- `README.md`
- `apps/`
- `services/`
- `n8n/`
- `fixtures/`
- `tests/`

实现类/函数：

- `docker-compose.yml`：定义本地服务拓扑。
- `services/*/Dockerfile`：定义服务镜像构建入口。

验收标准：

- 目录边界清晰，Compose 能解析。

测试方法：`docker compose -p after-sales-implementation config --quiet`

##### A2：配置 Docker Compose

目标：启动 PostgreSQL、Redis、ai-service、mock-api、feishu-adapter 和 n8n。

修改文件：

- `docker-compose.yml`
- `.env.example`

实现类/函数：

- `postgres` service：提供业务数据库。
- `redis` service：提供秒杀库存配额。
- `ai-service` service：提供 AImodel 和工具适配。
- `mock-api` service：提供业务事实 API。
- `feishu-adapter` service：提供飞书协议适配。
- `n8n` service：提供 Workflow 编排。

验收标准：

- 依赖关系、端口、健康检查和环境变量可读。

测试方法：`docker compose -p after-sales-implementation config --quiet`

##### A3：建立 fixtures 和环境变量模板

目标：提供稳定演示数据和本地环境变量模板。

修改文件：

- `.env.example`
- `fixtures/data/*.json`
- `fixtures/events/*.json`
- `fixtures/messages/*.json`

实现类/函数：

- `fixtures/data/items.json`：商品基础数据。
- `fixtures/data/orders.json`：订单演示数据。
- `fixtures/data/inventory_location_balances.json`：库存余额数据。
- `fixtures/data/procurement_suppliers.json`：采购供应商数据。

验收标准：

- mock-api 能读取 fixtures，并在无数据库时提供 fallback。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py -q`

##### A4：建立 uv 驱动的 Python 测试入口

目标：所有 Python 服务使用 uv 执行测试和检查。

修改文件：

- `services/mock-api/pyproject.toml`
- `services/ai-service/pyproject.toml`
- `services/feishu-adapter/pyproject.toml`

实现类/函数：

- `[project.optional-dependencies].test`：声明 pytest、ruff、httpx 等测试依赖。
- `[tool.pytest.ini_options]`：配置服务内 pythonpath。

验收标准：

- 三个 Python 服务均可通过 uv 执行 pytest。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests -q`

##### A5：建立根项目 workflow 测试

目标：用测试约束 n8n Workflow 结构和根项目文档一致性。

修改文件：

- `tests/test_department_workflows.py`
- `tests/test_current_docs.py`

实现类/函数：

- `test_department_workflows.py`：验证 workflow 文件存在、webhook 和关键工具节点。
- `test_current_docs.py`：验证根文档中的关键项目说明。

验收标准：

- 根测试能在 uv 环境下执行。

测试方法：`uv run --project services/mock-api pytest tests -q`

#### 阶段 B：Warehouse Workflow

##### B1：建立仓储数据模型和 repository

目标：以仓库、库位和库存余额为核心维护仓储事实。

修改文件：

- `services/mock-api/app/warehouse_store.py`
- `fixtures/data/warehouses.json`
- `fixtures/data/inventory_location_balances.json`

实现类/函数：

- `WarehouseRepository`：封装 PostgreSQL 与 fixtures fallback。
- `get_warehouse_repository()`：提供仓储 repository 单例入口。

验收标准：

- 仓储数据可按商品、仓库和库位读取，库存查询与风险判断均来源于 `inventory_location_balances`。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_warehouse_store.py -q`

##### B2：实现库存查询与异常查询 API

目标：提供库存事实查询和仓储异常分析能力。

修改文件：

- `services/mock-api/app/routers/warehouse/inventory.py`
- `services/mock-api/app/routers/warehouse/schemas.py`

实现类/函数：

- `lookup_inventory()`：按条件查询库存。
- `search_inventory_exceptions()`：返回缺货、临期、过期、冻结等异常。

验收标准：

- 接口返回商品、仓库、库位、可用库存和风险字段。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py -q`

##### B3：实现履约风险和订单确认后库存扣减

目标：支持员工确认发仓时按仓库和 FEFO 扣减库存。

修改文件：

- `services/mock-api/app/routers/warehouse/orders.py`
- `services/mock-api/app/routers/warehouse/state.py`

实现类/函数：

- `confirm_order_fulfillment()`：确认发仓并按 FEFO 扣减库存。
- `release_expired_orders()`：释放超时未付款订单库存。

验收标准：

- 库存不足时返回明确阻塞原因
- 订单失败不会留下错误扣减。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_warehouse_store.py -q`

##### B4：实现待审批采购单创建

目标：当库存低于阈值时直接创建待审批采购单，供 Procurement Workflow 审批。

修改文件：

- `services/mock-api/app/routers/procurement/purchase_orders.py`
- `services/mock-api/app/routers/procurement/service.py`
- `services/mock-api/app/routers/procurement/state.py`

实现类/函数：

- `create_purchase_order_request()`：根据低库存采购需求创建待审批采购单。
- `list_purchase_orders()`：查询采购单和审批状态。

验收标准：

- 低库存采购需求直接落入 `purchase_orders`。
- 采购单包含商品、仓库、库位、数量、原因和 `approval_status`。
- 采购单不依赖 `replenishment_requests` 或 `request_id`。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_procurement_router_structure.py -q`

##### B5：实现 Warehouse n8n Workflow

目标：编排仓储消息、Agent 和工具调用。

修改文件：

- `n8n/workflows/warehouse-workflow.json`

实现类/函数：

- Warehouse webhook：接收飞书转发消息。
- Warehouse tool nodes：调用库存、履约和采购需求工具。

验收标准：

- workflow 文件包含入口、Agent 和关键工具节点。
- 飞书输入 `@warehouse 查询 item_vinda_tissue 库存` 后，应返回仓库、库位、可用库存和风险建议。

测试方法：`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

##### B6：实现仓储测试与回归门禁

目标：覆盖仓储 API、repository 和 workflow 结构。

修改文件：

- `services/mock-api/tests/test_warehouse_store.py`
- `services/mock-api/tests/test_warehouse_router_structure.py`

实现类/函数：

- `test_warehouse_store.py`：验证仓储事实读写。
- `test_warehouse_router_structure.py`：验证仓储路由结构。

验收标准：

- 仓储相关测试通过，失败时能定位到具体接口或状态。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_warehouse_store.py services\mock-api\tests\test_warehouse_router_structure.py -q`

#### 阶段 C：Procurement Workflow

##### C1：建立采购单数据模型和状态规则

目标：定义采购单、审批状态、支付状态和到仓状态。

修改文件：

- `services/mock-api/app/routers/procurement/schemas.py`
- `services/mock-api/app/routers/procurement/state.py`

实现类/函数：

- `PurchaseOrder`：采购单结构，承载采购需求、审批结果、供应商、支付和到仓同步状态。
- `approval_status`：采购审批状态，表达待审批、已批准和已驳回。
- `payment_status`：采购付款状态，表达未支付和已支付。
- `warehouse_sync_status`：仓库同步状态，表达待到货、到仓未同步和已同步。

验收标准：

- `purchase_orders` 是采购需求和采购执行的唯一事实表。
- `purchase_orders` 不包含 `request_id` 字段。
- `approval_status` 能表达待审批、已批准和已驳回。
- `payment_status` 能表达未支付和已支付。
- `warehouse_sync_status` 能表达待到货、到仓未同步和已同步。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_procurement_router_structure.py -q`

##### C2：实现采购单查询、批准和驳回

目标：支持采购人员 review 采购单并直接更新采购单审批状态。

修改文件：

- `services/mock-api/app/routers/procurement/purchase_orders.py`
- `services/mock-api/app/routers/procurement/service.py`

实现类/函数：

- `approve_purchase_order()`：批准采购单并更新 `approval_status`。
- `reject_purchase_order()`：驳回采购单并记录原因。
- `list_purchase_orders()`：按审批、支付和到仓状态查询采购单。

验收标准：

- 批准采购单后保留同一 `purchase_order_id` 并更新审批状态。
- 驳回采购单后保留原因，不进入后续支付或到仓同步链路。
- 系统不提供单独的采购申请查询接口。
- 飞书输入 `@procurement 批准 PO-5001` 后，应返回采购单编号和审批状态。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py -q`

##### C3：实现采购单批量审批

目标：批量处理待审批采购单，避免重复审批和状态错乱。

修改文件：

- `services/mock-api/app/routers/procurement/service.py`

实现类/函数：

- `approve_purchase_orders_batch()`：批量批准待审批采购单。
- `select_pending_purchase_orders()`：筛选符合条件的待审批采购单。

验收标准：

- 重复批准不会创建或复制采购单。
- 已批准或已驳回采购单不会被批量任务错误覆盖。
- 飞书输入 `@procurement 批量批准采购单` 后，应返回处理数量、成功数量和跳过数量。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py -q`

##### C4：实现 Procurement n8n Workflow

目标：编排采购单审批、采购单查询和采购状态跟踪工具。

修改文件：

- `n8n/workflows/procurement-workflow.json`

实现类/函数：

- Procurement webhook：接收采购消息。
- Procurement tool nodes：调用采购单审批、采购单查询和到货确认工具。

验收标准：

- workflow 文件包含入口、Agent 和采购工具节点。
- 飞书输入 `@procurement 查询 PO-5001` 后，Procurement Workflow 应返回采购单审批、支付和到仓同步状态。

测试方法：`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

##### C5：实现采购测试与回归门禁

目标：覆盖采购 API 和 workflow 结构。

修改文件：

- `services/mock-api/tests/test_procurement_router_structure.py`

实现类/函数：

- `test_procurement_router_structure.py`：验证采购路由、schema 和接口边界。

验收标准：

- 采购相关测试通过。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_procurement_router_structure.py -q`

#### 阶段 D：Delivery Workflow

##### D1：建立物流供应商与订单物流字段

目标：提供物流状态查询所需基础数据。

修改文件：

- `fixtures/data/delivery_providers.json`
- `fixtures/data/orders.json`
- `services/mock-api/app/routers/delivery/state.py`

实现类/函数：

- `DELIVERY_PROVIDERS`：物流供应商集合。
- `DELIVERY_CASES`：物流跟进 case 集合。

验收标准：

- 订单可关联物流供应商、物流单号和配送状态。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_delivery_router_structure.py -q`

##### D2：实现物流状态查询 API

目标：按订单号查询物流状态。

修改文件：

- `services/mock-api/app/routers/delivery/router.py`
- `services/mock-api/app/routers/delivery/service.py`

实现类/函数：

- `lookup_delivery_status()`：查询指定订单物流状态。

验收标准：

- 返回订单状态、供应商、物流单号、风险等级和建议动作。
- 飞书输入 `@delivery 查询 ord_101 物流` 后，应返回物流供应商、物流单号、订单状态和风险建议。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py -q`

##### D3：实现物流异常搜索 API

目标：按状态和供应商筛选物流异常。

修改文件：

- `services/mock-api/app/routers/delivery/router.py`
- `services/mock-api/app/routers/delivery/service.py`

实现类/函数：

- `search_delivery_exceptions()`：筛选物流异常订单。

验收标准：

- 支持按 status、provider_id 过滤。
- 飞书输入 `@delivery 查询顺丰已发货订单` 后，应返回匹配的物流订单列表。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_delivery_router_structure.py -q`

##### D4：实现物流跟进 case API

目标：在需要人工跟进时创建物流 case。

修改文件：

- `services/mock-api/app/routers/delivery/router.py`
- `services/mock-api/app/routers/delivery/schemas.py`

实现类/函数：

- `create_delivery_case()`：创建物流跟进 case。

验收标准：

- case 包含订单、原因、状态和创建来源。
- 飞书输入 `@delivery 为 ord_101 创建物流延迟跟进 case` 后，应返回 case 编号和当前状态。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_delivery_router_structure.py -q`

##### D5：实现 Delivery n8n Workflow

目标：编排物流查询、异常搜索和 case 创建。

修改文件：

- `n8n/workflows/delivery-workflow.json`

实现类/函数：

- Delivery webhook：接收物流消息。
- Delivery tool nodes：调用状态、异常和 case 工具。

验收标准：

- workflow 文件包含入口、Agent 和物流工具节点。
- 飞书输入 `@delivery 当前有哪些已发货订单` 后，Delivery Workflow 应调用物流异常搜索工具并返回列表。

测试方法：`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

##### D6：实现物流测试与回归门禁

目标：覆盖物流 API 和 workflow 结构。

修改文件：

- `services/mock-api/tests/test_delivery_router_structure.py`

实现类/函数：

- `test_delivery_router_structure.py`：验证物流路由结构和关键接口。

验收标准：

- 物流相关测试通过。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_delivery_router_structure.py -q`

#### 阶段 E：Operations Workflow

##### E1：定义运营只读边界

目标：明确 Operations 只做汇总和建议，不直接修改领域事实。

修改文件：

- `n8n/workflows/operations-workflow.json`
- `tests/test_department_workflows.py`

实现类/函数：

- Operations Agent prompt：声明只读边界。
- Workflow assertions：验证不包含写库存、写采购或写物流状态节点。

验收标准：

- Operations Workflow 不直接调用写操作。

测试方法：`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

##### E2：实现运营摘要 mock API

目标：提供跨领域异常摘要接口。

修改文件：

- `services/mock-api/app/main.py`

实现类/函数：

- `operations_summary_mock()`：返回运营异常摘要、incident 列表和 next_actions。

验收标准：

- 接口返回稳定结构，便于 n8n 调用。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py -q`

##### E3：实现 Operations n8n Workflow

目标：编排运营摘要和回复。

修改文件：

- `n8n/workflows/operations-workflow.json`

实现类/函数：

- Operations webhook：接收运营消息。
- Operations tool node：调用 summary API。

验收标准：

- workflow 文件包含入口和摘要工具。
- 飞书输入 `@operations 今日有哪些运营异常` 后，Operations Workflow 应返回跨领域异常摘要。

测试方法：`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

##### E4：实现跨领域异常摘要输出

目标：把库存、物流、采购相关异常整理为可读回复。

修改文件：

- `n8n/workflows/operations-workflow.json`
- `services/mock-api/app/main.py`

实现类/函数：

- `operations_summary_mock()`：输出 incidents 和 next_actions。

验收标准：

- 回复中包含异常来源、严重程度和下一步动作。
- 飞书输入 `@operations 汇总低库存和售后风险` 后，应返回异常来源、严重程度和下一步动作。

测试方法：`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

##### E5：实现运营 workflow 测试

目标：防止 Operations Workflow 越界修改事实。

修改文件：

- `tests/test_department_workflows.py`

实现类/函数：

- `test_operations_workflow_*`：验证入口、工具和边界。

验收标准：

- workflow 结构变化会触发测试失败。

测试方法：`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

#### 阶段 F：电商项目

##### F1：实现首页、搜索与 Departments 导购

目标：提供商品浏览、搜索入口、首页促销轮播、Flash Deals 商品跳转和 Departments 分类导购入口。

修改文件：

- `apps/talonmart-web/src/views/HomeView.vue`
- `apps/talonmart-web/src/views/HomeView.spec.ts`
- `apps/talonmart-web/src/views/SearchView.vue`
- `apps/talonmart-web/src/views/DepartmentCategoryView.vue`
- `apps/talonmart-web/src/views/DepartmentCategoryView.spec.ts`
- `apps/talonmart-web/src/router/index.ts`
- `apps/talonmart-web/src/services/searchApi.ts`
- `apps/talonmart-web/src/types/search.ts`
- `services/mock-api/app/routers/search.py`
- `services/mock-api/tests/test_api.py`

实现类/函数：

- `HomeView.vue`：展示统一商城顶部导航、搜索入口、促销轮播和 Flash Deals。
- `HomeView.spec.ts`：验证首页轮播、Departments 下拉、Flash Deals 展示和详情跳转。
- `SearchView.vue`：展示搜索输入和搜索结果。
- `DepartmentCategoryView.vue`：读取 `/cp/:departmentSlug` 路由参数并展示该 department 下的商品。
- `DepartmentCategoryView.spec.ts`：验证分类页导航、商品展示和状态反馈。
- `searchProducts()`：调用商品搜索接口。
- `searchProductsByCategory()`：按 department/category 查询商品。
- `search_products()`：支持 category 参数并返回该分类下的商品结果。

验收标准：

- 用户可以搜索商品并进入详情页。
- 首页使用促销轮播承载主视觉内容，轮播切换通过平滑位移动画完成。
- 首页首屏内容由促销轮播、Flash Deals 和统一顶部导航组成，促销信息集中在轮播中呈现。
- 首页展示首版 Departments：Grocery、Clothing, Shoes & Accessories、Baby & Kids、Electronics。
- 用户点击 Electronics 后跳转到 `/cp/electronics`。
- `/cp/electronics` 页面应查询 electronics category 下的商品并展示列表。
- Flash Deals 商品图片和标题点击后进入对应商品详情页。
- Home、Search、Department、Product Detail、Cart 页面使用统一商城顶部导航：蓝色主栏、TM 标识、搜索框、Account、Cart。
- 统一顶部导航第二行以 Departments 下拉入口作为分类导航入口。
- Department 详情页通过统一顶部导航中的 Departments 下拉框完成分类切换。
- 空结果、加载中和接口失败状态都有明确页面反馈。

测试方法：`pnpm --dir apps/talonmart-web test:unit -- HomeView SearchView DepartmentCategoryView`

##### F2：实现商品详情与评论

目标：展示商品详情、卖点、规格、评论，以及商品展示卡片的白底无边框和评分体验。

修改文件：

- `apps/talonmart-web/src/views/SearchView.vue`
- `apps/talonmart-web/src/views/DepartmentCategoryView.vue`
- `apps/talonmart-web/src/views/ProductDetailView.vue`
- `apps/talonmart-web/src/views/ProductDetailView.spec.ts`
- `apps/talonmart-web/src/services/productDetailApi.ts`
- `apps/talonmart-web/src/services/flashSaleApi.ts`
- `apps/talonmart-web/src/services/productReviewApi.ts`
- `apps/talonmart-web/src/types/search.ts`
- `apps/talonmart-web/src/types/flashSale.ts`

实现类/函数：

- `SearchView.vue`：展示搜索商品卡片。
- `DepartmentCategoryView.vue`：展示 Departments 商品卡片。
- `ProductDetailView.vue`：展示商品详情。
- `ProductDetailView.vue`：读取 active flash sale 并为命中商品展示秒杀折扣价。
- `ProductDetailView.spec.ts`：验证详情页在商品存在 active flash sale 时展示秒杀价和划线原价。
- `fetchProductDetail()`：读取商品详情。
- `fetchFlashSales()`：读取 active flash sale，用于详情页匹配当前商品折扣。
- `fetchProductReviews()`：读取商品评论。

验收标准：

- 商品详情页包含商品信息和评论信息。
- 商品详情页命中 active flash sale 且 `sale_price < item_price` 时，价格区展示 `sale_price` 为当前购买展示价，并展示 `item_price` 为划线原价。
- 商品详情页没有匹配 active flash sale 时，价格区展示商品详情接口返回的普通 `price`。
- 搜索页和 Departments 页面商品卡片使用统一白色背景，不显示商品卡片边框。
- 首页 Flash Deals 商品展示使用白色背景，不显示商品图或商品卡片边框。
- 搜索页和 Departments 页面商品卡片显示星级评分与评分数量。

测试方法：`pnpm --dir apps/talonmart-web test:unit -- SearchView DepartmentCategoryView ProductDetailView`

##### F3：实现购物车页面

目标：支持查询、添加和删除购物车商品。

修改文件：

- `apps/talonmart-web/src/views/CartView.vue`
- `apps/talonmart-web/src/services/cartApi.ts`

实现类/函数：

- `CartView.vue`：展示购物车。
- `fetchCart()`：查询购物车。
- `removeCartItem()`：删除购物车条目。

验收标准：

- 用户可以查看并修改购物车。

测试方法：`pnpm --dir apps/talonmart-web test:unit -- CartView`

##### F4：实现秒杀前端接口

目标：展示秒杀活动、折扣价格和真实折扣场景下的商品原价，并触发抢购。

修改文件：

- `apps/talonmart-web/src/services/flashSaleApi.ts`
- `apps/talonmart-web/src/types/flashSale.ts`
- `apps/talonmart-web/src/views/HomeView.vue`
- `apps/talonmart-web/src/views/HomeView.spec.ts`
- `services/mock-api/app/routers/flash_sales.py`
- `services/mock-api/app/warehouse_store.py`
- `services/mock-api/tests/test_api.py`

实现类/函数：

- `fetchFlashSale()`：读取秒杀活动。
- `purchaseFlashSale()`：提交秒杀购买。
- `list_flash_sales()`：读取秒杀活动并结合商品原价。
- `/flash-sales`：返回秒杀活动、秒杀价、库存、状态和可选商品原价 `item_price`。
- `FlashSale.item_price`：表示后端返回的可选商品原价，供前端判断是否展示划线价。

验收标准：

- 前端能处理成功、库存不足和重复购买响应。
- 秒杀商品存在 `item_price > sale_price` 时，前端展示 `sale_price` 为折扣价，并展示 `item_price` 为划线原价。
- 秒杀商品缺少 `item_price` 或 `item_price <= sale_price` 时，前端只展示 `sale_price`，不使用本地静态数据补造原价。
- 用户从 Flash Deals 进入商品详情页后，详情页基于 active flash sale 匹配同一 `item_id` 并展示折扣价。
- `/flash-sales` 接口返回的 `item_price` 来源于关联商品的 `items.price`，不要求 `flash_sales` 表重复存储商品原价。

测试方法：`pnpm --dir apps/talonmart-web test:unit -- HomeView flashSaleApi`；`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py -q`

##### F5：实现 AI 模式浮动入口和聊天面板

目标：提供前端右下角 AI 模式浮动入口和会话交互。

修改文件：

- `apps/talonmart-web/src/components/AiModeSidebar.vue`
- `apps/talonmart-web/src/components/AiModeChatPanel.vue`

实现类/函数：

- `AiModeSidebar.vue`：展示右下角黄色笑脸浮动入口，并负责打开或关闭 AI 聊天面板。
- `AiModeChatPanel.vue`：展示消息、输入框和流式回答。

验收标准：

- 用户点击右下角黄色笑脸入口后打开 AI 聊天面板。
- AI 入口不展示桌面版、插件版、购物车等侧栏文案。
- 用户可以在聊天面板中选择历史会话或创建新会话并发送消息。

测试方法：`pnpm --dir apps/talonmart-web test:unit -- AiModeSidebar`

##### F6：实现前端 API client 和类型

目标：统一前端调用后端接口的类型和错误处理。

修改文件：

- `apps/talonmart-web/src/services/*.ts`
- `apps/talonmart-web/src/types/*.ts`

实现类/函数：

- `apiClient.ts`：统一 axios client。
- `aiModelApi.ts`：AI 模式接口。
- `productDetailApi.ts`：商品详情接口。
- `cartApi.ts`：购物车接口。
- `flashSale.ts`：定义秒杀活动响应类型，包含可选商品原价 `item_price`。

验收标准：

- API client 有类型定义和单元测试覆盖。
- 秒杀活动类型支持后端返回的可选 `item_price` 字段，前端将缺失值视为没有可展示划线价。
- 商品详情页复用秒杀活动类型匹配当前商品折扣，不新增独立详情页折扣类型。

测试方法：`pnpm --dir apps/talonmart-web test:unit -- aiModelApi`

##### F7：实现前端单元/E2E 测试

目标：覆盖前端关键页面和用户路径。

修改文件：

- `apps/talonmart-web/src/**/*.spec.ts`
- `apps/talonmart-web/e2e/vue.spec.ts`

实现类/函数：

- `*.spec.ts`：组件和 API client 单元测试。
- `vue.spec.ts`：浏览器 E2E 测试。

验收标准：

- 前端测试能稳定执行。

测试方法：`pnpm --dir apps/talonmart-web test:unit`

##### F8：实现分类排行榜和热门商品展示

目标：为每个 category 提供可重建、可缓存、可展示的商品排行榜能力，并在首页、分类页和商品详情页形成完整入口。

修改文件：

- `services/mock-api/app/warehouse_store.py`
- `services/mock-api/app/main.py`
- `services/mock-api/app/routers/category_rankings.py`
- `services/mock-api/tests/test_api.py`
- `services/mock-api/tests/test_warehouse_store.py`
- `apps/talonmart-web/src/services/categoryRankingApi.ts`
- `apps/talonmart-web/src/services/categoryRankingApi.spec.ts`
- `apps/talonmart-web/src/types/categoryRanking.ts`
- `apps/talonmart-web/src/views/HomeView.vue`
- `apps/talonmart-web/src/views/HomeView.spec.ts`
- `apps/talonmart-web/src/views/DepartmentCategoryView.vue`
- `apps/talonmart-web/src/views/DepartmentCategoryView.spec.ts`
- `apps/talonmart-web/src/views/ProductDetailView.vue`
- `apps/talonmart-web/src/views/ProductDetailView.spec.ts`

实现类/函数：

- `item_rank_events`：保存商品浏览、加购、购买、收藏、评论等排行榜事实事件。
- `category_rank_snapshots`：保存分类排行榜快照，支持 Redis 缓存失效后的重建和历史追踪。
- `record_item_rank_event()`：写入商品排行事件。
- `rebuild_category_rankings()`：按 category、rank_type、window_type 聚合事件并生成排行榜快照。
- `get_category_ranking()`：优先读取 Redis ZSET，未命中时从 PostgreSQL 快照恢复并回填 Redis。
- `GET /rankings/categories/{category_id}`：返回指定 category 的排行榜商品、分数、排名和商品基础信息。
- `GET /rankings/home/hot`：返回首页 `Bet you like it.` 栏目所需热门商品。
- `categoryRankingApi.ts`：封装排行榜 HTTP 调用。
- `categoryRanking.ts`：定义排行榜项、榜单类型、时间窗口和响应类型。
- `HomeView.vue`：展示 `Bet you like it.` 热门商品栏目。
- `DepartmentCategoryView.vue`：在每个分类页提供排行榜入口和 Top 商品展示。
- `ProductDetailView.vue`：商品属于当前 category Top3 时展示可点击排行榜标签。

验收标准：

- PostgreSQL 保存排行榜事实事件和排行榜快照，Redis 只保存可重建的 ZSET 排序缓存。
- Redis key 采用 `rank:category:{category_id}:{rank_type}:{window_type}` 结构，ZSET member 为 `item_id`，score 为聚合分数。
- 首页展示 `Bet you like it.` 栏目，内容来自热门商品排行榜接口。
- 首页 `Bet you like it.` 跨分类热门列表展示每个商品在原 category snapshot 中的排名，不对跨分类结果重新递增编号。
- 每个分类页提供排行榜入口，用户可以查看该分类下的热门商品。
- 商品详情页命中所属分类 Top3 时展示排行榜标签，例如 `#1 in Grocery`，标签点击后进入对应分类排行榜入口。
- Redis 不可用或缓存未命中时，API 可以从 PostgreSQL 快照读取榜单并返回结果。
- 排行榜接口返回空结果时，前端显示稳定空状态，不影响首页、分类页和详情页主体内容。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py services\mock-api\tests\test_warehouse_store.py -q`；`pnpm --dir apps/talonmart-web test:unit -- HomeView DepartmentCategoryView ProductDetailView categoryRankingApi`

#### 阶段 G：AImodel

##### G1：建立 AImodel Router 和 schemas

目标：提供前端 AI 模式所需 HTTP 接口。

修改文件：

- `services/ai-service/app/routers/AImodel/router.py`
- `services/ai-service/app/routers/AImodel/schemas.py`

实现类/函数：

- `chat()`：处理 AI 模式聊天请求。
- `ConversationCreate` / `MessageCreate`：定义请求结构。

验收标准：

- 前端可创建会话并发起聊天。

测试方法：`uv run --project services/ai-service pytest services\ai-service\tests\test_aimodel_agent.py -q`

##### G2：实现 conversation/message/user_memory 与 LangChain 会话记忆

目标：保存会话、消息、用户长期偏好，并把 AImodel 的会话状态接入 LangChain/LangGraph 记忆机制。业务消息表继续作为前端历史会话和评估溯源的数据源，LangChain PostgreSQL checkpointer 负责 Agent 运行时的短期上下文续接。

修改文件：

- `services/ai-service/app/routers/AImodel/memory.py`
- `services/ai-service/app/routers/AImodel/service.py`
- `services/ai-service/tests/test_aimodel_memory.py`
- `services/ai-service/tests/test_aimodel_agent.py`

实现类/函数：

- `AImodelMemoryStore.initialize()`：初始化 `conversation`、`message`、`message_query_trace`、`user_memory`、`agent_trace` 和 `agent_trace_event` 业务表；LangChain checkpointer 作为 Agent 运行时组件复用，不替代业务消息落库。
- `ensure_conversation()`：创建或续用会话，返回稳定的数字 `conversation_id`。
- `append_user_message()` / `append_assistant_message()`：写入业务消息表，保持前端历史会话、评估 answer source 和 RAG trace 溯源能力。
- `load_user_memories()` / `upsert_user_memory()`：读取和更新用户长期偏好。
- `get_langchain_checkpointer()`：返回可被 `create_agent(checkpointer=...)` 复用的进程级 LangChain/LangGraph checkpointer；配置 `DATABASE_URL` 时使用 PostgreSQL `PostgresSaver` 并执行 `setup()`，未配置数据库时测试环境使用内存实现。
- `build_langchain_config()`：以 `conversation_id` 构造 `{"configurable": {"thread_id": str(conversation_id)}}`，确保同一会话复用同一 Agent thread。
- `stream_chat_events()`：调用 LangChain Agent 时传入 checkpointer 和 thread config；长期用户偏好仍作为显式上下文注入，业务消息落库不由 checkpointer 替代。

验收标准：

- 同一用户可以查询历史会话并继续对话。
- 同一 `conversation_id` 的多轮 Agent 调用使用同一个 LangChain thread，不再只依赖手动截取最近 5 条消息维持上下文。
- `conversation` / `message` / `message_query_trace` 仍是前端历史、RAG trace 关联和 Ragas message answer source 的权威业务记录。
- `user_memory` 继续保存长期偏好，进入 Agent 前以显式上下文注入，不与 LangChain checkpoint 混淆。
- 未配置 PostgreSQL 时，测试环境使用进程级内存 checkpointer 和 `NoopAiModelMemoryStore` 保持可运行；配置 PostgreSQL 时必须使用 LangGraph `PostgresSaver`。

测试方法：`uv run --project services/ai-service pytest services\ai-service\tests\test_aimodel_memory.py services\ai-service\tests\test_aimodel_agent.py -q`
##### G3：实现商品搜索和详情工具

目标：让 Agent 获取真实商品事实。

修改文件：

- `services/ai-service/app/routers/AImodel/tools.py`

实现类/函数：

- `search_products()`：调用商品搜索 API。
- `get_product_detail()`：调用商品详情 API。

验收标准：

- 商品推荐和链接对比不编造价格、库存或链接。

测试方法：`uv run --project services/ai-service pytest services\ai-service\tests\test_aimodel_rag_tool.py -q`

##### G4：实现 RAG MCP 客户端

目标：通过 stdio MCP 调用独立 RAG 子系统。

修改文件：

- `services/ai-service/app/routers/AImodel/tools.py`

实现类/函数：

- `PersistentMcpRagKnowledgeClient`：复用 MCP 子进程和 session。
- `get_rag_knowledge_client()`：返回进程级客户端。
- `close_rag_knowledge_client()`：释放 MCP 资源。

验收标准：

- 多次查询复用同一 MCP session，shutdown 能释放资源。

测试方法：`uv run --project services/ai-service pytest services\ai-service\tests\test_aimodel_rag_tool.py -q`

##### G5：实现 LangChain Agent 编排

目标：根据用户意图选择商品工具、RAG 工具、联网搜索工具或直接回复路径。

修改文件：

- `services/ai-service/app/routers/AImodel/service.py`
- `services/ai-service/tests/test_aimodel_agent.py`
- `services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- `AImodelAgentService.chat_stream()`：编排消息、工具和流式输出。
- `build_agent_tools()`：构造 Agent 工具列表。
- `build_rag_tool()`：构造无参数 RAG 工具，工具调用时使用当前 turn 的原始用户问题作为实际 RAG query。
- `_run_rag_tool()`：执行 RAG MCP 查询并记录本轮工具结果。

验收标准：

- 简单咨询、商品推荐、链接对比、知识问答、公开信息和直接回复都能走正确路径。
- LangChain Agent 只能决定是否调用 `rag_tool` 或其他已授权工具，不能通过工具参数自由生成或改写 RAG 检索 query。
- `rag_tool` 工具 schema 不暴露 `query` 参数；实际传给 RAG MCP 的 query 必须等于当前用户原始问题。
- 同一用户 turn 内重复调用 `rag_tool` 时应复用首次 RAG 结果，不重复触发 RAG MCP 查询，也不新增额外 query trace。
- 需要改写、扩展、多跳或关键词化检索时，由 RAG 子系统内部 QueryProcessor、Query Planner 或检索链路负责，并写入 RAG query trace。

测试方法：`uv run --project services/ai-service pytest services\ai-service\tests\test_aimodel_agent.py services\ai-service\tests\test_aimodel_rag_tool.py -q`

##### G6：实现 SSE 流式响应和输出清洗

目标：前端实时看到回答，不看到内部 tool result。

修改文件：

- `services/ai-service/app/routers/AImodel/service.py`
- `apps/talonmart-web/src/services/aiModelApi.ts`

实现类/函数：

- `_clean_agent_visible_text()`：清洗内部 JSON 和 trace 信息。
- `streamChat()`：前端消费 SSE。

验收标准：

- 前端只显示用户可读回答。

测试方法：`uv run --project services/ai-service pytest services\ai-service\tests\test_aimodel_agent.py -q`

##### G7：实现 message_query_trace 关联

目标：把最终消息和 RAG query trace 关联，方便后续评估和排查。

修改文件：

- `services/ai-service/app/routers/AImodel/memory.py`
- `services/ai-service/app/routers/AImodel/service.py`

实现类/函数：

- `link_message_query_trace()`：记录 message_id 和 query_trace_id。
- `_query_trace_ids_from_tool_results()`：从本轮 RAG 工具结果中提取去重后的 query trace id。

验收标准：

- AImodel 调用 RAG 后可从 message 追溯 query trace。
- 单个用户 turn 内重复触发 `rag_tool` 时，message 默认只关联本轮复用后的一个 RAG query trace。
- 关联的 RAG trace `raw_query` 应保持为用户原始问题，避免 Agent 自行生成多个关键词化 query 后导致评估和路由漂移。

测试方法：`uv run --project services/ai-service pytest services\ai-service\tests\test_aimodel_memory.py services\ai-service\tests\test_aimodel_rag_tool.py -q`

##### G8：实现 Tavily 联网搜索工具

目标：为 AImodel 增加受控联网搜索能力，用于查询商品库和 RAG 知识库之外的公开网页信息；该工具只作为外部信息补充，不替代 `mock-api` 商品事实、订单事实或 RAG 引用上下文。

修改文件：

- `services/ai-service/pyproject.toml`
- `.env.example`
- `docker-compose.yml`
- `services/ai-service/app/routers/AImodel/tools.py`
- `services/ai-service/app/routers/AImodel/service.py`
- `services/ai-service/tests/test_aimodel_agent.py`
- `services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- `TavilySearchClient`：封装 Tavily Search API 调用，读取环境变量配置并处理超时、空结果和错误响应。
- `search_web_with_tavily()`：AImodel 工具适配函数，返回标准化联网搜索结果。
- `build_web_search_tool()`：构造 LangChain 工具，并把工具结果写入本轮 `tool_results`。
- `AIMODEL_WEB_SEARCH_ENABLED` / `TAVILY_API_KEY`：控制联网搜索工具是否启用和是否具备凭证。

验收标准：

- 未配置 `TAVILY_API_KEY` 时，联网搜索工具返回明确的 unavailable 结果，AImodel 仍可使用商品工具和 RAG 工具。
- 已配置 `TAVILY_API_KEY` 时，AImodel 可通过 Tavily 查询公开网页信息。
- Tavily 工具不得调用内网地址、`mock-api`、`ai-service`、RAG 内部 API 或任意用户传入 URL，只能通过 Tavily 官方搜索 API 获取结果。
- 商品价格、库存、优惠、可购买链接仍必须来自 `mock-api` 商品工具，不能由 Tavily 搜索结果覆盖。
- SSE 输出清洗仍隐藏工具参数、原始字段名、内部错误堆栈和调试信息。

测试方法：`uv run --project services/ai-service pytest services\ai-service\tests\test_aimodel_agent.py services\ai-service\tests\test_aimodel_rag_tool.py -q`；`uv run --project services/ai-service ruff check services\ai-service\app\routers\AImodel\tools.py services\ai-service\app\routers\AImodel\service.py services\ai-service\tests\test_aimodel_agent.py services\ai-service\tests\test_aimodel_rag_tool.py`

##### G9：实现 AImodel 测试与回归门禁

目标：覆盖 AImodel 核心工具、记忆和流式输出，包含商品工具、RAG MCP 工具和 Tavily 联网搜索工具的回归边界。

修改文件：

- `services/ai-service/tests/test_aimodel_agent.py`
- `services/ai-service/tests/test_aimodel_memory.py`
- `services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- `test_aimodel_agent.py`：验证 Agent 行为。
- `test_aimodel_memory.py`：验证会话和记忆。
- `test_aimodel_rag_tool.py`：验证 RAG MCP 与联网搜索工具边界。

验收标准：

- 目标测试稳定通过。
- 未配置联网搜索 key 时测试仍可离线执行，不访问 Tavily 真实服务。

测试方法：`uv run --project services/ai-service pytest services\ai-service\tests\test_aimodel_agent.py services\ai-service\tests\test_aimodel_memory.py services\ai-service\tests\test_aimodel_rag_tool.py -q`

##### G10：实现 AImodel Intent Router

目标：将面向用户问题的工具编排意图识别前置到 AImodel 层，采用 RAG 当前树状意图配置设计，先决定 action 和 collection，再进入 LangChain Agent 或直接工具路径；对跨知识库问题，基于 intent candidate score 阈值动态选择多个 RAG collection，不通过 YAML 为每个跨域场景硬编码 collections。

修改文件：

- `services/ai-service/app/routers/AImodel/intent_router.py`
- `services/ai-service/app/routers/AImodel/intent_routes.yaml`
- `services/ai-service/app/routers/AImodel/service.py`
- `services/ai-service/tests/test_aimodel_agent.py`
- `services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- `AImodelIntentRule`：表示树状配置中的一个 domain/category/intent 叶子节点。
- `AImodelIntentRoute`：输出 action、collection、collections、domain、category、intent、confidence、reason、matched_rule、matched_terms 和 fallback_used。
- `load_aimodel_intent_routes()`：加载 `routers -> domain -> categories -> intents` 配置并预编译 regex。
- `AImodelIntentRouter.route()`：按 rules -> semantic profile -> LLM fallback -> default 的顺序选择工具动作和主 collection。
- `AImodelIntentRouter.route_with_candidates()`：返回 winner 与 top candidates，每个 candidate 包含 collection、score、domain、category、intent 和 matched_rule。
- `select_rag_collections_by_score()`：根据 candidate score、winner score、collection 去重和最小阈值动态选择多 collection，例如保留 `score >= max(min_score, winner_score - delta)` 的 RAG candidates。
- `intent_routes.yaml`：为售后限制、安装限制、特殊商品安装服务等内部政策场景提供 policies 规则，使这类问题进入政策知识库候选。
- `stream_chat_events()` / `_run_langchain_agent_stream()`：消费 AImodel 意图结果，必要时把 collection 或 collections 传给 `rag_tool`，并保留原始用户 query。

验收标准：

- AImodel Intent Router 配置结构与 RAG Intent Router 保持同类设计，支持 domain、category、intent、priority、confidence、match.any、match.all、match.regex、action 和 collection。
- action 至少支持 `rag`、`product_api`、`web`、`direct`、`refuse`，其中 `rag` 必须携带目标 collection。
- AImodel 不通过新增 YAML `collections` 字段来解决每个跨域问题，而是基于已有 intent candidate score 阈值动态选择多个 collection。
- 当 top candidates 中多个 RAG collection 的分数接近 winner 且超过阈值时，AImodel 应把多个 collection 发送给 RAG；例如 `manual + policies`、`shopping_guides + faq`。
- 安装限制、售后限制、特殊商品售后限制和大家电安装服务类问题必须把 `policies` 纳入候选 collections；如果问题同时涉及选购建议，也应保留相近的 `shopping_guides` 候选。
- 多 collection 查询应并行执行；任一 collection 成功返回 evidence 时，最终 Agent 可使用所有成功 evidence；失败或 empty 的 collection 应记录但不得阻断其他 collection。
- 选购指南、FAQ、政策、客服话术等内部知识问题由 AImodel 选择 collection 或 collections 后调用 RAG；RAG query 仍使用用户原始问题。
- 商品价格、库存、优惠、可购买链接仍走商品 API；外部公开信息走 Tavily；寒暄和明显越界问题不调用 RAG。
- message_query_trace 只在实际调用 RAG 后写入；需要 RAG 但未产生 trace 时必须触发 Gate 检查。
- 一个 assistant message 可以关联多个 RAG query_trace_id；多 collection 查询必须把所有实际 RAG trace 写入 message_query_trace。
- Agent Trace 必须记录 winner、top3 candidate score、selected collections、score 阈值、被过滤候选、每个 collection 的 query_trace_id、状态和 evidence 数量。
- 若所有 collection 均 empty，最终回答仍按现有空证据策略处理，不使用 LLM 常识补写内部规则。
- 单元测试覆盖规则命中、collection 选择、direct/refuse 不调用 RAG、RAG 调用保留原始 query、低置信度 fallback、多 collection 选择、score 低于阈值被过滤、并行查询部分失败和多个 query_trace_id 持久化。

测试方法：`uv run --project services/ai-service pytest services\ai-service\tests\test_aimodel_agent.py services\ai-service\tests\test_aimodel_rag_tool.py -q`

##### G11：实现 LangChain Middleware Agent Trace

目标：通过 LangChain Middleware 建立 AImodel Agent Trace，记录一次用户 turn 从意图识别、工具授权、LangChain 工具调用、RAG trace 关联到最终回答状态的可观测链路，用于排查 Agent 为什么调用或没有调用某个工具。

修改文件：

- `services/ai-service/app/routers/AImodel/agent_trace.py`
- `services/ai-service/app/routers/AImodel/service.py`
- `services/ai-service/app/routers/AImodel/memory.py`
- `services/ai-service/tests/test_aimodel_agent.py`
- `services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- `AgentTraceContext`：保存 agent_trace_id、conversation_id、message_id、user_query、intent_result、intent_details、allowed_tools、tool_calls、query_trace_ids 和 error。
- `LangChainAgentTraceMiddleware`：接入 LangChain Middleware，在工具调用前后记录 tool name、输入摘要、输出摘要、耗时和异常。
- `record_intent_route()`：在进入 LangChain Agent 前将主表 intent 结果精简为 action、collection、domain、category、intent、confidence，并在 intent event 中记录 matched_rule、matched_terms、matched_regex、fallback 和 top3 candidate score。
- `record_allowed_tools()`：记录 `_agent_tools_for_intent_route()` 输出的工具白名单，便于区分“Agent 没调用”和“工具未授权”。
- `ensure_agent_trace_schema()`：创建 `agent_trace` 和 `agent_trace_event` 表及必要索引。
- `persist_agent_trace()`：在用户 turn 结束时写入 PostgreSQL，关联 message_id、conversation_id 和 RAG query_trace_id。

验收标准：

- 每次 AImodel chat 都生成一个 agent_trace_id，并持久化到 `agent_trace` / `agent_trace_event`，可追溯用户原始问题、intent 结果、allowed_tools、tool_calls、最终回答完成状态和 error；intent 具体细节通过 intent event 展示 top3 candidate score。
- LangChain 工具调用由 Middleware 采集，不在每个工具实现中重复写散落日志。
- AImodel 前置意图识别和工具授权由 service 层显式写入 Agent Trace，因为这些步骤发生在 LangChain Agent 之前。
- `rag_tool` 调用成功时，Agent Trace 必须记录对应 RAG query_trace_id；未调用 RAG 时应能从 allowed_tools 与 tool_calls 看出原因。
- Trace 内容不得记录 API key、完整 prompt、完整 RAG context、完整工具 JSON、chunk 正文或用户隐私型大文本，只保留摘要、ID、计数、耗时和状态。
- 数据库表必须支持按 conversation_id、message_id、agent_trace_id、query_trace_id 和 created_at 查询。
- 单元测试覆盖 intent route 记录、allowed_tools 记录、Middleware tool call success/error 记录、RAG trace id 关联和敏感内容过滤。

测试方法：`uv run --project services/ai-service pytest services\ai-service\tests\test_aimodel_agent.py services\ai-service\tests\test_aimodel_rag_tool.py -q`

#### 阶段 H：飞书应用与协作后台

##### H1：建立 feishu-adapter 基础能力

目标：提供飞书长连接、多机器人事件接入、n8n 转发、飞书回复、run log 记录、table_id-first 多维表格定位、table_id 持久记忆、分页同步和图片上传基础能力。

修改文件：

- `services/feishu-adapter/app/main.py`
- `services/feishu-adapter/app/feishu_client.py`
- `services/feishu-adapter/app/feishu_events.py`
- `services/feishu-adapter/app/feishu_long_connection.py`
- `services/feishu-adapter/tests/test_feishu_adapter.py`
- `.env.example`
- `docker-compose.yml`

实现类/函数：

- `create_app()`：创建飞书适配服务并注册机器人、同步和通知端点。
- `handle_feishu_event()`：解析飞书事件并路由到对应机器人处理链路。
- `reply_text_message()`：向原消息线程发送机器人回复。
- `send_group_text_message()`：向指定群主动发送业务通知。
- `write_run_log()`：记录事件、工作流、工具调用、耗时和回复状态。
- `ensure_*_table()`：所有飞书表同步优先使用已配置或已记忆的 `table_id` 校验表是否存在，表名只用于首次创建或 `table_id` 缺失时的兜底查找。
- `load_feishu_table_state()`：服务启动时读取 `FEISHU_TABLE_STATE_PATH` 指向的本地状态文件，作为环境变量缺失时的表 ID 来源。
- `remember_feishu_table_state()`：表创建或首次按名称解析成功后，持久化 `table_id`、`view_id` 和 `table_url` 状态。
- `fetch_business_table_rows_paginated()`：按 `limit + offset` 从 mock-api 拉取多页 read model，并兼容旧版单页响应。
- `sync_business_table()`：统一编排 schema 获取、分页取数、飞书字段补齐和按业务键 upsert。
- `resolve_image_source_url()`：根据数据库图片 URL 和 OSS 配置解析可下载图片地址，支持公开 URL 和阿里云 OSS 签名 URL。
- `upload_image_to_feishu()`：将图片内容上传到飞书文件接口并返回可写入多维表格图片字段的 `file_token`。
- `get_or_upload_feishu_image_token()`：以图片 URL 和内容摘要为缓存键复用已上传图片，避免每次同步重复上传。
- `FEISHU_IMAGE_TOKEN_CACHE_PATH`：本地图片 token 缓存文件路径，缓存 `image_url -> file_token` 映射。
- `ALIYUN_OSS_ACCESS_KEY_ID` / `ALIYUN_OSS_ACCESS_KEY_SECRET` / `ALIYUN_OSS_ENDPOINT` / `ALIYUN_OSS_BUCKET`：阿里云 OSS 读取配置，仅在处理私有 OSS 图片时需要。

验收标准：

- feishu-adapter 能启动飞书长连接监听多个机器人。
- 飞书消息可以按机器人名称转发到对应 n8n webhook。
- 机器人回复和主动群消息都通过统一 Feishu client 发送。
- run log 记录 event_id、message_id、bot_name、workflow、status、latency_ms、tool_calls 和 error。
- 所有表同步端点以 `table_id` 为主定位飞书表；业务人员修改飞书表名后，只要 `table_id` 仍有效，同步不得创建重复表。
- 飞书表创建成功或通过名称首次解析成功后，服务必须把 `table_id` 持久化到 `FEISHU_TABLE_STATE_PATH` 指向的本地状态文件；后续启动在未显式配置环境变量时优先复用该状态。
- `table_id` 无效或表被删除时，系统清空失效状态并按当前默认表名或请求表名重新创建。
- 通用表同步从源端读取 `items`、`count`、`has_more` 和 `next_offset`；当源端未返回分页字段时按单页兼容处理。
- 通用表同步在多页场景下持续请求下一页，直到 `has_more=false` 或 `next_offset` 为空，不因默认单页限制丢失记录。
- n8n 定时任务只触发对应同步端点，不在 workflow 内实现分页循环。
- 表同步响应包含 table_id、table_name、table_url、synced_count、page_count 和错误摘要。
- 图片上传能力从数据库保存的图片 URL 读取图片，上传到飞书后返回稳定 `file_token`，供各业务表写入真实图片字段。
- 阿里云 OSS 访问参数通过环境变量配置，不得硬编码；缺少 OSS 密钥时只能处理可直接访问的公开图片 URL。
- 需要阿里云 OSS 密钥时，开发流程必须停止并等待用户填写环境变量，不能在代码、文档或测试输出中暴露真实密钥。
- 图片上传失败不能阻塞整张表同步，应保留图片 URL 文本字段并返回明确的图片上传错误摘要。

测试方法：`uv run --project services/feishu-adapter pytest services\feishu-adapter\tests\test_feishu_adapter.py -q`；`docker compose -p after-sales-implementation config --quiet`

##### H2：实现库存余额和库存流水飞书表同步

目标：将库存余额和库存流水 read model 同步到飞书多维表格，供飞书应用页面引用。

修改文件：

- `services/feishu-adapter/app/main.py`
- `services/feishu-adapter/app/view_template_builder.py`
- `services/mock-api/app/routers/warehouse/inventory.py`
- `n8n/workflows/warehouse-inventory-balances-refresh.json`
- `n8n/workflows/warehouse-inventory-movements-refresh.json`
- `tests/test_department_workflows.py`

实现类/函数：

- `sync_inventory_balances_table()`：同步库存余额。
- `sync_inventory_movements_table()`：同步库存流水。
- `Warehouse Inventory Balances Refresh`：定时刷新库存余额飞书表。
- `Warehouse Inventory Movements Refresh`：定时刷新库存流水飞书表。

验收标准：

- 接口返回 table_id、table_url、synced_count 和错误摘要。
- 库存余额表严格映射数据库 `inventory_location_balances`：`id`、`warehouse_id`、`location_code`、`item_id`、`production_date`、`expiry_date`、`quantity_on_hand`、`reorder_threshold`、`storage_status`、`created_at`、`updated_at`。
- 库存余额表不展示 `Warehouse`、`Category`、`Item Name`、`Brand`、`Risk Level`、`Balance Status`、`Last Synced At`、`Sync Status`、`Source Version` 等非本表字段。
- `id` 来源于数据库 `inventory_location_balances.id`；无数据库 fallback 时使用稳定可读的 `fallback:{item_id}:{warehouse_id}:{location_code}`。
- 定时任务调用 `/warehouse/inventory-balances-table/sync` 刷新飞书余额表。
- 库存余额同步复用 H1 分页能力，源端超过单页数量时不得丢失记录。
- 库存流水表严格映射数据库 `inventory_movements`，展示员工确认发仓、退款、退货等库存变更的业务来源、商品、仓库、库位、数量变化、原因、关联订单和创建时间。
- 定时任务调用 `/warehouse/inventory-movements-table/sync` 刷新飞书库存流水表。
- 库存流水同步复用 H1 分页能力，源端超过单页数量时不得丢失记录。

测试方法：`uv run --project services/feishu-adapter pytest services\feishu-adapter\tests -q`；`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

##### H3：实现采购到仓库存同步和采购单飞书表同步

目标：将已到仓未同步采购单写入或更新库位库存余额，并将采购单 read model 同步到飞书多维表格，供采购页面和运营驾驶舱使用。

修改文件：

- `services/mock-api/app/routers/warehouse/purchase_orders.py`
- `services/feishu-adapter/app/main.py`
- `services/feishu-adapter/app/view_template_builder.py`
- `services/mock-api/app/routers/procurement/service.py`
- `n8n/workflows/procurement-purchase-orders-sync.json`
- `tests/test_department_workflows.py`

实现类/函数：

- `sync_arrived_purchase_orders()`：扫描并同步到仓采购单。
- `sync_purchase_order_inventory()`：按采购单触发库存余额同步。
- `POST /warehouse/purchase-orders/{purchase_order_id}/sync-inventory`：供飞书按钮自动化调用的单据库存同步接口。
- `mark_purchase_order_synced()`：更新采购单仓储同步状态。
- `provision_procurement_purchase_orders_table()`：创建采购单表。
- `sync_procurement_purchase_orders_table()`：同步采购单。
- `Feishu Purchase Order Stock Sync Automation`：采购单飞书表按钮触发的原生自动化流程。
- `Procurement Purchase Orders Sync`：定时同步采购单表。

验收标准：

- 同步后库存事实增加，采购单状态从 `arrived_unsynced` 进入 `synced`。
- 采购单飞书表提供 `Sync Inventory` 原生按钮字段，不得用普通 text 字段伪装按钮；按钮仅对 `warehouse_sync_status=arrived_unsynced` 的采购单作为业务操作入口。
- 当前飞书字段 OpenAPI 不支持创建或更新 `type=3001` 按钮字段；adapter 不得尝试通过字段 OpenAPI 创建按钮，也不得删除名为 `Sync Inventory` 的人工配置字段。按钮字段由飞书 UI 和自动化配置负责维护，记录同步时必须跳过按钮单元格写入。
- 用户点击采购单行内“同步库存”按钮后，飞书多维表格原生自动化流程应发送 `POST /warehouse/purchase-orders/{purchase_order_id}/sync-inventory` 到后端，由后端完成库存余额写入或更新。
- 飞书原生自动化流程只负责传递采购单 ID、触发后端接口和回写执行结果，不借助 n8n，不在飞书侧计算库存数量。采购单记录同步时不写入按钮单元格值，按钮列由表字段配置和飞书自动化负责展示与触发。
- 自动化流程请求体应包含采购单 ID、触发来源和操作者标识，后端响应应包含同步状态、更新库存余额行数和错误摘要。
- 后端库存同步成功后，只更新或写入 `inventory_location_balances`，并将采购单状态从 `arrived_unsynced` 更新为 `synced`；采购到仓同步不写入 `inventory_movements`。
- 按钮触发的单据库存同步成功后，mock-api 必须调用飞书采购单表同步端点刷新当前采购单行，使飞书表中的 Warehouse Sync Status 及时变为 synced。
- 按钮触发的单据库存同步成功后，mock-api 必须调用飞书库存余额表同步端点，按当前采购单的 `warehouse_id + item_id` 刷新对应库存余额行。
- 采购到仓同步直接根据 `purchase_orders.warehouse_sync_status` 控制幂等，不依赖单独同步任务表。
- 表同步结果包含写入数量和表链接。
- 采购单飞书表展示 `Approval Status`，用于区分待审批、已批准和已驳回采购单。
- 采购单飞书表展示 `Reason`。
- 采购单飞书表不展示 `Request ID`、`Supplier ID`、`Item ID`、`Last Synced At`、`Sync Status`、`Source Version`、`current_quantity`、`reorder_threshold` 或 `suggested_quantity`。
- `purchase_orders` 数据库表不保存 `current_quantity`、`reorder_threshold` 或 `suggested_quantity`。
- 飞书端不存在独立的 `Procurement Replenishment Requests` 表。
- 采购单表有独立 n8n 定时同步任务，并调用 `/procurement/purchase-orders-table/sync` 端点刷新 read model。
- 采购单同步复用 H1 分页能力，源端超过单页数量时不得丢失记录。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_warehouse_store.py -q`；`uv run --project services/feishu-adapter pytest services\feishu-adapter\tests\test_feishu_adapter.py -q`；`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

##### H4：实现订单发仓确认通知

目标：用户支付后由飞书机器人主动发送订单详情、候选发仓方案和物流选择，员工确认后执行库存扣减并进入发货链路。

修改文件：

- `services/mock-api/app/routers/warehouse/orders.py`
- `services/mock-api/app/warehouse_store.py`
- `services/mock-api/tests/test_api.py`
- `services/mock-api/tests/test_warehouse_store.py`
- `services/feishu-adapter/app/main.py`
- `services/feishu-adapter/app/feishu_client.py`
- `services/feishu-adapter/tests/test_feishu_adapter.py`
- `.env.example`
- `docker-compose.yml`
- `apps/talonmart-web/src/services/checkoutApi.ts`
- `apps/talonmart-web/src/types/checkout.ts`
- `apps/talonmart-web/src/views/CartView.vue`
- `apps/talonmart-web/src/views/CartView.spec.ts`

实现类/函数：

- `ORDER_STATUS_UNPAID`：订单创建后等待用户付款。
- `ORDER_STATUS_PENDING_FULFILLMENT_REVIEW`：用户付款后等待员工确认发仓。
- `ORDER_STATUS_PENDING_SHIPMENT`：员工确认发仓并完成库存扣减后等待发货。
- `confirm_order_fulfillment()`：确认订单使用的发仓策略并扣减库存。
- `list_order_fulfillment_candidates()`：返回可满足订单的候选仓库和库存风险。
- `send_order_fulfillment_review_message()`：主动向飞书群发送订单详情和候选发仓方案。
- `send_fulfillment_review_notification()`：用户支付成功后按环境变量触发 feishu-adapter 发仓确认通知。

验收标准：

- 用户下单成功后不立即扣减库存，订单进入 `unpaid`，且不发送发仓确认通知。
- 用户支付成功后订单进入 `pending_fulfillment_review`，并触发发仓确认通知。
- 发仓确认通知由 `FEISHU_FULFILLMENT_REVIEW_NOTIFY_URL` 和 `FEISHU_FULFILLMENT_REVIEW_CHAT_ID` 控制。
- 员工确认发仓后才扣减库存，并将订单状态更新为 `pending_shipment`。
- 员工确认时可以选择物流服务商，订单更新 `delivery_provider_id`、`delivery_provider_name` 和跟踪单号。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py services\mock-api\tests\test_warehouse_store.py -q`；`uv run --project services/feishu-adapter pytest services\feishu-adapter\tests\test_feishu_adapter.py -q`；`pnpm --dir apps/talonmart-web test:unit -- CartView checkoutApi`

##### H5：实现采购到货入库确认通知

目标：扫描预计今日到货的已支付采购单，由飞书机器人主动通知仓储人员确认是否入库，员工确认后进入采购到仓库存同步链路。

修改文件：

- `services/mock-api/app/routers/warehouse/purchase_orders.py`
- `services/mock-api/app/routers/procurement/purchase_orders.py`
- `services/mock-api/app/warehouse_store.py`
- `services/mock-api/tests/test_api.py`
- `services/mock-api/tests/test_warehouse_store.py`
- `services/feishu-adapter/app/main.py`
- `services/feishu-adapter/app/feishu_client.py`
- `services/feishu-adapter/tests/test_feishu_adapter.py`
- `n8n/workflows/warehouse-purchase-arrival-notify.json`
- `tests/test_department_workflows.py`
- `.env.example`
- `docker-compose.yml`

实现类/函数：

- `list_purchase_orders()`：查询采购单。
- `confirm_purchase_order_arrival()`：确认采购单到仓。
- `list_today_purchase_order_arrivals()`：查询待入库采购单。
- `send_purchase_arrival_notification()`：向飞书群发送今日到货采购单和入库确认入口。
- `confirm_purchase_order_arrival_batch()`：员工确认全部或指定采购单已入库后更新 `warehouse_sync_status=arrived_unsynced`。
- `procurement_arrival_fast_path_payload()`：对明确采购到货确认指令走确定性 fast path。
- `Warehouse Purchase Arrival Notify`：定时扫描今日到货采购单并触发飞书通知。

验收标准：

- 到仓确认只更新采购状态，不直接写仓储库存。
- 飞书输入 `@procurement PO-5001 已到仓库` 后，应将采购单标记为到仓未同步并返回当前仓储同步状态。
- 今日到货通知只包含已支付且 `warehouse_sync_status=pending_arrival` 的采购单。
- 飞书消息包含采购单号、商品、数量、仓库、库位、预计到货日期和确认入口文本。
- 员工可以确认全部到货采购单，也可以指定部分采购单入库。
- 入库确认后采购单进入 `arrived_unsynced`，后续由仓储同步工具写入或更新库存余额。
- 明确的 `@procurement PO-* arrived at warehouse` 指令应命中 fast path，不依赖 LLM 解析。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py services\mock-api\tests\test_warehouse_store.py -q`；`uv run --project services/feishu-adapter pytest services\feishu-adapter\tests\test_feishu_adapter.py -q`；`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

##### H6：设计飞书应用信息架构和首页草图

目标：确定飞书应用页面结构、首页布局、组件类型和数据源边界，并将 `.superpowers/brainstorm/codex-20260617231026/content/talonmart-dashboard-v1.html` 作为首页草图基准，为实际搭建提供规范。

修改文件：

- `DEV_SPEC.md`
- `.superpowers/brainstorm/`

实现类/函数：

- 飞书应用页面结构：定义运营驾驶舱、订单履约中心、库存管理中心、采购管理中心和商品运营中心。
- 首页草图：采用 `talonmart-dashboard-v1.html` 中的 Dashboard + Workbench 结构，定义 5 个指标卡、3 个分析区、3 个待办区和 4 个快捷按钮的布局比例。

验收标准：

- 首页采用“上半部分看数据、下半部分处理待办”的均衡布局。
- 首页首版使用已有飞书表搭建可见部分，缺失数据源在任务中明确补齐。
- 运营驾驶舱页面包含指标卡、订单状态图、库存风险图、商品排行榜、待发仓列表、今日到货列表和低库存补货建议。
- 草图页面包含 UTF-8 字符集声明，中文内容在浏览器中正常显示。
- 后续真实搭建以该 HTML 草图的信息层级为准，飞书原生组件只需接近布局和业务结构，不要求逐像素还原 CSS。

测试方法：人工审查 DEV_SPEC 与草图页面；确认 `.superpowers/` 不进入 Git 追踪。

##### H7：搭建飞书应用首页运营驾驶舱

目标：在当前已改名的飞书应用中按 H6 HTML 草图重新搭建首页，形成“上方看指标与趋势、下方处理业务待办”的运营驾驶舱。

修改文件：

- 飞书多维表格应用页面配置
- `DEV_SPEC.md`

实现类/函数：

- 首页指标区：展示今日订单、待发仓确认、低库存 SKU、今日到货和待审批采购单 5 个指标卡。
- 首页分析区：展示订单状态分布、库存风险和热门商品 3 个分析组件；真实图表能力不足时使用绑定真实表的列表、统计视图或明确占位。
- 首页待办区：展示待发仓确认列表、今日到货采购单和待审批采购单 3 个业务待办组件。
- 首页操作区：展示同步库存余额、同步采购单、发送今日到货通知和刷新排行榜 4 个快捷入口；无法绑定实际按钮动作时使用明确文本入口说明。

验收标准：

- 应用左侧存在“运营驾驶舱”页面。
- 首页组件按 H6 草图展示，视觉结构包含指标区、分析区、待办区和操作区。
- 指标区至少出现今日订单、待发仓确认、低库存 SKU、今日到货和待审批采购单 5 个业务指标标题。
- 分析区至少出现订单状态分布、库存风险和热门商品 3 个业务组件标题。
- 待办区至少出现待发仓确认列表、今日到货采购单和待审批采购单 3 个业务组件标题。
- 操作区至少出现同步库存余额、同步采购单、发送今日到货通知和刷新排行榜 4 个快捷入口标题。
- 已有飞书表能绑定的组件使用真实数据；缺失数据源组件显示明确占位或空状态。
- Chrome 验证页面可预览，组件标题、数据源和布局与规范一致。

测试方法：使用 Chrome 打开飞书应用预览并截图/人工核对；检查相关表同步接口返回正常。

##### H8：实现订单与订单明细飞书表同步

目标：为飞书应用业务操作页补齐订单履约和订单明细真实数据源，使订单履约中心能够同时查看订单主状态和订单行明细。

修改文件：

- `services/mock-api/app/routers/warehouse/orders.py`
- `services/mock-api/app/warehouse_store.py`
- `services/feishu-adapter/app/main.py`
- `services/feishu-adapter/tests/test_feishu_adapter.py`
- `services/mock-api/tests/test_api.py`
- `services/mock-api/tests/test_warehouse_store.py`
- `n8n/workflows/order-fulfillment-table-sync.json`
- `n8n/workflows/order-items-table-sync.json`
- `tests/test_department_workflows.py`
- `DEV_SPEC.md`

实现类/函数：

- `get_order_fulfillment_table_schema()`：返回订单履约飞书表字段契约。
- `get_order_fulfillment_table_rows()`：返回待付款、待发仓确认、待出库和已发货订单 read model。
- `get_order_items_table_schema()`：返回订单明细飞书表字段契约。
- `get_order_items_table_rows()`：返回订单、商品、仓库、库位、数量和状态组成的订单明细 read model。
- `sync_order_fulfillment_table()`：创建或复用 `Order Fulfillment` 飞书表并按 `order_id` upsert 订单总览记录。
- `sync_order_items_table()`：创建或复用 `Order Items` 飞书表并按 `Order Item ID` upsert 订单明细记录。
- `Order Fulfillment Table Sync`：每 10 分钟调用 `/orders/fulfillment-table/sync` 刷新订单履约飞书表。
- `Order Items Table Sync`：每 10 分钟调用 `/orders/items-table/sync` 刷新订单明细飞书表。

验收标准：

- mock-api 提供订单履约和订单明细的 table schema / table rows 端点。
- feishu-adapter 提供 `/orders/fulfillment-table/sync` 和 `/orders/items-table/sync` 端点。
- 两个同步端点在配置缺失时返回明确 not configured 响应。
- 两个同步端点在配置完整时能够使用 table_id-first 策略补齐字段并 upsert 记录。
- 返回结果包含 table_id、table_name、table_url、synced_count 和 items。
- 返回结果包含 page_count，用于确认订单履约和订单明细同步读取了多少页源数据。
- 订单总览以 `order_id` 作为业务单号展示和同步标识，不展示 PostgreSQL 自增 `id`；其余字段与 `orders` 表业务字段保持一致，不展示聚合摘要字段。
- 订单总览不展示 `id` 或 `created_by`。
- 订单总览中的“发货”和“退货”按钮由业务人员在飞书页面中人工创建；订单同步流程只能补齐订单字段和更新记录，不能删除这两个按钮字段。
- `/warehouse/orders/{order_id}/fulfillment/confirm` 作为订单总览“发货”按钮目标接口：未传 `warehouse_id` 时自动选择可满足整单且总库存最多的仓库；确认后填写 `delivery_provider_id`、`delivery_provider_name`、`tracking_no`、`selected_warehouse_id` 和 `selected_warehouse_name`，同步扣减库存余额，写入库存流水，将订单和明细更新为 `shipped`，并刷新订单总览与库存流水飞书表。
- “退货”按钮的目标后端动作：更新订单 `status`，写入退货库存流水，并刷新订单总览与库存流水飞书表。
- 订单明细表可以展示 `Order Item ID` 作为业务行标识，但不展示数据库自增 `id`。
- 订单履约表有独立 n8n 定时同步任务，并调用 `/orders/fulfillment-table/sync` 端点。
- 订单明细表有独立 n8n 定时同步任务，并调用 `/orders/items-table/sync` 端点。
- 订单履约和订单明细同步复用 H1 分页能力，支持超过 100 条记录的源端 read model。

测试方法：`uv run --project services/feishu-adapter pytest services\feishu-adapter\tests\test_feishu_adapter.py -q`；`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py services\mock-api\tests\test_warehouse_store.py -q`；`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

##### H9：实现商品飞书表同步

目标：为飞书应用商品运营中心补齐独立商品主数据表，使员工能够在飞书中查看真实商品图片、价格、分类、评分、库存摘要和排行榜摘要。

修改文件：

- `services/mock-api/app/warehouse_store.py`
- `services/mock-api/app/routers/search.py`
- `services/mock-api/app/routers/product_details.py`
- `services/mock-api/tests/test_api.py`
- `services/mock-api/tests/test_warehouse_store.py`
- `services/feishu-adapter/app/main.py`
- `services/feishu-adapter/tests/test_feishu_adapter.py`
- `n8n/workflows/items-table-sync.json`
- `fixtures/data/items.json`
- `tests/test_department_workflows.py`
- `DEV_SPEC.md`

实现类/函数：

- `items.image`：商品主数据图片地址字段，保存可直接访问的 URL、OSS 对象 URL 或不含 bucket 的 OSS object key，由 `ALIYUN_OSS_BUCKET` 决定运行环境中的真实 bucket。
- `get_items_table_schema()`：返回商品飞书表字段契约，包含 Product Image、Image URL、Item Name、Brand、Category、Price、Rating、Review Count 和 Source Version。
- `get_items_table_rows()`：返回商品主数据 read model。
- `sync_items_table()`：创建或复用 `Items` 飞书表并按 `Item ID` upsert 商品记录。
- `build_items_table_record_fields()`：将数据库 `items.image` 转换为飞书图片字段值，并同时保留原始 Image URL 便于排查。
- `Items Table Sync`：每 10 分钟调用 `/items/table/sync` 刷新商品主数据飞书表。

验收标准：

- `items` 表包含 `image` 字段，fixture 和 PostgreSQL schema 初始化都能写入默认图片 URL。
- 商品详情和商品飞书 read model 优先使用 `items.image`，缺失时才使用占位图。
- 飞书商品表包含 `Product Image` 真实图片字段和 `Image URL` 文本字段；`Product Image` 使用 H1 图片上传能力写入飞书 `file_token`。
- 已存在的旧版 Items 表如果保留 `Image` 文本字段，同步时也应回填该字段为 `Image URL` 的同一原始图片引用，保证旧视图不显示空值；新建表不再主动创建 `Image` 字段。
- 数据库中的 OSS 图片 URL 可以通过配置生成可下载地址并上传到飞书，多维表格中展示真实图片缩略图。
- 同一图片 URL 多次同步应复用已上传的飞书图片 token，避免重复上传和触发飞书限流。
- 图片上传失败时商品行仍应同步，`Image URL` 保留原始地址，响应中返回失败数量和失败原因。
- 商品表分类字段使用分类展示名，不直接展示内部 `category_id`。
- 商品表有独立 n8n 定时同步任务，并调用 `/items/table/sync` 端点。
- 商品同步复用 H1 分页能力，支持超过 100 条商品主数据同步。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py services\mock-api\tests\test_warehouse_store.py -q`；`uv run --project services/feishu-adapter pytest services\feishu-adapter\tests\test_feishu_adapter.py -q`；`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

##### H10：实现秒杀活动和秒杀结果飞书表同步

目标：为飞书应用商品运营中心补齐秒杀活动和秒杀结果数据源，使运营人员能查看活动配置、库存配额、实时结果和关联订单。

修改文件：

- `services/mock-api/app/routers/flash_sales.py`
- `services/mock-api/app/warehouse_store.py`
- `services/mock-api/tests/test_api.py`
- `services/mock-api/tests/test_warehouse_store.py`
- `services/feishu-adapter/app/main.py`
- `services/feishu-adapter/tests/test_feishu_adapter.py`
- `n8n/workflows/flash-sales-table-sync.json`
- `n8n/workflows/flash-sale-claims-table-sync.json`
- `tests/test_department_workflows.py`
- `DEV_SPEC.md`

实现类/函数：

- `get_flash_sales_table_schema()`：返回秒杀活动飞书表字段契约。
- `get_flash_sales_table_rows()`：返回活动商品、价格、库存配额、状态和时间窗口 read model。
- `get_flash_sale_claims_table_schema()`：返回秒杀结果飞书表字段契约。
- `get_flash_sale_claims_table_rows()`：返回用户、商品、订单和结果状态 read model。
- `sync_flash_sales_table()`：创建或复用 `Flash Sales` 飞书表并按 `Flash Sale ID` upsert 活动记录。
- `sync_flash_sale_claims_table()`：创建或复用 `Flash Sale Claims` 飞书表并按 `Claim ID` upsert 抢购结果记录。
- `Flash Sales Table Sync`：每 10 分钟调用 `/flash-sales/table/sync` 刷新秒杀活动飞书表。
- `Flash Sale Claims Table Sync`：每 10 分钟调用 `/flash-sales/claims-table/sync` 刷新秒杀结果飞书表。

验收标准：

- mock-api 提供秒杀活动和秒杀结果的 table schema / table rows 端点。
- feishu-adapter 提供 `/flash-sales/table/sync` 和 `/flash-sales/claims-table/sync` 端点。
- 秒杀活动表展示商品名称、活动价、商品原价、库存配额、状态和时间窗口。
- 秒杀结果表展示用户、商品、订单、状态和时间，不展示内部数据库实现字段。
- 秒杀活动表和秒杀结果表都有独立 n8n 定时同步任务。
- 秒杀活动和秒杀结果同步复用 H1 分页能力，支持超过 100 条活动或抢购结果同步。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py -q`；`uv run --project services/feishu-adapter pytest services\feishu-adapter\tests\test_feishu_adapter.py -q`；`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

##### H11：搭建飞书应用业务操作页

目标：搭建订单、库存、采购和商品运营页面，让员工能从飞书应用处理核心业务，并形成接近应用预览图的业务操作台布局。

修改文件：

- 飞书多维表格应用页面配置
- `n8n/workflows/product-operations-table-sync.json`
- `tests/test_department_workflows.py`
- `DEV_SPEC.md`

实现类/函数：

- 订单履约中心：使用顶部状态摘要、主订单列表和筛选入口展示待付款、待发仓确认、待出库和已发货订单。
- 库存管理中心：使用库存状态摘要、库存余额列表、低库存视图和仓库/库位筛选支撑库存处理。
- 采购管理中心：使用采购待办摘要、采购单列表、审批状态筛选和今日到货入口支撑采购处理。
- 商品运营中心：使用商品运营摘要、商品运营列表、Flash Deals/排行榜字段和筛选入口支撑商品运营。
- `Product Operations Table Sync`：每 10 分钟调用 `/products/operations-table/sync` 刷新商品运营飞书表。

验收标准：

- 应用左侧存在订单履约、库存管理、采购管理和商品运营页面。
- 每个页面至少包含一个业务摘要区、一个真实数据列表和一个业务筛选入口。
- 订单履约中心绑定 `Order Fulfillment`，库存管理中心绑定 `Warehouse Inventory Balances`，采购管理中心绑定 `Procurement Purchase Orders`，商品运营中心绑定 `Product Operations`。
- 页面命名、组件标题和字段展示与 TalonMart 业务术语一致。
- 页面布局避免只堆一个空表，应呈现“顶部看状态、下方处理列表”的业务操作台结构。
- 所有页面组件只能绑定真实飞书表或明确的空状态，不展示误导性假数据。
- 商品运营表有独立 n8n 定时同步任务，并调用 `/products/operations-table/sync` 端点。

测试方法：使用 Chrome 逐页预览；人工核对页面组件、数据源、筛选行为和业务操作台布局；`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

##### H12：实现飞书应用联调与验收门禁

目标：验证飞书应用、feishu-adapter、多维表格同步和业务 API 的端到端协作。

修改文件：

- `services/feishu-adapter/tests/test_feishu_adapter.py`
- `tests/test_department_workflows.py`
- `README.md`
- `DEV_SPEC.md`

实现类/函数：

- 飞书应用验收清单：记录页面、组件、数据源和按钮动作的检查项。
- 表格同步回归测试：验证库存、采购和通知相关接口稳定。
- Chrome 验证流程：记录从应用页面进入关键业务表和触发机器人动作的步骤。

验收标准：

- 首页和业务操作页都能打开预览。
- 库存、采购、订单发仓和采购到货相关同步接口通过测试。
- 任务结束摘要包含飞书页面验证结果、未接入的数据源和后续补齐任务。

测试方法：`uv run --project services/feishu-adapter pytest services\feishu-adapter\tests\test_feishu_adapter.py -q`；`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`；Chrome 人工验收飞书应用页面。

##### H13：实现飞书表全量对账策略

目标：为所有飞书表同步补齐源端删除和飞书旧记录处理策略，使飞书表不因历史同步产生长期脏数据。

修改文件：

- `services/feishu-adapter/app/main.py`
- `services/feishu-adapter/tests/test_feishu_adapter.py`
- `DEV_SPEC.md`

实现类/函数：

- `reconcile_missing_records()`：比较本次源端 keys 和飞书现有 keys。
- `mark_feishu_record_inactive()`：对源端已不存在的飞书记录写入 inactive 状态或同步备注。
- `delete_feishu_record()`：在显式参数允许时删除源端已不存在的飞书记录。

验收标准：

- 默认策略为标记 inactive，不直接删除飞书记录。
- 显式传入 `delete_missing=true` 时才允许删除源端不存在的飞书记录。
- 对账结果返回 active_count、inactive_count、deleted_count 和 skipped_count。

测试方法：`uv run --project services/feishu-adapter pytest services\feishu-adapter\tests\test_feishu_adapter.py -q`

#### 阶段 I：Quality And Delivery

##### I1：统一全量验证命令

目标：把后端、前端、workflow 和 Compose 验证命令统一成文档和脚本。

修改文件：

- `DEV_SPEC.md`
- `README.md`
- `scripts/verify_local.ps1`

实现类/函数：

- `verify_local.ps1`：执行根测试、服务测试、前端测试和 Compose 校验。

验收标准：

- 开发者可以一键或按文档完成本地验证。

测试方法：`powershell -ExecutionPolicy Bypass -File scripts\verify_local.ps1`

##### I2：强化 run log 与错误回放

目标：保留运行日志、失败事件和回放入口。

修改文件：

- `services/mock-api/app/main.py`
- `scripts/replay_failed_event.ps1`

实现类/函数：

- `create_run_log()`：写入运行日志。
- `create_dead_letter()`：记录失败事件。
- `replay_event()`：创建回放任务。

验收标准：

- 失败事件可查询、可回放。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py -q`

##### I3：强化 workflow 结构测试

目标：防止 workflow 文件缺少入口或关键工具节点。

修改文件：

- `tests/test_department_workflows.py`

实现类/函数：

- Workflow existence tests：验证关键 workflow 文件。
- Tool node tests：验证工具节点名称和 webhook。

验收标准：

- workflow 结构变更会被测试捕获。

测试方法：`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

##### I4：强化文档一致性测试

目标：确保根 DEV_SPEC、README 和关键运行说明保持一致。

修改文件：

- `tests/test_current_docs.py`
- `DEV_SPEC.md`
- `README.md`

实现类/函数：

- `test_current_docs.py`：验证文档存在、关键章节和命令。

验收标准：

- 删除或重命名关键内容时测试失败。

测试方法：`uv run --project services/mock-api pytest tests\test_current_docs.py -q`

##### I5：增加本地一键验收脚本

目标：降低人工验证成本。

修改文件：

- `scripts/verify_local.ps1`

实现类/函数：

- `Invoke-PythonTests`：运行 Python 测试。
- `Invoke-FrontendTests`：运行前端测试。
- `Invoke-ComposeCheck`：运行 Compose 校验。

验收标准：

- 任一阶段失败时脚本返回非零退出码。

测试方法：`powershell -ExecutionPolicy Bypass -File scripts\verify_local.ps1`

##### I6：增加演示前健康检查脚本

目标：演示前检查服务、端口、fixtures 和关键 API。

修改文件：

- `scripts/demo_check.ps1`

实现类/函数：

- `Test-ApiHealth`：检查服务 health。
- `Test-Fixtures`：检查演示数据。
- `Test-Frontend`：检查前端入口。

验收标准：

- 脚本能明确指出缺失服务或配置。

测试方法：`powershell -ExecutionPolicy Bypass -File scripts\demo_check.ps1`

##### I7：强化 Docker 启动说明

目标：补充本地启动、端口、环境变量和常见问题。

修改文件：

- `README.md`
- `docker-compose.yml`

实现类/函数：

- README local run section：说明启动顺序和端口。
- Compose healthcheck：补充服务健康检查。

验收标准：

- 新开发者可按文档启动服务并访问前端/API。

测试方法：`docker compose -p after-sales-implementation up -d --build`

#### 阶段 J：RPA Data Operations

阶段结构：

- **通用能力 A：影刀网页导出 CSV**。通用模板统一输入循环、页面等待、站点适配调用、成功/失败行收集和原始 CSV 导出。
- **具体实现 A1：京东商品采集**。京东适配子流程从商品 URL 清单采集当前可见商品字段，形成 dataset_type 为 jd_product 的原始 CSV。
- **通用能力 B：pandas 数据处理**。通用核心统一文件读取、dataset contract、processor 路由、通用校验、标准化输出、批次清单、归档和重放。
- **具体实现 B1：京东商品处理**。jd_product processor 处理影刀京东商品 CSV，输出标准化 CSV 和失败 CSV。

阶段边界：阶段 J 只交付通用文件能力和京东商品首个实现，不新增数据库表，不修改 items 表结构或数据，不直接写入 PostgreSQL，也不接入 mock-api、Operations Workflow 或飞书 read model。

##### J1：定义通用网页 CSV 交付与处理扩展契约

目标：定义影刀通用模板、站点实现、原始 CSV 和 pandas dataset processor 之间稳定的扩展边界，并固化京东商品首个 dataset contract。

修改文件：

- `DEV_SPEC.md`
- `rpa/yingdao/README.md`
- `services/data-ops/src/data_ops/core/contracts.py`
- `fixtures/rpa/jd_product_urls.csv`
- `fixtures/rpa/jd_product_export.csv`
- `tests/test_current_docs.py`

实现类/函数：

- `WebPageExportContract`：定义 dataset_type、输入清单、通用输出列、站点扩展列、页面状态和失败代码。
- `DatasetContract`：定义 pandas processor 的必填列、可选列、类型、唯一性规则和输出文件名。
- `ProcessorContract`：定义 processor 接收 DataFrame 和 contract 后返回标准行、失败行及统计摘要。
- `RuntimeDirectoryContract`：固定 var/rpa/inbox、var/rpa/normalized、var/rpa/archive 和 var/rpa/failed 的职责。
- `jd_product contract`：定义京东 URL 输入和原始 CSV 字段，不把京东字段放进通用 contract。

验收标准：

- 通用原始 CSV 至少包含 dataset_type、batch_id、input_index、source_url、captured_at、crawl_status 和 error_code。
- 新站点实现可以追加站点字段，但不能改变通用列语义。
- 新 dataset processor 可以通过 dataset_type 注册，不修改 CSV 读取和批次核心。
- jd_product URL fixture 和原始 CSV fixture 只使用合成或脱敏数据。
- 京东字段、展示价格语义、采集地区和页面状态在 jd_product contract 中明确。
- 契约明确不执行数据库 DDL、数据库写入或 items 修改。

测试方法：uv run --project services/mock-api pytest tests\test_current_docs.py -q

##### J2：建立影刀通用网页导出 CSV 模板

目标：建立可复用的影刀主流程骨架，使具体网站只实现页面打开、就绪判断和字段提取子流程。

修改文件：

- `影刀 RPA 通用网页导出模板应用`
- `rpa/yingdao/templates/web-page-to-csv.md`
- `rpa/yingdao/README.md`

实现类/函数：

- `LoadInputRows`：读取输入 CSV，并为每行分配稳定 input_index。
- `InvokeSiteAdapter`：按 dataset_type 调用对应站点适配子流程。
- `AppendExportRow`：无论成功、部分成功或失败都追加一条带通用状态的结果。
- `ExportRawCsv`：使用固定列头和 batch_id 导出原始 UTF-8 CSV。
- `StopForManualVerification`：遇到登录、验证码、权限或访问限制时停止并保留现场。

验收标准：

- 模板不包含京东 CSS/XPath、京东字段名或京东页面状态判断。
- 每个输入行恰好生成一条结果行，单行字段缺失不能静默丢弃输入。
- 站点适配失败不会破坏已经收集的结果，并产生明确 error_code。
- 原始文件名包含 dataset_type、batch_id 和采集时间。
- 凭据、Cookie、Token 和验证码内容不写入 CSV、日志或仓库。
- 模板只操作已授权或允许访问的页面，不实现验证码或访问限制绕过。

测试方法：使用本地或脱敏测试页面人工运行影刀模板，验证成功、字段缺失、适配失败和人工验证四种结果都能导出 CSV

##### J3：实现影刀京东商品采集适配器

目标：基于 J2 通用模板实现首个站点适配子流程，从京东商品 URL 清单采集当前页面可见商品信息。

修改文件：

- `影刀 RPA 京东商品适配子流程`
- `rpa/yingdao/implementations/jd-product-export.md`
- `fixtures/rpa/jd_product_urls.csv`
- `fixtures/rpa/jd_product_export.csv`

实现类/函数：

- `ParseJdSkuId`：从输入 URL 或当前页面解析京东 SKU。
- `WaitForJdProductPage`：等待商品标题和核心区域加载，区分页面就绪、下架、无货、登录提示、验证码和超时。
- `ClassifyJdPageState`：返回 success、partial 或 failed 及稳定 error_code。
- `ExtractJdProduct`：采集 jd_sku_id、title、display_price、shop_name、primary_image_url 和 capture_region。
- `BuildJdProductRow`：把京东字段与 J1 通用列合并后返回模板主流程。

验收标准：

- 输入文件只包含 input_index 和 product_url；每个 URL 对应一个明确商品 SKU。
- 第一版不抓取搜索结果列表，不枚举全部颜色、容量等变体。
- display_price 表示采集时、当前登录状态和 capture_region 下的页面展示价格，不表达长期或全地区价格。
- 页面下架、无货、字段部分缺失和 URL 无效均有明确状态，不伪造默认商品值。
- 遇到登录、验证码或访问限制立即停止并转人工处理，不尝试绕过。
- 至少使用正常商品、第三方店铺、无货或下架、无效 URL 等样例完成人工验收。

测试方法：影刀人工运行 fixtures\rpa\jd_product_urls.csv，核对输入行数、原始 CSV 行数、页面状态、错误代码和错误截图

##### J4：建立 pandas 通用 CSV 处理框架

目标：建立与具体网站无关的 pandas 文件处理核心，通过 dataset_type 将原始 CSV 路由到对应 processor。

修改文件：

- `services/data-ops/pyproject.toml`
- `services/data-ops/uv.lock`
- `services/data-ops/src/data_ops/__init__.py`
- `services/data-ops/src/data_ops/cli.py`
- `services/data-ops/src/data_ops/core/__init__.py`
- `services/data-ops/src/data_ops/core/contracts.py`
- `services/data-ops/src/data_ops/core/csv_io.py`
- `services/data-ops/src/data_ops/core/validation.py`
- `services/data-ops/src/data_ops/processors/__init__.py`
- `services/data-ops/src/data_ops/processors/registry.py`
- `services/data-ops/tests/test_core.py`
- `services/data-ops/tests/test_processor_registry.py`

实现类/函数：

- `DatasetProcessor`：定义 validate、normalize 和 split_results 的 processor 接口。
- `read_source_file`：读取 CSV/XLSX，显式处理编码、分隔符、Sheet 和字符串列。
- `validate_common_columns`：验证 J1 规定的通用列、行数和状态值。
- `register_processor`：注册 dataset_type 到 processor factory 的映射。
- `get_processor`：按 dataset_type 获取 processor，未知类型返回明确错误。
- `process_dataset`：执行通用读取、processor 调用、输出写入和统计汇总。
- `write_canonical_csv`：以 UTF-8、固定列顺序和原子替换方式写出 CSV。

验收标准：

- core 和 registry 不导入京东 processor 的字段常量或页面规则。
- CLI 必须显式接收 dataset_type、输入路径和输出根目录。
- 未注册 dataset_type、缺少通用列、文件编码错误和空文件返回非零退出码。
- 相同输入、contract 和 processor 重复执行时输出内容保持一致。
- data-ops 不依赖数据库驱动，不包含 PostgreSQL、mock-api 或 items 写入代码。

测试方法：uv run --project services/data-ops pytest services\data-ops\tests\test_core.py services\data-ops\tests\test_processor_registry.py -q

##### J5：实现通用批次、归档与失败重放

目标：用文件系统和 JSON manifest 为所有 dataset_type 提供一致的批次追踪、成功归档、失败隔离和重放能力。

修改文件：

- `services/data-ops/src/data_ops/core/batch_manifest.py`
- `services/data-ops/src/data_ops/cli.py`
- `services/data-ops/tests/test_batch_manifest.py`
- `scripts/run_data_ops.ps1`
- `.gitignore`

实现类/函数：

- `calculate_file_sha256`：计算原始文件内容摘要。
- `build_batch_manifest`：记录 batch_id、dataset_type、输入摘要、processor、行数、状态和输出文件。
- `archive_successful_batch`：成功后归档原始文件、标准化/失败 CSV 和 manifest。
- `quarantine_failed_batch`：处理失败时保存原始文件、错误摘要和 manifest。
- `replay_batch`：使用 manifest 中的 dataset_type、输入文件和 contract 重放批次。

验收标准：

- manifest 不包含账号、密码、Cookie、Token 或未脱敏行内容。
- 不同 dataset_type 使用同一 manifest 结构和目录状态。
- 只有输出文件和 manifest 原子落盘后，原始文件才进入 archive。
- 处理失败进入 failed，并保留稳定错误代码和重放入口。
- 同一文件摘要和 contract 重复处理时可识别重复批次。
- var/rpa 被 Git 忽略，批次状态不写入数据库。

测试方法：uv run --project services/data-ops pytest services\data-ops\tests\test_batch_manifest.py -q

##### J6：实现京东商品 pandas processor

目标：实现与 J3 原始 CSV 对应的 jd_product processor，输出标准化京东商品 CSV 和失败 CSV。

修改文件：

- `services/data-ops/src/data_ops/processors/jd_product.py`
- `services/data-ops/src/data_ops/processors/registry.py`
- `services/data-ops/tests/test_jd_product_processor.py`
- `fixtures/rpa/jd_product_export.csv`

实现类/函数：

- `JdProductProcessor.validate`：验证通用列及 jd_sku_id、title、display_price、shop_name、primary_image_url 和 capture_region。
- `JdProductProcessor.normalize`：清理字符串、标准化 SKU、解析展示价格并统一 captured_at。
- `JdProductProcessor.split_results`：把 success、partial 和 failed 转换为标准化结果与失败结果。
- `identify_jd_duplicates`：按 input_index 保证输入可追踪，并标记重复 URL 或重复 SKU。
- `register_jd_product_processor`：以 jd_product 注册 processor。

验收标准：

- processor 只处理 jd_product contract，不在通用 core 中增加京东条件分支。
- success 行必须保留可追踪 source_url、jd_sku_id、title、capture_region 和 captured_at。
- 展示价格同时保留原始文本和可解析的数值字段；无法解析时进入 partial 或 failed，不写零价替代。
- failed CSV 保留 input_index、source_url、crawl_status 和 error_code。
- 输入行数等于标准化结果、失败结果和显式重复结果的可对账总数。
- processor 不查询、不更新 items，也不创建商品映射或数据库记录。

测试方法：uv run --project services/data-ops pytest services\data-ops\tests\test_jd_product_processor.py -q

##### J7：打通京东商品端到端文件链路

目标：把影刀京东适配器、原始 CSV、pandas processor、manifest、archive 和 failed 串成首个可演示实例。

修改文件：

- `rpa/yingdao/implementations/jd-product-export.md`
- `rpa/yingdao/README.md`
- `scripts/run_data_ops.ps1`
- `services/data-ops/tests/test_jd_product_processor.py`
- `services/data-ops/tests/test_batch_manifest.py`

实现类/函数：

- `jd_product RPA handoff`：影刀把原始 CSV 写入 var/rpa/inbox 并返回文件路径和 batch_id。
- `Invoke-JdProductProcessing`：使用 dataset_type=jd_product 调用 data-ops CLI。
- `JD product file E2E test`：使用脱敏原始 CSV 验证 processor、输出、manifest 和归档。
- `JD product replay test`：验证失败文件修正后可使用原 batch contract 重放。

验收标准：

- 输入 URL 数、影刀原始 CSV 行数和 pandas 对账总数一致。
- 正常行进入标准化 CSV，partial/failed 行保留原因且不静默丢失。
- 影刀中断后可从未处理 input_index 继续；pandas 失败后可独立重放，不重新抓取网页。
- 原始 CSV 保持不可变，标准化、失败、manifest 和 archive 文件可通过 batch_id 关联。
- 端到端流程仍不写数据库、不修改 items、不调用现有业务 API。

测试方法：uv run --project services/data-ops pytest services\data-ops\tests -q；powershell -ExecutionPolicy Bypass -File scripts\run_data_ops.ps1 -InputPath fixtures\rpa\jd_product_export.csv -DatasetType jd_product -OutputRoot D:\tmp\talonmart-rpa-acceptance；影刀京东商品人工验收

##### J8：实现扩展指南与阶段质量门禁

目标：证明阶段 J 的通用性，固化新增站点实现和新增 pandas processor 的步骤，并完成自动测试与影刀人工验收。

修改文件：

- `rpa/yingdao/README.md`
- `rpa/yingdao/templates/web-page-to-csv.md`
- `services/data-ops/tests/test_core.py`
- `services/data-ops/tests/test_processor_registry.py`
- `services/data-ops/tests/test_jd_product_processor.py`
- `services/data-ops/tests/test_batch_manifest.py`
- `tests/test_current_docs.py`
- `DEV_SPEC.md`

实现类/函数：

- `RPA extension checklist`：定义新增站点实现时必须提供的输入 fixture、页面状态、字段映射、错误代码和人工验收。
- `Processor extension checklist`：定义新增 dataset processor 时必须提供的 contract、注册项、标准/失败输出和测试。
- `Boundary assertions`：验证通用模板没有京东字段，通用 pandas core 没有站点字段，data-ops 没有数据库依赖或 items 修改。
- `Phase J E2E assertions`：验证 jd_product 同时复用了通用影刀模板和通用 pandas 核心。

验收标准：

- 文档能够指导第二个站点实现只新增 implementations 下的适配说明和对应 dataset processor。
- 通用能力与京东实现的目录、职责、测试和错误边界明确分离。
- 缺通用列、未知 dataset_type、京东字段缺失、价格不可解析、重复 SKU、空文件和失败重放均有测试。
- 影刀人工验收包含正常商品、第三方店铺、无货或下架、无效 URL、登录或验证中断等场景。
- 全文和测试确认阶段 J 未新增数据库表、未修改 items 表结构或数据，也未接入下游系统。

测试方法：uv run --project services/data-ops pytest services\data-ops\tests -q；uv run --project services/mock-api pytest tests\test_current_docs.py -q；影刀通用模板和京东商品适配器人工验收清单
