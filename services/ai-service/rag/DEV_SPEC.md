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
- **数据浏览器**：查询索引中的文档和 chunk 详情，方便确认知识是否进入系统，以及 chunk 内容是否适合被检索。
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

- 使用 Ragas 评估 RAG 输出上下文或由评估脚本触发生成的 Agent 最终回答的忠实度、上下文相关性等生成质量。
- 使用自定义指标评估检索质量，例如 `hit_rate`、`MRR`、引用命中率、空结果率。
- 评估入口从 golden set 读取问题和标准答案，调用真实 Query Pipeline 生成
  `query_result`，再按 `query_result.contexts[*].chunk_id` 回查 chunk 正文构造
  Ragas `retrieved_contexts`。
- 评估结果写入 PostgreSQL：`rag_evaluation_results` 保存 run 级聚合指标，`rag_evaluation_sample_results.metrics` 保存每条 golden sample 自己的 Ragas 指标，便于定位 faithfulness、answer_relevancy、context_precision、context_recall 等低分原因；如果 evaluator 只返回聚合指标，不得把聚合分数复制到样本级结果。
- Dashboard 评估面板基于 PostgreSQL 展示历史趋势、聚合分数和样本级诊断明细。

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
| BM25 中文分词 | `jieba` 应用层 analyzer | C8 的 BM25Indexer 统一使用应用层 jieba 精确模式分词，摄取与查询共用同一 analyzer；不依赖 PostgreSQL 中文分词扩展，便于 Docker、本地测试和跨环境部署 |
| PDF 转 Markdown | MarkItDown | 统一进入 Markdown 中间格式 |
| MCP | Python 官方 MCP SDK + stdio transport | 暴露 RAG tools；开发与首版集成都使用 stdio，由 AImodel 后端长期拉起 RAG MCP 子进程 |
| Dashboard | Streamlit | 本地轻量 Dashboard |
| 测试 | pytest | 单元、集成、E2E、评估测试 |

### 3.2 RAG 流水线设计

RAG 流水线分为两条主链路：**数据摄取流水线** 和 **检索流水线**。

整体设计参考 LlamaIndex 的分层思想，但不直接依赖 LlamaIndex 框架。项目内部自定义轻量接口，例如 `BaseLoader`、`BaseSplitter`、`BaseTransform`、`BaseEmbedding`、`BaseVectorStore`，让每一层都可以独立替换、组合和测试。

#### 3.2.1 流水线框架

数据摄取流水线负责把外部文件变成可检索的向量和索引数据：

```text
Dedup -> Loader -> DocumentSummarizer -> Splitter -> Transform（包含 ImageCaptioner） -> DenseEncoder/BM25Indexer -> BatchProcessor -> Upsert -> 文档生命周期管理
```

检索流水线负责把用户问题变成可引用、可直接供 AImodel 使用的最终上下文：

```text
查询预处理 -> 双路混合检索 -> 候选过滤 -> 重排 -> 最终上下文构造
```

流水线要支持 **可组合**：不同 Loader、Splitter、Transform、Embedding 和 VectorStore 可以通过配置组合成不同策略。例如首版使用 PDF/Markdown Loader + RecursiveCharacterTextSplitter + 包含 ImageCaptioner 的 Transform Pipeline + DashScope Embedding + pgvector，后续可以替换某一层而不重写整条链路。

#### 3.2.2 数据摄取流水线

数据摄取的目标是先识别原始资料是否发生变化，再把 PDF、Markdown、文档说明等资料转换为统一的 `Document(id + text + summary + metadata)` 对象，随后逐步加工为 chunk、embedding 和可追踪的索引记录。

| 层级 | 职责 | 关键实现要素 |
| --- | --- | --- |
| `Dedup` | 在进入 Loader 前判断原始文档是否需要摄取 | 每个文档先计算 SHA256 哈希纹；若 `rag_documents` 中同一 collection、canonical source_path 和 source_hash 的文档状态为 `success`，则写入 skipped ingestion trace 并直接结束摄取 |
| `BaseLoader` | 将不同来源的文件转换为统一 `Document(id + text + summary + metadata)` 对象 | 负责文件识别、使用 MarkItDown 完成 PDF -> Markdown、使用 PyMuPDF 提取 PDF 图片、编码处理和基础 metadata 抽取；`summary` 为顶层字段，由独立摘要步骤生成或更新；只处理去重判断后确认需要摄取的文档 |
| `DocumentSummarizer` | 为加载后的文档生成顶层 `Document.summary` | 作为 Loader 之后、Splitter 之前的独立步骤；读取 `document_summary_prompt.yaml`；复用统一 LLM provider；已有同版本摘要时保持幂等；摘要只作为全局语义上下文，不写入 `metadata.summary` |
| `BaseSplitter` | 纯文本切分工具 | 职责边界固定为 `str -> List[str]`，不直接接触 `Document`、`Chunk`、metadata、图片引用等业务对象；首版使用 LangChain `RecursiveCharacterTextSplitter` 作为底层 splitter |
| `DocumentChunker` | 将 `Document` 适配为业务 `Chunk` 对象 | 调用 `libs.splitter` 得到 `List[str]` 后，转换为符合 `core.types` 契约的 `List[Chunk]`；负责生成 `chunk_id`、复制检索过滤需要的业务 metadata、添加 `chunk_index`、计算 `start_offset/end_offset`、把 `source_path` 写入 chunk metadata，根据 heading offset 写入唯一章节结构字段 `section_path`，并通过扫描 chunk 正文中的 `[[image:image_id]]` 占位符生成 `image_refs`；`Document.metadata.images[]` 保存完整文档图片清单的 `id/path`，`Chunk.metadata` 保存关联图片的 `image_refs` |
| `BaseTransform` | 对粗切分 chunk 做语义二次加工和上下文增强 | 利用 LLM 的语义理解能力合并逻辑上密切相关但被物理切割拆开的 chunk；去除页眉页脚、重复目录、无意义噪声和解析残留；注入标题路径、文档主题、相邻摘要、业务 metadata |
| `ImageCaptioner` | 对带图片引用的 chunk 生成图片 caption | 当 `vision_llm.enabled=true` 且 chunk 存在 `image_refs` 时调用 Vision LLM；生成 caption 后替换 chunk 正文中的图片占位符，使 caption 进入 Dense/BM25 可检索文本；执行详情写入 ingestion trace 的 `transform.sub_stages`，不写入 chunk metadata |
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

Indexing Pipeline 首版必须并入统一摄取入口：`IngestionPipeline.run()` 在完成 Loader、Splitter 和包含 ImageCaptioner 的 Transform Pipeline 后继续调用 `IngestionPipeline.run_indexing()`，串联 `content_hash` 差量判断、Dense 向量编码、BM25Indexer 和 pgvector/BM25 upsert。该统一入口必须有集成测试，不能只实现分散的 encoder 或 upsert step。

#### 3.2.3 核心数据对象设计

RAG 流水线内部统一使用 `Document` 和 `Chunk` 作为核心数据对象。Loader 负责生成基础 `Document`，文档摘要步骤负责补充 `Document.summary`，Splitter 和 Transform 负责把 `Document` 加工为 `Chunk`，Embedding 和 Storage 只面向稳定的 `Chunk` 写入索引。

`Document` 字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `str` | 文档稳定 ID，建议由 `collection + source_path + source_hash` 生成 |
| `text` | `str` | 文档统一文本内容，PDF 先转 Markdown，图片位置写入占位符 |
| `summary` | `str/null` | 文档级语义摘要，供 chunk rewrite、文档摘要工具和 Dashboard 使用；空值表示摘要步骤未启用或摘要生成降级 |
| `metadata` | `dict` | 文档元数据，包含来源、标题、collection、hash、图片列表等信息；其中 `images[]` 对外只保留 `id/path`，图片定位信息仅作为 Loader 内部插入占位符的临时数据；Loader 可在内存态保留 `headings` 供 chunker 计算章节，但 `rag_documents.metadata` 持久化前必须裁剪 loader-only `headings` |

`Chunk` 字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `str` | chunk 稳定 ID，使用 `hash(source_path + section_path + content_hash)` |
| `text` | `str` | chunk 最终可检索文本，包含上下文增强和可选图片描述 |
| `chunk_index` | `int` | chunk 在当前 Document 中的排序，从 0 开始递增 |
| `start_offset` | `int` | chunk 在 `Document.text` 中的起始位置 |
| `end_offset` | `int` | chunk 在 `Document.text` 中的结束位置 |

`metadata.images[]` 字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `str` | 图片稳定 ID |
| `path` | `str` | 原始图片在文件系统中的存储路径 |

说明：

- 字段命名统一使用 `start_offset`，不使用 `start_offest`。
- Loader 可以在内部使用页码、物理位置、文本锚点、`text_offset` 和 `text_length` 生成图片占位符，但这些定位字段不得持久化到最终 `Document.metadata.images[]` 或 `Chunk.metadata`。`Document.metadata.headings` 只作为摄取内存态结构输入，用于 `DocumentChunker` 计算 chunk `section_path`，不得写入 `rag_documents.metadata`。
- `DocumentChunker` 必须通过扫描 chunk 正文中的 `[[image:image_id]]` 占位符生成 `image_refs`。
- `Chunk.metadata` 是 chunk 来源字段的唯一持久化载体，必须包含 `document_id` 和 `source_path`，并按需包含 `collection`、`doc_type`、`topic`、`chunk_index`、`section_path` 和 `image_refs`。
- `Chunk.metadata` 只保留检索过滤、引用构造和业务解释需要的字段，例如 `collection`、`document_id`、`source_path`、`doc_type`、`topic`、`chunk_index`、`section_path` 和可选 `image_refs`。章节结构只使用 `section_path: List[str]` 表示，不额外保存 `section`、`h2`、`h3` 或 `h4` 对象。不得保存 `images`、`headings`、`source_type`、`source_hash`、`title`、`image_captions`、`rewrite`、`semantic_merge` 或 `denoise` 等图片详情和 Transform 执行信息。

`RetrievalResult` 字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `chunk_id` | `str` | 命中的 chunk ID |
| `text` | `str` | 命中的 chunk 文本 |
| `score` | `float` | 当前检索路线返回的相关性分数；Dense/BM25 分数量纲不同，只记录，不直接互相比大小 |
| `metadata` | `dict` | chunk metadata，包含 collection、document_id、source_path、section_path、image_refs、文档状态等过滤和引用信息 |

`ProcessedQuery` 是 Query Processor 向 Dense Route、Sparse Route、HybridSearch 和 Trace 传递的统一查询对象：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `raw_query` | `str` | 用户原始输入，保留用于 Trace 和问题回溯 |
| `normalized_query` | `str` | Unicode、全半角和空白归一化后的检索 query；rewrite 成功时保存 rewrite 结果 |
| `keywords` | `tuple[str, ...]` | 不可变的有序去重关键词快照，供 Sparse Route 查询 BM25 |
| `collection` | `str` | 调用方或 Intent Router 最终确定的查询目标知识集合 |
| `top_k` | `int` | 最终期望返回数量 |
| `rewrite_applied` | `bool` | 是否成功应用 query rewrite |
| `rewrite_fallback_reason` | `str/null` | rewrite 异常或空结果时的稳定降级原因 |

Query rewrite 通过最小化 `QueryRewriter.rewrite(query)` 接口注入，Query Processor 不直接创建或判断具体 LLM Provider。未注入 rewriter 或配置关闭时直接使用原始标准化 query；Provider 异常或返回空白时自动 fallback，不阻断后续检索。Query Processor 只负责查询文本标准化、关键词提取、rewrite 和基础参数解析，不承载业务意图识别；意图识别由独立 Intent Router 在 Query Processor 之后执行。

#### 3.2.4 检索流水线

检索流水线的目标是把用户自然语言问题转换为高质量上下文，供 AImodel 生成最终回答。

| 阶段 | 职责 | 关键实现要素 |
| --- | --- | --- |
| 查询预处理 | 清洗和标准化用户问题 | 做 query normalize、关键词提取、可选 query rewrite，并解析 collection/top_k 等基础调用参数；不做业务意图识别 |
| 意图识别与路由 | 判断查询所属知识域和检索策略 | **Intent Router**：输入 `raw_query` 和 `ProcessedQuery`，基于规则、轻量语义匹配和可选 LLM fallback 输出候选 collection、domain intent、问题复杂度、检索策略、置信度和原因；结果写入 query trace 的 `intent_routing` stage |
| 双路混合检索 | 同时召回关键词相关和语义相关的 chunk | **Dense Route**：输入 `ProcessedQuery` 和 Intent Router 的路由结果，计算 Query Embedding，检索 pgvector，返回 `List[RetrievalResult]`；**Sparse Route**：使用 `ProcessedQuery.keywords` 查询 BM25 倒排索引，按 `chunk_id` 回表读取 chunk 文本和 metadata，返回 `List[RetrievalResult]`；**Fusion** 先完成 RRF 排名融合；**HybridSearch** 依赖 Query Processor、Intent Router、Dense Route、Sparse Route 和 Fusion，并负责候选去重和单路降级 |
| Rerank 前候选过滤与跳过决策 | 在精排前过滤候选并判断是否需要执行昂贵 rerank | 支持按 `collection`、`doc_type`、来源类型、文档状态、权限和生命周期状态等参数过滤，避免不符合调用参数的内容进入 Reranker；过滤后按整批候选执行 rerank skip gate，当 fusion 排序已满足高置信条件时直接使用过滤后的 RRF Top-k，否则将过滤后的 `fusion_top_k` 整批送入 Cross-Encoder 或 LLM Rerank |
| 重排 | 提升最终上下文排序质量 | 支持 Cross-Encoder 和 LLM Rerank；只在 rerank skip gate 未通过时对过滤后的候选进行二次排序，观察 rerank 前后排名变化；当 rerank 服务不可用、超时或返回异常时，自动 fallback 到过滤后的 RRF 融合排序结果 |
| Async Query Runtime | 提升在线查询并发和可控超时能力 | Phase I 新增 `AsyncQueryRuntime` 与 provider async 契约；在线 query、MCP 和 evaluation 可走 async 路径；ingestion 暂不 async 化；同步入口保留兼容包装 |
| Self-RAG 证据决策 | 判断 rerank 后证据是否相关且足够 | **SelfRagController** 位于 rerank 之后、Response Builder 之前；单 collection 查询直接消费 rerank 结果；多 collection 查询必须先完成各 collection retrieval/rerank，再跨 collection merge，最后只执行一次 Self-RAG judge；Top2/Top3 分数稳定较高时直接通过；中等置信度时先剔除极低分 chunk，减少 judge 上下文拥挤，再通过一次 LLM 调用同时返回 relevance 与 evidence sufficiency 判断；极低置信度或 judge 不通过时暂时只返回 empty result，不直接调用 Web/Tavily；后续可扩展纠错、重试或外部搜索建议 |
| 最终上下文构造 | 输出可被 Agent 直接使用的上下文和引用 | 单 collection 查询基于 Self-RAG 通过的最终候选生成编号证据块；多 collection 查询在跨 collection merge 和单次 Self-RAG 后只执行一次 Response Builder；再使用配置驱动 Prompt 将证据整理为 Agent-ready final context；保留 `[1]`、`[2]` 编号与 `query_result.contexts.rank` 对齐；只允许压缩、去重、结构化和补充使用约束，不生成最终答案、不编造商品价格/库存/链接；优化失败时 fallback 到原始编号证据块 |

RRF 融合不直接比较 Dense 分数和 BM25 分数，因为两类分数的量纲不同。融合时基于候选在各自检索结果中的排名进行倒数加权，排名越靠前贡献越大，从而让语义召回和关键词召回都能公平参与最终排序。

检索链路必须能解释每一步：原始 query 如何被预处理，BM25 和 Dense 各自召回了什么，哪些结果在 rerank 前被过滤，rerank 如何改变排序，Self-RAG 为什么接受或拒绝证据，最终引用来自哪里。

### 3.3 MCP 服务设计

首版 MCP 传输协议固定为 **stdio**。AImodel 后端作为 MCP client，在服务启动时拉起并长期复用一个 RAG MCP 子进程，而不是每次用户对话临时启动。推荐启动命令：

```powershell
uv run --project services/ai-service/rag python -m src.mcp_server.server --transport stdio
```

stdio 协议要求 stdout/stdin 只承载 MCP 协议帧，业务日志不得写入 stdout。RAG MCP 普通运行日志写入 `src/logs/app.log`，错误诊断可以写 stderr；Trace 仍按可观测性阶段写入结构化日志。MCP 启动入口必须加载本地 `.env`，并读取 `DATABASE_URL`、`DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、`RAG_SETTINGS_PATH`、`RAG_DEFAULT_COLLECTION` 等环境变量。

AImodel 集成时不让 Agent 直接依赖 RAG 检索内部实现。`services/ai-service/app/routers/AImodel/tools.py` 提供 AImodel 侧 RAG 工具适配：`PersistentMcpRagKnowledgeClient` 负责通过 stdio MCP 启动并长期复用一个 RAG MCP 子进程，调用 `query_knowledge_hub`；`search_shopping_guides` 负责把 MCP 公共响应转换为 AImodel 工具结果。H3 再把该工具包装进 LangChain Agent 工具集合，Agent 只依赖业务工具边界。

MCP 工具一：`query_knowledge_hub`

输入：

```json
{
  "query": "如何挑选高性价比无线耳机？",
  "collection": "shopping_guides",
  "collections": ["shopping_guides", "faq"],
  "top_k": 5,
  "no_rerank": false,
  "include_image_base64": false
}
```

输出：

```json
{
  "ok": true,
  "content": "Knowledge context for the Agent...\n\n[1] 可用于回答的第一段整理后知识上下文\n\n[2] 可用于回答的第二段整理后知识上下文",
  "citations": [
    {
      "document_id": "doc_wireless_earbuds",
      "chunk_id": "chunk_wireless_earbuds_core",
      "title": "无线耳机选购指南",
      "section_path": ["核心判断标准"],
      "source_uri": "shopping_guides/wireless-earbuds.md",
      "score": 0.82,
      "trace_id": "query_20260604_xxx"
    }
  ],
  "images": [
    {
      "image_id": "image_codec_table",
      "file_path": "data/images/shopping_guides/image_codec_table.png",
      "mime_type": "image/png",
      "page": 2,
      "width": 800,
      "height": 600,
      "caption": "无线耳机编码格式对比表。",
      "quality_status": "ok",
      "chunk_ids": ["chunk_wireless_earbuds_core"]
    }
  ],
  "trace_id": "query_20260604_xxx",
  "query_trace_ids": ["query_20260604_xxx"],
  "collection_results": [
    {
      "collection": "shopping_guides",
      "trace_id": "query_20260604_xxx",
      "candidate_count": 5,
      "status": "success"
    }
  ],
  "is_empty": false
}
```

`content` 是 RAG 实际返回给 Agent 或调用方的最终上下文。默认先由最终排序后的
chunk 文本按 `[1]`、`[2]` 编号生成证据块，再按 `response.evidence_context_optimizer`
配置使用 Prompt 整理为 Agent-ready final context；禁用优化或优化失败时 fallback 到
原始编号证据块。`content` 不直接序列化 Dense/Sparse 分数、向量、Provider 返回、
过滤报告或内部 tool result。`citations` 和 `images` 使用独立公共契约；默认只返回图片
metadata 与受管 `file_path`，不默认返回 base64，避免 stdio tool payload 过大。若调用方
明确传入 `include_image_base64=true`，后续工具实现可以附加受限大小的
`base64_content` 字段。`collection` 保留单 collection 兼容；`collections` 用于多 collection 查询，若两者同时传入，以去重后的 `collections` 为准，并把 `collection` 作为 `primary_collection` 兼容字段写入 trace。MCP 层不实现并行检索逻辑，只校验 query、collection/collections、top_k、no_rerank 和 include_image_base64，然后调用 D3 的并行检索编排能力。没有检索命中时返回 `ok=true`、`is_empty=true`、空
`content`、空引用和空图片列表。

业务可恢复错误不直接抛出给 Agent，而是返回结构化错误：

```json
{
  "ok": false,
  "error": {
    "code": "empty_collection",
    "message": "当前知识库暂无可检索内容"
  }
}
```

配置错误、数据库不可用、MCP server 启动失败等系统级错误可以抛出异常并写入
`src/logs/app.log`。

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
`list_providers()` 内部必须自动确保内置实现完成注册，业务代码不需要手动调用
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

ingestion:
  document_summary:
    enabled: true
    llm_provider: deepseek
    prompt_path: config/prompts/document_summary_prompt.yaml
    max_document_chars: 12000

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
- **分层清晰**：按 `llm`、`embedding`、`splitter`、`vector_store`、`reranker`、`retrieval`、`response`、`ingestion`、`observability` 等模块组织。
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
  timezone: Asia/Shanghai
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
      model: qwen-vl-max
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
    - name: image_captioner
      enabled: true
      prompt_path: config/prompts/image_caption_prompt.yaml

retrieval:
  query_rewrite_enabled: true
  dense_top_k: 30
  sparse_top_k: 30
  fusion_top_k: 12
  final_top_k: 5
  rrf_k: 60
  async_enabled: true
  max_collection_concurrency: 3
  per_collection_timeout_seconds: 60
  final_judge_timeout_seconds: 90
  response_timeout_seconds: 90
  filters:
    include_deleted: false
    default_collection: shopping_guides


rerank:
  enabled: true
  default: llm
  fallback: rrf
  prompt_path: config/prompts/rerank_prompt.yaml
  top_k: 5
  skip_gate:
    enabled: true
    min_candidates: 3
    max_candidates_for_skip: 5
    min_dual_route_hits: 1
    min_rrf_margin_ratio: 0.08
    require_document_consistency: false
    require_section_consistency: false
  providers:
    llm:
      llm_provider: deepseek
      timeout_seconds: 60
    cross_encoder:
      model: BAAI/bge-reranker-base
      device: cpu

self_rag:
  enabled: true
  high_confidence_top_n: 3
  high_confidence_min_score: 0.75
  medium_confidence_min_top_score: 0.35
  judge_min_candidate_score: 0.15
  relevance_threshold: 0.70
  evidence_sufficiency_threshold: 0.70
  fallback_action: empty
  judge_llm_provider: deepseek
  judge_prompt_path: config/prompts/self_rag_judge_prompt.yaml

response:
  evidence_context_optimizer:
    enabled: true
    llm_provider: deepseek
    prompt_path: config/prompts/evidence_context_prompt.yaml
    fallback_to_raw: true

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
  transform_snapshots:
    enabled: true
    max_chunks_per_step: 20
    max_chars_per_chunk: 800
    include_unchanged_chunks: false

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
  llm_provider: deepseek
  embedding_provider: dashscope
  async_enabled: true
  max_sample_concurrency: 2
  max_metric_concurrency: 2
  metrics:
    retrieval:
      hit_rate_at_k: true
      mrr: true
      ndcg: true
    generation:
      faithfulness: true
      answer_relevancy: true
      context_precision: true
      context_recall: true
      answer_correctness: false

mcp:
  enabled: true
  tools:
    - query_knowledge_hub
    - list_collections
    - get_document_summary
```

### 3.6 PostgreSQL 数据设计

PostgreSQL 是唯一持久化层，不使用 SQLite。所有数据库时间字段使用
`TIMESTAMPTZ` 保存绝对时间；RAG 应用连接池必须根据
`database.timezone` 为每条 PostgreSQL session 执行 `SET TIME ZONE`，
默认使用 `Asia/Shanghai`，使 Dashboard、MCP 和本地脚本通过应用连接
查询到的入库时间按北京时间展示。

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
| `rag_documents` | 文档正文、文档级摘要、元数据、SHA256、摄取状态 |
| `rag_chunks` | chunk 文本、metadata、embedding vector |
| `rag_collection_profiles` | Intent Router collection profile 文本、content_hash 和缓存 embedding |
| `rag_bm25_terms` | BM25 词项统计 |
| `image_index` | 图片文件路径和来源索引 |
| `rag_query_traces` | Query Trace 索引及顶层 `query_result` JSONB 快照 |
| `rag_ingestion_traces` | Ingestion Trace 索引、摄取历史和 skipped 结果摘要 |
| `rag_evaluation_runs` | 评估任务 |
| `rag_evaluation_results` | run 级聚合评估指标，用于历史趋势 |
| `rag_evaluation_sample_results` | golden sample 级评估诊断明细，用于定位低分样本 |

`rag_collection_profiles` 用于持久化 Intent Router 的 collection profile embedding，避免服务启动时重复调用 embedding provider：

```sql
CREATE TABLE IF NOT EXISTS rag_collection_profiles (
    id TEXT PRIMARY KEY,
    collection TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    profile_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    embedding vector(1536),
    provider TEXT,
    model TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (collection, profile_name)
);

CREATE INDEX IF NOT EXISTS idx_rag_collection_profiles_collection
    ON rag_collection_profiles(collection);
```

`id` 使用 `hash(collection + profile_name)`，`content_hash` 使用 `sha256(profile_text)`。Intent Router 启动时读取配置中的 profile 文本并计算 hash：数据库中 hash 相同且 embedding 存在时直接加载；hash 变化或记录不存在时才调用 embedding provider，并通过 upsert 更新缓存。首版推荐每个 collection 聚合为一条 profile（description + examples），将 `shopping_guides/faq/policies/manual` 控制在少量 profile embedding，查询时只需要对用户 query 做一次 embedding，再与内存 profile 向量做本地 cosine similarity。

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
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_collection ON image_index(collection);
CREATE INDEX idx_doc_hash ON image_index(doc_hash);
```

### 3.7 多模态图片处理设计

多模态处理选型为 **Image-to-Text 策略**。图片不单独建立 CLIP 多模态向量，而是先由 Vision LLM 转换为可检索的文本描述，再把描述注入 chunk 正文，使图片语义进入 Dense/BM25 索引。

#### 3.7.1 图片处理全流程

```text
文档
  -> Loader 提取图片并生成 image_id
  -> 在文档文本中写入图片占位符
  -> 输出 Document(id + text + summary + metadata.images[])
  -> Splitter 保留图片引用标记到对应 chunk
  -> ImageCaptioner 判断 vision_llm 和 image_refs
  -> 满足条件时生成 caption，写入 image_caption_artifacts，并替换 chunk 正文中的图片占位符
  -> Storage 存储增强后的 chunk、原始图片和 artifacts 中的原始 caption
```

各阶段输出：

| 阶段 | 输出 |
| --- | --- |
| Loader | `Document(id + text + summary + metadata.images[])`，其中 `summary` 是顶层可空字段，`text` 包含图片占位符，`metadata.images[]` 只保存图片 `id/path` |
| Splitter | chunk 文本保留图片引用标记，chunk metadata 增加 `image_refs: List[image_id]`；`Chunk.metadata` 不复制 `Document.metadata.images[]` |
| ImageCaptioner | 当 `vision_llm.enabled=true` 且 chunk 存在 `image_refs` 时生成 caption；把原始 caption 写入运行时 `image_caption_artifacts`，并把 `[[image:image_id]]` 替换为可被后续 rewrite 融入正文的 caption 文本 |
| Storage | 向量库存储增强后的 chunk，文件系统保存原始图片，PostgreSQL `image_index` 表优先使用 `image_caption_artifacts` 保存原始 caption 和质量状态 |

#### 3.7.2 Loader 技术要点

Loader 负责从 PDF、Markdown 或其他文档中抽取图片，并建立图片与文档文本之间的引用关系。

关键实现：

- **提取策略**：PDF 文本由 MarkItDown 转换，PDF 图片由 PyMuPDF 按页码和物理位置提取；Markdown 图片按本地图片语法解析。
- **图片 ID**：为每张图片生成稳定 `image_id`，建议基于 `source_doc + page + image_index + image_hash`。
- **引用标记**：在文档文本中写入图片占位符，例如 `[[image:image_xxx]]`，确保后续 splitter 能保留图片与上下文的关系；PDF 图片应先按 `page + position.y + position.x` 排序，再插入到对应页文本区间末尾、下一页标记之前，避免所有图片占位符集中追加到文档末尾。
- **页标记降级**：当 MarkItDown 输出包含 `<!-- page: N -->` 等页标记时，Loader 使用页区间定位；当转换结果没有页标记时，Loader 按源位置稳定排序后追加占位符；页码、物理位置和文本 offset 仅作为 Loader 内部临时定位数据，最终 `Document.metadata.images[]` 只保留 `id/path`。
- **原始图片存储**：原始图片保存到本地文件系统，数据库只保存索引和 metadata。

#### 3.7.3 Splitter 技术要点

Splitter 必须保留图片引用和文本上下文之间的关联，不能在切分时丢失图片占位符。

关键实现：

- **关联保持**：如果图片占位符位于某个标题或段落附近，应保留在对应 chunk 中。
- **chunk metadata 扩展**：每个命中图片的 chunk 增加 `image_refs: List[image_id]`；没有图片引用的 chunk 不保留 `image_refs`，所有 chunk 都不保存 `images[]`。
- **上下文保护**：当图片前后文本共同解释图片含义时，splitter 应尽量避免把图片占位符和说明文字切到不同 chunk。

#### 3.7.4 ImageCaptioner 技术要点

ImageCaptioner 是图片 caption 的业务编排层。它只在 `vision_llm.enabled=true` 且 chunk metadata 中存在 `image_refs` 时调用 Vision LLM；底层图片理解能力由 `BaseVisionLLM` 的具体实现提供。ImageCaptioner 负责读取图片引用、定位 `Document.metadata.images[]` 中的图片路径、调用图片描述能力、把 caption 写回 chunk 正文，并把原始 caption、状态、provider、model 和关联 chunk 写入运行时 `image_caption_artifacts`，供统一 upsert 写入 `image_index`。ImageCaptioner 不向 Vision LLM 发送完整 `document_context`，避免把整篇文档作为图片理解上下文造成额外 token 消耗；图片理解只使用图片本身、图片类型和 Prompt 策略。同一轮摄取中相同 `image_id` 即使被多个 chunk 引用，也只能调用一次 Vision LLM，后续 chunk 复用同一 caption 或失败结果；Provider 失败时必须记录经过脱敏和长度限制的底层错误类型与错误信息，禁止记录 API Key、base64 图片正文或完整请求体。skipped、failed、low_quality、provider、model、耗时和快照写入 ingestion trace 的 `transform.sub_stages`，但 trace 不作为 upsert 的业务数据源。

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
- PostgreSQL 使用 `image_index` 表保存图片索引信息；`image_index.metadata.caption` 优先来自运行时 `image_caption_artifacts`，仅在 artifacts 不存在时兼容解析最终 chunk 正文中的 `[[image_caption:...]]`。
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
- **Vision LLM 降级**：如果 Vision LLM 不可用，图片保留占位符，但不生成描述、不参与检索，并在 ingestion trace 的 `image_captioner` 子阶段记录 skipped 原因。
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
| `load` | Loader 类型、原始文件类型、转换后的 `Document(id + text + summary + metadata)` 摘要、图片提取数量、耗时、失败详情 |
| `document_summary` | 摘要 Prompt 版本、LLM Provider、是否生成摘要、摘要长度、是否复用已有摘要、耗时、失败详情 |
| `split` | Splitter 类型、粗切分 chunk 数量、标题层级识别结果、平均 chunk 长度、耗时、失败详情 |
| `transform` | Transform Pipeline 总耗时、输入输出 chunk 数量，以及按配置顺序记录的 `sub_stages`；每个子阶段包含配置步骤名、具体实现类、耗时、输入输出 chunk 数量、状态、失败详情和受限 `snapshots` 预览 |
| `embed` | Embedding Provider、`content_hash` 命中数量、待生成 embedding 数量、Dense 编码批次数、Sparse/BM25 编码批次数、耗时、失败详情 |
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

Query Trace 面向查询链路，结构固定为 **基础信息、各阶段详情、查询结果、汇总指标、评估指标**。`query_result` 与统计指标分离，避免业务结果被埋入 `summary_metrics`，方便评估、审计和 Dashboard 直接读取。

基础信息：

| 字段 | 记录内容 |
| --- | --- |
| `trace_id` | 单次查询链路追踪 ID |
| `trace_type` | 固定为 `query` |
| `started_at` | 查询开始时间 |
| `raw_query` | 用户原始询问 |
| `collection` | 查询目标知识集合；单 collection 查询时等于目标 collection，多 collection 查询时为 primary collection 或兼容字段 |
| `collections` | 多 collection 查询目标列表，按调用方或 Intent Router 选择顺序记录，单 collection 查询时可为空或只包含 `collection` |
| `primary_collection` | 多 collection 查询的主 collection，用于兼容旧调用方和 Dashboard 默认过滤 |
| `multi_collection` | 是否启用多 collection 并行检索 |
| `request_source` | 调用来源，例如 AImodel、MCP tool、Dashboard |

各阶段详情：

| 阶段 | 记录内容 |
| --- | --- |
| `query_processing` | 原始 query、改写 query（若有）、query normalize 方法、关键词数量、collection/top_k 参数、耗时 |
| `intent_routing` | 输入 query 摘要、候选 collection、domain intent、问题复杂度、检索策略、置信度、命中原因、fallback 状态、耗时 |
| `dense` | Query Embedding 模型、向量库 Provider、命中的 chunk ID 列表、候选数量、耗时 |
| `sparse` | BM25 方法、倒排索引命中词、命中的 chunk ID 列表、候选数量、缺失 chunk ID、耗时 |
| `fusion` | RRF 融合方法、Dense/BM25 候选来源、融合后候选快照、重复候选合并结果和耗时；async 多 collection 查询仍只记录 fusion 语义，不能把 dense/sparse/filter/rerank 总耗时合并进 fusion |
| `filter` | 过滤参数、过滤前候选快照、过滤后候选快照、被过滤候选与原因、耗时 |
| `rerank` | Reranker Provider、过滤后 rerank 前候选快照、rerank 后候选快照、skip gate 决策、fallback 原因（若有）、耗时 |
| `self_rag` | 证据分档、极低分候选裁剪数量、单次 LLM judge 输入摘要、relevance/evidence sufficiency 判断、最终通过候选快照、empty fallback 原因、耗时 |

候选快照只保存评估与回表所需的轻量字段：`rank`、`chunk_id`、`score` 和少量可过滤 metadata，不保存完整 chunk 正文。Dense 与 Sparse 阶段只记录命中的 `chunk_ids`，避免 trace 体积过大；Fusion、Filter、Rerank、Self-RAG 阶段记录排序变化、过滤变化、skip gate 决策和证据决策结果；async 多 collection 查询必须保持与同步链路一致的顶层 stage：`intent_routing`、`dense`、`sparse`、`fusion`、`filter`、`rerank`、`self_rag`、`response`。每个顶层 stage 可在 `details.collection_runs` 中记录 collection 级耗时、候选数量、状态和 fallback 原因，但不得把 dense/sparse/filter/rerank 的耗时合并记入 fusion。

查询结果：

| 字段 | 记录内容 |
| --- | --- |
| `contexts` | 最终进入响应构造的结果列表，每项包含 `chunk_id`、最终 `score` 和 `rank` |
| `content` | RAG 实际返回给 Agent 或调用方的 Agent-ready final context；禁用优化或优化失败时保存原始编号证据块 |
| `citations` | 与最终 contexts 对齐的轻量引用快照，仅保存 `document_id`、`chunk_id`、`title`、`section_path`、`score`、`trace_id`；不重复记录由完整公共响应和文档存储负责的 `source_uri` |
| `images` | 与最终 contexts 关联的轻量图片快照，仅保存 `image_id`、`chunk_ids`、`quality_status`；图片 caption、路径、页码、尺寸和 MIME 类型仍由完整公共响应与图片索引负责 |

汇总指标：

| 字段 | 记录内容 |
| --- | --- |
| `total_duration_ms` | 从 query_processing 到 response 的端到端耗时 |
| `top_score` | 最终排名第一项的分数；空结果时为 `null` |
| `candidate_count_by_stage` | dense、sparse、fusion、filter、rerank、self_rag 各阶段候选数量 |
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
| 请求结束 | pipeline 结束时调用 `trace_context.flush()`；Query Trace 将基础信息、阶段详情、查询结果、汇总指标和评估指标序列化，Ingestion Trace 保持基础信息、阶段详情、汇总指标和评估指标结构，并追加写入日志文件 |

Trace 事件示例：

```json
{"trace_id":"query_xxx","stage":"dense","method":"pgvector_search","provider":"pgvector","duration_ms":42,"candidate_count":30,"status":"success","details":{"top_k":30,"chunk_ids":["chunk_001","chunk_002"]}}
```

#### 3.8.5 Dashboard 功能设计

Dashboard 使用 Streamlit 实现，面向开发者、面试官和项目演示场景。页面设计以“看配置、管文档、查数据、追链路、跑评估”为核心。

页面 1：**系统总览**

| 模块 | 功能 |
| --- | --- |
| 组件配置 | 读取 `settings.yaml`，展示当前可插拔组件，包括 LLM、Embedding、Splitter、Reranker、VectorStore、Evaluator；Reranker 如果通过 `llm_provider` 间接调用 LLM，必须展示最终 LLM Provider 和模型；Transform 主行下使用展开/收起区域展示 `sub_transform` 的 provider、model、model_source 和 prompt_path |
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
| 单次查询详情 | 展示 query 原文、改写 query、各阶段耗时瀑布图、Dense/BM25 召回对比、RRF 融合结果、Rerank 前后对比、Self-RAG 证据决策和最终 Top-k 结果 |

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
- AImodel 最终 assistant message 通过逻辑关联表 `message_query_trace(message_id, query_trace_id)` 关联本轮使用的全部 RAG Query Trace；不使用物理外键，一个回答允许关联多个 trace id。
- RAG 评估入口允许在受控评估模式下调用 AImodel chat 接口生成最终 assistant message，然后通过 `message_query_trace` 读取 message 作为 Ragas answer；RAG 仍只读 AImodel message 结果，不接管 AImodel 会话业务。

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
| 中层 | Query 集成测试 | 中等 | 修改检索链路时运行 | 验证查询链路模块协作 | Query Processing -> Dense/BM25 -> RRF -> Rerank -> Self-RAG -> Citation |
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
| Self-RAG | rerank 后证据分档、低分候选裁剪、单次 LLM judge、empty fallback | 验证 Top2/Top3 高分直接通过；验证中置信候选先剔除极低分 chunk 后一次性返回 relevance 与 evidence sufficiency；验证极低置信或 judge 不通过时返回 empty result 且不调用 Web |
| TraceContext | 阶段记录、耗时统计、JSON Lines 输出 | 验证 `record_stage()` 记录阶段详情；验证 `flush()` 写出结构化 JSON；验证 error 和 fallback 信息进入汇总指标 |
| Factory | 配置驱动、接口隔离、优雅降级 | 验证根据 `settings.yaml` 创建指定 Provider；验证未知 Provider 抛出可读错误；验证默认 fallback 策略生效 |

#### 4.2.3 集成与端到端测试重点

| 测试类型 | 测试重点 | 典型测试用例 |
| --- | --- | --- |
| Ingestion 集成测试 | 验证摄取链路可完整写入 PostgreSQL/pgvector | 使用一份小型 Markdown 指南，执行 load -> split -> transform -> batch -> upsert，验证文档、chunk 正文 caption、Dense 向量、BM25 索引、`image_index` 和 ingestion trace 都存在 |
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
    "expected_doc_ids": [
      "doc-wireless-earbuds"
    ],
    "difficulty": "easy"
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
│       ├── self_rag_judge_prompt.yaml             # Self-RAG 阶段一次性判断 relevance 和 evidence sufficiency 的提示词模板
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
│   │   │   ├── async_runtime.py                   # 在线查询 async runtime，编排 async provider、并发 collection、单次 Self-RAG 和 Response
│   │   │   ├── async_adapters.py                  # 将同步 provider 包装为 async 接口的兼容适配层
│   │   │   ├── hybrid_engine.py                   # 编排 Dense Route、Sparse Route 和融合流程
│   │   │   ├── dense_route.py                     # Query Embedding 和 pgvector 语义召回
│   │   │   ├── sparse_route.py                    # BM25 和倒排索引关键词召回
│   │   │   ├── fusion.py                          # RRF 排名倒数融合
│   │   │   ├── trace_snapshots.py                 # Query Trace 候选快照构造
│   │   │   ├── reranker.py                        # 调用 reranker 并处理 fallback
│   │   │   └── self_rag_controller.py             # Rerank 后执行证据相关性、充分性判断和 empty fallback
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
│   │   │   ├── fake_llm.py                        # 测试和离线降级使用的 Fake LLM 实现
│   │   │   ├── openai_compatible_llm.py           # OpenAI-compatible Chat 通用适配器
│   │   │   ├── deepseek_client.py                 # DeepSeek OpenAI-compatible Chat 实现
│   │   │   ├── ccswitch_client.py                 # CCSwitch 本地 OpenAI-compatible Chat 实现
│   │   │   └── dashscope_vision_llm.py            # 百炼 Qwen-VL 图片 caption 实现
│   │   ├── splitter/
│   │   │   ├── base_splitter.py                   # Splitter 最小抽象接口
│   │   │   ├── splitter_factory.py                # 根据配置创建 Splitter
│   │   │   ├── recursive_character_splitter.py    # LangChain RecursiveCharacterTextSplitter 包装
│   │   │   └── markdown_section_splitter.py        # Markdown section-aware splitter 实现
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
| `config/settings.example.yaml` | 提供完整版本化配置模板 | 展示 LLM、Embedding、Splitter、Transform steps、VectorStore、Reranker、Evaluator、Intent Router 配置和参数 |
| `config/settings.yaml` | 管理本地运行配置和组件选择 | 由示例模板复制，允许环境定制并被 Git 忽略 |
| `config/intent_routes.yaml` | 保存 Intent Router 规则路由配置 | 按 route 定义 collection、domain_intent、priority、confidence、any/all/regex 匹配条件；由代码加载、校验并预编译 regex，不写死在业务代码中 |
| `config/collection_profiles.yaml` | 保存 collection 语义画像 | 为 `shopping_guides/faq/policies/manual` 等 collection 定义 description 和 examples；profile 文本 hash 与 embedding 缓存在 `rag_collection_profiles` |
| `config/prompts/rerank_prompt.yaml` | 保存 rerank 阶段提示词 | prompt 与代码分离，便于评估不同 rerank 策略 |
| `config/prompts/document_summary_prompt.yaml` | 保存文档级摘要提示词 | 在 Loader 后生成 `Document.summary`，为 chunk rewrite 提供全局语义上下文；首版通过 `ingestion.document_summary.llm_provider=deepseek` 显式使用 DeepSeek |
| `config/prompts/rewrite_chunk_prompt.yaml` | 保存 chunk 语义改写提示词 | 支持 Transform 阶段结合 `Document.summary` 做 chunk rewrite；Prompt 只接收 chunk 正文和文档摘要，不接收 metadata 或 image_refs；输出只允许把 searchable text 写入 `text` 字段，禁止把 metadata/image_refs 报告写入正文 |
| `config/prompts/semantic_merge_prompt.yaml` | 保存相邻 chunk 合并判断提示词 | 仅合并逻辑连续内容，要求结构化 merge 决策和合并文本 |
| `config/prompts/image_caption_prompt.yaml` | 保存图片 caption 提示词 | 使用英文 Prompt 指令，按图片类型生成可检索的简体中文描述，并原样保留图片中的文字 |
| `data/raw/shopping_guides/` | 存放 shopping_guides collection 原始文档 | 按 collection 分类，便于离线摄取和回归测试 |
| `data/db/postgres/` | 存放 PostgreSQL 本地开发辅助数据 | 保存初始化辅助文件、dump 或本地持久化数据 |
| `data/db/bm25/` | 存放 BM25 本地索引辅助数据 | 保存倒排索引和词项统计缓存 |
| `tests/fixtures/golden_set.json` | 存放黄金测试集 | JSON 格式，包含问题、标准答案、collection、期望文档 ID 和难度；与 `evaluation.golden_set_path` 保持一致 |

#### 5.3.2 Core 层

| 文件 | 具体职责 | 关键技术点 |
| --- | --- | --- |
| `src/core/config.py` | 加载 settings 和 prompt 配置 | Pydantic/YAML 校验、环境变量覆盖、默认值处理 |
| `src/core/types.py` | 定义核心数据结构 | `Document(id,text,summary,metadata)`、`Chunk(id,text,metadata,chunk_index,start_offset,end_offset)`、`RetrievalResult(chunk_id,text,score,metadata)`、`Document.metadata.images[]` 的 `id/path` 契约、`Citation`、`TraceRecord` |
| `src/core/errors.py` | 定义统一异常类型 | 配置错误、Provider 错误、检索错误、摄取错误、MCP 错误 |
| `src/core/bm25_analyzer.py` | 统一 BM25 词法分析和候选契约 | 摄取与在线查询复用同一个 analyzer；英文/数字保持 normalize，中文使用 jieba 精确模式分词，避免分析漂移和 ingestion/storage 循环依赖 |
| `src/core/query_engine/query_processor.py` | 处理用户 query | normalize、可选 rewrite、collection/top_k 解析、关键词提取；不承担业务意图识别 |
| `src/core/query_engine/intent_router.py` | 执行查询意图识别与路由 | `IntentRouter` 在 Query Processor 之后运行；先按 `intent_routes.yaml` 执行规则路由，再按 `collection_profiles.yaml` 与 `rag_collection_profiles` 执行语义画像路由，最后按配置进入 LLM fallback；输出候选 collection、domain intent、复杂度、检索策略、置信度、命中原因和降级状态，并写入 `intent_routing` trace stage |
| `src/core/query_engine/async_runtime.py` | 编排在线查询 async 主链路 | `AsyncQueryRuntime` 只覆盖 query/MCP/evaluation 在线路径；通过 async provider 契约执行 query processing、collection retrieval/rerank、跨 collection merge、单次 Self-RAG 和单次 Response Builder；同步 `QueryRuntime` 保持兼容入口 |
| `src/core/query_engine/async_adapters.py` | 提供同步 provider 的 async 兼容层 | 使用 `asyncio.to_thread()` 包装尚未原生 async 化的 LLM、Embedding、VectorStore、BM25Indexer、Reranker 和 Response 优化器；统一 timeout、cancel 和错误转换边界 |
| `src/core/query_engine/parallel_retrieval.py` | 编排多 collection 并行检索 | `ParallelRetrievalController` 消费 Intent Router 或 MCP 传入的 collections，按 collection 并发执行 retrieval/rerank 子链路，汇总 per-collection trace、部分失败和最终合并候选；多 collection 模式下 Self-RAG 和 Response Builder 必须在跨 collection merge 后只执行一次 |
| `src/core/query_engine/hybrid_engine.py` | 编排混合检索主流程 | `HybridSearch`、Intent Router 结果消费、Dense/BM25 双路召回、RRF Fusion、候选去重、保留过滤前 fusion 快照、rerank 前 metadata 过滤、单路失败降级 |
| `src/core/query_engine/dense_route.py` | 执行语义向量召回 | Query Embedding、pgvector search、返回 `RetrievalResult(chunk_id,text,score,metadata)` |
| `src/core/query_engine/sparse_route.py` | 执行关键词召回 | `ProcessedQuery.keywords`、`bm25_indexer.query()`、`vector_store.get_by_ids()` 回表、返回 `RetrievalResult`，直接复用 Chunk.metadata 中的来源字段供 CitationBuilder 使用 |
| `src/core/query_engine/fusion.py` | 融合 Dense/BM25 结果 | RRF 基于排名倒数加权，不直接比较不同分数 |
| `src/core/query_engine/trace_snapshots.py` | 构造 Query Trace 候选快照 | 输出不含正文的轻量候选快照；Dense/Sparse 只记录 chunk IDs，Fusion/Filter/Rerank 记录排序与过滤变化 |
| `src/core/query_engine/reranker.py` | 编排过滤后候选的精排与降级 | `RerankController` 调用 Cross-Encoder/LLM Reranker；provider 缺失、超时、异常或返回过滤集外候选时 fallback 到调用前保存的过滤后 RRF 顺序；`RerankOutcome` 显式返回最终结果、fallback 状态和原因，禁止从 provider metadata 推断控制流；输出和 fallback 均使用防御性副本并记录低侵入 rerank trace |
| `src/core/query_engine/self_rag_controller.py` | 执行 rerank 后证据决策 | `SelfRagController` 根据 TopN rerank 分数进行高/中/低置信分档；中置信时先剔除极低分候选，再用一个 LLM judge 同时返回 relevance 与 evidence sufficiency；当前 fallback 只支持 empty result，不直接调用 Web/Tavily |
| `src/core/response/response_builder.py` | 构建 RAG 工具公开响应 | `KnowledgeHubResponseBuilder` 先从最终排序 chunk 文本生成编号证据块，再调用可选 `EvidenceContextOptimizer` 生成 Agent-ready final context；优化失败时按配置 fallback 到原始编号证据块；不序列化内部 route/tool metadata |
| `src/core/response/evidence_context_optimizer.py` | 优化最终上下文 | 读取 `evidence_context_prompt.yaml`，调用统一 `BaseLLM.chat()` 将编号证据压缩、去重和结构化为供 AImodel 直接使用的上下文；禁止生成最终答案或动态商品事实 |
| `src/core/response/__init__.py` | 导出响应层公共契约 | 为 MCP、AImodel、CLI 和 Dashboard 稳定导出 Citation、KnowledgeHubResponse、ResponseImage 及其 Builder/Assembler |
| `src/core/response/citation_builder.py` | 从最终排序结果构建引用来源 | 顶层 metadata 来源字段、标题文件名回退、section_path 归一化、trace_id 关联、缺失来源 fail fast、不从 chunk 正文猜测 citation |
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
| `src/libs/llm/base_llm.py` | 定义 LLMClient 抽象接口 | `chat(messages) -> response`；Phase I 增加 `async_chat(messages) -> response`，原生 async provider 直接实现，旧同步实现通过适配器兼容 |
| `src/libs/llm/base_vision_llm.py` | 定义 Vision LLM 抽象接口 | `caption_image(image_path, prompt) -> VisionCaptionResponse`，只暴露图片 caption 所需的最小接口；ingestion 暂不纳入 Phase I async 改造 |
| `src/libs/llm/llm_factory.py` | 创建 LLMClient | 根据 settings 选择 OpenAI/Azure/Ollama/DeepSeek |
| `src/libs/llm/openai_client.py` | OpenAI Chat 实现 | OpenAI SDK、统一 messages 输入输出 |
| `src/libs/llm/azure_openai_client.py` | Azure OpenAI Chat 实现 | Azure endpoint、deployment、api-version |
| `src/libs/llm/ollama_client.py` | Ollama 本地 LLM 实现 | 本地模型调用、离线降级 |
| `src/libs/llm/deepseek_client.py` | DeepSeek 兼容接口实现 | OpenAI-compatible chat API |
| `src/libs/llm/dashscope_vision_llm.py` | 百炼 Vision LLM 实现 | 使用 Qwen-VL-Max 生成图片 caption；读取 `DASHSCOPE_API_KEY` 和 `DASHSCOPE_BASE_URL`，返回统一 `VisionCaptionResponse` |
| `src/libs/splitter/base_splitter.py` | 定义 Splitter 抽象接口 | 纯文本工具，接口固定为 `split(text: str) -> List[str]` |
| `src/libs/splitter/splitter_factory.py` | 创建 Splitter | 根据配置选择 splitter 实现 |
| `src/libs/splitter/recursive_character_splitter.py` | 包装 LangChain splitter | 只输出文本片段 `List[str]`，不创建业务 `Chunk`，不引入 LangChain RAG 链路 |
| `src/libs/splitter/markdown_section_splitter.py` | Markdown 结构感知 splitter | 优先按 `###` 构建 section，短 section 合并，长 section 二次切分，表格按行分组并保留表头 |
| `src/libs/transform/base_transform.py` | 定义 Transform 抽象接口 | `transform(chunks, context) -> chunks`；具体执行顺序由 ingestion pipeline 负责 |
| `src/libs/embedding/base_embedding.py` | 定义 EmbeddingClient 抽象接口 | `embed(text)`、`embed_batch(texts)`；Phase I 增加 `async_embed()` 和 `async_embed_batch()`，用于 query embedding 与 evaluation embedding 并发调用 |
| `src/libs/embedding/embedding_factory.py` | 创建 EmbeddingClient | 根据配置选择 OpenAI/fake embedding |
| `src/libs/embedding/openai_embedding.py` | OpenAI 兼容 embedding 实现 | 百炼 `text-embedding-v4`、1536 维、批量调用和响应顺序恢复 |
| `src/libs/embedding/fake_embedding.py` | 测试 embedding 实现 | 单元测试稳定向量，不访问外部 API |
| `src/libs/vector_store/base_vector_store.py` | 定义 VectorStore 抽象接口 | `upsert(chunks)`、`search(vector, filters, top_k)`；Phase I 增加 `async_search()`，upsert 所属 ingestion 路径暂不 async 化 |
| `src/libs/vector_store/vector_store_factory.py` | 创建向量存储实现 | 首版创建 pgvector store，预留扩展 |
| `src/libs/vector_store/pgvector_store.py` | pgvector 实现 | PostgreSQL vector(1536)、cosine search、metadata filter；Dense search 直接读取 metadata 并注入 RetrievalResult |
| `src/libs/vector_store/fake_vector_store.py` | 内存 VectorStore 测试实现 | cosine search、metadata filter、ID 顺序恢复，并与 pgvector 保持 metadata 来源字段契约 |
| `src/libs/reranker/base_reranker.py` | 定义 Reranker 抽象接口 | `rerank(query, candidates)`；Phase I 增加 `async_rerank()`，LLM reranker 使用原生 async HTTP，Cross-Encoder 可使用受限 executor 或专用推理队列 |
| `src/libs/reranker/reranker_factory.py` | 创建 Reranker | Cross-Encoder、LLM Rerank、None/fallback |
| `src/libs/reranker/cross_encoder_reranker.py` | Cross-Encoder 精排实现 | query-document pair 打分、排序 |
| `src/libs/reranker/llm_reranker.py` | LLM Rerank 实现 | prompt 驱动排序、超时 fallback |
| `src/libs/evaluator/base_evaluator.py` | 定义 Evaluator 抽象接口 | `evaluate(dataset, predictions) -> metrics` 保持最小抽象；支持具体 evaluator 额外提供 `evaluate_with_samples()` 返回样本级指标 |
| `src/libs/evaluator/evaluator_factory.py` | 创建 Evaluator | Ragas 或自定义指标 |
| `src/libs/evaluator/ragas_evaluator.py` | Ragas 指标实现 | Faithfulness、Answer Relevancy、Context Precision、Context Recall；透出聚合指标和可选样本级指标 |
| `src/libs/evaluator/custom_evaluator.py` | 自定义指标实现 | Hit Rate、MRR、NDCG、citation_hit_rate |

#### 5.3.4 Ingestion 层

| 文件 | 具体职责 | 关键技术点 |
| --- | --- | --- |
| `src/ingestion/pipeline.py` | 编排离线摄取与索引主流程 | 编排 dedup -> load -> document_summary -> split -> transform -> existing content_hash vector lookup -> Dense/BM25 batch -> transactional upsert -> lifecycle success；图片 caption 记录在 `transform.sub_stages.image_captioner`；支持 Loader-only 模式并拒绝部分依赖和空 chunk 快照 |
| `src/ingestion/loader.py` | 调用 Loader 并输出 Document | 去重通过后的 Loader 调用和 Document 标准化 |
| `src/ingestion/document_summarizer.py` | 生成文档级语义摘要 | 读取 `document_summary_prompt.yaml`，调用统一 LLMClient，写入 `Document.summary`，按 prompt version 和文档 hash 保持幂等 |
| `src/ingestion/pdf_to_markdown.py` | PDF 转 Markdown 辅助逻辑 | MarkItDown、页码、图片抽取、基于图片矩形和相邻文本块生成图片锚点 |
| `src/ingestion/chunk/splitter_step.py` | 执行 chunk 初始切分 | 调用 `DocumentChunker`，完成 `Document -> List[Chunk]` 业务适配 |
| `src/ingestion/chunk/document_chunker.py` | 业务 chunk 适配器 | 调用 `libs.splitter` 的 `str -> List[str]` 能力，生成 `chunk_id`、保留检索过滤所需 metadata、添加 `chunk_index`、写入来源 metadata、通过占位符扫描分发 `image_refs`，并把纯图片占位符片段合并到相邻正文 chunk |
| `src/ingestion/chunk/chunk_id.py` | 生成稳定 chunk_id | `hash(source_path + section_path + content_hash)` |
| `src/ingestion/transform/transformer.py` | 编排 Transform 阶段 | 从 `settings.transform.steps` 读取顺序并串行执行；通过可选 observer 输出每个实现的耗时、输入输出数量、变化/未变化数量、状态、错误和受限 before/after 快照 |
| `src/ingestion/transform/metadata_enricher.py` | metadata 注入实现 | 标题路径、来源、文档主题、业务 metadata 注入 |
| `src/ingestion/transform/chunk_rewriter.py` | LLM 改写 chunk | 使用 `Document.summary` 作为全局上下文；将 chunk 拆分为文本节点与图片节点，只改写文本节点，再按原顺序重组图片占位符 |
| `src/ingestion/transform/semantic_merge_transform.py` | 智能合并 chunk | 合并逻辑相关但被物理切割的 chunk，保留来源 metadata 和 image_refs |
| `src/ingestion/transform/denoise_transform.py` | 去噪处理 | 删除页眉页脚、重复目录、解析残留，保留图片占位符 |
| `src/ingestion/transform/image_captioner.py` | 图片 caption 编排 | `vision_llm.enabled` 判断、`image_refs` 条件触发、占位符替换为 `[[image_caption:image_id]] + caption`、trace 执行详情输出 |
| `src/ingestion/embedding/embedding_step.py` | 编排 Embedding 阶段 | `run_dense()` 提供窄粒度差量编码；`run_batch()` 复用数据库已有 content_hash 向量、对当前批次重复内容只调用一次模型，并为每个有序 chunk 生成完整 Dense 结果，同时编排 BM25Indexer |
| `src/ingestion/embedding/dense_encoder.py` | DenseEncoder | content_hash 计算、差量判断、单 chunk `embed()` 编码和 C9 批量 `embed_batch()` 编码；不承担 retry、upsert 或 BM25 职责 |
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
| `src/scripts/run_evaluation.py` | 运行评估任务 | 读取 golden_set.json，默认使用 AImodel message answer，并支持 RAG context answer 调试模式，输出指标并写库 |
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
| `src/observability/services/evaluation_service.py` | Dashboard 运行评估 | 触发评估、读取历史趋势、持久化 run 级指标和 golden sample 级诊断指标；只在 evaluator 返回真实样本级指标时写入 `sample_results.metrics` |
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
| `src/observability/evaluation/ragas_adapter.py` | Ragas 适配 | 将项目数据转换为 Ragas 0.2 输入列，返回 run 级聚合指标，并从 Ragas dataframe 提取逐样本指标用于诊断 |

#### 5.3.7 外部接口层

| 文件 | 具体职责 | 关键技术点 |
| --- | --- | --- |
| `src/mcp_server/server.py` | 启动 MCP Server | Python 官方 MCP SDK、stdio/http 生命周期 |
| `src/mcp_server/tools.py` | 暴露 MCP tools | `query_knowledge_hub`、`list_collections`、`get_document_summary` |
| `services/ai-service/app/routers/AImodel/tools.py` | AImodel 工具适配 | 封装 `PersistentMcpRagKnowledgeClient` 和 `search_shopping_guides`，长期复用 stdio MCP 子进程并隐藏内部 RAG/MCP JSON |

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

在线查询数据流从 AImodel、MCP 工具或本地 `query.py` 脚本请求开始，经过 Query Processor、Intent Router、HybridSearch、Rerank 前候选过滤、Rerank、Self-RAG Controller 和 Response Builder，最终返回带引用来源的上下文结果。

```text
[1] 用户问题 / AImodel 请求
    例如：帮我推荐高性价比无线耳机
    |
    v
[2] Query Processor
    - query 标准化
    - 可选 query rewrite
    - 关键词提取
    - 解析 collection / top_k 默认值
    |
    v
[3] Intent Router
    - 识别知识域和业务意图
    - 判断问题复杂度与检索策略
    - 输出候选 collection、置信度和原因
    - 写入 query trace 的 intent_routing stage
    |
    v
[4] TraceContext
    - 创建 query trace
    - 记录基础请求信息
    |
    v
[5] HybridSearch
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
[6] Rerank 前候选过滤
    - 根据 collection、doc_type、来源类型等参数过滤融合候选
    - deleted / failed / 无权限候选不会进入 Reranker
    |
    v
[7] Reranker 可用性判断
    - 如果可用：对过滤后的候选执行 Cross-Encoder 或 LLM Rerank，输出精排结果
    - 如果不可用 / 超时 / 异常：fallback 到过滤后的 RRF 排序结果
    |
    v
[8] Response Builder
    - 构造引用来源
    - 组装多模态内容
    - 隐藏内部工具调用细节
    |
    v
[9] 返回 MCP / AImodel / query.py
    - 上下文 + 引用 + trace_id
    |
    v
[10] Query Trace
     - 记录召回对比、rerank 前后变化和端到端耗时
```

关键说明：

- 在线查询不直接生成最终购物答案，而是为 AImodel 提供 **可引用的知识上下文**。
- Dense Route 解决语义相似问题，Sparse Route 解决关键词、品牌、型号、术语等精确匹配问题。
- HybridSearch 负责集成 Dense/BM25、候选去重和 RRF Fusion。
- RRF Fusion 基于排名融合，避免 Dense 分数和 BM25 分数量纲不同导致排序失真。
- 候选过滤必须发生在 Rerank 前，避免不符合 `collection`、`doc_type`、权限或生命周期状态的内容进入重排阶段。
- Rerank skip gate 只做整批跳过或整批进入 Reranker 的决策，避免混合不同量纲的 fusion score 与 rerank score。
- Reranker 不可用时必须优雅降级，保证查询链路仍然可以返回可用结果。
- Self-RAG Controller 负责判断 rerank 后证据是否相关且足够；证据不足时暂时只返回 empty result，不在 RAG 内部直接调用 Web/Tavily。
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
| Phase C | Ingestion & Indexing Pipeline | 先去重的数据摄取、Loader、PDF -> Markdown、Markdown section-aware Splitter、包含 ImageCaptioner 的 Transform Pipeline、content_hash 差量、Dense/BM25Indexer 双路索引、pgvector upsert、统一 Pipeline MVP 和 `ingest.py` 脚本入口 | [✔] |
| Phase D | Retrieval | Query Processor、Intent Router、并行检索编排、Dense Route、Sparse Route、RRF Fusion、HybridSearch、Rerank 前候选过滤、Rerank、Self-RAG Controller、Response Builder 和 query.py 脚本入口 | [✔] |
| Phase E | MCP 工具服务 | MCP Server 和 `query_knowledge_hub`、`list_collections`、`get_document_summary` tools 暴露 | [✔] |
| Phase F | 可观测与管理平台 | TraceContext、结构化日志、ingestion/query 链路打点、Dashboard services、六大 Streamlit 页面和页面测试 | [✔] |
| Phase G | 质量评估体系 | 黄金测试集、检索指标、配置驱动 Ragas 生成指标、评估脚本进度日志、真实 Query Pipeline 评估入口、AImodel message answer 评估、策略对比和评估趋势 | [✔] |
| Phase H | AImodel 联调集成 | 集成前验收门禁、AImodel RAG 工具适配、商品 API 协同、前端/Agent 联调、端到端测试和 MCP 长连接优化 | [✔] |
| Phase I | Async Query Runtime | 在线 Query/MCP/Evaluation async 化、provider 原生 async、multi-collection 真并发、merge 后单次 Self-RAG 和 Response Builder；暂不改 ingestion async | [✔] |

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
| Phase D | Retrieval | 在线查询主链路已可基于已摄取知识库执行 Query Processor、Intent Router、Dense/Sparse 双路召回、RRF 融合、metadata filter、Rerank、Self-RAG 证据决策、Response Builder 和 CLI 查询；多 collection 并行检索编排已具备核心合并能力 | QueryProcessor、IntentRouter、ParallelRetrievalController、DenseRoute、SparseRoute、HybridSearch、RerankController、RerankOutcome、SelfRagController、KnowledgeHubResponseBuilder、`query.py` CLI、PostgreSQL/pgvector/BM25 集成测试 | `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests -q`；`uv run --project services/ai-service/rag python -m src.scripts.query --help` | 2026-06-07 |
| Phase E | MCP 工具服务 | MCP stdio 工具服务可被 AImodel 或其他 MCP client 发现工具 schema 并调用查询、collection 列表和文档摘要能力 | FastMCP stdio server、`.env` 加载、app.log 文件日志、支持单/多 collection 的 `query_knowledge_hub`、`list_collections`、`get_document_summary`、结构化业务错误、schema/contract 测试 | `uv run --project services/ai-service/rag pytest services/ai-service/rag/tests/unit/test_mcp_tools.py -v`；`uv run --project services/ai-service/rag python -m src.mcp_server.server --help` | 2026-06-08 |
| Phase F | 可观测与管理平台 | 可观测链路、结构化 trace、Dashboard services、六大页面和 Ingestion 管理页真实摄取操作可用 | TraceContext/TraceController、JSON Lines trace、ingestion/query 打点、Dashboard service DTO、六大 Streamlit 页面、Dashboard 启动脚本、IngestionOperationService 和页面集成测试 | `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests/integration/test_dashboard_pages.py -v`；`uv run --project services/ai-service/rag python -m src.scripts.run_dashboard --dry-run --port 8504` | 2026-06-09 |
| Phase G | 质量评估体系 | 质量评估体系支持黄金测试集、检索指标、Ragas 生成质量适配、真实评估进度日志、真实 Query Pipeline 评估入口、策略对比 runner 和评估趋势持久化 | `tests/fixtures/golden_set.json`、黄金样本 schema 校验、Hit Rate@K、MRR、NDCG、配置驱动 Ragas generation metrics、`faithfulness`、`answer_relevancy`、`context_precision`、`context_recall`、可选 `answer_correctness`、`run_evaluation.py`、`EvaluationReporter`、`src/logs/evaluation.log.jsonl`、hybrid/dense_only/sparse_only/rerank 策略对比、evaluation run/results 持久化、Agent-ready final context 评估输入、AImodel message answer 评估输入 | `uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_evaluation.py -q`；`uv run --project services/ai-service/rag ruff check services/ai-service/rag/src services/ai-service/rag/tests` | 2026-06-27 |
| Phase H | AImodel 联调集成 | RAG 独立模块已通过集成前验收，shopping guide RAG 工具已接入 AImodel Agent 工具集合，推荐、链接对比、选购指南和政策 FAQ 场景规则已写入 Agent system prompt，流式前端输出具备 tool result 和内部 ID 防泄漏门禁，AImodel RAG MCP client 可长期复用 stdio 子进程 | Dashboard 六大页面 service-backed 渲染测试、离线摄取到 Hybrid Query 的全链路 E2E、MCP stdio 子进程工具发现、`search_shopping_guides` 工具适配、Agent tool list 接入、商品事实/API 与知识补充/RAG 边界、推荐/对比/指南/FAQ 场景覆盖、message-query-trace 逻辑关联、SSE tool JSON 过滤、chunk id/trace id 可见输出过滤、Persistent MCP client 长期复用子进程、FastAPI shutdown 释放 MCP client | `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_dashboard_pages.py services\ai-service\rag\tests\e2e\test_full_rag_flow.py -v`；`uv run --project services/ai-service/rag pytest services\ai-service\tests\test_aimodel_rag_tool.py services\ai-service\tests\test_aimodel_memory.py services\ai-service\tests\test_aimodel_agent.py -v` | 2026-06-12 |
| Phase I | Async Query Runtime | 在线查询链路具备 async runtime、provider 原生 async、multi-collection 真并发和统一后处理，保留同步入口兼容且不改 ingestion async | Async provider 契约、SyncToAsync adapters、AsyncQueryRuntime、async ParallelRetrievalController、MCP async tool path、evaluation async sample runner、async performance report、merge 后单次 Self-RAG 和 Response Builder | `uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_async_query_runtime.py services\ai-service\rag\tests\integration\test_query_pipeline.py services\ai-service\rag\tests\e2e\test_full_rag_flow.py -v`；`uv run --project services/ai-service/rag ruff check services/ai-service/rag/src services/ai-service/rag/tests` | 2026-06-29 |

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
- 从 Markdown/PDF 提取正文、标题层级、图片占位符和图片 metadata；标题层级作为内存态 chunker 输入，不作为文档表 metadata 持久化。
- 生成稳定 chunk ID、来源 metadata、image_refs 和有序 chunk_index。
- 串行执行 metadata enrich、chunk rewrite、semantic merge、denoise 和 image caption。
- 复用成功文档中相同 content_hash 的 Dense 向量，仅编码未命中或内容变化的 chunk。
- 将 document、chunk、pgvector、BM25 posting 和 image index 作为完整快照写入；document metadata 入库前裁剪 loader-only headings，chunk metadata 保留 `section_path`。
- 通过 `python -m src.scripts.ingest --path ... [--collection ...] [--force]` 摄取单文件或递归目录。

验证方式：

- `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests -q`
- `uv run --project services/ai-service/rag python -m src.scripts.ingest --help`

下一阶段入口：

阶段 D 直接复用已持久化的 chunk、Dense 向量和 BM25 posting，实现 Query Processor、Intent Router、Dense Route、Sparse Route、RRF Fusion、metadata filter、Rerank 和本地 `query.py` 调试入口。

#### 阶段 D 交付里程碑：Retrieval

完成日期：2026-06-07

项目当前位置：

RAG 提供可独立运行的在线检索能力。查询入口从用户 query 开始，完成查询预处理、Dense 向量召回、BM25 关键词召回、RRF 排名融合、Rerank 前 metadata filter、可降级 Rerank、Self-RAG 证据决策、引用构造和多模态响应组装。

可用功能：

- 通过 `QueryProcessor` 生成 normalized query、keywords、collection、top_k 和购物意图信号。
- 通过 `DenseRoute` 查询 pgvector 语义候选，通过 `SparseRoute` 查询 BM25 倒排索引并回表 chunk。
- 通过 `HybridSearch` 执行 Dense/Sparse 双路 RRF 融合，并在 Rerank 前完成 collection、doc_type、source_type、document_status、lifecycle_status 和 permission 过滤。
- 通过 `RerankController` 在 Cross-Encoder/LLM Reranker 可用时重排候选，在不可用、超时、异常或非法输出时回退过滤后的 RRF 顺序。
- 通过 `RerankOutcome` 显式返回 rerank 结果、fallback 状态和 fallback reason，避免从 provider metadata 推断控制流。
- 通过 `SelfRagController` 对 rerank 后候选执行证据分档；高置信直接通过，中置信一次性调用 LLM judge 判断 relevance 和 evidence sufficiency，低置信或 judge 不通过时返回 empty result。
- 通过 `KnowledgeHubResponseBuilder` 输出文本上下文、引用来源和命中图片，不暴露内部 route/tool metadata。
- 通过 `python -m src.scripts.query --query ... [--top-k ...] [--collection ...] [--verbose] [--no-rerank]` 调试完整查询链路。

验证方式：

- `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services/ai-service/rag/tests -q`
- `uv run --project services/ai-service/rag python -m src.scripts.query --help`

下一阶段入口：

阶段 E 直接复用 `QueryRuntime`、`ParallelRetrievalController`、`SelfRagDecision`、`KnowledgeHubResponse`、citation 和 collection 查询能力，把单/多 collection 在线检索链路封装为 MCP tools，提供给 AImodel Agent 调用。

#### 阶段 E 交付里程碑：MCP 工具服务

完成日期：2026-06-08

项目当前位置：

RAG 提供可被 MCP client 调用的 stdio 工具服务。AImodel 或其他外部调用方可以通过 MCP tools 发现工具 schema，并调用知识库查询、collection 列表和文档摘要能力。MCP 层将 `QueryRuntime`、`KnowledgeHubResponse`、citation、多模态图片公开字段和 collection 元数据查询封装成稳定的工具边界。

可用功能：

- 通过 `python -m src.mcp_server.server --transport stdio` 启动 FastMCP stdio server，stdout/stdin 只承载 MCP 协议帧，业务日志写入 `src/logs/app.log`。
- 通过 `.env` 加载 `DATABASE_URL`、`DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、`RAG_SETTINGS_PATH`、`RAG_DEFAULT_COLLECTION` 等运行变量。
- 通过 `query_knowledge_hub` 查询 RAG 知识库，支持 `query`、`collection`、`collections`、`top_k`、`no_rerank`、`include_image_base64` 参数，并默认不返回图片 base64。
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

阶段 G 直接复用 PostgreSQL 中的 evaluation run/result 结构和 Dashboard 评估面板，已完成黄金测试集、自定义检索指标、Ragas adapter、策略对比、评估趋势输出、真实评估进度日志和 AImodel message answer 评估入口。

#### 阶段 I 交付里程碑：Async Query Runtime

完成日期：

项目当前位置：

RAG 在线查询链路完成 async 化，MCP 和 evaluation 可以通过 async runtime 执行多 collection 查询。多 collection 查询以 collection 为并发单元执行 retrieval/rerank，跨 collection merge 后统一执行一次 Self-RAG judge 和一次 Response Builder。离线 ingestion 仍保持同步批处理架构。

可用功能：

- Async provider 契约和同步 provider 兼容适配层。
- AsyncQueryRuntime 在线查询入口。
- 多 collection 真并发检索与部分失败降级。
- MCP `query_knowledge_hub` async 调用路径。
- Evaluation async 样本并发和指标并发限流。
- Query Trace 中可观察 async collection runs、timeout、partial failure、merge 后 Self-RAG 和 response 阶段。

验证方式：

- `uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_async_query_runtime.py services\ai-service\rag\tests\unit\test_mcp_tools.py services\ai-service\rag\tests\unit\test_evaluation.py -v`
- `$env:DATABASE_URL='postgresql://agent:agent@localhost:5432/agent_ops'; uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_query_pipeline.py -v`
- 使用 first10/last10 golden set 对比 async 前后的平均查询耗时、RAG trace 数量、Self-RAG judge 调用次数和 Ragas 指标。

下一阶段入口：

- 根据 async 查询链路效果决定是否继续对 ingestion 的 image caption、document summary 和 embedding batch 做 async/batch 优化。

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
| C8 | 实现 BM25Indexer | [✔] | 2026-06-07 | BM25Candidate、BM25IndexResult、BM25Indexer.index/query、词频统计、倒排索引、关键词 Top-k 排序、应用层 jieba 中文分词和重复 index 状态重建；实现该分词优化后需要重建受影响 collection 的 BM25 posting，并用黄金集对比 Sparse/Hybrid 召回变化 |
| C9 | 实现 BatchProcessor 批处理优化 | [✔] | 2026-06-07 | BatchProcessor、BatchRunResult、BatchSuccess、BatchFailure、DenseEncoder.encode_batch、batch_size 拆分、throttle_seconds 节流、有限 retry、失败隔离、EmbeddingStep.run_batch、Dense/BM25 批处理编排；20 个相关测试、145 个全量测试通过，2 个 external smoke test 默认跳过 |
| C10 | 实现统一 upsert | [✔] | 2026-06-07 | rag_bm25_terms schema、BM25Storage、UpsertStep 单事务完整快照写入、pgvector/image/repository 调用方事务接口、图片文件失败恢复、重复 upsert 幂等和内容变更旧 chunk 清理；2 个 C10 PostgreSQL 集成测试、148 个全量测试通过，2 个 external smoke test 默认跳过 |
| C11 | 实现统一 Pipeline MVP 编排和集成测试 | [✔] | 2026-06-07 | IngestionPipelineResult、完整依赖校验、run_indexing、Markdown 图片摄取、Splitter、包含 ImageCaptioner 的 Transform Pipeline、成功文档 content_hash 向量复用、重复内容单次编码、Dense/BM25 batch、统一 upsert、lifecycle success 和重复文件 dedup skip；6 个 ingestion integration 测试、14 个 embedding 单元测试、153 个全量测试通过，2 个 external smoke test 默认跳过 |
| C12 | 实现 `ingest.py` 摄取脚本入口 | [✔] | 2026-06-10 | 必填 `--path`、可选 `--collection`、`--force`、父目录 `.env` 自动加载、系统环境优先、RAG 根目录运行时路径解析、递归 Markdown/PDF 发现、配置驱动 Pipeline 组装、JSON 结果、错误码和连接池释放；文档摘要使用 `ingestion.document_summary.llm_provider` 指定的 DeepSeek Provider；68 个相关单元测试通过 |

#### 阶段 D：Retrieval

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| D1 | 实现 Query Processor | [✔] | 2026-06-07 | 不可变 ProcessedQuery 和 keywords 快照、Unicode/空白标准化、关键词提取、collection/top_k 类型校验与默认覆盖、可注入 QueryRewriter 和异常/空结果 fallback；Query Processor 不承载业务意图识别 |
| D2 | 实现 Intent Router | [✔] | 2026-06-28 | 独立 Intent Router 已接入 QueryRuntime；规则配置、collection profile 配置、profile embedding cache、`intent_routing` trace stage、评估 message 模式真实对话路径已完成；150 个相关单元测试、36 个集成测试和 ruff 通过 |
| D3 | 实现并行检索编排 | [✔] | 2026-06-29 | ParallelRetrievalController、MCP collections 参数、跨 collection routing_score + reciprocal_rank 合并、per-collection trace_id/状态汇总、部分失败降级和公共 collection_results 输出；103 个 Retrieval/MCP 单元测试通过 |
| D4 | 实现 Dense Route 向量检索 | [✔] | 2026-06-07 | raw query/ProcessedQuery 输入、Query Embedding、配置驱动 dense_top_k、VectorStore 语义召回、RetrievalResult 校验、embedding/vector search 错误边界和低侵入 Trace；8 个 D4 单元测试通过 |
| D5 | 实现 Sparse Route BM25 回表检索 | [✔] | 2026-06-07 | raw query/ProcessedQuery 输入、配置驱动 sparse_top_k、BM25 关键词召回、VectorStore 按 ID 回表、BM25 顺序与分数保留、缺失 chunk 跳过、空 keywords skip、错误边界和低侵入 Trace；9 个 D5 单元测试通过 |
| D6 | 实现 RRF Fusion | [✔] | 2026-06-07 | Dense/Sparse 双路 RRF 排名融合、top_k/rrf_k 参数校验、route 内重复 chunk 去重、跨 route 候选合并、RRF 分数输出、fusion metadata 诊断和稳定 tie-break；8 个 D6 单元测试通过 |
| D7 | 实现 HybridSearch 编排 | [✔] | 2026-06-07 | ProcessedQuery 与 Intent Router 输出输入、Dense/Sparse 双路调用、RRF Fusion 编排、配置驱动 fusion_top_k/rrf_k、HybridSearchResult、单路失败降级、双路失败错误边界和低侵入 Trace；5 个 D7 单元测试通过 |
| D8 | 实现 Rerank 前候选过滤与跳过决策 | [✔] | 2026-06-07 | CandidateFilter、CandidateFilterReport、HybridSearch.search filters 参数、HybridSearch.apply_metadata_filter 可复用入口、collection/doc_type/source_type/document_status/lifecycle_status/permission 过滤、默认排除 deleted、include_deleted 布尔校验、过滤 trace、rerank skip gate 高置信整批跳过策略和未知过滤键错误边界；8 个 D8 单元测试通过 |
| D9 | 实现 Cross-Encoder Reranker 适配 | [✔] | 2026-06-07 | CrossEncoderReranker、CrossEncoderScorer 协议、query-doc pair 打分、按模型分数稳定排序、top_k 截断、rerank metadata 诊断、sentence-transformers 惰性加载、ProviderError 错误边界和 RerankerFactory cross_encoder 注册；8 个 D9 单元测试通过 |
| D10 | 实现 LLM Rerank 适配 | [✔] | 2026-06-11 | LLMReranker、PromptTemplate 加载、BaseLLM 注入和结构化 JSON 排名解析；Prompt 强制只返回 JSON object array，禁止 ID-only array、Markdown fence 和解释文字；真实 DeepSeek 查询验证 rerank 成功且未触发 fallback |
| D11 | 实现 rerank fallback | [✔] | 2026-06-07 | RerankController、RerankOutcome、配置驱动 top_k、provider 调用前候选深拷贝、reranker 不可用/直接或 ProviderError 包装的 timeout/普通异常 fallback、非法/过滤集外/候选数量不符的 provider 输出防护、过滤后 RRF 顺序保留、显式 fallback 状态、低侵入 rerank trace 和 trace sink 失败隔离；28 个 Reranker 单元测试通过 |
| D12 | 实现 Self-RAG Controller | [✔] | 2026-06-28 | Rerank 后证据决策层；Top2/Top3 高置信直接通过；中置信先剔除极低分 chunk，再一次性调用 LLM judge 返回 relevance 与 evidence sufficiency；低置信或 judge 不通过时暂时 empty fallback，不直接调用 Web/Tavily |
| D13 | 实现引用构造 | [✔] | 2026-06-07 | 共享不可变 Citation 契约、CitationBuilder、Dense/Sparse/Fake 检索 metadata 来源字段传播、顶层 metadata 读取、排序保持、URI 文件名解码标题回退、section_path 归一化、JSON 输出、trace_id 关联、脏类型/缺失来源 fail fast 和输入 metadata 不变性；Citation、核心类型、来源 metadata 和 pgvector 回归测试通过 |
| D14 | 实现多模态 Response Builder | [✔] | 2026-06-07 | 不可变 KnowledgeHubResponse/ResponseImage 公共契约、排名编号证据块、配置驱动 EvidenceContextOptimizer、优化失败 fallback、CitationBuilder 复用、image_refs 有序去重和关联 chunk 聚合、ImageResolver 最小接口、ImageStorage 批量 ID 查询、缺失图片安全跳过、显式空结果以及内部 route/tool metadata 隔离；Response Builder 单元测试和真实 PostgreSQL 图片查询集成测试通过 |
| D15 | 实现 `query.py` 脚本入口 | [✔] | 2026-06-07 | 配置驱动完整查询链路、PostgreSQL BM25 collection 查询、过滤前 Fusion 快照、RerankOutcome 显式 fallback 状态、安全 verbose 输出、no-rerank 跳过和连接池释放；63 个 Retrieval 单元测试通过 |
| D16 | 实现 Retrieval 单元测试矩阵 | [✔] | 2026-06-07 | 120 个 Retrieval/Reranker/Response/Self-RAG 单元测试，补齐 Fusion 失败、PostgreSQL BM25 边界、QueryRuntime rerank、Self-RAG 分档和 empty fallback、Citation 来源 metadata 和图片 resolver 脏契约；目标模块覆盖率 91% |
| D17 | 实现 Retrieval 集成测试 | [✔] | 2026-06-07 | PostgreSQL/pgvector 集成测试，覆盖 QueryProcessor、DenseRoute、SparseRoute、HybridSearch、metadata filter、RerankController、SelfRagController、Response Builder、`query.py` verbose 输出、Dense 失败时 Sparse fallback；2 个 D17 集成测试通过 |

#### 阶段 E：MCP 工具服务

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| E1 | 搭建 MCP Server | [✔] | 2026-06-07 | FastMCP server 工厂、stdio 启动入口、`.env` 加载、app.log 文件日志、配置驱动 tool 注册、未知工具 fail fast、E1 placeholder tool 错误边界和 SDK ToolError 包装契约；5 个 MCP 单元测试通过 |
| E2 | 暴露 `query_knowledge_hub` | [✔] | 2026-06-08 | QueryKnowledgeHubTool、QueryRuntime/ParallelRetrievalController 适配、请求原语校验先于 settings 加载、默认 collection/top_k、单/多 collection 参数、no_rerank、结构化业务错误、默认不返回图片 base64、显式 include_image_base64 支持、PostgreSQL pool 打开失败也能释放资源和 FastMCP 真实 query tool 注册；MCP 层只透传并行检索请求，不实现检索业务逻辑 |
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
| G1 | 准备黄金测试集格式 | [✔] | 2026-06-10 | `tests/fixtures/golden_set.json`，定义 `id/collection/question/golden_answer/expected_doc_ids/difficulty` 字段，并覆盖 shopping_guides、faq、policies、manual 等知识库问题；2 个单元测试通过 |
| G2 | 实现自定义检索指标 | [✔] | 2026-06-10 | `src/observability/evaluation/metrics.py`，实现无 LLM 依赖的 Hit Rate@K、MRR 和 NDCG@K；指标支持字符串来源和 mapping 候选输入，校验 dataset/predictions 对齐、top_k 和 expected_doc_ids 契约；5 个 evaluation 单元测试通过 |
| G3 | 接入 Ragas 生成指标 | [✔] | 2026-06-10 | `src/core/config.py`、`src/observability/evaluation/ragas_adapter.py`、`src/libs/evaluator/ragas_evaluator.py`、`src/scripts/run_evaluation.py`，封装配置驱动的 Ragas generation metrics；默认启用 `faithfulness`、`answer_relevancy`、`context_precision`、`context_recall`，`answer_correctness` 默认关闭；`enabled_generation_metrics()` 从 `settings.evaluation.metrics.generation` 解析启用指标并由 `run_evaluation.py` 传给 evaluator；真实 Ragas 依赖采用懒加载，普通单测使用 fake backend，真实 import 测试使用 external marker 隔离；adapter 在真实 backend 边界将项目内部 `question/answer/contexts/ground_truth` 行转换为 Ragas 0.2 的 `user_input/response/retrieved_contexts/reference` 列，并对空 `metric_names` fail fast |
| G4 | 实现策略对比评估 | [✔] | 2026-06-10 | `src/observability/evaluation/runner.py` 通过可注入 retrieval callable 对比 hybrid、dense_only、sparse_only、rerank 四种策略，并复用 Hit Rate@K、MRR@K、NDCG@K 计算指标；runner 不直接打开数据库或构造 QueryRuntime；包入口导出 `RetrievalStrategy` 以支持外部自定义策略，空 metrics 配置 fail fast；9 个 evaluation 单元测试通过，1 个 external 测试按环境跳过 |
| G5 | 实现评估历史趋势展示 | [✔] | 2026-06-10 | `EvaluationRunner.save_results()`，将策略对比结果写入 `EvaluationRepository` 边界，生成一条 evaluation run 和按 `strategy.metric` 命名的 metric rows；metric details 保留 strategy、retrieval_mode、use_rerank、raw_metric_name、sample_count 和 predictions，供 Dashboard 趋势与详情展示；保存前校验各策略 prediction 数量一致，避免 summary 误导；11 个 evaluation 单元测试通过，1 个 external 测试按环境跳过 |
| G6 | 实现评估脚本进度日志与控制台反馈 | [✔] | 2026-06-27 | `run_evaluation.py` 通过 `EvaluationReporter` 输出可读阶段日志、样本级进度、耗时、trace/message 关联和失败定位；同时写入 `src/logs/evaluation.log.jsonl` JSONL 诊断日志，保留最终评估 JSON 输出契约；单元测试覆盖 reporter 注入、样本进度、失败事件和最终结果输出；25 个 evaluation 单元测试通过，1 个 external 测试按环境跳过，ruff 通过 |
| G7 | 实现真实 Ragas 评估入口与最终上下文优化 | [✔] | 2026-06-12 | 注册 `ragas` 到 `EvaluatorFactory`；新增 `run_evaluation.py` 读取 golden set、调用真实 Query Pipeline、支持 `message` 与 `rag` 两种 answer source；默认 `message` 对每个 golden question 调用 AImodel chat 接口生成 assistant message，再通过 `message_query_trace` 读取 message 作为 Ragas answer；显式 `rag` 才使用 `query_result.content` 作为上下文包调试 answer；按 `query_result.contexts` 回查 chunk 正文构造 Ragas `retrieved_contexts`、调用 `RagasEvaluator` 并写入 evaluation run/results/sample_results；`RagasEvaluator.evaluate_with_samples()` 从 Ragas dataframe 提取逐样本指标，`EvaluationService` 将其写入 `rag_evaluation_sample_results.metrics`，aggregate-only evaluator 不复制聚合指标到样本结果；同时将 `query_result.content` 升级为配置驱动的 Agent-ready final context，优化失败 fallback 到原始编号证据块；evaluation 单元测试覆盖样本级 metrics 持久化，ruff 通过，真实 Ragas provider 创建 smoke 通过 |

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

#### 阶段 I：Async Query Runtime

| 任务编号 | 任务名称 | 状态 | 完成日期 | 备注 |
| --- | --- | --- | --- | --- |
| I1 | 定义 async provider 契约与兼容适配层 | [✔] | 2026-06-29 | 为 LLM、Embedding、VectorStore 和 Reranker 补充默认 async 最小接口；新增 SyncToAsync adapters，支持 timeout 与 cancellation；同步 retrieval/evaluation async 配置；158 个相关单元测试和 ruff 通过 |
| I2 | 实现 provider 原生 async 化 | [✔] | 2026-06-29 | OpenAI-compatible/DeepSeek/CCSwitch/Embedding provider 使用原生 async SDK client；pgvector/BM25 在线查询提供 async 方法；LLM Reranker 使用 async LLM；Cross-Encoder 通过 worker thread 避免阻塞事件循环；142 个相关单元测试和 ruff 通过 |
| I3 | 实现 AsyncQueryRuntime | [✔] | 2026-06-29 | 新增在线查询 `AsyncQueryRuntime`，覆盖 query processing、intent routing、hybrid retrieval、rerank、Self-RAG 和 Response Builder；`run_query_cli()` 支持同步/async runtime 兼容执行；8 个 async runtime 单元测试、2 个 query pipeline 集成测试和 ruff 通过 |
| I4 | 实现 multi-collection 真并发与 merge 后统一后处理 | [✔] | 2026-06-29 | 新增 `AsyncParallelRetrievalController`，使用 `asyncio.gather()` 并发执行 collection retrieval/rerank 子链路；跨 collection merge 后统一 top_k 截断并保留 collection/routing/merge metadata；async trace 必须保持与同步链路一致的顶层 stage，分别记录 dense/sparse/fusion/filter/rerank 耗时、候选数、状态和 fallback 原因；Self-RAG judge 和 Response Builder 在 merge 后只执行一次；支持 max concurrency、per-collection timeout、partial failure、全部 empty 和全部失败；111 个 I4 指定单元测试和 ruff 通过 |
| I5 | 接入 MCP 与 evaluation async 路径 | [✔] | 2026-06-29 | `query_knowledge_hub` 可 await async runtime 且保留 MCP 公共响应和错误 envelope；MCP 默认按 `retrieval.async_enabled` 选择 async runtime，多 collection 工具调用使用 async gather 聚合公共结果；evaluation 在 `evaluation.async_enabled` 下并发构造 prediction，支持 `max_sample_concurrency`，Ragas client 支持 `max_metric_concurrency` 映射到 evaluator worker；RAG answer-source 单样本失败写入 prediction error，message answer-source 保持缺失 message/trace 时 fail fast；59 个 I5 目标测试通过，1 个外部 Ragas 测试按 marker 跳过 |
| I6 | 完成 async 查询验收与性能对比 | [✔] | 2026-06-29 | async 链路相关单元、集成和 MCP stdio contract 测试通过；新增 `async_query_performance_report()` 汇总 first10/last10 风格的 sync/async avg latency、P95、RAG trace、Self-RAG judge、Response Builder、timeout 和 Ragas metric delta；新增 `data/resume/async_query_runtime.md` 记录 deterministic 验收 fixture 与已有本地 first10/last10 历史耗时，严格 live A/B benchmark 需在外部模型启用时单独运行；14 个 I6 目标测试和 ruff 通过 |

### 6.4 总体进度表

| 阶段 | 总任务数 | 已完成 | 进度 |
| --- | ---: | ---: | --- |
| Phase A | 7 | 7 | 100% |
| Phase B | 11 | 11 | 100% |
| Phase C | 12 | 12 | 100% |
| Phase D | 17 | 17 | 100% |
| Phase E | 4 | 4 | 100% |
| Phase F | 12 | 12 | 100% |
| Phase G | 6 | 6 | 100% |
| Phase H | 7 | 7 | 100% |
| Phase I | 6 | 6 | 100% |
| **总计** | **80** | **78** | **97.5%** |

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
评估历史必须拆分为 `rag_evaluation_runs` 任务表、
`rag_evaluation_results` 聚合指标结果表和
`rag_evaluation_sample_results` 样本级诊断表。聚合指标用于 Dashboard
历史趋势；样本级结果用于保存每条 golden question 的 answer、contexts、
trace/chunk 溯源和 per-sample metrics，避免只能从 message/trace 反查低分原因。

修改文件：`src/storage/schema.sql`、`tests/integration/test_repositories.py`

实现类/函数：

- `image_index`：记录图片文件路径、collection、doc_hash 和页码
- `rag_query_traces`：定义数据库表结构
- `rag_ingestion_traces`：定义数据库表结构
- `rag_evaluation_runs`：定义数据库表结构
- `rag_evaluation_results`：按 `run_id` 保存 run 级单项聚合指标和结果详情
- `rag_evaluation_sample_results`：按 `run_id + sample_id` 幂等保存每条样本的输入、输出、溯源和指标明细

验收标准：`image_index`、Trace 和三张评估表可初始化；
`idx_collection`、`idx_doc_hash` 索引存在；Trace 表分别保存基础信息、
阶段详情、汇总指标和评估指标；评估结果通过外键归属评估任务；
样本级结果通过 `run_id + sample_id` 保持幂等，至少包含 `question`、
`golden_answer`、`generated_answer`、`retrieved_contexts`、`context_chunk_ids`、
`query_trace_ids`、`metrics` 和 `error`；schema 可重复执行。

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
- `EvaluationSampleResultRecord`：保存单条 golden sample 的问题、标准答案、生成答案、检索上下文、chunk/trace 溯源、per-sample metrics 和错误信息
- `TraceRepository.upsert_query_trace()`：自动创建缺失 collection，并按 `trace_id` 幂等写入 Query Trace
- `TraceRepository.upsert_ingestion_trace()`：按 `trace_id` 幂等写入 Ingestion Trace
- `TraceRepository.get_query_trace()`：按 ID 查询 Query Trace
- `TraceRepository.get_ingestion_trace()`：按 ID 查询 Ingestion Trace
- `TraceRepository.list_query_traces()`：按 collection 和开始时间倒序查询 Query Trace 历史
- `TraceRepository.list_ingestion_traces()`：按 collection 和开始时间倒序查询 Ingestion Trace 历史
- `EvaluationRepository.upsert_run()`：自动创建缺失 collection，并按稳定 ID 更新评估任务生命周期
- `EvaluationRepository.upsert_results()`：事务内批量写入指标，按 `run_id + metric_name` 幂等更新并保持输入顺序
- `EvaluationRepository.upsert_sample_results()`：事务内批量写入样本级诊断，按 `run_id + sample_id` 幂等更新并保持输入顺序
- `EvaluationRepository.get_run()`：按稳定 ID 查询评估任务
- `EvaluationRepository.list_runs()`：按 collection 和创建时间倒序查询评估历史
- `EvaluationRepository.list_results()`：按指标名称稳定排序查询任务结果
- `EvaluationRepository.list_sample_results()`：按 sample 顺序查询任务的样本级诊断结果

验收标准：Query/Ingestion Trace 可从 running 状态幂等更新为完成状态，Ingestion 四段式与 Query 五段式
JSON 数据写入后返回深层不可变记录；Trace 历史可按 collection 查询；评估任务
和多个指标结果可写入和查询；同一任务同名指标再次写入时更新稳定结果 ID、
分数和详情，保留原始创建时间；同一任务同一 sample 再次写入时更新`r`nanswer、contexts、trace/chunk 溯源、metrics 和 error，保留原始创建时间；`r`n批量结果返回顺序与输入一致；所有只读 SQL
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

目标：为 Sparse Route 构建 BM25 词项、词频和倒排索引数据，并通过应用层中文 analyzer 提升品牌、型号、参数词和政策关键词的稀疏召回稳定性。

修改文件：`src/core/bm25_analyzer.py`、`src/ingestion/embedding/bm25_indexer.py`、`src/storage/bm25_storage.py`、`tests/unit/test_bm25.py`、`tests/integration/test_query_pipeline.py`、`config/settings.example.yaml`、`pyproject.toml`、`uv.lock`

实现类/函数：

- `tokenize_bm25_text()`：使用 jieba 精确模式输出中文词项，并保留英文/数字 normalize
- `normalize_bm25_keywords()`：查询侧复用同一 analyzer，确保摄取与在线检索词项一致
- `BM25Indexer.index()`：使用统一 analyzer 生成 BM25 词项、词频和倒排索引数据
- `BM25Indexer.query()`：根据关键词返回候选 `chunk_id` 和 BM25 分数
- `BM25Storage.query()`：继续基于 PostgreSQL posting 动态计算 collection 级 BM25 分数，不依赖数据库中文分词扩展

验收标准：中文 analyzer 不依赖 PostgreSQL 扩展；中文内容使用 jieba 精确模式分词；英文品牌、数字型号和中英混合内容必须保持可检索；短 query 和长 chunk 使用同一 analyzer；实现后必须重新摄取或重建受影响 collection 的 BM25 posting，避免旧 posting 与新 analyzer 混用；RAGAS 和检索指标需要记录变更前后差异。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_bm25.py services\ai-service\rag\tests\integration\test_query_pipeline.py -v`；`uv run --project services/ai-service/rag ruff check services/ai-service/rag/src services/ai-service/rag/tests`

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
- `DocumentRepository.upsert_in_transaction()`：复用调用方事务写入 document，并在写入 `rag_documents.metadata` 前裁剪 loader-only `headings`
- `ChunkRepository.upsert_many_in_transaction()`：复用调用方事务替换完整 chunk 快照
- `PgVectorStore.upsert_in_transaction()`：复用调用方事务写入 Dense 向量
- `ImageStorage.image_path()`：安全解析 `data/images/{collection}/` 下的受管图片路径
- `ImageStorage.upsert_index_in_transaction()`：复用调用方事务写入图片索引

验收标准：同一完整快照重复 upsert 不产生重复记录且返回相同有序 ID；Transform 基于新 content_hash 生成新 chunk_id 后，统一 upsert 清理旧 chunk 及其 BM25 posting；支持批量 upsert 且返回结果保持输入顺序；文档、chunk、向量、BM25 和 `image_index` 在同一个 PostgreSQL 事务内一致写入；`rag_documents.metadata` 不持久化 loader-only `headings`，`rag_chunks.metadata.section_path` 仍按 chunk 保留；`image_index.metadata.caption` 优先使用 `image_caption_artifacts` 中的原始 caption，即使最终 chunk 正文被 rewrite 移除 `[[image_caption:...]]` 节点也必须保留 caption；没有 artifacts 时兼容解析最终 chunk 正文；向量或数据库写入失败时所有数据库记录回滚，事务前被替换的受管图片恢复原内容。

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

目标：完成 query 标准化、关键词提取、基础参数解析和可选 rewrite，为后续 Intent Router 与检索阶段提供稳定 `ProcessedQuery`。

修改文件：`src/core/query_engine/__init__.py`、`src/core/query_engine/query_processor.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `QueryRewriter.rewrite()`：定义可注入的最小 query rewrite 接口
- `ProcessedQuery`：定义 Query Processor 向后续检索阶段传递的不可变标准对象，只保存查询预处理字段
- `QueryProcessor.process()`：标准化输入、解析默认参数、执行可选 rewrite 并提取关键词

验收标准：支持 Unicode NFKC 和空白 normalize；拒绝空 query、非字符串或空 collection、非整数或非正整数 top_k；collection 和 top_k 默认读取 settings 且允许调用方覆盖；输出不可变的有序去重 keywords 快照；输出字段仅限 raw query、normalized query、keywords、collection、top_k 和 rewrite 状态；配置关闭或未注入 rewriter 时不调用 rewrite；rewrite 成功后使用重写 query 生成 keywords；rewrite 异常或空结果时回退标准化原 query 并记录稳定原因。

测试方法：`uv run --project services/ai-service/rag pytest services/ai-service/rag/tests/unit/test_retrieval.py -v`

##### D2：实现 Intent Router

目标：在 Query Processor 之后新增独立意图识别与路由层，根据用户问题选择知识域、候选 collection 和检索策略，并将结果写入 Query Trace。

修改文件：`config/settings.example.yaml`、`config/intent_routes.yaml`、`config/collection_profiles.yaml`、`src/core/config.py`、`src/core/query_engine/__init__.py`、`src/core/query_engine/intent_router.py`、`src/core/query_engine/runtime.py`、`src/core/trace/trace_context.py`、`src/storage/schema.sql`、`src/storage/repositories.py`、`tests/unit/test_config.py`、`tests/unit/test_retrieval.py`、`tests/unit/test_trace_context.py`、`tests/integration/test_repositories.py`

实现类/函数：

- `IntentRoute`：保存路由结果，包括 `domain_intent`、`collections`、`complexity`、`retrieval_strategy`、`confidence`、`method`、`reason`、`fallback_used`
- `IntentRule`：表示 `intent_routes.yaml` 中的一条规则，包含 `name`、`collection`、`domain_intent`、`priority`、`confidence`、`match.any`、`match.all` 和 `match.regex`
- `CollectionProfile`：表示 `collection_profiles.yaml` 中的 collection 语义画像，包含 description、examples、content_hash 和聚合 profile text
- `IntentRouter.route()`：接收 `raw_query` 与 `ProcessedQuery`，按 rules -> semantic_profile -> llm_fallback -> default 的顺序输出可追踪、可降级的路由结果
- `IntentRouter._route_by_rules()`：加载并校验 `config/intent_routes.yaml`，预编译 regex，使用 normalize query、jieba token 和原文字符串匹配 any/all/regex 规则
- `IntentRouter._score_rule_match()`：用 `confidence + any_count*0.03 + all_group_count*0.08 + regex_count*0.10 + priority/1000` 计算规则分数，并将最终 confidence 限制在 `0.99` 以内
- `IntentRouter._route_by_semantic_profile()`：读取 `config/collection_profiles.yaml`，按 `content_hash` 复用或刷新 `rag_collection_profiles` 中的 profile embedding，并与 query embedding 做本地 cosine similarity
- `IntentRouter._route_with_llm_fallback()`：在配置启用时调用 LLM 做兜底路由，失败时返回安全默认路由
- `CollectionProfileRepository.upsert_profile_embedding()`：按 `collection/profile_name/content_hash` 写入或更新 profile embedding 缓存
- `CollectionProfileRepository.list_profile_embeddings()`：读取可复用的 profile embedding，供 Intent Router 启动时加载到内存
- `TraceContext.record_query_stage()`：允许记录 `intent_routing` stage，并持久化路由结果摘要

验收标准：Intent Router 必须独立于 Query Processor，不能把 `intent` 字段重新写回 `ProcessedQuery`；关键词规则必须存储在 `config/intent_routes.yaml`，不得散落在代码 if/else 中；规则配置必须支持 `any/all/regex`，并通过 `priority` 和 `confidence` 分别表达冲突裁决权重与规则命中后的基础可信度；`priority` 只用于近似同分时的排序，推荐初始优先级为 policies=100、manual=90、faq=80、shopping_guides=70、default=10；`confidence` 是规则命中的基础可信度，强政策规则建议 0.92-0.98，客服话术 0.88-0.94，FAQ 0.82-0.90，选购指南 0.78-0.88，弱兜底 0.50-0.65；最终规则分数按 `confidence + any_count*0.03 + all_group_count*0.08 + regex_count*0.10 + priority/1000` 计算并封顶为 0.99；规则最高分达到阈值时直接返回，低于阈值时进入 semantic profile 或 LLM fallback；collection profile 必须存储在 `config/collection_profiles.yaml`，每个 collection 首版聚合 description 和 examples 为一条 profile text；profile embedding 必须持久化在 `rag_collection_profiles`，用 `sha256(profile_text)` 作为 `content_hash`，hash 未变时启动只加载缓存，hash 变化或 embedding 缺失时才调用 embedding provider；首版路由结果至少包含候选 collection、业务意图标签、问题复杂度、检索策略、置信度、命中原因、matched_rule、matched_terms、matched_regex 和 fallback 状态；配置关闭 LLM 或 LLM 不可用时必须降级到规则/语义路由或安全默认 collection；路由结果可被 HybridSearch/QueryRuntime 消费，AImodel 真实对话仍按生产工具选择路径运行；`rag_query_traces.stages` 必须新增 `intent_routing` 阶段，记录输入摘要、输出路由、method/provider、耗时、匹配详情和错误；评估场景中的 golden `collection` 作为期望路由标签和诊断字段，message 模式按真实 AImodel 对话路径运行。

测试方法：`uv run --project services/ai-service/rag pytest services/ai-service/rag/tests/unit/test_retrieval.py services/ai-service/rag/tests/unit/test_trace_context.py -v`


##### D3：实现并行检索编排

目标：在 Intent Router 输出多个候选 collection 或 MCP 显式传入 `collections` 时，提供统一的多 collection 并行检索入口；该层只负责任务拆分、并发执行、结果合并和 trace 汇总，不替代 Dense/Sparse/Hybrid/Rerank 的单 collection 责任边界。

修改文件：`src/core/query_engine/__init__.py`、`src/core/query_engine/parallel_retrieval.py`、`src/core/query_engine/runtime.py`、`src/core/trace/trace_context.py`、`tests/unit/test_retrieval.py`、`tests/unit/test_trace_context.py`

实现类/函数：

- `ParallelRetrievalRequest`：保存原始 query、ProcessedQuery、候选 collections、top_k、no_rerank、metadata filter 和 request_source 等并行检索输入
- `CollectionRetrievalTask`：表示单个 collection 的检索任务，包含 collection、per_collection_top_k、routing_score、routing_reason 和超时配置
- `ParallelRetrievalResult`：保存合并后的最终候选、每个 collection 的结果摘要、失败摘要、trace IDs 和是否使用部分降级
- `ParallelRetrievalController.search()`：按 collection 拆分检索任务，并发调用单 collection `QueryRuntime` 或 `HybridSearch` 路径，再合并结果
- `ParallelRetrievalController._merge_collection_results()`：跨 collection 合并候选；优先保留同一 reranker 产生的可比较 rerank score，分数不可比时使用 `routing_score + reciprocal_rank` 稳定融合，使高置信 collection 的强结果优先，同时允许低置信 collection 的高排名结果进入最终上下文
- `ParallelRetrievalController._record_trace()`：向 Query Trace 写入 `parallel_retrieval` stage，并记录每个 collection 的候选数量、耗时、状态、失败原因和最终保留结果

验收标准：当调用方只传入单个 `collection` 时必须保持现有单 collection 查询行为；当传入 `collections` 或 Intent Router 输出多个候选 collection 时，必须去重、保序并限制最大并行 collection 数；每个 collection 内部仍执行 D4-D13 的 Dense/Sparse、RRF、filter、rerank 和 Self-RAG 流程，不允许在并行层重写业务检索逻辑；并行层必须支持 per-collection timeout、部分失败降级和空结果汇总，单个 collection 失败不得吞掉其他 collection 的可用结果；跨 collection merge 默认策略为：若所有 collection 使用同一个 reranker 且分数可比较，则按 rerank score 全局排序；若 rerank 不可用、部分 collection rerank fallback 或分数不可比，则使用 `routing_score + reciprocal_rank` 融合；最终结果必须统一执行 `top_k` 稳定截断；每条结果 metadata 必须保留 `collection`、`collection_rank`、`routing_score`、`merge_score` 和 `merge_reason`，其中 `merge_reason` 需要说明本条结果来自 rerank score 还是 routing/RRF fallback 融合；Query Trace 基础信息必须记录 `collection`、`collections`、`primary_collection` 和 `multi_collection=true/false`，阶段详情必须新增 `parallel_retrieval`，记录 `collection_runs`、`merged_candidate_count`、`partial_failure_count`、`selected_collections`、`dropped_collections`、`child_trace_ids` 或 per-collection stage summary；单元测试必须覆盖单 collection 兼容、多 collection 并行成功、部分失败、全部空结果、重复 collection 去重、最大并发限制和 trace sink 失败隔离。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py services\ai-service\rag\tests\unit\test_trace_context.py -v`

##### D4：实现 Dense Route 向量检索

目标：输入用户 query 或 `ProcessedQuery`，完成 Query Embedding、pgvector 向量检索，并返回统一的 `RetrievalResult(chunk_id,text,score,metadata)`。

修改文件：`src/core/query_engine/__init__.py`、`src/core/query_engine/dense_route.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `DenseTraceContext.record_stage()`：定义 Dense Route 使用的最小 Trace 注入接口
- `DenseRoute.search()`：处理 raw query 或 ProcessedQuery，执行 Query Embedding 和 VectorStore 语义召回

验收标准：raw query 必须先通过 QueryProcessor，ProcessedQuery 可直接复用；调用 `EmbeddingClient.embed(processed_query.normalized_query)`；默认使用 `retrieval.dense_top_k` 作为 Dense 粗召回数量，并允许调用方显式覆盖；调用 VectorStore 完成 Top-k 向量检索，但不在 D4 提前执行 D8 的 metadata 过滤；所有候选统一校验为 `RetrievalResult(chunk_id,text,score,metadata)`；空 query、非法 top_k、embedding 失败、vector search 失败和空结果都有可测试分支；可选 Trace 记录 `route=dense`、provider-independent `method=vector_search`、top_k、候选数量、状态和耗时；Trace sink 异常不得覆盖检索结果或原始 RetrievalError。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D5：实现 Sparse Route BM25 回表检索

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

##### D6：实现 RRF Fusion

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

##### D7：实现 HybridSearch 编排

目标：编排 Dense Route、Sparse Route 和 RRF Fusion，完成候选去重、双路召回融合和单路失败降级。

修改文件：`src/core/query_engine/__init__.py`、`src/core/query_engine/hybrid_engine.py`、`tests/unit/test_retrieval.py`

实现类/函数：

- `HybridTraceContext.record_stage()`：定义 HybridSearch 使用的最小 Trace 注入接口
- `HybridSearchResult`：定义流程返回结果
- `HybridSearch.search()`：编排双路召回、候选去重和 RRF 融合
- `HybridSearch._record_trace()`：记录 hybrid 阶段候选数量、失败路线和降级原因

验收标准：前置依赖为 D1、D2、D4、D5、D6；输入 `ProcessedQuery` 和 Intent Router 输出；分别执行 Dense/BM25 两路检索；调用 RRF Fusion 生成融合排序，按 `chunk_id` 去重并保留 `dense_rank`、`sparse_rank`、`dense_score`、`sparse_score`；使用 `retrieval.fusion_top_k` 和 `retrieval.rrf_k`；返回结果必须同时保留 dense 原始候选、sparse 原始候选、融合候选、fallback 状态和 fallback 原因；单路失败时允许降级为另一条路线并写入 trace details；双路均失败时抛出 `RetrievalError`；Trace sink 异常不得覆盖检索结果或原始 RetrievalError。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D8：实现 Rerank 前候选过滤与跳过决策

目标：在 RRF Fusion 之后、Reranker 之前，根据调用参数过滤候选，并在过滤后的候选已经满足高置信条件时跳过昂贵 rerank，降低在线查询延迟。

修改文件：`config/settings.example.yaml`、`src/core/config.py`、`src/core/query_engine/__init__.py`、`src/core/query_engine/hybrid_engine.py`、`src/core/query_engine/reranker.py`、`tests/unit/test_retrieval.py`、`tests/unit/test_reranker.py`

实现类/函数：

- `CandidateFilterReport`：记录过滤后结果、过滤前后数量、过滤原因统计和被过滤 chunk_id
- `CandidateFilter.apply()`：按参数过滤候选结果
- `CandidateFilter._first_rejection_reason()`：返回候选被过滤的首个原因
- `HybridSearch.apply_metadata_filter()`：在进入 rerank 前执行 metadata 过滤
- `HybridSearch._record_filter_trace()`：记录 filter 阶段过滤参数、数量变化和过滤原因
- `RerankSkipGate.evaluate()`：基于过滤后的 fusion 候选执行整批高置信判断
- `RerankSkipDecision`：返回是否跳过 rerank、原因、置信特征和最终候选来源
- `RerankController.rerank_with_outcome()`：在 skip gate 通过时直接返回过滤后的 RRF Top-k，并显式记录 skipped 状态
- `_matches_filter()`：执行 metadata 精确匹配或多值匹配
- `_has_permission()` / `_has_all_permissions()`：执行权限过滤

验收标准：支持 `collection`、`doc_type`、`source_type`、`document_status`、`lifecycle_status`、`permission`、`permissions`、`include_deleted` 参数；默认排除 `lifecycle_status=deleted` 的候选，除非显式设置布尔值 `include_deleted=true`；`include_deleted` 必须是 boolean，不能用字符串隐式转换；过滤发生在 RRF Fusion 之后、Rerank 之前；`HybridSearch.search(filters=...)` 和 `HybridSearch.apply_metadata_filter()` 复用同一过滤逻辑，供后续 `--collection` 等脚本参数调用；过滤后保持原有候选顺序；过滤结果数量和过滤原因写入 trace details；未知过滤键必须抛出 `RetrievalError`，避免静默忽略调用方输入；rerank skip gate 必须在过滤后按整批候选决策，不允许混合使用“部分 fusion 结果 + 部分 rerank 结果”的 a+b 排序；高置信通过时直接返回过滤后的 RRF `final_top_k`，并在 rerank trace 中记录 `skipped=true`、`skip_reason`、`confidence_features`、`before_candidates` 和 `after_candidates`；未通过时过滤后的 `fusion_top_k` 整批进入 Reranker；gate 判断不得直接比较 Dense/BM25 原始分数，RRF margin 只能作为相对特征，并应结合 dense/sparse 双路命中、rank 稳定性、document/section 一致性和候选数量；skip gate 阈值必须来自 settings，不允许硬编码；Trace sink 异常不得覆盖过滤结果、skip 决策或原始 RetrievalError。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### D9：实现 Cross-Encoder Reranker

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

##### D10：实现 LLM Rerank

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

##### D11：实现 rerank fallback

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

##### D12：实现 Self-RAG Controller

目标：在 rerank 之后、引用和响应构造之前判断证据是否相关且足够，避免低质量候选进入最终上下文。

修改文件：`config/settings.example.yaml`、`config/prompts/self_rag_judge_prompt.yaml`、`src/core/config.py`、`src/core/query_engine/__init__.py`、`src/core/query_engine/self_rag_controller.py`、`src/scripts/query.py`、`tests/unit/test_retrieval.py`、`tests/unit/test_config.py`、`tests/integration/test_query_pipeline.py`

实现类/函数：

- `SelfRagSettings`：读取 Self-RAG 阈值、judge LLM provider、Prompt 路径和 fallback action
- `SelfRagDecision`：封装 `decision`、`score_band`、`selected_results`、`fallback_action`、`judge_result` 和 `reason`
- `SelfRagJudgeResult`：封装单次 LLM judge 返回的 `relevance_score`、`evidence_sufficiency_score`、`relevant`、`sufficient`、`missing_evidence` 和 `reason`
- `SelfRagController.evaluate()`：接收 query、rerank 后候选和可选 trace，执行分档、裁剪、judge 和 empty fallback
- `SelfRagController._classify_score_band()`：根据 TopN rerank score 判断 high/medium/low 置信分档
- `SelfRagController._trim_low_score_candidates()`：在调用 judge 前剔除低于 `judge_min_candidate_score` 的 chunk，减少上下文拥挤和 judge token 消耗
- `SelfRagController._judge_relevance_and_sufficiency()`：用一个 LLM 调用同时判断 relevance 与 evidence sufficiency，要求返回严格 JSON object
- `SelfRagController._empty_decision()`：在低置信或 judge 不通过时返回 empty result 决策，不调用 Web/Tavily
- `self_rag_judge_prompt.yaml`：英文 Prompt，要求同时评价候选与 query 的相关性、证据是否足够回答，并列出缺失证据；禁止生成最终答案
- `SelfRagTraceContext.record_stage()`：记录 `self_rag` stage 的分档、裁剪数量、judge 结果、selected chunk IDs、empty fallback reason 和耗时

验收标准：Self-RAG Controller 必须位于 `RerankController` 之后、`KnowledgeHubResponseBuilder` 之前；TopN 数量和阈值必须来自 settings，不允许硬编码；当 Top2/Top3 分数均达到高置信阈值时直接通过，不调用 LLM judge；当 Top1 达到中置信阈值但 TopN 不满足高置信时，必须先按 `judge_min_candidate_score` 剔除极低分 chunk，再通过一次 LLM 调用同时返回 relevance 与 evidence sufficiency，不允许为两个 judge 分别调用 LLM；低置信、裁剪后无候选、judge 返回非法 JSON、LLM 失败、relevance 未通过或 evidence sufficiency 未通过时，fallback action 暂时只允许 `empty`，不得在 RAG 内部直接调用 Web/Tavily；empty fallback 必须保留 trace reason，并让 Response Builder 输出 `is_empty=true`、空 content/citations/images；Self-RAG 不得修改输入 `RetrievalResult`；trace 必须记录 score_band、top_scores、trimmed_count、judge_called、judge_result、selected_chunk_ids、fallback_action 和 reason；单元测试必须覆盖 high direct pass、medium judged pass、medium judged empty、low empty、judge JSON 非法和 trace sink 失败隔离。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_retrieval.py services\ai-service\rag\tests\unit\test_config.py -v`
##### D13：实现引用构造

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

##### D14：实现多模态响应组装

目标：把最终排序 chunk 转换为可直接交给 MCP、AImodel、CLI 和 Dashboard 的
公开知识响应；响应包含 Agent-ready final context、D13 引用、命中图片和 trace_id，但不
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
Provider 失败或返回空内容时按配置 fallback 到原始编号证据块；citations 复用 D13 的 grounded citation；
图片引用从 `metadata.image_refs` 读取，必须是非空字符串列表，跨 chunk 去重并
保持首次引用顺序，同一图片记录所有关联 chunk IDs；图片索引采用一次批量查询，
解析结果不依赖数据库返回顺序；缺失图片索引安全跳过且不影响文本响应；公开图片
只包含 image_id、managed file_path、mime_type、page、尺寸、caption、quality_status
和 chunk_ids，不泄漏 image hash、原始 extraction path、Provider payload 或任意扩展
metadata；空候选返回 `ok=true`、`is_empty=true`、空 content/citations/images，且不
访问图片存储；构造过程不修改 RetrievalResult。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_response_builder.py -v`；使用 Docker PostgreSQL 设置 `DATABASE_URL` 后执行 `uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\integration\test_repositories.py::test_image_storage_saves_files_and_queries_upserted_indexes -v`

##### D15：实现 query.py 脚本入口

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

##### D16：建立 Retrieval 单元测试矩阵

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

##### D17：实现 Retrieval 集成测试

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
- `QueryKnowledgeHubTool._validate_request()`：在打开数据库前校验 query、collection/collections、top_k、no_rerank 和 include_image_base64
- `QueryKnowledgeHubTool._normalize_collections()`：兼容单 `collection` 和多 `collections`，去重、保序并限制最大 collection 数
- `QueryKnowledgeHubTool._attach_image_base64()`：仅在显式请求时为受管图片附加受限大小的 base64 内容
- `_business_error()`：构建 `ok=false` 的可恢复业务错误 envelope
- `_default_runtime_builder()`：复用阶段 D QueryRuntime 组合路径
- `create_mcp_server(query_knowledge_hub=...)`：把 E2 的真实 query tool 注册到 FastMCP，E3 工具继续保持 placeholder

验收标准：返回 content、citations、trace_id 和 query_trace_ids；默认返回图片 metadata 和受管 file_path，不默认返回 base64；可预留 `include_image_base64=false` 参数，仅在显式请求时附加受限大小的 `base64_content`；`collection` 保持单 collection 兼容，`collections` 支持多 collection 查询，两者同时传入时以 `collections` 为准并把首个有效 collection 作为 primary collection；MCP 层只做参数校验和公共响应投影，不实现并行检索业务逻辑，必须调用阶段 D3 的并行检索编排能力；多 collection 响应必须包含 `collection_results`，记录每个 collection 的 trace_id、候选数量、状态和失败原因；业务可恢复错误返回 `{"ok": false, "error": {"code": "...", "message": "..."}}`，不直接把内部异常或 tool result 暴露给 Agent。

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
- `TraceContext.record_query_stage()`：仅允许记录 `query_processing`、`intent_routing`、`dense`、`sparse`、`fusion`、`filter`、`rerank`、`response` 查询阶段
- `TraceContext.finish_query()`：写入顶层 `query_result`、query 汇总指标和评估指标，并生成完整结构化快照
- `_validate_query_result()`：校验 `contexts/content/citations/images` 轻量查询结果快照，严格限制 citation/image 字段，避免 trace 重复保存完整公共响应或泄漏内部 provider payload
- `_validate_optional_finite_float()`：校验 `top_score` 和 context score 为有限数值或空值
- `_validate_candidate_count_by_stage()`：校验 Dense、Sparse、Fusion、Filter、Rerank 阶段候选数量
- `_validate_bool()`：校验 fallback、empty_result 等布尔指标，避免字符串 truthy 值污染结构化日志

验收标准：包含 query 基础信息、阶段详情、顶层 `query_result`、汇总指标、评估指标；基础信息必须包含 `trace_id`、`trace_type=query`、`started_at`、`collection`、`raw_query`，并在存在时记录 `request_source`；阶段详情必须限制在 `query_processing/intent_routing/dense/sparse/fusion/filter/rerank/response`；Dense/Sparse 阶段 details 必须记录命中的 `chunk_ids`，Fusion/Filter/Rerank 阶段 details 必须记录轻量候选快照和排序/过滤变化；`query_result` 必须包含 `contexts/content/citations/images`，contexts 每项包含 `chunk_id/score/rank`，citations 不包含 `source_uri`，images 仅包含 `image_id/chunk_ids/quality_status`；汇总指标仅保存 `top_score`、`candidate_count_by_stage`、`fallback_used`、`error`、`total_duration_ms`；评估指标支持 `query_document_relevance`、`citation_hit_rate`、`rerank_delta`、`empty_result`。

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

验收标准：ingestion 链路记录 dedup、load、split、transform、embed、upsert；图片 caption 不得重复记录为顶层 stage，只能记录在 `transform.sub_stages.image_captioner`；顶层 `transform.duration_ms` 保留整个 Transform Pipeline 总耗时，`transform.sub_stages` 按实际执行顺序记录每个启用实现的名称、具体类、耗时、输入输出 chunk 数、`changed_count`、`unchanged_count` 和状态，使 Dashboard 能区分“执行但未改变”与“未执行”；`image_captioner` 子阶段必须记录图片 caption 的 provider/model、输入图片数、成功 caption 数和失败/降级原因；某个实现失败时必须先记录该失败子阶段，再让主链路按原错误语义失败；Transform snapshots 必须由配置控制，默认只记录变化 chunk、每步最多 20 个、每段预览最多 800 字，不额外调用 LLM 或数据库；query 链路记录 query_processing、intent_routing、dense、sparse、fusion、filter、rerank、response，并在结束时保存顶层 `query_result`；正常、失败、跳过和降级结束都会 flush 同一种 trace snapshot；启用 PostgreSQL 持久化时，真实 ingestion/query 链路的最终 snapshot 同时进入 JSONL 与对应 trace 表，Dashboard 可直接读取；不得仅在去重跳过等特殊分支单独写入数据库。

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

- `tests/fixtures/golden_set.json`：保存 JSON 数组格式的黄金测试集，每条样本包含问题、标准答案、collection、期望命中文档 ID 和难度
- `test_golden_set_fixture_exists_and_contains_representative_cases()`：验证 fixture 存在并覆盖购物指南核心品类
- `test_golden_set_samples_follow_required_schema()`：验证样本 ID 唯一、字段非空、collection、expected_doc_ids 和 difficulty 完整

验收标准：问题、答案、来源文档字段完整；样本 ID 唯一；每条样本声明 collection、expected_doc_ids 和 difficulty；样例覆盖购物指南、FAQ、政策和客服话术。

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

验收标准：指标计算无需真实 LLM；支持 `retrieved_sources` 为字符串列表或候选 mapping 列表；输入数量不一致、`top_k` 非法或黄金样本缺少 `expected_doc_ids` 时应 fail fast。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_evaluation.py -v`

##### G3：接入 Ragas 指标

目标：封装 Ragas 生成质量指标，并支持从统一配置读取本次启用的 generation metrics 和 Ragas runtime 参数。

修改文件：`config/settings.example.yaml`、`config/settings.yaml`、`src/core/config.py`、`src/observability/evaluation/__init__.py`、`src/observability/evaluation/ragas_adapter.py`、`src/libs/evaluator/ragas_evaluator.py`、`src/scripts/run_evaluation.py`、`tests/unit/test_config.py`、`tests/unit/test_evaluation.py`

实现类/函数：

- `EvaluationGenerationMetricsSettings`：描述 `settings.evaluation.metrics.generation` 下每个 Ragas 指标开关
- `enabled_generation_metrics()`：从配置解析启用的 Ragas generation metrics，全部关闭时 fail fast
- `EvaluationRagasSettings`：描述 `settings.evaluation.ragas` 下的 Ragas runtime 参数，例如 `timeout_seconds` 和 `max_workers`。
- `RagasEvaluator`：封装 Ragas 生成质量评估执行逻辑
- `RagasEvaluator.evaluate()`：将黄金集和生成结果转换为 Ragas-compatible rows，并返回归一化数值指标
- `_load_ragas_backend()`：懒加载可选 Ragas 依赖，避免普通开发环境强制安装 evaluation extra
- `_to_ragas_v02_row()`：在真实 Ragas backend 边界将项目行字段映射为 Ragas 0.2 单轮评估列
- `_normalize_ragas_result()`：将 Ragas mapping、scores 或 dataframe 结果归一化为 `metric_name -> float`

验收标准：`settings.example.yaml` 中 generation metrics 包含 `faithfulness`、`answer_relevancy`、`context_precision`、`context_recall` 和 `answer_correctness`；`faithfulness`、`answer_relevancy`、`context_precision`、`context_recall` 默认启用，`answer_correctness` 默认关闭；`settings.example.yaml` 和本地 `settings.yaml` 的 `evaluation.ragas` 必须声明 `timeout_seconds` 与 `max_workers`，真实 Ragas backend 必须使用该配置构造 RunConfig，不得在 adapter 中硬编码 runtime 参数；`enabled_generation_metrics()` 必须位于 `src/core/config.py` 并返回配置启用的指标列表；全部关闭时必须 fail fast；`run_evaluation.py` 必须把解析后的 `metric_names` 传入 `EvaluationService.run_evaluation(... evaluator_options=...)`，并写入 `settings_snapshot`；Ragas 测试使用 marker 隔离；普通单元测试不得导入真实 Ragas 依赖；空 `metric_names` 必须 fail fast；真实 Ragas 未安装时应返回可读 ProviderError。

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

##### G6：实现评估脚本进度日志与控制台反馈

目标：让真实评估入口具备可观察运行反馈，长时间评估时可以明确看到配置加载、数据库连接、逐样本 AImodel/RAG 预测、Ragas 指标计算和结果持久化进度；失败时能直接定位到样本、阶段和耗时。

修改文件：`src/scripts/run_evaluation.py`、`src/observability/evaluation/ragas_adapter.py`、`src/libs/evaluator/ragas_evaluator.py`、`tests/unit/test_evaluation.py`

实现类/函数：

- `EvaluationReporter`：封装评估脚本的控制台进度输出、单行刷新 UI、阶段耗时格式化和带时间戳的 JSONL 诊断日志写入
- `EvaluationReporter.run_started()`：输出 dataset、sample_count、answer_source、top_k、rerank、evaluator 和 metrics
- `EvaluationReporter.step_started()`：记录配置加载、数据库连接、schema 初始化、vector store 构建、Ragas 执行和结果持久化等阶段开始事件，并启动 running heartbeat
- `EvaluationReporter.render_status()`：使用单行刷新输出当前运行状态，控制台时间前缀使用 `[7s]`、`[1m12s]`、`[1h01m01s]` 格式展示当前阶段耗时
- `EvaluationReporter.step_done()`：记录阶段耗时和成功状态，控制台完成行不额外输出 duration，并停止当前阶段 heartbeat
- `EvaluationReporter.sample_started()`：输出当前样本序号、样本 ID、collection 和问题字符数，避免控制台编码不一致导致中文乱码；中文问题预览仅写入 JSONL
- `EvaluationReporter.sample_step_done()`：记录并在控制台展开 AImodel chat、message resolve、query trace load、chunk lookup 和 prediction ready 等 build_predictions 样本内部步骤
- `EvaluationReporter.ragas_observer()`：接收 Ragas LLM/Embedding started、done、failed 事件，写入 JSONL，并在控制台展开 call_id、method、provider、model、prompt/text/output 字符数、vector_count、dimension、duration 和错误类型
- `EvaluationReporter._start_heartbeat()`：在 CLI 刷新模式下以 10 秒间隔启动后台计时刷新，让长时间阻塞调用也能持续显示耗时且避免输出过密
- `EvaluationReporter._stop_heartbeat()`：阶段完成或失败时停止后台刷新，避免残留线程继续写控制台
- `EvaluationReporter.failed()`：输出失败阶段、样本 ID、错误类型、错误消息、耗时和排查提示
- `EvaluationReporter.completed()`：输出最终 run_id、指标摘要、样本数量和总耗时
- `RagasEvaluatorClient.__init__()`：接收可选 Ragas model call observer，并传递给 Ragas adapter
- `RagasEvaluator`：接收可选 model call observer，保持 fake backend 和真实 backend 均可测试
- `ProjectRagasLLM.generate_text()`：在真实 Ragas 调用项目 LLM 时发出 started/done/failed 观测事件
- `ProjectRagasEmbeddings.embed_query()`：在真实 Ragas 调用单条 Embedding 时发出 started/done/failed 观测事件
- `ProjectRagasEmbeddings.embed_documents()`：在真实 Ragas 调用批量 Embedding 时发出 started/done/failed 观测事件

验收标准：评估脚本运行时必须在控制台展示阶段级进度和样本级进度；控制台进度 UI 默认使用单行刷新，heartbeat 默认 10 秒刷新一次，时间前缀必须使用 `[7s]`、`[1m12s]`、`[1h01m01s]` 这类当前阶段耗时格式；阶段完成行不得额外拼接 `duration=...`；当输出目标不支持刷新或测试注入 list writer 时必须降级为普通追加行；默认最终评估结果 JSON 仍保持可被脚本和 Dashboard 读取的稳定输出，不得被进度 UI 破坏；长时间步骤必须展示开始事件和可持续刷新的 running 状态，避免用户误判为无响应；Ragas 调用评估 LLM 和 Embedding 时必须写入 JSONL 事件并输出可读控制台状态，能看出调用类型、方法、耗时、provider、model、输入输出长度、向量数量和失败原因；Ragas 模型调用日志不得记录完整 prompt、完整 response、完整 retrieved contexts、embedding 原文、向量值、API key、base64 或其他敏感大字段；失败时必须包含 sample_id、collection、step、elapsed_ms、error_type 和 error_message；JSONL 日志写入 `src/logs/evaluation.log.jsonl`，每条事件必须包含 Asia/Shanghai ISO-8601 `timestamp`、event、run_id、dataset_name、sample_index/sample_count（若适用）、collection（若适用）、step、status 和 duration_ms（完成或失败事件）；单元测试必须使用 fake output/logger/fake Ragas backend，不得真实调用 AImodel、Ragas、Embedding 或 PostgreSQL。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_evaluation.py -v`；`uv run --project services/ai-service/rag ruff check services/ai-service/rag/src services/ai-service/rag/tests`

##### G7：实现真实 Ragas 评估入口与最终上下文优化

目标：把已有 Ragas adapter 从单元适配能力提升为可运行的真实评估入口，并把
`query_result.content` 从裸 chunk 拼接升级为 AImodel 可直接使用的最终上下文；同时支持在评估时主动调用 AImodel 生成 assistant message，并读取该 message 作为 Ragas answer。

修改文件：`config/settings.example.yaml`、`config/prompts/evidence_context_prompt.yaml`、`src/core/config.py`、`src/core/response/evidence_context_optimizer.py`、`src/core/response/response_builder.py`、`src/libs/evaluator/evaluator_factory.py`、`src/libs/evaluator/ragas_evaluator.py`、`src/libs/evaluator/__init__.py`、`src/scripts/query.py`、`src/scripts/run_evaluation.py`、`tests/unit/test_config.py`、`tests/unit/test_response_builder.py`、`tests/unit/test_evaluation.py`、`services/ai-service/app/routers/AImodel/schemas.py`、`services/ai-service/app/routers/AImodel/service.py`、`services/ai-service/tests/test_aimodel_rag_tool.py`

实现类/函数：

- `EvidenceContextOptimizer.optimize()`：将编号证据块整理为 Agent-ready final context，不生成最终答案
- `KnowledgeHubResponseBuilder.build()`：生成原始编号证据块，并在启用 optimizer 时返回优化后的最终上下文
- `RagasEvaluatorClient.evaluate()`：作为 `BaseEvaluator` 实现委托 `src.observability.evaluation.RagasEvaluator`
- `RagasEvaluatorClient.__init__()`：读取 `evaluation.llm_provider` 与 `evaluation.embedding_provider`，将项目 LLM/Embedding 客户端注入 Ragas
- `_load_ragas_backend()`：将项目 `BaseLLM`/`BaseEmbedding` 包装为 Ragas 可用的 LLM 与 Embeddings，避免真实评估依赖 Ragas 默认模型
- `EvaluatorFactory.register_builtin_providers()`：注册 `fake` 和 `ragas`
- `run_evaluation_cli()`：加载 golden set、执行 Query Pipeline、构造 predictions、调用 evaluator 并持久化聚合指标和样本级诊断结果
- `_prediction_from_query_result()`：将 `query_result.content` 映射为 Ragas answer，并按 contexts 回查 chunk text 构造 `retrieved_contexts`
- `EvaluationAnswerSource`：声明 `rag` 与 `message` 两种 answer source，避免命令行参数和内部分支使用裸字符串
- `AImodelEvaluationClient.chat()`：在评估模式下按真实 AImodel 用户问题路径调用 chat 接口，发送 golden question 和评估元数据，并消费 SSE/响应流直到 assistant message 落库
- `MessageAnswerRepository.get_answer_from_chat_result()`：根据 AImodel chat 的 `conversation_id` 与最终 answer 定位最新 assistant message，并读取该 message 关联的全部 query trace id
- `QueryTraceResultRepository.get_query_result()`：按 AImodel message 关联的 query trace id 读取真实 `query_result`
- `_collection_for_sample()`：仅在 `--answer-source rag` 的组件评估路径中选择目标 collection；默认 `message` 模式不使用 golden collection 强制 AImodel 路由
- `_prediction_from_message_answer()`：以 AImodel assistant message 作为 Ragas answer，并使用关联 trace 的 `query_result.contexts` 对应 chunk text 构造 `retrieved_contexts`

验收标准：`settings.example.yaml` 包含 `response.evidence_context_optimizer` 配置以及 `evaluation.llm_provider/evaluation.embedding_provider` 配置；Prompt 使用英文指令；启用优化时 `query_result.content` 是 Agent-ready final context，并保留 `[1]` 等证据编号；LLM 优化失败、未配置或返回空内容时按配置 fallback 到原始编号证据块；`query_result.contexts` 继续保存 `chunk_id/score/rank` 用于溯源和评估；`EvaluatorFactory.create(provider="ragas")` 可创建真实 Ragas evaluator 且仍保持懒加载；真实 Ragas 评估必须使用项目配置的 LLM/Embedding provider，不依赖 Ragas 默认模型；`run_evaluation.py` 支持 `--collection`、`--golden-set`、`--evaluator`、`--top-k`、`--no-rerank` 和 `--answer-source message|rag`；`--answer-source` 默认值必须为 `message`；`--collection` 缺省时，`--answer-source rag` 组件评估按每条 golden sample 的 `collection` 字段选择目标知识库，允许一次评估跨 `shopping_guides/faq/policies/manual` 等多个 collection；`--collection` 显式传入时继续覆盖所有样本以便做单 collection 调试；默认 message 模式必须为每个 golden question 按真实 AImodel 用户问题路径调用 chat，由 Agent 按生产工具选择策略决定是否调用 RAG；golden collection 仅作为期望路由标签和诊断字段；先根据 chat 结果定位落库 assistant message，再通过 `message_query_trace` 读取该 message 关联的真实 RAG query trace；显式 `--answer-source rag` 才使用 `query_result.content` 作为上下文包调试 answer；默认 message 模式下未找到 message、AImodel 调用失败或 trace 关联缺失时必须 fail fast，不能静默 fallback 到 `query_result.content`；evaluation run 的 `settings_snapshot` 和 result details 必须记录 `answer_source`、`sample_collection`、`effective_collection`、`query_trace_id`、`query_trace_ids`、`message_id` 和 `conversation_id`；评估结果必须写入 `rag_evaluation_runs`、`rag_evaluation_results` 和 `rag_evaluation_sample_results`；样本级结果必须保存每条 golden question 的 `question`、`golden_answer`、`generated_answer`、`retrieved_contexts`、`context_chunk_ids`、`query_trace_ids`、`metrics` 和 `error`，便于定位 faithfulness/answer_relevancy 低分原因；单元测试不得真实调用外部 LLM、Embedding、Ragas backend 或 AImodel HTTP 服务。

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

#### 阶段 I：Async Query Runtime 实施明细

##### I1：定义 async provider 契约与兼容适配层

目标：为在线 query、MCP 和 evaluation 链路建立 async 调用边界，同时保留现有同步 provider 可用性。

修改文件：

- `config/settings.example.yaml`
- `config/settings.yaml`
- `src/core/config.py`
- `src/core/query_engine/async_adapters.py`
- `src/libs/llm/base_llm.py`
- `src/libs/embedding/base_embedding.py`
- `src/libs/vector_store/base_vector_store.py`
- `src/libs/reranker/base_reranker.py`
- `tests/unit/test_config.py`
- `tests/unit/test_async_query_runtime.py`

实现类/函数：

- `AsyncProviderSettings`：读取 async 开关、并发数和 timeout。
- `BaseLLM.async_chat()`：LLM async 最小接口，默认通过兼容适配层调用同步 `chat()`。
- `BaseEmbedding.async_embed()`：query embedding async 最小接口。
- `BaseEmbedding.async_embed_batch()`：evaluation embedding async 批量接口。
- `BaseVectorStore.async_search()`：在线向量检索 async 最小接口。
- `BaseReranker.async_rerank()`：在线 rerank async 最小接口。
- `SyncToAsyncLLMAdapter`：用 `asyncio.to_thread()` 包装同步 LLM。
- `SyncToAsyncEmbeddingAdapter`：用 `asyncio.to_thread()` 包装同步 embedding。
- `SyncToAsyncVectorStoreAdapter`：用 `asyncio.to_thread()` 包装同步 vector store。
- `SyncToAsyncRerankerAdapter`：用 `asyncio.to_thread()` 包装同步 reranker。

验收标准：async 接口必须保持与同步接口一致的输入输出契约；旧 provider 不实现原生 async 时仍可通过 adapter 使用；adapter 必须支持 timeout、cancel 后错误转换和 trace-safe 错误消息；配置新增项必须同步 `settings.example.yaml` 与本地 `settings.yaml` 结构，不提交本地 secret；单元测试不得真实调用外部 API。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_config.py services\ai-service\rag\tests\unit\test_async_query_runtime.py -v`

##### I2：实现 provider 原生 async 化

目标：把在线查询使用的外部 I/O provider 改为原生 async，减少线程池依赖并提升并发查询吞吐。

修改文件：

- `src/libs/llm/openai_compatible_llm.py`
- `src/libs/llm/deepseek_client.py`
- `src/libs/llm/ccswitch_client.py`
- `src/libs/embedding/openai_embedding.py`
- `src/libs/vector_store/pgvector_store.py`
- `src/storage/bm25_storage.py`
- `src/ingestion/embedding/bm25_indexer.py`
- `src/libs/reranker/llm_reranker.py`
- `src/libs/reranker/cross_encoder_reranker.py`
- `tests/unit/test_model_providers.py`
- `tests/unit/test_retrieval.py`

实现类/函数：

- `OpenAICompatibleLLM.async_chat()`：使用 async HTTP client 调用 OpenAI-compatible chat endpoint。
- `OpenAIEmbedding.async_embed()`：使用 async HTTP client 调用 embedding endpoint。
- `OpenAIEmbedding.async_embed_batch()`：保持批量输入输出顺序。
- `PgVectorStore.async_search()`：执行 async pgvector 查询并返回 `RetrievalResult`。
- `BM25Storage.async_query()`：执行 async BM25 posting 查询。
- `BM25Indexer.async_query()`：为在线 sparse route 暴露 async 查询入口。
- `LLMReranker.async_rerank()`：使用 async LLM 调用执行 rerank。
- `CrossEncoderReranker.async_rerank()`：使用受限 executor 或专用推理队列执行本地模型推理。

验收标准：OpenAI-compatible、DeepSeek、CCSwitch 和 DashScope 相关在线 provider 应优先使用原生 async；PostgreSQL/pgvector/BM25 在线查询必须提供 async 方法；Cross-Encoder 不得阻塞事件循环；provider async 方法与同步方法在 fake input 下返回等价结构；外部 provider 真实调用继续使用 external marker 隔离。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_model_providers.py services\ai-service\rag\tests\unit\test_retrieval.py -v`

##### I3：实现 AsyncQueryRuntime

目标：新增在线查询 async runtime，作为 MCP、query.py 和 evaluation 的 async 主入口，同时保留同步 `QueryRuntime` 兼容路径。

修改文件：

- `src/core/query_engine/async_runtime.py`
- `src/core/query_engine/__init__.py`
- `src/scripts/query.py`
- `src/core/trace/trace_controller.py`
- `tests/unit/test_async_query_runtime.py`
- `tests/integration/test_query_pipeline.py`

实现类/函数：

- `AsyncQueryRuntime.execute()`：async 查询主入口，返回与同步 runtime 等价的公开响应。
- `AsyncQueryRuntime._process_query()`：执行 query normalize、rewrite 和 intent routing。
- `AsyncQueryRuntime._search_single_collection()`：执行单 collection hybrid retrieval、filter 和 rerank。
- `AsyncQueryRuntime._apply_self_rag()`：对最终候选执行一次证据决策。
- `AsyncQueryRuntime._build_response()`：对最终候选执行一次 Response Builder。
- `build_async_query_runtime()`：按 settings 构建 async runtime 依赖。
- `run_query_cli()`：支持配置驱动选择 async runtime，输出契约保持兼容。

验收标准：`AsyncQueryRuntime` 必须覆盖 query processing、intent routing、hybrid retrieval、rerank、Self-RAG 和 Response Builder；单 collection 结果与同步 runtime 在 fake provider 下等价；同步 `QueryRuntime` 不删除，旧测试继续通过；async runtime 的 trace stage 名称与现有 query trace 兼容；CLI 输出 JSON 和 verbose 字段保持稳定。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_async_query_runtime.py services\ai-service\rag\tests\integration\test_query_pipeline.py -v`

##### I4：实现 multi-collection 真并发与 merge 后统一后处理

目标：将多 collection 从顺序编排升级为真正并发执行，并把 Self-RAG judge 和 Response Builder 移到跨 collection merge 后只执行一次。

修改文件：

- `src/core/query_engine/parallel_retrieval.py`
- `src/core/query_engine/async_runtime.py`
- `src/core/query_engine/trace_snapshots.py`
- `src/core/trace/trace_context.py`
- `tests/unit/test_async_query_runtime.py`
- `tests/unit/test_retrieval.py`
- `tests/unit/test_trace_context.py`

实现类/函数：

- `AsyncParallelRetrievalController.search()`：使用 `asyncio.gather()` 并发执行 collection retrieval/rerank 子任务。
- `AsyncParallelRetrievalController._run_collection()`：为单个 collection 执行 retrieval/rerank，不执行最终 Self-RAG 或 Response。
- `AsyncParallelRetrievalController._merge_collection_results()`：跨 collection 合并结果并统一 top_k 截断。
- `AsyncParallelRetrievalController._record_trace()`：记录 collection runs、timeout、partial failure、merge snapshot 和 child trace id。
- `AsyncQueryRuntime._finalize_merged_results()`：对 merge 后候选执行一次 Self-RAG 和一次 Response Builder。

验收标准：多 collection 查询必须用 `asyncio.gather()` 或等价任务编排并发执行 collection 子任务；每个 collection 只执行 retrieval/rerank 子链路；Self-RAG judge 和 Response Builder 对 merge 后最终候选只调用一次；支持 `retrieval.max_collection_concurrency`、`retrieval.per_collection_timeout_seconds`、partial failure、全部 empty 和全部失败；trace 中必须能看出 collection 级耗时和最终统一后处理耗时；单元测试必须验证两个中置信 collection 不会触发两次 Self-RAG LLM judge。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_async_query_runtime.py services\ai-service\rag\tests\unit\test_retrieval.py services\ai-service\rag\tests\unit\test_trace_context.py -v`

##### I5：接入 MCP 与 evaluation async 路径

目标：让外部 MCP 调用和评估脚本消费 async runtime，并为评估提供可控样本并发和指标并发。

修改文件：

- `src/mcp_server/tools.py`
- `src/mcp_server/server.py`
- `src/scripts/run_evaluation.py`
- `src/observability/evaluation/runner.py`
- `src/observability/evaluation/ragas_adapter.py`
- `tests/unit/test_mcp_tools.py`
- `tests/unit/test_evaluation.py`

实现类/函数：

- `QueryKnowledgeHubTool.query_knowledge_hub()`：await async runtime，并保留 MCP 公共输出契约。
- `_default_async_runtime_builder()`：为 MCP 创建 async runtime。
- `run_evaluation_async()`：按 golden samples 并发构造 predictions。
- `EvaluationAsyncLimiter`：限制 sample concurrency 和 metric concurrency。
- `RagasEvaluatorClient.async_evaluate_with_samples()`：在 Ragas 支持 async 时走 async 指标调用，否则受限降级到同步包装。

验收标准：MCP `query_knowledge_hub` 保持 schema 和错误 envelope 不变；async path 必须写入与同步 path 等价的 query trace 和 query_result；evaluation 支持 `evaluation.async_enabled`、`max_sample_concurrency` 和 `max_metric_concurrency`；评估失败样本必须记录 error，不得因为单样本失败中断已完成样本持久化；Ragas 调用日志继续保留 provider/model/耗时观测。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_mcp_tools.py services\ai-service\rag\tests\unit\test_evaluation.py -v`

##### I6：完成 async 查询验收与性能对比

目标：验证 async 链路正确性、可观测性和性能收益，形成可复现的 before/after 对比。

修改文件：

- `tests/unit/test_async_query_runtime.py`
- `tests/integration/test_query_pipeline.py`
- `tests/e2e/test_full_rag_flow.py`
- `data/resume/async_query_runtime.md`
- `src/scripts/run_evaluation.py`

实现类/函数：

- `test_async_query_runtime_matches_sync_contract()`：验证 async 与 sync 输出契约一致。
- `test_multi_collection_async_runs_collections_concurrently()`：验证 collection 并发而非顺序执行。
- `test_multi_collection_runs_one_self_rag_and_one_response()`：验证 merge 后统一后处理。
- `test_async_mcp_query_knowledge_hub_contract()`：验证 MCP async tool 输出安全字段。
- `async_query_performance_report()`：汇总 first10/last10 latency、trace 数量、judge 次数和指标变化。

验收标准：async 链路相关单元、集成和 MCP contract 测试通过；first10/last10 对比报告必须记录平均查询耗时、P95、RAG trace 数量、Self-RAG judge 次数、Response Builder 次数和 Ragas 指标变化；若 async 指标下降或超时增加，报告必须说明原因和下一步优化建议；`data/resume/async_query_runtime.md` 只记录可用于项目复盘的量化结果，不包含 secret、完整 prompt 或用户隐私数据。

测试方法：`uv run --project services/ai-service/rag pytest services\ai-service\rag\tests\unit\test_async_query_runtime.py services\ai-service\rag\tests\integration\test_query_pipeline.py services\ai-service\rag\tests\e2e\test_full_rag_flow.py -v`；`uv run --project services/ai-service/rag ruff check services/ai-service/rag/src services/ai-service/rag/tests`

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
- **只描述最终状态**：技术设计、模块职责、任务备注和验收标准只记录当前有效成果与约束，不保留“原先、后来、新增、改为、不再、重构为、修复了”等演进过程。需要说明兼容性时，直接描述当前支持的输入或行为。
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
- **连续开发**：用户输入 `next` 时，先按本规范提交当前通过验收的任务，再开始下一个待执行任务。

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

RAG 独立模块统一使用 **uv** 管理 Python 依赖、虚拟环境、锁文件和命令执行。

执行要求：

- `pyproject.toml` 是依赖声明来源，`uv.lock` 是完整解析结果，两者必须同时提交。
- 首次开发或依赖变化后执行 `uv sync --project services/ai-service/rag --extra dev`。
- 常规测试使用 `uv run --project services/ai-service/rag pytest ...`。
- 静态检查使用 `uv run --project services/ai-service/rag ruff check ...`。
- Python 脚本使用 `uv run --project services/ai-service/rag python ...`。
- CI 和 Docker 必须使用 `--frozen`，锁文件与声明不一致时直接失败，禁止隐式更新。
- Docker 只安装生产依赖，使用 `uv sync --frozen --no-dev`，不把宿主机 `.venv` 复制进镜像。
- auto-coder 的 Python、pytest、Ruff 和规格同步命令统一通过 uv 选择项目环境。
- 依赖升级必须显式执行 `uv lock --upgrade-package <package>` 或经审查的 `uv lock --upgrade`，不能在普通测试任务中隐式升级。
