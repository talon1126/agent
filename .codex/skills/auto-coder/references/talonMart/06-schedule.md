<!-- synced-from: DEV_SPEC.md -->
<!-- reference: 任务计划与状态 -->

## 6. 项目排期

状态标记：`[ ]` 未开始，`[~]` 进行中，`[✔]` 已完成。

### 6.1 阶段总览表

| 阶段 | 阶段标题 | 目标 | 状态 |
| --- | --- | --- | --- |
| 阶段 A | Project Foundation | 建立本地运行、共享服务、测试入口和基础规范 | [✔] |
| 阶段 B | Warehouse Workflow | 完成仓储库存、履约、补货和飞书库存表闭环 | [✔] |
| 阶段 C | Procurement Workflow | 完成补货审批、采购单和采购飞书表闭环 | [✔] |
| 阶段 D | Delivery Workflow | 完成物流查询、异常和 case 闭环 | [✔] |
| 阶段 E | Operations Workflow | 完成跨领域只读摘要和运营建议闭环 | [✔] |
| 阶段 F | 电商项目 | 完成 TalonMart 商品、Departments 导购、购物车、秒杀和前端体验 | [✔] |
| 阶段 G | AImodel | 完成前端 AI 聊天、商品工具、会话记忆和 RAG MCP 集成 | [✔] |
| 阶段 H | Quality And Delivery | 完成全量质量门禁、演示脚本和部署检查 | [~] |

### 6.2 交付里程碑

| 阶段 | 项目当前位置 | 可用功能 | 验证方式 | 下一阶段入口 | 完成日期 |
| --- | --- | --- | --- | --- | --- |
| 阶段 A | 基础服务可运行 | Docker Compose、fixtures、Python/Node 测试入口 | `docker compose -p after-sales-implementation config --quiet` | Warehouse Workflow |  |
| 阶段 B | 仓储主链路可演示 | 库存查询、履约风险、补货申请、库存表同步 | `uv run --project services/mock-api pytest services\mock-api\tests\test_warehouse_store.py -q` | Procurement Workflow |  |
| 阶段 C | 采购主链路可演示 | 补货审批、采购单、到仓确认、采购表同步 | `uv run --project services/mock-api pytest services\mock-api\tests\test_procurement_router_structure.py -q` | Delivery Workflow |  |
| 阶段 D | 物流主链路可演示 | 物流状态、异常查询、case 创建 | `uv run --project services/mock-api pytest services\mock-api\tests\test_delivery_router_structure.py -q` | Operations Workflow |  |
| 阶段 E | 运营只读汇总可用 | 异常摘要、风险汇总、后续动作建议 | `uv run --project services/mock-api pytest tests\test_department_workflows.py -q` | 电商项目 |  |
| 阶段 F | 电商项目可用 | 商品、Departments 导购、详情、购物车、秒杀、AI 模式 | `pnpm --dir apps/talonmart-web test:unit` | AImodel |  |
| 阶段 G | AImodel 可用 | 流式聊天、工具调用、会话记忆、RAG MCP | `uv run --project services/ai-service pytest services\ai-service\tests -q` | Quality And Delivery |  |
| 阶段 H | 质量门禁持续完善 | 全量验证、演示检查、部署说明 | 全量测试矩阵 | 发布/演示 |  |

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
| B1 | 建立仓储数据模型和 repository | [✔] |  | 批次、库位、库存余额 |
| B2 | 实现库存查询与异常查询 API | [✔] |  | warehouse inventory |
| B3 | 实现履约风险和订单库存扣减 | [✔] |  | FEFO、整单同仓 |
| B4 | 实现补货申请创建 | [✔] |  | replenishment_requests |
| B5 | 实现采购到仓库存同步 | [✔] |  | arrived_unsynced -> synced |
| B6 | 实现飞书库存表/余额表同步 | [✔] |  | feishu-adapter sync endpoints |
| B7 | 实现 Warehouse n8n Workflow | [✔] |  | warehouse-workflow.json |
| B8 | 实现仓储测试与回归门禁 | [✔] |  | warehouse tests |

#### 阶段 C：Procurement Workflow

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| C1 | 建立采购数据模型和状态规则 | [✔] |  | replenishment_requests、purchase_orders |
| C2 | 实现补货申请查询、批准和驳回 | [✔] |  | requests.py |
| C3 | 实现批量批准和采购单复用 | [✔] |  | service.py |
| C4 | 实现采购单查询和到仓确认 | [✔] |  | purchase_orders.py |
| C5 | 实现采购飞书表 provision/sync | [✔] |  | feishu-adapter procurement endpoints |
| C6 | 实现 Procurement n8n Workflow | [✔] |  | procurement-workflow.json |
| C7 | 实现采购测试与回归门禁 | [✔] |  | procurement router tests |

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
| G8 | 实现 AImodel 测试与回归门禁 | [✔] |  | ai-service tests |

#### 阶段 H：Quality And Delivery

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| H1 | 统一全量验证命令 | [~] |  | uv、pnpm、docker compose |
| H2 | 强化 run log 与错误回放 | [✔] |  | run-logs、dead-letter、replay |
| H3 | 强化 workflow 结构测试 | [✔] |  | tests/test_department_workflows.py |
| H4 | 强化文档一致性测试 | [~] |  | tests/test_current_docs.py |
| H5 | 增加本地一键验收脚本 | [ ] |  | scripts/verify_local.ps1 |
| H6 | 增加演示前健康检查脚本 | [ ] |  | scripts/demo_check.ps1 |
| H7 | 强化 Docker 启动说明 | [ ] |  | README / compose |

### 6.4 总体进度表

| 阶段 | 总任务数 | 已完成 | 进度 |
| --- | ---: | ---: | --- |
| 阶段 A | 5 | 5 | 100% |
| 阶段 B | 8 | 8 | 100% |
| 阶段 C | 7 | 7 | 100% |
| 阶段 D | 6 | 6 | 100% |
| 阶段 E | 5 | 5 | 100% |
| 阶段 F | 7 | 7 | 100% |
| 阶段 G | 8 | 8 | 100% |
| 阶段 H | 7 | 2 | 29% |
| **总计** | **53** | **48** | **91%** |

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
- `fixtures/data/inventory_batches.json`：库存批次数据。
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

目标：以批次、库位和库存余额为核心维护仓储事实。

修改文件：

- `services/mock-api/app/warehouse_store.py`
- `fixtures/data/warehouses.json`
- `fixtures/data/inventory_batches.json`

实现类/函数：

- `WarehouseRepository`：封装 PostgreSQL 与 fixtures fallback。
- `get_warehouse_repository()`：提供仓储 repository 单例入口。

验收标准：

- 仓储数据可按商品、仓库、库位和批次读取。

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

- 接口返回商品、仓库、库位、批次、可用库存和风险字段。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py -q`

##### B3：实现履约风险和订单库存扣减

目标：支持订单创建时按仓库和 FEFO 扣减库存。

修改文件：

- `services/mock-api/app/routers/warehouse/orders.py`
- `services/mock-api/app/routers/warehouse/state.py`

实现类/函数：

- `create_warehouse_order()`：创建订单并扣减库存。
- `release_expired_orders()`：释放超时未付款订单库存。

验收标准：

- 库存不足时返回明确阻塞原因
- 订单失败不会留下错误扣减。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_warehouse_store.py -q`

##### B4：实现补货申请创建

目标：当库存低于阈值时创建补货申请，供采购 Workflow 审批。

修改文件：

- `services/mock-api/app/routers/procurement/requests.py`
- `services/mock-api/app/routers/procurement/state.py`

实现类/函数：

- `create_replenishment_request()`：创建补货申请。
- `list_replenishment_requests()`：查询补货申请。

验收标准：

- 补货申请包含 item、warehouse、quantity、reason 和状态。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_procurement_router_structure.py -q`

##### B5：实现采购到仓库存同步

目标：将已到仓未同步采购单写入库存批次和库位余额。

修改文件：

- `services/mock-api/app/routers/warehouse/purchase_orders.py`
- `services/mock-api/app/routers/warehouse/sync_jobs.py`

实现类/函数：

- `sync_arrived_purchase_orders()`：扫描并同步到仓采购单。
- `mark_purchase_order_synced()`：更新采购单仓储同步状态。

验收标准：

- 同步后库存事实增加，采购单状态从 `arrived_unsynced` 进入 `synced`。
- 飞书输入 `@warehouse 同步采购到仓库存` 后，Warehouse Workflow 应扫描已到仓未同步采购单并返回同步数量。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_warehouse_store.py -q`

##### B6：实现飞书库存表/余额表同步

目标：将仓储 read model 同步到飞书多维表格。

修改文件：

- `services/feishu-adapter/app/main.py`
- `services/feishu-adapter/app/view_template_builder.py`

实现类/函数：

- `provision_inventory_table()`：创建或复用库存表。
- `sync_inventory_table()`：同步库存快照。
- `sync_inventory_balances_table()`：同步库存余额。

验收标准：

- 接口返回 table_id、table_url、synced_count 和错误摘要。
- 飞书输入 `@warehouse 同步 item_vinda_tissue 库存到飞书` 后，应返回库存表链接、写入数量和同步状态。

测试方法：`uv run --project services/feishu-adapter pytest services\feishu-adapter\tests -q`

##### B7：实现 Warehouse n8n Workflow

目标：编排仓储消息、Agent 和工具调用。

修改文件：

- `n8n/workflows/warehouse-workflow.json`

实现类/函数：

- Warehouse webhook：接收飞书转发消息。
- Warehouse tool nodes：调用库存、履约、补货和同步工具。

验收标准：

- workflow 文件包含入口、Agent 和关键工具节点。
- 飞书输入 `@warehouse 查询 item_vinda_tissue 库存` 后，应返回批次、库位、可用库存和风险建议。

测试方法：`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

##### B8：实现仓储测试与回归门禁

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

##### C1：建立采购数据模型和状态规则

目标：定义补货申请、采购单和到仓状态。

修改文件：

- `services/mock-api/app/routers/procurement/schemas.py`
- `services/mock-api/app/routers/procurement/state.py`

实现类/函数：

- `ReplenishmentRequest`：补货申请结构。
- `PurchaseOrder`：采购单结构。

验收标准：

- 状态字段能表达待审批、已审批、未支付、已支付、到仓未同步和已同步。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_procurement_router_structure.py -q`

##### C2：实现补货申请查询、批准和驳回

目标：支持采购人员 review 补货申请。

修改文件：

- `services/mock-api/app/routers/procurement/requests.py`

实现类/函数：

- `approve_replenishment_request()`：批准补货申请。
- `reject_replenishment_request()`：记录驳回原因。

验收标准：

- 批准生成采购动作，驳回保留原因且不生成采购单。
- 飞书输入 `@procurement 批准 REQ-1001 生成采购单` 后，应返回采购单编号和申请状态。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py -q`

##### C3：实现批量批准和采购单复用

目标：批量处理待审批申请，避免重复创建采购单。

修改文件：

- `services/mock-api/app/routers/procurement/service.py`

实现类/函数：

- `approve_replenishment_requests_batch()`：批量批准申请。
- `get_or_create_purchase_order()`：按业务键复用采购单。

验收标准：

- 重复批准不会生成重复 PO。
- 飞书输入 `@procurement 批量批准生成采购单` 后，应返回处理数量、已复用采购单和新增采购单。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py -q`

##### C4：实现采购单查询和到仓确认

目标：维护采购单支付和到仓同步状态。

修改文件：

- `services/mock-api/app/routers/procurement/purchase_orders.py`

实现类/函数：

- `list_purchase_orders()`：查询采购单。
- `confirm_purchase_order_arrival()`：确认采购单到仓。

验收标准：

- 到仓确认只更新采购状态，不直接写仓储库存。
- 飞书输入 `@procurement PO-5001 已到仓库` 后，应将采购单标记为到仓未同步并返回当前仓储同步状态。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_procurement_router_structure.py -q`

##### C5：实现采购飞书表 provision/sync

目标：同步补货申请和采购单到飞书多维表格。

修改文件：

- `services/feishu-adapter/app/main.py`

实现类/函数：

- `provision_procurement_replenishment_requests_table()`：创建补货申请表。
- `sync_procurement_replenishment_requests_table()`：同步补货申请。
- `provision_procurement_purchase_orders_table()`：创建采购单表。
- `sync_procurement_purchase_orders_table()`：同步采购单。

验收标准：

- 表同步结果包含写入数量和表链接。
- 飞书输入 `@procurement 同步采购单` 后，应返回采购单表链接、写入数量和同步状态。

测试方法：`uv run --project services/feishu-adapter pytest services\feishu-adapter\tests\test_feishu_adapter.py -q`

##### C6：实现 Procurement n8n Workflow

目标：编排采购审批、采购单和飞书表同步工具。

修改文件：

- `n8n/workflows/procurement-workflow.json`

实现类/函数：

- Procurement webhook：接收采购消息。
- Procurement tool nodes：调用审批、采购单、同步工具。

验收标准：

- workflow 文件包含入口、Agent 和采购工具节点。
- 飞书输入 `@procurement 同步补货请求` 后，Procurement Workflow 应调用飞书表同步工具并返回同步结果。

测试方法：`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

##### C7：实现采购测试与回归门禁

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
- Home、Search、Department、Product Detail、Cart 页面使用统一商城顶部导航：蓝色主栏、TM 标识、Pickup 定位、搜索框、Account、Cart。
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

##### G2：实现 conversation/message/user_memory

目标：保存会话、消息和用户长期偏好。

修改文件：

- `services/ai-service/app/routers/AImodel/memory.py`

实现类/函数：

- `AImodelMemoryStore.initialize()`：初始化表结构。
- `create_conversation()`：创建会话。
- `append_message()`：追加消息。
- `upsert_user_memory()`：更新用户偏好。

验收标准：

- 同一用户可以查询历史会话并继续对话。

测试方法：`uv run --project services/ai-service pytest services\ai-service\tests\test_aimodel_memory.py -q`

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

目标：根据用户意图选择商品工具或 RAG 工具。

修改文件：

- `services/ai-service/app/routers/AImodel/service.py`

实现类/函数：

- `AImodelAgentService.chat_stream()`：编排消息、工具和流式输出。
- `build_agent_tools()`：构造 Agent 工具列表。

验收标准：

- 简单咨询、商品推荐、链接对比和知识问答都能走正确工具。

测试方法：`uv run --project services/ai-service pytest services\ai-service\tests\test_aimodel_agent.py -q`

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

验收标准：

- AImodel 调用 RAG 后可从 message 追溯 query trace。

测试方法：`uv run --project services/ai-service pytest services\ai-service\tests\test_aimodel_memory.py services\ai-service\tests\test_aimodel_rag_tool.py -q`

##### G8：实现 AImodel 测试与回归门禁

目标：覆盖 AImodel 核心工具、记忆和流式输出。

修改文件：

- `services/ai-service/tests/test_aimodel_agent.py`
- `services/ai-service/tests/test_aimodel_memory.py`
- `services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- `test_aimodel_agent.py`：验证 Agent 行为。
- `test_aimodel_memory.py`：验证会话和记忆。
- `test_aimodel_rag_tool.py`：验证 RAG MCP 工具边界。

验收标准：

- 目标测试稳定通过。

测试方法：`uv run --project services/ai-service pytest services\ai-service\tests\test_aimodel_agent.py services\ai-service\tests\test_aimodel_memory.py services\ai-service\tests\test_aimodel_rag_tool.py -q`

#### 阶段 H：Quality And Delivery

##### H1：统一全量验证命令

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

##### H2：强化 run log 与错误回放

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

##### H3：强化 workflow 结构测试

目标：防止 workflow 文件缺少入口或关键工具节点。

修改文件：

- `tests/test_department_workflows.py`

实现类/函数：

- Workflow existence tests：验证关键 workflow 文件。
- Tool node tests：验证工具节点名称和 webhook。

验收标准：

- workflow 结构变更会被测试捕获。

测试方法：`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

##### H4：强化文档一致性测试

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

##### H5：增加本地一键验收脚本

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

##### H6：增加演示前健康检查脚本

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

##### H7：强化 Docker 启动说明

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
