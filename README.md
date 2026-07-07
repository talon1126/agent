<img width="2560" height="1398" alt="image" src="https://github.com/user-attachments/assets/76c2859d-7992-4091-8abc-a3c162bb022f" />

# TalonMart Agent

TalonMart Agent 是一个本地优先的电商业务 Agent 与企业运营管理系统。项目围绕 TalonMart 电商业务构建前端购物体验、AImodel 对话助手、飞书 ERP 协作后台、n8n 业务 Workflow、mock-api 业务事实 API，以及独立的 RAG 知识服务。

核心文档：

- `DEV_SPEC.md`：根项目规格，覆盖 TalonMart、飞书 ERP、AImodel、mock-api、n8n workflow 和本地部署。
- `dev_spec_rag.md`：RAG 子系统规格，从 `services/ai-service/rag/DEV_SPEC.md` 复制，覆盖摄取、检索、评估、Dashboard 和 MCP。

## 核心能力

### 飞书 ERP 与协作后台

飞书侧不是单纯聊天入口，而是 TalonMart 的企业运营管理后台。系统通过 `feishu-adapter` 将 PostgreSQL / mock-api 中的订单、库存、采购、商品、秒杀等业务数据同步到飞书多维表格，再基于飞书应用搭建运营驾驶舱和业务操作页。

主要能力：

- 多机器人接入：仓储、采购、物流、运营等飞书机器人通过 `feishu-adapter` 接入 n8n workflow。
- 多维表格 read model：库存余额、库存流水、采购单、订单总览、订单明细、商品主数据、秒杀活动、秒杀结果等数据同步到飞书表。
- table_id 优先定位：飞书表改名后仍通过持久化 table id 定位，避免重复建表。
- 分页同步：源端超过 100 条记录时按 cursor / page 同步，避免丢数据。
- 商品图片同步：数据库中的商品图片 URL 可上传为飞书图片字段，支持商品表展示真实图片。
- 运营驾驶舱：飞书应用首页展示订单、库存、采购、热门商品和待办处理区。
- 业务操作按钮：采购到仓同步、订单发货、退货等人工操作通过飞书按钮调用后端确定性接口。

飞书 ERP 的定位是“协作、展示、人工确认和运营入口”，业务事实仍以 PostgreSQL / mock-api 为准。

### RAG 知识服务

RAG 子系统位于 `services/ai-service/rag`，用于为 AImodel 提供可引用、可追踪、可评估的内部知识检索能力。它作为独立模块维护，也通过 MCP stdio server 被 AImodel 长期复用。

主要能力：

- 文档摄取：支持 Markdown / PDF 等文件进入统一摄取流水线。
- 智能分块：Markdown 按标题层级、section、表格和长度策略切分，保留 section path。
- Transform：支持图片 caption、去噪、chunk rewrite、semantic merge 等摄取增强能力。
- 混合检索：Dense Embedding + BM25 稀疏检索 + RRF 融合。
- Rerank：支持 CrossEncoder、Qwen Reranker、LLM Rerank 等 provider。
- Self-RAG：在 rerank 后判断上下文相关性和证据充分性，避免低置信上下文进入回答。
- 多 collection 并行检索：AImodel 可传入多个 collection，RAG 内部并行检索、合并、重排和统一 response。
- Query Trace：记录 query processing、intent、dense、sparse、fusion、filter、rerank、self_rag、response 等阶段。
- Ingestion Trace：记录文档摄取、transform、embedding、upsert 等阶段耗时和状态。
- Dashboard：Streamlit 六大页面用于系统总览、摄取管理、数据浏览、Query Trace、Ingestion Trace 和评估。
- 评估体系：支持 Ragas 指标和自定义 Hit@K、MRR@K、NDCG@K 等检索指标。

当前 AImodel 通过 `PersistentMcpRagKnowledgeClient` 调用 RAG MCP，不直接 import RAG 内部模块。

### AImodel

AImodel 是 TalonMart 前端 AI 模式背后的 Agent 服务，负责理解用户意图、选择工具并组织最终回答。

主要能力：

- 商品搜索、商品详情、购物车等业务工具调用。
- RAG MCP 内部知识库调用，用于选购指南、FAQ、平台政策和客服话术。
- Tavily 联网搜索，用于公开信息、外部市场信息和新品/排行榜等场景。
- AImodel Intent Router，按 domain/category/intent 路由工具和 collection。
- LangChain Agent Trace，记录意图结果、授权工具、工具调用、RAG trace 关联和错误。
- PostgreSQL 会话与消息表，支持前端历史会话和评估溯源。
- LangGraph PostgreSQL checkpointer，支持同一 conversation 的 Agent thread 续接。

### TalonMart 电商前端

前端位于 `apps/talonmart-web`，提供电商购物体验和 AI 模式入口。

主要能力：

- 首页轮播、Flash Deals、热门推荐和商品卡片。
- Departments 导购和分类页。
- 商品详情页、评论星级、折扣价展示。
- 购物车、地址和订单流程。
- 秒杀活动和排行榜入口。
- 右下角 AI 模式助手入口。

### mock-api 与业务事实

`services/mock-api` 是本地演示和测试用的确定性业务事实 API。Agent 不直接改业务状态，必须通过 mock-api 提供的确定性接口处理订单、库存、采购、物流、商品、购物车和秒杀。

核心业务表包括：

- `items`
- `categories`
- `orders`
- `order_items`
- `inventory_location_balances`
- `inventory_movements`
- `purchase_orders`
- `delivery_providers`
- `flash_sales`
- `flash_sale_claims`
- `conversation`
- `message`
- `message_query_trace`
- `agent_trace`
- `agent_trace_event`
- `user_memory`

RAG 的 `rag_*` 表、image index、trace 和 evaluation 表由 RAG 子系统单独维护。

## 架构概览

```text
TalonMart Web
    |
    v
ai-service / AImodel
    |
    +--> mock-api business tools
    |
    +--> RAG MCP stdio server
    |
    +--> Tavily web search

Feishu Bots / Feishu App
    |
    v
feishu-adapter
    |
    +--> n8n workflows
    |
    +--> Feishu Bitable sync endpoints
    |
    +--> mock-api business APIs

PostgreSQL + Redis
    |
    +--> business facts
    +--> agent memory / traces
    +--> RAG documents / chunks / evaluation
    +--> flash sale counters
```

## 技术栈

| 模块 | 技术 |
| --- | --- |
| 前端 | Vue 3、TypeScript、Vite、Vue Router、Pinia、Vitest、Playwright |
| AImodel | FastAPI、LangChain、LangGraph Checkpointer、MCP SDK、psycopg |
| RAG | FastAPI、PostgreSQL、pgvector、BM25、CrossEncoder/Qwen Reranker、Streamlit、Ragas |
| mock-api | FastAPI、Pydantic、SQLAlchemy、psycopg、Redis |
| 飞书适配 | FastAPI、httpx、lark-oapi |
| Workflow | n8n |
| 数据库 | PostgreSQL |
| 缓存 | Redis |
| 包管理 | uv |
| 本地部署 | Docker Compose |

## 目录结构

```text
agent/
├── apps/
│   └── talonmart-web/                  # TalonMart Vue 前端
├── services/
│   ├── ai-service/                     # AImodel Agent 与 RAG MCP client
│   │   └── rag/                        # 独立 RAG 子系统
│   ├── mock-api/                       # 电商业务事实 API
│   ├── feishu-adapter/                 # 飞书事件、回复和多维表格同步
│   └── postgres/                       # PostgreSQL 初始化脚本
├── n8n/
│   └── workflows/                      # 部门 workflow 和定时同步任务
├── fixtures/                           # 本地演示数据
├── tests/                              # 跨服务 workflow / 集成测试
├── DEV_SPEC.md                         # 根项目开发规格
├── dev_spec_rag.md                     # RAG 开发规格副本
└── docker-compose.yml                  # 本地服务编排
```

## 常用命令

安装依赖和运行测试统一使用 uv。

```powershell
uv run --project services/mock-api pytest services\mock-api\tests -q
uv run --project services/ai-service pytest services\ai-service\tests -q
uv run --project services/feishu-adapter pytest services\feishu-adapter\tests -q
uv run --project services/ai-service/rag pytest services\ai-service\rag\tests -q
```

前端测试：

```powershell
pnpm --dir apps/talonmart-web test:unit
pnpm --dir apps/talonmart-web test:e2e
```

Docker Compose：

```powershell
docker compose -p after-sales-implementation up -d --build
docker compose -p after-sales-implementation ps
```

RAG Dashboard：

```powershell
uv run --project services/ai-service/rag python -m src.scripts.run_dashboard
```

RAG 查询：

```powershell
uv run --project services/ai-service/rag python -m src.scripts.query --help
```

RAG 评估：

```powershell
uv run --project services/ai-service/rag python -m src.scripts.run_evaluation --help
```

## 开发约束

- 业务事实只能由确定性 API 写入，Agent 不直接编造订单、库存、采购或物流状态。
- 飞书表是 read model 和协作入口，不是最终事实源。
- RAG 回答必须保留可追踪来源，不能替商品库编造商品价格、库存或链接。
- 修改功能时应同步更新对应 DEV_SPEC。
- Python 子项目依赖通过 uv 维护。
- 涉及 Docker 服务行为变化时，应重建对应容器后验证。

## 当前重点

项目当前重点是两条主线：

1. **飞书 ERP**：让订单、库存、采购、商品和秒杀数据稳定同步到飞书多维表格，并在飞书应用中形成可操作的运营驾驶舱。
2. **RAG**：让 AImodel 能稳定调用内部知识库，通过检索指标、Ragas、Trace 和 Dashboard 持续优化回答质量。
