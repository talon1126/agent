<!-- synced-from: DEV_SPEC.md -->
<!-- reference: 功能规范 -->

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
