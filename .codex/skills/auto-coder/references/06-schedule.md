<!-- synced-from: services\ai-service\rag\DEV_SPEC.md -->
<!-- reference: 任务计划与状态 -->

## 6. 项目排期

### 6.1 阶段预览表

状态标记说明：`[ ]` 表示未开始，`[~]` 表示进行中，`[✔]` 表示已完成。

| 阶段 | 阶段标题 | 目标 | 状态 |
| --- | --- | --- | --- |
| Phase A | 配置与项目骨架 | 独立模块基础文件、uv 依赖锁定、Docker 部署骨架、pytest 冒烟测试、配置模板、prompt 配置、核心类型和配置加载 | [✔] |
| Phase B | 数据持久化与可插拔组件 | PostgreSQL/pgvector schema、repository、文档生命周期管理和 libs 可插拔实现 | [✔] |
| Phase C | Ingestion & Indexing Pipeline | 先去重的数据摄取、Loader、PDF -> Markdown、Splitter、Transform、ImageCaptioner、content_hash 差量、Dense/BM25Indexer 双路索引、pgvector upsert、统一 Pipeline MVP 和 `ingest.py` 脚本入口 | [~] |
| Phase D | Retrieval | Query Processor、Dense Route、Sparse Route、RRF Fusion、HybridSearch、Rerank 前候选过滤、Rerank、Response Builder 和 query.py 脚本入口 | [ ] |
| Phase E | MCP 工具服务 | MCP Server 和 `query_knowledge_hub`、`list_collections`、`get_document_summary` tools 暴露 | [ ] |
| Phase F | 可观测与管理平台 | TraceContext、结构化日志、ingestion/query 链路打点、Dashboard services、六大 Streamlit 页面和页面测试 | [ ] |
| Phase G | 质量评估体系 | 黄金测试集、Ragas、自定义指标、策略对比和评估趋势 | [ ] |
| Phase H | AImodel 联调集成 | 集成前验收门禁、AImodel RAG 工具适配、商品 API 协同、前端/Agent 联调和端到端测试 | [ ] |

### 6.2 交付里程碑

每完成一个阶段后，必须维护该阶段的交付里程碑。里程碑不是重复任务列表，而是面向后续开发者、面试官和项目讲解者说明：**当前项目已经走到哪里、已经有哪些可用功能、下一阶段从哪里继续**。

维护要求：

- **项目当前位置**：说明当前阶段完成后，RAG 系统处于什么能力状态。
- **可用功能**：列出此时已经可以实际运行、测试或演示的功能。
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
| Phase B | 数据持久化与可插拔组件 | 持久化、可插拔组件契约和首批真实 Provider 已就绪，可进入 Ingestion Pipeline 开发 | PostgreSQL/pgvector schema、Repository、文档生命周期、Loader/Splitter/LLM/Embedding/VectorStore/Reranker/Evaluator Factory、BaseTransform、DeepSeek、OpenAI Embedding、PgVectorStore 与 fake 测试实现 | `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests -q` | 2026-06-06 |
| Phase C | Ingestion & Indexing Pipeline | 未完成 | 暂无 | 暂无 |  |
| Phase D | Retrieval | 未完成 | 暂无 | 暂无 |  |
| Phase E | MCP 工具服务 | 未完成 | 暂无 | 暂无 |  |
| Phase F | 可观测与管理平台 | 未完成 | 暂无 | 暂无 |  |
| Phase G | 质量评估体系 | 未完成 | 暂无 | 暂无 |  |
| Phase H | AImodel 联调集成 | 未完成 | 暂无 | 暂无 |  |

#### 阶段 A 交付里程碑：配置与项目骨架

完成日期：2026-06-06

项目当前位置：

RAG 已形成使用 uv 锁定依赖、可独立安装、测试和构建 Docker 镜像的 Python 子模块。统一配置、Prompt、核心数据对象和异常边界已经稳定，后续阶段可以直接围绕这些契约实现 PostgreSQL 持久化、Provider Factory 和业务 Pipeline。

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

RAG 已具备 PostgreSQL/pgvector 持久化基础、完整 Repository 边界和八类可插拔组件包。配置可以创建百炼 DeepSeek、OpenAI Embedding、PgVectorStore 以及不访问外部服务的 fake 实现，阶段 C 可以直接编排离线摄取链路。

可用功能：

- 初始化 PostgreSQL/pgvector schema，并通过连接池执行事务和健康检查。
- 持久化 collection、document、chunk、image、trace 和 evaluation 数据。
- 管理文档生命周期，删除文档时同步清理关联 chunks 和 images。
- 通过空注册表、`register_builtin_providers()` 和 Factory 创建 Loader、Splitter、LLM、Embedding、VectorStore、Reranker、Evaluator。
- 保留 `BaseTransform` 抽象接口，具体 Transform 在阶段 C 的 ingestion pipeline 中串行实现。
- 通过百炼 OpenAI-compatible endpoint 调用 `deepseek-v4-flash`。
- 批量调用 `text-embedding-3-small` 并保持输入输出顺序。
- 为已持久化 chunk 写入 pgvector，执行 cosine search、metadata filter 和按 chunk_id 顺序回表。
- 使用 fake provider 和 RRF 顺序 fallback 执行无外部依赖测试。

验证方式：

- `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests -q`
- `$env:RUN_RAG_EXTERNAL_TESTS='1'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests/external/test_model_providers.py -v`

下一阶段入口：

阶段 C 复用 `DocumentRepository`、`ChunkRepository`、`ImageStorage`、`LoaderFactory`、`SplitterFactory`、`BaseTransform`、`EmbeddingFactory` 和 `VectorStoreFactory`，实现 dedup -> load -> split -> transform -> encode -> upsert 的完整 Ingestion Pipeline。Transform 由 `src/ingestion/transform/TransformPipeline` 串行编排，不创建独立工厂。

### 6.3 阶段任务跟踪表

任务拆分原则：

- 每个子任务都应尽量控制为 **45-75 分钟** 可完成、可验收的增量，避免过薄的纯占位任务，也避免一次覆盖多个模块的厚重任务。
- 每个子任务默认都包含 **TDD 流程**：先写对应 pytest 单元测试或冒烟测试，再实现最小代码让测试通过。
- 若某个任务需要数据库、LLM 或外部模型，应优先使用 fake provider、mock 或测试容器，真实外部调用使用 pytest marker 隔离。

#### 阶段 A：配置与项目骨架

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| A1 | 创建独立模块基础文件 | [✔] | 2026-06-06 | 已创建独立模块说明、项目元数据、依赖声明、pytest 配置、忽略规则和基础包入口 |
| A2 | 创建独立运行入口、Docker 骨架和 pytest 冒烟测试 | [✔] | 2026-06-06 | 已创建最小运行入口、健康状态、Docker 骨架、六个关键包入口，4 个冒烟测试通过 |
| A3 | 创建 `config/settings.example.yaml` 示例配置 | [✔] | 2026-06-06 | 已覆盖全部可插拔组件、流水线、存储、可观测、Dashboard、评估和 MCP 配置，5 个单元测试通过；C4 将运行时 settings 与版本化模板分离 |
| A4 | 创建 prompt 配置目录 | [✔] | 2026-06-06 | 已创建统一英文 Prompt YAML 契约，覆盖 rerank、chunk rewrite、六类图片理解策略和中文 caption 输出，10 个配置测试通过 |
| A5 | 实现配置读取和校验 | [✔] | 2026-06-06 | 已实现完整 `RagSettings`、Provider/model selector、活动环境变量、Embedding/pgvector 维度、检索参数和 Prompt 占位符校验，18 个配置测试通过 |
| A6 | 定义核心类型和统一异常 | [✔] | 2026-06-06 | 已实现 Document、ImageMetadata、Chunk、RetrievalResult 及六类 RagError 子类，覆盖必填位置、非空文本、来源区间和异常链校验，16 个类型测试通过 |
| A7 | 迁移至 uv 包管理与锁定环境 | [✔] | 2026-06-06 | 已生成 183 包锁文件，创建 111 包开发环境，统一 README/auto-coder/DEV_SPEC 命令，Docker frozen build 与运行通过；6 个冒烟测试、106 个全量测试通过 |

#### 阶段 B：数据持久化与可插拔组件

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| B1 | 编写 collection/document/chunk schema | [✔] | 2026-06-06 | 已实现稳定字符串 ID、pgvector/HNSW、核心约束和索引；真实 PostgreSQL 连续初始化两次通过，5 个集成测试通过 |
| B2 | 编写 image/trace/evaluation schema | [✔] | 2026-06-06 | 已实现图片索引、四段式 Query/Ingestion Trace、评估任务和指标结果表；真实 PostgreSQL 幂等初始化通过，8 个集成测试通过 |
| B3 | 实现数据库连接池和 schema 初始化 | [✔] | 2026-06-06 | 已实现配置驱动惰性连接池、生命周期、健康检查、事务回滚和幂等 schema 初始化；15 个集成测试通过 |
| B4 | 实现 Document/Chunk/Image Repository | [✔] | 2026-06-06 | 已实现 collection 自动创建、文档版本替换、Chunk 批量 upsert、图片安全落盘和索引查询；19 个集成测试通过 |
| B5 | 实现 Trace/Evaluation Repository | [✔] | 2026-06-06 | 已实现 Query/Ingestion Trace 与评估任务/指标的不可变记录、幂等 upsert 和历史查询；21 个集成测试通过 |
| B6 | 实现文档生命周期管理 | [✔] | 2026-06-06 | 已实现 `lifecycle_status` schema、状态流转、retrievable 查询过滤和 deleted 清理 chunks/images；23 个集成测试通过 |
| B7 | 建立 libs 可插拔组件包结构 | [✔] | 2026-06-06 | 已创建八个 libs 可插拔组件包和稳定导入契约；2 个单元测试通过 |
| B8 | 实现 Loader/Splitter libs 基类、factory 和 DocumentChunker 契约 | [✔] | 2026-06-06 | 已实现 loader/splitter 基类、注册表工厂、fake/markdown/pdf loader、fake/recursive splitter 和 DocumentChunker 契约；9 个指定单元测试通过 |
| B9 | 实现 LLM/Embedding libs 基类、factory 和 fake 实现 | [✔] | 2026-06-06 | 已实现 BaseLLM/LLMFactory/FakeLLM 与 BaseEmbedding/EmbeddingFactory/FakeEmbedding，统一 `chat()`、`embed()`、`embed_batch()`；10 个指定单元测试通过 |
| B10 | 实现 VectorStore/Reranker/Evaluator libs 基类、factory 和 fake 实现 | [✔] | 2026-06-06 | 已实现三类最小接口、注册表工厂、固定维度 fake vector store、确定性 fake 和 RRF 顺序回退；17 个指定单元测试通过 |
| B11 | 实现首批真实组件最小适配 | [✔] | 2026-06-06 | 已实现百炼 DeepSeek、OpenAI text-embedding-3-small 和 PgVectorStore；22 个 factory 单元测试、1 个 pgvector 集成测试通过，2 个 external smoke test 默认跳过 |

#### 阶段 C：Ingestion & Indexing Pipeline

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| C1 | 实现文档 SHA256 去重与 skipped 快速结束 | [✔] | 2026-06-06 | 已实现流式 SHA256、success 文档去重查询、force 绕过、Loader 前短路和 skipped ingestion trace；5 个单元测试、1 个 PostgreSQL 集成测试通过 |
| C2 | 实现文档加载、Markdown 标准化与图片引用提取 | [✔] | 2026-06-06 | 已实现 canonical Markdown、fenced-code 感知标题与图片解析、安全本地 Markdown 图片引用、MarkItDown/PyMuPDF PDF 转换、xref 去重、失败写入清理、稳定图片占位符与 metadata；11 个新增单元测试及真实文本/图片 PDF 冒烟测试通过 |
| C3 | 实现 DocumentChunker、稳定 chunk_id 与引用保留验证 | [✔] | 2026-06-06 | 已实现独立稳定 chunk ID、heading offset、section_path 分发、metadata 深拷贝、chunk_index、source_ref、image_refs 和 SplitterStep；24 个相关单元测试、111 个全量测试通过 |
| C4 | 实现 Transform 抽象基类与具体实现 | [✔] | 2026-06-06 | 已分离本地 settings 与版本化模板，保留 BaseTransform，新增 ingestion TransformPipeline、metadata/rewrite/semantic merge/denoise 串行实现、英文 merge Prompt、噪声 fixture 和幂等测试；49 个相关测试、120 个全量测试通过，2 个 external smoke test 默认跳过 |
| C5 | 实现 ImageCaptioner | [✔] | 2026-06-06 | 已实现 ImageCaptioner、ImageToTextTransform、image_to_text transform step、skipped/failed/low_quality 状态和 caption metadata；34 个相关测试、125 个全量测试通过，2 个 external smoke test 默认跳过 |
| C6 | 实现 DenseEncoder | [✔] | 2026-06-06 | 已实现 DenseEncodingResult、DenseEncoder、EmbeddingStep.run_dense、content_hash 差量跳过、当前运行去重、有限向量校验和单 chunk 向量生成；6 个相关测试、131 个全量测试通过，2 个 external smoke test 默认跳过 |
| C7 | 实现 BM25Indexer | [✔] | 2026-06-07 | 已实现 BM25Candidate、BM25IndexResult、BM25Indexer.index/query、词频统计、倒排索引、关键词 Top-k 排序、中文连续文本 n-gram fallback 和重复 index 状态重建；6 个相关测试、137 个全量测试通过，2 个 external smoke test 默认跳过 |
| C8 | 实现 BatchProcessor 批处理优化 | [✔] | 2026-06-07 | 已实现 BatchProcessor、BatchRunResult、BatchSuccess、BatchFailure、DenseEncoder.encode_batch、batch_size 拆分、throttle_seconds 节流、有限 retry、失败隔离、EmbeddingStep.run_batch、Dense/BM25 批处理编排；20 个相关测试、145 个全量测试通过，2 个 external smoke test 默认跳过 |
| C9 | 实现 pgvector upsert | [ ] |  | 同一 chunk 两次 upsert 产生相同 id；内容变更 id 变更；支持批量 upsert 且保持顺序 |
| C10 | 实现统一 Pipeline MVP 编排和集成测试 | [ ] |  | 串联摄取、ImageCaptioner、content_hash、Dense、BM25Indexer、batch、upsert，验证最小可运行索引链路 |
| C11 | 新增 `ingest.py` 摄取脚本入口 | [ ] |  | 调用 pipeline，支持 `--collection`、`--path`、`--force` |

#### 阶段 D：Retrieval

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| D1 | 实现 Query Processor | [ ] |  | query 标准化、意图识别、可选 query rewrite |
| D2 | 实现 Dense Route 向量检索 | [ ] |  | 输入 query/ProcessedQuery，完成 Query Embedding、pgvector 检索并返回 `RetrievalResult` |
| D3 | 实现 Sparse Route BM25 回表检索 | [ ] |  | `ProcessedQuery.keywords -> bm25_indexer.query -> chunk_ids -> vector_store.get_by_ids` |
| D4 | 实现 RRF Fusion | [ ] |  | 基于排名倒数融合，不比较两路分数 |
| D5 | 实现 HybridSearch 编排 | [ ] |  | 依赖 D1/D2/D3/D4，集成候选去重、双路召回、融合和降级 |
| D6 | 实现 Rerank 前候选过滤 | [ ] |  | 在进入 Reranker 前按 `collection`、`doc_type` 等参数过滤候选 |
| D7 | 实现 Cross-Encoder Reranker 适配 | [ ] |  |  |
| D8 | 实现 LLM Rerank 适配 | [ ] |  |  |
| D9 | 实现 rerank fallback | [ ] |  | 不可用、超时、异常时回退过滤后的 RRF 结果 |
| D10 | 实现引用构造 | [ ] |  | 来源标题、章节、路径、trace_id |
| D11 | 实现多模态 Response Builder | [ ] |  | 组装 chunk 关联图片，隐藏内部工具 JSON |
| D12 | 新增 `query.py` 脚本入口 | [ ] |  | 调用完整 `hybridsearch + filter + rerank`，支持 query/top-k/collection/verbose/no-rerank |
| D13 | 实现 Retrieval 单元测试矩阵 | [ ] |  | Query Processor、Dense、Sparse、RRF、HybridSearch、Filter、Rerank、Response、query.py |
| D14 | 实现 Retrieval 集成测试 | [ ] |  | 覆盖 Dense/BM25/Hybrid/Filter/Rerank/fallback/query.py |

#### 阶段 E：MCP 工具服务

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| E1 | 搭建 MCP Server | [ ] |  |  |
| E2 | 暴露 `query_knowledge_hub` | [ ] |  |  |
| E3 | 暴露 `list_collections` 和 `get_document_summary` | [ ] |  |  |
| E4 | 完成 MCP tools 测试 | [ ] |  |  |

#### 阶段 F：可观测与管理平台

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| F1 | 实现 TraceContext 和 TraceController | [ ] |  |  |
| F2 | 实现 ingestion trace 数据结构 | [ ] |  | 基础信息、阶段详情、汇总指标、评估指标 |
| F3 | 实现 query trace 数据结构 | [ ] |  | Dense/BM25、fusion、filter、rerank 前后变化 |
| F4 | 实现 Python logging + JSONFormatter | [ ] |  | 追加写入 `src/logs/traces.jsonl` |
| F5 | 将 Trace 打点注入 ingestion 和 query 链路 | [ ] |  | 每个 pipeline 阶段调用 `record_stage()`，结束时 flush |
| F6 | 实现配置读取和数据浏览服务 | [ ] |  | Dashboard services，配套单元测试 |
| F7 | 实现 Trace 读取和评估服务 | [ ] |  | Dashboard services，配套单元测试 |
| F8 | 实现系统总览与 Ingestion 管理页面 | [ ] |  | Streamlit 页面可启动 |
| F9 | 实现数据浏览器与 Query Trace 页面 | [ ] |  | 文档、chunk、召回对比、rerank 变化 |
| F10 | 实现 Ingestion Trace 与评估面板页面 | [ ] |  | 阶段耗时、评估趋势 |
| F11 | 实现 Dashboard 启动脚本和冒烟测试 | [ ] |  | `src/scripts/run_dashboard.py` |
| F12 | 完成 Dashboard 六大页面测试 | [ ] |  | 系统总览、Ingestion 管理、数据浏览器、Query Trace、Ingestion Trace、评估面板 |

#### 阶段 G：质量评估体系

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| G1 | 准备黄金测试集格式 | [ ] |  |  |
| G2 | 实现自定义检索指标 | [ ] |  | Hit Rate@K、MRR、NDCG |
| G3 | 接入 Ragas 生成指标 | [ ] |  | Faithfulness、Answer Relevancy |
| G4 | 实现策略对比评估 | [ ] |  | Hybrid、Dense-only、Sparse-only、Rerank 对比 |
| G5 | 实现评估历史趋势展示 | [ ] |  | Dashboard 评估面板 |

#### 阶段 H：AImodel 联调集成

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| H1 | 执行 AImodel 集成前验收门禁 | [ ] |  | Dashboard 六大页面测试和 RAG 全链路 E2E 通过后才进入集成 |
| H2 | 实现 AImodel RAG 工具适配 | [ ] |  |  |
| H3 | 将 RAG 工具接入 Agent 工具列表 | [ ] |  |  |
| H4 | 验证商品 API 工具与 RAG 工具协同 | [ ] |  | 商品事实走 API，知识补充走 RAG |
| H5 | 验证简单询问和商品链接场景 | [ ] |  | 推荐、对比、选购指南、政策 FAQ |
| H6 | 完成前后端联调和端到端测试 | [ ] |  | AImodel 工具响应不暴露 tool result |

### 6.4 总体进度表

| 阶段 | 总任务数 | 已完成 | 进度 |
| --- | ---: | ---: | --- |
| Phase A | 7 | 7 | 100% |
| Phase B | 11 | 11 | 100% |
| Phase C | 11 | 8 | 73% |
| Phase D | 14 | 0 | 0% |
| Phase E | 4 | 0 | 0% |
| Phase F | 12 | 0 | 0% |
| Phase G | 5 | 0 | 0% |
| Phase H | 6 | 0 | 0% |
| **总计** | **70** | **26** | **37%** |

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

修改文件：`config/prompts/rerank_prompt.yaml`、`config/prompts/rewrite_chunk_prompt.yaml`、`config/prompts/image_to_text_prompt.yaml`

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
- `Chunk(id,text,chunk_index,start_offset,end_offset,source_ref)`：定义核心数据契约
- `ImageMetadata`：定义核心数据契约
- `RetrievalResult`：定义流程返回结果
- `RagError`：定义 RAG 子系统统一异常基类

验收标准：`Document.metadata.images[]` 支持 `id/path/page/text_offset/text_length/position`；`Chunk` 支持 `start_offset`、`end_offset` 和可选 `source_ref`；类型可被 Ingestion、Retrieval、Trace 复用。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_types.py -v`

##### A7：迁移至 uv 包管理与锁定环境

目标：使用 uv 统一 RAG 独立模块的依赖解析、虚拟环境创建、锁文件、测试命令和 Docker 安装流程，消除系统 Python 与项目依赖状态不一致的问题。

修改文件：`pyproject.toml`、`uv.lock`、`Dockerfile`、`.dockerignore`、`.gitignore`、`README.md`、`tests/test_smoke.py`、`.codex/skills/auto-coder/SKILL.md`、`DEV_SPEC.md`

实现类/函数：

- `uv.lock`：锁定生产依赖和可选开发依赖的完整版本与来源
- `Dockerfile`：使用固定版本 uv 和 `uv sync --frozen --no-dev` 构建独立运行环境
- `README.md`：记录 `uv sync --extra dev`、`uv run pytest`、`uv run ruff` 和本地运行命令
- `test_uv_project_contract()`：验证锁文件、Python 版本、开发 extra 和 uv 项目配置
- `test_docker_skeleton_uses_uv()`：验证 Docker 不再使用 pip，并通过 frozen lock 安装生产依赖
- `auto-coder/SKILL.md`：所有 Python、pytest、Ruff 和规格同步命令通过 `uv run --project services/ai-service/rag` 执行，不再手工激活 `.venv`

验收标准：`uv lock --check` 通过；`uv sync --extra dev --frozen` 创建项目 `.venv`；所有测试和 Ruff 通过 `uv run` 执行；Dockerfile 复制 `pyproject.toml` 与 `uv.lock` 并使用固定版本 uv frozen sync；README 和 auto-coder 不再要求手工激活虚拟环境或使用 pip 安装项目依赖；`AGENTS.md` 等无关 dirty 文件不纳入任务。

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

- `PostgresPool.from_settings()`：从 `settings.database.url_env` 读取 DSN，并按 `pool_size` 创建惰性连接池
- `PostgresPool.open()`：启动连接池并等待最小连接可用
- `PostgresPool.close()`：关闭连接池
- `PostgresPool.connection()`：提供自动归还连接的上下文
- `PostgresPool.transaction()`：提供提交/回滚事务上下文
- `PostgresPool.health_check()`：执行轻量数据库可用性检查
- `init_schema()`：读取并以事务方式执行 `schema.sql`

验收标准：连接池完全由 `DatabaseSettings` 和环境变量驱动，不在源码中
硬编码 DSN；可完成打开、健康检查、连接借用、事务提交/回滚和关闭；
`init_schema()` 可重复执行；配置缺失、连接失败、SQL 文件缺失或 SQL
执行失败时抛出带安全上下文和原始 cause 的 `DatabaseError` 或
`ConfigurationError`。

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

验收标准：Query/Ingestion Trace 可从 running 状态幂等更新为完成状态，四段式
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
- `LoaderFactory.create()`：根据配置或 provider 创建实现，内部自动确保内置实现已注册
- `BaseSplitter.split(text) -> List[str]`：定义输入输出契约
- `SplitterFactory.register_builtin_providers()`：一次性注入 fake/recursive_character 内置实现
- `SplitterFactory.create()`：根据配置或 provider 创建实现，内部自动确保内置实现已注册
- `DocumentChunker.chunk(document) -> List[Chunk]`：定义输入输出契约

验收标准：可创建 fake/markdown/pdf loader 和 splitter；`libs.splitter` 只接收文本并返回 `List[str]`；`DocumentChunker` 契约测试覆盖 `chunk_id`、metadata 继承、`chunk_index`、`source_ref`、图片引用分发，以及 `List[str] -> List[Chunk]` 类型转换。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_factories.py services\ai-service\rag\tests\unit\test_splitter.py -v`

##### B9：实现 LLM/Embedding 抽象和工厂

目标：统一 LLM 与 Embedding 调用接口，支持 fake provider 测试。

修改文件：`src/libs/llm/*`、`src/libs/embedding/*`、`tests/unit/test_factories.py`

实现类/函数：

- `BaseLLM`：定义最小抽象接口
- `LLMFactory.register_builtin_providers()`：一次性注入 fake 内置实现，真实 provider 在 B11 注册
- `LLMFactory.create()`：根据配置或 provider 创建实现，内部自动确保内置实现已注册
- `BaseEmbedding`：定义最小抽象接口
- `EmbeddingFactory.register_builtin_providers()`：一次性注入 fake 内置实现，真实 provider 在 B11 注册
- `EmbeddingFactory.create()`：根据配置或 provider 创建实现，内部自动确保内置实现已注册

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
- `OpenAIEmbedding.embed()`：复用批量接口生成单条 `text-embedding-3-small` 向量
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

目标：将输入文档转换为标准 `Document(id, text, metadata)`；完成 PDF -> Markdown、Markdown 标准化、标题层级 metadata 提取；若文档存在图片，则执行图片提取、生成 `image_id`、写入图片占位符，并填充 `metadata.images[]`。

修改文件：`pyproject.toml`、`src/ingestion/pdf_to_markdown.py`、`src/libs/loader/markdown_loader.py`、`src/libs/loader/pdf_loader.py`、`tests/unit/test_loader.py`

实现类/函数：

- `MarkItDownConverter`：将 PDF 转换为 canonical Markdown
- `MarkdownLoader.load()`：加载 Markdown 并提取标题层级与 metadata
- `PdfLoader.load()`：加载 PDF 并输出标准 Document
- `extract_images()`：使用 PyMuPDF 仅在 PDF 存在图片时抽取图片字节、页码与物理位置信息

验收标准：PDF 使用 MarkItDown 转换为 canonical Markdown，并由独立的 PyMuPDF 图片提取边界补充图片字节、页码和物理位置；同一页面重复出现的 PyMuPDF xref 只解析一次，但保留该 xref 的多个物理位置；多图片写入中途失败时清理当前临时文件和本次已写文件，不遗留无 Document 对应的孤儿资源；Markdown 可输出标准 `Document(id + text + metadata)` 并提取标题层级，fenced code block 内的标题和图片示例不得被业务解析器改写；Markdown 本地图片只能读取源文档目录及其子目录，父目录穿越或远程地址保留原语法且不生成 metadata；无图片文档不生成无效图片 metadata；有图片文档生成稳定 `image_id`、`[[image:image_id]]` 占位符和 `metadata.images[]`；转换器和图片提取器支持依赖注入，单元测试不得依赖真实 PDF 解析包。

测试方法：`uv run --project services/ai-service/rag pytest -p no:cacheprovider services\ai-service\rag\tests\unit\test_loader.py -v`；单元测试通过注入 fake MarkItDown converter 和 fake PyMuPDF module 验证转换与图片提取契约，不依赖真实外部解析环境。

##### C3：实现 DocumentChunker、稳定 chunk_id 与引用保留验证

目标：把 `libs.splitter` 输出的 `List[str]` 转换为符合 `core.types` 契约的 `List[Chunk]`，使用独立且稳定的 chunk ID 规则，并验证标题层级、offset 和图片引用不会在业务适配中丢失。

修改文件：`src/libs/loader/markdown_loader.py`、`src/ingestion/chunk/document_chunker.py`、`src/ingestion/chunk/splitter_step.py`、`src/ingestion/chunk/chunk_id.py`、`tests/unit/test_loader.py`、`tests/unit/test_splitter.py`

实现类/函数：

- `DocumentChunker.chunk()`：将 Document 转换为带业务 metadata 的 Chunk 列表
- `build_chunk_id()`：根据 `source_path + section_path + content_hash` 生成稳定 chunk 标识
- `build_source_ref()`：建立 chunk 到来源文档的引用
- `extract_heading_hierarchy()`：为标题层级 metadata 补充源文本 `text_offset`
- `attach_section_path()`：根据标题 offset 将当前标题层级写入 chunk metadata
- `distribute_image_refs()`：根据图片占位符 offset 分发图片引用

验收标准：Loader 的每个 heading metadata 包含 canonical `Document.text` 中的起始 offset；同来源、同章节、同内容生成相同 `chunk_id`，来源、章节或内容变化时 ID 发生变化；每个 chunk 都通过独立 `build_chunk_id()` 规则生成 ID；`Document.metadata` 被复制到 `Chunk.metadata`；按顺序添加 `chunk_index`；根据文档来源建立 `source_ref`；chunk metadata 根据 heading offset 包含当前 chunk 对应的 `section_path` 和按需分发的 `image_refs`；没有图片的 chunk 不添加无效引用；完成 `List[str] -> List[Chunk]` 类型转换。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_splitter.py -v`

##### C4：实现 Transform 抽象基类与具体实现

目标：集中实现 Transform 阶段的抽象契约、具体能力和 ingestion 串行编排，包括 metadata 注入、LLM chunk rewrite、智能合并和去噪；Transform 不使用 factory/provider 模式，摄取流水线必须根据 `settings.transform.steps` 按顺序执行 enabled step。

修改文件：`.gitignore`、`README.md`、`config/settings.example.yaml`、`src/core/config.py`、`src/libs/transform/base_transform.py`、`src/ingestion/transform/transformer.py`、`src/ingestion/transform/metadata_enricher.py`、`src/ingestion/transform/chunk_rewriter.py`、`src/ingestion/transform/semantic_merge_transform.py`、`src/ingestion/transform/denoise_transform.py`、`tests/fixtures/noisy_documents/`、`tests/unit/test_config.py`、`tests/unit/test_transformer.py`

实现类/函数：

- `BaseTransform.transform()`：定义 Transform 最小抽象契约
- `TransformPipeline.from_settings()`：从 `settings.transform.steps` 构建 enabled step 链路
- `TransformPipeline.run()`：按配置顺序串行执行 Transform
- `MetadataEnricher.transform()`：注入标题路径、来源、文档主题等上下文 metadata
- `ChunkRewriter.transform()`：利用 LLM 重写 chunk，使片段语义更完整
- `SemanticMergeTransform.transform()`：合并逻辑相关但被物理切开的相邻 chunk
- `DenoiseTransform.transform()`：清理空白、页眉页脚、目录和解析残留噪声

验收标准：运行时 `config/settings.yaml` 被 Git 忽略，仓库提交 `config/settings.example.yaml` 作为完整模板；`settings.transform.steps` 只描述步骤顺序、启用状态和 prompt_path，不包含 provider；`src.libs.transform` 只暴露 `BaseTransform`；具体 Transform 位于 `src/ingestion/transform/`；chunk 包含标题、来源、主题上下文；fake LLM 下可 rewrite；逻辑相关 chunk 可合并且 metadata 不丢失；页眉页脚、目录和解析残留可清理。

补充要求：执行该任务时必须在 `settings.example.yaml` 和本地 `settings.yaml` 中配置真实启用的 Transform steps 链路，测试不能只依赖 fake transform；需要创建典型噪声场景 fixture，例如连续空白、页眉页脚、重复目录、页码水印、PDF 解析断行、无意义符号残留和图片占位符附近噪声。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_transformer.py -v`

##### C5：实现 ImageCaptioner

目标：当 `vision_llm.enabled=true` 且 chunk metadata 中存在 `image_refs` 时，为关联图片生成 caption，并将 caption 写入 chunk metadata；未启用 Vision LLM 或没有 `image_refs` 时必须安全跳过。

修改文件：`src/ingestion/transform/image_captioner.py`、`src/ingestion/transform/image_to_text_transform.py`、`config/settings.example.yaml`、`tests/unit/test_transformer.py`

实现类/函数：

- `ImageCaptioner.caption()`：读取 chunk 的 `image_refs` 并生成图片描述
- `ImageCaptioner.should_caption()`：判断是否满足 `vision_llm.enabled=true` 且存在 `image_refs`
- `ImageCaptioner.write_metadata()`：将 `image_caption_status` 和 `image_captions` 写入 chunk metadata
- `ImageToTextTransform.transform()`：调用 Vision LLM 生成图片描述并返回结构化 caption 结果

验收标准：启用 `vision_llm` 且存在 `image_refs` 时会生成 caption 并写入 chunk metadata；未启用 `vision_llm` 时不调用 Vision LLM，并写入 skipped 状态；没有 `image_refs` 的 chunk 不生成 caption；Vision LLM 失败时写入 failed/low_quality 状态并保留原 chunk；caption 可被后续 DenseEncoder 和 BM25Indexer 使用。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_transformer.py -v`

##### C6：实现 DenseEncoder

目标：将默认 embedding 模型适配、chunk `content_hash` 差量判断和 Dense 向量生成统一收敛到 `DenseEncoder`。

修改文件：`src/libs/embedding/openai_embedding.py`、`src/ingestion/embedding/dense_encoder.py`、`src/ingestion/embedding/embedding_step.py`、`tests/unit/test_embedding.py`

实现类/函数：

- `OpenAIEmbedding.embed()`：调用 `text-embedding-3-small` 生成单条文本向量
- `DenseEncoder.should_encode()`：基于 chunk `content_hash` 判断是否需要重新生成 Dense 向量
- `DenseEncoder.encode()`：生成单个 chunk 的 Dense 语义向量
- `EmbeddingStep.run_dense()`：编排 DenseEncoder 并输出待写入向量结果

验收标准：fake 默认可测，真实调用 marker 隔离；已存在 content_hash 不重复调用模型；新 chunk 可以生成 Dense 向量；DenseEncoder 不承担批处理职责。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_embedding.py -v`

##### C7：实现 BM25Indexer

目标：为 Sparse Route 构建 BM25 词项、词频和倒排索引数据。

修改文件：`src/ingestion/embedding/bm25_indexer.py`、`tests/unit/test_bm25.py`

实现类/函数：

- `BM25Indexer.index()`：生成 BM25 词项、词频和倒排索引数据
- `BM25Indexer.query()`：根据关键词返回候选 `chunk_id` 和 BM25 分数

验收标准：可为 chunk 构建 BM25 索引；可按关键词召回候选 chunk；索引结果可被 Sparse Route 复用。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_bm25.py -v`

##### C8：实现 BatchProcessor 批处理优化

目标：在 DenseEncoder 和 BM25Indexer 均完成后，提供统一批处理能力，处理批量输入、限流、重试和失败隔离。

修改文件：`src/ingestion/embedding/batch_processor.py`、`src/ingestion/embedding/embedding_step.py`、`tests/unit/test_embedding.py`、`tests/unit/test_bm25.py`

实现类/函数：

- `BatchProcessor.run()`：按配置批量执行编码或索引任务
- `BatchProcessor.retry_failed()`：对可重试失败执行有限重试
- `DenseEncoder.encode_batch()`：通过 `EmbeddingClient.embed_batch()` 批量生成 Dense 向量并保持顺序
- `EmbeddingStep.run_batch()`：编排 DenseEncoder 与 BM25Indexer 的批处理执行

验收标准：批处理大小受配置控制；Dense 和 BM25 两路都能复用 BatchProcessor；部分失败不影响其他 chunk；重试次数和失败记录可测试。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_embedding.py services\ai-service\rag\tests\unit\test_bm25.py -v`

##### C9：实现统一 upsert

目标：将文档、chunk、向量、BM25 和图片索引一致写入，并保证 upsert 幂等性和批量顺序。

修改文件：`src/ingestion/storage/upsert_step.py`、`src/storage/image_storage.py`、`tests/integration/test_ingestion_pipeline.py`

实现类/函数：

- `UpsertStep.run()`：统一写入 chunk、向量、BM25 和图片索引
- `ImageStorage.upsert_index()`：写入图片索引并保持数据库记录一致

验收标准：同一 chunk 两次 upsert 产生相同 id；chunk 内容变更时 id 随 `content_hash` 变更；支持批量 upsert 且返回结果保持输入顺序；文档、chunk、向量、BM25 和 `image_index` 记录一致写入。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_ingestion_pipeline.py -v`

##### C10：实现统一 Pipeline MVP 编排和集成测试

目标：在统一 `pipeline.py` 中把摄取结果、ImageCaptioner、DenseEncoder、BM25Indexer、BatchProcessor 和 upsert 串成最小可运行链路。

修改文件：`src/ingestion/pipeline.py`、`tests/integration/test_ingestion_pipeline.py`

实现类/函数：

- `IngestionPipeline.run_indexing()`：编排索引 MVP 子链路
- `IngestionPipeline.run()`：串联摄取与索引主链路
- `IngestionPipelineResult`：定义统一摄取与索引流程返回结果

验收标准：给定原始文档路径，可以完成去重、Loader、Splitter、Transform、ImageCaptioner 条件 caption、DenseEncoder 差量编码、BM25Indexer 索引、BatchProcessor 批处理和 upsert；重复执行时具备幂等性。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_ingestion_pipeline.py -v`

##### C11：新增 ingest.py 摄取脚本入口

目标：提供本地命令行入口，调用 Ingestion Pipeline 执行离线文档摄取。

修改文件：`src/scripts/ingest.py`、`tests/unit/test_loader.py`

实现类/函数：

- `parse_args()`：解析命令行参数
- `run_ingest_cli()`：执行本地摄取流程

验收标准：支持 `--collection` 指定 collection；支持 `--path` 指定待摄取文件或目录；支持 `--force` 强制重新摄取并绕过 SHA256 skipped 快速结束；参数缺失时返回可读错误。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_loader.py -v`
#### 阶段 D：Retrieval

##### D1：实现 query 预处理

目标：完成 query 标准化、意图识别和可选 rewrite。

修改文件：`src/core/query_engine/query_processor.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `QueryProcessor.process()`：处理输入并输出标准对象

验收标准：支持 normalize、意图识别、可选 rewrite。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D2：实现 Dense Route 向量检索

目标：输入用户 query 或 `ProcessedQuery`，完成 Query Embedding、pgvector 向量检索，并返回统一的 `RetrievalResult(chunk_id,text,score,metadata)`。

修改文件：`src/core/query_engine/dense_route.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `DenseRoute.search()`：执行 Query Embedding 和 pgvector 语义召回

验收标准：调用 `EmbeddingClient.embed(processed_query.normalized_query)`；调用 vector store 完成 Top-k 向量检索；返回结果字段统一为 `chunk_id`、`text`、`score`、`metadata`；空 query、embedding 失败、空结果都有可测试分支；trace details 记录 route、top_k、候选数量和耗时。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D3：实现 Sparse Route BM25 回表检索

目标：使用 `QueryProcessor.process()` 生成的 `ProcessedQuery.keywords` 进行 BM25 检索，再通过 chunk_id 回表读取 chunk 正文和 metadata。

修改文件：`src/core/query_engine/sparse_route.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `SparseRoute.search()`：执行 BM25 关键词召回并按 chunk_id 回表
- `BM25Indexer.query()`：执行索引查询
- `VectorStore.get_by_ids()`：按 ID 回表读取数据

验收标准：流程固定为 `keywords -> bm25_indexer.query(keywords, top_k) -> [{chunk_id, score}] -> vector_store.get_by_ids(chunk_ids) -> [{id, text, metadata}] -> List[RetrievalResult]`；keywords 为空时返回空结果并记录 skipped 原因；BM25 返回的 chunk_id 顺序应被保留；缺失 chunk_id 应被跳过并写入 trace details。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D4：实现 RRF Fusion

目标：融合 Dense/BM25 两路候选，避免直接比较不同分数。

修改文件：`src/core/query_engine/fusion.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `reciprocal_rank_fusion()`：按排名倒数融合 Dense/BM25 候选

验收标准：基于排名倒数融合，不比较分数。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D5：实现 HybridSearch 编排

目标：编排 Dense Route、Sparse Route 和 RRF Fusion，完成候选去重、双路召回融合和单路失败降级。

修改文件：`src/core/query_engine/hybrid_engine.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `HybridSearch.search()`：编排双路召回、候选去重和 RRF 融合
- `HybridSearchResult`：定义流程返回结果

验收标准：前置依赖为 D1、D2、D3、D4；输入 `ProcessedQuery`；分别执行 Dense/BM25 两路检索；按 `chunk_id` 去重并保留 `dense_rank`、`sparse_rank`、`dense_score`、`sparse_score`；调用 RRF Fusion 生成融合排序；单路失败时允许降级为另一条路线并写入 trace details。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D6：实现 Rerank 前候选过滤

目标：在 RRF Fusion 之后、Reranker 之前，根据调用参数过滤候选，避免把不符合限定条件的 chunk 送入重排阶段。

修改文件：`src/core/query_engine/hybrid_engine.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `CandidateFilter.apply()`：按参数过滤候选结果
- `HybridSearch.apply_metadata_filter()`：在进入 rerank 前执行 metadata 过滤

验收标准：支持 `collection`、`doc_type`、来源类型、文档状态、权限、生命周期状态等参数；过滤发生在 RRF Fusion 之后、Rerank 之前；`--collection` 等脚本参数复用同一过滤逻辑；过滤结果数量和过滤原因写入 trace details。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D7：实现 Cross-Encoder Reranker

目标：支持 Cross-Encoder 对过滤后的候选进行精排。

修改文件：`src/libs/reranker/cross_encoder_reranker.py`、`tests/unit/test_reranker.py`

实现类/函数：

- `CrossEncoderReranker.rerank()`：执行候选重排

验收标准：只接收过滤后的候选；可按 query-doc pair 重新排序。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_reranker.py -v`

##### D8：实现 LLM Rerank

目标：支持 LLM 对过滤后的候选进行重排。

修改文件：`src/libs/reranker/llm_reranker.py`、`tests/unit/test_reranker.py`

实现类/函数：

- `LLMReranker.rerank()`：执行候选重排

验收标准：只接收过滤后的候选；fake LLM 下可稳定排序。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_reranker.py -v`

##### D9：实现 rerank fallback

目标：在 reranker 不可用、超时或异常时回退到过滤后的 RRF 结果。

修改文件：`src/core/query_engine/reranker.py`、`tests/unit/test_reranker.py`

实现类/函数：

- `RerankController.rerank_or_fallback()`：执行 rerank 并在异常时回退过滤后的 RRF 结果

验收标准：超时、异常、不可用时回退过滤后的 RRF 排序；不会重新引入已被过滤的候选。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_reranker.py -v`

##### D10：实现引用构造

目标：为最终上下文构建可展示的引用来源。

修改文件：`src/core/response/citation_builder.py`、`tests/unit/test_response_builder.py`

实现类/函数：

- `CitationBuilder.build()`：构建输出对象

验收标准：输出来源标题、章节、路径、trace_id。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_response_builder.py -v`

##### D11：实现多模态响应组装

目标：组装命中 chunk 关联图片，并隐藏内部工具 JSON。

修改文件：`src/core/response/multimodal_assembler.py`、`src/core/response/response_builder.py`、`tests/unit/test_response_builder.py`

实现类/函数：

- `MultimodalAssembler`：组装多模态内容
- `KnowledgeHubResponseBuilder`：构建知识库工具返回内容和引用信息

验收标准：可组装图片信息，不泄漏内部 JSON。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_response_builder.py -v`

##### D12：新增 query.py 脚本入口

目标：提供本地命令行入口，完整调用 `hybridsearch + filter + rerank` 查询链路，方便调试和验收。

修改文件：`src/scripts/query.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `main()`：命令行或服务入口
- `parse_args()`：解析命令行参数
- `run_query_cli()`：执行本地查询流程

验收标准：支持 `--query "问题"` 必填参数；支持 `--top-k 10` 默认返回 10 条；支持 `--collection xxx` 限定检索集合，并在 rerank 前过滤候选；支持 `--verbose` 展示 QueryProcessor、Dense、Sparse、Fusion、Filter、Rerank 等中间结果；支持 `--no-rerank` 跳过 Reranker 阶段。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D13：建立 Retrieval 单元测试矩阵

目标：集中覆盖 Retrieval 链路的核心单元行为。

修改文件：`tests/unit/test_retrieval.py`、`tests/unit/test_reranker.py`、`tests/unit/test_response_builder.py`

实现类/函数：

- 测试用例

验收标准：Query、Dense、Sparse、RRF、HybridSearch、Rerank 前过滤、Rerank、Response、query.py 参数解析均覆盖。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit -v`

##### D14：实现 Retrieval 集成测试

目标：验证完整查询链路可串联运行。

修改文件：`tests/integration/test_query_pipeline.py`

实现类/函数：

- `test_query_pipeline_hybrid()`：验证对应行为

验收标准：覆盖 Dense/BM25/Hybrid/Filter/Rerank/fallback。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_query_pipeline.py -v`
#### 阶段 E：MCP 工具服务

##### E1：搭建 MCP Server

目标：创建 MCP Server 入口并注册工具。

修改文件：`src/mcp_server/server.py`、`tests/unit/test_mcp_tools.py`

实现类/函数：

- `create_mcp_server()`：创建服务实例

验收标准：server 可启动并注册 tools。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_mcp_tools.py -v`

##### E2：暴露知识库查询工具

目标：提供 `query_knowledge_hub` 工具。

修改文件：`src/mcp_server/tools.py`、`tests/unit/test_mcp_tools.py`

实现类/函数：

- `query_knowledge_hub`：暴露对外工具能力

验收标准：返回 content、citations、trace_id。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_mcp_tools.py -v`

##### E3：暴露 collection 和 summary 工具

目标：提供 collection 列表和文档摘要查询工具。

修改文件：`src/mcp_server/tools.py`、`tests/unit/test_mcp_tools.py`

实现类/函数：

- `list_collections`：暴露对外工具能力
- `get_document_summary`：暴露对外工具能力

验收标准：空集合返回可读错误。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_mcp_tools.py -v`

##### E4：完成 MCP schema 测试

目标：验证 MCP tools schema 与文档契约一致。

修改文件：`tests/unit/test_mcp_tools.py`

实现类/函数：

- schema 测试

验收标准：tools schema 与文档一致，不泄漏内部 JSON。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_mcp_tools.py -v`

#### 阶段 F：可观测与管理平台

##### F1：实现 Trace 上下文

目标：提供 ingestion/query 链路通用 Trace 上下文。

修改文件：`src/core/trace/trace_context.py`、`src/core/trace/trace_controller.py`、`tests/unit/test_trace_context.py`

实现类/函数：

- `TraceContext`：保存单次 query/ingestion 的 trace 上下文
- `TraceController`：统一记录阶段信息并 flush 结构化日志

验收标准：可记录阶段耗时和输入输出摘要。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_trace_context.py -v`

##### F2：实现 ingestion trace 结构

目标：定义 ingestion trace 的基础信息、阶段详情、汇总指标和评估指标。

修改文件：`src/core/trace/trace_context.py`、`tests/unit/test_trace_context.py`

实现类/函数：

- `build_ingestion_trace()`：构建标准对象

验收标准：包含基础信息、阶段详情、汇总指标、评估指标。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_trace_context.py -v`

##### F3：实现 query trace 结构

目标：定义 query trace 的检索、融合和重排追踪结构。

修改文件：`src/core/trace/trace_context.py`、`tests/unit/test_trace_context.py`

实现类/函数：

- `build_query_trace()`：构建标准对象

验收标准：包含 Dense/BM25、fusion、rerank 变化。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_trace_context.py -v`

##### F4：实现 JSON Lines 日志

目标：将 Trace 按 JSON Lines 追加写入本地日志。

修改文件：`src/observability/structured_log.py`、`src/storage/trace_log_storage.py`、`tests/unit/test_trace_context.py`

实现类/函数：

- `JsonlTraceWriter`：将 trace 追加写入 JSON Lines 日志

验收标准：每行合法 JSON，可追加写入。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_trace_context.py -v`

##### F5：将 Trace 打点注入 ingestion 和 query 链路

目标：让 Trace 不停留在独立工具层，而是真正进入 ingestion 和 query 的运行主链路。

修改文件：`src/ingestion/pipeline.py`、`src/core/query_engine/query_processor.py`、`src/core/query_engine/hybrid_engine.py`、`tests/integration/test_ingestion_pipeline.py`、`tests/integration/test_query_pipeline.py`

实现类/函数：

- `TraceController.record_stage()` 注入点：记录链路阶段信息
- `IngestionPipeline.run()` trace 打点：注入链路追踪点
- `IngestionPipeline.run_indexing()` trace 打点：注入索引子链路追踪点
- `HybridEngine.search()` trace 打点：注入链路追踪点

验收标准：ingestion 链路记录 dedup、load、split、transform、image_caption、embed、upsert；query 链路记录 query_processing、dense、sparse、fusion、filter、rerank、response；正常结束和异常 fallback 都会 flush trace。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_ingestion_pipeline.py services\ai-service\rag\tests\integration\test_query_pipeline.py -v`

##### F6：实现配置读取和数据浏览服务

目标：为 Dashboard 提供配置读取和文档/chunk 查询能力。

修改文件：`src/observability/services/config_reader.py`、`src/observability/services/data_browser_service.py`、`tests/integration/test_dashboard_services.py`

实现类/函数：

- `ConfigReaderService`：读取 settings 并展示当前组件配置
- `DataBrowserService`：查询文档、chunk、图片和索引状态

验收标准：可读取 settings 和文档/chunk 数据。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`

##### F7：实现 Trace 读取和评估服务

目标：为 Dashboard 提供 trace 历史和评估趋势数据。

修改文件：`src/observability/services/trace_reader_service.py`、`src/observability/services/evaluation_service.py`、`tests/integration/test_dashboard_services.py`

实现类/函数：

- `TraceReaderService`：读取 query/ingestion trace 历史和详情
- `EvaluationService`：运行评估任务并读取指标趋势

验收标准：可读取 trace 历史和评估趋势。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`

##### F8：实现总览和摄取管理页面

目标：实现系统总览和 Ingestion 管理页面。

修改文件：`src/observability/pages/overview.py`、`src/observability/pages/ingestion_manage.py`、`tests/integration/test_dashboard_services.py`

实现类/函数：

- 页面渲染函数

验收标准：总览和摄取管理页面可启动。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`

##### F9：实现数据浏览和 Query Trace 页面

目标：实现数据浏览器和 Query Trace 可视化页面。

修改文件：`src/observability/pages/data_browser.py`、`src/observability/pages/query_trace.py`、`tests/integration/test_dashboard_services.py`

实现类/函数：

- 页面渲染函数

验收标准：可展示文档、chunk、召回对比、rerank 变化。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`

##### F10：实现 Ingestion Trace 和评估页面

目标：实现摄取追踪和评估趋势页面。

修改文件：`src/observability/pages/ingestion_trace.py`、`src/observability/pages/evaluation.py`、`tests/integration/test_dashboard_services.py`

实现类/函数：

- 页面渲染函数

验收标准：可展示阶段耗时和评估趋势。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`

##### F11：实现 Dashboard 启动脚本

目标：提供本地启动 Streamlit Dashboard 的脚本入口。

修改文件：`src/scripts/run_dashboard.py`、`tests/integration/test_dashboard_services.py`

实现类/函数：

- `run_dashboard()`：启动 Streamlit Dashboard 入口

验收标准：脚本可加载 app，不要求真实启动浏览器。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`

##### F12：完成 Dashboard 六大页面测试

目标：在进入 AImodel 集成前，验证六大 Dashboard 页面都能基于测试数据正常渲染。

修改文件：`tests/integration/test_dashboard_pages.py`、`src/observability/pages/overview.py`、`src/observability/pages/ingestion_manage.py`、`src/observability/pages/data_browser.py`、`src/observability/pages/query_trace.py`、`src/observability/pages/ingestion_trace.py`、`src/observability/pages/evaluation.py`

实现类/函数：

- 六个页面的 `render_*()` 函数测试夹具

验收标准：系统总览、Ingestion 管理、数据浏览器、Query Trace、Ingestion Trace、评估面板都可以读取测试配置、测试数据库记录和测试 trace，并完成页面渲染入口调用。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_pages.py -v`

#### 阶段 G：质量评估体系

##### G1：准备黄金测试集格式

目标：定义黄金测试集字段和 fixture 样例。

修改文件：`tests/fixtures/golden_set.json`、`tests/unit/test_evaluation.py`

实现类/函数：

- fixture schema

验收标准：问题、答案、来源文档字段完整。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_evaluation.py -v`

##### G2：实现自定义检索指标

目标：实现 Hit Rate、MRR、NDCG 等检索指标。

修改文件：`src/observability/evaluation/metrics.py`、`tests/unit/test_evaluation.py`

实现类/函数：

- `HitRateMetric`：计算评估指标
- `MRRMetric`：计算评估指标
- `NDCGMetric`：计算评估指标

验收标准：指标计算无需真实 LLM。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_evaluation.py -v`

##### G3：接入 Ragas 指标

目标：封装 Ragas 生成质量指标。

修改文件：`src/observability/evaluation/ragas_adapter.py`、`tests/unit/test_evaluation.py`

实现类/函数：

- `RagasEvaluator`：封装评估执行逻辑

验收标准：Ragas 测试使用 marker 隔离。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_evaluation.py -v`

##### G4：实现策略对比评估

目标：支持 Hybrid、Dense-only、Sparse-only、Rerank 等策略对比。

修改文件：`src/observability/evaluation/runner.py`、`tests/unit/test_evaluation.py`

实现类/函数：

- `EvaluationRunner.compare_strategies()`：对比不同检索策略

验收标准：可对比 Hybrid、Dense-only、Sparse-only、Rerank。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_evaluation.py -v`

##### G5：实现评估趋势输出

目标：保存评估结果，供 Dashboard 展示历史趋势。

修改文件：`src/observability/evaluation/runner.py`、`tests/unit/test_evaluation.py`

实现类/函数：

- `EvaluationRunner.save_results()`：保存评估结果

验收标准：评估结果可写入 PostgreSQL 并供 Dashboard 展示。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_evaluation.py -v`

#### 阶段 H：AImodel 联调集成

##### H1：执行 AImodel 集成前验收门禁

目标：在接入 AImodel 前确认 RAG 独立模块已经完成 Dashboard 六大页面测试和全链路 E2E 验收。

修改文件：`tests/integration/test_dashboard_pages.py`、`tests/e2e/test_full_rag_flow.py`

实现类/函数：

- `test_dashboard_six_pages_render()`：验证对应行为
- `test_full_rag_flow_before_aimodel_integration()`：验证对应行为

验收标准：Dashboard 六大页面测试通过；全链路 E2E 覆盖离线摄取、Indexing Pipeline、Hybrid Query、Trace 写入、Dashboard 可读和引用结果构造。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_pages.py services\ai-service\rag\tests\e2e\test_full_rag_flow.py -v`

##### H2：实现 AImodel RAG 工具适配

目标：封装 AImodel 可调用的 RAG 工具。

修改文件：`services/ai-service/app/routers/AImodel/tools.py`、`services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- `search_shopping_guides`：暴露对外工具能力

验收标准：工具返回格式化内容和引用。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\tests\test_aimodel_rag_tool.py -v`

##### H3：接入 Agent 工具列表

目标：把 RAG 工具加入 AImodel Agent 工具集合。

修改文件：`services/ai-service/app/routers/AImodel/service.py`、`services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- `build_rag_tool()`：构建标准对象

验收标准：Agent 可调用 RAG 工具。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\tests\test_aimodel_rag_tool.py -v`

##### H4：验证商品 API 与 RAG 边界

目标：明确商品事实走商品 API，知识补充走 RAG。

修改文件：`services/ai-service/app/routers/AImodel/service.py`、`services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- system prompt 工具边界

验收标准：商品事实走 API，知识补充走 RAG。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\tests\test_aimodel_rag_tool.py -v`

##### H5：验证简单询问和链接场景

目标：覆盖推荐、对比、选购指南、政策 FAQ 等用户场景。

修改文件：`services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- 场景测试

验收标准：推荐、对比、选购指南、政策 FAQ 都有覆盖。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\tests\test_aimodel_rag_tool.py -v`

##### H6：完成端到端联调测试

目标：验证前端/Agent/RAG 的端到端输出契约。

修改文件：`services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- E2E 测试

验收标准：前端/Agent 响应不暴露 tool result 或 chunk id。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\tests\test_aimodel_rag_tool.py -v`
