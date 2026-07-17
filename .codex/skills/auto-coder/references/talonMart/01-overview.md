<!-- synced-from: DEV_SPEC.md -->
<!-- reference: 项目概述 -->

## 1. 项目概述

### 1.1 项目定位

TalonMart Agent 是一个本地优先的电商业务 Agent 系统。项目用 Vue 前端、FastAPI 服务、n8n Workflow、飞书适配器、影刀 RPA、pandas、PostgreSQL、Redis 和 fixtures 数据，构建一个可演示、可测试、可逐步扩展的电商运营与购物辅助平台。

系统按 **Workflow + 项目模块** 划分业务能力，而不是把所有能力塞进一个大 Agent：

- **Warehouse Workflow**：库存、库位、履约风险和采购需求创建。
- **Procurement Workflow**：采购单审批、采购单查询和采购到仓状态跟踪。
- **Delivery Workflow**：物流状态查询、物流异常查询、物流跟进 case。
- **Operations Workflow**：跨领域异常摘要、运营风险汇总、后续动作建议。
- **电商项目**：TalonMart 用户界面、商品搜索、Departments 导购、商品详情、购物车、秒杀和前端 API client。
- **AImodel**：前端 AI 模式、商品咨询、商品对比、会话记忆、RAG MCP 知识调用。
- **RPA Data Operations**：建立影刀通用网页数据导出 CSV 能力和 pandas 可扩展数据处理能力，以京东商品采集与处理作为首个具体实现。

### 1.2 项目边界

根项目负责：

- 前端用户购物体验和 AI 模式交互。
- `ai-service` 中的 AImodel Agent、会话记忆、工具适配和 RAG MCP 客户端。
- `mock-api` 中的商品、购物车、配送地址、秒杀、订单、仓储、采购、物流和政策类确定性 API。
- `feishu-adapter` 中的飞书事件接入、多机器人配置、n8n 转发、飞书回复和多维表格同步。
- `n8n/workflows` 中的部门 Workflow 编排。
- `rpa/yingdao` 中的影刀通用网页导出模板、站点实现、CSV 交付契约和人工验收记录。
- `services/data-ops` 中的 pandas 通用文件处理核心、dataset processor、校验和批次文件管理。
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
