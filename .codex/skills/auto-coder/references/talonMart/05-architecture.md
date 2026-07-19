<!-- synced-from: DEV_SPEC.md -->
<!-- reference: 架构与模块设计 -->

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

已授权目标网页 + 输入清单
          |
          v
影刀通用网页导出模板 ──> 站点适配子流程 ──> 原始 CSV(dataset_type)
                                                   |
                                                   v
                                      services/data-ops 通用核心
                                                   |
                                                   v
                                         dataset processor 注册表
                                                   |
                                                   v
                                     标准化 CSV + 失败 CSV + 批次清单

首个实现：京东商品 URL ──> 影刀 jd_product 实现 ──> pandas jd_product processor
```

### 5.2 目录结构树

```text
agent/                                                      # 项目根目录
├── DEV_SPEC.md                                             # 根项目开发规范
├── README.md                                               # 项目总览说明
├── docker-compose.yml                                      # 本地服务编排
├── .env.example                                            # 环境变量模板
├── .gitignore                                              # 忽略 RPA 运行文件和本地敏感数据
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
│   │   │           ├── intent_router.py                    # AImodel 意图路由
│   │   │           ├── intent_routes.yaml                  # AImodel 意图配置
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
│   │   │       ├── procurement/                            # 采购路由包
│   │   │       │   ├── __init__.py                         # 采购包标记
│   │   │       │   ├── router.py                           # 采购路由聚合
│   │   │       │   ├── schemas.py                          # 采购数据模型
│   │   │       │   ├── state.py                            # 采购内存状态
│   │   │       │   ├── service.py                          # 采购业务服务
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
│   ├── data-ops/                                           # RPA 文件数据处理项目
│   │   ├── pyproject.toml                                  # pandas 和测试依赖
│   │   ├── uv.lock                                         # data-ops 依赖锁
│   │   ├── src/                                            # data-ops 源码
│   │   │   └── data_ops/                                   # 文件处理 Python 包
│   │   │       ├── __init__.py                             # Python 包标记
│   │   │       ├── app.py                                  # 具体 processor 注册与 CLI 应用组合入口
│   │   │       ├── cli.py                                  # CSV 处理命令入口
│   │   │       ├── core/                                   # 通用文件处理核心
│   │   │       │   ├── __init__.py                         # Core 包标记
│   │   │       │   ├── contracts.py                        # Dataset 与 CSV 契约
│   │   │       │   ├── csv_io.py                           # CSV/XLSX 读取和 UTF-8 CSV 输出
│   │   │       │   ├── validation.py                       # 通用列与行级校验
│   │   │       │   └── batch_manifest.py                   # 批次清单、归档和失败信息
│   │   │       └── processors/                             # 具体 dataset processor
│   │   │           ├── __init__.py                         # Processor 包标记
│   │   │           ├── registry.py                         # dataset_type 到 processor 路由
│   │   │           ├── jd_product_contract.py              # 京东原始与标准化 CSV 契约
│   │   │           └── jd_product.py                       # 京东商品首个处理实现
│   │   └── tests/                                          # data-ops 测试
│   │       ├── test_core.py                                # 通用读取、契约和校验测试
│   │       ├── test_processor_registry.py                  # Processor 注册与路由测试
│   │       ├── test_jd_product_processor.py                # 京东商品处理测试
│   │       └── test_batch_manifest.py                      # 通用批次和归档测试
│   └── postgres/                                           # PostgreSQL 镜像
│       ├── Dockerfile                                      # 数据库镜像构建
│       └── initdb/                                         # 数据库初始化脚本
│           ├── 001-create-pg-search.sql                    # pg_search 初始化
│           └── 002-create-vector.sql                       # pgvector 初始化
├── rpa/                                                    # 桌面 RPA 资产说明
│   └── yingdao/                                            # 影刀 RPA 流程目录
│       ├── README.md                                       # 运行边界、凭据和扩展说明
│       ├── templates/                                      # 可复用影刀流程模板
│       │   └── web-page-to-csv.md                          # 通用网页抓取并导出 CSV 模板
│       └── implementations/                                # 具体网站和页面实现
│           └── jd-product-export.md                        # 京东商品网页采集实现
├── n8n/                                                    # n8n 工作流目录
│   └── workflows/                                          # 工作流 JSON
│       ├── warehouse-workflow.json                         # 仓储工作流
│       ├── warehouse-inventory-balances-refresh.json       # 库存余额刷新
│       ├── warehouse-inventory-movements-refresh.json      # 库存流水刷新
│       ├── warehouse-order-timeout-release.json            # 订单超时释放
│       ├── warehouse-purchase-arrival-notify.json          # 采购到货入库通知
│       ├── procurement-workflow.json                       # 采购工作流
│       ├── procurement-purchase-orders-sync.json           # 采购单表定时同步
│       ├── order-fulfillment-table-sync.json               # 订单履约表定时同步
│       ├── order-items-table-sync.json                     # 订单明细表定时同步
│       ├── items-table-sync.json                           # 商品主数据表定时同步
│       ├── product-operations-table-sync.json              # 商品运营表定时同步
│       ├── flash-sales-table-sync.json                     # 秒杀活动表定时同步
│       ├── flash-sale-claims-table-sync.json               # 秒杀结果表定时同步
│       ├── delivery-workflow.json                          # 物流工作流
│       └── operations-workflow.json                        # 运营工作流
├── fixtures/                                               # 测试与演示数据
│   ├── data/                                               # 业务数据 fixtures
│   │   ├── categories.json                                 # 商品分类数据
│   │   ├── customers.json                                  # 客户数据
│   │   ├── delivery_providers.json                         # 物流供应商数据
│   │   ├── inventory.json                                  # 库存快照数据
│   │   ├── inventory_location_balances.json                 # 库存余额数据
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
│   ├── rpa/                                                # 脱敏 RPA 文件 fixtures
│   │   ├── jd_product_urls.csv                             # 京东商品 URL 输入样例
│   │   └── jd_product_export.csv                           # 影刀京东商品原始 CSV 样例
│   └── policies/                                           # 政策文档 fixtures
│       └── policy markdown fixtures                        # 政策 Markdown 集合
├── scripts/                                                # 本地辅助脚本
│   ├── generate_department_workflows.py                    # 生成部门工作流
│   ├── replay_failed_event.ps1                             # 回放失败事件
│   ├── run_data_ops.ps1                                    # 执行 RPA CSV 标准化
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
| AI 服务 | `services/ai-service/app/routers/AImodel/service.py` | Agent 编排 | LangChain message、Intent Router 调用、工具调用、流式响应 |
| AI 服务 | `services/ai-service/app/routers/AImodel/intent_router.py` | AImodel 意图路由 | 树状规则配置、action/collection 决策、candidate score、score 阈值过滤、多 collection 候选、fallback |
| AI 服务 | `services/ai-service/app/routers/AImodel/tools.py` | 工具适配 | 商品 API、RAG MCP client、Tavily 联网搜索、长连接复用 |
| AI 服务 | `services/ai-service/app/routers/AImodel/memory.py` | 会话记忆 | conversation、message、user_memory、message_query_trace |
| AI 服务 | `services/ai-service/app/routers/AImodel/agent_trace.py` | Agent Trace | LangChain Middleware trace、intent route、tool call、RAG trace 关联、错误摘要 |
| 业务 API | `services/mock-api/app/main.py` | mock-api 入口 | 路由注册、health、政策搜索、run log |
| 业务 API | `services/mock-api/app/warehouse_store.py` | 仓储 repository | PostgreSQL 优先、fixtures fallback、库存事实 |
| 业务 API | `services/mock-api/app/routers/category_rankings.py` | 分类排行榜路由 | PostgreSQL 事实/快照、Redis ZSET 缓存、Top 商品返回 |
| 业务 API | `services/mock-api/app/routers/warehouse/router.py` | Warehouse 路由聚合 | 库存、订单、采购需求创建 |
| 业务 API | `services/mock-api/app/routers/procurement/router.py` | Procurement 路由聚合 | 采购单审批、采购单查询 |
| 业务 API | `services/mock-api/app/routers/delivery/router.py` | Delivery 路由聚合 | 物流状态、异常、case |
| 飞书 | `services/feishu-adapter/app/main.py` | 飞书服务入口 | 多机器人、事件转发、表格同步 |
| 飞书 | `services/feishu-adapter/app/feishu_events.py` | 事件归一化 | 消息类型、mention、payload 转换 |
| 飞书 | `services/feishu-adapter/app/intent_router.py` | 仓储 fast path | 明确同步/视图意图识别 |
| 飞书 | `services/feishu-adapter/app/view_template_builder.py` | 视图模板 | 受控模板、字段映射、视图计划 |
| 飞书应用 | 飞书多维表格应用页面配置 | 企业管理后台页面 | 运营驾驶舱、业务操作台、组件绑定和人工验收 |
| RPA | `rpa/yingdao/templates/web-page-to-csv.md` | 通用网页导出模板 | 输入循环、页面就绪等待、适配子流程调用、结果累积、错误记录和原始 CSV 导出 |
| RPA | `rpa/yingdao/implementations/jd-product-export.md` | 京东商品首个实现 | 商品 URL 输入、京东详情页状态识别、可见字段采集、失败行和人工验收 |
| 文件数据处理 | `services/data-ops/src/data_ops/app.py` | 应用组合入口 | 注册内置具体 processor，并把命令参数交给不感知站点的通用 CLI |
| 文件数据处理 | `services/data-ops/src/data_ops/core/csv_io.py` | 通用文件格式统一 | CSV/XLSX 读取、编码处理、UTF-8 CSV 输出和原始文件保护 |
| 文件数据处理 | `services/data-ops/src/data_ops/processors/registry.py` | Processor 路由 | 根据 dataset_type 解析并调用具体数据处理器 |
| 文件数据处理 | `services/data-ops/src/data_ops/processors/jd_product_contract.py` | 京东文件契约 | 京东输入列、站点扩展列、标准化价格列和输出文件名 |
| 文件数据处理 | `services/data-ops/src/data_ops/processors/jd_product.py` | 京东商品处理实现 | 字段校验、类型标准化、重复识别、标准化 CSV 和失败 CSV |
| 文件数据处理 | `services/data-ops/src/data_ops/core/batch_manifest.py` | 文件批次管理 | 批次清单、输入输出摘要、成功归档、失败隔离和重放 |
| Workflow | `n8n/workflows/warehouse-workflow.json` | Warehouse 编排 | 库存、履约和采购需求工具 |
| Workflow | `n8n/workflows/warehouse-purchase-arrival-notify.json` | 采购到货入库通知 | 定时扫描今日到货采购单并触发飞书群通知 |
| Workflow | `n8n/workflows/order-fulfillment-table-sync.json` | 订单履约表同步 | 每 10 分钟刷新 Order Fulfillment 飞书 read model |
| Workflow | `n8n/workflows/order-items-table-sync.json` | 订单明细表同步 | 每 10 分钟刷新 Order Items 飞书 read model |
| Workflow | `n8n/workflows/items-table-sync.json` | 商品主数据表同步 | 每 10 分钟刷新 Items 飞书 read model |
| Workflow | `n8n/workflows/product-operations-table-sync.json` | 商品运营表同步 | 每 10 分钟刷新 Product Operations 飞书 read model |
| Workflow | `n8n/workflows/flash-sales-table-sync.json` | 秒杀活动表同步 | 每 10 分钟刷新 Flash Sales 飞书 read model |
| Workflow | `n8n/workflows/flash-sale-claims-table-sync.json` | 秒杀结果表同步 | 每 10 分钟刷新 Flash Sale Claims 飞书 read model |
| Workflow | `n8n/workflows/procurement-workflow.json` | Procurement 编排 | 审批和采购单查询 |
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
库存、履约风险、采购需求创建
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
purchase_orders
    |
    v
采购单审批结果 / 采购单查询结果
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
AImodel service 读取记忆
    |
    v
AImodel Intent Router 选择 action / collection
    |
    +--> mock-api 商品事实工具
    |
    +--> 无参数 rag_tool 绑定原始用户问题和目标 collection
           |
           v
        RAG MCP 知识工具
    |
    v
清洗后的流式回答 + message 持久化
```

#### 5.4.6 飞书应用与协作后台业务流程

```text
飞书应用页面
    |
    v
指标卡 / 图表 / 列表 / 按钮组件
    |
    v
飞书多维表格 read model
    |
    v
feishu-adapter 同步端点 / 主动通知端点
    |
    v
mock-api / PostgreSQL 业务事实
```

设计约束：

- 飞书应用使用当前已改名的应用，不新建应用。
- 首页采用“运营驾驶舱 + 待办处理区”的均衡布局。
- 缺失数据源先以明确空状态呈现，后续通过 H7-H9 任务补齐。
- 飞书应用中的按钮只触发明确的同步接口、机器人指令或人工操作入口，不直接绕过后端业务规则。

#### 5.4.7 RPA Data Operations 业务流程

```text
输入清单 + dataset contract
    |
    v
影刀通用网页导出模板
    |
    +--> 站点适配子流程
             |
             v
      var/rpa/inbox 原始 CSV(dataset_type)
    |
    v
services/data-ops 通用核心
    |
    +--> processor registry
             |
             v
      具体 dataset processor
    |
    +--> var/rpa/normalized 标准化 UTF-8 CSV
    |
    +--> var/rpa/failed 失败 CSV 和错误摘要
    |
    +--> var/rpa/archive 原始文件和批次清单

首个实例：京东商品 URL -> 影刀 jd_product 实现 -> pandas jd_product processor
```

设计约束：

- 影刀只操作已获授权或允许访问的页面，凭据不得写入仓库、CSV fixture、日志或批次清单。
- 影刀通用模板只负责输入循环、公共状态、结果收集和 CSV 导出；网站元素、字段解析和页面状态判断由 implementations 下的站点实现负责。
- 京东商品首个实现按输入 URL 采集一个明确 SKU 的当前可见页面信息，不遍历搜索结果、全部变体，不绕过登录、验证码、访问限制或平台规则。
- pandas 通用核心不包含京东字段判断；`jd_product` 的列契约和业务校验只存在于对应 processor。
- 原始文件保持不可变，标准化输出、归档文件和失败文件使用不同目录。
- `fixtures/rpa` 只保存合成或脱敏数据，`var/rpa` 全目录忽略 Git。
