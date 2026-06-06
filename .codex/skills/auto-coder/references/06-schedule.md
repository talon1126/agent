<!-- synced-from: services\ai-service\rag\DEV_SPEC.md -->
<!-- reference: 任务计划与状态 -->

## 6. 项目排期

### 6.1 阶段预览表

状态标记说明：`[ ]` 表示未开始，`[~]` 表示进行中，`[✔]` 表示已完成。

| 阶段 | 阶段标题 | 目标 | 状态 |
| --- | --- | --- | --- |
| Phase A | 配置与项目骨架 | 独立模块基础文件、Docker 部署骨架、pytest 冒烟测试、`settings.yaml`、prompt 配置、核心类型和配置加载 | [✔] |
| Phase B | 数据持久化与可插拔组件 | PostgreSQL/pgvector schema、repository、文档生命周期管理和 libs 可插拔实现 | [ ] |
| Phase C | Ingestion & Indexing Pipeline | 先去重的数据摄取、Loader、PDF -> Markdown、Splitter、Transform、ImageCaptioner、content_hash 差量、Dense/BM25Indexer 双路索引、pgvector upsert、统一 Pipeline MVP 和 `ingest.py` 脚本入口 | [ ] |
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
    
    - `pytest ...`
    - Dashboard 页面入口：
    
    下一阶段入口：

阶段里程碑表：

| 阶段 | 阶段标题 | 项目当前位置 | 可用功能 | 验证方式 | 完成日期 |
| --- | --- | --- | --- | --- | --- |
| Phase A | 配置与项目骨架 | 独立 RAG 模块骨架、运行配置、Prompt 和共享数据契约已就绪，可进入持久化与可插拔组件开发 | 独立 CLI/Docker 入口、类型化配置加载、活动环境变量校验、英文 Prompt、核心领域类型和统一异常 | `pytest services\ai-service\rag\tests\test_smoke.py services\ai-service\rag\tests\unit\test_config.py services\ai-service\rag\tests\unit\test_types.py -q` | 2026-06-06 |
| Phase B | 数据持久化与可插拔组件 | 未完成 | 暂无 | 暂无 |  |
| Phase C | Ingestion & Indexing Pipeline | 未完成 | 暂无 | 暂无 |  |
| Phase D | Retrieval | 未完成 | 暂无 | 暂无 |  |
| Phase E | MCP 工具服务 | 未完成 | 暂无 | 暂无 |  |
| Phase F | 可观测与管理平台 | 未完成 | 暂无 | 暂无 |  |
| Phase G | 质量评估体系 | 未完成 | 暂无 | 暂无 |  |
| Phase H | AImodel 联调集成 | 未完成 | 暂无 | 暂无 |  |

#### 阶段 A 交付里程碑：配置与项目骨架

完成日期：2026-06-06

项目当前位置：

RAG 已形成可独立安装、测试和构建 Docker 镜像的 Python 子模块。统一配置、Prompt、核心数据对象和异常边界已经稳定，后续阶段可以直接围绕这些契约实现 PostgreSQL 持久化、Provider Factory 和业务 Pipeline。

可用功能：

- 通过 `main.py` 输出无外部依赖的健康状态。
- 从 `settings.yaml` 加载类型化配置，并在启动前校验 Provider、模型、活动环境变量、检索参数和 Embedding 维度。
- 加载并校验 rerank、chunk rewrite 和 image-to-text Prompt。
- 创建并序列化 `Document`、`ImageMetadata`、`Chunk` 和 `RetrievalResult`。
- 通过统一 `RagError` 异常层级区分配置、Provider、数据库、摄取、检索和 MCP 错误。

验证方式：

- `pytest services\ai-service\rag\tests\test_smoke.py services\ai-service\rag\tests\unit\test_config.py services\ai-service\rag\tests\unit\test_types.py -q`

下一阶段入口：

阶段 B 直接复用 `RagSettings` 建立 PostgreSQL/pgvector schema 与连接池，复用核心数据对象实现 Repository，并以 `RagError` 子类统一持久化和 Provider 错误边界。

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
| A3 | 创建 `config/settings.yaml` 示例配置 | [✔] | 2026-06-06 | 已覆盖全部可插拔组件、流水线、存储、可观测、Dashboard、评估和 MCP 配置，5 个单元测试通过 |
| A4 | 创建 prompt 配置目录 | [✔] | 2026-06-06 | 已创建统一英文 Prompt YAML 契约，覆盖 rerank、chunk rewrite、六类图片理解策略和中文 caption 输出，10 个配置测试通过 |
| A5 | 实现配置读取和校验 | [✔] | 2026-06-06 | 已实现完整 `RagSettings`、Provider/model selector、活动环境变量、Embedding/pgvector 维度、检索参数和 Prompt 占位符校验，18 个配置测试通过 |
| A6 | 定义核心类型和统一异常 | [✔] | 2026-06-06 | 已实现 Document、ImageMetadata、Chunk、RetrievalResult 及六类 RagError 子类，覆盖必填位置、非空文本、来源区间和异常链校验，16 个类型测试通过 |

#### 阶段 B：数据持久化与可插拔组件

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| B1 | 编写 collection/document/chunk schema | [ ] |  | PostgreSQL + pgvector，含单元或集成测试 |
| B2 | 编写 image/trace/evaluation schema | [ ] |  | `image_index`、Trace 索引、评估历史 |
| B3 | 实现数据库连接池和 schema 初始化 | [ ] |  | `PostgresPool`、`init_schema()` |
| B4 | 实现 Document/Chunk/Image Repository | [ ] |  | 文档、chunk、ImageStorage 图片落盘和 `image_index` 入库 |
| B5 | 实现 Trace/Evaluation Repository | [ ] |  | Trace 索引和评估历史写入 PostgreSQL |
| B6 | 实现文档生命周期管理 | [ ] |  | `pending`、`processing`、`success`、`failed`、`deleted` |
| B7 | 建立 libs 可插拔组件包结构 | [ ] |  | loader、llm、splitter、transform、embedding、vector_store、reranker、evaluator |
| B8 | 实现 Loader/Splitter libs 基类、factory 和 DocumentChunker 契约 | [ ] |  | `libs.splitter` 保持 `str -> List[str]`，`DocumentChunker` 负责 `Document -> List[Chunk]` |
| B9 | 实现 LLM/Embedding libs 基类、factory 和 fake 实现 | [ ] |  | 统一 `chat()`、`embed()`、`embed_batch()` |
| B10 | 实现 Transform libs 基类、factory 和 fake 实现 | [ ] |  | `BaseTransform`、`TransformFactory`、`FakeTransform` |
| B11 | 实现 VectorStore/Reranker/Evaluator libs 基类、factory 和 fake 实现 | [ ] |  | 覆盖 fallback 和未知 provider 错误 |
| B12 | 实现首批真实组件最小适配 | [ ] |  | OpenAI、DeepSeek、pgvector、RecursiveCharacterTextSplitter；真实调用用 marker 隔离 |

#### 阶段 C：Ingestion & Indexing Pipeline

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| C1 | 实现文档 SHA256 去重与 skipped 快速结束 | [ ] |  | 去重必须先于 Loader；hash 未变更时直接结束并写入 trace |
| C2 | 实现文档加载、Markdown 标准化与图片引用提取 | [ ] |  | PDF -> Markdown、metadata 提取；若存在图片则提取图片并写入占位符 |
| C3 | 实现 DocumentChunker 业务适配与引用保留验证 | [ ] |  | chunk_id、metadata 继承、chunk_index、source_ref、标题层级和图片引用保留 |
| C4 | 实现 Transform 具体实现 | [ ] |  | settings.yaml 配置真实 Transform；覆盖 metadata 注入、rewrite、合并、去噪和典型噪声场景 |
| C5 | 实现 ImageCaptioner | [ ] |  | 当启用 `vision_llm` 且 chunk 存在 `image_refs` 时，生成 caption 并写入 metadata |
| C6 | 实现 chunk_id 生成工具并接入 DocumentChunker | [ ] |  | `hash(source_path + section_path + content_hash)`，由 DocumentChunker 调用 |
| C7 | 实现 DenseEncoder | [ ] |  | 封装 `text-embedding-3-small`、content_hash 差量判断和 Dense 向量生成 |
| C8 | 实现 BM25Indexer | [ ] |  | 生成 BM25 词项、词频和倒排索引数据 |
| C9 | 实现 BatchProcessor 批处理优化 | [ ] |  | 放在 DenseEncoder 和 BM25Indexer 之后，统一处理批量、限流、重试和失败隔离 |
| C10 | 实现 pgvector upsert | [ ] |  | 同一 chunk 两次 upsert 产生相同 id；内容变更 id 变更；支持批量 upsert 且保持顺序 |
| C11 | 实现统一 Pipeline MVP 编排和集成测试 | [ ] |  | 串联摄取、ImageCaptioner、content_hash、Dense、BM25Indexer、batch、upsert，验证最小可运行索引链路 |
| C12 | 新增 `ingest.py` 摄取脚本入口 | [ ] |  | 调用 pipeline，支持 `--collection`、`--path`、`--force` |

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
| Phase A | 6 | 6 | 100% |
| Phase B | 12 | 0 | 0% |
| Phase C | 12 | 0 | 0% |
| Phase D | 14 | 0 | 0% |
| Phase E | 4 | 0 | 0% |
| Phase F | 12 | 0 | 0% |
| Phase G | 5 | 0 | 0% |
| Phase H | 6 | 0 | 0% |
| **总计** | **71** | **6** | **8%** |

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

测试方法：使用 `python -c` 验证 `pyproject.toml` 可被 `tomllib` 解析，并验证 `src` 包可导入。

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

测试方法：`pytest services\ai-service\rag\tests\test_smoke.py -v`

##### A3：创建统一配置示例

目标：提供首版 `settings.yaml` 示例，作为配置驱动开发的入口。

修改文件：`config/settings.yaml`

实现类/函数：

- 配置字段样例

验收标准：LLM、Embedding、Transform、Retrieval、Dashboard 配置齐全。

测试方法：`pytest services\ai-service\rag\tests\unit\test_config.py -v`

##### A4：创建 prompt 配置目录

目标：将提示词从业务代码中分离，便于后续评估和策略替换。

修改文件：`config/prompts/rerank_prompt.yaml`、`config/prompts/rewrite_chunk_prompt.yaml`、`config/prompts/image_to_text_prompt.yaml`

实现类/函数：

- prompt 模板

验收标准：三类 prompt 可被读取；Prompt 的 system instruction、user template、description 和策略说明统一使用英文；Image-to-Text Prompt 必须通过英文指令要求 `description` 和 `key_facts` 使用简体中文，并让 `extracted_text` 原样保留图片文字；英文检查只禁止 CJK 指令，不得错误拒绝 `°C`、`≥` 等合法技术符号。

测试方法：`pytest services\ai-service\rag\tests\unit\test_config.py -v`

##### A5：实现配置读取和校验

目标：实现配置加载、环境变量引用和缺失配置校验。

修改文件：`src/core/config.py`、`tests/unit/test_config.py`

实现类/函数：

- `RagSettings`：定义 settings.yaml 的配置结构和校验规则
- `load_settings()`：加载配置或模板
- `load_prompt()`：加载配置或模板

验收标准：缺配置时抛可读异常，环境变量引用可校验。

测试方法：`pytest services\ai-service\rag\tests\unit\test_config.py -v`

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

测试方法：`pytest services\ai-service\rag\tests\unit\test_types.py -v`

#### 阶段 B：数据持久化与可插拔组件

##### B1：建立核心文档 schema

目标：创建 collection、document、chunk 的 PostgreSQL/pgvector 基础表。

修改文件：`src/storage/schema.sql`、`tests/integration/test_repositories.py`

实现类/函数：

- `rag_collections`：定义数据库表结构
- `rag_documents`：定义数据库表结构
- `rag_chunks`：定义数据库表结构

验收标准：pgvector extension 和核心表可初始化。

测试方法：`pytest services\ai-service\rag\tests\integration\test_repositories.py -v`

##### B2：建立图片、Trace、评估 schema

目标：补齐 `image_index` 图片索引、Trace 索引和评估历史相关表。

修改文件：`src/storage/schema.sql`、`tests/integration/test_repositories.py`

实现类/函数：

- `image_index`：记录图片文件路径、collection、doc_hash 和页码
- `rag_query_traces`：定义数据库表结构
- `rag_ingestion_traces`：定义数据库表结构
- `rag_evaluation_runs`：定义数据库表结构

验收标准：`image_index`、Trace 和评估表可初始化；`idx_collection`、`idx_doc_hash` 索引存在；schema 可重复执行。

测试方法：`pytest services\ai-service\rag\tests\integration\test_repositories.py -v`

##### B3：实现数据库连接和 schema 初始化

目标：封装 PostgreSQL 连接池和 schema 初始化入口。

修改文件：`src/storage/postgres.py`、`tests/integration/test_repositories.py`

实现类/函数：

- `PostgresPool`：管理 PostgreSQL 连接池和事务入口
- `init_schema()`：初始化基础设施

验收标准：连接池可创建，schema 初始化失败有明确异常。

测试方法：`pytest services\ai-service\rag\tests\integration\test_repositories.py -v`

##### B4：实现文档、chunk、图片仓储

目标：实现 RAG 核心数据的 repository 写入和读取。

修改文件：`src/storage/repositories.py`、`src/storage/image_storage.py`、`tests/integration/test_repositories.py`

实现类/函数：

- `DocumentRepository`：封装数据访问逻辑
- `ChunkRepository`：封装数据访问逻辑
- `ImageStorage.save_image()`：保存图片到 `data/images/{collection}/`
- `ImageStorage.upsert_index()`：写入 `image_index` 图片索引

验收标准：文档、chunk 可写入和读取；图片文件保存到 `data/images/{collection}/`；`image_index` 可按 `collection` 和 `doc_hash` 查询。

测试方法：`pytest services\ai-service\rag\tests\integration\test_repositories.py -v`

##### B5：实现 Trace 和评估仓储

目标：支持 Trace 索引和评估结果写入 PostgreSQL。

修改文件：`src/storage/repositories.py`、`tests/integration/test_repositories.py`

实现类/函数：

- `TraceRepository`：封装数据访问逻辑
- `EvaluationRepository`：封装数据访问逻辑

验收标准：Trace 和评估结果可写入和查询。

测试方法：`pytest services\ai-service\rag\tests\integration\test_repositories.py -v`

##### B6：实现文档生命周期管理

目标：统一文档状态流转，避免 deleted/failed 文档进入检索。

修改文件：`src/storage/repositories.py`、`tests/integration/test_repositories.py`

实现类/函数：

- `mark_processing()`：更新生命周期状态
- `mark_success()`：更新生命周期状态
- `mark_deleted()`：更新生命周期状态

验收标准：文档状态按生命周期流转，deleted 不进入检索。

测试方法：`pytest services\ai-service\rag\tests\integration\test_repositories.py -v`

##### B7：创建 libs 可插拔包结构

目标：创建所有可插拔组件包，为后续接口和工厂提供目录边界。

修改文件：`src/libs/*`、`tests/unit/test_factories.py`

实现类/函数：

- 包初始化文件

验收标准：loader、llm、splitter、transform、embedding、vector_store、reranker、evaluator 包存在。

测试方法：`pytest services\ai-service\rag\tests\unit\test_factories.py -v`

##### B8：实现 Loader/Splitter 抽象和工厂

目标：实现 Loader 与纯文本 Splitter 的最小接口、工厂和测试实现，并明确 `DocumentChunker` 的业务适配契约。

修改文件：`src/libs/loader/*`、`src/libs/splitter/*`、`src/ingestion/chunk/document_chunker.py`、`tests/unit/test_factories.py`、`tests/unit/test_splitter.py`

实现类/函数：

- `BaseLoader`：定义最小抽象接口
- `LoaderFactory`：根据配置创建实现
- `BaseSplitter.split(text) -> List[str]`：定义输入输出契约
- `SplitterFactory`：根据配置创建实现
- `DocumentChunker.chunk(document) -> List[Chunk]`：定义输入输出契约

验收标准：可创建 fake/markdown/pdf loader 和 splitter；`libs.splitter` 只接收文本并返回 `List[str]`；`DocumentChunker` 契约测试覆盖 `chunk_id`、metadata 继承、`chunk_index`、`source_ref`、图片引用分发，以及 `List[str] -> List[Chunk]` 类型转换。

测试方法：`pytest services\ai-service\rag\tests\unit\test_factories.py services\ai-service\rag\tests\unit\test_splitter.py -v`

##### B9：实现 LLM/Embedding 抽象和工厂

目标：统一 LLM 与 Embedding 调用接口，支持 fake provider 测试。

修改文件：`src/libs/llm/*`、`src/libs/embedding/*`、`tests/unit/test_factories.py`

实现类/函数：

- `BaseLLM`：定义最小抽象接口
- `LLMFactory`：根据配置创建实现
- `BaseEmbedding`：定义最小抽象接口
- `EmbeddingFactory`：根据配置创建实现

验收标准：`chat()`、`embed()`、`embed_batch()` 接口统一。

测试方法：`pytest services\ai-service\rag\tests\unit\test_factories.py -v`

##### B10：实现 Transform 抽象和工厂

目标：补齐 Transform 可插拔基类、工厂和 fake 实现。

修改文件：`src/libs/transform/base_transform.py`、`src/libs/transform/transform_factory.py`、`src/libs/transform/fake_transform.py`、`tests/unit/test_factories.py`

实现类/函数：

- `BaseTransform`：定义最小抽象接口
- `TransformFactory`：根据配置创建实现
- `FakeTransform`：执行具体转换逻辑

验收标准：Transform 可按配置创建，fake transform 可用于单元测试。

测试方法：`pytest services\ai-service\rag\tests\unit\test_factories.py -v`

##### B11：实现 VectorStore/Reranker/Evaluator 抽象和工厂

目标：统一向量存储、重排和评估组件的可插拔接口。

修改文件：`src/libs/vector_store/*`、`src/libs/reranker/*`、`src/libs/evaluator/*`、`tests/unit/test_factories.py`

实现类/函数：

- `BaseVectorStore`：定义最小抽象接口
- `BaseReranker`：定义最小抽象接口
- `BaseEvaluator`：定义最小抽象接口

验收标准：未知 provider 抛可读错误，fallback 可配置。

测试方法：`pytest services\ai-service\rag\tests\unit\test_factories.py -v`

##### B12：实现首批真实组件最小适配

目标：接入首批真实 provider 的最小可用实现，并保留 fake 默认测试路径。

修改文件：`src/libs/llm/*`、`src/libs/embedding/openai_embedding.py`、`src/libs/vector_store/pgvector_store.py`、`tests/unit/test_factories.py`

实现类/函数：

- `DeepSeekClient`：封装 DeepSeek Chat 模型调用
- `OpenAIEmbedding`：封装 text-embedding-3-small 向量生成
- `PgVectorStore`：封装存储访问能力

验收标准：真实调用默认不跑，marker 隔离；fake provider 默认可测。

测试方法：`pytest services\ai-service\rag\tests\unit\test_factories.py -v`

#### 阶段 C：Ingestion & Indexing Pipeline

##### C1：实现文档 SHA256 去重与 skipped 快速结束

目标：在进入 Loader 前判断文档是否变化，未变化直接走 skipped 快速结束，并记录 trace 摘要。

修改文件：`src/ingestion/pipeline.py`、`src/storage/repositories.py`、`tests/unit/test_loader.py`

实现类/函数：

- `calculate_sha256()`：计算文档稳定哈希标识
- `should_skip_document()`：判断文档是否可以跳过处理
- `IngestionPipeline.run()`：在 Loader 前执行去重判断并处理 skipped 分支

验收标准：hash 未变更时不进入 Loader，不执行 PDF 转换、图片提取、Splitter 和 Transform；skipped 分支写入 trace 摘要。

测试方法：`pytest services\ai-service\rag\tests\unit\test_loader.py -v`

##### C2：实现文档加载、Markdown 标准化与图片引用提取

目标：将输入文档转换为标准 `Document(id, text, metadata)`；完成 PDF -> Markdown、Markdown 标准化、标题层级 metadata 提取；若文档存在图片，则执行图片提取、生成 `image_id`、写入图片占位符，并填充 `metadata.images[]`。

修改文件：`src/ingestion/pdf_to_markdown.py`、`src/libs/loader/markdown_loader.py`、`src/libs/loader/pdf_loader.py`、`tests/unit/test_loader.py`

实现类/函数：

- `MarkItDownConverter`：将 PDF 转换为 canonical Markdown
- `MarkdownLoader.load()`：加载 Markdown 并提取标题层级与 metadata
- `PdfLoader.load()`：加载 PDF 并输出标准 Document
- `extract_images()`：仅在文档存在图片时抽取图片资源并生成图片引用

验收标准：PDF 可转换为 canonical Markdown；Markdown 可输出标准 `Document(id + text + metadata)`；无图片文档不生成无效图片 metadata；有图片文档生成 `image_id`、图片占位符和 `metadata.images[]`。

测试方法：`pytest services\ai-service\rag\tests\unit\test_loader.py -v`

##### C3：实现 DocumentChunker 业务适配与引用保留验证

目标：把 `libs.splitter` 输出的 `List[str]` 转换为符合 `core.types` 契约的 `List[Chunk]`，并验证标题层级、offset 和图片引用不会在业务适配中丢失。

修改文件：`src/ingestion/chunk/document_chunker.py`、`src/ingestion/chunk/splitter_step.py`、`src/ingestion/chunk/chunk_id.py`、`tests/unit/test_splitter.py`

实现类/函数：

- `DocumentChunker.chunk()`：将 Document 转换为带业务 metadata 的 Chunk 列表
- `build_chunk_id()`：生成稳定 chunk 标识
- `build_source_ref()`：建立 chunk 到来源文档的引用
- `attach_section_path()`：将标题层级写入 chunk metadata
- `distribute_image_refs()`：根据图片占位符 offset 分发图片引用

验收标准：每个 chunk 都包含稳定 `chunk_id`；`Document.metadata` 被复制到 `Chunk.metadata`；按顺序添加 `chunk_index`；根据文档来源建立 `source_ref`；chunk metadata 包含 `section_path` 和按需分发的 `image_refs`；没有图片的 chunk 不添加无效引用；完成 `List[str] -> List[Chunk]` 类型转换。

测试方法：`pytest services\ai-service\rag\tests\unit\test_splitter.py -v`

##### C4：实现 Transform 具体实现

目标：集中实现 Transform 阶段的具体能力，包括 metadata 注入、LLM chunk rewrite、智能合并和去噪。

修改文件：`config/settings.yaml`、`src/libs/transform/metadata_enricher.py`、`src/libs/transform/chunk_rewriter.py`、`src/libs/transform/semantic_merge_transform.py`、`src/libs/transform/denoise_transform.py`、`tests/fixtures/noisy_documents/`、`tests/unit/test_transformer.py`

实现类/函数：

- `MetadataEnricher.transform()`：注入标题路径、来源、文档主题等上下文 metadata
- `ChunkRewriter.transform()`：利用 LLM 重写 chunk，使片段语义更完整
- `SemanticMergeTransform.transform()`：合并逻辑相关但被物理切开的相邻 chunk
- `DenoiseTransform.transform()`：清理空白、页眉页脚、目录和解析残留噪声

验收标准：chunk 包含标题、来源、主题上下文；fake LLM 下可 rewrite；逻辑相关 chunk 可合并且 metadata 不丢失；页眉页脚、目录和解析残留可清理。

补充要求：执行该任务时必须在 `settings.yaml` 中配置真实启用的 Transform 链路，测试不能只依赖 fake transform；需要创建典型噪声场景 fixture，例如连续空白、页眉页脚、重复目录、页码水印、PDF 解析断行、无意义符号残留和图片占位符附近噪声。

测试方法：`pytest services\ai-service\rag\tests\unit\test_transformer.py -v`

##### C5：实现 ImageCaptioner

目标：当 `vision_llm.enabled=true` 且 chunk metadata 中存在 `image_refs` 时，为关联图片生成 caption，并将 caption 写入 chunk metadata；未启用 Vision LLM 或没有 `image_refs` 时必须安全跳过。

修改文件：`src/ingestion/transform/image_captioner.py`、`src/libs/transform/image_to_text_transform.py`、`config/settings.yaml`、`tests/unit/test_transformer.py`

实现类/函数：

- `ImageCaptioner.caption()`：读取 chunk 的 `image_refs` 并生成图片描述
- `ImageCaptioner.should_caption()`：判断是否满足 `vision_llm.enabled=true` 且存在 `image_refs`
- `ImageCaptioner.write_metadata()`：将 caption、caption_provider、caption_status 写入 chunk metadata
- `ImageToTextTransform.transform()`：调用 Vision LLM 生成图片描述并返回结构化 caption 结果

验收标准：启用 `vision_llm` 且存在 `image_refs` 时会生成 caption 并写入 chunk metadata；未启用 `vision_llm` 时不调用 Vision LLM，并写入 skipped 状态；没有 `image_refs` 的 chunk 不生成 caption；Vision LLM 失败时写入 failed/low_quality 状态并保留原 chunk；caption 可被后续 DenseEncoder 和 BM25Indexer 使用。

测试方法：`pytest services\ai-service\rag\tests\unit\test_transformer.py -v`

##### C6：生成稳定 chunk_id 并接入 DocumentChunker

目标：为 DocumentChunker 生成业务 Chunk 时提供稳定可追踪的 ID 规则。

修改文件：`src/ingestion/chunk/chunk_id.py`、`src/ingestion/chunk/document_chunker.py`、`tests/unit/test_splitter.py`

实现类/函数：

- `build_chunk_id()`：生成稳定 chunk 标识

验收标准：同来源、章节、内容生成稳定 ID；DocumentChunker 创建的每个 Chunk 都调用该规则写入 `chunk.id`。

测试方法：`pytest services\ai-service\rag\tests\unit\test_splitter.py -v`

##### C7：实现 DenseEncoder

目标：将默认 embedding 模型适配、chunk `content_hash` 差量判断和 Dense 向量生成统一收敛到 `DenseEncoder`。

修改文件：`src/libs/embedding/openai_embedding.py`、`src/ingestion/embedding/dense_encoder.py`、`src/ingestion/embedding/embedding_step.py`、`tests/unit/test_embedding.py`

实现类/函数：

- `OpenAIEmbedding.embed()`：调用 `text-embedding-3-small` 生成单条文本向量
- `DenseEncoder.should_encode()`：基于 chunk `content_hash` 判断是否需要重新生成 Dense 向量
- `DenseEncoder.encode()`：生成单个 chunk 的 Dense 语义向量
- `EmbeddingStep.run_dense()`：编排 DenseEncoder 并输出待写入向量结果

验收标准：fake 默认可测，真实调用 marker 隔离；已存在 content_hash 不重复调用模型；新 chunk 可以生成 Dense 向量；DenseEncoder 不承担批处理职责。

测试方法：`pytest services\ai-service\rag\tests\unit\test_embedding.py -v`

##### C8：实现 BM25Indexer

目标：为 Sparse Route 构建 BM25 词项、词频和倒排索引数据。

修改文件：`src/ingestion/embedding/bm25_indexer.py`、`tests/unit/test_bm25.py`

实现类/函数：

- `BM25Indexer.index()`：生成 BM25 词项、词频和倒排索引数据
- `BM25Indexer.query()`：根据关键词返回候选 `chunk_id` 和 BM25 分数

验收标准：可为 chunk 构建 BM25 索引；可按关键词召回候选 chunk；索引结果可被 Sparse Route 复用。

测试方法：`pytest services\ai-service\rag\tests\unit\test_bm25.py -v`

##### C9：实现 BatchProcessor 批处理优化

目标：在 DenseEncoder 和 BM25Indexer 均完成后，提供统一批处理能力，处理批量输入、限流、重试和失败隔离。

修改文件：`src/ingestion/embedding/batch_processor.py`、`src/ingestion/embedding/embedding_step.py`、`tests/unit/test_embedding.py`、`tests/unit/test_bm25.py`

实现类/函数：

- `BatchProcessor.run()`：按配置批量执行编码或索引任务
- `BatchProcessor.retry_failed()`：对可重试失败执行有限重试
- `EmbeddingStep.run_batch()`：编排 DenseEncoder 与 BM25Indexer 的批处理执行

验收标准：批处理大小受配置控制；Dense 和 BM25 两路都能复用 BatchProcessor；部分失败不影响其他 chunk；重试次数和失败记录可测试。

测试方法：`pytest services\ai-service\rag\tests\unit\test_embedding.py services\ai-service\rag\tests\unit\test_bm25.py -v`

##### C10：实现统一 upsert

目标：将文档、chunk、向量、BM25 和图片索引一致写入，并保证 upsert 幂等性和批量顺序。

修改文件：`src/ingestion/storage/upsert_step.py`、`src/storage/image_storage.py`、`tests/integration/test_ingestion_pipeline.py`

实现类/函数：

- `UpsertStep.run()`：统一写入 chunk、向量、BM25 和图片索引
- `ImageStorage.upsert_index()`：写入图片索引并保持数据库记录一致

验收标准：同一 chunk 两次 upsert 产生相同 id；chunk 内容变更时 id 随 `content_hash` 变更；支持批量 upsert 且返回结果保持输入顺序；文档、chunk、向量、BM25 和 `image_index` 记录一致写入。

测试方法：`pytest services\ai-service\rag\tests\integration\test_ingestion_pipeline.py -v`

##### C11：实现统一 Pipeline MVP 编排和集成测试

目标：在统一 `pipeline.py` 中把摄取结果、ImageCaptioner、DenseEncoder、BM25Indexer、BatchProcessor 和 upsert 串成最小可运行链路。

修改文件：`src/ingestion/pipeline.py`、`tests/integration/test_ingestion_pipeline.py`

实现类/函数：

- `IngestionPipeline.run_indexing()`：编排索引 MVP 子链路
- `IngestionPipeline.run()`：串联摄取与索引主链路
- `IngestionPipelineResult`：定义统一摄取与索引流程返回结果

验收标准：给定原始文档路径，可以完成去重、Loader、Splitter、Transform、ImageCaptioner 条件 caption、DenseEncoder 差量编码、BM25Indexer 索引、BatchProcessor 批处理和 upsert；重复执行时具备幂等性。

测试方法：`pytest services\ai-service\rag\tests\integration\test_ingestion_pipeline.py -v`

##### C12：新增 ingest.py 摄取脚本入口

目标：提供本地命令行入口，调用 Ingestion Pipeline 执行离线文档摄取。

修改文件：`src/scripts/ingest.py`、`tests/unit/test_loader.py`

实现类/函数：

- `parse_args()`：解析命令行参数
- `run_ingest_cli()`：执行本地摄取流程

验收标准：支持 `--collection` 指定 collection；支持 `--path` 指定待摄取文件或目录；支持 `--force` 强制重新摄取并绕过 SHA256 skipped 快速结束；参数缺失时返回可读错误。

测试方法：`pytest services\ai-service\rag\tests\unit\test_loader.py -v`
#### 阶段 D：Retrieval

##### D1：实现 query 预处理

目标：完成 query 标准化、意图识别和可选 rewrite。

修改文件：`src/core/query_engine/query_processor.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `QueryProcessor.process()`：处理输入并输出标准对象

验收标准：支持 normalize、意图识别、可选 rewrite。

测试方法：`pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D2：实现 Dense Route 向量检索

目标：输入用户 query 或 `ProcessedQuery`，完成 Query Embedding、pgvector 向量检索，并返回统一的 `RetrievalResult(chunk_id,text,score,metadata)`。

修改文件：`src/core/query_engine/dense_route.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `DenseRoute.search()`：执行 Query Embedding 和 pgvector 语义召回

验收标准：调用 `EmbeddingClient.embed(processed_query.normalized_query)`；调用 vector store 完成 Top-k 向量检索；返回结果字段统一为 `chunk_id`、`text`、`score`、`metadata`；空 query、embedding 失败、空结果都有可测试分支；trace details 记录 route、top_k、候选数量和耗时。

测试方法：`pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D3：实现 Sparse Route BM25 回表检索

目标：使用 `QueryProcessor.process()` 生成的 `ProcessedQuery.keywords` 进行 BM25 检索，再通过 chunk_id 回表读取 chunk 正文和 metadata。

修改文件：`src/core/query_engine/sparse_route.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `SparseRoute.search()`：执行 BM25 关键词召回并按 chunk_id 回表
- `BM25Indexer.query()`：执行索引查询
- `VectorStore.get_by_ids()`：按 ID 回表读取数据

验收标准：流程固定为 `keywords -> bm25_indexer.query(keywords, top_k) -> [{chunk_id, score}] -> vector_store.get_by_ids(chunk_ids) -> [{id, text, metadata}] -> List[RetrievalResult]`；keywords 为空时返回空结果并记录 skipped 原因；BM25 返回的 chunk_id 顺序应被保留；缺失 chunk_id 应被跳过并写入 trace details。

测试方法：`pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D4：实现 RRF Fusion

目标：融合 Dense/BM25 两路候选，避免直接比较不同分数。

修改文件：`src/core/query_engine/fusion.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `reciprocal_rank_fusion()`：按排名倒数融合 Dense/BM25 候选

验收标准：基于排名倒数融合，不比较分数。

测试方法：`pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D5：实现 HybridSearch 编排

目标：编排 Dense Route、Sparse Route 和 RRF Fusion，完成候选去重、双路召回融合和单路失败降级。

修改文件：`src/core/query_engine/hybrid_engine.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `HybridSearch.search()`：编排双路召回、候选去重和 RRF 融合
- `HybridSearchResult`：定义流程返回结果

验收标准：前置依赖为 D1、D2、D3、D4；输入 `ProcessedQuery`；分别执行 Dense/BM25 两路检索；按 `chunk_id` 去重并保留 `dense_rank`、`sparse_rank`、`dense_score`、`sparse_score`；调用 RRF Fusion 生成融合排序；单路失败时允许降级为另一条路线并写入 trace details。

测试方法：`pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D6：实现 Rerank 前候选过滤

目标：在 RRF Fusion 之后、Reranker 之前，根据调用参数过滤候选，避免把不符合限定条件的 chunk 送入重排阶段。

修改文件：`src/core/query_engine/hybrid_engine.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `CandidateFilter.apply()`：按参数过滤候选结果
- `HybridSearch.apply_metadata_filter()`：在进入 rerank 前执行 metadata 过滤

验收标准：支持 `collection`、`doc_type`、来源类型、文档状态、权限、生命周期状态等参数；过滤发生在 RRF Fusion 之后、Rerank 之前；`--collection` 等脚本参数复用同一过滤逻辑；过滤结果数量和过滤原因写入 trace details。

测试方法：`pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D7：实现 Cross-Encoder Reranker

目标：支持 Cross-Encoder 对过滤后的候选进行精排。

修改文件：`src/libs/reranker/cross_encoder_reranker.py`、`tests/unit/test_reranker.py`

实现类/函数：

- `CrossEncoderReranker.rerank()`：执行候选重排

验收标准：只接收过滤后的候选；可按 query-doc pair 重新排序。

测试方法：`pytest services\ai-service\rag\tests\unit\test_reranker.py -v`

##### D8：实现 LLM Rerank

目标：支持 LLM 对过滤后的候选进行重排。

修改文件：`src/libs/reranker/llm_reranker.py`、`tests/unit/test_reranker.py`

实现类/函数：

- `LLMReranker.rerank()`：执行候选重排

验收标准：只接收过滤后的候选；fake LLM 下可稳定排序。

测试方法：`pytest services\ai-service\rag\tests\unit\test_reranker.py -v`

##### D9：实现 rerank fallback

目标：在 reranker 不可用、超时或异常时回退到过滤后的 RRF 结果。

修改文件：`src/core/query_engine/reranker.py`、`tests/unit/test_reranker.py`

实现类/函数：

- `RerankController.rerank_or_fallback()`：执行 rerank 并在异常时回退过滤后的 RRF 结果

验收标准：超时、异常、不可用时回退过滤后的 RRF 排序；不会重新引入已被过滤的候选。

测试方法：`pytest services\ai-service\rag\tests\unit\test_reranker.py -v`

##### D10：实现引用构造

目标：为最终上下文构建可展示的引用来源。

修改文件：`src/core/response/citation_builder.py`、`tests/unit/test_response_builder.py`

实现类/函数：

- `CitationBuilder.build()`：构建输出对象

验收标准：输出来源标题、章节、路径、trace_id。

测试方法：`pytest services\ai-service\rag\tests\unit\test_response_builder.py -v`

##### D11：实现多模态响应组装

目标：组装命中 chunk 关联图片，并隐藏内部工具 JSON。

修改文件：`src/core/response/multimodal_assembler.py`、`src/core/response/response_builder.py`、`tests/unit/test_response_builder.py`

实现类/函数：

- `MultimodalAssembler`：组装多模态内容
- `KnowledgeHubResponseBuilder`：构建知识库工具返回内容和引用信息

验收标准：可组装图片信息，不泄漏内部 JSON。

测试方法：`pytest services\ai-service\rag\tests\unit\test_response_builder.py -v`

##### D12：新增 query.py 脚本入口

目标：提供本地命令行入口，完整调用 `hybridsearch + filter + rerank` 查询链路，方便调试和验收。

修改文件：`src/scripts/query.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `main()`：命令行或服务入口
- `parse_args()`：解析命令行参数
- `run_query_cli()`：执行本地查询流程

验收标准：支持 `--query "问题"` 必填参数；支持 `--top-k 10` 默认返回 10 条；支持 `--collection xxx` 限定检索集合，并在 rerank 前过滤候选；支持 `--verbose` 展示 QueryProcessor、Dense、Sparse、Fusion、Filter、Rerank 等中间结果；支持 `--no-rerank` 跳过 Reranker 阶段。

测试方法：`pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D13：建立 Retrieval 单元测试矩阵

目标：集中覆盖 Retrieval 链路的核心单元行为。

修改文件：`tests/unit/test_retrieval.py`、`tests/unit/test_reranker.py`、`tests/unit/test_response_builder.py`

实现类/函数：

- 测试用例

验收标准：Query、Dense、Sparse、RRF、HybridSearch、Rerank 前过滤、Rerank、Response、query.py 参数解析均覆盖。

测试方法：`pytest services\ai-service\rag\tests\unit -v`

##### D14：实现 Retrieval 集成测试

目标：验证完整查询链路可串联运行。

修改文件：`tests/integration/test_query_pipeline.py`

实现类/函数：

- `test_query_pipeline_hybrid()`：验证对应行为

验收标准：覆盖 Dense/BM25/Hybrid/Filter/Rerank/fallback。

测试方法：`pytest services\ai-service\rag\tests\integration\test_query_pipeline.py -v`
#### 阶段 E：MCP 工具服务

##### E1：搭建 MCP Server

目标：创建 MCP Server 入口并注册工具。

修改文件：`src/mcp_server/server.py`、`tests/unit/test_mcp_tools.py`

实现类/函数：

- `create_mcp_server()`：创建服务实例

验收标准：server 可启动并注册 tools。

测试方法：`pytest services\ai-service\rag\tests\unit\test_mcp_tools.py -v`

##### E2：暴露知识库查询工具

目标：提供 `query_knowledge_hub` 工具。

修改文件：`src/mcp_server/tools.py`、`tests/unit/test_mcp_tools.py`

实现类/函数：

- `query_knowledge_hub`：暴露对外工具能力

验收标准：返回 content、citations、trace_id。

测试方法：`pytest services\ai-service\rag\tests\unit\test_mcp_tools.py -v`

##### E3：暴露 collection 和 summary 工具

目标：提供 collection 列表和文档摘要查询工具。

修改文件：`src/mcp_server/tools.py`、`tests/unit/test_mcp_tools.py`

实现类/函数：

- `list_collections`：暴露对外工具能力
- `get_document_summary`：暴露对外工具能力

验收标准：空集合返回可读错误。

测试方法：`pytest services\ai-service\rag\tests\unit\test_mcp_tools.py -v`

##### E4：完成 MCP schema 测试

目标：验证 MCP tools schema 与文档契约一致。

修改文件：`tests/unit/test_mcp_tools.py`

实现类/函数：

- schema 测试

验收标准：tools schema 与文档一致，不泄漏内部 JSON。

测试方法：`pytest services\ai-service\rag\tests\unit\test_mcp_tools.py -v`

#### 阶段 F：可观测与管理平台

##### F1：实现 Trace 上下文

目标：提供 ingestion/query 链路通用 Trace 上下文。

修改文件：`src/core/trace/trace_context.py`、`src/core/trace/trace_controller.py`、`tests/unit/test_trace_context.py`

实现类/函数：

- `TraceContext`：保存单次 query/ingestion 的 trace 上下文
- `TraceController`：统一记录阶段信息并 flush 结构化日志

验收标准：可记录阶段耗时和输入输出摘要。

测试方法：`pytest services\ai-service\rag\tests\unit\test_trace_context.py -v`

##### F2：实现 ingestion trace 结构

目标：定义 ingestion trace 的基础信息、阶段详情、汇总指标和评估指标。

修改文件：`src/core/trace/trace_context.py`、`tests/unit/test_trace_context.py`

实现类/函数：

- `build_ingestion_trace()`：构建标准对象

验收标准：包含基础信息、阶段详情、汇总指标、评估指标。

测试方法：`pytest services\ai-service\rag\tests\unit\test_trace_context.py -v`

##### F3：实现 query trace 结构

目标：定义 query trace 的检索、融合和重排追踪结构。

修改文件：`src/core/trace/trace_context.py`、`tests/unit/test_trace_context.py`

实现类/函数：

- `build_query_trace()`：构建标准对象

验收标准：包含 Dense/BM25、fusion、rerank 变化。

测试方法：`pytest services\ai-service\rag\tests\unit\test_trace_context.py -v`

##### F4：实现 JSON Lines 日志

目标：将 Trace 按 JSON Lines 追加写入本地日志。

修改文件：`src/observability/structured_log.py`、`src/storage/trace_log_storage.py`、`tests/unit/test_trace_context.py`

实现类/函数：

- `JsonlTraceWriter`：将 trace 追加写入 JSON Lines 日志

验收标准：每行合法 JSON，可追加写入。

测试方法：`pytest services\ai-service\rag\tests\unit\test_trace_context.py -v`

##### F5：将 Trace 打点注入 ingestion 和 query 链路

目标：让 Trace 不停留在独立工具层，而是真正进入 ingestion 和 query 的运行主链路。

修改文件：`src/ingestion/pipeline.py`、`src/core/query_engine/query_processor.py`、`src/core/query_engine/hybrid_engine.py`、`tests/integration/test_ingestion_pipeline.py`、`tests/integration/test_query_pipeline.py`

实现类/函数：

- `TraceController.record_stage()` 注入点：记录链路阶段信息
- `IngestionPipeline.run()` trace 打点：注入链路追踪点
- `IngestionPipeline.run_indexing()` trace 打点：注入索引子链路追踪点
- `HybridEngine.search()` trace 打点：注入链路追踪点

验收标准：ingestion 链路记录 dedup、load、split、transform、image_caption、embed、upsert；query 链路记录 query_processing、dense、sparse、fusion、filter、rerank、response；正常结束和异常 fallback 都会 flush trace。

测试方法：`pytest services\ai-service\rag\tests\integration\test_ingestion_pipeline.py services\ai-service\rag\tests\integration\test_query_pipeline.py -v`

##### F6：实现配置读取和数据浏览服务

目标：为 Dashboard 提供配置读取和文档/chunk 查询能力。

修改文件：`src/observability/services/config_reader.py`、`src/observability/services/data_browser_service.py`、`tests/integration/test_dashboard_services.py`

实现类/函数：

- `ConfigReaderService`：读取 settings 并展示当前组件配置
- `DataBrowserService`：查询文档、chunk、图片和索引状态

验收标准：可读取 settings 和文档/chunk 数据。

测试方法：`pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`

##### F7：实现 Trace 读取和评估服务

目标：为 Dashboard 提供 trace 历史和评估趋势数据。

修改文件：`src/observability/services/trace_reader_service.py`、`src/observability/services/evaluation_service.py`、`tests/integration/test_dashboard_services.py`

实现类/函数：

- `TraceReaderService`：读取 query/ingestion trace 历史和详情
- `EvaluationService`：运行评估任务并读取指标趋势

验收标准：可读取 trace 历史和评估趋势。

测试方法：`pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`

##### F8：实现总览和摄取管理页面

目标：实现系统总览和 Ingestion 管理页面。

修改文件：`src/observability/pages/overview.py`、`src/observability/pages/ingestion_manage.py`、`tests/integration/test_dashboard_services.py`

实现类/函数：

- 页面渲染函数

验收标准：总览和摄取管理页面可启动。

测试方法：`pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`

##### F9：实现数据浏览和 Query Trace 页面

目标：实现数据浏览器和 Query Trace 可视化页面。

修改文件：`src/observability/pages/data_browser.py`、`src/observability/pages/query_trace.py`、`tests/integration/test_dashboard_services.py`

实现类/函数：

- 页面渲染函数

验收标准：可展示文档、chunk、召回对比、rerank 变化。

测试方法：`pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`

##### F10：实现 Ingestion Trace 和评估页面

目标：实现摄取追踪和评估趋势页面。

修改文件：`src/observability/pages/ingestion_trace.py`、`src/observability/pages/evaluation.py`、`tests/integration/test_dashboard_services.py`

实现类/函数：

- 页面渲染函数

验收标准：可展示阶段耗时和评估趋势。

测试方法：`pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`

##### F11：实现 Dashboard 启动脚本

目标：提供本地启动 Streamlit Dashboard 的脚本入口。

修改文件：`src/scripts/run_dashboard.py`、`tests/integration/test_dashboard_services.py`

实现类/函数：

- `run_dashboard()`：启动 Streamlit Dashboard 入口

验收标准：脚本可加载 app，不要求真实启动浏览器。

测试方法：`pytest services\ai-service\rag\tests\integration\test_dashboard_services.py -v`

##### F12：完成 Dashboard 六大页面测试

目标：在进入 AImodel 集成前，验证六大 Dashboard 页面都能基于测试数据正常渲染。

修改文件：`tests/integration/test_dashboard_pages.py`、`src/observability/pages/overview.py`、`src/observability/pages/ingestion_manage.py`、`src/observability/pages/data_browser.py`、`src/observability/pages/query_trace.py`、`src/observability/pages/ingestion_trace.py`、`src/observability/pages/evaluation.py`

实现类/函数：

- 六个页面的 `render_*()` 函数测试夹具

验收标准：系统总览、Ingestion 管理、数据浏览器、Query Trace、Ingestion Trace、评估面板都可以读取测试配置、测试数据库记录和测试 trace，并完成页面渲染入口调用。

测试方法：`pytest services\ai-service\rag\tests\integration\test_dashboard_pages.py -v`

#### 阶段 G：质量评估体系

##### G1：准备黄金测试集格式

目标：定义黄金测试集字段和 fixture 样例。

修改文件：`tests/fixtures/golden_set.json`、`tests/unit/test_evaluation.py`

实现类/函数：

- fixture schema

验收标准：问题、答案、来源文档字段完整。

测试方法：`pytest services\ai-service\rag\tests\unit\test_evaluation.py -v`

##### G2：实现自定义检索指标

目标：实现 Hit Rate、MRR、NDCG 等检索指标。

修改文件：`src/observability/evaluation/metrics.py`、`tests/unit/test_evaluation.py`

实现类/函数：

- `HitRateMetric`：计算评估指标
- `MRRMetric`：计算评估指标
- `NDCGMetric`：计算评估指标

验收标准：指标计算无需真实 LLM。

测试方法：`pytest services\ai-service\rag\tests\unit\test_evaluation.py -v`

##### G3：接入 Ragas 指标

目标：封装 Ragas 生成质量指标。

修改文件：`src/observability/evaluation/ragas_adapter.py`、`tests/unit/test_evaluation.py`

实现类/函数：

- `RagasEvaluator`：封装评估执行逻辑

验收标准：Ragas 测试使用 marker 隔离。

测试方法：`pytest services\ai-service\rag\tests\unit\test_evaluation.py -v`

##### G4：实现策略对比评估

目标：支持 Hybrid、Dense-only、Sparse-only、Rerank 等策略对比。

修改文件：`src/observability/evaluation/runner.py`、`tests/unit/test_evaluation.py`

实现类/函数：

- `EvaluationRunner.compare_strategies()`：对比不同检索策略

验收标准：可对比 Hybrid、Dense-only、Sparse-only、Rerank。

测试方法：`pytest services\ai-service\rag\tests\unit\test_evaluation.py -v`

##### G5：实现评估趋势输出

目标：保存评估结果，供 Dashboard 展示历史趋势。

修改文件：`src/observability/evaluation/runner.py`、`tests/unit/test_evaluation.py`

实现类/函数：

- `EvaluationRunner.save_results()`：保存评估结果

验收标准：评估结果可写入 PostgreSQL 并供 Dashboard 展示。

测试方法：`pytest services\ai-service\rag\tests\unit\test_evaluation.py -v`

#### 阶段 H：AImodel 联调集成

##### H1：执行 AImodel 集成前验收门禁

目标：在接入 AImodel 前确认 RAG 独立模块已经完成 Dashboard 六大页面测试和全链路 E2E 验收。

修改文件：`tests/integration/test_dashboard_pages.py`、`tests/e2e/test_full_rag_flow.py`

实现类/函数：

- `test_dashboard_six_pages_render()`：验证对应行为
- `test_full_rag_flow_before_aimodel_integration()`：验证对应行为

验收标准：Dashboard 六大页面测试通过；全链路 E2E 覆盖离线摄取、Indexing Pipeline、Hybrid Query、Trace 写入、Dashboard 可读和引用结果构造。

测试方法：`pytest services\ai-service\rag\tests\integration\test_dashboard_pages.py services\ai-service\rag\tests\e2e\test_full_rag_flow.py -v`

##### H2：实现 AImodel RAG 工具适配

目标：封装 AImodel 可调用的 RAG 工具。

修改文件：`services/ai-service/app/routers/AImodel/tools.py`、`services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- `search_shopping_guides`：暴露对外工具能力

验收标准：工具返回格式化内容和引用。

测试方法：`pytest services\ai-service\tests\test_aimodel_rag_tool.py -v`

##### H3：接入 Agent 工具列表

目标：把 RAG 工具加入 AImodel Agent 工具集合。

修改文件：`services/ai-service/app/routers/AImodel/service.py`、`services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- `build_rag_tool()`：构建标准对象

验收标准：Agent 可调用 RAG 工具。

测试方法：`pytest services\ai-service\tests\test_aimodel_rag_tool.py -v`

##### H4：验证商品 API 与 RAG 边界

目标：明确商品事实走商品 API，知识补充走 RAG。

修改文件：`services/ai-service/app/routers/AImodel/service.py`、`services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- system prompt 工具边界

验收标准：商品事实走 API，知识补充走 RAG。

测试方法：`pytest services\ai-service\tests\test_aimodel_rag_tool.py -v`

##### H5：验证简单询问和链接场景

目标：覆盖推荐、对比、选购指南、政策 FAQ 等用户场景。

修改文件：`services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- 场景测试

验收标准：推荐、对比、选购指南、政策 FAQ 都有覆盖。

测试方法：`pytest services\ai-service\tests\test_aimodel_rag_tool.py -v`

##### H6：完成端到端联调测试

目标：验证前端/Agent/RAG 的端到端输出契约。

修改文件：`services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- E2E 测试

验收标准：前端/Agent 响应不暴露 tool result 或 chunk id。

测试方法：`pytest services\ai-service\tests\test_aimodel_rag_tool.py -v`
