# Query Trace Chunk Flow 展示设计

## 背景

AImodel RAG 的 Query Trace 已经记录 Dense、Sparse、Fusion、Filter、Rerank 和最终 `query_result` 的阶段信息。当前 Dashboard Query Trace 页面主要展示阶段耗时、候选数量、最终 contexts 和 rerank delta，但缺少一个面向调试和评估的聚合视角：无法快速看出哪些 chunk 在多个阶段反复出现、哪些 chunk 被过滤淘汰、哪些 chunk 被 rerank 提前或降级。

本设计只调整 Query Trace 页面展示策略，不改变查询链路、不重新计算检索结果、不修改 trace 写入契约。

## 目标

- 让用户快速识别高频出现的 chunk。
- 解释 chunk 从 Dense/Sparse 召回到 Fusion、Filter、Rerank、Final 的流转过程。
- 支持后续评估分析，例如 Hit Rate、MRR、NDCG、rerank delta 和空结果原因定位。
- 避免在页面中直接堆叠完整 chunk 正文，保持 Dashboard 轻量、可扫描。

## 数据来源

页面只消费 `TraceDetail.waterfall` 与 `TraceDetail.query_result` 中已有字段：

- `dense.details.chunk_ids`
- `sparse.details.chunk_ids`
- `fusion.details.fused_candidates`
- `filter.details.before_candidates`
- `filter.details.after_candidates`
- `filter.details.rejected_candidates`
- `rerank.details.before_candidates`
- `rerank.details.after_candidates`
- `query_result.contexts`

其中 Dense 和 Sparse 只记录命中的 chunk ID；Fusion、Filter、Rerank 使用轻量候选快照，包含 `rank`、`chunk_id`、`score` 和少量 metadata。

## 推荐方案

采用 **Chunk Frequency Summary + Chunk Flow Matrix** 两个视图组合。

### Chunk Frequency Summary

该视图面向“哪些 chunk 出现次数比较多”。

字段：

- `chunk_id`：chunk 唯一 ID。
- `appeared_count`：该 chunk 出现在 Dense、Sparse、Fusion、Filter、Rerank、Final 中的阶段数量。
- `stages`：出现过的阶段列表。
- `final_rank`：最终进入 `query_result.contexts` 时的排名；未进入则为空。
- `best_score`：候选快照中可获得的最高 score。
- `filtered_reason`：若被 filter 淘汰，展示原因，例如 `collection`。

排序规则：

1. `appeared_count` 降序。
2. `final_rank` 非空优先。
3. `best_score` 降序。
4. `chunk_id` 升序，保证稳定显示。

### Chunk Flow Matrix

该视图面向“chunk 在阶段之间如何变化”。

字段：

- `chunk_id`
- `dense`：命中则显示 `hit`。
- `sparse`：命中则显示 `hit`。
- `fusion_rank`：RRF 融合后的排名。
- `filter`：`kept` 或 `rejected:<reason>`。
- `rerank_rank`：Rerank 后排名。
- `final_rank`：最终 contexts 排名。

该矩阵直接解释：

- 只被 Dense 命中的语义相关候选。
- 只被 Sparse 命中的关键词候选。
- 同时被 Dense/Sparse 命中的稳定候选。
- Fusion 后进入候选池但被 Filter 淘汰的 chunk。
- Rerank 前后排名变化。
- 最终进入 Agent 上下文的 chunk。

## 页面位置

Query Trace 页面结构调整为：

```text
Query Trace
├─ History
├─ Stage Waterfall
├─ Retrieval Comparison
│  ├─ Candidate Counts
│  ├─ Chunk Frequency Summary
│  └─ Chunk Flow Matrix
├─ Rerank Delta
└─ Query Result
```

`Chunk Frequency Summary` 和 `Chunk Flow Matrix` 放在 `Retrieval Comparison` 区域中，紧跟 candidate counts。这样用户先看到阶段候选数量，再看到具体 chunk 的跨阶段表现。

## 聚合规则

实现一个页面内纯函数，例如 `build_chunk_flow_rows(trace: TraceDetail) -> tuple[dict[str, object], ...]`。

该函数负责：

1. 从各阶段 details 提取 chunk ID 和候选快照。
2. 以 `chunk_id` 为主键聚合阶段信息。
3. 生成 frequency summary 所需字段。
4. 生成 flow matrix 所需字段。
5. 对缺失字段做兼容处理。

兼容性要求：

- 旧 trace 缺少 `chunk_ids` 或候选快照时，页面不报错。
- 旧 trace 只展示已有 candidate counts 和 query_result。
- 空查询、空召回、filter 全部淘汰时，表格显示空列表或明确状态，不抛异常。

## 不做的事情

- 不展示完整 chunk 正文。
- 不做 Sankey 流向图。
- 不在页面重新查询数据库补全文本。
- 不在 Query Trace 页面重新计算 Dense/Sparse/Fusion/Rerank。
- 不改变 query trace 写入结构。

## 测试方案

测试放在 Dashboard 页面测试中，覆盖：

- Dense/Sparse chunk IDs 能聚合到 flow rows。
- Fusion candidate rank 能显示为 `fusion_rank`。
- Filter rejected candidate 能显示 `rejected:<reason>`。
- Rerank after candidates 能显示 `rerank_rank`。
- `query_result.contexts` 能生成 `final_rank`。
- 缺少新字段的旧 trace 可以正常渲染。

## 验收标准

- Query Trace 页面出现 `Chunk Frequency Summary`。
- Query Trace 页面出现 `Chunk Flow Matrix`。
- 高频 chunk 按出现次数排序。
- filter 淘汰原因可见。
- rerank 前后排名变化可见。
- 旧 trace 不报错。
- 页面测试通过。

## 自审

- 本设计聚焦 Query Trace 页面展示，不扩大到 trace 写入或评估 runner。
- 数据来源全部来自现有 TraceDetail，不引入新的数据库查询。
- 展示结构能解释用户当前关心的“出现次数比较多的 chunk”。
- 兼容旧 trace，避免 Dashboard 因历史数据缺字段而失败。
