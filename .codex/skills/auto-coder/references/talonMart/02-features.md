<!-- synced-from: DEV_SPEC.md -->
<!-- reference: 功能规范 -->

## 2. 核心特点

### 2.1 Workflow 与项目模块分阶段开发

项目排期按业务 Workflow 和项目模块组织。Warehouse、Procurement、Delivery、Operations 是 n8n 驱动的部门 Workflow；电商项目、AImodel 和飞书应用与协作后台是应用模块，不按 Workflow 表述。每个阶段都包含业务目标、后端 API、入口、测试和验收标准，适合让 AI 按任务逐步实现，也适合用户实时检查进度。

### 2.2 确定性业务工具

Agent 不直接修改业务事实。库存、采购单、订单、物流、商品和购物车都通过 `mock-api` 的确定性接口处理，避免大模型凭空生成业务状态。

### 2.3 飞书与前端双入口

企业内部 Workflow 通过飞书机器人和 n8n 进入，普通用户通过 TalonMart 前端进入。两类入口共用后端事实 API，但交互方式不同。

### 2.4 AImodel + RAG MCP

AImodel 负责用户购物咨询、商品对比和工具编排，商品事实由 `mock-api` 提供，选购指南、FAQ、平台政策和客服话术由 RAG MCP 服务提供。AImodel 侧使用独立 Intent Router，采用 RAG 当前的树状意图配置思想，先判断 `action` 和目标 `collection`，再决定调用商品 API、RAG、Tavily、直接回复或拒答。AImodel 侧通过 LangChain Middleware 建立 Agent Trace，记录意图识别、授权工具、LangChain 工具调用、RAG trace 关联和最终回答状态。RAG MCP 通过 stdio 子进程复用，避免每次查询重复启动。

### 2.5 飞书应用企业管理后台

飞书不只作为机器人入口，也作为 TalonMart 的**企业运营管理后台**。系统通过 feishu-adapter 将订单、库存、采购和商品运营数据同步到多维表格，再使用飞书应用搭建器的页面、指标卡、图表、排行榜、列表、筛选器和按钮组件，构建“运营驾驶舱 + 业务操作台”。飞书应用只承载展示、协作、提醒和人工操作入口，业务事实仍以 PostgreSQL / mock-api 为准。

### 2.6 全链路测试与可观测

项目同时使用 Python pytest、Vue Vitest、Playwright、workflow 结构测试、run log、RAG trace 关联和 Docker Compose 校验，覆盖代码、工作流和运行配置。

### 2.7 影刀 RPA 与 pandas 文件流水线

阶段 J 建立两块相互独立、通过 CSV 契约协作的通用能力，并以京东商品自动采集流水线完成首个自动编排实例：

1. **影刀网页导出 CSV**：通用模板负责读取输入清单、打开已授权页面、等待页面就绪、调用站点适配子流程、记录成功或失败行并导出原始 CSV。新增目标网站或页面时只增加站点实现，不复制批次循环、错误记录和 CSV 导出骨架。
2. **pandas 数据处理**：通用核心负责 CSV/XLSX 读取、编码统一、dataset contract 校验、processor 路由、行级错误、标准化输出、批次清单、归档和失败重放。新增数据类型时注册新的 dataset processor，不修改通用读取和批次核心。

首个完整实现使用 `dataset_type=jd_product`。京东 URL 发现脚本从关键词或已授权的搜索/分类入口发现商品详情 URL，按 SKU 规范化、去重并生成输入清单；自动编排器把输入清单交给影刀京东商品实现，采集当前页面可见的 SKU、标题、展示价格、店铺、主图和采集状态并导出原始 CSV，再调用对应 pandas processor 输出标准化 CSV、失败 CSV、批次清单和统一流水线结果。

阶段 J 只交付通用文件能力和京东商品自动文件流水线：**不新增数据库表，不修改 `items` 表结构或数据，不直接写入 PostgreSQL，不改造 mock-api、Operations Workflow 或飞书 read model**。数据入库、商品关联和下游展示方案由阶段 J 完成后另行设计。
