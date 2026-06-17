<!-- synced-from: DEV_SPEC.md -->
<!-- reference: 技术栈与依赖 -->

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
| feishu-adapter | `services/feishu-adapter` | 飞书事件、n8n 转发、多维表格同步、飞书应用数据支撑 |
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
| `warehouse_inventory_sync_jobs` | 仓储库存同步任务表，保存采购到仓后需要写入库存批次与库存余额的待处理任务。 |
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
