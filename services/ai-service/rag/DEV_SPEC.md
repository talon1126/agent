# AImodel RAG DEV_SPEC

> 本文档用于指导 `services/ai-service/rag` 下模块化 RAG 系统的开发。文档面向后续开发者和 agent，要求实现过程遵循 TDD、配置驱动、模块化边界和英文代码注释规范。

## 1. 项目概述

### 1.1 项目定位

`AImodel RAG` 是 `ai-service` 内的检索增强生成子系统，用于为 AImodel 购物对话 agent 提供“可引用、可追踪、可评估”的知识检索能力。

首版定位不是替代商品搜索、商品详情、价格、库存和商品链接推荐。实时商品事实仍由现有后端商品 API 工具提供，RAG 负责补充以下知识：

- 品类选购指南，例如无线耳机、解压玩具、护肤品、家居好物、小家电。
- 平台政策和 FAQ，例如退换货、优惠券、物流、售后说明。
- 商品说明文档和品牌知识，例如使用方法、适用人群、避坑点。
- 对比决策标准，例如高性价比、送礼、学生党、敏感肌、降噪通勤等判断规则。

### 1.2 设计理念

本项目强调“架构清晰、易于讲解、可逐步替换”。所有核心能力都通过抽象接口和工厂模式接入，运行时通过 `settings.yaml` 切换 Provider。

核心原则：

- **本地优先**：Dashboard、Trace、评估和索引管理优先在本地完成。
- **可独立部署**：`services/ai-service/rag` 可作为独立 Python 模块通过 Docker 部署，也可以作为 `ai-service` 内部子系统被 AImodel 调用。
- **PostgreSQL 统一持久化**：文档、chunk、embedding、BM25 统计、摄取任务、Trace、评估结果全部写入 PostgreSQL。
- **pgvector 首发**：首版只实现 `pgvector` 向量库，接口层预留后续扩展。
- **不绑定 RAG 框架**：不使用 LlamaIndex，不使用 LangChain RAG 链路；仅允许使用 `langchain-text-splitters` 中的 splitter。
- **多模态轻量化**：图片采用 Image-to-Text 策略，由 Vision LLM 生成中文图片描述，再注入 chunk 文本，不使用 CLIP 多模态向量。
- **可观测和可评估**：Ingestion 和 Query 全链路都有 Trace，RAG 质量通过 Ragas 和自定义指标持续评估。
- **安全边界明确**：RAG 结果必须带来源引用，不允许替商品库编造商品名称、商品价格或商品链接。

### 1.3 首批知识库

首批 collection：

```text
shopping_guides
```

首批人工维护 Markdown 指南：

- 无线耳机选购指南
- 解压玩具选购指南
- 护肤品选购指南
- 家居好物选购指南
- 小家电选购指南

每篇指南建议使用统一结构：

```markdown
# 类目选购指南

## 适合人群
## 核心判断标准
## 高性价比怎么判断
## 常见坑点
## 推荐决策规则
## 用户常见问题
```

## 2. 核心特点

### 2.1 智能分块

传统 RAG 项目常见问题是把文档按固定长度硬切，容易把一个完整观点拆散，导致检索命中的片段缺少语义上下文。

本项目采用 **智能分块** 思路：不是简单定长切分，而是结合 Markdown 标题层级、段落结构、列表结构和语义边界进行切分，尽量让每个 chunk 保留一个相对完整的知识点。

这样做的价值是：

- 检索结果更容易命中完整观点，而不是零碎句子。
- Agent 总结时上下文更完整，回答更自然。
- 后续引用来源时，可以更清楚地对应到原文章节。

### 2.2 上下文增强

普通 chunk 往往只保存正文片段，一旦脱离原始文档，就可能看不懂“这里的它”“这个方法”“上述参数”指的是什么。

本项目会做 **上下文增强**：在 chunk 进入索引前，把标题路径、文档主题、关键元数据、相邻段落摘要和图片描述等信息补充到 chunk 中，让每个 chunk 尽量具备独立表达能力。

上下文增强的作用：

- 提升短问题和口语化问题的命中率。
- 降低模型拿到片段后误解上下文的概率。
- 让最终回答更容易解释“为什么推荐这个判断标准”。

### 2.3 混合检索

单一检索方式很难覆盖所有问题。关键词检索擅长命中明确词语，但不理解同义表达；向量检索能理解语义相似，但可能漏掉关键型号、品牌、术语。

本项目采用 **混合检索**：

- **稀疏检索 BM25**：根据关键词、词频和文档相关性进行匹配，适合品牌名、类目名、参数名、政策关键词等精确查询。
- **稠密检索 Dense Embedding**：使用百炼 `text-embedding-v4`（Qwen3-Embedding 系列）把问题和 chunk 转成向量，适合理解“高性价比”“适合送人”“通勤降噪”这类语义问题。

两路结果再做融合排序，既保留关键词检索的稳定性，也利用语义检索的泛化能力。

这个设计适合购物问答场景，因为用户既可能输入明确商品词，也可能输入非常主观的需求描述。

### 2.4 两段式重排

检索系统一般先追求“召回足够多”，再追求“排序足够准”。如果只靠第一阶段检索结果直接回答，可能会把相关但不够关键的片段排在前面。

本项目采用 **粗排到精排** 的两段式设计：

- 粗排阶段：用 BM25 和 Dense 检索快速召回候选片段。
- 精排阶段：支持 Cross-Encoder 或 LLM Rerank，对候选片段重新排序。

这样可以在性能和质量之间取得平衡：粗排保证速度和覆盖面，精排提升最终提供给 Agent 的上下文质量。

### 2.5 全链路可插拔

RAG 系统里最容易变化的是模型、向量库、重排器和评估方式。如果直接把某个 Provider 写死在业务代码里，后续替换成本会很高。

本项目采用 **可插拔架构**：LLM、Embedding、Reranker、VectorStore、Splitter、Evaluator 都通过抽象接口、工厂模式和 `settings.yaml` 配置创建。

首批支持方向包括：

- LLM：Azure OpenAI、OpenAI、Ollama、DeepSeek。
- Embedding：百炼 `text-embedding-v4`，通过 OpenAI 兼容接口调用。
- VectorStore：PostgreSQL + pgvector。
- Splitter：仅使用 LangChain 的 `RecursiveCharacterTextSplitter`，不依赖 LangChain RAG 框架。

这个设计的亮点是：项目可以清楚展示“工程可扩展性”，而不是只做一个临时 Demo。

### 2.6 MCP 集成

本项目会通过 **Python 官方 MCP SDK** 把 RAG 能力暴露为标准工具，让外部 agent 或开发工具可以直接调用知识库。

核心工具包括：

- `query_knowledge_hub`：查询知识库并返回带引用的结果。
- `list_collections`：查看当前有哪些知识集合。
- `get_document_summary`：获取某个文档的摘要和结构。

MCP 的价值是让 RAG 不只服务当前 AImodel，也可以作为一个通用知识服务被其他 agent 复用。

### 2.7 可视化管理平台

很多 RAG 项目只有后端接口，出了问题只能翻日志，难以向非开发者解释“知识库里有什么、检索为什么命中、策略调整有没有变好”。

本项目提供 **可视化管理平台**，用 Streamlit 构建本地 Dashboard，把摄取、检索、追踪和评估过程都放到页面上展示。

平台包含六大功能页面：

- **系统总览**：展示当前启用的可插拔组件，包括 LLM、Embedding、Splitter、Reranker 等
- **Ingestion 管理**：支持在页面选择文件进行摄入，实时展示 Markdown 转换、智能分块、上下文增强、Embedding、入库等阶段进度，也支持删除已摄入文档。
- **数据浏览器**：查询已经索引的文档和 chunk 详情，方便确认知识是否真的进入系统，以及 chunk 内容是否适合被检索。
- **Query 追踪**：查看查询历史、耗时瀑布图、Dense/BM25 召回对比，以及 rerank 前后的排名变化，用可视化方式解释一次查询是如何得到结果的。
- **Ingestion 追踪**：展示每次摄取任务在各阶段的耗时和状态，帮助定位是文件解析慢、分块慢、模型调用慢，还是数据库写入慢。
- **评估面板**：展示运行中的评估任务、各项指标对比和历史趋势对比，确保每一次分块、检索、重排或提示词策略调整都有量化分数支撑。

这个平台的亮点是把 RAG 从“黑盒问答接口”变成 **可观察、可调试、可量化优化的工程系统**。

### 2.8 可观测性

RAG 系统的难点不是“能不能回答”，而是回答不好时能不能定位原因。问题可能出在文档摄取、分块、embedding、检索、重排或最终生成。

本项目内置 **全链路可观测性**：

- Ingestion 链路记录文档转换、分块、增强、embedding、入库等步骤。
- Query 链路记录问题处理、稀疏检索、稠密检索、融合、重排和引用构造。
- Trace 使用结构化 JSON Lines 日志，并把关键索引写入 PostgreSQL。
- Dashboard 使用 Streamlit 本地启动，不依赖 LangSmith 等外部平台。

这个设计方便展示系统工程能力：不仅能做 RAG，还能解释 RAG 为什么这样回答。

### 2.9 质量评估

RAG 项目不能只靠人工体验判断效果，需要有可重复的质量评估。

本项目设计了 **可插拔评估体系**：

- 使用 Ragas 评估回答的忠实度、上下文相关性等生成质量。
- 使用自定义指标评估检索质量，例如 `hit_rate`、`MRR`、引用命中率、空结果率。
- 评估结果写入 PostgreSQL，并在 Dashboard 中展示趋势。

这样可以把 RAG 从“能跑的功能”提升为“能持续优化的系统”。

## 3. 技术选型

### 3.1 运行时技术栈

| 类别 | 首版选择 | 说明 |
| --- | --- | --- |
| 语言 | Python 3.12 | 与现有 `ai-service` 保持一致 |
| 包与环境管理 | uv | 统一依赖解析、`uv.lock` 锁定、`.venv` 创建、测试执行和 Docker 安装 |
| Web 服务 | FastAPI | AImodel 已使用 FastAPI |
| 数据库 | PostgreSQL | 唯一持久化层 |
| 向量库 | pgvector | 首版唯一实现 |
| Embedding | 百炼 `text-embedding-v4`（Qwen3-Embedding 系列） | 使用 1536 维，与现有 pgvector schema 保持一致 |
| Splitter | `langchain-text-splitters` | 只使用 splitter，不使用 LangChain RAG |
| PDF 转 Markdown | MarkItDown | 统一进入 Markdown 中间格式 |
| MCP | Python 官方 MCP SDK | 暴露 RAG tools |
| Dashboard | Streamlit | 本地轻量 Dashboard |
| 测试 | pytest | 单元、集成、E2E、评估测试 |

### 3.2 RAG 流水线设计

RAG 流水线分为两条主链路：**数据摄取流水线** 和 **检索流水线**。

整体设计参考 LlamaIndex 的分层思想，但不直接依赖 LlamaIndex 框架。项目内部自定义轻量接口，例如 `BaseLoader`、`BaseSplitter`、`BaseTransform`、`BaseEmbedding`、`BaseVectorStore`，让每一层都可以独立替换、组合和测试。

#### 3.2.1 流水线框架

数据摄取流水线负责把外部文件变成可检索的向量和索引数据：

```text
Dedup -> Loader -> Splitter -> Transform -> ImageCaptioner -> DenseEncoder/BM25Indexer -> BatchProcessor -> Upsert -> 文档生命周期管理
```

检索流水线负责把用户问题变成可引用的上下文结果：

```text
查询预处理 -> 双路混合检索 -> 候选过滤 -> 重排 -> 引用结果构造
```

流水线要支持 **可组合**：不同 Loader、Splitter、Transform、Embedding 和 VectorStore 可以通过配置组合成不同策略。例如首版使用 PDF/Markdown Loader + RecursiveCharacterTextSplitter + ImageCaptioner + DashScope Embedding + pgvector，后续可以替换某一层而不重写整条链路。

#### 3.2.2 数据摄取流水线

数据摄取的目标是先识别原始资料是否发生变化，再把 PDF、Markdown、文档说明等资料转换为统一的 `Document(id + text + metadata)` 对象，随后逐步加工为 chunk、embedding 和可追踪的索引记录。

| 层级 | 职责 | 关键实现要素 |
| --- | --- | --- |
| `Dedup` | 在进入 Loader 前判断原始文档是否需要摄取 | 每个文档先计算 SHA256 哈希纹；若 `rag_documents` 中同一 collection、canonical source_path 和 source_hash 的文档状态为 `success`，则写入 skipped ingestion trace 并直接结束，不再执行 PDF 转换、图片提取、splitter、transform 和 embedding |
| `BaseLoader` | 将不同来源的文件转换为统一 `Document(id + text + metadata)` 对象 | 负责文件识别、使用 MarkItDown 完成 PDF -> Markdown、使用 PyMuPDF 提取 PDF 图片、编码处理和基础 metadata 抽取；只处理去重判断后确认需要摄取的文档 |
| `BaseSplitter` | 纯文本切分工具 | 职责边界固定为 `str -> List[str]`，不直接接触 `Document`、`Chunk`、metadata、图片引用等业务对象；首版使用 LangChain `RecursiveCharacterTextSplitter` 作为底层 splitter |
| `DocumentChunker` | 将 `Document` 适配为业务 `Chunk` 对象 | 调用 `libs.splitter` 得到 `List[str]` 后，转换为符合 `core.types` 契约的 `List[Chunk]`；负责生成 `chunk_id`、继承 `document.metadata`、添加 `chunk_index`、计算 `start_offset/end_offset`、建立 `source_ref`，并按图片占位符位置分发 `image_refs` |
| `BaseTransform` | 对粗切分 chunk 做语义二次加工和上下文增强 | 利用 LLM 的语义理解能力合并逻辑上密切相关但被物理切割拆开的 chunk；去除页眉页脚、重复目录、无意义噪声和解析残留；注入标题路径、文档主题、相邻摘要、业务 metadata |
| `ImageCaptioner` | 对带图片引用的 chunk 生成图片 caption | 当 `vision_llm.enabled=true` 且 chunk 存在 `image_refs` 时调用 Vision LLM；生成 caption 后写入 chunk metadata；未启用 Vision LLM、无 `image_refs` 或生成失败时安全跳过并写入状态 |
| `BaseEmbedding` | 将增强后的 chunk 执行双路索引 | 在编码前先计算 `content_hash`，只对数据库中不存在的内容哈希执行新编码；DenseEncoder 调用百炼 `text-embedding-v4` 生成 1536 维语义向量；BM25Indexer 生成词项、词频和倒排索引；BatchProcessor 统一处理批量、限流、重试和失败隔离 |
| `BaseVectorStore` | 将 chunk、metadata、Dense 向量和 Sparse 检索数据写入 PostgreSQL | 首版只实现 PostgreSQL + pgvector；upsert 时保证同一文档版本的 chunk 可覆盖更新；`chunk_id` 使用 `hash(source_path + section_path + content_hash)` 生成，确保同一来源、同一章节、同一内容具有稳定标识 |
| 文档生命周期管理 | 管理文档从摄取、更新、删除到重建索引的状态 | 支持 `pending`、`processing`、`success`、`failed`、`deleted`；删除文档时同步删除对应 chunk、向量、BM25 统计和检索可见状态 |

chunk 标识规则：

```text
chunk_id = hash(source_path + section_path + content_hash)
```

其中 `source_path` 表示文档来源路径，`section_path` 表示标题层级或逻辑章节，`content_hash` 表示 chunk 最终文本内容哈希。这个规则让 chunk 在重复摄取、差量更新和引用追踪时保持稳定。

Splitter 职责边界：

- `libs.splitter`：纯文本切分工具，输入 `str`，输出 `List[str]`，只负责切分策略，不涉及业务对象。
- `DocumentChunker`：业务适配器，输入 `Document` 对象，输出 `List[Chunk]` 对象，负责补齐 chunk 业务字段和类型转换。

摄取链路的重点不是只完成入库，而是保证 **可重复执行、可差量计算、可跳过重复、可追踪失败、可删除重建**。

Indexing Pipeline 首版必须并入统一摄取入口：`IngestionPipeline.run()` 在完成 Loader、Splitter、Transform 和 ImageCaptioner 后继续调用 `IngestionPipeline.run_indexing()`，串联 `content_hash` 差量判断、Dense 向量编码、BM25Indexer 和 pgvector/BM25 upsert。该统一入口必须有集成测试，不能只实现分散的 encoder 或 upsert step。

#### 3.2.3 核心数据对象设计

RAG 流水线内部统一使用 `Document` 和 `Chunk` 作为核心数据对象。Loader 只负责生成 `Document`，Splitter 和 Transform 负责把 `Document` 加工为 `Chunk`，Embedding 和 Storage 只面向稳定的 `Chunk` 写入索引。

`Document` 字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `str` | 文档稳定 ID，建议由 `collection + source_path + source_hash` 生成 |
| `text` | `str` | 文档统一文本内容，PDF 先转 Markdown，图片位置写入占位符 |
| `metadata` | `dict` | 文档元数据，包含来源、标题、collection、hash、图片列表等信息 |

`Chunk` 字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `str` | chunk 稳定 ID，使用 `hash(source_path + section_path + content_hash)` |
| `text` | `str` | chunk 最终可检索文本，包含上下文增强和可选图片描述 |
| `chunk_index` | `int` | chunk 在当前 Document 中的排序，从 0 开始递增 |
| `start_offset` | `int` | chunk 在 `Document.text` 中的起始位置 |
| `end_offset` | `int` | chunk 在 `Document.text` 中的结束位置 |
| `source_ref` | `dict/null` | 可选来源引用，建议包含 `document_id`、`source_path`、`section_path`、`page`、`collection`，用于引用构造和 trace 回溯 |

`metadata.images[]` 字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `str` | 图片稳定 ID |
| `path` | `str` | 原始图片在文件系统中的存储路径 |
| `page` | `int/null` | 图片在原文中的页码；Markdown 图片可为空 |
| `text_offset` | `int` | 图片占位符在 `Document.text` 中的起始位置 |
| `text_length` | `int` | 图片占位符文本长度 |
| `position` | `dict` | 图片在原文中的物理位置信息，例如 `x`、`y`、`width`、`height`、`bbox` |

说明：

- 字段命名统一使用 `start_offset`，不使用 `start_offest`。
- `text_offset` 和 `text_length` 基于完整 `Document.text` 计算，Splitter 根据 offset 交集为 chunk 生成 `image_refs`。
- `source_ref` 是可选字段，但首版建议保留，方便 Dashboard 展示引用来源、原文位置和关联图片。
- `DocumentChunker` 必须把 `Document.metadata` 复制到 `Chunk.metadata`，再追加 `chunk_index`、`image_refs`、`source_ref` 等 chunk 级字段，避免丢失文档来源信息。

`RetrievalResult` 字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `chunk_id` | `str` | 命中的 chunk ID |
| `text` | `str` | 命中的 chunk 文本 |
| `score` | `float` | 当前检索路线返回的相关性分数；Dense/BM25 分数量纲不同，只记录，不直接互相比大小 |
| `metadata` | `dict` | chunk metadata，包含 collection、source_ref、section_path、image_refs、文档状态等过滤和引用信息 |

`ProcessedQuery` 是 Query Processor 向 Dense Route、Sparse Route、HybridSearch 和 Trace 传递的统一查询对象：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `raw_query` | `str` | 用户原始输入，保留用于 Trace 和问题回溯 |
| `normalized_query` | `str` | Unicode、全半角和空白归一化后的检索 query；rewrite 成功时保存 rewrite 结果 |
| `keywords` | `tuple[str, ...]` | 不可变的有序去重关键词快照，供 Sparse Route 查询 BM25 |
| `intent` | `str` | `knowledge_query`、`recommendation`、`comparison` 或 `product_lookup` |
| `collection` | `str` | 查询目标知识集合 |
| `top_k` | `int` | 最终期望返回数量 |
| `requires_product_tool` | `bool` | 是否需要商品 API 补充价格、库存、链接或具体商品事实 |
| `rewrite_applied` | `bool` | 是否成功应用 query rewrite |
| `rewrite_fallback_reason` | `str/null` | rewrite 异常或空结果时的稳定降级原因 |

Query rewrite 通过最小化 `QueryRewriter.rewrite(query)` 接口注入，Query Processor 不直接创建或判断具体 LLM Provider。未注入 rewriter 或配置关闭时直接使用原始标准化 query；Provider 异常或返回空白时自动 fallback，不阻断后续检索。

#### 3.2.4 检索流水线

检索流水线的目标是把用户自然语言问题转换为高质量上下文，供 AImodel 生成最终回答。

| 阶段 | 职责 | 关键实现要素 |
| --- | --- | --- |
| 查询预处理 | 清洗和理解用户问题 | 做 query normalize、关键词提取、可选 query rewrite；识别 collection、top_k、用户意图和是否需要商品工具协同 |
| 双路混合检索 | 同时召回关键词相关和语义相关的 chunk | **Dense Route**：输入 `ProcessedQuery`，计算 Query Embedding，检索 pgvector，返回 `List[RetrievalResult]`；**Sparse Route**：使用 `ProcessedQuery.keywords` 查询 BM25 倒排索引，按 `chunk_id` 回表读取 chunk 文本和 metadata，返回 `List[RetrievalResult]`；**Fusion** 先完成 RRF 排名融合；**HybridSearch** 依赖 Query Processor、Dense Route、Sparse Route 和 Fusion，并负责候选去重和单路降级 |
| Rerank 前候选过滤 | 在精排前过滤候选 | 支持按 `collection`、`doc_type`、来源类型、文档状态、权限和生命周期状态等参数过滤，避免不符合调用参数的内容进入 Reranker |
| 重排 | 提升最终上下文排序质量 | 支持 Cross-Encoder 和 LLM Rerank；只对过滤后的候选进行二次排序，观察 rerank 前后排名变化；当 rerank 服务不可用、超时或返回异常时，自动 fallback 到过滤后的 RRF 融合排序结果 |
| 引用结果构造 | 输出可被 Agent 使用的上下文和引用 | 返回答案上下文、来源标题、文档路径、章节、score、trace id；Agent 只能基于命中内容总结，不编造来源 |

RRF 融合不直接比较 Dense 分数和 BM25 分数，因为两类分数的量纲不同。融合时基于候选在各自检索结果中的排名进行倒数加权，排名越靠前贡献越大，从而让语义召回和关键词召回都能公平参与最终排序。

检索链路必须能解释每一步：原始 query 如何被预处理，BM25 和 Dense 各自召回了什么，哪些结果在 rerank 前被过滤，rerank 如何改变排序，最终引用来自哪里。

### 3.3 MCP 服务设计

MCP 工具一：`query_knowledge_hub`

输入：

```json
{
  "query": "如何挑选高性价比无线耳机？",
  "collection": "shopping_guides",
  "top_k": 5
}
```

输出：

```json
{
  "ok": true,
  "content": "可用于回答的知识摘要",
  "citations": [
    {
      "document_id": 1,
      "chunk_id": 12,
      "title": "无线耳机选购指南",
      "heading_path": ["核心判断标准"],
      "source_uri": "shopping_guides/wireless-earbuds.md",
      "score": 0.82
    }
  ],
  "trace_id": "query_20260604_xxx"
}
```

MCP 工具二：`list_collections`

用途：列出当前可检索 collection、文档数量、chunk 数量、最近更新时间。

MCP 工具三：`get_document_summary`

用途：按 `document_id` 或 `source_uri` 返回文档摘要、章节列表和摄取状态。

### 3.4 可插拔架构设计

可插拔架构的目标是让 RAG 系统的核心能力可以替换，但上层业务逻辑不需要感知底层 Provider 差异。业务代码只依赖统一接口，具体实现由配置和工厂决定。

核心设计原则：

- **接口隔离**：为每类组件定义最小化抽象接口，上层业务逻辑只依赖接口，不依赖具体实现类。
- **工厂模式**：使用工厂根据配置动态实例化实现类，避免在业务流程里写 Provider 判断逻辑。
- **配置驱动**：通过统一 `settings.yaml` 切换组件，无需修改代码即可替换 LLM、Embedding、Reranker、VectorStore 等能力。
- **优雅降级**：当前组件不可用时，应自动回退到默认或安全方案。例如 rerank 不可用时回退到 RRF 排序，LLM query rewrite 不可用时回退到原始 query。
- **统一调用方式**：同一类组件对外暴露一致方法，上层调用代码保持稳定。

通用结构示意：

```text
业务代码
  -> <Component>Factory.get_xxx(settings)
  -> 读取配置并决定具体实现
  -> {Implementation A | Implementation B | Implementation C}
  -> 都实现同一个最小化接口
```

#### 3.4.1 统一接口设计

| 组件 | 最小接口 | 说明 |
| --- | --- | --- |
| `LLMClient` | `chat(messages) -> response` | 上层只传入统一 messages，不关心 Azure OpenAI、OpenAI、Ollama、DeepSeek 的 SDK 差异 |
| `EmbeddingClient` | `embed(text) -> vector`，`embed_batch(texts) -> vectors` | 单文本和批量文本使用同一抽象，方便 ingestion 批处理和 query embedding |
| `RerankerClient` | `rerank(query, candidates) -> ranked_candidates` | Cross-Encoder 和 LLM Rerank 都实现同一排序接口 |
| `VectorStoreClient` | `upsert(chunks)`，`search(vector, filters, top_k)` | 首版实现 pgvector，上层检索流程不直接写 SQL |
| `SplitterClient` | `split(text) -> List[str]` | 首版包装 LangChain `RecursiveCharacterTextSplitter`，只做纯文本切分；业务 `Chunk` 由 `DocumentChunker` 生成 |
| `EvaluatorClient` | `evaluate(dataset, predictions) -> metrics` | Ragas 和自定义指标都走统一评估入口 |

LLM 和 Embedding Provider 必须统一调用接口：

```text
LLMClient.chat(messages) -> response
EmbeddingClient.embed(text) -> vector
EmbeddingClient.embed_batch(texts) -> vectors
```

这样 AImodel RAG 的 pipeline 不需要知道底层使用的是 OpenAI、Azure OpenAI、Ollama 还是 DeepSeek。Provider 的差异集中在 adapter 层处理。

#### 3.4.2 工厂与配置

工厂类建议：

```text
LLMFactory
EmbeddingFactory
RerankerFactory
VectorStoreFactory
SplitterFactory
EvaluatorFactory
```

工厂读取配置后返回对应实现。Factory 的注册表默认保持为空，通过
`register_builtin_providers()` 统一注入项目内置实现类；`create()` 和
`list_providers()` 内部必须自动确保内置实现已注册，业务代码不需要手动调用
注册步骤。

```yaml
llm:
  provider: deepseek
  model: deepseek-v4-flash

embedding:
  provider: dashscope
  model: text-embedding-v4

rerank:
  provider: cross_encoder
  fallback: rrf

transform:
  steps:
    - name: metadata_enrich
    - name: rewrite_chunk
      prompt_path: config/prompts/rewrite_chunk_prompt.yaml
    - name: semantic_merge
      prompt_path: config/prompts/semantic_merge_prompt.yaml
    - name: denoise
```

业务代码只调用：

```text
llm = LLMFactory.get_llm(settings)
embedding = EmbeddingFactory.get_embedding(settings)
reranker = RerankerFactory.get_reranker(settings)
```

Factory 注册表约束：

- **空注册表启动**：Factory 类变量不直接写死 provider -> implementation 映射。
- **显式内置注入**：每个 Factory 提供 `register_builtin_providers()`，集中注册 fake 和项目内置 provider。
- **自动 ensure**：`create()`、`list_providers()` 在读取注册表前自动调用 `register_builtin_providers()`。
- **可扩展注册**：仍保留 `register(provider, implementation_class)`，用于测试或后续扩展。
- **接口校验**：注册时必须校验实现类继承对应 Base 接口。
- **业务无感知**：Pipeline、Dashboard、MCP 等上层业务只传 settings/provider，不直接 import 具体实现类。

Transform 不创建 Factory，也不作为 Provider 选项。Transform 由 ingestion pipeline 根据
`settings.transform.steps` 按顺序串行执行，`src.libs.transform` 只保留
`BaseTransform` 抽象契约，具体实现放在 `src/ingestion/transform/`。

#### 3.4.3 Provider 选项

| 能力 | Provider |
| --- | --- |
| LLM | Azure OpenAI、OpenAI、Ollama、DeepSeek |
| Embedding | 百炼 `text-embedding-v4`；通过 OpenAI 兼容适配器调用，后续可扩展 OpenAI、Azure OpenAI Embedding、Ollama Embedding |
| VectorStore | pgvector；接口预留 Qdrant、Milvus、Chroma |
| Splitter | RecursiveCharacterTextSplitter |
| Reranker | Cross-Encoder、LLM Rerank、None |
| Evaluator | Ragas、自定义指标 |

#### 3.4.4 优雅降级策略

| 组件 | 不可用场景 | 降级方案 |
| --- | --- | --- |
| Query Rewrite LLM | LLM 超时、限流、配置缺失 | 使用原始 query 继续检索 |
| Reranker | Cross-Encoder/LLM Rerank 不可用 | 回退到 RRF 融合排序 |
| Dense Embedding | Embedding API 临时失败 | 返回可观测错误，不写入半成品向量；Query 阶段可仅用 Sparse Route 返回候选 |
| Sparse Route | BM25 索引缺失或构建中 | 仅使用 Dense Route，并在 Trace 中记录降级原因 |
| Dashboard | Streamlit 启动失败 | 不影响 MCP 和 AImodel 查询主链路 |

### 3.5 配置管理

版本化配置模板：`services/ai-service/rag/config/settings.example.yaml`

本地运行配置：`services/ai-service/rag/config/settings.yaml`

配置设计目标：

- **模板与运行配置分离**：仓库提交完整的 `settings.example.yaml`；开发者复制为本地 `settings.yaml` 后按环境修改，`settings.yaml` 必须被 Git 忽略。
- **统一入口**：所有可插拔组件都从本地 `settings.yaml` 读取配置，不在业务代码中写死 Provider、模型名和参数。
- **分层清晰**：按 `llm`、`embedding`、`splitter`、`vector_store`、`reranker`、`retrieval`、`ingestion`、`observability` 等模块组织。
- **环境变量隔离敏感信息**：API Key、数据库连接串等敏感信息只写环境变量名，不直接写入配置文件。
- **支持默认与降级**：每类组件都允许配置 fallback，便于组件不可用时回退到安全方案。
- **适合 Dashboard 展示**：Dashboard 可以直接读取配置，展示当前启用的 LLM、Embedding、Splitter、Reranker、VectorStore 和 Evaluator。

`settings.example.yaml` 示例：

```yaml
project:
  name: aimodel-rag
  default_collection: shopping_guides
  environment: local

database:
  provider: postgresql
  url_env: DATABASE_URL
  pool_size: 5
  echo_sql: false

llm:
  default: deepseek
  fallback: openai
  providers:
    deepseek:
      model: deepseek-v4-flash
      api_key_env: DASHSCOPE_API_KEY
      base_url_env: DASHSCOPE_BASE_URL
      timeout_seconds: 60
    openai:
      model: gpt-4.1-mini
      api_key_env: OPENAI_API_KEY
      timeout_seconds: 60
    ollama:
      model: qwen2.5:7b
      base_url: http://localhost:11434
      timeout_seconds: 120

vision_llm:
  default: qwen_vl_max
  enabled: true
  providers:
    qwen_vl_max:
      model: Qwen-VL-Max
      api_key_env: DASHSCOPE_API_KEY
      base_url_env: DASHSCOPE_BASE_URL
      timeout_seconds: 90
    openai:
      model: gpt-4o
      api_key_env: OPENAI_API_KEY
      timeout_seconds: 90

embedding:
  default: dashscope
  fallback: none
  batch_size: 64
  cache_enabled: true
  providers:
    dashscope:
      model: text-embedding-v4
      dimensions: 1536
      api_key_env: DASHSCOPE_API_KEY
      base_url_env: DASHSCOPE_BASE_URL
      timeout_seconds: 60

vector_store:
  provider: pgvector
  collection_table: rag_collections
  document_table: rag_documents
  chunk_table: rag_chunks
  distance: cosine
  embedding_dimensions: 1536

splitter:
  default: recursive_character
  providers:
    recursive_character:
      chunk_size: 900
      chunk_overlap: 150
      separators:
        - "\n## "
        - "\n### "
        - "\n\n"
        - "\n"
        - "。"
        - " "

transform:
  steps:
    - name: metadata_enrich
      enabled: true
    - name: rewrite_chunk
      enabled: true
      prompt_path: config/prompts/rewrite_chunk_prompt.yaml
    - name: semantic_merge
      enabled: true
      prompt_path: config/prompts/semantic_merge_prompt.yaml
    - name: denoise
      enabled: true
    - name: image_to_text
      enabled: true
      prompt_path: config/prompts/image_to_text_prompt.yaml

retrieval:
  query_rewrite_enabled: true
  dense_top_k: 30
  sparse_top_k: 30
  fusion_top_k: 12
  final_top_k: 5
  rrf_k: 60
  filters:
    include_deleted: false
    default_collection: shopping_guides

rerank:
  enabled: true
  default: llm
  fallback: rrf
  prompt_path: config/prompts/rerank_prompt.yaml
  top_k: 5
  providers:
    llm:
      llm_provider: deepseek
      timeout_seconds: 60
    cross_encoder:
      model: BAAI/bge-reranker-base
      device: cpu

ingestion:
  raw_data_dir: data/raw
  markdown_dir: data/markdown
  image_dir: data/images
  dedup:
    document_hash: sha256
    chunk_hash: sha256
  lifecycle:
    allow_delete: true
    soft_delete: true

storage:
  bm25_index_dir: data/db/bm25
  postgres_data_dir: data/db/postgres
  embedding_cache_dir: src/cache/embedding
  caption_cache_dir: src/cache/captions
  processing_cache_dir: src/cache/processing

observability:
  app_log_path: src/logs/app.log
  trace_jsonl_path: src/logs/traces.jsonl
  persist_to_postgresql: true
  json_formatter: true

dashboard:
  enabled: true
  port: 8501
  pages:
    - overview
    - ingestion_manage
    - data_browser
    - query_trace
    - ingestion_trace
    - evaluation

evaluation:
  golden_set_path: tests/fixtures/golden_set.json
  metrics:
    retrieval:
      hit_rate_at_k: true
      mrr: true
      ndcg: true
    generation:
      faithfulness: true
      answer_relevancy: true

mcp:
  enabled: true
  tools:
    - query_knowledge_hub
    - list_collections
    - get_document_summary
```

### 3.6 PostgreSQL 数据设计

PostgreSQL 是唯一持久化层，不使用 SQLite。

核心文档对象统一使用 Python 业务层生成的稳定字符串 ID。数据库不得为
`rag_collections`、`rag_documents` 或 `rag_chunks` 另行生成自增主键：

- `rag_collections.id` 直接保存 collection 的稳定字符串 ID。
- `rag_documents.id` 直接保存 `core.types.Document.id`。
- `rag_chunks.id` 直接保存 `core.types.Chunk.id`。
- `rag_documents.collection_id` 和 `rag_chunks.document_id` 使用 `TEXT`，
  与被引用对象的稳定字符串 ID 保持同一类型。

该约束确保 Loader、Ingestion、Storage、Retrieval 和 Trace 使用同一标识，
避免在 Repository 层维护数据库 ID 与 Python 领域 ID 的额外映射。

建议表：

| 表名 | 用途 |
| --- | --- |
| `rag_collections` | collection 元数据 |
| `rag_documents` | 文档元数据、SHA256、摄取状态 |
| `rag_chunks` | chunk 文本、metadata、embedding vector |
| `rag_bm25_terms` | BM25 词项统计 |
| `image_index` | 图片文件路径和来源索引 |
| `rag_query_traces` | Query Trace 索引 |
| `rag_ingestion_traces` | Ingestion Trace 索引、摄取历史和 skipped 结果摘要 |
| `rag_evaluation_runs` | 评估任务 |
| `rag_evaluation_results` | 评估结果 |

`rag_chunks.embedding` 使用 pgvector：

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_chunks (
    id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    source_ref JSONB,
    heading_path JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(1536),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_rag_chunks_offsets
        CHECK (start_offset >= 0 AND end_offset > start_offset)
);
```

`image_index` 用于保存原始图片文件索引，图片文件由 `ImageStorage` 落盘到 `data/images/{collection}/`：

```sql
CREATE TABLE image_index (
    image_id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    collection TEXT,
    doc_hash TEXT,
    page_num INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_collection ON image_index(collection);
CREATE INDEX idx_doc_hash ON image_index(doc_hash);
```

### 3.7 多模态图片处理设计

多模态处理选型为 **Image-to-Text 策略**。图片不单独建立 CLIP 多模态向量，而是先由 Vision LLM 转换为可检索的文本描述，再把描述注入 chunk 正文或 metadata 中。

#### 3.7.1 图片处理全流程

```text
文档
  -> Loader 提取图片并生成 image_id
  -> 在文档文本中写入图片占位符
  -> 输出 Document(id + text + metadata.images[])
  -> Splitter 保留图片引用标记到对应 chunk
  -> ImageCaptioner 判断 vision_llm 和 image_refs
  -> 满足条件时生成 caption 并写入 chunk metadata
  -> Storage 存储增强后的 chunk 和原始图片
```

各阶段输出：

| 阶段 | 输出 |
| --- | --- |
| Loader | `Document(id + text + metadata.images[])`，其中 `text` 包含图片占位符，`metadata.images[]` 保存图片基础信息 |
| Splitter | chunk 文本保留图片引用标记，chunk metadata 增加 `image_refs: List[image_id]` |
| ImageCaptioner | 当 `vision_llm.enabled=true` 且 chunk 存在 `image_refs` 时生成 caption，并写入 chunk metadata |
| Storage | 向量库存储增强后的 chunk，文件系统保存原始图片，PostgreSQL `image_index` 表保存图片索引信息 |

#### 3.7.2 Loader 技术要点

Loader 负责从 PDF、Markdown 或其他文档中抽取图片，并建立图片与文档文本之间的引用关系。

关键实现：

- **提取策略**：PDF 文本由 MarkItDown 转换，PDF 图片由 PyMuPDF 按页码和物理位置提取；Markdown 图片按本地图片语法解析。
- **图片 ID**：为每张图片生成稳定 `image_id`，建议基于 `source_doc + page + image_index + image_hash`。
- **引用标记**：在文档文本中写入图片占位符，例如 `[[image:image_xxx]]`，确保后续 splitter 能保留图片与上下文的关系。
- **原始图片存储**：原始图片保存到本地文件系统，数据库只保存索引和 metadata。

#### 3.7.3 Splitter 技术要点

Splitter 必须保留图片引用和文本上下文之间的关联，不能在切分时丢失图片占位符。

关键实现：

- **关联保持**：如果图片占位符位于某个标题或段落附近，应保留在对应 chunk 中。
- **chunk metadata 扩展**：每个 chunk 增加 `image_refs: List[image_id]`。
- **上下文保护**：当图片前后文本共同解释图片含义时，splitter 应尽量避免把图片占位符和说明文字切到不同 chunk。

#### 3.7.4 ImageCaptioner 技术要点

ImageCaptioner 是图片 caption 的业务编排层。它只在 `vision_llm.enabled=true` 且 chunk metadata 中存在 `image_refs` 时调用 Vision LLM；底层图片理解能力由 `ImageToTextTransform` 提供，ImageCaptioner 负责读取图片引用、调用图片描述能力、写入 chunk metadata，并处理 skipped、failed、low_quality 等状态。

Vision LLM 选型：

| 模型 | 提供商 | 特点 | 适用场景 | 推荐星级 |
| --- | --- | --- | --- | --- |
| GPT-4o | OpenAI | 图像理解能力强，文本描述稳定，适合复杂图文场景 | 高质量图片描述、复杂图表、商品图理解 | ★★★★★ |
| Qwen-VL-Max | 阿里云百炼 | 中文场景友好，适合中文文档和中文商品图片 | 中文图片说明、商品图、截图理解 | ★★★★☆ |
| Gemini Vision | Google | 多模态能力强，适合复杂视觉理解 | 复杂图表、跨语言图片理解 | ★★★★☆ |
| LLaVA / 本地 Vision 模型 | 本地模型 | 本地可控，成本低，但稳定性取决于部署质量 | 离线实验、低敏感度图片处理 | ★★★☆☆ |

Image-to-Text Prompt 设计：

```text
You are the image understanding component of a RAG document ingestion system.
Generate a retrieval-oriented description from the visible image content.
Requirements:
1. Describe visible objects, text, structures, processes, and data.
2. Emphasize facts useful for retrieval when the image contains products,
   specifications, procedures, or comparisons.
3. Write description and key_facts in Simplified Chinese, but preserve visible
   source text verbatim in extracted_text.
4. Do not invent brands, prices, models, or conclusions absent from the image.
5. Return low_quality with a reason when the image cannot be interpreted.
```

不同图片类型的理解策略：

| 图片类型 | 理解重点 | Prompt 引导方向 |
| --- | --- | --- |
| 商品图片 | 商品外观、材质、结构、使用场景、可见卖点 | 描述用户可能用于比较和购买决策的可见特征 |
| 参数截图 | 参数项、数值、单位、适用条件 | 提取关键参数，避免遗漏限制条件 |
| 流程图 | 节点、箭头、顺序、输入输出 | 按步骤描述流程，保留节点关系 |
| 表格图片 | 表头、行列关系、对比维度 | 转换为结构化文字摘要 |
| UI 截图 | 页面模块、按钮、状态、错误提示 | 描述界面功能和可见状态 |
| 装饰图或低信息图片 | 判断是否有检索价值 | 标记低价值或 low_quality，避免污染索引 |

#### 3.7.5 Storage 技术要点

Storage 负责同时保存增强后的 chunk 和原始图片索引。

关键实现：

- 增强后的 chunk 写入 PostgreSQL + pgvector，chunk metadata 包含 `image_refs`。
- 原始图片文件保存在本地文件系统。
- PostgreSQL 新增 `image_index` 表保存图片索引信息。
- 检索命中 chunk 后，如果 chunk metadata 中包含 `image_refs`，响应可以返回相关图片信息，供 Dashboard 或前端展示。

`image_index` 表建议字段：

| 字段 | 说明 |
| --- | --- |
| `image_id` | 图片稳定 ID |
| `file_path` | 原始图片在文件系统中的路径 |
| `source_doc` | 来源文档 |
| `page` | 所在页码 |
| `width` | 图片宽度 |
| `height` | 图片高度 |
| `mime_type` | 图片 MIME 类型 |
| `image_hash` | 图片内容哈希 |
| `quality_status` | 图片描述质量，例如 `ok`、`low_quality`、`skipped` |

注入格式：

```markdown
图片说明：这张图展示了无线耳机佩戴方式，重点体现耳塞大小、耳挂结构和运动场景稳定性。
```

#### 3.7.6 质量保障

- **描述质量检测**：如果生成描述过短、内容为空、Vision LLM 明确表示无法识别，图片应标记为 `low_quality`。
- **图片压缩**：大尺寸图片在调用 Vision LLM 前应提前压缩，降低请求成本和超时概率。
- **Vision LLM 降级**：如果 Vision LLM 不可用，图片保留占位符，但不生成描述、不参与检索，并在 chunk metadata 中标记 `image_caption_status=skipped`。
- **批量处理优化**：图片描述应支持批处理、并发限流和失败重试，避免大量图片摄取时阻塞整个 Ingestion Pipeline。

### 3.8 可观测性与可视化管理平台设计

为了避免 RAG 系统常见的“黑盒问题”，本项目需要覆盖 Ingestion 和 Query 两条链路的全流程监控，同时提供数据浏览、文档管理、组件概览和评估面板，让整个系统具备 **可量化、透明化、可回溯** 的工程能力。

#### 3.8.1 设计理念

- **双链路全覆盖追踪**：同时追踪数据摄取链路和查询链路，不只看最终结果，也记录中间阶段。
- **透明可回溯**：能够解释系统为什么召回这些文档、Dense/BM25 各自返回了什么、Rerank 之后结果如何变化。
- **低侵入性**：追踪逻辑与业务逻辑隔离，通过 `TraceContext` 注入 trace id、stage、provider、method、details，避免在业务流程中散落日志代码。
- **轻量化**：采用结构化 JSON Lines 日志和本地 Streamlit Dashboard，不依赖 LangSmith 等外部平台。
- **动态组件感知**：Dashboard 基于 trace 中的 `method`、`provider`、`details` 动态渲染组件状态和执行细节，切换 LLM、Embedding、Splitter、Reranker 后不需要修改 Dashboard 代码。

#### 3.8.2 Ingestion Trace 数据结构

Ingestion Trace 面向文档摄取链路，结构固定为 **基础信息、各阶段详情、汇总指标、评估指标**。

基础信息：

| 字段 | 记录内容 |
| --- | --- |
| `trace_id` | 单次摄取链路追踪 ID |
| `trace_type` | 固定为 `ingestion` |
| `started_at` | 摄取开始时间 |
| `collection` | 摄取目标知识集合 |
| `source_uri` | 原始文档路径或外部来源 |
| `source_hash` | 原始文档 SHA256 哈希纹 |

各阶段详情：

| 阶段 | 记录内容 |
| --- | --- |
| `dedup` | 原始文件 SHA256、`rag_documents` success 文档命中结果、是否跳过摄取、跳过原因、耗时、失败详情 |
| `load` | Loader 类型、原始文件类型、转换后的 `Document(id + text + metadata)` 摘要、图片提取数量、耗时、失败详情 |
| `split` | Splitter 类型、粗切分 chunk 数量、标题层级识别结果、平均 chunk 长度、耗时、失败详情 |
| `transform` | Transform 方法、LLM Provider、合并的 chunk 数量、去噪内容摘要、图片描述注入数量、上下文增强摘要、耗时、失败详情 |
| `embed` | Embedding Provider、`content_hash` 命中数量、新增 embedding 数量、Dense 编码批次数、Sparse/BM25 编码批次数、耗时、失败详情 |
| `upsert` | VectorStore Provider、写入 chunk 数量、更新 chunk 数量、跳过 chunk 数量、删除旧版本数量、耗时、失败详情 |

汇总指标：

| 字段 | 记录内容 |
| --- | --- |
| `total_duration_ms` | 从 load 到 upsert 的端到端耗时 |
| `document_status` | 摄取结果，例如 `success`、`skipped`、`failed` |
| `chunk_count` | 最终可检索 chunk 数量 |
| `embedded_count` | 实际执行 embedding 的 chunk 数量 |
| `skipped_count` | 因文档哈希或 chunk 内容哈希命中而跳过的数量 |
| `error` | 链路级错误信息；无错误时为空 |

评估指标：

| 字段 | 记录内容 |
| --- | --- |
| `chunk_quality_score` | chunk 语义完整性或人工/LLM 质量评分 |
| `noise_reduction_summary` | 去噪效果摘要，例如删除页眉页脚、重复目录、解析残留的数量 |
| `embedding_coverage` | 成功生成 Dense/BM25Indexer 的 chunk 占比 |
| `index_ready` | 文档是否达到可检索状态 |

#### 3.8.3 Query Trace 数据结构

Query Trace 面向查询链路，结构固定为 **基础信息、各阶段详情、汇总指标、评估指标**。

基础信息：

| 字段 | 记录内容 |
| --- | --- |
| `trace_id` | 单次查询链路追踪 ID |
| `trace_type` | 固定为 `query` |
| `started_at` | 查询开始时间 |
| `raw_query` | 用户原始询问 |
| `collection` | 查询目标知识集合 |
| `request_source` | 调用来源，例如 AImodel、MCP tool、Dashboard |

各阶段详情：

| 阶段 | 记录内容 |
| --- | --- |
| `query_processing` | 原始 query、改写 query（若有）、query normalize 方法、意图识别结果、耗时 |
| `dense` | Query Embedding 模型、向量库 Provider、Top-k 语义候选、候选分数、候选数量、耗时 |
| `sparse` | BM25 方法、倒排索引命中词、Top-k 关键词候选、候选分数、候选数量、耗时 |
| `fusion` | RRF 融合方法、Dense/BM25 候选来源、融合后排名、重复候选合并结果、耗时 |
| `filter` | 过滤参数、过滤前候选数量、过滤后候选数量、被过滤原因、耗时 |
| `rerank` | Reranker Provider、过滤后 rerank 前排名、rerank 后排名、fallback 原因（若有）、耗时 |

汇总指标：

| 字段 | 记录内容 |
| --- | --- |
| `total_duration_ms` | 从 query_processing 到 response 的端到端耗时 |
| `top_k_results` | 最终返回给 Agent 的 Top-k 结果摘要 |
| `candidate_count_by_stage` | dense、sparse、fusion、filter、rerank 各阶段候选数量 |
| `fallback_used` | 是否触发降级，例如 rerank fallback 到 RRF |
| `error` | 链路级错误信息；无错误时为空 |

评估指标：

| 字段 | 记录内容 |
| --- | --- |
| `query_document_relevance` | 召回文档与 query 的相关性分数 |
| `citation_hit_rate` | 最终引用是否来自实际召回文档 |
| `rerank_delta` | Rerank 前后排名变化摘要 |
| `empty_result` | 是否为空结果 |

#### 3.8.4 Trace 结构化日志与追踪机制

Trace 结构化日志基于 **Python logging + JSONFormatter** 实现。日志以 JSON Lines 形式追加到本地日志文件，方便 Dashboard 按行读取、过滤和聚合。

本地 Dashboard 基于 **Streamlit** 读取日志文件，并提供交互式可视化能力。Dashboard 不直接侵入 pipeline 执行逻辑，只消费 Trace 日志和 PostgreSQL 中的索引数据。

追踪机制实现：

| 时机 | 设计 |
| --- | --- |
| 请求开始 | 在 pipeline 入口创建 `TraceContext` 实例，生成唯一 `trace_id`，并写入请求基础信息，例如 `trace_type`、`started_at`、`collection`、`raw_query` 或 `source_uri` |
| 阶段记录 | 每个阶段执行结束后调用 `trace_context.record_stage()`，记录阶段名、耗时、输入摘要、输出摘要、候选数量、错误信息、Provider 和 method |
| 请求结束 | pipeline 结束时调用 `trace_context.flush()`，将基础信息、阶段详情、汇总指标和评估指标序列化为 JSON，并追加写入日志文件 |

Trace 事件示例：

```json
{"trace_id":"query_xxx","stage":"dense","method":"pgvector_search","provider":"pgvector","duration_ms":42,"candidate_count":30,"status":"success","details":{"top_k":30}}
```

#### 3.8.5 Dashboard 功能设计

Dashboard 使用 Streamlit 实现，面向开发者、面试官和项目演示场景。页面设计以“看配置、管文档、查数据、追链路、跑评估”为核心。

页面 1：**系统总览**

| 模块 | 功能 |
| --- | --- |
| 组件配置 | 读取 `settings.yaml`，展示当前可插拔组件，包括 LLM、Embedding、Splitter、Reranker、VectorStore、Evaluator |
| 数据资产统计 | 展示各 collection 的文档数量、chunk 数量、Dense 向量状态、Sparse 索引状态 |
| 系统健康指标 | 展示最近一次 Ingestion 和 Query 的耗时、状态、错误摘要和最近 Trace 时间 |

页面 2：**评估面板**

| 模块 | 功能 |
| --- | --- |
| 评估运行 | 选择评估后端，例如 Ragas 或自定义指标，点击运行评估任务 |
| 指标展示 | 展示 `hit_rate`、`MRR`、`query_document_relevance`、`citation_hit_rate` 等指标 |
| 历史趋势 | 对比不同策略版本下的指标变化，确保每次策略调整都有量化分数支撑 |

页面 3：**Query Trace**

| 模块 | 功能 |
| --- | --- |
| 历史列表 | 展示历史 query trace，支持按 collection、状态、耗时、是否 fallback 过滤 |
| 单次查询详情 | 展示 query 原文、改写 query、各阶段耗时瀑布图、Dense/BM25 召回对比、RRF 融合结果、Rerank 前后对比和最终 Top-k 结果 |

页面 4：**Ingestion 管理**

| 模块 | 功能 |
| --- | --- |
| 文档选择与摄取 | 通过页面选择文件并触发摄取流程，展示 load、split、transform、embed、upsert 的实时阶段进度 |
| 文档删除 | 支持删除已摄入文档，并同步处理文档状态、chunk、向量、Sparse 索引和检索可见性 |

页面 5：**数据浏览器**

| 模块 | 功能 |
| --- | --- |
| 文档列表视图 | 展示 collection 下的文档列表、摄取状态、来源路径、更新时间、chunk 数量 |
| Chunk 详情视图 | 查看 chunk 文本、metadata、标题路径、Dense 向量状态、Sparse 索引状态和引用来源 |
| 数据来源 | 展示文档原始来源、source hash、摄取批次和关联 trace id |

页面 6：**Ingestion Trace**

| 模块 | 功能 |
| --- | --- |
| 历史列表 | 展示历史 ingestion trace，支持按文档、collection、状态、耗时过滤 |
| 单次摄取详情 | 展示阶段耗时瀑布图、处理统计、跳过原因、失败详情和各阶段详情展开 |

### 3.9 AImodel 集成边界

AImodel 侧新增 RAG 工具时，应保持工具边界清晰：

- 商品推荐仍调用商品搜索工具。
- 商品链接详情仍调用商品详情工具。
- RAG 工具只返回知识片段、引用和 trace id。
- SSE 正文不能暴露 RAG 原始工具 JSON。
- Assistant 最终回答可以展示引用标题，但不展示内部 chunk id。

建议工具名：

```text
search_shopping_guides
```

## 4. 测试方案

### 4.1 TDD 原则

RAG 项目涉及文档解析、分块、检索、重排、评估和可视化，多数问题如果等到端到端阶段才发现，定位成本会非常高。因此本项目采用 TDD 作为核心开发方法。

核心原则：

- **早测试，常测试**：任何功能模块在实现的同时就应当生成单元测试，避免业务逻辑先堆起来再补测试。
- **测试即文档**：测试用例本身就是最标准的行为规范。一个组件应该如何处理正常输入、异常输入、边界条件，都应当能从测试中看出来。
- **快速反馈循环**：单元测试应当在秒级内执行完毕，支持开发者高频执行，快速发现问题。
- **分层测试金字塔**：大量单元测试作为基座，少量关键路径集成测试作为保障，极少数端到端测试验证完整流程。

执行要求：

- 单元测试不依赖真实 OpenAI、DeepSeek、PostgreSQL 网络环境。
- Provider 通过 fake client 或 monkeypatch 测试。
- 集成测试可以依赖本地 PostgreSQL/pgvector。
- LLM 和 Vision LLM 实测测试必须加 marker，默认 CI 不跑。
- 每个阶段的实现至少包含一个正向用例、一个边界用例和一个失败用例。

pytest marker：

```ini
markers =
    unit: fast unit tests
    integration: local PostgreSQL or service integration tests
    e2e: end-to-end tests
    llm: tests requiring real model API
    slow: slow tests
```

### 4.2 分层测试

#### 4.2.1 测试分层策略

测试分层采用 **测试金字塔** 结构：底层用大量快速单元测试保证组件正确性，中层用集成和契约测试保证模块协作，顶层用少量端到端测试验证完整用户路径。

```text
             E2E / 质量评估
          少量，验证完整主流程

       集成测试 / 契约测试 / Dashboard 测试
       中等数量，验证模块协作和外部接口

  单元测试 / Repository 测试 / 检索算法测试
  大量，秒级反馈，覆盖独立组件和核心算法
```

| 层级 | 测试类型 | 数量占比 | 运行频率 | 主要目标 | 代表场景 |
| --- | --- | --- | --- | --- | --- |
| 底层 | 单元测试 | 最高 | 每次开发高频运行 | 验证独立组件内部逻辑 | Loader、Splitter、Transformer、Embedding、BM25、Retrieval、Reranker、TraceContext、Factory |
| 底层 | Repository 测试 | 较高 | 本地数据库可用时运行 | 验证 PostgreSQL 持久化边界 | schema 初始化、文档去重、chunk upsert、`image_index` 写入、ImageStorage 落盘、trace 写入、评估记录写入 |
| 底层 | 检索算法测试 | 较高 | 每次修改检索策略时运行 | 验证排序、融合和 fallback 行为 | Dense Route、Sparse Route、RRF、过滤、Rerank、Rerank fallback |
| 中层 | Ingestion 集成测试 | 中等 | 修改摄取链路时运行 | 验证摄取链路模块协作 | PDF/Markdown -> Document -> Chunk -> Transform -> Embedding -> pgvector upsert |
| 中层 | Indexing 集成测试 | 中等 | 修改索引链路时运行 | 验证索引 MVP 编排 | content_hash 差量判断 -> Dense 编码 -> BM25Indexer -> pgvector/BM25 upsert |
| 中层 | Query 集成测试 | 中等 | 修改检索链路时运行 | 验证查询链路模块协作 | Query Processing -> Dense/BM25 -> RRF -> Rerank -> Citation |
| 中层 | MCP 契约测试 | 中等 | 修改 MCP tool 时运行 | 验证外部工具接口稳定 | tools schema、正常查询、空 collection、异常返回 |
| 中层 | Dashboard 测试 | 较少 | 修改可视化页面时运行 | 验证本地 Dashboard 六大页面可读取数据并渲染 | 系统总览、Ingestion 管理、数据浏览器、Query Trace、Ingestion Trace、评估面板 |
| 顶层 | E2E 测试 | 最少 | AImodel 集成前、发布前或关键改动后运行 | 验证完整用户路径 | 摄取 shopping guide -> 索引 -> 查询 -> Trace -> Dashboard 展示 -> AImodel 工具返回引用 |
| 顶层 | RAG 质量评估 | 最少但持续保留 | 策略调整后运行 | 验证检索和回答质量是否提升 | hit_rate、MRR、query_document_relevance、citation_hit_rate、历史趋势对比 |

#### 4.2.2 单元测试重点

| 模块 | 测试重点 | 典型测试用例 |
| --- | --- | --- |
| Loader | 格式解析、元数据提取、图片引用收集、文档哈希去重 | 测试解析 PDF/Markdown；检查图片占位符 `[[image:image_id]]`；验证 Markdown 标题层级提取；验证相同 SHA256 且历史状态为 `success` 时跳过 |
| Splitter | 语义边界切分、标题层级保留、图片引用保持 | 验证不会简单按固定长度硬切；检查 chunk metadata 中的 `image_refs`；验证图片占位符不会和相关说明文字分离 |
| Transformer | LLM 语义二次加工、chunk 合并、去噪、幂等性 | 验证逻辑相关 chunk 可合并；验证页眉页脚和重复目录被去除；验证同一输入重复 transform 不会重复合并 chunk |
| ImageCaptioner | Vision LLM 图片 caption、条件触发、降级和幂等性 | 验证 `vision_llm.enabled=true` 且存在 `image_refs` 时生成 caption；验证未启用 Vision LLM 或无 `image_refs` 时跳过；验证低质量图片标记为 `low_quality`；验证重复执行不会重复注入 caption |
| Embedding | 差量计算、批处理、Dense/BM25Indexer 双路索引、幂等性 | 验证已有 `content_hash` 不重复 embedding；验证批处理被调用；验证 Dense 向量和 BM25Indexer 索引数据都被生成；验证同一批 chunk 重复执行 embedding 时不会重复调用模型或生成重复向量记录 |
| BM25 | 分词、倒排索引、关键词候选召回 | 验证关键词命中文档；验证品牌、型号、政策词能精确召回；验证空 query 或停用词 query 的处理 |
| Retrieval | Query 预处理、Dense Route、Sparse Route、RRF 融合、过滤 | 验证 Query Embedding 被调用；验证 BM25 和 pgvector 双路候选合并；验证 RRF 基于排名倒数融合；验证 deleted/failed 文档不会进入结果 |
| Reranker | Cross-Encoder/LLM Rerank、排序变化、fallback | 验证 rerank 前后排名变化被记录；验证 reranker 超时或异常时 fallback 到 RRF 排序 |
| TraceContext | 阶段记录、耗时统计、JSON Lines 输出 | 验证 `record_stage()` 记录阶段详情；验证 `flush()` 写出结构化 JSON；验证 error 和 fallback 信息进入汇总指标 |
| Factory | 配置驱动、接口隔离、优雅降级 | 验证根据 `settings.yaml` 创建指定 Provider；验证未知 Provider 抛出可读错误；验证默认 fallback 策略生效 |

#### 4.2.3 集成与端到端测试重点

| 测试类型 | 测试重点 | 典型测试用例 |
| --- | --- | --- |
| Ingestion 集成测试 | 验证摄取链路可完整写入 PostgreSQL/pgvector | 使用一份小型 Markdown 指南，执行 load -> split -> transform -> image_caption -> batch -> upsert，验证文档、chunk、caption metadata、Dense 向量、BM25 索引、`image_index` 和 ingestion trace 都存在 |
| Indexing 集成测试 | 验证索引 MVP 编排 | 准备测试文档或增强后 chunk fixture，执行 `IngestionPipeline.run()` 或 `IngestionPipeline.run_indexing()`，检查 content_hash 跳过、Dense/BM25Indexer 和 upsert 结果 |
| Query 集成测试 | 验证查询链路可返回带引用的 Top-k 结果 | 摄取测试文档后查询“如何挑选高性价比无线耳机”，验证 Dense/BM25 候选、RRF 结果、最终引用来源 |
| MCP 集成测试 | 验证 MCP tool 契约稳定 | 调用 `query_knowledge_hub`，验证返回 `content`、`citations`、`trace_id`，并且空 collection 返回可读错误 |
| Dashboard 集成测试 | 验证 Dashboard 六大页面能读取真实数据 | 准备 trace JSON Lines 和测试数据库记录，验证系统总览、Ingestion 管理、数据浏览器、Query Trace、Ingestion Trace、评估面板都能读取并渲染数据 |

端到端测试应覆盖两个核心场景：

| 场景 | 流程 | 验证重点 |
| --- | --- | --- |
| 数据准备（离线摄取） | 准备测试文档 -> 离线摄取 -> 检查 chunk 结果 -> 检查 storage -> 验证幂等性 | chunk 质量是否达标；图片是否被正确提取、描述和关联；metadata 是否完整；Dense/BM25 索引是否正确；重复执行摄取不会重复生成 chunk、embedding 或图片记录 |
| 召回测试 | 基于已摄取知识库准备不同难度、不同类型 query -> 执行混合检索 -> 验证召回结果 -> 对比不同策略效果 | Top-k 结果命中率是否达标；包含图片的 chunk 是否能被命中；空查询、超长查询、无结果查询等边界处理是否正确；排序质量是否稳定；对比 Hybrid、Dense-only、Sparse-only 策略；验证 rerank 对最终排序的影响 |

### 4.3 RAG 质量评估

RAG 质量评估需要准备 **黄金测试集**，每条样本包含问题、标准答案和来源文档，用于同时评估检索质量和生成质量。

黄金测试集使用 JSON 格式：

```json
[
  {
    "id": "guide_wireless_earbuds_001",
    "question": "如何挑选高性价比无线耳机？",
    "golden_answer": "高性价比无线耳机应重点关注连接稳定性、续航、佩戴舒适度、通话质量和售后保障。如果用于通勤，还应关注主动降噪和抗风噪表现。",
    "expected_sources": [
      "shopping_guides/wireless-earbuds.md"
    ],
    "expected_keywords": [
      "连接稳定性",
      "续航",
      "主动降噪",
      "售后保障"
    ]
  }
]
```

检索指标：

| 指标 | 目标 |
| --- | --- |
| `Hit Rate@K` | >= 90% |
| `MRR` | >= 0.8 |
| `NDCG@K` | >= 0.85 |

生成指标：

| 指标 | 目标 |
| --- | --- |
| `Faithfulness` | >= 0.9 |
| `Answer Relevancy` | >= 0.85 |

评估运行要求：

- 定期运行评估任务，监控指标是否回归。
- 每次调整 splitter、transform、embedding、BM25、RRF、rerank 或 prompt 后，都应运行同一批黄金测试集进行对比。
- 评估结果写入 PostgreSQL，并在 Dashboard 评估面板展示历史趋势。
- 如果检索指标下降，需要优先检查 chunk 质量、metadata、Dense/BM25 召回和 RRF 排序。
- 如果生成指标下降，需要优先检查引用来源、上下文注入和回答 prompt。

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
│       ├── rewrite_chunk_prompt.yaml              # chunk 语义改写与增强提示词模板
│       ├── semantic_merge_prompt.yaml              # 相邻 chunk 语义合并判断提示词模板
│       └── image_to_text_prompt.yaml              # 图片转文字描述提示词模板
├── data/
│   ├── raw/                                       # 按 collection 分类存放原始测试文档和本地摄取文件
│   │   └── shopping_guides/                       # shopping_guides collection 的原始文档
│   ├── markdown/                                  # PDF 转换后的 Markdown 中间文件
│   ├── db/                                        # 本地开发数据库数据和索引文件
│   │   ├── postgres/                              # PostgreSQL 本地数据、dump 或初始化辅助文件
│   │   └── bm25/                                  # BM25 倒排索引和词项统计缓存
│   └── eval/                                      # 黄金测试集和评估数据
├── src/
│   ├── core/
│   │   ├── config.py                              # 读取 settings.yaml 和 prompt 配置
│   │   ├── types.py                               # Document、Chunk、RetrievalResult 等核心类型
│   │   ├── errors.py                              # RAG 子系统统一异常定义
│   │   ├── query_engine/
│   │   │   ├── query_processor.py                 # 查询预处理、query normalize 和可选 rewrite
│   │   │   ├── hybrid_engine.py                   # 编排 Dense Route、Sparse Route 和融合流程
│   │   │   ├── dense_route.py                     # Query Embedding 和 pgvector 语义召回
│   │   │   ├── sparse_route.py                    # BM25 和倒排索引关键词召回
│   │   │   ├── fusion.py                          # RRF 排名倒数融合
│   │   │   └── reranker.py                        # 调用 reranker 并处理 fallback
│   │   ├── response/
│   │   │   ├── response_builder.py                # 构建最终返回给 Agent 的上下文响应
│   │   │   ├── citation_builder.py                # 组装引用来源和文档出处
│   │   │   └── multimodal_assembler.py            # 组装命中 chunk 关联的图片等多模态内容
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
│   │   │   ├── llm_factory.py                     # 根据配置创建 LLMClient
│   │   │   ├── openai_client.py                   # OpenAI Chat 实现
│   │   │   ├── azure_openai_client.py             # Azure OpenAI Chat 实现
│   │   │   ├── ollama_client.py                   # Ollama 本地 LLM 实现
│   │   │   └── deepseek_client.py                 # DeepSeek 兼容接口实现
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
│   │   │   ├── image_to_text_transform.py         # 调用 Vision LLM 生成结构化图片 caption
│   │   │   └── image_captioner.py                 # 根据 image_refs 生成 caption 并写入 metadata
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
| `config/prompts/rewrite_chunk_prompt.yaml` | 保存 chunk 语义改写提示词 | 支持 Transform 阶段做 chunk rewrite 并保持事实和图片引用 |
| `config/prompts/semantic_merge_prompt.yaml` | 保存相邻 chunk 合并判断提示词 | 仅合并逻辑连续内容，要求结构化 merge 决策和合并文本 |
| `config/prompts/image_to_text_prompt.yaml` | 保存图片转文字提示词 | 使用英文 Prompt 指令，按图片类型生成可检索的简体中文描述，并原样保留图片中的文字 |
| `data/raw/shopping_guides/` | 存放 shopping_guides collection 原始文档 | 按 collection 分类，便于离线摄取和回归测试 |
| `data/db/postgres/` | 存放 PostgreSQL 本地开发辅助数据 | 保存初始化辅助文件、dump 或本地持久化数据 |
| `data/db/bm25/` | 存放 BM25 本地索引辅助数据 | 保存倒排索引和词项统计缓存 |
| `data/eval/golden_set.json` | 存放黄金测试集 | JSON 格式，包含问题、标准答案、来源文档和关键词 |

#### 5.3.2 Core 层

| 文件 | 具体职责 | 关键技术点 |
| --- | --- | --- |
| `src/core/config.py` | 加载 settings 和 prompt 配置 | Pydantic/YAML 校验、环境变量覆盖、默认值处理 |
| `src/core/types.py` | 定义核心数据结构 | `Document(id,text,metadata)`、`Chunk(id,text,chunk_index,start_offset,end_offset,source_ref)`、`RetrievalResult(chunk_id,text,score,metadata)`、`metadata.images[]`、`Citation`、`TraceRecord` |
| `src/core/errors.py` | 定义统一异常类型 | 配置错误、Provider 错误、检索错误、摄取错误、MCP 错误 |
| `src/core/query_engine/query_processor.py` | 处理用户 query | normalize、可选 rewrite、collection/top_k 解析、意图识别 |
| `src/core/query_engine/hybrid_engine.py` | 编排混合检索主流程 | `HybridSearch`、Dense/BM25 双路召回、RRF Fusion、候选去重、rerank 前 metadata 过滤、单路失败降级 |
| `src/core/query_engine/dense_route.py` | 执行语义向量召回 | Query Embedding、pgvector search、返回 `RetrievalResult(chunk_id,text,score,metadata)` |
| `src/core/query_engine/sparse_route.py` | 执行关键词召回 | `ProcessedQuery.keywords`、`bm25_indexer.query()`、`vector_store.get_by_ids()` 回表、返回 `RetrievalResult` |
| `src/core/query_engine/fusion.py` | 融合 Dense/BM25 结果 | RRF 基于排名倒数加权，不直接比较不同分数 |
| `src/core/query_engine/reranker.py` | 对过滤后的融合结果做精排 | Cross-Encoder/LLM Rerank、超时异常 fallback 到过滤后的 RRF 结果 |
| `src/core/response/response_builder.py` | 构建 RAG 工具响应 | 输出 answer_context、citations、metadata、trace_id |
| `src/core/response/citation_builder.py` | 构建引用来源 | 文档标题、source_uri、section_path、chunk_id、score |
| `src/core/response/multimodal_assembler.py` | 组装多模态命中内容 | 根据 `image_refs` 返回相关图片 metadata 和 file_path |
| `src/core/trace/trace_context.py` | 管理单次 trace 上下文 | `trace_id`、基础信息、阶段列表、汇总指标、评估指标 |
| `src/core/trace/trace_controller.py` | 编排 trace 写入 | `record_stage()`、`flush()`、错误和 fallback 记录 |

#### 5.3.3 Libs 可插拔抽象层

| 文件 | 具体职责 | 关键技术点 |
| --- | --- | --- |
| `src/libs/loader/base_loader.py` | 定义 Loader 抽象接口 | `load(source) -> Document(id + text + metadata)` |
| `src/libs/loader/loader_factory.py` | 创建 Loader 实现 | 根据文件类型和配置选择 Markdown/PDF Loader |
| `src/libs/loader/markdown_loader.py` | 加载 Markdown 文档 | 提取标题层级、metadata、图片引用 |
| `src/libs/loader/pdf_loader.py` | 加载 PDF 文档 | PDF -> Markdown、图片提取、图片占位符写入 |
| `src/libs/llm/base_llm.py` | 定义 LLMClient 抽象接口 | `chat(messages) -> response` |
| `src/libs/llm/llm_factory.py` | 创建 LLMClient | 根据 settings 选择 OpenAI/Azure/Ollama/DeepSeek |
| `src/libs/llm/openai_client.py` | OpenAI Chat 实现 | OpenAI SDK、统一 messages 输入输出 |
| `src/libs/llm/azure_openai_client.py` | Azure OpenAI Chat 实现 | Azure endpoint、deployment、api-version |
| `src/libs/llm/ollama_client.py` | Ollama 本地 LLM 实现 | 本地模型调用、离线降级 |
| `src/libs/llm/deepseek_client.py` | DeepSeek 兼容接口实现 | OpenAI-compatible chat API |
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
| `src/libs/vector_store/pgvector_store.py` | pgvector 实现 | PostgreSQL vector(1536)、cosine search、metadata filter |
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
| `src/ingestion/pipeline.py` | 编排离线摄取与索引主流程 | C10 已实现 dedup -> load -> split -> transform/image_caption -> existing content_hash vector lookup -> Dense/BM25 batch -> transactional upsert -> lifecycle success；保留 Loader-only 兼容模式并拒绝部分依赖和空 chunk 快照 |
| `src/ingestion/loader.py` | 调用 Loader 并输出 Document | 去重通过后的 Loader 调用和 Document 标准化 |
| `src/ingestion/pdf_to_markdown.py` | PDF 转 Markdown 辅助逻辑 | MarkItDown、页码、图片抽取 |
| `src/ingestion/chunk/splitter_step.py` | 执行 chunk 初始切分 | 调用 `DocumentChunker`，完成 `Document -> List[Chunk]` 业务适配 |
| `src/ingestion/chunk/document_chunker.py` | 业务 chunk 适配器 | 调用 `libs.splitter` 的 `str -> List[str]` 能力，生成 `chunk_id`、继承 metadata、添加 `chunk_index`、建立 `source_ref`、按需分发 `image_refs` |
| `src/ingestion/chunk/chunk_id.py` | 生成稳定 chunk_id | `hash(source_path + section_path + content_hash)` |
| `src/ingestion/transform/transformer.py` | 编排 Transform 阶段 | 从 `settings.transform.steps` 读取顺序，串行执行 metadata_enrich、rewrite_chunk、semantic_merge、denoise、image_to_text |
| `src/ingestion/transform/metadata_enricher.py` | metadata 注入实现 | 标题路径、来源、文档主题、业务 metadata 注入 |
| `src/ingestion/transform/chunk_rewriter.py` | LLM 改写 chunk | 提升语义完整性和检索可读性，Prompt 从配置读取 |
| `src/ingestion/transform/semantic_merge_transform.py` | 智能合并 chunk | 合并逻辑相关但被物理切割的 chunk，保留 source_ref 和 image_refs |
| `src/ingestion/transform/denoise_transform.py` | 去噪处理 | 删除页眉页脚、重复目录、解析残留，保留图片占位符 |
| `src/ingestion/transform/image_to_text_transform.py` | 图片理解适配 | 调用注入的 Vision LLM，解析 status、description、extracted_text、key_facts 和 reason |
| `src/ingestion/transform/image_captioner.py` | 图片 caption 编排 | `vision_llm.enabled` 判断、`image_refs` 条件触发、caption 写入 chunk metadata |
| `src/ingestion/embedding/embedding_step.py` | 编排 Embedding 阶段 | `run_dense()` 提供窄粒度差量编码；`run_batch()` 复用数据库已有 content_hash 向量、对当前批次重复内容只调用一次模型，并为每个有序 chunk 生成完整 Dense 结果，同时编排 BM25Indexer |
| `src/ingestion/embedding/dense_encoder.py` | DenseEncoder | content_hash 计算、差量判断、单 chunk `embed()` 编码和 C8 批量 `embed_batch()` 编码；不承担 retry、upsert 或 BM25 职责 |
| `src/ingestion/embedding/bm25_indexer.py` | BM25Indexer | C7 已实现 in-memory BM25 分词、词频、倒排索引构建和关键词候选查询，为后续 Sparse Route 和 BM25 持久化提供可复用统计结果 |
| `src/ingestion/embedding/batch_processor.py` | 批处理优化 | C8 已实现按 batch_size 拆分、可配置 throttle_seconds 节流、失败批次按 item 隔离、有限 retry、失败记录和有序成功结果返回 |
| `src/ingestion/storage/upsert_step.py` | 写入摄取结果 | C9 已实现完整文档快照校验、受管图片复制、document/chunk/vector/BM25/image_index 单事务写入、失败回滚和输入顺序保持 |

#### 5.3.5 Storage 与本地运行层

| 文件 | 具体职责 | 关键技术点 |
| --- | --- | --- |
| `src/storage/postgres.py` | 管理 PostgreSQL 连接 | 连接池、事务、超时、健康检查 |
| `src/storage/schema.sql` | 定义数据库 schema | pgvector extension、documents、chunks、`rag_bm25_terms`、`image_index`、traces、evaluation |
| `src/storage/vector_storage.py` | 管理向量存储 | pgvector upsert/search、metadata filter |
| `src/storage/bm25_storage.py` | 管理 BM25 索引数据 | C9 已实现 document 级完整 posting 快照替换，持久化 term_frequency、document_frequency、document_length 和 average_document_length |
| `src/storage/image_storage.py` | 管理图片文件和索引 | 原始图片保存到 `data/images/{collection}/`；支持安全路径解析、原子文件替换和调用方事务内 image_index upsert |
| `src/storage/trace_log_storage.py` | 管理 trace 日志读写 | `traces.jsonl` 追加写入和 Dashboard 读取 |
| `src/storage/repositories.py` | 管理通用 repository | documents、chunks、source_hash 去重查询、成功文档 content_hash 向量复用查询、traces、evaluation_runs |
| `src/logs/app.log` | 保存应用运行日志 | 普通运行日志和错误排查 |
| `src/logs/traces.jsonl` | 保存结构化 trace 日志 | ingestion/query trace JSON Lines |
| `src/cache/embedding/` | 缓存 embedding 结果 | content_hash 差量计算和重复请求复用 |
| `src/cache/captions/` | 缓存图片描述 | image_hash 命中后跳过 Vision LLM |
| `src/cache/processing/` | 缓存摄取中间状态 | PDF 转换、临时图片、失败恢复 |
| `src/scripts/run_dashboard.py` | 启动 Dashboard | 本地 Streamlit 启动脚本 |
| `src/scripts/run_evaluation.py` | 运行评估任务 | 读取 golden_set.json，输出指标并写库 |
| `src/scripts/query.py` | 本地查询调试 | 调用完整 `hybridsearch + rerank`，支持 `--query`、`--top-k`、`--collection`、`--verbose`、`--no-rerank` |
| `src/scripts/ingest.py` | 本地离线摄取 CLI | 自动发现父目录 `.env` 且不覆盖系统注入变量；递归发现 Markdown/PDF；将运行时相对路径固定解析到 RAG 根目录；读取默认 collection；配置驱动组装完整 Pipeline；转发 force；输出 JSON 结果并管理 PostgreSQL pool 生命周期 |

#### 5.3.6 Observability 层

| 文件 | 具体职责 | 关键技术点 |
| --- | --- | --- |
| `src/observability/structured_log.py` | 配置结构化日志 | Python logging + JSONFormatter |
| `src/observability/services/config_reader.py` | Dashboard 读取配置 | 展示当前启用组件和 provider |
| `src/observability/services/data_browser_service.py` | Dashboard 查询数据资产 | 文档、chunk、图片、metadata、索引状态 |
| `src/observability/services/trace_reader_service.py` | Dashboard 读取 trace | query/ingestion 历史、瀑布图数据、fallback 原因 |
| `src/observability/services/evaluation_service.py` | Dashboard 运行评估 | 触发评估、读取历史趋势 |
| `src/observability/pages/overview.py` | 系统总览页面 | 组件配置、collection 统计、健康指标 |
| `src/observability/pages/query_trace.py` | Query Trace 页面 | Dense/BM25 对比、RRF、rerank 前后对比 |
| `src/observability/pages/ingestion_trace.py` | Ingestion Trace 页面 | 阶段耗时瀑布图、跳过原因、失败详情 |
| `src/observability/pages/ingestion_manage.py` | Ingestion 管理页面 | 文件选择、摄取进度、文档删除 |
| `src/observability/pages/data_browser.py` | 数据浏览器页面 | 文档列表、chunk 详情、图片引用 |
| `src/observability/pages/evaluation.py` | 评估面板页面 | 指标展示、历史趋势、策略对比 |
| `src/observability/dashboard/app.py` | Streamlit 入口 | 页面路由和启动入口 |
| `src/observability/dashboard/layout.py` | Dashboard 公共布局 | 导航、筛选器、通用图表容器 |
| `src/observability/evaluation/runner.py` | 评估任务运行器 | 读取黄金测试集、执行检索和生成评估 |
| `src/observability/evaluation/metrics.py` | 自定义指标 | Hit Rate、MRR、NDCG、citation_hit_rate |
| `src/observability/evaluation/ragas_adapter.py` | Ragas 适配 | Faithfulness、Answer Relevancy |

#### 5.3.7 外部接口层

| 文件 | 具体职责 | 关键技术点 |
| --- | --- | --- |
| `src/mcp_server/server.py` | 启动 MCP Server | Python 官方 MCP SDK、stdio/http 生命周期 |
| `src/mcp_server/tools.py` | 暴露 MCP tools | `query_knowledge_hub`、`list_collections`、`get_document_summary` |
| `src/adapter/aimodel_tool.py` | AImodel 工具适配 | 封装 `search_shopping_guides`，隐藏内部工具 JSON |

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
- chunk 级差量依赖 `content_hash`，只对新增或变更 chunk 执行 embedding。
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

## 6. 项目排期

### 6.1 阶段预览表

状态标记说明：`[ ]` 表示未开始，`[~]` 表示进行中，`[✔]` 表示已完成。

| 阶段 | 阶段标题 | 目标 | 状态 |
| --- | --- | --- | --- |
| Phase A | 配置与项目骨架 | 独立模块基础文件、uv 依赖锁定、Docker 部署骨架、pytest 冒烟测试、配置模板、prompt 配置、核心类型和配置加载 | [✔] |
| Phase B | 数据持久化与可插拔组件 | PostgreSQL/pgvector schema、repository、文档生命周期管理和 libs 可插拔实现 | [✔] |
| Phase C | Ingestion & Indexing Pipeline | 先去重的数据摄取、Loader、PDF -> Markdown、Splitter、Transform、ImageCaptioner、content_hash 差量、Dense/BM25Indexer 双路索引、pgvector upsert、统一 Pipeline MVP 和 `ingest.py` 脚本入口 | [✔] |
| Phase D | Retrieval | Query Processor、Dense Route、Sparse Route、RRF Fusion、HybridSearch、Rerank 前候选过滤、Rerank、Response Builder 和 query.py 脚本入口 | [~] |
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
| Phase B | 数据持久化与可插拔组件 | 持久化、可插拔组件契约和首批真实 Provider 已就绪，可进入 Ingestion Pipeline 开发 | PostgreSQL/pgvector schema、Repository、文档生命周期、Loader/Splitter/LLM/Embedding/VectorStore/Reranker/Evaluator Factory、BaseTransform、DeepSeek、DashScope Embedding、PgVectorStore 与 fake 测试实现 | `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests -q` | 2026-06-06 |
| Phase C | Ingestion & Indexing Pipeline | 离线摄取与索引主链路已完成，可通过 CLI 将 Markdown/PDF 文件或目录写入 PostgreSQL、pgvector、BM25 和图片索引 | SHA256 去重、Loader、智能分块、Transform、图片 caption 降级、差量 Dense 编码、BM25、事务 upsert、生命周期管理和 `ingest.py` CLI | `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests -q`；`uv run --project services/ai-service/rag python -m src.scripts.ingest --help` | 2026-06-07 |
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

RAG 已具备 PostgreSQL/pgvector 持久化基础、完整 Repository 边界和八类可插拔组件包。配置可以创建百炼 DeepSeek、百炼 `text-embedding-v4`、PgVectorStore 以及不访问外部服务的 fake 实现，阶段 C 可以直接编排离线摄取链路。

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

阶段 C 复用 `DocumentRepository`、`ChunkRepository`、`ImageStorage`、`LoaderFactory`、`SplitterFactory`、`BaseTransform`、`EmbeddingFactory` 和 `VectorStoreFactory`，实现 dedup -> load -> split -> transform -> encode -> upsert 的完整 Ingestion Pipeline。Transform 由 `src/ingestion/transform/TransformPipeline` 串行编排，不创建独立工厂。

#### 阶段 C 交付里程碑：Ingestion & Indexing Pipeline

完成日期：2026-06-07

项目当前位置：

RAG 已具备可独立运行的离线数据摄取能力。统一 `IngestionPipeline` 可以从原始 Markdown/PDF 文档开始，完成 source hash 去重、Loader 标准化、业务 Chunk 适配、串行 Transform、图片描述降级、Dense/BM25 双路索引、差量向量复用和 PostgreSQL 事务写入。

可用功能：

- 对未变化且已成功摄取的文档执行 SHA256 skipped 快速结束。
- 从 Markdown/PDF 提取正文、标题层级、图片占位符和图片 metadata。
- 生成稳定 chunk ID、source_ref、image_refs 和有序 chunk_index。
- 串行执行 metadata enrich、chunk rewrite、semantic merge、denoise 和 image caption。
- 复用成功文档中相同 content_hash 的 Dense 向量，仅编码新增或变化内容。
- 将 document、chunk、pgvector、BM25 posting 和 image index 作为完整快照写入。
- 通过 `python -m src.scripts.ingest --path ... [--collection ...] [--force]` 摄取单文件或递归目录。

验证方式：

- `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests -q`
- `uv run --project services/ai-service/rag python -m src.scripts.ingest --help`

下一阶段入口：

阶段 D 直接复用已持久化的 chunk、Dense 向量和 BM25 posting，实现 Query Processor、Dense Route、Sparse Route、RRF Fusion、metadata filter、Rerank 和本地 `query.py` 调试入口。

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
| B11 | 实现首批真实组件最小适配 | [✔] | 2026-06-06 | 已实现百炼 DeepSeek、百炼 text-embedding-v4 OpenAI 兼容调用和 PgVectorStore；factory 单元测试、pgvector 集成测试和 external smoke test 已覆盖 |

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
| C9 | 实现统一 upsert | [✔] | 2026-06-07 | 已实现 rag_bm25_terms schema、BM25Storage、UpsertStep 单事务完整快照写入、pgvector/image/repository 调用方事务接口、图片文件失败恢复、重复 upsert 幂等和内容变更旧 chunk 清理；2 个 C9 PostgreSQL 集成测试、148 个全量测试通过，2 个 external smoke test 默认跳过 |
| C10 | 实现统一 Pipeline MVP 编排和集成测试 | [✔] | 2026-06-07 | 已实现 IngestionPipelineResult、完整依赖校验、run_indexing、Markdown 图片摄取、Splitter、Transform/ImageCaptioner、成功文档 content_hash 向量复用、重复内容单次编码、Dense/BM25 batch、统一 upsert、lifecycle success 和重复文件 dedup skip；6 个 ingestion integration 测试、14 个 embedding 单元测试、153 个全量测试通过，2 个 external smoke test 默认跳过 |
| C11 | 新增 `ingest.py` 摄取脚本入口 | [✔] | 2026-06-07 | 已实现必填 `--path`、可选 `--collection`、`--force`、父目录 `.env` 自动加载、系统环境优先、RAG 根目录运行时路径解析、递归 Markdown/PDF 发现、配置驱动 Pipeline 组装、JSON 结果、错误码和连接池释放；真实购物指南 PDF 完成 4 个 chunk、4 个 Dense 向量、4 份 BM25 数据和 3 张图片摄取；25 个 Loader/CLI 测试、163 个全量测试通过，2 个 external smoke test 默认跳过 |

#### 阶段 D：Retrieval

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| D1 | 实现 Query Processor | [✔] | 2026-06-07 | 已实现不可变 ProcessedQuery 和 keywords 快照、Unicode/空白标准化、关键词提取、collection/top_k 类型校验与默认覆盖、四类购物意图、商品工具协同判断、可注入 QueryRewriter 和异常/空结果 fallback；15 个 D1 单元测试通过 |
| D2 | 实现 Dense Route 向量检索 | [✔] | 2026-06-07 | 已实现 raw query/ProcessedQuery 输入、Query Embedding、配置驱动 dense_top_k、VectorStore 语义召回、RetrievalResult 校验、embedding/vector search 错误边界和低侵入 Trace；8 个 D2 单元测试通过 |
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
| Phase C | 11 | 11 | 100% |
| Phase D | 14 | 2 | 14% |
| Phase E | 4 | 0 | 0% |
| Phase F | 12 | 0 | 0% |
| Phase G | 5 | 0 | 0% |
| Phase H | 6 | 0 | 0% |
| **总计** | **70** | **31** | **44%** |

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

- `OpenAIEmbedding.embed()`：通过百炼 OpenAI 兼容接口调用 `text-embedding-v4` 生成单条文本向量
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

修改文件：`src/storage/schema.sql`、`src/storage/bm25_storage.py`、`src/storage/repositories.py`、`src/libs/vector_store/pgvector_store.py`、`src/ingestion/storage/upsert_step.py`、`src/storage/image_storage.py`、`tests/integration/test_ingestion_pipeline.py`、`tests/integration/test_repositories.py`

实现类/函数：

- `UpsertStep.run()`：校验完整索引快照，并在一个 PostgreSQL 事务内统一写入 document、chunk、向量、BM25 和图片索引
- `BM25Storage.upsert_index()`：按 document 替换完整 BM25 posting 快照
- `DocumentRepository.upsert_in_transaction()`：复用调用方事务写入 document
- `ChunkRepository.upsert_many_in_transaction()`：复用调用方事务替换完整 chunk 快照
- `PgVectorStore.upsert_in_transaction()`：复用调用方事务写入 Dense 向量
- `ImageStorage.image_path()`：安全解析 `data/images/{collection}/` 下的受管图片路径
- `ImageStorage.upsert_index_in_transaction()`：复用调用方事务写入图片索引

验收标准：同一完整快照重复 upsert 不产生重复记录且返回相同有序 ID；Transform 基于新 content_hash 生成新 chunk_id 后，统一 upsert 清理旧 chunk 及其 BM25 posting；支持批量 upsert 且返回结果保持输入顺序；文档、chunk、向量、BM25 和 `image_index` 在同一个 PostgreSQL 事务内一致写入；向量或数据库写入失败时所有数据库记录回滚，事务前被替换的受管图片恢复原内容。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_ingestion_pipeline.py -v`

##### C10：实现统一 Pipeline MVP 编排和集成测试

目标：在统一 `pipeline.py` 中把摄取结果、ImageCaptioner、DenseEncoder、BM25Indexer、BatchProcessor 和 upsert 串成最小可运行链路。

修改文件：`src/ingestion/pipeline.py`、`src/ingestion/embedding/embedding_step.py`、`src/storage/repositories.py`、`tests/unit/test_embedding.py`、`tests/integration/test_ingestion_pipeline.py`

实现类/函数：

- `IngestionPipeline.run_indexing()`：编排索引 MVP 子链路
- `IngestionPipeline.run()`：串联摄取与索引主链路
- `IngestionPipelineResult`：定义统一摄取与索引流程返回结果
- `ChunkRepository.get_dense_vectors_by_content_hashes()`：读取同一 collection 中成功文档的可复用 Dense 向量
- `EmbeddingStep.run_batch()`：复用已有 content_hash 向量，避免重复模型调用并恢复每个 chunk 的有序 Dense 结果

验收标准：给定原始文档路径，可以完成去重、Loader、Splitter、Transform、ImageCaptioner 条件 caption、DenseEncoder 编码、BM25Indexer 索引、BatchProcessor 批处理、统一 upsert 和 lifecycle success；同一路径同内容重复执行时命中 successful source hash 并直接 skipped，不重复调用 Loader、Embedding 或 upsert；文档局部变化时，数据库中成功文档已有的 content_hash 必须复用 Dense 向量，仅对新增或变化内容调用 embedding；当前批次重复内容只调用一次模型，但仍为每个 chunk 返回独立且有序的 Dense 结果；Loader-only 模式保持 C1 兼容；部分后置组件配置必须启动失败；Splitter/Transform 产生空 chunk 时不得写入成功文档。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_ingestion_pipeline.py -v`

##### C11：新增 ingest.py 摄取脚本入口

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

## 7. 开发规范

### 7.1 英文注释要求

所有新增业务代码必须使用**源码级英文注释**。该要求覆盖 Python 模块、类、函数、方法、测试、配置文件和脚本。注释必须让首次接触项目的开发者无需反复追踪调用链，即可理解当前文件为什么存在、负责什么以及如何安全使用。

注释要求：

- **模块 docstring**：说明文件在系统架构中的位置、核心职责、主要协作对象和明确不负责的边界。
- **类 docstring**：说明类所代表的业务概念、生命周期、依赖关系和调用方式。
- **函数/方法 docstring**：说明业务目的、关键处理步骤、参数含义、返回值契约、可能抛出的异常和可观察副作用。
- **测试 docstring**：说明被保护的行为契约、测试输入或前置条件，以及失败通常意味着哪类回归。
- **行内注释**：只用于解释难以从代码直接读出的业务原因、算法选择、fallback、兼容处理或安全限制。
- **配置和脚本注释**：说明配置项或命令对运行行为的影响、默认策略及使用限制。
- **接口实现注释**：明确接口职责和具体实现职责，尤其说明 provider、factory、pipeline stage 与上层业务之间的边界。

Python docstring 使用一致的源码级结构。存在对应内容时，应包含 `Args`、`Returns`、`Raises`、`Side Effects` 或 `Notes`；不存在参数、返回值或异常时不添加空章节。注释必须描述当前实现的真实行为，不得复制通用模板、虚构异常或为不同方法生成相同的空泛说明。

注释重点说明：

- 业务意图和当前文件的存在理由
- 工具、组件和分层职责边界
- 输入输出及数据契约
- 异常处理和优雅降级策略
- 配置开关对运行行为的影响
- 与 AImodel、Dashboard、MCP 或 Pipeline 的协作关系

避免无意义逐行翻译、仅重复函数名称、使用“执行该层任务”等空泛描述，或用长注释掩盖本应通过命名和结构解决的代码问题。

### 7.2 Prompt 语言规范

所有提交到仓库的 Prompt 配置统一使用英文编写，包括 `description`、`system_prompt`、`user_prompt`、策略说明、约束条件和输出格式说明。统一语言便于开发者审查、版本对比、评估和跨 Provider 复用。

Prompt 语言规范：

- Prompt 指令和模板本身必须使用英文，不得混入中文说明。
- 输入数据可以保留用户或原始文档的自然语言，不需要在进入 Prompt 前强制翻译。
- 当业务需要模型输出中文时，应使用英文指令明确指定输出语言，而不是把 Prompt 本身改为中文。Image-to-Text 的 `description` 和 `key_facts` 使用简体中文，`extracted_text` 原样保留图片中的文字，不执行翻译。
- 结构化字段名、占位符和枚举值保持稳定，不因输出语言变化而改变。
- 测试必须扫描 Prompt 配置中的 CJK 字符，防止后续修改重新引入中文指令；不得使用“非 ASCII 即非英文”的判断，因为英文 Prompt 可以合法包含弯引号、温度单位和数学符号等 Unicode 内容。

### 7.3 错误处理规范

RAG 子系统错误分为：

- 配置错误：启动阶段直接失败。
- Provider 错误：返回可读错误并写 trace。
- 检索空结果：返回 `ok=true`、`is_empty=true`，让 agent 自然说明没有知识命中。
- 数据库错误：写 trace 后抛出服务异常。
- MCP 参数错误：返回 MCP tool error content。

### 7.4 安全输出规范

- 不输出内部工具 JSON。
- 不输出隐藏 prompt。
- 不编造 citation。
- 不把 RAG 内容当作实时商品事实。
- 不把过期知识用于价格、库存、优惠券有效期判断。

### 7.5 环境变量

```dotenv
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/agent
DASHSCOPE_API_KEY=你的阿里云百炼 API Key
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_API_KEY=你的 OpenAI API Key
RAG_SETTINGS_PATH=services/ai-service/rag/config/settings.yaml
RAG_DEFAULT_COLLECTION=shopping_guides
RAG_ENABLED=true
```

### 7.6 首版完成定义

首版完成需要同时满足：

- 能摄取 5 篇 `shopping_guides` Markdown 文档。
- 能写入 PostgreSQL 和 pgvector。
- Indexing Pipeline MVP 并入 `IngestionPipeline` 统一编排，并具备集成测试。
- 能通过 hybrid search 找到相关 chunk。
- 能返回 citation。
- 能通过 MCP tool 查询。
- 能被 AImodel 作为工具调用。
- pytest 单元测试和核心集成测试通过。
- Ingestion 和 Query 链路都接入 Trace 打点，并能在 trace 日志中回溯阶段耗时和候选变化。
- Dashboard 六大页面测试通过，能看到文档、chunk、trace 和评估结果。
- AImodel 集成前全链路 E2E 验收通过。

### 7.7 规格反馈同步规范

DEV_SPEC 是项目设计、实施和验收的**单一事实来源**。用户在开发过程中提出的更正、补充要求和质量约束不能只保留在对话上下文中，必须及时回写文档，使后续开发者和 AI 能继续遵循最新决策。

同步要求：

- **先更新规范，再继续开发**：用户明确修改架构、流程顺序、命名、文件位置、数据契约、测试要求、提交要求或验收标准时，应先修改 DEV_SPEC，再继续对应任务。
- **执行影响范围检查**：一次更正可能同时影响技术选型、目录结构、模块职责、数据流、测试方案、任务明细和进度统计。不能只修改用户直接指出的一行。
- **以最新明确要求为准**：新要求与旧文档冲突时，采用用户最新的明确要求，并删除或改写所有过期描述。
- **保持排期一致**：任务合并、删除、拆分或调整顺序后，必须同步更新阶段预览、任务表、实施明细、总任务数、完成数和进度百分比。
- **同步自动开发参考文件**：DEV_SPEC 修改完成后，必须执行 `sync_spec.py --force`，确保 auto-coder 使用最新规范。
- **记录可复用规则**：如果用户更正的是可长期复用的开发流程，例如注释语言、TDD、Git 提交格式或任务完成方式，应写入“开发规范”，不能只修改当前任务。
- **避免无依据扩展**：无法从代码、现有文档或用户说明确认的细节，应先询问用户，不能自行写入规范并当作已确认事实。

每次同步后至少检查：

- 是否仍存在旧名称、旧路径、旧阶段顺序或旧任务编号。
- 目录树与模块职责表是否和任务修改文件一致。
- 测试方法与验收标准是否能够实际执行。
- Trace 阶段、数据流和 Pipeline 顺序是否保持一致。
- 任务状态、完成日期和测试结果是否来源于真实执行。

### 7.8 Git 提交规范

项目采用**一个任务一个原子提交**的方式保存进度。提交只包含当前任务实现、对应测试、DEV_SPEC 进度更新和同步后的参考文件，不混入无关修改。

提交标题格式：

```text
<type>(<scope>): [TASK_ID] <summary>
```

示例：

```text
feat(rag): [A3] add unified RAG settings example
```

提交正文必须使用以下结构：

```text
Changes:
- add ...
- update ...

Testing:
- describe the TDD red-green evidence
- list the exact verification commands

Design Principles:
- list the architecture or design principles applied

Task: A3 - 阶段 A：配置与项目骨架
Spec: DEV_SPEC.md Section 6 (Project Schedule)
Tests: ✅ 5/5 passed in 0.10s
```

提交要求：

- **Changes**：具体列出新增、修改和删除内容，描述实际行为，不使用“update files”等模糊表述。
- **Testing**：记录测试命令、TDD 红灯原因、回归范围和必要的手动验证。
- **Design Principles**：记录本任务实际遵循的设计原则或模式，例如 TDD、配置驱动、工厂模式、接口隔离、优雅降级、单一事实来源。没有特殊模式时应明确写 `None beyond existing project conventions`，不能虚构。
- **Task**：必须包含任务编号和阶段标题。
- **Spec**：固定指向 `DEV_SPEC.md Section 6 (Project Schedule)`；若任务同时修改开发规范，可补充对应章节。
- **Tests**：必须引用提交前最后一次真实测试结果，包括通过数量和耗时，不得沿用旧执行结果或估算数据。
- **原子性**：暂存时使用精确文件路径；发现无关 dirty 文件时不纳入提交。
- **历史重写**：已经推送的提交只有在用户明确要求时才能重写，并使用 `git push --force-with-lease` 更新远程。
- **连续开发**：用户输入 `next` 时，先按本规范提交当前已完成任务，再开始下一个待执行任务。

### 7.9 任务完成审查门禁

每个开发任务完成实现、测试和 DEV_SPEC 进度同步后，必须进入代码审查模式检查当前任务的全部 staged、unstaged 和 untracked 变更。审查是任务完成流程的一部分，不能省略，也不能在审查完成后自动开始下一任务。

执行规则：

- **审查范围**：当前任务新增或修改的源码、测试、配置、Prompt、DEV_SPEC 和同步后的 auto-coder reference。
- **审查重点**：正确性、数据契约、异常处理、配置驱动、测试覆盖、注释质量、规范一致性和无关文件混入。
- **问题闭环**：发现可执行问题时，先按 TDD 修复，再重新运行相关测试和代码审查，直到没有未解决的审查问题。
- **审查报告**：任务结束摘要必须列出本轮审查发现的全部可执行问题，并逐项说明影响、根因、修改文件和实际修复方式。已经修复的问题也不能从最终摘要中省略；若未发现问题，应明确写“审查未发现可执行问题”。
- **修复证据**：每个审查问题都应附上对应的失败测试或检查结果、修复后的验证命令及结果，使用户能够判断问题是否真正闭环。
- **强制停止**：审查无问题后，输出任务摘要、测试证据和建议提交信息，然后停止并等待用户输入 `commit`、`skip` 或 `next`。
- **连续开发约束**：用户输入 `next` 时，只提交已经通过审查的上一任务，再执行一个新任务；新任务完成审查后必须再次停止。
- **确认不可跨任务或由压缩上下文推断**：只有在当前任务的最终审查摘要已经明确展示给用户之后，用户新发送的 `next` 才能授权提交该任务并开始下一任务。上下文自动压缩、恢复摘要、较早任务留下的 `next`，或尚未向用户展示审查结果时收到的模糊继续指令，都不能替代这次确认；此时必须先展示当前任务审查结果并停止，等待用户重新发送 `next`。
- **禁止自动连跑**：单次 `next` 不得连续实现两个或更多未开始任务。

### 7.10 uv 包与环境管理规范

RAG 独立模块统一使用 **uv** 管理 Python 依赖、虚拟环境、锁文件和命令执行，不再使用系统 Python、手工 `pip install` 或手工激活 `.venv` 作为项目开发流程。

执行要求：

- `pyproject.toml` 是依赖声明来源，`uv.lock` 是完整解析结果，两者必须同时提交。
- 首次开发或依赖变化后执行 `uv sync --project services/ai-service/rag --extra dev`。
- 常规测试使用 `uv run --project services/ai-service/rag pytest ...`。
- 静态检查使用 `uv run --project services/ai-service/rag ruff check ...`。
- Python 脚本使用 `uv run --project services/ai-service/rag python ...`。
- CI 和 Docker 必须使用 `--frozen`，锁文件与声明不一致时直接失败，禁止隐式更新。
- Docker 只安装生产依赖，使用 `uv sync --frozen --no-dev`，不把宿主机 `.venv` 复制进镜像。
- auto-coder 在任何 Python、pytest、Ruff 或规格同步命令前不再手工激活 `.venv`，统一通过 uv 选择项目环境。
- 依赖升级必须显式执行 `uv lock --upgrade-package <package>` 或经审查的 `uv lock --upgrade`，不能在普通测试任务中隐式升级。
