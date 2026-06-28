<!-- synced-from: services\ai-service\rag\DEV_SPEC.md -->
<!-- reference: 测试规范 -->

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
