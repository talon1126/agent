# AImodel Modular RAG

`AImodel Modular RAG` 是 `ai-service` 下可独立开发和部署的检索增强生成子系统，用于为购物对话 Agent 提供可引用、可追踪、可评估的知识检索能力。

## 项目定位

- 使用 PostgreSQL 和 pgvector 持久化文档、Chunk、向量与运行数据。
- 使用 Dense Embedding 与 BM25 构建双路混合检索。
- 通过统一抽象接口和工厂模式切换 LLM、Embedding、Splitter、VectorStore、Reranker 与 Evaluator。
- 使用 MCP tools 向 AImodel 或其他 MCP Client 暴露知识检索能力。
- 使用结构化 Trace、Streamlit Dashboard 和评估指标降低 RAG 黑盒程度。

商品价格、库存、详情和商品链接等实时事实仍由现有商品 API 工具负责，RAG 仅补充选购指南、政策 FAQ、商品说明和品牌知识。

## 开发状态

项目按照 [DEV_SPEC.md](DEV_SPEC.md) 中的阶段任务进行开发。当前阶段仅建立独立 Python 模块的基础文件，运行入口、配置、测试和业务实现将在后续任务中逐步补充。

## 开发约束

- Python 版本为 3.12。
- 使用 pytest 执行单元、集成和端到端测试。
- 不使用 LlamaIndex 或 LangChain RAG 框架，仅允许使用 `langchain-text-splitters`。
- 外部服务在单元测试中必须使用 Fake 或 Mock 隔离。
- 配置值统一来自 `config/settings.yaml` 或环境变量，不在业务代码中硬编码。
