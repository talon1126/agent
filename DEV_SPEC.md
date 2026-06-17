# TalonMart Agent DEV_SPEC

> 本文档覆盖 `D:\Project\agent` 根项目的完整开发周期，用于指导 TalonMart 多 Workflow Agent 系统的设计、实现、测试、评审和阶段推进。
>
> `services/ai-service/rag` 是独立 RAG 子系统，内部摄取、检索、评估、Dashboard 和 MCP Server 设计由 `services/ai-service/rag/DEV_SPEC.md` 维护。本文档只描述根项目如何通过 **RAG MCP 服务** 调用它。

## 1. 项目概述

### 1.1 项目定位

TalonMart Agent 是一个本地优先的电商业务 Agent 系统。项目用 Vue 前端、FastAPI 服务、n8n Workflow、飞书适配器、PostgreSQL、Redis 和 fixtures 数据，构建一个可演示、可测试、可逐步扩展的电商运营与购物辅助平台。

系统按 **Workflow + 项目模块** 划分业务能力，而不是把所有能力塞进一个大 Agent：

- **Warehouse Workflow**：库存、批次、库位、履约风险、补货申请、库存表同步。
- **Procurement Workflow**：补货申请审批、采购单生成、采购单同步、到仓确认。
- **Delivery Workflow**：物流状态查询、物流异常查询、物流跟进 case。
- **Operations Workflow**：跨领域异常摘要、运营风险汇总、后续动作建议。
- **电商项目**：TalonMart 用户界面、商品搜索、Departments 导购、商品详情、购物车、秒杀和前端 API client。
- **AImodel**：前端 AI 模式、商品咨询、商品对比、会话记忆、RAG MCP 知识调用。

### 1.2 项目边界

根项目负责：

- 前端用户购物体验和 AI 模式交互。
- `ai-service` 中的 AImodel Agent、会话记忆、工具适配和 RAG MCP 客户端。
- `mock-api` 中的商品、购物车、配送地址、秒杀、订单、仓储、采购、物流和政策类确定性 API。
- `feishu-adapter` 中的飞书事件接入、多机器人配置、n8n 转发、飞书回复和多维表格同步。
- `n8n/workflows` 中的部门 Workflow 编排。
- Docker Compose 本地运行、PostgreSQL/Redis 基础设施、fixtures 和测试体系。

根项目不负责：

- RAG 子系统内部实现细节。
- 真实支付、真实物流承运商、真实 ERP/WMS/OMS 集成。
- 飞书线上应用权限、Base 运维和外部账号管理。

### 1.3 设计理念

- **Workflow first**：每个阶段围绕一个可演示 Workflow 推进，便于用户逐个检查业务闭环。
- **事实源优先**：商品、库存、订单、采购和物流事实来自 `mock-api` / PostgreSQL，Agent 只负责调用工具和组织回答。
- **边界可解释**：n8n 编排、FastAPI 工具、飞书适配、前端 API client 和数据库职责分层清晰。
- **本地优先**：Docker Compose、fixtures 和 uv 命令让开发者能在本机完成主要验证。
- **TDD 驱动**：每个任务都有目标、修改文件、实现对象、验收标准和测试方法。

## 2. 核心特点

### 2.1 Workflow 与项目模块分阶段开发

项目排期按业务 Workflow 和项目模块组织。Warehouse、Procurement、Delivery、Operations 是 n8n 驱动的部门 Workflow；电商项目和 AImodel 是应用模块，不按 Workflow 表述。每个阶段都包含业务目标、后端 API、入口、测试和验收标准，适合让 AI 按任务逐步实现，也适合用户实时检查进度。

### 2.2 确定性业务工具

Agent 不直接修改业务事实。库存、采购单、订单、物流、商品和购物车都通过 `mock-api` 的确定性接口处理，避免大模型凭空生成业务状态。

### 2.3 飞书与前端双入口

企业内部 Workflow 通过飞书机器人和 n8n 进入，普通用户通过 TalonMart 前端进入。两类入口共用后端事实 API，但交互方式不同。

### 2.4 AImodel + RAG MCP

AImodel 负责用户购物咨询和商品对比，商品事实由 `mock-api` 提供，选购指南和文档知识由 RAG MCP 服务提供。RAG MCP 通过 stdio 子进程复用，避免每次查询重复启动。

### 2.5 飞书多维表格 read model

仓储和采购数据可同步到飞书多维表格，供运营人员查看、筛选和协作。飞书表只作为展示层，不反向成为系统事实源。

### 2.6 全链路测试与可观测

项目同时使用 Python pytest、Vue Vitest、Playwright、workflow 结构测试、run log、RAG trace 关联和 Docker Compose 校验，覆盖代码、工作流和运行配置。

## 3. 技术选型

### 3.1 总体技术栈

| 层级 | 技术 | 说明 |
| --- | --- | --- |
| 前端 | Vue 3、TypeScript、Vite、Vue Router、Pinia、Vitest、Playwright | TalonMart 页面、AI 模式和前端测试 |
| AI 服务 | FastAPI、Pydantic、psycopg、LangChain、MCP SDK | AImodel Agent、会话记忆、工具适配、RAG MCP client |
| 业务 API | FastAPI、Pydantic、SQLAlchemy、psycopg、Redis | 商品、购物车、仓储、采购、物流、秒杀和政策接口 |
| 飞书适配 | FastAPI、httpx、lark-oapi | 飞书事件、多机器人、回复、多维表格同步 |
| Workflow | n8n | 部门 Workflow 编排、工具调用和定时任务 |
| 数据库 | PostgreSQL | 业务事实、会话、记忆和同步状态 |
| 缓存/原子计数 | Redis | 秒杀库存扣减和补偿 |
| Python 包管理 | uv | Python 依赖、虚拟环境、测试和脚本统一入口 |
| 部署 | Docker Compose | 本地服务编排和依赖启动 |

### 3.2 Python 包管理

Python 子项目统一使用 **uv** 执行测试、脚本和静态检查：

```powershell
uv run --project services/mock-api pytest services\mock-api\tests -q
uv run --project services/ai-service pytest services\ai-service\tests -q
uv run --project services/feishu-adapter pytest services\feishu-adapter\tests -q
uv run --project services/mock-api ruff check services\mock-api
```

依赖声明以各服务 `pyproject.toml` 为准。后续新增或升级依赖时应通过 `uv add`、`uv lock` 或等价 uv 命令维护锁定结果，不能在普通测试任务中隐式升级依赖。

### 3.3 服务职责

| 服务 | 位置 | 职责 |
| --- | --- | --- |
| TalonMart Web | `apps/talonmart-web` | 商品浏览、搜索、详情、购物车、秒杀、AI 模式 |
| ai-service | `services/ai-service` | AImodel Agent、会话记忆、工具编排、RAG MCP 调用 |
| mock-api | `services/mock-api` | 电商业务事实 API 和 fixtures/PostgreSQL fallback |
| feishu-adapter | `services/feishu-adapter` | 飞书事件、n8n 转发、多维表格同步 |
| postgres | `services/postgres` | PostgreSQL 镜像和初始化脚本 |
| n8n | `n8n/workflows` | Workflow JSON 和定时任务编排 |

### 3.4 AImodel 与 RAG MCP 集成

```text
TalonMart AI 模式
    |
    v
ai-service /AImodel/chat
    |
    v
AImodel Agent
    |
    +--> mock-api 商品搜索 / 商品详情 / 购物车工具
    |
    +--> PersistentMcpRagKnowledgeClient
              |
              v
          RAG MCP stdio server
```

设计约束：

- AImodel 只依赖 RAG MCP 公共工具响应，不直接 import RAG 内部模块。
- `request_source` 必须区分 `aimodel`、`mcp`、`query_cli`，便于后续 trace 分析。
- 前端输出必须过滤原始 tool result、chunk id、trace id 等内部细节。
- 商品事实优先调用 `mock-api`，RAG 只提供知识上下文。

### 3.5 Workflow 调用链

```text
飞书用户
    |
    v
feishu-adapter
    |
    v
n8n Workflow
    |
    +--> ai-service
    |
    +--> mock-api
    |
    +--> feishu-adapter table sync endpoints
    |
    v
飞书回复 / 多维表格 read model
```

设计约束：

- `feishu-adapter` 只处理协议、去重、转发、回复和表格同步，不承担核心业务决策。
- n8n 负责编排，FastAPI 服务负责确定性工具。
- Workflow 工具返回必须可测试、可审计、可复现。

### 3.6 PostgreSQL 数据设计

根项目 PostgreSQL 只记录 TalonMart、Workflow、AImodel 和会话相关业务表。RAG 子系统的 `rag_*`、`image_index`、Trace 和评估表由 `services/ai-service/rag/DEV_SPEC.md` 单独维护，不列入本表。

| 表名 | 用途 |
| --- | --- |
| `warehouses` | 仓库主数据，保存仓库编号、名称、城市、区域和启用状态。 |
| `storage_locations` | 库位主数据，保存仓库内 A1、B1、C1 等具体库位、温区和容量。 |
| `categories` | 商品分类表，保存商品分类名称和默认存储要求。 |
| `items` | 商品主数据，保存商品名称、品牌、规格、价格、搜索文本、单位和条码。 |
| `item_reviews` | 商品评论表，保存用户评分、标题、正文和时间。 |
| `inventory_batches` | 批次库存事实表，按仓库、库位、商品和批次保存库存数量与保质期。 |
| `inventory_location_balances` | 库位库存余额表，保存当前可售库存；飞书余额表使用数据库 `id` 作为 `Balance ID`，不展示 `category_id` 或 `item_id`。 |
| `replenishment_requests` | 补货申请表，保存低库存触发后交给采购审核的结构化需求；飞书补货申请 read model 只展示业务可读字段，不展示 `category_id` 或 `item_id`。 |
| `warehouse_inventory_sync_jobs` | 仓储库存同步任务表，保存采购到仓后需要同步飞书库存视图的待处理任务。 |
| `orders` | 订单主表，保存下单、发仓确认、付款、发货、到货、退款、退货和物流状态；状态统一使用英文枚举：`pending_fulfillment_review`、`unpaid`、`pending_shipment`、`shipped`、`arrived`、`refunded`、`returned`、`canceled`。 |
| `order_items` | 订单明细表，保存订单命中的商品、仓库、库位、批次和数量。 |
| `inventory_movements` | 库存流水表，记录员工确认发仓、退款和退货对库存余额的影响。 |
| `delivery_providers` | 物流供应商表，保存承运商名称、热线、单号前缀和启用状态。 |
| `users` | TalonMart 用户表，保存本地演示和购物车流程使用的用户资料。 |
| `delivery_addresses` | 配送地址表，保存用户收货人、电话、地址和默认地址标记。 |
| `cart_items` | 购物车明细表，按用户和商品保存加入购物车时的商品快照价格与数量。 |
| `flash_sales` | 秒杀活动表，保存秒杀商品、秒杀价、营销库存配额、开始时间和结束时间；`/flash-sales` 接口结合 `items.price` 返回可选商品原价，用于前端在存在真实折扣时展示划线价。 |
| `flash_sale_claims` | 秒杀抢购结果表，记录用户抢购结果、关联订单和一人一单约束。 |
| `item_rank_events` | 商品排行榜事件事实表，记录浏览、加购、购买、收藏、评论等可聚合行为。 |
| `category_rank_snapshots` | 分类排行榜快照表，保存各 category、rank_type、window_type 下的商品排名、分数和生成时间，Redis 丢失后可重建榜单。 |
| `procurement_suppliers` | 采购供应商表，保存供应商、商品、交期、采购价和可靠性。 |
| `purchase_orders` | 采购单表，保存补货申请审核后生成的采购单、支付状态和仓库同步状态；飞书采购单 read model 不展示 `supplier_id` 或 `item_id`。 |
| `session_state` | Agent 会话状态表，保存飞书/会话维度的短期状态，例如最近订单。 |
| `user_profile` | 用户画像表，保存用户资料、偏好、摘要和会话沉淀信息。 |
| `conversation` | AImodel 会话表，保存前端 AI 模式中的会话标题、用户和时间。 |
| `message` | AImodel 消息表，保存用户与 assistant 消息、链接和推荐链接。 |
| `message_query_trace` | AImodel 与 RAG Query Trace 关联表，用于从最终消息追溯一次 RAG 查询。 |
| `user_memory` | AImodel 长期记忆表，保存用户偏好、证据和置信度。 |

## 4. 测试方案（TDD）

### 4.1 TDD 原则

- **早测试、常测试**：功能实现同时编写测试。
- **测试即文档**：测试用例表达业务边界和接口契约。
- **快速反馈**：优先编写秒级单元测试，再补集成和 E2E。
- **分层测试金字塔**：单元测试为主，集成测试覆盖关键协作，E2E 覆盖完整路径。

### 4.2 测试分层

| 层级 | 命令 | 目标 |
| --- | --- | --- |
| 根项目测试 | `uv run --project services/mock-api pytest tests -q` | 验证 workflow 结构和文档一致性 |
| mock-api 测试 | `uv run --project services/mock-api pytest services\mock-api\tests -q` | 验证业务事实 API |
| ai-service 测试 | `uv run --project services/ai-service pytest services\ai-service\tests -q` | 验证 AImodel、会话、工具、RAG MCP client |
| feishu-adapter 测试 | `uv run --project services/feishu-adapter pytest services\feishu-adapter\tests -q` | 验证飞书事件、意图路由、表格同步 |
| 前端单元测试 | `pnpm --dir apps/talonmart-web test:unit` | 验证 Vue 组件和 API client |
| 前端 E2E | `pnpm --dir apps/talonmart-web test:e2e` | 验证浏览器用户路径 |
| 静态检查 | `uv run --project <service> ruff check ...` | 验证 Python 代码质量 |
| Compose 检查 | `docker compose -p after-sales-implementation config --quiet` | 验证本地部署配置 |

### 4.3 Workflow 与项目模块测试重点

| 范围 | 测试重点 |
| --- | --- |
| Warehouse Workflow | 库存查询、履约风险、补货申请、采购到仓同步、库存表同步 |
| Procurement Workflow | 补货审批、驳回、批量审批、采购单复用、到仓确认、采购表同步 |
| Delivery Workflow | 物流状态、物流异常、物流 case、订单状态边界 |
| Operations Workflow | 异常摘要、跨领域只读汇总、后续动作建议 |
| 电商项目 | 页面交互、Departments 导购、API client、AI 模式、购物车和商品详情路径 |
| AImodel | 流式聊天、商品工具、RAG MCP、会话记忆、输出清洗 |

## 5. 系统架构与模块设计

### 5.1 整体架构图

```text
┌──────────────────────────────────────────────────────────────┐
│ 用户入口                                                       │
├──────────────────────────────┬───────────────────────────────┤
│ TalonMart Web                 │ 飞书机器人                     │
│ 商品 / 购物车 / AI 模式        │ Warehouse / Procurement / etc. │
└───────────────┬──────────────┴───────────────┬───────────────┘
                │                              │
                v                              v
┌──────────────────────────────┐   ┌────────────────────────────┐
│ ai-service                    │   │ feishu-adapter             │
│ AImodel / memory / tools       │   │ event / bot / table sync   │
└───────────────┬──────────────┘   └──────────────┬─────────────┘
                │                                  v
                │                  ┌────────────────────────────┐
                │                  │ n8n workflows               │
                │                  │ department orchestration     │
                │                  └──────────────┬─────────────┘
                │                                  │
                v                                  v
┌──────────────────────────────────────────────────────────────┐
│ mock-api                                                      │
│ product / cart / flash sale / warehouse / procurement / delivery │
└───────────────┬──────────────────────────────┬───────────────┘
                │                              │
                v                              v
┌──────────────────────────────┐   ┌────────────────────────────┐
│ PostgreSQL                    │   │ Redis                      │
│ business facts / memory        │   │ flash sale quota           │
└──────────────────────────────┘   └────────────────────────────┘

ai-service ── stdio MCP ── services/ai-service/rag
```

### 5.2 目录结构树

```text
agent/                                                      # 项目根目录
├── DEV_SPEC.md                                             # 根项目开发规范
├── README.md                                               # 项目总览说明
├── docker-compose.yml                                      # 本地服务编排
├── .env.example                                            # 环境变量模板
├── netlify.toml                                            # 前端部署配置
├── apps/                                                   # 前端应用目录
│   └── talonmart-web/                                      # TalonMart Vue 前端
│       ├── package.json                                    # 前端依赖与脚本
│       ├── pnpm-lock.yaml                                  # 前端依赖锁文件
│       ├── vite.config.ts                                  # Vite 构建配置
│       ├── vitest.config.ts                                # Vitest 测试配置
│       ├── playwright.config.ts                            # Playwright E2E 配置
│       ├── tsconfig.json                                   # TypeScript 根配置
│       ├── tsconfig.app.json                               # 前端应用 TS 配置
│       ├── tsconfig.node.json                              # Node 工具 TS 配置
│       ├── tsconfig.vitest.json                            # Vitest TS 配置
│       ├── index.html                                      # 前端 HTML 入口
│       ├── public/                                         # 前端静态资源
│       │   └── favicon.ico                                 # 浏览器图标
│       ├── e2e/                                            # 前端端到端测试
│       │   ├── tsconfig.json                               # E2E TS 配置
│       │   └── vue.spec.ts                                 # 浏览器路径测试
│       └── src/                                            # 前端源码目录
│           ├── main.ts                                     # Vue 应用启动入口
│           ├── App.vue                                     # 前端应用壳
│           ├── router/                                     # 前端路由目录
│           │   └── index.ts                                # 页面路由定义
│           ├── views/                                      # 页面视图目录
│           │   ├── HomeView.vue                            # 首页页面
│           │   ├── HomeView.spec.ts                        # 首页单元测试
│           │   ├── SearchView.vue                          # 商品搜索页面
│           │   ├── DepartmentCategoryView.vue              # Departments 分类页面
│           │   ├── DepartmentCategoryView.spec.ts          # Departments 分类测试
│           │   ├── ProductDetailView.vue                   # 商品详情页面
│           │   ├── ProductDetailView.spec.ts               # 商品详情测试
│           │   ├── CartView.vue                            # 购物车页面
│           │   ├── CartView.spec.ts                        # 购物车测试
│           │   └── AboutView.vue                           # 关于页面
│           ├── components/                                 # 前端组件目录
│           │   ├── AiModeSidebar.vue                       # AI 浮动入口组件
│           │   ├── AiModeSidebar.spec.ts                   # AI 浮动入口测试
│           │   ├── AiModeChatPanel.vue                     # AI 聊天面板
│           │   ├── HelloWorld.vue                          # 模板示例组件
│           │   ├── TheWelcome.vue                          # 模板欢迎组件
│           │   ├── WelcomeItem.vue                         # 模板欢迎项
│           │   └── icons/                                  # 图标组件目录
│           │       ├── IconCommunity.vue                   # 社区图标
│           │       ├── IconDocumentation.vue               # 文档图标
│           │       ├── IconEcosystem.vue                   # 生态图标
│           │       ├── IconSupport.vue                     # 支持图标
│           │       └── IconTooling.vue                     # 工具图标
│           ├── services/                                   # 前端 API 客户端
│           │   ├── apiClient.ts                            # 通用 HTTP client
│           │   ├── aiModelApi.ts                           # AI 模式 API
│           │   ├── aiModelApi.spec.ts                      # AI API 测试
│           │   ├── cartApi.ts                              # 购物车 API
│           │   ├── categoryRankingApi.ts                   # 分类排行榜 API
│           │   ├── categoryRankingApi.spec.ts              # 分类排行榜 API 测试
│           │   ├── checkoutApi.ts                          # 结算 API
│           │   ├── flashSaleApi.ts                         # 秒杀 API
│           │   ├── flashSaleApi.spec.ts                    # 秒杀 API 测试
│           │   ├── productDetailApi.ts                     # 商品详情 API
│           │   ├── productDetailApi.spec.ts                # 商品详情 API 测试
│           │   ├── productReviewApi.ts                     # 商品评论 API
│           │   ├── productReviewApi.spec.ts                # 商品评论 API 测试
│           │   └── searchApi.ts                            # 商品搜索 API
│           ├── types/                                      # 前端类型定义
│           │   ├── aiModel.ts                              # AI 模式类型
│           │   ├── cart.ts                                 # 购物车类型
│           │   ├── categoryRanking.ts                      # 分类排行榜类型
│           │   ├── checkout.ts                             # 结算类型
│           │   ├── flashSale.ts                            # 秒杀类型
│           │   ├── productDetail.ts                        # 商品详情类型
│           │   ├── productReview.ts                        # 商品评论类型
│           │   └── search.ts                               # 搜索类型
│           ├── assets/                                     # 前端样式与资源
│           │   ├── base.css                                # 基础样式
│           │   ├── logo.svg                                # 项目 Logo
│           │   └── main.css                                # 主样式入口
│           └── stores/                                     # Pinia 状态目录
│               └── counter.ts                              # 模板计数 store
├── services/                                               # 后端服务目录
│   ├── ai-service/                                         # AI 服务
│   │   ├── Dockerfile                                      # AI 服务镜像
│   │   ├── pyproject.toml                                  # AI 服务依赖
│   │   ├── app/                                            # AI 服务源码
│   │   │   ├── __init__.py                                 # Python 包标记
│   │   │   ├── main.py                                     # FastAPI 入口
│   │   │   ├── schemas.py                                  # 通用请求模型
│   │   │   ├── decision_engine.py                          # 决策引擎
│   │   │   ├── message_agent.py                            # 消息 Agent
│   │   │   ├── message_schemas.py                          # 消息模型
│   │   │   ├── order_status_tool.py                        # 订单状态工具
│   │   │   ├── session_store.py                            # 会话状态存储
│   │   │   ├── transcription.py                            # 语音转写适配
│   │   │   └── routers/                                    # AI 服务路由
│   │   │       ├── __init__.py                             # 路由包标记
│   │   │       └── AImodel/                                # AImodel 模块
│   │   │           ├── __init__.py                         # AImodel 包标记
│   │   │           ├── router.py                           # AImodel HTTP 路由
│   │   │           ├── service.py                          # AImodel 编排服务
│   │   │           ├── schemas.py                          # AImodel 数据模型
│   │   │           ├── memory.py                           # AImodel 记忆存储
│   │   │           └── tools.py                            # AImodel 工具适配
│   │   ├── tests/                                          # AI 服务测试
│   │   │   ├── test_aimodel_agent.py                       # AImodel 行为测试
│   │   │   ├── test_aimodel_memory.py                      # AImodel 记忆测试
│   │   │   ├── test_aimodel_rag_tool.py                    # RAG 工具测试
│   │   │   ├── test_api.py                                 # AI 服务接口测试
│   │   │   ├── test_decision_engine.py                     # 决策引擎测试
│   │   │   └── test_message_agent.py                       # 消息 Agent 测试
│   │   └── rag/                                            # 独立 RAG 子项目
│   │       └── DEV_SPEC.md                                 # RAG 子项目规范
│   ├── mock-api/                                           # 业务事实 API
│   │   ├── Dockerfile                                      # mock-api 镜像
│   │   ├── pyproject.toml                                  # mock-api 依赖
│   │   ├── app/                                            # mock-api 源码
│   │   │   ├── __init__.py                                 # Python 包标记
│   │   │   ├── main.py                                     # mock-api 入口
│   │   │   ├── store.py                                    # fixture 读取工具
│   │   │   ├── warehouse_store.py                          # 仓储数据仓库
│   │   │   └── routers/                                    # 业务路由目录
│   │   │       ├── __init__.py                             # 路由包标记
│   │   │       ├── cart.py                                 # 购物车路由
│   │   │       ├── category_rankings.py                    # 分类排行榜路由
│   │   │       ├── delivery_addresses.py                   # 配送地址路由
│   │   │       ├── flash_sales.py                          # 秒杀路由
│   │   │       ├── product_details.py                      # 商品详情路由
│   │   │       ├── product_reviews.py                      # 商品评论路由
│   │   │       ├── search.py                               # 商品搜索路由
│   │   │       ├── warehouse/                              # 仓储路由包
│   │   │       │   ├── __init__.py                         # 仓储包标记
│   │   │       │   ├── router.py                           # 仓储路由聚合
│   │   │       │   ├── schemas.py                          # 仓储数据模型
│   │   │       │   ├── state.py                            # 仓储内存状态
│   │   │       │   ├── inventory.py                        # 库存查询路由
│   │   │       │   ├── orders.py                           # 仓储订单路由
│   │   │       │   ├── purchase_orders.py                  # 到仓同步路由
│   │   │       │   └── sync_jobs.py                        # 库存同步任务路由
│   │   │       ├── procurement/                            # 采购路由包
│   │   │       │   ├── __init__.py                         # 采购包标记
│   │   │       │   ├── router.py                           # 采购路由聚合
│   │   │       │   ├── schemas.py                          # 采购数据模型
│   │   │       │   ├── state.py                            # 采购内存状态
│   │   │       │   ├── service.py                          # 采购业务服务
│   │   │       │   ├── requests.py                         # 补货申请路由
│   │   │       │   ├── purchase_orders.py                  # 采购单路由
│   │   │       │   └── mock.py                             # 采购建议 mock
│   │   │       └── delivery/                               # 物流路由包
│   │   │           ├── __init__.py                         # 物流包标记
│   │   │           ├── router.py                           # 物流路由聚合
│   │   │           ├── schemas.py                          # 物流数据模型
│   │   │           ├── state.py                            # 物流内存状态
│   │   │           └── service.py                          # 物流业务服务
│   │   └── tests/                                          # mock-api 测试
│   │       ├── test_api.py                                 # 通用 API 测试
│   │       ├── test_delivery_router_structure.py           # 物流结构测试
│   │       ├── test_policy_rag_eval.py                     # 政策评估测试
│   │       ├── test_procurement_router_structure.py        # 采购结构测试
│   │       ├── test_warehouse_router_structure.py          # 仓储结构测试
│   │       └── test_warehouse_store.py                     # 仓储存储测试
│   ├── feishu-adapter/                                     # 飞书适配服务
│   │   ├── Dockerfile                                      # 飞书适配镜像
│   │   ├── pyproject.toml                                  # 飞书适配依赖
│   │   ├── app/                                            # 飞书适配源码
│   │   │   ├── __init__.py                                 # Python 包标记
│   │   │   ├── main.py                                     # 飞书适配入口
│   │   │   ├── feishu_client.py                            # 飞书 API client
│   │   │   ├── feishu_events.py                            # 飞书事件解析
│   │   │   ├── feishu_long_connection.py                   # 飞书长连接
│   │   │   ├── intent_router.py                            # 意图 fast path
│   │   │   ├── view_template_builder.py                    # 视图模板构建
│   │   │   ├── intents/                                    # 意图配置目录
│   │   │   │   └── warehouse.json                          # 仓储意图配置
│   │   │   └── view_templates/                             # 视图模板目录
│   │   │       └── warehouse_inventory.json                # 仓储视图模板
│   │   └── tests/                                          # 飞书适配测试
│   │       ├── test_feishu_adapter.py                      # 飞书适配测试
│   │       ├── test_intent_router.py                       # 意图路由测试
│   │       └── test_view_template_builder.py               # 视图模板测试
│   └── postgres/                                           # PostgreSQL 镜像
│       ├── Dockerfile                                      # 数据库镜像构建
│       └── initdb/                                         # 数据库初始化脚本
│           ├── 001-create-pg-search.sql                    # pg_search 初始化
│           └── 002-create-vector.sql                       # pgvector 初始化
├── n8n/                                                    # n8n 工作流目录
│   └── workflows/                                          # 工作流 JSON
│       ├── warehouse-workflow.json                         # 仓储工作流
│       ├── warehouse-inventory-balances-refresh.json       # 库存余额刷新
│       ├── warehouse-order-timeout-release.json            # 订单超时释放
│       ├── warehouse-purchase-arrival-notify.json          # 采购到货入库通知
│       ├── procurement-workflow.json                       # 采购工作流
│       ├── procurement-replenishment-requests-sync.json    # 补货申请表定时同步
│       ├── procurement-purchase-orders-sync.json           # 采购单表定时同步
│       ├── delivery-workflow.json                          # 物流工作流
│       └── operations-workflow.json                        # 运营工作流
├── fixtures/                                               # 测试与演示数据
│   ├── data/                                               # 业务数据 fixtures
│   │   ├── categories.json                                 # 商品分类数据
│   │   ├── customers.json                                  # 客户数据
│   │   ├── delivery_providers.json                         # 物流供应商数据
│   │   ├── inventory.json                                  # 库存快照数据
│   │   ├── inventory_batches.json                          # 库存批次数据
│   │   ├── items.json                                      # 商品数据
│   │   ├── orders.json                                     # 订单数据
│   │   ├── procurement_suppliers.json                      # 采购供应商数据
│   │   ├── shipments.json                                  # 物流单数据
│   │   ├── storage_locations.json                          # 库位数据
│   │   ├── warehouse_exceptions.json                       # 仓储异常数据
│   │   ├── warehouse_locations.json                        # 仓储库位数据
│   │   └── warehouses.json                                 # 仓库数据
│   ├── events/                                             # 事件 fixtures
│   │   ├── bad_review_public.json                          # 差评事件
│   │   ├── logistics_delay.json                            # 物流延迟事件
│   │   ├── low_confidence.json                             # 低置信事件
│   │   ├── low_stock.json                                  # 低库存事件
│   │   ├── mock_api_failure.json                           # API 失败事件
│   │   ├── refund_high_value.json                          # 高额退款事件
│   │   └── refund_normal.json                              # 普通退款事件
│   ├── evals/                                              # 评估数据目录
│   │   └── policy_rag_eval.json                            # 政策评估集
│   ├── messages/                                           # 消息 fixtures
│   │   ├── order_status_audio_qwen_missing_config.json     # 音频缺配置消息
│   │   ├── order_status_audio_transcript.json              # 音频转写消息
│   │   └── order_status_text.json                          # 订单文本消息
│   └── policies/                                           # 政策文档 fixtures
│       └── policy markdown fixtures                        # 政策 Markdown 集合
├── scripts/                                                # 本地辅助脚本
│   ├── generate_department_workflows.py                    # 生成部门工作流
│   ├── replay_failed_event.ps1                             # 回放失败事件
│   ├── send_event.ps1                                      # 发送事件 fixture
│   ├── send_message.ps1                                    # 发送消息 fixture
│   └── update_multi_domain_workflow.py                     # 更新多领域工作流
└── tests/                                                  # 根项目测试
    ├── test_current_docs.py                                # 当前文档测试
    └── test_department_workflows.py                        # 部门工作流测试
```
### 5.3 模块职责说明表

| 层级 | 文件 | 具体职责 | 关键技术点 |
| --- | --- | --- | --- |
| 前端 | `apps/talonmart-web/src/App.vue` | 前端应用壳 | 全局布局和路由出口 |
| 前端 | `apps/talonmart-web/src/router/index.ts` | 页面路由 | 首页、搜索、商品详情、购物车 |
| 前端 | `apps/talonmart-web/src/components/AiModeSidebar.vue` | AI 模式浮动入口 | 右下角笑脸入口、聊天面板开关、会话面板挂载 |
| 前端 | `apps/talonmart-web/src/components/AiModeChatPanel.vue` | AI 聊天面板 | SSE 流式输出、消息格式化、内部结果过滤 |
| 前端 | `apps/talonmart-web/src/services/aiModelApi.ts` | AImodel API client | chat stream、conversation、message |
| 前端 | `apps/talonmart-web/src/services/categoryRankingApi.ts` | 分类排行榜 API client | 首页热门、分类榜单、详情页 Top 标签 |
| AI 服务 | `services/ai-service/app/main.py` | FastAPI 入口 | 路由注册、启动初始化、shutdown 释放资源 |
| AI 服务 | `services/ai-service/app/routers/AImodel/router.py` | AImodel HTTP 路由 | chat、conversation、message、memory |
| AI 服务 | `services/ai-service/app/routers/AImodel/service.py` | Agent 编排 | LangChain message、工具调用、流式响应 |
| AI 服务 | `services/ai-service/app/routers/AImodel/tools.py` | 工具适配 | 商品 API、RAG MCP client、长连接复用 |
| AI 服务 | `services/ai-service/app/routers/AImodel/memory.py` | 会话记忆 | conversation、message、user_memory、message_query_trace |
| 业务 API | `services/mock-api/app/main.py` | mock-api 入口 | 路由注册、health、政策搜索、run log |
| 业务 API | `services/mock-api/app/warehouse_store.py` | 仓储 repository | PostgreSQL 优先、fixtures fallback、库存事实 |
| 业务 API | `services/mock-api/app/routers/category_rankings.py` | 分类排行榜路由 | PostgreSQL 事实/快照、Redis ZSET 缓存、Top 商品返回 |
| 业务 API | `services/mock-api/app/routers/warehouse/router.py` | Warehouse 路由聚合 | 库存、订单、同步任务 |
| 业务 API | `services/mock-api/app/routers/procurement/router.py` | Procurement 路由聚合 | 补货申请、采购单、到仓确认 |
| 业务 API | `services/mock-api/app/routers/delivery/router.py` | Delivery 路由聚合 | 物流状态、异常、case |
| 飞书 | `services/feishu-adapter/app/main.py` | 飞书服务入口 | 多机器人、事件转发、表格同步 |
| 飞书 | `services/feishu-adapter/app/feishu_events.py` | 事件归一化 | 消息类型、mention、payload 转换 |
| 飞书 | `services/feishu-adapter/app/intent_router.py` | 仓储 fast path | 明确同步/视图意图识别 |
| 飞书 | `services/feishu-adapter/app/view_template_builder.py` | 视图模板 | 受控模板、字段映射、视图计划 |
| Workflow | `n8n/workflows/warehouse-workflow.json` | Warehouse 编排 | 库存、履约、补货、同步工具 |
| Workflow | `n8n/workflows/warehouse-purchase-arrival-notify.json` | 采购到货入库通知 | 定时扫描今日到货采购单并触发飞书群通知 |
| Workflow | `n8n/workflows/procurement-workflow.json` | Procurement 编排 | 审批、采购单、到仓确认 |
| Workflow | `n8n/workflows/delivery-workflow.json` | Delivery 编排 | 物流查询、异常、case |
| Workflow | `n8n/workflows/operations-workflow.json` | Operations 编排 | 跨领域摘要和只读汇总 |
| 测试 | `tests/test_department_workflows.py` | Workflow 结构测试 | webhook、工具节点、边界检查 |
| 测试 | `tests/test_current_docs.py` | 文档一致性测试 | 根文档、关键章节、契约文本 |

### 5.4 业务流程说明

#### 5.4.1 Warehouse 业务流程

```text
飞书仓储消息
    |
    v
feishu-adapter
    |
    v
n8n warehouse-workflow
    |
    v
mock-api warehouse router
    |
    v
warehouse_store / PostgreSQL
    |
    v
库存、履约风险、补货申请、飞书库存表同步结果
```

#### 5.4.2 Procurement 业务流程

```text
采购审批消息
    |
    v
n8n procurement-workflow
    |
    v
mock-api procurement router
    |
    v
replenishment_requests / purchase_orders
    |
    v
飞书采购表同步 / 到仓状态更新
```

#### 5.4.3 Delivery 业务流程

```text
物流查询消息
    |
    v
n8n delivery-workflow
    |
    v
mock-api delivery router
    |
    v
orders / delivery_providers / delivery cases
    |
    v
物流状态、异常列表、跟进 case
```

#### 5.4.4 电商项目业务流程

```text
浏览器页面操作
    |
    v
Vue component
    |
    v
frontend service API client
    |
    v
mock-api
    |
    +--> 首页 / 搜索 / 商品详情 / 购物车 / 秒杀
    |
    +--> Departments 导购 /cp/:departmentSlug
    |
    v
页面状态更新与用户反馈
```

#### 5.4.5 AImodel 业务流程

```text
前端 AI 模式输入
    |
    v
aiModelApi.ts 发起 SSE 请求
    |
    v
ai-service AImodel router
    |
    v
AImodel service 读取记忆并调用工具
    |
    +--> mock-api 商品事实工具
    |
    +--> RAG MCP 知识工具
    |
    v
清洗后的流式回答 + message 持久化
```

## 6. 项目排期

状态标记：`[ ]` 未开始，`[~]` 进行中，`[✔]` 已完成。

### 6.1 阶段总览表

| 阶段 | 阶段标题 | 目标 | 状态 |
| --- | --- | --- | --- |
| 阶段 A | Project Foundation | 建立本地运行、共享服务、测试入口和基础规范 | [✔] |
| 阶段 B | Warehouse Workflow | 完成仓储库存、履约、补货、到货入库确认和飞书库存表闭环 | [✔] |
| 阶段 C | Procurement Workflow | 完成补货审批、采购单和采购飞书表闭环 | [✔] |
| 阶段 D | Delivery Workflow | 完成物流查询、异常和 case 闭环 | [✔] |
| 阶段 E | Operations Workflow | 完成跨领域只读摘要和运营建议闭环 | [✔] |
| 阶段 F | 电商项目 | 完成 TalonMart 商品、Departments 导购、购物车、秒杀、排行榜、发仓确认和前端体验 | [✔] |
| 阶段 G | AImodel | 完成前端 AI 聊天、商品工具、会话记忆和 RAG MCP 集成 | [✔] |
| 阶段 H | Quality And Delivery | 完成全量质量门禁、演示脚本和部署检查 | [~] |

### 6.2 交付里程碑

| 阶段 | 项目当前位置 | 可用功能 | 验证方式 | 下一阶段入口 | 完成日期 |
| --- | --- | --- | --- | --- | --- |
| 阶段 A | 基础服务可运行 | Docker Compose、fixtures、Python/Node 测试入口 | `docker compose -p after-sales-implementation config --quiet` | Warehouse Workflow |  |
| 阶段 B | 仓储主链路可演示 | 库存查询、履约风险、补货申请、采购到货入库确认、库存表同步 | `uv run --project services/mock-api pytest services\mock-api\tests\test_warehouse_store.py -q` | Procurement Workflow |  |
| 阶段 C | 采购主链路可演示 | 补货审批、采购单、到仓确认、采购表同步 | `uv run --project services/mock-api pytest services\mock-api\tests\test_procurement_router_structure.py -q` | Delivery Workflow |  |
| 阶段 D | 物流主链路可演示 | 物流状态、异常查询、case 创建 | `uv run --project services/mock-api pytest services\mock-api\tests\test_delivery_router_structure.py -q` | Operations Workflow |  |
| 阶段 E | 运营只读汇总可用 | 异常摘要、风险汇总、后续动作建议 | `uv run --project services/mock-api pytest tests\test_department_workflows.py -q` | 电商项目 |  |
| 阶段 F | 电商项目可用 | 商品、Departments 导购、详情、购物车、秒杀、排行榜、发仓确认、AI 模式 | `pnpm --dir apps/talonmart-web test:unit` | AImodel | 2026-06-17 |
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
| B3 | 实现履约风险和订单确认后库存扣减 | [✔] |  | FEFO、整单同仓、员工确认扣减 |
| B4 | 实现补货申请创建 | [✔] |  | replenishment_requests |
| B5 | 实现采购到仓库存同步 | [✔] |  | arrived_unsynced -> synced |
| B6 | 实现飞书库存表/余额表同步 | [✔] |  | feishu-adapter sync endpoints |
| B7 | 实现 Warehouse n8n Workflow | [✔] |  | warehouse-workflow.json |
| B8 | 实现仓储测试与回归门禁 | [✔] |  | warehouse tests |
| B9 | 实现采购到货入库确认通知 | [✔] | 2026-06-17 | 今日到货采购单、飞书通知、员工入库确认 |

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
| F8 | 实现分类排行榜和热门商品展示 | [✔] | 2026-06-17 | PostgreSQL facts、Redis ZSET、HomeView、DepartmentCategoryView、ProductDetailView |
| F9 | 实现订单发仓确认通知 | [✔] | 2026-06-17 | 支付后发仓确认、候选发仓、物流选择、员工确认后扣减 |

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
| 阶段 B | 9 | 9 | 100% |
| 阶段 C | 7 | 7 | 100% |
| 阶段 D | 6 | 6 | 100% |
| 阶段 E | 5 | 5 | 100% |
| 阶段 F | 9 | 9 | 100% |
| 阶段 G | 8 | 8 | 100% |
| 阶段 H | 7 | 2 | 29% |
| **总计** | **56** | **51** | **91%** |

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
- `services/mock-api/app/routers/warehouse/inventory.py`
- `n8n/workflows/warehouse-inventory-balances-refresh.json`
- `tests/test_department_workflows.py`

实现类/函数：

- `provision_inventory_table()`：创建或复用库存表。
- `sync_inventory_table()`：同步库存快照。
- `sync_inventory_balances_table()`：同步库存余额。
- `Warehouse Inventory Balances Refresh`：每 10 分钟刷新库存余额飞书表。

验收标准：

- 接口返回 table_id、table_url、synced_count 和错误摘要。
- 库存余额表展示 `Balance ID`、Warehouse、Location、Item Name、数量、状态和更新时间，不展示 `Category ID` 或 `Item ID`。
- `Balance ID` 来源于数据库 `inventory_location_balances.id`；无数据库 fallback 时使用稳定可读的 `fallback:{item_id}:{warehouse_id}:{location}`。
- `Warehouse Inventory Balances Refresh` 定时任务调用 `/warehouse/inventory-balances-table/sync`，用于周期性刷新余额表。
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

##### B9：实现采购到货入库确认通知

目标：扫描预计今日到货的已支付采购单，由飞书机器人主动通知仓储人员确认是否入库，员工确认后进入既有采购到仓库存同步链路。

修改文件：

- `services/mock-api/app/routers/warehouse/purchase_orders.py`
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

- `list_today_purchase_order_arrivals()`：查询 payment_status=paid 且 estimated_arrival_date 为指定日期的待入库采购单。
- `send_purchase_arrival_notification()`：向飞书群发送今日到货采购单和入库确认入口。
- `confirm_purchase_order_arrival_batch()`：员工确认全部或指定采购单已入库后更新 `warehouse_sync_status=arrived_unsynced`。
- `Warehouse Purchase Arrival Notify`：定时扫描今日到货采购单并触发飞书通知。

验收标准：

- 今日到货通知只包含已支付且 `warehouse_sync_status=pending_arrival` 的采购单。
- 飞书消息包含采购单号、商品、数量、仓库、库位、预计到货日期和确认入口文本。
- 员工可以确认全部到货采购单，也可以指定部分采购单入库。
- 入库确认后采购单进入 `arrived_unsynced`，后续由仓储同步工具写入库存批次和库存余额。
- 通知未配置或发送失败时，扫描接口返回明确状态，不修改采购单事实。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py services\mock-api\tests\test_warehouse_store.py -q`；`uv run --project services/feishu-adapter pytest services\feishu-adapter\tests\test_feishu_adapter.py -q`；`uv run --project services/mock-api pytest tests\test_department_workflows.py -q`

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
- `services/mock-api/app/routers/procurement/service.py`
- `n8n/workflows/procurement-replenishment-requests-sync.json`
- `n8n/workflows/procurement-purchase-orders-sync.json`
- `tests/test_department_workflows.py`

实现类/函数：

- `provision_procurement_replenishment_requests_table()`：创建补货申请表。
- `sync_procurement_replenishment_requests_table()`：同步补货申请。
- `provision_procurement_purchase_orders_table()`：创建采购单表。
- `sync_procurement_purchase_orders_table()`：同步采购单。
- `Procurement Replenishment Requests Sync`：定时同步补货申请表。
- `Procurement Purchase Orders Sync`：定时同步采购单表。

验收标准：

- 表同步结果包含写入数量和表链接。
- 补货申请飞书表不展示 `Category ID` 或 `Item ID`。
- 采购单飞书表不展示 `Supplier ID` 或 `Item ID`。
- 补货申请和采购单都有独立 n8n 定时同步任务，并调用对应 `/procurement/*-table/sync` 端点。
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

##### F9：实现订单发仓确认通知

目标：用户下单成功后先创建未付款订单，用户支付后由飞书机器人主动在群里发送订单详情、候选发仓方案和物流选择，员工确认后再执行库存扣减并进入发货链路。

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
- `send_fulfillment_review_notification()`：用户支付成功后按环境变量触发 feishu-adapter 发仓确认通知，未配置或发送失败不回滚付款状态。
- `createWarehouseOrder()`：前端下单后展示待付款状态。

验收标准：

- 订单状态统一使用英文枚举：`pending_fulfillment_review`、`unpaid`、`pending_shipment`、`shipped`、`arrived`、`refunded`、`returned`、`canceled`。
- 用户下单成功后不立即扣减库存，订单进入 `unpaid`，且不发送发仓确认通知。
- 用户支付成功后订单进入 `pending_fulfillment_review`，并触发发仓确认通知。
- `FEISHU_FULFILLMENT_REVIEW_NOTIFY_URL` 和 `FEISHU_FULFILLMENT_REVIEW_CHAT_ID` 控制发仓确认通知；通知未配置或发送失败时付款仍成功，并在响应中返回 notification 状态。
- 飞书群主动消息包含订单编号、商品明细、收货城市、推荐发仓、可选发仓、可选物流和确认入口文本。
- 员工确认发仓后才扣减库存，并将订单状态更新为 `pending_shipment`。
- 员工选择其他发仓策略时，系统按选择的仓库重新校验库存，不满足时返回阻塞原因。
- 员工确认时可以选择物流服务商，订单更新 `delivery_provider_id`、`delivery_provider_name` 和跟踪单号。
- `orders` 表不包含 `released_at` 字段，未付款超时取消通过订单状态和 `cancelled_at` 表达。

测试方法：`uv run --project services/mock-api pytest services\mock-api\tests\test_api.py services\mock-api\tests\test_warehouse_store.py -q`；`uv run --project services/feishu-adapter pytest services\feishu-adapter\tests\test_feishu_adapter.py -q`；`pnpm --dir apps/talonmart-web test:unit -- CartView checkoutApi`

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

## 7. 开发规范

### 7.1 规范优先

用户明确修改架构、阶段、命名、文件位置、测试要求或提交规则时，应先更新 `DEV_SPEC.md`，再继续对应开发任务。文档只描述当前有效状态，不保留“原先、后来、改为、不再”等过程性说明。

### 7.2 英文源码注释

所有新增业务代码必须使用**源码级英文注释**。该要求覆盖 Python 模块、类、函数、方法、测试、配置文件和脚本。注释必须让首次接触项目的开发者无需反复追踪调用链，即可理解当前文件为什么存在、负责什么以及如何安全使用。

注释要求：

- **模块 docstring**：说明文件在系统架构中的位置、核心职责、主要协作对象和明确不负责的边界。
- **类 docstring**：说明类所代表的业务概念、生命周期、依赖关系和调用方式。
- **函数/方法 docstring**：说明业务目的、关键处理步骤、参数含义、返回值契约、可能抛出的异常和可观察副作用。
- **测试 docstring**：说明被保护的行为契约、测试输入或前置条件，以及失败通常意味着哪类回归。
- **行内注释**：只用于解释难以从代码直接读出的业务原因、算法选择、fallback、兼容处理或安全限制。
- **配置和脚本注释**：说明配置项或命令对运行行为的影响、默认策略及使用限制。
- **接口实现注释**：明确接口职责和具体实现职责，尤其说明 provider、factory、pipeline stage 与上层业务之间的边界。

Python docstring 使用一致的源码级结构。存在对应内容时，应包含 `Args`、`Returns`、`Raises`、`Side Effects` 或 `Notes`；不存在参数、返回值或异常时不添加空章节。注释必须描述当前实现的真实行为，不得复制通用模板、虚构异常或为不同方法生成相同的空泛说明。

注释重点说明：

- 业务意图和当前文件的存在理由
- 工具、组件和分层职责边界
- 输入输出及数据契约
- 异常处理和优雅降级策略
- 配置开关对运行行为的影响
- 与 AImodel、Dashboard、MCP 或 Pipeline 的协作关系

避免无意义逐行翻译、仅重复函数名称、使用“执行该层任务”等空泛描述，或用长注释掩盖本应通过命名和结构解决的代码问题。

### 7.3 Prompt 语言

提交到仓库的 Prompt 配置统一使用英文，包括 description、system prompt、user prompt、输出格式和约束条件。面向用户的中文回复文案可以保留中文。

### 7.4 uv 包与环境管理

Python 服务统一通过 uv 执行命令：

```powershell
uv run --project services/mock-api pytest services\mock-api\tests -q
uv run --project services/ai-service pytest services\ai-service\tests -q
uv run --project services/feishu-adapter pytest services\feishu-adapter\tests -q
uv run --project services/mock-api ruff check services\mock-api
```

依赖升级必须显式执行 uv 依赖管理命令并提交相关锁文件。普通测试任务不得隐式升级依赖。

### 7.5 TDD 与任务闭环

每个实现任务必须包含测试方法。修复 bug 时先补能复现问题的测试或最小验证，再修改实现并重新运行目标测试。任务结束前必须做代码审查式自检，列出发现的问题和修复方式；没有问题时明确说明未发现可执行问题。

### 7.6 Git 提交规范

一个任务一个原子提交。提交标题格式：

```text
<type>(<scope>): [TASK_ID] <summary>
```

提交正文结构：

```text
Changes:
- describe concrete file and behavior changes

Testing:
- list exact verification commands and results

Design Principles:
- list real principles used, or None beyond existing project conventions

Task: <TASK_ID> - <phase title>
Spec: DEV_SPEC.md Section 6 (Project Schedule)
Tests: ✅ <passed>/<total> passed in <duration>
```

暂存时必须使用精确文件路径，不混入无关 dirty 文件。已经推送的提交只有在用户明确要求时才能重写。

### 7.7 RAG 边界

根项目只维护 RAG MCP 调用边界。RAG 内部数据结构、摄取流水线、检索流水线、评估、Dashboard 和 MCP tool schema 的内部变更，应先阅读并更新 `services/ai-service/rag/DEV_SPEC.md`。

### 7.8 规格反馈同步规范

DEV_SPEC 是项目设计、实施和验收的**单一事实来源**。用户在开发过程中提出的更正、补充要求和质量约束不能只保留在对话上下文中，必须及时回写文档，使后续开发者和 AI 能继续遵循最新决策。

同步要求：

- **先更新规范，再继续开发**：用户明确修改架构、流程顺序、命名、文件位置、数据契约、测试要求、提交要求或验收标准时，应先修改 DEV_SPEC，再继续对应任务。
- **执行影响范围检查**：一次更正可能同时影响技术选型、目录结构、模块职责、数据流、测试方案、任务明细和进度统计。不能只修改用户直接指出的一行。
- **以最新明确要求为准**：新要求与旧文档冲突时，采用用户最新的明确要求，并删除或改写所有过期描述。
- **只描述最终状态**：技术设计、模块职责、任务备注和验收标准只记录当前有效成果与约束，不保留“原先、后来、新增、改为、不再、重构为、修复了”等演进过程。需要说明兼容性时，直接描述当前支持的输入或行为。
- **保持排期一致**：任务合并、删除、拆分或调整顺序后，必须同步更新阶段预览、任务表、实施明细、总任务数、完成数和进度百分比。
- **同步自动开发参考文件**：DEV_SPEC 修改完成后，必须执行 `sync_spec.py --force`，确保 auto-coder 使用最新规范。
- **记录可复用规则**：如果用户更正的是可长期复用的开发流程，例如注释语言、TDD、Git 提交格式或任务完成方式，应写入“开发规范”，不能只修改当前任务。
- **避免无依据扩展**：无法从代码、现有文档或用户说明确认的细节，应先询问用户，不能自行写入规范并当作已确认事实。

每次同步后至少检查：

- 是否仍存在旧名称、旧路径、旧阶段顺序或旧任务编号。

- 目录树与模块职责表是否和任务修改文件一致。

- 测试方法与验收标准是否能够实际执行。

- Trace 阶段、数据流和 Pipeline 顺序是否保持一致。

- 任务状态、完成日期和测试结果是否来源于真实执行。

### 7.9 任务完成审查门禁

每个开发任务完成实现、测试和 DEV_SPEC 进度同步后，必须进入代码审查模式检查当前任务的全部 staged、unstaged 和 untracked 变更。审查是任务完成流程的一部分，不能省略，也不能在审查完成后自动开始下一任务。

  执行规则：

  - **审查范围**：当前任务新增或修改的源码、测试、配置、Prompt、DEV_SPEC 和同步后的 auto-coder reference。
  - **审查重点**：正确性、数据契约、异常处理、配置驱动、测试覆盖、注释质量、规范一致性和无关文件混入。
  - **问题闭环**：发现可执行问题时，先按 TDD 修复，再重新运行相关测试和代码审查，直到没有未解决的审查问题。
  - **审查报告**：任务结束摘要必须列出本轮审查发现的全部可执行问题，并逐项说明影响、根因、修改文件和实际修复方式。已经修复的问题也不能从最终摘要中省略；若未发现问题，应明确写“审查未发现可执行问题”。
  - **修复证据**：每个审查问题都应附上对应的失败测试或检查结果、修复后的验证命令及结果，使用户能够判断问题是否真正闭环。
  - **强制停止**：审查无问题后，输出任务摘要、测试证据和建议提交信息，然后停止并等待用户输入 `commit`、`skip` 或 `next`。
  - **连续开发约束**：用户输入 `next` 时，只提交已经通过审查的上一任务，再执行一个新任务；新任务完成审查后必须再次停止。
  - **确认不可跨任务或由压缩上下文推断**：只有在当前任务的最终审查摘要已经明确展示给用户之后，用户新发送的 `next` 才能授权提交该任务并开始下一任务。上下文自动压缩、恢复摘要、较早任务留下的 `next`，或尚未向用户展示审查结果时收到的模糊继续指令，都不能替代这次确认；此时必须先展示当前任务审查结果并停止，等待用户重新发送 `next`。
  - **禁止自动连跑**：单次 `next` 不得连续实现两个或更多未开始任务。
