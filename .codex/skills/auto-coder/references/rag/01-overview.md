<!-- synced-from: services\ai-service\rag\DEV_SPEC.md -->
<!-- reference: 项目概述 -->

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
