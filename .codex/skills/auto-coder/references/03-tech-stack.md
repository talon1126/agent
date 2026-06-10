<!-- synced-from: services\ai-service\rag\DEV_SPEC.md -->
<!-- reference: 技术栈与依赖 -->

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
| MCP | Python 官方 MCP SDK + stdio transport | 暴露 RAG tools；开发与首版集成都使用 stdio，由 AImodel 后端长期拉起 RAG MCP 子进程 |
| Dashboard | Streamlit | 本地轻量 Dashboard |
| 测试 | pytest | 单元、集成、E2E、评估测试 |

### 3.2 RAG 流水线设计

RAG 流水线分为两条主链路：**数据摄取流水线** 和 **检索流水线**。

整体设计参考 LlamaIndex 的分层思想，但不直接依赖 LlamaIndex 框架。项目内部自定义轻量接口，例如 `BaseLoader`、`BaseSplitter`、`BaseTransform`、`BaseEmbedding`、`BaseVectorStore`，让每一层都可以独立替换、组合和测试。

#### 3.2.1 流水线框架

数据摄取流水线负责把外部文件变成可检索的向量和索引数据：

```text
Dedup -> Loader -> DocumentSummarizer -> Splitter -> Transform -> ImageCaptioner -> DenseEncoder/BM25Indexer -> BatchProcessor -> Upsert -> 文档生命周期管理
```

检索流水线负责把用户问题变成可引用的上下文结果：

```text
查询预处理 -> 双路混合检索 -> 候选过滤 -> 重排 -> 引用结果构造
```

流水线要支持 **可组合**：不同 Loader、Splitter、Transform、Embedding 和 VectorStore 可以通过配置组合成不同策略。例如首版使用 PDF/Markdown Loader + RecursiveCharacterTextSplitter + ImageCaptioner + DashScope Embedding + pgvector，后续可以替换某一层而不重写整条链路。

#### 3.2.2 数据摄取流水线

数据摄取的目标是先识别原始资料是否发生变化，再把 PDF、Markdown、文档说明等资料转换为统一的 `Document(id + text + summary + metadata)` 对象，随后逐步加工为 chunk、embedding 和可追踪的索引记录。

| 层级 | 职责 | 关键实现要素 |
| --- | --- | --- |
| `Dedup` | 在进入 Loader 前判断原始文档是否需要摄取 | 每个文档先计算 SHA256 哈希纹；若 `rag_documents` 中同一 collection、canonical source_path 和 source_hash 的文档状态为 `success`，则写入 skipped ingestion trace 并直接结束，不再执行 PDF 转换、图片提取、splitter、transform 和 embedding |
| `BaseLoader` | 将不同来源的文件转换为统一 `Document(id + text + summary + metadata)` 对象 | 负责文件识别、使用 MarkItDown 完成 PDF -> Markdown、使用 PyMuPDF 提取 PDF 图片、编码处理和基础 metadata 抽取；`summary` 为顶层字段，后续由独立摘要步骤生成或更新，不放入 `metadata.summary`；只处理去重判断后确认需要摄取的文档 |
| `DocumentSummarizer` | 为加载后的文档生成顶层 `Document.summary` | 作为 Loader 之后、Splitter 之前的独立步骤；读取 `document_summary_prompt.yaml`；复用统一 LLM provider；已有同版本摘要时保持幂等；摘要只作为全局语义上下文，不写入 `metadata.summary` |
| `BaseSplitter` | 纯文本切分工具 | 职责边界固定为 `str -> List[str]`，不直接接触 `Document`、`Chunk`、metadata、图片引用等业务对象；首版使用 LangChain `RecursiveCharacterTextSplitter` 作为底层 splitter |
| `DocumentChunker` | 将 `Document` 适配为业务 `Chunk` 对象 | 调用 `libs.splitter` 得到 `List[str]` 后，转换为符合 `core.types` 契约的 `List[Chunk]`；负责生成 `chunk_id`、继承非图片类 `document.metadata`、添加 `chunk_index`、计算 `start_offset/end_offset`、建立 `source_ref`，并按图片占位符位置分发 `image_refs`；`Document.metadata.images[]` 保留完整文档图片清单，`Chunk.metadata.images[]` 只保留当前 chunk 通过 `image_refs` 命中的图片子集 |
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

RAG 流水线内部统一使用 `Document` 和 `Chunk` 作为核心数据对象。Loader 负责生成基础 `Document`，文档摘要步骤负责补充 `Document.summary`，Splitter 和 Transform 负责把 `Document` 加工为 `Chunk`，Embedding 和 Storage 只面向稳定的 `Chunk` 写入索引。

`Document` 字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `str` | 文档稳定 ID，建议由 `collection + source_path + source_hash` 生成 |
| `text` | `str` | 文档统一文本内容，PDF 先转 Markdown，图片位置写入占位符 |
| `summary` | `str/null` | 文档级语义摘要，供 chunk rewrite、文档摘要工具和 Dashboard 使用；空值表示摘要步骤未启用或摘要生成降级 |
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
- `DocumentChunker` 必须把 `Document.metadata` 中的来源、标题、collection、hash、heading 等非图片字段复制到 `Chunk.metadata`，再追加 `chunk_index`、`image_refs` 等 chunk 级 metadata；`Document.metadata.images[]` 是文档级完整图片清单，不能无脑复制到每个 chunk；`Chunk.metadata.images[]` 只能保留当前 chunk 命中的图片子集，没有图片引用的 chunk 必须删除 `images` 和 `image_refs`。
- 来源引用保存在独立 `Chunk.source_ref` 字段，Dense/Sparse Retrieval 构造 `RetrievalResult` 时再将其深拷贝到 result metadata，避免持久化职责混淆或丢失文档来源信息。

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

首版 MCP 传输协议固定为 **stdio**。AImodel 后端作为 MCP client，在服务启动时拉起并长期复用一个 RAG MCP 子进程，而不是每次用户对话临时启动。推荐启动命令：

```powershell
uv run --project services/ai-service/rag python -m src.mcp_server.server --transport stdio
```

stdio 协议要求 stdout/stdin 只承载 MCP 协议帧，业务日志不得写入 stdout。RAG MCP 普通运行日志写入 `src/logs/app.log`，错误诊断可以写 stderr；Trace 仍按可观测性阶段写入结构化日志。MCP 启动入口必须加载本地 `.env`，并读取 `DATABASE_URL`、`DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、`RAG_SETTINGS_PATH`、`RAG_DEFAULT_COLLECTION` 等环境变量。

AImodel 集成时不让 Agent 直接依赖 MCP SDK。后续 H2/H3 应新增 AImodel 侧 adapter：`rag_mcp_client.py` 负责启动/连接 stdio MCP 子进程，`rag_tool.py` 负责把 MCP `query_knowledge_hub` 包装成 LangChain Tool。Agent 只依赖 `RagKnowledgeTool` 这类业务工具，底层可以从直连 Python 平滑切换为 MCP。

MCP 工具一：`query_knowledge_hub`

输入：

```json
{
  "query": "如何挑选高性价比无线耳机？",
  "collection": "shopping_guides",
  "top_k": 5,
  "no_rerank": false,
  "include_image_base64": false
}
```

输出：

```json
{
  "ok": true,
  "content": "[1] 可用于回答的第一段知识上下文\n\n[2] 可用于回答的第二段知识上下文",
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
  "is_empty": false
}
```

`content` 只由最终排序后的 chunk 文本按 `[1]`、`[2]` 编号格式化，不直接序列化
Dense/Sparse 分数、向量、Provider 返回、过滤报告或内部 tool result。`citations` 和
`images` 使用独立公共契约；默认只返回图片 metadata 与受管 `file_path`，不默认返回
base64，避免 stdio tool payload 过大。若调用方明确传入 `include_image_base64=true`，
后续工具实现可以附加受限大小的 `base64_content` 字段。没有检索命中时返回
`ok=true`、`is_empty=true`、空 `content`、空引用和空图片列表。

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
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
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
  -> 输出 Document(id + text + summary + metadata.images[])
  -> Splitter 保留图片引用标记到对应 chunk
  -> ImageCaptioner 判断 vision_llm 和 image_refs
  -> 满足条件时生成 caption 并写入 chunk metadata
  -> Storage 存储增强后的 chunk 和原始图片
```

各阶段输出：

| 阶段 | 输出 |
| --- | --- |
| Loader | `Document(id + text + summary + metadata.images[])`，其中 `summary` 是顶层可空字段，`text` 包含图片占位符，`metadata.images[]` 保存图片基础信息 |
| Splitter | chunk 文本保留图片引用标记，chunk metadata 增加 `image_refs: List[image_id]`；`Chunk.metadata.images[]` 只保留当前 chunk 命中的图片子集 |
| ImageCaptioner | 当 `vision_llm.enabled=true` 且 chunk 存在 `image_refs` 时生成 caption，并写入 chunk metadata |
| Storage | 向量库存储增强后的 chunk，文件系统保存原始图片，PostgreSQL `image_index` 表保存图片索引信息 |

#### 3.7.2 Loader 技术要点

Loader 负责从 PDF、Markdown 或其他文档中抽取图片，并建立图片与文档文本之间的引用关系。

关键实现：

- **提取策略**：PDF 文本由 MarkItDown 转换，PDF 图片由 PyMuPDF 按页码和物理位置提取；Markdown 图片按本地图片语法解析。
- **图片 ID**：为每张图片生成稳定 `image_id`，建议基于 `source_doc + page + image_index + image_hash`。
- **引用标记**：在文档文本中写入图片占位符，例如 `[[image:image_xxx]]`，确保后续 splitter 能保留图片与上下文的关系；PDF 图片应先按 `page + position.y + position.x` 排序，再插入到对应页文本区间末尾、下一页标记之前，避免所有图片占位符集中追加到文档末尾。
- **页标记降级**：当 MarkItDown 输出包含 `<!-- page: N -->` 等页标记时，Loader 使用页区间定位；当转换结果没有页标记时，Loader 按源位置稳定排序后追加占位符，并保留 `metadata.images[].position` 供后续改进。
- **原始图片存储**：原始图片保存到本地文件系统，数据库只保存索引和 metadata。

#### 3.7.3 Splitter 技术要点

Splitter 必须保留图片引用和文本上下文之间的关联，不能在切分时丢失图片占位符。

关键实现：

- **关联保持**：如果图片占位符位于某个标题或段落附近，应保留在对应 chunk 中。
- **chunk metadata 扩展**：每个命中图片的 chunk 增加 `image_refs: List[image_id]`，并将 `images[]` 裁剪为这些引用对应的图片子集；没有图片引用的 chunk 不保留 `images[]`。
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
| `load` | Loader 类型、原始文件类型、转换后的 `Document(id + text + summary + metadata)` 摘要、图片提取数量、耗时、失败详情 |
| `document_summary` | 摘要 Prompt 版本、LLM Provider、是否生成摘要、摘要长度、是否复用已有摘要、耗时、失败详情 |
| `split` | Splitter 类型、粗切分 chunk 数量、标题层级识别结果、平均 chunk 长度、耗时、失败详情 |
| `transform` | Transform Pipeline 总耗时、输入输出 chunk 数量，以及按配置顺序记录的 `sub_stages`；每个子阶段包含配置步骤名、具体实现类、耗时、输入输出 chunk 数量、状态、失败详情和受限 `snapshots` 预览 |
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
