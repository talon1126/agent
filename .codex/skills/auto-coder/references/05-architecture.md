<!-- synced-from: services\ai-service\rag\DEV_SPEC.md -->
<!-- reference: 架构与模块设计 -->

## 5. 系统架构与模块设计

### 5.1 整体架构图

```text
                                  +----------------------+
                                  |      AImodel Agent   |
                                  |  search_shopping_guides
                                  +----------+-----------+
                                             |
                                             v
+--------------------------------------------------------------------------------+
|                                  RAG System                                    |
|                                                                                |
|  +---------------------------- Core Layer ----------------------------------+  |
|  |                                                                            |  |
|  |  Query Engine                                                              |  |
|  |   -> Query Processor                                                       |  |
|  |   -> Hybrid Engine                                                         |  |
|  |       -> Dense Route                                                       |  |
|  |       -> Sparse Route                                                      |  |
|  |       -> Fusion (RRF)                                                      |  |
|  |   -> Reranker                                                              |  |
|  |   -> Response Builder                                                      |  |
|  |                                                                            |  |
|  |  Trace Controller                                                          |  |
|  +-------------------------------+--------------------------------------------+  |
|                                  |                                               |
|                                  v                                               |
|  +--------------------------- Storage Layer --------------------------------+  |
|  |  Vector Storage(pgvector) | BM25 Index | Image Storage | Trace Logs        |  |
|  +-------------------------------+--------------------------------------------+  |
|                                  ^                                               |
|                                  |                                               |
|  +---------------------- Ingestion Pipeline(Offline) -----------------------+  |
|  |  Loader(pdf -> md, metadata, images) -> Splitter -> Transformer           |  |
|  |      -> Embedding(Dense + Sparse) -> Upsert                               |  |
|  +-------------------------------+--------------------------------------------+  |
|                                  ^                                               |
|                                  |                                               |
|  +------------------------ Pluggable Libs Layer ----------------------------+  |
|  |  loader | llm | splitter | embedding | vector_store | reranker | evaluator |  |
|  |  each package contains: base interface + factory + implementations        |  |
|  +-------------------------------+--------------------------------------------+  |
|                                  |                                               |
|                                  v                                               |
|  +------------------------ Observability Layer -----------------------------+  |
|  |  TraceContext | Evaluation | Dashboard(Streamlit) | Structured Log        |  |
|  +----------------------------------------------------------------------------+  |
|                                                                                |
|  MCP Server exposes: query_knowledge_hub / list_collections / get_document_summary |
+--------------------------------------------------------------------------------+
```

### 5.2 目录结构树

```text
services/ai-service/rag/
├── DEV_SPEC.md                                    # RAG 子系统开发规范文档
├── README.md                                      # 独立 RAG 模块说明、启动方式和开发命令
├── pyproject.toml                                 # Python 包配置、依赖、pytest 和 lint 配置
├── uv.lock                                        # uv 生成的依赖锁文件，保证本地、CI 和 Docker 可复现
├── main.py                                        # 独立部署入口，启动 FastAPI/MCP 或本地调试命令
├── Dockerfile                                     # 独立 Docker 镜像构建文件
├── .dockerignore                                  # Docker 构建上下文忽略规则
├── .gitignore                                     # RAG 模块本地缓存、日志和临时文件忽略规则
├── config/
│   ├── settings.example.yaml                      # 版本化完整配置模板，包含组件 Provider 选择
│   ├── settings.yaml                              # 本地运行配置，由模板复制且不提交 Git
│   └── prompts/
│       ├── rerank_prompt.yaml                     # Rerank 阶段使用的提示词模板
│       ├── document_summary_prompt.yaml           # 文档级语义摘要提示词模板
│       ├── rewrite_chunk_prompt.yaml              # chunk 语义改写与增强提示词模板
│       ├── semantic_merge_prompt.yaml              # 相邻 chunk 语义合并判断提示词模板
│       ├── image_caption_prompt.yaml              # 图片 caption 生成提示词模板
│       └── evidence_context_prompt.yaml           # 查询结果最终上下文优化提示词模板
├── data/
│   ├── raw/                                       # 按 collection 分类存放原始测试文档和本地摄取文件
│   │   └── shopping_guides/                       # shopping_guides collection 的原始文档
│   ├── markdown/                                  # PDF 转换后的 Markdown 中间文件
│   ├── db/                                        # 本地开发数据库数据和索引文件
│   │   ├── postgres/                              # PostgreSQL 本地数据、dump 或初始化辅助文件
│   │   └── bm25/                                  # BM25 倒排索引和词项统计缓存
│   └── eval/                                      # 评估运行输出和临时对比数据；黄金测试集固定放在 tests/fixtures/
├── src/
│   ├── core/
│   │   ├── config.py                              # 读取 settings.yaml 和 prompt 配置
│   │   ├── types.py                               # Document、Chunk、RetrievalResult 等核心类型
│   │   ├── errors.py                              # RAG 子系统统一异常定义
│   │   ├── bm25_analyzer.py                       # 摄取与在线查询共享的 BM25 分词和候选契约
│   │   ├── query_engine/
│   │   │   ├── query_processor.py                 # 查询预处理、query normalize 和可选 rewrite
│   │   │   ├── hybrid_engine.py                   # 编排 Dense Route、Sparse Route 和融合流程
│   │   │   ├── dense_route.py                     # Query Embedding 和 pgvector 语义召回
│   │   │   ├── sparse_route.py                    # BM25 和倒排索引关键词召回
│   │   │   ├── fusion.py                          # RRF 排名倒数融合
│   │   │   ├── trace_snapshots.py                 # Query Trace 候选快照构造
│   │   │   └── reranker.py                        # 调用 reranker 并处理 fallback
│   │   ├── response/
│   │   │   ├── __init__.py                        # 导出 Citation、KnowledgeHubResponse 等响应层公共契约
│   │   │   ├── response_builder.py                # 构建格式化上下文、引用、图片和空结果标记
│   │   │   ├── citation_builder.py                # 组装引用来源和文档出处
│   │   │   ├── multimodal_assembler.py            # 批量解析 image_refs 并组装公开图片信息
│   │   │   └── evidence_context_optimizer.py      # 将编号证据整理为 Agent-ready final context
│   │   └── trace/
│   │       ├── trace_context.py                   # 单次 ingestion/query 的 trace 上下文
│   │       └── trace_controller.py                # trace 阶段记录和 flush 编排
│   ├── libs/
│   │   ├── loader/
│   │   │   ├── base_loader.py                     # Loader 最小抽象接口
│   │   │   ├── loader_factory.py                  # 根据配置创建 Loader 实现
│   │   │   ├── markdown_loader.py                 # Markdown 文档加载实现
│   │   │   └── pdf_loader.py                      # PDF 转 Markdown 并抽取图片的加载实现
│   │   ├── llm/
│   │   │   ├── base_llm.py                        # LLMClient 最小抽象接口
│   │   │   ├── base_vision_llm.py                 # Vision LLM 最小抽象接口
│   │   │   ├── llm_factory.py                     # 根据配置创建 LLMClient
│   │   │   ├── openai_client.py                   # OpenAI Chat 实现
│   │   │   ├── azure_openai_client.py             # Azure OpenAI Chat 实现
│   │   │   ├── ollama_client.py                   # Ollama 本地 LLM 实现
│   │   │   ├── deepseek_client.py                 # DeepSeek 兼容接口实现
│   │   │   └── dashscope_vision_llm.py            # 百炼 Qwen-VL 图片 caption 实现
│   │   ├── splitter/
│   │   │   ├── base_splitter.py                   # Splitter 最小抽象接口
│   │   │   ├── splitter_factory.py                # 根据配置创建 Splitter
│   │   │   └── recursive_character_splitter.py    # LangChain RecursiveCharacterTextSplitter 包装
│   │   ├── transform/
│   │   │   └── base_transform.py                  # Transform 最小抽象接口
│   │   ├── embedding/
│   │   │   ├── base_embedding.py                  # EmbeddingClient 最小抽象接口
│   │   │   ├── embedding_factory.py               # 根据配置创建 EmbeddingClient
│   │   │   ├── openai_embedding.py                # OpenAI 兼容 Embedding 实现，支持百炼 text-embedding-v4
│   │   │   └── fake_embedding.py                  # 单元测试使用的假 embedding 实现
│   │   ├── vector_store/
│   │   │   ├── base_vector_store.py               # VectorStore 最小抽象接口
│   │   │   ├── vector_store_factory.py            # 根据配置创建向量存储实现
│   │   │   └── pgvector_store.py                  # PostgreSQL pgvector 实现
│   │   ├── reranker/
│   │   │   ├── base_reranker.py                   # Reranker 最小抽象接口
│   │   │   ├── reranker_factory.py                # 根据配置创建 Reranker
│   │   │   ├── cross_encoder_reranker.py          # Cross-Encoder 精排实现
│   │   │   └── llm_reranker.py                    # LLM Rerank 实现
│   │   └── evaluator/
│   │       ├── base_evaluator.py                  # Evaluator 最小抽象接口
│   │       ├── evaluator_factory.py               # 根据配置创建评估器
│   │       ├── ragas_evaluator.py                 # Ragas 指标评估实现
│   │       └── custom_evaluator.py                # Hit Rate、MRR、NDCG 等自定义指标实现
│   ├── ingestion/
│   │   ├── pipeline.py                            # 离线摄取与 Indexing Pipeline MVP 统一编排
│   │   ├── document_summarizer.py                 # Loader 后生成 Document.summary 的独立摘要步骤
│   │   ├── chunk/
│   │   │   ├── splitter_step.py                   # 调用 splitter 并生成初始 chunk
│   │   │   ├── document_chunker.py                # 将 Document 适配为符合 core.types 契约的 Chunk
│   │   │   └── chunk_id.py                        # 生成 hash(source_path + section_path + content_hash)
│   │   ├── transform/
│   │   │   ├── transformer.py                     # Transform 串行主编排
│   │   │   ├── metadata_enricher.py               # metadata 注入 Transform 实现
│   │   │   ├── chunk_rewriter.py                  # 利用 LLM 对 chunk 进行语义改写
│   │   │   ├── semantic_merge_transform.py        # 合并逻辑相关但被物理切割的 chunk
│   │   │   ├── denoise_transform.py               # 去除页眉页脚、重复目录和解析噪声
│   │   │   └── image_captioner.py                 # 根据 image_refs 生成 caption 并写入 chunk 正文
│   │   ├── embedding/
│   │   │   ├── embedding_step.py                  # Embedding 阶段主编排
│   │   │   ├── dense_encoder.py                   # Dense 向量编码
│   │   │   ├── bm25_indexer.py                    # BM25Indexer 倒排索引构建
│   │   │   └── batch_processor.py                 # 批处理、限流和重试优化
│   │   ├── storage/
│   │   │   └── upsert_step.py                     # 写入 chunk、向量、BM25 和图片索引
│   │   ├── loader.py                              # 调用 libs.loader 并输出 Document
│   │   └── pdf_to_markdown.py                     # PDF 转 Markdown 辅助逻辑
│   ├── storage/
│   │   ├── postgres.py                            # PostgreSQL 连接池和事务封装
│   │   ├── schema.sql                             # PostgreSQL/pgvector 表结构
│   │   ├── vector_storage.py                      # 向量存储 repository
│   │   ├── bm25_storage.py                        # BM25 倒排索引和词项统计存储
│   │   ├── image_storage.py                       # image_index 表和原始图片索引存储
│   │   ├── trace_log_storage.py                   # Trace JSON Lines 日志写入和读取
│   │   └── repositories.py                        # 文档、chunk、评估等通用 repository
│   ├── logs/
│   │   ├── app.log                                # 应用运行日志
│   │   └── traces.jsonl                           # ingestion/query 结构化 Trace 日志
│   ├── cache/
│   │   ├── embedding/                             # embedding 批处理和差量计算缓存
│   │   ├── captions/                              # Vision LLM 图片描述缓存
│   │   └── processing/                            # 摄取过程中的临时处理缓存
│   ├── scripts/
│   │   ├── run_dashboard.py                       # 启动 Streamlit Dashboard
│   │   ├── run_evaluation.py                      # 运行黄金测试集评估任务
│   │   ├── query.py                               # 本地执行完整 hybridsearch + rerank 查询
│   │   └── ingest.py                              # 本地执行离线文档摄取
│   ├── observability/
│   │   ├── structured_log.py                      # Python logging + JSONFormatter 配置
│   │   ├── services/
│   │   │   ├── config_reader.py                   # Dashboard 读取 settings 和组件配置
│   │   │   ├── data_browser_service.py            # Dashboard 查询文档、chunk、图片数据
│   │   │   ├── trace_reader_service.py            # Dashboard 读取 query/ingestion trace
│   │   │   ├── ingestion_operation_service.py     # Dashboard 触发真实摄取操作
│   │   │   └── evaluation_service.py              # Dashboard 运行评估和读取历史趋势
│   │   ├── pages/
│   │   │   ├── overview.py                        # 系统总览页面
│   │   │   ├── query_trace.py                     # Query Trace 页面
│   │   │   ├── ingestion_trace.py                 # Ingestion Trace 页面
│   │   │   ├── ingestion_manage.py                # Ingestion 管理页面
│   │   │   ├── data_browser.py                    # 数据浏览器页面
│   │   │   └── evaluation.py                      # 评估面板页面
│   │   ├── dashboard/
│   │   │   ├── app.py                             # Streamlit Dashboard 入口
│   │   │   └── layout.py                          # Dashboard 公共布局
│   │   └── evaluation/
│   │       ├── runner.py                          # 评估任务运行器
│   │       ├── metrics.py                         # 自定义指标实现
│   │       └── ragas_adapter.py                   # Ragas 指标适配
│   ├── mcp_server/
│   │   ├── server.py                              # Python 官方 MCP SDK server 入口
│   │   └── tools.py                               # query_knowledge_hub 等 MCP tools
│   └── adapter/
│       └── aimodel_tool.py                        # AImodel LangChain tool 适配层
└── tests/
    ├── test_smoke.py                              # 独立模块导入、main.py 导入和配置样例冒烟测试
    ├── unit/
    │   ├── test_config.py                         # settings.yaml、prompt 和环境变量配置读取测试
    │   ├── test_types.py                          # Document、Chunk、ImageMetadata 等核心类型测试
    │   ├── test_loader.py                         # Loader 解析、元数据和图片引用测试
    │   ├── test_splitter.py                       # Splitter 语义切分和 image_refs 测试
    │   ├── test_transformer.py                    # Transform、ImageCaptioner、caption 和幂等性测试
    │   ├── test_embedding.py                      # Embedding 差量计算、双路编码和批处理测试
    │   ├── test_bm25.py                           # BM25 分词、倒排索引和关键词召回测试
    │   ├── test_retrieval.py                      # Dense/BM25/RRF/过滤测试
    │   ├── test_reranker.py                       # Rerank 排序和 fallback 测试
    │   ├── test_trace_context.py                  # TraceContext record_stage/flush 测试
    │   └── test_factories.py                      # libs 内 factory 配置驱动测试
    ├── integration/
    │   ├── test_repositories.py                   # PostgreSQL schema 和 repository 测试
    │   ├── test_ingestion_pipeline.py             # 离线摄取与索引编排完整链路测试
    │   ├── test_query_pipeline.py                 # 查询链路和引用结果测试
    │   ├── test_mcp_tools.py                      # MCP tools 契约测试
    │   ├── test_dashboard_services.py             # Dashboard services 读取数据测试
    │   └── test_dashboard_pages.py                # Dashboard 六大页面渲染和数据注入测试
    ├── e2e/
    │   ├── test_offline_ingestion_idempotency.py  # 离线摄取和幂等性端到端测试
    │   ├── test_recall_quality.py                 # Hybrid/Dense/BM25 召回质量端到端测试
    │   ├── test_full_rag_flow.py                  # 摄取、索引、查询、Trace、Dashboard 的全链路 E2E 验收
    │   └── test_aimodel_rag_tool.py               # AImodel RAG 工具端到端测试
    └── fixtures/
        ├── shopping_guides/                       # 测试用购物指南文档
        ├── noisy_documents/                       # 页眉页脚、目录、水印和解析断行等噪声样本
        ├── images/                                # 测试图片素材
        └── golden_set.json                        # 黄金测试集
```

### 5.3 模块职责说明表

#### 5.3.1 配置与数据层

| 文件 | 具体职责 | 关键技术点 |
| --- | --- | --- |
| `README.md` | 说明 RAG 独立模块的定位、启动方式和常用命令 | 面向开发者和部署人员，包含 Docker、pytest、Dashboard、MCP 入口 |
| `pyproject.toml` | 管理 Python 项目元数据、依赖和测试配置 | PEP 621、pytest markers、可选 extras、统一工具配置 |
| `uv.lock` | 锁定完整 Python 依赖图 | 由 `uv lock` 生成并提交；本地、CI 和 Docker 使用 `--frozen` 校验，不手工编辑 |
| `main.py` | 提供独立运行入口 | 可启动 FastAPI/MCP 服务，也可分发到 ingestion、query、dashboard 调试命令 |
| `Dockerfile` | 构建独立 RAG 服务镜像 | Python 3.12、固定版本 uv、`uv sync --frozen --no-dev`、非 root 运行、健康检查预留 |
| `.dockerignore` | 控制 Docker 构建上下文 | 排除缓存、日志、测试数据和本地数据库文件 |
| `.gitignore` | 控制 RAG 模块本地忽略文件 | 排除本地 `config/settings.yaml`、`src/logs/*.log`、`src/cache/`、`data/db/`、临时图片和模型缓存 |
| `config/settings.example.yaml` | 提供完整版本化配置模板 | 展示 LLM、Embedding、Splitter、Transform steps、VectorStore、Reranker、Evaluator 配置和参数 |
| `config/settings.yaml` | 管理本地运行配置和组件选择 | 由示例模板复制，允许环境定制并被 Git 忽略 |
| `config/prompts/rerank_prompt.yaml` | 保存 rerank 阶段提示词 | prompt 与代码分离，便于评估不同 rerank 策略 |
| `config/prompts/document_summary_prompt.yaml` | 保存文档级摘要提示词 | 在 Loader 后生成 `Document.summary`，为 chunk rewrite 提供全局语义上下文；首版通过 `ingestion.document_summary.llm_provider=deepseek` 显式使用 DeepSeek |
| `config/prompts/rewrite_chunk_prompt.yaml` | 保存 chunk 语义改写提示词 | 支持 Transform 阶段结合 `Document.summary` 做 chunk rewrite；Prompt 只接收 chunk 正文和文档摘要，不接收 metadata 或 image_refs；输出只允许把 searchable text 写入 `text` 字段，禁止把 metadata/image_refs 报告写入正文 |
| `config/prompts/semantic_merge_prompt.yaml` | 保存相邻 chunk 合并判断提示词 | 仅合并逻辑连续内容，要求结构化 merge 决策和合并文本 |
| `config/prompts/image_caption_prompt.yaml` | 保存图片 caption 提示词 | 使用英文 Prompt 指令，按图片类型生成可检索的简体中文描述，并原样保留图片中的文字 |
| `data/raw/shopping_guides/` | 存放 shopping_guides collection 原始文档 | 按 collection 分类，便于离线摄取和回归测试 |
| `data/db/postgres/` | 存放 PostgreSQL 本地开发辅助数据 | 保存初始化辅助文件、dump 或本地持久化数据 |
| `data/db/bm25/` | 存放 BM25 本地索引辅助数据 | 保存倒排索引和词项统计缓存 |
| `tests/fixtures/golden_set.json` | 存放黄金测试集 | JSON 格式，包含问题、标准答案、来源文档和关键词；与 `evaluation.golden_set_path` 保持一致 |

#### 5.3.2 Core 层

| 文件 | 具体职责 | 关键技术点 |
| --- | --- | --- |
| `src/core/config.py` | 加载 settings 和 prompt 配置 | Pydantic/YAML 校验、环境变量覆盖、默认值处理 |
| `src/core/types.py` | 定义核心数据结构 | `Document(id,text,summary,metadata)`、`Chunk(id,text,chunk_index,start_offset,end_offset,source_ref)`、`RetrievalResult(chunk_id,text,score,metadata)`、`Document.metadata.images[]` 的 `id/path` 契约、`Citation`、`TraceRecord` |
| `src/core/errors.py` | 定义统一异常类型 | 配置错误、Provider 错误、检索错误、摄取错误、MCP 错误 |
| `src/core/bm25_analyzer.py` | 统一 BM25 词法分析和候选契约 | 摄取与在线查询复用相同英文/数字 normalize、中文 full-span 与 2/3-gram 分词，避免分析漂移和 ingestion/storage 循环依赖 |
| `src/core/query_engine/query_processor.py` | 处理用户 query | normalize、可选 rewrite、collection/top_k 解析、意图识别 |
| `src/core/query_engine/hybrid_engine.py` | 编排混合检索主流程 | `HybridSearch`、Dense/BM25 双路召回、RRF Fusion、候选去重、保留过滤前 fusion 快照、rerank 前 metadata 过滤、单路失败降级 |
| `src/core/query_engine/dense_route.py` | 执行语义向量召回 | Query Embedding、pgvector search、返回 `RetrievalResult(chunk_id,text,score,metadata)` |
| `src/core/query_engine/sparse_route.py` | 执行关键词召回 | `ProcessedQuery.keywords`、`bm25_indexer.query()`、`vector_store.get_by_ids()` 回表、返回 `RetrievalResult`，并将 `Chunk.source_ref` 深拷贝到 result metadata 供 CitationBuilder 使用 |
| `src/core/query_engine/fusion.py` | 融合 Dense/BM25 结果 | RRF 基于排名倒数加权，不直接比较不同分数 |
| `src/core/query_engine/trace_snapshots.py` | 构造 Query Trace 候选快照 | 输出不含正文的轻量候选快照；Dense/Sparse 只记录 chunk IDs，Fusion/Filter/Rerank 记录排序与过滤变化 |
| `src/core/query_engine/reranker.py` | 编排过滤后候选的精排与降级 | `RerankController` 调用 Cross-Encoder/LLM Reranker；provider 缺失、超时、异常或返回过滤集外候选时 fallback 到调用前保存的过滤后 RRF 顺序；`RerankOutcome` 显式返回最终结果、fallback 状态和原因，禁止从 provider metadata 推断控制流；输出和 fallback 均使用防御性副本并记录低侵入 rerank trace |
| `src/core/response/response_builder.py` | 构建 RAG 工具公开响应 | `KnowledgeHubResponseBuilder` 先从最终排序 chunk 文本生成编号证据块，再调用可选 `EvidenceContextOptimizer` 生成 Agent-ready final context；优化失败时按配置 fallback 到原始编号证据块；不序列化内部 route/tool metadata |
| `src/core/response/evidence_context_optimizer.py` | 优化最终上下文 | 读取 `evidence_context_prompt.yaml`，调用统一 `BaseLLM.chat()` 将编号证据压缩、去重和结构化为供 AImodel 直接使用的上下文；禁止生成最终答案或动态商品事实 |
| `src/core/response/__init__.py` | 导出响应层公共契约 | 为 MCP、AImodel、CLI 和 Dashboard 稳定导出 Citation、KnowledgeHubResponse、ResponseImage 及其 Builder/Assembler |
| `src/core/response/citation_builder.py` | 从最终排序结果构建引用来源 | `source_ref` 优先、顶层 metadata 兼容、标题文件名回退、section_path 归一化、trace_id 关联、缺失来源 fail fast、不从 chunk 正文猜测 citation |
| `src/core/response/multimodal_assembler.py` | 组装多模态命中内容 | 按最终检索顺序收集、去重 `image_refs`，通过最小 `ImageResolver.find_by_ids()` 接口批量读取图片索引，恢复首次引用顺序，只投影 file_path、caption、尺寸、质量状态和关联 chunk IDs |
| `src/core/trace/trace_context.py` | 管理单次 trace 上下文 | `trace_id`、基础信息、阶段列表、汇总指标、评估指标；主阶段可携带经过校验和防御性复制的 `sub_stages`，Transform 子阶段可携带受限 snapshots 和 JSON-safe `details`，用于保留 `image_captioner` 等实现的 provider、model、计数与失败原因 |
| `src/core/trace/trace_controller.py` | 编排 trace 写入 | `record_stage()`、`flush()`、错误和 fallback 记录 |

#### 5.3.3 Libs 可插拔抽象层

| 文件 | 具体职责 | 关键技术点 |
| --- | --- | --- |
| `src/libs/loader/base_loader.py` | 定义 Loader 抽象接口 | `load(source) -> Document(id + text + summary + metadata)` |
| `src/libs/loader/loader_factory.py` | 创建 Loader 实现 | 根据文件类型和配置选择 Markdown/PDF Loader |
| `src/libs/loader/markdown_loader.py` | 加载 Markdown 文档 | 提取标题层级、metadata、图片引用 |
| `src/libs/loader/pdf_loader.py` | 加载 PDF 文档 | PDF -> Markdown、图片提取、优先按 PyMuPDF 邻近文本锚点插入图片占位符，锚点不可用时再按页标记或稳定追加降级 |
| `src/libs/llm/base_llm.py` | 定义 LLMClient 抽象接口 | `chat(messages) -> response` |
| `src/libs/llm/base_vision_llm.py` | 定义 Vision LLM 抽象接口 | `caption_image(image_path, prompt) -> VisionCaptionResponse`，只暴露图片 caption 所需的最小接口 |
| `src/libs/llm/llm_factory.py` | 创建 LLMClient | 根据 settings 选择 OpenAI/Azure/Ollama/DeepSeek |
| `src/libs/llm/openai_client.py` | OpenAI Chat 实现 | OpenAI SDK、统一 messages 输入输出 |
| `src/libs/llm/azure_openai_client.py` | Azure OpenAI Chat 实现 | Azure endpoint、deployment、api-version |
| `src/libs/llm/ollama_client.py` | Ollama 本地 LLM 实现 | 本地模型调用、离线降级 |
| `src/libs/llm/deepseek_client.py` | DeepSeek 兼容接口实现 | OpenAI-compatible chat API |
| `src/libs/llm/dashscope_vision_llm.py` | 百炼 Vision LLM 实现 | 使用 Qwen-VL-Max 生成图片 caption；读取 `DASHSCOPE_API_KEY` 和 `DASHSCOPE_BASE_URL`，返回统一 `VisionCaptionResponse` |
| `src/libs/splitter/base_splitter.py` | 定义 Splitter 抽象接口 | 纯文本工具，接口固定为 `split(text: str) -> List[str]` |
| `src/libs/splitter/splitter_factory.py` | 创建 Splitter | 根据配置选择 splitter 实现 |
| `src/libs/splitter/recursive_character_splitter.py` | 包装 LangChain splitter | 只输出文本片段 `List[str]`，不创建业务 `Chunk`，不引入 LangChain RAG 链路 |
| `src/libs/transform/base_transform.py` | 定义 Transform 抽象接口 | `transform(chunks, context) -> chunks`；具体执行顺序由 ingestion pipeline 负责 |
| `src/libs/embedding/base_embedding.py` | 定义 EmbeddingClient 抽象接口 | `embed(text)`、`embed_batch(texts)` |
| `src/libs/embedding/embedding_factory.py` | 创建 EmbeddingClient | 根据配置选择 OpenAI/fake embedding |
| `src/libs/embedding/openai_embedding.py` | OpenAI 兼容 embedding 实现 | 百炼 `text-embedding-v4`、1536 维、批量调用和响应顺序恢复 |
| `src/libs/embedding/fake_embedding.py` | 测试 embedding 实现 | 单元测试稳定向量，不访问外部 API |
| `src/libs/vector_store/base_vector_store.py` | 定义 VectorStore 抽象接口 | `upsert(chunks)`、`search(vector, filters, top_k)` |
| `src/libs/vector_store/vector_store_factory.py` | 创建向量存储实现 | 首版创建 pgvector store，预留扩展 |
| `src/libs/vector_store/pgvector_store.py` | pgvector 实现 | PostgreSQL vector(1536)、cosine search、metadata filter；Dense search 同时读取独立 `source_ref` 列并注入 RetrievalResult metadata |
| `src/libs/vector_store/fake_vector_store.py` | 内存 VectorStore 测试实现 | cosine search、metadata filter、ID 顺序恢复，并与 pgvector 保持 source_ref 引用传播契约 |
| `src/libs/reranker/base_reranker.py` | 定义 Reranker 抽象接口 | `rerank(query, candidates)` |
| `src/libs/reranker/reranker_factory.py` | 创建 Reranker | Cross-Encoder、LLM Rerank、None/fallback |
| `src/libs/reranker/cross_encoder_reranker.py` | Cross-Encoder 精排实现 | query-document pair 打分、排序 |
| `src/libs/reranker/llm_reranker.py` | LLM Rerank 实现 | prompt 驱动排序、超时 fallback |
| `src/libs/evaluator/base_evaluator.py` | 定义 Evaluator 抽象接口 | `evaluate(dataset, predictions) -> metrics` |
| `src/libs/evaluator/evaluator_factory.py` | 创建 Evaluator | Ragas 或自定义指标 |
| `src/libs/evaluator/ragas_evaluator.py` | Ragas 指标实现 | Faithfulness、Answer Relevancy |
| `src/libs/evaluator/custom_evaluator.py` | 自定义指标实现 | Hit Rate、MRR、NDCG、citation_hit_rate |

#### 5.3.4 Ingestion 层

| 文件 | 具体职责 | 关键技术点 |
| --- | --- | --- |
| `src/ingestion/pipeline.py` | 编排离线摄取与索引主流程 | 编排 dedup -> load -> document_summary -> split -> transform -> existing content_hash vector lookup -> Dense/BM25 batch -> transactional upsert -> lifecycle success；图片 caption 记录在 `transform.sub_stages.image_captioner`；支持 Loader-only 模式并拒绝部分依赖和空 chunk 快照 |
| `src/ingestion/loader.py` | 调用 Loader 并输出 Document | 去重通过后的 Loader 调用和 Document 标准化 |
| `src/ingestion/document_summarizer.py` | 生成文档级语义摘要 | 读取 `document_summary_prompt.yaml`，调用统一 LLMClient，写入 `Document.summary`，按 prompt version 和文档 hash 保持幂等 |
| `src/ingestion/pdf_to_markdown.py` | PDF 转 Markdown 辅助逻辑 | MarkItDown、页码、图片抽取、基于图片矩形和相邻文本块生成图片锚点 |
| `src/ingestion/chunk/splitter_step.py` | 执行 chunk 初始切分 | 调用 `DocumentChunker`，完成 `Document -> List[Chunk]` 业务适配 |
| `src/ingestion/chunk/document_chunker.py` | 业务 chunk 适配器 | 调用 `libs.splitter` 的 `str -> List[str]` 能力，生成 `chunk_id`、保留检索过滤所需 metadata、添加 `chunk_index`、建立 `source_ref`、通过占位符扫描分发 `image_refs`，并把纯图片占位符片段合并到相邻正文 chunk |
| `src/ingestion/chunk/chunk_id.py` | 生成稳定 chunk_id | `hash(source_path + section_path + content_hash)` |
| `src/ingestion/transform/transformer.py` | 编排 Transform 阶段 | 从 `settings.transform.steps` 读取顺序并串行执行；通过可选 observer 输出每个实现的耗时、输入输出数量、变化/未变化数量、状态、错误和受限 before/after 快照 |
| `src/ingestion/transform/metadata_enricher.py` | metadata 注入实现 | 标题路径、来源、文档主题、业务 metadata 注入 |
| `src/ingestion/transform/chunk_rewriter.py` | LLM 改写 chunk | 使用 `Document.summary` 作为全局上下文；将 chunk 拆分为文本节点与图片节点，只改写文本节点，再按原顺序重组图片占位符 |
| `src/ingestion/transform/semantic_merge_transform.py` | 智能合并 chunk | 合并逻辑相关但被物理切割的 chunk，保留 source_ref 和 image_refs |
| `src/ingestion/transform/denoise_transform.py` | 去噪处理 | 删除页眉页脚、重复目录、解析残留，保留图片占位符 |
| `src/ingestion/transform/image_captioner.py` | 图片 caption 编排 | `vision_llm.enabled` 判断、`image_refs` 条件触发、占位符替换为 `[[image_caption:image_id]] + caption`、trace 执行详情输出 |
| `src/ingestion/embedding/embedding_step.py` | 编排 Embedding 阶段 | `run_dense()` 提供窄粒度差量编码；`run_batch()` 复用数据库已有 content_hash 向量、对当前批次重复内容只调用一次模型，并为每个有序 chunk 生成完整 Dense 结果，同时编排 BM25Indexer |
| `src/ingestion/embedding/dense_encoder.py` | DenseEncoder | content_hash 计算、差量判断、单 chunk `embed()` 编码和 C8 批量 `embed_batch()` 编码；不承担 retry、upsert 或 BM25 职责 |
| `src/ingestion/embedding/bm25_indexer.py` | BM25Indexer | 提供 in-memory BM25 词频、倒排索引构建和关键词候选查询；复用 core analyzer，并接受可选 collection 参数以保持 Sparse Route 最小接口一致 |
| `src/ingestion/embedding/batch_processor.py` | 批处理优化 | 按 batch_size 拆分任务，支持可配置 throttle_seconds 节流、失败批次按 item 隔离、有限 retry、失败记录和有序成功结果返回 |
| `src/ingestion/storage/upsert_step.py` | 写入摄取结果 | 校验完整文档快照，复制受管图片，并在单一事务中写入 document/chunk/vector/BM25/image_index；失败时回滚并保持输入顺序 |

#### 5.3.5 Storage 与本地运行层

| 文件 | 具体职责 | 关键技术点 |
| --- | --- | --- |
| `src/storage/postgres.py` | 管理 PostgreSQL 连接 | 连接池、事务、超时、健康检查；按 `database.timezone` 初始化 session timezone，默认北京时间 |
| `src/storage/schema.sql` | 定义数据库 schema | pgvector extension、documents、chunks、`rag_bm25_terms`、`image_index`、traces、evaluation |
| `src/storage/vector_storage.py` | 管理向量存储 | pgvector upsert/search、metadata filter |
| `src/storage/bm25_storage.py` | 管理 BM25 索引数据 | 支持 document 级 posting 快照替换和 collection 隔离的 PostgreSQL BM25 查询，按当前语料动态计算 corpus stats 并输出有序候选 |
| `src/storage/image_storage.py` | 管理图片文件和索引 | 原始图片保存到 `data/images/{collection}/`；支持安全路径解析、原子文件替换、调用方事务内 image_index upsert，以及 Response Builder 使用的 `find_by_ids()` 批量查询 |
| `src/storage/trace_log_storage.py` | 管理 trace 日志读写 | `traces.jsonl` 追加写入和 Dashboard 读取 |
| `src/storage/repositories.py` | 管理通用 repository | documents、chunks、source_hash 去重查询、成功文档 content_hash 向量复用查询、traces、evaluation_runs |
| `src/logs/app.log` | 保存应用运行日志 | 普通运行日志和错误排查 |
| `src/logs/traces.jsonl` | 保存结构化 trace 日志 | ingestion/query trace JSON Lines |
| `src/cache/embedding/` | 缓存 embedding 结果 | content_hash 差量计算和重复请求复用 |
| `src/cache/captions/` | 缓存图片描述 | image_hash 命中后跳过 Vision LLM |
| `src/cache/processing/` | 缓存摄取中间状态 | PDF 转换、临时图片、失败恢复 |
| `src/scripts/run_dashboard.py` | 启动 Dashboard | 加载本地 `.env`、校验 Streamlit app 可导入、构建无 shell 的 `streamlit run` 命令，支持 dry-run 和注入命令执行器，测试不真实打开浏览器 |
| `src/scripts/run_evaluation.py` | 运行评估任务 | 读取 golden_set.json，输出指标并写库 |
| `src/scripts/query.py` | 本地查询调试 | 配置驱动组装 QueryProcessor、Dense、持久化 BM25 Sparse、RRF、Filter、可选 Rerank 和 Response Builder；支持安全 verbose 输出、schema 初始化和 PostgreSQL pool 生命周期管理 |
| `src/scripts/ingest.py` | 本地离线摄取 CLI | 自动发现父目录 `.env` 且不覆盖系统注入变量；递归发现 Markdown/PDF；将运行时相对路径固定解析到 RAG 根目录；读取默认 collection；配置驱动组装完整 Pipeline；转发 force；输出 JSON 结果并管理 PostgreSQL pool 生命周期 |

#### 5.3.6 Observability 层

| 文件 | 具体职责 | 关键技术点 |
| --- | --- | --- |
| `src/observability/structured_log.py` | 配置结构化日志 | Python logging + JSONFormatter |
| `src/observability/services/config_reader.py` | Dashboard 读取配置 | 展示当前启用组件和 provider |
| `src/observability/services/data_browser_service.py` | Dashboard 查询数据资产 | 文档、chunk、图片、metadata、索引状态 |
| `src/observability/services/trace_reader_service.py` | Dashboard 读取 trace | query/ingestion 历史、主阶段瀑布图、Transform 子阶段 DTO、Transform snapshot DTO、fallback 原因；兼容缺少 `sub_stages/snapshots` 字段的 trace |
| `src/observability/services/ingestion_operation_service.py` | Dashboard 摄取操作编排 | 接收页面提交的 collection/source_path/force，复用 ingestion pipeline/CLI 组装逻辑触发真实摄取，返回成功、跳过、失败、trace_id 和处理数量；不得只返回 pending DTO |
| `src/observability/services/evaluation_service.py` | Dashboard 运行评估 | 触发评估、读取历史趋势 |
| `src/observability/pages/overview.py` | 系统总览页面 | 组件配置、collection 统计、健康指标 |
| `src/observability/pages/query_trace.py` | Query Trace 页面 | Dense/BM25 对比、RRF、rerank 前后对比 |
| `src/observability/pages/ingestion_trace.py` | Ingestion Trace 页面 | 主阶段耗时瀑布图、Transform Breakdown、按 Transform 类型着色且用红绿标注文本变更的 Transform Result Diff、跳过原因和失败详情 |
| `src/observability/pages/ingestion_manage.py` | Ingestion 管理页面 | 文件选择、摄取进度、文档删除 |
| `src/observability/pages/data_browser.py` | 数据浏览器页面 | 文档列表、chunk 详情、图片引用 |
| `src/observability/pages/evaluation.py` | 评估面板页面 | 指标展示、历史趋势、策略对比 |
| `src/observability/dashboard/app.py` | Streamlit 入口 | 导入六大页面模块、提供 sidebar 页面导航、按选中页面组装 service-backed page model 并渲染；不在 import 阶段打开数据库或调用外部 Provider |
| `src/observability/dashboard/layout.py` | Dashboard 公共布局 | 导航、筛选器、通用图表容器 |
| `src/observability/evaluation/runner.py` | 评估任务运行器 | 读取黄金测试集、执行检索和生成评估 |
| `src/observability/evaluation/metrics.py` | 自定义指标 | Hit Rate、MRR、NDCG、citation_hit_rate |
| `src/observability/evaluation/ragas_adapter.py` | Ragas 适配 | Faithfulness、Answer Relevancy |

#### 5.3.7 外部接口层

| 文件 | 具体职责 | 关键技术点 |
| --- | --- | --- |
| `src/mcp_server/server.py` | 启动 MCP Server | Python 官方 MCP SDK、stdio/http 生命周期 |
| `src/mcp_server/tools.py` | 暴露 MCP tools | `query_knowledge_hub`、`list_collections`、`get_document_summary` |
| `services/ai-service/app/routers/AImodel/tools.py` | AImodel 工具适配 | 封装 `StdioMcpRagKnowledgeClient` 和 `search_shopping_guides`，隐藏内部 RAG/MCP JSON |

### 5.4 数据流设计

RAG 子系统的数据流分为三类：**离线摄取数据流**、**在线查询数据流** 和 **管理操作数据流**。离线摄取负责把原始资料变成可检索索引，在线查询负责把用户问题变成带引用的上下文结果，管理操作负责支撑 Dashboard 中的文档管理、数据浏览、Trace 查看和评估运行。

#### 5.4.1 离线摄取数据流

离线摄取数据流从 PDF、Markdown 等原始资料开始，先执行 SHA256 去重判断；只有确认文档发生变化后，才进入 Loader、Splitter、Transform、Embedding 和 Upsert，最终写入 PostgreSQL、pgvector、BM25 索引、图片存储和 trace 日志。

```text
[1] 原始文档
    PDF / Markdown / 商品说明文档 / 选购指南
    |
    v
[2] 文档 SHA256 去重判断
    - 直接基于原始文件计算 SHA256
    - 查询同一 collection + canonical source_path 的 rag_documents success 记录
    - 如果 source_hash 未变更：写入 skipped ingestion trace，流程结束
    - 如果 hash 已变更：继续执行 Loader
    |
    v
[3] Loader
    - PDF -> Markdown
    - 提取标题、来源、页码等 metadata
    - 提取图片，为图片生成 image_id
    - 在正文中保留图片占位符
    |
    v
[4] 生成 Document
    - Document = markdown + metadata + images
    |
    v
[5] Splitter
    - 语义感知切分，不做简单定长切分
    - 保留标题层级 section_path
    - 保留图片引用 image_refs
    |
    v
[6] Transform
    - LLM 重写 chunk
    - 元数据注入
    - 智能合并逻辑相关 chunk
    - 去除噪声内容
    |
    v
[7] ImageCaptioner
    - 判断 vision_llm.enabled
    - 判断 chunk metadata 是否存在 image_refs
    - 满足条件时生成 caption 并写入 metadata
    - 无 image_refs 或未启用 Vision LLM 时写入 skipped 状态
    |
    v
[8] Indexing Pipeline MVP
    - 编排 chunk content_hash 差量判断
    - 如果 content_hash 已存在：跳过 embedding，复用已有索引数据
    - 如果 content_hash 不存在：执行 Dense 向量生成、BM25Indexer 和批处理计算
    - 串联 Dense/BM25Indexer 和 upsert
    |
    v
[9] Upsert
    - 写入文档、chunk、pgvector、BM25、图片记录
    |
    v
[10] Ingestion Trace
    - 记录阶段耗时、跳过原因、失败详情和汇总指标
```

关键说明：

- 文档级去重依赖 `SHA256`，未变更文档直接结束，避免重复摄取。
- chunk 级差量依赖 `content_hash`，只对未命中或内容变更 chunk 执行 embedding。
- Transform 阶段负责把粗切分结果加工成更适合检索的知识片段，包括 **LLM 重写、元数据注入、图片描述生成、智能合并和去噪**。
- Upsert 阶段统一写入 PostgreSQL 相关表，并保持文档生命周期、chunk、向量、BM25 和图片记录的一致性。

#### 5.4.2 在线查询数据流

在线查询数据流从 AImodel、MCP 工具或本地 `query.py` 脚本请求开始，经过 Query Processor、HybridSearch、Rerank 前候选过滤、Rerank 和 Response Builder，最终返回带引用来源的上下文结果。

```text
[1] 用户问题 / AImodel 请求
    例如：帮我推荐高性价比无线耳机
    |
    v
[2] Query Processor
    - query 标准化
    - 用户意图识别
    - 可选 query rewrite
    - 判断是否需要商品 API 工具协同
    |
    v
[3] TraceContext
    - 创建 query trace
    - 记录基础请求信息
    |
    v
[4] HybridSearch
    |
    |-- Dense Route
    |   - 计算 Query Embedding
    |   - 检索 pgvector
    |   - 返回 List[RetrievalResult]
    |
    |-- Sparse Route
    |   - 使用 ProcessedQuery.keywords
    |   - bm25_indexer.query(keywords, top_k)
    |   - vector_store.get_by_ids(chunk_ids)
    |   - 返回 List[RetrievalResult]
    |
    |-- RRF Fusion
        - 基于排名倒数融合
        - 不直接比较 Dense 分数和 BM25 分数
    |
    v
[5] Rerank 前候选过滤
    - 根据 collection、doc_type、来源类型等参数过滤融合候选
    - deleted / failed / 无权限候选不会进入 Reranker
    |
    v
[6] Reranker 可用性判断
    - 如果可用：对过滤后的候选执行 Cross-Encoder 或 LLM Rerank，输出精排结果
    - 如果不可用 / 超时 / 异常：fallback 到过滤后的 RRF 排序结果
    |
    v
[7] Response Builder
    - 构造引用来源
    - 组装多模态内容
    - 隐藏内部工具调用细节
    |
    v
[8] 返回 MCP / AImodel / query.py
    - 上下文 + 引用 + trace_id
    |
    v
[9] Query Trace
     - 记录召回对比、rerank 前后变化和端到端耗时
```

关键说明：

- 在线查询不直接生成最终购物答案，而是为 AImodel 提供 **可引用的知识上下文**。
- Dense Route 解决语义相似问题，Sparse Route 解决关键词、品牌、型号、术语等精确匹配问题。
- HybridSearch 负责集成 Dense/BM25、候选去重和 RRF Fusion。
- RRF Fusion 基于排名融合，避免 Dense 分数和 BM25 分数量纲不同导致排序失真。
- 候选过滤必须发生在 Rerank 前，避免不符合 `collection`、`doc_type`、权限或生命周期状态的内容进入重排阶段。
- Reranker 不可用时必须优雅降级，保证查询链路仍然可以返回可用结果。
- Response Builder 负责隐藏内部工具细节，只返回适合 Agent 使用的格式化内容、引用和多模态材料。

#### 5.4.3 管理操作数据流

管理操作数据流服务于 Streamlit Dashboard，覆盖文档摄取、文档删除、数据浏览、Trace 查看、评估运行和组件配置查看等后台管理动作。

```text
[1] Dashboard 用户操作
    用户在 Streamlit 管理平台发起操作
    |
    v
[2] 操作类型判断
    |
    |-- A. 选择文件摄取
    |   |
    |   v
    |   Ingestion 管理页创建摄取任务
    |   |
    |   v
    |   执行离线摄取数据流
    |   |
    |   v
    |   写入 PostgreSQL / pgvector / BM25 / 图片存储 / ingestion trace
    |
    |-- B. 删除已摄入文档
    |   |
    |   v
    |   文档生命周期管理标记 deleted
    |   |
    |   v
    |   同步处理 chunk、向量、BM25、图片引用的可见状态或清理动作
    |   |
    |   v
    |   写入 PostgreSQL 和 traces.jsonl
    |
    |-- C. 浏览已索引数据
    |   |
    |   v
    |   数据浏览服务读取文档、chunk、图片和 metadata
    |   |
    |   v
    |   数据浏览器页面展示文档列表和 chunk 详情
    |
    |-- D. 查看 Query / Ingestion Trace
    |   |
    |   v
    |   Trace 读取服务读取 traces.jsonl
    |   |
    |   v
    |   Trace 页面展示耗时瀑布图、召回对比、rerank 前后变化和失败详情
    |
    |-- E. 运行评估
    |   |
    |   v
    |   评估服务读取黄金测试集
    |   |
    |   v
    |   Evaluation Runner 执行检索评估和生成评估
    |   |
    |   v
    |   评估结果写入 PostgreSQL
    |   |
    |   v
    |   评估面板展示指标对比和历史趋势
    |
    |-- F. 查看系统总览
        |
        v
        配置读取服务读取 settings.yaml
        |
        v
        系统总览页面展示组件配置、collection 数据资产和最近健康指标
```

关键说明：

- Dashboard 不直接绕过业务流程修改索引，摄取和删除都必须通过统一服务层执行，保证 trace、数据库和索引状态一致。
- 数据浏览和 Trace 查看以只读为主，用于解释“知识库里有什么”和“为什么这次查询召回这些内容”。
- 评估运行会把结果写入 PostgreSQL，便于在 Dashboard 中展示不同策略的量化对比和历史趋势。
- 管理操作数据流必须和离线摄取、在线查询共用配置和组件工厂，避免 Dashboard 展示的组件状态与真实运行状态不一致。
