<!-- synced-from: services\ai-service\rag\DEV_SPEC.md -->
<!-- reference: 任务计划与状态 -->

## 6. 项目排期

### 6.1 阶段预览表

状态标记说明：`[ ]` 表示未开始，`[~]` 表示进行中，`[✔]` 表示已完成。

| 阶段 | 阶段标题 | 目标 | 状态 |
| --- | --- | --- | --- |
| Phase A | 配置与项目骨架 | 独立模块基础文件、uv 依赖锁定、Docker 部署骨架、pytest 冒烟测试、配置模板、prompt 配置、核心类型和配置加载 | [✔] |
| Phase B | 数据持久化与可插拔组件 | PostgreSQL/pgvector schema、repository、文档生命周期管理和 libs 可插拔实现 | [✔] |
| Phase C | Ingestion & Indexing Pipeline | 先去重的数据摄取、Loader、PDF -> Markdown、Markdown section-aware Splitter、包含 ImageCaptioner 的 Transform Pipeline、content_hash 差量、Dense/BM25Indexer 双路索引、pgvector upsert、统一 Pipeline MVP 和 `ingest.py` 脚本入口 | [✔] |
| Phase D | Retrieval | Query Processor、Dense Route、Sparse Route、RRF Fusion、HybridSearch、Rerank 前候选过滤、Rerank、Response Builder 和 query.py 脚本入口 | [✔] |
| Phase E | MCP 工具服务 | MCP Server 和 `query_knowledge_hub`、`list_collections`、`get_document_summary` tools 暴露 | [✔] |
| Phase F | 可观测与管理平台 | TraceContext、结构化日志、ingestion/query 链路打点、Dashboard services、六大 Streamlit 页面和页面测试 | [✔] |
| Phase G | 质量评估体系 | 黄金测试集、Ragas、自定义指标、真实 Query Pipeline 评估入口、策略对比和评估趋势 | [✔] |
| Phase H | AImodel 联调集成 | 集成前验收门禁、AImodel RAG 工具适配、商品 API 协同、前端/Agent 联调、端到端测试和 MCP 长连接优化 | [✔] |

### 6.2 交付里程碑

每完成一个阶段后，必须维护该阶段的交付里程碑。里程碑不是重复任务列表，而是面向后续开发者、面试官和项目讲解者说明：**当前项目位置、可用功能和下一阶段入口**。

维护要求：

- **项目当前位置**：说明当前阶段完成后，RAG 系统处于什么能力状态。
- **可用功能**：列出当前可以实际运行、测试或演示的功能。
- **验证方式**：列出该阶段完成后最小验证命令或页面入口。
- **下一阶段入口**：说明下一个阶段依赖当前阶段的哪些产物继续开发。
- **状态更新时间**：记录阶段完成日期，便于进度追踪。

里程碑记录模板：

    #### 阶段 X 交付里程碑：阶段标题

    完成日期：

    项目当前位置：

    可用功能：

    -

    验证方式：

    - `uv run --project services/ai-service/rag pytest ...`
    - Dashboard 页面入口：

    下一阶段入口：

阶段里程碑表：

| 阶段 | 阶段标题 | 项目当前位置 | 可用功能 | 验证方式 | 完成日期 |
| --- | --- | --- | --- | --- | --- |
| Phase A | 配置与项目骨架 | 独立 RAG 模块骨架、uv 锁定环境、运行配置、Prompt 和共享数据契约已就绪，可进入持久化与可插拔组件开发 | `uv.lock`、项目 `.venv`、独立 CLI、frozen Docker 构建、类型化配置加载、活动环境变量校验、英文 Prompt、核心领域类型和统一异常 | `uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\test_smoke.py services\ai-service\rag\tests\unit\test_config.py services\ai-service\rag\tests\unit\test_types.py -q` | 2026-06-06 |
| Phase B | 数据持久化与可插拔组件 | 持久化、可插拔组件契约和首批真实 Provider 已就绪，可进入 Ingestion Pipeline 开发 | PostgreSQL/pgvector schema、Repository、文档生命周期、Loader/Splitter/LLM/Embedding/VectorStore/Reranker/Evaluator Factory、BaseTransform、DeepSeek、DashScope Embedding、PgVectorStore 与 fake 测试实现 | `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests -q` | 2026-06-06 |
| Phase C | Ingestion & Indexing Pipeline | 离线摄取与索引主链路可通过 CLI 将 Markdown/PDF 文件或目录写入 PostgreSQL、pgvector、BM25 和图片索引 | SHA256 去重、Loader、Markdown section-aware 智能分块、Transform、图片 caption 降级、差量 Dense 编码、BM25、事务 upsert、生命周期管理和 `ingest.py` CLI | `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests -q`；`uv run --project services/ai-service/rag python -m src.scripts.ingest --help` | 2026-06-07 |
| Phase D | Retrieval | 在线查询主链路可基于已摄取知识库执行 Query Processor、Dense/Sparse 双路召回、RRF 融合、metadata filter、Rerank、Response Builder 和 CLI 查询 | QueryProcessor、DenseRoute、SparseRoute、HybridSearch、RerankController、RerankOutcome、KnowledgeHubResponseBuilder、`query.py` CLI、PostgreSQL/pgvector/BM25 集成测试 | `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests -q`；`uv run --project services/ai-service/rag python -m src.scripts.query --help` | 2026-06-07 |
| Phase E | MCP 工具服务 | MCP stdio 工具服务可被 AImodel 或其他 MCP client 发现工具 schema 并调用查询、collection 列表和文档摘要能力 | FastMCP stdio server、`.env` 加载、app.log 文件日志、`query_knowledge_hub`、`list_collections`、`get_document_summary`、结构化业务错误、schema/contract 测试 | `uv run --project services/ai-service/rag pytest services/ai-service/rag/tests/unit/test_mcp_tools.py -v`；`uv run --project services/ai-service/rag python -m src.mcp_server.server --help` | 2026-06-08 |
| Phase F | 可观测与管理平台 | 可观测链路、结构化 trace、Dashboard services、六大页面和 Ingestion 管理页真实摄取操作可用 | TraceContext/TraceController、JSON Lines trace、ingestion/query 打点、Dashboard service DTO、六大 Streamlit 页面、Dashboard 启动脚本、IngestionOperationService 和页面集成测试 | `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests/integration/test_dashboard_pages.py -v`；`uv run --project services/ai-service/rag python -m src.scripts.run_dashboard --dry-run --port 8504` | 2026-06-09 |
| Phase G | 质量评估体系 | 质量评估体系支持黄金测试集、检索指标、Ragas 生成质量适配、真实 Query Pipeline 评估入口、策略对比 runner 和评估趋势持久化 | `tests/fixtures/golden_set.json`、黄金样本 schema 校验、Hit Rate@K、MRR、NDCG、Ragas faithfulness、Ragas answer_relevancy adapter、`run_evaluation.py`、hybrid/dense_only/sparse_only/rerank 策略对比、evaluation run/results 持久化、Agent-ready final context 评估输入 | `uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_config.py services\ai-service\rag\tests\unit\test_response_builder.py services\ai-service\rag\tests\unit\test_evaluation.py -q` | 2026-06-12 |
| Phase H | AImodel 联调集成 | RAG 独立模块已通过集成前验收，shopping guide RAG 工具已接入 AImodel Agent 工具集合，推荐、链接对比、选购指南和政策 FAQ 场景规则已写入 Agent system prompt，流式前端输出具备 tool result 和内部 ID 防泄漏门禁，AImodel RAG MCP client 可长期复用 stdio 子进程 | Dashboard 六大页面 service-backed 渲染测试、离线摄取到 Hybrid Query 的全链路 E2E、MCP stdio 子进程工具发现、`search_shopping_guides` 工具适配、Agent tool list 接入、商品事实/API 与知识补充/RAG 边界、推荐/对比/指南/FAQ 场景覆盖、message-query-trace 逻辑关联、SSE tool JSON 过滤、chunk id/trace id 可见输出过滤、Persistent MCP client 长期复用子进程、FastAPI shutdown 释放 MCP client | `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_pages.py services\ai-service\rag\tests\e2e\test_full_rag_flow.py -v`；`uv run --project services/ai-service/rag pytest services\ai-service\tests\test_aimodel_rag_tool.py services\ai-service\tests\test_aimodel_memory.py services\ai-service\tests\test_aimodel_agent.py -v` | 2026-06-12 |

#### 阶段 A 交付里程碑：配置与项目骨架

完成日期：2026-06-06

项目当前位置：

RAG 是使用 uv 锁定依赖、可独立安装、测试和构建 Docker 镜像的 Python 子模块。统一配置、Prompt、核心数据对象和异常边界为 PostgreSQL 持久化、Provider Factory 和业务 Pipeline 提供稳定契约。

可用功能：

- 通过 `main.py` 输出无外部依赖的健康状态。
- 通过 `uv.lock`、项目 `.venv` 和 `uv run` 复现本地、auto-coder 与 Docker 依赖环境。
- 通过固定版本 uv 和 `uv sync --frozen --no-dev` 构建非 root Docker 镜像。
- 从 `settings.yaml` 加载类型化配置，并在启动前校验 Provider、模型、活动环境变量、检索参数和 Embedding 维度。
- 加载并校验 rerank、chunk rewrite 和 image-to-text Prompt。
- 创建并序列化 `Document`、`ImageMetadata`、`Chunk` 和 `RetrievalResult`。
- 通过统一 `RagError` 异常层级区分配置、Provider、数据库、摄取、检索和 MCP 错误。

验证方式：

- `uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\test_smoke.py services\ai-service\rag\tests\unit\test_config.py services\ai-service\rag\tests\unit\test_types.py -q`

下一阶段入口：

阶段 B 直接复用 `RagSettings` 建立 PostgreSQL/pgvector schema 与连接池，复用核心数据对象实现 Repository，并以 `RagError` 子类统一持久化和 Provider 错误边界。

#### 阶段 B 交付里程碑：数据持久化与可插拔组件

完成日期：2026-06-06

项目当前位置：

RAG 提供 PostgreSQL/pgvector 持久化基础、完整 Repository 边界和八类可插拔组件包。配置可以创建百炼 DeepSeek、百炼 `text-embedding-v4`、PgVectorStore 以及不访问外部服务的 fake 实现。

可用功能：

- 初始化 PostgreSQL/pgvector schema，并通过连接池执行事务和健康检查。
- 持久化 collection、document、chunk、image、trace 和 evaluation 数据。
- 管理文档生命周期，删除文档时同步清理关联 chunks 和 images。
- 通过空注册表、`register_builtin_providers()` 和 Factory 创建 Loader、Splitter、LLM、Embedding、VectorStore、Reranker、Evaluator。
- 保留 `BaseTransform` 抽象接口，具体 Transform 在阶段 C 的 ingestion pipeline 中串行实现。
- 通过百炼 OpenAI-compatible endpoint 调用 `deepseek-v4-flash`。
- 通过百炼 OpenAI 兼容接口批量调用 `text-embedding-v4` 并保持输入输出顺序。
- 为已持久化 chunk 写入 pgvector，执行 cosine search、metadata filter 和按 chunk_id 顺序回表。
- 使用 fake provider 和 RRF 顺序 fallback 执行无外部依赖测试。

验证方式：

- `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests -q`
- `$env:RUN_RAG_EXTERNAL_TESTS='1'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests/external/test_model_providers.py -v`

下一阶段入口：

阶段 C 复用 `DocumentRepository`、`ChunkRepository`、`ImageStorage`、`LoaderFactory`、`SplitterFactory`、`BaseTransform`、`EmbeddingFactory` 和 `VectorStoreFactory`，实现 dedup -> load -> document_summary -> split -> transform -> encode -> upsert 的完整 Ingestion Pipeline。Transform 由 `src/ingestion/transform/TransformPipeline` 串行编排，不创建独立工厂。

#### 阶段 C 交付里程碑：Ingestion & Indexing Pipeline

完成日期：2026-06-07

项目当前位置：

RAG 提供可独立运行的离线数据摄取能力。统一 `IngestionPipeline` 从原始 Markdown/PDF 文档开始，完成 source hash 去重、Loader 标准化、业务 Chunk 适配、串行 Transform、图片描述降级、Dense/BM25 双路索引、差量向量复用和 PostgreSQL 事务写入。

可用功能：

- 对未变化且已成功摄取的文档执行 SHA256 skipped 快速结束。
- 从 Markdown/PDF 提取正文、标题层级、图片占位符和图片 metadata。
- 生成稳定 chunk ID、来源 metadata、image_refs 和有序 chunk_index。
- 串行执行 metadata enrich、chunk rewrite、semantic merge、denoise 和 image caption。
- 复用成功文档中相同 content_hash 的 Dense 向量，仅编码未命中或内容变化的 chunk。
- 将 document、chunk、pgvector、BM25 posting 和 image index 作为完整快照写入。
- 通过 `python -m src.scripts.ingest --path ... [--collection ...] [--force]` 摄取单文件或递归目录。

验证方式：

- `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests -q`
- `uv run --project services/ai-service/rag python -m src.scripts.ingest --help`

下一阶段入口：

阶段 D 直接复用已持久化的 chunk、Dense 向量和 BM25 posting，实现 Query Processor、Dense Route、Sparse Route、RRF Fusion、metadata filter、Rerank 和本地 `query.py` 调试入口。

#### 阶段 D 交付里程碑：Retrieval

完成日期：2026-06-07

项目当前位置：

RAG 提供可独立运行的在线检索能力。查询入口从用户 query 开始，完成查询预处理、Dense 向量召回、BM25 关键词召回、RRF 排名融合、Rerank 前 metadata filter、可降级 Rerank、引用构造和多模态响应组装。

可用功能：

- 通过 `QueryProcessor` 生成 normalized query、keywords、collection、top_k 和购物意图信号。
- 通过 `DenseRoute` 查询 pgvector 语义候选，通过 `SparseRoute` 查询 BM25 倒排索引并回表 chunk。
- 通过 `HybridSearch` 执行 Dense/Sparse 双路 RRF 融合，并在 Rerank 前完成 collection、doc_type、source_type、document_status、lifecycle_status 和 permission 过滤。
- 通过 `RerankController` 在 Cross-Encoder/LLM Reranker 可用时重排候选，在不可用、超时、异常或非法输出时回退过滤后的 RRF 顺序。
- 通过 `RerankOutcome` 显式返回 rerank 结果、fallback 状态和 fallback reason，避免从 provider metadata 推断控制流。
- 通过 `KnowledgeHubResponseBuilder` 输出文本上下文、引用来源和命中图片，不暴露内部 route/tool metadata。
- 通过 `python -m src.scripts.query --query ... [--top-k ...] [--collection ...] [--verbose] [--no-rerank]` 调试完整查询链路。

验证方式：

- `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests -q`
- `uv run --project services/ai-service/rag python -m src.scripts.query --help`

下一阶段入口：

阶段 E 直接复用 `QueryRuntime`、`KnowledgeHubResponse`、citation 和 collection 查询能力，把在线检索链路封装为 MCP tools，提供给 AImodel Agent 调用。

#### 阶段 E 交付里程碑：MCP 工具服务

完成日期：2026-06-08

项目当前位置：

RAG 提供可被 MCP client 调用的 stdio 工具服务。AImodel 或其他外部调用方可以通过 MCP tools 发现工具 schema，并调用知识库查询、collection 列表和文档摘要能力。MCP 层将 `QueryRuntime`、`KnowledgeHubResponse`、citation、多模态图片公开字段和 collection 元数据查询封装成稳定的工具边界。

可用功能：

- 通过 `python -m src.mcp_server.server --transport stdio` 启动 FastMCP stdio server，stdout/stdin 只承载 MCP 协议帧，业务日志写入 `src/logs/app.log`。
- 通过 `.env` 加载 `DATABASE_URL`、`DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、`RAG_SETTINGS_PATH`、`RAG_DEFAULT_COLLECTION` 等运行变量。
- 通过 `query_knowledge_hub` 查询 RAG 知识库，支持 `query`、`collection`、`top_k`、`no_rerank`、`include_image_base64` 参数，并默认不返回图片 base64。
- 通过 `list_collections` 查看已摄取 collection 的文档、chunk 和更新时间摘要。
- 通过 `get_document_summary` 按 `document_id` 或 `source_uri` 查询文档摘要、章节 outline 和基础索引信息。
- 通过结构化业务错误 envelope 返回可恢复错误：`{"ok": false, "error": {"code": "...", "message": "..."}}`。
- 通过 MCP contract 测试锁定 FastMCP 官方 schema、成功输出安全字段和业务错误格式，避免 Agent 看到内部工具 JSON、provider payload、prompt、向量或 BM25 细节。

验证方式：

- `uv run --project services/ai-service/rag pytest services/ai-service/rag/tests/unit/test_mcp_tools.py -v`
- `uv run --project services/ai-service/rag python -m src.mcp_server.server --help`

下一阶段入口：

阶段 F 使用 TraceContext/TraceController 覆盖 Ingestion 和 Query 链路，写入结构化日志，并为 Dashboard 六大页面提供 trace、配置、数据浏览和评估读取能力。

#### 阶段 F 交付里程碑：可观测与管理平台

完成日期：2026-06-08

项目当前位置：

RAG 提供可观测和可视化管理能力。Ingestion 和 Query 主链路注入 TraceContext/TraceController，运行过程写入结构化 JSON Lines 日志，并把关键 trace、文档、chunk、图片、collection 和评估记录投影给 Dashboard 读取。Streamlit Dashboard 提供 sidebar 六页导航，并按选中页面渲染对应 service-backed 页面。

可用功能：

- 通过 `TraceContext` 和 `TraceController` 记录 ingestion/query 基础信息、阶段详情、汇总指标、评估指标、fallback 和错误信息。
- 通过 `JsonFormatter`、`configure_jsonl_logger()` 和 `JsonlTraceWriter` 写入结构化日志和 `traces.jsonl`。
- Ingestion Pipeline 和 Query Runtime 接入 trace 打点，Dashboard 可读取历史 trace 和单次 trace 详情。
- Dashboard service 层可读取组件配置、collection 统计、文档列表、chunk 详情、图片索引、query trace、ingestion trace、evaluation run 和指标趋势。
- sidebar 导航包含六大 Streamlit 页面：系统总览、Ingestion 管理、数据浏览器、Query Trace、Ingestion Trace、评估面板。
- `run_dashboard.py` 可校验 app、构建 Streamlit 启动命令并支持 dry-run，测试不需要真实打开浏览器。
- `test_dashboard_pages.py` 使用真实 PostgreSQL 测试数据验证六大页面都能读取配置、数据库记录、trace 和 evaluation 数据并完成渲染入口调用；`test_dashboard_services.py` 覆盖 app sidebar 导航和默认页面分发。

验证方式：

- `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_pages.py -v`
- `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`
- `uv run --project services/ai-service/rag python -m src.scripts.run_dashboard --dry-run --port 8504`

下一阶段入口：

阶段 G 直接复用 PostgreSQL 中的 evaluation run/result 结构和 Dashboard 评估面板，继续实现黄金测试集、自定义检索指标、Ragas adapter、策略对比和评估趋势输出。

### 6.3 阶段任务跟踪表

任务拆分原则：

- 每个子任务都应尽量控制为 **45-75 分钟** 可完成、可验收的增量，避免过薄的纯占位任务，也避免一次覆盖多个模块的厚重任务。
- 每个子任务默认都包含 **TDD 流程**：先写对应 pytest 单元测试或冒烟测试，再实现最小代码让测试通过。
- 若某个任务需要数据库、LLM 或外部模型，应优先使用 fake provider、mock 或测试容器，真实外部调用使用 pytest marker 隔离。

#### 阶段 A：配置与项目骨架

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| A1 | 创建独立模块基础文件 | [✔] | 2026-06-06 | 独立模块说明、项目元数据、依赖声明、pytest 配置、忽略规则和基础包入口 |
| A2 | 创建独立运行入口、Docker 骨架和 pytest 冒烟测试 | [✔] | 2026-06-06 | 最小运行入口、健康状态、Docker 骨架、六个关键包入口，4 个冒烟测试通过 |
| A3 | 创建 `config/settings.example.yaml` 示例配置 | [✔] | 2026-06-06 | 版本化配置模板覆盖可插拔组件、流水线、存储、可观测、Dashboard、评估和 MCP，运行时 `settings.yaml` 由 Git 忽略；5 个单元测试通过 |
| A4 | 创建 prompt 配置目录 | [✔] | 2026-06-06 | 统一英文 Prompt YAML 契约，覆盖 rerank、chunk rewrite、六类图片理解策略和中文 caption 输出，10 个配置测试通过 |
| A5 | 实现配置读取和校验 | [✔] | 2026-06-06 | 完整 `RagSettings`、Provider/model selector、活动环境变量、Embedding/pgvector 维度、检索参数和 Prompt 占位符校验，18 个配置测试通过 |
| A6 | 定义核心类型和统一异常 | [✔] | 2026-06-06 | Document、ImageMetadata、Chunk、RetrievalResult 及六类 RagError 子类，覆盖必填位置、非空文本、来源区间和异常链校验，16 个类型测试通过 |
| A7 | 迁移至 uv 包管理与锁定环境 | [✔] | 2026-06-06 | 183 包锁文件，创建 111 包开发环境，统一 README/auto-coder/DEV_SPEC 命令，Docker frozen build 与运行通过；6 个冒烟测试、106 个全量测试通过 |

#### 阶段 B：数据持久化与可插拔组件

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| B1 | 编写 collection/document/chunk schema | [✔] | 2026-06-06 | 稳定字符串 ID、pgvector/HNSW、核心约束和索引；真实 PostgreSQL 连续初始化两次通过，5 个集成测试通过 |
| B2 | 编写 image/trace/evaluation schema | [✔] | 2026-06-06 | 图片索引、包含顶层 `query_result` 的 Query Trace、四段式 Ingestion Trace、评估任务和指标结果表；真实 PostgreSQL 幂等初始化通过 |
| B3 | 实现数据库连接池和 schema 初始化 | [✔] | 2026-06-10 | 配置驱动惰性连接池、生命周期、健康检查、事务回滚和幂等 schema 初始化；每条 PostgreSQL session 使用 `database.timezone=Asia/Shanghai`，真实连接池验证 `SHOW timezone = Asia/Shanghai`；78 个相关回归测试通过 |
| B4 | 实现 Document/Chunk/Image Repository | [✔] | 2026-06-06 | collection 自动创建、文档版本替换、Chunk 批量 upsert、图片安全落盘和索引查询；19 个集成测试通过 |
| B5 | 实现 Trace/Evaluation Repository | [✔] | 2026-06-06 | Query/Ingestion Trace 与评估任务/指标的不可变记录、幂等 upsert 和历史查询；21 个集成测试通过 |
| B6 | 实现文档生命周期管理 | [✔] | 2026-06-06 | `lifecycle_status` schema、状态流转、retrievable 查询过滤和 deleted 清理 chunks/images；23 个集成测试通过 |
| B7 | 建立 libs 可插拔组件包结构 | [✔] | 2026-06-06 | 八个 libs 可插拔组件包和稳定导入契约；2 个单元测试通过 |
| B8 | 实现 Loader/Splitter libs 基类、factory 和 DocumentChunker 契约 | [✔] | 2026-06-06 | loader/splitter 基类、注册表工厂、fake/markdown/pdf loader、fake/recursive splitter 和 DocumentChunker 契约；9 个指定单元测试通过 |
| B9 | 实现 LLM/Embedding libs 基类、factory 和 fake 实现 | [✔] | 2026-06-06 | BaseLLM/LLMFactory/FakeLLM 与 BaseEmbedding/EmbeddingFactory/FakeEmbedding，统一 `chat()`、`embed()`、`embed_batch()`；10 个指定单元测试通过 |
| B10 | 实现 VectorStore/Reranker/Evaluator libs 基类、factory 和 fake 实现 | [✔] | 2026-06-06 | 三类最小接口、注册表工厂、固定维度 fake vector store、确定性 fake 和 RRF 顺序回退；17 个指定单元测试通过 |
| B11 | 实现首批真实组件最小适配 | [✔] | 2026-06-06 | 百炼 DeepSeek、百炼 text-embedding-v4 OpenAI 兼容调用和 PgVectorStore；factory 单元测试、pgvector 集成测试和 external smoke test 覆盖对应 Provider 边界 |

#### 阶段 C：Ingestion & Indexing Pipeline

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| C1 | 实现文档 SHA256 去重与 skipped 快速结束 | [✔] | 2026-06-06 | 流式 SHA256、success 文档去重查询、force 绕过、Loader 前短路和 skipped ingestion trace；5 个单元测试、1 个 PostgreSQL 集成测试通过 |
| C2 | 实现文档加载、Markdown 标准化与图片引用提取 | [✔] | 2026-06-10 | canonical Markdown、fenced-code 感知标题与图片解析、安全本地 Markdown 图片引用、MarkItDown/PyMuPDF PDF 转换、xref 去重、失败写入清理、稳定图片占位符与 metadata；PyMuPDF 图片矩形绑定邻近文本锚点，MarkItDown 无页标记时仍能将图片插入对应章节附近；真实购物指南 PDF 冒烟验证通过 |
| C3 | 实现 DocumentChunker、稳定 chunk_id 与引用保留验证 | [✔] | 2026-06-10 | 稳定 chunk ID、heading offset、section_path 分发、metadata 深拷贝、chunk_index、来源 metadata、image_refs 和 SplitterStep；纯图片占位符片段合并到相邻正文 chunk，确保检索单元包含文本语义 |
| C4 | 实现 MarkdownSectionSplitter | [✔] | 2026-06-23 | 按 `###` 构建 Markdown section，章节层级由 `DocumentChunker` 写入 `section_path`；短 section 合并，长 section 内部二次切分，长表格按行分组且每个分片保留表头；表格拆分时只有第一段可携带表格前正文，后续表格分片只保留表头和数据行；表格尾部 chunk 可与后续建议块在 `chunk_size` 内合并；chunk 正文不保留 `#`、`##`、`###` 标题行；41 个配置和 splitter 单元测试通过 |
| C5 | 实现 Transform 抽象基类与具体实现 | [✔] | 2026-06-10 | BaseTransform、配置驱动 TransformPipeline、metadata enrich、chunk rewrite、semantic merge、denoise、英文 Prompt、噪声 fixture 和幂等测试；ChunkRewriter 仅使用文本节点与 Document.summary 调用 LLM，并按原顺序保留图片占位符；无效文本响应直接失败，纯图片占位符 chunk 跳过文本 rewrite |
| C6 | 实现 ImageCaptioner | [✔] | 2026-06-11 | `image_captioner` transform step、`BaseVisionLLM`、`DashScopeVisionLLM`、正文 caption 注入和 Dense/BM25 索引；图片 caption/provenance 记录在 `transform.sub_stages` |
| C7 | 实现 DenseEncoder | [✔] | 2026-06-06 | DenseEncodingResult、DenseEncoder、EmbeddingStep.run_dense、content_hash 差量跳过、当前运行去重、有限向量校验和单 chunk 向量生成；6 个相关测试、131 个全量测试通过，2 个 external smoke test 默认跳过 |
| C8 | 实现 BM25Indexer | [✔] | 2026-06-07 | BM25Candidate、BM25IndexResult、BM25Indexer.index/query、词频统计、倒排索引、关键词 Top-k 排序、中文连续文本 n-gram fallback 和重复 index 状态重建；6 个相关测试、137 个全量测试通过，2 个 external smoke test 默认跳过 |
| C9 | 实现 BatchProcessor 批处理优化 | [✔] | 2026-06-07 | BatchProcessor、BatchRunResult、BatchSuccess、BatchFailure、DenseEncoder.encode_batch、batch_size 拆分、throttle_seconds 节流、有限 retry、失败隔离、EmbeddingStep.run_batch、Dense/BM25 批处理编排；20 个相关测试、145 个全量测试通过，2 个 external smoke test 默认跳过 |
| C10 | 实现统一 upsert | [✔] | 2026-06-07 | rag_bm25_terms schema、BM25Storage、UpsertStep 单事务完整快照写入、pgvector/image/repository 调用方事务接口、图片文件失败恢复、重复 upsert 幂等和内容变更旧 chunk 清理；2 个 C10 PostgreSQL 集成测试、148 个全量测试通过，2 个 external smoke test 默认跳过 |
| C11 | 实现统一 Pipeline MVP 编排和集成测试 | [✔] | 2026-06-07 | IngestionPipelineResult、完整依赖校验、run_indexing、Markdown 图片摄取、Splitter、包含 ImageCaptioner 的 Transform Pipeline、成功文档 content_hash 向量复用、重复内容单次编码、Dense/BM25 batch、统一 upsert、lifecycle success 和重复文件 dedup skip；6 个 ingestion integration 测试、14 个 embedding 单元测试、153 个全量测试通过，2 个 external smoke test 默认跳过 |
| C12 | 实现 `ingest.py` 摄取脚本入口 | [✔] | 2026-06-10 | 必填 `--path`、可选 `--collection`、`--force`、父目录 `.env` 自动加载、系统环境优先、RAG 根目录运行时路径解析、递归 Markdown/PDF 发现、配置驱动 Pipeline 组装、JSON 结果、错误码和连接池释放；文档摘要使用 `ingestion.document_summary.llm_provider` 指定的 DeepSeek Provider；68 个相关单元测试通过 |

#### 阶段 D：Retrieval

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| D1 | 实现 Query Processor | [✔] | 2026-06-07 | 不可变 ProcessedQuery 和 keywords 快照、Unicode/空白标准化、关键词提取、collection/top_k 类型校验与默认覆盖、四类购物意图、商品工具协同判断、可注入 QueryRewriter 和异常/空结果 fallback；15 个 D1 单元测试通过 |
| D2 | 实现 Dense Route 向量检索 | [✔] | 2026-06-07 | raw query/ProcessedQuery 输入、Query Embedding、配置驱动 dense_top_k、VectorStore 语义召回、RetrievalResult 校验、embedding/vector search 错误边界和低侵入 Trace；8 个 D2 单元测试通过 |
| D3 | 实现 Sparse Route BM25 回表检索 | [✔] | 2026-06-07 | raw query/ProcessedQuery 输入、配置驱动 sparse_top_k、BM25 关键词召回、VectorStore 按 ID 回表、BM25 顺序与分数保留、缺失 chunk 跳过、空 keywords skip、错误边界和低侵入 Trace；9 个 D3 单元测试通过 |
| D4 | 实现 RRF Fusion | [✔] | 2026-06-07 | Dense/Sparse 双路 RRF 排名融合、top_k/rrf_k 参数校验、route 内重复 chunk 去重、跨 route 候选合并、RRF 分数输出、fusion metadata 诊断和稳定 tie-break；8 个 D4 单元测试通过 |
| D5 | 实现 HybridSearch 编排 | [✔] | 2026-06-07 | ProcessedQuery 输入、Dense/Sparse 双路调用、RRF Fusion 编排、配置驱动 fusion_top_k/rrf_k、HybridSearchResult、单路失败降级、双路失败错误边界和低侵入 Trace；5 个 D5 单元测试通过 |
| D6 | 实现 Rerank 前候选过滤 | [✔] | 2026-06-07 | CandidateFilter、CandidateFilterReport、HybridSearch.search filters 参数、HybridSearch.apply_metadata_filter 可复用入口、collection/doc_type/source_type/document_status/lifecycle_status/permission 过滤、默认排除 deleted、include_deleted 布尔校验、过滤 trace 和未知过滤键错误边界；8 个 D6 单元测试通过 |
| D7 | 实现 Cross-Encoder Reranker 适配 | [✔] | 2026-06-07 | CrossEncoderReranker、CrossEncoderScorer 协议、query-doc pair 打分、按模型分数稳定排序、top_k 截断、rerank metadata 诊断、sentence-transformers 惰性加载、ProviderError 错误边界和 RerankerFactory cross_encoder 注册；8 个 D7 单元测试通过 |
| D8 | 实现 LLM Rerank 适配 | [✔] | 2026-06-11 | LLMReranker、PromptTemplate 加载、BaseLLM 注入和结构化 JSON 排名解析；Prompt 强制只返回 JSON object array，禁止 ID-only array、Markdown fence 和解释文字；真实 DeepSeek 查询验证 rerank 成功且未触发 fallback |
| D9 | 实现 rerank fallback | [✔] | 2026-06-07 | RerankController、RerankOutcome、配置驱动 top_k、provider 调用前候选深拷贝、reranker 不可用/直接或 ProviderError 包装的 timeout/普通异常 fallback、非法/过滤集外/候选数量不符的 provider 输出防护、过滤后 RRF 顺序保留、显式 fallback 状态、低侵入 rerank trace 和 trace sink 失败隔离；28 个 Reranker 单元测试通过 |
| D10 | 实现引用构造 | [✔] | 2026-06-07 | 共享不可变 Citation 契约、CitationBuilder、Dense/Sparse/Fake 检索 metadata 来源字段传播、顶层 metadata 读取、排序保持、URI 文件名解码标题回退、section_path 归一化、JSON 输出、trace_id 关联、脏类型/缺失来源 fail fast 和输入 metadata 不变性；Citation、核心类型、来源 metadata 和 pgvector 回归测试通过 |
| D11 | 实现多模态 Response Builder | [✔] | 2026-06-07 | 不可变 KnowledgeHubResponse/ResponseImage 公共契约、排名编号证据块、配置驱动 EvidenceContextOptimizer、优化失败 fallback、CitationBuilder 复用、image_refs 有序去重和关联 chunk 聚合、ImageResolver 最小接口、ImageStorage 批量 ID 查询、缺失图片安全跳过、显式空结果以及内部 route/tool metadata 隔离；Response Builder 单元测试和真实 PostgreSQL 图片查询集成测试通过 |
| D12 | 实现 `query.py` 脚本入口 | [✔] | 2026-06-07 | 配置驱动完整查询链路、PostgreSQL BM25 collection 查询、过滤前 Fusion 快照、RerankOutcome 显式 fallback 状态、安全 verbose 输出、no-rerank 跳过和连接池释放；63 个 Retrieval 单元测试通过 |
| D13 | 实现 Retrieval 单元测试矩阵 | [✔] | 2026-06-07 | 120 个 Retrieval/Reranker/Response 单元测试，补齐 Fusion 失败、PostgreSQL BM25 边界、QueryRuntime rerank、no-op/duplicate/empty fallback、Citation 来源 metadata 和图片 resolver 脏契约；目标模块覆盖率 91% |
| D14 | 实现 Retrieval 集成测试 | [✔] | 2026-06-07 | PostgreSQL/pgvector 集成测试，覆盖 QueryProcessor、DenseRoute、SparseRoute、HybridSearch、metadata filter、RerankController、Response Builder、`query.py` verbose 输出、Dense 失败时 Sparse fallback；2 个 D14 集成测试通过 |

#### 阶段 E：MCP 工具服务

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| E1 | 搭建 MCP Server | [✔] | 2026-06-07 | FastMCP server 工厂、stdio 启动入口、`.env` 加载、app.log 文件日志、配置驱动 tool 注册、未知工具 fail fast、E1 placeholder tool 错误边界和 SDK ToolError 包装契约；5 个 MCP 单元测试通过 |
| E2 | 暴露 `query_knowledge_hub` | [✔] | 2026-06-08 | QueryKnowledgeHubTool、QueryRuntime 适配、请求原语校验先于 settings 加载、默认 collection/top_k、no_rerank、结构化业务错误、默认不返回图片 base64、显式 include_image_base64 支持、PostgreSQL pool 打开失败也能释放资源和 FastMCP 真实 query tool 注册；12 个 MCP 单元测试通过 |
| E3 | 暴露 `list_collections` 和 `get_document_summary` | [✔] | 2026-06-08 | MetadataTool、PostgresMetadataReader、真实 FastMCP collection/summary handler 注册、空 collection 可读业务错误、document_id/source_uri 参数校验、文档摘要与章节 outline 返回；17 个 MCP 单元测试通过 |
| E4 | 完成 MCP tools 测试 | [✔] | 2026-06-08 | 官方 FastMCP schema 精确断言、成功输出安全字段扫描和结构化业务错误 envelope 契约测试；20 个 MCP 单元测试通过 |

#### 阶段 F：可观测与管理平台

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| F1 | 实现 TraceContext 和 TraceController | [✔] | 2026-06-08 | `src/core/trace` 包导出、内存 TraceContext、TraceController、阶段耗时/输入输出摘要记录、flush sink、错误/fallback 详情和防御性快照；4 个 TraceContext 单元测试通过 |
| F2 | 实现 ingestion trace 数据结构 | [✔] | 2026-06-10 | `TraceContext.ingestion()`、source_uri/source_hash 基础信息校验、摄取阶段 allowlist、ingestion summary/evaluation 指标和 JSON-safe None 语义；顶层阶段支持结构化 `sub_stages`，用于保存 Transform Pipeline 内每个具体实现的独立耗时、状态和受限 snapshots |
| F3 | 实现 query trace 数据结构 | [✔] | 2026-06-11 | Query 五段式结构；Dense/Sparse 记录命中的 chunk IDs，Fusion/Filter/Rerank 记录轻量候选快照和排序变化；顶层 `query_result.contexts` 保存可回查原始 chunk 的 `chunk_id/score/rank`，`query_result.content` 保存实际返回给 Agent 的最终上下文，citation 不记录 source_uri、image 仅记录 image_id/chunk_ids/quality_status；summary 使用 `top_score` 代替重复的 `top_k_results`，并完成结构校验和 Dashboard DTO 透传 |
| F4 | 实现 Python logging + JSONFormatter | [✔] | 2026-06-08 | `JsonFormatter`、`configure_jsonl_logger()` 和 `JsonlTraceWriter`，支持创建父目录、单行合法 JSON、trace snapshot 顶层 JSON 写入和 TraceController sink 集成；`src/logs/.gitkeep` 纳入版本控制，运行时 `*.log/*.jsonl` 由 Git 忽略；15 个 TraceContext/TraceWriter 单元测试通过 |
| F5 | 将 Trace 打点注入 ingestion 和 query 链路 | [✔] | 2026-06-11 | JSONL/PostgreSQL 双写、Transform 子阶段打点、Query 阶段候选快照打点，并将 QueryRuntime 实际返回给 Agent/调用方的 Agent-ready final context、contexts 和轻量 citations/images 投影写入 Query Trace 顶层 `query_result`，完整 citation/image 响应契约保持不变；真实查询验证 `top_score/query_result` 正常写入 |
| F6 | 实现配置读取和数据浏览服务 | [✔] | 2026-06-08 | Dashboard 配置概览服务和数据浏览服务，可读取组件配置、文档、chunk、图片和索引状态；2 个 Dashboard service 集成测试和 ruff 通过 |
| F7 | 实现 Trace 读取和评估服务 | [✔] | 2026-06-08 | Dashboard trace 历史/详情读取、阶段瀑布图 DTO、候选数量/降级信息投影、同步评估运行和指标趋势读取；4 个 Dashboard service 集成测试和 ruff 通过 |
| F8 | 实现系统总览、Ingestion 管理页面和摄取操作 | [✔] | 2026-06-10 | `IngestionOperationService`，点击 Run ingestion 会复用 `run_ingest_cli()` 触发真实摄取并展示 success/skipped/failed 结果；支持多文件选择、目录上传、服务器文件夹候选发现和单文件取消摄入；22 个 Dashboard 集成测试和 ruff 通过 |
| F9 | 实现数据浏览器与 Query Trace 页面 | [✔] | 2026-06-10 | 数据浏览器和 Query Trace 页面；Trace 下拉框使用固定 session_state key 驱动详情切换，跨 collection 的过期选择自动回退最新记录 |
| F10 | 实现 Ingestion Trace 与评估面板页面 | [✔] | 2026-06-10 | Ingestion Trace 和评估面板页面；包含主阶段瀑布图、changed/unchanged Transform Breakdown、柱状图和按 Transform 类型着色的 Result Diff；未变化文本使用独立显式深色样式 |
| F11 | 实现 Dashboard 启动脚本和冒烟测试 | [✔] | 2026-06-08 | Streamlit app 最小入口、六大页面模块导入校验、`run_dashboard.py` dry-run、端口配置、headless 启动命令和注入 command runner；14 个 Dashboard service/page/launcher 集成测试通过 |
| F12 | 完成 Dashboard 六大页面测试 | [✔] | 2026-06-08 | `test_dashboard_pages.py` 使用真实 PostgreSQL 测试数据验证六大页面读取配置、数据库记录、trace 和 evaluation 数据并完成渲染；app 入口测试覆盖 sidebar 六页导航和默认页面分发；16 个 Dashboard 集成测试和 ruff 通过 |

#### 阶段 G：质量评估体系

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| G1 | 准备黄金测试集格式 | [✔] | 2026-06-10 | `tests/fixtures/golden_set.json`，定义 `id/collection/question/golden_answer/expected_sources/expected_keywords` 字段，并覆盖无线耳机、人体工学键盘和解压玩具三类购物指南问题；2 个单元测试通过 |
| G2 | 实现自定义检索指标 | [✔] | 2026-06-10 | `src/observability/evaluation/metrics.py`，实现无 LLM 依赖的 Hit Rate@K、MRR 和 NDCG@K；指标支持字符串来源和 mapping 候选输入，校验 dataset/predictions 对齐、top_k 和 expected_sources 契约；5 个 evaluation 单元测试通过 |
| G3 | 接入 Ragas 生成指标 | [✔] | 2026-06-10 | `src/observability/evaluation/ragas_adapter.py`，封装 faithfulness 和 answer_relevancy 生成质量指标；真实 Ragas 依赖采用懒加载，普通单测使用 fake backend，真实 import 测试使用 external marker 隔离；adapter 在真实 backend 边界将项目内部 `question/answer/contexts/ground_truth` 行转换为 Ragas 0.2 的 `user_input/response/retrieved_contexts/reference` 列，并对空 `metric_names` fail fast；7 个 evaluation 单元测试通过，1 个 external 测试按环境跳过 |
| G4 | 实现策略对比评估 | [✔] | 2026-06-10 | `src/observability/evaluation/runner.py` 通过可注入 retrieval callable 对比 hybrid、dense_only、sparse_only、rerank 四种策略，并复用 Hit Rate@K、MRR@K、NDCG@K 计算指标；runner 不直接打开数据库或构造 QueryRuntime；包入口导出 `RetrievalStrategy` 以支持外部自定义策略，空 metrics 配置 fail fast；9 个 evaluation 单元测试通过，1 个 external 测试按环境跳过 |
| G5 | 实现评估历史趋势展示 | [✔] | 2026-06-10 | `EvaluationRunner.save_results()`，将策略对比结果写入 `EvaluationRepository` 边界，生成一条 evaluation run 和按 `strategy.metric` 命名的 metric rows；metric details 保留 strategy、retrieval_mode、use_rerank、raw_metric_name、sample_count 和 predictions，供 Dashboard 趋势与详情展示；保存前校验各策略 prediction 数量一致，避免 summary 误导；11 个 evaluation 单元测试通过，1 个 external 测试按环境跳过 |
| G6 | 实现真实 Ragas 评估入口与最终上下文优化 | [✔] | 2026-06-12 | 注册 `ragas` 到 `EvaluatorFactory`；新增 `run_evaluation.py` 读取 golden set、调用真实 Query Pipeline、用 `query_result.content` 作为 RAG answer、按 `query_result.contexts` 回查 chunk 正文构造 Ragas `retrieved_contexts`、调用 `RagasEvaluator` 并写入 evaluation run/results；同时将 `query_result.content` 升级为配置驱动的 Agent-ready final context，优化失败 fallback 到原始编号证据块；58 个目标单元测试通过，ruff 通过，真实 Ragas provider 创建 smoke 通过 |

#### 阶段 H：AImodel 联调集成

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| H1 | 执行 AImodel 集成前验收门禁 | [✔] | 2026-06-12 | Dashboard 六大页面、RAG 全链路 E2E 和 MCP stdio 可连接验收通过；3 个 H1 目标测试通过，ruff 通过，可进入 H2 AImodel 工具适配 |
| H2 | 实现 AImodel RAG 工具适配 | [✔] | 2026-06-12 | 新增 `search_shopping_guides` 和 `StdioMcpRagKnowledgeClient`，默认通过 `uv run --project services/ai-service/rag` 启动 stdio MCP 并调用 `query_knowledge_hub`，只返回 content、citations、images、is_empty、trace_id 等公共字段；保留可注入 client 以便单元测试和后续长期连接优化；MemoryStore 已支持 assistant message 与多个 query trace 的去重逻辑关联；27 个 AImodel 目标测试通过，ruff 通过 |
| H3 | 将 RAG 工具接入 Agent 工具列表 | [✔] | 2026-06-12 | 新增 `build_rag_tool()`，将 `search_shopping_guides` 包装成 LangChain Agent 工具并加入同步/流式 Agent tools 列表；工具调用结果进入 per-request `tool_results`，最终 assistant message 可关联去重后的 RAG query trace id；测试环境缺少 LangChain 时使用轻量 fallback tool 保持单元测试可运行；29 个 AImodel 目标测试通过，ruff 通过 |
| H4 | 验证商品 API 工具与 RAG 工具协同 | [✔] | 2026-06-12 | System prompt 明确商品事实必须来自商品搜索/详情工具，覆盖价格、库存、优惠、规格、可购买商品和商品链接；RAG 只用于选购指南、品类知识、政策 FAQ、售后规则和文档知识上下文；禁止把 RAG 当实时商品事实来源或编造引用；22 个 AImodel 边界回归测试通过，ruff 通过 |
| H5 | 验证简单询问和商品链接场景 | [✔] | 2026-06-12 | System prompt 明确推荐场景使用商品搜索工具、商品链接对比场景使用商品详情工具、选购指南和政策 FAQ 场景使用 RAG 工具；新增场景测试覆盖四类入口；23 个 AImodel 场景回归测试通过，ruff 通过 |
| H6 | 完成前后端联调和端到端测试 | [✔] | 2026-06-12 | AImodel SSE 输出过滤原始 RAG tool JSON，并移除普通文本和跨流片段形式的 `chunk_id`、`trace_id` 等内部标识；前端可见 delta、done answer 和持久化 assistant message 均使用清洗后的回答；25 个 AImodel 目标测试通过，ruff 通过 |
| H7 | 优化 AImodel MCP 长连接 | [✔] | 2026-06-12 | `get_rag_knowledge_client()` 返回进程级 `PersistentMcpRagKnowledgeClient`，RAG stdio MCP 子进程和 `ClientSession` 在多次 RAG 查询间复用；FastAPI shutdown 调用 `close_rag_knowledge_client()` 释放资源，未创建过 client 时 shutdown 不会启动新 MCP 资源，session 启动失败会清理后台事件循环；AImodel 通过 MCP 调用 RAG 时 `request_source=aimodel`，直接 MCP 调用默认 `request_source=mcp`，CLI 保持 `request_source=query_cli`；ai-service Docker 镜像包含 RAG 子项目并可在 `/app/rag` 浅路径启动 MCP；H7 目标回归测试、MCP 工具测试、Query Runtime 测试和 AImodel RAG 工具测试通过，ruff 通过 |

### 6.4 总体进度表

| 阶段 | 总任务数 | 已完成 | 进度 |
| --- | ---: | ---: | --- |
| Phase A | 7 | 7 | 100% |
| Phase B | 11 | 11 | 100% |
| Phase C | 12 | 12 | 100% |
| Phase D | 14 | 14 | 100% |
| Phase E | 4 | 4 | 100% |
| Phase F | 12 | 12 | 100% |
| Phase G | 5 | 5 | 100% |
| Phase H | 7 | 7 | 100% |
| **总计** | **71** | **71** | **100%** |

### 6.5 阶段实施明细

> 每个子任务都必须按 TDD 执行：先写测试，再写实现。每个子任务都采用“标题 -> 文字”的结构，避免宽表格影响阅读。真实 LLM/API 调用必须使用 pytest marker 隔离。

#### 阶段 A：配置与项目骨架

##### A1：创建独立模块基础文件

目标：让 RAG 子系统具备独立 Python 项目的基础文件，方便后续单独开发、测试和说明。

修改文件：`README.md`、`pyproject.toml`、`.gitignore`、`src/__init__.py`、`tests/__init__.py`

实现类/函数：

- 项目元数据
- 依赖声明
- pytest 配置
- 模块忽略规则
- 包初始化文件

验收标准：`pyproject.toml` 可被 Python 工具识别，README 说明独立模块定位，目录可被 Python 导入。

测试方法：使用 `uv run --project services/ai-service/rag python -c` 验证 `pyproject.toml` 可被 `tomllib` 解析，并验证 `src` 包可导入。

##### A2：创建独立运行入口、Docker 骨架和 pytest 冒烟测试

目标：让 RAG 子系统可以作为独立模块构建 Docker 镜像，具备最小本地运行入口和 pytest 测试基座，并在入口建立后验证关键包可导入。

修改文件：`main.py`、`Dockerfile`、`.dockerignore`、`tests/test_smoke.py`、`src/core/__init__.py`、`src/libs/__init__.py`、`src/ingestion/__init__.py`、`src/storage/__init__.py`、`src/observability/__init__.py`、`src/mcp_server/__init__.py`

实现类/函数：

- `main()`：命令行或服务入口
- 健康检查占位
- Docker 构建入口
- `test_main_importable()`：验证最小运行入口可导入
- `test_rag_packages_importable()`：验证 `src.core`、`src.libs`、`src.ingestion`、`src.storage`、`src.observability`、`src.mcp_server` 等关键包可导入

验收标准：`main.py` 可导入；Dockerfile 明确 Python 版本、依赖安装和启动命令；构建上下文不会包含日志、缓存和本地数据库数据；pytest 可运行；关键包 import 校验通过。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\test_smoke.py -v`

##### A3：创建统一配置示例

目标：提供首版 `settings.example.yaml` 示例，作为配置驱动开发的入口；本地运行时复制为被 Git 忽略的 `settings.yaml`。

修改文件：`config/settings.example.yaml`

实现类/函数：

- 配置字段样例

验收标准：LLM、Embedding、Transform、Retrieval、Dashboard 配置齐全。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_config.py -v`

##### A4：创建 prompt 配置目录

目标：将提示词从业务代码中分离，便于后续评估和策略替换。

修改文件：`config/prompts/rerank_prompt.yaml`、`config/prompts/document_summary_prompt.yaml`、`config/prompts/rewrite_chunk_prompt.yaml`、`config/prompts/image_caption_prompt.yaml`

实现类/函数：

- prompt 模板

验收标准：三类 prompt 可被读取；Prompt 的 system instruction、user template、description 和策略说明统一使用英文；Image-to-Text Prompt 必须通过英文指令要求 `description` 和 `key_facts` 使用简体中文，并让 `extracted_text` 原样保留图片文字；英文检查只禁止 CJK 指令，不得错误拒绝 `°C`、`≥` 等合法技术符号。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_config.py -v`

##### A5：实现配置读取和校验

目标：实现配置加载、环境变量引用和缺失配置校验。

修改文件：`src/core/config.py`、`tests/unit/test_config.py`

实现类/函数：

- `RagSettings`：定义 settings.yaml 的配置结构和校验规则
- `load_settings()`：加载配置或模板
- `load_prompt()`：加载配置或模板

验收标准：缺配置时抛可读异常，环境变量引用可校验。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_config.py -v`

##### A6：定义核心类型和异常

目标：建立 Ingestion、Retrieval、Trace 共用的数据类型和异常基类。

修改文件：`src/core/types.py`、`src/core/errors.py`、`tests/unit/test_types.py`

实现类/函数：

- `Document(id,text,metadata)`：定义核心数据契约
- `Chunk(id,text,metadata,chunk_index,start_offset,end_offset)`：定义核心数据契约
- `ImageMetadata`：定义核心数据契约
- `RetrievalResult`：定义流程返回结果
- `RagError`：定义 RAG 子系统统一异常基类

验收标准：`Document.metadata.images[]` 只持久化 `id/path`；Loader 内部可以使用页码、物理位置和 offset 完成占位符插入，但不得把这些定位字段写入最终 metadata；`Chunk` 支持 `metadata`、`start_offset` 和 `end_offset`；来源字段统一写入 chunk metadata，类型可被 Ingestion、Retrieval、Trace 复用。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_types.py -v`

##### A7：迁移至 uv 包管理与锁定环境

目标：使用 uv 统一 RAG 独立模块的依赖解析、虚拟环境创建、锁文件、测试命令和 Docker 安装流程，消除系统 Python 与项目依赖状态不一致的问题。

修改文件：`pyproject.toml`、`uv.lock`、`Dockerfile`、`.dockerignore`、`.gitignore`、`README.md`、`tests/test_smoke.py`、`.codex/skills/auto-coder/SKILL.md`、`DEV_SPEC.md`

实现类/函数：

- `uv.lock`：锁定生产依赖和可选开发依赖的完整版本与来源
- `Dockerfile`：使用固定版本 uv 和 `uv sync --frozen --no-dev` 构建独立运行环境
- `README.md`：记录 `uv sync --extra dev`、`uv run pytest`、`uv run ruff` 和本地运行命令
- `test_uv_project_contract()`：验证锁文件、Python 版本、开发 extra 和 uv 项目配置
- `test_docker_skeleton_uses_uv()`：验证 Docker 通过 uv frozen lock 安装生产依赖
- `auto-coder/SKILL.md`：所有 Python、pytest、Ruff 和规格同步命令通过 `uv run --project services/ai-service/rag` 执行

验收标准：`uv lock --check` 通过；`uv sync --extra dev --frozen` 创建项目 `.venv`；所有测试和 Ruff 通过 `uv run` 执行；Dockerfile 复制 `pyproject.toml` 与 `uv.lock` 并使用固定版本 uv frozen sync；README 和 auto-coder 统一记录 uv 工作流；`AGENTS.md` 等无关 dirty 文件不纳入任务。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\test_smoke.py -v`；`$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services\ai-service\rag\tests -q`；`uv run --project services/ai-service/rag ruff check services\ai-service\rag\src services\ai-service\rag\tests`

#### 阶段 B：数据持久化与可插拔组件

##### B1：建立核心文档 schema

目标：创建 collection、document、chunk 的 PostgreSQL/pgvector 基础表。
三个核心表直接使用 Python 业务层生成的稳定字符串 ID，不增加数据库自增
主键或额外业务 ID 映射列。

修改文件：`src/storage/schema.sql`、`tests/integration/test_repositories.py`、
`services/postgres/Dockerfile`、`services/postgres/initdb/002-create-vector.sql`

实现类/函数：

- `rag_collections`：定义数据库表结构
- `rag_documents`：定义数据库表结构
- `rag_chunks`：定义数据库表结构
- `services/postgres/Dockerfile`：为共享 PostgreSQL 18 镜像安装 pgvector
- `002-create-vector.sql`：为新建数据库卷启用 `vector` extension

验收标准：pgvector extension 和核心表可初始化；`rag_collections.id`、
`rag_documents.id`、`rag_chunks.id` 均为 `TEXT PRIMARY KEY`；
`rag_documents.collection_id` 和 `rag_chunks.document_id` 使用 `TEXT`
关联稳定 Python ID；schema 支持重复执行。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_repositories.py -v`

##### B2：建立图片、Trace、评估 schema

目标：补齐 `image_index` 图片索引、Trace 索引和评估历史相关表。
评估历史必须拆分为 `rag_evaluation_runs` 任务表和
`rag_evaluation_results` 指标结果表，支持一个评估任务保存多项指标结果。

修改文件：`src/storage/schema.sql`、`tests/integration/test_repositories.py`

实现类/函数：

- `image_index`：记录图片文件路径、collection、doc_hash 和页码
- `rag_query_traces`：定义数据库表结构
- `rag_ingestion_traces`：定义数据库表结构
- `rag_evaluation_runs`：定义数据库表结构
- `rag_evaluation_results`：按 `run_id` 保存单项评估指标和结果详情

验收标准：`image_index`、Trace 和两张评估表可初始化；
`idx_collection`、`idx_doc_hash` 索引存在；Trace 表分别保存基础信息、
阶段详情、汇总指标和评估指标；评估结果通过外键归属评估任务；
schema 可重复执行。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_repositories.py -v`

##### B3：实现数据库连接和 schema 初始化

目标：封装 PostgreSQL 连接池和 schema 初始化入口。

修改文件：`src/storage/postgres.py`、`tests/integration/test_repositories.py`

实现类/函数：

- `PostgresPool.from_settings()`：从 `settings.database.url_env` 读取 DSN，按 `pool_size` 创建惰性连接池，并为每条连接配置 `database.timezone`
- `PostgresPool.open()`：启动连接池并等待最小连接可用
- `PostgresPool.close()`：关闭连接池
- `PostgresPool.connection()`：提供自动归还连接的上下文
- `PostgresPool.transaction()`：提供提交/回滚事务上下文
- `PostgresPool.health_check()`：执行轻量数据库可用性检查
- `init_schema()`：读取并以事务方式执行 `schema.sql`

验收标准：连接池完全由 `DatabaseSettings` 和环境变量驱动，不在源码中
硬编码 DSN；通过连接池获取的 PostgreSQL session 必须显示
`SHOW timezone = Asia/Shanghai`；可完成打开、健康检查、连接借用、事务提交/回滚和关闭；
`init_schema()` 可重复执行；配置缺失、连接失败、SQL 文件缺失或 SQL
执行失败时抛出带安全上下文和原始 cause 的 `DatabaseError` 或
`ConfigurationError`。修改数据库默认 timezone 或 `database.timezone` 后，
必须重启长期运行的 Dashboard、MCP 和 API 进程，使旧连接池释放已有 session。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_repositories.py -v`

##### B4：实现文档、chunk、图片仓储

目标：实现 RAG 核心数据的 repository 写入和读取。

修改文件：`src/storage/repositories.py`、`src/storage/image_storage.py`、`tests/integration/test_repositories.py`

实现类/函数：

- `DocumentRepository.upsert()`：自动创建缺失 collection，并按稳定 ID 写入文档；同一 `collection + source_path` 的 source_hash 变化产生新 ID 时替换旧版本并级联清理旧数据
- `DocumentRepository.get_by_id()`：按 Python 文档 ID 重建 `Document`
- `DocumentRepository.list_by_collection()`：按 collection 查询并稳定排序文档
- `ChunkRepository.upsert_many()`：事务内批量写入 Chunk，计算 `content_hash`，处理稳定 ID 与 `chunk_index` 重排冲突并保持输入顺序
- `ChunkRepository.get_by_id()`：按当前稳定 ID 重建 `Chunk`
- `ChunkRepository.list_by_document()`：按 `chunk_index` 返回文档当前 Chunk
- `ImageIndexRecord`：定义 Dashboard 和多模态响应使用的不可变图片索引记录
- `ImageStorage.save_image()`：安全保存图片到 `data/images/{collection}/`，支持 Unicode collection/image ID 并阻止路径穿越
- `ImageStorage.upsert_index()`：幂等写入 `image_index` 图片索引
- `ImageStorage.find_by_collection()`：按 collection 查询图片索引
- `ImageStorage.find_by_doc_hash()`：按源文档 SHA256 查询图片索引

验收标准：文档、chunk 可写入和读取；缺失 collection 可由首次文档写入自动创建；
同一来源内容变化时新文档 ID 替换旧版本并清理旧 Chunk；Chunk 稳定 ID 在重新
切分后发生位置交换时仍可在单事务内完成 upsert；所有只读 SQL 异常统一转换为
带 operation context 和原始 cause 的 `DatabaseError`；图片文件安全保存到
`data/images/{collection}/`；`image_index` 可幂等写入并按 `collection`
和 `doc_hash` 查询。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_repositories.py -v`

##### B5：实现 Trace 和评估仓储

目标：支持 Trace 索引和评估结果写入 PostgreSQL。

修改文件：`src/storage/schema.sql`、`src/storage/repositories.py`、`tests/integration/test_repositories.py`

实现类/函数：

- `QueryTraceRecord`：以深层不可变结构表示 Query Trace 基础信息、阶段详情、汇总指标和评估指标
- `IngestionTraceRecord`：以深层不可变结构表示 Ingestion Trace 四段式数据
- `EvaluationRunRecord`：保存评估任务状态、配置快照、汇总和错误信息
- `EvaluationResultRecord`：保存单项指标分数及其证据详情
- `TraceRepository.upsert_query_trace()`：自动创建缺失 collection，并按 `trace_id` 幂等写入 Query Trace
- `TraceRepository.upsert_ingestion_trace()`：按 `trace_id` 幂等写入 Ingestion Trace
- `TraceRepository.get_query_trace()`：按 ID 查询 Query Trace
- `TraceRepository.get_ingestion_trace()`：按 ID 查询 Ingestion Trace
- `TraceRepository.list_query_traces()`：按 collection 和开始时间倒序查询 Query Trace 历史
- `TraceRepository.list_ingestion_traces()`：按 collection 和开始时间倒序查询 Ingestion Trace 历史
- `EvaluationRepository.upsert_run()`：自动创建缺失 collection，并按稳定 ID 更新评估任务生命周期
- `EvaluationRepository.upsert_results()`：事务内批量写入指标，按 `run_id + metric_name` 幂等更新并保持输入顺序
- `EvaluationRepository.get_run()`：按稳定 ID 查询评估任务
- `EvaluationRepository.list_runs()`：按 collection 和创建时间倒序查询评估历史
- `EvaluationRepository.list_results()`：按指标名称稳定排序查询任务结果

验收标准：Query/Ingestion Trace 可从 running 状态幂等更新为完成状态，Ingestion 四段式与 Query 五段式
JSON 数据写入后返回深层不可变记录；Trace 历史可按 collection 查询；评估任务
和多个指标结果可写入和查询；同一任务同名指标再次写入时更新稳定结果 ID、
分数和详情，保留原始创建时间；批量结果返回顺序与输入一致；所有只读 SQL
异常统一转换为带 operation context 和原始 cause 的 `DatabaseError`。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_repositories.py -v`

##### B6：实现文档生命周期管理

目标：统一文档状态流转，避免 deleted/failed 文档进入检索。

修改文件：`src/storage/schema.sql`、`src/storage/repositories.py`、`tests/integration/test_repositories.py`

实现类/函数：

- `rag_documents.lifecycle_status`：保存文档生命周期状态
- `mark_processing()`：更新生命周期状态为 `processing`
- `mark_success()`：更新生命周期状态为 `success`
- `mark_failed()`：更新生命周期状态为 `failed`，保留数据用于失败排查和重试
- `mark_deleted()`：更新生命周期状态为 `deleted`，并同步删除该文档下的 chunks 和 `image_index` 记录
- `get_lifecycle_status()`：查询文档当前生命周期状态
- `list_retrievable_by_collection()`：仅返回 `success` 状态文档，供检索可见数据使用

验收标准：`rag_documents` 具备一等生命周期字段和查询索引；文档状态按
`pending -> processing -> success/failed/deleted` 流转；`mark_deleted()`
不物理删除文档记录，但必须删除该文档下的 chunks 和图片索引；deleted/failed
文档不进入后续检索可见数据。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_repositories.py -v`

##### B7：创建 libs 可插拔包结构

目标：创建所有可插拔组件包，为后续接口和工厂提供目录边界。

修改文件：`src/libs/*`、`tests/unit/test_factories.py`

实现类/函数：

- 包初始化文件

验收标准：loader、llm、splitter、transform、embedding、vector_store、reranker、evaluator 包存在。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_factories.py -v`

##### B8：实现 Loader/Splitter 抽象和工厂

目标：实现 Loader 与纯文本 Splitter 的最小接口、工厂和测试实现，并明确 `DocumentChunker` 的业务适配契约。

修改文件：`src/libs/loader/*`、`src/libs/splitter/*`、`src/ingestion/chunk/document_chunker.py`、`tests/unit/test_factories.py`、`tests/unit/test_splitter.py`

实现类/函数：

- `BaseLoader`：定义最小抽象接口
- `LoaderFactory.register_builtin_providers()`：一次性注入 fake/markdown/pdf 内置实现
- `LoaderFactory.create()`：根据配置或 provider 创建实现，内部自动确保内置实现完成注册
- `BaseSplitter.split(text) -> List[str]`：定义输入输出契约
- `SplitterFactory.register_builtin_providers()`：一次性注入 fake/recursive_character 内置实现
- `SplitterFactory.create()`：根据配置或 provider 创建实现，内部自动确保内置实现完成注册
- `DocumentChunker.chunk(document) -> List[Chunk]`：定义输入输出契约

验收标准：可创建 fake/markdown/pdf loader 和 splitter；`libs.splitter` 只接收文本并返回 `List[str]`；`DocumentChunker` 契约测试覆盖 `chunk_id`、metadata 继承、`chunk_index`、`source_path`、图片引用分发，以及 `List[str] -> List[Chunk]` 类型转换。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_factories.py services\ai-service\rag\tests\unit\test_splitter.py -v`

##### B9：实现 LLM/Embedding 抽象和工厂

目标：统一 LLM 与 Embedding 调用接口，支持 fake provider 测试。

修改文件：`src/libs/llm/*`、`src/libs/embedding/*`、`tests/unit/test_factories.py`

实现类/函数：

- `BaseLLM`：定义最小抽象接口
- `LLMFactory.register_builtin_providers()`：一次性注入 fake 内置实现，真实 provider 在 B11 注册
- `LLMFactory.create()`：根据配置或 provider 创建实现，内部自动确保内置实现完成注册
- `BaseEmbedding`：定义最小抽象接口
- `EmbeddingFactory.register_builtin_providers()`：一次性注入 fake 内置实现，真实 provider 在 B11 注册
- `EmbeddingFactory.create()`：根据配置或 provider 创建实现，内部自动确保内置实现完成注册

验收标准：`chat()`、`embed()`、`embed_batch()` 接口统一。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_factories.py -v`

##### B10：实现 VectorStore/Reranker/Evaluator 抽象和工厂

目标：统一向量存储、重排和评估组件的可插拔接口。

修改文件：`src/libs/vector_store/*`、`src/libs/reranker/*`、`src/libs/evaluator/*`、`tests/unit/test_factories.py`

实现类/函数：

- `BaseVectorStore.upsert()`：按 Chunk 与 Dense Vector 的位置关系执行稳定 ID upsert，并按输入顺序返回 chunk_id
- `BaseVectorStore.search()`：执行 Dense Vector 检索和 metadata 精确过滤，返回 `List[RetrievalResult]`
- `BaseVectorStore.get_by_ids()`：按 BM25 返回的 chunk_id 顺序回表，跳过已不存在的 ID
- `FakeVectorStore`：使用固定向量维度、内存索引和余弦相似度提供确定性测试实现，不替代真实 pgvector
- `BaseReranker.rerank()`：对过滤后的 `RetrievalResult` 候选进行统一重排
- `FakeReranker`：根据配置的 chunk_id 顺序提供确定性重排结果
- `NoOpReranker`：保持过滤后的 RRF 候选顺序，用于 none 和 rerank fallback
- `BaseEvaluator.evaluate()`：统一黄金数据集与预测结果的批量评估入口
- `FakeEvaluator`：校验数据集与预测数量一致后返回配置的确定性指标
- `VectorStoreFactory.register_builtin_providers()`：一次性注入 fake 内置实现；pgvector 在 B11 注册
- `RerankerFactory.register_builtin_providers()`：一次性注入 fake、none 和 RRF fallback 内置实现
- `EvaluatorFactory.register_builtin_providers()`：一次性注入 fake 内置实现；custom/Ragas 在阶段 G 注册

验收标准：未知 provider 抛可读错误；Reranker 可根据 `settings.rerank.fallback` 回退到保持候选顺序的安全实现；未实现的真实 provider 不得静默映射到 fake。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_factories.py -v`

##### B11：实现首批真实组件最小适配

目标：接入首批真实 provider 的最小可用实现，并保留 fake 默认测试路径。

修改文件：`src/libs/llm/*`、`src/libs/embedding/openai_embedding.py`、`src/libs/vector_store/pgvector_store.py`、`tests/unit/test_factories.py`、`tests/integration/test_repositories.py`、`tests/external/test_model_providers.py`

实现类/函数：

- `DeepSeekClient.__init__()`：从 `api_key_env` 和 `base_url_env` 解析百炼凭据与 OpenAI-compatible endpoint，支持注入 SDK client 进行单元测试，并将 SDK 初始化失败包装为不暴露凭据的 `ConfigurationError`
- `DeepSeekClient.chat()`：将 `ChatMessage` 转换为 OpenAI-compatible messages，并返回不包含凭据和完整 SDK 对象的 `LLMResponse`
- `OpenAIEmbedding.__init__()`：解析 OpenAI 凭据、模型、超时和固定向量维度，并将 SDK 初始化失败包装为统一配置错误
- `OpenAIEmbedding.embed()`：复用批量接口生成单条百炼 `text-embedding-v4` 向量
- `OpenAIEmbedding.embed_batch()`：单次请求生成批量向量，根据 response index 恢复输入顺序并校验数量与维度
- `PgVectorStore.upsert()`：为 `ChunkRepository` 已持久化的 chunk 原子写入向量，不重复负责 chunk 内容和生命周期持久化
- `PgVectorStore.search()`：使用 pgvector cosine distance 和 JSONB metadata filter 返回 `RetrievalResult`
- `PgVectorStore.get_by_ids()`：按调用方提供的 chunk_id 顺序回表并跳过缺失 ID
- `LLMFactory.register_builtin_providers()`：注册 fake 和 deepseek
- `EmbeddingFactory.register_builtin_providers()`：注册 fake 和 openai
- `VectorStoreFactory.register_builtin_providers()`：注册 fake 和 pgvector

验收标准：单元测试通过注入 SDK client 验证真实 Provider 协议但不访问网络；真实 DeepSeek/OpenAI 调用必须同时带 `external` marker 且仅在 `RUN_RAG_EXTERNAL_TESTS=1` 时运行；PgVectorStore 在真实 PostgreSQL 中通过重复 upsert、cosine search、metadata filter 和顺序回表测试；fake provider 保持默认可测。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_factories.py -v`；`$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_repositories.py -v`；`$env:RUN_RAG_EXTERNAL_TESTS='1'; uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\external\test_model_providers.py -v`

#### 阶段 C：Ingestion & Indexing Pipeline

##### C1：实现文档 SHA256 去重与 skipped 快速结束

目标：在进入 Loader 前判断文档是否变化，未变化直接走 skipped 快速结束，并记录 trace 摘要。

修改文件：`src/ingestion/pipeline.py`、`src/ingestion/__init__.py`、`src/storage/repositories.py`、`tests/unit/test_loader.py`、`tests/integration/test_repositories.py`

实现类/函数：

- `calculate_sha256()`：计算文档稳定哈希标识
- `DocumentRepository.has_successful_source_hash()`：规范化 SHA256 大小写，并按 collection、canonical source_path、source_hash 和 `success` 状态查询可跳过文档
- `should_skip_document()`：根据 repository 命中结果和 force 参数判断文档是否可以跳过处理
- `IngestionPipeline.run()`：在 Loader 前执行去重判断；skipped 时写入完成的 ingestion trace，未命中时才调用 Loader
- `IngestionRunResult`：返回 source、source_hash、status、Document 和 trace 摘要

验收标准：去重范围固定为同一 collection 和 canonical source_path；source_hash 未变更且 lifecycle 为 `success` 时不进入 Loader，不执行 PDF 转换、图片提取、Splitter 和 Transform；`force=true` 时始终继续；skipped 分支写入 `rag_ingestion_traces`，结果包含跳过原因和耗时摘要。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_loader.py -v`；`$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_repositories.py -v`

##### C2：实现文档加载、Markdown 标准化与图片引用提取

目标：将输入文档转换为标准 `Document(id, text, summary, metadata)`；完成 PDF -> Markdown、Markdown 标准化、标题层级 metadata 提取；若文档存在图片，则执行图片提取、生成 `image_id`、写入图片占位符，并填充 `metadata.images[]`。Loader 阶段只保证 `summary` 字段存在且可为空，真正的 LLM 摘要生成由后续独立摘要步骤负责。

修改文件：`pyproject.toml`、`src/ingestion/pdf_to_markdown.py`、`src/libs/loader/markdown_loader.py`、`src/libs/loader/pdf_loader.py`、`src/ingestion/document_summarizer.py`、`config/prompts/document_summary_prompt.yaml`、`tests/unit/test_loader.py`、`tests/unit/test_transformer.py`

实现类/函数：

- `MarkItDownConverter`：将 PDF 转换为 canonical Markdown
- `MarkdownLoader.load()`：加载 Markdown 并提取标题层级与 metadata
- `PdfLoader.load()`：加载 PDF 并输出标准 Document
- `extract_images()`：使用 PyMuPDF 仅在 PDF 存在图片时抽取图片字节、页码与物理位置信息，并根据图片矩形选择最近的后续或前置文本块作为 Markdown 插入锚点
- `DocumentSummarizer.summarize()`：在 Loader 后为 Document 生成顶层摘要，作为后续 chunk rewrite 的全局上下文

验收标准：PDF 使用 MarkItDown 转换为 canonical Markdown，并由独立的 PyMuPDF 图片提取边界补充图片字节、页码、物理位置和邻近文本锚点；同一页面重复出现的 PyMuPDF xref 只解析一次，但保留该 xref 的多个物理位置用于内部定位；PDF 图片占位符必须优先根据按页顺序解析的邻近文本锚点插入对应正文附近，重复锚点使用顺序游标映射到后续页面；锚点不可用或文本块提取异常时必须优雅回退到页标记区间或确定性文末追加，不能导致图片提取或整个 PDF 摄取失败，正常可定位图片不能集中追加到文档末尾；多图片写入中途失败时清理当前临时文件和本次已写文件，不遗留无 Document 对应的孤儿资源；Markdown 可输出标准 `Document(id + text + summary + metadata)` 并提取标题层级，`summary` 是顶层字段且可为 `null`，不得写入 `metadata.summary`；`DocumentSummarizer` 作为 Loader 后的独立步骤生成 `Document.summary`，已有同版本摘要时保持幂等；fenced code block 内的标题和图片示例不得被业务解析器改写；Markdown 本地图片只能读取源文档目录及其子目录，父目录穿越或远程地址保留原语法且不生成 metadata；无图片文档不生成无效图片 metadata；有图片文档生成稳定 `image_id`、`[[image:image_id]]` 占位符和仅包含 `id/path` 的 `metadata.images[]`；转换器和图片提取器支持依赖注入，单元测试不得依赖真实 PDF 解析包。

测试方法：`uv run --project services/ai-service/rag pytest -p no:cacheprovider services\ai-service\rag\tests\unit\test_loader.py -v`；单元测试通过注入 fake MarkItDown converter 和 fake PyMuPDF module 验证转换与图片提取契约，不依赖真实外部解析环境。

##### C3：实现 DocumentChunker、稳定 chunk_id 与引用保留验证

目标：把 `libs.splitter` 输出的 `List[str]` 转换为符合 `core.types` 契约的 `List[Chunk]`，使用独立且稳定的 chunk ID 规则，并验证标题层级、offset 和图片引用不会在业务适配中丢失。

修改文件：`src/libs/loader/markdown_loader.py`、`src/ingestion/chunk/document_chunker.py`、`src/ingestion/chunk/splitter_step.py`、`src/ingestion/chunk/chunk_id.py`、`tests/unit/test_loader.py`、`tests/unit/test_splitter.py`

实现类/函数：

- `DocumentChunker.chunk()`：将 Document 转换为带业务 metadata 的 Chunk 列表
- `build_chunk_id()`：根据 `source_path + section_path + content_hash` 生成稳定 chunk 标识
- `attach_section_path()`：根据标题 offset 将当前标题层级写入 chunk metadata
- `extract_heading_hierarchy()`：为标题层级 metadata 补充源文本 `text_offset`
- `attach_section_path()`：根据标题 offset 将当前标题层级写入 chunk metadata
- `distribute_image_refs()`：扫描 chunk 正文中的 `[[image:image_id]]` 占位符并分发图片引用
- `_merge_image_only_parts()`：将 splitter 产生的纯图片占位符片段合并到相邻正文 chunk，保留源文本顺序和 offset

验收标准：Loader 的每个 heading metadata 包含 canonical `Document.text` 中的起始 offset；同来源、同章节、同内容生成相同 `chunk_id`，来源、章节或内容变化时 ID 发生变化；每个 chunk 都通过独立 `build_chunk_id()` 规则生成 ID；`Chunk.metadata` 只保留 `collection`、`document_id`、`doc_type`、`topic`、`chunk_index`、`section_path` 和可选 `image_refs` 等检索过滤字段，不复制 `images`、`headings`、`source_path`、`source_type`、`source_hash` 或 `title`；`Document.metadata.images[]` 保留完整文档图片清单的 `id/path`；按顺序添加 `chunk_index`；将 `source_path` 写入 chunk metadata；chunk metadata 根据 heading offset 包含当前 chunk 对应的 H2+ `section_path`，H1 文档标题不进入 `section_path`，并通过占位符扫描按需分发 `image_refs`；没有图片的 chunk 不添加无效 `image_refs`；splitter 产生的纯图片占位符 chunk 必须合并到相邻正文，不能作为缺少文本语义的独立检索单元；完成 `List[str] -> List[Chunk]` 类型转换。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_splitter.py -v`

##### C4：实现 MarkdownSectionSplitter

目标：为 Markdown 文档提供结构感知分块策略，优先以 `###` section 作为业务分块单元，章节层级通过 `DocumentChunker` 的 `section_path` metadata 保留，chunk 正文不注入 Markdown 标题行，避免短标题独立成 chunk，并降低长表格被无语义切断的概率。

修改文件：`config/settings.example.yaml`、`src/libs/splitter/markdown_section_splitter.py`、`src/libs/splitter/splitter_factory.py`、`src/ingestion/chunk/document_chunker.py`、`src/ingestion/chunk/splitter_step.py`、`tests/unit/test_splitter.py`、`tests/fixtures/markdown_documents/`

实现类/函数：

- `MarkdownSectionSplitter.split()`：按 Markdown 标题结构生成 section-aware 文本片段
- `MarkdownSectionSplitter.build_sections()`：基于 `#`、`##`、`###` 标题构建 section，标题层级只用于确定切分边界和后续 `section_path` 映射，不直接写入 chunk 正文
- `MarkdownSectionSplitter.merge_short_sections()`：将低于最小长度的相邻 sibling section 合并，避免生成只有标题或语义过薄的 chunk
- `MarkdownSectionSplitter.split_long_section()`：当单个 `###` section 超过 `chunk_size` 时，在该 section 内部继续二次切分，并保持分片可定位到原始 heading offset
- `MarkdownSectionSplitter.split_markdown_table()`：识别 Markdown 表格并按行分组；表格被拆成多个 chunk 时，每个分片都重复表头和分隔行，只有第一段表格可携带表格前正文，后续分片不重复前文
- `DocumentChunker.chunk()`：根据 splitter 输出的文本片段和原文 offset 生成稳定 `Chunk`，并保留 `section_path`、`source_path`、offset 和 `image_refs`；`section_path` 是 chunk metadata 中唯一章节结构字段
- `SplitterFactory.register_builtin_providers()`：注册 `markdown_section` splitter provider，允许通过配置切换 Markdown 分块策略

验收标准：Markdown 文档优先按 `###` 构建 section；每个 section chunk metadata 包含从 `##` 开始的完整 `section_path`，可追溯 H2/H3/H4 层级，且不额外生成 `section`、`h2`、`h3` 或 `h4` metadata 字段；短 section 不得单独形成只有标题或极短正文的 chunk，应与后续同级 section 合并或并入相邻语义块；超过 `chunk_size` 的 `###` section 必须在 section 内部二次切分，后续分片通过 metadata 保留当前 H2+ `section_path`，chunk 正文不得保留非代码块内的 `#`、`##`、`###` 标题行；长表格必须按行分组切分，任意表格分片都必须保留原始表头和分隔行；表格被拆成多个 chunk 时，第一段可以携带表格前正文说明，后续表格分片不得重复携带表格前正文，避免重复 embedding 同一段说明；同一 `###` section 内表格尾部 chunk 与后续“选购建议/建议/总结”块合并后不超过 `chunk_size` 时应合并，避免参数尾行和建议形成两个过薄 chunk；图片占位符 `[[image:image_id]]` 不能因 section 合并或表格切分丢失，`image_refs` 仍按最终 chunk 正文分发；`libs.splitter` 仍保持纯文本切分职责，不直接创建业务 `Chunk`；PDF 转 Markdown 后仍可复用该策略，无法识别标题结构时优雅回退到递归字符切分。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_splitter.py -v`

##### C5：实现 Transform 抽象基类与具体实现

目标：集中实现 Transform 阶段的抽象契约、具体能力和 ingestion 串行编排，包括 metadata 注入、LLM chunk rewrite、智能合并和去噪；Transform 不使用 factory/provider 模式，摄取流水线必须根据 `settings.transform.steps` 按顺序执行 enabled step。

修改文件：`.gitignore`、`README.md`、`config/settings.example.yaml`、`config/prompts/rewrite_chunk_prompt.yaml`、`src/core/config.py`、`src/libs/transform/base_transform.py`、`src/ingestion/transform/transformer.py`、`src/ingestion/transform/metadata_enricher.py`、`src/ingestion/transform/chunk_rewriter.py`、`src/ingestion/transform/semantic_merge_transform.py`、`src/ingestion/transform/denoise_transform.py`、`tests/fixtures/noisy_documents/`、`tests/unit/test_config.py`、`tests/unit/test_transformer.py`

实现类/函数：

- `BaseTransform.transform()`：定义 Transform 最小抽象契约
- `TransformPipeline.from_settings()`：从 `settings.transform.steps` 构建 enabled step 链路
- `TransformPipeline.run()`：按配置顺序串行执行 Transform
- `MetadataEnricher.transform()`：注入标题路径、来源、文档主题等上下文 metadata
- `ChunkRewriter.transform()`：读取 `document_summary` 作为全局上下文，利用 LLM 重写 chunk，使片段语义更完整；改写前将 chunk 拆分为文本节点和图片占位符节点，只把文本节点与文档摘要发送给 LLM，完成后按原节点顺序重组图片占位符；不发送 `Chunk.metadata`、`image_refs` 或图片节点；必须解析 JSON schema 或清理 Markdown 分段回复，只把正文写入 `Chunk.text`，不得把 metadata、image_refs、Prompt 标签或代码块写入 chunk content；仅包含图片占位符的 chunk 必须跳过文本 LLM rewrite 并保留给后续 Image-to-Text；普通文本节点的合法 JSON 响应缺少非空 `text` 时必须作为 provider 无效响应失败，不得把原始 JSON 写入正文
- `SemanticMergeTransform.transform()`：合并逻辑相关但被物理切开的相邻 chunk
- `DenoiseTransform.transform()`：清理空白、页眉页脚、目录和解析残留噪声

验收标准：运行时 `config/settings.yaml` 被 Git 忽略，仓库提交 `config/settings.example.yaml` 作为完整模板；`ingestion.document_summary.llm_provider` 显式配置为 `deepseek`，运行时摘要步骤必须按该 provider 构建 LLM；`settings.transform.steps` 只描述步骤顺序、启用状态和 prompt_path，不包含 provider；`src.libs.transform` 只暴露 `BaseTransform`；具体 Transform 位于 `src/ingestion/transform/`；chunk 包含标题、来源、主题上下文；`ChunkRewriter` 必须接收 `document_summary` 并只把它作为语义背景，不得凭摘要补造 chunk 中不存在的事实；`ChunkRewriter` 不得把 `Chunk.metadata`、`image_refs` 或图片占位符节点发送给大模型，metadata/image_refs 只能在 Python 对象层面继承和维护；含正文和图片的 chunk 必须分别改写各文本节点并按原顺序恢复每个图片占位符，图片不得被删除、复制或移动；仅包含图片占位符的 chunk 跳过文本 rewrite；fake LLM 下可 rewrite；LLM 返回 JSON 或 Markdown 分段时，最终 `Chunk.text` 只能包含可检索正文和原有图片占位符，metadata 和 image_refs 只能保留在 `Chunk.metadata`；普通文本节点的合法 JSON `text` 为空或缺失时摄取必须失败，不得把 `{ "text": ... }` JSON 结构作为 chunk 正文写入；`rewrite`、`semantic_merge` 和 `denoise` 的执行详情只进入 ingestion trace 的 `transform.sub_stages`，不得写入 chunk metadata；逻辑相关 chunk 可合并且 metadata 不丢失；页眉页脚、目录和解析残留可清理。

补充要求：执行该任务时必须在 `settings.example.yaml` 和本地 `settings.yaml` 中配置真实启用的 Transform steps 链路，测试不能只依赖 fake transform；需要创建典型噪声场景 fixture，例如连续空白、页眉页脚、重复目录、页码水印、PDF 解析断行、无意义符号残留和图片占位符附近噪声。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_transformer.py -v`

##### C6：实现 ImageCaptioner

目标：当 `vision_llm.enabled=true` 且 chunk metadata 中存在 `image_refs` 时，为关联图片生成 caption，并将 chunk 正文中的 `[[image:image_id]]` 替换为 `[[image_caption:image_id]] + caption`；未启用 Vision LLM 或没有 `image_refs` 时必须安全跳过。

修改文件：`src/libs/llm/base_vision_llm.py`、`src/libs/llm/dashscope_vision_llm.py`、`src/ingestion/transform/image_captioner.py`、`config/settings.example.yaml`、`config/prompts/image_caption_prompt.yaml`、`tests/unit/test_transformer.py`

实现类/函数：

- `BaseVisionLLM.caption_image()`：定义 Vision LLM 图片 caption 的最小统一接口
- `DashScopeVisionLLM.caption_image()`：调用百炼 Qwen-VL-Max 生成图片 caption
- `ImageCaptioner.caption()`：读取 chunk 的 `image_refs` 并生成图片描述，同时把原始 caption 写入运行时 `image_caption_artifacts`
- `ImageCaptioner.should_caption()`：判断是否满足 `vision_llm.enabled=true` 且存在 `image_refs`
- `ImageCaptioner.replace_placeholder()`：把原始图片占位符替换为 caption 节点并保留 `image_refs`
- `ImageCaptioner.trace_details()`：输出 image_captioner 的执行状态、provider、model、图片数量、caption 数量和失败原因，供 Transform sub_stage 使用

验收标准：启用 `vision_llm` 且存在 `image_refs` 时会生成 caption 并替换 chunk 正文中的图片占位符；替换后的文本包含 `[[image_caption:image_id]]` 和可检索 caption，原相对位置保持不变；ImageCaptioner 不向 Vision LLM 传递完整 `document_context`；生成成功、低质量或失败结果时都要把结构化结果写入运行时 `image_caption_artifacts`，至少包含 `image_id`、`caption`、`status`、`provider`、`model`、`reason` 和 `source_chunk_ids`；未启用 `vision_llm` 时不调用 Vision LLM，并通过 trace 记录 skipped；没有 `image_refs` 的 chunk 不生成 caption；同一轮摄取中相同 `image_id` 只调用一次 Vision LLM，并在所有引用该图片的 chunk 中复用结果；Vision LLM 失败或低质量时保留原图片占位符并通过 trace 记录 failed/low_quality、provider、model、底层错误类型和经过脱敏/截断的错误信息；chunk metadata 不写入 `image_captions`、`image_caption_status` 或 provider/model；caption 文本可被后续 DenseEncoder 和 BM25Indexer 使用。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_transformer.py -v`

##### C7：实现 DenseEncoder

目标：将默认 embedding 模型适配、chunk `content_hash` 差量判断和 Dense 向量生成统一收敛到 `DenseEncoder`。

修改文件：`src/libs/embedding/openai_embedding.py`、`src/ingestion/embedding/dense_encoder.py`、`src/ingestion/embedding/embedding_step.py`、`tests/unit/test_embedding.py`

实现类/函数：

- `OpenAIEmbedding.embed()`：通过百炼 OpenAI 兼容接口调用 `text-embedding-v4` 生成单条文本向量
- `DenseEncoder.should_encode()`：基于 chunk `content_hash` 判断是否需要重新生成 Dense 向量
- `DenseEncoder.encode()`：生成单个 chunk 的 Dense 语义向量
- `EmbeddingStep.run_dense()`：编排 DenseEncoder 并输出待写入向量结果

验收标准：fake 默认可测，真实调用 marker 隔离；已存在 content_hash 不重复调用模型；新 chunk 可以生成 Dense 向量；DenseEncoder 不承担批处理职责。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_embedding.py -v`

##### C8：实现 BM25Indexer

目标：为 Sparse Route 构建 BM25 词项、词频和倒排索引数据。

修改文件：`src/ingestion/embedding/bm25_indexer.py`、`tests/unit/test_bm25.py`

实现类/函数：

- `BM25Indexer.index()`：生成 BM25 词项、词频和倒排索引数据
- `BM25Indexer.query()`：根据关键词返回候选 `chunk_id` 和 BM25 分数

验收标准：可为 chunk 构建 BM25 索引；可按关键词召回候选 chunk；索引结果可被 Sparse Route 复用。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_bm25.py -v`

##### C9：实现 BatchProcessor 批处理优化

目标：在 DenseEncoder 和 BM25Indexer 均完成后，提供统一批处理能力，处理批量输入、限流、重试和失败隔离。

修改文件：`src/ingestion/embedding/batch_processor.py`、`src/ingestion/embedding/embedding_step.py`、`tests/unit/test_embedding.py`、`tests/unit/test_bm25.py`

实现类/函数：

- `BatchProcessor.run()`：按配置批量执行编码或索引任务
- `BatchProcessor.retry_failed()`：对可重试失败执行有限重试
- `DenseEncoder.encode_batch()`：通过 `EmbeddingClient.embed_batch()` 批量生成 Dense 向量并保持顺序
- `EmbeddingStep.run_batch()`：编排 DenseEncoder 与 BM25Indexer 的批处理执行

验收标准：批处理大小受配置控制；Dense 和 BM25 两路都能复用 BatchProcessor；部分失败不影响其他 chunk；重试次数和失败记录可测试。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_embedding.py services\ai-service\rag\tests\unit\test_bm25.py -v`

##### C10：实现统一 upsert

目标：将文档、chunk、向量、BM25 和图片索引一致写入，并保证 upsert 幂等性和批量顺序。

修改文件：`src/storage/schema.sql`、`src/storage/bm25_storage.py`、`src/storage/repositories.py`、`src/libs/vector_store/pgvector_store.py`、`src/ingestion/storage/upsert_step.py`、`src/storage/image_storage.py`、`tests/integration/test_ingestion_pipeline.py`、`tests/integration/test_repositories.py`

实现类/函数：

- `UpsertStep.run()`：校验完整索引快照，并在一个 PostgreSQL 事务内统一写入 document、chunk、向量、BM25 和图片索引；图片 caption 优先读取 `image_caption_artifacts`
- `BM25Storage.upsert_index()`：按 document 替换完整 BM25 posting 快照
- `DocumentRepository.upsert_in_transaction()`：复用调用方事务写入 document
- `ChunkRepository.upsert_many_in_transaction()`：复用调用方事务替换完整 chunk 快照
- `PgVectorStore.upsert_in_transaction()`：复用调用方事务写入 Dense 向量
- `ImageStorage.image_path()`：安全解析 `data/images/{collection}/` 下的受管图片路径
- `ImageStorage.upsert_index_in_transaction()`：复用调用方事务写入图片索引

验收标准：同一完整快照重复 upsert 不产生重复记录且返回相同有序 ID；Transform 基于新 content_hash 生成新 chunk_id 后，统一 upsert 清理旧 chunk 及其 BM25 posting；支持批量 upsert 且返回结果保持输入顺序；文档、chunk、向量、BM25 和 `image_index` 在同一个 PostgreSQL 事务内一致写入；`image_index.metadata.caption` 优先使用 `image_caption_artifacts` 中的原始 caption，即使最终 chunk 正文被 rewrite 移除 `[[image_caption:...]]` 节点也必须保留 caption；没有 artifacts 时兼容解析最终 chunk 正文；向量或数据库写入失败时所有数据库记录回滚，事务前被替换的受管图片恢复原内容。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_ingestion_pipeline.py -v`

##### C11：实现统一 Pipeline MVP 编排和集成测试

目标：在统一 `pipeline.py` 中把摄取结果、ImageCaptioner、DenseEncoder、BM25Indexer、BatchProcessor 和 upsert 串成最小可运行链路。

修改文件：`src/ingestion/pipeline.py`、`src/ingestion/embedding/embedding_step.py`、`src/storage/repositories.py`、`tests/unit/test_embedding.py`、`tests/integration/test_ingestion_pipeline.py`

实现类/函数：

- `IngestionPipeline.run_indexing()`：编排索引 MVP 子链路，并把 transform runtime artifacts 显式传递给统一 upsert
- `IngestionPipeline.run()`：串联摄取与索引主链路
- `IngestionPipelineResult`：定义统一摄取与索引流程返回结果
- `ChunkRepository.get_dense_vectors_by_content_hashes()`：读取同一 collection 中成功文档的可复用 Dense 向量
- `EmbeddingStep.run_batch()`：复用已有 content_hash 向量，避免重复模型调用并恢复每个 chunk 的有序 Dense 结果

验收标准：给定原始文档路径，可以完成去重、Loader、Splitter、包含 ImageCaptioner 条件 caption 的 Transform Pipeline、DenseEncoder 编码、BM25Indexer 索引、BatchProcessor 批处理、统一 upsert 和 lifecycle success；Transform runtime context 中的 `image_caption_artifacts` 必须从 ImageCaptioner 传递到 `run_indexing()` 和 `UpsertStep.run()`；同一路径同内容重复执行时命中 successful source hash 并直接 skipped，不重复调用 Loader、Embedding 或 upsert；文档局部变化时，数据库中成功文档已有的 content_hash 必须复用 Dense 向量，仅对新增或变化内容调用 embedding；当前批次重复内容只调用一次模型，但仍为每个 chunk 返回独立且有序的 Dense 结果；Loader-only 模式保持 C1 兼容；部分后置组件配置必须启动失败；Splitter/Transform 产生空 chunk 时不得写入成功文档。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_ingestion_pipeline.py -v`

##### C12：实现 ingest.py 摄取脚本入口

目标：提供本地命令行入口，调用 Ingestion Pipeline 执行离线文档摄取。

修改文件：`src/scripts/__init__.py`、`src/scripts/ingest.py`、`tests/unit/test_loader.py`、`pyproject.toml`、`uv.lock`、`.gitignore`

实现类/函数：

- `parse_args()`：解析命令行参数
- `run_ingest_cli()`：执行本地摄取流程
- `_discover_sources()`：递归发现并按路径排序 Markdown/PDF 文件
- `_build_pipeline()`：通过配置、Factory、Repository 和 Storage 组装完整 Pipeline
- `_load_local_environment()`：从当前工作目录向上发现 `.env`，仅补充未由 Shell、CI 或 Docker 注入的环境变量
- `_resolve_runtime_path()`：将配置中的相对运行时路径稳定解析到 RAG 模块根目录
- `main()`：提供 `python -m src.scripts.ingest` 模块入口

验收标准：`--path` 必填并支持单个 `.md`、`.markdown`、`.pdf` 文件或递归目录；`--collection` 可覆盖 collection，未提供时读取 `project.default_collection`；`--force` 原样传入 Pipeline 并绕过 SHA256 skipped 快速结束；本地执行时从当前目录向上发现最近的 `.env`，但不得覆盖 Shell、CI 或 Docker 已注入的变量；`settings.yaml` 中的运行时相对路径必须基于 RAG 模块根目录解析，不得受启动目录影响；目录只处理支持的文档类型并按绝对路径稳定排序；无支持文件时返回退出码 2 和可读错误；运行失败时返回退出码 1 并始终关闭 PostgreSQL pool；全部成功时输出包含 collection、force、processed 和逐文件状态的 JSON。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_loader.py -v`
#### 阶段 D：Retrieval

##### D1：实现 query 预处理

目标：完成 query 标准化、意图识别和可选 rewrite。

修改文件：`src/core/query_engine/__init__.py`、`src/core/query_engine/query_processor.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `QueryIntent`：定义知识查询、推荐、对比和动态商品查询意图
- `QueryRewriter.rewrite()`：定义可注入的最小 query rewrite 接口
- `ProcessedQuery`：定义 Query Processor 向后续检索阶段传递的不可变标准对象
- `QueryProcessor.process()`：标准化输入、解析默认参数、识别意图、执行可选 rewrite 并提取关键词

验收标准：支持 Unicode NFKC 和空白 normalize；拒绝空 query、非字符串或空 collection、非整数或非正整数 top_k；collection 和 top_k 默认读取 settings 且允许调用方覆盖；输出不可变的有序去重 keywords 快照；识别 `knowledge_query`、`recommendation`、`comparison` 和 `product_lookup`；标记是否需要商品 API 工具协同；配置关闭或未注入 rewriter 时不调用 rewrite；rewrite 成功后使用重写 query 生成 keywords；rewrite 异常或空结果时回退标准化原 query 并记录稳定原因。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D2：实现 Dense Route 向量检索

目标：输入用户 query 或 `ProcessedQuery`，完成 Query Embedding、pgvector 向量检索，并返回统一的 `RetrievalResult(chunk_id,text,score,metadata)`。

修改文件：`src/core/query_engine/__init__.py`、`src/core/query_engine/dense_route.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `DenseTraceContext.record_stage()`：定义 Dense Route 使用的最小 Trace 注入接口
- `DenseRoute.search()`：处理 raw query 或 ProcessedQuery，执行 Query Embedding 和 VectorStore 语义召回

验收标准：raw query 必须先通过 QueryProcessor，ProcessedQuery 可直接复用；调用 `EmbeddingClient.embed(processed_query.normalized_query)`；默认使用 `retrieval.dense_top_k` 作为 Dense 粗召回数量，并允许调用方显式覆盖；调用 VectorStore 完成 Top-k 向量检索，但不在 D2 提前执行 D6 的 metadata 过滤；所有候选统一校验为 `RetrievalResult(chunk_id,text,score,metadata)`；空 query、非法 top_k、embedding 失败、vector search 失败和空结果都有可测试分支；可选 Trace 记录 `route=dense`、provider-independent `method=vector_search`、top_k、候选数量、状态和耗时；Trace sink 异常不得覆盖检索结果或原始 RetrievalError。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D3：实现 Sparse Route BM25 回表检索

目标：使用 `QueryProcessor.process()` 生成的 `ProcessedQuery.keywords` 进行 BM25 检索，再通过 chunk_id 回表读取 chunk 正文和 metadata。

修改文件：`src/core/query_engine/__init__.py`、`src/core/query_engine/sparse_route.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `BM25CandidateLike`：定义 Sparse Route 消费的 BM25 候选最小协议
- `BM25IndexerLike.query()`：定义 Sparse Route 依赖的 BM25 查询协议
- `SparseTraceContext.record_stage()`：定义 Sparse Route 使用的最小 Trace 注入接口
- `SparseRoute.search()`：处理 raw query 或 ProcessedQuery，执行 BM25 关键词召回并按 chunk_id 回表
- `BM25Indexer.query()`：执行索引查询
- `VectorStore.get_by_ids()`：按 ID 回表读取数据

验收标准：流程固定为 `keywords -> bm25_indexer.query(keywords, top_k) -> [{chunk_id, score}] -> vector_store.get_by_ids(chunk_ids) -> [{id, text, metadata}] -> List[RetrievalResult]`；raw query 必须先通过 QueryProcessor，ProcessedQuery 可直接复用；默认使用 `retrieval.sparse_top_k` 并允许调用方显式覆盖；keywords 为空时返回空结果并记录 skipped 原因；BM25 返回的 chunk_id 顺序和 BM25 原始分数应被保留；BM25 无候选时不执行回表；缺失 chunk_id 应被跳过并写入 trace details；可选 Trace 记录 `route=sparse`、`method=bm25`、top_k、keyword_count、候选数量、缺失 chunk_id、状态和耗时；Trace sink 异常不得覆盖检索结果或原始 RetrievalError。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D4：实现 RRF Fusion

目标：融合 Dense/BM25 两路候选，避免直接比较不同分数。

修改文件：`src/core/query_engine/__init__.py`、`src/core/query_engine/fusion.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `_FusionCandidate`：累计同一 chunk 在 Dense/Sparse 两路中的排名、原始分数和输出 payload
- `reciprocal_rank_fusion()`：按排名倒数融合 Dense/BM25 候选
- `_validate_positive_integer()`：校验 top_k 和 rrf_k 参数
- `_add_route_contributions()`：将单一路线的首个 chunk 命中贡献写入 RRF 累计器
- `_to_retrieval_result()`：将融合状态转换为 `RetrievalResult`

验收标准：基于排名倒数融合，不比较 Dense/BM25 原始分数；同一路线内重复 chunk 只使用首次出现的排名贡献；跨路线相同 chunk 合并为一个结果；输出 `RetrievalResult.score` 为 RRF 分数；输出 metadata 中包含 `fusion.dense_rank`、`fusion.sparse_rank`、`fusion.dense_score`、`fusion.sparse_score` 和 `fusion.sources`，供 HybridSearch、Trace 和 Dashboard 使用；支持 top_k 截断；空输入返回空列表；非法 top_k 或 rrf_k 抛出 `RetrievalError`。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D5：实现 HybridSearch 编排

目标：编排 Dense Route、Sparse Route 和 RRF Fusion，完成候选去重、双路召回融合和单路失败降级。

修改文件：`src/core/query_engine/__init__.py`、`src/core/query_engine/hybrid_engine.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `HybridTraceContext.record_stage()`：定义 HybridSearch 使用的最小 Trace 注入接口
- `HybridSearchResult`：定义流程返回结果
- `HybridSearch.search()`：编排双路召回、候选去重和 RRF 融合
- `HybridSearch._record_trace()`：记录 hybrid 阶段候选数量、失败路线和降级原因

验收标准：前置依赖为 D1、D2、D3、D4；输入 `ProcessedQuery`；分别执行 Dense/BM25 两路检索；调用 RRF Fusion 生成融合排序，按 `chunk_id` 去重并保留 `dense_rank`、`sparse_rank`、`dense_score`、`sparse_score`；使用 `retrieval.fusion_top_k` 和 `retrieval.rrf_k`；返回结果必须同时保留 dense 原始候选、sparse 原始候选、融合候选、fallback 状态和 fallback 原因；单路失败时允许降级为另一条路线并写入 trace details；双路均失败时抛出 `RetrievalError`；Trace sink 异常不得覆盖检索结果或原始 RetrievalError。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D6：实现 Rerank 前候选过滤

目标：在 RRF Fusion 之后、Reranker 之前，根据调用参数过滤候选，避免把不符合限定条件的 chunk 送入重排阶段。

修改文件：`src/core/query_engine/__init__.py`、`src/core/query_engine/hybrid_engine.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `CandidateFilterReport`：记录过滤后结果、过滤前后数量、过滤原因统计和被过滤 chunk_id
- `CandidateFilter.apply()`：按参数过滤候选结果
- `CandidateFilter._first_rejection_reason()`：返回候选被过滤的首个原因
- `HybridSearch.apply_metadata_filter()`：在进入 rerank 前执行 metadata 过滤
- `HybridSearch._record_filter_trace()`：记录 filter 阶段过滤参数、数量变化和过滤原因
- `_matches_filter()`：执行 metadata 精确匹配或多值匹配
- `_has_permission()` / `_has_all_permissions()`：执行权限过滤

验收标准：支持 `collection`、`doc_type`、`source_type`、`document_status`、`lifecycle_status`、`permission`、`permissions`、`include_deleted` 参数；默认排除 `lifecycle_status=deleted` 的候选，除非显式设置布尔值 `include_deleted=true`；`include_deleted` 必须是 boolean，不能用字符串隐式转换；过滤发生在 RRF Fusion 之后、Rerank 之前；`HybridSearch.search(filters=...)` 和 `HybridSearch.apply_metadata_filter()` 复用同一过滤逻辑，供后续 `--collection` 等脚本参数调用；过滤后保持原有候选顺序；过滤结果数量和过滤原因写入 trace details；未知过滤键必须抛出 `RetrievalError`，避免静默忽略调用方输入；Trace sink 异常不得覆盖过滤结果或原始 RetrievalError。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D7：实现 Cross-Encoder Reranker

目标：支持 Cross-Encoder 对过滤后的候选进行精排。

修改文件：`src/libs/reranker/__init__.py`、`src/libs/reranker/cross_encoder_reranker.py`、`src/libs/reranker/reranker_factory.py`、`tests/unit/test_reranker.py`

实现类/函数：

- `CrossEncoderScorer.predict()`：定义 Cross-Encoder scorer 最小协议，便于测试注入和真实模型适配
- `CrossEncoderReranker.rerank()`：执行候选重排
- `CrossEncoderReranker._get_scorer()`：惰性加载 `sentence_transformers.CrossEncoder`
- `CrossEncoderReranker._validate_scores()`：校验模型分数数量和有限性
- `CrossEncoderReranker._with_rerank_score()`：复制候选并写入 rerank 分数和 metadata 诊断
- `RerankerFactory.register_builtin_providers()`：注册 `cross_encoder` provider

验收标准：只接收过滤后的候选；按 `(query, candidate.text)` 组成 query-doc pair 调用 Cross-Encoder scorer；按模型分数降序重排，分数相同保持过滤后的原始顺序；支持 `top_k` 截断；返回新的 `RetrievalResult` 副本，不修改输入候选；输出 `RetrievalResult.score` 为 Cross-Encoder 分数，metadata 写入 `rerank.provider`、`rerank.model` 和 `rerank.original_score`；空候选直接返回空列表且不加载模型；query 为空、top_k 非法、模型输出数量不一致、分数非有限或 scorer 运行失败均有可测试分支；真实模型通过 `sentence_transformers.CrossEncoder` 惰性加载，单元测试必须通过注入 scorer 避免网络或模型下载。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_reranker.py -v`

##### D8：实现 LLM Rerank

目标：支持 LLM 对过滤后的候选进行重排。

修改文件：`src/libs/reranker/llm_reranker.py`、`src/libs/reranker/reranker_factory.py`、`src/libs/reranker/__init__.py`、`tests/unit/test_reranker.py`、`tests/unit/test_factories.py`

实现类/函数：

- `LLMReranker.rerank()`：调用注入的 `BaseLLM` 对过滤后的候选执行 Prompt 驱动重排
- `LLMReranker._build_messages()`：渲染英文 rerank Prompt，并以稳定 `candidate_id` 序列化候选
- `LLMReranker._parse_ranking()`：解析并校验 LLM JSON array 输出，拒绝未知 ID、重复 ID 和非法 score
- `rerank_prompt.yaml`：要求模型只返回严格 JSON object array；禁止 Markdown fence、解释文字、ID-only array 和 JSON 前后附加内容
- `LLMReranker._apply_ranking()`：按 LLM 排序返回 `RetrievalResult` 副本，未返回候选按过滤后原顺序追加
- `RerankerFactory.register_builtin_providers()`：注册 `llm` provider

验收标准：只接收过滤后的候选；通过 `BaseLLM` 注入 fake LLM，不访问外部 API；可按 LLM 返回的 `candidate_id` 稳定排序并支持 `top_k`；返回新的 `RetrievalResult` 副本，不修改输入候选；LLM 返回 score 时写入 `RetrievalResult.score`，metadata 写入 `rerank.provider`、`rerank.model`、`rerank.llm_provider`、`rerank.original_score` 和可选 reason；LLM 未返回的候选按过滤后的原始顺序追加；空候选直接返回空列表且不调用 LLM；query 为空、top_k 非法、JSON 非法、未知候选 ID、重复候选 ID、非法 score 均有可测试分支；`RerankerFactory.create(provider="llm", llm_client=fake)` 可显式创建 LLM reranker；settings 默认 `llm` 但未注入 `llm_client` 时按 `settings.rerank.fallback` 回退到 RRF，避免 settings-only 本地启动阶段直接失败。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_reranker.py -v`

##### D9：实现 rerank fallback

目标：在 reranker 不可用、超时或异常时回退到过滤后的 RRF 结果。

修改文件：`src/core/query_engine/reranker.py`、`src/core/query_engine/__init__.py`、`tests/unit/test_reranker.py`

实现类/函数：

- `RerankController.rerank_or_fallback()`：调用配置的 Reranker，并在不可用、超时、异常或非法输出时回退过滤后的 RRF 结果
- `RerankController.rerank_with_outcome()`：返回最终候选和显式 fallback 状态，避免从 provider metadata 推断控制流
- `RerankOutcome`：封装 rerank 结果、fallback_used 和 fallback_reason
- `RerankController._validate_provider_results()`：校验 provider 结果数量符合 `min(filtered_count, top_k)`、只包含过滤后候选且不存在重复 ID
- `RerankController._is_timeout_error()`：识别直接 TimeoutError 和 ProviderError/SDK 异常链中包装的 timeout
- `RerankController._fallback()`：从 provider 调用前保存的候选副本恢复过滤后 RRF 顺序
- `RerankController._record_trace()`：记录 rerank 前后排名、fallback 原因、错误类型和耗时
- `RerankTraceContext.record_stage()`：定义 rerank 阶段使用的最小 trace 注入接口

验收标准：默认使用 `settings.rerank.top_k`，并支持调用方覆盖正整数 `top_k`；reranker 成功时返回 provider 排序的 `RetrievalResult` 防御性副本，输出数量必须等于 `min(filtered_count, top_k)`，避免排序组件意外过滤或清空召回结果；`rerank_with_outcome()` 必须显式返回 fallback_used 和 fallback_reason，业务编排层不得通过 `RetrievalResult.metadata` 判断 rerank 是否降级；reranker 为 `None`、抛出直接 `TimeoutError`、在 `ProviderError`/SDK 异常链中包装 timeout、Provider 异常或普通异常时，回退到调用前保存的过滤后 RRF 候选顺序；provider 调用使用独立候选深拷贝，即使 provider 修改输入后失败也不能污染 fallback 结果；provider 返回未知 chunk_id、重复 ID、数量不符或非法 `RetrievalResult` 时视为非法输出并 fallback；任何路径都不能重新引入已被 metadata filter 排除的候选；空候选直接返回空列表；query 为空或 top_k 非法时 fail fast，不被 fallback 隐藏；可选 trace 记录 before/after order、fallback_used、fallback_reason、error_type、项目异常的 trace-safe context 和耗时，不记录任意第三方异常原文，trace sink 异常不得替换查询结果。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_reranker.py -v`

##### D10：实现引用构造

目标：为最终上下文构建可展示的引用来源。

修改文件：`src/core/types.py`、`src/core/response/__init__.py`、`src/core/response/citation_builder.py`、`src/core/query_engine/sparse_route.py`、`src/libs/vector_store/fake_vector_store.py`、`src/libs/vector_store/pgvector_store.py`、`tests/unit/test_response_builder.py`、`tests/unit/test_retrieval.py`、`tests/unit/test_factories.py`、`tests/integration/test_repositories.py`

实现类/函数：

- `Citation`：定义 document_id、chunk_id、title、section_path、source_uri、score、trace_id 的不可变共享契约
- `CitationBuilder.build()`：按最终候选排序批量构建 citation
- `CitationBuilder._build_one()`：从单个 `RetrievalResult` 的顶层 metadata 构建来源
- `CitationBuilder._first_present()`：按稳定别名读取顶层 metadata 来源字段
- `CitationBuilder._title_from_source_uri()`：仅基于真实 source_uri 文件名生成缺省展示标题
- `CitationBuilder._normalize_section_path()`：将字符串或有序字符串列表归一化为不可变章节路径
- `SparseRoute._to_retrieval_results()`：将回表 Chunk.metadata 深拷贝到 Sparse RetrievalResult metadata
- `FakeVectorStore.search()`：在测试 Dense 结果中传播 metadata 来源字段，保持与生产实现一致
- `PgVectorStore.search()`：读取 PostgreSQL metadata 并注入 Dense RetrievalResult metadata

验收标准：输入最终排序后的 `Sequence[RetrievalResult]` 和非空 query trace_id，输出顺序一致的 `List[Citation]`；每条 citation 包含 document_id、chunk_id、来源标题、section_path、source_uri、最终 score 和 trace_id，并可通过 `model_dump(mode="json")` 直接得到 JSON array 形式的 section_path；Dense pgvector/Fake search 和 Sparse 回表都必须把 Chunk.metadata 深拷贝到 RetrievalResult metadata，确保真实检索链路不丢失 `document_id`、`source_path` 和 `section_path`；标题缺失时只允许从已验证 source_uri 文件名生成展示标题，对 URL 百分号编码执行解码，禁止从 chunk 正文猜测；document_id、title、source_uri 必须是真实非空字符串，缺少来源、脏结构化类型、章节结构非法或 trace_id 为空时 fail fast，避免生成不可验证 citation；构造过程不修改 retrieval metadata；空结果返回空列表。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_response_builder.py -v`

##### D11：实现多模态响应组装

目标：把最终排序 chunk 转换为可直接交给 MCP、AImodel、CLI 和 Dashboard 的
公开知识响应；响应包含 Agent-ready final context、D10 引用、命中图片和 trace_id，但不
暴露 Dense/Sparse 中间结果、向量、Provider payload、过滤报告或内部 tool JSON。

修改文件：`config/prompts/evidence_context_prompt.yaml`、`src/core/response/__init__.py`、
`src/core/response/evidence_context_optimizer.py`、`src/core/response/multimodal_assembler.py`、
`src/core/response/response_builder.py`、`src/storage/image_storage.py`、`tests/unit/test_response_builder.py`、
`tests/integration/test_repositories.py`

实现类/函数：

- `ResponseImage`：定义只包含公开图片字段的不可变响应对象
- `ImageResolver.find_by_ids()`：定义 Response 层所依赖的最小图片批量查询接口
- `MultimodalAssembler.assemble()`：按最终排名收集、去重并解析 chunk 关联图片
- `MultimodalAssembler._collect_references()`：验证 image_refs 契约并聚合关联 chunk IDs
- `MultimodalAssembler._to_response_image()`：隔离内部索引 metadata，只投影公开图片字段
- `EvidenceContextOptimizer.optimize()`：调用 LLM 将编号证据块整理为 Agent-ready final context
- `KnowledgeHubResponse`：定义 content、citations、images、trace_id 和 is_empty 公共响应
- `KnowledgeHubResponseBuilder.build()`：组合格式化上下文、引用和多模态内容
- `KnowledgeHubResponseBuilder._format_content()`：将排序 chunk 文本格式化为编号上下文
- `ImageStorage.find_by_ids()`：单次 PostgreSQL 查询读取命中图片索引

验收标准：输入最终排序后的 `Sequence[RetrievalResult]` 和非空 trace_id，输出
不可变 `KnowledgeHubResponse`；`content` 是 AImodel 可直接使用的最终上下文，
由按 `[1]`、`[2]` 排名编号的原始证据块优化得到；优化结果必须保留证据编号，
不得生成最终答案、不得编造价格/库存/链接，且不包含 retrieval metadata；优化器不可用、
Provider 失败或返回空内容时按配置 fallback 到原始编号证据块；citations 复用 D10 的 grounded citation；
图片引用从 `metadata.image_refs` 读取，必须是非空字符串列表，跨 chunk 去重并
保持首次引用顺序，同一图片记录所有关联 chunk IDs；图片索引采用一次批量查询，
解析结果不依赖数据库返回顺序；缺失图片索引安全跳过且不影响文本响应；公开图片
只包含 image_id、managed file_path、mime_type、page、尺寸、caption、quality_status
和 chunk_ids，不泄漏 image hash、原始 extraction path、Provider payload 或任意扩展
metadata；空候选返回 `ok=true`、`is_empty=true`、空 content/citations/images，且不
访问图片存储；构造过程不修改 RetrievalResult。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_response_builder.py -v`；使用 Docker PostgreSQL 设置 `DATABASE_URL` 后执行 `uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_repositories.py::test_image_storage_saves_files_and_queries_upserted_indexes -v`

##### D12：实现 query.py 脚本入口

目标：提供本地命令行入口，完整调用 `hybridsearch + filter + rerank` 查询链路，方便调试和验收。

修改文件：`src/scripts/query.py`、`src/core/bm25_analyzer.py`、
`src/core/query_engine/sparse_route.py`、`src/core/query_engine/hybrid_engine.py`、
`src/ingestion/embedding/bm25_indexer.py`、`src/storage/bm25_storage.py`、
`tests/unit/test_retrieval.py`

实现类/函数：

- `main()`：命令行或服务入口
- `parse_args()`：解析命令行参数
- `run_query_cli()`：执行本地查询流程
- `QueryRuntime.execute()`：串联 QueryProcessor、HybridSearch、Filter、可选 Rerank 和 Response Builder
- `RerankController.rerank_with_outcome()`：为 `QueryRuntime` 提供显式 rerank/fallback 状态
- `BM25Storage.query()`：按 collection 查询 PostgreSQL BM25 posting 并返回有序候选
- `normalize_bm25_keywords()`：统一摄取和在线查询的 BM25 关键词分析
- `HybridSearchResult.fused_results`：保存 metadata 过滤前的 RRF 结果，供 verbose、Trace 和评估对比

验收标准：支持 `--query "问题"` 必填参数；支持 `--top-k 10` 默认返回 10 条；
支持 `--collection xxx` 限定 Dense/Sparse 最终候选集合，持久化 Sparse 查询必须按
collection 隔离 posting 和 corpus stats，并在 rerank 前过滤候选；支持 `--verbose`
展示 QueryProcessor、Dense、Sparse、Fusion、Filter、Rerank 等中间结果，但只输出
chunk_id、score 和稳定 query 字段，不泄漏 metadata、向量、Provider 响应或 tool
payload；支持 `--no-rerank` 直接跳过 RerankController 并保持过滤后 RRF 顺序；
正常 rerank 和 rerank fallback 都必须由 `RerankOutcome` 显式决定，不允许根据
最终候选的 metadata 是否存在 rerank 字段来推断；
该标志必须在组件组合阶段阻止 LLM/Cross-Encoder Reranker 的创建，避免已跳过阶段
仍因模型依赖或凭据不可用而启动失败；
成功和异常路径都必须关闭 PostgreSQL pool；摄取和在线 BM25 查询使用同一 analyzer，
且 core/storage 不反向依赖 ingestion pipeline。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D13：建立 Retrieval 单元测试矩阵

目标：集中覆盖 Retrieval 链路的核心单元行为。

修改文件：`tests/unit/test_retrieval.py`、`tests/unit/test_reranker.py`、`tests/unit/test_response_builder.py`

实现类/函数：

- 测试用例

验收标准：Query、Dense、Sparse、RRF、HybridSearch、Rerank 前过滤、Rerank、
Response、query.py 参数解析均覆盖；同时覆盖 Hybrid Fusion 非预期异常边界、
PostgreSQL BM25 非法 top_k/collection、空 terms 和驱动异常、QueryRuntime 的
rerank/no-rerank 双路径、RerankController 空候选/重复候选 fallback、NoOpReranker
防御性副本、Citation 的缺失/非法来源 metadata 以及图片 resolver 重复记录；单元测试
不访问真实模型或网络服务，目标 Retrieval/Reranker/Response 模块覆盖率不低于 90%。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit -v`

##### D14：实现 Retrieval 集成测试

目标：验证完整查询链路可串联运行。

修改文件：`tests/integration/test_query_pipeline.py`

实现类/函数：

- `FixedQueryEmbedding.embed()`：使用固定查询向量驱动真实 pgvector 检索
- `FailingQueryEmbedding.embed()`：模拟 Dense provider 不可用
- `_persist_fixture()`：写入隔离 collection、document、chunk、dense vector 和 BM25 posting
- `_runtime()`：组合 QueryProcessor、DenseRoute、SparseRoute、HybridSearch、RerankController 和 Response Builder
- `test_query_pipeline_hybrid()`：验证 Dense、BM25、RRF、Filter、Rerank、Response 和 `query.py --verbose`
- `test_query_pipeline_falls_back_to_sparse_when_dense_provider_fails()`：验证 Dense 失败时仍可使用 Sparse 结果完成响应

验收标准：使用真实 PostgreSQL、pgvector 和 BM25Storage，测试数据必须使用隔离 collection 并在 finally 中清理；Dense Route 能召回语义候选，Sparse Route 能通过 `BM25Indexer.index()` 生成的 posting 召回关键词候选；HybridSearch 必须先融合 Dense/Sparse，再在 Rerank 前过滤 collection；Rerank 成功时 final_results 按 provider 排序，且 `fallback_used=false` 不依赖 result metadata；Dense provider 失败时保留 Sparse 结果并标记 fallback；`query.py --verbose` 输出必须包含稳定 debug 字段且不泄漏 metadata。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_query_pipeline.py -v`
#### 阶段 E：MCP 工具服务

##### E1：搭建 MCP Server

目标：创建 MCP Server 入口并注册工具。

修改文件：`src/mcp_server/server.py`、`tests/unit/test_mcp_tools.py`

实现类/函数：

- `create_mcp_server()`：创建服务实例
- `parse_args()`：解析 MCP server 启动参数，首版仅支持 `--transport stdio`
- `run_stdio_server()`：加载 `.env`、配置 app.log、创建 FastMCP server 并启动 stdio transport
- `main()`：提供 `python -m src.mcp_server.server --transport stdio` 入口
- `_load_local_environment()`：从 RAG 根目录或项目根目录加载 `.env`
- `_configure_stdio_logging()`：把普通运行日志写入 `src/logs/app.log`，避免污染 stdout
- `_validate_tool_names()`：校验 settings 中声明的 tool 都属于当前支持集合
- `_register_placeholder_tool()`：按配置注册 E1 placeholder tool 处理函数
- `_query_knowledge_hub_placeholder()`：保留查询工具 schema，并在 E2 前返回明确未实现错误
- `_list_collections_placeholder()`：保留 collection 列表工具 schema，并在 E3 前返回明确未实现错误
- `_get_document_summary_placeholder()`：保留文档摘要工具 schema，并在 E3 前返回明确未实现错误

验收标准：`create_mcp_server(settings=...)` 返回官方 `FastMCP` 实例；server 名称稳定为 `aimodel-rag`；当 `settings.mcp.enabled=true` 时只注册 `settings.mcp.tools` 中声明且当前支持的工具；未知 tool 名称必须在 server 创建阶段抛出 `McpError`，避免外部客户端看到静默缺失能力；E1 placeholder tool 通过官方 `call_tool()` 调用时会被 MCP SDK 包装为 `ToolError`，错误信息必须明确说明当前工具尚未实现；首版 MCP 传输协议固定为 stdio，`main()` 支持 `--transport stdio` 并拒绝其它 transport；stdio 模式下 stdout/stdin 只允许 MCP 协议使用，普通日志必须写入 `src/logs/app.log`；server 启动时应加载 `.env`，以支持 AImodel 后端长期拉起 RAG MCP 子进程；E1 不得打开数据库连接、创建 LLM/Embedding/Reranker provider 或执行 Retrieval。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_mcp_tools.py -v`

##### E2：暴露知识库查询工具

目标：提供 `query_knowledge_hub` 工具。

修改文件：`src/mcp_server/tools.py`、`tests/unit/test_mcp_tools.py`

实现类/函数：

- `QueryKnowledgeHubTool.query_knowledge_hub()`：执行 MCP 查询工具入口，返回公共 RAG response 或结构化业务错误
- `QueryKnowledgeHubTool._validate_request()`：在打开数据库前校验 query、collection、top_k、no_rerank 和 include_image_base64
- `QueryKnowledgeHubTool._attach_image_base64()`：仅在显式请求时为受管图片附加受限大小的 base64 内容
- `_business_error()`：构建 `ok=false` 的可恢复业务错误 envelope
- `_default_runtime_builder()`：复用阶段 D QueryRuntime 组合路径
- `create_mcp_server(query_knowledge_hub=...)`：把 E2 的真实 query tool 注册到 FastMCP，E3 工具继续保持 placeholder

验收标准：返回 content、citations、trace_id；默认返回图片 metadata 和受管 file_path，不默认返回 base64；可预留 `include_image_base64=false` 参数，仅在显式请求时附加受限大小的 `base64_content`；业务可恢复错误返回 `{"ok": false, "error": {"code": "...", "message": "..."}}`，不直接把内部异常或 tool result 暴露给 Agent。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_mcp_tools.py -v`

##### E3：暴露 collection 和 summary 工具

目标：提供 collection 列表和文档摘要查询工具。

修改文件：`src/mcp_server/tools.py`、`tests/unit/test_mcp_tools.py`

实现类/函数：

- `MetadataTool.list_collections()`：列出可检索 collection，返回文档数、chunk 数和最近更新时间
- `MetadataTool.get_document_summary()`：按 document_id 或 source_uri 返回文档摘要、章节列表和摄取状态
- `MetadataTool._validate_summary_request()`：在加载配置和打开数据库前校验摘要查询身份参数
- `PostgresMetadataReader.list_collections()`：从 PostgreSQL 读取 success 文档可检索 collection 概览
- `PostgresMetadataReader.get_document_summary()`：从 PostgreSQL 读取单文档公开摘要，不返回全文、向量或内部 trace
- `create_mcp_server(list_collections=..., get_document_summary=...)`：把 E3 的真实 metadata tools 注册到 FastMCP

验收标准：`list_collections` 返回可检索 collection、文档数量、chunk 数量和最近更新时间；无可检索 collection 时返回可读 `ok=false` 业务错误；`get_document_summary` 支持按 `document_id` 或 `source_uri` 查询，返回文档摘要、章节列表、摄取状态和 chunk 数量；缺失文档返回可读 `document_not_found` 业务错误；工具输出不得泄漏全文、向量、BM25 postings、内部 trace 或数据库原始异常。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_mcp_tools.py -v`

##### E4：完成 MCP schema 测试

目标：验证 MCP tools schema 与文档契约一致。

修改文件：`tests/unit/test_mcp_tools.py`

实现类/函数：

- `test_mcp_tool_schemas_match_documented_contract()`：通过官方 FastMCP `list_tools()` 校验工具输入 schema、required 字段、默认值、输出 schema 和描述
- `test_mcp_success_outputs_do_not_leak_internal_fields()`：调用三个真实工具 handler 并递归检查返回值不泄漏 debug、metadata、embedding、BM25、provider、prompt、tool_result 等内部字段
- `test_mcp_business_errors_use_stable_public_envelope()`：校验 query、collection 和 document summary 的可恢复错误都使用稳定 `ok=false` envelope

验收标准：tools schema 与文档一致，不泄漏内部 JSON；三个 MCP tool 的成功输出只包含公共字段；可恢复错误统一返回 `{"ok": false, "error": {"code": "...", "message": "..."}}`；E4 聚焦 MCP schema 和 tool contract，AImodel 连接后的完整 E2E 验收放在 H1 执行。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_mcp_tools.py -v`

#### 阶段 F：可观测与管理平台

##### F1：实现 Trace 上下文

目标：提供 ingestion/query 链路通用 Trace 上下文。

修改文件：`src/core/trace/__init__.py`、`src/core/trace/trace_context.py`、`src/core/trace/trace_controller.py`、`tests/unit/test_trace_context.py`

实现类/函数：

- `TraceContext`：保存单次 query/ingestion 的 trace 上下文
- `TraceController`：统一记录阶段信息并 flush 结构化日志
- `TraceContext.record_stage()`：记录阶段名、耗时、状态、输入摘要、输出摘要、provider、method、candidate_count、details 和 error，并兼容现有 query_engine trace protocol
- `TraceContext.finish()`：设置完成状态、finished_at、汇总指标、评估指标和端到端耗时
- `TraceContext.to_dict()`：生成 JSON-compatible trace 快照
- `TraceController.record_stage()`：将阶段记录委托给 TraceContext
- `TraceController.flush()`：完成 trace 并调用注入的 sink
- `src/core/trace/__init__.py`：导出 TraceContext 和 TraceController

验收标准：可记录阶段耗时和输入输出摘要；可记录错误和 fallback 详情；`flush()` 可生成结构化快照并调用注入 sink；F1 不提前实现 JSONFormatter/traces.jsonl 文件写入，该能力留给 F4。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_trace_context.py -v`

##### F2：实现 ingestion trace 结构

目标：定义 ingestion trace 的基础信息、阶段详情、汇总指标和评估指标。

修改文件：`src/core/trace/trace_context.py`、`tests/unit/test_trace_context.py`

实现类/函数：

- `TraceContext.ingestion()`：构建标准 ingestion trace，上线前校验 `collection`、`source_uri` 和 SHA256 `source_hash`
- `TraceContext.record_ingestion_stage()`：仅允许记录约定的摄取主阶段，并允许主阶段携带结构化 `sub_stages`
- `_normalize_sub_stages()`：校验并防御性复制子阶段名称、实现类、耗时、输入输出数量、状态、错误、JSON-safe `details` 和 snapshots
- `_normalize_transform_snapshots()`：校验每个快照只包含受限预览、chunk 标识、变化类型和截断标记
- `TraceContext.finish_ingestion()`：写入 ingestion 汇总指标和评估指标，并生成完整结构化快照
- `_validate_sha256()`：校验摄取源哈希纹
- `_validate_non_negative_int()`：校验 chunk、embedding、skip 等计数指标
- `_validate_optional_ratio()`：校验质量分数和 embedding 覆盖率
- `_json_section()`：区分“缺省 section”与“嵌套 None 值”，避免破坏 skip_reason/error 语义

验收标准：包含 ingestion 基础信息、阶段详情、汇总指标、评估指标；基础信息必须包含 `trace_id`、`trace_type=ingestion`、`started_at`、`collection`、`source_uri`、`source_hash`；阶段详情必须限制在约定的摄取主阶段；主阶段可选携带 `sub_stages`，每项必须包含 `name`、`duration_ms`、`status`、`input_count`、`output_count`，并可包含 `method`、`provider`、结构化 `error`、JSON-safe `details` 和受限 `snapshots`；`details` 必须被完整持久化，用于记录 `image_captioner` 的 provider、model、输入图片数、成功 caption 数和失败/降级原因；snapshot 只能保存 `chunk_id`、`chunk_index`、`change_type`、`before_preview`、`after_preview`、`before_truncated`、`after_truncated`，不得保存完整正文；汇总指标必须包含 `document_status`、`chunk_count`、`embedded_count`、`skipped_count`、`error`、`total_duration_ms`；评估指标支持 `chunk_quality_score`、`noise_reduction_summary`、`embedding_coverage`、`index_ready`。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_trace_context.py -v`

##### F3：实现 query trace 结构

目标：定义 query trace 的检索、融合和重排追踪结构。

修改文件：`src/core/trace/trace_context.py`、`tests/unit/test_trace_context.py`

实现类/函数：

- `TraceContext.query()`：构建标准 query trace，上线前校验 `collection`、用户原始 `raw_query` 和可选 `request_source`
- `TraceContext.record_query_stage()`：仅允许记录 `query_processing`、`dense`、`sparse`、`fusion`、`filter`、`rerank` 六个查询阶段
- `TraceContext.finish_query()`：写入顶层 `query_result`、query 汇总指标和评估指标，并生成完整结构化快照
- `_validate_query_result()`：校验 `contexts/content/citations/images` 轻量查询结果快照，严格限制 citation/image 字段，避免 trace 重复保存完整公共响应或泄漏内部 provider payload
- `_validate_optional_finite_float()`：校验 `top_score` 和 context score 为有限数值或空值
- `_validate_candidate_count_by_stage()`：校验 Dense、Sparse、Fusion、Filter、Rerank 阶段候选数量
- `_validate_bool()`：校验 fallback、empty_result 等布尔指标，避免字符串 truthy 值污染结构化日志

验收标准：包含 query 基础信息、阶段详情、顶层 `query_result`、汇总指标、评估指标；基础信息必须包含 `trace_id`、`trace_type=query`、`started_at`、`collection`、`raw_query`，并在存在时记录 `request_source`；阶段详情必须限制在 `query_processing/dense/sparse/fusion/filter/rerank/response`；Dense/Sparse 阶段 details 必须记录命中的 `chunk_ids`，Fusion/Filter/Rerank 阶段 details 必须记录轻量候选快照和排序/过滤变化；`query_result` 必须包含 `contexts/content/citations/images`，contexts 每项包含 `chunk_id/score/rank`，citations 不包含 `source_uri`，images 仅包含 `image_id/chunk_ids/quality_status`；汇总指标仅保存 `top_score`、`candidate_count_by_stage`、`fallback_used`、`error`、`total_duration_ms`；评估指标支持 `query_document_relevance`、`citation_hit_rate`、`rerank_delta`、`empty_result`。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_trace_context.py -v`

##### F4：实现 JSON Lines 日志

目标：将 Trace 按 JSON Lines 追加写入本地日志。

修改文件：`src/observability/structured_log.py`、`src/storage/trace_log_storage.py`、`tests/unit/test_trace_context.py`

实现类/函数：

- `JsonFormatter.format()`：将 Python `logging.LogRecord` 序列化为单行 JSON，保留中文 query 和 trace payload
- `JsonFormatter.make_record()`：为单元测试和适配层构造带结构化 extra 的 log record
- `configure_jsonl_logger()`：创建独立 UTF-8 file logger，使用 JSONFormatter 追加写入 JSON Lines
- `JsonlTraceWriter.write()`：校验 trace snapshot 并追加写入 `traces.jsonl`
- `JsonlTraceWriter.__call__()`：作为 `TraceController` sink 直接接收 flush 后的 trace snapshot

验收标准：每行合法 JSON，可追加写入；`JsonlTraceWriter` 自动创建父目录；写入的 trace 行以 trace snapshot 为顶层对象，便于 Dashboard 按行读取；同一个 writer 连续写入多条 trace 时保持追加顺序；可直接作为 `TraceController` 的 sink 使用。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_trace_context.py -v`

##### F5：将 Trace 打点注入 ingestion 和 query 链路

目标：让 Trace 不停留在独立工具层，而是真正进入 ingestion 和 query 的运行主链路。

修改文件：`src/ingestion/pipeline.py`、`src/scripts/ingest.py`、`src/scripts/query.py`、`src/core/query_engine/hybrid_engine.py`、`src/core/trace/trace_context.py`、`src/core/trace/trace_controller.py`、`src/storage/trace_log_storage.py`、`src/storage/schema.sql`、`tests/unit/test_trace_context.py`、`tests/integration/test_ingestion_pipeline.py`、`tests/integration/test_query_pipeline.py`、`tests/integration/test_repositories.py`

实现类/函数：

- `TraceController.record_stage()` 注入点：记录链路阶段信息
- `TraceController.flush_ingestion()`：按 ingestion trace 契约 flush 汇总指标
- `TraceController.flush_query()`：按 query trace 契约 flush 顶层 `query_result`、`top_score` 和汇总指标
- `IngestionPipeline.run()` trace 打点：注入链路追踪点
- `TransformPipeline.run()` trace observer：按配置顺序测量每个具体 Transform 实现，成功和失败都生成子阶段记录；记录 `changed_count/unchanged_count` 解释实际处理结果；开启 `observability.transform_snapshots.enabled` 时记录变化 chunk 的受限 before/after 预览；`image_captioner` 必须额外记录 provider、model、image_count、caption_count 和失败/降级原因
- `IngestionPipeline.run_indexing()` trace 打点：注入索引子链路追踪点
- `HybridSearch.search()` trace 打点：将 RRF 阶段统一记录为 `fusion`
- `QueryRuntime.execute()` trace 打点：注入 query_processing、rerank 跳过、response 和最终 flush；将实际返回给 Agent/调用方的 content、contexts 及精简后的 citations/images 快照写入 `query_result`
- `PostgresTraceWriter`：将 TraceController 完成后的统一 snapshot 转换为 Query/Ingestion Trace Record，并按 `trace_id` 幂等写入 PostgreSQL；Query Trace 独立持久化 `query_result` JSONB
- `CompositeTraceWriter`：将同一最终 snapshot 分发至 JSONL 和 PostgreSQL writer，避免业务链路为不同存储编写特殊分支
- Trace writer CLI 注入：`ingest.py` 和 `query.py` 默认使用 `settings.observability.trace_jsonl_path`；当 `settings.observability.persist_to_postgresql=true` 时同时写入 PostgreSQL
- Trace 状态约束：Query/Ingestion trace 表接受 `degraded`，且 `init_schema()` 可幂等升级本地数据库约束

验收标准：ingestion 链路记录 dedup、load、split、transform、embed、upsert；图片 caption 不得重复记录为顶层 stage，只能记录在 `transform.sub_stages.image_captioner`；顶层 `transform.duration_ms` 保留整个 Transform Pipeline 总耗时，`transform.sub_stages` 按实际执行顺序记录每个启用实现的名称、具体类、耗时、输入输出 chunk 数、`changed_count`、`unchanged_count` 和状态，使 Dashboard 能区分“执行但未改变”与“未执行”；`image_captioner` 子阶段必须记录图片 caption 的 provider/model、输入图片数、成功 caption 数和失败/降级原因；某个实现失败时必须先记录该失败子阶段，再让主链路按原错误语义失败；Transform snapshots 必须由配置控制，默认只记录变化 chunk、每步最多 20 个、每段预览最多 800 字，不额外调用 LLM 或数据库；query 链路记录 query_processing、dense、sparse、fusion、filter、rerank、response，并在结束时保存顶层 `query_result`；正常、失败、跳过和降级结束都会 flush 同一种 trace snapshot；启用 PostgreSQL 持久化时，真实 ingestion/query 链路的最终 snapshot 同时进入 JSONL 与对应 trace 表，Dashboard 可直接读取；不得仅在去重跳过等特殊分支单独写入数据库。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_ingestion_pipeline.py services\ai-service\rag\tests\integration\test_query_pipeline.py -v`；`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_trace_context.py -v`；`uv run --project services/ai-service/rag ruff check services/ai-service/rag/src services/ai-service/rag/tests`

##### F6：实现配置读取和数据浏览服务

目标：为 Dashboard 提供配置读取和文档/chunk 查询能力。

修改文件：`src/observability/services/__init__.py`、`src/observability/services/config_reader.py`、`src/observability/services/data_browser_service.py`、`tests/integration/test_dashboard_services.py`

实现类/函数：

- `ConfigReaderService`：读取 settings 并展示当前组件配置
- `DataBrowserService`：查询文档、chunk、图片和索引状态
- `ConfigReaderService.read_overview()`：输出项目身份、组件配置、Dashboard 页面和关键路径；Reranker 需要解析 `llm_provider` 对应的真实 LLM 模型，Transform 需要输出每个 `sub_transform` 的 provider/model/model_source/prompt_path
- `DataBrowserService.collection_stats()`：统计 collection 的文档、chunk、图片、Dense 和 BM25 索引数量
- `DataBrowserService.list_documents()`：返回文档列表、生命周期、来源和子资源数量
- `DataBrowserService.list_chunks()`：返回 chunk 明细、Dense/BM25 状态和 image_refs
- `DataBrowserService.get_chunk_detail()`：按 chunk_id 查询单条 chunk 明细
- `DataBrowserService.list_images()`：返回 image_index 记录

验收标准：可读取 settings 和文档/chunk 数据。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`；`uv run --project services/ai-service/rag ruff check services/ai-service/rag/src services/ai-service/rag/tests`

##### F7：实现 Trace 读取和评估服务

目标：为 Dashboard 提供 trace 历史和评估趋势数据。

修改文件：`src/observability/services/__init__.py`、`src/observability/services/trace_reader_service.py`、`src/observability/services/evaluation_service.py`、`tests/integration/test_dashboard_services.py`

实现类/函数：

- `TraceReaderService`：读取 query/ingestion trace 历史和详情
- `EvaluationService`：运行评估任务并读取指标趋势
- `TraceReaderService.list_query_traces()`：返回 Query Trace 历史列表，包含耗时、阶段数量、fallback 状态和输入摘要
- `TraceReaderService.list_ingestion_traces()`：返回 Ingestion Trace 历史列表，包含耗时、阶段数量和来源文件摘要
- `TraceReaderService.get_query_trace_detail()`：返回 Query Trace 阶段瀑布图、候选数量、顶层 query_result、summary/evaluation metrics 和 rerank delta
- `TraceReaderService.get_ingestion_trace_detail()`：返回 Ingestion Trace 主阶段瀑布图、Transform 子阶段明细、Transform snapshot diff、summary/evaluation metrics 和错误详情
- `EvaluationService.run_evaluation()`：通过 EvaluatorFactory 同步运行评估并持久化 run/results
- `EvaluationService.list_runs()`：返回 evaluation run 历史和指标摘要
- `EvaluationService.get_run_detail()`：按 run_id 返回评估详情、指标明细和 settings snapshot
- `EvaluationService.metric_trends()`：按 metric_name 返回历史趋势点

验收标准：可读取 trace 历史和评估趋势。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`；`uv run --project services/ai-service/rag ruff check services/ai-service/rag/src services/ai-service/rag/tests`

##### F8：实现总览、摄取管理页面和摄取操作

目标：实现系统总览和 Ingestion 管理页面，并让 Dashboard 的 `Run ingestion` 按钮触发真实摄取操作，而不是仅返回 `pending orchestration` 占位状态。

修改文件：`src/observability/pages/__init__.py`、`src/observability/pages/overview.py`、`src/observability/pages/ingestion_manage.py`、`src/observability/dashboard/app.py`、`src/observability/services/ingestion_operation_service.py`、`tests/integration/test_dashboard_services.py`、`tests/integration/test_dashboard_pages.py`

实现类/函数：

- `build_overview_page_model()`：读取配置、collection 统计和最新 query/ingestion trace，生成系统总览页面模型
- `render_overview_page()`：渲染组件配置、Transform 行下的 `sub_transform` 展开明细、数据资产统计和系统健康指标
- `build_ingestion_manage_page_model()`：读取默认 collection、raw data 路径和已索引文档列表
- `render_ingestion_manage_page()`：渲染摄取参数、force 选项、文件/目录选择控件、批量候选确认表、已索引文档表格和删除选择控件；当用户点击 `Run ingestion` 时展示真实摄取结果或错误信息
- `IngestionOperationRequest`：保存 Dashboard 摄取请求参数，包括 collection、source_path/source_paths、force 和可选 uploaded_files
- `IngestionOperationResult`：保存真实摄取结果，包括 status、processed、trace_ids、source_paths、error 和 summary
- `IngestionOperationService.run_ingestion()`：校验 source_path/source_paths，保存用户通过 Dashboard 上传的文件，复用 ingestion pipeline/CLI 组装逻辑执行单文件或批量摄取，返回真实结果；不得返回未执行的 pending 状态
- `render_dashboard_page()`：把 `IngestionOperationService` 注入 Ingestion 管理页面，让页面提交能够进入真实 service 层

职责边界：

- Dashboard 页面负责采集参数、展示进度/结果和错误，不直接拼接底层 pipeline 依赖。
- Dashboard 页面必须提供文件选择入口：多文件上传使用 Streamlit 文件选择器，目录上传使用 `accept_multiple_files="directory"`，服务器本机目录仍支持路径输入。
- 当用户选择目录或一次选择多个文件时，页面必须展示待摄取文件列表，允许用户取消某个文件后再提交；最终只摄取用户保留的候选文件。
- `IngestionOperationService` 负责把 Dashboard 请求转换为摄取执行，复用 `src.scripts.ingest` 或相同 Pipeline Builder，避免 Dashboard 与 CLI 出现两套摄取逻辑。
- 上传文件应保存到 `data/raw/{collection}/dashboard_uploads/` 下，再把保存后的本地路径交给摄取入口。
- 摄取操作必须继续走 `IngestionPipeline`，保证 dedup、document_summary、split、transform、embedding、upsert、trace 写入和错误处理一致。
- 测试中必须使用 fake provider、测试数据库或注入 runner，禁止真实调用外部 LLM/Embedding。

验收标准：总览和摄取管理页面可启动；用户可通过文件选择器一次选择多个文件，也可通过目录上传选择文件夹；选择文件夹或多个文件后页面展示候选文件列表并支持取消某个文件；点击 `Run ingestion` 后只摄取被选中的文件；成功时 PostgreSQL 中可以看到写入或更新的 document、chunk、image index 和 ingestion trace；skipped 时返回真实 skipped 结果和 trace_id；失败时页面展示结构化错误；页面仅展示真实摄取执行结果。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_services.py services\ai-service\rag\tests\integration\test_dashboard_pages.py -v`；`uv run --project services/ai-service/rag ruff check services/ai-service/rag/src services/ai-service/rag/tests`

##### F9：实现数据浏览和 Query Trace 页面

目标：实现数据浏览器和 Query Trace 可视化页面。

修改文件：`src/observability/pages/data_browser.py`、`src/observability/pages/query_trace.py`、`tests/integration/test_dashboard_services.py`

实现类/函数：

- `build_data_browser_page_model()`：读取文档、chunk、chunk detail 和图片列表，生成数据浏览页面模型
- `render_data_browser_page()`：渲染文档列表、chunk 列表、chunk 详情、来源 metadata、image_refs 和图片表格
- `build_query_trace_page_model()`：读取 Query Trace 历史和选中 trace 详情，生成 Query Trace 页面模型
- `render_query_trace_page()`：渲染 Query Trace 历史、阶段瀑布图、Dense/Sparse/Fusion/Rerank 候选数量对比、Chunk Frequency Summary、Chunk Flow Matrix、`query_result.contexts`、`top_score` 和 rerank delta；Trace 下拉框使用固定 widget key 持久化选择
- Dashboard Query Trace 分发：每次 Streamlit 重跑从 `session_state` 读取已选 Query Trace ID，并传入 `build_query_trace_page_model()`

验收标准：可展示文档、chunk、召回对比、rerank 变化；Query Trace 页面必须基于 Dense/Sparse 的 `chunk_ids`、Fusion/Filter/Rerank 的轻量候选快照和 `query_result.contexts` 展示 Chunk Frequency Summary，字段包含 `chunk_id`、`appeared_count`、`stages`、`final_rank`、`best_score`、`filtered_reason`，并按出现次数、最终命中、最高分稳定排序；Query Trace 页面必须展示 Chunk Flow Matrix，字段包含 `chunk_id`、`dense`、`sparse`、`fusion_rank`、`filter`、`rerank_rank`、`final_rank`，用于观察同一 chunk 在 dense、sparse、fusion、filter、rerank 和最终结果中的流转；旧 trace 缺少新增字段时页面不得报错，应展示空表或已有可用信息；选择任意 Query Trace 后详情必须同步切换；已选 ID 不属于当前 collection 时自动回退到最新记录。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`；`uv run --project services/ai-service/rag ruff check services/ai-service/rag/src services/ai-service/rag/tests`

##### F10：实现 Ingestion Trace 和评估页面

目标：实现摄取追踪和评估趋势页面。

修改文件：`src/observability/pages/ingestion_trace.py`、`src/observability/pages/evaluation.py`、`tests/integration/test_dashboard_services.py`

实现类/函数：

- `build_ingestion_trace_page_model()`：读取 Ingestion Trace 历史和选中 trace 详情，生成摄取追踪页面模型
- `render_ingestion_trace_page()`：渲染摄取 trace 历史、主阶段耗时瀑布图、包含 changed/unchanged 数量的 Transform Breakdown 表格和柱状图、Transform Result Diff 红绿内容差异卡片、处理统计、质量指标和错误详情；Trace 下拉框使用固定 widget key 持久化选择
- Dashboard Ingestion Trace 分发：每次 Streamlit 重跑从 `session_state` 读取已选 Ingestion Trace ID，并传入 `build_ingestion_trace_page_model()`
- `build_evaluation_page_model()`：读取 evaluation run 历史、选中 run detail 和 metric trends，生成评估页面模型
- `render_evaluation_page()`：渲染评估运行入口、run 历史、指标详情、settings snapshot 和趋势图，并返回运行评估意图 DTO

验收标准：可展示阶段耗时和评估趋势；Transform 主阶段存在 `sub_stages` 时，页面必须按执行顺序展示每个 Transform 实现的名称、实现类、耗时、输入输出 chunk 数、变化数量、未变化数量、状态和错误，避免将“没有 diff”误解为“没有执行”；`image_captioner` 子阶段详情必须展示 provider/model、图片数量、caption 数量和失败/降级原因；存在 snapshots 时展示 Transform Result Diff，用专属颜色区分 `metadata_enrich`、`rewrite_chunk`、`semantic_merge`、`denoise` 和 `image_captioner`，并以浅红背景标注 before 中被删除或替换的内容、以浅绿背景标注 after 中新增或替换的内容；Diff 必须采用兼容中英文混排的细粒度 token 对比，不能将无空格的整段中文直接判定为单个替换块，也不能使用影响长文本可读性的整段删除线；未变化文本必须使用独立的显式深色前景样式，不能依赖 Streamlit `pre`/代码块主题继承，确保浅色和深色主题下都可读；同时展示 before/after 预览、变化类型和截断标记；缺少 `sub_stages/snapshots` 或变化计数的 trace 保持可读且不显示空明细区；选择任意 Ingestion Trace 后阶段耗时、Transform Breakdown、Result Diff、处理统计和错误详情必须同步切换；已选 ID 不属于当前 collection 时自动回退到最新记录。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`；`uv run --project services/ai-service/rag ruff check services/ai-service/rag/src services/ai-service/rag/tests`

##### F11：实现 Dashboard 启动脚本

目标：提供本地启动 Streamlit Dashboard 的脚本入口。

修改文件：`src/observability/dashboard/__init__.py`、`src/observability/dashboard/app.py`、`src/scripts/run_dashboard.py`、`tests/integration/test_dashboard_services.py`

实现类/函数：

- `load_dashboard_pages()`：导入并校验六大 Dashboard 页面模块
- `main()`：渲染轻量 Dashboard shell 并返回已加载页面
- `resolve_dashboard_app_path()`：解析 Streamlit app 文件路径
- `load_dashboard_app()`：导入 app module 并校验公开 callable 契约
- `build_streamlit_command()`：构建无 shell 的 `streamlit run` 命令
- `run_dashboard()`：加载 `.env`、校验 app、支持 dry-run，并通过可注入 command runner 启动 Streamlit

验收标准：脚本可加载 app，不要求真实启动浏览器。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`；`uv run --project services/ai-service/rag ruff check services/ai-service/rag/src services/ai-service/rag/tests`

##### F12：完成 Dashboard 六大页面测试

目标：在进入 AImodel 集成前，验证六大 Dashboard 页面都能基于测试数据正常渲染。

修改文件：`tests/integration/test_dashboard_pages.py`、`tests/integration/test_dashboard_services.py`、`src/observability/dashboard/app.py`

实现类/函数：

- `test_dashboard_six_pages_render_from_services_and_test_database()`：用真实 PostgreSQL 测试数据验证六大页面都能读取服务 DTO 并完成渲染
- `FakeStreamlit`：记录六大页面的 Streamlit 调用，避免测试启动浏览器
- `_seed_dashboard_fixture()`：写入文档、chunk、图片、query trace、ingestion trace 和 evaluation run 测试数据
- `DASHBOARD_PAGE_LABELS`：定义六大页面在 sidebar 中展示的稳定名称
- `select_dashboard_page()`：渲染 sidebar radio 导航并返回选中页面
- `render_dashboard_page()`：按选中页面构建 service-backed model 并调用对应页面 renderer

验收标准：系统总览、Ingestion 管理、数据浏览器、Query Trace、Ingestion Trace、评估面板都可以读取测试配置、测试数据库记录和测试 trace，并完成页面渲染入口调用。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_pages.py -v`；`uv run --project services/ai-service/rag ruff check services/ai-service/rag/src services/ai-service/rag/tests`

#### 阶段 G：质量评估体系

##### G1：准备黄金测试集格式

目标：定义黄金测试集字段和 fixture 样例。

修改文件：`tests/fixtures/golden_set.json`、`tests/unit/test_evaluation.py`

实现类/函数：

- `tests/fixtures/golden_set.json`：保存 JSON 数组格式的黄金测试集，每条样本包含问题、标准答案、来源文档和关键词
- `test_golden_set_fixture_exists_and_contains_representative_cases()`：验证 fixture 存在并覆盖购物指南核心品类
- `test_golden_set_samples_follow_required_schema()`：验证样本 ID 唯一、字段非空、来源文档和关键词完整

验收标准：问题、答案、来源文档字段完整；样本 ID 唯一；每条样本声明 collection、expected_sources 和 expected_keywords；首批样例覆盖无线耳机、人体工学键盘和解压玩具。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_evaluation.py -v`

##### G2：实现自定义检索指标

目标：实现 Hit Rate、MRR、NDCG 等检索指标。

修改文件：`src/observability/evaluation/__init__.py`、`src/observability/evaluation/metrics.py`、`tests/unit/test_evaluation.py`

实现类/函数：

- `HitRateMetric`：计算 Hit Rate@K，判断每条问题的 Top-K 结果中是否命中任一黄金来源
- `MRRMetric`：计算 Mean Reciprocal Rank，衡量第一个相关来源在排序中的位置
- `NDCGMetric`：计算二值相关性的 NDCG@K，衡量 Top-K 结果排序质量
- `HitRateMetric.score()`：接收黄金集和检索预测结果，返回平均 Hit Rate@K
- `MRRMetric.score()`：接收黄金集和检索预测结果，返回平均 Reciprocal Rank
- `NDCGMetric.score()`：接收黄金集和检索预测结果，返回平均 NDCG@K

验收标准：指标计算无需真实 LLM；支持 `retrieved_sources` 为字符串列表或候选 mapping 列表；输入数量不一致、`top_k` 非法或黄金样本缺少 `expected_sources` 时应 fail fast。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_evaluation.py -v`

##### G3：接入 Ragas 指标

目标：封装 Ragas 生成质量指标。

修改文件：`src/observability/evaluation/__init__.py`、`src/observability/evaluation/ragas_adapter.py`、`tests/unit/test_evaluation.py`

实现类/函数：

- `RagasEvaluator`：封装 Ragas 生成质量评估执行逻辑
- `RagasEvaluator.evaluate()`：将黄金集和生成结果转换为 Ragas-compatible rows，并返回归一化数值指标
- `_load_ragas_backend()`：懒加载可选 Ragas 依赖，避免普通开发环境强制安装 evaluation extra
- `_to_ragas_v02_row()`：在真实 Ragas backend 边界将项目行字段映射为 Ragas 0.2 单轮评估列
- `_normalize_ragas_result()`：将 Ragas mapping、scores 或 dataframe 结果归一化为 `metric_name -> float`

验收标准：Ragas 测试使用 marker 隔离；普通单元测试不得导入真实 Ragas 依赖；默认指标包含 `faithfulness` 和 `answer_relevancy`；空 `metric_names` 必须 fail fast；真实 Ragas 未安装时应返回可读 ProviderError。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_evaluation.py -v`

##### G4：实现策略对比评估

目标：支持 Hybrid、Dense-only、Sparse-only、Rerank 等策略对比。

修改文件：`src/observability/evaluation/__init__.py`、`src/observability/evaluation/runner.py`、`tests/unit/test_evaluation.py`

实现类/函数：

- `RetrievalStrategy`：描述一次评估中的检索模式和是否启用 rerank
- `StrategyComparisonResult`：保存单个策略的预测记录和指标结果
- `StrategyRetrievalFn`：定义可注入的检索执行 callable，隔离真实 QueryRuntime/数据库依赖
- `EvaluationRunner.compare_strategies()`：按 hybrid、dense_only、sparse_only、rerank 等策略逐一执行检索并计算指标
- `EvaluationRunner._score()`：复用 Hit Rate@K、MRR@K、NDCG@K 计算每个策略得分

验收标准：可对比 Hybrid、Dense-only、Sparse-only、Rerank；包入口应导出 `RetrievalStrategy` 以支持外部自定义策略；空 metrics 配置必须 fail fast；单元测试不得依赖真实 PostgreSQL、Embedding 或 QueryRuntime；策略输出需保留 predictions，便于后续 Dashboard 展示和 G5 保存。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_evaluation.py -v`

##### G5：实现评估趋势输出

目标：保存评估结果，供 Dashboard 展示历史趋势。

修改文件：`src/observability/evaluation/runner.py`、`tests/unit/test_evaluation.py`

实现类/函数：

- `EvaluationSaveResult`：返回已写入的 evaluation run 和 metric result records
- `EvaluationResultRepository`：定义保存 evaluation run/results 所需的最小 repository 协议
- `EvaluationRunner.save_results()`：保存策略对比评估结果，生成一条 run 和多个 `strategy.metric` 指标行
- `_comparison_result_records()`：将策略指标展开为 `EvaluationResultRecord` 列表
- `_result_id()`：基于 run_id 和 metric_name 生成幂等 metric row ID

验收标准：评估结果可通过 `EvaluationRepository` 边界写入 PostgreSQL 并供 Dashboard 展示；metric name 应使用 `strategy.metric` 形式便于历史趋势分组；metric details 必须保留 strategy、retrieval_mode、use_rerank、raw_metric_name、sample_count 和 predictions；保存前必须校验各策略 prediction 数量一致；单元测试使用 fake repository，不直接连接真实 PostgreSQL。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_evaluation.py -v`

##### G6：实现真实 Ragas 评估入口与最终上下文优化

目标：把已有 Ragas adapter 从单元适配能力提升为可运行的真实评估入口，并把
`query_result.content` 从裸 chunk 拼接升级为 AImodel 可直接使用的最终上下文。

修改文件：`config/settings.example.yaml`、`config/prompts/evidence_context_prompt.yaml`、`src/core/config.py`、`src/core/response/evidence_context_optimizer.py`、`src/core/response/response_builder.py`、`src/libs/evaluator/evaluator_factory.py`、`src/libs/evaluator/ragas_evaluator.py`、`src/libs/evaluator/__init__.py`、`src/scripts/query.py`、`src/scripts/run_evaluation.py`、`tests/unit/test_config.py`、`tests/unit/test_response_builder.py`、`tests/unit/test_evaluation.py`

实现类/函数：

- `EvidenceContextOptimizer.optimize()`：将编号证据块整理为 Agent-ready final context，不生成最终答案
- `KnowledgeHubResponseBuilder.build()`：生成原始编号证据块，并在启用 optimizer 时返回优化后的最终上下文
- `RagasEvaluatorClient.evaluate()`：作为 `BaseEvaluator` 实现委托 `src.observability.evaluation.RagasEvaluator`
- `RagasEvaluatorClient.__init__()`：读取 `evaluation.llm_provider` 与 `evaluation.embedding_provider`，将项目 LLM/Embedding 客户端注入 Ragas
- `_load_ragas_backend()`：将项目 `BaseLLM`/`BaseEmbedding` 包装为 Ragas 可用的 LLM 与 Embeddings，避免真实评估依赖 Ragas 默认模型
- `EvaluatorFactory.register_builtin_providers()`：注册 `fake` 和 `ragas`
- `run_evaluation_cli()`：加载 golden set、执行 Query Pipeline、构造 predictions、调用 evaluator 并持久化结果
- `_prediction_from_query_result()`：将 `query_result.content` 映射为 Ragas answer，并按 contexts 回查 chunk text 构造 `retrieved_contexts`

验收标准：`settings.example.yaml` 包含 `response.evidence_context_optimizer` 配置以及 `evaluation.llm_provider/evaluation.embedding_provider` 配置；Prompt 使用英文指令；启用优化时 `query_result.content` 是 Agent-ready final context，并保留 `[1]` 等证据编号；LLM 优化失败、未配置或返回空内容时按配置 fallback 到原始编号证据块；`query_result.contexts` 继续保存 `chunk_id/score/rank` 用于溯源和评估；`EvaluatorFactory.create(provider="ragas")` 可创建真实 Ragas evaluator 且仍保持懒加载；真实 Ragas 评估必须使用项目配置的 LLM/Embedding provider，不依赖 Ragas 默认模型；`run_evaluation.py` 支持 `--collection`、`--golden-set`、`--evaluator`、`--top-k`、`--no-rerank`，可把评估结果写入 `rag_evaluation_runs` 和 `rag_evaluation_results`；单元测试不得真实调用外部 LLM、Embedding 或 Ragas backend。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_config.py services\ai-service\rag\tests\unit\test_response_builder.py services\ai-service\rag\tests\unit\test_evaluation.py -v`；`uv run --project services/ai-service/rag ruff check services/ai-service/rag/src services/ai-service/rag/tests`

#### 阶段 H：AImodel 联调集成

##### H1：执行 AImodel 集成前验收门禁

目标：通过 Dashboard 六大页面测试、RAG 全链路 E2E 和 MCP stdio 可连接验收，确认 RAG 独立模块满足 AImodel 集成条件。

修改文件：`tests/integration/test_dashboard_pages.py`、`tests/e2e/test_full_rag_flow.py`

实现类/函数：

- `test_dashboard_six_pages_render()`：验证六大 Dashboard 页面可以基于 service-backed PostgreSQL 测试数据完成渲染，覆盖系统总览、Ingestion 管理、数据浏览器、Query Trace、Ingestion Trace 和评估面板
- `test_full_rag_flow_before_aimodel_integration()`：使用 fake LLM/Vision/Embedding provider 运行真实离线摄取、Indexing Pipeline、Hybrid Query、Trace PostgreSQL 写入、Dashboard service 读取和引用结果构造
- `test_rag_mcp_stdio_before_aimodel_integration()`：启动 stdio MCP server 子进程并验证 MCP client 可 `list_tools` 和调用核心 tool 契约

验收标准：Dashboard 六大页面测试通过；全链路 E2E 覆盖离线摄取、Indexing Pipeline、Hybrid Query、Trace 写入、Dashboard 可读和引用结果构造；stdio MCP 子进程可由测试 client 启动、列出 `query_knowledge_hub`、`list_collections`、`get_document_summary`，并能按 tool contract 返回结构化结果或结构化业务错误。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_pages.py services\ai-service\rag\tests\e2e\test_full_rag_flow.py -v`

##### H2：实现 AImodel RAG 工具适配

目标：封装 AImodel 可调用的 RAG 工具。

修改文件：`services/ai-service/pyproject.toml`、`services/ai-service/app/routers/AImodel/tools.py`、`services/ai-service/app/routers/AImodel/service.py`、`services/ai-service/app/routers/AImodel/memory.py`、`services/ai-service/tests/test_aimodel_rag_tool.py`、`services/ai-service/tests/test_aimodel_memory.py`

实现类/函数：

- `StdioMcpRagKnowledgeClient.query_knowledge_hub()`：默认通过 RAG 子项目 uv 环境启动 stdio MCP，并调用 RAG `query_knowledge_hub` 工具
- `search_shopping_guides()`：暴露 AImodel 对外工具能力，只返回格式化上下文、引用、图片、空结果标记和 trace id
- `_public_rag_tool_data()`：过滤 MCP 响应，仅保留 Agent 可消费的公共字段
- `_is_tool_result_json()`：识别 RAG 工具 JSON，避免前端流式输出内部 tool result
- `message_query_trace`：使用 `message_id + query_trace_id` 保存 assistant message 与一个或多个 RAG Query Trace 的逻辑关联，不使用物理外键
- `AiModelMemoryStore.append_assistant_message()`：保存最终回答时原子写入去重后的 query trace 关联，并返回 message id
- `AiModelMemoryStore.list_message_query_traces()`：按 message id 查询用于生成该回答的 trace id

验收标准：工具返回格式化内容、引用、图片和 trace id；RAG business error 转换为可读 `AiModelToolResult` 错误；最终 assistant message 保存后可查询本轮使用的全部 RAG trace id；无 RAG 调用时不写入虚假关联；流式回答不会泄漏 `search_shopping_guides` 工具 JSON。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\tests\test_aimodel_rag_tool.py services\ai-service\tests\test_aimodel_memory.py services\ai-service\tests\test_aimodel_agent.py -v`；`uv run --project services/ai-service/rag ruff check services\ai-service\app\routers\AImodel\tools.py services\ai-service\app\routers\AImodel\service.py services\ai-service\app\routers\AImodel\memory.py services\ai-service\tests\test_aimodel_rag_tool.py services\ai-service\tests\test_aimodel_memory.py`

##### H3：接入 Agent 工具列表

目标：把 RAG 工具加入 AImodel Agent 工具集合。

修改文件：`services/ai-service/app/routers/AImodel/service.py`、`services/ai-service/app/routers/AImodel/memory.py`、`services/ai-service/tests/test_aimodel_rag_tool.py`、`services/ai-service/tests/test_aimodel_memory.py`

实现类/函数：

- `build_rag_tool()`：构建 `search_shopping_guides` LangChain Agent 工具，并在测试环境缺少 LangChain 时返回同名轻量 fallback tool
- `_run_rag_tool()`：调用 AImodel RAG 工具适配层并把结果追加到本轮 `tool_results`
- `_query_trace_ids_from_tool_results()`：收集本轮 Agent 工具结果中的 RAG trace id，并在最终 assistant message 入库时建立关联

验收标准：同步 Agent 和流式 Agent 的工具列表都包含 `search_shopping_guides`；Agent 可调用 RAG 工具并把返回结果加入 `tool_results`；一个 assistant message 可关联多个 RAG Query Trace，重复 trace id 只保存一次；关联写入与 assistant message 写入处于同一数据库事务。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\tests\test_aimodel_rag_tool.py services\ai-service\tests\test_aimodel_memory.py services\ai-service\tests\test_aimodel_agent.py -v`；`uv run --project services/ai-service/rag ruff check services\ai-service\app\routers\AImodel\service.py services\ai-service\app\routers\AImodel\tools.py services\ai-service\app\routers\AImodel\memory.py services\ai-service\tests\test_aimodel_rag_tool.py services\ai-service\tests\test_aimodel_memory.py`

##### H4：验证商品 API 与 RAG 边界

目标：明确商品事实走商品 API，知识补充走 RAG。

修改文件：`services/ai-service/app/routers/AImodel/service.py`、`services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- `SYSTEM_PROMPT`：明确商品事实/API 与知识补充/RAG 的职责边界

验收标准：商品价格、库存、优惠、规格、可购买商品和商品链接等商品事实只能来自商品搜索工具或商品详情工具；RAG 只用于选购指南、品类知识、政策 FAQ、售后规则和文档知识上下文；Agent 不得把 RAG 内容当作实时商品事实来源，不得用 RAG 生成价格、库存、优惠、可购买商品或商品链接，不得编造引用。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\tests\test_aimodel_rag_tool.py services\ai-service\tests\test_aimodel_agent.py -v`；`uv run --project services/ai-service/rag ruff check services\ai-service\app\routers\AImodel\service.py services\ai-service\tests\test_aimodel_rag_tool.py`

##### H5：验证简单询问和链接场景

目标：覆盖推荐、对比、选购指南、政策 FAQ 等用户场景。

修改文件：`services/ai-service/app/routers/AImodel/service.py`、`services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- `SYSTEM_PROMPT`：明确推荐、商品链接对比、选购指南和政策 FAQ 的工具选择规则
- 场景测试：覆盖推荐、对比、选购指南、政策 FAQ

验收标准：推荐场景必须使用商品搜索工具；商品链接对比场景必须使用商品详情工具；选购指南和政策 FAQ 场景必须使用 RAG 工具；四类场景都有测试覆盖。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\tests\test_aimodel_rag_tool.py services\ai-service\tests\test_aimodel_agent.py -v`；`uv run --project services/ai-service/rag ruff check services\ai-service\app\routers\AImodel\service.py services\ai-service\tests\test_aimodel_rag_tool.py`

##### H6：完成端到端联调测试

目标：验证前端/Agent/RAG 的端到端输出契约。

修改文件：`services/ai-service/app/routers/AImodel/service.py`、`services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- `_AgentVisibleStreamFilter`：在 SSE delta 和最终 answer 聚合前移除普通文本和跨流片段形式的内部 RAG 标识。
- `stream_chat_events()`：复用同一清洗结果输出前端 delta，并持久化清洗后的 assistant answer。
- `test_stream_chat_hides_rag_tool_payload_and_internal_ids_from_frontend()`：验证端到端流式输出不会泄漏 RAG tool JSON、chunk id、trace id 或 tool_results。

验收标准：前端/Agent 响应不暴露 tool result、原始工具 JSON、chunk id 或 trace id。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\tests\test_aimodel_rag_tool.py -v`；`uv run --project services/ai-service/rag pytest services\ai-service\tests\test_aimodel_rag_tool.py services\ai-service\tests\test_aimodel_agent.py -v`；`uv run --project services/ai-service/rag ruff check services\ai-service\app\routers\AImodel\service.py services\ai-service\tests\test_aimodel_rag_tool.py`

##### H7：优化 AImodel MCP 长连接

目标：将 AImodel 侧 RAG MCP client 从“每次查询打开一次 stdio MCP session”升级为“进程级长期复用一个 RAG MCP 子进程和一个 `ClientSession`”。

修改文件：`services/ai-service/app/routers/AImodel/tools.py`、`services/ai-service/app/main.py`、`services/ai-service/tests/test_aimodel_rag_tool.py`、`services/ai-service/Dockerfile`、`docker-compose.yml`、`services/ai-service/rag/src/mcp_server/server.py`、`services/ai-service/rag/tests/unit/test_mcp_tools.py`

实现类/函数：

- `PersistentMcpRagKnowledgeClient`：维护后台事件循环线程、stdio MCP session、启动锁和关闭逻辑。
- `PersistentMcpRagKnowledgeClient.query_knowledge_hub()`：同步工具入口复用同一个 MCP session 调用 `query_knowledge_hub`。
- `PersistentMcpRagKnowledgeClient.close()`：显式关闭 MCP session、stdio 资源和后台事件循环。
- `get_rag_knowledge_client()`：返回进程级 persistent client，保留 `RagKnowledgeClient` 抽象边界。
- `close_aimodel_rag_client()`：FastAPI shutdown hook，释放进程级 persistent MCP client。
- `QueryKnowledgeHubTool.query_knowledge_hub()`：接收可选 `request_source`，默认记录为 `mcp`。
- `QueryRuntime.execute()`：将调用来源写入 query trace，CLI 显式传入 `query_cli`。
- `services/ai-service/Dockerfile`：将 RAG 子项目打包进 ai-service 镜像并安装 RAG 运行依赖。
- `RAG_MCP_COMMAND=python`：容器内直接通过 Python 模块入口启动 MCP server，避免依赖 `uv` 二次启动。
- `_default_env_paths()`：兼容 `/app/rag` 这类独立 Docker 部署浅路径。

验收标准：同一个 client 连续两次 `query_knowledge_hub()` 只初始化一次 MCP session；调用 `close()` 后再次查询会重新创建 session；未创建过进程级 client 时 shutdown 不会创建新 MCP 资源；session 启动失败时不会残留后台事件循环线程；`search_shopping_guides()` 仍只返回公共 RAG 字段；AImodel 触发的 query trace 记录 `request_source=aimodel`，直接 MCP 工具调用记录 `request_source=mcp`，CLI 脚本记录 `request_source=query_cli`；单元测试不得启动真实 RAG 子进程；Docker 启动的 `ai-service` 容器内存在 `/app/rag`，并可通过 AImodel 前端代理请求触发 RAG query trace。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\tests\test_aimodel_rag_tool.py -v`；`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_mcp_tools.py services\ai-service\rag\tests\unit\test_retrieval.py services\ai-service\tests\test_aimodel_rag_tool.py -q`；`uv run --project services/ai-service/rag ruff check services\ai-service\app\routers\AImodel\tools.py services\ai-service\app\main.py services\ai-service\tests\test_aimodel_rag_tool.py services\ai-service\rag\src\mcp_server\server.py services\ai-service\rag\src\mcp_server\tools.py services\ai-service\rag\src\scripts\query.py services\ai-service\rag\tests\unit\test_mcp_tools.py services\ai-service\rag\tests\unit\test_retrieval.py`
